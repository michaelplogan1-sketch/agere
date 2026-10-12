# Exact incremental patient inputs

Protocol and baseline preparation now share mutation-tracked source fingerprints in the derived warehouse. Inserts, updates, deletes and SQLite replacements invalidate the affected patients. Moving a record marks both its prior and new patient. Roster and relevant clinical-analysis changes participate; unrelated result-cache writes do not. The original received warehouse is untouched.

The initial build hashes each source payload. Subsequent preparations recompute only dirty patients, using an atomic SQLite transaction. Missing triggers cause a full rebuild; unexpected trigger definitions and corrupted cache checksums fail explicitly. No file timestamp or assumed source immutability substitutes for data tracking.

## Measured result

The independent full scan reproduced every protocol fingerprint across **1,614 synthetic patients and 1,049,339 received records**. The final comparison took **24.310 seconds** for the old full scan; unchanged fingerprint reuse took **0.005214 seconds**, with **0 source payloads rehashed**. These are this laptop's measured preparation times, not a total-answer latency or generalized speed claim.

Source: `artifacts/agere-input-fingerprints-v1/verification.json`.

The actual question path was run for the whole cohort. Reduced-EF source-review criteria remain supported for 14 patients, with 1,600 having insufficient evidence and none gaining clinical eligibility. The admission-notification baseline example retains 19 patients with baseline observations and 1,595 with no resolved index; its 228 included source records and 24 machine annotations retain the existing availability/date exclusions. These example-specific denominators are not prevalence estimates or validated forecast targets.

## Integration and verification

- `patient_input_index.py`: exact source fingerprints, mutation tracking and checksums.
- `care_protocols.py`: incremental preparation, validation before saving newly computed results and revalidation of cached inputs before returning them.
- `baseline_evidence.py`: incrementally cached raw-source components plus separately hashed text-event inputs; feature/target separation unchanged.
- Workbench: displays patients reused/recomputed and records rehashed alongside protocol/baseline results.

69 focused checks pass in `artifacts/agere-input-fingerprints-v1/regression.log`, including the existing protocol and baseline cases. A new adversarial replacement case initially found that SQLite REPLACE could skip the prior patient's invalidation. The failure receipt is retained; a before-insert capture now covers it, including installations with recursive triggers disabled. The final full-source comparison and question runs were repeated after this fix.

No paid API calls, new model weights or clinical outcome claims. Text-event baseline preparation still scans the selected ledger rows. Initial fingerprint installation takes a write transaction; WAL permits concurrent source readers, but other writers must wait. This is a local development optimization, not an established distributed-ingestion service.
