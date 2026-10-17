# Agere build log: add one layer at a time, measure, keep or document

Method (Mike, 2026-10-16): start from what exists, add one thing, test on a few questions, keep it if it helps, and if it
hurts, write down why and decide what to add next.

- **Dev set:** 22 questions from the generator's truth-backed v2.8 set (2 per domain + 2 unsupported), drawn with seed
  20261016. The ids are in tmp/dev_ids.txt. The other 125 are not used while iterating, so the final check stays clean.
- **Scoring:** the generator's sealed scorer, v2.8-sample-0.30 scope. We see only correct or incorrect per question,
  never the answers. Defined mode: question + its published definition.
- **Warehouse:** the active v2.8 sample build, on main code with the merged fixes. Each step is a separate service on
  :8886 with the listed flags; the live :8885 is not touched.
- **Noise:** with 22 questions, ±2 is noise. Only larger moves count, and repeat a step before dropping a layer on a
  small loss.

| Step | Layers on | Correct /22 | Answered | Right when answered | $ total | Median s | Keep? |
|---|---|---|---|---|---|---|---|
| 0 | deterministic core only (no paid calls) | 2 | 0 | n/a | 0.00 | 10 | baseline |
| 1 | + metric router (Haiku picks a governed metric) | 3 | 6 | 1/6 | 0.15 | 1 | keep, but fix precision (see step 1) |
| 2 | + operator router (Haiku picks an engine) | 4 | 7 | 2/7 | 0.14 | 3 | keep |
| 3 | + hosted SQL (Haiku writes SQL on the semantic layer) | 6 | 16 | 5/16 | 0.63 | 35 | keep; precision 31% |
| 4 | + guard re-plan (full-catalog probe on zeros/declines) | 6 | 16 | 5/16 | 0.00 | 29 | keep (neutral here) |
| 5 | + together: Haiku semantic analyst in parallel + Opus reconcile | 13 | 21 | 12/21 | 2.05 | 50 | keep the analyst; question the Opus reconcile |
| 6 | together, but on disagreement the analyst answers (no Opus) | 13 | 21 | 12/21 | 0.02 | 31 | keep: same accuracy, ~1% of the cost |
| 7 | + canonical conventions in the analyst's docs | 12 | 21 | 11/21 | 0.02 | 38 | neutral (noise); kept, it is general |
| 8 | + payer adjudication history table (every version, with CARC) | 15 | 21 | 14/21 | 0.02 | 34 | keep: REV-12 fixed, best so far |
| 9 | + record_correction table (all streams, with link_field) | 15 | 21 | 14/21 | 0.03 | 50 | keep (general data-quality table; DQ-10 still missed) |
| 10 | + unchecked-zero guard (an analyst-unconfirmed 0/empty is withheld) | 15 | 21 | 14/21 | 0.02 | 34 | keep as a safety rule; didn't fire on dev |
| 11 | + canonical fill table (picked-up fills, class windows, new-user flags) | 15 | 21 | 14/21 | 0.02 | 31 | keep (general); neutral, the analyst didn't use it |
| 12 | + analyst format repair (extract embedded JSON, else ask once) | 13-14 | 21 | 12-13/21 | 0.08-0.09 | 29 | keep as a safety fix; never fired |
| 13 | + drug-class vocabulary for all of healthcare (+50 classes, 6 groups, 96 brands) | 13 | 21 | 12/21 | 1.46 (cache refill) | 51 | keep: scope/coverage; neutral on score |
| 1x | step 1 + answer-guard cross-check (Sonnet + Opus judge, on by default) | 4 | 6 | 2/6 | 3.19 | 90 | too costly for +1; revisit last |

