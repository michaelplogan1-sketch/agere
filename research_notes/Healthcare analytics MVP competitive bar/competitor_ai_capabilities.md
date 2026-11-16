# Competitor AI and analytics capabilities: population health, VBC and healthcare data platforms (2025 to Nov 2026)

Research date: 2026-11-15. Public web only. About 18 tool calls were used. Several findings come from search-engine summaries of pages. Each such finding is marked "(search summary)" and its source URL should be re-checked before anyone quotes it. Findings without that mark were read directly from the page (WebFetch). "Vendor claim" means the vendor itself published the figure. "Independent" means someone with no stake in the product measured it.

## Q1. What natural-language / AI analytics features exist, what data they run on, and whether answers are verified, certified or reusable

### Takeaway
The general data platforms are the furthest along on governed natural-language Q&A. Databricks Genie uses Unity Catalog metric views, trusted assets, benchmarks and an ontology. Snowflake Cortex Analyst uses semantic views and verified queries. Both make answers reusable through curated question/SQL pairs and governed metric definitions. The healthcare-specific vendors are different. Innovaccer, Arcadia, Lightbeam, Health Catalyst and Epic all market conversational "copilot" or "conversational AI" layers over their own population-health data models, and now pre-built agents for care gaps, care navigation and prior authorization. None of them publicly documents a mechanism comparable to verified queries or certified answers. The most they offer is "show the steps" transparency (Innovaccer) and audit trails (Arcadia, Innovaccer).

### Cited Findings

