# Agere runnable release and controlled work recovery

G6 now has a versioned application and Python runtime that starts from a fresh
local installation. Existing synthetic owned work can resume, survive a restart,
and be rolled back by creating another installation from the sealed checkpoint.
The active original workbench and shared provider ledger remain unchanged.

This closes the missing reproducible-startup check. Local operations are **5/6**,
source/adapter conformance **4/5**, and the full release remains **4/9 engineering
requirements passed**. These counts are acceptance requirements, not a percentage
of the product completed. Full restored operation and clinical/model qualification
are not accepted.

The implementation and evidence are in
`artifacts/agere-runnable-release-v1/implementation-receipt.json`.

## Executable behavior

- `release-v2` archives 386 application/rule/configuration/style files and 26,098
  runtime/library files (1,050,020,564 bytes). Its paired `checkpoint-v2` contains
  1,427 build files, 66 SQLite databases and 20,988,029,867 bytes.
- The archived interpreter, libraries and application start independently of the
  current development checkout. A later acceptance-configuration change in the
  checkout did not change or prevent verification of the archive.
- A fresh `installation-v2` reopened the original respiratory question, exact
  evidence/decision, buyer/payer context, saved tasks and follow-up. The selected
  existing task advanced from revision 2 to hold revision 3 and handoff-prepared
  revision 4. Duplicate replay was idempotent; a stale revision received HTTP 409.
  No external handoff was sent and no human review was claimed.
- Restart preserved revision 4. A separate fresh `rollback` installation restored
  revision 2. Both the changed installation's revision-4 audit and the rollback's
  revision-2 audit remain intact. Rollback never overwrites an existing installation
  or rewinds the shared provider ledger.
- Original individual and population decisions, work and follow-up matched their
  baseline after each operation. The original 1,614-person roster and 1,238,686
  received records remain available. Existing source and adapter evidence stays
  attached to its original versions.

## Verification and failures retained

26 focused tests passed, covering archive/runtime/configuration corruption,
unsupported platform, exact release code, checkpoint changes, changed frozen-plan
dependencies, installation identity/mount tampering, destination collisions,
rollback audit preservation and the existing transactional recovery boundaries.
Actual local HTTP, browser reading, stopped-process restart and fresh-directory
rollback checks passed. The live scorecard is saved in `scorecard-final.json`.
The preservation audit found no changes to 170 protected files or 128 preceding
implementation receipts. There were zero new provider calls, semantic inferences
or human reviews; provider dollars for this increment were zero. Total local
compute effort is not represented as a measured dollar amount.

The first archive failed real application reopening because the earlier code
inventory omitted adjacent JSON rules and CSS. That attempt executed no work
events. The inventory was corrected, a regression check added, and a new checkpoint,
archive and installation verified. Failed logs and manifests are retained under
the unsuffixed attempt directories and `failed-package-analysis.json`; they are
not overwritten by the successful `-v2` evidence.

The browser also exposed a remaining transient initial-panel JSON parsing error
when a restricted endpoint returns HTML 403. The saved question subsequently loads
and its work state is correct. This is a remaining operational UI defect, not a
claim of complete UI acceptance.

## Scope and compatibility

This is a **same-platform local installation** with explicitly retained external
source, model and research resources. It is not a universal or disconnected
cross-machine distribution. The checkpoint and release directories must both be
retained at their recorded locations. External dependencies are checked at their
declared manifest level; this is not a new audit of every model/source byte.

The installation mounts the current shared ledger rather than making a rollback
copy. Frozen cohort plans retain their original workspace and source bindings.
Their hashes and original locations are explicit in the installation contract;
the existing learning engine correctly rejects relocated qualification. No frozen
engine, plan, evaluation family or validation check was rewritten or bypassed.

The installed service currently permits saved reads and existing owned-work
events only. New question preparation, portable intake saving, statement review
and model interpretation remain disabled there. The original workbench retains
its existing functionality. Complete operational activation cannot be claimed
until the remaining paths and qualification compatibility are verified.

## Reopen the verified installation

Run from any directory on this machine. The original workbench uses port 8885;
the temporary verification server on 8887 has been stopped.

```sh
/Users/mikel/clinical-insights-local/artifacts/agere-runnable-release-v1/installation-v2/runtime/python/bin/python3.12 -I /Users/mikel/clinical-insights-local/artifacts/agere-runnable-release-v1/installation-v2/bootstrap.py check
/Users/mikel/clinical-insights-local/artifacts/agere-runnable-release-v1/installation-v2/runtime/python/bin/python3.12 -I /Users/mikel/clinical-insights-local/artifacts/agere-runnable-release-v1/installation-v2/bootstrap.py serve --port 8887
```

For another rollback, stop the copy, use the archived release's `bootstrap.py
install --output <fresh-absolute-directory> --resume-work`, and retain the changed
installation and its audit. Existing output directories are refused. The archived
interpreter must run its own bootstrap; a newer checkout must not substitute its
code for the sealed release.

Release ID: `622aa74df06c1408eabb749c0682d509be57c9921bdce04337385c1680eb50d7`.
Checkpoint ID: `8e7a7e209c1e96e0a03ec4eaa8d25147021c21e330a9a4b07147a915c89d0192`.

## Engineering assessment and next boundary

This is measurable progress against the evening review's runnable-release finding:
the original data-only recovery point now has its own executable code/runtime and
actual work resumption/rollback evidence. The modular architecture and complete
population/VBC/OAP charter remain appropriate. Packaging does not resolve clinical
qualification, changed-source interpretation or production readiness.

The canonical next action remains G6 in `AGERE_BUILD_CONTROL.json`: finish normal
deterministic question/intake/work operation in the installed release, correct the
restricted-page startup error, and make qualification compatibility explicit
without transferring frozen approval to a changed workspace. Verify source/version
invalidation and activation/rollback before closing the remaining operational and
adapter checks. Reuse this archive path; do not add another model catalog or broad
refactor.

G3 remains 5/6 with medication execution and actual changed-source recomputation
incomplete. The specific P-1001-to-Anthropic transfer remains blocked pending its
existing approval; no retry occurred. G5 routing remains 4/5, with full economics,
the eight-label supervision gap and independent model/clinical qualification still
open. All ten backlog areas and external production prerequisites remain in scope.
