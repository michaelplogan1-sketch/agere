# Agere storage plan

**Status:** explicit user instruction from Mike, 2026-11-09. Under `AGENTS.md`
("Latest explicit user instructions take precedence"), this is a standing
boundary, not a competing product plan. It does not change the gate sequence in
`AGERE_BUILD_CONTROL.json`. Do it before the next product run.

**Why:** The Mac's 926 GB disk is full (6 GB free on 2026-11-09). The runs on
2026-11-08 alone used about 34 GB. If the disk fills while a run is writing a
22 GB SQLite file, that run's output can be truncated or corrupted. Losing work
is exactly what we are trying to prevent.

**The one rule above all others:** no work, evidence or learning may be lost.
Every step below is either read-only, additive, or a *verified move* that the
user approves. Nothing is deleted outright.

---

## 1. What we found (2026-11-09)

- About 4 TB of file content sits on a 926 GB disk. Most copies are APFS clones
  that share blocks. Space is consumed when a clone **diverges**, meaning
  something writes to it, or when code falls back to a full physical copy.
- Large files repeat many times: `stack.sqlite3` (22 GB) has 73 copies,
  `events.sqlite3` 106, `semantic.sqlite3` 70, `warehouse.sqlite3` 58,
  `vectors.npy` 61, `stream-records.sqlite3` 65 and `semantic.duckdb` 44.
- `clinical_core/agere/local_release.py` `sqlite_copy()` already prefers
  `cp -c` (`cold_filesystem_clone`). The 2026-11-08 packages recorded 352
  clones, 8 plain copies and 2 `sqlite_online_backup` copies. Those 2 fallbacks
  happen whenever the source has a non-empty WAL or journal. Each one is a full
  22 GB physical write.
- **Sealed copies are being opened read-write.** 7 `installation/` and 6
  `checkpoint/` `stack.sqlite3` files have `-wal`/`-shm` side files. Every
  page written into a clone breaks block sharing, and over a run that adds up
  to most of the file. A checkpoint is supposed to be frozen, so it should
  never get a WAL.
- Every package holds **two** clones: `installation/build/stack/` and
  `checkpoint/build/stack/`. Once either is written, both cost real space.
- `received-copy/` and `dependencies-current*/` directories (for example
  `agere-evidence-change-current-2026-11-08/dependencies-current/received-copy/stack.sqlite3`,
  22.9 GB) are not produced by any code in the repo. They come from ad-hoc
  shell copies made during agent turns, with no record of how they were made.
- Many full `shutil.copy*`/`sqlite3.backup()` paths exist outside
  `local_release.py`: `build_cohort_stack.py`,
  `clinical/warehouse_retrieval.py`, `clinical/semantic_layer.py`,
  `clinical/evaluation.py`, `clinical/learning_cohorts.py`,
  `verified_carry_forward.py`, `benchmark_native_care_state.py`,
  `benchmark_pre_event_state.py`, `verify_decision_*`, `verify_graph_*` and
  `tools/eval_quarantine.py`.
- Several JSON outputs are 0.5–0.6 GB each, for example
  `dependencies-current-v3/*/stale.json` and `*/member.json`.
- Pace: 19–88 new artifact folders a day (27 on 2026-11-08).
- `artifacts/`, `models/`, SQLite and vectors are git-ignored (see
  `AGENTS.md`), so **none of the evidence is versioned or backed up.**
  Receipts in `BUILD_OWNER.json`, `AGERE_BUILD_CONTROL.json`,
  `CONTINUE_AGERE.md` and `docs/agere/` point to artifact paths. Deleting an
  artifact breaks those references.
- Learned output lives **inside** artifact folders, not just in `models/`.
  `artifacts/agere-reconciliation-contract-v2/protected-for-continuation.json`
  pins weights and train/validation/calibration data in
  `agere-learning-cohorts-v1`, `agere-appointment-followthrough-v1`,
  `agere-healthcare-v123`, `agere-source-distillation-v1` and
  `agere-reader-comparison-v1`. Many other folders hold `.npz`, `.safetensors`,
  `train*.jsonl` or `models/` (for example `agere-learning-safety-v1`, the
  `agere-evidence-reader-v*` folders and `agere-semantic-students-v*`).
