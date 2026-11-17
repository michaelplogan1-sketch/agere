# Buyer adoption and buying requirements for AI in hospital and health plan analytics, operations and finance (2024-2026)

Compiled 2026-11-17. Scope: what is new relative to `reports/Healthcare analytics MVP competitive bar.md`, which already covered Bessemer's 2026 ROI scorecard (3x ROI, 12-month payback, 72% governance committees, 23% able to quantify ROI, 28%/24% piloting vs 12%/6% in production), Menlo's 6.6- and 11.3-month buying cycles, the September 2025 Joint Commission-CHAI guidance, the CHAI Applied Model Card and HTI-5. Those are not repeated here except where new detail changes them.

Source labels used below:
- **[opened]**: I fetched and read the page.
- **[snippet]**: the figure comes only from search-result text. I did not open the page.
- **[vendor/investor]**: the publisher sells AI products or invests in companies that do.

## Adoption surveys: share using AI for revenue cycle, operations and analytics; budgets; time to purchase

### Takeaway
Measured "adoption" ranges from 22% to 88% depending on the definition. At the strict end, Menlo (investor-run) counts 22% of healthcare organisations as having deployed domain-specific AI. At the loose end, 80-88% of health systems say they are exploring or using AI in finance. Deployment at scale in revenue cycle is about a quarter of systems. Payers lag providers on deployment and spend, and their buying cycle is lengthening. Analytics is named as a priority, but no survey found sizes it as a separate spend category.

