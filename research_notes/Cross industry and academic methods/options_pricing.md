# Options and derivatives pricing methods for value-based care, capacity investment and shared-savings risk

Research date: 2026-11-17. Scope: Black-Scholes, real options, risk-neutral valuation, hedging, reinsurance/stop-loss pricing, and their proven use for VBC contracts, capacity investment and shared-savings risk. Baselines named by the product owner: the existing Monte Carlo + VaR/CVaR contract valuation, a deterministic NPV, a corridor table, and an LLM alone.

Source-access note: eCFR (redirect to a bot check), PMC (browser check), and Taylor & Francis (403) could not be read directly. Cornell LII, RePEc/IDEAS, AJMC, CMS, Milliman, SOA and Insurance Journal pages were opened. No PDFs were downloaded; PDF-only sources found in search are listed as gaps.

## 1. Is the shared-savings / downside-risk payoff in MSSP and ACO REACH literally option-like, and what do published option valuations add over Monte Carlo?

### Takeaway
Yes. The MSSP and REACH settlement rules are piecewise-linear functions of (benchmark - actual spend): a minimum savings rate (a deductible or strike), a sharing rate (the slope), a cap (the call spread's upper strike), and for two-sided tracks a mirrored short put with its own floor. The one published "option pricing" paper (Friedberg et al., AJMC 2013) prices the payoff by **bootstrap simulation of the cost distribution, not by Black-Scholes or risk-neutral methods**. Agere's existing Monte Carlo already does this. What the literature adds is the **framing and decomposition** (how much of an expected payout is pure chance, as a function of panel size and contract terms) and a measured model-error rate. It does not add a new pricing engine.

### Cited Findings
**Payoff definition (CMS regulation, BASIC track, 42 CFR 425.605, read via Cornell LII):**
- MSR/MLR elections for two-sided tracks: (A) 0%; (B) symmetrical 0.5%–2.0% in 0.5-point steps; (C) a symmetrical variable MSR/MLR based on the number of assigned beneficiaries, with MLR = -MSR — [42 CFR 425.605, Cornell LII](https://www.law.cornell.edu/cfr/text/42/425.605)
- Final sharing rates (performance years from 2024): Levels A/B (one-sided) 40%; Levels C/D/E (two-sided) 50%; each is multiplied by the quality score under the alternative quality standard — [42 CFR 425.605](https://www.law.cornell.edu/cfr/text/42/425.605)
- Performance payment limit (the cap): shared savings are capped at 10% of the updated benchmark and paid first-dollar once the MSR is met. From 2027, ACOs with fewer than 5,000 beneficiaries may face an alternative limit — [42 CFR 425.605](https://www.law.cornell.edu/cfr/text/42/425.605)
- Downside (the short put): a fixed 30% shared-loss rate for Levels C–E. Level C losses are capped at 2% of ACO participants' Parts A/B FFS revenue, bounded by 1% of benchmark. Level D is capped at 4% of revenue, bounded by 2% of benchmark. Level E references the QPP Advanced APM nominal-risk thresholds (exact percentages not in the extracted text) — [42 CFR 425.605](https://www.law.cornell.edu/cfr/text/42/425.605)
- ENHANCED track (42 CFR 425.610): 75% savings sharing up to a performance payment limit. Its MSR references 425.605(b) — [Search excerpt of 42 CFR 425.610, Cornell LII](https://www.law.cornell.edu/cfr/text/42/425.610). A secondary CHCS table gives the ENHANCED limits as savings capped at 20% of benchmark and losses at a rate of 1 minus the final sharing rate (40–75%), capped at 15% of benchmark. Not verified against the regulation text — [CHCS comparison table (PDF, not opened)](https://www.chcs.org/media/Comparison-of-Risk-and-Reward-Under-Basic-Track-and-Enhanced-Track.pdf)
- BASIC-track size-based MSRs range from 2.0% to 12.2%, linearly interpolated by beneficiary count — [Milliman, Birkeland/Shaikh/Smith, 1 Feb 2024](https://www.milliman.com/en/insight/mssp-minimum-savings-loss-rates-msr-mlr)

**ACO REACH (CMS Innovation Center):**
- Two options: Professional (50% shared savings/losses) and Global (100%). Both are constrained by "a series of risk corridors." From PY2026, the first Global corridor narrows from 25% to 10% of benchmark, so savings and losses above 10% are shared with CMS. The quality withhold rises from 2% to 5% of benchmark — [CMS ACO REACH PY2026 Model Update Quick Reference](https://www.cms.gov/priorities/innovation/aco-reach-model-performance-year-2026-model-update-quick-reference)
- The page also mentions a CMS-run stop-loss recalculation for PY2024. The full corridor band percentages are in CMS financial-settlement PDFs, which were not opened — [same page](https://www.cms.gov/priorities/innovation/aco-reach-model-performance-year-2026-model-update-quick-reference)
- REACH and LEAD include an optional CMS-administered stop-loss arrangement (search summary of Wakely white paper, not verified) — [Wakely blog](https://www.wakely.com/blog/rethinking-downside-risk-the-role-of-stop-loss-in-aco-contracts/)

**Published option-pricing of shared savings (Friedberg, Buendia, Lauderdale, Hussey; AJMC 2013;19(8):e285–e292, Commonwealth Fund):**
- Method: bootstrap simulation, not Black-Scholes or risk-neutral. The authors drew 10,000 simulated providers per size class from 77,364 commercial members with 2009–2010 allowed costs. The "option price" is the payer's mean loss from bonuses paid by chance — [AJMC](https://www.ajmc.com/view/option-pricing-a-flexible-tool-to-disseminate-shared-savings-contracts)
- Base case (50% sharing, no real savings, no downside): $225 per patient (3.1% of cost) for 500-patient providers vs $23 (0.3%) for 60,000-patient providers. About 31% of 500-patient providers profit from chance alone — [AJMC](https://www.ajmc.com/view/option-pricing-a-flexible-tool-to-disseminate-shared-savings-contracts)
- Contract features: a 2% MSR with first-dollar sharing barely moved the small-provider price ($225 to $223) but halved the large-provider price. A 10% bonus cap took $225 to $193. Truncating outliers at the 99th percentile cut prices about 50% at every size, the most broadly effective feature. Downside risk lowered prices for providers with 10,000 or fewer patients. A 2% real cost reduction drove prices to zero for all but the smallest providers — [AJMC](https://www.ajmc.com/view/option-pricing-a-flexible-tool-to-disseminate-shared-savings-contracts)
- The authors' limitations: data from one commercial plan, one expected-cost predictor, quality not simulated — [AJMC](https://www.ajmc.com/view/option-pricing-a-flexible-tool-to-disseminate-shared-savings-contracts)

**Model error and chance outcomes in MSSP (Duncan, Mackenzie, Bonfiglio, Wrigley, Liao; North American Actuarial Journal 27(2):242–252, 2023; DOI 10.1080/10920277.2021.1993927):**
- Using Medicare FFS samples, the probability of a false-positive or false-negative settlement is about 5–8% for a typical ACO, 26–28% for diabetes-heavy populations and about 23% for cancer populations — [IDEAS/RePEc abstract](https://ideas.repec.org/a/taf/uaajxx/v27y2023i2p242-252.html)
- Payer-level average mis-payment is about 1.0–2.0% of claims (cancer) and 1.5–2.5% (diabetes). The impact on an individual ACO is much larger, since an efficient ACO can fail to realize gains or owe money — [IDEAS/RePEc](https://ideas.repec.org/a/taf/uaajxx/v27y2023i2p242-252.html)

**Empirical MSR election evidence (Milliman 2024):**
- In PY2020–2022, two-sided ACOs lost $365M of savings to the MSR, while the MLR protected them from $149M of losses. The 2.0% and size-based elections drove most of the forgone savings ($90M and $263M). 17% of two-sided ACOs chose the size-based MSR in PY2022 — [Milliman](https://www.milliman.com/en/insight/mssp-minimum-savings-loss-rates-msr-mlr)

**Other valuation work:**
- A Health Affairs Forefront piece on the Next Generation ACO reports large differences in measured value depending on the valuation methodology (search snippet only, not opened) — [Health Affairs Forefront](https://www.healthaffairs.org/content/forefront/cms-pay-performance-paradox)

### Inferences
- Payoff shape: the BASIC two-sided payoff is roughly 0.5 x [call spread on savings with strike = MSR and cap = 10% of benchmark] minus 0.3 x [put spread on losses with strike = MLR and floor = revenue/benchmark cap]. First-dollar sharing makes it a "digital-gated" call: crossing the MSR pays from the first dollar, not only above the MSR. That gate creates a discontinuity, which matters for the gradient and sensitivity reporting the product shows (the payoff "delta" near the MSR is a spike). A REACH Global corridor table is a multi-strike call/put spread ladder.
- Risk-neutral valuation does not apply. The underlying (an ACO's population spend relative to benchmark) is not traded and cannot be replicated, so there is no arbitrage-free price. The right measure is the **physical (real-world) distribution**, which is what Agere's Monte Carlo already samples. Every published health source found uses physical-measure simulation.
- What would beat the existing Monte Carlo baseline is not a pricing formula. Three cheap additions make it more useful to a CFO: (a) an exact payoff implementation of the CMS settlement rules (MSR gate, first-dollar sharing, quality multiplier, caps by level, REACH corridors, stop-loss), (b) an "expected chance payout" decomposition à la Friedberg (contract value with zero true savings), and (c) a "model-error probability" à la Duncan (P(settled sign differs from true sign)). All three are computable from the existing simulation.
- An MSR/MLR election comparator (0% vs 0.5–2.0% vs size-based) is a named CFO decision. Milliman's PY2020–22 data give a test target: higher MSRs forgo more savings than they protect.
- An LLM alone cannot reproduce these numbers reliably. It can explain the payoff, but the expected value depends on the population's cost distribution.

### Recommendations (per idea)
- **Exact CMS payoff engine (MSSP levels A–E, ENHANCED, REACH corridors) applied to the existing Monte Carlo draws:** TRIAL. Buyer: CFO / VBC contracting. Baseline: a corridor table on the point estimate. Evidence it adds value: the payoff is nonlinear and gated, so E[payoff(cost)] differs from payoff(E[cost]) (Jensen's gap); Friedberg shows a 3.1% of cost chance value at small panels. Data: attributed beneficiaries, benchmark, historical PMPM by member, track/level, MSR election, quality score. Maturity: high (rules are published). Acceptance test: reproduce a public-use-file ACO's settled shared savings from its reported benchmark, expenditure and quality score.
- **Chance-payout and model-error decomposition:** TRIAL, low cost, inside the existing Monte Carlo. Buyer: CFO, analytics. Data: member-level cost history. Maturity: peer-reviewed (AJMC 2013, NAAJ 2023).
- **MSR/MLR election advisor:** TRIAL as a scenario comparator. Must not be presented as advice for a specific ACO beyond its own data.
- **Black-Scholes / risk-neutral pricing of ACO contracts:** SKIP. There is no tradable underlying and no published health application that beats simulation.

### Gaps
- Exact REACH corridor bands and sharing percentages for Professional and Global in PY2026 (in the CMS financial settlement PDFs, not opened).
- Exact ENHANCED performance payment limit and loss limit from regulation text (eCFR blocked; the CHCS figures are secondary).
- The full variable-MSR table by beneficiary count (in the regulation and a Milliman figure; not extracted).
- No published paper found that compares risk-neutral or closed-form option pricing of ACO contracts against Monte Carlo on accuracy.

## 2. Real options in hospital capacity investment (beds, ORs, service lines, staged builds, equipment): case studies with numbers vs NPV

### Takeaway
There is a small applied literature: theses, engineering case studies and government appraisal guidance. The one quantified hospital case found is the University Hospital Zurich PET Centre. Its flexible design had an estimated lifetime net benefit of CHF 167.18M, 20% above the basic design and 2.2% above a "robust" design. The method was simulation plus decision rules, not Black-Scholes. Evidence that real options beat NPV in hospital decisions is thin and mostly self-reported by authors.

### Cited Findings
- Zurich PET Centre (Esders, Martani, Adey): the flexible building design was estimated to yield a net benefit of CHF 167.18M over the clinic's lifetime, 20% and 2.2% higher than the basic and robust designs (figures from a search summary; the case-study page returned 404 on fetch, so this is unverified) — [Strategic Engineering case study](https://strategic-engineering.co/blog/case/evaluating-initial-building-designs-considering-possible-future-changes-the-example-of-the-new-pet-centre-of-the-university-hospital-of-zurich)
- Hajizadeh, Ranjbar-Bourani, Gaza (2019), "Improving the Value of Strategic Projects with Uncertainty and Flexibility: A Central Hospital Case Study": a four-step simulation-based evaluation of flexibility applied to a central hospital design. Numeric results were not available in the abstract record — [Catalog record](https://katalog.lib.cas.cz/KNAV/EdsRecord/edsair,edsair.doi...........ca4f603dd8f05bdd86b64d2db814d93e)
- MIT S.M. thesis (Maseda): real options analysis of flexibility in an emergency department expansion, using a hypothetical Greater-Boston hospital (search summary only) — [DSpace@MIT](https://dspace.mit.edu/handle/1721.1/44704)
- Australian Transport Assessment and Planning guidance T8 includes a worked real-options example for a hospital upgrade using a decision tree. It describes real options assessment as DCF/cost-benefit methodology applied to options such as staged expansion (search summary; PDF not opened) — [ATAP T8](https://www.atap.gov.au/sites/default/files/documents/atap-t8-real-options-assessment.pdf)
- Lovejoy & Li (2002, Management Science) on hospital operating-room capacity expansion is a foundational reference (not retrieved) — gap.

### Inferences
- The practical form is "flexibility in design" (staged builds, shell space, convertible units) valued by simulating demand paths with explicit decision rules (expand if utilization exceeds X% for Y quarters). Agere's Monte Carlo infrastructure fits this better than any lattice or Black-Scholes model.
- Baseline: deterministic NPV plus scenario/sensitivity tables. Real options value = E[NPV with decision rules] - NPV(fixed plan). It is only meaningful when demand uncertainty is large and the decision is staged or reversible.
- Buyer: CFO and operations, for bed/OR/service-line capacity. Data: demand forecast with an uncertainty distribution (admissions, OR minutes, referral growth), capex by stage, contribution margin per case, lead times. Agere already has a population/volume forecast capability that could feed the demand paths. Clinical-quality effects of over- or under-capacity (boarding, diversion) are seldom priced in the cases found.

### Recommendation
- **Simulation-based staged-capacity valuation (NPV with decision rules vs fixed-plan NPV):** PARK. It answers a real CFO question, but it needs capex and margin inputs Agere does not hold in its warehouse, and the published evidence is a handful of case studies without out-of-sample validation. Revisit when a capacity-planning gate exists. Trial as a thin layer over the existing Monte Carlo if a buyer supplies capex data.
- **Black-Scholes / binomial lattice for capacity:** SKIP (see section 5).

### Gaps
- No peer-reviewed hospital case found with a back-tested comparison (the real-options decision vs the NPV decision vs what happened).
- Hajizadeh 2019 and Maseda numeric results not retrieved (full text not accessible as HTML).

## 3. Stop-loss, reinsurance and provider excess-loss pricing: standard actuarial practice for plans and risk-bearing providers

### Takeaway
Stop-loss for health plans and ACOs is a mature, standard market. It comes as specific (per-member) and aggregate layers, priced by actuaries from rate manuals and aggregate-loss models (compound frequency/severity, simulated or computed). The SOA reinsurance newsletter gives concrete ACO aggregate stop-loss terms: attachment at 105% of expected claims, exposure capped at 10% of expected claims, rate on line 1–2%. Aggregate ACO stop-loss is described as relatively new, volatile and thinly supplied.

### Cited Findings
- SOA Reinsurance News (Mark Troutman, FSA, Dec 2023): a provider excess-of-loss (PXS) policy for ACOs should include both aggregate and specific stop-loss. Typical aggregate terms: attachment 105% of expected claims, exposure capped at 10% of expected claims, rate on line 1–2% — [SOA Reinsurance News](https://www.soa.org/sections/reinsurance/reinsurance-newsletter/2023/december/rsn-2023-12-troutment/)
- Same source: illustrative premium loads of 9.0% commission, 8.5% MGU fee and 12.5% carrier load (30% total, implying a 70% target loss ratio). Carriers may require quota-share and/or excess-of-loss reinsurance. Aggregate ACO stop-loss is "a relatively new and volatile risk" with limited market capacity. Rates come from actuarial rate manuals (ESL, HMO, PXS manuals) plus underwriting and managed-care review — [SOA Reinsurance News](https://www.soa.org/sections/reinsurance/reinsurance-newsletter/2023/december/rsn-2023-12-troutment/)
- Specific stop-loss rates range from under $1 PMPM to over $200 PMPM depending on attachment point, area, case size, network and plan provisions (SOA Health Watch, Oct 2020; search summary, PDF not opened) — [SOA Health Watch 2020](https://www.soa.org/4a7e4d/globalassets/assets/library/newsletters/health-watch-newsletter/2020/october/hsn-2020-iss-10-nelson-passwater-prieto.pdf)
- Aggregate excess pricing is commonly done by Monte Carlo of a compound (e.g., Poisson-Gamma) frequency/severity model, producing expected layer loss, burn rate and rate on line (generic reinsurance method; tool documentation, search summary) — [Metricgate aggregate excess pricing](https://metricgate.com/docs/aggregate-excess-pricing/); open-source equivalent: [aggregate Python library, reinsurance pricing guide](https://aggregate.readthedocs.io/en/latest/2_user_guides/2_x_re_pricing.html)
- Wakely (2026 white paper) compares stop-loss structures for MSSP and LEAD contracts as tools to limit ACO repayment exposure. The page gives no figures — [Wakely](https://www.wakely.com/blog/rethinking-downside-risk-the-role-of-stop-loss-in-aco-contracts/)
- Market example: Hannover Re provided aggregate excess-loss cover to a Next Generation ACO (trade news, search result only) — [Reinsurance News](https://www.reinsurancene.ws/hannover-re-us-provides-aggregate-excess-loss-cover-next-generation-aco/)

### Inferences
- This is the most directly useful item for Agere. A risk-bearing provider's CFO question, "what is a fair premium for an aggregate stop-loss at 105% attachment and 10% limit on my REACH or MSSP downside, and does buying it beat self-insuring?", is a layer price on the same simulated loss distribution Agere already generates. Expected layer loss = E[min(max(L - A, 0), limit)]; add loading and compare to a quoted rate on line.
- The buyer is also the analytics team: a specific stop-loss layer (per-member truncation) is the same operation as Friedberg's 99th-percentile outlier truncation, which cut chance payouts about 50%. Agere could show how a specific stop-loss attachment changes both the contract's variance and its CVaR.
- Credibility weighting (Bühlmann) of the ACO's own experience against a manual rate is standard practice for small blocks. It is a cheap, explainable baseline-beater for small panels, where pure experience is noisy (consistent with Duncan's finding that error is larger for small or high-variance populations).
- Baselines to beat: (a) the quoted rate on line or manual rate, (b) a normal approximation of aggregate loss, (c) an LLM estimate. A layer computed from the simulated distribution should beat (b) because health costs are heavily skewed (the top 20% of patients account for about 80% of spend in Friedberg's data).

### Recommendation
- **Stop-loss / excess-layer pricing on the existing Monte Carlo (specific and aggregate; expected layer loss, rate on line, buy-vs-retain CVaR comparison):** TRIAL. Buyer: CFO / risk-bearing provider and plan. Data: member-level claims for the attributed population, contract terms, attachment and limit, and a quote if available. Maturity: high (standard actuarial practice). Acceptance: reproduce a textbook or open-library layer price on a known compound distribution, then show the CVaR reduction per premium dollar on the warehouse population.
- **Bühlmann credibility blend for small panels:** TRIAL as a component tied to the small-ACO variance failure.
- **Full rate manual construction:** SKIP. It is a consulting/carrier function and needs external manual data.

### Gaps
- No public, peer-reviewed accuracy comparison of ACO aggregate stop-loss pricing methods was found.
- Specific and aggregate PMPM ranges for ACOs in 2025–2026 were not found on an openable page.

## 4. Hedging: can a provider hedge medical-cost-trend or volume risk? Instruments and lessons

### Takeaway
No liquid instrument exists for hedging medical cost trend or provider volume. The historical exchange product (CBOT health insurance futures, 1990s) failed to gain traction. The World Bank's 2017 pandemic bonds paid out late and expensively and were not renewed. The newest instrument is a pair of Milliman ETFs launched in April 2026 that proxy medical-cost inflation using a basket of healthcare equities plus gold and Treasuries. That is a proxy hedge with basis risk and no published effectiveness data. For providers, the practical tools are contractual: stop-loss, corridors, carve-outs and trend guarantees.

### Cited Findings
- World Bank Pandemic Emergency Financing (PEF) bonds: $320M of bonds sold, plus $105M in swaps with Swiss Re and Munich Re. Coupons were 6.9% over Libor (senior tranche) and 11.5% (junior). Investors earned about $96M in interest before writedown. COVID-19 triggered a payout confirmed on April 16, 2020, more than five weeks after the WHO pandemic declaration: $132.5M from the bonds plus $63.3M from swaps (about $195.8M). The bonds did not trigger for the 2018 Ebola outbreak. The World Bank confirmed it will not issue a second round — [Insurance Journal, Dec 10, 2020](https://www.insurancejournal.com/news/international/2020/12/10/593490.htm)
- Center for Global Development (Ritchie & Plant, Apr 9, 2020): the Bank paid about $36M/year to insure $430M. Triggers required 12 weeks of outbreak growth, so disbursement took more than 14 weeks. Recorded-death triggers failed under undercounting. Basis risk means paying out too often (wasted premium) or too rarely. Parametric insurance is sound in principle, with CCRIF's fast Haiti 2010 payment cited as a better model — [CGD](https://www.cgdev.org/blog/good-idea-executed-badly-why-world-bank-should-not-renew-pandemic-emergency-facility-insurance)
- Insurance Journal also notes that pandemic losses correlate with financial-market losses, which weakens the bonds as a hedge and raises their cost — [Insurance Journal](https://www.insurancejournal.com/news/international/2020/12/10/593490.htm)
- Milliman Healthcare Inflation Guard ETF (MHIG) and Healthcare Inflation Plus ETF (MHIP), launched April 2026: they aim to track U.S. medical cost trend (Milliman Health Trend Guidelines, about 35M insured lives) using about 70 healthcare equities plus gold and Treasuries. They are marketed to retirees and Bermuda captives. No AUM or performance was given — [Royal Gazette, June 10, 2026](https://www.royalgazette.com/?p=517856)
- Lieberthal (2016), "Hedging Medical Spending Growth: An Adaptive Expectations Approach," asks whether any financial asset's returns track unexpected medical spending growth. The full text was blocked by a PMC browser check, so results are unverified — [PMC5021446](https://pmc.ncbi.nlm.nih.gov/articles/PMC5021446/)
- CBOT health insurance futures were based on an index of national health-care cost growth (legal-journal summary; trading history not confirmed) — [vLex](https://law-journals-books.vlex.com/vid/the-futures-look-bright-53356903)

### Inferences
- A hospital or plan cannot hedge its own population cost or volume in a market. Every candidate index has large basis risk relative to a specific attributed population, and Duncan's model-error results show that idiosyncratic variance dominates at ACO scale. The lesson from the PEF and CBOT is that triggers must be transparent, fast and tied to the exposure; complex parametric hedges are expensive.
- For Agere, "hedging" should mean contract-design levers (MSR/MLR elections, corridors, stop-loss attachment, carve-outs, risk-adjustment caps), evaluated on the existing simulation. Basis-risk analysis of an index hedge is a niche item.

### Recommendation
- **Market hedging instruments (index futures, ETFs, cat or pandemic bonds) for providers or plans:** SKIP for product. Possibly PARK a one-page "basis-risk check" (correlate an ACO's cost-trend residual with a public index) if a plan buyer asks.
- **Contract-lever "hedge" comparison (stop-loss vs MSR election vs corridor level) on the existing Monte Carlo:** TRIAL, merged with the items in sections 1 and 3.

### Gaps
- No evidence of hedge effectiveness for the 2026 Milliman ETFs, and no confirmed history of CBOT health futures volume or delisting (sources not openable).
- No provider-volume hedge (e.g., weather-indexed elective volume) found in use.

## 5. Do Black-Scholes assumptions fail for healthcare costs, and what does the literature recommend instead?

### Takeaway
Yes, on all three counts. The underlying (population spend, project value) is not traded, so there is no replication or risk-neutral measure. Health costs are heavily right-skewed, with persistence and mean reversion at the member level, not lognormal geometric Brownian motion. Exposure is settled annually, with no continuous hedging. Practitioners and texts recommend binomial or lattice models with early exercise, or, more generally, simulation under the real-world distribution with explicit decision rules. The health sources found (Friedberg; the Zurich and Hajizadeh hospital cases; Duncan) all use simulation.

### Cited Findings
- Real options often involve illiquid, non-tradable assets, which undermines Black-Scholes assumptions (traded underlying, continuous hedging, frictionless markets); volatility of a non-traded project is hard to estimate (search summaries of ACCA/finance teaching notes) — [Real option valuation methods: Black-Scholes inputs and limitations](https://www.pastpaperhero.com/resources/acca-afm-real-option-valuation-methods-black-scholes-inputs-and-limitations); [MIT OCW 15.431 lecture 5](https://ocw.mit.edu/courses/15-431-entrepreneurial-finance-spring-2011/087effe609c8c7c597ba8c92f86d11ec_MIT15_431S11_lec05.pdf)
- Damodaran (NYU Stern) recommends binomial models for real options because early exercise is the rule and underlying values are discontinuous (search summary of lecture slides) — [Damodaran session slides](https://pages.stern.nyu.edu/~adamodar/podcasts/valUGspr22/session24slides.pdf)
- Health-cost skew: the top 20% of patients account for about 80% of spend, and only 50% of top-quintile patients remain top-quintile the next year — [Friedberg et al., AJMC 2013](https://www.ajmc.com/view/option-pricing-a-flexible-tool-to-disseminate-shared-savings-contracts)
- Outcome error is driven by population variance and imperfect risk adjustment (23–28% error probability for chronic-heavy populations) — [Duncan et al., NAAJ 2023](https://ideas.repec.org/a/taf/uaajxx/v27y2023i2p242-252.html)

### Inferences
- A closed-form Black-Scholes value on an ACO contract would misprice it, through a lognormal tail error and a gated, capped payoff that is not a vanilla option, and would give a false appearance of precision. The existing Monte Carlo on empirical or bootstrapped member costs is the correct baseline and already the method the literature uses.
- Black-Scholes could work as a cross-check only as an "explainability" approximation: a normal or lognormal approximation of the call spread can sanity-check the simulation's order of magnitude cheaply. It should never be the reported value.

### Recommendation
- **Black-Scholes / risk-neutral closed forms:** SKIP as a valuation method. An optional closed-form approximation as a unit-test oracle for the payoff engine is acceptable but not a product feature.
- **Simulation under the physical measure with bootstrapped member costs (existing):** KEEP as the baseline. Add the exact CMS payoff and layer pricing (sections 1 and 3).

### Gaps
- No peer-reviewed paper found that directly quantifies Black-Scholes vs simulation error on healthcare cost contracts.

## 6. CMS official documentation of MSSP minimum savings rate and risk corridors as the payoff definition

### Takeaway
The authoritative payoff definition for MSSP is 42 CFR 425.605 (BASIC) and 425.610 (ENHANCED), plus the annual CMS "Shared Savings and Losses and Assignment Methodology Specifications." ACO REACH corridors are defined in CMS Innovation Center financial-settlement guides and the annual model-update quick references. Section 1 lists the extracted terms.

### Cited Findings
- BASIC track: MSR/MLR elections, sharing rates of 40%/50%, a 10% of benchmark cap, a 30% loss rate and Level C/D revenue- and benchmark-based loss limits — [42 CFR 425.605, Cornell LII](https://www.law.cornell.edu/cfr/text/42/425.605)
- ENHANCED track: 75% sharing up to a performance payment limit; MSR per 425.605(b) — [42 CFR 425.610, Cornell LII](https://www.law.cornell.edu/cfr/text/42/425.610)
- REACH PY2026: the Global first corridor narrowed from 25% to 10%; the quality withhold rose from 2% to 5% — [CMS PY2026 quick reference](https://www.cms.gov/priorities/innovation/aco-reach-model-performance-year-2026-model-update-quick-reference)
- Methodology specification and settlement guides (PDFs, not opened): [MSSP Shared Savings and Losses and Assignment Methodology Specifications](https://www.cms.gov/files/document/medicare-shared-savings-program-shared-savings-and-losses-and-assignment-methodology-specifications.pdf-4); [ACO REACH PY2025 Financial Settlement Guide](https://www.cms.gov/files/document/py25-reach-fin-settlement-guide.pdf); [ACO REACH FAQs (financial)](https://cms.gov/priorities/innovation/media/document/aco-reach-finfaqs)
- The CMS MSSP "About" page gives no rate or cap details and points to Program Guidance & Specifications — [CMS MSSP About](https://www.cms.gov/medicare/payment/fee-for-service-providers/shared-savings-program-ssp-acos/about)

### Inferences
- The payoff engine should be versioned per performance year and per track, with each parameter cited to the regulation or spec version. This fits the product's "versioned evidence" requirement. The PY2026 REACH corridor change is an example of a rule change that makes an unversioned corridor table wrong.

### Gaps
- Exact REACH Professional and Global corridor bands and percentages, and the Level E loss limit percentages, need the CMS PDFs or eCFR text; neither was accessible as HTML in this session.

---

### Summary of recommendations
| Idea | Buyer question | Baseline to beat | Recommendation |
|---|---|---|---|
| Exact CMS payoff engine (MSSP A–E, ENHANCED, REACH corridors, quality multiplier) on the existing Monte Carlo | CFO: expected settlement, P(owe), CVaR under our track and election | Corridor table on the point estimate; LLM | TRIAL |
| Chance-payout and model-error decomposition (Friedberg / Duncan) | CFO/analytics: how much of the expected bonus is luck at our panel size? | Existing MC without decomposition | TRIAL (cheap) |
| MSR/MLR election comparator | CFO: which election (0%, 0.5–2%, size-based)? | Fixed rule of thumb | TRIAL |
| Stop-loss / excess-layer pricing and buy-vs-retain CVaR | CFO: is the quoted stop-loss fair? Should we buy it? | Quoted or manual rate; normal approximation | TRIAL |
| Bühlmann credibility for small panels | Analytics: stabilize small-ACO estimates | Raw experience | TRIAL (component) |
| Staged capacity real options (simulated NPV with decision rules) | CFO/ops: build now vs staged/shell space | Deterministic NPV + scenarios | PARK |
| Market hedges (index futures, ETFs, pandemic bonds) | Plan/provider: hedge trend? | Contract levers | SKIP |
| Black-Scholes / risk-neutral closed forms | none | Monte Carlo | SKIP (test oracle at most) |
