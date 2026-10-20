# Agere CTO review — evidence appendix (2026-10-19)

This review was read-only. The repo, the runtime checkout and the saved work were not changed. There were no model API calls and no benchmarks were run or rescored.

## Test environment

- **Versions.** The runtime checkout `/Users/mikel/agere-mvp-run` is at `b28989f` (matches the record). The repo HEAD `ee67bac` differs only in docs: `AGERE_BUILD_CONTROL.json`, `BUILD_OWNER.json`, `SYNTHETIC_ACTION_WORKFLOW.md` and `AGERE_PRODUCT_DIRECTION.md`.
- **Discrepancies.**
  1. The :8885 service was not running. PID 23772 is gone, and `/private/tmp/agere-synthetic-live-20261019.log` ends in a shutdown. `BUILD_OWNER.json` still says "ready".
  2. The runtime uses an uncommitted config overlay, `config/agere/active_warehouse.json`. It points at v2.8 sample-0.30; the committed config points at v2.0.
  3. Whether :8885 ran with `--allow-live-stack` can't be verified now that the process is gone. `answers.sqlite3` has `together_semantic_analyst` rows on 10-19, so it was live at least part of that day.
  4. `Stack.model_roles` reports `qwen3_4b_active: False` (stack_runtime.py:124). In fact the narrow Qwen LoRA is on by default, and the Qwen rewrite rung is on by default offline.
- **Scratch instance.** I made an APFS copy-on-write clone of the build plus a `git archive b28989f` export, rewrote the clone's `stack/*-link.json`, and served it on :8895. API keys were absent and outbound HTTP was proxied to a dead port. This is the product's real offline behavior; it is not the live Opus path.
- **Probe scripts and raw outputs:**
  - `/private/tmp/claude-501/-Users-mikel/c919721f-d2fb-44e0-a07c-a45a86be3467/scratchpad/probe/` holds `q1.json`, `q2.json`, `r1.json`, `r2.json` and `run.py`.
  - To reproduce one question: POST `{"question": ...}` to `/api/stack/compose`.

## A. Offline probe results (compose path, b28989f, no hosted models)

| Question | Result |
|---|---|
| Original HF question | `patient_therapy_review`, 32 rows, 1.1 s |
| "Which HFrEF patients are missing an SGLT2 inhibitor, and who is their PCP?" | 400: words not interpreted locally: missing, sglt2 |
| "List patients with HFrEF who are not on GDMT, with PCP and cardiologist" | 400: reduced, ejection, not, medical, therapy, primary, care, cardiologist |
| HF question + "over age 75 on Medicare Advantage" / "seen in the ED in last 6 months" | therapy route correctly declines; then 400 (`"or" joins terms…`) |
| Readmitted within 30 days last year, by hospital | 400: readmitted, year |
| PCP ED rate per 1000, risk-adjusted | 400: adjusted, risk, 1000 |
| PMPM by payer, diabetics, last 12 vs prior 12 months | 400 |
| A1c > 9, no PCP visit in 6 months, plus food/transport needs | 400 |
| Third-next-available for cardiologists | partial: "not computable (no slot inventory)". This is a correct, honest unknown. |
| CKD 4 not seeing a nephrologist | 400: stage, not, nephrologist, 4 |
| Denial rates by payer and reason, last quarter | 400: last, quarter |
| Opioids per prescriber + benzodiazepine overlap | 400 |
| MSSP top-50 avoidable spend | 400 |

The same base question answers locally, but most added constraints are refused:

| Base (answered locally) | Added constraint | Result |
|---|---|---|
| ED visits 2029 = 1,494 | Medicaid | 51, answered |
| | first half of 2029 | 781, answered |
| | over 65 | **grounding_failed: "Patient age_latest is never populated"** |
| | excluding patients who died | 400 |
| Denial rate 2029 = 5.23% | Medicare Advantage | 400 |
| | by month | 400 |
| No-show 2029 = 5.52% | cardiology appointments | 400 |
| | needs interpreter | 400 |
| On a statin = 497 | diabetics on statin but NOT ACEi/ARB | **749, "answered" (wrong semantics)** |
| | had an ED visit in 2029 | 400 |
| RAF by practice | HF patients | 400 |
| PMPM by payer | patients with 2+ ED visits | 400 |

