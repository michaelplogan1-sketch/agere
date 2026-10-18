"""Dependent replay: answers and plans record what they read; a tick that touches it makes them stale.

Registry (<ws>/streaming/streaming.sqlite3, table answers): one row per registered answer with
    kind            'cohort_family' (code-rendered from a parsed family spec: re-run for free at the new as_of),
                    'sql' (code-rendered SQL with its as_of baked in: re-run as is), 'model' (written by a model: flagged
                    for re-ask, never re-run automatically), 'plan' (an action-backbone plan over a cohort)
    reads           semantic tables named in the query (or given), the cohort definition (family spec), the patient
                    ids the answer returned (or the plan's cohort), as_of and as_of_mode ('rolling' follows the
                    workspace watermark; 'pinned' keeps its as_of and is stale only when data it reads changed)
A tick makes an answer stale when (a) a table it reads changed (exact per-table row diff measured during the splice),
or (b) it is rolling and the watermark moved. The reason is kept. Stale answers lose their verified-cache entries:
learning.sqlite3 (verified pairs are keyed by the data release, so after a tick only the pairs of answers that are
still fresh are carried forward to the new release; stale ones are not) and question-plans.sqlite3 rows for the same
question are removed.

Hooks for the action backbone (plans/*, the work store): call these after Workspace.ingest_delta returns a tick id.
    affected_patients(ws_dir, tick_id) -> {patient_id: {'streams': [...], 'flags': [...], 'tables': [...]}}
    stale_plans(ws_dir, tick_id)       -> [answer rows of kind 'plan' (or any kind) made stale by that tick, with the
                                           cohort patients the tick touched]
    register_plan(ws_dir, plan_id, question, patient_ids, tables, as_of) -> registers a plan so stale_plans can find it
Nothing here edits plans/* or the work store.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from . import ticks

DDL = """
CREATE TABLE IF NOT EXISTS answers(answer_id TEXT PRIMARY KEY, question TEXT, kind TEXT, spec TEXT, query TEXT, as_of_mode TEXT,
  as_of TEXT, tables TEXT, patient_ids TEXT, count INTEGER, result_hash TEXT, status TEXT, deterministic INTEGER,
  created_at TEXT, created_tick INTEGER, refreshed_tick INTEGER, stale_since_tick INTEGER, stale_reason TEXT, verified_question TEXT);
CREATE TABLE IF NOT EXISTS answer_history(answer_id TEXT, tick_id INTEGER, at TEXT, as_of_before TEXT, as_of_after TEXT,
  count_before INTEGER, count_after INTEGER, added TEXT, removed TEXT, changed INTEGER, stale_reason TEXT);
CREATE TABLE IF NOT EXISTS answer_stale(tick_id INTEGER, answer_id TEXT, reason TEXT, PRIMARY KEY(tick_id, answer_id));
CREATE TABLE IF NOT EXISTS cache_invalidations(tick_id INTEGER, answer_id TEXT, store TEXT, question TEXT, rows INTEGER, action TEXT, at TEXT);
"""
_WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def connect(ws_dir):
    db = ticks.connect(ws_dir)
    db.executescript(DDL)
    return db


def tables_read(sql, semantic_path):
    db = sqlite3.connect(f'file:{semantic_path}?mode=ro', uri=True)
    try:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    finally:
        db.close()
    return sorted({w for w in _WORD.findall(sql or '') if w in names})


def _hash(ids):
    return hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()[:16]


def _last_tick(db):
    r = db.execute('SELECT max(tick_id) FROM ticks').fetchone()
    return r[0] if r else None


def register(ws_dir, answer_id, question, *, kind, semantic_path, as_of, patient_ids=None, count=None, spec=None, query=None,
             tables=None, as_of_mode='rolling', verified_question=None):
    deterministic = int(kind in ('cohort_family', 'sql'))
    tables = tables if tables is not None else tables_read(query, semantic_path)
    ids = sorted(set(patient_ids or []))
    db = connect(ws_dir)
    try:
        with db:
            db.execute('INSERT OR REPLACE INTO answers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (answer_id, question, kind, json.dumps(spec) if spec is not None else None, query, as_of_mode, as_of,
                        json.dumps(tables), json.dumps(ids), count if count is not None else len(ids), _hash(ids), 'fresh',
                        deterministic, time.strftime('%Y-%m-%dT%H:%M:%S'), _last_tick(db), None, None, None,
                        verified_question or question))
    finally:
        db.close()
    return {'answer_id': answer_id, 'tables': tables, 'count': count if count is not None else len(ids)}


def register_plan(ws_dir, plan_id, question, patient_ids, tables, as_of, semantic_path=None):
    """Action-backbone hook: register a plan's cohort and the tables it read so stale_plans(tick) can find it."""
    return register(ws_dir, plan_id, question, kind='plan', semantic_path=semantic_path, as_of=as_of, patient_ids=patient_ids,
                    tables=list(tables), as_of_mode='pinned')


