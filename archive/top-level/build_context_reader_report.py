from pathlib import Path
import json
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'artifacts/agere-evidence-reader-v4';QUERIES=ROOT/'artifacts/agere-evidence-queries-v1'
def read(path):return json.loads(path.read_text())
if __name__=='__main__':
 e=read(OUT/'fresh-evaluation.json');run=read(OUT/'warehouse/run.json');audit=read(OUT/'warehouse/source-audit.json');runtime=read(OUT/'runtime-audit.json');queue=read(OUT/'review-batch-summary.json');regression=read(OUT/'authored-regression-diagnostic.json');queries=read(QUERIES/'catalog-results.json');index=read(QUERIES/'run.json')
 table='\n'.join('| '+a+' | '+str(m['teacher_consensus_cases'])+' | '+' | '.join(str(m['matches'][v]) for v in ('v3_hybrid','lexical_retrained','scoped_retrained','typed_context'))+' |' for a,m in e['metrics'].items())
 cost=sum(r['cost_estimate']['estimated_usd'] for r in e['requests'])
 qtable='\n'.join('| '+q['title']+' | '+str(q['result']['patients_matching_evidence_plan'])+' |' for q in queries)
 text=f'''# Agere: context reader, learning queue and evidence workbench

All records are synthetic. This is an implemented research stack, not a clinically validated system.

## What changed

- Trained four context-aware heads for assertion, subject, time and evidence relation. Target-local denial/uncertainty cues, active headings and copy-forward markers retain exact source spans.
- Reused the two earlier warehouse diagnostics as explicitly declared training data. Original authored training examples remain. Per-axis teacher disagreement and eight internally contradictory metadata labels were excluded. These labels have not been human adjudicated.
- Compared the frozen primary against the previous hybrid, a retrained whole-window classifier, and a retrained scoped classifier. No foundation weights were fine-tuned; Qwen was not involved.
- Connected a second local whole-window view for disagreement detection, with explicit review reasons and content-addressed caching. Both views remain unvalidated.
- Added a source-group-deduplicated learning queue using Jensen–Shannon divergence and entropy, with a score-independent audit slice. These are established information-theory tools, not newly invented mathematics.
- Added indexed evidence queries joining structured observations with narrative annotations. Deterministic predicates and caches answer recorded-evidence questions without a paid LLM call for each question.

## Frozen primary / fresh warehouse diagnostic

Weights and predictions were frozen before blind Jev/Perplexity labeling of {e['examples']} new excerpts. Earlier diagnostic patients and normalized target-clause/concept groups were excluded. New texts still share a synthetic generator and warehouse; semantic templates may overlap. Teacher agreement is not clinical truth. Only teacher-agreed cases enter the following denominators; disagreements remain in the review queue.

| Axis | Agreed cases | Previous hybrid | Whole-window retrained | Scoped retrained | Typed context primary |
|---|---:|---:|---:|---:|---:|
{table}

Source: [fresh-evaluation.json](fresh-evaluation.json), [frozen selection](fresh-selection.json), [predictions before labels](fresh-predictions-before-labels.json).

The primary passed its predeclared *review-only connection* condition: no axis lost agreed-case matches against the previous hybrid. This is not a clinical acceptance gate or a statistical superiority test. Source-style training helped substantially. Added cue features have mixed incremental value: the simpler retrained scoped model matched more evidence relations, while the primary matched slightly more subject/time labels. No post-hoc branch switching was performed.

## Robustness and remaining errors

Under {runtime['unrelated_context_mutations']['pairs']} unrelated-context additions, assertion labels changed {runtime['unrelated_context_mutations']['changed_axis_predictions']['v3_hybrid']['assertion']} times for the previous hybrid and {runtime['unrelated_context_mutations']['changed_axis_predictions']['typed_context']['assertion']} times for the primary. The primary still changed time labels {runtime['unrelated_context_mutations']['changed_axis_predictions']['typed_context']['time']} times and relation labels {runtime['unrelated_context_mutations']['changed_axis_predictions']['typed_context']['relation']} times. The simpler retrained scoped model changed none of these tested axes. Stability does not imply correctness. Source: [runtime-audit.json](runtime-audit.json).

Previously exposed authored test examples: {regression['results']['test']['examples']}; primary matches by axis: {json.dumps({a:m['typed_context'] for a,m in regression['results']['test']['matches_authored'].items()})}. These are development regression checks, not new independent evaluation. Source: [authored-regression-diagnostic.json](authored-regression-diagnostic.json).

Still unresolved: omitted parent headings, true observation dates versus note dates, cross-sentence coreference, rare relation classes, temporal sensitivity and shared errors between teachers. Candidate detection still uses a bounded vocabulary. Neither clinical eligibility nor maximum tolerated dose nor comprehensive program understanding follows from these results.

## Full execution and economics

{run['counts']['patients']:,} patients, {run['counts']['visible_records_processed']:,} visible records and {run['counts']['annotation_bindings']:,} annotations. Local two-view inference took {run['elapsed_seconds']:.2f} seconds. {run['new_paid_api_calls']} paid inference calls and {run['clinical_decisions_changed']} clinical workflow decisions changed. This is slower than the earlier single-view hybrid; the extra view adds compute and disagreement visibility, not a measured dollar saving.

All {audit['exact_source_bindings']:,} source bindings and target/cue offsets passed audit. {audit['cache_replay_examples']:,} cached predictions replayed identically. The separate runtime parity check matched all {runtime['runtime_matches_frozen_predictions']} frozen primary predictions. Sources: [warehouse run](warehouse/run.json), [source audit](warehouse/source-audit.json), [runtime audit](runtime-audit.json).

New diagnostic labeling token-price estimate: **${cost:.9f}**, not an invoice. Training, warehouse inference, review selection and evidence querying ran locally. CPU, memory, storage and electricity are not included in this token estimate. No new foundation-model download was needed.

274 software tests passed; [test log](tests-final.log). API status/filtering/source checks and origin restrictions passed; [HTTP receipt](http-verification.json). Browser execution of the nephrology evidence query and expansion of its exact note source worked; no browser warning/error logs were observed. These are engineering checks, not clinical validation.

## Learning queue

The queue scans {queue['annotations_scanned']:,} bindings and excludes groups already used in teacher-labeled diagnostics. It selected {queue['selected']} excerpts from {queue['unique_eligible_groups']:,} eligible unique groups: {queue['selection_counts']['diverse_context_disagreement']} by diverse disagreement ranking and {queue['selection_counts']['score_independent_audit']} by score-independent audit sampling. Maximum two excerpts per patient; repeated numeric/template variants cannot dominate by frequency.

Jensen–Shannon divergence is `H((p+q)/2) - (H(p)+H(q))/2`, using base-two entropy. It measures model disagreement, not clinical risk or error probability. No selected item is automatically a training label. Sources: [review batch](review-batch.json), [summary](review-batch-summary.json).

## Evidence workbench

The new index contains {index['counts']['structured']:,} structured observations and {index['counts']['text']:,} narrative annotations across the same {index['counts']['patients']:,} synthetic patients. A bounded plan combines evidence clauses with all/any and present/not-observed operations; source, concept, context axes and structured scalar values are explicit. No arbitrary SQL or unbounded question answering is accepted.

| Documentation pattern | Patients matching evidence predicates |
|---|---:|
{qtable}

These are documentation patterns, not proven missed care. In particular, a requested outside record does not prove referral or attendance, and no completed referral record does not prove no visit occurred. Historical EF annotations do not establish current HFrEF.

All six SQL results matched an independent Python set scan over the {index['counts']['structured']+index['counts']['text']:,} indexed evidence rows. Query outputs include exact source examples, negative-evidence meanings and snapshot/model identity. Repeated plans use a cache that changes with code, model or source snapshot. Sources: [catalog results](../agere-evidence-queries-v1/catalog-results.json), [execution audit](../agere-evidence-queries-v1/execution-audit.json).

## Open and run

- [Evidence workbench](http://127.0.0.1:8882/explore)
- [Reader and review filters](http://127.0.0.1:8882/reader)

```sh
.venv/bin/python -m clinical_core.agere serve --run artifacts/agere-clinical-workflows-v2 --annotations artifacts/agere-evidence-reader-v4/warehouse --evidence-queries artifacts/agere-evidence-queries-v1 --port 8882
```

Original v2/v3 artifacts and clinical workflow decisions remain preserved. Source-world generation remains owned by the other chat.

## Next engineering work

1. Label/adjudicate the diverse learning queue, preserving provenance and disagreement; evaluate the simpler scoped model against the primary on a new independent source/style set before selecting another runtime.
2. Expand section/copy-forward context and physiological-date handling; support explicit event-date query constraints and distinguish them from source arrival dates.
3. Add a bounded natural-language-to-evidence-plan adapter with explicit unsupported-question behavior and injection/semantic tests. The current workbench uses explicit plans, not a general question-answering agent.
4. Extend concept mappings and separately validate program/measure logic. Neither a new foundation model nor speculative physics is a substitute for that evidence.
'''
 (OUT/'README.md').write_text(text)
 (QUERIES/'README.md').write_text('# Agere evidence queries\n\nSee the [implementation and evaluation report](../agere-evidence-reader-v4/README.md).\n\n[Open the workbench](http://127.0.0.1:8882/explore).\n\nAll records are synthetic. Not observed in the index never proves missing care.\n')
 print(str(OUT/'README.md'))
