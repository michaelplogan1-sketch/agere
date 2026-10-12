# VBC research and training foundation

**Start with [the research report](RESEARCH_REPORT.md), then [the training and build plan](TRAINING_AND_BUILD_PLAN.md).**

As of October 8, 2026, this package connects public evidence to the existing CareSignal prototype and question registry. It is a scoped research project, not an exhaustive systematic review or a claim of implemented clinical capability.

## Deliverables

| File | Contents |
|---|---|
| [RESEARCH_REPORT.md](RESEARCH_REPORT.md) | Company, payer, CMS, international, QI and adjacent-engineering findings; architecture implications and limitations |
| [TRAINING_AND_BUILD_PLAN.md](TRAINING_AND_BUILD_PLAN.md) | What to retrieve, compile, train and evaluate; synthetic-world expansion and build order |
| [EVIDENCE_NOTES.md](EVIDENCE_NOTES.md) | Source-by-source scope, design lesson and caveat |
| [evidence_catalog.json](evidence_catalog.json) | 89 source targets: 84 scoped reviews and five incomplete leads |
| [cms_inventory.csv](cms_inventory.csv) / [JSON](cms_inventory.json) | 105 directory entries; 40 overviews reviewed; metadata-only coverage explicit for the rest |
| [QUESTIONS.md](QUESTIONS.md) | Readable research question supplement |
| [question_supplement.json](question_supplement.json) / [CSV](question_supplement.csv) | 96 proposed formulations across 16 domains, with data needs, failure cases, operators, reviewers and source references |
| [combined_question_snapshot.json](combined_question_snapshot.json) | Original 140 question rows plus 96 research rows; some are refinements, not distinct new capabilities |
| [synthetic_case_specs.json](synthetic_case_specs.json) | 32 original synthetic acceptance-scenario specifications; expert review and execution pending |
| [coverage_summary.json](coverage_summary.json) | Computed coverage counts and explicit limits |
| [mining_findings.json](mining_findings.json) | Reproducible directory completeness/category checks and timing discrepancies requiring interpretation |
| [validation_result.json](validation_result.json) | Integrity-check results; not clinical validation |
| [directory_provenance.json](directory_provenance.json) | CMS endpoint, retrieval hash and pagination check |
| [retrieval_manifest.json](retrieval_manifest.json) | Raw retrieval history, response metadata and temporary reading-file locations |
| [sources_seed.json](sources_seed.json) | Reproducible public-source targets |

`evidence_catalog.json` is the authority for **what was reviewed**. A successful download in the retrieval manifest is not a claim that the full document was read. Local PubMed challenge pages and other empty shells were not treated as article text; the catalog records where primary abstracts or web-rendered sections supplied the scoped review.

The CMS directory is not the entire CMS regulatory universe. Public payer materials are not signed commercial contracts. Historical and announced models do not establish current patient eligibility. Full technical rule compilation remains future work.

## Relationship to earlier work

- [Existing stack report](/Users/mikel/clinical-insights-local/STACK_BUILD_REPORT.md)
- [Previous engineering research](/Users/mikel/clinical-insights-local/ENGINEERING_RESEARCH.md)
- [Live question registry](/Users/mikel/clinical-insights-local/config/questions.json)
- [Dual-track product plan](/Users/mikel/caresignal-mvp/deliverables/vbc-intelligence/DUAL_TRACK_PLAN.md)

The live registry and product code were not changed by this package. Proposed questions are explicitly unimplemented. All scenario patients and events are synthetic; the research documents themselves are real public sources. No employer data or paid model calls were used.

## Reproduce and check

From `/Users/mikel/clinical-insights-local`:

```sh
python3 research/vbc-landscape/build_library.py
python3 research/vbc-landscape/build_curriculum.py
python3 research/vbc-landscape/validate_research.py
```

The public-source collector is a separate network operation requiring `lxml` and `pypdf`. It reuses recorded retrievals and keeps full extracted reading texts in a temporary directory. The original annotations, metadata, links and CMS inventory are the persistent research deliverables. Builds do not retrain a model or add runtime handlers.
