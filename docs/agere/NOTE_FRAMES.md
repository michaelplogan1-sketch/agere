# Note frames (v1, 2026-10-23)

Typed readings of clinical text for the question spec's `note` predicate (QUESTION_SPEC.md, Addition 1). Code: `clinical_core/agere/clinical/note_frames.py` (schema, validation, interface), `note_evidence.py` (passages), `note_questions.py` (System One questions, combine rules). Labeled passages, labeling guide, calibration and the dev check are in `tools/note_labels/`.

## How it works

1. A question about what a chart says becomes a predicate: **frame**, **concept**, **statuses**, optional **detail**, **window**, **combine**.
2. `note_evidence.retrieve` finds candidate passages (matched sentence plus one sentence each side, a short document whole) in the patient's notes, HIE documents, scanned/OCR text and other clinical text streams, bounded per patient and per record, deterministic.
3. Each passage gets one System One status question (Jev; Perplexity when Jev's calibrated probability is under the field's threshold or it says unknown). Passages whose status can satisfy the predicate get detail questions, only for the constrained fields (`answer_patient` reads every field).
4. Code combines passage readings (`any | latest | first | all | conflict | trend`). A passage below threshold is undecided; an undecided passage that could change the answer makes the patient `unknown` (never "no"), and a count over patients is a range.

## Statuses (shared by every frame)

| status | definition |
|---|---|
| `documented` | The passage states that the PATIENT has, had in this encounter, did, or received the thing now: an affirmative statement about the patient that is current as of the note date (or an event the note reports as having occurred at or just before the note date). |
| `denied` | The passage explicitly states the thing is absent or did not happen for the patient: "denies", "no", "not taking", "negative for", "does not recognize this as a current medicine", "NKDA". Silence is not denial. |
| `possible` | The passage is uncertain whether the patient has or did it: suspected, probable, rule-out pending, differential, "concern for", "may have", "cannot exclude", a question that remains open. |
| `planned_not_done` | The passage recommends, orders, schedules, offers, requests or intends the thing for the patient but does not show it carried out, including offered-and-declined, due, overdue, or deferred. |
| `historical` | The passage places the thing in the patient's past and not as current at the note date: "history of", "resolved", "previously", "in 2027", "was on X until March", a closed episode. |
| `other_person` | The passage is about someone other than the patient (family member, relative, caregiver, household member). |
| `not_mentioned` | The passage says nothing about this concept for the patient: a retrieval miss, or only a hypothetical or conditional mention ("return if fever", "evaluate for X", risk teaching) that does not state the patient has it. |

Result-only value: `unknown` (undecided). `not_mentioned` also covers a passage that merely matched a word, or only a hypothetical/conditional mention ("return if fever"), which states nothing about the patient.

## Combine rules

| rule | meaning |
|---|---|
| `any` | The predicate holds when ANY retrieved passage reads with a status in `statuses` (and matching detail). |
| `latest` | The most recent dated passage that addresses the concept decides the patient value. |
| `first` | The earliest dated passage that addresses the concept decides the patient value. |
| `trend` | Numeric values in dated passages are ordered by date; the patient value is the direction of change (increased | decreased | stable) with first and last values. Needs two dated values. |
| `conflict` | Holds when passages address the concept with contradictory readings (affirmed in one, denied in another, or different values); the patient value is documented when a conflict exists. |
| `all` | Holds only when EVERY passage that addresses the concept has a status in `statuses` (at least one must). |

`conflict` returns status `documented` with `detail.conflict = true` when passages disagree (affirmed vs denied, or different values/dates), else `not_mentioned` with `conflict = false`. `trend` takes `detail.trend` in the predicate (increased | decreased | stable; stable means under 5% change).

## Frames and their closed detail options

### `condition` - Condition or diagnosis

Concept kind: condition. Default combine: `any`. Question stem: "Does the passage say the patient has (or had) the condition ..."

| detail field | kind | options / how read |
|---|---|---|
| `acuity` | choice | `acute`, `chronic`, `unspecified` |
| `severity` | choice | `mild`, `moderate`, `severe`, `unspecified` |
| `course` | choice | `new`, `in_remission`, `persistent`, `worsening`, `improving`, `stable`, `resolved`, `intermittent`, `unspecified` |
| `assessment` | multi | `monitored`, `evaluated`, `assessed`, `treated` |
| `stage_or_type` | quote | the sentence that gives it |
| `event_time` | time | HH:MM picked from clock times in the passage |

