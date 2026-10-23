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
