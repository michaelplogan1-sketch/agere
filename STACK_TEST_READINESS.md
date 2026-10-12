> **Active build — October 9, 2026:** Agere uses the original-cohort
> `healthcare-warehouse-v1.14`: **1,614 synthetic patients, 894,223 received records,
> 88 source categories**. The current build is `artifacts/agere-healthcare-interpretation-v1/`.
> Native medication/care/access/finance/goals accounts, new narrative fields and
> linked specimen/encounter clocks now feed the query and patient-state layers.
> Read [the native interpretation build](WAREHOUSE_INTERPRETATION_BUILD.md) first.
> Live: http://127.0.0.1:8885/stack. Start with
> `.venv/bin/python serve_current_warehouse.py --allow-live-stack --port 8885`.
> `config/agere/active_warehouse.json` controls follow-on builders. Earlier population
> results below remain historical; forecasts and model qualifications do not transfer.

# Agere readiness — October 9, 2026

Newest build: `BASELINE_EVIDENCE_BUILD.md`; current release
`artifacts/agere-baseline-stack-v1/`. A direct outcome → baseline graph connection
now assembles patient-specific pre-index measurements, note proposals, medication
supply and source-arrival blocks. Three 10,000-patient runs retain 144 source-dated
baselines per admission/discharge task; explicit clinical-date baselines remain
unavailable. Feature values and target labels export separately with hashes.
139 focused checks, zero paid calls, no new model weights. The exports have no
useful target-class variation and are not authorized training data. This implements
the plan's longitudinal/forecasting/economics connection, not completed prediction
or clinical eligibility. Broader source semantics, clinical/payer prerequisites,
program/outcome attribution and payment accountability remain.


Previous build: `CARE_PROTOCOL_BUILD.md`; current release
`artifacts/agere-care-protocols-v2/`. The new `protocol_review` operator connects
separate source mappings, structured numeric/range evidence, existing narrative EF
proposals and reviewed documentary state to versioned review definitions. It feeds
complete population membership and immutable program-selection context. All three
protocols evaluated 10,000 synthetic patients: 887 EF-history source-review signals,
1,061 supported low-eGFR review criteria and 110 supported affordability reports.
The latter feed 110 program assessments with protocol evidence attached. 159 focused
checks, zero paid calls, no new model weights or fabricated human reviews. Current
clinical state, full clinical/payer eligibility and broader learned interpretation
remain unfinished. These are review counts, not accuracy estimates. Next: broader
source-bound longitudinal clinical/medication relations and effective prerequisites
connected to baseline-safe outcome analysis. See the report for exact limits.


**Working synthetic integration prototype; not a feature-complete alpha.** Existing
paths can be tested end to end now. The full intended product still has substantial
build work. A completion percentage or delivery-date estimate is not established.

Latest connected increment: `OUTCOME_LAYER_BUILD.md` adds versioned observation
outcomes, explicit index/follow-up windows, complete population accounting,
statistical reuse and candidate-label exports. 112 focused checks and live browser
execution passed; all new API usage was zero. Three 10,000-patient definitions
expose provisional links and unresolved clinical timing. No new model was fitted;
current usable targets lack class variation. This does not complete the clinical,
causal, predictive, protocol or payment layers.

Previous connected increment: `EVIDENCE_REQUIREMENTS_BUILD.md` adds generic
documentary requirement logic, exact selection evidence in program assessments,
conditional evidence-impact counts and explicit document-role exclusions.
Three full 10,000-patient contract runs and a 110-patient affordability review
proposal execute in the live workbench; 215 focused checks pass. This is additional
integration, not broad clinical interpretation, trained model improvement or
feature-complete alpha status. See the current v3 implementation receipt.

Latest source-learning increment: `/learn` now acquires received evidence across
source streams, protects training/evaluation ancestry, marks exact targets and
connects machine suggestions to explicit review. 96 queue tasks; 29 successful
suggestions on eight selected sources, with context and response-format failures
reported separately. No new weights or human reviews. Source-section/scope handling
and competent broader interpretation remain unfinished. See SOURCE_LEARNING_BUILD.md.

Latest training increment: lexical and frozen-MiniLM source heads now fit, calibrate,
save versioned artifacts and serve optional source-bound proposals. See
`SEMANTIC_STUDENT_BUILD.md`. Both failed selective acceptance on the authored
synthetic development corpus. No human reviews exist in the new source-review
workflow. The fitting machinery is built; a competent broadly trained clinical
interpreter and automatic clinical release are not. 143 focused checks and browser
integration passed with zero paid calls in this increment.

