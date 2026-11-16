# Common review rules under uncertain information models

Selected existing documentary review questions now support joint information-value scenarios and comparison of one common observation-to-review rule across explicit alternative models. Correlated observations are not double-counted; each exact request costs once. No-information, single-request, structural coverage, highest worst-model utility and smallest worst-model regret baselines are visible. No model probability, clinical benefit or acquisition authority is inferred.

The preceding [joint information-value integration](SHARED_INFORMATION_BUILD.md) is delivered and preserved. This second capability asks a different question: which fixed acquisition bundle and observation-to-review-choice rule performs best across the explicitly supplied models when model identity is unknown? It does not combine independently optimized, model-specific terminal choices into a fictitious common policy.

## Executable workflow

Through the actual question planner, open **Compare joint information value across questions**. Saved scenarios can be copied as starting assumptions without editing their history. Save an explicit variant, then select two to four current scenarios under **Compare decisions across uncertain information models**. The exact source/work proposal, selected questions, request universe, named states, utility matrix/unit, common objective and joint observation definitions must match. Priors, reliability, costs and availability can differ explicitly.

The new adapter enumerates every deterministic common rule within its declared bound: up to three requests, three terminal review choices and four joint observation vectors. It evaluates the same rule in every model using their marginal likelihoods. It compares highest worst-model expected net utility (maximin) and smallest worst-model regret, where each model's benchmark is its own best feasible acquisition-and-review strategy. No-acquisition baselines also use one common choice across models. A candidate must be available in every selected model. Unavailable bundles remain visible. Ties use the stated deterministic order, not clinical preference.

These are standard finite decision criteria, consistent with the definitions in [MIT's classical decision-theory notes](https://ocw.mit.edu/courses/16-422-human-supervisory-control-of-automated-systems-spring-2004/1e2d571dad4b0bcdc9452d433400da21_030204classdecis.pdf), applied here to supplied documentary-review models. No novel theorem, model quality or clinical validity is claimed. No probabilities are assigned to which model is true. The selected uncertainty set is not a confidence region or exhaustive set of possible patient states.

## Actual example and verification

The saved P-7193 comparison binds two medication questions, two exact requests and two deliberately hypothetical models: one informative, one uninformative. The same 14 common rules are enumerated. Maximin favors **no acquisition**, with worst-model expected net utility0.5. Minimax regret favors **one request**, using the same response rule in both models, with worst regret0.1 versus0.2 for no acquisition. That rule's worst modeled net utility is0.4. These are different criteria with visible trade-offs; Agere does not choose a clinical action or dispatch work.

The final focused suite passes **72 checks, including 28 new tests across the two capabilities (16 joint and12 common-rule tests)**. The earlier60-check suite is included, not additive. Thirty random joint models match an independent direct expectation sum;25 random model sets match independently enumerated common-policy criteria. Tests include conflicting model-specific choices, uncertain priors, uninformative evidence, infeasible bundles, compatibility bounds, stale sources/implementation and immutable replay. Python compilation and JavaScript syntax pass.

Actual UI entry/save, copy-and-edit variant, comparison save and clean reload pass. Exact source request inspection remains connected. API replay and invalid-preview rejection pass. Browser errors are zero. Original source questions remain unresolved. First scenario `b52bf9e313b95763777795124775a1c14ba5e1c495964bf75c2af3f6d67c0f94` remains unchanged; variant `0061cd0cf71165867c86196841cbd5ab92881a7df62f1f71c5a57f6c12d54d95` and comparison `df098eec1f24f38173cda07103c7a08611cc918014a34b2d85c4719c46a3f3ce` are current.

All114 preceding protected files match;115 are now protected. Original work histories, prior clinical option/context/component reviews, capacity/cost/payment scenarios, the single-question information scenario and 19 separate Documents files remain unchanged. No provider/token-count calls; uncapped ledger unchanged at812 validated/10 failed/$6.292875524 historical estimated API cost. Local compute and review labor are unpriced.

## Remaining limits

- The selected models are an explicit uncertainty set, not a confidence interval, exhaustive set or probability distribution over models.
- The same observation-to-choice rule is evaluated in every model. Averaging model-specific optimal values would not establish an executable common rule.
- Only deterministic simultaneous acquisition bundles and terminal review choices are enumerated; no randomization or sequential acquisition.
- All model assumptions remain unvalidated. Robustness within this set is not clinical validity, worst-case patient safety, benefit or care priority.
- Costs and utilities use the same supplied unit. No-acquisition baselines choose a common review action, not a different action for each model.
- A bundle must be assumed available in every selected model to be considered. Infeasible bundles remain visible without a robust policy.
- Source/model changes invalidate current comparison. No source acquisition, task mutation, clinical recommendation or external action occurs.

This does not add sequential acquisition, randomized strategies, empirical priors, clinical utilities, causal benefit, actual collection or qualified predictive models. Shared expected information value is connected for registered documentary contracts; arbitrary cross-domain task and quoted-payer question adapters remain incomplete. Existing single-question scenarios remain separate and unchanged. Broader clinical, payer, operational and portable-product acceptance remains false.

Next: Return to clinical/source interpretation and pharmacy action support. Start with the actual P-7193 medication regimen/non-use questions retained by portfolio 72a14f250440632549946c522f97e1f87ee17a911518d2571104a6e483e3ae44. Inspect received medication orders, fills, use/history/follow-up reports, narrative context and current mappings to distinguish unavailable source accounts from unmapped or unassessed evidence. Connect useful supported interpretation back to the exact questions and existing buyer/pharmacy capability, coverage and review machinery; use current primary references for any clinical content, leave patient goals/consent, titration authority, appropriateness and clinical review explicit. Do not treat this patient as the product boundary. Then continue broader clinical/payer recommendations, qualified prediction/discovery, empirical operations and portable acceptance. Preserve the three pending exact outbound payloads, original v1.23 and frozen/source-family boundaries. Do not refit unchanged single-class endpoints, invent assumptions or substitute further mathematical catalogs for product work.

Server11644, session49119, port8885. No verification jobs remain. The sole-owner five-minute continuation remains active. Checkpoint 2026-10-11T06:26:42.471739+00:00.

![Saved common-rule comparison](artifacts/agere-information-robustness-v1/browser-comparison.png)
