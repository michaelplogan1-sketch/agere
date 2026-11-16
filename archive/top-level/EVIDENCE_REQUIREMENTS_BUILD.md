# Agere — source evidence connected to reusable requirements and program work

October 9, 2026. All records and results are synthetic. This delivers an integrated
documentary reasoning layer; it does not complete the entire clinical product.

## What now executes

`evidence_requirements.py` evaluates versioned definitions from
`documentary_contracts.json` against the existing interpretation ledger. Source
mappings are configuration; the evaluator does not contain disease-specific
branches. Each binding declares its concept, relationship, allowed streams,
documentary clock and expected polarity. Boolean all/any/not composition preserves
supported, contradicted and unresolved results. No missing input becomes false.

The `requirements` population-graph operation runs over the complete input set,
persists a source-linked receipt for every patient, and supplies membership to
comparisons, analytics, forecasts, further set composition and program worklists.
The local compiler and frontier capability catalog both expose the operation.
The workbench includes contract examples and patient-level evidence inspection.

Conditional evidence impact evaluates the declared expression with each unresolved
binding set to true and false. Population summaries count where resolving one
input could settle the result. This is prior-free logical sensitivity, not a
calibrated probability, expected clinical benefit, monetary value or optimal
acquisition policy. Multiple unresolved inputs can still be necessary.

`requirement_work.py` carries exact selection receipts into immutable program
assessments, including through intervening set operations. A union cannot pretend
an upstream requirement assessed patients outside that node's input population.
Saving a proposal and progressing work verify that the selection evidence still
matches current source interpretations. Changed reviews invalidate old work
context; a refreshed assessment requires reassessment through the existing program
workflow. Preparing a proposal creates no staff review, enrollment or patient contact.

## Document structure

`source_structure.py` supplies exact-span roles from explicit headings, metadata
fields and signed author blocks. It retains marker text and offsets. Unknown
headings and mixed sections remain explicit; family-history and medication-list
roles alone do not decide clinical meaning.

The existing assertion and interpretation paths attach this context. Signature,
metadata and instruction occurrences are excluded from dated patient-event
accounts. Literal structured observations and clinical text retain their different
authorities. The role inspector is also available on source-reading inspection.

The actual synthetic palliative example contains the selected word in a credentialed
author/specialty line followed by a signature date. The new parser recognises the
signature occurrence; the assertion reconciliation excludes that occurrence from
dated patient accounts. Three earlier saved model readings reference it. Those
outputs, their model inputs and their training/evaluation artifacts remain intact.
This is a downstream structural correction, not improved model accuracy or a
claim that the models were retrained to understand sections.

## Actual warehouse runs

Current completed artifact: `artifacts/agere-evidence-requirements-v3/`.

| Documentary contract | Patients | Supported | Contradicted | Unresolved |
|---|---:|---:|---:|---:|
| Affordability report | 10,000 | 110 | 2,022 | 7,868 |
| Dated nephrology plan and completion accounts | 10,000 | 0 | 0 | 10,000 |
| Medication use and hold report readiness | 10,000 | 0 | 0 | 10,000 |

The affordability result feeds a complete 110-patient program-review proposal.
All 110 immutable assessments carry verified selection context. Unknown and
contradicted patients remain accounted for outside that selected population.
The other contracts require dated or interpreted evidence that the current
bindings do not establish; their unresolved results are not evidence of missing
care. All runs and exact replays used zero new paid API calls. All existing model
weights and the reserved Kev/d1 comparison remain unchanged. No human reviews
were created. Previously computed patient interpretations and contract results
are reused; code identity is computed once per population pass rather than
repeatedly inspecting the same reader code for every patient.

Evidence: `run.json`, `graph-1.json` through `graph-4.json`,
`work-context-verification.json`, `source-role-audit.json`,
`signature-assertion-reconciliation.json`, `implementation-receipt.json` and
`workbench.jpg` in that artifact directory. v1 is the initial integration run;
v2 was interrupted by an interface change during development and is incomplete.
Only v3 records the completed current contract-to-program integration.

## Verification and limits

215 focused checks passed across requirements, program work, source/interpretation
state, assertions, readers, learning, graph composition, planning, context and
clinical prerequisites. They include three-valued truth tables, binding-stream
isolation, missing versus negative evidence, signature/body separation, Unicode
spans, review revocation, per-patient reuse and stale program-context rejection.
JavaScript syntax passed. The live browser verified the new complete-population
result, patient evidence and downstream program proposal. Software checks are not
clinical accuracy evidence.

Clinical state beyond documentary accounts, full clinical/payer definitions,
episode linkage, outcomes/attribution, general causal/predictive adapters and
trained broad interpretation remain unfinished. Next build should add shared
typed outcome/episode inputs and broader source-to-state bindings using this
same graph. These definitions are development review contracts, not an automatic
CMS ingestion system or reviewed clinical eligibility protocols. Portable adapters
and the synthetic-first commercial product direction remain requirements.

```sh
.venv/bin/python build_evidence_requirements.py --output artifacts/NEW_REQUIREMENT_BUILD
```

The server runs at `http://127.0.0.1:8885/stack`. Use **Connect affordability
evidence to program review** to inspect the connected path. Source inspection API:
`/api/stack/requirements?id=RECEIPT_ID`; catalog: `/api/stack/requirement-contracts`.