**Databricks (AI/BI Genie, Unity Catalog metric views, Genie Ontology)**
- Genie spaces answer natural-language questions over Unity Catalog tables. Databricks guidance says to ground them with Unity Catalog metric views. Metric views are reusable SQL objects that define and govern business KPIs. — [Databricks docs: Unity Catalog semantics](https://docs.databricks.com/en/uc-semantics/) (search summary)
- Databricks best-practice guidance says metric views should enforce consistent definitions. It also recommends benchmarks and feedback loops to maintain quality. Practitioner guidance adds three things: fix business-logic errors by adding example SQL, trusted assets or metric views; require thumbs-up/down feedback; and run regular benchmarks to catch drift. — [Databricks blog: Genie space best practices](https://databricks.com/blog/data-dialogue-best-practices-guide-building-high-performing-genie-spaces); [Databricks community: Genie metadata QA](https://community.databricks.com/t5/technical-blog/genie-metadata-qa-hands-on-testing-tips/ba-p/142423) (search summary)
- Genie Ontology (2026) extracts context from workspace assets, including notebooks, dashboards, saved SQL and documents, so that Genie can answer. The Nov 11, 2026 benchmark post does not discuss trusted or certified assets. — [Databricks blog, Nov 11 2026](https://www.databricks.com/blog/genie-genie-ontology-delivers-16-accuracy-less-half-tokens)
- Genie space limits (third-party summary, Aug 2026):
  - Up to 30 Unity Catalog tables per space, raised from 25 in 2026. Databricks advises five or fewer.
  - 100 instructions and 200 knowledge-store snippets per space.
  - 20 questions per minute per workspace via the UI.
  - 10,000 conversations per space.
  - Requires a pro or serverless SQL warehouse.
  - Databricks documents Genie as "nondeterministic."
  - Source: [Colrows: Cortex Analyst vs Genie (2026)](https://colrows.com/blogs/cortex-analyst-vs-genie/)
- Coverage of Data + AI Summit 2026 says Genie was given ongoing enterprise jobs, with humans setting the limits. The details were not verified. — [Superpower Daily](https://superpowerdaily.com/posts/databricks-gives-genie-ongoing-enterprise-jobs-with-humans-setting-the-limits); [Datapao Summit 2026 recap](https://datapao.com/blog/databricks-data-ai-summit-2026) (search summary)
- Healthcare use:
  - Premier reportedly plans to scale Genie to as many as 20,000 users across hospitals (search summary; primary source not fetched).
  - Health Catalyst released 10 AI-integrated data toolkits on Databricks Marketplace. — [Health Catalyst IR](https://ir.healthcatalyst.com/news-releases/news-release-details/accelerating-measurable-improvement-through-actionable-insights) (search summary)

**Snowflake (Cortex Analyst, semantic views, verified queries)**
- Cortex Analyst answers natural-language questions by generating SQL over a semantic model or semantic view. — [Snowflake docs: Cortex Analyst](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst) (search summary)
- Verified queries are curated question/SQL pairs. Cortex Analyst can return one directly when a user asks a similar question. They carry `verified_at` and `verified_by` governance fields. — [QueryPlane: Cortex Analyst in practice](https://queryplane.com/blog/snowflake-cortex-analyst-in-practice/) (search summary)
- Snowflake has a built-in evaluation feature:
  - It runs Cortex Analyst's generated SQL and compares the results with verified queries as ground truth.
  - Each run reports accuracy and regressions.
  - Evaluation sets are curated manually from verified queries; they are not auto-generated.
  - Source: [Snowflake docs: Cortex Analyst evaluations](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations) (search summary)
- Practitioner reports (undated) say two things. Semantic views only work when treated as a strict interface. Verified queries are the main accuracy lever, and prompt engineering cannot make up for a poorly described YAML model. — [Colrows](https://colrows.com/blogs/cortex-analyst-vs-genie/)
- Documented limits:
  - Cortex Analyst reads only Snowflake metadata and compute.
  - The model is chosen by Snowflake, not the user, and results vary by model.
  - Native support is in 9 regions; cross-region inference is used elsewhere.
  - Source: [Colrows](https://colrows.com/blogs/cortex-analyst-vs-genie/)

**Innovaccer (Healthcare Intelligence Cloud, Gravity, agents, Population Health Copilot, Atlas)**
- Population Health Copilot 2.0 (Feb 27, 2025):
  - Marketed as "ask your data anything in plain English."
  - It runs parallel queries across multiple datasets, which the post does not name.
  - It uses a chain-of-thought approach and shows the steps behind each answer.
  - The post makes no accuracy claim and describes no human review or formal verification.
  - Source: [Innovaccer blog, Feb 2025](https://innovaccer.ai/blogs/population-health-copilot-2-0-is-now-faster-smarter-and-more-intuitive)
- Gravity (2026):
  - More than 50 pre-built healthcare AI agents.
  - A Healthcare Knowledge Graph that powers population health analytics.
  - Agent Studio and a Healthcare Model Context Protocol (HMCP) for orchestrating workflows across EHRs.
  - Agents cover prior authorization, care-gap closure, patient access, revenue cycle and population health.
  - Claimed governance: model impact assessments, always-on PHI redaction, role-based access control, and audit logs.
  - Source: [BusinessWire: Innovaccer Gartner MQ Leader, Jul 29 2026](https://www.businesswire.com/news/home/20260729355311/en/Innovaccer-Named-a-Leader-in-the-Inaugural-Gartner-Magic-Quadrant-for-Healthcare-Provider-Industry-Cloud-Platforms) (search summary)
- Atlas, described as a "population health operating system" for VBC, fee-for-service and new CMS models, launched in January 2026. Innovaccer was accepted into the first cohort of the CMS ACCESS model on Apr 13, 2026. — [Innovaccer/BusinessWire](https://www.businesswire.com/news/home/20260127402414/en) (search summary)
- Provider Copilot provides pre-visit summaries. BSIM Healthcare Services adopted it in April 2026, along with ambient scribe, the Agentic AI Access Center and Flow Auth. — [BusinessWire, Apr 9 2026](https://www.businesswire.com/news/home/20260409604598/en/BSIM-Healthcare-Services-Adopts-Innovaccers-AI-Agents-to-Streamline-Clinical-Workflows-and-Enhance-Patient-Access) (search summary)
- One article title reports Innovaccer is "pouring $250M into its agentic AI platform." The article was not read. — [Plocamium](https://plocamium.com/globals/279/Why-Innovaccer-Is-Pouring-250M-Into-Its-Agentic-AI-Platform)

**Arcadia (Ascent, CareJourney, LifeScience; Oct 2026)**
- Announced Oct 5, 2026 at Aggregate:
  - Ascent Insight: "conversational AI," automated report delivery, predictive risk modeling, governed data management and "explainable intelligence."
  - Ascent Care: AI patient prioritization and an autonomous care-navigation agent covering the whole attributed population.
  - Ascent Performance: care-gap closure, quality improvement and risk adjustment.
  - Ascent Finance: actuarial modeling, risk scenarios, contract execution and multi-payer reconciliation.
  - Ascent Network: provider scoring, episode analytics, benchmarks, network adequacy and leakage.
  - Ascent Connect: links EHR and claims data.
  - Source: [HIT Consultant, Oct 5 2026](https://hitconsultant.net/2026/10/05/arcadia-launches-ascent-carejourney-lifescience-agentic-population-health-platform/)
- Arcadia's governance model:
  - Agent workflows start in assistive mode and move to autonomous execution only after performance thresholds are proven.
  - Every autonomous action is logged in a tamper-evident audit trail that records the reasoning and data lineage.
  - No verified- or certified-answer mechanism is described for the conversational Q&A.
  - Source: [HIT Consultant](https://hitconsultant.net/2026/10/05/arcadia-launches-ascent-carejourney-lifescience-agentic-population-health-platform/)

**Health Catalyst (Ignite, Healthcare.AI)**
- Ignite unifies clinical, financial and claims data into a standardized model for self-service analytics with embedded AI through Healthcare.AI. — [Health Catalyst Ignite product page](https://www.healthcatalyst.com/products/health-catalyst-ignite-data-and-analytics) (search summary)
- The 2025 product roadmap lists a Q1 2026 item: use AI to rank analysis results by potential value and present them in natural language. This is a plan, and no confirmation that it shipped was found. — [Health Catalyst product roadmap PDF (2025)](https://docs.healthcatalyst.com/getting-started/articles/media/product-roadmaps.pdf) (search summary)
- Healthcare.AI Enhanced Analytics and Ignite can be bought on the Azure Marketplace under a Microsoft partnership. — [Health Catalyst news release](https://healthcatalystinc.gcs-web.com/news-releases/news-release-details/health-catalyst-announces-strategic-partnership-microsoft) (search summary)
- No shipped text-to-SQL or "ask your data" assistant in Ignite was found in 2026 sources.

**Lightbeam Health Solutions**
- At HIMSS26 (March 2026), Lightbeam showed two Microsoft Copilot-enabled tools on the Microsoft Marketplace:
  - Analytics Copilot: conversational questions over population health data.
  - Cohort Copilot: builds dynamic patient groups with inclusion and exclusion logic.
  - Lightbeam also announced support for new CMS models and actuarial tools connected to Syntax and Wakely.
  - Source: [Lightbeam HIMSS 2026 press release PDF](https://lightbeamhealth.com/wp-content/uploads/2026/03/Press-Release-LB-HIMSS-2026-Highlights.pdf) (search summary)
- No public detail was found on the data scope, accuracy, verification or governance of these Copilots.

**Epic (brief)**
- Cogito, Epic's analytics suite including Caboodle and SlicerDicer, is used to track readmissions and quality.
- UGM 2026 coverage describes two newer features:
  - "Cogito Assistant," which produces dynamic visualizations.
  - "Cosmos Curiosity," which uses de-identified Cosmos data to flag risks such as readmission and to answer population-level questions.
- Epic also previewed "Agent Factory" for building AI agents (HIMSS26).
- Sources: [Forbes, UGM 2026](https://www.forbes.com/sites/davidchou/2026/08/25/epics-big-bet-three-themes-from-ugm-2026/); [MedCity News, Aug 2026](https://medcitynews.com/2026/08/epic-ftc-agentic-ai/); [Fierce Healthcare, HIMSS26](https://www.fiercehealthcare.com/ai-and-machine-learning/himss26-epic-expands-ai-roadmap-previews-factory-build-and-orchestrate-ai) (search summary)
- Emmie is a patient-facing MyChart assistant, not an analytics tool. — [Epic](https://www.epic.com/epic/post/community-health-network-adopts-emmie-an-ai-assistant-in-mychart-to-enhance-patient-experience/) (search summary)

**Others (brief)**
- Palantir (Foundry/AIP for Hospitals) focuses on operations: command centers, census, staffing and bed forecasting. Cleveland Clinic, Tampa General, Nebraska Medicine and HCA are named users. NYC Health + Hospitals reportedly did not renew its contract in March 2026. — [Palantir for Hospitals](https://www.palantir.com/offerings/palantir-for-hospitals/); [AFSC factsheet, Jun 2026 (advocacy source)](https://afsc.org/sites/default/files/2026-06/palantir-in-healthcare_final_0.pdf) (search summary)
- Komodo Health's "Marmot" AI engine runs on its Healthcare Map and is available on AWS Marketplace. Komodo also announced a multi-year NCQA quality-measurement collaboration on Aug 26, 2026. — [Komodo Health](https://www.komodohealth.com/?p=18530); [MobiHealthNews](https://www.mobihealthnews.com/news/komodo-health-ncqa-partner-accelerate-healthcare-quality-measurement) (search summary)
- Milliman MedInsight launched MIKE (MedInsight Knowledge Engine), an AI assistant in MedInsight Health Cloud, on Sep 14, 2026. — [Webull/press](https://www.webull.com/news/15576896791774208); [Milliman insight](https://www.milliman.com/en/insight/collection-conviction-ai-healthcare-analytics) (search summary)
- Optum: the closest match found is HealthTrust's Crimson AI analytics platform, built with Optum and launched Feb 25, 2026. — [HealthTrust](https://healthtrustpg.com/?p=17271) (search summary)
- Cedar Gate: nothing found.

### Inferences
- The competitive bar for trust in natural-language answers is set by the horizontal platforms. Their pattern has three parts: governed metric definitions, curated question/SQL pairs reused as answers and as ground truth, and regression evaluation. Healthcare vendors compete instead on pre-built data models, measures and agents, and say little publicly about checking whether an answer is correct.
- Every healthcare vendor's 2026 messaging has moved from dashboards to agents with audit logs. Arcadia's "assistive first, autonomous after proven thresholds" is the most explicit staged-activation policy found.

### Gaps
- Not found: Snowflake's healthcare and life-sciences data cloud specifics, and any healthcare-specific Cortex Analyst deployment with figures.
- Not found: what data Innovaccer's Copilot, Arcadia's Ascent Insight or Lightbeam's Analytics Copilot can query (whole warehouse or curated marts), or whether they support saved or verified answers.
- Not confirmed: whether Health Catalyst shipped its natural-language roadmap item.

## Q2. Published accuracy figures for question-answering features and how they were measured

### Takeaway
Every published accuracy figure for natural-language Q&A is vendor-run (Databricks, Snowflake) or a consultancy anecdote. No healthcare vendor publishes Q&A accuracy, and no independent healthcare-data benchmark of Genie or Cortex Analyst was found. Outcome claims from the healthcare vendors, such as fewer admissions, are self-reported. None names a control group or validator.

### Cited Findings
- Databricks Genie plus Genie Ontology (Nov 11, 2026):
  - Pass rates: Genie 91.0%, unnamed frontier "Agent A" 45.0%, "Agent B" 58.0%.
  - Genie was 3 to 4 times faster and used less than half the tokens (about 432k vs 889k to 1.05M per run).
  - Setup: built with Turing on a fictional UK energy company in a live workspace with hundreds of tables (largest about 120M rows). Each agent ran 5 times per task.
  - Grading: a calibrated rubric with tolerances set in advance; a run passed only if it met every check.
  - The task count is not stated. Access to the benchmark requires contacting the Databricks account team.
  - The authors themselves caution that gaps may be smaller in new deployments, or for questions answerable from schemas alone.
  - Source: [Databricks blog](https://www.databricks.com/blog/genie-genie-ontology-delivers-16-accuracy-less-half-tokens)
- Earlier Databricks figures:
  - 84.5% first-try correct on a 28-question internal benchmark (June 2026), against a baseline of about 50% without ontology context. — [Databricks blog / search summary](https://www.databricks.com/blog/genie-genie-ontology-delivers-16-accuracy-less-half-tokens) (search summary; not confirmed on the fetched page)
  - "From 32% to over 90%" against "a leading coding agent" on an internal benchmark (May 2026). — [Colrows](https://colrows.com/blogs/cortex-analyst-vs-genie/)
- Snowflake Cortex Analyst:
  - "90%+ SQL accuracy" on an internal 150-question suite, vs 51% for single-shot GPT-4o (Aug 2024 engineering blog; dated). — [Colrows](https://colrows.com/blogs/cortex-analyst-vs-genie/); [Atlan](https://atlan.com/know/snowflake/cortex-analyst-vs-text-to-sql/) (search summary)
  - On BIRD-SQL, Claude 3.5 Sonnet reportedly rose from 57% to 78% when wrapped in a semantic model, against a human baseline of 93%. This is secondhand; the primary source was not found. — [Atlan](https://atlan.com/know/snowflake/cortex-analyst-vs-text-to-sql/) (search summary)
- Consultancy and independent evidence:
  - Syrencloud got 8 of 15 correct (53%) on a fresh Genie space, and 15 of 15 after metadata remodeling. — [Colrows](https://colrows.com/blogs/cortex-analyst-vs-genie/); [Syrencloud](https://syrencloud.com/?p=7319)
  - Kanerika estimates 60 to 70% real-world accuracy without a semantic model, vs about 90% in benchmarks. This is an estimate, not a study. — [Kanerika/Medium](https://medium.com/@kanerika/snowflake-cortex-a-practical-guide-to-cost-accuracy-and-fit-308f9356d53e)
  - In academic enterprise text-to-SQL benchmarks (Spider 2.0, BEAVER), raw frontier models solve 10 to 21% of tasks. — [Colrows](https://colrows.com/blogs/cortex-analyst-vs-genie/)
  - In one independent multi-join churn test by Claire Gouze, Genie was among the few tools that answered correctly. — [Agentic Fabriq](https://www.agenticfabriq.com/blog/cortex-analyst-vs-genie-accuracy) (search summary)
- Healthcare vendor outcome claims (not Q&A accuracy):
  - Lightbeam's "41% average relative reduction in avoidable admissions" (Mar 4, 2025):
    - The figure is an unweighted average of two clients: a rural Georgia Medicare ACO (39%, 130 admissions avoided) and an IDN's Medicaid members (43%, 65 avoided).
    - No denominators, no definition of "avoidable," no comparison group and no validator are given.
    - The model uses "more than 4,500 clinical and SDOH risk factors."
    - Source: [Lightbeam press release](https://lightbeamhealth.com/news/lightbeam-ai-model-enables-average-41-relative-reduction-in-avoidable-admissions/)
  - Arcadia said at Aggregate 2026 that care management productivity rose 75% and hospitalizations fell nearly 25% where its approach is used. This is vendor-reported. — [Arcadia: Aggregate 2026 takeaways](https://arcadia.io/resources/aggregate-2026-takeaways/) (search summary)
  - Palantir says Cleveland Clinic cut unused orthopedic OR time by 40% and raised transfer volume by 7.6%. This is vendor marketing. — [Palantir for Hospitals](https://www.palantir.com/offerings/palantir-for-hospitals/) (search summary)
- Wrong answers and human review:
  - Databricks calls Genie nondeterministic and recommends human curation, feedback and benchmarks.
  - Snowflake's evaluation feature measures regressions against human-verified queries.
  - Innovaccer offers only step transparency, and Arcadia offers assistive mode plus an audit trail.
  - Sources: [Colrows](https://colrows.com/blogs/cortex-analyst-vs-genie/); [Snowflake evaluations docs](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations); [Innovaccer](https://innovaccer.ai/blogs/population-health-copilot-2-0-is-now-faster-smarter-and-more-intuitive); [HIT Consultant](https://hitconsultant.net/2026/10/05/arcadia-launches-ascent-carejourney-lifescience-agentic-population-health-platform/)

### Inferences
- Across sources, a fresh deployment scores about 50 to 70% and a curated one about 90%. The work of curating metadata and verified queries is what moves accuracy, more than the choice of model.
- A competitor that can show measured accuracy on healthcare questions would stand alone. That means a held-out question set with supported-unknown handling and regression tracking. No healthcare vendor publishes this.

### Gaps
- No independent benchmark of Genie or Cortex Analyst on claims, EHR or quality-measure data was found.
- No hallucination or wrong-answer rate was found for any healthcare vendor's copilot.

## Q3. Pricing models and indicative prices

### Takeaway
Only the horizontal platforms publish natural-language Q&A pricing. Snowflake charges per message in AI credits, plus warehouse compute. Databricks Genie has no per-question AI charge, but you pay for SQL warehouse runtime. Healthcare vendors do not publish prices. The available estimates are unverified third-party figures in the $0.5M to $3M+ per year range for Health Catalyst. No public PMPM figures were found for any vendor.

### Cited Findings
- Snowflake Cortex Analyst is billed per message, roughly 6.7 credits per 100 messages, plus warehouse compute for the generated SQL. From April 1, 2026, AI usage is metered in a separate "AI Credit" at $2.00 per credit (global routing) or $2.20 (regional). — [Colrows](https://colrows.com/blogs/cortex-analyst-vs-genie/)
- Databricks Genie has no per-question AI charge. Customers pay for SQL warehouse runtime, including idle time. The Genie API free tier allows about 5 questions per minute, best effort. — [Colrows](https://colrows.com/blogs/cortex-analyst-vs-genie/)
- The Databricks Genie benchmark reports token use (about 432k per run) but no cost. — [Databricks blog](https://www.databricks.com/blog/genie-genie-ontology-delivers-16-accuracy-less-half-tokens)
- Health Catalyst publishes no prices. Third-party estimates conflict and give no methodology:
  - $1M to $3M+ per year for enterprise contracts.
  - "$500K+ per year" as a starting point.
  - $150K to $300K per year of professional services during an initial 18-month Analytics Accelerator engagement.
  - Sources: [MedAIVerdict](https://medaiverdict.com/tools/health-catalyst); [RevCycleAI](https://revcycleai.com/pro/resources/deep-dives/health-catalyst-vendor-deep-dive/); [ChallengingVoice](https://www.challengingvoice.com/tools/health-catalyst/) (search summary)
- Arcadia: a third-party review warns that organizations early in VBC maturity may struggle to get value that justifies enterprise platform costs. It gives no price. — [MedAIVerdict: Arcadia](https://medaiverdict.com/tools/arcadia-analytics) (search summary)

### Inferences
- Healthcare population health platforms are sold as enterprise subscriptions with significant professional services. Their natural-language Q&A is bundled, not priced separately. The marginal cost of a question on Snowflake is cents (6.7 credits per 100 messages at $2 is about $0.13 per message, plus compute).

### Gaps
- No public per-member-per-month pricing was found for Innovaccer, Arcadia, Lightbeam, Health Catalyst, Epic Healthy Planet or Cogito.
- Databricks DBU rates for the serverless SQL warehouses behind Genie were not retrieved.

## Q4. Implementation time, data onboarding requirements and common complaints

### Takeaway
On the horizontal platforms, the recurring complaint is the ongoing work of curating metadata: messy metadata leads to wrong answers. On the healthcare platforms, KLAS feedback is about cost, roadmap and staffing rather than AI quality. Health Catalyst has data-storage costs and an unclear roadmap, Arcadia has staff turnover and perceived lack of expertise, and Epic has a training burden.

### Cited Findings
- Onboarding requirements:
  - Genie needs Unity Catalog registration and a pro or serverless SQL warehouse.
  - A Databricks community practitioner says Genie fails when metadata is messy and needs ongoing work from people who understand the data.
  - Human curation is described as the "largest unbudgeted cost" for both Genie and Cortex Analyst.
  - Source: [Colrows](https://colrows.com/blogs/cortex-analyst-vs-genie/)
- KLAS health analytics report (May 2025):
  - Health Catalyst: customers report a strong history of success and responsive support. Nearly one-third plan to leave, mainly over data storage costs, and they perceive slower technology delivery and an unclear roadmap.
  - Innovaccer: customers use the most advanced capabilities on average and praise ease of use, training and staff.
  - Arcadia: satisfaction has improved through more proactive support, but customers cite perceived lack of expertise and staff turnover.
  - Epic: high-quality support and capable Cogito tools (Caboodle, SlicerDicer); training is the top barrier.
  - Oracle Health: poor service and difficult to use.
  - Dimensional Insight: highest overall satisfaction.
  - Source: [HIT Consultant summary of KLAS, May 2 2025](https://hitconsultant.net/2025/05/02/klas-report-which-health-analytics-vendors-deliver-results/)
- A KLAS user comment (May 2025) complains that Health Catalyst "charges for everything" on professional services. The KLAS vendor page shows a population health applications score of 75.0 from 5 comments; the period is unclear. — [KLAS comments](https://klasresearch.com/comments/health-catalyst-population-health-applications/92312); [KLAS Health Catalyst](https://klasresearch.com/vendor-ratings/health-catalyst/64186) (search summary)
- A third-party page cites KLAS 2025 population health scores of 80.6 for Health Catalyst (10 organizations, not ranked) and 79.7 for Arcadia. — [UsTechAutomations](https://ustechautomations.com/resources/blog/health-catalyst-vs-arcadia-2027) (search summary; low-quality source)
- Lightbeam:
  - KLAS shows an overall score of 87.6 (window Oct 2025 to Oct 2026), and a "Lightbeam AI" listing with few comments (about 19). — [KLAS Lightbeam](https://klasresearch.com/vendor-ratings/lightbeam/64945) (search summary; page metadata unreliable)
  - Best in KLAS for Remote Patient Monitoring (2025, 93.6) is a different product line. — [Lightbeam news](https://lightbeamhealth.com/news/lightbeam-health-awarded-best-in-klas-for-remote-patient-monitoring/) (search summary)
- Innovaccer was named a Leader in the inaugural Gartner Magic Quadrant for Healthcare Provider Industry Cloud Platforms (July 2026). — [BusinessWire](https://www.businesswire.com/news/home/20260729355311/en/Innovaccer-Named-a-Leader-in-the-Inaugural-Gartner-Magic-Quadrant-for-Healthcare-Provider-Industry-Cloud-Platforms) (search summary)
- Health Catalyst says moving its own analytics team to Ignite gave a 20% increase in analytics capacity and a 38% cut in storage and compute costs. This is a vendor case study. — [Health Catalyst case study PDF](https://downloads.healthcatalyst.com/success-stories/HCAT-Modernizing-Analytics-with-Ignite-a-Unified-Data-and-Analytics-Ecosystem-at-Health-Catalyst-case-study.pdf) (search summary)
- Arcadia claims Ascent connects systems "without typical integration overhead" but gives no timeline. — [HIT Consultant](https://hitconsultant.net/2026/10/05/arcadia-launches-ascent-carejourney-lifescience-agentic-population-health-platform/)

### Inferences
- An offering that ships with healthcare semantics already curated would remove the most-cited cost of horizontal natural-language Q&A: measures, attribution, readmission definitions and verified question sets.

### Gaps
- No published implementation timelines (weeks or months) were found for any vendor.
- G2 and Gartner Peer Insights reviews were not retrieved.
- No 2026 KLAS population health report was found publicly.

## Q5. Readmission and value-based-care specific features each claims

### Takeaway
The healthcare vendors all claim the VBC basics: care gaps, risk adjustment, attribution, contract and financial modeling, and CMS model support. 2026 additions are autonomous care-navigation and care-gap agents. Readmission is mostly handled by risk models (Epic Cogito/Cosmos, Lightbeam's avoidable-admission model). No vendor documents readmission analytics as a natural-language question workflow.

### Cited Findings
- Arcadia Ascent covers care-gap closure, risk adjustment, the actuarial and contract lifecycle, multi-payer reconciliation, episode analytics, leakage and steerage. Its readmission features are not mentioned. — [HIT Consultant](https://hitconsultant.net/2026/10/05/arcadia-launches-ascent-carejourney-lifescience-agentic-population-health-platform/)
- Innovaccer offers Atlas for VBC and CMS models, CMS ACCESS model participation, and Gravity agents for care-gap closure and population health. — [BusinessWire](https://www.businesswire.com/news/home/20260729355311/en/Innovaccer-Named-a-Leader-in-the-Inaugural-Gartner-Magic-Quadrant-for-Healthcare-Provider-Industry-Cloud-Platforms) (search summary)
- Lightbeam has a prescriptive AI model for avoidable admissions and ED use, support for new CMS models, and actuarial tools connected to Syntax and Wakely. Its press release does not mention readmissions specifically. — [Lightbeam press release](https://lightbeamhealth.com/news/lightbeam-ai-model-enables-average-41-relative-reduction-in-avoidable-admissions/); [Lightbeam HIMSS26 PDF](https://lightbeamhealth.com/wp-content/uploads/2026/03/Press-Release-LB-HIMSS-2026-Highlights.pdf) (search summary)
- Epic: Cogito tracks readmissions and quality, Cosmos-trained predictive models cover readmission, and Cosmos Curiosity (2026) flags readmission risk. — [Mindbowser](https://www.mindbowser.com/epic-cogito-executive-guide/); [MedCity News](https://medcitynews.com/2026/08/epic-ftc-agentic-ai/) (search summary)
- Komodo Health and NCQA have a multi-year collaboration to modernize quality measurement (Aug 2026). — [MobiHealthNews](https://www.mobihealthnews.com/news/komodo-health-ncqa-partner-accelerate-healthcare-quality-measurement) (search summary)

### Inferences
- Readmission and VBC "analytics" from these vendors means prebuilt models, worklists and contract tooling. Ad hoc, sourced answers to new readmission or VBC questions are not evidenced anywhere.

### Gaps
- Not found: Health Catalyst's specific 2026 readmission and VBC AI features, Cedar Gate entirely, and details of Optum's analytics AI.
- Not found: any vendor's chart- or document-reading accuracy for population analytics.
