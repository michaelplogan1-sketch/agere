# Claude ↔ Codex coordination receipt, 2026-10-26

Claude keeps sole integration and runtime ownership: product files, the cascade-v2 worktree, the :8888 measurement server and pushes to
main. Codex owns only new diagnostic harness, test and protocol paths (its review is
`docs/agere/reviews/2026-10-26-stack-and-build-critical-review.md`). Mike's standing instruction is to stop re-architecting: fix specific
measured failures, small and tested.

## Candidate SHAs

| What | SHA | Notes |
|---|---|---|
| Product code loaded in the running :8888 server (`agere-measure-sub`, subscription transport) | **13aecb9** | Started before the commits below; this is the version being measured now |
| Runner process for the in-flight dev3 check run | started at 13aecb9 | Scorer v2 and keys v2 included; it predates the outage stop in a562cd8 |
| Current head of `claude/backstop` (cascade-v2) and origin/main | **3f7b50e** | Not yet loaded in any server |

## Fixes since 13aecb9 (all on 3f7b50e, unit-tested, not yet measured end to end)

- **a562cd8** Runner stops after two consecutive provider-outage responses instead of scoring them; `--quick N` takes N questions per
  bucket.
- **fbefd93** Quote repair (`snap`) never changes negation or stopped/current status. Codex's reproduction ("does not take insulin"
  snapped to "does take insulin") now fails as it should.
- **967eaa6** Three changes:
  - the backstop answer goes through the same `answer_checks.gate` as every proposal; a failure gets one repair attempt with the reasons,
    else it ships `verification: unverified` and `status: partial`, never as a checked answer;
  - the document tools (read, list, search, judge) enforce the request's patient scope (named patient, prior patient or saved cohort)
    and the as-of date at the tool boundary;
  - the subscription transport parses native `<invoke>` tool-call markup. That was the root cause of the x1-25 BUILD-style failure and of
    the earlier unexplained backstop `JSONDecodeError`s; verified on x1-25 with exact $5,815.91.
- **3f7b50e** Scorer v2: an absence answer must name what is absent (Codex's mortality-linkage vs pharmacy-field reproduction).
- Tests: test_backstop_agent 21 and test_scorer_v2 plus test_dev3_set 34 all pass. The full-suite regression diff on 3f7b50e is pending
  and runs before the next server restart.

## Run validity (aggregate only, as agreed)

- `/tmp/e2e_sub_dev3_check_v6.json` (45.1 %) is **invalid**: 26 or more rows are subscription weekly-limit errors.
- The API attempt (`/tmp/e2e_api_dev3_check_v6.json`) is **invalid**: Anthropic API key with credit balance too low. It was stopped, and
  Mike asked for the subscription only.
- The in-flight `/tmp/e2e_sub_dev3_check_v7.json` (candidate 13aecb9) was at 56/71 rows with no outage pattern, and a direct subscription
  probe returned OK at write time. Treat it as valid only if its saved rows contain no provider-error text; it will be checked with the
  runner's outage regex before any number is reported.
- **The held-out milestone was cancelled** (the chained step was killed before it started): 13aecb9 is superseded by unmeasured fixes, so
  held-out would measure the wrong candidate. Held-out runs once, on a frozen qualified candidate, after a valid check / quick run and a
  clean regression diff.

## Provider availability

- The subscription CLI probe answered OK during this check run. Mike's UI reports the weekly limit near or at 100 %, so access is treated
  as unverified for any new run. Each run starts with a one-call probe, and the runner stops after two outage responses. There are no
  repeated outage calls and no paid retry loops.
- The external Anthropic API is not used, per Mike.

## Integration plan

1. When the check run finishes: validate its rows (outage regex), report the aggregate only, and do not grade CHECK text against the
   warehouse.
2. Run the regression diff on 3f7b50e. If there are 0 new failures, restart :8888 on 3f7b50e and run one `--quick 3` on BUILD cases only
   for a functional confirmation.
3. Codex's harness (three matched arms: exact evidence plus explicit definition / investigator with tools / full stack) runs against the
   frozen candidate. Its failure classes (data or key, retrieval or tool, routing or acceptance, provider) decide the next specific fixes,
   using fresh or open BUILD cases only.
4. Freeze definitions, scorer v2, keys v2, data, configuration and candidate SHA. Then one milestone run: the check half as validation
   (labelled as a reused validation set), and held-out once. The final claim needs a fresh, independently authored set with a separate
   adjudicator.
5. Codex's remaining recommendations (release manifest exposing the loaded SHA, a single investigation controller, per-claim evidence
   binding) are deferred under Mike's stop-remaking instruction unless a measured failure requires them.