def mark_stale(ws_dir, tick_id):
    """-> [{answer_id, reason}] newly stale after tick_id."""
    db = connect(ws_dir)
    out = []
    try:
        tick = db.execute('SELECT as_of_before, as_of FROM ticks WHERE tick_id = ?', (tick_id,)).fetchone()
        if not tick:
            return out
        moved = tick[0] != tick[1]
        changed = {t: n for t, n in db.execute('SELECT tbl, patients_changed FROM tick_table WHERE tick_id = ? AND rows_changed > 0', (tick_id,))}
        with db:
            for aid, kind, tables, mode, ids, status in db.execute(
                    'SELECT answer_id, kind, tables, as_of_mode, patient_ids, status FROM answers').fetchall():
                reasons = []
                hit = sorted(set(json.loads(tables or '[]')) & set(changed))
                if hit:
                    reasons.append('tables changed: ' + ', '.join(hit))
                if moved and mode == 'rolling':
                    reasons.append(f'watermark moved {tick[0]} -> {tick[1]}')
                if kind == 'plan' and not hit:
                    members = set(json.loads(ids or '[]'))
                    touched = {r[0] for r in db.execute('SELECT patient_id FROM tick_patient WHERE tick_id = ?', (tick_id,))}
                    if members & touched:
                        reasons.append(f'{len(members & touched)} cohort patients touched')
                if not reasons:
                    continue
                new_status = 'stale' if kind in ('cohort_family', 'sql') else 'needs_reask' if kind == 'model' else 'stale'
                db.execute("UPDATE answers SET status = ?, stale_since_tick = CASE WHEN status = 'fresh' THEN ? ELSE stale_since_tick END, "
                           "stale_reason = ? WHERE answer_id = ?", (new_status, tick_id, '; '.join(reasons), aid))
                db.execute('INSERT OR REPLACE INTO answer_stale VALUES (?,?,?)', (tick_id, aid, '; '.join(reasons)))
                out.append({'answer_id': aid, 'kind': kind, 'status': new_status, 'reason': '; '.join(reasons)})
    finally:
        db.close()
    return out


