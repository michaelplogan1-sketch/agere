# Dev generalization repair, October 24

Release a7f2c9b is pushed to main and the synthetic service on :8885 restarted; /health reports ready. The target of >95% remains unmet. No current held-out evaluation was started. A later inspection incident is recorded below; current held-out bank and generator remain unopened.

Repairs preserve polarity, conjunctions, ratio denominators, rolling claim dates, event-specific note reasons and safe plan-cache revision. Completed discovery fields are cached even if another field times out. The chart adapter retains negative boolean results as derived rows so the answer guard can inspect them. Explicit reported historical event counts retain the dated note scope; stated zero counts remain zero.

## Measurements

All free measurements use frozen6424730 versus19262be, cold evaluation stacks, no Claude or local model tiers.

| Set | Before | After |
|---|---:|---:|
| Original dev |83/100|84/100|
| Dev paraphrases |214/295|217/295|
| Composition dev |15/130|26/130|

The paid100-question dev run used frozen6424730 and the real conversation handler, with Haiku first and normal escalation available. Under the shipped scorer it scored87 correct,7 wrong,6 refusals:87% accuracy and92.6% precision among nonrefusals. Recorded provider usage estimate: $0.192139175;45 Haiku calls plus hosted System One calls, no Sonnet. The older frozen scorer gave86%; raw responses were rescored without new API calls. This is not a measurement of the later shipped routing/notes fixes.

| Bucket | Correct |
|---|---:|
| Structured patient |19/20|
| Notes |18/20|
| Population quality |18/20|
| Claims/pharmacy/social factors |15/20|
| Insight |17/20|

Additional diagnostics:6 Haiku calls $0.02617635; interrupted mutable-version dev run $0.043482654, not a full accuracy measurement; source-note probe $0.00023557. Usage estimates are not invoice totals. Failed calls have no reported token usage.

## Release evidence

`tools/runtests_diff.py`: combined candidate113 modules/9 failing tests versus current-main f351e7a111 modules/10 failing tests; no new failure names. Reported-count increment4 modules/0 versus2 modules/0. Existing failures were retained, not weakened. Focused reported-count/notes/boolean tests35 passed. Live readiness verified after restart.

Raw receipts, failures and responses: `/tmp/agere_generalization/`. Paid failed-dev chains are being rerun on the shipped release with a separate ledger; report them as targeted diagnostics, never full accuracy. Remaining gaps include conversational antecedents, multi-part claims calculations and broad composition coverage. Dev-key semantic disagreements remain wrong in scoring; no keys were changed. G3 remains active, G5 incomplete and full acceptance false.

## Follow-up delivery and evaluation hygiene

fd5d95b preserves a transformed summary’s exact original input membership when its engine contract omits the population field, including an explicit empty input. Conflicting contract/transform inputs are rejected. Candidate and main each passed25 regression modules with0 failing tests;9 focused summary tests passed. Pushed and local service restarted. This repairs provenance preservation; it does not establish that every conversational reference resolves correctly.

A subsequent inspection of the first100 lines of `tools/dev_oracle.py` accidentally displayed a legacy block labelled HELDOUT. The inspection stopped and the user was informed. Those displayed legacy questions must not be claimed unseen or used for tuning. No subsequent oracle inspection or tuning was performed; the current dev-set held-out question bank, reserved evaluation directory, private data and generator remain unopened. All reported measurements above preceded this exposure.

The isolated committed composition candidate c26073f scored77/130 correct,21 wrong,32 refusals against26/130,8 wrong,96 refusals before; no previously correct answer regressed. It was not landed. Its regression diff found an unconsumed historical wording regression, repaired in the newer isolated candidate. Direct source-SQL audit confirms dev c3-31’s key counts556 visit rows rather than201 distinct patients; Agere’s201 is retained as a scored failure pending independent key adjudication. No key changed.