## B. Confirmed defects

1. **Age reported as unavailable when it is populated.**
   - `semantic.patient` has `age_latest` and `birth_date` filled for 4,115 of 4,115 patients.
   - grounding.py:66-71 checks the raw source profile for stream `patients`, not the semantic layer, and the compiler emitted field `age_latest`.
   - The user sees a data-absence claim that is false.
2. **Statin / ACEi / ARB question inverted.**
   - The cohort family is `therapy_class_gap` with `mode: any_missing` across statin, ACEi and ARB. That counts diabetics missing any of the three classes, which ignores "on a statin".
   - Result: 749, which is more than the 497 total statin users, returned with status `answered` and `missing: []`.
3. **The HF route ignores polarity.**
   - `accepts()` (therapy_review.py:16-27) is a closed word allowlist.
   - "Flag HFrEF patients who ARE on full GDMT at target dose" and "Review HFrEF patients on GDMT" return the same 32 "not on GDMT" table.
   - "Find all HFrEF patients, flag who isn't on GDMT…" is rejected because of the contraction ("isn").
4. **The HF review answers "answered" with `missing: []` unconditionally** (therapy_review.py:124-126).
5. **Deceased patients are in the HF worklist.** SYN-75CDA6FA-E6C died 2029-10-26 and WHP-988829DF2D02 died 2027-06-14; as_of is 2030-05-27. There is no alive or enrolled filter.
6. **EF currency.**
   - There is no recency window: the "latest" EF of any age counts (therapy_review.py:68-72).
   - 12 of the 27 numeric EFs are dated 2026-08-28, about 3.75 years before as_of.
   - 8 of the 27 come from a 2029 SYN cluster in which the same EF repeats across notes 3–4 days apart. Those are copy-forward statements treated as independent current measurements.
7. **The pillar flag contradicts the dose column in the same row.**
   - 25 of 32 rows say "Reconcile unobserved pillars: rasi, beta_blocker, mra, sglt2i" (120-day fill lookback via `router._GDMT`).
   - The `recorded_doses` column lists the same drugs as "(active)" from the med list, with no reconciliation or explanation.
   - Example: P-1007 lists carvedilol, empagliflozin, sacubitril/valsartan and spironolactone, all "active". The last carvedilol fill was 2026-09-11.
8. **"Flag which…" is not answered.** Every row carries a flag ("Review regimen…" even when nothing is missing), so the subset the question asked for is never separated out.
9. **The max-dose half of the question is never computed.** `maximum_tolerated_dose` is a constant "Unknown" string. Yet the warehouse has:
   - `note_therapy_change`: 2,018 dose_increase, 16 dose_decrease, 234 declined, 71 stop
   - `therapy_barrier`: 1,271 rows, including 520 intolerance/side-effect, 335 allergy/angioedema and 46 patient-declined
   - drug strengths in med list, prescription and fill names (e.g. "carvedilol 25 MG Oral Tablet")

   A target-dose comparison and a documented-intolerance check are computable deterministically.
10. **PCP "Unknown" hides payer attribution.**
    - All 9 patients with PCP Unknown have a `member_month.attribution_practice_id`, e.g. WHN-PRACTICE-FL-6.
    - The route reads only `patient.pcp_clinician_id`.
11. **Cardiologist "Unknown" hides claims.**
    - All 9 WHP patients with cardiologist Unknown have 1–14 `payer_claim` rows rendered by a cardiology NPI.
    - The route reads only EHR encounters and care-team records.
    - The 4 P-/HF patients with Unknown genuinely have no cardiology evidence.
12. **Follow-ups.**
    - Context is carried by rewriting the question string (conversation.py:387-424), not by binding the prior cohort.
    - The HF answer has no structured frame, so offline follow-ups fail.
    - Failed turns are stored as the prior question. The 4th follow-up "Group them by PCP" resolved to "Exclude patients who have died by PCP?".
    - Without a `conversation_id`, "Group them by PCP" answered over the entire population (2,501 unattributed…) with status `answered`.
