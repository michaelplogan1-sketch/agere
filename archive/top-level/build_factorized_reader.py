"""Run reproducible synthetic generation, verification and local reader training."""
from pathlib import Path
import argparse
import json
import os

from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical.factorized_training import FAMILIES, generate, check, fit
from clinical_core.agere.clinical.relation_learning import write

ROOT = Path(__file__).resolve().parent
OUT = ROOT/'artifacts/agere-evidence-reader-v2'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=['generate', 'augment', 'verify', 'fit'])
    p.add_argument('--live', action='store_true')
    p.add_argument('--available', action='store_true', help='Verify completed generation batches while remaining batches are generated')
    p.add_argument('--retry-failed-generation', action='store_true', help='Explicit single replacement for a preserved transport-timeout failure')
    args = p.parse_args()
    OUT.mkdir(parents=True, exist_ok=True); receipts = OUT/'receipts'; receipts.mkdir(exist_ok=True)
    if args.stage == 'fit':
        rows = [json.loads(x) for x in (OUT/'examples.jsonl').read_text().splitlines()]
        for path in sorted(OUT.glob('repair-*.json')):rows.extend(json.loads(path.read_text()))
        card = fit(rows, json.loads((OUT/'labels.json').read_text()), OUT, ROOT)
        print(json.dumps({'stage': 'trained', 'rows': card['rows'], 'review_candidates': card['review_candidates']}), flush=True)
        return
    if not args.live:
        raise ValueError('Paid stages require --live')
    for line in Path('/Users/mikel/insight-engine/.env').read_text().splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        k, v = line.split('=', 1); k = k.strip().removeprefix('export ')
        if k in {'TYPESAFE_API_KEY', 'PERPLEXITY_API_KEY', 'ANTHROPIC_API_KEY'}:
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))
    ledger = BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
    calls = []; before = ledger.receipt(); status = 'running'
    try:
        if args.stage == 'augment':
            for family in range(6):
                batch,receipt=generate(family,0,ledger,receipts,repair=True)
                calls.append(receipt);write(OUT/f'repair-{family}.json',batch);ledger.settle_completed()
                print(json.dumps({'stage':'training_only_repair','families':family+1}),flush=True)
        elif args.stage == 'generate':
            rows = []
            for family in range(len(FAMILIES)):
                for start in range(0, 32, 8):
                    batch, receipt = generate(family, start, ledger, receipts, retry_transport=args.retry_failed_generation)
                    rows += batch; calls.append(receipt)
                    write(OUT/f'generated-{family}-{start}.json', batch)
                    ledger.settle_completed()
                    print(json.dumps({'stage': 'generated', 'examples': len(rows), 'total': len(FAMILIES)*32}), flush=True)
            (OUT/'examples.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        else:
            if args.available:
                rows=[]
                for family in range(len(FAMILIES)):
                    for start in range(0,32,8):
                        path=OUT/f'generated-{family}-{start}.json'
                        if path.exists():rows.extend(json.loads(path.read_text()))
                if not rows:raise ValueError('No completed batches available')
            else:
                rows = [json.loads(x) for x in (OUT/'examples.jsonl').read_text().splitlines()]
            for path in sorted(OUT.glob('repair-*.json')):rows.extend(json.loads(path.read_text()))
            result = {}
            for i in range(0, len(rows), 2):
                for provider in ('jev', 'perplexity'):
                    batch, receipt = check(rows[i:i+2], provider, ledger, receipts)
                    calls.append({'provider': provider, **receipt})
                    for r in batch:
                        result.setdefault(r['example_id'], {})[provider] = r
                    write(OUT/'labels.partial.json', result)
                ledger.settle_completed()
                print(json.dumps({'stage': 'verified', 'examples': len(result), 'total': len(rows)}), flush=True)
            if not args.available:write(OUT/'labels.json', result)
        status = 'complete'
    finally:
        receipt = {'stage': args.stage, 'status': status, 'synthetic': True, 'budget_before': before,
                   'budget_after': ledger.receipt(), 'requests': calls}
        # Preserve each invocation instead of overwriting the first paid run.
        with (OUT/'executions.jsonl').open('a') as stream:
            stream.write(json.dumps(receipt)+'\n')
        ledger.close()


if __name__ == '__main__':
    main()
