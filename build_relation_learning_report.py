"""Render only recorded synthetic training receipts; no model calls."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
OUT = ROOT/'artifacts/agere-relation-student-v1'


def main():
    dataset = json.loads((OUT/'dataset.json').read_text())
    card = json.loads((OUT/'model-card.json').read_text())
    execution = json.loads((OUT/'execution.json').read_text())
    labels = json.loads((OUT/'labels.json').read_text())
    cost = sum(r.get('cost_estimate', {}).get('estimated_usd', 0) for r in execution['request_receipts'])
    lines = ['# Source-bound relation-learning pilot', '',
        '**All records are synthetic. No reader was promoted and no clinical workflow decisions changed.**', '',
        'The pipeline now prepares source-verified wider excerpts, labels them with two blind typed models, '
        'and trains both a lexical reader and frozen BioClinical ModernBERT classification heads. '
        'It exposed inadequate training coverage and ambiguous machine supervision; it did not establish a better reader.', '',
        '## Dataset', '',
        f"{dataset['source_bindings']:,} source bindings → {dataset['unique_clause_inputs']:,} distinct clause inputs "
        f"→ {dataset['template_groups']} heuristic near-duplicate groups → {dataset['selected_examples']} selected examples.", '',
        f"Frozen split: {dataset['splits']['train']} training, {dataset['splits']['calibration']} calibration, "
        f"{dataset['splits']['test']} test examples. One representative per group. Source: [dataset.json](dataset.json).", '',
        'All selected target quotes and wider context windows were checked against the visible warehouse snapshot '
        'and its preserved OCR extraction. Existing rules still decide which mentions enter this dataset. '
        'The grouping heuristic does not guarantee semantic independence, and this is not a patient- or world-held-out evaluation.', '',
        '## Blind machine supervision', '',
        '| Axis | Jev/Perplexity agreement | Training examples after agreement filter |',
        '|---|---:|---:|']
    for axis, report in card['axes'].items():
        n = sum(r['jev'][axis]['label'] == r['perplexity'][axis]['label'] for r in labels.values())
        lines.append(f"| {axis} | {n}/{len(labels)} | {report['fit_examples']} |")
    lines += ['', f"{card['review_candidates']} excerpts have at least one disagreement; see [review queue](review-queue.json). "
        'These are machine labels, not clinician-reviewed truth. Teachers received identical bounded inputs without '
        'patient identifiers, prior rule labels, student predictions, split assignments or each other’s answers.', '',
        '## Held-out test results', '',
        'Agreement below uses only test examples where both teachers agreed. '
        'The full receipt also reports agreement against each teacher on all test examples. '
        'Scores do not measure clinical correctness.', '',
        '| Axis | Lexical + logistic | Frozen BioClinical + logistic | Training-majority control | Agreed test examples |',
        '|---|---:|---:|---:|---:|']
    for axis, report in card['axes'].items():
        models = report['models']
        cells = [f"{models[m]['test']['agreement_on_teacher_agreed']:.1%}" for m in ('lexical', 'bioclinical_frozen', 'training_majority_control')]
        n = models['lexical']['test']['teacher_agreed_examples']
        lines.append(f"| {axis} | {' | '.join(cells)} | {n} |")
    lines += ['', 'Source: [model-card.json](model-card.json). The neural and lexical heads use identical inputs, '
        'training rows, splits, balanced loss and C=1. Their representations have different scale and capacity; '
        'neither was tuned on test data. The majority control is a diagnostic added to the same frozen results, '
        'not a clinical reader. The foundation encoder weights were not updated.', '',
        '## Training gaps', '']
    for axis, report in card['axes'].items():
        lines.append(f"- {axis}: no agreed training examples for " + ', '.join(report['missing_training_classes']) + '.')
    lines += ['', 'Examples worth reviewing include a prior-history CKD statement classified as affirmed versus historical; '
        'a copied medication list with current use explicitly unverified; and “Palliative Medicine” in a signature '
        'being interpreted as a care plan versus unknown. These expose temporal-context and section-role problems. '
        'Do not automatically promote either teacher’s interpretation.', '',
        '## Compute and verification', '',
        f"- {len(execution['request_receipts'])} provider request receipts; token-price estimate ${cost:.9f}. This is not an invoice.",
        f"- Latest encoder run: {card['encoder']['stats']['texts_encoded']} inputs encoded, {card['encoder']['stats']['cache_hits']} cache hits; "
        'subsequent fit runs reuse content-addressed vectors. Current-run cache statistics are in the model card.',
        '- Regression results: [tests.log](tests.log). Tests check source offsets, synthetic-only provider calls, '
        'blinding, probability contracts, leakage rejection and existing clinical/context behavior.',
        '- Shared ledger, raw provider responses, exact input and label hashes are retained. Failed calls do not silently retry.', '',
        '## Next work', '',
        'Use the training/calibration failure categories to create a new, more varied dataset with all intended classes '
        'represented and clearer temporal/section definitions. Preserve this frozen test as an explored diagnostic; '
        'use new untouched patients, source conditions and language templates for subsequent claims. '
        'Measure missing-mention recall and decision preservation before adding either reader to a clinical workflow.', '',
        'Commands: `train_warehouse_relation_student.py prepare`, `label --live`, `fit`; use a fresh output directory '
        'for a new dataset. Render this report with `build_relation_learning_report.py`.']
    (OUT/'README.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    main()
