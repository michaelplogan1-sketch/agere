"""Bottling step 5: held-out evaluation of a local SQL model, executed READ ONLY against the semantic layer.

For every test question the model writes k candidates (index 0 greedy, the rest sampled, one batch); each is linted,
executed (sqlite mode=ro) and sanity-checked, and its result signature compared with the verified reference answer.
`summary` then scores every acceptance rule (first k' candidates, m agreeing) for precision / coverage.

Usage:
  .venv/bin/python tools/bottling/evaluate.py run <test.jsonl> <stack> <model_dir> <adapter_dir|none> <out.jsonl> [k] [bottled|ladder]
  .venv/bin/python tools/bottling/evaluate.py bench <stack> <model_dir> <adapter_dir|none> <out.jsonl> [k]
  .venv/bin/python tools/bottling/evaluate.py summary <out.jsonl>...
"""
import json, sqlite3, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.clinical import bottled, nl2sql, source_profile


def prompt_for(style, question, profile, as_of):
    """'bottled' = the rung's compact prompt; 'ladder' = the pilot's training prompt (full ladder rules + schema slice)."""
    msgs = bottled.messages(question, profile, as_of)
    if style == 'ladder': msgs[0]['content'] = nl2sql.RULES + nl2sql.VERIFY_RULES + f'\nThe data snapshot is as of {as_of}.'
    return msgs


def stack_facts(stack):
    db = sqlite3.connect(f'file:{Path(stack) / "semantic.sqlite3"}?mode=ro', uri=True)
    try:
        meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
        roster = db.execute('SELECT count(*) FROM patient').fetchone()[0]
    finally:
        db.close()
    return json.loads((Path(stack) / 'run.json').read_text()).get('as_of') or meta.get('as_of') or '2029-08-15', roster


def run(test, stack, model_dir, adapter, out, k=5, style='bottled'):
    profile = source_profile.load(stack)
    as_of, roster = stack_facts(stack)
    t0 = time.perf_counter()
    model = bottled.BottledModel(model_dir, None if adapter == 'none' else adapter)
    print(f'loaded {model.identity} in {time.perf_counter() - t0:.1f}s', flush=True)
    done = {json.loads(l)['question'] for l in open(out)} if Path(out).exists() else set()
    rows = [json.loads(l) for l in open(test) if l.strip()]
    with open(out, 'a') as f:
        for i, r in enumerate(rows):
            if r['question'] in done: continue
            try:  # the verified query re-executed READ ONLY on THIS stack is the reference (v1.23 and v2.0 differ)
                ref = bottled.signature(bottled.execute_ro(stack, r['sql'])[1])
            except ValueError as exc:
                print(f'{i + 1}/{len(rows)} reference query fails on this stack: {exc}', flush=True); continue
            texts, seconds = model(prompt_for(style, r['question'], profile, as_of), k=k)
            start = time.perf_counter()
            cands = [bottled.check(stack, profile, r['question'], nl2sql._extract_sql(t), roster) for t in texts]
            rec = {'question': r['question'], 'model': model.identity, 'prompt': style, 'decode_seconds': round(seconds, 2),
                   'check_seconds': round(time.perf_counter() - start, 2),
                   'candidates': [{'ok': c['ok'], 'rejected': c.get('rejected'), 'signature': c.get('signature'),
                                   'correct': bool(c['ok'] and c['signature'] == ref), 'sql': c['sql']} for c in cands]}
            f.write(json.dumps(rec) + '\n'); f.flush()
            print(f"{i + 1}/{len(rows)} {seconds:.1f}s greedy={'ok' if cands[0]['ok'] else 'rej'} correct={rec['candidates'][0]['correct']}", flush=True)


def _scalar(rows):
    return rows[0][0] if len(rows) == 1 and len(rows[0]) == 1 else None


def truth_match(question, expected, rows):
    """Value-level match against the benchmark reference (column names ignored; same rule for every system)."""
    kind, tol = question['answer_type'], question.get('tolerance', 0)
    if kind == 'unsupported': return False
    if kind in ('count', 'value'):
        v, e = _scalar(rows), expected.get(kind)
        return isinstance(v, (int, float)) and isinstance(e, (int, float)) and abs(v - e) <= max(tol, 1e-6 if kind == 'value' else 0)
    if kind == 'patient_set':
        return bool(rows) and {r[0] for r in rows} == set(expected.get('patient_ids') or [])
    return bottled.signature(rows) == bottled.signature([list(r.values()) for r in expected.get('table') or []])


def ladder_rows(answer):
    kind = answer.get('answer_type')
    if kind in ('count', 'value'): return [[answer.get(kind)]]
    if kind == 'patient_set': return [[i] for i in answer.get('patient_ids') or []]
    if kind == 'table': return [list(r.values()) for r in answer.get('table') or []]
    return []


