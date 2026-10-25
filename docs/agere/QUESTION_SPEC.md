# Question spec (v1, 2026-10-23)

Every question becomes a typed **question spec** before anything runs. The spec is the existing typed request contract
(`request_plan`: `source`, `select`, `where`, `group_by`, `order_by`, `limit`, `unresolved`, optional `analyses`), with three
additions. One compiler turns a spec into SQL; the decision context (`decision_context.py`) is built into that compiler.
LLMs write specs, never SQL, on the main path.

## Why

Measured on 2026-10-23:
- the deterministic grammar under the decision context answered 26 of 26 dev questions exactly, in under 1 s, at $0;
- System One chose the right engine about 87% of the time from a closed list, but read structured criteria from summary sheets
  badly;
- Claude-written free-form SQL was slow (10–20 s), nondeterministic and often wrong, and result-summary confirmation was weak;
- the serial 34-tier cascade spent its budget before the best attempt ran.

## Addition 1: the `note` predicate (owner: builder A, `note_frames.py`)

A note predicate stands anywhere a predicate can (inside `where`, `and`/`or`/`not`, `exists`). System One reads the
evidence; code combines the readings.

```json
{"op": "note",
 "frame": "medication_event",
 "concept": {"term": "metformin", "codes": ["RxNorm:6809"], "quote": "metformin"},
 "statuses": ["documented"],
 "detail": {"event": "stopped", "reason": ["side_effect", "cost"]},
 "window": {"start": "2029-05-28", "end": "2030-05-27", "quote": "in the last year"},
 "combine": "any",
 "sources": ["clinical_note", "scanned_document_text", "hie_document"],
 "quote": "had metformin stopped because of side effects or cost"}
```

- **`frame`** is one of a fixed set: `condition`, `symptom`, `medication_event`, `procedure_test`, `finding_value`,
  `adverse_event`, `care_process`, `social_need`, `function_cognition`, `preference_refusal`, `plan_followup`,
  `note_contradiction`. The set lives in `note_frames.FRAMES`, and each frame declares its `detail` fields and their closed
  option lists.
- **`statuses`** are the shared assertion options, whose meaning never varies by frame:
  `documented | denied | possible | planned_not_done | historical | other_person | not_mentioned`.
  The predicate holds when the combined status is in `statuses`.
- **`combine`** says how several passages or notes reduce to one patient value:
  `any | latest | first | trend | conflict | all`.
- **The answer** (per patient) carries the status, the detail values, and each supporting passage's `record_id`, date,
  note type and exact quote. Unknown is never silently "no": an undecided patient makes a count a range, as in
  `decision_context` and `atomic_questions`.
- **Evidence** is the relevant sentences or passages with date and note type, retrieved by concept and window, not whole
  notes or summaries.

## Addition 2: the `subject` (single patient; owner: builder C, `chart_reader.py`)

```json
{"subject": {"kind": "patient", "patient_id": "SYN-67436663-A0F", "quote": "SYN-67436663-A0F"}}
```

