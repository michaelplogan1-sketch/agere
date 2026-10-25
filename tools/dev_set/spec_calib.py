"""Measure the Haiku spec writer directly (the generalization layer: unfamiliar wording -> the shared typed spec -> the deterministic compiler).

For each question: spec_fill.fill with the Haiku filler only (no grammar, no System One slots), the same validation the spec_path tier uses, then
execution of the compiled contract and the dev-set scorer. Reports, per bucket: valid spec, correct, wrong, invalid (with the validation reason
counted), $ and seconds. Paid (Haiku) when run without --dry-run; development sets only (dev, compose/dev2, dev3 halves) -- never held-out.

    .venv/bin/python -m tools.dev_set.spec_calib --set dev3 --half build --out /tmp/spec_build.json
    .venv/bin/python -m tools.dev_set.spec_calib --set dev3 --half check --out /tmp/spec_check.json   # transfer: measure only, never tune
"""
import argparse
import collections
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.dev_set import run as dev_run  # noqa: E402
from tools.dev_set.run_inprocess import read, STACK  # noqa: E402
from tools.dev_set.halves import keep  # noqa: E402


def reason_of(error):
    """A short class for a validation failure (for the tally)."""
    text = str(error or '')
    for pat, label in ((r'[Uu]nconsumed|not consumed|uncovered', 'unconsumed words'), (r'[Qq]uote', 'quote binding'), (r'[Uu]nknown (?:table|field|column)', 'unknown schema'),
                       (r'literal|number', 'literal check'), (r'window|period', 'window'), (r'decision context', 'decision context'), (r'JSON|decode|Expecting', 'malformed JSON'),
                       (r'out of time|deadline', 'timeout'), (r'[Aa]mbiguous', 'ambiguous'), (r'operation|engine', 'engine/operation')):
        if re.search(pat, text): return label
    return 'other'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--set', default='dev3', choices=('dev', 'compose', 'dev3'))
    ap.add_argument('--half', choices=('build', 'check', 'all'))
    ap.add_argument('--bucket')
    ap.add_argument('--ids', nargs='*')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--out')
    args = ap.parse_args(argv)
    qs = keep(read(args.set), args.half)
    qs = [q for q in qs if (not args.bucket or q['bucket'] == args.bucket) and (not args.ids or q['id'] in args.ids)]
    qs = qs[: args.limit] if args.limit else qs
    os.environ.update(AGERE_EVALUATION='1', AGERE_RESULT_CACHE='off', AGERE_LOCAL_TYPED='off', AGERE_LOCAL_NORMALIZER='off', AGERE_LOCAL_QWEN='off',
                      AGERE_LOCAL_MODEL='off', AGERE_LOCAL_SQL='off', AGERE_BOTTLED='off', AGERE_BOTTLED_COHORT='off')
    from run_integrated_stack import keys
    keys()
    from clinical_core.agere.clinical.stack_runtime import Stack
    from clinical_core.agere.clinical import spec_fill, shared_planner, request_plan, decision_context
    stack = Stack(str(STACK), allow_live=True)
    catalog = request_plan.Catalog.load(stack.path)
    as_of = stack.retriever.meta['snapshot']['as_of']
    rows = []
    for q in qs:
        text = q['key'].get('standalone') if q.get('follow_up_of') and q['key'].get('standalone') else q['question']
        t0 = time.time()
        row = {'id': q['id'], 'bucket': q['bucket'], 'status': None, 'reason': None, 'usd': 0.0}
        try:
            with decision_context.scope(text, as_of):
                filled = spec_fill.fill(stack, catalog, text, fillers=('haiku',), seconds=40.0)
                row['usd'] = (filled.get('receipt') or {}).get('usd', 0.0)
                contract = filled['contract']
                graph = shared_planner.graph_for(contract)
                receipt = {'route': 'shared_planner', 'planner': 'shared_planner', 'request_contract': contract,
                           'interpretation': {'interpreter': 'spec_calib'}, 'new_paid_api_calls': 0, 'known_api_estimate_usd': 0.0, 'api_cost_known': True,
                           'feedback_identity': request_plan.fingerprint([])}
                result = shared_planner.execute_contract(stack, text, graph, receipt)
            correct, detail = dev_run.score(q, result)
            row.update(status='correct' if correct else ('wrong' if correct is False else 'unanswered'), detail=detail,
                       spec=filled.get('plan'))
        except spec_fill.SpecNotFilled as exc:
            row.update(status='invalid', reason=reason_of(exc), error=str(exc)[:400], usd=(getattr(exc, 'receipt', None) or {}).get('usd', 0.0))
        except Exception as exc:  # noqa: BLE001
            row.update(status='error', reason=type(exc).__name__, error=str(exc)[:400])
        row['seconds'] = round(time.time() - t0, 1)
        rows.append(row)
        print(f"{q['id']:>7} {q['bucket'][:16]:16} {row['status']:10} {row.get('reason') or '':18} ${row['usd'] or 0:.4f} {row['seconds']}s", flush=True)
    by = collections.defaultdict(collections.Counter)
    for r in rows:
        by[r['bucket']][r['status']] += 1; by['ALL'][r['status']] += 1
    print('\n=== spec writer (Haiku only) ===')
    for b, c in by.items():
        n = sum(c.values()); valid = c['correct'] + c['wrong'] + c['unanswered']
        print(f"{b:34s} n={n:3d} valid={valid:3d} correct={c['correct']:3d} wrong={c['wrong']:3d} invalid={c['invalid']:3d} error={c['error']:3d}")
    print('invalid reasons:', dict(collections.Counter(r['reason'] for r in rows if r['status'] in ('invalid', 'error'))))
    print(f"usd={sum(r['usd'] or 0 for r in rows):.3f}")
    if args.out: Path(args.out).write_text(json.dumps(rows, indent=1, default=str))


if __name__ == '__main__':
    main()
