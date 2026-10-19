"""Build a limited proof brief from existing validated-set scores; never rescore or call a model."""
from datetime import datetime, timezone
import json
from pathlib import Path

from bench.study import analyze, common
from bench.study.validation import require_validated


def main():
    source = common.STUDY_ROOT / 'S1-lean'
    qs = [q for q in common.read_jsonl(source / 'questions.jsonl') if q['set'] == 'locked']
    validation = require_validated(qs)
    ids = [q['id'] for q in qs]
    rows, sources = {}, {}
    for arm in ('A', 'B'):
        file = source / arm / 'p1/score.json'
        scored = json.loads(file.read_text())
        selected = {r['id']: r for r in scored['questions'] if r['set'] == 'locked'}
        if set(selected) != set(ids) or len(selected) != len(ids):
            raise ValueError('Incomplete paired validated-set results')
        rows[arm] = [selected[qid] for qid in ids]
        if any(r.get('cost_usd') is None for r in rows[arm]):
            raise ValueError('Unknown cost: cannot report cost per correct answer')
        sources[arm] = {'path': str(file.relative_to(common.ROOT)), 'sha256': common.sha256_file(file)}
    comparison = analyze.compare(rows['A'], rows['B'], 'A', 'B')
    regression_dir = common.STUDY_ROOT / 'S4-input-contract-regression'
    regression_questions = common.read_jsonl(regression_dir / 'questions.jsonl')
    require_validated(regression_questions)
    regression_file = regression_dir / 'A/p1/score.json'
    regression = json.loads(regression_file.read_text())
    if {r['id'] for r in regression['questions']} != set(ids):
        raise ValueError('Regression set differs from the validated comparison')
    result = {'created_at': datetime.now(timezone.utc).isoformat(), 'status': 'limited_demonstration_not_superiority_proof',
              'analysis_type': 'existing validated Gold 10 subset only; no answers rerun or rescored',
              'reference_validation': validation, 'sources': sources,
              'post_change_regression': {'path': str(regression_file.relative_to(common.ROOT)),
                                         'sha256': common.sha256_file(regression_file),
                                         'summary': regression['summary'],
                                         'role': 'regression only, not an independent comparison'},
              'arms': {arm: {'n': len(rs), 'passes': sum(r['correct'] for r in rs),
                            'api_cost_usd': round(sum(r['cost_usd'] for r in rs), 8),
                            'api_cost_per_pass_usd': sum(r['cost_usd'] for r in rs) / sum(r['correct'] for r in rs)}
                       for arm, rs in rows.items()}, 'comparison': comparison}
    out = common.STUDY_ROOT / 'validated-proof-2026-10-19'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'evidence.json').write_text(json.dumps(result, indent=2) + '\n')
    a, b = result['arms']['A'], result['arms']['B']
    brief = f'''# Agere analytical evidence — October 19, 2026

The validated demonstration is promising, but it does **not yet establish general accuracy superiority**.

| Nine independently reconciled Gold 10 questions | Agere layered stack | Opus text-to-SQL |
|---|---:|---:|
| Questions passing the registered scorer | {a['passes']}/{a['n']} | {b['passes']}/{b['n']} |
| Incremental model API charges | ${a['api_cost_usd']:.2f} | ${b['api_cost_usd']:.2f} |
| Model API dollars per passing answer | ${a['api_cost_per_pass_usd']:.3f} | ${b['api_cost_per_pass_usd']:.3f} |

This is a reanalysis of the saved S1-lean A/B results on the already validated Gold 10 subset. No answers were
rerun or rescored to create this brief. The original question wording, native synthetic population, model settings,
pricing and cold-cache policies are retained in [the source run](../../../artifacts/study/S1-lean/run.json).
The baseline receives semantic documentation and one SQL execution repair. The scorer's patient-list pass threshold
is F1 >= 0.98; it is not an exact-match headline. Gold 10 references were independently reconciled with the generator
before the original run, under digest 05c5b54ba34728cd.

There are two discordant question pairs in Agere's favor and none in Opus's. Exact paired McNemar p =
**{comparison['mcnemar_p']:.2f}**. Nine questions, selected around existing product workflows, do not support a broad
accuracy claim. The API cost advantage is observed on this set; local compute, engineering, validation and operating
cost are unmeasured and must not be described as zero. No percentage cost saving for a customer or general deployment
projection is established.

The [machine-readable calculation](../../../artifacts/study/validated-proof-2026-10-19/evidence.json) pins both
score files by hash and includes paired uncertainty calculations. Original [Agere](../../../artifacts/study/S1-lean/A/p1/score.json)
and [Opus](../../../artifacts/study/S1-lean/B/p1/score.json) files also contain unvalidated native rows; **only their
nine locked-set rows contribute above**. S1 D/E had credit failures and are excluded. Gold-50's 19/41 and 22/41
are not evidence of comparative accuracy while its reference audit remains open.

## Chosen proof route

Validate all Gold-50 references from delivered data and reconcile through the independent generator's scorer, then
rescore the saved A/B answers once on unchanged, validated questions. This avoids paying again for existing outputs
and preserves the unfavorable cases. Disclose every void question and do not silently replace wording. Any follow-up
run of the repaired stack must be explicitly post hoc and use validated references; it cannot be called held-out proof.

[EHRSQL](https://github.com/glee4810/EHRSQL) is a useful later independent hospital-query replication.
[BIRD Mini-Dev](https://github.com/bird-bench/mini_dev) is a useful cross-domain SQL check. Both need a new data/schema
integration and measure a different workload. Choosing either now would add integration and validation work without
establishing performance on Agere's native ACO workflow. This is an engineering judgment, not a measured cost comparison.

## What changed and what remains

The product now accepts complete analytical questions and definitions up to 12,000 characters across planning,
execution and conversation storage. Three saved Gold-50 abstentions reported the old 1,500-character rejection.
The fix is general and verified on independent tiny fixtures; their benchmark correctness has not been retested.

One authorized post-change Gold 10 regression (S4-input-contract-regression) passed
{regression['summary']['correct']}/{regression['summary']['n']} with ${regression['summary']['cost_usd']:.2f}
in model API charges and median latency {regression['summary']['median_seconds']:.2f} seconds.
This checks preservation of existing validated behavior; it is not a fresh held-out comparison or evidence
that the pending Gold-50 failures are corrected.

A bounded Opus review candidate can address analyst implementation failures or unsupported clarification requests.
It executes and checks SQL, preserves genuine missing-input declines, records costs, and requires a quoted definition
to override clarification. It remains **disabled by default**, with no model-quality improvement claimed.

The runner and scorer refuse pending reference sets before further study work. The
[source-audit packet](REFERENCE_AUDIT_2026-10-19.md) contains the requested breakdowns for all five pending disputes.
The independent generator owner must reconcile those findings and reseal corrected references. All 41 new questions
still require per-question independent validation; the five disputes are not the complete validation task.
'''
    target = common.ROOT / 'docs/agere/study/PROOF_STATUS.md'
    target.write_text(brief)
    print(json.dumps({'brief': str(target), 'evidence': str(out / 'evidence.json'), 'mcnemar_p': comparison['mcnemar_p']}))


if __name__ == '__main__':
    main()
