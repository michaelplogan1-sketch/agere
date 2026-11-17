"""Recipe upkeep: each saved recipe's track record, and whether it keeps answering.

Promotion is gated; life after promotion was not. A promoted recipe is replayed with no model, so a wrong one repeats
silently. This module keeps a record for each recipe (bottles.bottle_event) and acts on it with one rule.

  use / near_use   the recipe answered a question, exactly or through the near-match rung (recorded by the router)
  check_agree      a different model wrote its own query for a question the recipe answers, and the rows agreed on the
                   full data and on every patient subset
  check_differ     they did not agree
  misapplied       the recipe answered a question it does not fit (found by the wrong-match test)

The rule, chosen from the research on library drift, which found retiring on thin evidence worse than not retiring at all:
  * one disagreement and no agreement -> a tie-break by a reader of a different kind (the frontier model);
  * two or more disagreements and more disagreements than agreements -> suspended: the recipe stops answering, is never
    deleted, and goes back to answering only if later checks agree more often than they differ;
  * a recipe misapplied twice is no longer offered by the near-match rung (bottles.near_blocked); exact matches still use it.

    python -m clinical_core.agere.tiered.upkeep backfill
    python -m clinical_core.agere.tiered.upkeep sweep [limit] [workers]
    python -m clinical_core.agere.tiered.upkeep report
"""
import json
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import bottles as bottle_store, store

OUT = Path('artifacts/agere-tiered-core/upkeep')
USE_CHECKS = 2          # besides its own question, re-check a recipe on up to this many questions it actually answered


def standing(bottles, bottle_id):
    """-> {'check_agree', 'check_differ', ...}: the record the rule reads. A re-check counts once per reader and question
    (the latest reading by that model of that question), so running the same model again is not a second opinion."""
    out = dict(bottles.record(bottle_id))
    latest = {}
    for kind, question, detail in bottles.db.execute("SELECT kind, question, detail_json FROM bottle_event WHERE bottle_id = ? AND kind IN ('check_agree', 'check_differ') "
                                                    "AND (detail_json IS NULL OR detail_json NOT LIKE '%superseded%') ORDER BY id", (bottle_id,)):
        latest[(str(question or '').lower(), (json.loads(detail or '{}')).get('model'))] = kind
    out['check_agree'] = sum(k == 'check_agree' for k in latest.values())
    out['check_differ'] = sum(k == 'check_differ' for k in latest.values())
    from . import models
    seen = [k for (q, m), k in latest.items() if m == models.LLM]
    out['frontier'] = 'differ' if 'check_differ' in seen else 'agree' if seen else None
    return out


def decide(record, status):
    """The rule. record: standing(); status: the recipe's status now. -> (new status or None to leave it, reason, tie_break_needed).
    More disagreeing readers than agreeing ones, and at least two, suspend. On a tie, the frontier model's reading decides;
    a tie it has not read yet, or a lone disagreement, asks for its reading. A suspended recipe is restored the same way."""
    agree, differ, frontier = record.get('check_agree', 0), record.get('check_differ', 0), record.get('frontier')
    if status not in ('promoted', 'suspended'): return None, '', False
    if (differ >= 2 and differ > agree) or (differ and differ == agree and frontier == 'differ'):
        want, why = 'suspended', f'independent re-checks: {differ} readers disagreed, {agree} agreed' + ('; the frontier model disagreed on the tie' if differ == agree else '')
    elif agree > differ or (agree and differ == agree and frontier == 'agree'):
        want, why = 'promoted', f'independent re-checks: {agree} readers agreed, {differ} disagreed'
    else:
        want, why = status, ''
    tie_break = frontier is None and differ >= 1 and differ >= agree
    if want == status: return None, '', tie_break and status in ('promoted', 'suspended')
    if status == 'promoted' and want == 'promoted': return None, '', False
    return want, why, False


