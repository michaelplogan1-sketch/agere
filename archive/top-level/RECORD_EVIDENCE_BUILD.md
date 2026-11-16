# Explicit record scope and evidence needs

October 10, 2026. Synthetic implementation milestone. Clinical correctness, retrieval sufficiency and full-stack acceptance remain unassessed/incomplete.

The question → evidence → interpretation → assessment path now supports a distinct **explicit-record scope**. It follows retrieved records through complete typed same-patient reference components without automatically including every other record in their source categories. The original category-complete contract and its saved single/multi-pass answers remain unchanged and replayable. Every new answer states which records were selected, how many were excluded, and that completeness of the selection has not been established.

Saved review questions now have executable evidence-location receipts. Exact received identifiers are bound without fuzzy matching; abbreviated identifiers and general questions remain unbound. Explicit record/field lookup distinguishes available selected evidence, received evidence outside scope, unavailable patient evidence, absent fields and received fields not transported to the model. These states do not establish absent care or resolve the question's meaning. A located need can prepare its complete connected component, run or replay bounded interpretation, and attach the answer plus its evidence-needs receipt to an existing program assessment. Parent answer → need → follow-up context edges are persisted separately.

## Exercised behavior

| Evidence | Observed result |
|---|---|
| Active warehouse | Original-cohort v1.23, unchanged; 1,614 synthetic patients, 1,238,686 received records, 158 categories |
| Same question and retrieval roots | Six retrieved records, 12 records after complete typed reference closure; 25 records exist in those five categories |
| Explicit selection | 12 selected records / 26 units / 31,493 UTF-8 context bytes; 13 other records in those categories excluded and reported |
| Prior category expansion | 25 records / 52 units / 57,577 UTF-8 context bytes; preserved in the prior milestone receipt |
| Evidence-need lookup on saved answer | Three of 21 proposed review questions contain exact received-record references; 18 remain unbound, not failed retrieval or evidence absence |
| Focused follow-up | Five connected records / 11 units; 514 other received patient records excluded, including five in the same categories |
| New interpretation | Two source passes and one reconciliation; requests 15,134 / 7,618 / 15,744 bytes; nine findings and 19 structurally verified exact citations |
| Model unit dispositions | Eight discussed, three marked not relevant to this question; these are unreviewed model judgments |
| Paid usage | Three inference calls, three native token-count requests; $0.186144 estimated from recorded token usage/prices, not invoice-verified |
| Replay and review | Zero new calls on saved replay; one assessment retains exact answer and evidence-needs provenance; work counts unchanged |
| Focused checks | 73 unittest executions, including imported fixtures; JS syntax; actual API validation, legacy replay, program preparation and browser flows |

Saved follow-up: http://localhost:8885/stack?case=d7d96be6f304b5649e24c329e38648d1ef6475c579c1645498dcd7fd177c8799

The size reduction measures an explicit scope change; it is **not** evidence of decision-preserving compression or improved clinical quality. Those 13 excluded records could matter to a broader question. They were excluded by the recorded retrieval/reference selection rule, not discarded to force a model call to fit.

## Contracts and integration

`record_context.py` uses existing source validation, original narrative/structured units, source clocks and context packing. It persists its own content-addressed contract alongside existing category contexts, includes an explicit record-scope declaration in transport, and checks the entire patient source fingerprint before reuse. A same-category addition or other source change invalidates the selection. Original units stay whole; incomplete/budget-overflow contexts cannot dispatch. A maximum of 200 explicit records, 30 categories and 60,000 context bytes remains enforced.

`record_synthesis.py` reuses `case_passes` partitioning, source dependencies, parsers, exact citation checks, reconciliation jobs, token counting and the existing provider ledger. Its separate runner keeps legacy replay identities intact. Each request includes the explicit record-scope declaration. One source pass needs no extra reconciliation call; larger scopes retain the bounded reconciliation behavior. Successful paid responses survive partial failure, and failed/pending calls cannot silently retry. Original findings/questions remain preserved, and cross-group semantic relationships remain unassessed when reconciliation uses multiple groups.