### `symptom` - Symptom or complaint

Concept kind: symptom. Default combine: `any`. Question stem: "Does the passage say the patient reports or has the symptom ..."

| detail field | kind | options / how read |
|---|---|---|
| `severity` | choice | `mild`, `moderate`, `severe`, `unspecified` |
| `course` | choice | `new`, `in_remission`, `persistent`, `worsening`, `improving`, `stable`, `resolved`, `intermittent`, `unspecified` |
| `trigger` | choice | `exertion`, `rest`, `positional`, `meal_related`, `medication_related`, `none_described` |
| `duration` | quote | the sentence that gives it |
| `event_time` | time | HH:MM picked from clock times in the passage |

### `medication_event` - Medication event

Concept kind: medication. Default combine: `latest`. Question stem: "Does the passage describe a medication event (start, stop, dose change, hold, switch, continuation, taking) ..."

| detail field | kind | options / how read |
|---|---|---|
| `event` | choice | `started`, `stopped`, `changed_dose`, `held`, `switched`, `continued` |
| `reason` | multi | `side_effect`, `cost`, `ineffective`, `patient_choice`, `contraindication`, `course_completed`, `clinician_decision`, `no_longer_indicated`, `other_with_quote` |
| `direction` | choice | `increased`, `decreased`, `unspecified` |
| `adherence` | choice | `taking_as_directed`, `taking_differently`, `not_taking`, `not_stated` |
| `initiated_by` | choice | `clinician`, `patient`, `pharmacy_or_plan`, `unspecified` |
| `reason_not_done` | choice | `patient_refusal`, `medical_contraindication`, `system_or_access_reason`, `not_indicated`, `clinician_decision`, `other_with_quote` |
| `amount` | number | number picked from candidates extracted from the passage |
| `sig` | quote | the sentence that gives it |
| `event_time` | time | HH:MM picked from clock times in the passage |

### `procedure_test` - Procedure, test or study

Concept kind: procedure. Default combine: `latest`. Question stem: "Does the passage describe the procedure, test or study being ordered, done or reported ..."

| detail field | kind | options / how read |
|---|---|---|
| `category` | choice | `imaging`, `laboratory`, `pathology_biopsy`, `endoscopy`, `cardiac_test`, `surgery_procedure`, `screening`, `other` |
| `result` | choice | `normal`, `abnormal`, `indeterminate`, `pending`, `not_reported` |
| `result_text` | quote | the sentence that gives it |
| `performed_on` | date | date picked from dates in the passage (or the note date) |
| `reason_not_done` | choice | `patient_refusal`, `medical_contraindication`, `system_or_access_reason`, `not_indicated`, `clinician_decision`, `other_with_quote` |
| `event_time` | time | HH:MM picked from clock times in the passage |

### `finding_value` - Measured value or score

Concept kind: measurement. Default combine: `latest`. Question stem: "Does the passage report a measured value, score or finding for the patient ..."

| detail field | kind | options / how read |
|---|---|---|
| `value` | number | number picked from candidates extracted from the passage |
| `unit` | quote | the sentence that gives it |
| `date` | date | date picked from dates in the passage (or the note date) |
| `kind` | choice | `vital_sign`, `laboratory`, `screening_score`, `imaging_measure`, `anthropometric`, `other` |
| `interpretation` | choice | `normal`, `abnormal`, `high`, `low`, `at_goal`, `not_at_goal`, `not_stated` |
| `event_time` | time | HH:MM picked from clock times in the passage |

### `adverse_event` - Adverse event, allergy or intolerance

Concept kind: adverse_event. Default combine: `any`. Question stem: "Does the passage describe the adverse event, allergy, intolerance or injury for the patient ..."

| detail field | kind | options / how read |
|---|---|---|
| `kind` | choice | `allergy`, `intolerance`, `side_effect`, `procedural_complication`, `fall`, `medication_error`, `hospital_acquired`, `other` |
| `agent` | quote | the sentence that gives it |
| `reaction` | quote | the sentence that gives it |
| `severity` | choice | `mild`, `moderate`, `severe`, `life_threatening`, `unspecified` |
| `outcome` | choice | `resolved`, `ongoing`, `hospitalization`, `death`, `unknown` |
| `count` | count | whole number picked from candidates |

