# Dataset provenance correction — October 9, 2026

The current Agere workbench remains connected to the historical ecosystem-v2.2
integration snapshot, through artifacts/agere-population-stack-v3. Direct read-only
SQL counts verified 10,000 patient IDs and 313,586 received records. The original
release manifest identifies 1,614 source families and explicitly says the variants
are not independent. Its preserved generator cycles through the source library to
produce the requested 10,000 rows. This is not the current original-cohort warehouse.

The other data build's current primary, verified from SINGLE_WAREHOUSE_BUILD.md,
release metrics and read-only SQL, is:

/Users/mikel/synthetic-patient-ecosystem/releases/healthcare-warehouse-v1.14/warehouse.sqlite

It contains 1,614 original patient IDs and 894,223 received records across 88 source
categories. Its patient ID set exactly matches the original source library. Its
synthetic cutoff is 2028-09-30; these are fictional histories, not real future care.
The original source JSON has 1,614 rows, of which 1,612 are marked runnable. The
initial insight-engine benchmark filtered to those runnable rows and drew each
three times, yielding 4,836 synthetic instances per world.

The latest Agere baseline run's 144 qualifying indices and its training limitations
refer ONLY to the historical 10,000-variant snapshot. They do not measure current
warehouse coverage or prove the current original-cohort dataset unsuitable for
training. No Agere run against v1.14 has been performed by this provenance check.

Correct the next integration priority: connect the stack through an explicit
source adapter to a pinned original-cohort warehouse release, account for every
received source category, preserve patient IDs and source/clinical/receipt clocks,
keep private author controls outside inference, then recompute evidence/outcome
coverage. Preserve old snapshots and result receipts as historical regression
artifacts. Do not merge historical variants into the original cohort or treat old
model/annotation caches as valid for the new source release. The other data build
is still evolving; check its latest declared primary before pinning the adapter.

Verification performed: source manifest and preserved generator inspected; source
library row/runnable counts read; both SQLite databases queried read-only; current
original ID set compared; current source categories counted. No data mutation,
model fitting, paid API call or source migration occurred in this check.

## Migration completed

Agere now uses the pinned original-cohort healthcare warehouse above. The historical
expanded benchmark remains available for regression work only. See
[HEALTHCARE_WAREHOUSE_CONNECTION.md](HEALTHCARE_WAREHOUSE_CONNECTION.md),
`artifacts/agere-healthcare-warehouse-v1.14/connection.json`, and
`config/agere/active_warehouse.json` for the active source and reproducible launch.
