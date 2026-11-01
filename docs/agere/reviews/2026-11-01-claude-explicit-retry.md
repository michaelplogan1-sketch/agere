**No material defect found.** I read the diff and traced the paths below; I did not run the tests.

**Checked and holding**
- **Row preservation:** `authorize_retry` only inserts into `request_retries`. It never updates or deletes `calls`, and the triggers make authorizations immutable. The prior attempt's digest is recorded in the payload.
- **Duplicate protection:** `BEGIN IMMEDIATE` plus the check that the resolved tail is `failed` blocks duplicates in every state:
  - An unused authorization has no `calls` row, so `row is None` raises.
  - Pending and validated tails raise.
  - A second concurrent authorizer resolves to the new attempt, finds no row, and raises.
- **Stale resolves:** a caller that resolved to a failed key before authorization then hits `reserve()` on that failed row and gets `retry_blocked`, so no paid call is made. A caller that resolved to an unused attempt key and loses the race gets "pending", also with no extra paid call.
- **Chains:** `resolve_request_key` returns the latest attempt for a key, and the chain stays consistent whether the owner authorizes from the root or from a mid-chain key (context_student passes the already-resolved key). The cycle guard is sound.
- **Cache reuse:** both `classify` and `send` resolve before the validated lookup. A validated retry replays by the logical key from a new process and ledger connection, as the test shows.
- **Failed-response protection:** `send` now requires `old[1] is None and not saved.exists()`, which is stricter than before. Response-bearing failures stay blocked.
- **Receipt paths:** these are keyed by the resolved attempt key. The prior key's `.json`, `-failure.json` and `-revalidated.json` files are never overwritten. The original-key revalidation branch runs before resolution, which matches its stated intent.
- **Accounting:** each attempt gets its own reservation, and the failed one keeps its reservation. There is no hidden cap or prompt change.

**Non-blocking observations (not defects against your requirements)**
1. In `send`, `authorize_retry` runs before the env-var and receipt-storage preflights. If one of those fails, it leaves an unused authorization. The next call resolves to it and uses it, so nothing is lost or duplicated. Moving the authorization after the preflights would be cleaner but is optional.
2. `authorize_retry` accepts any `failed` row, including response-bearing ones. That is consistent with an explicit owner call on a verified list. The build owner's DNS-only list is the only guard, so filter on `response IS NULL` in the driver script.
3. Pre-existing and unchanged: `reserve()` deletes `abandoned` rows on re-reserve. This can now apply to a retry attempt that died pending, but never to the preserved original failed row.
4. `prepared` results from `classify` now report the resolved attempt hash rather than the logical hash. Check that no caller compares it to the logical key.
