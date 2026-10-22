"""Benchmark reference audit via distinguishing / mutated databases (VeriEQL-style, test-suite accuracy).

A reference SQL and a candidate SQL that agree on one database may still mean different things, and two that disagree
may differ only on a corner the question never asked about. This audit searches for DISTINGUISHING databases:
  1. both queries run on the real database (differ_on_original if their results differ there);
  2. a small sample database is built from the tables both queries read (rows of a seeded sample of patients plus small
     lookup tables, same schema), and both run on it;
  3. targeted mutations of the columns the queries actually read (found with the SQLite authorizer): delete a row,
     duplicate a row, NULL a value, shift a date (+-1, +-30, +-365 days), perturb a number (+-1, x10, 0), swap a value with
     another row's, reassign a row to another patient. Each mutant runs both queries; any mutant where the multisets of
     result rows differ is a distinguishing database, reported with the exact mutation and both outputs (bounded).
Verdicts: differ_on_original | distinguished (agree on the real data, but a plausible database separates them: the
reference and candidate encode different definitions; adjudicate which the question means) | equivalent_on_suite (no
distinguishing database within the budget: test-suite equivalent, a disagreement elsewhere is format only) |
reference_error | candidate_error. bench/adjudicate.py attaches this evidence to every failed item it re-adjudicates.

    .venv/bin/python bench/mutation_audit.py --db <sqlite> --reference bench/sql/C01.sql --candidate cand.sql [--budget 300]
    .venv/bin/python bench/mutation_audit.py --db <warehouse.sqlite3> --errata     (re-adjudicate every errata correction)
Evaluation only: never used to train or tune the engine. No model calls.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import random
import re
import sqlite3
import sys

VERSION = 'bench-mutation-audit/1'
PATIENT_COLUMNS = ('patient_id', 'patient', 'person_id', 'member_id')
SMALL_TABLE = 5000
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}')


def _register(db):
    try:
        if str(Path(__file__).resolve().parents[1]) not in sys.path: sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from clinical_core.agere.clinical.sql_query import register
        register(db)
    except Exception:  # noqa: BLE001 - plain SQLite when the engine's SQL functions are unavailable
        pass
    return db


def reads(db, *sqls):
    """{table: {columns}} read by the queries (SQLite authorizer on prepare)."""
    seen = {}

    def auth(action, a1, a2, *_):
        if action == sqlite3.SQLITE_READ and a1 and not a1.startswith('sqlite_'): seen.setdefault(a1, set()).add(a2)
        return sqlite3.SQLITE_OK
    db.set_authorizer(auth)
    try:
        for sql in sqls:
            try: db.execute('EXPLAIN ' + sql).fetchall()
            except sqlite3.Error: pass
    finally:
        db.set_authorizer(None)
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return {t: c for t, c in seen.items() if t in names}


def _norm(v):
    if isinstance(v, float): return round(v, 6)
    return v


def result(db, sql):
    """('ok', sorted rows) or ('error', message)."""
    try:
        rows = db.execute(sql).fetchmany(20001)
    except sqlite3.Error as exc:
        return 'error', str(exc)[:200]
    return 'ok', sorted((tuple(_norm(v) for v in r) for r in rows), key=lambda r: tuple((x is None, str(x)) for x in r))


def _cols(db, table): return [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]


def sample(src, tables, patients=60, seed=0):
    """In-memory copy of `tables` restricted to a seeded sample of patients (small tables copied whole)."""
    rng = random.Random(seed)
    out = _register(sqlite3.connect(':memory:'))
    pids = set()
    keyed = {}
    for t in tables:
        cols = _cols(src, t)
        key = next((c for c in PATIENT_COLUMNS if c in cols), None)
        keyed[t] = key
        if key: pids |= {r[0] for r in src.execute(f'SELECT DISTINCT "{key}" FROM "{t}" LIMIT 20000')}
    chosen = set(rng.sample(sorted(p for p in pids if p is not None), min(patients, len(pids)))) if pids else set()
    for t in tables:
        ddl = src.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (t,)).fetchone()[0]
        out.execute(ddl)
        cols = _cols(src, t)
        marks = ','.join('?' * len(cols))
        if keyed[t]:
            ids = sorted(chosen)
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                rows = src.execute(f'SELECT * FROM "{t}" WHERE "{keyed[t]}" IN ({",".join("?" * len(chunk))})', chunk).fetchall()
                out.executemany(f'INSERT INTO "{t}" VALUES ({marks})', rows)
        else:
            out.executemany(f'INSERT INTO "{t}" VALUES ({marks})', src.execute(f'SELECT * FROM "{t}" LIMIT {SMALL_TABLE}').fetchall())
    return out, sorted(chosen)


def _shift(value, days):
    try:
        d = dt.date.fromisoformat(str(value)[:10]) + dt.timedelta(days=days)
        return d.isoformat() + str(value)[10:]
    except ValueError:
        return value


def literals(*sqls):
    """Boundary constants the queries compare against: quoted dates/strings and numbers (each with its +-1 neighbours)."""
    dates, nums, words = set(), set(), set()
    for sql in sqls:
        for q in re.findall(r"'([^']{1,40})'", sql):
            if DATE.match(q): dates |= {q, _shift(q, 1), _shift(q, -1)}
            else: words.add(q)
        for n in re.findall(r'(?<![\w.\'])(\d+(?:\.\d+)?)(?![\w.\'-])', re.sub(r"'[^']*'", ' ', sql)):
            v = float(n) if '.' in n else int(n)
            nums |= {v, v + 1, v - 1}
    return sorted(dates), sorted(nums), sorted(words)


def mutations(db, focus, rng, budget, consts=((), (), ())):
    """Yield (description, [(sql, params)]) mutations over the focus columns, round-robin, up to budget."""
    plans = []
    dates, nums, words = consts
    for t, cols in focus.items():
        rowids = [r[0] for r in db.execute(f'SELECT rowid FROM "{t}"')]
        if not rowids: continue
        allcols = _cols(db, t)
        key = next((c for c in PATIENT_COLUMNS if c in allcols), None)
        others = [r[0] for r in db.execute(f'SELECT DISTINCT "{key}" FROM "{t}"')] if key else []
        for _ in range(max(3, budget // max(1, len(focus)))):
            rid = rng.choice(rowids)
            plans.append((f'delete {t} rowid {rid}', [(f'DELETE FROM "{t}" WHERE rowid = ?', (rid,))]))
            plans.append((f'duplicate {t} rowid {rid}', [(f'INSERT INTO "{t}" SELECT * FROM "{t}" WHERE rowid = ?', (rid,))]))
            if key and len(others) > 1:
                plans.append((f'reassign {t} rowid {rid} to another patient',
                              [(f'UPDATE "{t}" SET "{key}" = ? WHERE rowid = ?', (rng.choice(others), rid))]))
            for c in sorted(cols & set(allcols)):
                v = db.execute(f'SELECT "{c}" FROM "{t}" WHERE rowid = ?', (rid,)).fetchone()[0]
                plans.append((f'null {t}.{c} rowid {rid}', [(f'UPDATE "{t}" SET "{c}" = NULL WHERE rowid = ?', (rid,))]))
                for lit in (dates if isinstance(v, str) and DATE.match(v) else nums if isinstance(v, (int, float)) else words):
                    plans.append((f'set {t}.{c} rowid {rid} to query boundary {lit!r}', [(f'UPDATE "{t}" SET "{c}" = ? WHERE rowid = ?', (lit, rid))]))
                if isinstance(v, str) and DATE.match(v):
                    for d in (1, -1, 30, -30, 365, -365):
                        plans.append((f'shift {t}.{c} rowid {rid} by {d} days', [(f'UPDATE "{t}" SET "{c}" = ? WHERE rowid = ?', (_shift(v, d), rid))]))
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    for nv in (v + 1, v - 1, v * 10, 0):
                        plans.append((f'set {t}.{c} rowid {rid} {v} -> {nv}', [(f'UPDATE "{t}" SET "{c}" = ? WHERE rowid = ?', (nv, rid))]))
                other = db.execute(f'SELECT "{c}" FROM "{t}" WHERE "{c}" IS NOT ? ORDER BY random() LIMIT 1', (v,)).fetchone()
                if other is not None:
                    plans.append((f'swap {t}.{c} rowid {rid} {str(v)[:30]!r} -> {str(other[0])[:30]!r}',
                                  [(f'UPDATE "{t}" SET "{c}" = ? WHERE rowid = ?', (other[0], rid))]))
    rng.shuffle(plans)
    return plans[:budget]


def _bounded(rows, n=5): return [list(r) for r in rows[:n]] + ([f'... {len(rows) - n} more rows'] if len(rows) > n else [])


def _diff(a, b):
    from collections import Counter
    ca, cb = Counter(a), Counter(b)
    return sorted((ca - cb).elements(), key=str), sorted((cb - ca).elements(), key=str)


def audit(db_path, reference_sql, candidate_sql, *, budget=300, patients=60, seed=0, keep=3):
    """-> audit record (see module doc). Read-only on db_path; mutations happen on an in-memory sample."""
    src = _register(sqlite3.connect(f'file:{db_path}?mode=ro', uri=True))
    try:
        focus = reads(src, reference_sql, candidate_sql)
        ref0, cand0 = result(src, reference_sql), result(src, candidate_sql)
        record = {'schema': VERSION, 'tables': {t: sorted(c) for t, c in focus.items()}, 'budget': budget, 'seed': seed}
        if ref0[0] == 'error': return {**record, 'verdict': 'reference_error', 'error': ref0[1]}
        if cand0[0] == 'error': return {**record, 'verdict': 'candidate_error', 'error': cand0[1]}
        record['original'] = {'reference_rows': len(ref0[1]), 'candidate_rows': len(cand0[1]), 'equal': ref0[1] == cand0[1]}
        if ref0[1] != cand0[1]:
            only_ref, only_cand = _diff(ref0[1], cand0[1])
            record['original'].update(only_reference=_bounded(only_ref), only_candidate=_bounded(only_cand))
        db, chosen = sample(src, focus, patients, seed)
    finally:
        src.close()
    rng = random.Random(seed)
    base_ref, base_cand = result(db, reference_sql), result(db, candidate_sql)
    record['sample'] = {'patients': len(chosen), 'equal': base_ref == base_cand,
                        'reference_rows': len(base_ref[1]) if base_ref[0] == 'ok' else None}
    found, tried = [], 0
    if base_ref != base_cand:
        a, b = _diff(base_ref[1], base_cand[1]) if base_ref[0] == base_cand[0] == 'ok' else ([], [])
        found.append({'mutation': 'none (sample database)', 'only_reference': _bounded(a), 'only_candidate': _bounded(b)})
    for desc, stmts in mutations(db, focus, rng, budget, literals(reference_sql, candidate_sql)):
        if len(found) >= keep: break
        tried += 1
        db.execute('SAVEPOINT m')
        try:
            for sql, params in stmts: db.execute(sql, params)
            r, c = result(db, reference_sql), result(db, candidate_sql)
            if r != c:
                a, b = _diff(r[1], c[1]) if r[0] == c[0] == 'ok' else ([], [])
                found.append({'mutation': desc, 'only_reference': _bounded(a), 'only_candidate': _bounded(b),
                              **({'error': r[1] if r[0] == 'error' else c[1]} if 'error' in (r[0], c[0]) else {})})
        except sqlite3.Error:
            pass
        finally:
            db.execute('ROLLBACK TO m'); db.execute('RELEASE m')
    db.close()
    record.update(mutants_tried=tried, distinguishing=found)
    record['verdict'] = ('differ_on_original' if not record['original']['equal'] else 'distinguished' if found else 'equivalent_on_suite')
    return record


def summary(record):
    """One line for an adjudication prompt."""
    v = record['verdict']
    if v in ('reference_error', 'candidate_error'): return f"{v}: {record.get('error')}"
    if v == 'equivalent_on_suite':
        return f"equivalent on the real data and on {record['mutants_tried']} mutated databases (test-suite equivalent)"
    first = (record.get('distinguishing') or [{}])[0]
    if v == 'differ_on_original':
        o = record['original']
        return (f"results differ on the real data ({o['reference_rows']} vs {o['candidate_rows']} rows); "
                + (f"also separated by: {first.get('mutation')}" if first else 'no extra mutant needed'))
    return (f"agree on the real data but a mutated database separates them ({first.get('mutation')}): reference-only rows "
            f"{json.dumps(first.get('only_reference'), default=str)[:200]}, candidate-only rows {json.dumps(first.get('only_candidate'), default=str)[:200]}")


def evidence_for(question, candidate_sql, db=None, *, budget=60, root=None):
    """Mutation evidence for one failed benchmark item (bench/adjudicate.py): None when there is no reference SQL, no candidate
    SQL, or the candidate reads a different database than the reference (not comparable on one database)."""
    root = Path(root or Path(__file__).resolve().parent)
    ref = question.get('reference_sql')
    if not ref or not candidate_sql or not (root / ref).exists(): return None
    if db is None:
        from bench.common import DEFAULT_DB as db
    rec = audit(db, (root / ref).read_text(), candidate_sql, budget=budget)
    if rec['verdict'] == 'candidate_error' and 'no such' in str(rec.get('error')): return None
    return {'verdict': rec['verdict'], 'summary': summary(rec), 'mutants_tried': rec.get('mutants_tried'),
            'distinguishing': (rec.get('distinguishing') or [])[:2]}


def audit_errata(db, root=None, budget=150):
    """Re-adjudicate every errata correction (bench/sql_errata/<split>/<ID>__*.sql) against its original reference SQL."""
    root = Path(root or Path(__file__).resolve().parent)
    out = []
    for f in sorted((root / 'sql_errata').glob('*/*.sql')):
        ident = f.name.split('__')[0]
        original = root / 'sql' / f.parent.name / f'{ident}.sql'
        if not original.exists(): continue
        rec = audit(db, original.read_text(), f.read_text(), budget=budget)
        out.append({'id': ident, 'errata': f'{f.parent.name}/{f.name}', 'verdict': rec['verdict'], 'summary': summary(rec)})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--db', type=Path, required=True)
    ap.add_argument('--reference', type=Path)
    ap.add_argument('--candidate', type=Path)
    ap.add_argument('--errata', action='store_true', help='audit every bench/sql_errata correction against its original reference')
    ap.add_argument('--budget', type=int, default=300)
    ap.add_argument('--patients', type=int, default=60)
    a = ap.parse_args(argv)
    if a.errata:
        print(json.dumps(audit_errata(a.db, budget=a.budget), indent=1)); return 0
    print(json.dumps(audit(a.db, a.reference.read_text(), a.candidate.read_text(), budget=a.budget, patients=a.patients), indent=1, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