def bench(stack, model_dir, adapter, out, k=5, results='/Users/mikel/clinical-insights-local/bench/results/agere'):
    """Truth-scored comparison on benchmark questions the hosted ladder answered (latest attempt each). Offline only:
    the runtime rung refuses benchmark questions; none of them (or near-duplicates) are in the training data."""
    from bench.common import load_questions
    qs = {q['id']: q for q in load_questions()}
    latest = {}
    for f in sorted(Path(results).glob('*.jsonl')):
        for line in open(f):
            try: r = json.loads(line)
            except ValueError: continue
            pr = ((r.get('response') or {}).get('planner_receipt') or {})
            if pr.get('route') == 'hosted_verified_sql' and r['id'] in qs and r.get('expected') and r.get('answer') and \
                    (r['id'] not in latest or r['started_at'] > latest[r['id']]['started_at']):
                latest[r['id']] = r
    profile = source_profile.load(stack)
    db = sqlite3.connect(f'file:{Path(stack) / "semantic.sqlite3"}?mode=ro', uri=True)
    roster = db.execute('SELECT count(*) FROM patient').fetchone()[0]; db.close()
    model = bottled.BottledModel(model_dir, None if adapter == 'none' else adapter)
    done = {json.loads(l)['id'] for l in open(out)} if Path(out).exists() else set()
    with open(out, 'a') as f:
        for i, (ident, r) in enumerate(sorted(latest.items())):
            if ident in done: continue
            q = qs[ident]
            texts, seconds = model(bottled.messages(q['question'], profile, q.get('as_of') or '2029-08-15'), k=k)
            cands = [bottled.check(stack, profile, q['question'], nl2sql._extract_sql(t), roster) for t in texts]
            rec = {'id': ident, 'question': q['question'], 'model': model.identity, 'decode_seconds': round(seconds, 2),
                   'ladder_correct': truth_match(q, r['expected'], ladder_rows(r['answer'])),
                   'ladder_agreement': (((r.get('response') or {}).get('planner_receipt') or {}).get('hosted_sql') or {}).get('agreement'),
                   'candidates': [{'ok': c['ok'], 'rejected': c.get('rejected'), 'signature': c.get('signature'),
                                   'correct': bool(c['ok'] and truth_match(q, r['expected'], c['rows'])), 'sql': c['sql']} for c in cands]}
            f.write(json.dumps(rec) + '\n'); f.flush()
            print(f"{i + 1}/{len(latest)} {seconds:.1f}s ladder={rec['ladder_correct']} greedy={rec['candidates'][0]['correct']}", flush=True)


def score(recs, k, m):
    accepted = correct = 0
    for r in recs:
        chosen, _ = bottled.decide([{**c, 'signature': c['signature']} for c in r['candidates'][:k]], m)
        if chosen is not None:
            accepted += 1; correct += chosen['correct']
    return accepted, correct


def summary(files):
    for path in files:
        recs = [json.loads(l) for l in open(path) if l.strip()]
        n = len(recs); kmax = min(len(r['candidates']) for r in recs)
        greedy_ok = sum(r['candidates'][0]['ok'] for r in recs)
        greedy_correct = sum(r['candidates'][0]['correct'] for r in recs)
        lat = sorted(r['decode_seconds'] for r in recs)
        print(f'\n{path} ({recs[0]["model"]}), n={n}, k={kmax}')
        if 'ladder_correct' in recs[0]:
            lc = sum(r['ladder_correct'] for r in recs)
            ps = [r for r in recs if r['ladder_agreement'] == 'primary=second']
            print(f'  hosted ladder truth precision {lc}/{n} = {lc / n:.3f} (primary=second {sum(r["ladder_correct"] for r in ps)}/{len(ps)})')
        print(f'  greedy exact-result accuracy {greedy_correct}/{n} = {greedy_correct / n:.3f}; '
              f'lint/sanity/exec rejection of greedy {n - greedy_ok}/{n} = {(n - greedy_ok) / n:.3f}; '
              f'decode p50 {lat[n // 2]:.1f}s p90 {lat[int(n * .9)]:.1f}s (all {kmax} decodes, one batch)')
        print('  rule (k decodes, m agree) | coverage | precision | accepted correct')
        for k in range(1, kmax + 1):
            for m in sorted({1, (k // 2) + 1, k}):
                if k == 1 and m != 1: continue
                a, c = score(recs, k, m)
                print(f'  k={k} m={m} | {a / n:.3f} ({a}/{n}) | {c / a if a else float("nan"):.3f} | {c}')


if __name__ == '__main__':
    if sys.argv[1] == 'run':
        a = sys.argv[2:]
        run(a[0], a[1], a[2], a[3], a[4], int(a[5]) if len(a) > 5 else 5, a[6] if len(a) > 6 else 'bottled')
    elif sys.argv[1] == 'bench':
        a = sys.argv[2:]
        bench(a[0], a[1], a[2], a[3], int(a[4]) if len(a) > 4 else 5)
    else:
        summary(sys.argv[2:])
