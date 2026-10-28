# Agere: completed offline learning and workflow results

The two declared revised local studies have finished and their saved outputs have been independently reproduced by native execution on the approved source variants. No experimental model is installed. Results below concern examined authored development questions, not independent clinical accuracy.

Repaired typed: full rehearsal, round 1: 185/304 correct, 33 wrong, 86 declined; 185 gained and 0 lost against the immediately preceding candidate.

Repaired typed: full rehearsal, round 2: 185/304 correct, 42 wrong, 77 declined; 11 gained and 11 lost against the immediately preceding candidate.

Repaired SQL: full rehearsal, round 1: 161/304 correct, 79 wrong, 64 declined; 119 gained and 13 lost against the immediately preceding candidate.

Repaired SQL: full rehearsal, round 2: 173/304 correct, 62 wrong, 69 declined; 29 gained and 17 lost against the immediately preceding candidate.

# Agere: measured learning before and after

Claude remains off. No candidate model is installed. Full clinical workflow and clinical/model qualification remain open.

Revised chain: **completed**. Original SQL study: **stopped after format failure**.

| Model study | Round | Before: correct / cases | After: correct / cases | Wrong after | Declined after | Correct gained | Correct lost | Selected |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Original typed: sparse correction | 1 | 0/208 | 126/208 | 36 | 46 | 126 | 0 | False |
| Original typed: sparse correction | 2 | 126/208 | 88/208 | 31 | 89 | 0 | 38 | False |
| Recovered SQL: full rehearsal | Stopped: format failure | — | — | — | — | — | — | — |
| Repaired typed: full rehearsal | 1 | 0/304 | 185/304 | 33 | 86 | 185 | 0 | False |
| Repaired typed: full rehearsal | 2 | 185/304 | 185/304 | 42 | 77 | 11 | 11 | False |
| Repaired SQL: full rehearsal | 1 | 55/304 | 161/304 | 79 | 64 | 119 | 13 | False |
| Repaired SQL: full rehearsal | 2 | 161/304 | 173/304 | 62 | 69 | 29 | 17 | False |

Before means the immediately preceding candidate on identical questions and sources; the first round compares with the pinned base. A declined answer is distinct from a wrong answer. Different study banks have different denominators. Calibration losses are kept separate in the JSON report.

| Shared stack behavior | Before | After | Interpretation |
| --- | --- | --- | --- |
| Required tables in fit prompts | 5331/5680 | 5680/5680 | Schema availability; not answer accuracy |
| Required tables in calibration prompts | 84/87 | 87/87 | Schema availability; not answer accuracy |
| Required tables in verification prompts | 296/304 | 304/304 | Schema availability; not answer accuracy |
| typed: unwanted reasoning prefixes in fitting | 5680/5680 | 0/5680 | Exact served prefix now used; not model accuracy |
| bottled: unwanted reasoning prefixes in fitting | 5680/5680 | 0/5680 | Exact served prefix now used; not model accuracy |
| SQL fitting targets in served output grammar | 0/5680 | 5680/5680 | Formatting repaired; numeric/source targets unchanged |
| Wrong results passing guards, original typed round 1 | 36 | 20 | 126 correct retained; 0 correct lost; declines 46 → 62 |
| Wrong results passing guards, original typed round 2 | 31 | 15 | 88 correct retained; 0 correct lost; declines 89 → 105 |
| Historical answer-format classifier, majority baseline → lexical head | 35.6% strict-label forced-choice accuracy | 91.4% strict-label forced-choice accuracy | Weak baseline; stronger native comparison below supersedes product-benefit qualification |
| Answer-format selector, existing native rules → learned head | 304/315 native correct; 0 wrong; 11 decline | 296/315 forced-choice correct; 80 confident choices | 0 useful confident gains; original candidate quarantined; corrected qualification rejects it |
| Mean fitting prompt characters | 2929 | 3199 | 9.2% more context |

Engineering verification: 566 passed, 4 skipped, 0 failures and 0 errors. These checks are not clinical accuracy.