- **Absent** means a population question (today's behaviour).
- **With a patient subject**, every patient-keyed relation binds `patient_id`. The deceased rule does not exclude a named
  patient, but a named deceased patient's answer says so.
- **Patient outputs** are ordinary `select` forms over patient-bound sources (latest value, list, count, timeline). A
  chart-level `note` predicate with `select: [{"kind": "note_answer"}]` returns the note frame answer itself (status,
  details, quotes).
- **Patient ids** are recognized from the warehouse's actual id formats (`P-…`, `SYN-…`, `WHP-…`), never guessed.
  A follow-up's pronoun ("her A1c") binds the prior turn's subject.

## Addition 3: read-back (owner: builder B, `spec_readback.py`)

`read_back(spec, catalog) -> str` deterministically renders the spec in plain English, including the decision-context
defaults applied. For example: "Counted living patients with a documented heart failure diagnosis (EHR or counted claim)
and no SGLT2 inhibitor fill in the 12 months ending 2030-05-27." It is shown with every answer and checked by System One,
part by part, against the question.

## Addition 4: composition constructs (2026-10-24; the compiler, the grammar and the spec writer share them)

These are typed forms the compiler accepts and the deterministic grammar produces (request_language + composition_language, claims_composition,
comparison_composition, sdoh_composition); the Haiku spec writer is told about them in `spec_fill.COMPOSITION_ADDENDUM`. Every quote is still a
literal slice of the question. A Boolean operation whose quote holds ONLY closed-class logic words (`request_plan.LOGIC_WORDS`: "both", "no",
"neither", "nor", "all at once", "at least two of these three") covers those words; every content word needs its own leaf predicate.

Predicates (anywhere a predicate can stand):
- `{"op": "at_least_of", "n": 2, "args": [p1, p2, p3], "quote": "at least two of these three"}` -- at least N of 2..12 predicates hold for the patient.
- `{"op": "sum_at_least", "table": "payer_claim", "field": "paid_cents", "where": pred, "amount": 50000, "scale": 0.01, "cmp": ">=", "quote": ...}` -- the
  patient's total of a money column over a related relation reaches an amount (`scale` converts cents to dollars; `cmp` is `>=` or `>`).
- `count_at_least` accepts `"distinct": "need"`: at least N DIFFERENT values of that column (two or more different social needs).
- Negation and exclusive-or are ordinary `not` / `and` / `or` trees: "A or B but not both" is `and(concept[A,B], not(and(A, B)))`; "neither A nor B"
  is `and(not A, not B)` with the quotes "neither" and "nor".

Dimensions and measures (in `select`):
- `{"kind": "cohort_group", "alias": "payer_type", "quote": [...], "groups": [{"label": "medicare", "where": pred, "quote": "Medicare (including dual and Medigap)"}, ...]}`
  -- a grouping dimension whose groups are named predicates (2..12; groups may overlap; a row in no group drops out). The label is the output value:
  numeric bands are `under_40`, `40_to_64`, `65_plus`; other labels are the snake-cased group wording. Groups that are values of ONE column are read as a
  plain grouping by that column restricted to the named values. A request over another relation joins `patient` (a `joins` entry) when a group reads a patient field.
- `{"kind": "count_related", "table": "utilization_event", "where": event_pred, "alias": "events"}` -- the total number of related rows of the counted patients
  (SUM over patients of their own count; stays count as episodes). With a ratio of scale 1000 or 100 it is a rate per patient group
  ("ED visits per 1,000 patients"): the denominator is every counted patient, with or without an event.
- `{"kind": "sum_related", "table": "payer_claim", "field": "paid_cents", "where": claim_pred}` -- the total of a money column over the related rows (the cost of a group of patients;
  with a ratio it is the average per patient, zero-claim patients included).

Reading rules the grammar applies (so a spec writer need not): "practice" in a ranking is the patient's primary-care practice (`pcp_practice_id`); a ranked or claims-grouped
dimension drops rows with no value for it; "Medicare (including dual and Medigap)" is `coverage_type in (medicare, dual, medigap)` and a note naming only one of them is declined;
an output that points back at another output ("how many fills was that") is declined, not guessed.

## Addition 5: analyst wording (2026-10-24 night; request_language, analyst_language, request_vocabulary, analyst_insights)

Measured on the independent dev3 set (written from public analyst and quality-measure sources, not from the grammar): population quality, claims and
insight questions in real analyst wording were mostly refused. The missing constructs are general; none reads a question's text.

Measure names (request_vocabulary + `Parser.governed`):
- Every library alias of a governed definition names it (`request_vocabulary.aliases()` reads the metric's `aliases`: "statin therapy for patients with diabetes",
  "PQI 90", "pdc for statins", "follow-up after ED visit"). A hand-written alias keeps its meaning when a name is claimed twice.
- Words that restate what the measure already is are consumed, never a constraint, and only when its definition states them: a kind word ("rate", "score",
  "PDC", "composite"), its own population ("for patients with diabetes", "share of diabetics who got ..."), its own window ("within 7 days", "7-day"), and a gloss after a
  colon ("Kidney health evaluation: share of diabetics 18 to 85 who got both an eGFR and a uACR in 2029"). `gloss_accounted` requires every content word and number to be in the
  definition (name, aliases, summary, denominator, numerator, value sets); direction words must agree (above / below). A number the definition states is carried by its digest.
- Analyst leads ("how are we doing on", "what was our performance on", "report on") are quoted with the measure.

Vocabulary and predicates (`request_language`, `analyst_language`, `claims_language`):
- Age-group nouns: children / kids / minors (under 18), seniors / older adults (65 and over); "65 and up". Deaths: "died in 2029", "deaths in 2029" (the death record by its own date).
- Outcome verbs before a status ("ended as no-shows"); a lifecycle word ("scheduled appointments ended as no-shows") names the rows, the outcome filters.
- "N or more active medications on their list" (`medication_list_entry`), "dx", "aren't on ...", "got BOTH an eGFR and a uACR in 2029" (the period covers both tests).
- Claims: place of service by name (urgent care 20, outpatient hospital 22, ambulatory surgery 24, skilled nursing 31, telehealth 02/10, independent lab 81; "inpatient" = a claim with an
  admission date), a drug named in a spend or fill question (`payer_pharmacy_claim.drug_name like %Ozempic%`), "reversed or recouped for dates of service in 2029" (a period after
  alternatives of one column covers all of them), "what did the plan pay for ...".
- Active coverage: an enrollment span open on the as-of date (compare op `null_or_gte`: the end date is missing or on or after it). A positive depression screen: the latest PHQ-9 of 10 or higher;
  a restated different cut-off is refused.
- Event shares: "what share of 2029 ED visits (were followed by | had) an office, primary care, specialty or telehealth visit within 7 days" is a ratio of rows of the event relation whose numerator is a
  `sequence` predicate anchored on the row itself (`analyst_language.event_share`); a visit list reads as one follow-up of any listed kind.
- "<measure> for A, B and C patients" and "<measure of A>, versus B" are the measure per named group (`measure_for_categories`, `contrast_ellipsis`).
- A lab leaf that bounds its results to a stated period and puts no threshold on the value ("had an eGFR in 2029") reads every result in the period: the latest-result rule applies to thresholds on a patient's
  current value (`request_plan.lab_presence_in_period`, `request_vocabulary.period_lab_reads`). An average or total over a patient attribute (average SVI, average age) describes living patients
  (`decision_context.patient_grain`).
- Spelling: a word that is neither a dictionary word nor a domain word, one edit from exactly one domain word with a common three-letter ending, is that word (`phrasing.fix_spelling`).

Insight shapes (`analyst_insights.py`, engine forms; every number states its numerator and denominator; groups below a stated minimum are not ranked):
readmission_drivers, use_concentration, evidence_gap, period_series, cohort_defined (a cohort the question itself defines: "I mean two or more ED visits in the last year but no admission"),
comorbidity_cost, drug_spend_drivers, outcome_rates (denials, pharmacy rejects by payer / place of service, with the usual reason), need_gap, group_vs_rest, condition_drivers,
segment_readmission, measure_by_group (worst and best practice on a governed measure), spend_mix, top_spenders; `yoy_trend` also reads "why did X jump in 2029 compared with 2028" and
says when the stated direction is not what the numbers show.

## How a spec is filled (owner: builder B)

Run in parallel, take the first spec that validates (`request_plan.compile_request` plus the literal check), and prefer
the cheapest when several validate together:
1. **The grammar** (`request_language`): free, instant.
2. **System One slot filling:** Jev or Perplexity choose from closed lists (concepts, code sets, measures, frames,
   statuses, windows, groupings) for the parts the grammar left open.
3. **One Haiku call that writes the spec JSON** (never SQL), with the decision-context block and the frame catalog in its
   prompt. One bounded repair from validator feedback. Sonnet only when Haiku's spec fails validation twice.

Then:
- **Verification:** System One checks each spec part against the question ("does the question mean the 12 months ending
  as-of?"). This replaces the result-summary cross-check for spec-path answers. A failed part is repaired or recorded as
  `unresolved`, never shipped silently.
- **Cache:** a verified spec is saved per question shape (the existing `request_shapes` / `shared_planner` stores), so a
  rewording reuses it for free. The cache is off in evaluation mode.
- **Free-form SQL writers** (`hosted_verified_sql`, `semantic_analyst`, `hybrid_analysts`, `frontier_graph_planner`,
  `stack_planner_propose`, `legacy_cost_ladder`, the Qwen rewrite and free-SQL tiers) stay registered but move after the
  spec path. Their answers are marked as free-form and need System One confirmation.

## Module boundaries (builders work in parallel; do not edit another builder's files)

| Builder | Owns | Must not edit |
|---|---|---|
| **A: note frames** | `note_frames.py`, `note_questions.py` (System One prompts and option lists), `note_evidence.py` (passage retrieval), `tools/note_labels/` (labeled examples, calibration), tests | `request_plan.py`, `request_language.py`, `proposers.py`, `shared_planner.py` |
| **B: spec path** | `request_plan.py` (the `note` op and `subject` compile hooks calling A's and C's executors via a registry), `spec_fill.py`, `spec_readback.py`, `spec_verify.py`, `proposers.py` ordering, `shared_planner.py` integration | A's and C's modules |
| **C: chart reader** | `chart_reader.py`, `patient_ids.py`, the single-patient outputs; tests | `request_plan.py` (registers through B's hook), A's modules (calls A's `answer_patient()`) |

**Interfaces** (stubs land first in `note_frames.py` and `chart_reader.py`, so B can integrate before A and C finish):
- `note_frames.evaluate(stack, predicate, patient_ids, *, as_of, ledger) -> {patient_id: {"status", "detail", "evidence": [...], "p"}}`
- `note_frames.answer_patient(stack, predicate, patient_id, *, as_of, ledger) -> same shape for one patient`
- `chart_reader.resolve_subject(stack, text) -> {"patient_id", "quote"} | None`
- `chart_reader.answer(stack, spec, *, as_of, ledger) -> answer contract` (status, value or table, sources, decision_context)

## Order of work today

1. This contract.
2. Builders A, B and C in parallel.
3. Integration: one 150-question dev run, scored per bucket.
4. Cascade order from measured per-bucket results.
5. The head-to-head against the fair LLM baseline (held-out split), then one sealed run.
