# Healthcare AI / natural-language analytics vendors, 2025-2026: what is new since the competitive-bar report

Scope note: this file adds to `reports/Healthcare analytics MVP competitive bar.md` and does not repeat it. That report already covers Snowflake Cortex Analyst and Databricks Genie (verified queries, accuracy claims, pricing), Innovaccer Copilot 2.0 and Gravity's 50+ agents, Arcadia Ascent (Oct 2026), Lightbeam's copilots and its 41% claim, Health Catalyst's missing NL assistant, Epic Cogito Assistant and Cosmos Curiosity (UGM 2026, via Forbes), the 2025 KLAS analytics report, Bessemer and Menlo buyer data, and PMPM pricing estimates. Today is 2026-11-17. "Fetched" means I opened the page. "Snippet-only" means the claim came from a search-result summary that I did not open. Every vendor number is vendor-reported unless marked otherwise. Several primary pages (Fierce Healthcare, Becker's, Healthcare IT News, openai.com, snowflake.com) returned HTTP 403 to the fetcher, so claims from those outlets are snippet-only.

## Q1. Frontier labs: healthcare offerings, partnerships and named customers

### Takeaway
All three frontier labs, plus Google Cloud, now sell HIPAA-eligible healthcare workspaces or agents to hospitals and payers. Each pairs a BAA, a no-training-on-customer-data promise and reference-data connectors (CMS coverage, ICD-10, NPI) with administrative workflows: prior auth, appeals, claims, documentation. None is positioned or evidenced as governed population-health or VBC analytics, and none publishes accuracy for analytics-style questions. The frontier labs are becoming the model layer and the connector layer under incumbents (Optum on Claude; Innovaccer on Snowflake Cortex, Databricks and Bedrock; Google's agent offering a choice of Google, Anthropic and OpenAI models).

### Cited Findings
- **Anthropic Claude for Healthcare**
  - Launched **Jan 11, 2026** (JPM). HIPAA-ready offerings for providers and payers.
  - Connectors: CMS Coverage Database (LCD/NCD), ICD-10, NPI Registry and PubMed. Agent Skills: FHIR development and a sample prior-auth review skill.
  - Workflows: prior auth (checks clinical criteria against records and proposes a determination for payer review), claims appeals, and care-coordination triage.
  - Named healthcare customers and partners: Banner Health, Qualified Health, Elation Health, Viz.ai, Premier, Commure, Carta Healthcare, Brellium and Heidi Health.
  - Benchmark claims (MedCalc, MedAgentBench) appear only as charts, with no numbers in the text.
  - The page does not describe population or payer analytics.
  - — [Anthropic](https://www.anthropic.com/news/healthcare-life-sciences) (fetched)
- **Optum and Anthropic partnership, announced Jul 13, 2026** by Optum Insight CEO Sandeep Dadlani.
  - Focus areas: claims, RCM, billing-integrity checks against contracts, and an auditable "next best action" for care teams.
  - Sits inside UnitedHealth's **$3B AI program for 2026-2027**.
  - Named Optum products: Optum Real (claims) and Optum Integrity One (RCM, 100+ health-plan clients). UST is linking Claude into its CarePath claims and care-management platform.
  - Governance framing is "clinician-in-the-loop"; Optum says medical-necessity determinations are made only by physicians, and it disputes an ongoing MA post-acute denial class action.
  - — [HIT Consultant](https://hitconsultant.net/2026/07/13/optum-partners-anthropic-healthcare-ai/) (fetched)
  - TechTarget's view (snippet-only): the announcement was "light on details," with no use-case timeline and no contract value — [TechTarget](https://www.techtarget.com/healthtechanalytics/feature/Optum-Anthropic-team-up-on-AI-but-impact-on-healthcare-is-still-unclear).
- **Unverified Claude customer claims (snippet-only, secondary sources; do not use without checking):**
  - Banner Health has 22,000 clinical providers using Claude, and 85% "report working faster."
  - Elation Health reports "61% faster chart insights."
  - An Ascension partnership exists.
  - — [search summary of Becker's/secondary sources](https://www.beckershospitalreview.com/healthcare-information-technology/ai/anthropic-rolls-out-claude-for-healthcare/)
- **OpenAI for Healthcare**
  - Launched **Jan 8-9, 2026**, one day after consumer ChatGPT Health. Includes ChatGPT for Healthcare and a healthcare-focused API.
  - Features: medical literature with citations, connections to institutional guidelines, and documentation templates (discharge summaries, prior-auth support).
  - Governance: BAA, data residency, audit logs, customer-managed keys, and no training on customer content.
  - Named hospitals: Boston Children's, MSK, Stanford Medicine Children's, Cedars-Sinai, UCSF, HCA, AdventHealth and Baylor Scott & White. API customers: Abridge, Ambience and EliseAI.
  - The Decoder notes the announcement "doesn't specify how extensively these institutions are actually using the products."
  - No benchmarks or pricing were published.
  - — [The Decoder](https://the-decoder.com/openai-launches-healthcare-product-line-signs-up-major-us-hospitals/) (fetched)
- **OpenAI update, Sep 1, 2026:** organisations can connect EHR and industry data to ChatGPT, including an **Epic integration** and a **"Healthcare Public Data" plugin** — [OpenAI](https://openai.com/index/chatgpt-connects-health-records-and-healthcare-sources) (snippet-only; 403 on fetch).
- **Google Cloud "Gemini for Healthcare"**
  - Announced **Nov 10, 2026**, in preview for Gemini Enterprise Standard and Plus customers.
  - Prebuilt skills: records synthesis, pre-visit preparation, coverage and policy review, and payer benefit determination.
  - Connectors:
    - EHRs: Epic and Oracle Health, both **read-only with no write-back**.
    - Reference data: CMS Coverage, NPI, RxNorm, ICD-10 and the IMO Health knowledge graph.
    - Revenue cycle: Optum eligibility tools, Waystar, Trizetto and others.
    - Evidence: Optum EvidenceVue and ASCO guidelines.
    - Analytics workflows: Alteryx.
  - Analytics mention: Stanford researchers can query OMOP in plain language. There is no BigQuery or population-analytics claim.
  - Launch customers: BCBS Massachusetts, Hartford HealthCare, HCA, Highmark, Northwell, Optum and Stanford School of Medicine.
  - Pricing: per-user seats or pay-as-you-go.
  - Governance: per-agent identity inheriting user RBAC, audit logs, "verifiable source citations," and model choice across Google, Anthropic and OpenAI with "smart routing" between cheap and high-reasoning models.
  - **No performance or outcome metrics.**
  - — [Google Cloud press release](https://www.googlecloudpresscorner.com/2026-11-10-Google-Cloud-Launches-Gemini-for-Healthcare) (fetched)
- **Microsoft (Fabric and Dragon Copilot)**
  - Fabric "healthcare data solutions" harmonises healthcare data (FHIR pipelines) for analytics. Its Dragon (DAX) Copilot data transformation, which lands ambient transcripts in a lakehouse for Power BI and ML, is still listed as **preview**.
  - I found no 2026 Fabric healthcare analytics agent.
  - — [Microsoft Learn](https://learn.microsoft.com/en-us/industry/healthcare/healthcare-data-solutions/overview) (snippet-only)
  - KLAS names Microsoft (Nuance/Dragon) the leader in ambient speech, followed by Abridge and Oracle Health — [HIT Consultant on KLAS Healthcare AI Update 2025](https://hitconsultant.net/2025/12/29/klas-report-why-hospitals-are-choosing-efficiency-over-agentic-ai-hype-in-2025/) (fetched).

### Inferences
- Every lab's healthcare launch shares the same pattern: BAA, no training, reference-code connectors, prior auth, appeals and documentation. Governed analytics over a customer's claims and EHR warehouse, with defined measures and an accuracy record, is not in any lab's offer. Agere's layer sits above these models and is complementary to them, not displaced by them.
- Google's "smart routing between fast/low-cost and high-reasoning models" and its "verifiable source citations" are now marketed features at the platform level. Tiered routing and citations alone will not differentiate Agere. Verified recipes, governed definitions, abstention and measured accuracy still can.
- A lab-backed incumbent (for example Optum with Claude, or Innovaccer with Cortex) is the most likely competitor shape. Buyers will see "built on Claude/Gemini/GPT" as table stakes.

### Gaps
- No lab publishes healthcare analytics or text-to-SQL accuracy. Anthropic's benchmark numbers are in charts I could not read in text.
- The OpenAI Sep 2026 Epic integration and Healthcare Public Data plugin were not opened (403). Their scope (read-only? which data?) is unverified.
- No contract values or pricing for Claude for Healthcare or ChatGPT for Healthcare were found.

## Q2. EHR vendors: Epic, Oracle Health, MEDITECH

### Takeaway
Epic now publishes usage and outcome statistics at scale. The most analytics-relevant figure is **SlicerDicer SideKick, which cut query-building time by up to 85% at RWJBarnabas**, alongside 20 early-adopter customers for Cosmos Curiosity. None is an accuracy metric. Oracle Health's 2025-2026 AI is documentation and payer-rule-aware RCM agents, and several of those agents are still labelled "planned." No MEDITECH analytics AI was found.

### Cited Findings
- **Epic UGM 2026 statistics** (Healthcare IT Today, Aug 20, 2026; Epic-reported):
  - Usage: about 1.4M clinicians assisted by AI each month. Monthly generative-AI activity grew from 50M (Jul 2025) to 2B (Jul 2026).
  - Analytics: **SlicerDicer SideKick cut query creation time by up to 85% (RWJBarnabas)**. 20 customers are early adopters of **Cosmos Curiosity**. Look-Alikes were used 25,000+ times. Cosmos Median Length of Stay is used on about 1M admissions a month.
  - RCM: the Professional Billing Coding Assistant cut coding-related denials by 33% across 160 organisations.
  - Cosmos scale: 320M patients and 23B encounters.
  - "Best Care Choices" is reported as live at both 60+ and 56 organisations (internally inconsistent).
  - The article has no payer-platform or population-health metrics.
  - — [Healthcare IT Today](https://www.healthcareittoday.com/2026/08/20/great-stats-from-epic-ugm/) (fetched)
- **Epic Agent Factory** (HIMSS26, March 2026) is a drag-and-drop builder for health systems' own agents inside Epic workflows. The assistants are Art (clinicians), Emmie (patients) and Penny (RCM). The CoMET models are pre-trained on 300M records and 16B events — [Fierce Healthcare](https://www.fiercehealthcare.com/ai-and-machine-learning/himss26-epic-expands-ai-roadmap-previews-factory-build-and-orchestrate-ai) (snippet-only; 403).
- **Oracle Health**
  - Payer-rules-aware agents (prior auth, eligibility, coding, claims) were announced Sep 11, 2025 — [Oracle](https://www.oracle.com/news/announcement/oracle-health-to-enable-accelerated-payer-provider-collaboration-2025-09-11/) (snippet-only).
  - An RCM AI portfolio followed on Sep 23, 2026, with some features labelled "planned" — [Oracle](https://www.oracle.com/news/announcement/oracle-health-advances-revenue-cycle-management-with-ai-2026-09-23/) (snippet-only).
  - Oracle says its Clinical AI Agent has saved 400,000+ documentation hours. A nursing version became generally available in Sep 2026 — [HIT Consultant](https://hitconsultant.net/2026/09/14/oracle-health-launches-clinical-ai-agent-for-nurses-inpatient-ehr-documentation/) (snippet-only).
  - The closest Oracle product to analytics is Health Data Intelligence, which ingests payer-provided insights for care gaps (snippet-only).
- **MEDITECH:** no 2025-2026 NL analytics product found (no search hits; not separately searched beyond the EHR query).

### Inferences
- Epic's measurable analytics claim is analyst speed ("query creation time"), not answer correctness. That is the same evidence gap the earlier report found, and Epic's scale now makes "faster queries" a commodity claim. Agere's differentiator remains measured correctness and abstention, together with cross-source scope (claims plus non-Epic data) that Epic-bound tools lack.
- Read-only EHR access for third-party agents (Google) and Epic's own Agent Factory suggest Epic will control in-workflow action. Agere's work and follow-up layer should plan to hand off to the EHR rather than compete inside it.

### Gaps
- No primary Epic page on Cosmos Curiosity or Cogito Assistant capabilities or accuracy was opened. Fierce, Becker's and HITN returned 403.
- MEDITECH was not researched in depth.

## Q3. Population health, VBC, payer-analytics platforms: changes in 2025-2026

### Takeaway
New since the earlier report:
- Innovaccer has formalised the **frontier and data-platform stack under its product**: Snowflake Cortex text-to-SQL (Jan 2026), Databricks Built-On validation (Mar 2026) and AWS Bedrock (Jun 2026).
- Komodo's **Marmot** produced the clearest third-party-anchored analytics outcome: NCQA tested a new HEDIS measure in about one month versus a historical 2-3 months.
- KLAS's 2026 VBC enablement report rewards service and operationalised insight, not AI.

No vendor in this segment published Q&A accuracy.

### Cited Findings
- **Innovaccer**
  - Jan 27, 2026: Gravity runs on Snowflake, and Cortex AI provides "natural-language analytics through text-to-SQL" and retrieval-augmented insights — [BusinessWire/Innovaccer](https://www.businesswire.com/news/home/20260127889694/en/Innovaccer-and-Snowflake-Partner-to-Drive-Enterprise-AI-Adoption-Across-Healthcare) (snippet-only).
  - Mar 12, 2026: Databricks "Built On" validation — [HLTH](https://hlth.com/insights/news/innovaccer-achieves-databricks-built-on-validation-to-scale-enterprise-healthcare-ai-2026-03-12) (snippet-only).
  - Jun 26, 2026: multi-year AWS collaboration (Bedrock) — [HLTH](https://hlth.com/insights/news/innovaccer-and-aws-sign-multi-year-strategic-collaboration-to-scale-ai-deployment-in-healthcare) (snippet-only).
  - Jul 2026: Gravity for Infusion — [HIT Consultant](https://hitconsultant.net/2026/07/17/innovaccer-launches-gravity-for-infusion-ai-platform/) (snippet-only).
  - Secondary claims of coding-accuracy gains at Kaiser and Ascension, and of prior-auth time falling from 45 minutes to under one, could not be traced to a primary release. Treat them as unverified.
- **Komodo Health**
  - Marmot (the successor to MapAI) is an "AI analytics platform" on a Healthcare Map of more than 330M de-identified patient journeys, "designed for transparency, auditability, and reproducibility."
  - Aug 26, 2026: NCQA will use Marmot to develop, validate and refresh HEDIS measures. The first is COF-E (follow-up after a positive non-invasive colorectal screening test), for MY2027. NCQA tested it in about one month across MA, Medicaid and commercial, versus a historical 2-3 months.
  - No accuracy or validation metrics were published.
  - — [Komodo press release](https://www.komodohealth.com/?p=21815) (fetched)
  - Expanded Alnylam deployment of Marmot, Jun 25, 2026 — (snippet-only, via search summary).
- **Cotiviti**
  - AI-enabled Proactive COB, Aug 13, 2026: other-insurance detection at enrollment — [Cotiviti](https://www.cotiviti.com/press-release/cotiviti-introduces-ai-enabled-proactive-cob-to-resolve-coordination-of-benefits-issues-at-enrollment) (snippet-only).
  - AI Contract Intelligence, Aug 2026 (snippet-only).
  - These are payment-integrity products, not NL analytics.
- **Inovalon:** no 2026 AI analytics launch found. Converged Risk on Inovalon ONE; integrated with Edifecs after the 2025 acquisition (snippet-only).
- **Aledade**
  - Serves about 1 in 5 new MSSP participants and nearly 20% of MSSP in 2026. Revenue passed $1B in 2025.
  - Its AI and analytics are embedded in EHR workflows. No NL analytics product was found.
  - — [Fierce Healthcare](https://www.fiercehealthcare.com/providers/aledade-grows-value-based-care-network-3000-primary-care-practices) (snippet-only)
- **KLAS Value-Based Care Enablement Services 2026** (Jan 29, 2026):
  - Aledade 95.7 (first); Guidehealth 87.5; Signify Health 77.6, whose analytics were "broad but insufficiently actionable" with "unclear accountability."
  - Pearl Health was praised for technology but asked to be more consultative.
  - The article never mentions AI. Winners "operationalize insights" with hands-on service. Sample size not stated.
  - — [HIT Consultant](https://hitconsultant.net/2026/01/29/klas-report-value-based-care-enablement-winners-2026/) (fetched)
- **Navina:** latest round found is a $55M Series C (Mar 2025, Goldman Sachs; $100M total). Customers include providers, ACOs, MSOs and plans, and it is integrated into agilon's 2,800-PCP network. No 2026 round found — [HIT Consultant](https://hitconsultant.net/2025/03/25/navina-secures-55m-to-expand-ai-powered-clinical-intelligence) (snippet-only).
- **Milliman:** 2026 output is LEAD and ACO modelling guidance, with an ACO Builder using data through Oct 2025. No MedInsight AI product announcement was found — [Milliman](https://www.milliman.com/pt-BR/insight/5-key-issues-acos-in-2026) (snippet-only).
- **Lightbeam and Health Catalyst:** nothing new found beyond the earlier report.
  - Health Catalyst's 2025 year in review lists 10 AI-integrated data toolkits on the Databricks Marketplace, plus HITRUST r2 — [HC Innovation Group](https://www.hcinnovationgroup.com/finance-revenue-cycle/article/55336749/new-leadership-partnerships-and-platforms-mark-health-catalysts-transformative-year) (snippet-only).
- **Optum:** see Q1 (Anthropic). Optum is also a Google Gemini for Healthcare launch customer and connector supplier (EvidenceVue, eligibility) — [Google Cloud](https://www.googlecloudpresscorner.com/2026-11-10-Google-Cloud-Launches-Gemini-for-Healthcare) (fetched).

### Inferences
- The Komodo and NCQA result is the best market template for an Agere claim: a named, credible measure steward, a concrete workflow (measure testing) and a time-to-answer baseline (2-3 months to about 1 month). It still has no correctness metric. Agere could pair the same kind of time claim with a correctness and abstention scorecard.
- Innovaccer's NL analytics is now literally Snowflake Cortex text-to-SQL. Its accuracy should therefore be expected to track Cortex's curation-dependent behaviour from the earlier report: high with verified queries, low on fresh semantic models. This is an inference, not measured.
- KLAS evidence keeps saying buyers reward actionability and service over technology. Agere's work and follow-up binding and its onboarding (curation as a service) matter as much as accuracy.

### Gaps
- No Innovaccer, Arcadia, Komodo, Cotiviti or Inovalon accuracy figures exist publicly.
- The KLAS 2026 population-health or analytics report (if any) was not found. Only the VBC enablement and AI-update reports were located.
- Pricing for Marmot and Gravity was not found.

## Q4. Data and cloud platforms with healthcare offerings

### Takeaway
AWS made Amazon Quick (Quick Sight BI, chat agents, Flows) **HIPAA-eligible**, with ACL-aware retrieval, row- and column-level security and chat logging, and published a health-plan deployment walkthrough on Oct 22, 2026. It also launched Amazon Connect Health (Mar 2026) with a "Patient Insights" care-gap preview. Snowflake's healthcare story is customer deployments (Zelis on Snowflake Intelligence) and Innovaccer. No healthcare-specific Genie or Cortex accuracy evidence was found.

### Cited Findings
- **AWS Amazon Quick** (Oct 22, 2026 blog):
  - HIPAA-eligible across web, desktop and mobile under a BAA. Includes Quick Sight BI (SPICE engine), Flows, Research and chat agents.
  - Governance: document-level ACLs at query time; row- and column-level security for structured BI; CloudWatch logs of chat conversations and user feedback (CloudTrail does not capture chat content); "deterministic guardrails"; no training on customer data.
  - Warns that user feedback is not encrypted with a customer-managed key, so it should be treated as a possible PHI surface.
  - Uses a fictional health plan as the example. No accuracy or pricing figures.
  - — [AWS blog](https://aws.amazon.com/blogs/machine-learning/deploy-ai-in-a-hipaa-eligible-environment-with-amazon-quick/) (fetched; page partly truncated)
- **Amazon Connect Health** (Mar 5-6, 2026): agentic AI for scheduling, documentation and patient verification, integrating with HealthLake. A "Patient Insights" preview builds clinical narratives and **care gaps from fragmented member records**. AWS also offers an open-source HealthLake MCP Server — [TechCrunch](https://techcrunch.com/2026/03/05/aws-amazon-connect-health-ai-agent-platform-health-care-providers/); [AWS](https://aws.amazon.com/blogs/industries/introducing-amazon-connect-health-agentic-ai-for-healthcare-built-for-the-people-who-deliver-it) (snippet-only).
- **Snowflake**
  - Zelis is deploying agents with Snowflake Intelligence "to democratize healthcare fintech insights and reduce data team workloads" — [Snowflake blog](https://www.snowflake.com/en/blog/ai-agents-healthcare-efficiency/) (snippet-only; 403; no numbers seen).
  - Snowflake and Hakkōda survey (Mar 10, 2026; 183 US provider and payer leaders): 65% have adopted, are experimenting with, or plan agentic AI within 12 months — (snippet-only via search).
- **Databricks:** no 2026 healthcare-specific Genie announcement found. Innovaccer and Health Catalyst build on it (see Q3).
- **Microsoft Fabric:** see Q1. Healthcare data solutions exist; the Dragon transcript-to-lakehouse capability is in preview.
- **Google:** see Q1 (Gemini for Healthcare). Plain-language OMOP querying is cited only as a Stanford research use.

### Inferences
- Platform-level governance (RBAC, row-level security, chat logs, BAA) is now standard on every hyperscaler BI and agent product. These are deployment prerequisites Agere must match, not differentiators.
- AWS's "care gaps from fragmented member records" preview is the first hyperscaler feature aimed at payer population work. Watch it, but it is a preview with no published evidence.

### Gaps
- No healthcare-domain accuracy evaluation of Amazon Quick, Snowflake Intelligence or Genie was found.
- The AWS health-plan walkthrough details sat in the truncated part of the page.

## Q5. Funded startups 2025-2026 (health-plan or hospital analytics with LLMs)

### Takeaway
2026 funding has gone overwhelmingly to **LLM claims, payment-integrity and risk-adjustment automation** (Alaffia, Daffodil, Anterior, Keebler), not to governed NL analytics over plan or ACO data. I found no 2025-2026 funded startup positioned as verified natural-language VBC analytics.

### Cited Findings
- Alaffia Health: $55M Series B (Feb 3, 2026); agentic AI for health-plan claims operations — [SiliconANGLE](https://siliconangle.com/2026/02/03/alaffia-health-raises-55m-expand-agentic-ai-health-plan-claims-operations/) (snippet-only).
- Daffodil Health: $16.3M Series A (Feb 2026, Flare Capital); LLM-driven claims automation for plans and TPAs — [MobiHealthNews](https://www.mobihealthnews.com/news/daffodil-health-raises-163m-ai-claims-automation) (snippet-only).
- Handl Health: $14.2M Series A (Feb 24, 2026); network analytics and alternative plan design for employer plans; LLM use not confirmed — [Axios Pro](https://www.axios.com/pro/health-tech-deals/2026/02/24/exclusive-handl-health-raises-142m-for-health-plan-ai) (snippet-only).
- Keebler Health: $16M Series A (Apr 2026); "LLM-native" risk adjustment for VBC organisations — [Let's Data Science](https://letsdatascience.com/news/keebler-health-secures-16m-series-a-financing-8285ff1b) (snippet-only).
- Anterior: $40M (2026); clinician-led AI for health-plan teams (utilisation management) — [Pulse 2.0](https://pulse2.com/anterior/amp/) (snippet-only).
- An AI insights platform for payers raised $5M (company and date not verified) — [MobiHealthNews](https://www.mobihealthnews.com/news/ai-insights-platform-payers-raises-5m) (snippet-only).

### Inferences
- Investors are funding narrow, ROI-countable admin automation. Agere's analytics pitch must translate into the same countable unit: analyst hours, measure-testing time, reconciled dollars.

### Gaps
- The fetcher could not open the snippet-only funding articles. Round details and products need checking.
- Crunchbase-style coverage of small analytics-copilot startups was not searched.

## Q6. Buyer evidence and independent reviews (new since the earlier report)

### Takeaway
Independent 2025-2026 evidence says buyers are still governance-first and admin-first. Agentic AI is barely deployed, and validation infrastructure, not tool supply, is the bottleneck. Inconsistent metric definitions across departments is a top-cited barrier, which supports Agere's governed-definitions design.

### Cited Findings
- **KLAS Healthcare AI Update 2025** (about Dec 29, 2025; 3,000+ respondents, 1,700+ organisations):
  - Top use categories: patient engagement 36%, RCM 24%, clinical efficiency 22%.
  - **Only 17 respondents mentioned agentic AI, and one organisation was using it.** Barriers were untrustworthy data and governance.
  - Microsoft and Epic are the most used and considered vendors. "Governance first, deployment second."
  - No analytics-specific statistics.
  - — [HIT Consultant](https://hitconsultant.net/2025/12/29/klas-report-why-hospitals-are-choosing-efficiency-over-agentic-ai-hype-in-2025/) (fetched)
- **CCM at UPMC and KLAS, "Validation and Trust"** (Aug 6, 2026; 27 health systems):
  - Deployment and testing: 93% have deployed third-party AI; 92% run pre-deployment testing; **only 44% have a dedicated sandbox or data platform to validate accuracy, safety and drift**.
  - Strategy maturity: 63% of AI strategies are developing or ad hoc; 4% are advanced.
  - Barriers (respondent counts): manual workarounds and spreadsheets 17; **inconsistent metrics or data definitions across departments 14**; talent 11; unstructured data 10.
  - UPMC's Ahavi engine evaluates third-party algorithms on de-identified populations.
  - — [HIT Consultant](https://hitconsultant.net/2026/08/06/ccm-klas-research-health-system-ai-governance-testing-report/) (fetched)

### Inferences
- Over half of systems lack a validation sandbox. An Agere deliverable that is itself a validation harness (a frozen question set, replayable recipes and a scorecard on the buyer's own data) addresses a stated infrastructure gap, not just a product preference.
- "Inconsistent metric definitions" (14 of 27) maps directly onto governed definitions and customer definition onboarding.

### Gaps
- No KLAS report rating NL analytics or copilots on accuracy exists that I could find.

## Q7. Where Agere could fill gaps (synthesis of Q1-Q6)

### Takeaway
The 2026 market has converged on frontier models plus healthcare connectors plus governance plumbing (BAA, RBAC, audit logs, citations, model routing). Labs and hyperscalers sell this for admin workflows, and incumbents resell it as copilots. Still missing everywhere:
1. Published correctness or abstention evidence for analytics answers.
2. Governed VBC definitions (attribution, TCOC/PMPM, RAF V28, measures) shipped pre-curated and reconcilable.
3. A validation harness buyers can run themselves.
4. Cross-source (claims plus EHR plus notes) population answers that are not locked to one EHR or one cloud.

### Cited Findings
- Labs and Google publish no performance metrics for their healthcare agents — [Google Cloud](https://www.googlecloudpresscorner.com/2026-11-10-Google-Cloud-Launches-Gemini-for-Healthcare); [The Decoder on OpenAI](https://the-decoder.com/openai-launches-healthcare-product-line-signs-up-major-us-hospitals/); [Anthropic](https://www.anthropic.com/news/healthcare-life-sciences) (all fetched).
- Epic's analytics evidence is query-building speed (up to 85%), not correctness — [Healthcare IT Today](https://www.healthcareittoday.com/2026/08/20/great-stats-from-epic-ugm/) (fetched).
- Komodo shows time-to-measure-testing (about 1 month vs 2-3), not accuracy — [Komodo](https://www.komodohealth.com/?p=21815) (fetched).
- Only 44% of systems have a validation sandbox; 14 of 27 cite inconsistent definitions — [HIT Consultant on CCM/KLAS](https://hitconsultant.net/2026/08/06/ccm-klas-research-health-system-ai-governance-testing-report/) (fetched).
- KLAS VBC winners "operationalize insights"; Signify's analytics were "broad but insufficiently actionable" — [HIT Consultant on KLAS](https://hitconsultant.net/2026/01/29/klas-report-value-based-care-enablement-winners-2026/) (fetched).

### Inferences
- **Gap 1, evidence:** be the first vendor with a published, held-out, abstention-scored VBC scorecard, plus an analyst-time baseline in Epic's and Komodo's style. The time claim matches what the market already reports; the correctness claim is what nobody reports.
- **Gap 2, model-agnostic governance layer:** labs now resell each other's models (Google routes across Anthropic and OpenAI). Agere's verified recipes and governed definitions can sit above any of them and run in the buyer's warehouse. Position this as complementary to Claude, Gemini and GPT for Healthcare rather than as a rival model product.
- **Gap 3, validation harness as product:** the CCM/KLAS 44% sandbox gap means "run our scorecard on your extract" can be the pilot itself.
- **Gap 4, payer and VBC analytics specifically:** every frontier launch targets prior auth, claims and documentation. Population TCOC, attribution reconciliation and measure development (Komodo and NCQA show demand) are underserved by LLM-native products.
- **Gap 5, read-only, decision-support posture:** Google's read-only EHR access and Optum's clinician-in-the-loop framing show the market rewards non-autonomous design. Agere's supported unknowns and human follow-up fit this posture.
- Threat: incumbents can add verified-query curation quickly, because Innovaccer runs on Cortex and Cortex has verified queries. Agere's moat would then be pre-curated VBC recipes plus published evidence, not the mechanism itself.

### Gaps
- No public price points for lab healthcare offerings, Marmot or Gravity.
- No independent head-to-head of any healthcare NL analytics product.
- HLTH 2026 (Nov 15-18) announcements could not be captured; this is a likely blind spot for the newest launches.