### Cited Findings
- **Menlo Ventures, State of AI in Healthcare 2025** [opened] [vendor/investor: Menlo marks the companies it backs in the report]. Survey of 700+ executives fielded with Morning Consult in Aug-Sep 2025: 410+ provider technology decision-makers, 120+ payer/benefits executives and 170+ pharma executives.
  - Deployed domain-specific AI: 22% of healthcare organisations overall (about 3% two years earlier); 27% of health systems; 18% of outpatient providers; 14% of payers.
  - Spend: healthcare AI spend was $1.4B in 2025, nearly triple 2024. Providers account for $1B (75%), outpatient providers $280M and payers only $50M (5%).
  - Spend by use case: ambient documentation $600M; coding and billing automation $450M; prior authorisation $100M+ (10x year on year); payer operations $50M+. Analytics is not reported as a category.
  - Buying cycle with AI compared with traditional IT: health systems 6.6 vs 8.0 months; outpatient providers 4.7 vs 6.0; payers 11.3 vs 9.4 (longer for AI).
  - Buyers weigh technology maturity, risk to patient care and short-term value. Cost is described as secondary. — [Menlo Ventures](https://menlovc.com/perspective/2025-the-state-of-ai-in-healthcare/)
- **HFMA with Eliciting Insights, Health System Readiness for AI** [opened]. 233 health systems plus CFO interviews in Q2 2025; released Aug 12, 2025.
  - 88% use AI internally. 71% have deployed pilot or full AI solutions in finance, revenue cycle or clinical areas.
  - Only 18% have a mature governance structure and a fully formed AI strategy. A secondary summary gives 17%.
  - Nearly 70% of CFOs report some governance structure, up from 40% in 2024.
  - Nearly 80% say an existing vendor, or a firm partnered with one, has a significant advantage over a new vendor piloting AI. 70% are more comfortable sharing data for AI with existing vendor partners.
  - Most say they lack the resources to identify where AI would deliver ROI. — [HFMA](https://www.hfma.org/health-system-adoption-of-ai-outpaces-internal-governance-and-strategy/)
- **HFMA with AKASA** [snippet] [vendor: AKASA sells revenue cycle AI]. 519 CFOs and revenue cycle leaders, HFMA Pulse Survey, April 2025; released Dec 2025.
  - 80% are "exploring, piloting or implementing" generative AI for revenue cycle.
  - About 20% of systems with $500M-$1B revenue are piloting or implementing, against 64% of larger systems.
  - Cost and budget are the largest barriers. — [HIT Consultant](https://hitconsultant.net/2025/12/17/hfma-survey-80-of-health-systems-adopt-genai-for-revenue-cycle-as-documentation-risks-rise); [AKASA](https://akasa.com/press/hfma-genai-revenue-cycle-survey)
- **HFMA, February 2026** [snippet]. 95 finance and revenue cycle leaders: 27% are deploying AI at scale across several revenue cycle functions, and 53% are still piloting in select areas. A separate HFMA/PayZen survey (205 leaders) found about 37% use generative AI in revenue cycle. — [HFMA](https://hfma.org/technology/most-healthcare-organizations-are-adopting-ai-in-the-revenue-cycle-hfma-poll)
- **Bain & Company with KLAS, 2025 Healthcare IT Spending survey** [opened]. 228 US provider and payer executives; Oct 9, 2025.
  - AI strategy in place or in development: about 70% of providers and 80% of payers, up from about 60% for both the year before.
  - Revenue cycle returned to the top provider priority.
  - Ambient documentation: about 20% of providers at full rollout and 40% in pilot. This is the only use case given a production share in the text. CDI, coding, prior authorisation and analytics are named as top use cases without percentages.
  - Fewer than 5% of providers said deployed AI was not meeting expectations. Payers reported more shortfalls, which Bain attributes partly to fewer internal resources for measuring impact.
  - Purchasing approach: about 60% of providers buy EHR-first when the EHR module is "good enough" (67% of Epic customers vs 46% of non-Epic). About 20% are primarily best-of-breed buyers.
  - Payers default to best-of-breed and prioritise total cost of ownership, functionality and scalability. National plans lean toward building their own tools.
  - About three quarters of payers expect value-based care IT spend to grow faster than other priorities over three years.
  - Payer priorities: care coordination, utilisation management, prior-authorisation automation, population data analytics, network optimisation, and VBC risk and quality tools.
  - Vendor-reported example: an Epic-Humana pilot processed 75% of coverage checks automatically. — [Bain](https://www.bain.com/insights/healthcare-it-investment-ai-moves-from-pilot-to-production/)
- **Oliver Wyman 2026 revenue cycle survey** [opened]. 200+ decision-makers and 90 end users at US providers (about May 2026; method undisclosed). 20-40% use AI broadly, and 70-90% plan to increase spending. — [Oliver Wyman](https://oliverwyman.com/our-expertise/perspectives/health/2026/may/ai-impact-revenue-cycle-healthcare.html)
- **Center for Connected Medicine (UPMC) with KLAS** [opened] [vendor: UPMC Enterprises markets the Ahavi validation platform]. "More than two dozen" health system leaders; Aug 6, 2026.
  - More than 90% have deployed third-party AI.
  - Deployment areas: clinical documentation 52%; revenue cycle, coding and billing 36%.
  - 63% describe their AI approach as developing or ad hoc.
  - There is no consensus on metrics for AI success or ROI. — [UPMC Enterprises](https://enterprises.upmc.com/resources/insights/governance-of-ai-solutions-at-health-systems/)
- **Hospital predictive AI, 2023 AHA Annual Survey** [snippet, consistent across several outlets]. About 2,425 hospitals; Nong et al., Health Affairs, Jan 2025.
  - 65% of hospitals used EHR-integrated predictive models.
  - Model sources: EHR vendor 79%, third parties 59%, self-developed 54%.
  - Billing and outpatient-follow-up models were evaluated less often than clinical ones. — [Healthcare Dive](https://www.healthcaredive.com/news/hospital-AI-evaluation-ai-bias-health-affairs/737059/); [Health Journalism](https://healthjournalism.org/blog/2025/02/hospitals-dont-always-test-ai-for-accuracy-and-bias-study-says/)

### Inferences
- The incumbent advantage is the most decision-relevant new number for Agere. About 80% of CFOs favour existing vendors or their partners, and about 60% of providers buy EHR-first. A new entrant should therefore plan to sell into or alongside the buyer's existing warehouse or EHR vendor ecosystem, not as a replacement.
- Payers are a smaller and slower market: 5% of AI spend, 11.3-month cycles, and a best-of-breed or build preference. They also rank VBC, risk and quality, and population analytics highly, which matches Agere's scope. Payer go-to-market should expect a longer cycle and a total-cost-of-ownership comparison against building in-house.
- Revenue cycle and ambient documentation absorb most budget. Population and VBC analytics would compete for a smaller, less clearly defined budget line. This is an inference: no survey reports analytics spend.

### Gaps
- I found no AHA, MGMA, CHIME, McKinsey or Deloitte 2025-26 AI adoption figures specific to analytics in this pass. Those publisher pages were not opened.
- No survey isolates spend or adoption for conversational analytics or population-health analytics.
- The original HFMA/AKASA page could not be read: Fierce Healthcare returned 403, so those figures are snippet-only.

## Measured ROI and outcomes of deployed AI in hospital operations and finance: independent vs vendor-reported

### Takeaway
Independent evidence that administrative AI lowers costs is essentially absent. The most credible independent body (PHTI, 2026) concludes that AI is speeding up and inflating administrative transactions under fee-for-service rather than reducing cost. Published ROI figures are almost entirely vendor- or client-reported.

### Cited Findings
- **PHTI, Administrative AI: Current Use and Potential Impact** (April 2026) [snippet; built on a January 2026 convening of health systems, plans, developers, investors and federal agencies]. AI is speeding up administrative transactions such as prior authorisation and coding without improving efficiency or lowering costs. AI-assisted billing is raising reimbursement and adding to medical cost inflation. — [Fierce Healthcare](https://fiercehealthcare.com/ai-and-machine-learning/ai-speeding-prior-authorizations-while-driving-higher-costs-health-systems); [BenefitsPro](https://www.benefitspro.com/amp/2026/04/15/administrative-ai-fails-to-lower-health-care-costs-under-current-incentive-models/) (both returned 403 when fetched)
- **PHTI, Driving Adoption of High-Value AI in Healthcare** (Oct 2026) [opened via HIT Consultant summary; full report not read].
  - Under fee-for-service, administrative AI accelerates existing processes, adds friction and drives upcoding.
  - An AI scribe was associated with a 5% rise in Level 5 visits, a 7% rise in Level 4 established-patient encounters, and about $1,004 in extra monthly billing per doctor.
  - Payer downcoding and denial algorithms are met by provider generative-AI appeals, an "automated arms race" in prior authorisation that raises volume without improving outcomes.
  - Recommendations: shift toward outcome-based payment (citing the CMS ACCESS model, mid-2026); set AI payment rates dynamically; avoid per-use fees that drive overuse. — [HIT Consultant](https://hitconsultant.net/2026/10/14/phti-report-driving-adoption-high-value-ai-healthcare-caroline-pearson-roadmap-payment-reform/)
- **Health Affairs, Mello et al.** (reported Jan 6-7, 2026) [opened via Stanford press release]. No published data show whether AI improves or worsens utilisation management outcomes. Insurers have not shared data supporting their benefit claims. Historical context: 82% of appealed Medicare Advantage denials were overturned, and over 93% of MA prior-authorisation requests were approved in 2019-2023. — [Stanford Law](https://law.stanford.edu/press/when-ai-algorithms-decide-whether-your-insurance-will-cover-your-care/)
- **Coding** [opened, figures unsourced]. Oliver Wyman cites coding accuracy of "90% or higher in specific clinical domains" and up to about 46% less coding time on complex cases, without naming the studies. It gives no measured outcomes for denials, cost to collect or days in AR. — [Oliver Wyman](https://oliverwyman.com/our-expertise/perspectives/health/2026/may/ai-impact-revenue-cycle-healthcare.html)
- **Self-reported satisfaction** [opened]. Fewer than 5% of providers said AI was not meeting expectations (Bain/KLAS 2025), but many said it was too early to quantify financial returns. — [Bain](https://www.bain.com/insights/healthcare-it-investment-ai-moves-from-pilot-to-production/)
- **WISeR** [snippet; hospital association report, not peer-reviewed]. Washington State hospital data on the CMS WISeR prior-authorisation pilot (three systems, 16 locations) report longer delays, higher costs and more denials. — [Rise Health](https://www.risehealth.org/insights-articles/another-report-finds-wiser-pilot-causing-longer-delays-higher-costs-and-care-denials/)

### Inferences
- A buyer who has read PHTI will discount transaction-speed and revenue-capture ROI claims. VBC and population analytics are on the right side of PHTI's argument: value in outcome-aligned payment comes from lower total cost of care, not more billing. Agere's ROI case should be framed in VBC terms, such as shared savings, avoided utilisation and analyst hours, rather than revenue uplift.
- Because buyers lack the resources to measure ROI (HFMA) and there is no consensus on ROI metrics (UPMC/KLAS), a pre-specified ROI measurement method in the pilot contract is itself a differentiator. This extends the earlier report's 23% finding.

### Gaps
- I found no peer-reviewed controlled study of AI's effect on denial rates, prior-authorisation turnaround or cost to collect at a hospital.
- The full PHTI reports (PDF) were not read, per the no-PDF constraint.
- I found no independent ROI evidence for AI analytics or decision-support tools in population health.

## Failure and abandonment rates (MIT NANDA "GenAI divide" 2025 and health-system pilots)

### Takeaway
The MIT NANDA "95%" is a preliminary, non-peer-reviewed, cross-industry figure. It rests on 52 interviews, 153 surveys and reviews of 300+ public initiatives from January-June 2025, the data are unreleased, and its definitions are disputed. It is not healthcare-specific and should not be quoted as a healthcare failure rate. Healthcare-specific evidence instead shows a large gap between piloting and production.

### Cited Findings
- **Method** [opened, secondary summary]. 52 executive interviews, 153 surveys and 300+ implementation reviews; labelled "preliminary" and anonymised; dated July 2025. Framing: "95% of businesses see no ROI" despite $30-40B invested, and "only 5% of custom tools reach production". The only healthcare statement is that finance, healthcare and energy "remain largely unchanged". — [AI Governance Library summary](https://www.aigl.blog/state-of-ai-in-business-2025.md)
- **Research window and sample** [snippet]. The research ran January-June 2025. Some press accounts misdescribe the sample as 150 interviews and 350 employees. — [letsdatascience](https://letsdatascience.com/news/mit-report-documents-genai-pilot-roi-gap-e0924d7d); [Fortune via Yahoo](https://finance.yahoo.com/news/mit-report-95-generative-ai-105412686.html)
- **Criticism** [snippet]. Critics say the 95% figure comes from the subset that piloted task-specific tools. The underlying data were gated behind a form, and Kevin Werbach called for release or retraction. Others argue that zero measured P&L impact is not the same as zero value. The report also says externally purchased tools succeed about 67% of the time against about 33% for internal builds (figure not verified in the primary text). — [Futuriom](https://www.futuriom.com/articles/news/why-we-dont-believe-mit-nandas-werid-ai-study/2025/08/); [arnon.dk](https://arnon.dk/mits-95-ai-failure-rate-is-wrong/)
- **Healthcare pilot-to-scale** [snippet]. In revenue cycle, 27% are deploying at scale and 53% are still piloting (HFMA, Feb 2026, n=95). — [HFMA](https://hfma.org/technology/most-healthcare-organizations-are-adopting-ai-in-the-revenue-cycle-hfma-poll)
- **Structural readiness** [opened]. 63% of systems describe their AI approach as developing or ad hoc (UPMC/KLAS 2026). More than 80% lack mature programs to manage AI investments (HFMA 2025). — [UPMC Enterprises](https://enterprises.upmc.com/resources/insights/governance-of-ai-solutions-at-health-systems/); [HFMA](https://www.hfma.org/health-system-adoption-of-ai-outpaces-internal-governance-and-strategy/)

### Inferences
- The defensible statement is that most healthcare AI work remains at pilot stage and few buyers can measure its return. "95% fail" is not defensible. Citing NANDA uncritically would undercut an evidence-first pitch.
- NANDA's buy-over-build signal, if accurate, and Bain's finding that national payers lean toward building both point to a "governed product inside your warehouse" positioning: buyers get a product while keeping data control.

### Gaps
- I found no healthcare-specific abandonment rate: no count of AI pilots discontinued at health systems or plans.
- I could not confirm NANDA's exact definitions of "success" and "deployment" without the primary PDF.

## Governance and procurement requirements: CHAI, Joint Commission-CHAI, committees and vendor evidence

### Takeaway
Pre-deployment evaluation is now nearly universal (92%), but fewer than half of health systems have anywhere to test, and post-deployment monitoring is the weakest link. Payers report governance principles (92%) but less consistent bias testing. A vendor that brings its own validation harness, a model card and monitoring removes the step buyers are least equipped to do.

### Cited Findings
- **Evaluation capacity** [opened]. 92% of health systems evaluate third-party AI before deployment, but only 44% have a dedicated data platform or environment for testing. Methods range from formal protocols to informal pilots. — [UPMC Enterprises](https://enterprises.upmc.com/resources/insights/governance-of-ai-solutions-at-health-systems/)
- **Hospital evaluation rates, 2023 data** [snippet]. 61% of hospitals using predictive models evaluated them for accuracy, and 44% for bias. Evaluation was more common at hospitals that built their own models, had high margins or belonged to systems. Billing models were less often evaluated. — [Healthcare Dive](https://www.healthcaredive.com/news/hospital-AI-evaluation-ai-bias-health-affairs/737059/)
- **Hospital evaluation rates, 2024 data** [snippet only; attributed to federal data, primary not opened]. 82% evaluated accuracy, 74% evaluated bias and 79% did post-implementation monitoring. — Search summary; the primary (likely an ASTP/ONC data brief) was not verified.
- **CFO governance** [opened]. Nearly 70% report some governance in 2025, up from 40% in 2024, but only 18% are mature. — [HFMA](https://www.hfma.org/health-system-adoption-of-ai-outpaces-internal-governance-and-strategy/)
- **CHAI Applied Model Cards** [snippet]. Applied Model Cards cover developer, intended use, target population, model type, data and key performance metrics, and are positioned for procurement. 36 health systems and solution partners committed to them, including Cleveland Clinic, Duke and Kaiser Permanente. CHAI built a model card registry with Avanade, and a draft assurance-lab certification framework (ANAB/ISO 17025, conflict-of-interest disclosure) was published. ModelOp markets itself as a CHAI-certified assurance provider. — [CHAI](https://chai.org/chai-advances-assurance-lab-certification-and-nutrition-label-for-health-ai/); [Healthcare IT News](https://www.healthcareitnews.com/news/chai-look-healthcare-ai-model-nutrition-labels-soon); [CHAI/ModelOp](https://www.chai.org/assurance-resource-providers/modelop)
- **ANSI/HSI 2800:2025** [snippet; vendor source, Censinet]. The standard expects a documented AI inventory, an approval process, local validation, audit trails and retirement rules. — [Censinet](https://censinet.com/perspectives/first-american-national-standard-ai-governance-healthcare-operations)

### Inferences
- An evidence pack built to clear these gates would contain: a CHAI-style Applied Model Card per AI function (router, small model, frontier SQL, chart reader); the held-out scorecard reproducible on the buyer's deidentified extract inside their own environment, which answers the "nowhere to validate" gap; a drift and monitoring plan tied to the weekly scorecard; and an audit trail plus retirement procedure for recipes and models, mapping to ANSI/HSI 2800.
- Administrative and billing tools have historically been evaluated less rigorously than clinical ones. Rigorous evaluation of an analytics tool therefore exceeds the norm.

### Gaps
- I found no public list of the evidence items health system committees request from vendors, beyond the framework documents.
- The current (2026) status of CHAI assurance-lab certification and the registry is unconfirmed. Search results were 2024-25.
- I did not open the Joint Commission-CHAI guidance itself in this pass; it was covered in the earlier report.

## Health plan specifics: AI use, risk adjustment, care management, claims and regulatory scrutiny

### Takeaway
Most large insurers use AI (84%, NAIC, 93 insurers), concentrated in claims and utilisation management. Payers are a small buyer of third-party AI ($50M, 5% of spend), have the longest AI buying cycle, and face the heaviest scrutiny of AI in coverage decisions. Their stated IT priorities (VBC, risk and quality, population analytics, care coordination) align with Agere's scope.

### Cited Findings
- **NAIC Health AI/ML Survey** [opened]. 93 insurers in 16 states with over $250M in premiums; fielded Nov 2024-Jan 2025; released May 20, 2025. 84% use AI/ML in some capacity, and about 92% have governance principles modelled on NAIC's AI Principles. Uses listed without percentages: utilisation management, prior authorisation, fraud and disease management. — [NAIC](https://content.naic.org/article/naic-survey-reveals-majority-health-insurers-embrace-ai)
- **NAIC detail** [snippet; from the NAIC memo PDF, not opened]. Among implemented models, 62% are for claims adjudication and 29% for prior authorisation. 12% of insurers use AI specifically in denying prior authorisations. Nearly a third do not regularly test models for bias. — [NAIC memo](https://content.naic.org/sites/default/files/inline-files/Health%20Survey%20Memo%20to%20BDAIWG%2005092025%20-%20Final.pdf); [Insurance Business](https://insurancebusinessmag.com/us/news/breaking-news/naic-highlights-ai-challenges-in-health-insurance-sector-515358.aspx)
- **Payer market size** [opened]. Payers are 14% adopted, account for $50M (5%) of healthcare AI spend, and have an 11.3-month AI buying cycle. Concerns include rising provider claims volume driven by provider AI. — [Menlo Ventures](https://menlovc.com/perspective/2025-the-state-of-ai-in-healthcare/)
- **Payer priorities and pressures** [opened]. Payers prioritise care coordination and UM, VBC risk and quality, and population analytics. They face rising MLRs, risk-adjustment scrutiny and Medicaid and ACA enrollment pressure. — [Bain](https://www.bain.com/insights/healthcare-it-investment-ai-moves-from-pilot-to-production/)
- **Regulatory scrutiny** [opened]. Coverage AI has drawn litigation, and insurers have not published benefit evidence. CMS requires specific reasons for denials. — [Stanford Law](https://law.stanford.edu/press/when-ai-algorithms-decide-whether-your-insurance-will-cover-your-care/)
- **State legislation** [snippet]. Health plans were a primary target of state AI legislation in 2025. — [DHI](https://dhinsights.org/news/health-plans-are-a-primary-target-of-ai-legislation-in-2025); [ABA](https://www.americanbar.org/groups/health_law/resources/esource/2026/regulatory-landscape-of-ai-in-health-insurance-space/)

### Inferences
- For payers, Agere should position as population, VBC and risk-quality analytics with human review, explicitly outside coverage or denial decisions, to avoid the most regulated use.
- A risk-adjustment analytics feature must be framed as accuracy and compliance (V28, chart-review exclusions), not RAF uplift, given risk-adjustment scrutiny and PHTI's upcoding finding.

### Gaps
- I found no AHIP 2025-26 survey on member plans' AI use.
- No payer-specific figures exist for AI in risk adjustment or care management.
- Specific statutes (for example California SB 1120) and the CMS MA AI guidance were not opened in this pass.
- No KLAS payer-AI vendor satisfaction data were found.
