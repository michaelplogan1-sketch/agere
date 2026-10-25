# Claim context and dev wording — October 25, 2026

Qualification is in progress. This document does not claim deployment or 95% correctness.

Mike's claim-count decision is implemented in the shared typed request compiler: eligible insurer claims for payer/VBC contract reports, all received deduplicated medical claims for clinical utilization reports, and separately labeled counts when context is unclear. Grouping by payer alone does not choose an insurer-only report. Date, predicate, grouping and saved-cohort constraints are compiled for each source. Overlapping readings are never added. Missing sources or source-specific fields remain unknown and make the answer partial; they are not represented by zero or by the other source's count. A dual-source answer does not create one undifferentiated action cohort.

The output-shape check accepts the two counts only for a sealed count contract with the correct source labels and numeric outputs. Report words inside literal values do not become context. Rates, money and distinct-patient counts keep their own governed definitions. This increment does not establish that every free-form SQL proposer follows this policy; broader analyst acceptance remains unqualified.

Insight forms now recognize equivalent wording for outreach priorities, care gaps, and frequent ED use. These use the existing governed computations. Extra age, year, exclusion or visit-threshold restrictions that the forms do not implement remain unconsumed and are passed to other interpretation paths. Frequent-ED reporting states the living-patient denominator explicitly and handles zero visits as an undefined share.

Independent tiny-warehouse tests cover source differences, current/nonduplicate insurer eligibility, dates, filters, payer grouping, empty and nonempty saved groups, source absence, unavailable source fields, quoted report-like values, wording variants, extra constraints, and zero ED volume. Existing tests that assumed an unspecified single claim source now state their intended report context. A workflow test now checks the validated contract's unresolved requirements instead of relying on mutation of an input object; the safety assertion remains.

## Measurements

- Claim-count-only candidate: 243/295 historical free paraphrases (82.4%), unchanged from d73be19; zero recovered/lost answers, $0. This bank did not measure the new source distinction.
- Final candidate's independent source-query matrix: 48/48 (100%), $0, no provider calls. This is a narrow engineering check, not unseen clinical accuracy.
- Intermediate combined candidate: 94/100 historical dev questions, Claude off, $0. Final candidate measurements and regression comparison are pending.

All interpretation diagnosis used open development questions/paraphrases and authored synthetic fixtures. Historical score keys/results remain unchanged. No held-out or CHECK questions were opened or rerun. The last held-out result remains 30%; no new unseen-accuracy claim follows from these development checks.

Anthropic's previous actual Haiku attempt failed for insufficient credits. No new paid attempt or Sonnet escalation was made in this increment. New spend is $0. Incoming subscription-transport code is not activated in this isolated runtime.

Runtime candidate is isolated from d73be19. Main contains other unqualified analyst/planner changes, so main must not be deployed wholesale. Landing and activation require zero new failures in the regression comparison and an exact release check. Rollback is d73be19.