### `care_process` - Care process or service

Concept kind: care_process. Default combine: `any`. Question stem: "Does the passage document the care process (referral, counseling, goals-of-care talk, directive, coordination, discharge teaching, screening, immunization, rehabilitation) ..."

| detail field | kind | options / how read |
|---|---|---|
| `kind` | choice | `referral`, `education_counseling`, `goals_of_care_discussion`, `advance_directive`, `code_status`, `care_plan`, `care_coordination`, `transition_of_care`, `medication_reconciliation`, `discharge_instruction`, `follow_up_visit`, `palliative_hospice`, `home_health`, `rehabilitation`, `screening_performed`, `immunization_given`, `case_management`, `interpreter_used`, `other` |
| `code_status` | choice | `full_code`, `dnr`, `dni`, `dnr_dni`, `comfort_measures_only`, `limited_interventions`, `not_specified` |
| `directive` | choice | `living_will`, `polst_molst`, `healthcare_proxy`, `dnr_order`, `none_on_file` |
| `outcome` | choice | `completed`, `ordered_or_referred`, `declined`, `unable`, `deferred` |
| `done_on` | date | date picked from dates in the passage (or the note date) |
| `topics` | multi | `risk_factors`, `warning_signs`, `emergency_activation`, `follow_up`, `medications`, `diet`, `activity`, `smoking_cessation`, `self_monitoring`, `other` |
| `event_time` | time | HH:MM picked from clock times in the passage |
| `reason_not_done` | choice | `patient_refusal`, `medical_contraindication`, `system_or_access_reason`, `not_indicated`, `clinician_decision`, `other_with_quote` |

### `social_need` - Social need or barrier

Concept kind: social_need. Default combine: `any`. Question stem: "Does the passage say the patient has the social need or barrier ..."

| detail field | kind | options / how read |
|---|---|---|
| `domain` | multi | `food_insecurity`, `housing_instability`, `homelessness`, `inadequate_housing`, `transportation_insecurity`, `utility_insecurity`, `financial_strain`, `medical_cost_burden`, `material_hardship`, `employment`, `education_literacy`, `social_isolation`, `stress`, `interpersonal_violence`, `elder_abuse`, `digital_access`, `language_barrier`, `legal`, `child_care`, `caregiver_strain`, `caregiver_role`, `incarceration`, `veteran_status`, `substance_use`, `health_insurance`, `discrimination`, `other` |
| `response` | choice | `referral_made`, `resources_provided`, `counseling`, `screened_only`, `patient_declined`, `need_resolved`, `none_documented` |
| `severity` | choice | `mild`, `moderate`, `severe`, `unspecified` |

### `function_cognition` - Function, cognition or safety

Concept kind: function. Default combine: `any`. Question stem: "Does the passage describe a problem or status in the patient's function, mobility, cognition or home safety ..."

| detail field | kind | options / how read |
|---|---|---|
| `domain` | choice | `mobility_gait_balance`, `self_care_adl`, `instrumental_adl`, `falls`, `memory_cognition`, `vision`, `hearing`, `swallowing_speech`, `continence`, `frailty`, `home_safety`, `mood`, `caregiver_support`, `other` |
| `level` | choice | `independent`, `needs_some_help`, `dependent`, `impaired`, `unspecified` |
| `device` | choice | `cane`, `walker`, `wheelchair`, `none`, `other`, `not_stated` |
| `raised_by` | choice | `patient`, `family_or_caregiver`, `clinician`, `screening_tool`, `unspecified` |
| `count` | count | whole number picked from candidates |

### `preference_refusal` - Preference, refusal or decision

Concept kind: decision. Default combine: `latest`. Question stem: "Does the passage record the patient's decision, preference or refusal about the thing ..."

| detail field | kind | options / how read |
|---|---|---|
| `decision` | choice | `accepted`, `declined`, `deferred`, `undecided`, `requested` |
| `reason` | multi | `cost`, `side_effect_concern`, `belief_or_values`, `logistics_transport`, `prefers_alternative`, `not_ready`, `family_decision`, `comfort_focused_goals`, `other_with_quote` |
| `target` | choice | `medication`, `procedure`, `test`, `vaccine`, `service_referral`, `lifestyle`, `life_sustaining`, `other` |

