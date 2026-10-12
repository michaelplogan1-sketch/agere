# Offline medication identity — October 9, 2026

The current population graph has a `medication_identity` operator. It resolves
documentary drug names using a pinned local NLM RxNorm subset, preserves unresolved
formulation and displays exact source fields/spans. It consumes a complete selected
population, not retrieval's top matches. It makes no hosted model call.

`build_medication_terminology.py` fetched 15 standard drug-name queries using NLM's
exact lookup (`search=0`) and concept-properties endpoints. Release version was
checked before and after acquisition. Raw responses, URLs and hashes are saved in
`artifacts/agere-medication-terminology-v1`; the attached catalog records release
**05-Oct-2026**, API **3.1.356**. No patient data was sent to NLM. This is a small
subset, not all RxNorm. Unmatched and ambiguous terms remain unresolved.

The operator distinguishes an ingredient/salt name from product strength, release
formulation, route, dose schedule and actual use. Daily dosing or the word
“succinate” alone does not satisfy its literal sustained/extended-release check.
Narrative drug-name matches retain the surrounding text and exact offsets;
historical, negated, copied and other-person mentions remain **name mentions**.
This module does not independently infer their exposure/assertion semantics.
Hidden `original_text` fields from synthetic document generation are not read.

## Complete synthetic run

Source: `artifacts/agere-population-stack-v3/examples/medication-identity-v1/execution.json`.

| Observation | Count |
|---|---:|
| Synthetic patients evaluated | 10,000 |
| Received source records inspected | 313,586 |
| Structured medication-name occurrences | 63,111 |
| Narrative medication-name occurrences | 47,643 |
| Patients with at least one named medication match | 3,779 |
| Patients with unresolved metoprolol-succinate release notation | 2,015 |
| Patients with explicit recorded ARB-class evidence | 822 |
| New paid API calls, including replay | 0 |

Independent raw structured-field counts matched: losartan 13,588; metoprolol
succinate 33,545; metformin 9,966; insulin glargine 6,012. Repeated names are source
occurrences, not independent patients, exposures or doses. Patient flag groups
overlap. Exact source normalization resolved 110,754 occurrences; it did not
establish current treatment in 110,754 independent events.

## Source-bound protocol context, not clinical eligibility

A versioned documentary context references the
[2022 AHA/ACC/HFSA guideline slide set](https://www.heart.org/-/media/832EA0F4E73948848612F228F7FA2D35.pdf),
printed pages 71, 72 and 74. It preserves the named beta-blocker options and
separate source-recorded ACEi/ARB/ARNi categories. It does not infer that every
RAAS-family drug is interchangeable or that the absence of an ARNi is a care gap.
The source `drug_class` field is retained, not independently clinically validated.

This is an engineering source-checked documentary screen, not a complete reviewed
protocol, current guideline compilation, GDMT eligibility decision or maximum
tolerated-dose assessment. Phenotype, date, actual use, formulation, indication,
contraindications, feasibility, tolerance and preferences remain required context.
Current terminology is applied retrospectively to the September 30 synthetic
snapshot; it does not simulate what terminology was available at that earlier time.

## Integration and verification

- Graph validation accepts one complete population input and no invented
  medication-identity parameters. Catalog, module and source identities govern
  result-cache reuse.
- The workbench's “Normalize medication identities locally” example executes the
  operator. Its exact authored question also compiles locally through the default
  economical planning path, including with hosted inference disabled.
- `run_medication_identity.py` executed all selected patients, compared independent
  structured counts, verified no paid calls and replay, and checked the maximum
  bounded ASCII question request remains below the planner's request-size cap.
- `test_medication_identity.py` covers exact/ambiguous identities, no salt or release
  collapse, negated/other-person name mentions, hidden-text exclusion, empty and
  invalid scope, terminology integrity and parameter contracts. The combined
  suite passed 115 checks.

NLM references: [RxNorm overview](https://www.nlm.nih.gov/research/umls/rxnorm/overview.html),
[exact lookup](https://www.lhncbc.nlm.nih.gov/RxNav/APIs/api-RxNorm.findRxcuiByString.html),
[concept properties](https://www.lhncbc.nlm.nih.gov/RxNav/APIs/api-RxNorm.getRxConceptProperties.html),
[release identity](https://www.lhncbc.nlm.nih.gov/RxNav/APIs/api-RxNorm.getRxNormVersion.html).

