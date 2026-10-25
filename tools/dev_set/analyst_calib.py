"""Measure Agere's semantic analyst directly on the DEV population / claims / insight questions (prepared 2026-10-24; NOT run: API credits).

The analyst is the product's own (semantic_analyst.analyze with the decision-context definition views, the numerator / denominator
contract, the arithmetic and the SQL definitions audit), called without the planner, caches or the web service, and scored by the
same scorer as every other dev-set run (tools/dev_set/run.py score()). Haiku first (the product's analyst model); --model changes it,
--definitions off sends the plain documented layer (the competitor shape) for an A/B on the same questions.

    .venv/bin/python tools/dev_set/analyst_calib.py --dry-run                 # lists the questions, makes no call
    .venv/bin/python tools/dev_set/analyst_calib.py --out calib_haiku.json    # Haiku, definitions on (spends credits)
    .venv/bin/python tools/dev_set/analyst_calib.py --definitions off --out calib_haiku_plain.json
    .venv/bin/python tools/dev_set/analyst_calib.py --rescore calib_haiku.json   # re-score a saved run, no calls

Only the `dev` split is ever loaded (held-out lines are skipped before they are parsed). Follow-ups are sent as their resolved
standalone question. Reports per bucket: correct / wrong / declined (the analyst or the definitions audit declined, or no answer) /
errors, accuracy, precision on answered, tool calls and cost; plus how often the audit rewrote or declined and the arithmetic replaced
a stated share.
"""
import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location('dev_run', Path(__file__).with_name('run.py'))
RUN = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RUN)

QUESTIONS = Path(__file__).with_name('questions.jsonl')
SEMANTIC = ROOT / 'artifacts' / 'agere-healthcare-v28-sample-0.30' / 'stack' / 'semantic.sqlite3'
BUCKETS = ('population_clinical_quality', 'population_claims_pharmacy_sdoh', 'insight')
DEFAULT_MODEL = 'claude-haiku-5-5'  # together.DEFAULT_SEMANTIC_MODEL


def load_dev(path=QUESTIONS, buckets=BUCKETS, ids=None, splits=('dev',)):
    """The dev questions of the requested buckets. A held-out line is skipped before it is parsed."""
    out = []
    with open(path) as handle:
        for line in handle:
            if '"split": "heldout"' in line or not line.strip():
                continue
            q = json.loads(line)
            if q.get('split') not in splits or q.get('bucket') not in buckets or (ids and q['id'] not in ids):
                continue
            out.append(q)
    return out


def as_response(result):
    """The analyst's answer in the response shape the dev-set scorer reads (as run_baseline.as_response)."""
    answer = result.get('answer') if isinstance(result.get('answer'), dict) else None
    if answer is None:
        return {'status': 'error', 'error': result.get('error') or 'no answer'}
    answer = dict(answer)
    answer.setdefault('headline', answer.get('value') if isinstance(answer.get('value'), str) else answer.get('answer'))
    return {'answer': answer, 'cost_receipt': {'usd': result.get('estimated_cost_usd'), 'paid_calls': result.get('new_paid_api_calls')}}


def classify(correct, resp):
    """'correct' | 'wrong' | 'declined' | 'error'."""
    status = RUN.status_of(resp)
    if status == 'error':
        return 'error'
    if correct is None or status == 'unsupported':
        return 'declined'
    return 'correct' if correct else 'wrong'


def run_one(q, semantic, send, model, definitions=True, max_tools=8):
    from clinical_core.agere.clinical import semantic_analyst
    text = q['key'].get('standalone') if q.get('follow_up_of') and q['key'].get('standalone') else q['question']
    as_of = str(semantic.as_of)[:10]
    t0 = time.time()
    result = semantic_analyst.analyze(text, as_of, semantic, send=send, model=model, max_tools=max_tools, format_retries=1,
                                      definitions=definitions)
    resp = as_response(result)
    correct, detail = RUN.score(q, resp)
    audit = result.get('definitions_audit') or {}
    return {'id': q['id'], 'bucket': q['bucket'], 'asked': text, 'outcome': classify(correct, resp), 'status': RUN.status_of(resp),
            'correct': correct, 'detail': detail, 'seconds': round(time.time() - t0, 2), 'usd': result.get('estimated_cost_usd'),
            'paid_calls': result.get('new_paid_api_calls'), 'tool_calls': len(result.get('tool_calls') or []), 'error': result.get('error'),
            'audit_action': audit.get('action'), 'audit_reason': audit.get('reason'),
            'arithmetic_replaced': bool((audit.get('arithmetic') or {}).get('replaced')), 'model': result.get('model'),
            'response': resp}  # raw answer, so a key change is re-scored without a paid re-run (--rescore)


