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
