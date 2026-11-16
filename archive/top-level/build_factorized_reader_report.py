"""Create a shareable execution receipt from actual training and warehouse runs."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
OUT = ROOT/'artifacts/agere-evidence-reader-v2'


def main():
    card = json.loads((OUT/'model-card.json').read_text())
    warehouse = json.loads((OUT/'warehouse/run.json').read_text())
    executions = [json.loads(x) for x in (OUT/'executions.jsonl').read_text().splitlines()]
    transfer_path=OUT/'warehouse-transfer.json'
    transfer=json.loads(transfer_path.read_text()) if transfer_path.exists() else None
    if transfer:executions.append({'requests':transfer['requests']})
    # Retain original priced receipt across cache replays; estimate each request once.
    unique = {}
    for run in executions:
        for request in run['requests']:
            if request.get('cost_estimate'):
                unique[request['request_hash']] = request
    priced = sum(r['cost_estimate']['estimated_usd'] for r in unique.values())
    leakage = json.loads((OUT/'leakage-audit.json').read_text())
    lines = ['# Agere factorized evidence reader', '',
        '**Executed on synthetic data. Learned annotations are review proposals, not confirmed clinical facts.**', '',
        '## What now runs', '',
        '- Four separate learned decisions per targeted text occurrence: assertion, subject, time and evidence relation.',
        '- Eight trained logistic heads: four lexical and four frozen BioClinical representations. No foundation-model fine-tuning.',
        '- Direct reading of mapped warehouse narrative fields with source-bound windows, preserved OCR text and exact target offsets.',
        '- A content-addressed local prediction cache, queryable annotation database, patient API attachment and source inspector.',
        '- Explicit abstention: annotations do not overwrite clinical workflow facts or authorize patient actions.', '',
        '## Training and evaluation', '',
        f"{card['rows']} authored synthetic examples; splits {json.dumps(card['splits'], sort_keys=True)}. "
        'Six development domains, a calibration domain and a test domain were fixed before generation. '
        'The domains share a generator and assignment recipes, so this is not independent-world validation.', '',
        'Training-only repair examples cover conflicting event types. They were added after inspecting training disagreements; '
        'the held-out text/assignments were preserved. A bare drug fragment had reasonably been classified as a list mention.', '',
        f"{len(leakage['quarantined'])} earlier-split examples quarantined by cross-split text-similarity checks. "
        f"{card['review_candidates']} examples have at least one authored/teacher disagreement; "
        f"{card['invalid_teacher_axes']} provider axes were invalid and excluded from supervision.", '',
        'Each head fits only examples where Claude-authored assignments, blind Jev and blind Perplexity agree on that axis. '
        'That agreement is machine supervision, not clinician review. All intended classes must be represented or fitting stops.', '',
        '| Dimension | Agreed training examples | Lexical test agreement | BioClinical test agreement | Majority control |',
        '|---|---:|---:|---:|---:|']
    for axis, metric in card['metrics'].items():
        cells = [f"{metric['models'][model]['test']['agreement_with_authored']:.1%}" for model in ('lexical', 'bioclinical_frozen', 'majority')]
        lines.append(f"| {axis} | {metric['training_examples']} | {' | '.join(cells)} |")
    lines += ['', 'Scores compare predictions with synthetic authored assignments on the held-out domain. '
        'Full per-class results, teacher-consensus subsets and example predictions are in [model-card.json](model-card.json). '
        'They do not measure clinical accuracy. V1 scores are not directly comparable because the labels and corpus changed.', '',
        '## Warehouse transfer — promotion failed', '',
        'The training-domain scores do not establish warehouse interpretation quality. '
        'A separate blind Jev/Perplexity diagnostic on 64 diverse warehouse excerpts found substantial failures. '
        'Unrelated negation in the wider context can contaminate the target’s label; current versus historical '
        'scope and target-specific relations remain unreliable.', '',
        '| Dimension | Local matches / teacher-consensus cases |', '|---|---:|']
    if transfer:
        for axis,m in transfer['metrics'].items():
            lines.append(f"| {axis} | {m['local_matches_teacher_consensus']}/{m['teacher_agreement_examples']} |")
    lines += ['', 'This sample is selected for diversity, not population prevalence; teacher agreement is not clinical truth. '
        'See [warehouse-transfer.json](warehouse-transfer.json) and [transfer review cases](warehouse-transfer-review.json). '
        '**The reader remains a debugging/annotation component and is not approved to drive care-gap findings.**', '',
        '## Warehouse execution', '',
        f"- {warehouse['counts']['patients']:,} synthetic patients and {warehouse['counts']['visible_records_processed']:,} visible records processed.",
        f"- {warehouse['counts']['annotation_bindings']:,} source-bound text annotations produced.",
        f"- {warehouse['elapsed_seconds']:.2f} seconds for the recorded pass; {warehouse['new_paid_api_calls']} paid API calls.",
        f"- Prediction/cache counts: {json.dumps(warehouse['cache'], sort_keys=True)}.",
        f"- {warehouse['clinical_decisions_changed']} clinical workflow decisions changed.", '',
        'Source: [warehouse/run.json](warehouse/run.json). “Current” means current relative to the source account; '
        'it must still be reconciled with patient chronology. Candidate recognition uses an explicit vocabulary, '
        'so this does not establish recall over all healthcare concepts or arbitrary chart language.', '',
        '## Provider economics', '',
        f"Recorded priced request receipts total ${priced:.6f}, with duplicate cached receipts excluded. "
        'This is a token-price estimate, not an invoice. The timed-out request may have incurred provider cost '
        'without returning usage; its conservative reservation is preserved in the shared ledger.', '',
        'One probability-ranking inconsistency caused the first verification stop. Revalidation quarantined invalid '
        'axes and retained valid axes from the saved response, without repeating that paid request. '
        'A separate generation transport timeout received one explicit replacement request with a distinct audited identity.', '',
        '## Use it', '',
        'Start the local inspector and attach learned annotations to patient detail:', '',
        '```sh', '.venv/bin/python -m clinical_core.agere serve \\',
        '  --run artifacts/agere-clinical-workflows-v2 \\',
        '  --annotations artifacts/agere-evidence-reader-v2/warehouse --port 8880', '```', '',
        'Open `http://127.0.0.1:8880/reader`. Filter by relation, assertion, subject, time or patient ID. '
        '`/api/patient?id=...` includes a paginated learned-annotation preview; `/api/annotations` exposes bounded filtering.', '',
        '## Remaining work', '',
        'Validate source-domain performance against independently reviewed annotations, improve section and temporal '
        'reconciliation, and measure missed concepts. Only then consider a calibrated acceptance policy. '
        'Keep simple controls in mathematical component comparisons. General clinical question-to-plan execution '
        'and broad program/contract reasoning are still separate unfinished layers.', '',
        'Checks and raw records: [tests.log](tests.log), [review-queue.json](review-queue.json), '
        '[leakage-audit.json](leakage-audit.json), [executions.jsonl](executions.jsonl).']
    (OUT/'README.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    main()