The large runs use the pinned Qwen3-4B model and local LoRA fitting, the existing native compiler/SQLite execution, received-schema discovery, literal/type checks, separated fitting/comparison families, full rehearsal and fresh source-variant review. The answer-format classifier is a separate completed study. No new clinical training of Jev, Perplexity, UltraBERT, rerankers or frontier models is established by these adapter runs.

The native shape comparison applies the runtime's existing count/value scalar compatibility to both selectors. Eight apparent label errors disappear; this is a grading correction, not learned improvement. Identical classifier model bytes are retained, and the old majority-only qualification is preserved as history. The new qualification requires useful confident decisions beyond native rules.

Total cost per clinically correct answer remains unknown. No stack-provider inference is used; local hardware, engineering and independent clinical-review costs were not measured.

Observed engineering domains in the completed model studies:

| Study / round | Domain | Correct before → after | Wrong after | Declined after | Correct lost |
| --- | --- | --- | --- | --- | --- |
| Original typed: sparse correction / 1 | appointments | 0 → 16 /16 | 0 | 0 | 0 |
| Original typed: sparse correction / 1 | claims | 0 → 8 /16 | 0 | 8 | 0 |
| Original typed: sparse correction / 1 | conditions | 0 → 9 /16 | 0 | 7 | 0 |
| Original typed: sparse correction / 1 | measurements | 0 → 8 /16 | 0 | 8 | 0 |
| Original typed: sparse correction / 1 | medications | 0 → 16 /16 | 0 | 0 | 0 |
| Original typed: sparse correction / 1 | outcomes_value | 0 → 16 /16 | 0 | 0 | 0 |
| Original typed: sparse correction / 1 | patients | 0 → 8 /16 | 8 | 0 | 0 |
| Original typed: sparse correction / 1 | pharmacy | 0 → 16 /16 | 0 | 0 | 0 |
| Original typed: sparse correction / 1 | providers | 0 → 16 /16 | 0 | 0 | 0 |
| Original typed: sparse correction / 1 | services | 0 → 3 /16 | 8 | 5 | 0 |
| Original typed: sparse correction / 1 | social | 0 → 10 /16 | 2 | 4 | 0 |
| Original typed: sparse correction / 1 | utilization | 0 → 0 /16 | 16 | 0 | 0 |
| Original typed: sparse correction / 1 | vbc | 0 → 0 /16 | 2 | 14 | 0 |
| Original typed: sparse correction / 2 | appointments | 16 → 16 /16 | 0 | 0 | 0 |
| Original typed: sparse correction / 2 | claims | 8 → 1 /16 | 0 | 15 | 7 |
| Original typed: sparse correction / 2 | conditions | 9 → 8 /16 | 0 | 8 | 1 |
| Original typed: sparse correction / 2 | measurements | 8 → 8 /16 | 0 | 8 | 0 |
| Original typed: sparse correction / 2 | medications | 16 → 8 /16 | 0 | 8 | 8 |
| Original typed: sparse correction / 2 | outcomes_value | 16 → 15 /16 | 0 | 1 | 1 |
| Original typed: sparse correction / 2 | patients | 8 → 8 /16 | 8 | 0 | 0 |
| Original typed: sparse correction / 2 | pharmacy | 16 → 0 /16 | 0 | 16 | 16 |
| Original typed: sparse correction / 2 | providers | 16 → 16 /16 | 0 | 0 | 0 |
| Original typed: sparse correction / 2 | services | 3 → 0 /16 | 1 | 15 | 3 |
| Original typed: sparse correction / 2 | social | 10 → 8 /16 | 4 | 4 | 2 |
| Original typed: sparse correction / 2 | utilization | 0 → 0 /16 | 16 | 0 | 0 |
| Original typed: sparse correction / 2 | vbc | 0 → 0 /16 | 2 | 14 | 0 |
| Repaired typed: full rehearsal / 1 | appointments | 0 → 16 /16 | 0 | 0 | 0 |
| Repaired typed: full rehearsal / 1 | claims | 0 → 8 /16 | 2 | 6 | 0 |
| Repaired typed: full rehearsal / 1 | conditions | 0 → 8 /16 | 0 | 8 | 0 |
| Repaired typed: full rehearsal / 1 | evidence | 0 → 52 /96 | 14 | 30 | 0 |
| Repaired typed: full rehearsal / 1 | measurements | 0 → 8 /16 | 0 | 8 | 0 |
| Repaired typed: full rehearsal / 1 | medications | 0 → 16 /16 | 0 | 0 | 0 |
| Repaired typed: full rehearsal / 1 | outcomes_value | 0 → 16 /16 | 0 | 0 | 0 |
| Repaired typed: full rehearsal / 1 | patients | 0 → 8 /16 | 7 | 1 | 0 |
| Repaired typed: full rehearsal / 1 | pharmacy | 0 → 16 /16 | 0 | 0 | 0 |
| Repaired typed: full rehearsal / 1 | providers | 0 → 16 /16 | 0 | 0 | 0 |
| Repaired typed: full rehearsal / 1 | services | 0 → 5 /16 | 5 | 6 | 0 |
| Repaired typed: full rehearsal / 1 | social | 0 → 16 /16 | 0 | 0 | 0 |
| Repaired typed: full rehearsal / 1 | utilization | 0 → 0 /16 | 0 | 16 | 0 |
| Repaired typed: full rehearsal / 1 | vbc | 0 → 0 /16 | 5 | 11 | 0 |
| Repaired typed: full rehearsal / 2 | appointments | 16 → 16 /16 | 0 | 0 | 0 |
| Repaired typed: full rehearsal / 2 | claims | 8 → 8 /16 | 8 | 0 | 0 |
| Repaired typed: full rehearsal / 2 | conditions | 8 → 7 /16 | 0 | 9 | 1 |
| Repaired typed: full rehearsal / 2 | evidence | 52 → 48 /96 | 18 | 30 | 10 |
| Repaired typed: full rehearsal / 2 | measurements | 8 → 8 /16 | 0 | 8 | 0 |
| Repaired typed: full rehearsal / 2 | medications | 16 → 16 /16 | 0 | 0 | 0 |
| Repaired typed: full rehearsal / 2 | outcomes_value | 16 → 16 /16 | 0 | 0 | 0 |
| Repaired typed: full rehearsal / 2 | patients | 8 → 8 /16 | 8 | 0 | 0 |
| Repaired typed: full rehearsal / 2 | pharmacy | 16 → 16 /16 | 0 | 0 | 0 |
| Repaired typed: full rehearsal / 2 | providers | 16 → 16 /16 | 0 | 0 | 0 |
| Repaired typed: full rehearsal / 2 | services | 5 → 10 /16 | 3 | 3 | 0 |
| Repaired typed: full rehearsal / 2 | social | 16 → 16 /16 | 0 | 0 | 0 |
| Repaired typed: full rehearsal / 2 | utilization | 0 → 0 /16 | 0 | 16 | 0 |
| Repaired typed: full rehearsal / 2 | vbc | 0 → 0 /16 | 5 | 11 | 0 |
| Repaired SQL: full rehearsal / 1 | appointments | 1 → 16 /16 | 0 | 0 | 0 |
| Repaired SQL: full rehearsal / 1 | claims | 2 → 0 /16 | 16 | 0 | 2 |
| Repaired SQL: full rehearsal / 1 | conditions | 0 → 13 /16 | 3 | 0 | 0 |
| Repaired SQL: full rehearsal / 1 | evidence | 32 → 34 /96 | 24 | 38 | 11 |
| Repaired SQL: full rehearsal / 1 | measurements | 0 → 16 /16 | 0 | 0 | 0 |
| Repaired SQL: full rehearsal / 1 | medications | 8 → 16 /16 | 0 | 0 | 0 |
| Repaired SQL: full rehearsal / 1 | outcomes_value | 0 → 15 /16 | 0 | 1 | 0 |
| Repaired SQL: full rehearsal / 1 | patients | 8 → 9 /16 | 7 | 0 | 0 |
| Repaired SQL: full rehearsal / 1 | pharmacy | 0 → 8 /16 | 8 | 0 | 0 |
| Repaired SQL: full rehearsal / 1 | providers | 0 → 8 /16 | 8 | 0 | 0 |
| Repaired SQL: full rehearsal / 1 | services | 0 → 3 /16 | 13 | 0 | 0 |
| Repaired SQL: full rehearsal / 1 | social | 4 → 14 /16 | 0 | 2 | 0 |
| Repaired SQL: full rehearsal / 1 | utilization | 0 → 1 /16 | 0 | 15 | 0 |
| Repaired SQL: full rehearsal / 1 | vbc | 0 → 8 /16 | 0 | 8 | 0 |
| Repaired SQL: full rehearsal / 2 | appointments | 16 → 16 /16 | 0 | 0 | 0 |
| Repaired SQL: full rehearsal / 2 | claims | 0 → 0 /16 | 15 | 1 | 0 |
| Repaired SQL: full rehearsal / 2 | conditions | 13 → 12 /16 | 4 | 0 | 3 |
| Repaired SQL: full rehearsal / 2 | evidence | 34 → 44 /96 | 20 | 32 | 7 |
| Repaired SQL: full rehearsal / 2 | measurements | 16 → 16 /16 | 0 | 0 | 0 |
| Repaired SQL: full rehearsal / 2 | medications | 16 → 16 /16 | 0 | 0 | 0 |
| Repaired SQL: full rehearsal / 2 | outcomes_value | 15 → 15 /16 | 0 | 1 | 0 |
| Repaired SQL: full rehearsal / 2 | patients | 9 → 8 /16 | 7 | 1 | 1 |
| Repaired SQL: full rehearsal / 2 | pharmacy | 8 → 10 /16 | 6 | 0 | 0 |
| Repaired SQL: full rehearsal / 2 | providers | 8 → 16 /16 | 0 | 0 | 0 |
| Repaired SQL: full rehearsal / 2 | services | 3 → 3 /16 | 10 | 3 | 0 |
| Repaired SQL: full rehearsal / 2 | social | 14 → 8 /16 | 0 | 8 | 6 |
| Repaired SQL: full rehearsal / 2 | utilization | 1 → 1 /16 | 0 | 15 | 0 |
| Repaired SQL: full rehearsal / 2 | vbc | 8 → 8 /16 | 0 | 8 | 0 |

