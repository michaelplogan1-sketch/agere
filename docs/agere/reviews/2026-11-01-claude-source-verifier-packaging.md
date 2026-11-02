# Source verifier packaging — independent review and owner follow-through

Claude reviewed the supplied code and diagnostics through the subscription transport. It executed no tests and made no clinical assessment. The exact returned review follows the owner assessment below.

The owner adopted the closed operational-authority classifier, retention of unknown/mixed provenance, exact native receipt digests, and a pre-dispatch guard against final claims using reviewer state as source evidence. Prior-review limitation prose is preserved while only its bound embedded JSON is referenced through the existing native store. The same projection is used for coverage and support. The retained global packet changes from 13,237,834 to 223,307 bytes; all 57 patient packet texts and 4,610 complete source contexts remain unchanged. This terminal-form reconstruction is distinct from the earlier live 7.46 MB packet.

For the nine remaining large patient dependencies, the review had not seen the existing gateway's TypeSafe long-context header. The [current provider contract](https://docs.typesafe.ai/models) declares 262k tokens per request and per question; the [32 MiB request limit](https://docs.typesafe.ai/api/errors) is a separate transport boundary. The owner reused that existing Jev route and kept all three protected source-cache producer files byte-identical. All nine complete packets were accepted by Jev 1.13 in 12.552 seconds, with 108,125–156,326 input tokens and $0.048852048 measured API cost. No Claude calls or field-diagnosis fanout occurred. All nine source judgments remain unknown. Accepted transport is engineering evidence, not independent qualification of long-context clinical interpretation.

Sixty-nine focused regressions pass, including mixed provenance, unknown lineage, source-preserving projection, unchanged patient packets, exact routed text, failed/absent routes and prevention of feedback self-certification. The default b41 installation remains the verified recovery point; no repeated package, model weights, source-reader campaign or clinical authority is promoted. Current local save/reopen and financial reuse are checked separately before this candidate is sealed.

Evidence: `artifacts/agere-operational-source-projection-2026-11-01/native-proof/source-closure.json`, `long-context-check/receipt.json`, and the exact review under `artifacts/agere-source-envelope-diagnostic-2026-11-01/claude-review/`.

## Exact independent review

# Review: verifier dependency packaging, 10 unsplittable packets

I did not run the continuity checker or any code. I read only the supplied excerpts, so I have not identified the current gate, and I have not seen `model_policy.py` or `localize_support_failure`. Everything below is either a code-reading observation or a labelled hypothesis.

## 1. What the evidence says about the ten

- **The summary packet is a different problem from the nine patient packets.** Its error ("requires complete patient-bound contexts") fits a packet with no `patient_id`. Claim partition cannot help it either, because `shared_evidence` sits in the protected section carried by every part.
- **The exclusion cannot touch the nine patient packets.** Their `shared_evidence` is 65 bytes. `local_shared` already filters operational views out through `native_result_dependencies`. Their weight is `evidence`, `answer_rows`, `review_scope` and the contracts. I read their failure as a transport-size problem on native rows, not an operational-row leak.
- **Typed-assertion deferral is already exhausted for the nine.** Their `source_contexts` are only 2–31 KB. `source_dependency_packets` defers only `documentary-relation/1` objects, and the parent still exceeds 230 KB. Other machine observation formats stay in the parent because `observation()` returns `None` for them.
- **The 13.24 MB vs 7.46 MB difference is probably the `answer` section.** 13.24 − 7.46 ≈ 5.78 MB, and the offline `answer` section is 5.79 MB. This is an inference, not a measurement. If it holds, the exclusion may make the live-form summary fit but would not make the terminal-form summary fit, because limitations stay inside `answer`. Measure the two separately and do not merge them in the receipt.

## 2. Risks in the exclusion proposal

1. **Marker semantics.** `incomplete_dependencies` is also a general mechanism. Skip a result only if the marker list is non-empty and every entry's `authority` is in one closed operational set. Mixed or other-authority results stay in evidence, since an incomplete real dependency is something the verifier should see.
   - Define the set once, next to the producers (`execution_state.py`), and import it. Keying on `authority` rather than `kind` matters, because the two producers use different `kind` values.
   - Drift in the set fails toward retention, which is the safe direction.
2. **Unknown lineage.** `native_result_dependencies` can return `None`. In that case, keep today's behavior and retain the rows. Otherwise you cannot show that final claims don't depend on them. The size problem then stays as a supported unknown.
3. **Fail-closed placement.** `check()` currently handles only `source_context_complete is False` and oversize. `dependency_complete` is hard-coded `True`. If the final-view closure, an assembly join, or a `computed_claims` source binding includes an operational view, emit the packet with `dependency_complete=False`. Then make `check()` return `unknown` with `decided_by=None` before any provider call. Without that second edit, the flag does nothing.
4. **Silent disappearance.** Add an explicit `operational_inputs` entry (authority, result_digest, row count) to the summary's `native_bindings`. Do not add `shared_evidence_provenance` for skipped rows. The skip must occur before `decode`, which would otherwise json-parse 18.9 MB for nothing.
5. **Limitation prose.** Leave it in `answer`. If the measurement shows `answer` still dominates, the least invasive reduction is to apply the existing exact-offset `review_guidance` to `answer.limitations` strings in the packet view only. Keep the original text digest in the native receipt, and do this only if the measurement requires it. Claims in the manifest are headline, facts and table rows, so limitations are not claims.
6. **Cache and identity.** Summary text changes, so prior summary verdicts invalidate. That is correct. Keep the change identifiable as a candidate on a non-`main` branch until it has retained-input evidence. Default b41 is the recovery point.
7. **Derived queries.** A `query_sql` over `feedback_json` may not inherit the marker. If so it would still enter the packet. Measure this on the retained calls. Do not paper over it with a name or content rule.

