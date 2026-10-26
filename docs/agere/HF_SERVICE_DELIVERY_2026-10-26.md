# Physician-requested HF service workflow

Mike requested a complete HFrEF population review: foundational medications,
dose comparisons, documented reasons for omissions or lower doses, recent HF
admissions, PCP/cardiology relationships and activity, followed by a daily
wraparound-service workspace with proposed monitoring-device fulfillment.

The ordinary compound request stalled in SQLite and was stopped after about
1,010 seconds. This attempt is preserved and is not successful product execution.
The requested deliverable was subsequently completed through assisted review of
the received synthetic sources, using existing governed relations, full-document
interpretation, automated evidence review and frontier composition. No
disease-specific product handler was added. Dataset values, patient exports,
model outputs and runtime artifacts remain local and are not part of this PR.

Local receipt: `artifacts/agere-hf-wraparound-2026-10-26/workflow-receipt.json`.
The receipt records population completeness, exact quoted-source validation,
automated-review uncertainty, frozen exports, render verification and actual
browser checks. The searchable workspace includes individual medication/dose
evidence and dated reasons, admissions, care-team evidence, daily owner queues,
service design and staged device review. Proposed actions remain separate from
confirmed enrollments, clinical orders, live sensor readings and shipment.

Current reduced EF, actual medication use, individualized appropriateness,
maximum tolerated dose, service eligibility and accountable clinical ownership
remain uncertain wherever the received evidence is missing, old or conflicting.
No physician adjudication, real care execution, or universal answer capability
is claimed. G3 remains active; all ten acceptance areas remain in scope.

The shared subscription transport had a separate hidden timeout/retry defect.
Commit `b944abd` honors the caller's timeout, leaves unbounded calls unbounded,
and removes automatic repeated generation after a timeout. Claude still uses
subscription transport only, with stop-on-limit behavior and no external-key
fallback. The focused suite passed 265 tests with two optional live skips.
This fix does not resolve the SQLite stall.

Next, trace the stalled ordinary query's execution plan and repair the shared
evidence-execution path, then rerun the original request through the product.
Do not replace that work with a bespoke HF handler or further one-off questions.
