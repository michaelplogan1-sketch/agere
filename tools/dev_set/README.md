# Development question set (v1)

150 questions that look like real healthcare-data questions (Innovaccer / Arcadia / Genie / chat-with-read-only-Epic style), with
answer keys that do not come from Agere's planners, and a seeded one-third held-out split. Use `dev` to develop and order the
cascade; use `heldout` sparingly to check you did not overfit. Synthetic data only
(`artifacts/agere-healthcare-v28-sample-0.30/stack/semantic.sqlite3`, as of 2030-05-27, opened read-only).

| bucket | n (dev / heldout) | covers | key |
|---|---|---|---|
| single_patient_structured | 30 (20 / 10) | latest labs and trends, active meds, last fill, PDC, ED / admissions, readmission, problem list, coverage, attribution, screening, immunization, allergy, social needs, claims paid, no-shows, BP control | SQL (exact values / lists) |
| single_patient_unstructured | 30 (20 / 10) | why a drug was stopped or refused, goals of care / code status, advance-directive status, falls, memory concern, social barriers, symptoms, ED / discharge / HIE / scanned (OCR) documents, notes that contradict each other, "not documented" | read from the text, labeled twice, exact quote + record id |
| population_clinical_quality | 30 (20 / 10) | A1c / BP control, GDMT gaps, readmissions, follow-up after ED, screening rates, care gaps, by practice / line of business | SQL, shares carry numerator and denominator |
| population_claims_pharmacy_sdoh | 30 (20 / 10) | spend, PMPM, high-cost claimants, out-of-network, denials, PDC, generics, rejects, returned to stock, purchased SDOH + social needs + SVI joined to clinical | SQL (values with units, sets, tables) |
| insight | 30 (20 / 10) | why / who / which / what is driving: ED rise, PMPM drivers, practice gaps, outreach priorities, cost concentration | rubric of required facts, each computed by SQL |

About 11 % are follow-ups (`follow_up_of` = parent id; the key carries a fully resolved `standalone` question) and 7 are
"the data does not contain this" (`answer_type: absent`, with the reason). A follow-up always shares its parent's split.

## Files
- `definitions.py` -- every definition (Mike 2026-10-23) as SQL fragments: living patients, latest vs any lab, "last year" = 12 months
  ending 2030-05-27 (from 2029-05-28), condition evidence (EHR + problem list + counted payer-claim diagnoses; diabetes also
  HbA1c >= 6.5, CKD also two eGFR < 60 at least 90 days apart; rule-outs excluded), spend (counts_in_metrics = 1), ED use, PDC, GDMT
  pillars, screening evidence, SDOH. Re-align a definition here and rebuild.
- `b1..b5_*.py` -- the questions and how each key is computed. `keys.py` -- engine: `python -m tools.dev_set.keys` rebuilds
  `questions.jsonl`; `--check` recomputes every key and compares (deterministic; seed 20261023).
- `labels_unstructured.json` -- bucket 2 labels with both passes. `run.py` -- runner and scorer. `test_dev_set.py` -- tests.

## How keys were made
- **Bucket 1, 3, 4**: plain SQL against the semantic layer using definitions.py. Patients are picked by seeded random from pools
  that mix rich (deep_chart) and sparse (claims-only) charts. PDC and one adherence share are computed in Python
  (`b1._pdc`): covered days with carry-forward, first fill to window end, >= 2 fills, adherent >= 0.8.
- **Bucket 2**: the labeler read the note text. Text lives in `record.payload` ($.text) for streams `notes` and `hie_documents`
  (`clinical_note` / `hie_document` hold metadata only) and in `scanned_document_text.text` for PDF-text and OCR documents.
  Each item was labeled twice independently: pass 1 by the set's author; pass 2 by a separate fresh model session that saw only the
  question and the documents. 42 candidates went through both passes: 41 agreed on first comparison and the 42nd (u39) differed only
  in format (a "yes" versus a sentence; same answer and evidence), so the measured disagreement rate is 0 % (2.4 % strict).
  30 items were kept (agreed ones, balanced across kinds); keys.py re-verifies every quote is an exact substring of the stored text.
  Honest limit: both labelers are the same model family and the notes are templated, so agreement is high partly because the
  items are clear; it is a check on reading errors, not on ambiguity of clinical judgment. `note_fact` (rules extraction) was
  used only to find candidate notes, never as truth.