All figures come from explicit reviewed receipts. This is an adaptive engineering report; it makes no clinical or competitor-superiority claim.

The original SQL attempt was stopped after repeated punctuation exposed a training/decoding format defect. Its weights, complete base comparison and partial candidate outputs are preserved. No candidate accuracy is assigned to that incomplete comparison. Revised typed/SQL jobs start from the pinned base under the repaired serialization contract.

Shared workflow source is verified by 992 unique offline checks: 990 passed and two optional live skips. The count combines an unchanged-source broad run with two corrected native-fixture reruns; failed receipts and the legacy-routing boundary correction remain preserved. The identical 16 medication/scope/survival regression checks move from 22 assertion/subtest failures and two exceptions to 16 passes. Known-defect checks and controls do not establish general clinical accuracy.

| Shared behavior | Before | After |
| --- | --- | --- |
| Two 5 mg tablets twice daily | 10 mg/day | 20 mg/day, original instruction and arithmetic retained |
| Half a 5 mg tablet twice daily | 10 mg/day | 5 mg/day |
| Missing supply duration | Asserted one-day supply and eight-day gap in the reproduced case | Duration/end/gap unknown; pickups retained |
| Empty and nonempty child selections | Nonempty shared follow-up cohort | Separate child populations |
| Bounded laboratory threshold | Bound used as an exact value | Interval meaning; unresolved units and subset-only estimates explicit |
| Constraint-sensitive allocation lift | Compared with an infeasible policy | Comparable feasibility checked; misleading lift withheld |