## Step 0: deterministic core only
- **Result:** the deterministic core declined all 20 answerable questions ("Not handled locally; hosted planning
  disabled"). It correctly declined the 2 unsupported ones.
- **Why:** the compiler, governed metrics and engines don't parse these definition-heavy, plain-English questions. Every
  correct answer has to come from the LLM layers.
- **Bug noted:** on DQ-09 the compiler produced "result_status cannot be both corrected and corrected on one record",
  a parse error on "corrected" in a sequence question. Fix later.
- **Next:** add the paid layers in ladder order, one per step: metric router (Haiku picks a governed metric), operator
  router (Haiku picks an engine), hosted SQL (Haiku writes SQL), guard re-plan, then the together analyst + reconcile.

## Step 1: + metric router
- **Result:** +1 correct (ACC-12). It answered 6 questions, but 5 were wrong: CLN-13, DQ-09, DQ-10, QUA-05, REV-03.
- **Why it hurts precision:** the router fits a governed metric whose built-in definition differs from the definition the
  question states. This is the same failure the generator's prototype saw ("generic metric definitions didn't match").
- **Hidden cost found:** the answer guard's cross-check (Sonnet second model + Opus judge) runs by default even with the
  other paid rungs off. With it (row 1x): 4/22 and 2/6 right, but $3.19 for 22 questions. It is now measured as its own
  layer (AGERE_CROSS_CHECK) and switched off in every other step.
- **Next:** the operator router (Haiku picks an engine).
- **Candidate fix for the metric router:** an exact-fit gate, so a metric answers only when its definition matches the
  question's stated definition; otherwise decline to the next rung.

## Step 2: + operator router
- **Result:** +1 correct (QUA-01, the screening rate, through the measure engine). There was one more answer and no new
  wrong answers; the 5 wrong ones are still the metric router's.
- **Next:** hosted SQL (Haiku writes SQL against the semantic layer). That is the rung that can follow a question's own
  definition.

## Step 3: + hosted SQL
- **Result:** +2 net correct (ACC-03, BH-11 and VBC-04 gained). It answered 16, with 5 right.
- **Lost:** BH-04 is now answered, though it is unsupported. An abstention became a false assertion.
- **Reference on the same 22:** Haiku + semantic layer alone (frontier_semantic) gets 7; Haiku + Tuva gets 8. The
  overlap with Agere is small: Agere has QUA-01 and BH-11, which the LLM misses; the LLM has BH-14, CLN-13 and DQ-09,
  which Agere misses. A union would be about 9, which is what the together layer is meant to capture.
- **Next:** guard re-plan (full-catalog probe on zeros and declines).

## Step 4: + guard re-plan
- **Result:** identical to step 3. None of the 22 answers was a zero or a data-absence decline, so the re-plan never fired.
- **$0:** identical model requests are served from the provider-ledger cache, so re-running a configuration doesn't pay
  twice.
- **Next:** the together layer (Haiku semantic analyst in parallel + Opus reconcile on disagreement).

## Step 5: + together (analyst + Opus reconcile)
- **Result:** 13/22, the largest gain so far (+7). It answered 21 and was right on 12 (57%). Cost $2.05, about $0.09 per
  question, nearly all of it the Opus reconcile turn.
- **Each component, scored separately from the same run:**

  | Component | Correct /22 |
  |---|---|
  | Fast path (steps 1–4) | 6 |
  | Haiku semantic analyst | 14 |
  | Together | 13 |

  The analyst reads Agere's current semantic layer (layer/17, with the claim-line and catalog fixes). That is why it
  beats the earlier Haiku runs on the old layer, which got 7.
- **Opus reconcile added nothing on this set.** Every question the fast path got right, the analyst also got right. On
  HOS-03 the reconciler replaced a correct analyst answer with its own wrong one.
- **Decisions:**

  | Decision | Count | Right |
  |---|---|---|
  | Agree | 3 | 2 (BH-04 is an unsupported question both answered) |
  | Both unsupported | 1 | 1 |
  | Reconciled to analyst | 14 | 8 |
  | Reconciled to fast | 2 | 1 |
  | Reconciled to its own answer | 2 | 1 |
- **Next (step 6):** the analyst answers on disagreement, with no Opus call (AGERE_DISAGREE_POLICY=analyst). The fast
  path stays as the agreement check that raises confidence.

## Step 6: analyst answers on disagreement (no Opus)
- **Result:** 13/22, the same as step 5, at $0.016 instead of $2.05. Kept as the default policy candidate
  (AGERE_DISAGREE_POLICY=analyst).
- **Noise:** in this run the analyst alone scored 12. Some calls were not cached and LLM answers varied (HOS-03 flipped).
  On VBC-04 the analyst's reply failed to parse and the fast path's answer saved it. That is a reason to keep the fast
  path.

## Step 7: canonical conventions in the analyst's documentation
- **Result:** 12/22 (HOS-08 lost). Within noise, and none of the targeted misses moved.
- **Why it didn't help (from the analyst's own SQL):**
  - REV-12: the analyst counted CO-18 denials from current claim versions. A denial later reworked and paid is
    superseded and invisible: the semantic layer has 689 claims against 850 in the received data. It's a data gap, not
    a docs gap.
  - RWE-02: reasonable new-user logic, but it matched statins by drug-name substring, which also catches nystatin.
- **Next (step 8):** add the missing data, not more words: a payer adjudication history table.

