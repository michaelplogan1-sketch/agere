# Verified local recovery — G6 partial

Agere can now checkpoint the complete connected build while its server is stopped, verify the checkpoint, restore it into a fresh directory and start that copy in inspection mode. The original warehouse and active configuration are never overwritten. The normal workbench is back on port 8885.

The actual checkpoint contains **1,427 files, 66 SQLite databases and 20,988,029,867 bytes**. Each database was copied through SQLite's backup API and passed `quick_check`. Internal evidence/state/longitudinal links were rebound only in the recovered installation; saved questions, results and work identities were preserved. The shared provider ledger is neither copied nor rolled back.

## Verified behavior

- 46 focused recovery/adapter tests pass. They exercise crash recovery with committed and uncommitted WAL transactions, corrupt/incompatible checkpoints, missing configuration, changed source/code/dependencies, unsafe paths, symlinks, active-server fencing and interrupted publication.
- The actual running server refuses a checkpoint. A completed offline checkpoint restores into a separate directory; both the original and recovered application start with the same pinned snapshot.
- Actual HTTP checks reopen the original individual respiratory decision and complete 1,614-member respiratory population decision, preserving all owned tasks and follow-ups. Request/dispatch history is identical. The source reading and its zero-review state are identical; no human review is invented.
- The independent four-person adapter example, corrected mapping and renamed source schema were saved through the existing API, copied, and reopened. Exact roster, unknown/missing distinction, clock exclusions and documentary results agree. Direct SQL on the active adapted warehouse confirms 1,614 people, 1,238,686 received records, 158 categories and 46,650 unknown event dates.
- The restored API refuses work mutation, local/hosted model execution and new intake saves. The browser displays the recovery banner and refuses handoff preparation while retaining work revision 2. A real restored-server restart and original-server checks preserve the decisions/tasks/follow-ups.
- No paid provider call, semantic inference or clinical review was created. Models load for server startup; that is not model execution or qualification. All 170 protected files and 127 prior implementation receipts retain their bytes.

The first full-size checkpoint was not published because empty WAL sidecars created by read-only SQLite inspection triggered the source-change fence. The implementation now ignores empty WAL sidecars while fencing nonempty WAL and main-store changes; the regression is tested. Failed logs are retained; owned failed staging copies were removed after successful recovery. Initial tests also exposed omitted empty linked directories, which are now copied and validated before publication.

## Remaining G6 limits

This is **same-machine inspection recovery**, not a standalone installer, full code rollback or resumption of care work. The exact current application code and installed Python/model environment are still required. Twelve external source/model/research dependencies are checked at their top-level JSON manifests; their complete contents are not archived. The current shared ledger remains an external, never-rewound dependency. The lock coordinates this launcher and checkpoint utility; independent writers must also be stopped, with source changes rejecting publication.

The frozen learning cohort deliberately rejects the relocated workspace as a current qualified context. Its saved statement/reading and raw review are preserved, but current cohort qualification does not silently transfer. No protected cohort, training split, model or generator was rewritten to make recovery pass.

Therefore G6 stays active. The scorecard records partial operational/adapter evidence, not full acceptance: **adapter 4/5, local operations 4/6; full release remains 4/9**. Independent code/runtime packaging, governed relocation of qualification dependencies, controlled work resumption and activation/rollback remain required. G3 and G5 retain their earlier blockers.

## Reproduce on this installation

From `/Users/mikel/clinical-insights-local`, stop the server and other writers before creating a checkpoint. Output directories must be new.

```sh
.venv/bin/python serve_current_warehouse.py --check
.venv/bin/python agere_checkpoint.py create --output /absolute/new-checkpoint
.venv/bin/python agere_checkpoint.py verify /absolute/new-checkpoint
.venv/bin/python agere_checkpoint.py restore /absolute/new-checkpoint --output /absolute/new-recovery
.venv/bin/python serve_current_warehouse.py --config /absolute/new-recovery/configuration.json --check
.venv/bin/python serve_current_warehouse.py --config /absolute/new-recovery/configuration.json --port 8887
```

Recovery refuses `--allow-live-stack`. A different application version is rejected; do not weaken this check to reuse stale models or work. The verified checkpoint and recovery are in `artifacts/agere-local-recovery-v1/`. The temporary recovery server is stopped after verification; the original workbench remains available.