def summarize(rows):
    by = {}
    for r in rows:
        by.setdefault(r['bucket'], []).append(r)
    lines = []
    for b, rs in [*by.items(), ('ALL', rows)]:
        n = len(rs)
        c = {k: sum(1 for r in rs if r['outcome'] == k) for k in ('correct', 'wrong', 'declined', 'error')}
        answered = c['correct'] + c['wrong']
        usd = sum(r.get('usd') or 0 for r in rs)
        lines.append(f"{b}: n={n} correct={c['correct']} wrong={c['wrong']} declined={c['declined']} error={c['error']} "
                     f"accuracy={c['correct'] / n:.1%} precision_on_answered={(c['correct'] / answered if answered else 0):.1%} "
                     f"usd={usd:.2f} (${usd / n:.3f}/q) audit_rewritten={sum(1 for r in rs if r.get('audit_action') == 'rewritten')} "
                     f"audit_declined={sum(1 for r in rs if r.get('audit_action') == 'declined')} "
                     f"share_replaced={sum(1 for r in rs if r.get('arithmetic_replaced'))}" if n else f'{b}: n=0')
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--bucket', action='append', choices=BUCKETS)
    ap.add_argument('--ids', nargs='*')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--model', default=DEFAULT_MODEL)
    ap.add_argument('--definitions', choices=('on', 'off'), default='on')
    ap.add_argument('--max-tools', type=int, default=8)
    ap.add_argument('--semantic', type=Path, default=SEMANTIC)
    ap.add_argument('--out', type=Path)
    ap.add_argument('--set', choices=('dev', 'dev3'), default='dev', help='dev3 = tools/dev_set/dev3_questions.jsonl (use --half)')
    ap.add_argument('--half', choices=('build', 'check', 'all'))
    ap.add_argument('--dry-run', action='store_true', help='list the selected dev questions and exit; no call')
    ap.add_argument('--rescore', type=Path, help='re-score a saved run against the current keys; no calls')
    a = ap.parse_args(argv)
    if a.rescore:
        saved = json.loads(a.rescore.read_text())
        keys = {q['id']: q for q in load_dev(buckets=BUCKETS)}
        rows = []
        for r in saved['rows']:
            if 'response' not in r or r['id'] not in keys:
                continue
            correct, detail = RUN.score(keys[r['id']], r['response'])
            rows.append({**r, 'correct': correct, 'detail': detail, 'outcome': classify(correct, r['response'])})
        print('\n'.join(summarize(rows)))
        return rows
    if a.set == 'dev3':
        from tools.dev_set.halves import keep
        qs = keep(load_dev(Path(__file__).with_name('dev3_questions.jsonl'), buckets=tuple(a.bucket or BUCKETS), ids=a.ids, splits=('dev3',)), a.half)[:a.limit]
    else:
        qs = load_dev(buckets=tuple(a.bucket or BUCKETS), ids=a.ids)[:a.limit]
    if a.dry_run:
        for q in qs:
            print(f"{q['id']:>6} {q['bucket'][:30]:30} {q['answer_type']}")
        print(f'{len(qs)} dev questions; model {a.model}; definitions {a.definitions}; no call made')
        return qs
    from bench.adapters import _sender, post_json
    from bench.common import SemanticWarehouse
    semantic = SemanticWarehouse(a.semantic)
    send = _sender(None, a.model, post_json)
    rows = []
    for q in qs:
        row = run_one(q, semantic, send, a.model, definitions=a.definitions == 'on', max_tools=a.max_tools)
        rows.append(row)
        print(f"{q['id']:>6} {q['bucket'][:14]:14} {row['outcome']:9} audit={row['audit_action']} {row['seconds']:.0f}s ${row['usd'] or 0:.3f}", flush=True)
    print('\n=== analyst per bucket ===')
    print('\n'.join(summarize(rows)))
    if a.out:
        a.out.write_text(json.dumps({'model': a.model, 'definitions': a.definitions, 'split': 'dev', 'rows': rows}, indent=1, default=str))
    return rows


if __name__ == '__main__':
    main()