Components actually used in the local studies: pinned Qwen3-4B LoRA, shared schema discovery and output serialization, native compilers/SQLite, source variants, fit-only correction/rehearsal and native review. The separate classifier and native-rule comparison are reported above. These studies do not train or qualify Jev, Perplexity, UltraBERT, rerankers or frontier models.

Stack-provider inference cost is zero for these offline studies. Hardware, engineering and independent clinical review were not priced, so total cost per clinically right answer is unknown.

The final workflow source changes adapter contracts. Frozen-study models retain their original contracts and cannot silently activate under the new source. Any selected experimental candidate still requires explicit final-source qualification. No weights, defaults or live runtime are promoted.

Next is the manual subscription-only ordinary full-note/dose/answer/deliverable run, followed by browser and physician acceptance and full-path cost qualification. Stop on any subscription limit. G3, all ten acceptance areas and company comparisons remain open. No clinical run or provider poll was started.

Additional received findings45-64 are repaired in the existing calibrator, shared arithmetic guard and mathematical engines. Repeated wordings cannot manufacture independent sample size; unresolved references do not become correct negative labels. An explicit count cannot satisfy a requested percentage/average/median and feeds the existing repair path. Exact probability ranks and raw scores are retained through recalibration; future trajectories include their process noise. Quantile simulations preserve probability levels and explicitly disclose linear assumptions outside supplied tails.