- The active workspace symlinks into the top-level
  `~/clinical-insights-local/artifacts/agere-current-mvp-2026-10-30/` and
  `~/clinical-insights-local/models/`. Some links point at
  `/private/tmp/agere-codex-integration-20261026/...`, which macOS may clear
  on reboot. Those links are a fragility in their own right.

---

## 2. Hard rules (apply immediately, every turn)

1. **Never `rm` anything under `artifacts/`, `models/`, `~/clinical-insights-local/artifacts/`,
   `~/synthetic-patient-ecosystem/`, `~/agere-*` or `~/Documents/Codex/`.**
   Space is recovered only through §6 (a verified move after user approval).
2. **Disk guard.** Before any run that creates or opens a package, refuse to
   start if free space is below `max(60 GB, 3 × largest database the run will
   copy)`. Record the refusal in the control checkpoint and stop. Add this check
   to `check_agere_build_control.py --intent build` so it cannot be skipped.
3. **No ad-hoc copies of large data.** Do not `cp`, `rsync` or `shutil.copy`
   any file over 100 MB from the shell. To reuse an existing database, reference
   it by path and sha256 in the receipt. If an isolated copy is truly needed,
   use `local_release.copy_file`/`sqlite_copy`, which clone, and record the
   method.
4. **Sealed means read-only.** Open every database under `checkpoint/`,
   `installation/` (unless it is the live serving build), `received-copy/`,
   `dependencies-*` or any sealed receipt with
   `file:...?mode=ro&immutable=1`. After sealing a checkpoint, `chmod a-w` its
   files.
5. **Never move or alter anything in the protected list (§3).**

---

## 3. Protected set: build it first, read-only

Write `tools/storage_protected_set.py`. It must only read and report. Output
`artifacts/storage/protected-set.json`, listing every artifact directory and
file that must be kept on the internal disk, with the reason for each:

- Every path in `protected-for-continuation.json` (with its sha256 checked).
- Every artifact path referenced by `BUILD_OWNER.json`,
  `AGERE_BUILD_CONTROL.json`, `CONTINUE_AGERE.md`, `AUTONOMOUS_STACK_QUEUE.md`,
  `docs/agere/**`, any `*receipt*.json`, and any `.py` or test in the repo
  (string literals and `Path` joins; treat a match on the folder name as a
  reference).
- Every symlink target reachable from the workspace, including those outside it.
  Flag targets under `/private/tmp` separately as **at risk**.
- Every directory containing learned output: `*.npz`, `*.safetensors`, `*.pt`,
  `*.bin`, `*.gguf`, `*.onnx`, `*.pkl`, `*.joblib`, `train*.jsonl`,
  `validation*.jsonl`, `calibration*.jsonl`, `learning*.sqlite3`, `models/`,
  `adapters/` or `weights*`.
- The current live serving build, the latest sealed receipt and its
  checkpoint, plus the newest checkpoint of each release line (Agere,
  Wollstonecraft).
- The original cohort, frozen experiments and reserved evaluations named in
  `BUILD_OWNER.json`.

When unsure, protect it. A false "keep" costs only disk space, while a false
"move" can cost work.

---

## 4. Inventory: read-only

Write `tools/storage_inventory.py`. It must only read. Output
`artifacts/storage/inventory.json` plus a short readable summary:

- Every artifact directory with logical size, newest file time, protected
  (yes/no and reason) and release line.
- For files over 1 GB: size, sha256 and whether the file has `-wal`/`-shm`
  side files (meaning it was opened read-write). Run hashing in the background:
  about 4 TB at about 3 GB/s is roughly 25 minutes.
- Groups of byte-identical large files.

Do not attempt to compute "unique physical bytes per folder". APFS does not
expose it reliably. Measure real savings with `df` before and after each move
in §6.

---

## 5. Stop the growth: code changes

Make each change as a normal candidate with tests and a receipt, and review
each before it becomes the default.

1. **One sealed copy per package, not two.** `installation/` should be created
   from `checkpoint/` only when an installation is actually started, by
   cloning. Or it should open the checkpoint database read-only and keep
   writable state in a small separate database (for example `runtime.sqlite3`).
