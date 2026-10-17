# Request to the generator: scorer fixes (2026-10-17)

From Agere's measurement repair (handoff 2026-10-17, plan step 1). The generator owns the scorer
(`ecosystem/eval_questions.py`, `_grade` ~line 1158); Agere only supplies failing cases and does not edit it.

## The cases
- Fixture: `bench/fixtures/scorer_cases.json` in clinical-insights-local (24 cases: one answer key, one prediction and
  the outcome a correct scorer gives).
- Runner (imports `_grade` only, read-only): `.venv/bin/python bench/scorer_cases.py` from clinical-insights-local.
- Today: **5 of 24 pass**. The 5 that pass are guards that must keep passing.

| Area | Cases | What happens today | What it should do |
|---|---|---|---|
| Extra / missing rows | S01–S05 | A top-5 table with 9 rows passes; a 10-month table missing a month passes (the 90% rule and the `1.5x+2` row allowance); a patient set with 20% extra or 10% missing patients passes (F1 ≥ 0.9) | Exact key set and exact patient set count as correct. Report partial credit (key recall, F1) separately, never as correct |
| Empty sets | S06, S07 | A correctly empty patient set scores F1 0; a correctly empty table can never pass (`bool(t)`) | An empty prediction for an empty key is correct |
| Duplicate keys | S10, S11 | Duplicate table keys, including keys that collide after `_tkey` normalization, are resolved silently in favour of the last row | Duplicate keys make the answer malformed |
| Units | S13, S15, S16 | An absolute tolerance wider than the value (0.4% with abs 0.5) accepts a 100x fraction error; `"12.5%"` and `"$1,234,567"` are scored as wrong numbers | Bound small values relatively as well (or have the contract declare a unit); a unit string is either parsed by a declared rule or reported as malformed |
| Malformed output | S18–S23 | `[['A']]` raises IndexError and a bare-number table raises AttributeError, aborting the whole run; `patient_ids: "P1,P2"` is iterated as characters; `count: true` reads as 1; an empty row becomes key `"None"`; a missing result field is graded "answered wrong" | Malformed is its own outcome and count (`status: "malformed"` from `_grade`), never a crash and never correct |
| Multi-column keys | S24 | A two-column key (`A -> B` switches) has no canonical form, so it depends on the separator a contestant happens to use | Ordered key columns in the contract (for example `"keys": [..]`), compared as a tuple |

## Also requested
- Declare a scorer version (`SCORER_VERSION = '...'`) in the scorer module. Agere's run manifest records it next to
  the module hashes, so a score always names the scorer that produced it.

## What Agere changed on its side (same commit)
- `bench/truth_run.py`: the table adapter keeps every key column and every measure column, reports duplicate keys, and
  keeps a stated empty answer (`patient_ids: []`, `table: []`) as answered instead of turning it into an abstention.
- `bench/manifest.py`: every scored run writes `manifest.json` (data/build sha256, docs, question file, scorer hashes,
  model settings, cache policy, pricing, code). Costs fail closed: an unknown cost makes the total unknown, and cached
  or no-new-call replays are counted apart.
- `fill_events.py`: a dispense and its payer claim merge only when they have the same ingredient(s).