`source_synthesis.py` dispatches by the persisted context type. Existing `/context`, `/context/plan`, `/context/synthesize`, progress and saved-answer routes now handle both contracts. `question_context.py` version 2 selects exact connected records. The main workspace shows both selected counts and exclusions within the same categories. Saved-answer restoration stops after a new user interaction, preventing a late saved response from mixing into a new question result.

`evidence_needs.py` provides exact source/field availability checks and durable follow-up edges. Source-pass questions with unambiguous finding provenance also bind unit aliases through the original pass manifest. The exercised new answer binds source pass 1 / u6 to the exact response narrative and source pass 2 / u3 to the copied-letter narrative: five of its 12 questions now have source bindings; seven remain unbound. Manual selectors retain their own record/field list independently of proposed review questions. Empty wrong-type selectors are rejected rather than silently treated as an empty list. `/api/stack/evidence-needs` and `/api/stack/evidence-needs/context` power the new answer controls. `synthesis_work.py` attaches the evidence-needs receipt ID/hash/summary to each answer in an assessment. Existing clinical/program candidacy rules are unchanged. Old prepared assessment contexts from before this bridge change must be prepared again; old answers remain readable. No work item, clinician review, external action or clinical state was created by these preparations.

## Cost and ownership

The shared ledger had **$1.83** reservation capacity before this milestone's paid run, with 678 validated and 10 failed requests. This differs from the prior milestone's ending $2.83/677/9: two intervening requests already existed. They were not part of this verification and were neither repeated nor erased. The focused follow-up's $1.50 ceiling fit the current budget. After its three validated calls, **$0.33** reservation capacity remains (681 validated / 10 failed). The $50 cap, failed holds and all other ledger limits were not changed. This is conservative reservation accounting, not the actual invoice balance. New uncached calls requiring the current $0.50 reservation are blocked; saved answers remain usable. Continue independent local engineering and audit the existing settlement mechanism before any future paid work; do not silently increase limits or purchase services.

`BUILD_OWNER.json` remains this chat's ownership record. The previous chat was rechecked idle. The single existing heartbeat remains the continuation mechanism. Current server PID **75501**, tool session **96793**, loopback port 8885, launched by `.venv/bin/python serve_current_warehouse.py --allow-live-stack --port 8885`. The user's pre-existing browser tab was preserved; verification used a separate tab.

No source migration, generator edit, hidden-truth access, model training/promotion, frozen-cohort changes or research-corpus modifications occurred. The research recovery and dispositions in `AGERE_RESEARCH_RECOVERY_2026-10-10.md` remain the research map.

## Remaining work and next substantive step

Exact identifier lookup does not interpret temporal or clinical questions, abbreviations, ambiguous cross-pass aliases, or whether a finding actually resolves a need. The alias audit found that source-pass u6 and reconciliation u6 denote different units. Exact citation IDs remain valid. The need view now disambiguates source-pass references using their original manifests; reconciled questions, mixed-pass questions and references naming a foreign pass remain unbound. The original paid answer and its misleading reconciliation question remain preserved. Stable alias provenance must next travel into the reconciliation request itself. Eighteen original questions remain unbound. A free-text question can still be carried into a narrower reconciliation group and create unnecessary review work; this milestone gives explicit source lookup/follow-up without silently deleting that historical question. Selection recall and meaning correctness need independent evaluation.

Next: connect typed need-to-unit/source/field/time bindings to the existing reviewed assertion/correction lifecycle and evidence-acquisition contracts. Record explicit addressed / unresolved / invalid-binding dispositions with cited evidence and reviewer provenance; machine findings alone must not mark clinical needs resolved. Preserve original proposals and source-change invalidation. Extend the delivered source-pass alias lookup to typed cross-pass question dependencies and carry those stable identities into reconciliation, rather than guessing from prose. This can advance locally using saved source/answer receipts while paid reservation capacity is low. Then continue broad warehouse planning, semantics, payer/provider/contract applicability, qualified prediction/discovery and portable full-stack acceptance. Do not rerun unchanged passing audits or pay again for this saved demonstration.