Authored controls: the nominal80% Uniform(0,100) interval changes from[18.03,81.97] to[10.04,89.96]; the close-score calibration boundary changes from.009804 to the independently expected.990196; future latent SD3.09114 becomes4.39376 after adding the declared9.75 process-variance units. Exact rank caching uses2 isotonic fits rather than128 for64 equivalent-rank test scores, with identical results. These are mathematical/engineering results, not measured patient accuracy or product savings.

Final-source replay of both completed typed students changes none of their saved outcomes: zero gains and zero losses. All391 approved targets remain acceptable on each ofthree explicit synthetic sources. Repeated questions and source variants are not independent clinical observations. One earlier legacy corpus-read attempt was blocked before content; that failed receipt and the final exclusion are retained. Risks beyond observed follow-up now remain unknown, including treatment-line persistence; earlier curves and descriptive lines remain available. The partially linear residual projection is labelled separately from the AIPW population effect. Authored one-year zero-risk/zero-width intervals become unknown when observation ends at day3. The +.205882 projection remains numerically unchanged and distinct from the -.5 oracle population ATE. A current-period point10 cannot coexist with120 received units: points/samples/coherent totals now respect received support; conditional coverage remains unqualified. The authored census proxy changes1 to101beds while new admissions remain one; the empirical proxy is not a staffed-bed measurement. R8 adaptive acquisition and R9 alternative simulation coupling remain unqualified opportunities, with no deployment or savings claim. Historical ACI feedback and known source arrival dates now stop at the requested cutoff. Endpoint values, last-observed-window calibration and within-window path events remain distinct. Explicit path-event requests keep an explained unknown; no endpoint substitution or first-passage doubling rule is introduced. A corresponding path-event model/capture definition remains a product gap. Graph sharing now preserves parameter-referenced evidence identities and input lineage; transformed graphs are fully revalidated and fall back to the original graph if validation fails. The authored missing evidence_b KeyError becomes an exact preserved reference projection, including explicit missing-patient context. Plain graph sharing remains supported. No arbitrary data strings are rewritten and no realized cost saving is asserted. Control limits now validate their actual rounded threshold and distinguish requested ARL, grid proxy, finite restart estimate, censoring and any-alarm probability. The authored h2.771 falsely reported100 cases per alarm though exact spacing is78; the supported boundary h2.801 reports simulated179.4, exact171, with uncertainty and no clinical calibration claim. Detector selection requires joint clean-null/gap/batch/runout/early-alarm bounds, observed comparable sensitivity and simultaneous miss-adjusted loss evidence; unsafe or unsupported fallback methods remain unqualified. A separate predeclared fresh synthetic check (seed20261028,150 series/scenario) ran eight detector variants on900 generated series. None qualified. On these identical new metrics the old selector would label naive Page-Hinkley qualified despite14.67% clean-null,82% gap and90.67% runout alarms under a10% budget. This negative result is retained: selector integrity improved, detector robustness did not become clinically qualified. No metric-driven retry, tuning, saved-verdict mutation or model/default activation occurred. The simulation uses the existing generator including its normal approximation for larger Poisson means; it is not clinical performance. Runtime/cost improvement is not inferred from the approximately11-second local check. Semantic-cache cutoff proposals now bind the error target, scorer, policy, loss, denominator, method and evidence identity; null cuts remain unresolved. The same authored tighter-target, null-cut and all-failed-feedback admissions change from reused to normal-planner fallback; fitting preserves unresolved entries instead of erasing them. Adaptive reuse keeps ordinary review and cannot inherit an unrelated global certificate. Qualified unchanged global reuse and explicit same-policy proposals remain supported. Policy-bound feedback cannot loosen a different encoder or risk target, and SQL literal whitespace remains significant in reference-plan agreement. No semantic error-rate or clinical-cost improvement is inferred. Acquisition advice now uses matched source/question/episode/attempt outcomes and conditional residual delivery rather than multiplying marginal rates. The identical authored same/disjoint/independent route pairs report80%,100%,96%; unpaired evidence remains unknown. The complementary-route replay covers100% at assumed$.20 per original question while retaining$100 of actually recorded failed/successful attempt cost. These are planning denominators, not realized savings or clinical accuracy. Duplicate IDs, retries, missing costs/outcomes and incompatible prior histories remain explicit. The old implicit95% stopping instruction is removed; this report does not activate a routing controller. VLAD now uses finite independent-Bernoulli count intervals and tails at each prefix, conditional on known risks at a fixed look; standardized z remains diagnostic. The rare30-case.001-risk nominal99.8% band changes from2.9569% outside probability to a conservative0.042696%; count interval[0,1] retains one event without a signal. Two-event tails remain diagnostic signals. Dense and heterogeneous controls pass. Fitted-risk, dependence, repeated-look, clinical and large-workload cost qualification remain open. Full-support recursion has quadratic time with no patient cap; floating-point residuals are reported. Calibration diagnostics now evaluate requested members while retaining declared global cost-risk/outreach reference models and intervals. Identical authored fixed predictions formerly gave92.5%/400 for every scope; now global92.5%/400, affected0%/30, unaffected100%/370 and empty unknown/0. Missing/nonfinite cases, reference versus evaluation populations, membership identity and source/date/target bindings survive mixed reports, final answers and saved-context reopen. Forecasts retain their existing requested-series reference scope. This is corrected evaluation, not changed predictions or new conditional-coverage/clinical qualification. Exact source-scoped outcome copies now retain received endpoint witnesses, per-copy clocks and evidence IDs instead of relying on an arbitrary hash representative. The identical authored undated duplicate no longer erases a received positive or its timing eligibility. Unknown-only records remain unresolved, and receipt-time decisions retain unknown index arrivals and exclude later copies of predecision received endpoints. Storage/export controls keep clinical labels null and grant no training authority. Contract CVaR now integrates precisely the worst requested probability mass, including fractional boundary scenarios. The same rare-loss example changes1 to20 dollars; the same two-sided contract keeps expected net4 but changes tail loss2 to20 and risk-adjusted value3 to-6, reversing its ranking against the unchanged one-sided value2. Payouts, means and VaR-based capacity stay unchanged. Exact rational integration and independent breakpoint minimization agree; these fictional arithmetic checks do not establish clinical learning, actual tail adequacy or commercial gains. The older System One student now retains complete normalized note text, dates, IDs, cutoff and option meanings under a code-bound input contract. Three authored input collisions are removed; source storage retains6423 rather than1500characters and two cutoff observations rather than one overwritten row. Paired/repeated machine observations of one complete decision state form one cross-fitting unit, not four independent cases; unresolved conflicts stay out of fitting and explicit supplied references can resolve teacher disagreement. Truncated legacy rows and old model manifests cannot masquerade as the new input contract; ordinary Jev/Perplexity fallback stays available. No live model accuracy, new pretrained training, clinical gain or new default is claimed. Reranker model/route settings remain unchanged pending downstream answer comparisons. Finding65 is repaired in the existing denial operation: labels and current header versions must be available at the stated fit origin; later labels can evaluate earlier submissions. On the same authored98claims, train66 becomes6, evaluation30 stays30, and an unsupported validated verdict becomes insufficient. Historical model targets become6known/60unknown, with no future CARC labels admitted. Current-header, coverage and duplicate provenance remains unqualified for pre-submission prediction; descriptive refits remain partial review evidence. No live accuracy, actual metric inflation or clinical/cost gain is inferred. See INTERPRETATION_QUALIFICATION_2026-10-27.md for scope, assumptions, before/after evidence and unqualified tails.