### `plan_followup` - Plan, follow-up or disposition

Concept kind: plan. Default combine: `latest`. Question stem: "Does the passage state a plan, follow-up, return instruction or disposition for the patient ..."

| detail field | kind | options / how read |
|---|---|---|
| `item` | choice | `follow_up_visit`, `referral`, `test_order`, `medication_plan`, `monitoring`, `call_back`, `return_precautions`, `education`, `disposition`, `goal` |
| `timeframe` | quote | the sentence that gives it |
| `due_on` | date | date picked from dates in the passage (or the note date) |
| `disposition` | choice | `home`, `home_health`, `skilled_nursing`, `inpatient_rehab`, `hospice`, `admitted`, `transferred`, `left_against_advice`, `expired`, `other`, `not_stated` |
| `responsible` | choice | `patient`, `clinician`, `care_team`, `unspecified` |

### `note_contradiction` - Contradiction between notes

Concept kind: proposition. Default combine: `conflict`. Question stem: "Does the passage state something about the proposition ..."

| detail field | kind | options / how read |
|---|---|---|
| `base_frame` | choice | `condition`, `symptom`, `medication_event`, `procedure_test`, `finding_value`, `adverse_event`, `care_process`, `social_need`, `function_cognition`, `preference_refusal`, `plan_followup` |
| `axis` | choice | `status`, `value`, `date`, `any` |

## Coverage research

Method: enumerate what each public chart-abstraction source asks of the chart, map every item to a frame (and detail), and record the gaps. Every gap became a schema change (listed below) or a documented exclusion. The CT.gov sample is a real 200-line draw (reproducible: `tools/note_labels/ctgov_criteria_sample.json`, tags in `coverage_ctgov.py`); the other sources are item lists I wrote from the published specifications (web search summaries of the CMS/Joint Commission/NCQA/Gravity/n2c2 pages, not full manual downloads) so their percentages describe the lists, not the manuals page by page. Tags were assigned by one labeler (me).

| source | items sampled | expressible by frames | structured-table (SQL) | not a chart question | gap |
|---|---|---|---|---|---|
| ClinicalTrials.gov eligibility lines (12 conditions, completed studies, seeded draw) | 200 | 163 | 11 | 26 | 0 |
| CMS SEP-1 abstraction elements (sepsis bundle) | 14 | 14 | 0 | 0 | 0 |
| Joint Commission STK stroke measure elements | 16 | 15 | 1 | 0 | 0 |
| HEDIS chart-review items (COA, TRC, readings, history, exclusions) | 22 | 22 | 0 | 0 | 0 |
| HCC / MEAT documentation criteria | 10 | 9 | 1 | 0 | 0 |
| Registry abstraction (GWTG-HF / NCDR style data elements) | 19 | 19 | 0 | 0 | 0 |
| Gravity Project SDOH domains and process steps | 23 | 23 | 0 | 0 | 0 |
| Assertion attributes (NegEx/ConText, i2b2-2010 classes, n2c2-2022 CMED dimensions, n2c2-2018 entity types) | 28 | 28 | 0 | 0 | 0 |

ClinicalTrials.gov: of 174 lines that a chart could answer, frames express 163 (93.7%) and structured tables the other 11 (age, sex, site, admission class): 100% together; 26 lines are not chart questions (consent, investigator judgment, willingness, study logistics). Lines per frame: condition 59, finding_value 37, medication_event 26, procedure_test 15, social_need 8, adverse_event 5, plan_followup 5, care_process 3, function_cognition 2, symptom 2, preference_refusal 1.

### Mapping notes