2. **Never write to a cloned database.** Wherever a run needs to change a
   large frozen database, either (a) write changes to a separate overlay
   database and `ATTACH` the frozen one read-only, or (b) accept a clone and
   record that it will diverge. Prefer (a).
3. **Make WAL-hot sources clonable.** In `sqlite_copy()`, under the existing
   `build_lock(exclusive=True)`, run `PRAGMA wal_checkpoint(TRUNCATE)` and
   then clone. This removes the `sqlite_online_backup` fallback for the normal
   case. Keep the fallback only when no lock can be taken, and log it loudly.
4. **Route every large copy through one helper.** Replace the direct
   `shutil.copy*`/`.backup()` calls listed in §1 with `local_release.copy_file`
   or `sqlite_copy`, so cloning is always attempted and the method is always
   recorded. Tests may keep small fixture copies.
5. **Reference instead of copy for dependency hand-offs.** Replace the
   `received-copy/` and `dependencies-current*/` pattern with a manifest entry
   (`{"path": ..., "sha256": ..., "opened": "ro"}`) pointing at the original.
6. **Long term, a content-addressed store.** Store each distinct large file
   once at `~/agere-store/sha256/<hash>.<ext>`, read-only (`chmod 444`). Runs
   and packages refer to it by hash through a symlink or a manifest. A new
   version only exists when the content actually changes. The sha256 values in
   `protected-for-continuation.json` and in receipts already fit this model.
7. **Huge JSON.** Compress outputs over 100 MB (`.json.gz`, as the answer
   workspaces already do), or store them as SQLite tables.
8. **Fix fragile links.** Copy, by clone, any protected symlink target under
   `/private/tmp` into the store or a permanent artifact folder, then repoint
   the link. Do this before the next reboot.

---

## 6. Recover space: verified move, never delete

The user is buying an external SSD. Until it is mounted, do steps 1–2 only.

1. Generate `artifacts/storage/move-candidates.json`: every directory **not**
   in the protected set, grouped as follows.
   - **A.** Top-level `~/clinical-insights-local/artifacts/` folders dated
     before 2026-10-25.
   - **B.** `~/agere-proposers-eval/{runs,build}` and
     `~/agere-question-qualification/artifacts`.
   - **C.** Superseded release folders (Wollstonecraft 2026-11-01 releases;
     `agere-integration-recovery-20261031`,
     `agere-integrated-delivery-2026-10-30`,
     `agere-current-source-review-installation-2026-10-31`), each only if it is
     not protected.
   - **D.** Unprotected run folders in the workspace older than 7 days.
2. **Show the list to the user and wait for an explicit "yes" per group.**
3. For each approved folder:
   - Copy it to the external drive with `rsync -aHX --numeric-ids` (preserving
     symlinks as symlinks). Then verify each file by size and sha256 against
     `inventory.json`.
   - Only after verification succeeds, rename the local folder to
     `<name>.moved-pending-delete`.
   - Run the continuity checker and the gate checks. If anything references the
     folder, rename it back and add it to the protected set.
   - After 7 days with no reference failures, and with the user's go-ahead,
     delete the local `.moved-pending-delete` copy. Record the external path in
     `artifacts/storage/archive-index.json` so receipts can still be resolved.
4. Record `df` before and after each group in the control decision log.

---

## 7. Back up what matters most

Separately from freeing space, the small, irreplaceable evidence has no backup
today. Add `tools/storage_evidence_backup.py`. It copies every file under 50 MB
in the protected set (receipts, manifests, reports, learned weights, training
data) to the external drive with sha256 verification, plus the full
`models/` folder. Run it after every sealed delivery.

---

## 8. Done means

- The disk guard is enforced by `check_agere_build_control.py`.
- A full package run adds at most a few GB of real disk use, measured with
  `df` before and after and recorded in its receipt.
- No sealed database has `-wal`/`-shm` side files.
- `protected-set.json`, `inventory.json` and `archive-index.json` exist and are
  current.
- Free space is above 150 GB after the §6 moves.
- Every receipt path referenced by the control files still resolves, locally
  or through `archive-index.json`.

Report progress in the usual **gate / workflow change / evidence / remaining
gap** form, with free space before and after.
