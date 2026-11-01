# Claude review: reservation accounting performance

Exact subscription review of2632d98. Supplied code/receipts only; no executed product tests or clinical judgment.

# Review of 2632d98 (gateway reserve authorization)

**Verdict: no introduced overspending, race, cache, failure or accounting defect found.** I could not see the old `reserve` or the diff, so the equivalence points below rest on the supplied receipts and on `receipt()` being unchanged. I ran nothing.

## Authorization and atomicity
- **Fresh cap read:** The cap is read after `BEGIN IMMEDIATE` and before the insert. The write lock is held across the read, the sum and the insert, so two connections cannot both consume the same remainder. The two-connection test exercises exactly this.
- **Same formula:** The capped check, `cap - imports - current < reservation`, matches the `remaining_reservation_usd` definition in `receipt()`. It sums the same `imports` and `calls.reserved` columns, including failed and pending rows. So failed attempts still hold their reservation, and the check does not drift from the reported number.
- **Uncapped authorization preserved:** A `NULL` cap skips the check entirely, and a cap removed on another connection is seen on the next reserve. Uncapped requests no longer depend on historical `usage_cost` pricing succeeding. That removes an incidental failure mode: a malformed old response could previously have blocked a fresh request. It is not a loss of protection, because uncapped has no limit to enforce.
- **Rollback safety:** If an abandoned row is deleted and the cap check then fails, the exception rolls back and the abandoned row is restored. Pending and failed keys still raise before reaching the cap logic, and the test shows their rows unchanged.

## Cache and failure behavior
- **Cache replay:** Validated replay happens before the cap check and commits first. A cache hit never consumes or requires authorization. The cache path is unchanged.
- **Every attempt is recorded:** The insert and the `reserved` event are unchanged, so each fresh attempt is still recorded.

## Pre-existing items, not introduced
- Float summation in the cap comparison has no tolerance. The tests use exactly representable values.
- A denied reservation leaves no `ledger_event` audit row. Only `reservation_requested` is logged.
- `ledger_event` runs before `BEGIN IMMEDIATE`. This assumes it commits, as it did before.
- `extend_authorization` compares against a cached `self.cap`. It does re-check the database value, so a stale cap fails closed.

## Evidence and test gaps (minor, none blocking)
1. **Uncapping not tested:** There is no test for a capped ledger being uncapped externally and the next reserve succeeding. The lowered-cap case is covered, and this is the symmetrical authorization-preserving case.
2. **Abandoned replacement not tested:** There is no test that an abandoned-row replacement under an exhausted cap rolls back and keeps the abandoned row.
3. **Telemetry not shown:** `cost_telemetry.tag` and `ledger_event` were not supplied. The after-profile shows no tag work, so I could not confirm they do not call `receipt()`. They are also unchanged by this commit.
4. **Timing sample:** The timing has 3 samples per arm, with the first after-sample about 8 times the others, so a 606x ratio is indicative only. The receipt scopes it as reservation overhead on one 25,474-row ledger, not workflow speedup. That framing is accurate.
5. **Receipt wording:** The exact-equality claim is credible only because `receipt()` was untouched. It does not show the reserve path unchanged. The unit tests carry that.

## Suggested follow-up
Add the two small tests above in the same file, with no gateway change.

## Owner follow-through

Both requested negative tests were added: an explicit cap removal after opening the ledger is honored, and a denied abandoned replacement rolls back its original fixture row unchanged. The gateway logic did not need another change. Ledger-event and cost-telemetry call sites are unchanged; measured live-history reservation replay does not price prior responses.

An isolated three-connection replay executes all2476reservations/finishes from the actual original request in1.233486seconds, preserving every original response value/status/error and the complete cost report. No provider was contacted and no real ledger row changed. The original4.172869196API-equivalent total reconciles exactly. This is accounting-path throughput, not the full workflow speed or clinical qualification.
