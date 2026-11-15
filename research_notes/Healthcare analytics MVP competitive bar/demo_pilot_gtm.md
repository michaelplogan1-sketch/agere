# Winning MVP demo, pilot and go-to-market for a healthcare analytics / AI startup (US, 2025-2026)

Research date: 2026-11-15. Public web sources only. Source quality is flagged inline: primary survey/regulator data (Menlo, Bessemer, CMS, AHRQ) is stronger than vendor blogs and GTM-consultancy content, which is used only where nothing better was found.

## 1. How paid pilots / proofs of concept are structured, judged and converted

### Takeaway
The winning pilot is designed backwards from the contract: 30-120 days, one pre-agreed outcome metric with a measured baseline, a named economic buyer, a written conversion clause (price, term, start date), and outputs that clear each procurement gate (security review, governance committee, usage data). Pilots are now abundant but production conversion is low (single-digit to low-teens percent of organisations), so "pilot purgatory" is the main risk, not getting a pilot.

### Cited Findings
**Market scale of piloting vs. production**
- Bessemer 2026 ROI Scorecard: share of organisations piloting AI rose 2025->2026 for providers 21%->28%, payers 19%->24%; share in full production barely moved, providers 8%->12%, payers flat at 6% — [Bessemer, 2026 Healthcare AI ROI Scorecard](https://www.bvp.com/atlas/the-2026-healthcare-ai-roi-scorecard)
- Same source: clinical AI reaches proof of concept at the same rate as administrative AI but converts to full deployment at about half the rate; largest shares moving into production are provider front office (14%), provider RCM (10%), payer member services (10%) — [Bessemer](https://www.bvp.com/atlas/the-2026-healthcare-ai-roi-scorecard)
- Bessemer describes 2025 as "the year of the PoC and pilot" with 2026 pilots graduating to scale; 35% of next-year AI spend goes to scaling proven use cases and 20% to workflow integration — [Bessemer](https://www.bvp.com/atlas/the-2026-healthcare-ai-roi-scorecard)
- Bessemer's 2025 predictions named "pilotitis" (repeated piloting without adoption) and predicted far more selective procurement — [Bessemer 2025 predictions](https://www.bvp.com/atlas/2025-healthcare-and-life-sciences-predictions)
- Chartis (Nov 2025) warns many health systems are at risk of "pilot purgatory" without significant value capture — [Chartis](https://www.chartis.com/insights/shifting-ai-adoption-curve-six-best-practices-health-systems-move-pilot-transformation)
- The often-quoted 60-70% pilot-to-customer conversion comes from a 2017 Rock Health survey of 85 startup founders, self-reported; treat it as an upper bound — cited in [The Growth Syndicate, Selling to hospitals](https://www.thegrowthsyndicate.com/resources/selling-to-hospitals)
- 73% of digital health contracts lasted two years or less (Peterson Health Technology Institute, 2025) — cited in [The Growth Syndicate](https://www.thegrowthsyndicate.com/resources/selling-to-hospitals)

**Sales cycle length**
- Menlo Ventures 2025 (700+ exec survey): health-system buying cycle 8.0 months for traditional IT vs 6.6 months for AI; outpatient providers 6.0->4.7 months; payers lengthened 9.4->11.3 months (payer caution partly from fear that provider AI raises claims volume) — [Menlo Ventures, 2025 State of AI in Healthcare](https://menlovc.com/perspective/2025-the-state-of-ai-in-healthcare/)
- The commonly quoted "12-18 months" has no primary source; interview-reported cycles ranged from six months to five years — [The Growth Syndicate](https://www.thegrowthsyndicate.com/resources/selling-to-hospitals)

**Pilot length and design**
- Healthtech GTM guidance: 30, 60 or 90 days with a hard end date; weekly data review; one midpoint decision call (e.g. week 3: "will this proceed per the agreement if it finishes at this level?"); defined exit terms on a miss — [LeadsAce, Why healthtech pilots don't convert](https://leadsace.io/why-pilots-dont-convert) (consultancy, no conversion data)
- Conversion depends on three things fixed before start: a pre-agreed success metric (not "see how it goes"), named roles including the economic buyer, and a conversion clause stating pricing, term and start date on success; charge a fee even if nominal, because paid pilots get implemented and discussed at budget level — [LeadsAce](https://leadsace.io/why-pilots-dont-convert)
- A vendor-side source suggests 90-180 days with committed customer resources and a predefined conversion path; template fields: objective, sponsor, data sources, clinician champion, timeline, success metrics, budget, conversion trigger — [Lyren](https://lyren.ai/blog/ai-innovation-delivery-business-development-leader-healthcare) (vendor blog)
- Practitioner advice: a stopping rule is "a date, a threshold and a named decision maker, agreed before the pilot begins"; metrics = one outcome metric with a pre-pilot baseline, two adoption metrics, one quality metric (e.g. edit rate), one experience question — [Clunic, AI agent pilot metrics](https://clunic.com/blog/ai-agent-pilot-metrics) (vendor blog)
- Vendor checklist timing: first workflow live in 4-8 weeks, measurable operational metrics 30-60 days after go-live; contracts should include an off-ramp if criteria are missed — [Linear Health, How to evaluate healthcare AI vendors (2026)](https://linear.health/blog/evaluate-healthcare-ai-vendors) (vendor blog)
- Common failure: vendors report AUC/sensitivity while leadership funds length of stay, readmissions, clinician time — [Zach Evans, From Pilot to Profit](https://zachevans.substack.com/p/from-pilot-to-profit-scaling-successful)
- Hospital buying gates: (1) champion, (2) budget holder, (3) IT and security (CIO/CISO/third-party risk/privacy/legal: BAA, SOC 2 Type II or HITRUST, security questionnaire, architecture and data-flow diagram, pen-test summary, integration route, incident/breach terms), (4) value analysis or IT/AI governance, (5) contracting/GPO, (6) adoption. Pilot should generate evidence for every gate; write conversion price, scale pricing and contract vehicle into the pilot agreement; schedule the readout before budget close; a prospect who won't name a signer or budget line is likely to stall — [The Growth Syndicate](https://www.thegrowthsyndicate.com/resources/selling-to-hospitals)
- 72% of organisations have AI governance committees running purchase/deployment decisions, increasingly centralised — [Bessemer](https://www.bvp.com/atlas/the-2026-healthcare-ai-roi-scorecard)
- Stanford Health Care evaluates models with its internal FURM (fair, useful, reliable) assessment, covering ethical review, workflow simulation, financial projections and deployment feasibility — cited in [Zach Evans](https://zachevans.substack.com/p/from-pilot-to-profit-scaling-successful)
- Large-buyer pattern (a16z, 2023): payers like BCBS Massachusetts test generative AI internally before exposing it to members/providers; clear ROI and KPIs prevent "death by pilot" — [a16z, Commercializing AI in Healthcare: The Enterprise Buyer Perspective](https://a16z.com/commercializing-ai-in-healthcare-the-enterprise-buyer-perspective/)

**Data access arrangements**
- A pilot needs a BAA when a covered entity routes PHI to the vendor, even for a pilot — [terms.law, Minimum legal scope for a pilot](https://www.terms.law/minimum-legal-scope-pilot/)
- Buyers' security packs commonly ask for SOC 2 Type II, BAA template with subprocessor flow-downs, pen-test summary, data-flow diagram, and an explicit statement that customer PHI is not used to train models — [StartupMD vendor-assessment checklist](https://blog.thestartupmd.com/blog/what-is-a-health-it-vendor-assessment)
- a16z: explain why each data element is needed and what performance gain it buys; more data requested = more friction; lack of candour on data use lost one company the deal — [a16z](https://a16z.com/commercializing-ai-in-healthcare-the-enterprise-buyer-perspective/)
- Staged precedent: a health authority prototypes on synthetic data to surface data-handling issues before real data, explicitly to build stakeholder trust — [ICO / G7 DPAs PET case study](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/data-sharing/privacy-enhancing-technologies/case-studies/g7-dpas-emerging-technologies-working-group-use-case-study-on-privacy-enhancing-technologies)

**Pilot-to-rollout case examples (ambient documentation, the best-documented category)**
- Cleveland Clinic: head-to-head pilot of five ambient scribes, 300+ clinicians over ~6 months, each product tested ~3 months; chose Ambience; 4,000 of 6,000 eligible clinicians actively using within 15 weeks, use voluntary; ~2 min saved per appointment, 14 min/day — [Fierce Healthcare](https://www.fiercehealthcare.com/ai-and-machine-learning/cleveland-clinic-chooses-ambience-ai-scribe). Another outlet describes it as a yearlong 2024 pilot with ~250 physicians — [Becker's](https://www.beckershospitalreview.com/healthcare-information-technology/ai/how-cleveland-clinic-rolled-out-ambient-ai-6-notes) (conflict in reported length/size)
- The Permanente Medical Group: 7,200+ physicians, 2.5M+ visits Oct 2023-Dec 2024, ~15,800 documentation hours saved in one year; top third of users drove ~90% of activations — [Mobius summary](https://www.mobius.md/blog/ambient-ai-scribes-lessons-from-patient-encounters) (secondary)
- Ardent Health: 70% of pilot clinicians reported reduced cognitive load; pilot users used the tool in 90% of scheduled Epic visits before enterprise rollout — [Fierce Healthcare](https://www.fiercehealthcare.com/health-tech/ambience-ardent-health-enterprise-rollout-ambient-ai-platform)
- Vanderbilt: enterprise launch to 2,400+ clinicians Jan 15 2025; by Mar 31 2025, 20.1% of visit notes used ambient scribing — [JAMIA](https://academic.oup.com/jamia/article/33/2/457/8307782)

### Inferences
- For an analytics/AI MVP, the competitive bar is a 60-90 day paid pilot (fee credited to year-one contract is a common way to make "nominal fee" acceptable), one outcome metric the CFO/VBC lead already tracks, and adoption metrics, with the conversion clause signed up front.
- Head-to-head bake-offs (Cleveland Clinic style) are now normal for crowded categories; expect to be measured against an incumbent or a competitor, not in isolation.
- Payers are the slowest buyers (11.3 months) and the smallest AI spenders ($50M of $1.4B in 2025); ACOs and risk-bearing provider groups likely sit between.
- Data staging the user's brief proposes (synthetic -> de-identified -> PHI) is reasonable, but note from HIPAA rules (45 CFR 164.514(e), not fetched this session): a limited data set is governed by a Data Use Agreement, whereas a BAA covers the vendor handling PHI as a business associate. In practice many pilots skip straight to PHI under a BAA once security review clears. Verify with counsel.

### Gaps
- No primary, current (2024-2026) pilot-to-contract conversion rate for health-system analytics specifically; the only figure is the 2017 self-reported Rock Health 60-70%.
- No published health-system innovation-centre pilot framework (e.g. Mayo, Providence, Atrium/Advocate, Jefferson) was retrieved; searches surfaced only vendor/consultancy guidance plus Stanford FURM by citation.
- Typical pilot fee amounts for analytics products were not found.

## 2. What a credible demo shows

### Takeaway
Buyers in 2025-2026 reward production readiness, validation evidence, transparent rationale, workflow fit and fast time-to-ROI; no source located describes a canonical "analytics demo" script, but the trust criteria translate directly into what to show.

### Cited Findings
- Clinical trust hinges on validation evidence (67%), transparent rationale (53%), patient-specific performance data (47%) and EHR integration (44%) — [Bessemer](https://www.bvp.com/atlas/the-2026-healthcare-ai-roi-scorecard)
- 57% of buyers say they are flooded with point-solution pitches; buyers are moving from evaluating capabilities to buying implementations; winners start with one wedge and expand, or offer co-development and deployment support — [Bessemer](https://www.bvp.com/atlas/the-2026-healthcare-ai-roi-scorecard)
- Menlo: buyers value production readiness (reliable at scale without heavy custom development), lower patient-risk use (non-patient-facing tools get faster approval), short-term ROI wins, and EHR integration; cost is secondary to trust and buyers pay a premium for AI they trust — [Menlo Ventures](https://menlovc.com/perspective/2025-the-state-of-ai-in-healthcare/)
- Customers slightly prefer buying from incumbent EHRs (Epic, Oracle Health) for coding, billing, prior auth, scheduling, CDS and navigation; startups are favoured for ambient scribing and chart review — [Menlo Ventures](https://menlovc.com/perspective/2025-the-state-of-ai-in-healthcare/)
- a16z: show where the product sits in daily workflow, which users it touches, whether it replaces or layers on tools; standalone tools outside the workflow go unused; buyers prefer tools that remove steps; startups need a clear "why not the incumbent" (faster implementation, time-to-ROI) — [a16z](https://a16z.com/commercializing-ai-in-healthcare-the-enterprise-buyer-perspective/)
- a16z sales piece: AI sales are shifting to faster pilots and proof-driven demos; buyers of tools that act in core workflows want compliance in place before committing — [a16z, Need for Speed in AI Sales](https://a16z.com/need-for-speed-in-ai-sales-ai-doesnt-just-change-what-you-sell-it-also-changes-how-you-sell-it/)
- Only 23% of health systems that report AI ROI can quantify it — [Perioperative Leader / Modern Healthcare, Sept 2025](https://www.periopleader.com/briefs/ai-vendors-bet-on-guaranteed-roi-as-competition-with-epic-intensifies)
- 30% of organisations are dropping vendors that fell short on security, compliance or performance; 42% are consolidating vendors — [Bessemer](https://www.bvp.com/atlas/the-2026-healthcare-ai-roi-scorecard)

### Inferences
- Given only 23% can quantify ROI, a demo that computes its own value (time to answer vs analyst baseline, cost per answer, dollars tied to a named measure) addresses an explicit buyer weakness.
- The Bessemer trust list maps to demo elements: validation evidence -> comparison against a held-out or analyst answer; transparent rationale -> visible audit trail/provenance per figure; patient-specific performance -> drill-down to the cohort/patient level; integration -> show the data path from the buyer's sources (FHIR/claims extracts) rather than a toy upload.
- Non-patient-facing analytics enjoys the "lower patient risk = faster approval" advantage Menlo identifies; keep the demo framed as decision support for population/finance teams.

### Gaps
- No sourced evidence found on buyers specifically requesting side-by-side "AI vs manual analyst" demos or "cost per answer" displays; these are reasonable but unsourced.
- No published time-to-first-value benchmark for analytics platforms (only the generic 4-8 week first-workflow claim from a vendor blog above).

## 3. ROI framing for readmissions and value-based care

### Takeaway
Buyers judge realized (not projected) ROI, with roughly 3x as the funding threshold and about 12 months to realization. For readmissions and VBC the credible anchors are public: HRRP penalty exposure (small for most hospitals, capped at 3% of Medicare base DRG payments), HCUP readmission cost (~$15-16K), and CMS-published ACO savings per beneficiary. Vendor-reported savings claims are discounted by buyers and should be.

### Cited Findings
**ROI thresholds**
- Enterprise buyers typically require a 3x realized return before funding at scale; average realized ROI across use cases 3.5x; provider RCM 4.0x, payer claims 3.4x, payer network 3.3x, payer member engagement 3.2x, provider front office 3.1x, provider clinical 2.9x — [Bessemer](https://www.bvp.com/atlas/the-2026-healthcare-ai-roi-scorecard)
- Buyers projected ~24 months to ROI and realized it in ~12; 54% see material ROI within year one; RCM value comes mainly from throughput and revenue, provider clinical AI from decision quality, not FTE cuts — [Bessemer](https://www.bvp.com/atlas/the-2026-healthcare-ai-roi-scorecard)
- a16z: buyers weigh ROI differently (cost savings vs revenue lift vs physician satisfaction); define KPIs early aligned to the buyer's current initiative — [a16z](https://a16z.com/commercializing-ai-in-healthcare-the-enterprise-buyer-perspective/)

**HRRP penalties (FY2026)**
- FY2026 (from Oct 1 2025): 240 hospitals (8.1%) face penalties of 1% or more, up from 208 (7%) in FY2025 — [Becker's](https://www.beckershospitalreview.com/finance/cms-more-hospitals-to-face-higher-readmission-penalties-in-2026)
- 21.8% (641) of hospitals not penalized; 70.1% face penalties under 1% — [Advisory Board, Sept 23 2025](https://www.advisory.com/daily-briefing/2025/09/23/readmission-penalties)
- Average penalty across ~2,799 participating hospitals ~0.34% of Medicare revenue (Definitive Healthcare, not FY-specific) — [Definitive Healthcare](https://www.definitivehc.com/resources/healthcare-insights/hospital-readmission-rate-penalties)
- FY2027 outlook: 75-82% of hospitals expected to be penalized, average rising to 0.44% — [Healthcare Dive](https://www.healthcaredive.com/news/hospital-readmission-reduction-program-penalties-rise-2027-cms/832473/)

**Readmission cost**
- 2020: average readmission cost $16,300 vs $14,500 index admission (12.4% higher) — [AHRQ HCUP SB307](https://hcup-us.ahrq.gov/reports/statbriefs/sb307-readmissions-2020.jsp)
- 2018: 3.8M adult 30-day readmissions, 14% rate, average readmission cost $15,200 — [AHRQ HCUP SB278](https://hcup-us.ahrq.gov/reports/statbriefs/sb278-Conditions-Frequent-Readmissions-By-Payer-2018.jsp)

**Shared savings (PY2024, most recent full results)**
- MSSP: net per-beneficiary savings $241 (up from $207), gross $643; >$2.4B net savings — [Health Affairs Forefront](https://www.healthaffairs.org/content/forefront/medicare-accountable-care-organizations-2024-increased-aco-participation-and-evolving); [Medical Economics](https://www.medicaleconomics.com/view/cms-acos-generated-2-4b-in-medicare-savings-in-2024)
- ACO REACH: $2.51B gross, $988.3M net savings; gross savings PBPM $88.04 (+24%); ~4.2% net savings rate; 96 of 115 ACOs (83%) earned savings — [CMS PY2024 fact sheet](https://www.cms.gov/priorities/innovation/files/acoreach-py2024-financialquality-fs.pdf); [Fierce Healthcare](https://www.fiercehealthcare.com/regulatory/aco-reach-participants-generated-nearly-1b-2024-savings-cms)
- Caveat: after incentive payments and other-model effects, an evaluation estimated net Medicare spending rose ~0.8% ($102/beneficiary) — [Healthcare Finance News](https://www.healthcarefinancenews.com/news/aco-reach-generated-988m-net-savings-2024)
- Vendor-claimed outperformance (self-reported, unaudited): Lightbeam clients 5.02% savings rate vs 4.72% non-clients — [Lightbeam](https://lightbeamhealth.com/?p=13789); Aledade >$665 per beneficiary — [Aledade](https://aledade-newsroom.prgloo.com/news/aledade-acos-save-over-usd-1-billion-in-2024); The Garage REACH customers $1,145 per capita vs $995 national — [Healthcare Dive press release](https://www.healthcaredive.com/press-release/20260715-the-garages-aco-reach-customers-generate-16844m-in-py-2024-gross-savings/)

**Labour framing**
- Half of surveyed organisations have cut or plan to cut headcount due to AI within six months, typically 8-13% of affected labour — [Bessemer](https://www.bvp.com/atlas/the-2026-healthcare-ai-roi-scorecard)
- a16z: AI products that do staff work can draw on labour budgets rather than IT budgets — [a16z Big Ideas 2025](https://a16z.com/big-ideas-in-tech-2025/)

### Inferences
- For most hospitals HRRP is a small line (sub-1% of Medicare base payments for 70%); penalty avoidance alone rarely carries a business case. A credible readmission case stacks avoided readmission cost (~$15-16K each, which is a cost to the hospital under DRG but a saving to risk-bearers), penalty reduction, and care-manager hours.
- For ACOs, credible framing is relative to CMS benchmarks: e.g. MSSP net $241/beneficiary and REACH ~$88 gross PBPM mean a 10,000-life ACO earns a modest pool; analytics fees must be a small fraction of the incremental savings attributable, with attribution method agreed up front.
- Expect buyers to discount any vendor savings claim that is not stated gross vs net, with denominator and cohort; showing those definitions is itself a credibility signal.

### Gaps
- No aggregate FY2026 HRRP penalty dollar total found (historically ~$300M+/yr per press, not verified here).
- No sourced, independent ROI study of analytics-driven care management or readmission programs (net of program cost) was found in this pass.
- No sourced data on "analyst hours saved" figures buyers accept for analytics tools.

## 4. Pricing models and indicative ranges

### Takeaway
Population-health/VBC analytics is sold mostly as annual subscriptions scaled to covered lives (third-party estimates ~$0.50-$10 PMPM, typical annual contracts in the low-six to low-seven figures, implementation billed separately). Clinical AI prices per provider per month; RCM often takes a percentage of revenue lifted. A clear 2025-2026 trend is risk-sharing: ROI guarantees, pay-after-savings and fees at risk against outcomes, usually as hybrid fixed-plus-variable.

### Cited Findings
- Third-party, unverified estimates: Arcadia $1-$10 PMPM (or ~$4), Innovaccer $0.50-$8 (or ~$3), enterprise range $1.50-$6 PMPM for large populations; Health Catalyst custom, listed $500K+/yr with outcome-based components — [MedicalRecords.com pricing guide](https://www.medicalrecords.com/ai-tools/pricing/population-health); [PulseRevOps](https://pulserevops.com/go-to-market-playbooks/gp0114) (low confidence)
- Arcadia annual contracts estimated $250K-$1.5M depending on population and modules; custom-quoted annual subscriptions scaled to covered lives/data volume, implementation billed separately — [ITQlick](https://www.itqlick.com/arcadia-io/pricing); [MedAI Verdict](https://medaiverdict.com/tools/arcadia-analytics) (third-party)
- Hidden costs: data integration fees, professional services, volumetric pricing on API calls or refresh frequency — [MedAI Verdict](https://medaiverdict.com/best-ai-population-health)
- Ambient scribes: average ~$250/provider/month, range ~$79-$900; self-serve tiers from $39 — [Commure, Scribe pricing 2026](https://www.commure.com/blog-scribe/scribe-pricing) (vendor)
- a16z pricing archetypes: surrogate pricing mirroring existing spend (e.g. take-rate for billing outsourcing), API/usage pricing, SaaS subscription, "AI staff" pricing at a fraction of human wages — [a16z](https://a16z.com/commercializing-ai-in-healthcare-the-enterprise-buyer-perspective/)
- Guaranteed-ROI offers (Sept 2025): Vital Software guarantees at least $1M return (system-size dependent) or free, excluding rural/non-competitive markets; Penguin ai free if no measurable ROI within 120 days; Carta Healthcare charges only after savings realized, no upfront fee, to avoid long budget approvals — [Perioperative Leader citing Modern Healthcare](https://www.periopleader.com/briefs/ai-vendors-bet-on-guaranteed-roi-as-competition-with-epic-intensifies)
- Startups tying fees to completed tasks (Prosper AI per completed call/workflow); an investor quoted is sceptical health systems like it; RCM vendors often take a percentage of added revenue — [MedCity News, 2026](https://medcitynews.com/2026/03/ai-startups-hospitals-healthcare/)
- Ambience reportedly put fees at risk against each health system's outcome targets (Aug 2026, via a pricing tracker; not verified with a primary source) — [UsagePricing](https://www.usagepricing.com/blueprint/activity/ambience-healthcare-2026-08-19-outcome-based-fees)
- ~23% of healthcare organisations prefer value-based pricing for AI (Black Book, via consultancy blog) — [Monetizely](https://www.getmonetizely.com/articles/what-is-the-optimal-pricing-model-for-ai-agents-in-healthcare-saas)
- GPO admin fees: ≤3% of price under the AKS safe harbor or disclosed; start a GPO contract after 2-3 customers ask; one-to-one ABM only pays at roughly $300K+/yr deal size; shared-savings or deferred payment suggested for struggling hospitals — [The Growth Syndicate](https://www.thegrowthsyndicate.com/resources/selling-to-hospitals)
- Menlo: cost is secondary to trust — [Menlo Ventures](https://menlovc.com/perspective/2025-the-state-of-ai-in-healthcare/)

### Inferences
- A first product aimed at ACOs/health systems could credibly price as a platform fee plus PMPM in the lower part of the $0.50-$6 band, with a variable component tied to agreed savings; a pure outcome-only fee creates cash-flow and attribution risk.
- Per-bed pricing was not found as a common model for analytics AI in these sources.

### Gaps
- No primary-source PMPM or per-bed prices for any named analytics vendor; all figures are third-party estimates.
- No "cost per answer" or per-query pricing precedent for analytics copilots found.

## 5. Lessons from fast growers and failures (2023-2026)

### Takeaway
Winners (ambient documentation, RCM) solved a painful, measurable workflow, integrated into the EHR, and proved value in head-to-head pilots; failures (Olive, Babylon, Forward) sold scale and AI narrative ahead of measured ROI and product maturity, and broke trust when renewals came due.

### Cited Findings
- 2025 healthcare AI spend ~$1.4B (nearly 3x 2024): ambient documentation $600M (2.4x YoY), coding/billing $450M, patient engagement $100M+ (20x), prior auth $100M+ (10x), payer ops $50M+; health systems $1B of the total, payers $50M; startups capture 85% of generative-AI healthcare spend — [Menlo Ventures](https://menlovc.com/perspective/2025-the-state-of-ai-in-healthcare/)
- Ambient scribe share: Nuance DAX 33%, Abridge 30%, Ambience 13%; 67% of outpatient providers likely to switch scribe vendor within three years — [Menlo Ventures](https://menlovc.com/perspective/2025-the-state-of-ai-in-healthcare/)
- RCM delivers the highest realized ROI (4.0x) and the highest share of semi/fully autonomous deployments (67%) — [Bessemer](https://www.bvp.com/atlas/the-2026-healthcare-ai-roi-scorecard)
- Fewer than half of internally built AI tools stay maintained and in use for 61% of organisations (a "buy over build" signal) — [Bessemer](https://www.bvp.com/atlas/the-2026-healthcare-ai-roi-scorecard)
- Olive AI (shut down 2023): large upfront hospital contracts, renewals collapsed when measurable savings did not appear at scale; bots frequently broke on brittle RPA; CEO cited strain from rapid expansion and lack of focus — [Dataconomy](https://dataconomy.com/2023/11/01/why-is-olive-ai-shutting-down/); [Marco Health](https://www.marco.health/the-rise-and-fall-of-olive-ai-lessons-from-a-healthcare-unicorn/) (secondary)
- Babylon: SPAC set unrealistic revenue expectations, expanded into care delivery before platform maturity, bankrupt 2023 with >$1B accumulated losses; Forward shut Nov 2024 after a capital-intensive model on a future-AI narrative — [HGM Advisory](https://www.hgm-advisory.com/insights/healthtech-failures) (secondary)
- "After Olive" commentary: healthcare leaders became sceptical of AI ROI promises — [Haverin Substack](https://haverin.substack.com/p/after-olive-why-healthcare-leaders)
- Epic's expanding AI suite is pushing startups toward risk-sharing to compete — [Perioperative Leader](https://www.periopleader.com/briefs/ai-vendors-bet-on-guaranteed-roi-as-competition-with-epic-intensifies)

### Inferences
- The analytics lesson: avoid selling breadth; pick one wedge with a measurable dollar outcome, make renewals depend on measured (not promised) value, and expect the EHR incumbent to be the default alternative.

### Gaps
- No well-documented 2023-2026 failure of a pure population-health analytics startup was retrieved; failure lessons above come from RPA, virtual care and care-delivery companies.

## 6. Synthetic data (e.g. Synthea) in demos and pilots

### Takeaway
Synthetic data is accepted for demos, integration testing and pre-PHI prototyping (EHR sandboxes themselves use Synthea), but it cannot validate outcome claims: Synthea's own maintainers say its disease models are simplified and its statistics vary by condition. Buyers will want the pilot proven on their own data.

### Cited Findings
- Synthea FAQ: statistical accuracy varies significantly by condition; limited diversity; models highly simplified, "better for learning how concepts are represented than for real insights"; covers common conditions only; one US state per generation run — [Synthea FAQ](https://github.com/synthetichealth/synthea/wiki/Frequently-Asked-Questions)
- Validation study: Synthea reliable on demographics and probability of services, but limited at modelling heterogeneous post-service outcomes and does not model care deviations and their outcomes — [BMC Med Inform Decis Mak, 2019](https://bmcmedinformdecismak.biomedcentral.com/track/pdf/10.1186/s12911-019-0793-0)
- Synthea medication data historically unrealistic; separate module built to fix it — [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC7651916)
- Few systematic methods exist to assess realism of synthetic EHR data — [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC10343944)
- SMART Health IT sandbox R4 data is Synthea; EHR vendor sandboxes forbid real PHI; one vendor's US Core validator found 2,412 errors on three Synthea patients ("a label is a claim, not proof") — [Nirmitee](https://nirmitee.io/blog/test-hospital-integrations-without-phi-synthea/) (vendor blog)
- Claims output structurally valid but edge cases (denials, adjustments, COB) may not match payer rules — [Tessl registry](https://tessl.io/registry/testland/synthea-healthcare-data) (secondary)
- Synthetic-first prototyping is used by public bodies to build trust before real data, with identifiability testing — [ICO / G7 DPAs](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/data-sharing/privacy-enhancing-technologies/case-studies/g7-dpas-emerging-technologies-working-group-use-case-study-on-privacy-enhancing-technologies)
- ONC funded synthetic data work (Synthea extensions) for patient-centred outcomes research — [HealthIT.gov](https://healthit.gov/blog/health-data/accelerating-patient-centered-outcomes-research-through-synthetic-health-data-generation/); [ONC Synthetic Data Challenge factsheet](https://www.healthit.gov/sites/default/files/page/2022-04/20220204_Synthetic_Data_Challenge_Factsheet.pdf)

### Inferences
- Synthetic data is a good demo and first-pilot-phase substrate if the demo is explicit that figures are illustrative; the selling point to show is workflow, provenance and the mechanics of the answer, not the magnitude of findings.
- Expect a buyer to ask that the same questions be re-run on their extract; the conversion pilot should be on real (de-identified or BAA-covered) data.

### Gaps
- No direct survey or buyer quote found on how health systems/ACOs weigh synthetic-data demos in procurement decisions.
