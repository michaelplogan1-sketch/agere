# Claim context and dev wording — October 25, 2026

Main implementation1868129 is pushed. Isolated runtime6fb43d4 is live and verified on port8885. Overall95% correctness remains unmet.

Mike's claim-count decision is implemented in the shared typed request compiler: eligible insurer claims for payer/VBC contract reports, all received deduplicated medical claims for clinical utilization reports, and separately labeled counts when context is unclear. Grouping by payer alone does not choose an insurer-only report. Date, predicate, grouping and saved-cohort constraints are compiled for each source. Overlapping readings are never added. Missing sources or source-specific fields remain unknown and make the answer partial; they are not represented by zero or by the other source's count. A dual-source answer does not create one undifferentiated action cohort.

The output-shape check accepts the two counts only for a sealed count contract with the correct source labels and numeric outputs. Report words inside literal values do not become context. Rates, money and distinct-patient counts keep their own governed definitions. This increment does not establish that every free-form SQL proposer follows this policy; broader analyst acceptance remains unqualified.

Insight forms now recognize equivalent wording for outreach priorities, care gaps, and frequent ED use. These use the existing governed computations. Extra age, year, exclusion or visit-threshold restrictions that the forms do not implement remain unconsumed and are passed to other interpretation paths. Frequent-ED reporting states the living-patient denominator explicitly and handles zero visits as an undefined share.

Independent tiny-warehouse tests cover source differences, current/nonduplicate insurer eligibility, dates, filters, payer grouping, empty and nonempty saved groups, source absence, unavailable source fields, quoted report-like values, wording variants, extra constraints, and zero ED volume. Existing tests that assumed an unspecified single claim source now state their intended report context. A workflow test now checks the validated contract's unresolved requirements instead of relying on mutation of an input object; the safety assertion remains.

## Measurements

- Claim-count-only candidate: 243/295 historical free paraphrases (82.4%), unchanged from d73be19; zero recovered/lost answers, $0. This bank did not measure the new source distinction.
- Final candidate's independent source-query matrix: 48/48 (100%), $0, no provider calls. This is a narrow engineering check, not unseen clinical accuracy.
- Final frozen dev:94/100 (94%),6wrong,$0; matched d73be19's free94/100 baseline. No dev accuracy gain is claimed.
- Final serial frozen paraphrases:248/295 (84.1%),13wrong,34refused,$0; five additional scored-correct answers, zero lost. Four additional answered questions still fail the historical rubric: three use Mike's approved living-ED definition and one uses differing governed screening definitions. Historical keys remain unchanged.
- An earlier parallel run scored247/295, losing one admission-note passage answer. That case passed alone in15.8seconds and in the final serial bank in28.3seconds versus31.2seconds in the failed run. Preserve the247result; note-answer timing remains a limitation. No isolated result was spliced into a full-bank score.
-92focused tests pass. Candidate95modules and merged-main97modules retain the same three baseline failures, with zero new failures. The baseline failures are the native typed-worker crash and two temporal-composition tests.
- Normal live interface:4/4 checks pass, zero new provider calls, $0. Ambiguous urgent-care2029 returns645all-received and637eligible-insurer; explicit contexts return the appropriate count; frequent ED reporting retains the living denominator.

All interpretation diagnosis used open development questions/paraphrases and authored synthetic fixtures. Historical score keys/results remain unchanged. No held-out or CHECK questions were opened or rerun. The last held-out result remains 30%; no new unseen-accuracy claim follows from these development checks.

Anthropic's previous actual Haiku attempt failed for insufficient credits. No new paid attempt or Sonnet escalation was made in this increment. New spend is $0. Incoming subscription-transport code is not activated in this isolated runtime.

Runtime candidate is isolated from d73be19. Main contains other unqualified analyst/planner changes, so main must not be deployed wholesale. Landing and activation followed zero-new-failure comparisons and the exact release checks above. Rollback is d73be19. Receipt: `artifacts/agere-claim-context-6fb43d4/implementation-receipt.json`.