13. **Errors are not separated.** Not-understood, data-absent and internal exceptions (`ValueError`/`TypeError`/`KeyError`) all become HTTP 400 `{'error'}` (service.py:742). The learning store logs every exception as "declined" (conversation.py:582).
14. **Action workflow** (found by the subagent on :8895):
    - The workspace fingerprint includes the question text, so a trailing period creates a new, empty workspace.
    - There are no review-state transition rules: the simulation runs on review_complete or blocked items.
    - RPM and custom agents clone the PharmD program, so their receipts say "pharmacist reviewed…".
    - On a concurrent create, one owner is silently dropped.
    - An unknown item ID returns "Work changed in another window".
    - Design-intent text is stored but never used, and there is one fixed HTML template.

## C. Architecture facts (from the routing trace)

- **Path:** `service.py:280` → `conversation.handle_compose` → `Stack.compose` (stack_runtime.py:126). Then, in order:
  1. therapy_review allowlist
  2. if live: `together.compose_together`
  3. `economical_planner.plan` ladder:
     - 6 exact-question modules, recipes, registered examples
     - dq/vbc families
     - local_compiler: 35 `_*_question` handlers, about 4.5k lines, about 751 regex calls, plus 48 JSON compiler rules
     - cohort families
     - Qwen rewrite (offline only)
     - learned SQL / semantic cache
     - live only: metric/operator routers (Haiku), hosted_verified_sql (Haiku/Sonnet/Opus judge/Perplexity), Opus graph planner
- **Offline fallback:** `economical_planner.py:608` raises "Not handled locally…; hosted planning disabled".
- **Live general path:** 3× Opus analysts write a contract and SQL, the system executes and votes, then at most one Opus repair (hybrid/live.py:27-29, 221, 236-249). If no analyst answer is usable, the full hosted ladder runs again (together.py:782).
- **Claiming:** a question is "claimed" when the route name is in `SPECIALIST_ROUTES` and the answer is non-empty (together.py:160-200). metric_router and operator_router claims have no code check for non-numeric constraints they ignored. Only numbers are checked (metric_router.py:268).
- **Duplicated work:** there are 5 LLM SQL/graph writers (hosted ladder, metric_router, operator_router, graph planner, hybrid analysts). At least 7 separate vocabularies or acceptance policies exist (compiler, compiler_rules, conversation.parse_frame, semantic_cache.canonical, escalation_reason, therapy allowlist, metric_router regexes).

## D. Token economics evidence

- **Provider ledger** (`artifacts/agere-full/provider-budget.sqlite3`, 40,864 rows), list-priced:

  | Model | Calls | List cost |
  |---|---|---|
  | Opus | 2,885 | $136.69 |
  | Sonnet | 3,026 | $63.58 |
  | Haiku | 32,169 | $46.13 |
  | **Total** | | **≈ $246.76** |

  - There were 1,516 failed or abandoned calls; 1,444 of them were credit-balance 400s.
  - Failed calls store no tokens.
  - The ledger has no question ID, so cost per answer can't be computed from it.
- **Answer log** (`answers.sqlite3`): 1,408 rows covering 692 distinct questions, 10-15 to 10-19. 905 rows (64%) made paid calls, $45.63 known.
  - It undercounts: unpriced or failed cost is logged as $0 (answer_log.py:23), and error rows log $0 after paid attempts.
  - One fixture shows the analyst cost at $0.2036 while planning_economics reports $0.0266.
- **Current default for an unclaimed question:** measured $0.15 and $0.20 (fixtures) and $0.82 / 290 s (the one live row). The code-derived upper bound is a few dollars per question. The latency budget is 900 s.
- **"49/50 at $0 vs $21.12 frontier":**
  - The question set is a visible development set, run about 35 times with compiler changes in between.
  - No saved scorecard supports 49/50.
  - The earlier Agere run cost $3.92, so the later $0 depends on caches.
  - The frontier baseline had raw SQL only: no semantic layer, concepts or governed metrics.
  - The "heldout" sets were written in the same session and re-run about 10 times.
- **Unmeasured:** local compute, model memory (the 803 MB model cache), human review, failed-call tokens and invoice reconciliation.
