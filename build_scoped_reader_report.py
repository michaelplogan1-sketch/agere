"""Summarize the frozen ablation and the explicitly post-hoc hybrid."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
V3 = ROOT/'artifacts/agere-evidence-reader-v3'
HYBRID = ROOT/'artifacts/agere-evidence-reader-v3-hybrid'


def main():
    exposed = json.loads((V3/'exposed-diagnostic.json').read_text())
    fresh = json.loads((V3/'fresh-evaluation.json').read_text())
    run = json.loads((HYBRID/'warehouse/run.json').read_text())
    audit = json.loads((HYBRID/'warehouse/source-audit.json').read_text())
    cost = sum(r.get('cost_estimate', {}).get('estimated_usd', 0) for r in fresh['requests'])
    lines = ['# Target-scoped reader and conservative hybrid', '',
        '**All patient data are synthetic. All outputs remain review proposals.**', '',
        'The new representation separates the target clause, trailing qualifiers, section headings and wider context. '
        'Source offsets are retained, and metadata contradictions between independent heads are flagged.', '',
        'The all-scoped reader improved evidence relations but regressed on assertions. It did not replace all four heads. '
        'The packaged hybrid retains the v2 assertion/subject/time heads unchanged and uses the v3 scoped relation head.', '',
        '## Controlled comparison', '',
        'Four branches were compared: frozen v2 whole-window baseline, whole-window with unrelated-topic augmentation, '
        'scoped representation, and scoped representation with augmentation. All use the same per-axis training labels, '
        'balanced logistic loss and C=4. Augmented copies receive 1/3 sample weight. Feature scales/capacity differ.', '',
        'No new labels were added to training. Scoped-plus-augmentation was declared the primary experimental branch '
        'before evaluation. No foundation model was fine-tuned and Qwen was not used.', '',
        '## New warehouse diagnostic', '',
        'Weights and predictions were frozen before requesting blind Jev/Perplexity labels for 64 new excerpts. '
        'Selection excluded patients and normalized clause/concept groups in the old 64-case diagnostic. '
        'This is still the same synthetic warehouse, with potentially shared semantic templates and selection bias. '
        'Teacher agreement is not clinical truth.', '',
        '| Axis | V2 whole window | Whole + augmentation | Scoped | Scoped + augmentation | Teacher-consensus cases |',
        '|---|---:|---:|---:|---:|---:|']
    for axis, m in fresh['metrics'].items():
        n = m['teacher_consensus_cases']
        counts = [str(m['matches'][v]) for v in ('v2_whole', 'whole_augmented', 'scoped', 'scoped_augmented')]
        lines.append(f"| {axis} | {' | '.join(counts)} | {n} |")
    lines += ['', 'Counts are matches, not percentages. Source: [fresh-evaluation.json](fresh-evaluation.json). '
        'For the primary scoped branch, relation repairs/regressions were '
        f"{len(fresh['metrics']['relation']['primary_transitions']['repaired'])}/"
        f"{len(fresh['metrics']['relation']['primary_transitions']['regressed'])}; assertion repairs/regressions were "
        f"{len(fresh['metrics']['assertion']['primary_transitions']['repaired'])}/"
        f"{len(fresh['metrics']['assertion']['primary_transitions']['regressed'])}.", '',
        'Unrelated-topic augmentation produced no additional match-count gain for the scoped reader on this run. '
        'Do not claim it improved performance.', '',
        '## Previously exposed warehouse diagnostic', '',
        '| Axis | Original v2 | Scoped + augmentation | Agreed cases |', '|---|---:|---:|---:|']
    for axis, m in exposed['metrics'].items():
        lines.append(f"| {axis} | {m['matches']['v2_whole']} | {m['matches']['scoped_augmented']} | {m['teacher_consensus_cases']} |")
    lines += ['', 'Source: [exposed-diagnostic.json](exposed-diagnostic.json). These cases informed the repair hypothesis '
        'and cannot support an independent performance claim.', '',
        '## What was connected', '',
        'The hybrid selection is **post-hoc**: it uses these diagnostics to choose only the relation-head change. '
        'Its independent validation remains unfinished. A parity check confirms unchanged assertion, subject and '
        'time predictions on the diagnostic inputs; the underlying weights are the original v2 weights.', '',
        f"Warehouse execution produced {run['counts']['annotation_bindings']:,} annotations across "
        f"{run['counts']['patients']:,} synthetic patients and {run['counts']['visible_records_processed']:,} visible records "
        f"in {run['elapsed_seconds']:.2f} seconds with {run['new_paid_api_calls']} paid inference calls. "
        f"{audit['exact_source_bindings']:,} exact source bindings passed audit; the audit also checked target containment in the scoped span. "
        f"The {audit['cache_replay_examples']:,}-example cache sample replayed identically with no new predictions.", '',
        'The original v2 artifacts are preserved. The hybrid does not alter clinical workflow results. '
        'The source inspector includes local scope and identifies which axis uses it.', '',
        f"New diagnostic API token-price estimate: ${cost:.6f}; not an invoice. Training and full warehouse inference were local.", '',
        'Start the hybrid inspector:', '', '```sh',
        '.venv/bin/python -m clinical_core.agere serve \\',
        '  --run artifacts/agere-clinical-workflows-v2 \\',
        '  --annotations artifacts/agere-evidence-reader-v3-hybrid/warehouse --port 8881', '```', '',
        'Open `http://127.0.0.1:8881/reader`.', '',
        '## Still failing / next work', '',
        '- Assertion/negation is not solved. Scoped features regressed on coordinated denials and terse problem-list entries; v2 failures also remain.',
        '- Cross-sentence qualifiers, copied parent headings outside the input window, coreference and chronology still need work.',
        '- Add source-style training for coordinated negation, terse lists and signatures, with reviewed labels and fresh independent evaluation.',
        '- The candidate vocabulary remains bounded; this is not general extraction of every healthcare concept.', '',
        'Source integrity and software tests do not prove clinical correctness. No confidence threshold authorizes automatic action.']
    (V3/'README.md').write_text('\n'.join(lines)+'\n')
    (HYBRID/'README.md').write_text('# Scoped relation hybrid\n\nSee [comparison and limitations](../agere-evidence-reader-v3/README.md).\n\n'
        'Only the relation head uses scoped features. Assertion, subject and time preserve v2 weights. '
        'This post-hoc hybrid remains review-only.\n')


if __name__ == '__main__':main()
