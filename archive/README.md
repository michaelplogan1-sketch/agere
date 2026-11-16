# Archive

Files that were at the top of the repository until 2026-11-16 and that nothing live uses: one-off experiment runners,
verification scripts, receipts generators, older reports and plans, and the tests of code that only those scripts used.
They were moved here with `git mv`, so each file's history continues (`git log --follow archive/top-level/<name>`).

- `top-level/` holds them under their original names. A top-level path in an older report or build-record entry
  (for example `run_x.py` or `AGERE_CTO_REVIEW_2026-10-20.md`) is now `archive/top-level/<same name>`.
- The old `README.md` is `top-level/README.md`.

What stayed live, and how that was decided: every file that the product core (`clinical_core/agere/tiered/`), the
customer web server (`clinical_core/agere/hosted/`), the local app shell, the serve scripts, the build checker or the
core's tests can import, directly or through other imports (including imports inside functions), stayed in place.
Tests whose imports stay inside that set moved to `tests/legacy/` and still run (`python run_tests.py --legacy`).

Archived code is not expected to run from here. To bring a file back, `git mv` it to its old place.
