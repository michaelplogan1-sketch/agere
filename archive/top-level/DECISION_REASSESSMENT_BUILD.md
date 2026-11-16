# Saved-question reassessment — partial G3 delivery

The shared question/work path can now prepare a bounded reanswer, validate its exact original question and date window, recompute local source-category service options from a current answer, and link replacement internal work to the prior task. Old answers, decisions and work remain immutable history. This implementation is available in the restarted original workbench.

**Engineering verification is partial. The actual changed-source interpretation is blocked by automatic approval review. G3 is not accepted; overall release engineering remains 6/9.**

## Delivered behavior

- The saved decision's existing changed-source preview offers **Reanswer and refresh service options**. The existing record/context/pass planner supplies prior-selected current records, newly received or corrected records and eligible typed references, with the original date window. Missing and excluded records are disclosed to the reader.
- A saved current answer must match the exact prepared context, received inventory, patient, original question and date definition. Incomplete/multiple context groups are explicitly rejected; no subset silently becomes a full answer.
- The refresh reuses the existing local source-category planner, buyer/payer matching and participation checks. The UI names this baseline even when the prior decision used the unqualified buyer-aware candidate. No paid planning is hidden in this operation.
- Finding comparison requires exact content equality to retain a result digest. Unchanged citations alone cannot preserve semantic validity. Other old findings become historical without a claim of resolution or semantic equivalence.
- Replacement internal review uses the existing queue/owner/prerequisite checks and retains its explicit link to the original task. It does not close old work, infer a care outcome, reserve a service, establish coverage or contact anyone.

## Evidence and limits

`artifacts/agere-decision-reassessment-v1/` retains:

- **42 focused tests passed**, including a current-answer **test stub** through recomputation and linked owned work, exact reproduced-result identity, stale source/task rejection, wrong answer/context rejection, changed citation comparison, no-change idempotency and multiple-group rejection. These are engineering tests, not real model output or clinical adjudication.
- Production HTTP handler and actual browser verified source preview, idempotent reanswer preparation and opening the exact bounded context in an isolated development replay. The replay contains a changed appointment status and a separate late-arriving conflicting account for P-3002: 8 selected records, 16 source units, 3 planned source passes and up to 4 inference calls. No inference call occurred.
- The fixture copies only the two relevant patients' records/events and uses read-only original retrieval vector inputs; it is not a rebuilt full-cohort retrieval release. The original received warehouse is untouched. The old parent answer is retained in the evidence file; the minimal replay did not relocate its provider cache, so its historical view also reports that prior cache unavailable.
- A fresh process reopened the saved preparation/context. The original server was restarted and its existing appointment question and work returned exactly the same view; the new endpoint correctly rejected a no-change request. Hosted inference remains disabled on the temporary verification server, which is now stopped.
- **170 protected files and 137 captured prior receipt files are unchanged**. Active P-3002 inventory and task are unchanged; the unrelated P-1004 inventory retains its exact identity in the replay. No fine-grained unaffected clinical result claim is made from this inventory check.
- Zero paid dispatches, zero clinical reviews, zero external care actions. The initial test fixture lacked a context ID and the first replay launcher used the wrong run-metadata path; both were corrected before the final checks.

Automatic approval review rejected the proposed P-3002 context transfer to Anthropic because it lacked explicit authorization for that specific payload/destination. The prepared payload is `current-context.json`, context `54b392eec8007e82640c2d0352628773011d104d30d8ca160fc1d795803d1817`. An approval request was presented. No retry, alternate provider or indirect dispatch was attempted. The earlier P-1001 medication transfer remains separately blocked.

## Still required

1. Specific approval, followed by actual bounded P-3002 interpretation, source-grounded inspection of conflicting/late evidence, real replacement decision/work execution, and the corresponding browser/restart checks. Resume the saved replay; do not rebuild or rerun failed keys blindly.
2. Bind missing/excluded dependency acquisition to this original decision/work path and verify it locally. This independent G3 work may continue while transfer approval is pending.
3. Demonstrate the full unchanged-result/dependency behavior and complete multi-group reassessment; the new bounded single-group path does not claim those are finished.
4. Complete the separately blocked medication workflow and retained G5 economics/model/clinical qualification requirements. All ten full-product backlog areas and population/VBC/OAP remain in scope.

No finite acceptance contract or scope was relaxed. The prior G6 archive remains the known-good local recovery point; this new candidate has not been repackaged as a release.