The objective remains interpretation, discovery, explanation, prediction,
missingness reasoning and supported action across healthcare, population health,
VBC, access, provider performance, programs and commercial/outcome-aligned payments.
HF/CKD are examples. Cost per supported result is part of each layer. Actual
execution agents remain a later integration boundary.

| Area | Executable now | Remaining build before broad synthetic acceptance |
|---|---|---|
| Data/evidence | Versioned snapshot, structured/received narrative mappings, complete cohorts, hybrid retrieval/reranking and source chronology | Broader terminology/relations, unmapped sources, adjudicated episodes |
| Context/interpretation | Task context, source assertions, relation/date-clock reconciliation, repeated-history controls, missingness, prerequisites, whole-statement passes, typed specialist support bindings and source-bound three-axis refinement with explicit correction/export | Broader extraction/current-use/completion/exception/intent interpretation, calibrated semantic acceptance and remaining model consumers |
| Models/economics | Context heads, MiniLM/BGE, local compiler/cache, typed Perplexity routing, optional Jev/Perplexity reviews, Claude escalation, experimental native Kev/d1, fitted lexical/MiniLM semantic heads and shadow serving | Broader reviewed supervision, calibrated skill routing, clinical release authorization, measured quality and total cost per role |
| Programs/measures/outcomes | Versioned quoted research rules, bounded bindings, persistent review/program work | Reviewed clinical/payer requirements, exclusions, attribution, denominators, outcome/cost definitions and update-impact handling |
| Discovery/prediction/action | Descriptive operators, exploratory comparisons, research forecasts and source-change models, earlier mathematical APIs | Further warehouse adapters, supported comparison/causal designs, patient-goal/capacity-aware recommendations and outcome/payment accountability |
| Deployment | Local synthetic UI/API and warehouse | Governed connectors, access controls, incremental jobs, packaging and external validation |

Bounded whole-statement decomposition and the typed specialist consumer are now
connected; see `CONTEXT_PASSES_BUILD.md`. Shared documentary assertion reuse and
cross-pass support joins are now connected; see `ASSERTION_RECONCILIATION_BUILD.md`.
Source-statement refinement, the same contract across four readers, explicit source
corrections and training-candidate export are now connected; see `SEMANTIC_READER_BUILD.md`.
94 focused software checks and two selected synthetic cases across four models
verify this integration. They do not establish clinical accuracy or a model winner.
Reviewed-source timeline reconciliation, complete review-status membership,
downstream composition and affected-patient reuse are now connected; see
`INTERPRETATION_STATE_BUILD.md`. 124 focused checks and full-population executions
verify the integration with no new paid calls. Next build: cross-source clinical
interpretation/state bindings and calibrated acceptance.
Typed documentary state and family-separated reviewed training preparation now
connect as well; see `SOURCE_STATE_AND_LEARNING_BUILD.md`. 153 focused checks
passed. No new fitting or release occurred; the current statement-review workflow
has no recorded human labels. Missing relation/date coverage remains visible in
full-population source-state queries. No clinical acceptance is implied.
Preserve all exceptions and conflicting accounts, retain explicit unknowns, and
reuse interpreted assertions across questions. Preserve the frozen Kev/d1 comparison
and its acceptance gates; new training needs appropriately separated new examples.

Then connect reviewed clinical/program/outcome contracts and broaden warehouse
adapters for discovery, prediction and action. Each step must produce an executable
warehouse question, rather than only add research or test scenarios.

Full synthetic acceptance must exercise representative clinical, utilization,
access, provider, program and financial paths through:

1. Question → complete defined population, including unknown membership.
2. Received evidence → source-bound interpretation and explicit missing inputs.
3. Interpretation → versioned rules or analytical assumptions.
4. Supported finding → review/action proposal and accountable outcome definitions.
5. Replay → stable provenance, changed-input invalidation and measured local/API cost.

The new context build processed three 10,000-patient workflows without paid model
calls. Those checks establish execution/accounting behavior, not clinical accuracy,
outcome benefit or superiority over real vendor products. See `TASK_CONTEXT_BUILD.md`
and its machine-readable receipts.