def same_numbers(a, b):
    """Code only. The same figures laid out differently: one row per year against one column per year, a label written
    "0" or "zero", a value rounded to two places. Every number in one table, rounded to two places, appears in the other as
    often; zeros and empty cells are left out, since one layout writes 0 where the other leaves the row out. Text cells are ignored, so this is only a fallback after the strict row comparison fails, and only when there are
    numbers to compare."""
    def numbers(rows):
        out = Counter()
        for r in rows or []:
            for v in r:
                if isinstance(v, bool) or v is None: continue
                if isinstance(v, (int, float)) and v != 0: out[round(float(v), 2)] += 1   # a zero and a missing row say the same
        return out
    na, nb = numbers(a), numbers(b)
    if na == nb:   # no non-zero figures on either side: both say there is nothing, whether as zero counts or as no rows
        return bool(na) or (not a and not b) or (not a and _has_figures(b)) or (not b and _has_figures(a)) or (_has_figures(a) and _has_figures(b))
    if len(a or []) == len(b or []) and na and nb and (not na - nb or not nb - na): return True   # one table adds a column, rows line up
    for x, y in ((na, nb), (nb, na)):                                        # one table adds a total of the others
        extra = y - x
        if not x - y and sum(extra.values()) == 1 and round(sum(k * v for k, v in x.items()), 2) == next(iter(extra)): return True
    return False


def _has_figures(rows):
    return any(v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)) for r in rows or [] for v in r)


def _same(a, b, loose):
    from . import evaluate
    return (evaluate.correct(a, b) and evaluate.correct(b, a)) or (loose and same_numbers(a, b))


def _subsets(first_sql, first_params, second_sql, second_params, seed, loose):
    from . import curriculum
    detail = []
    for cohort in curriculum.patient_subsets(seed):
        db = store.connect(cohort)
        try:
            a, b = store.run(first_sql, first_params, db)[1], store.run(second_sql, second_params, db)[1]
            detail.append({'patients': len(cohort), 'same': _same(a, b, loose), 'rows': [len(a), len(b)]})
        except store.RecipeError as exc:
            detail.append({'patients': len(cohort), 'same': False, 'error': str(exc)[:120]})
        finally:
            db.close()
    return all(d['same'] for d in detail), detail


def _params_for_second(recipe):
    return {k: (int(v['value']) if v.get('type') == 'int' else str(v.get('value'))) for k, v in (recipe.get('params') or {}).items()}


def check(bottle, question, params, model):
    """One independent re-check. -> ('agree' | 'differ' | 'skipped', detail). Opens its own connections (runs in a thread)."""
    from . import curriculum, evaluate, models
    db = store.connect()
    try:
        saved = store.run(bottle['sql'], params, db)[1]
        second, step = models.llm_recipe(question, model=model)
        if second is None or second.get('read'):
            return 'skipped', {'why': 'the second model wrote no query', 'model': model, 'usd': step.get('usd', 0.0)}
        other = _params_for_second(second)
        rows = store.run(second['sql'], other, db)[1]
        detail = {'model': model, 'usd': step.get('usd', 0.0), 'second_sql': second['sql'][:600], 'second_params': other}
        strict = evaluate.correct(saved, rows) and evaluate.correct(rows, saved)
        if not strict and not same_numbers(saved, rows):
            return 'differ', {**detail, 'where': 'full data', 'saved_rows': len(saved), 'second_rows': len(rows), 'saved_first': str(saved[:2])[:200], 'second_first': str(rows[:2])[:200]}
        ok, parts = _subsets(bottle['sql'], params, second['sql'], other, question, loose=not strict)
        if not strict: detail['layout_only'] = True     # the same figures in a different layout
        return ('agree', detail) if ok else ('differ', {**detail, 'where': 'a patient subset', 'subsets': [p for p in parts if not p['same']][:2]})
    except Exception as exc:
        if 'limit' in str(exc).lower(): raise
        return 'skipped', {'why': f'{type(exc).__name__}: {str(exc)[:160]}', 'model': model}
    finally:
        db.close()


