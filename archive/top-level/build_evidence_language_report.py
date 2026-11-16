from pathlib import Path
import json
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'artifacts/agere-evidence-language-v1'
if __name__=='__main__':
 s=json.loads((OUT/'summary.json').read_text());answers=json.loads((OUT/'warehouse-answers.json').read_text())
 table='\n'.join('| '+r['question']+' | '+(str(r['result']['patients_matching_evidence_plan']) if r['result'] else 'Not executed: beyond snapshot')+' |' for r in answers)
 text=f'''# Agere: question-to-evidence compiler and source-date queries

All patient records are synthetic. The evidence reader remains unvalidated for clinical decisions. This update changes querying, not the trained reader weights or clinical workflow results.

## Working flow

The workbench now accepts supported plain-English evidence questions, compiles every clause into an explicit plan, displays a readable interpretation, and executes that plan against the existing structured/narrative evidence index. The planner is a deterministic controlled-language compiler, not a newly trained LLM or an unrestricted medical agent.

- Supported concepts include heart failure, CKD, dyspnea, edema, EF, financial hardship, outside records, nephrology and bounded medication classes.
- Narrative mentions, affirmed/denied/uncertain/retracted statements and current/historical/planned annotations remain different predicates.
- Structured orders, pharmacy claim transactions, administration and medication history remain different record types. A pharmacy claim is not necessarily a fill, and an order is not proof of taking medication.
- All/any combinations and missing indexed evidence are supported. Mixed AND/OR without explicit grouping is rejected.
- Source event dates can be constrained with explicit ISO date ranges. Arrival dates and physiological event dates are not substituted. Requests beyond the warehouse cutoff are rejected instead of returning a misleading empty cohort.
- Every clause must parse completely. Age, causal, eligibility, treatment, unimplemented time or other extra constraints prevent execution; no partial plan is silently substituted.
- A drug-specific request is not broadened to its medication class. “No dyspnea in notes” is ambiguous and rejected; “denied dyspnea in notes” and “no notes mentioning dyspnea” compile to different evidence predicates.
- Chart contents are displayed as source data and never fed back as planner instructions. No SQL or code is taken from chart text or question text.

## Actual warehouse answers

| Question | Synthetic patients matching evidence predicates |
|---|---:|
{table}

Source: [warehouse-answers.json](warehouse-answers.json). Counts describe recorded evidence, not confirmed prevalence, missed care, adherence, eligibility or treatment need. Date-window cohorts can overlap; their sizes do not establish an incidence or outcome trend. The beta-blocker result means an order record exists and a claim record does not match—it is not a list of patients who failed to fill or take a prescription.

## Verification

- {s['positive_plan_checks']} developer-authored positive cases matched their expected explicit plans.
- {s['negative_no_execution_checks']} unsupported or misleading requests produced no executable partial plan.
- {s['warehouse_requests']} warehouse requests ran through the question API logic, including one out-of-snapshot date rejection.
- {s['date_audit_queries']} date-constrained queries matched an independent Python set scan.
- 294 software tests passed. No trained reader weights changed.
- HTTP checks covered results, interpretation, unsupported qualifiers, date boundaries, input bounds and origin restrictions.
- Browser checks confirmed a natural-language order/claim query returned 340 matches and displayed both interpreted conditions; an age-qualified unsupported question then cleared the old results and did not execute. No browser warning/error logs were observed.

Sources: [acceptance cases](acceptance-cases.json), [date audit](date-query-audit.json), [test log](tests.log), [HTTP verification](http-verification.json). These are engineering acceptance tests, not independent natural-language benchmark accuracy or clinical validation.

## What is not implemented

This compiler did not fully compile any of the {s['curriculum_questions']} broad questions in `config/questions.json`; [coverage audit](curriculum-coverage.json). Those questions require broader clinical criteria, causality, program logic, provider attribution, risk adjustment or additional data/operators. Existing three declared clinical review workflows are separate. Keyword relevance, a related workflow, or a partially matching evidence cohort is not a full answer.

The current language grammar is deliberately limited and will reject some valid paraphrases. It has not been evaluated for general clinician-language coverage. The explicit plan API supports only bounded evidence operations; no unconstrained SQL generator or clinical action agent is connected.

## Economics and reproducibility

{ s['new_paid_api_calls'] } paid model calls for this update. Question parsing and SQL execution are local; repeated equivalent plans reuse the existing source/model/code-versioned result cache. Local CPU/storage still have costs. The new plan fields reuse exact source dates in the immutable evidence payloads, so the existing warehouse/index need not be rebuilt.

## Open and run

[Question workbench](http://127.0.0.1:8883/explore) · [Reader inspector](http://127.0.0.1:8883/reader)

```sh
.venv/bin/python -m clinical_core.agere serve --run artifacts/agere-clinical-workflows-v2 --annotations artifacts/agere-evidence-reader-v4/warehouse --evidence-queries artifacts/agere-evidence-queries-v1 --port 8883
```

New module: `clinical_core/agere/clinical/evidence_language.py`. Updated evidence predicates, service endpoint `/api/evidence-ask`, and question workbench. Tests: `test_evidence_language.py`, `test_evidence_dates.py`. Reproduce the artifact checks with `evaluate_evidence_language.py`; this does not call paid APIs.

## Next implementation priorities

1. Broader question planning with explicit coverage of every requested constraint, evaluated independently of the planner that produces proposals. Unverified interpretations must remain proposals.
2. Add missing operators and data bindings for prioritized catalog questions rather than declaring them answered by matching a few words.
3. Continue reader source/style transfer evaluation and physiological-date reconciliation. Source-date query support does not solve copied-history dating.
'''
 (OUT/'README.md').write_text(text);print(str(OUT/'README.md'))
