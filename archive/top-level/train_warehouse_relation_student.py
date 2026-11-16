"""Prepare, blind-label, and fit relation specialists on synthetic source excerpts.

Paid calls require --live. Completed calls replay through the shared ledger;
failed attempts never silently retry. Dataset and labels remain inspectable.
"""
from pathlib import Path
import argparse
import json
import os

from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical.relation_learning import prepare, label_batch, fit, write

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['prepare', 'label', 'fit'])
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/agere-relation-student-v1')
    parser.add_argument('--limit', type=int, default=180)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--lexical-only', action='store_true')
    args = parser.parse_args()
    out = args.output
    if args.stage == 'prepare':
        if (out/'dataset.json').exists():
            raise ValueError('Use a fresh output directory for a changed dataset')
        print(json.dumps(prepare(ROOT, out, args.limit), indent=2))
        return
    rows = [json.loads(line) for line in (out/'examples.jsonl').read_text().splitlines()]
    if args.stage == 'fit':
        labels = json.loads((out/'labels.json').read_text())
        card = fit(rows, labels, out, ROOT, neural=not args.lexical_only)
        print(json.dumps({'stage': 'fit_complete', 'review_candidates': card['review_candidates'],
                          'axes': {k: {'fit_examples': v['fit_examples'], 'missing_training_classes': v['missing_training_classes']} for k, v in card['axes'].items()}}))
        return
    if not args.live:
        raise ValueError('Labeling requires explicit --live')
    for line in Path('/Users/mikel/insight-engine/.env').read_text().splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        k, v = line.split('=', 1)
        k = k.strip().removeprefix('export ')
        if k in {'TYPESAFE_API_KEY', 'PERPLEXITY_API_KEY'}:
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))
    receipts = out/'receipts'
    receipts.mkdir(exist_ok=True)
    ledger = BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
    before = ledger.receipt()
    calls = []
    results = {}
    try:
        # Four excerpts / eight typed decisions keep each request under 20KB.
        for i in range(0, len(rows), 4):
            for provider in ('jev', 'perplexity'):
                labels, receipt = label_batch(rows[i:i+4], provider, ledger, receipts)
                calls.append({'provider': provider, **receipt})
                for label in labels:
                    results.setdefault(label['example_id'], {})[provider] = label
                write(out/'labels.partial.json', results)
            ledger.settle_completed()
            write(out/'execution.json', {'synthetic': True, 'status': 'labeling', 'completed_examples': len(results),
                'budget_before': before, 'budget_after': ledger.receipt(), 'request_receipts': calls})
            print(json.dumps({'stage': 'labeled', 'examples': len(results), 'total': len(rows)}), flush=True)
        write(out/'labels.json', results)
        write(out/'execution.json', {'synthetic': True, 'status': 'labels_complete', 'completed_examples': len(results),
            'budget_before': before, 'budget_after': ledger.receipt(), 'request_receipts': calls})
    except Exception as exc:
        write(out/'execution.json', {'synthetic': True, 'status': 'labeling_failed', 'completed_provider_results': sum(len(x) for x in results.values()),
            'error_type': type(exc).__name__, 'budget_before': before, 'budget_after': ledger.receipt(), 'request_receipts': calls})
        raise
    finally:
        ledger.close()


if __name__ == '__main__':
    main()