## 3. Smallest completion path for the nine

Order the existing lossless steps first, so the two already-partitioned patients (61CC2186, EE8F) are unchanged. Then apply one new step to the nine.

**Recommended: a measured-oversize route in `check()`, at the `bytes > 240000` branch.** The nine arrive there as intact, complete packets.

- **Envelope from policy, not a constant.** Dispatch only if the route's declared context window for `agere_investigation_support_dependency` admits the packet under the same conservative bytes-per-token ratio.
  - If `model_policy.py` has no larger-context Claude entry, this is blocked for this increment, because that file is off-limits. The nine then stay supported unknowns.
  - Above the fallback envelope, return `unknown` with no provider call.
- **The packet text must be byte-identical to the unrouted one.** No sampling, no truncation, no per-patient cap.
- **Record the route on the result and steps.** Include `context_route`, bytes, and the ledger entry. The existing `worker_ledger` path keeps failed-key protection.
- **Check the localization path.** On a non-supported answer, `localize_support_failure(packet, …)` may send the full ~300 KB packet once per claim, 36 times. That would be a spend multiplier, and it might hit the small-route envelope. Verify this on the code. If it does, skip localization for oversize-routed packets and leave the result `unknown`.

**Engineering vs qualification.**
- Engineering entailment is that a complete packet reached a model whose declared window can hold it, with the route and accounting recorded. A supported unknown remains a success when the route does not answer.
- Long-context verification accuracy at 250–350 KB is unqualified. The result should carry that label and must not be read as qualification.
- Any agreement check against the two partitioned patients, or seeded-fault mutations of a retained packet, is a smoke diagnostic with tiny n. It is not a model-superiority claim.

**Not recommended first: restricting patient evidence to the final-view closure.**
- The `support_packets` docstring deliberately gives patients every observed row. Dropping out-of-closure rows would remove possible contrary or historical evidence from the check.
- If it is ever done, it needs a recorded count and digests of what was left out, and a separate contrary-evidence check. That is a new design, not a bounded fix.
- Extending source deferral to non-`documentary-relation/1` machine formats would need a per-format quote and offset validation contract. Consider it only if the byte decomposition shows those rows dominate.

## 4. Decisive checks on retained inputs

Offline, no provider calls. A provider call, if you ever run one, is a separate step.

1. **Byte decomposition of the nine.** Split each by origin (`machine_document_observation` vs executed result view), by in-closure vs out-of-closure, and by the compact vs readable representation. This decides whether the fallback is needed at all and by how much.
2. **Summary before and after exclusion.** Measure the live-form and terminal-form summaries separately. Record the residual per-section bytes.
3. **Exclusion unit tests.**
   - The four marked results are absent from `shared_evidence`, and `native_bindings` keeps their `result_digest` byte-for-byte.
   - A result whose columns look like feedback but has no marker stays in evidence. This proves there is no content heuristic.
   - Other-authority and mixed marker lists stay.
4. **Fail-closed cases.**
   - A final view whose assembly joins an operational view makes no decider call and returns `unknown`, and the aggregate cannot confirm the answer.
   - Unknown lineage retains the old behavior.
5. **Patient packets unchanged.** All final-patient packets are byte-identical before and after the exclusion, and the `restore_table` equality still holds.
6. **Routing.**
   - A packet at 244,125 bytes is routed with its text digest unchanged.
   - A packet above the declared envelope, or a route with no declaration, returns `unknown` with zero provider calls.
   - The 61CC2186 and EE8F packets still partition and are not routed.
7. **Unknown preserved.** A route returning `unknown` or no `decided_by` leaves the packet unknown and the answer unconfirmed.
8. **Accounting.** Each routed call carries a ledger and step receipt with route and bytes. Failed-key behavior is unchanged.

## 5. Report in AGENTS.md format

- **Gate:** not read.
- **Workflow change (proposed):**
  - Operational rows leave the summary, with their digests retained.
  - Oversize patient packets get a policy-declared route instead of an automatic unknown.
- **Evidence:** the offline diagnostics you supplied. I executed nothing.
- **Remaining gap:**
  - It is not established that the exclusion alone makes the terminal-form summary fit.
  - I have not seen whether a larger-context route exists.
  - I have not seen whether localization respects envelopes.
  - Long-context verification quality is unqualified.

If two increments pass without gate progress, the AGENTS.md rule requires a reassessment recorded in the control decision log.