# ---- candidate (off by default): a bounded equivalence check before a saved recipe is reused -------------------------------
# The gate above compares the saved recipe with a second model's query for ONE set of parameter values (full data and patient
# subsets). A recipe can agree for 2029 and be wrong for 2027, or agree only because the data has no NULL where the two
# queries treat NULL differently. This check searches for a counterexample, code only: the same two queries for every
# parameter value the data holds (one parameter varied at a time, up to MAX_CASES) and on a copy of the tables they read
# with one value in twenty set to NULL. It is a bounded search, not a proof of equivalence; passing means no counterexample
# was found in these cases. Switch: AGERE_TIERED_EQUIVALENCE=on (promotion keeps a recipe provisional when it fails).
import os as _os
import re as _re

MAX_CASES = 12


def equivalence_enabled():
    return _os.environ.get('AGERE_TIERED_EQUIVALENCE', 'off') == 'on'


def _placeholders(sql):
    return list(dict.fromkeys(_re.findall(r'(?<![:\w]):([A-Za-z_]\w*)', sql or '')))


def _second_params(second_sql, detail, values):
    """-> (the second query's values, link: its parameter -> the saved recipe's parameter it follows), or (None, {}).
    Values as recorded when the check recorded them; otherwise matched to the saved recipe's by name, then by value."""
    recorded = {k: str(v) for k, v in (detail.get('second_params') or {}).items()}
    out, link = {}, {}
    for name in _placeholders(second_sql):
        if name in recorded: out[name] = recorded[name]
        elif name in values: out[name] = str(values[name])
        else:
            same = [k for k, v in values.items() if str(v) in [str(x) for x in recorded.values()]] or ([next(iter(values))] if len(values) == 1 else [])
            if not same: return None, {}
            out[name] = str(values[same[0]])
        follow = [k for k, v in values.items() if k == name] or [k for k, v in values.items() if str(v) == out[name]]
        if len(follow) == 1: link[name] = follow[0]
    return out, link


def _text_domain(db, sql, name, current, limit=4):
    col = _re.search(rf'(\w+)\s*(?:=|IN\s*\()\s*:{name}\b', sql, _re.I)
    if not col: return [current]
    col = col.group(1)
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') UNION SELECT name FROM sqlite_temp_master WHERE type IN ('table','view')"):
        if not _re.search(rf'\b{_re.escape(table)}\b', sql): continue
        try:
            vals = [r[0] for r in db.execute(f'SELECT "{col}", COUNT(*) FROM "{table}" WHERE "{col}" IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT {limit}')]
        except Exception:
            continue
        if vals: return list(dict.fromkeys([current] + [str(v) for v in vals]))
    return [current]