## Step 8: + payer adjudication history (every version)
- **New:** clinical_core/agere/clinical/adjudication_history.py builds payer_adjudication from the build's own
  received warehouse (all 199,410 versions; 18,527 superseded). It upgrades semantic.sqlite3 in place.
- **Check:** CO-18 denials dated 2028 = 850 distinct claims, matching the received data; 0 unmapped patients.
- **Docs:** conventions now point denial-history questions to payer_adjudication and class filters to drug_class.
- **Step 8 result:** 15/22, the best so far. It answered 21, with 14 right (67%), for $0.02. REV-12 (the duplicate-denial
  count) is fixed as targeted, and HOS-03 and HOS-08 are back (likely noise).

## Step 9: + record_correction (every superseded record, all streams)
- **New:** record_correction (in adjudication_history.py) holds 21,256 supersedes_record_id corrections, matching the
  received data, plus 1,743 lab corrections linked by replaces_result_record_id. link_field says which link applies.
- **Result:** 15/22, unchanged. On DQ-10 the analyst counted both correction types (22,999) where the question names
  only supersedes_record_id. That is a model instruction-following miss, not missing data, so it isn't chased further
  (to avoid tuning to one question).

## Where it stands after 9 steps
- **Dev set:** 2/22 (deterministic core) → 15/22. Precision on answered went 1/6 → 14/21. Cost is $0.001–0.03 per
  question once the Opus reconcile and the answer-guard cross-check are off.
- **What moved the needle:** the semantic analyst (+7), the no-Opus disagreement policy (same accuracy, 1% of the cost)
  and missing history data (+2 to +3). Docs-only conventions did not move it.
- **Remaining misses and their causes:**
  - RX-10: the analyst failed, so the fast path's 0 shipped.
  - RWE-02, RWE-12: fill and class logic.
  - REV-03: top CARCs; it now reads payer_adjudication but still misses.
  - RX-09: medication stops recorded only in notes (NLP).
  - DQ-10: instruction following.
  - BH-04: answered although the key says unsupported. PHQ-2 does exist on HRAs, so the key itself may be wrong;
    adjudicate before counting it.
- **Candidate next layers, best first:**
  1. A one-time check on the 125 held-out questions, to confirm the gains generalize. Cheap at the step-9 settings.
  2. When the analyst fails or the fast path returns 0, re-probe or abstain instead of shipping a bare 0 (RX-10).
  3. A canonical fill table with class, supply window and new-user / restart / switch flags precomputed (design notes
     §3), so pharmacy and RWE questions stop depending on the analyst's SQL.
  4. Drug-class vocabulary for all of healthcare: about 14k payer fills are unclassified (ADHD, antidepressants,
     contraceptives, antibiotics).
  5. A visit/encounter grouper table (design notes §1) for hospital-ops questions.
  6. Make the in-place passes (adjudication history, corrections) part of the semantic-layer build, so a rebuild
     keeps them.

## Held-out check (step-9 settings), 2026-10-16
- **Sample:** 10 held-out questions, one per domain, never used while iterating (seed 20261017; tmp/holdout10_ids.txt):
  ACC-10, BH-10, CLN-06, DQ-08, HOS-01, QUA-04, REV-09, RWE-10, RX-13, VBC-02.
- **Result:** 7/10 correct, 9 answered, 7/9 right (78%). In line with the dev set (15/22, 14/21), so the gains generalize.
- **Same 10, earlier baselines (saved runs, same keys):** Haiku + old semantic layer 2/10; Haiku + Tuva 1/10.
- **Misses:** ACC-10 and VBC-02 (wrong answer), RX-13 (abstained).
- **Cost:** under $0.01. A 125-question run was started first and stopped at Mike's request (use 10); its partial calls
  were cached and reused here.

## Step 10: unchecked-zero guard
- **Change:** when the analyst produced no answer and the fast path's answer is 0 or empty, the answer is withheld
  (partial, low confidence) instead of shipping a confident 0 (AGERE_UNCHECKED_ZERO=abstain).
- **Result:** 15/22, unchanged. In this run the analyst answered RX-10 itself (wrongly), so the guard never fired. Kept
  as a safety rule: a bare unchecked zero is the classic missed-source answer.

## Step 11: canonical fill table (design notes §3)
- **New:** clinical_core/agere/clinical/fill_events.py builds fill in place: 131,108 picked-up fills from 166,318
  source rows (35,210 dispense + payer-claim duplicates merged); class_key; supply_end; class sequence; days since the
  previous fill; gap after the previous supply; days of data before; new_user_180. Nystatin is no longer a statin.
