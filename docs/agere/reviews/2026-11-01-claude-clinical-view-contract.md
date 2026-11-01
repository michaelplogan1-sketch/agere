## Concrete defect

**1. The first quota-rejected request is not classified as billing, so it likely skips the fallback.** (`gateway.py`, `classify_provider_error`; confirm against `S.decide`.)

- The new 401 branch opens the circuit, then re-raises the raw `HTTPError`.
- `classify_provider_error` only maps code 402 to `billing`. For other codes it falls through to text matching on `str(exc)`, which is `HTTP Error 401: …` and does not include the body.
- So the triggering request returns `None` (a "request error"), and `call_with_fallback` and any other classifier-based caller propagate it unchanged. Only later requests get `ProviderUnavailable(kind='billing')`.
- The new test covers the circuit and the cache but not a fallback for the first rejection. The existing fallback test uses 402 only.
- **Smallest fix:** where `quota` is set, tag the exception, e.g. `e.billing_quota=True`. Have `classify_provider_error` return `'billing'` when that attribute is true. Add a test that runs `S.decide` with a first-request 401 quota rejection and asserts the fallback runs.
- If `S.decide` already catches any exception from the primary provider and falls back, this is moot. I can't see that code.

## Hypotheses and minor items (not blocking)

- **Care-collection selection:** `next(row[k] for k in care_keys if k in row)` takes the first alias present. If `care_assignments` is null and `structured_care_team` holds objects, you get a false "available care missing" rejection. A fix would be `any(objects(row[k]) for k in care_keys if k in row)`. This is only reachable with duplicate aliases.
- **`objects()` shape check:** it accepts any list of dicts, so `[{'note':'x'}]` satisfies `care_assignments`. That matches the stated scope ("presence of intact objects"), but the contract does not enforce `clinician_id`/`role`.
- **Care-DTO gating:** it requires all 9 columns. If a native care tool omits one, such as `effective_end`, `available_care` stays empty and null silently passes. This is the intended narrowing, but it is a silent miss; the proof doesn't show that the real tool emits that exact shape.
- **Trigger scope:** `requested` is true for any `kind=='deliverable'` step, not only medication-review templates. A flag table plus one stray detail key (e.g. `dose_summary`) in a non-medication deliverable would demand all three bindings. This is bounded by the clinical-flag and detail-key requirements.
- **One issue per pass:** the function returns on the first issue. A row with a missing binding and an available-care problem takes two repair rounds, which costs only local repairs.
- **Quota detection is not provider-gated:** it applies to any provider, but the circuit is keyed per credential, so the blast radius is limited. Non-dict `error` (e.g. a bare string `"insufficient_quota"`) is deliberately not matched.

## Checked, no defect found

- `detail` is always bound inside the `HTTPError` branch where the 401 check sits.
- Unparsable or non-dict bodies fall to `quota=False` via the caught `ValueError`/`AttributeError`.
- `[]` is correctly rejected, because `objects([])` returns a falsy `[]`.
- The `row[...]` access after the missing-key return is safe.
- The flag-only, zero, unknown and dose-count guards are unchanged.
- Cached successes and failed-key protections are untouched.

## Evidence gap

The ordinary-run proof shows only the missing-binding rejection. It does not show an accepted positive path or a real 401 fallback.

## Owner assessment

`system_one.decide` and `judge_fields` already catch the triggering exception and try the configured alternative, so the proposed first-request fallback defect is not reproduced on those paths. The shared error classifier nevertheless now tags the explicit quota rejection as billing, with a real first-request typed-fallback regression. Cached success and failed-attempt protections stay intact.

The available-care gate matches the complete native relationship shape actually emitted by the ordinary query. The current formatter gives a present canonical field precedence over aliases, so accepting a populated alias behind a null canonical field would silently render unknown; that suggestion was not applied. Detailed care and fill objects now require their existing date/source and date/count interface keys. Explicit null remains valid only for source-scoped unavailable input. Positive-path and cross-patient availability regressions pass. Actual final binding and report audits remain required; these tests are not clinical qualification.
