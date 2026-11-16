"""Compare frozen readers on exposed cases and a fresh source-bound sample."""
from pathlib import Path
from collections import defaultdict
import argparse
import hashlib
import json
import os
import sqlite3
import joblib
from threadpoolctl import threadpool_limits
from clinical_core.contracts import digest
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical.evidence_reader import AXES, input_feature
from clinical_core.agere.clinical.factorized_training import check
from clinical_core.agere.clinical.relation_learning import normalize, write

ROOT = Path(__file__).resolve().parent
OLD = ROOT/'artifacts/agere-evidence-reader-v2'
OUT = ROOT/'artifacts/agere-evidence-reader-v3'
VARIANTS = ('v2_whole', 'whole_augmented', 'scoped', 'scoped_augmented')


def group(r):return digest([r['input']['concept'], normalize(r['source']['context'])])


def fresh_cases(old_cases):
    forbidden = {group(r) for r in old_cases}; patients = {r['patient_id'] for r in old_cases}
    grouped = {}; db = sqlite3.connect((OLD/'warehouse/annotations.sqlite3').as_uri()+'?mode=ro', uri=True)
    try:
        for (payload,) in db.execute('SELECT payload FROM annotations'):
            r = json.loads(payload); g = group(r)
            if g in forbidden or r['patient_id'] in patients:continue
            key = digest(['scoped-fresh/1', r['input'], r['source']])
            if g not in grouped or key < grouped[g][0]:grouped[g] = (key, r)
    finally:db.close()
    strata = defaultdict(list)
    for key, r in sorted(grouped.values(), key=lambda v: v[0]):strata[r['input']['concept']].append((key, r))
    rows = []
    while len(rows) < 64 and any(strata.values()):
        for concept in sorted(strata):
            if strata[concept] and len(rows) < 64:
                key, r = strata[concept].pop(0); rows.append({**r, 'example_id': key})
    return rows


def predictions(rows):
    inputs = [r['input'] for r in rows]; result = {v: {r['example_id']: {} for r in rows} for v in VARIANTS}
    with threadpool_limits(limits=4):
        for a in AXES:
            for v in VARIANTS:
                path = OLD/f'{a}-lexical.joblib' if v == 'v2_whole' else OUT/f'{a}-{v}.joblib'
                model = joblib.load(path)
                x = inputs if v.startswith('scoped') else [input_feature(x) for x in inputs]
                for r, p in zip(rows, model.predict(x)):result[v][r['example_id']][a] = str(p)
    return result


def score(rows, labels, preds):
    metrics = {}
    for a in AXES:
        agreed = [r for r in rows if labels[r['example_id']]['jev'][a]['label'] is not None and
            labels[r['example_id']]['jev'][a]['label'] == labels[r['example_id']]['perplexity'][a]['label']]
        counts = {v: sum(preds[v][r['example_id']][a] == labels[r['example_id']]['jev'][a]['label'] for r in agreed) for v in VARIANTS}
        transitions = {'repaired': [], 'regressed': [], 'still_wrong': []}
        for r in agreed:
            rid = r['example_id']; truth = labels[rid]['jev'][a]['label']
            old, new = preds['v2_whole'][rid][a] == truth, preds['scoped_augmented'][rid][a] == truth
            if not old and new:transitions['repaired'].append(rid)
            if old and not new:transitions['regressed'].append(rid)
            if not old and not new:transitions['still_wrong'].append(rid)
        metrics[a] = {'teacher_consensus_cases': len(agreed), 'matches': counts, 'primary_transitions': transitions}
    return metrics


def main():
    p = argparse.ArgumentParser(); p.add_argument('--live', action='store_true'); args = p.parse_args()
    if not args.live:raise ValueError('Fresh teacher labeling requires --live')
    card_hash = hashlib.sha256((OUT/'model-card.json').read_bytes()).hexdigest()
    old_rows = json.loads((OLD/'warehouse-transfer-cases.json').read_text())
    old_labels = json.loads((OLD/'warehouse-transfer-labels.json').read_text())
    old_predictions = predictions(old_rows)
    write(OUT/'exposed-diagnostic.json', {'status': 'previously_exposed_diagnostic', 'metrics': score(old_rows, old_labels, old_predictions), 'predictions': old_predictions})
    path = OUT/'fresh-cases.json'
    if path.exists():
        manifest = json.loads((OUT/'fresh-selection.json').read_text())
        if manifest['model_card_sha256'] != card_hash:raise ValueError('Weights changed after fresh set selected')
        rows = json.loads(path.read_text())
    else:
        rows = fresh_cases(old_rows); write(path, rows)
        write(OUT/'fresh-selection.json', {'synthetic': True, 'model_card_sha256': card_hash, 'cases_digest': digest(rows),
            'examples': len(rows), 'selection': 'Weights frozen first. Exclude prior diagnostic patients and normalized target-clause/concept groups. Minimum hash then concept round-robin.',
            'limitations': 'Same synthetic warehouse and generator; not independent-world or clinical validation. Heuristic group exclusion cannot ensure unseen semantic templates.'})
    pred = predictions(rows); write(OUT/'fresh-predictions-before-labels.json', pred)
    for line in Path('/Users/mikel/insight-engine/.env').read_text().splitlines():
        if '=' not in line or line.lstrip().startswith('#'):continue
        k, v = line.split('=', 1); k = k.strip().removeprefix('export ')
        if k in {'TYPESAFE_API_KEY', 'PERPLEXITY_API_KEY'}:os.environ.setdefault(k, v.strip().strip('"').strip("'"))
    receipts = OUT/'receipts'; receipts.mkdir(exist_ok=True)
    ledger = BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3'); labels = {}; calls = []
    try:
        for i in range(0, len(rows), 2):
            for provider in ('jev', 'perplexity'):
                answers, receipt = check(rows[i:i+2], provider, ledger, receipts)
                calls.append({'provider': provider, **receipt})
                for r in answers:labels.setdefault(r['example_id'], {})[provider] = r
            ledger.settle_completed(); write(OUT/'fresh-labels.partial.json', labels)
            print(json.dumps({'fresh_examples_checked': len(labels), 'total': len(rows)}), flush=True)
        write(OUT/'fresh-labels.json', labels)
        metrics = score(rows, labels, pred)
        write(OUT/'fresh-evaluation.json', {'synthetic': True, 'examples': len(rows), 'metrics': metrics,
            'model_card_sha256': card_hash, 'automatic_clinical_promotion': False,
            'scope': 'Fresh source-group diagnostic against two machine teachers; no clinical correctness claim', 'requests': calls,
            'budget_after': ledger.receipt()})
        write(OUT/'fresh-review-queue.json', [{**r, 'teachers': labels[r['example_id']],
            'readers': {v: pred[v][r['example_id']] for v in VARIANTS}} for r in rows if any(
                pred['scoped_augmented'][r['example_id']][a] != labels[r['example_id']]['jev'][a]['label'] or
                pred['scoped_augmented'][r['example_id']][a] != labels[r['example_id']]['perplexity'][a]['label'] for a in AXES)])
        print(json.dumps({'metrics': metrics}, indent=2))
    finally:ledger.close()


if __name__ == '__main__':main()