- **Result:** dev 15/22 and held-out 7/10, both unchanged.
- **Why neutral:** on RX-10, RWE-02 and RWE-12 the analyst kept joining payer_pharmacy_claim to medication and ignored the
  fill table, even with it documented. Statin new users in 2029 come out at 115 from the table and from the analyst's own
  SQL, payer-only or all sources. So the RWE-02 miss is not fill handling. It is either a finer definitional point or a
  key issue, and isn't chased (no tuning to one key).
- **Lesson:** a new table helps only when it carries data the analyst can't otherwise reach (step 8). Re-packaging data
  it can already compute doesn't change its answers.

## Next candidates (re-ranked)
1. Put the step-9/10 settings (cross-check off, analyst on disagreement, unchecked-zero guard) and the in-place passes
   into the default build and the live :8885, so the product runs what was measured. That needs Mike's OK, because it
   changes live defaults.
2. Retry the analyst once when its reply fails to parse (seen on VBC-04): a cheap fix for a real failure.
3. Drug-class vocabulary for all of healthcare (about 14k unclassified fills); needed for scope, not for current misses.
4. Visit grouper table, only if a hospital-ops miss shows the analyst can't reach the data otherwise.

## Live defaults (2026-10-16, Mike approved), commit fd43ca6
- **Settings:** AGERE_CROSS_CHECK default off; AGERE_DISAGREE_POLICY default analyst; the unchecked-zero guard is on.
  The semantic-layer build now runs adjudication history, record correction and fill events.
- **Live :8885** restarted on these defaults. Smoke tests: heart failure, both paths agree (329), $0.009; denial rate,
  analyst on disagreement, $0.0008.
- **Fix:** with the analyst policy, no empty reconcile step is recorded, so cost stays priced (it had read as unknown).

## Step 12: analyst format repair
- **Change:** product-only format_retries=1 in semantic_analyst.tool_loop/analyze (the bench baselines keep 0 and still
  fail on bad JSON). On a non-JSON final reply it first extracts the embedded JSON object (free), then asks once for only
  the JSON object.
- **Result:** dev 13/22 then 14/22 on two runs (step 11: 15; within ±2 noise); held-out 7/10, unchanged. No analyst reply
  failed to parse in these runs, so the repair never fired.
- **Kept:** a cheap fix for a real failure seen at step 6 (VBC-04).

## Step 13: drug-class vocabulary for all of healthcare
- **Change, drug_classes.py:**
  - About 50 ingredient-based classes. ADHD (stimulant and non-stimulant). Antidepressants: SNRI, NDRI, SARI,
    tetracyclic, tricyclic. Lithium, anticonvulsants, gabapentinoids. Alpha-1 blockers, 5-ARIs, bladder
    antimuscarinics, beta-3 agonists. LAMA, LABA, leukotriene modifiers. Smoking cessation, hormonal contraceptives.
    Nine antibiotic classes. Antivirals, antiretrovirals, systemic corticosteroids, csDMARDs, TNF inhibitors, dementia
    drugs, 5-HT3 antiemetics, H2 blockers, triptans, opioid antagonists, vitamins D and B12, acetaminophen,
    thiazolidinediones (now in the diabetes group), epinephrine auto-injectors.
  - New groups: antidepressant, adhd_medication, antibiotic, copd_maintenance, dementia_medication, bph_medication.
  - Meperidine added to opioid.
  - Oral glycopyrrolate is deliberately not a LAMA. Estrogen hormone therapy is left out, so combined contraceptives
    aren't double-tagged.
- **Brands:** config/agere/drug_brands.json gains 96 common US brands (400 total), so every class has brand coverage.
- **In place:** fill_events.apply_reclassify gives unclassified medication rows their classes, refreshes
  drug_class_vocabulary and rebuilds fill. Unclassified payer fills: 14,405 → 68.
  - Rows already classified keep their old single class until the next full semantic rebuild (e.g. hydrocodone/
    acetaminophen has opioid, not yet acetaminophen). The build runs the same classify(), so a rebuild is consistent.
- **Result:** dev 13/22 (QUA-01, a breast-screening question unrelated to drugs, flipped; within noise); held-out 7/10,
  unchanged.
- **Cost:** $1.46 dev + $0.47 held-out. The vocabulary is part of the prompts, so every call missed the cache once
  (about $0.03–0.14 per uncached question). Later runs cache again.
- **Kept:** this layer is about scope. Questions about the 14k fills outside cardiometabolic care (ADHD,
  antidepressants, antibiotics, contraceptives) can now be answered by class. The dev and held-out sets are
  cardiometabolic-heavy, so they don't show it.

