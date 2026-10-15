# Request to the synthetic-warehouse generator: truth-backed question sets

**From:** Agere. **For:** v2.9, or v2.10 if v2.9 is frozen.

## Why
Hand-written benchmark references go stale whenever the warehouse changes. heldout9 was written on v2.0 and scored on v2.8. On 18 of 60 questions, Agere and an independent Opus-plus-semantic-layer baseline computed the same answer and the reference disagreed. A stale answer key makes any accuracy number meaningless. The sealed eval scorer doesn't have this problem, because its truth comes from the simulation itself.

## Ask
A sealed question set whose answers come from the generator's own truth, scored by the same sealed mechanism as `ecosystem.eval_scorer`.

1. **About 150 natural-language questions**, balanced across healthcare domains, about 15 each:
   - clinical care
   - hospital operations (ED/inpatient/observation counts, LOS, disposition, census)
   - revenue cycle (paid, denied, reversed, days to payment, CARC)
   - quality and safety (measures, readmissions planned vs unplanned)
   - pharmacy (fills picked up, adherence/PDC, therapy events)
   - population health / VBC (attribution, PMPM, HCC)
   - behavioral health (PHQ-9, depression)
   - access (appointments, no-shows, lead times, wellness visits)
   - life sciences / RWE (new users, persistence, lines of therapy)
   - data quality / feeds (late, duplicate, corrected records)
2. **Every answer computed from truth** (`private/eval_truth/` or the simulator), at the release as_of. Each answer comes with its definition, stated so a real analyst could reproduce it from the client data: which events count, de-duplication, time anchor. Prefer questions whose truth is *recoverable* from client-visible data. Mark the rest as `not_recoverable`, scored separately as context.
3. **Answer types:** count, value (with tolerance), patient_set (P/R), table (keyed rows). Also include **unsupported** questions whose prerequisite data truly isn't delivered, with the missing element named.
4. **Sealed:** questions are public. Answers live only in `private/`. Scoring is `python3 -m ecosystem.eval_scorer --questions <predictions.jsonl>`, aggregates only, with the same small-cell rules and `--sample-release` patient scope.
5. **Versioned with the release:** answers are regenerated for every release, so they never go stale.

## Agere side
Agere writes `question_answers.jsonl` as `{id, answer_type, count | value | patient_ids | table, status, interpretation}`, matching Agere's bench answer contract. Agere never reads the answers.
