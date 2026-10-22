# DuckDB / Parquet migration plan: answer path and training export

2026-10-21. Scope: the live answer path (`/ask` → `/api/stack/compose` on :8885) and a single
training/eval export. Write stores stay on SQLite. DuckDB and Parquet are free (MIT / Apache);
`duckdb==1.5.6` is already in `.venv` and `.venv-tuva`.

## 1. Measured on the active warehouse (v28-sample-0.30)

| Test | Result |
|---|---|
| Claims paid by product×month, raw `records` JSON | SQLite 10.1 s → DuckDB over the SQLite file 2.7 s → DuckDB over Parquet 0.43 s → typed DuckDB table 0.01 s; identical results |
| `records` (2.42M rows) → zstd Parquet | 13 s, 0.52 GB |
| `stack/semantic.sqlite3` (8.7 GB, 59 tables) → `semantic.duckdb` | 26 s, 4.53 GB, 0 table failures |
| Replay of 101 distinct SQL statements from `stack/question-plans.sqlite3` | 70 run on SQLite (rest need bound params / population). 26 run on DuckDB **unchanged**; on the 25 that ran on both, median 0.374 s → 0.017 s (22×), p90 2.99 s → 0.46 s, total 32.2 s → 2.6 s |
| Result parity on those 25 | 21 identical, **4 silently wrong on DuckDB** (1,369 → 0; quarterly counts → 0; rates → NULL; 3 rows → 0 rows) |

The 4 silent mismatches all come from one rule: DuckDB `json_extract` returns JSON (`"paid"`), SQLite
returns the scalar (`paid`). A bare `json_extract(...) = 'paid'` raises a conversion error on DuckDB,
but `CAST(json_extract(...) AS TEXT) = 'paid'`, `substr(json_extract(...),1,10)` and `LIKE` keep the
quotes and match nothing, without raising. Dialect failures on the other 45: `date(x,'-N months')` (12), `julianday`
(10), malformed-JSON casts (16), parameter binding (29), ambiguous overloads (7). **A parity gate is
mandatory; "it runs" is not evidence it answers correctly.**

## 2. Current seams (verified file:line)

- One executor for all analytic SQL: `clinical_core/agere/clinical/sql_query.py:48` `execute()` →
  `semantic_layer.ensure()` → `sqlite3.connect(mode=ro)`, SQLite authorizer, progress-handler time
  limit 600 s, `MAX_ROWS` 200000, population injected as `json_each(:population)` CTE, Python UDFs
  registered via `register()` (`age_at`, `drug_key`, `dose_mg`, `per_day`, `target_mg`,
  `statement_*`, `clinical_measurements`).
- Other read-only SQL on `semantic.sqlite3`: `request_plan.Catalog.load` / `schema_slice`
  (`request_plan.py:88,307`), `general_analysis` checks / data_currency / tracing
  (`general_analysis.py:420,623,655`), `answer_format.present` (`answer_format.py:33,131`),
  `request_discovery` raw-catalog probe (`request_discovery.py:249-300`).
- SQLite dialect is emitted by the compiler (`request_plan.py:171,241,303,345`), taught by
  `semantic_layer.SCHEMA_DOC` (line 143) and `general_analysis.ANALYSIS_SYSTEM` (34-64), and
  appears in macros in `request_vocabulary.py`, `dose.py`, `general_analysis.py`.
- Row-scan engines decode JSON in Python: `aggregate`, `explain`, `cohorts`, `analysis_graph` via
  `record_store.load` (`stack/stream-records.sqlite3`, 6.1 GB) and `stack/stack.sqlite3` (22.7 GB,
  which also holds write tables `result_cache`, `analyses`, `proposals`).
- Semantic build: `semantic_layer.build` (`semantic_layer.py:886`), `semantic-layer/19`,
  rebuilt by `ensure()` on version/source-digest mismatch.
- Existing DuckDB code: `bench/tuva.py` (read-only guard pattern to reuse),
  `onboarding/connectors.py`, `test_warehouse/` (Tuva on DuckDB). `bench/common.py:166` still
  says duckdb is not installed (stale).

## 3. Target

```
raw release ─► stack.sqlite3 (write tables only) + records/*.parquet (per stream, typed)
                         │
                semantic_layer.build ─► semantic.duckdb  (typed: DATE, DOUBLE, JSON payload)
                         │
   sql_query.execute(engine=duckdb) ◄── compiler / prompts / macros emit DuckDB SQL
   request_discovery / aggregate / explain read Parquet through DuckDB
Write stores (unchanged SQLite): question-plans, learning, answers, conversations, model-cache,
   briefing-rank, events, evidence-*, outcomes, interpretation-state, assertion-receipts,
   provider-budget ledger, stack.sqlite3 result_cache/analyses/proposals.
```

