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
