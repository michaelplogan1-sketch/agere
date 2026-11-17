# Cross-industry methods with measured transfer to hospital and health-plan operations and finance

Research date: 2026-11-17. Source-access note: most evidence below came from search-result abstracts and snippets. Pages that were **actually opened and read** are marked [OPENED]. Pages that were tried but **failed** (bot check, CAPTCHA, empty, 404) are marked [FETCH FAILED]: the numbers for those come from search snippets of the same article and should be checked before anyone relies on them. No PDFs were downloaded. Snippets were the only route to some PDF-hosted sources (SOA, CAS).

Recommendation key for the owner's entry rule (named buyer problem, beats a simple baseline and an LLM alone on accuracy and cost): **trial** = published evidence beats a simple baseline in healthcare and the data is usually on hand; **park** = plausible, but transfer evidence is weak or the data needs are heavy; **skip** = no measured advantage, or already covered.

## Clinic no-show overbooking models: measured gains vs no overbooking or fixed overbooking

### Takeaway
Overbooking driven by each patient's predicted no-show risk has measured gains over flat (fixed-rate) overbooking. Huang and Hanauer (2014) reported at least 6% less patient waiting, 27% less overtime and 3% lower total cost. No-show models typically reach AUC 0.82–0.85, but positive predictive value is low. **Recommendation: trial** (buyer: operations/CFO; the baseline is the clinic's current flat overbooking rule).

