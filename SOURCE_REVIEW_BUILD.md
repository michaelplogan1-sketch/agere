# Changed-source review through the original decision

G3 now connects detected source changes to bounded review contexts and the original
question, date window and internal task. This closes a preparation and attachment
gap. G3 remains **5/6 individual engineering workflows**; no new model interpretation
or clinical qualification is claimed.

New decisions index the source dependencies of every saved finding. The existing
decision/work screen proposes current prior-selected records, changed received
records and typed reference dependencies. It preserves the original date window,
lists exclusions and absent records, and distinguishes changed cited sources from
unassessed effects on other findings. Historical decisions disclose their partial
finding index. Preparation is disabled when no source change is established.

Preparation reuses the existing whole-record context and source-pass planner. Each
group retains exact source versions and original task binding. Saved subset answers
can attach through the existing synthesis interface and decision-follow-up store;
they cannot automatically replace the full original answer, establish clinical
change, authorize care or complete work. Changed sources, context omissions and
changed task revisions prevent stale attachment. No new product module or store was
introduced.

Verification:

- 62 focused tests passed, including date exclusions, typed references, historical
  dependencies, stale revisions, wrong answer bindings and partial-answer guards.
  JavaScript syntax passed.
- Actual active-warehouse HTTP and browser checks reopened the functional decision
  and correctly disabled preparation for unchanged evidence.
- An isolated development replay used the real protected context planner to prepare
  nine whole records (eight previous records and one explicitly nonclinical probe)
  without omissions. The original question, date window, active warehouse and work
  remained unchanged. A fresh process reopened the review and verified every context
  hash.
- Answer attachment is covered by contract tests with explicit machine stubs. It
  has not been verified with a newly generated model answer. Positive changed-source
  preparation ran in the isolated replay, not as a fabricated active-warehouse
  arrival.
- The initial verifier failed because its standalone bootstrap lacked the existing
  runtime context adapters. That failure is retained. A corrected verifier using
  the same adapters as the workbench passed in a fresh evidence directory.
- All 170 protected files and 186 prior sealed evidence files remain unchanged.
  No provider calls or additional provider spend occurred.

Evidence is sealed in
`artifacts/agere-source-review-v1/implementation-receipt.json`.
The successful integration evidence is under that directory's `verified/` subfolder.

Remaining G3 work includes the incomplete medication execution, subject to the
pending specific transfer approval, and actual changed-source reinterpretation and
affected-result verification. Patient-level invalidation remains conservative;
selection sufficiency and clinical relevance remain unassessed. Population/VBC/OAP,
role qualification and packaging are still required by the unchanged full release
contract. Follow the canonical build control for the next independent increment.