def invalidate_caches(ws_dir, tick_id, stale, *, old_context=None, new_context=None):
    """Drop verified-cache entries for stale answers; carry fresh answers' verified pairs to the new data release."""
    ws_dir = Path(ws_dir)
    pipe = ws_dir / 'pipeline'
    db = connect(ws_dir)
    log = []
    try:
        stale_q = {r[0]: r[1] for r in db.execute('SELECT answer_id, verified_question FROM answers WHERE status IN (?, ?)', ('stale', 'needs_reask'))
                   if r[0] in {s['answer_id'] for s in stale}}
        fresh_q = [r[0] for r in db.execute("SELECT verified_question FROM answers WHERE status = 'fresh'")]
        learn = pipe / 'learning.sqlite3'
        if learn.is_file():
            ldb = sqlite3.connect(learn)
            try:
                with ldb:
                    for aid, q in stale_q.items():
                        n = ldb.execute("UPDATE verified SET context = context || '|stale@tick' || ? WHERE lower(trim(question)) = lower(trim(?)) "
                                        "AND context NOT LIKE '%|stale@tick%'", (str(tick_id), q)).rowcount
                        log.append((tick_id, aid, 'learning.verified', q, n, 'invalidated'))
                    if old_context and new_context and old_context != new_context:
                        for q in fresh_q:
                            rows = ldb.execute('SELECT question, plan_kind, plan, signature, route, models, agreement, created, provenance '
                                               'FROM verified WHERE lower(trim(question)) = lower(trim(?)) AND context = ?', (q, old_context)).fetchall()
                            for r in rows:
                                prov = json.dumps({'carried_forward_by': 'agere-streaming/1', 'tick': tick_id, 'from_context': old_context})
                                ldb.execute('INSERT OR REPLACE INTO verified(question, plan_kind, plan, signature, route, models, agreement, created, '
                                            'context, provenance) VALUES (?,?,?,?,?,?,?,?,?,?)', (*r[:8], new_context, prov))
                            if rows:
                                log.append((tick_id, None, 'learning.verified', q, len(rows), 'carried_forward'))
            finally:
                ldb.close()
        plans = pipe / 'question-plans.sqlite3'
        if plans.is_file() and stale_q:
            pdb = sqlite3.connect(plans)
            try:
                with pdb:
                    want = {q.strip().lower() for q in stale_q.values()}
                    for key, payload in pdb.execute('SELECT id, payload FROM plans').fetchall():
                        try:
                            q = (json.loads(payload).get('question') or '').strip().lower()
                        except ValueError:
                            continue
                        if q in want:
                            pdb.execute('DELETE FROM plans WHERE id = ?', (key,))
                            log.append((tick_id, None, 'question-plans', q, 1, 'invalidated'))
            finally:
                pdb.close()
        with db:
            db.executemany('INSERT INTO cache_invalidations VALUES (?,?,?,?,?,?,?)', [(*l, time.strftime('%Y-%m-%dT%H:%M:%S')) for l in log])
    finally:
        db.close()
    return log


