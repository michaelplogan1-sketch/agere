> **Active build — October 9, 2026:** Agere uses the original-cohort
> `healthcare-warehouse-v1.14`: **1,614 synthetic patients, 894,223 received records,
> 88 source categories**. The current build is `artifacts/agere-healthcare-interpretation-v1/`.
> Native medication/care/access/finance/goals accounts, new narrative fields and
> linked specimen/encounter clocks now feed the query and patient-state layers.
> Read [the native interpretation build](WAREHOUSE_INTERPRETATION_BUILD.md) first.
> Live: http://127.0.0.1:8885/stack. Start with
> `.venv/bin/python serve_current_warehouse.py --allow-live-stack --port 8885`.
> `config/agere/active_warehouse.json` controls follow-on builders. Earlier population
> results below remain historical; forecasts and model qualifications do not transfer.

# Agere connected healthcare warehouse

All patients, records, dates, and examples are synthetic. This build uses the
original-cohort healthcare warehouse being built to test Agere. It does not use
the historical expanded 10,000-variant benchmark as its active population.

## Source and population

Pinned source: `/Users/mikel/synthetic-patient-ecosystem/releases/healthcare-warehouse-v1.14/warehouse.sqlite`.
Source SHA-256: `ebac786ebaf9f93e0ef8d326fbc2cc0dd8d7c2897c3e450f26e380ffded7b81e`.

The warehouse contains **1,614 original patients**, **894,223 received source
records**, and **88 source categories**. The categories are values in the source
record table, not 88 SQLite tables. The original patient ID set is retained
exactly. The original handoff's 1,612 runnable benchmark patients and its 4,836
resampled population describe different artifacts; none is silently substituted.

The snapshot cutoff is **2028-09-30**, a fictional date from the synthetic release.
There are **46,650 records with unknown event dates**. Those records are retained;
receipt dates are not substituted for event dates. Inference reads public received
records only. Generator truth and withheld/unreceived evidence are not inputs.

## Historical initial connection build

Output: `artifacts/agere-healthcare-warehouse-v1.14/`.

- Read-only, hash-verified adapter preserves native source fields and provenance.
- Rebuilt the clinical source pass, deterministic operators, complete cohort store,
  source-bound event ledger, documentary state, and bounded evidence index.
- All **894,223 received records** are lexically searchable, across all **88**
  categories; the index contains **1,649,678 passages**.
- **74,924 distinct narrative passage texts** were embedded locally. Structured
  payloads remain fully available to exact and lexical retrieval; zero-vector
  placeholders are excluded from dense ranking.
- The existing v4 local reader was executed on the new warehouse, producing
  **24,290 source-linked machine annotations**. These are extraction proposals,
  not adjudicated clinical facts or an accuracy result.
- Reused pinned local model weights and portable medication terminology. No old
  patient conclusions, worklists, or fitted forecasts were transplanted.
- **Zero paid API calls** for this migration; no new model training occurred.

The active configuration is `config/agere/active_warehouse.json`. Main follow-on
build entry points resolve that configuration, so they do not silently fall back
to the expanded historical benchmark.

Launch the connected workbench with:

```sh
.venv/bin/python serve_current_warehouse.py --allow-live-stack --port 8885
```

The live flag permits the existing selectively routed paid integrations; it does
not itself call them. Local registered queries remain local.

## Initial connection boundaries (superseded where documented above)

Transport and retrieval coverage do not imply that every source field is already
understood by every clinical operator. `mapping-boundaries.json` explicitly lists
`ordering_reason`, `course_summary`, `history_text`, and `goc`: their literal text
is preserved and searchable, but these fields are not yet part of the existing
bounded narrative candidate extractor. Explicit clinical-date bindings and native
source semantics also need broader mapping. Treat these as adapter gaps, not
missing patient care.

The optional frozen lexical/MiniLM shadow students were **not attached**: adding
warehouse narrative fields changes the reader contract included in their frozen
identity. The attachment check rejected them. Their manifests and weights remain
untouched; they need separate requalification on the new contract. The completed
v4 reader execution is a different component and remains attached.

Old population-specific forecast and learning-queue attachments are not valid on
this population and were not copied. Build new baseline/outcome exports against
this pinned source and reassess actual target support before fitting. Historical
findings about 144 index patients or lack of label variation do not describe this
warehouse. Existing outcome definitions and review criteria still require their
stated evidence; no new clinical or payer eligibility was inferred.

The next integration work is to extend and qualify source meanings on this
warehouse, then exercise the broad population-health/VBC question curriculum and
measure retrieval, interpretation, missingness, and action-support failures
separately. Warehouse releases should be upgraded explicitly with fresh receipts.

## Verification and live state

The local server on port 8885 and the browser both show the new source and counts.
The UI's source-completeness query completed with zero paid calls. Screenshot:
`artifacts/agere-healthcare-warehouse-v1.14/workbench.png`.

240 migration/regression checks passed. A further 35 catalog/planner/composition
checks passed, with some overlap; these are software checks, not clinical-accuracy
estimates. `verification.json` records exact original ID equality, category counts,
full lexical coverage, actual local retrieval with source-linked original patient
IDs, and queries run over the complete population.

The financial evidence query matches **115** original patients with a literal
positive received screen, independently reproduced from the source database. This
is not a determination of assistance eligibility. Public ADT records contain **19**
admission notifications, **42** discharge notifications, and **23** observation
starts. Observation starts are not promoted to admissions. The source-clock
baseline passes retain **19** and **42** indexed patients respectively; the
explicit-clinical-clock pass leaves **1,614** without a resolved clinical index
under the current binding contract. That exposes mapping/evidence limitations,
not absence of hospital care. Exact query receipts are saved under `verification/`.

The larger catalog initially produced a **26,968-byte** frontier request for a
1,500-character ASCII question. Factoring shared fields and exact vocabularies,
using a round-trip-checked compact serialization, and reducing repeated prompt
instructions brings the same check to **19,913 bytes**, within the existing
**20,000-byte** guard. No catalog fields or values are removed by the new factoring
step. These are serialized bytes, not measured tokens, charges, or proven model
quality. The existing guard still rejects larger requests.

All implementation hashes, verification receipts, limitations, and the running
server session are recorded in `implementation-receipt.json`. Main follow-on
builders now resolve the active source. Broader interpretation and forecast
qualification remain subsequent build work on this warehouse.
