# New contexts use stable references directly

October 10, 2026. New category or explicit-record contexts now run the existing bounded source-pass contracts, save those passes independently, and proceed directly to pass-qualified reconciliation. There is no intervening legacy reconciliation call. Existing paid legacy answers replay unchanged. A one-pass context needs no reconciliation call.

`source_stage_synthesis.py` preserves source results and progress independently from reconciliation, checks source and implementation identities, verifies complete declared-unit accounting, and records request, token and usage evidence. Reconciliation failure leaves the validated source passes reusable. Pending or failed provider requests retain the existing no-silent-retry policy. Uncapped spending remains uncapped; input bounds, caching and accounting remain enforced.

The normal `/api/stack/context/plan`, `/synthesize`, `/progress` and saved-answer inspection paths route through this implementation. Final answers integrate with existing evidence needs and program assessments. A saved source-stage parent explicitly records that its cross-pass reasoning is pending; it does not claim a completed overall answer.

## Verification

- 71 focused tests passed across default routing, stable references, uncapped accounting, category/record scope, synthesis work and action work state. Six tests specifically cover the new default engine, saved replay, partial recovery, single-pass handling, tamper invalidation and failed-call protection.
- Actual HTTP exercised the existing packet-follow-up context `744de15fc0327a22661f33477e2325c79ea53fcb1545c2f3f367371eff080624`: five received records, 11 source units, two source calls and one stable reconciliation call, estimated total **$0.225312**. This is a provider usage estimate, not an invoice.
- Saved answer `fc6d15d4fd41a67aafd6615ba5c5c34c53688afc6f7373f3e69357abb3c59815` contains 12 findings and 25 structurally checked exact citations. Six of 14 review questions have exact source bindings; eight remain unbound. Source pass request sizes are 15,114 and 7,598 bytes.
- Main-route replay makes zero new calls. Saved context planning, progress, canonical inspection, evidence-needs binding and program assessment attachment pass. Four prior answers are unchanged.
- Browser verification shows the source coverage, findings, stable references, proposed questions and cost receipt; no console errors were observed. See `artifacts/agere-default-stable-v1/answer-desktop.png`.
- Sixteen protected modules/catalogs and active original-cohort v1.23 configuration are unchanged. No generator, frozen model, reserved cohort or Documents research edits.

Implementation and verification data: `artifacts/agere-default-stable-v1/implementation-receipt.json`, with HTTP, source-stage, answer, needs, assessment, replay and preservation artifacts beside it.

## Remaining limits

Correct source identity and exact quotation do not establish clinical correctness, retrieval sufficiency, current treatment state, eligibility, consent or patient benefit. Source-pass proposals remain preserved even when later excerpts qualify them; no automatic semantic resolution is claimed. Cross-group relations remain unassessed when partitioning is required. The next integration returns the interpreted follow-up evidence to its exact internal work request without treating machine output as a human review or completed care.