- **Bucket 5**: each required fact is a SQL-computed number, ranking or term (`b5_insight.py`). Two items have a false premise
  ("why did ED use rise in Q3 2029?" -- it fell, 383 to 339; "does high SVI have worse diabetes control" -- it does not).

## Scoring (`run.py`)
count / value exact within the stated tolerance (a proportion also accepted as a percent); share = value within +-0.5 pt (or numerator /
denominator in the key); patient_set = exact set (a count-only answer is flagged, not credited); table = every expected row matched by value
(order checked when `order_matters`); list = every key item present; label / date / yes_no from the answer value or text; note =
answer match AND a supporting record id cited; absent = correct only if the service refuses (a made-up number is wrong); rubric = all required
facts present and no forbidden claim (the per-question `fact_share` is also reported). A refused or unanswered question is counted in
coverage and refusals, not as wrong; `accuracy` counts it as not correct, `precision_on_answered` does not.

Follow-ups: the service links a follow-up through `conversation_id` in the `/api/stack/compose` body, but in evaluation mode
(`X-Agere-Evaluation: 1`) it stores no conversation turn or saved cohort, so the parent is invisible to the follow-up. `--follow-ups
conversation` (default) sends the real chain and reports what happened; `--follow-ups standalone` sends the resolved question.

## Known limits and data gaps
- Questions name patients by id (the warehouse has no patient-name column; names appear only inside some note text).
- Rubric scoring is regex / number matching: it can miss a correct paraphrase or credit a coincidental number (`near` windows reduce that).
- The 12-month window starts 2029-05-28; claims and labs begin 2026-09, so "ten-year" lookbacks only see the observed window.
- Event counts (ED visits, admissions) include every patient; patient sets and rates use living, non-duplicate patients.
- Things people ask that this warehouse cannot answer (kept as `absent` items or noted): patient-experience / CAHPS scores, medical loss
  ratio / premium revenue, manufacturer rebates and net drug cost, star ratings and HEDIS submissions, HCC / RAF risk-adjustment revenue,
  cervical-cancer screening (no Pap / HPV data), genetic or genomic results.
- Smoke test (5 dev questions, 2026-10-23): the live service refused all five as `not_understood` -- patient ids are not parsed as
  entities by the current planners. That is a finding about the service, not the set.

## Composition set (split `dev2`)
`compose_questions.jsonl` (built by `compose_keys.py`) is a second development set for iterating on population / claims / insight
COMPOSITION without tuning on the original 100 dev questions and without touching `heldout`. Its split is always `dev2`; every row
carries a `family` (condition_combo, condition_lab, condition_med, utilization_window, share_denominator, grouped_breakdown, top_n,
group_comparison, claims_money, pharmacy, sdoh, follow_up, absent) and the same bucket names / key schema as `questions.jsonl`
(ids `c3-NN` clinical quality, `c4-NN` claims / pharmacy / SDOH, `c5-NN` insight).

    .venv/bin/python -m tools.dev_set.compose_keys --summary        # counts per bucket / family / answer type
    .venv/bin/python -m tools.dev_set.compose_keys --check          # recompute every key, compare with the file
    .venv/bin/python -m tools.dev_set.run --set compose             # live service (also --split dev2)
    .venv/bin/python -m tools.dev_set.run_inprocess --set compose   # in-process, Claude off (also --split dev2); add --ids / --bucket
    .venv/bin/python -m unittest tools.dev_set.test_compose_set

Keys are independent SQL (no product SQL builder is imported) against the decision context: living, de-duplicated patients; age from the
birth date; latest lab unless the question says ever / any; condition evidence incl. counted claim diagnoses and the diabetes / CKD lab
rules; Medicare = medicare + dual + medigap coverage; "on" a drug = a picked-up fill in the 12 months ending 2030-05-27; rolling year
(2029-05-27, 2030-05-27]; frequent ED = 3+ visits; high cost = $50,000+. Event counts (ED / inpatient / observation) are over living
patients; money counts every member's claims. Patient payer-type groups are commercial / Medicare / Medicaid by `patient.coverage_type`;
practice = PCP practice, else the attributing practice of the latest enrollment span. Insight items are rubrics of SQL-computed facts.
The test file checks that no question duplicates (or nearly duplicates) a non-held-out dev question; held-out lines are filtered out
before they are parsed, so overlap with held-out questions is not checkable and is avoided only by writing from the tables.