def run_cohort_family(question, as_of, semantic_path):
    """Deterministic cohort-family answer (no model). -> (spec, sql, patient_ids) or None."""
    from ..clinical import cohort_families as cf
    p = cf.parse(question, as_of)
    if p is None or p['missing']:
        return None
    spec = cf.validate({'family': p['family'], 'parameters': p['parameters'], 'returns': p['returns']})
    sem = sqlite3.connect(f'file:{semantic_path}?mode=ro', uri=True)
    try:
        names = {r[0] for r in sem.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        sql = cf.render(spec, as_of, names)
        ids = sorted({r[0] for r in sem.execute(sql) if r[0]})
    finally:
        sem.close()
    return spec, sql, ids


def _run_sql(sql, semantic_path):
    sem = sqlite3.connect(f'file:{semantic_path}?mode=ro', uri=True)
    try:
        return sorted({r[0] for r in sem.execute(sql) if r[0]})
    finally:
        sem.close()


def refresh(ws_dir, semantic_path, as_of, *, stale_only=True, tick_id=None):
    """Re-run deterministic stale answers at the current as_of (rolling) or their own (pinned). Model answers stay flagged
    for re-ask. -> report {refreshed, changed, needs_reask, answers: [...]}."""
    db = connect(ws_dir)
    rows = []
    try:
        tick_id = tick_id or _last_tick(db)
        q = 'SELECT answer_id, question, kind, spec, query, as_of_mode, as_of, patient_ids, count, status, stale_reason FROM answers'
        if stale_only:
            q += " WHERE status IN ('stale', 'needs_reask')"
        for aid, question, kind, spec, query, mode, a_as_of, ids, count, status, reason in db.execute(q).fetchall():
            before = set(json.loads(ids or '[]'))
            if kind not in ('cohort_family', 'sql'):
                rows.append({'answer_id': aid, 'kind': kind, 'status': 'needs_reask', 'count_before': count, 'reason': reason})
                continue
            run_as_of = as_of if mode == 'rolling' else a_as_of
            t0 = time.time()
            if kind == 'cohort_family':
                res = run_cohort_family(question, run_as_of, semantic_path)
                if res is None:
                    rows.append({'answer_id': aid, 'kind': kind, 'status': 'refresh_failed', 'reason': 'family no longer parses'})
                    continue
                spec_new, query_new, after = res
            else:
                spec_new, query_new, after = (json.loads(spec) if spec else None), query, _run_sql(query, semantic_path)
            after = set(after)
            added, removed = sorted(after - before), sorted(before - after)
            changed = bool(added or removed)
            tables = tables_read(query_new, semantic_path)
            with db:
                db.execute("UPDATE answers SET status = 'fresh', spec = ?, query = ?, as_of = ?, tables = ?, patient_ids = ?, count = ?, "
                           "result_hash = ?, refreshed_tick = ?, stale_reason = NULL, stale_since_tick = NULL WHERE answer_id = ?",
                           (json.dumps(spec_new) if spec_new is not None else None, query_new, run_as_of, json.dumps(tables),
                            json.dumps(sorted(after)), len(after), _hash(after), tick_id, aid))
                db.execute('INSERT INTO answer_history VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (aid, tick_id, time.strftime('%Y-%m-%dT%H:%M:%S'), a_as_of, run_as_of, len(before), len(after),
                            json.dumps(added), json.dumps(removed), int(changed), reason))
            rows.append({'answer_id': aid, 'kind': kind, 'status': 'refreshed', 'count_before': len(before), 'count_after': len(after),
                         'added': len(added), 'removed': len(removed), 'changed': changed, 'as_of_before': a_as_of,
                         'as_of_after': run_as_of, 'seconds': round(time.time() - t0, 2), 'reason': reason,
                         'added_sample': added[:5], 'removed_sample': removed[:5]})
    finally:
        db.close()
    return {'refreshed': sum(1 for r in rows if r['status'] == 'refreshed'), 'changed': sum(1 for r in rows if r.get('changed')),
            'needs_reask': sum(1 for r in rows if r['status'] == 'needs_reask'), 'answers': rows}


# ----------------------------------------------------------------------------------------------- action backbone hooks
def affected_patients(ws_dir, tick_id):
    """{patient_id: {'streams', 'flags', 'tables'}} for one tick: patients whose records changed (added / removed /
    payload changed / person changed) plus patients whose semantic rows changed for processing-time reasons (watermark)."""
    db = connect(ws_dir)
    try:
        out = {}
        for pid, streams, reason in db.execute('SELECT patient_id, streams, reason FROM tick_patient WHERE tick_id = ?', (tick_id,)):
            out[pid] = {'streams': json.loads(streams or '[]'), 'flags': sorted(set((reason or '').split(',')) - {''}), 'tables': []}
        for t, pid in db.execute('SELECT tbl, patient_id FROM tick_table_patient WHERE tick_id = ?', (tick_id,)):
            out.setdefault(pid, {'streams': [], 'flags': [], 'tables': []})['tables'].append(t)
        return out
    finally:
        db.close()


def stale_plans(ws_dir, tick_id, *, kinds=('plan',)):
    """Registered plans (or other kinds) made stale by tick_id, with the cohort members that tick touched, so the action
    backbone can reassess just those plans / patients."""
    db = connect(ws_dir)
    try:
        touched = set(affected_patients(ws_dir, tick_id))
        out = []
        q = ('SELECT a.answer_id, a.question, a.kind, a.patient_ids, s.reason, a.as_of FROM answer_stale s '
             'JOIN answers a ON a.answer_id = s.answer_id WHERE s.tick_id = ?')
        for aid, question, kind, ids, reason, as_of in db.execute(q, (tick_id,)):
            if kinds and kind not in kinds:
                continue
            members = set(json.loads(ids or '[]'))
            out.append({'plan_id': aid, 'question': question, 'kind': kind, 'reason': reason, 'as_of': as_of,
                        'cohort_size': len(members), 'touched_members': sorted(members & touched)})
        return out
    finally:
        db.close()
