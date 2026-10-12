# Scientific references in the original-question workflow

Delivered October 12, 2026 under G3. A question can now carry a dated scientific
reference, its applicability questions, full patient/population evidence, owned
internal review and an exact missing-evidence request through the existing product.
This is a connection of previously separate capabilities, not clinical qualification.

The actual free-form development question kept all 1,614 original patients. Its
four nodes selected the population, reconstructed received care-request/completion
accounts, reconciled financial accounts, and attached the available ATS delivery-
choice reference. The planner chose the registered version and evidence links;
the verifier did not inject a graph or reference choice. All six applicability
questions remain unresolved for every patient. Linked context is a proposal for
review, not a judgment that it is relevant, sufficient or clinically applicable.

The P-1001 member review retains 160 exact source versions, the original population
question and dated reference. Existing literal care-request evidence nominates
navigation review for 1,268 members; the reference itself nominates no service.
One synthetic task was assigned to the existing navigation review team and handed
off internally. Its saved evidence request names the exact access question and
reference version. No request was sent externally and no treatment, eligibility,
benefit, enrollment or clinical review is claimed.

The shared planner now selects exact reference IDs before loading their complete
question definitions. All four available reference titles/IDs remain visible;
three unselected definitions are explicitly deferred. The selected request uses
17,837 bytes and the selector 19,633 bytes, both within the actual 20,000-byte
transport limit. The earlier 32,000-byte planning guard did not match that limit;
it now does. Original text, unknowns, loaded/unloaded identities and failed receipts
are retained. Empty question bindings explicitly preserve missing patient evidence.

Verification:

- 160 focused graph, planner, work/acquisition, reference lifecycle and reassessment
  tests pass. Browser JavaScript and embedded workbench script parse successfully.
- Exhaustive audit: 1,614 reference member receipts, 9,684 unresolved questions,
  3,228 exact upstream context links and 6,456 questions without linked evidence.
- Actual API question → parent → member → task → handoff → applicability request →
  check → unchanged recomputation succeeds; browser DOM/interaction confirms all
  six questions, reference dates, exact saved request and owner, with no JS errors.
- In a consistent disposable copy, correcting one patient's received financial
  source changes that person's financial and reference receipts only: 4,840 of
  4,842 node receipts stay identical, including all other 1,613 members.
- Withdrawing the reference in that private copy changes all 1,614 reference
  receipts while all 3,228 care/financial receipts stay identical. Both changes
  stale the prior question/request and require reanswer; old work and answers
  remain unchanged. Original reference/source stores were not mutated.
- Restart reopens parent/member/task/request as current; same question/result replays
  with zero provider calls. Parent/member/request reopens took 1.682/1.015/2.021s;
  replay took 1.583s. The final question took 25.168s with its selector cached.
- 327 baseline files preserved: 170 protected files and 157 prior receipts. Original
  v1.23 warehouse, 1,614-person roster, ten-area backlog and every prior row in ten
  work tables remain unchanged. One task and its append-only review history added.

Product inference: four provider calls across development, including one failed
plan and a superseded selector, have returned usage totaling an estimated $0.273228.
The final execution made one new plan call ($0.067660) and reused its selector;
actual final selector plus plan usage totals $0.140920 if neither is cached.
This is API usage estimation, not an invoice or total cost. Engineering-chat tokens,
local compute and human reviewer time are separate and unmeasured. No SLM/model
qualification or competitive superiority is inferred from these checks.

The release assessment honestly reports 6/9 finite engineering requirements, overall
failed: this code change invalidates the previous current-code compatibility receipt
for evidence-change reassessment. That immutable receipt remains intact; today's
source/reference tests do not silently promote or replace it. Historical individual
and mixed-criteria contexts and incomplete medication remain explicit regressions.
G3 remains active; no gate is promoted.

Remaining scope includes actual assessment of supporting/contrary/missing clinical
context, study-level evidence and deduplication, update surveillance, verified
clinical applicability, provider/contract attribution, causal/OAP terms, qualified
prediction/discovery and model roles. This delivery does not broaden the reference
corpus or automatically carry earlier applicability answers into a replacement.
The existing service-task reference reassessment path remains preserved; graph
recomputation retains its own exact original question and never selects a successor
reference automatically. Lifecycle impact preview for legacy service manifests is
not a complete cross-product graph-impact index.

The current Find Agere site inspiration site was inspected read-only. Runtime8885
exposes this delivered feature. The separate public/design checkout was not changed.
The native scrolled screenshot returned a blank large surface; DOM and interaction
checks pass, but pixel-layout/responsive acceptance is not claimed.

Next G3 increment: connect question-specific source discovery and synthesis to these
exact applicability gaps, using existing source-plan, source-meaning, reconciliation
and review machinery. Demonstrate supporting, contrary and missing evidence with
exact excerpts and dates under the same original patient/population question, plus
an explicit later-source reassessment. Assess relevance and evidence sufficiency
separately from clinical applicability. This advances the master plan's evidence,
meaning, science and accountable work layers; retain provider/VBC/OAP, broad query
composition, historical workflow recovery and all G5 qualification gaps. Do not
substitute another reference catalog or release-receipt-only increment.

Evidence: `artifacts/agere-graph-references-v1/implementation-receipt.json`.
Build and automatic heartbeat pause at this human turn boundary.