## 4. Answer path, in order

**A. Parity harness first (no behavior change).** `tools/duckdb_parity/replay.py`: replays every
SQL statement from `question-plans.sqlite3`, bench reference SQL (`bench/sql/*`), and the compiler's
output for the dev/locked question sets against both engines with the same bound population and
UDFs; normalizes rows (sorted, floats to 4 dp, dates to ISO) and reports identical / differs /
fails per statement. This is the gate for every later step. Done = harness runs in CI-equivalent
smoke and reproduces the 21/4 split above.

**B. Build `semantic.duckdb` beside `semantic.sqlite3`.** Post-step in `semantic_layer.build`:
`ATTACH semantic.sqlite3 (TYPE sqlite)` → `CREATE TABLE … AS SELECT` with explicit casts (TEXT
dates → DATE, payload → JSON), copy `meta` incl. `source_digest`, write to `.tmp` and swap (same as
today). `ensure()` checks both files' digests. ~30 s added to a build.

**C. Engine switch in `sql_query.execute`.** `engine` read from `config/agere/active_warehouse.json`
(default `sqlite`). DuckDB executor reuses the `bench/tuva.py` guard: `read_only=True`,
`extract_statements` = exactly one SELECT, `enable_external_access=false`,
`lock_configuration=true`, timer `interrupt()` for the time limit, `fetchmany(MAX_ROWS+1)`.
Population becomes `population(patient_id) AS (SELECT unnest(?::VARCHAR[]))`. UDFs: `age_at` and
date helpers as DuckDB SQL macros; dose/drug helpers via `create_function` (Python scalar UDFs,
same code). One connection per process held open read-only (DuckDB allows many readers).
Apply the same switch to `Catalog.load`, `general_analysis`, `answer_format`, `request_discovery`.

**D. Dialect.** The compiler and prompts emit DuckDB natively; no runtime transpiler.
- `request_plan.py`: `json_extract(...)` → `payload->>'$.f'` (string) with explicit casts;
  `date(as_of,'-N months')` → `as_of::DATE - INTERVAL N MONTH`; `julianday(a)-julianday(b)` →
  `date_diff('day', b, a)`; `substr` grains → `strftime` / `date_trunc`.
- `SCHEMA_DOC` and `ANALYSIS_SYSTEM`: say DuckDB, show `->>` only.
- Lint in `sql_query.validate` (DuckDB mode): reject bare `json_extract(`; require `->>` or
  `json_extract_string`. This blocks the silent-zero class at the door.
- Cached / learned SQL in `question-plans.sqlite3` and `learning.sqlite3`: keep an `engine` field on
  each stored statement; SQLite-era entries re-plan on first use under DuckDB (no automatic
  rewrite of stored SQL).

**E. Shadow, then flip.** Serve SQLite, run DuckDB in shadow on every compose, log diffs to
`stack/duckdb-shadow.sqlite3`. Flip `engine` to `duckdb` when the harness and shadow log show zero
unexplained differences on the locked + dev sets and the replay corpus. Keep `semantic.sqlite3`
built for one release as the rollback (config flip, no rebuild).

**F. Raw catalog and row engines.** Export `records` per stream to Parquet at build
(`stack/records/stream=<name>/part-*.parquet`, typed common columns + JSON payload). Point
`request_discovery` probes and `record_store.load` at DuckDB over those files (full-catalog
zero-result probe becomes sub-second). Move `aggregate` / `explain` set and arithmetic work into
SQL where they currently decode JSON in Python. `stack.sqlite3` keeps only its write tables; its
`records` copy and `stream-records.sqlite3` retire after parity.

**G. Canonical model (follows F).** Attach `tuva.duckdb` as a second read-only schema so the
compiler can target the OMOP/Tuva-aligned canonical tables (the 2026-10-15 priority) without a copy.

## 5. Training / eval export

**One exporter:** `tools/export/training_parquet.py --release <build> --out artifacts/training-export/<release-digest>/`.
DuckDB writes Parquet directly; no pyarrow/pandas needed (MLX keeps reading JSONL; HF `datasets`
can read the Parquet if added later).