def parameter_cases(db, sql, params, values):
    """-> [values]: the original values, then one parameter varied at a time over the values the data holds."""
    cases = [dict(values)]
    for name, spec in (params or {}).items():
        t, cur = spec.get('type'), values.get(name)
        if cur is None: continue
        if t == 'year': alt = [str(y) for y in range(2026, 2031)]
        elif t == 'month': alt = [f'{y}-{m:02d}' for y, m in ((2026, 7), (2027, 3), (2028, 12), (2029, 1), (2030, 6))]
        elif t == 'int':
            n = int(cur); alt = sorted({1, 3, max(1, n // 2), n * 2, 10})
        elif t == 'text': alt = _text_domain(db, sql, name, str(cur))
        else: alt = []
        for v in alt:
            if str(v) != str(cur): cases.append({**values, name: int(v) if t == 'int' else str(v)})
    return cases[:MAX_CASES]


def _with_nulls(db, sql):
    """Shadow each table the query reads with a TEMP copy where one non-key value in twenty is NULL (the main file stays
    read-only)."""
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    used = [t for t in names if _re.search(rf'\b{_re.escape(t)}\b', sql)]
    db.execute('PRAGMA query_only=OFF')
    try:
        for t in used:
            db.execute(f'DROP TABLE IF EXISTS temp."{t}"')
            db.execute(f'CREATE TEMP TABLE "{t}" AS SELECT * FROM main."{t}"')
            cols = [r[1] for r in db.execute(f'PRAGMA main.table_info("{t}")') if not (r[1] == 'id' or r[1].endswith('_id') or r[5])]
            for i, c in enumerate(cols):
                db.execute(f'UPDATE temp."{t}" SET "{c}" = NULL WHERE (rowid + {i}) % 20 = 7')
    finally:
        db.execute('PRAGMA query_only=ON')
    return used


def bounded(saved_sql, saved_params, values, second_sql, detail):
    """-> {'verdict': 'agree' | 'differ_parameters' | 'differ_nulls' | 'skipped', 'cases', 'counterexample'}. Code only."""
    second, link = _second_params(second_sql, detail, values)
    if second is None: return {'verdict': 'skipped', 'why': 'second query parameters could not be matched'}
    db = store.connect()
    try:
        cases = parameter_cases(db, saved_sql, saved_params, values)
        def both(v):
            other = {k: (v[link[k]] if k in link else second[k]) for k in second}
            a = store.run(saved_sql, v, db)[1]; b = store.run(second_sql, other, db)[1]
            return _same(a, b, loose=True), a, b
        for v in cases:
            ok, a, b = both(v)
            if not ok: return {'verdict': 'differ_parameters', 'cases': len(cases), 'counterexample': {'values': v, 'saved': str(a[:3])[:200], 'second': str(b[:3])[:200]}}
        _with_nulls(db, saved_sql + ' ' + second_sql)
        ok, a, b = both(values)
        if not ok: return {'verdict': 'differ_nulls', 'cases': len(cases) + 1, 'counterexample': {'values': values, 'saved': str(a[:3])[:200], 'second': str(b[:3])[:200]}}
        return {'verdict': 'agree', 'cases': len(cases) + 1}
    except store.RecipeError as exc:
        return {'verdict': 'skipped', 'why': str(exc)[:160]}
    finally:
        db.close()


def equivalence_sweep(out='artifacts/agere-tiered-core/trials/equivalence-journal.jsonl', only=None):
    """Every promoted recipe with a recorded second-model query, checked by bounded(); each result is appended as it is scored,
    and a rerun skips recipes already in the journal."""
    bottles = bottle_store.Bottles()
    path = Path(out); path.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(l)['bottle'] for l in path.read_text().splitlines() if l.strip()} if path.exists() else set()
    rows = bottles.db.execute("SELECT e.bottle_id, e.question, e.params_json, e.detail_json, b.sql, b.params_json, b.template FROM bottle_event e JOIN bottle b ON b.id = e.bottle_id "
                              "WHERE b.status = 'promoted' AND e.kind = 'check_agree' AND e.detail_json LIKE '%second_sql%' ORDER BY e.id").fetchall()
    first = {}
    for r in rows: first.setdefault(r[0], r)   # the check that promoted it
    counts = Counter()
    for bid, question, values, detail, sql, params, template in first.values():
        if (only and bid not in only) or bid in done: continue
        detail = json.loads(detail or '{}')
        if len(detail.get('second_sql', '')) >= 600: res = {'verdict': 'skipped', 'why': 'second query was truncated when recorded'}
        else: res = bounded(sql, json.loads(params or '{}'), json.loads(values or '{}'), detail['second_sql'], detail)
        counts[res['verdict']] += 1
        with open(path, 'a') as f: f.write(json.dumps({'bottle': bid, 'template': template, 'question': question, **res}, default=str) + '\n')
    return dict(counts)


def recheck_doubtful(workers=4):
    """Recipes with any recorded disagreement are checked again with the current comparison. Their earlier checks are kept
    in the record but marked superseded, so the rule reads only the new ones; a suspended recipe that now agrees is restored."""
    bottles = bottle_store.Bottles()
    ids = set()
    for eid, bid, detail in bottles.db.execute("SELECT id, bottle_id, detail_json FROM bottle_event WHERE kind = 'check_differ' AND (detail_json IS NULL OR detail_json NOT LIKE '%superseded%')").fetchall():
        if not still_differs(json.loads(detail or '{}')):
            bottles.db.execute("UPDATE bottle_event SET detail_json = json_set(COALESCE(detail_json, '{}'), '$.superseded', 'yes') WHERE id = ?", (eid,))
            ids.add(bid)
    bottles.db.commit()
    return sweep(only=ids, workers=workers)


def still_differs(detail):
    """A recorded disagreement the current comparison would still call one: different figures on the full data. Only
    disagreements the comparison may now read differently (a subset, or figures it cannot recover) are set aside and checked again."""
    import ast
    if detail.get('where') != 'full data': return False
    try:
        a, b = ast.literal_eval(detail.get('saved_first') or ''), ast.literal_eval(detail.get('second_first') or '')
    except (ValueError, SyntaxError):
        return False
    return not same_numbers(a, b)


def backfill(ledger_path='artifacts/agere-tiered-core/ledger.sqlite3'):
    """Past answers by saved recipes, from the question ledger, into the track record (once; marked as backfilled)."""
    bottles = bottle_store.Bottles()
    if bottles.db.execute("SELECT 1 FROM bottle_event WHERE detail_json LIKE '%backfilled%' LIMIT 1").fetchone(): return {'already': True}
    led, n = sqlite3.connect(ledger_path), Counter()
    for at, run, question, tier, recipe_json in led.execute("SELECT at, run, question, final_tier, recipe_json FROM question WHERE status = 'answered' AND final_tier IN ('bottles', 'near')"):
        recipe = json.loads(recipe_json or '{}')
        if not recipe.get('bottle_id'): continue
        bottles.event(recipe['bottle_id'], 'use' if tier == 'bottles' else 'near_use', question, recipe.get('params'), {'backfilled': True, 'run': run}, at=at)
        n[tier] += 1
    return dict(n)


def sweep(limit=None, workers=4, models_=None, only=None):
    """Re-check promoted recipes, those never checked and most used first: each on its own question and up to USE_CHECKS
    questions it answered, by the small model; a lone disagreement gets a tie-break by the frontier model. Then the rule."""
    from . import curriculum, models
    curriculum._keys(); curriculum._preload()
    first, tie = models_ or (models.SLM, models.LLM)
    bottles = bottle_store.Bottles()
    records = bottles.records()
    todo = [b for b in bottles.full() if b['status'] in ('promoted', 'suspended') and not b.get('read') and (b.get('provenance') or {}).get('question')]
    if only is not None: todo = [b for b in todo if b['id'] in only]
    checked = lambda b: records.get(b['id'], {}).get('check_agree', 0) + records.get(b['id'], {}).get('check_differ', 0)
    used = lambda b: records.get(b['id'], {}).get('use', 0) + records.get(b['id'], {}).get('near_use', 0)
    todo.sort(key=lambda b: (checked(b) > 0, -used(b), b['id']))
    todo = todo[:limit]

    def questions(b):
        own = b['provenance']['question']
        hit = bottles_local().match(own, statuses=('promoted', 'suspended'))
        out = [(own, hit[1])] if hit and hit[0]['id'] == b['id'] else []
        out += [(q, p) for q, p in bottles_local().uses(b['id']) if q.lower() != own.lower()][:USE_CHECKS]
        return out

    import threading
    local = threading.local()
    def bottles_local():
        if not hasattr(local, 'b'): local.b = bottle_store.Bottles()
        return local.b

    def work(b):
        done = []
        for q, p in questions(b):
            verdict, detail = check(b, q, p, first)
            done.append((q, p, verdict, detail))
        return b, done

    started, out, lines = time.monotonic(), Counter(), []
    with ThreadPoolExecutor(int(workers)) as pool:
        results = list(pool.map(work, todo))
    for b, done in results:
        if not done: out['no question selects this recipe any more'] += 1; continue
        for q, p, verdict, detail in done:
            if verdict != 'skipped': bottles.event(b['id'], 'check_' + verdict, q, p, detail)
            out['checks ' + verdict] += 1
        record = standing(bottles, b['id'])
        new, why, tie_break = decide(record, b['status'])
        if tie_break:
            src = next(((x[0], x[1]) for x in done if x[2] == 'differ'), None)
            if src is None:   # the disagreement is in the record from an earlier pass
                row = bottles.db.execute("SELECT question, params_json FROM bottle_event WHERE bottle_id = ? AND kind = 'check_differ' "
                                         "AND (detail_json IS NULL OR detail_json NOT LIKE '%superseded%') ORDER BY id DESC LIMIT 1", (b['id'],)).fetchone()
                src = (row[0], json.loads(row[1] or '{}')) if row else None
        if tie_break and src:
            q, p = src
            verdict, detail = check(b, q, p, tie)
            if verdict != 'skipped': bottles.event(b['id'], 'check_' + verdict, q, p, {**detail, 'tie_break': True})
            out['tie-breaks ' + verdict] += 1
            record = standing(bottles, b['id'])
            new, why, _ = decide(record, b['status'])
        if new:
            bottles.set_status(b['id'], new, why)
            out[f'{b["status"]} -> {new}'] += 1
            differ = next((d for _, _, v, d in done if v == 'differ'), {})
            lines.append(f"{b['id']} {b['status']} -> {new}: {why} | {b['template']}\n   saved:  {b['sql'][:300]}\n   second: {str(differ.get('second_sql'))[:300]}")
        out['recipes re-checked'] += 1
    summary = {**dict(out), 'seconds': round(time.monotonic() - started, 1), 'usd': round(sum(d.get('usd', 0.0) for _, done in results for *_, d in done), 4)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"sweep-{time.strftime('%Y%m%d-%H%M%S')}.txt").write_text(json.dumps(summary, indent=1) + '\n\n' + '\n\n'.join(lines) + '\n')
    return summary


def report(ledger_path='artifacts/agere-tiered-core/ledger.sqlite3'):
    """Store-level standing: how many recipes are checked, agree, are suspended or blocked for near-match; and the share of
    answers given by saved recipes beside how many of those came from a recipe now known to disagree."""
    bottles = bottle_store.Bottles()
    records, full = bottles.records(), bottles.full()
    status = Counter(b['status'] for b in full)
    checked = [b for b in full if records.get(b['id'], {}).get('check_agree', 0) + records.get(b['id'], {}).get('check_differ', 0)]
    doubtful = {b['id'] for b in full if records.get(b['id'], {}).get('check_differ', 0)}
    uses = Counter()
    for bid, r in records.items():
        uses['answers by saved recipes'] += r['use'] + r['near_use']
        if bid in doubtful: uses['of those, by a recipe a re-check disagreed with'] += r['use'] + r['near_use']
    led = sqlite3.connect(ledger_path)
    total = led.execute("SELECT COUNT(*) FROM question WHERE status = 'answered'").fetchone()[0]
    by_saved = led.execute("SELECT COUNT(*) FROM question WHERE status = 'answered' AND final_tier IN ('bottles', 'near')").fetchone()[0]
    lines = [f"recipes by status: {dict(status)}",
             f"re-checked: {len(checked)} of {status['promoted'] + status['suspended']} promoted or suspended",
             f"re-checks: agree {sum(r['check_agree'] for r in records.values())}, differ {sum(r['check_differ'] for r in records.values())}",
             f"recipes with any disagreement: {len(doubtful)}; suspended: {status['suspended']}; blocked for near-match: {len(bottles.near_blocked())}",
             f"answered questions in the ledger: {total}; by a saved recipe: {by_saved} ({by_saved / total:.0%})" if total else 'no answered questions in the ledger',
             f"track record: {dict(uses)}"]
    return '\n'.join(lines)


def main(argv):
    if argv[:1] == ['equivalence']:
        print(equivalence_sweep()); return
    if argv[:1] == ['backfill']: print(json.dumps(backfill())); return
    if argv[:1] == ['sweep']: print(json.dumps(sweep(int(argv[1]) if len(argv) > 1 else None, int(argv[2]) if len(argv) > 2 else 4), indent=1)); return
    if argv[:1] == ['recheck']: print(json.dumps(recheck_doubtful(int(argv[1]) if len(argv) > 1 else 4), indent=1)); return
    if argv[:1] == ['report']: print(report()); return
    print(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
