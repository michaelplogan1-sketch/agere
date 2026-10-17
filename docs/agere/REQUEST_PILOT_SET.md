# Request to the generator: frozen pilot set (plan step 3)

**From:** Agere hub. **Date:** 2026-10-17. **Data:** healthcare-warehouse-v2.8, sample 0.30 (the same snapshot as H3).

## Why
The 30 dev/held-out questions are now development evidence: the stack is at 10/10 held-out and 18/20 dev on them, and
we have looked at their failures. A clean measurement needs fresh questions that no Agere session has seen, frozen
before any contestant runs.

## Ask: 100 new intents
None of them may reuse or paraphrase an existing question id or intent from the v2.8 question bank.

| Group | n | What it is |
|---|---|---|
| A. Template | 40 | From the generator's truth templates with new parameters (drug class, condition, window, product, grouping). About 4 per domain across the 10 domains (CLN HOS REV QUA RX VBC BH ACC RWE DQ). |
| B. Independently authored | 40 | Written the way a client analyst, CMO or ops lead would ask, then given a precise definition. Include the feasible clinical workflow tasks (CT-01, CT-03 to CT-07, index date 2028-12-31) as multi-step items. Spread across domains. |
| C. Ambiguous or unsupported | 20 | 10 where the wording has two defensible readings (the correct response is to ask for clarification, naming the two readings); 10 whose required data isn't delivered (the correct response is "unsupported", naming the missing element). |

**Per question:** id (new prefix `PS-`), domain, group, answer_type (count, value with tolerance, patient_set, table,
clarify, unsupported), the question, and a definition precise enough for an analyst to reproduce from client data
(events that count, de-duplication, time anchor, scope, as_of).

## Keys
- The generator writes every key from source data; Agere never writes or reads keys.
- Two independent derivations per key must reconcile. Send disagreements to Mike to adjudicate.
- Keys stay in private/. Publish an answer-key digest (sha256) with the questions so every run manifest can pin it.
- Scorer: group C must score "clarify" or "unsupported" as correct and any numeric answer as wrong. Report precision on
  answered items and coverage separately. Bump SCORER_VERSION and add fixture cases for the two new types.

## Splits (frozen before any contestant runs)
- **dev (30):** 12 A, 12 B, 6 C. Agere may see per-question results for these.
- **calibration (70):** 28 A, 28 B, 14 C. Aggregates only, scored once per contestant; used for the paid comparison and
  for abstention thresholds.
- Choose the split with a fixed seed and publish it with the questions.

## After delivery (Agere side)
1. `bench/question_set.py` builds `bench/questions/pilot_dev.jsonl` and `pilot_cal.jsonl`; freeze the manifest.
2. Run the stack on dev only. Fix things where they live, never per question.
3. One paid comparison on calibration: Agere, a cheap analyst and a strong analyst, 3 fresh repeats each, replay off,
   every cost counted, intervals clustered by intent.