```
warehouse/<table>.parquet          typed semantic tables (from semantic.duckdb)
records/stream=<s>/part-*.parquet  raw records, payload JSON
examples/<task>.parquet            one table per task (below)
mlx/<task>/{train,valid,test}.jsonl  emitted from examples/ for mlx-lm
MANIFEST.json                       release digest, per-file sha256 + row counts, split rule, denylist digest
```

**Common example schema:** `example_id, task, split, group_key, source_release, release_digest,
input, target, weight, provenance(json)`.

**Tasks pulled in (existing builders keep their logic; the exporter reads their outputs):**
local normalizer (`tools/local_planner/build_dataset.py`), bottling SQL pairs
(`tools/bottling/prepare.py`; targets re-executed against `semantic.duckdb` once step D lands),
learned router (`tools/learned_routing/train.py`), finite joint model (`train_joint_observation.py`),
task heads (`clinical_core/agere/training.py`), warehouse predictors
(`clinical/warehouse_prediction.py`).

**Splits:** the exporter records each builder's existing split, it does not re-split, so current
models stay comparable. New tasks use one shared rule: `sha256(group_key) → 70/15/15`, with
`group_key` = source family (clinical), household/patient (warehouse), question cluster (NL tasks).
Fix while here: `local_planner/build_dataset.py:67` copies `valid` into `test`; give it a real test
bucket.

**Denylist enforced at export (fails the export on any hit, exact + normalized-text hash):**
`/Users/mikel/agere-eval-bank-2/bank.json`, `/Users/mikel/agere-sealed-eval/` (never copied into
the repo), Gold-10 / Gold-50 and breadth sets in `docs/agere/study/`, `bench/questions`,
`bench/acceptance`, `bench/capability`, generator `gold50.jsonl` / `private/eval_*`, the EHRSQL 2024 test split.

**Same exporter, test-bed SKU:** `warehouse/` + `records/` + the generator's native `raw/` slice +
a `semantic.duckdb`, with answer keys shipped separately and sealed. This is the sellable
warehouse package from the GTM plan.

## 6. Additions (decided 2026-10-21)

**External data, public licences only, no credentialed data:**

| Source | Use | Rule |
|---|---|---|
| EHRSQL 2024 + MIMIC-IV demo (100 pts; ~5.1K train / ~1.2K test question→SQL; ~20% unanswerable; CC-BY-4.0 + ODbL) | External public benchmark for the study (answer + abstain, scored with their reliability metric); train split as extra text-to-SQL supervision for the bottled SQL model | First, ahead of the others. EHRSQL test split goes on the export denylist; never trained on |
| CMS DE-SynPUF (sample 1; beneficiary, inpatient, outpatient, carrier, Part D) | Foreign claims layout through onboarding: proves "sits on any data" on a schema our generator did not write | Synthetic: structure tests only, no clinical rates quoted |
| OHDSI Eunomia GiBleed / synpuf-1k (OMOP, DuckDB-native) | Fixtures for the OMOP round trip and the canonical model (step G) | Small; fixtures, not training data |

Full MIMIC-IV (PhysioNet credentialed, DUA) is out of scope.

**DuckDB features:**
- FTS (BM25) in step F: measure against the current lexical stage in `answer_guard` / note retrieval on the same
  cases; adopt only on equal recall.
- `postgres` / `mysql` / `excel` / `httpfs` readers added to `onboarding/connectors.py` as client-source connectors.
- DuckDB-WASM: browser demo of the test-bed SKU (after the exporter).
- VSS (HNSW): not now; index persistence is experimental. Revisit to replace `stack/vectors.npy` (6.7 GB).

## 7. Housekeeping

- Pin `duckdb==1.5.6` in `requirements-agere.lock` and uncomment it in
  `deploy/requirements-hosted.txt`.
- Replace the stale message at `bench/common.py:166` with a DuckDB read path (reuse `bench/tuva.py`).
- DuckDB `memory_limit` and `threads` set in one place (service config); the Tuva build hit
  17.8 GB peak at defaults.

## 8. Definition of done

| Step | Done when |
|---|---|
| A | Harness reproduces the measured split and runs in the smoke suite |
| B | `semantic.duckdb` built and digest-checked on every build |
| C+D | Harness: 100% identical on every statement that ran on SQLite; bare `json_extract` rejected |
| E | Shadow log zero unexplained diffs on locked + dev sets; flip; rollback = config flip |
| F | Discovery probe and record_store on Parquet; `stream-records.sqlite3` retired |
| Export | `MANIFEST.json` reproducible byte-for-byte from a release; denylist test fails on a planted bank item |