- **SEP-1**: severe sepsis / septic shock / suspected infection / organ dysfunction = `condition` (`possible` for suspected); initial and repeat lactate, blood culture = `procedure_test` (+ `event_time`); broad-spectrum antibiotic, 30 mL/kg crystalloid (`amount`), vasopressor = `medication_event`; repeat volume status / tissue perfusion reassessment = `procedure_test`/`care_process`; refusal or comfort care = `preference_refusal`/`care_process.code_status`. The 3-hour and 6-hour clocks are arithmetic over `event_time`/`date` details done by the caller; the frame supplies the times.
- **Stroke**: dysphagia screen before oral intake and its documented reason for not doing it = `procedure_test.reason_not_done`; antithrombotic by day 2 and at discharge, statin, anticoagulation for AF, thrombolytic and why not = `medication_event` (+`reason_not_done`); stroke education = `care_process` with `topics`; rehabilitation assessed = `care_process.kind=rehabilitation`; NIHSS = `finding_value`; last-known-well = `symptom.event_time`; arrival time = structured (encounter table).
- **HEDIS**: advance care plan, medication review/reconciliation, notification of admission, receipt of discharge information, patient engagement = `care_process`; functional status assessment = `function_cognition`; pain assessment, BP and HbA1c readings = `finding_value`; screening history with date = `procedure_test` (`historical`, `performed_on`); hospice/palliative = `care_process.palliative_hospice`; frailty/advanced illness/ESRD/pregnancy = `condition`; transplant/dialysis = `procedure_test`; documented statin exclusion causes (myalgia, rhabdomyolysis) = `adverse_event`.
- **MEAT**: monitor/evaluate/assess/treat = `condition.assessment`; stage/type = `condition.stage_or_type`; remission = `condition.course=in_remission`; "history of" vs current = status `historical` vs `documented`; face-to-face visit, signature and credentials are note metadata (structured), not frame readings.
- **Gravity**: each domain is a `social_need.domain` option; screening/referral/resources/counseling/resolved = `social_need.response`; goal = `plan_followup.item=goal`.
- **Assertion standards**: negation = `denied`; experiencer = `other_person`; temporality = `historical` / `documented` / `planned_not_done` (future); certainty = `possible`; i2b2 "conditional" (symptom only on exertion) = `documented` + `symptom.trigger`; i2b2 "hypothetical" = `not_mentioned`; CMED action/actor = `medication_event.event/direction/initiated_by`; n2c2-2018 reason and ADE = `medication_event.reason` and `adverse_event`; strength/dose/frequency/route/duration = `medication_event.amount` and `sig`.

### Schema changes the coverage check forced

- `event_time` (HH:MM) on condition, symptom, medication_event, procedure_test, finding_value, care_process (SEP-1, stroke, STEMI clocks).
- `medication_event.reason_not_done`, `amount`, `sig`; `procedure_test.reason_not_done`; `care_process.topics` (education topics), `reason_not_done`.
- `social_need.domain`: `substance_use`, `caregiver_role`, `health_insurance`, `discrimination`; `response`: `need_resolved`.
- `plan_followup.item`: `goal`. `condition.course`: `in_remission`.
- `not_mentioned` widened to cover hypothetical/conditional mentions (i2b2 "hypothetical").
- `combine=trend` takes a pseudo-detail `trend`.

### Documented exclusions (not frames)

- Age, sex, birth date, site, admission class, insurance type: structured tables.
- Consent, willingness, investigator judgment, study logistics, genotype inclusion, contraception counseling for trial entry: not chart questions.
- Clock arithmetic (door-to-needle, within 3 hours of presentation): the caller computes it from `event_time` and `date` details.
- Clinical judgment about eligibility ("clinically significant", "in the opinion of the investigator"): not answerable from text; returned as `unknown`.

## Labeled passages and agreement