### Cited Findings
- Individual-level overbooking (overbooking only slots where the patient is predicted to no-show) vs common flat-overbooking methods: at least 6% less patient waiting, 27% less overtime and 3% lower total cost. The no-show model's validation error rate was 13.9%. Huang & Hanauer, Appl Clin Inform 2014;5(3):836-860 — [Thieme abstract](https://www.thieme-connect.com/products/ejournals/abstract/10.4338/ACI-2014-04-RA-0026) [FETCH FAILED: bot check; numbers from search snippet]
- Rural health system (2023, BMC Health Serv Res): no-show model AUC 0.84 on training data and 0.83 on test data. The no-show rate was 6.0%. The recommended policy was 1 overbook per 6 at-risk appointments per provider per day. At a 0.4 cutoff, sensitivity was 0.71 and PPV was 0.18. — [BMC HSR 10.1186/s12913-023-09969-5](https://link.springer.com/article/10.1186/s12913-023-09969-5) [snippet only]
- Earlier models: Daggy et al. (2010) reported AUC 0.82 for a model built into a scheduling system. A stacking model in primary care reached AUC 0.85. — [same BMC HSR article](https://dx.doi.org/10.1186/s12913-023-09969-5) [snippet only]
- In probabilistic scheduling work, a small Brier-score improvement in the no-show prediction produced a large drop in schedule cost (a weighted average of patient waiting and provider overtime). — [search result, same query set](https://link.springer.com/10.1186/s12913-023-09969-5) [snippet; attribution to a specific paper not confirmed]

### Inferences
- The right baseline is flat overbooking at the clinic's mean no-show rate, not "no overbooking". The published gain over flat overbooking is modest on cost (about 3%) and larger on overtime (about 27%).
- With PPV around 0.18, a method that schedules from calibrated probabilities (the Brier score matters) should beat one that thresholds a classifier. Agere's existing credit-scorecard risk machinery plus calibration checks could supply the no-show probability, and a newsvendor- or EMSR-style rule could turn it into overbook counts. This is an airline revenue-management transfer.
- Engineering acceptance check: on held-out clinic sessions, simulated wait, overtime and idle time vs flat overbooking and vs an LLM asked to set overbooks.

### Gaps
- Full text of Huang & Hanauer could not be opened, so the setting, simulation vs prospective design and utilisation figures are unverified.
- No prospective randomized trial of predictive overbooking vs flat overbooking was found in this pass.

## Bornhuetter-Ferguson, Mack stochastic chain-ladder and credibility-weighted IBNR for health claims: accuracy vs plain chain-ladder

### Takeaway
No published, health-specific, head-to-head measurement showing BF or Mack consistently beating chain-ladder was found. The evidence and actuarial consensus say BF is more stable for immature incurral months, which in health means the most recent 1–3 months, and chain-ladder is better for mature months. ML reserving beats chain-ladder in individual studies but not consistently. **Recommendation: trial BF/Benktander as an option for the recent-months cells of the existing chain-ladder runout, scored by backtest. Park ML reserving.** Buyer: CFO/plan actuary.

### Cited Findings
- A 2004 SOA Valuation Actuary Symposium paper (health) states the author's opinion that BF accuracy depends on the accuracy of the a priori total incurred estimate, and calls BF clumsy compared with simpler methods. This is an opinion, not a measurement. — [SOA VA04 session 39](https://www.soa.org/49381c/globalassets/assets/library/proceedings/valuation-actuary-symposium-proceedings/2004/december/va04-39ts.pdf) [PDF, snippet only, not opened]
- The SOA Health Section commissioned a study of the accuracy of commonly used health IBNR methods across a range of scenarios (Health Watch, Feb 2010). Its results were not retrieved. — [SOA Health Watch 2010 iss63](https://www.soa.org/globalassets/assets/library/newsletters/health-watch-newsletter/2010/february/hsn-2010-iss63-chadick-siegel.pdf) [FETCH FAILED: 404]
- Non-health comparisons conflict. An Indonesian reinsurance study found chain-ladder more accurate than BF on MAPE. Another found BF closer to actual than Cape Cod. — [Unpatti Variance journal](https://ojs3.unpatti.ac.id/index.php/variance/article/download/14423/11832) [snippet only]
- An ML reserving article (Journal of Risk and Insurance, vol 92(2)) claims better accuracy than chain-ladder on two real portfolios. It also states that no proposed model consistently beats chain-ladder across a broad range of scenarios. — [JRI 10.1111/jori.12501 (proxy mirror in search results)](https://onlinelibrary-wiley-com-443.webvpn.zafu.edu.cn/doi/10.1111/jori.12501) [snippet only]
- An individual-claims ML method was positioned as letting the accident date alter development factors, which chain-ladder holds fixed (survival-analysis IBNR, 2023–24). — [arXiv 2312.14549](https://arxiv.org/html/2312.14549v3) [snippet only]
- Richman and Wüthrich (2026) state that no ML reserving method has become a gold standard. — [arXiv 2602.15385](https://arxiv.org/pdf/2602.15385v1) [snippet only]

### Inferences
- In health, the a priori for BF is naturally the PMPM or the pricing or budget expected cost. This also ties to the VBC/contract valuation already in the product: a BF blend lets the contract-expected PMPM govern the immature months.
- Mack stochastic chain-ladder gives a variance for the reserve. The product already has conformal intervals, so Mack adds value only if its intervals are better calibrated in backtest. Treat it as an alternative interval source, not a new feature.
- Acceptance test: rolling-origin backtest on runout triangles, comparing chain-ladder, BF and Benktander for months 0–3 by MAPE, with interval coverage for Mack vs conformal.

### Gaps
- The SOA 2010 health IBNR accuracy study results were not obtained (404 on the newsletter link).
- No health-specific quantified comparison of BF or Mack vs chain-ladder was found.

## Bühlmann credibility for small-population provider and ACO benchmarking

### Takeaway
Credibility procedures, including greatest-accuracy (Bühlmann) credibility Z = n/(n+k), are codified in Actuarial Standard of Practice No. 25. The standard originated in health and casualty practice and was broadened to all practice areas in the 2012–13 revision. It gives the governance language ("relevant experience" criteria) a CFO or actuary audience expects. **Recommendation: trial** as the default blending rule for small-panel provider, ACO or employer-group PMPM and quality benchmarks. Buyer: CFO, network/VBC contracting.

### Cited Findings
- ASOP 25 (revision exposure draft, Sept 2012) applies to all actuarial services involving credibility procedures. The original 1996 standard was limited to accident and health, group term life and P&C. It recognises limited-fluctuation (classical) credibility and greatest-accuracy (Bühlmann) credibility, which uses n/(n+k) with k derived from variance components. It also covers empirical and other Bayesian procedures. It does not mandate a method. — [Actuarial Standards Board, ASOP 25 revision page](https://www.actuarialstandardsboard.org/asops/credibility-procedures-2/) [OPENED]
- Section 3.3 of the same draft: relevant experience should resemble the subject experience in population, coverage, frequency, severity or other risk characteristics. Experience that cannot be adjusted to meet this "should not be used". The actuary should also consider overlap between subject and relevant experience. — [ASB ASOP 25 page](https://www.actuarialstandardsboard.org/asops/credibility-procedures-2/) [OPENED]
- The ASB comment responses confirm that "greatest accuracy credibility" is the primary name for the approach also called the Bühlmann approach. — [ASB comments on ASOP 25](https://actuary.org/wp-content/uploads/2025/08/asop025_174_CommentsOnly.pdf) [PDF, snippet only]
- The SOA published a 2019 paper on credibility methods for life, health and pensions that covers Bühlmann-Straub. — [SOA credibility methods](https://production.soa.org/492f2a/globalassets/assets/files/resources/tables-calcs-tools/credibility-methods-life-health-pensions.pdf) [PDF, snippet only]

### Inferences
- Bühlmann-Straub is mathematically an empirical-Bayes random-effects shrinkage estimator. It is the actuarial version of the hospital-profiling shrinkage in the next section, so one implementation can serve both the CFO (PMPM credibility) and quality (rate shrinkage) audiences with different documentation.
- The relevant-experience criteria map directly onto Agere's population/date-scope binding in the decision context: the comparison population must be recorded and justified.
- Simple baselines to beat: raw small-panel rate, and a fixed minimum-n suppression rule. Measure by out-of-time prediction error of next-period PMPM or rate.

### Gaps
- Final adopted ASOP 25 text and date were not confirmed. The page opened was the 2012 exposure draft.
- No published measured comparison of credibility-weighted vs raw ACO benchmarks was found in this pass.

## Inventory and newsvendor policies for blood products and PAR levels: measured waste reduction

### Takeaway
This has the strongest measured transfer in the set. Stanford's 3-day-ahead platelet demand model cut platelet expiration from 10.5% to 3.2% over a 29-month retrospective period while never letting end-of-day inventory drop below 10 units, and it was put into use at the Stanford blood center. An Aachen replication found LASSO and LSTM about equally good, so the simple model was enough. **Recommendation: trial** (buyer: operations, lab or transfusion service, CFO). The baseline is the current standing order or historical ordering.

### Cited Findings
- Guan et al., PNAS 2017 (DOI 10.1073/pnas.1714097114) used EMR data from Jan 2013 to Jun 2015 to forecast platelet units transfused 3 days ahead with 43 features (census, CBC, day of week, average daily transfusions). Expiration rate fell from 10.5% to 3.2% over 29 months (retrospective). Remaining end-of-day units stayed above 10. — [Medical Xpress / ASA press summary](https://medicalxpress.com/news/2018-01-statistical-future-demand-blood-platelets.pdf) and [PMC5664553](https://pmc.ncbi.nlm.nih.gov/articles/PMC5664553) [snippets only]
- The roughly $80M/year national savings figure is a press-release projection, not a measurement. — [ASA press release](https://www.amstat.org/asa/files/pdfs/pressreleases/2018-New-Statistical-Model-Forecast-Future-Demand-of-Blood-Platelets.pdf) [PDF, snippet only]
- Deployment at the Stanford blood center was reported for July 2018. — [Posit talk page](https://posit.co/resources/videos/forecasting-platelet-blood-bag-demand-to-reduce-inventory-wastage-at-the-stanford-blood-center) [snippet only]
- Schilling et al., JMIR Med Inform 2022 (RWTH Aachen): LASSO vs LSTM on the same 81 clinical features, fed into a simulated inventory against historical data. Both models had similar accuracy that was sufficient to cut waste and shortage significantly, with an estimated gain of about US$250,000/year. The authors caution that accuracy may vary by site. — [PMC8848235](https://pmc.ncbi.nlm.nih.gov/articles/PMC8848235/) [FETCH FAILED: CAPTCHA]; [JMIR](https://medinform.jmir.org/2022/2/e29978) [FETCH FAILED: empty]; numbers from search snippet

### Inferences
- The Aachen LASSO ≈ LSTM result supports the owner's rule: a regularised linear forecaster plus a newsvendor/base-stock ordering rule is the candidate. The existing Chronos-2/TimesFM forecasters should have to beat it, not the reverse.
- The same pattern (short-horizon demand forecast → order-up-to level with a shortage floor) generalises to PAR levels for high-cost supplies and pharmacy. No measured hospital PAR-level result was retrieved in this pass.
- Acceptance metric: simulated outdate % and stockout days vs historical ordering on held-out months.

### Gaps
- No RBC-specific or pharmacy/PAR-level measured waste result was retrieved.
- Both platelet studies are retrospective simulations. The Stanford deployment's prospective outcome was not found.

## Elective surgery scheduling with revenue-management-style protection levels

### Takeaway
EMSRb protection levels have been adapted to OR capacity by payer or reimbursement class, and newsvendor-type models set elective vs emergency allocation. The retrieved evidence is modelling and simulation only, with no measured operational outcomes. **Recommendation: park** (keep as a research requirement; re-enter if a buyer names an OR-block or emergency-bump problem and a site has block-utilisation data).

### Cited Findings
- Stanciu, Vargas & May (2010) modified Belobaba's EMSRb to set near-optimal protection levels for patient classes in a multi-tier reimbursement system. — [Bucknell Digital Commons](https://digitalcommons.bucknell.edu/fac_conf/63) [snippet only]
- Sung et al. (POMS 2019, 28(6):1407-1430) derived the optimal OR capacity allocated to electives as a function of the emergency arrival rate. — [RePEc](https://econpapers.repec.org/RePEc:bla:popmgt:v:28:y:2019:i:6:p:1407-1430) [snippet only]
- A discrete-event simulation comparing dedicated, flexible and hybrid emergency-capacity policies found the hybrid generally best, with results depending on the hospital. — [WSC 2010 paper](https://www.informs-sim.org/wsc10papers/217.pdf) [PDF, snippet only]
- An elective portfolio was framed as a multidimensional inverse newsvendor problem with OR and recovery-bed constraints (Operations Research). — [INFORMS OR 10.1287/opre.2019.1848](https://pubsonline.informs.org/doi/pdf/10.1287/opre.2019.1848) [snippet only]

### Inferences
- Protecting capacity by reimbursement class raises equity and access concerns that a clinician owner and quality buyers would reject as a default. A defensible framing is protecting capacity by clinical urgency class, with emergency arrivals from Agere's existing Erlang/forecast models.
- The existing Erlang queueing plus a newsvendor allocation rule probably covers most of this. Adding it would not need a new model family.

### Gaps
- No measured real-world result (utilisation, cancellations, revenue) for RM protection levels in ORs was found.

## Hierarchical Bayesian shrinkage for provider profiling vs raw rates

### Takeaway
Shrinkage (random-effects, empirical Bayes) estimators are well established over raw rates for provider classification, especially with small caseloads. He, Selck & Normand (2014) show that misclassification depends on reliability, the distribution of sample sizes and tier cutoffs, so the gain is not uniform. **Recommendation: trial**, shared with the Bühlmann credibility implementation. Buyer: quality, network management, VBC.

### Cited Findings
- He, Selck & Normand, Stat Med 2014;33(7):1081-1103 compared unadjusted hospital averages, random-effects shrinkage estimators and two posterior-probability classification methods. They derived accuracy formulas and examined how misclassification depends on data reliability, hospital sample-size distribution and tier cutoffs, using Monte Carlo and real data. The abstract gives no headline numbers. — [CDC Stacks record](https://stacks.cdc.gov/view/cdc/76303) [OPENED]
- Austin (BMC Med Res Methodol 2008) derived Bayes decision rules for using hierarchical regression models to identify high-mortality hospitals. Posterior tail probabilities are the Bayes rule under generalised 0-1 loss. The choice of loss function changes the classification of only a few borderline hospitals. — [PMC2415179](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC2415179/) [snippet only]
- Proponents argue that shrinkage lets low-volume hospitals be included and removes regression-to-the-mean bias. Whether shrinkage improves classification accuracy is described as not fully settled. — [search summary of PMC6400472 / CDC Stacks](https://pmc.ncbi.nlm.nih.gov/articles/PMC6400472) [snippet only]

### Inferences
- For the product, the decisive output is the posterior probability that a provider exceeds a threshold, not a shrunk rank. This is honest about uncertainty and maps to a "supported unknown" result.
- Baselines: raw rate with minimum-n suppression, and a funnel plot with control limits. The product already has control charts, so the funnel plot is nearly free. Measure by out-of-time stability of tier assignments and next-period prediction error.

### Gaps
- The CMS Hospital Compare / star-ratings methodology pages were not opened in this pass, so CMS's exact use of hierarchical models and shrinkage is not cited here.
- No quantitative misclassification figures were extracted. The full text was a PDF and was not downloaded.

## Other cross-industry areas (brief)

### Takeaway
- **Aviation-style checklists:** strong measured transfer. **Recommendation: skip for product**, because this is a care-process intervention, not an analytic.
- **Lean/SPC:** reviews show process improvements but with weak study designs. Control charts are already in the product.
- **Home-health vehicle routing:** the evidence is a vendor case study plus feasibility studies. **Recommendation: park.**
- **Weather-style probabilistic ED forecasting:** stacked ensembles at least match the best single model, and pinball/quantile scoring is used. Ensembles were retired from the product earlier. **Recommendation:** adopt the scoring rules (pinball loss, interval coverage) in the existing forecast checks, with no new model.
- **Sports analytics (WAR/Elo) and astronomy:** not researched in this pass (budget).

### Cited Findings
- WHO Surgical Safety Checklist (Haynes et al., NEJM 2009;360:491-9): 8 hospitals, 3,733 patients before and 3,955 after. Mortality fell from 1.5% to 0.8% and inpatient complications from 11% to 7.0%, with equal reductions in high- and low-income sites. The design was before-after and non-randomized. — [Harvard Gazette 2009](https://news.harvard.edu/gazette/story/2009/01/surgical-safety-checklist-drops-deaths-and-complications-by-more-than-one-third/) [snippet only]
- Lean in hospitals: one review (Mousavi Isfahani et al.) reports that all assessed indicators improved, most often timeliness and length of stay. "Lean interventions in healthcare: do they actually work?" found potential but inconsistent benefits, mostly in process outcomes, with a lack of control groups and confounding. — [IJHCQA via Emerald](https://emeraldinsight.com/insight/content/doi/10.1108/IJHCQA-05-2014-0058/full/html); [typeset review](https://typeset.io/pdf/lean-interventions-in-healthcare-do-they-actually-work-a-16oo10wb8u.pdf) [snippets only]
- Home-care routing: a commercial route-planner case study at an in-home primary care group claims about 25% fewer miles. This is a vendor claim, not independent. A peer-reviewed pediatric home-hospitalisation feasibility study (Barcelona) compared manual vs route-planner scheduling on planning time, distance and travel time, but its figures were not retrieved. — [vendor case study](https://www.casestudies.com/company/routesavvy/case-study/caritas-in-home-primary-care-customer-case-study); [PMC9353305](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9353305/) [snippets only]
- ED forecasting: a stacked ensemble for weekly ILI ED visits (Alberta) met or exceeded every individual model on all forecast targets. A Paris-region study made 10/50/90% quantile forecasts post-processed with Adaptive Conformal Inference. A study of hourly ED arrivals used pinball and quantile-bias scoring. — [PMC7984626](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7984626/); [HAL 04539380](https://hal-cnrs.archives-ouvertes.fr/hal-04539380v1); [Cardiff ORCA 157566](https://orca.cardiff.ac.uk/157566) [snippets only]

### Inferences
- Adaptive conformal inference on ED quantile forecasts lines up with the conformal intervals already in the product. This supports improving existing forecast checks (pinball loss, adaptive conformal) rather than adding an ensemble module.
- The checklist evidence argues for checklist-style governance in Agere's own workflow, for example a pre-release check on each analytic, not as a buyer-facing model.

### Gaps
- Sports-analytics transfers (WAR-style clinician or program value, Elo ratings) and astronomy methods (survey anomaly detection) were not searched. No evidence is recorded and they should not be scored from these notes.
- CRPS-specific ED or census studies were not found.
- No peer-reviewed quantified home-health routing savings were retrieved.
- Discrete-event simulation outcomes were not separately searched.
