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

Use `--ids D01 D05 C18` for a selected run. `--jobs 1` is the default; up to four
concurrent cases are supported and recorded, so these timings must not be called
controlled single-request latency. `--resume bench/results/<system>/<run>.jsonl`
appends only unattempted IDs and rejects a changed suite. A new run is an explicit
new attempt. Results are local, gitignored JSONL. `bench/score.py` selects the most
recent attempt per ID, never the best result, and writes `SCORECARD.md`.

Counts and patient sets require exact matches; numerical values use the declared
absolute tolerance. Tables compare row multisets with exact column names.
Partial/grounding-failed answers do not pass as complete answers, even if their
known count matches. Unsupported answers must identify the declared missing
prerequisites. That phrase-based rubric does not establish clinical reasoning
quality. Its diagnostic SQL confirms no supplied prerequisite artifact; it does
not prove the absence of every possible narrative clue.

Costs are product API estimates using the dated rates in `config/agere/pricing.json`,
not invoices. Engineering-chat usage, local hardware and human review are excluded.
Missing cost never becomes zero. Inspect matched-question coverage and false
assertions alongside cost per correct answer; abstaining on everything must not
look like a cheap high-quality system. The reference arm measures query execution,
not natural-language understanding. A fair comparative claim additionally needs
the same completed cases, equivalent evidence access and controlled timing.
