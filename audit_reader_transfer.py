"""Blind teacher comparison on diverse source-bound warehouse annotations.

This is a diagnostic after development, not an independent clinical test.
"""
from pathlib import Path
from collections import defaultdict
import json
import os
import sqlite3
from clinical_core.contracts import digest
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical.evidence_reader import AXES
from clinical_core.agere.clinical.factorized_training import check
from clinical_core.agere.clinical.relation_learning import normalize, write

ROOT = Path(__file__).resolve().parent
OUT = ROOT/'artifacts/agere-evidence-reader-v2'


def main():
    db = sqlite3.connect((OUT/'warehouse/annotations.sqlite3').as_uri()+'?mode=ro', uri=True)
    groups = {}
    try:
        for (payload,) in db.execute('SELECT payload FROM annotations'):
            r = json.loads(payload)
            group = digest([r['input']['concept'], normalize(r['source']['context'])])
            key = digest([r['input'], r['source']])
            if group not in groups or key < groups[group][0]:
                groups[group] = (key, r)
    finally:
        db.close()
    strata = defaultdict(list)
    for key, r in sorted(groups.values(), key=lambda p: p[0]):
        strata[(r['input']['concept'], r['annotation']['axes']['relation']['label'])].append((key, r))
    selected = []
    while len(selected) < 64 and any(strata.values()):
        for name in sorted(strata):
            if strata[name] and len(selected) < 64:
                key, r = strata[name].pop(0)
                selected.append({**r, 'example_id': key})
    write(OUT/'warehouse-transfer-cases.json', selected)
    for line in Path('/Users/mikel/insight-engine/.env').read_text().splitlines():
        if '=' not in line or line.lstrip().startswith('#'):continue
        k, v = line.split('=', 1); k = k.strip().removeprefix('export ')
        if k in {'TYPESAFE_API_KEY', 'PERPLEXITY_API_KEY'}:
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))
    ledger = BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3'); calls = []; result = {}
    try:
        for i in range(0, len(selected), 2):
            for provider in ('jev', 'perplexity'):
                batch, receipt = check(selected[i:i+2], provider, ledger, OUT/'receipts')
                calls.append({'provider': provider, **receipt})
                for r in batch:result.setdefault(r['example_id'], {})[provider] = r
            ledger.settle_completed()
            print(json.dumps({'stage': 'warehouse_transfer', 'examples': len(result)}), flush=True)
        write(OUT/'warehouse-transfer-labels.json', result)
        metrics = {}
        for axis in AXES:
            agreed = [r for r in selected if result[r['example_id']]['jev'][axis]['label'] is not None and
                result[r['example_id']]['jev'][axis]['label'] == result[r['example_id']]['perplexity'][axis]['label']]
            same = sum(r['annotation']['axes'][axis]['label'] == result[r['example_id']]['jev'][axis]['label'] for r in agreed)
            metrics[axis] = {'teacher_agreement_examples': len(agreed), 'local_matches_teacher_consensus': same,
                             'agreement_fraction': same/len(agreed) if agreed else None}
        reviews = [{**r, 'teacher_labels': result[r['example_id']]} for r in selected if any(
            r['annotation']['axes'][a]['label'] != result[r['example_id']]['jev'][a]['label'] or
            r['annotation']['axes'][a]['label'] != result[r['example_id']]['perplexity'][a]['label'] for a in AXES)]
        write(OUT/'warehouse-transfer-review.json', reviews)
        report = {'synthetic': True, 'examples': len(selected), 'metrics': metrics, 'review_candidates': len(reviews),
            'model_identity': selected[0]['annotation']['model_identity'],
            'selection': 'One minimum-hash occurrence per normalized clause/concept, round-robin across concept and predicted-relation strata; not prevalence representative.',
            'status': 'diagnostic_only', 'decision_authority': False,
            'scope': 'Blind machine consensus is not clinical truth. Development warehouse, not an independent outcome test.',
            'requests': calls, 'budget_after': ledger.receipt()}
        write(OUT/'warehouse-transfer.json', report)
        print(json.dumps({'metrics': metrics, 'review_candidates': len(reviews)}, indent=2))
    finally:
        ledger.close()


if __name__ == '__main__':
    main()