## Demand engine: Timestral candidate (2026-10-17)
- **Change:** demand.py gains a `Timestral` model: Mistral's time-series foundation model, used zero-shot on CPU via
  the `timestral` package (Apache-2.0 code; weights under Modified MIT, downloaded from the HF Hub). The point forecast
  is the forecast median. Simulations draw from the 21 forecast quantiles, and the existing split-conformal step sets
  the intervals. Opt-in with `AGERE_DEMAND_TIMESTRAL=28M,123M`. Default off, so the live path is unchanged.
  test_demand passes (19/19).
- **Test:** `tools/demand/timestral_compare.py` on the active v2.8 sample (as-of 2030-05-27). 68 series: 9 measures
  × week/month × all plus top-3 facilities. Horizon 13 weeks or 6 months, same screen/regime/rolling-origin backtest
  as forecast_series. Output: tmp/timestral/compare.json. Runtime 115 s for both sizes.
- **In-sample (misleading):** compared against the best of ~11 classic models picked on the same backtest, Timestral
  looks even. 123M is 3% better weekly (geo-mean MASE ratio 0.971, 15/31) and 9% worse monthly (1.088, 2/35).
  That compares one model with a minimum taken over 11, so it favours the classic pick.
- **Out-of-sample (the real test):** pick on the first half of the origins, score on later origins.
  | | classic pick | + Timestral in pool | Timestral-123M alone | 123M vs classic (geo) | 123M better |
  |---|---|---|---|---|---|
  | week (31) | 0.815 | — | 0.712 | 0.851 | 23/31 |
  | month (35) | 0.897 | 0.896 | 0.838 | 0.924 | 20/35 |
  | all (66) | 0.858 | — | 0.779 | 0.889 | 43/66 |
  Adding Timestral to the selection pool helps weekly (0.765 → 0.704 over 33 series) and leaves monthly flat. Using
  Timestral directly beats both, because the MASE pick overfits the backtest. 28M is nearly as good (geo 0.897
  overall, 42/66).
- **Coverage:** after conformal, Timestral matches the classic winner. 123M: 80% → 0.783 vs 0.796; 95% → 0.928 vs
  0.930. Its raw quantiles under-cover at 80% (0.82), so keep the conformal step.
- **Read:** about 11% lower out-of-sample error on demand forecasts at the same interval coverage. The weekly series
  gain most (obs stays, specialty, inpatient at HOSPITAL-2). Limits: synthetic series, about 20 late origins per
  series, groups correlated with their totals.
- **Status (Mike approved 2026-10-17): Timestral-123M is the DEFAULT demand model.**
  - `AGERE_DEMAND_TIMESTRAL` defaults to `123M`; set `off` to go back to classic MASE selection.
  - The classic backtest still runs. Its pick is reported as `backtest_pick` and is the fallback when Timestral fails
    (missing package or weights, runtime error). The naive baseline MASE is still reported.
  - The output carries `model_selection` (`default` | `backtest_mase`), and the summary and method text say which.
  - test_demand's seasonal-recovery check now asserts on `backtest_pick`. Timestral itself passes that test's accuracy
    checks: it beats seasonal naive and tracks the true mean with about 6.8% error.
  - Tests: 30/30 (test_demand + test_healthcare_questions) with the default and with `off`.
- **Install:** the package isn't on PyPI. Install it with
  `uv pip install --python .venv/bin/python --no-deps git+https://github.com/mistralai/timestral` (torch, numpy,
  safetensors and huggingface_hub are already in the venv). Weights (490 MB) live in the HF cache; the first call per
  process loads them (~15 s).
- **Cache fix:** analysis_graph's result_cache key ignored env flags. A :8886 service with `off` wrote a
  classic-model answer that live :8885 then replayed. The key now includes `demand.default_model()` for any plan with
  a demand node.
- **End to end on live :8885** (restarted 2026-10-17, no cache hit):
  - "Forecast monthly ED visits for the next 6 months": timestral_123m, MASE 0.61 vs seasonal naive 0.87, 80%
    conformal coverage 0.71.
  - "Weekly admissions forecast with uncertainty": timestral_123m, MASE 0.69 vs 1.11, coverage 0.78.
  - The Agere fast path answers both. The final answer status is partial/unsupported because the analyst (LLM over
    the semantic layer) says "no forecasting model" and the analyst policy wins on disagreement. The same happens with
    Timestral off, so this predates the change. It's a routing gap in its own right: forecast questions should defer
    to the demand engine.
