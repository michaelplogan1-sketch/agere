# Independent answer benchmark

Run from the repository root:

```sh
.venv/bin/python -m unittest bench.test_bench
.venv/bin/python bench/run.py --system reference
.venv/bin/python bench/run.py --system agere
.venv/bin/python bench/run.py --system frontier_sql
.venv/bin/python bench/score.py
```

Python's standard library is sufficient. `--database` selects the received
`warehouse.sqlite3`; the default is the shared original-cohort v1.23 artifact.
The database stays read-only. No product module is imported. A worktree can use
the existing environment at `/Users/mikel/clinical-insights-local/.venv`.

There are 30 development questions and 20 challenge cases in `questions/`, with
separate raw-source SQL in `sql/`. Each question specifies observational scope,
answer type, units and date rules. Challenges are visible development artifacts,
not a sealed generalization test. References are inspectable independent query
implementations, not externally adjudicated clinical truth. The five observed
SNOMED diabetes concepts were identified from received condition descriptions;
prediabetes and family history are excluded. This is not a general terminology
crosswalk. The explicit HbA1c reference returns P-1008 and P-4007.

The SQL connection exposes only original-roster, identity-matched records received
by the cutoff, and public patient/source-entity views. SQL can inspect JSON keys,
values, labels, units and clocks. It cannot read cached answers, mutate data,
attach another database, bypass the scoped views or load extensions. Query
timeouts and output truncation are explicit. All references execute against these
same views. Future *known* scheduled events remain received evidence; individual
temporal questions explicitly constrain event dates.

`agere` invokes the assigned `/api/stack/compose` endpoint, which may use paid
product inference and its normal server-side caches. It scores only the top-level
`answer` contract. A missing contract fails; it is not reconstructed from internal
node outputs. The benchmark itself never opens a writable product database or
restarts the server. Planning-only telemetry is marked incomplete if other model
calls occur. No ledger-wide deltas are attributed to a question during concurrent
work.

`frontier_sql` uses the specified `claude-opus-5-5` with the same question, cutoff,
received warehouse and a bounded read-only SQL tool. It receives schema/help but
neither reference queries nor expected answers. Its key comes from the environment
or the assigned `/Users/mikel/insight-engine/.env`; keys are never logged. Each
question permits at most 20 executed SQL calls, including failed queries. All
returned token usage and tool results are recorded. There are no automatic paid
retries. Provider failures retain known usage and mark unavailable cost unknown.

`frontier_semantic` is the fair commercial-product baseline ("LLM + semantic layer", the shape of Databricks
Genie or Snowflake Cortex Analyst). The same model gets the documented semantic model as a cached system block:
the semantic tables with their column docs (`SCHEMA_DOC`, parsed as text with `ast`), the concept library code
sets, adopted definitions, governed metric and quality-measure definitions (`config/agere/metrics`, `measures`)
and 18 verified question-to-SQL examples (`semantic_examples.json`, development/challenge only, each checked
against its reference). Its one tool is read-only `query_sql` over `<build>/stack/semantic.sqlite3` (max 8
calls; `bench.common.SemanticWarehouse`: mode=ro, query_only, authorizer, time limit, explicit truncation).
No planner, compiler, router, engine, verifier or cache is called. Rows record cache read/write tokens and mark
`prompt_example` questions, which must be excluded from accuracy claims. The semantic layer is one snapshot
(`meta.as_of`) with superseded corrections removed, so run it with `--as-of` set to that snapshot, e.g.

```sh
.venv/bin/python bench/run.py --system frontier_semantic --as-of 2030-05-27 \
  --database artifacts/bench-db/v28-sample-indexed.sqlite3 \
  --semantic-database artifacts/agere-healthcare-v28-sample-0.30/stack/semantic.sqlite3 --ids D09 D14
```

The runner refuses a semantic layer whose build release differs from the scored warehouse release.

Use `--ids D01 D05 C18` for a selected run. `--jobs 1` is the default; up to four
concurrent cases are supported and recorded, so these timings must not be called
controlled single-request latency. `--resume bench/results/<system>/<run>.jsonl`
appends only unattempted IDs and rejects a changed suite. A new run is an explicit
new attempt. Results are local, gitignored JSONL. `bench/score.py` selects the most
recent attempt per ID, never the best result, and writes `SCORECARD.md`.

Counts and patient sets require exact matches; numerical values use the declared
absolute tolerance. Tables compare row multisets with exact column names.
Partial/grounding-failed answers do not pass as complete answers, even if their
known count matches. A correct count serialized in `value` is reported as a value
match and answer-type failure, not a false numerical assertion. Nonzero
`unknown_count` alone is not a failure: a question
can explicitly exclude undated records and still have a complete scoped answer.
The live C06 baseline exposed this distinction; its exact count remains correct
while its 817 excluded undated records stay visible. Scorecard generation rescores
preserved answers using current rules without rewriting raw runs or repeating paid
calls. Unsupported answers must identify the declared missing
prerequisites. That phrase-based rubric does not establish clinical reasoning
quality. Its diagnostic SQL confirms no supplied prerequisite artifact; it does
not prove the absence of every possible narrative clue.

The first full frontier run scored 49/50. D16 requested encounter records; its
answer instead counted 3,875 distinct encounter IDs, explicitly explaining that
the 7,750 received rows contained two versions per encounter. That disclosed
interpretation fails the record-count reference; it is not an invented count.
Keep this distinction visible when reading the mechanical false-assertion metric.

Costs are product API estimates using the dated rates in `config/agere/pricing.json`,
not invoices. Engineering-chat usage, local hardware and human review are excluded.
Missing cost never becomes zero. Inspect matched-question coverage and false
assertions alongside cost per correct answer; abstaining on everything must not
look like a cheap high-quality system. The reference arm measures query execution,
not natural-language understanding. A fair comparative claim additionally needs
the same completed cases, equivalent evidence access and controlled timing.

## Errata and reliability (opt-in)

`bench/errata.json` audits every non-capability question: category a (reference correct), b (question
under-specifies a definition; accepted alternative readings with their own expected answers), c (reference
contradicts the question or another split's convention, e.g. roster vs latest reported age) or d (reference
bug, corrected SQL in `bench/sql_errata/`). The canonical conventions (age, windows, CKD, latest-lab ties,
obesity, follow-up day 0, ED visit, precision) are listed in its `conventions` block. Originals in
`questions/` and `sql/` are never edited.

- `score.py --errata` writes `SCORECARD-errata.md`: correct = canonical answer, or an accepted alternative
  whose definition the answer states (interpretation / definition / computed_as / readings). Without
  `--errata` scoring is unchanged.
- `run.py --errata` also computes the errata readings for the run's own release and stores `errata_score`
  next to the default `score`. `run.py --repeat 3` records three attempts per question; the scorecard reports
  pass^3 (latest three attempts all correct).
- `errata_replay.py` re-scores saved Agere receipts (ladder and governed-metric oracle) with and without errata.
