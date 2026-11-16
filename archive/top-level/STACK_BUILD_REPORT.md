# Stack expansion — October 8, 2026

Implemented and tested on synthetic data. This personal-laptop stack does not depend on employer systems.

## Corpus now available

- 1,614 source-library rows; 1,612 runnable source families.
- 4,836 generated patient IDs across 13 existing worlds.
- 62,868 patient–world instances and 366,182 note–world instances processed by the local baseline.
- Repeated worlds and jittered clones are not independent patients. Note counts include copy-forward content.
- Source hashes, source-family mappings and prospective partitions are in `artifacts/corpus/manifest.json` and `partitions.json`.
- Inference reads feeds only. Full-world oracle scoring runs separately. No training was performed.
- All data can be used across development and evaluation; reserved test families and evaluation worlds must not become training examples.
- The complete source/world files were present but Git-ignored; the earlier file listing missed them.

## Question coverage

`config/questions.json` preserves the original 76 questions and adds 64, for 140 total. The readable list is `QUESTION_REGISTRY.md`.

| Coverage | Questions | Meaning |
|---|---:|---|
| Implemented | 6 | Feed counts, note repetition, event cutoff, lab coverage, quote locations, reader review states |
| Partial | 6 | Narrow HFrEF class-gap, treatment-barrier, outside-supply, documentation and discovery analyses |
| Not implemented | 128 | Preserved specifications with data requirements and review ownership |

Partial HFrEF support is the existing synthetic medication-class heuristic, not full GDMT or maximal tolerated dosing. SNF rankings, causal admission explanations, renal trajectories and real program eligibility are not represented as implemented. The API states that limitation.

## Full-world baseline rerun

These results use the existing lexicon/rules engine, not the new hosted readers. Re-running historical synthetic worlds is not independent clinical validation.

| World | Patients | Notes | Materialized patterns found | Card precision | Worklist precision |
|---|---:|---:|---:|---:|---:|
| dev | 4836 | 27864 | 9/9 | 0.957 | 0.918 |
| eval2 | 4836 | 27804 | 9/9 | 1.0 | 0.912 |
| eval3 | 4836 | 28010 | 9/9 | 1.0 | 0.919 |
| eval4 | 4836 | 27646 | 9/9 | 1.0 | 0.925 |
| eval5 | 4836 | 27694 | 9/9 | 0.952 | 0.917 |
| eval6 | 4836 | 27719 | 9/9 | 1.0 | 0.924 |
| eval7_llm | 4836 | 27864 | 9/9 | 1.0 | 0.908 |
| hard12 | 4836 | 28651 | 8/8 | 1.0 | 0.923 |
| hard13 | 4836 | 28716 | 8/8 | 1.0 | 0.907 |
| hard14 | 4836 | 28619 | 7/8 | 1.0 | 0.9 |
| hard15 | 4836 | 28451 | 9/9 | 0.955 | 0.912 |
| hard16_llm | 4836 | 28587 | 7/7 | 1.0 | 0.889 |
| hard_dev | 4836 | 28557 | 7/8 | 1.0 | 0.9 |

Exact outputs, question responses and evaluation details are under `artifacts/corpus/<world>/`. Numbers above come from `artifacts/corpus/summary.json`.

## Jev and frontier API integration

- Implemented provider-neutral evidence tasks, Jev typed choices with distributions, and Claude schema-constrained responses.
- Added validation for references and outputs, finite call budgets, conservative dollar reservations, no automatic retries, provider response caching, and service-error fallback.
- Unknown, conflicting or absent evidence remains distinguishable from a provider outage. Outputs are marked unvalidated proposals; there is no calibrated clinical acceptance policy.
- The broad natural-language planner and CMS/payer-document-to-rule compiler remain future work. These adapters currently classify bounded evidence tasks.

First synthetic development pilot:

| Provider | Attempted | Returned | Labels matching synthetic state |
|---|---:|---:|---:|
| jev | 8 | 2 | 2 |
| claude | 8 | 8 | 7 |

Jev returned HTTP 529 overload responses on six attempts. This is an availability result, not six clinical classification errors. Its small successful subset cannot establish comparative accuracy. Claude returned all eight and matched seven expected synthetic states. This was a bounded, selected development sample with the same retrieved evidence, not a full-world worklist evaluation.

The subsequent live cascade probe received an uncertain Jev answer and invoked Claude; the final answer remained unknown. Transient-overload fallback and no-frontier review behavior were separately verified with tests.

The user-authorized cap was $10.00. Across the comparison and routing probe, $9.00 was reserved conservatively; estimated token charges on returned usage total $0.04641925. Six failed attempts supplied no usage and remain unpriced. This estimate excludes tax, hardware and review labor; reservations are not charges.
Rates were checked against [TypeSafe model pricing](https://docs.typesafe.ai/models) and [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing). Usage and individual results are in `artifacts/live-provider-pilot/summary.json`, `usage.json` and `routing-probe.json`. No additional API calls are scheduled.

## Running API

- [Health](http://127.0.0.1:8876/health)
- [All questions](http://127.0.0.1:8876/questions)
- [World summary](http://127.0.0.1:8876/worlds)
- [HFrEF example](http://127.0.0.1:8876/answers/HF-01?world=hard16_llm)
- [Unsupported SNF example](http://127.0.0.1:8876/answers/SNF-01?world=hard16_llm)

The service is read-only, bound to loopback, and serves precomputed synthetic results. Requests to this API do not trigger paid inference. Restart with `.venv/bin/python -m clinical_core.service` from this project.

## Validation and next layer

27 local software tests pass. HTTP checks confirmed the live question registry, partial HFrEF answer, unsupported SNF response and note-repetition analysis. Source truth exclusion, clone/family partitions, invalid model references, service fallback, caching and budget exhaustion have explicit tests.

Next, implement a small clinical slice end to end: longitudinal kidney evidence, medication dose/tolerance and financial/program eligibility. That requires extending the synthetic generator and its hidden truth with the missing fields, not just adding prompts. Use the 140-question registry to select acceptance cases and data requirements. Calibrate routing on development families, evaluate on reserved families/new worlds, and only then train a specialist where the measured benefit justifies it.
