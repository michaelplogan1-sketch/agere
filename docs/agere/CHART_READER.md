# Chart reader (builder C)

Answers a question about one patient from every structured source, or from the notes through note frames (`QUESTION_SPEC.md`, Addition 2).

## Modules
- `patient_ids.py` recognizes SYN-/WHP-/P- ids (and enrollment member ids) by exact match against the patient table; case, hyphen and underscore tolerant; never guesses (unknown, partial and ambiguous ids return a clarification). Pronouns bind the previous turn's subject.
- `chart_intents.py` is the deterministic grammar (intent, domain, concept, window, ordinal). Open slots go to System One as closed-list choices (`chart_reader.fill_gaps`).
- `chart_queries.py` are the executors; `chart_summary.py` the care gaps and the patient brief; `chart_notes.py` the note routing, predicates and rendering over `note_frames.answer_patient`.
- `chart_reader.py` is the interface: `resolve_subject`, `answer`, `ask`, `tier`. `chart_reader_op.py` is the `chart_reader` graph operation.

## Intents
latest_value, trend, list, count_in_window, last_event, timeline, status, sum, summary (plus the ordinal "the one before", and extremes "highest / lowest ever").
Domains: labs and vitals, medication list, fills (last, count, PDC), prescriptions (never filled), returned fills, ED / inpatient / observation (utilization_event), 30-day readmission (stay), visits, appointments and no-shows, immunizations, cancer screening, annual wellness, allergies, conditions (governed evidence), problem list, tobacco, social needs, purchased SDOH, SVI, claims and spend, pharmacy claims, coverage, practice, demographics, vital status, care gaps, notes.

## Registrations (one line each, made on this branch except the cascade tier)
- `engine_suite.MODULES['chart_reader'] = 'chart_reader_op'` (done): the graph operation.
- `analysis_graph.headline`: returns `answer['chart_headline']` when present (done, 2 lines).
- `decision_context.population`: a question that names one patient and uses no cohort wording is not living-only (done).
- `conversation.py` (done, 3 small hooks): `handle_compose` calls `patient_ids.rebind(path, conversation_id, question)` (rewrites pronouns, sets `patient_ids.current_prior()`); `summaries` stores `answer['subject']` in `answer_summary`; `record` calls `patient_ids.remember`.
- **proposers.py (B or coordinator)**: add to `TIERS` `'chart_reader': ('free (grammar + SQL); System One only for open slots and note passages', 'single-patient chart answers; exact ids; dev 20/20, SQL-oracle 0 disagreements', 'graph')`, to `STAGES` first stage `reuse_certified` (before `exact_local_plan`: a named patient is never a cohort question), and
  `def tier_chart_reader(self): from .chart_reader import tier; return self.graph('chart_reader', tier(self.stack, self.question, self.population, ledger=self.ledger))`.
- Spec path (B): subject via `chart_reader.resolve_subject(stack, text)`; execution via `chart_reader.answer(stack, spec, as_of=..., ledger=...)` with `spec = {'subject': {...}, 'question': text}` or a typed `chart` (`validate_chart`): `{'intent', 'domain', 'concept', 'window', 'ordinal', 'attribute', 'mode'}`; note questions pass `spec['note'] = [note predicates]`.

## Answer contract
`status` (answered | unsupported | needs_clarification), `answer_type`, `value` / `unit` / `count`, `headline` (value, unit, dates), `table`, `sources` (table, record_id, date), `patient_ids`, `subject` (with the typed frame, for follow-ups), `intent`, `read_back`, `decision_context` receipt, `deceased` (set for a named deceased patient), `notes`, `cost`.

## Checks
`python -m tools.chart_reader_eval` scores the dev buckets with the dev-set scorer; `python -m tools.chart_reader_oracle --n 30` cross-checks 36 question templates against independent SQL from `tools/dev_set/definitions.py`; `python -m unittest test_chart_reader`.

## Open concepts, source cues, multi-part (2026-10-23)
See NOTE_FRAMES.md "Open concepts and source cues". The typed chart carries `note_sources`, `note_types`, `note_dates`, `open_concepts`, `asks_response`; `chart_notes.split_parts` / `render_open` / `focus_quote` render findings in words with quotes. `tools/chart_reader_eval` also reports `lead_verdict` (the scorer applied to the headline alone, where the Yes / No lead is the first word).

## Residual classes (2026-10-23)
- **Condition control.** `config/agere/condition_measures.json` maps a library condition to the measures that govern it (diabetes: HbA1c; hypertension: systolic and diastolic; CKD: eGFR and UACR; heart failure: NT-proBNP; hyperlipidemia / CAD: LDL; depression: PHQ-9; ...), each with its source. `chart_intents.condition_control`: a condition with control or direction wording and no measure of its own becomes a lab question over those measures (trend when the wording gives a direction, else latest); a lab question that names its condition keeps it. The typed concept carries `condition`, `condition_quote` and `concepts`, so the literal check finds both words. `chart_queries.condition_measures_answer` states one reading per measure.
- **How many over notes.** A note question that asks how many has one predicate (the first frame) and returns `answer_type: count`: the number a note states (latest stating note), else the distinct documented occurrences in the window (one per note date); an undecided reading is not a count.
- **Persistence after an event.** "A <date> note stopped X. Do later notes still list X?" / "after <event>, is X still ...": the dated note (or the stopping event) is read first as the anchor and cited; later notes are read with `combine: any`; Yes when any later passage lists X without stopping, holding or switching it, else No with the ending records. The typed chart carries `anchor_dates`, `asks_persistence`, `asks_count`. ISO dates in a question are dates for the number check (`local_normalizer.numbers`), not year ranges.
- `python -m tools.chart_reader_eval --live --gates` also runs the service-side requirement ledger over the typed chart and the answer-shape check on every answer.