484 real warehouse passages (44 per assertion frame, 11 frames; seeded stratified draw over 8-18 concepts per frame with extra weight on negated, historical, family and planned wording; patients named in the dev set's bucket-2 dev questions excluded). Each was labeled twice: pass 1 by the set's author, pass 2 blind by four separate fresh sessions that saw only the guide and the passage (`tools/note_labels/LABEL_GUIDE.md`, `p1_*.txt`, `p2_agent*_labels.json`). First check: 87.2% status agreement; medication_event was 45.5% because the guide did not say whether "reported use on 0 of 14 days" is `denied` or `documented`. The guide (v2) now states the medication rules and a fresh blind session relabeled the 44 medication passages: 93.2%. Overall 91.5% status agreement (87.2% before the clarification); per frame: condition 95.5, symptom 93.2, medication_event 93.2, procedure_test 97.7, finding_value 90.9, adverse_event 88.6, care_process 88.6, social_need 75.0, function_cognition 95.5, preference_refusal 100, plan_followup 88.6. Detail fields both passes labeled agree 285 of 307 (92.8%). `python -m tools.note_labels.build_labels` reproduces this; only status-agreed items enter calibration. social_need is lowest because "difficulty obtaining the prescription ... contact the pharmacy about cost" reads as a cost need to one labeler and not to the other; those items are excluded, not forced.

Caveat: both labelers are the same model family, and the v2 clarification was written after seeing where pass 1 and pass 2 differed, so the medication figure measures consistency under the written rule, not independent judgment. `note_contradiction` has no passage-level labels (its readings are the base frame's); its combine logic is tested with synthetic readings only.


## Live measurement status

System One keys (`TYPESAFE_API_KEY`, `PERPLEXITY_API_KEY`) were not available to the builder session, so no live Jev/Perplexity call has been made and
`config/agere/note_frames_calibration.json` does not exist (the reader runs with identity shifts and the default 0.8 threshold until it is fitted).
Ready to run once keys are present: `python -m tools.note_labels.calibrate collect` (about 900 calls, ledger-cached, at most 4 concurrent) then
`calibrate fit`; `python -m tools.note_labels.dev_bucket2 --db ... --live` for the end-to-end score. Without keys the dev check measures retrieval only:
see below.

### Dev bucket-2 retrieval (no model): hand predicates for the 20 dev questions

Key evidence quote found in a retrieved passage (same record id, quote text): 15 of 17 questions fully, mean recall 0.912; the 3 `not documented` keys retrieve zero
passages. Misses: b2-03 (the referral half of the answer needs a "resources/referral" concept the single term "financial" does not reach) and b2-24 (only the weak
term "started" was given; the key wants the medication names). `tools/note_labels/dev_bucket2_retrieval.json` has the rows.

## Open concepts and source cues (2026-10-23)

Code: `clinical_core/agere/clinical/note_open.py` (cue lexicons, class members, source cues), `note_questions.evaluate_open` / `combine_open`, `chart_notes.build` / `render`.

**Open concept.** A question that names a CLASS ("What drug intolerance do the notes record?", "Anything about social or financial barriers?", "What symptoms has the patient reported?") builds a predicate whose concept is `{"term": <the user's phrase>, "open": true, "class": "adverse" | "social" | "symptom" | "response"}`. The class word is never searched for. Passages are retrieved by the frame's cue lexicon (adverse events: intolerance/allergy/side effect/adverse reaction/caused/stopped due to ...; social: one cue set per Gravity domain, every `social_need.domain` option is covered; symptoms: a symptom vocabulary). In each passage code names the candidate findings from the concept library (drugs and brands, allergens, social domains, symptoms); a passage with a cue and no known member is named by its quoted cue sentence. Each named finding gets the usual typed status question (calibration keys unchanged) plus the frame's open detail fields. A reading under its calibrated threshold is an UNDECIDED finding, listed as undecided and never counted; a status outside the question's statuses (`denied`, `not_mentioned`, `other_person`, `planned_not_done`) is not a finding. The answer lists each distinct finding with status, detail, first and last date, record ids and quotes. Passages identical apart from digits are read once and the reading is copied to the other records (they are all listed).

**Source cues.** `note_open.source_cues` maps wording to `sources` (outside report/records -> scanned + HIE + other text; scanned/OCR/fax -> scanned; HIE/CCD -> hie_document) and `note_types` (discharge_summary, ed_note, radiology_report, pathology_report, procedure_report, consult_note, annual_wellness_visit, telephone_message, admission_note, nursing_note, lab_report). A note type matches the record's note type, stream or document title. These are predicate keys (`sources`, `note_types`), the typed chart request repeats them as `note_sources`, `note_types`, `note_dates`, `open_concepts`.

**Dated note.** "the 2029-08-05 note", "the 2027-10-28 discharge summary", "the ED note from August 5, 2029", "the note dated 08/05/2029" bind that day; "later than" a dated note starts the next day; a date that only dates an event ("stopped on 2029-08-05") does not bind a note.

**Multi-part questions.** A later question that only points back ("What was done about it?") is not the frame cue: the first question is read, then care_process and plan_followup (open, class `response`) are read over the same and later notes with the finding's own records first. Both parts are stated in words with quotes; with no finding the response part says so. A result question with no date ("the impression on the outside report") lists every matching report with its own reading.
