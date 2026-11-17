# PHI terms of frontier AI labs and cloud hosts, and US regulatory context for AI in hospital and health-plan analytics

Research date: 2026-11-17. **This is not legal advice.** It summarizes public vendor pages and regulatory sources so the owner can see the shape of the options. Contract terms (BAA text, ZDR addenda, Product Terms) control, and they change often. Counsel and each vendor's sales or compliance team must confirm before any PHI flows.

Method note: pages marked **[opened]** were fetched and read during this research. Claims marked **[snippet-only]** come from search-result summaries or secondary sources that were not opened, or opened but returned 403. Several official pages blocked automated fetch (OpenAI help center and usage policies, openai.com/enterprise-privacy, HHS Section 1557 page, CA leginfo, Akin Gump). Those claims are flagged.

## Q1. Anthropic: BAA, ZDR, training, residency, excluded features

### Takeaway
Anthropic offers a BAA for Claude Enterprise and the first-party (1P) API. Coverage is listed feature by feature, and several API features are explicitly *not* covered: Batch, Files (beta), Skills (beta), Code Execution and Web Fetch. Under a BAA, "Covered Models" require 30-day retention and cannot use ZDR unless Anthropic authorizes it. US-only inference costs 1.1x.

### Cited Findings
- **BAA mechanics.** Enterprise Primary Owners accept the BAA in "Data and privacy" settings. For the 1P API the Primary Owner signs a BAA and contacts Sales to enable it. The BAA covers only the organization that accepted it. — [Anthropic Privacy Center: BAAs for commercial customers](https://privacy.claude.com/en/articles/8114513-business-associate-agreements-baa-for-commercial-customers) [opened]
- **Covered 1P API features.** Messages API (including prompt caching, structured outputs, memory, web search, bash and text editor tools), Token Counting, Models, Org Management, Compliance and Computer Use APIs. Messages API feature coverage applies to "versions of the BAA accepted after 4/1/26". — [same page](https://privacy.claude.com/en/articles/8114513-business-associate-agreements-baa-for-commercial-customers) [opened]
- **Not covered on the API.** Batch, Files (beta), Skills (beta), Code Execution, Web Fetch. — [same page](https://privacy.claude.com/en/articles/8114513-business-associate-agreements-baa-for-commercial-customers) [opened]
- **Covered in Enterprise chat.** Chat, Voice, Web Search, Research, and Projects and Artifacts (within chat only). File creation and code execution are covered, excluding network access and external websites. Enterprise feature coverage applies to BAA versions accepted after 12/2/25. — [same page](https://privacy.claude.com/en/articles/8114513-business-associate-agreements-baa-for-commercial-customers) [opened]
- **Not covered at all.** Connectors, MCP servers, desktop extensions, Claude in Chrome, Enterprise Search, skills and plugins, Cowork in the cloud, Claude Code cloud sessions, the mobile app, and beta products. Claude Code over Bedrock, Vertex or Foundry routes, or through LLM gateways, is not covered by Anthropic's BAA. — [same page](https://privacy.claude.com/en/articles/8114513-business-associate-agreements-baa-for-commercial-customers) [opened]
- **ZDR conflict for "Covered Models".** The BAA page says Covered Models "require 30-day data retention and aren't available with zero data retention (ZDR) enabled" unless Anthropic expressly authorizes otherwise. — [same page](https://privacy.claude.com/en/articles/8114513-business-associate-agreements-baa-for-commercial-customers) [opened]. The data-residency doc also marks ZDR as "excludes Covered Models". — [Claude docs: Data residency](https://platform.claude.com/docs/en/manage-claude/data-residency) [opened]
- **Default retention.** API inputs and outputs are deleted "within 30 days of receipt or generation". Exceptions: services the customer controls (such as the Files API), other agreements, Usage Policy enforcement (up to 2 years for violations, trust and safety scores up to 7 years) and legal requirements. ZDR is "available by agreement". Covered Models carry limited retention and review for safety work. Page dated July 1, 2026. — [Anthropic Privacy Center: How long do you store my organization's data](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data) [opened]
- **Training default.** The retention page does not address training. Press coverage of the January 2026 Claude for Healthcare launch quotes Anthropic: "we do not use user health data to train models". — [Fierce Healthcare](https://www.fiercehealthcare.com/ai-and-machine-learning/jpm26-anthropic-launches-claude-healthcare-targeting-health-systems-payers) [snippet-only]. Anthropic's commercial-terms training position was not confirmed on a page opened in this research. **Gap.**
- **Data residency.** `inference_geo` takes "global" (default) or "us" per request, with workspace-level `allowed_inference_geos` and `default_inference_geo`. Workspace geo (storage at rest and endpoint processing) is set at creation, and "us" is currently the only option. US-only inference on Claude 4.6+ models is priced at **1.1x** across all token categories. It is supported on Claude 4.6 and later; Opus 4.5 and earlier return a 400. On Bedrock and Google-managed Vertex the region comes from the endpoint or inference profile. On Foundry, the US Data Zone Standard deployment type applies, also at 1.1x. Data residency is listed as HIPAA-eligible. — [Claude docs: Data residency](https://platform.claude.com/docs/en/manage-claude/data-residency) [opened]
- **Claude for Healthcare.** Launched around January 11–12, 2026 at JPM. Described as HIPAA-ready tooling for providers and payers, with connectors such as the CMS Coverage Database and ICD-10. — [Fierce Healthcare](https://www.fiercehealthcare.com/ai-and-machine-learning/jpm26-anthropic-launches-claude-healthcare-targeting-health-systems-payers), [Anthropic news](https://anthropic.com/news/healthcare-life-sciences) [snippet-only, not opened]. **Conflict:** press reports say the healthcare connectors are "HIPAA-compliant", but the current BAA page lists Connectors and MCP servers as *not covered*. Trust the BAA page and confirm with Anthropic.

### Inferences
- For Agere's server-side text-to-SQL and note reading, the covered path is the 1P **Messages API** under a signed BAA, with prompt caching and structured outputs allowed. Agere must **not** route PHI through the Batch API, Files API, Code Execution or Web Fetch. That rules out a "cheap overnight batch note-reading" design on the 1P API unless Anthropic extends coverage.
- If Agere's chosen model is a "Covered Model", expect 30-day retention even with a BAA. Hospital security reviews that demand ZDR will need an Anthropic exception, a non-covered model, or a hyperscaler route. The Bedrock page (Q4) lists Claude Fable 5.x as retaining all traffic for 30 days, which suggests Fable is one such model. This is an inference; the Covered Models list on Anthropic's API and data retention page was not opened.
- Budget about 10% extra for US-only inference, which many health customers will require.

### Gaps
- The exact Covered Models list and the full feature-eligibility table ([api-and-data-retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)) were not opened.
- No primary source was opened for commercial training defaults.

## Q2. OpenAI: API BAA, ZDR/MAM, ChatGPT Enterprise / for Healthcare

### Takeaway
OpenAI API data is not used for training by default. A BAA is available for approved API customers. ZDR and Modified Abuse Monitoring (MAM) require sales approval, and many stateful endpoints (Assistants, Files, vector stores, Batch, fine-tuning, evals, Conversations) are not ZDR-eligible. ChatGPT for Healthcare (January 2026) is the BAA-eligible ChatGPT product. Consumer ChatGPT Health is not.

### Cited Findings
- **Training default.** API data is not used to train or improve models unless the customer opts in (since March 1, 2023). Abuse-monitoring logs are kept up to 30 days by default. — [OpenAI API docs: Your data](https://developers.openai.com/api/docs/guides/your-data) [opened]
- **ZDR and MAM.** Both need prior OpenAI approval via sales. Once approved, they are configured per organization or project under Data controls. ZDR also forces `store=false` on `/v1/responses` and `/v1/chat/completions`. — [same page](https://developers.openai.com/api/docs/guides/your-data) [opened]
- **ZDR-eligible endpoints.** `/v1/responses`, `/v1/chat/completions`, `/v1/embeddings`, `/v1/completions`, `/v1/moderations`, `/v1/audio`, `/v1/realtime`, `/v1/decisions`, and `/v1/images` for some models. **Not ZDR-eligible:** Conversations, ChatKit threads, Agents, Assistants and Threads, vector stores, files, fine-tuning, evals, batches. Extended prompt caching may hold KV tensors on GPU-local storage for up to 24 hours. — [same page](https://developers.openai.com/api/docs/guides/your-data) [opened]
- **Web search under a BAA.** Live web search is not HIPAA-eligible or BAA-covered. Offline mode (`external_web_access: false`) on the Responses `web_search` tool can be covered in a ZDR-enabled project. — [same page](https://developers.openai.com/api/docs/guides/your-data) [opened]
- **Retained data under a BAA.** Customers with a BAA and "Private Retention with PSP" can process PHI on BAA-eligible endpoints even when data is retained. The Decisions API is HIPAA-eligible under an executed "Business Associate and Healthcare Addendum". — [same page](https://developers.openai.com/api/docs/guides/your-data) [opened]
- **BAA request path.** OpenAI's help article on getting a BAA returned 403. Secondary sources say API BAAs are approved case by case via baa@openai.com. — [OpenAI Help Center](https://help.openai.com/en/articles/8660679-how-can-i-get-a-business-associate-agreement-baa-with-openai) [blocked]; [Medcurity](https://medcurity.com/is-chatgpt-hipaa-compliant/) [snippet-only]
- **ChatGPT for Healthcare.** Launched January 8, 2026. Eligible customers can apply for a BAA, and shared content is not used for training. — [OpenAI: OpenAI for Healthcare](https://openai.com/index/openai-for-healthcare/) [snippet-only]. Secondary sources say ChatGPT Free, Plus, Pro, Team and self-serve Business are not BAA-eligible. ChatGPT Health (consumer, January 7, 2026) makes no HIPAA claim. Sources disagree on which Enterprise or Edu workspaces qualify. — [bdemerson](https://www.bdemerson.com/article/is-chatgpt-hipaa-compliant), [intuitionlabs](https://intuitionlabs.ai/articles/is-chatgpt-hipaa-compliant) [snippet-only]
- **Data residency.** The Your-data page points to a separate data-residency guide; regions were not confirmed. — [same page](https://developers.openai.com/api/docs/guides/your-data) [opened]
- **Usage policy.** openai.com/policies/usage-policies returned 403, so healthcare provisions (tailored medical advice requiring a licensed professional; automated high-stakes decisions without human review) were **not verified** in this research. **Gap.**

### Inferences
- Agere's stateless Responses or Chat Completions calls with `store=false` fit both a BAA and ZDR. Agere must avoid Assistants, vector stores, Files and Batch for PHI unless the BAA plus PSP retention terms cover them.

### Gaps
- OpenAI usage-policy healthcare text, the BAA help article, enterprise-privacy residency regions and pricing for residency were all blocked.

## Q3. Google: Gemini on Vertex AI under the Google Cloud BAA

### Takeaway
The Google Cloud BAA covers the whole Google Cloud infrastructure and lists generative AI products under their newer names ("Generative AI on Agent Platform", "Gemini Enterprise Agent Platform"). Pre-GA offerings are excluded. Real ZDR on Vertex requires disabling the default 24-hour implicit cache and avoiding Grounding with Google Search, Maps grounding, session resumption and Deep Research.

### Cited Findings
- **BAA scope.** "The HIPAA BAA for Google Cloud includes the entire Google Cloud infrastructure". Covered AI items include Generative AI on Agent Platform, Gemini Enterprise Agent Platform, Gemini Enterprise, Gemini in BigQuery and Vertex AI Workbench. The name "Vertex AI" itself was not found as a list item, apparently because of renaming. — [Google Cloud HIPAA compliance](https://docs.cloud.google.com/docs/security/compliance/hipaa) [opened]
- **Exclusions.** "Don't use pre-GA offerings ... with PHI". Disable services that cannot be covered. Compliance is "a shared responsibility". — [same page](https://docs.cloud.google.com/docs/security/compliance/hipaa) [opened]
- **Training.** Google won't use customer data to train or fine-tune models without permission, including pre-GA models. — [Vertex AI zero data retention](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/vertex-ai-zero-data-retention) [opened, undated]
- **Implicit caching.** On by default: in-memory and durable tiers, each deleted within 24 hours, and CMEK is not supported for the durable tier. Disable it with `cacheConfig disableCache: true`. — [same page](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/vertex-ai-zero-data-retention) [opened]
- **Features that retain data regardless.** Grounding with Google Search keeps logs up to 3 days and cannot be disabled (use Web Grounding for Enterprise instead). Maps grounding stores data 30 days. Gemini Live session resumption stores up to 24 hours. Deep Research sessions are kept 7 days. The Interactions API defaults to `store=true`. Abuse-monitoring logging may apply to "Advanced AI models". OpenAI flagship models on Vertex need separate ZDR approval. — [same page](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/vertex-ai-zero-data-retention) [opened]
- **Residency.** Regional and multi-regional endpoints keep the durable cache in location. The global endpoint can serve requests from any region. — [same page](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/vertex-ai-zero-data-retention) [opened]
- **Claude through Google.** Claude on Google Cloud comes in "Anthropic managed" and "Google managed" variants. On the Google-managed one, region follows the endpoint. — [Claude docs: Data residency](https://platform.claude.com/docs/en/manage-claude/data-residency) [opened]

### Inferences
- For a Vertex route, Agere needs a regional US endpoint, caching disabled, no Search grounding and only GA models. The Anthropic-managed versus Google-managed Claude distinction may change which BAA applies. Confirm with both companies.

### Gaps
- The Gemini prohibited-use or generative AI use policy healthcare text was not opened.
- The BAA's applicability to the "Anthropic managed" Claude on Google Cloud was not confirmed.

## Q4. AWS Bedrock: HIPAA eligibility

### Takeaway
Amazon Bedrock is on AWS's HIPAA Eligible Services list (page updated November 16, 2026) and is covered by the standard AWS BAA. Model providers cannot see prompts. Some frontier models retain traffic for abuse detection: all OpenAI non-OSS models (flagged traffic) and Claude Fable 5.x (all traffic, 30 days, unless the customer is EFS-eligible).

### Cited Findings
- **Eligibility.** Bedrock and Bedrock AgentCore, Comprehend Medical, Transcribe (including HealthScribe) and Amazon Q Business are listed as HIPAA eligible. Page last updated November 16, 2026. — [AWS HIPAA Eligible Services Reference](https://aws.amazon.com/compliance/hipaa-eligible-services-reference/) [opened]
- **Provider isolation.** Model Deployment Accounts are operated by Bedrock. "Model providers don't have access to those accounts", so they cannot see logs or customer prompts. — [Bedrock data protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html) [opened]
- **Abuse-detection retention.** Bedrock uses a zero operator access model. For OpenAI models other than the gpt-oss family, classifier-flagged traffic is retained up to 30 days, with full ZDR available on request. For Claude Fable 5, 5.1 and 5.2, Bedrock "retains all traffic for up to 30 days", and flagged traffic may get AWS human review. Enterprise Frontier Safeguards (EFS)-eligible customers can use these models with ZDR for internal use through at least June 30, 2027. Retained data is not shared with providers. Under cross-region inference, retained data sits in the destination region. — [Bedrock abuse detection](https://docs.aws.amazon.com/bedrock/latest/userguide/abuse-detection.html) [opened]
- **No Anthropic BAA coverage on Bedrock.** Anthropic's BAA does not cover Bedrock, Vertex or Foundry routes. The cloud's BAA governs there. — [Anthropic BAA page](https://privacy.claude.com/en/articles/8114513-business-associate-agreements-baa-for-commercial-customers) [opened]

### Inferences
- Bedrock is the most straightforward "one BAA" path for a customer already on AWS. For Claude Opus/Sonnet models, which are not on the Fable retention list, the page names no extra retention. Global cross-region inference profiles could move PHI processing out of the US, so Agere should use US geographic profiles.

### Gaps
- The Bedrock data-retention subpage was not opened.

## Q5. Azure OpenAI / Microsoft Foundry: HIPAA

### Takeaway
Microsoft's HIPAA BAA is included by default in the Product Terms and DPA for covered entities and business associates. Foundry "Models sold by Azure", including Azure OpenAI, are hosted by Microsoft, and OpenAI never sees prompts. Default abuse monitoring can store flagged prompts for human review unless the customer is approved for modified abuse monitoring. Global and DataZone deployments widen where processing happens.

### Cited Findings
- **BAA mechanics.** No separate signature is needed. The BAA comes through the Product Terms and DPA. Healthcare SaaS vendors on Azure sign their own BAAs with customers, and those customers don't need a Microsoft BAA. In-scope services are listed on "Cloud services in audit scope". The page dates from 2023/2024. — [Microsoft Learn: HIPAA – Azure Compliance](https://learn.microsoft.com/en-us/azure/compliance/offerings/offering-hipaa-us) [opened]
- **Data handling.** Prompts and completions are NOT available to OpenAI, NOT used to train, and models are stateless. — [Microsoft Learn: Data, privacy and security for Foundry Models sold by Azure](https://learn.microsoft.com/en-us/azure/ai-foundry/responsible-ai/openai/data-privacy) (ms.date 2026-05-18) [opened]
- **Residency.** Standard deployments process within the customer geography. "Global" can process in any geography. "DataZone US" processes anywhere in the US. Data at rest stays in the customer geography. Batch is a Global deployment type. — [same page](https://learn.microsoft.com/en-us/azure/ai-foundry/responsible-ai/openai/data-privacy) [opened]
- **Stored features.** The Responses API, Assistants Threads (preview), Stored completions (preview), Files and vector stores store data in the resource's geography. "Models or features in preview might not support all of the above conditions." — [same page](https://learn.microsoft.com/en-us/azure/ai-foundry/responsible-ai/openai/data-privacy) [opened]
- **Abuse monitoring.** Flagged samples may be stored for human review by authorized Microsoft employees. Managed customers can apply for modified abuse monitoring (`ContentLogging=false`). Automated review still runs. — [same page](https://learn.microsoft.com/en-us/azure/ai-foundry/responsible-ai/openai/data-privacy) [opened]
- **Claude on Foundry.** The US Data Zone Standard deployment keeps inference in the US at a 1.1x price. — [Claude docs: Data residency](https://platform.claude.com/docs/en/manage-claude/data-residency) [opened]

### Inferences
- An Azure route needs Standard (regional) or DataZone US deployments, no preview features with PHI, and ideally a modified-abuse-monitoring approval.

### Gaps
- The "Cloud services in audit scope" list was not opened to confirm Foundry and Azure OpenAI by name. It is widely reported as in scope, but that was not verified here.

## Q6. Usage policies on medical advice and high-risk healthcare decisions

### Takeaway
Anthropic's AUP (effective November 12, 2026) defines "High-risk AI Recommendations" in medical, healthcare-access and insurance contexts. These require meaningful review by a qualified human and disclosure that AI was used. Internal research, analytics drafting and fixed-rule application are excluded. OpenAI's and Google's policies were not verified in this research.

### Cited Findings
- **Anthropic: scope.** High-risk categories cover diagnosis; starting, stopping or adjusting treatment; interpreting test results for triage or treatment; triage or waitlist priority; eligibility, denial or discontinuation of care programs; and insurance underwriting, claims, coverage, prior-authorization and medical-necessity decisions. — [Anthropic Usage Policy](https://www.anthropic.com/legal/aup) [opened]
- **Anthropic: controls.** A qualified person, licensed where law requires, must "meaningfully review a High-risk AI Recommendation" before use, and the individual must be told "AI was used to produce it". Wholly favorable decisions, such as paying a claim or approving a prior auth, need no pre-review where law permits. Excluded: general health education, internal drafting or research, and applying fixed rules without model judgment. — [same](https://www.anthropic.com/legal/aup) [opened]
- **OpenAI.** The usage-policies page returned 403 and was not verified. — [OpenAI usage policies](https://openai.com/policies/usage-policies/) [blocked]
- **Microsoft.** The Foundry doc references the Microsoft AI Code of Conduct, which governs abuse monitoring. Its text was not opened. — [Foundry data privacy](https://learn.microsoft.com/en-us/azure/ai-foundry/responsible-ai/openai/data-privacy) [opened]

### Inferences
- Agere's population analytics (cohort counts, VBC performance, care-gap lists for staff) looks like "internal research/analytics" under Anthropic's AUP. Features that rank individual members for outreach priority, flag care-program eligibility, or support prior authorization or claims fall into High-risk categories. Those need a human reviewer workflow and patient-facing AI disclosure where outputs reach the individual.

### Gaps
- OpenAI, Google Gemini and Microsoft healthcare-specific policy text was not obtained.

## Q7. US regulatory context

### Takeaway
The federal transparency regime has loosened. HTI-5 (finalized around October 2026) removes the HTI-1 predictive DSI source-attribute and risk-management requirements. FDA's January 2026 CDS guidance relaxed the treatment of single recommendations. Remaining duties come from Section 1557's §92.210 (still in force), CMS's MA individualized-determination rules, and a growing set of state laws: CA AB 3030 and SB 1120, Colorado SB26-189 (ADMT, obligations from January 1, 2027), Texas TRAIGA and SB 1188, and Utah.

### Cited Findings
- **ONC HTI-1 and HTI-5.** HTI-1 §170.315(b)(11) required certified health IT developers to support source attributes for evidence-based and predictive DSIs. The HTI-5 proposed rule (comment period closed February 27, 2026) proposed removing them. — [Covington](https://www.covingtonblogs.com/2026/01/15/hhs-proposes-changes-to-the-health-it-certification-program-and-information-blocking-regulations-in-hti-5-proposed-rule/) [snippet-only]. A law-firm alert dated October 28, 2026 reports the HTI-5 **final rule** removes §170.315(b)(11)(iv) source attributes and (b)(11)(v) intervention risk management, plus §170.315(a)(9). — [Akin Gump](https://www.akingump.com/en/insights/ai-law-and-regulation-tracker/onc-issues-hti-5-final-rule-removing-model-card-and-risk-management-requirements-for-predictive-decision-support-interventions) [snippet-only, 403 on open]. One source gives an effective date of December 28, 2026. — [IntuitionLabs](https://intuitionlabs.ai/pdfs/clinical-ai-buyer-evidence-checklist-hti-5.pdf) [snippet-only, not verified]. The HealthIT.gov DSI page returned 404. **Flag: verify against the Federal Register.**
- **FDA CDS.** The current guidance is dated January 2026 and is final ("Content current as of: 01/29/2026"), superseding 2022. — [FDA: Clinical Decision Support Software](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/clinical-decision-support-software) [opened]. The page did not list the four §520(o)(1)(E) criteria. Law-firm summaries say the January 6, 2026 revision eased enforcement for CDS that gives a single recommendation to an HCP, and it still covers only HCP-facing CDS. — [Covington](https://www.cov.com/en/news-and-insights/insights/2026/01/5-key-takeaways-from-fdas-revised-clinical-decision-support-cds-software-guidance), [Arnold & Porter](https://www.arnoldporter.com/en/perspectives/advisories/2026/01/fda-cuts-red-tape-on-clinical-decision-support-software) [snippet-only]. The FDA page also says that under "Executive Order 14434" agencies use "Super Intelligence" in place of "AI". This is recorded as observed and was not verified independently.
- **CMS Medicare Advantage (CMS-4201-F, 2024 rule; FAQ of February 6, 2024).** MAOs may use algorithms and AI to assist coverage decisions, but determinations must rest on the individual's circumstances under 42 CFR §422.101(c). Algorithms using larger data sets instead of the patient's own history do not comply. Algorithms cannot change publicly posted criteria (§422.101(b)(6)). Section 1557 is cited on bias. — [Norton Rose Fulbright](https://www.nortonrosefulbright.com/en-gb/knowledge/publications/644bd9a2/cms-clarifies-medicare-advantage-organizations-use-of-ai-and-algorithms-in-coverage-decisions), [Manatt](https://www.manatt.com/insights/newsletters/health-highlights/cms-weighs-in-on-the-use-of-algorithms-and-artific) [snippet-only; CMS FAQ URL returned 404]
- **Section 1557 §92.210.** The rule bars covered entities from discriminating through "patient care decision support tools". They must make reasonable efforts to identify and mitigate risk, with a delayed compliance date (commonly cited as May 1, 2025). — [Federal Register 2024-08711](https://www.govinfo.gov/content/pkg/FR-2024-05-06/pdf/2024-08711.pdf) [snippet-only; FR HTML redirected to a block page]. A June 2, 2026 FR notice records the *Tennessee v. Kennedy* vacatur (October 22, 2025) of gender-identity provisions only. §92.210 is not among the vacated sections, and "the rest of the 2024 Rule remains in force". — [Federal Register, June 2, 2026, doc 2026-11015](https://www.govinfo.gov/content/pkg/FR-2026-06-02/html/2026-11015.htm) [opened]
- **California.** AB 3030: health facilities and clinics using generative AI for patient clinical communications must include an AI disclaimer and instructions for reaching a human, with an exemption when a licensed provider reviews the message. In force since January 1, 2025. SB 1120: medical-necessity denials by plans and insurers must be made by a licensed clinician, not an algorithm alone. — [Akerman / JD Supra](https://www.jdsupra.com/legalnews/new-year-new-ai-rules-healthcare-ai-9758831/) [snippet-only; leginfo returned 403]
- **Colorado.** SB26-189, "Automated Decision-Making Technology", was signed May 14, 2026 (Session Law Ch. 131). It "repeals and reenacts" SB24-205. Health-care services are a "consequential decision" area. From January 1, 2027, developers must give deployers documentation (intended uses, training data categories, limitations, human-review instructions). Deployers must give point-of-interaction notice, explain an adverse decision within 30 days, and keep records for 3 years. Consumers can request correction and human review. The AG enforces, with a 60-day cure period until 2030. Some entities are exempt "to the extent [they] comply with other legal obligations". — [Colorado General Assembly: SB26-189](https://leg.colorado.gov/bills/sb26-189) [opened]. Older reporting of a June 30, 2026 SB24-205 date is superseded.
- **Texas.** TRAIGA (HB 149), effective January 1, 2026, requires providers to disclose AI use in care to patients. SB 1188, effective September 1, 2025, requires practitioners using AI for diagnosis or treatment to act within license scope, review AI-generated records and disclose. — [Bass Berry](https://bassberry.com/news/texas-illinois-and-colorado-legislative-developments-impact-healthcare-ai-and-other-industries-while-u-s-congress-fails-to-pass-moratorium/), [Akerman](https://www.akerman.com/en/perspectives/hrx-new-year-new-ai-rules-healthcare-ai-laws-now-in-effect.html) [snippet-only]
- **Utah.** The AI Policy Act was amended to focus on regulated occupations, with added mental-health chatbot protections. Reporting says hospitals must disclose AI use in patient care. — [NCSL](https://www.ncsl.org/state-legislatures-news/details/new-trends-emerge-as-states-refine-ai-legislation), [Becker's](https://www.beckershospitalreview.com/healthcare-information-technology/ai/4-states-regulating-ai-in-healthcare/) [snippet-only]
- **Trend.** One 2026 count reports seven states passed insurer AI coverage-decision laws in 2026. A federal executive order directs Commerce to identify "onerous" state AI laws and names Colorado. — [RISE Health](https://www.risehealth.org/insights-articles/ai-health-insurance-state-laws-2026/), [Epstein Becker Green](https://www.ebglaw.com/insights/publications/ai-legislation-2026-legislative-wrap-up) [snippet-only]

### Inferences
- **Mostly out of scope.** HTI-1 and HTI-5 bind certified health IT developers, not an analytics stack outside ONC certification. FDA device rules mainly bite on patient-specific diagnostic or treatment recommendations to clinicians. Population and administrative analytics (cohort sizing, VBC contract performance, cost and utilization, quality-measure gaps reported in aggregate) generally fall outside FDA CDS and outside "patient care decision support tools". That is a reasonable but unconfirmed reading.
- **In scope when Agere touches individuals:**
  1. Member- or patient-level risk lists that drive outreach, care-management enrollment or triage could be "patient care decision support tools" under §92.210. Customers then need bias-mitigation documentation from Agere, and Agere should ship subgroup performance and input-variable disclosure.
  2. Any payer feature supporting coverage, prior-auth, medical-necessity or utilization-management decisions triggers CMS §422.101(c) individualization, CA SB 1120 licensed-reviewer rules, Colorado ADMT notice and appeal duties (from 2027), the Anthropic AUP high-risk controls and other state insurer-AI laws. The practical answer is to keep those features out of scope or human-gated, with clinician sign-off and audit trails.
  3. Note-reading output shown to patients (rare for Agere) would trigger CA AB 3030 and TX disclosure.
- **Disclosure kit Agere should still offer**, even after HTI-5 removed the federal mandate: model and source attributes in "model card" style, intended use, an "analytics, not clinical decision support" statement, the Colorado developer documentation set (from January 1, 2027), the human-review instructions the AUP requires, and the per-feature BAA/ZDR route actually in use.
- **Deployment routes, summarized:**
  - (a) Anthropic 1P API with BAA, US inference_geo at 1.1x, synchronous Messages only, no Batch, Files or Code Execution, and 30-day retention if the model is a Covered Model.
  - (b) AWS Bedrock under the AWS BAA with US inference profiles. Some Claude models carry 30-day abuse retention unless EFS.
  - (c) Vertex under the Google BAA: GA models only, regional endpoint, caching off, no Search grounding.
  - (d) Azure Foundry under the Product Terms BAA: Standard or DataZone US, modified abuse monitoring, no preview features.
  - (e) OpenAI API with BAA and ZDR or PSP: Responses or Chat with `store=false`, no Assistants, Files or Batch.

### Gaps
- No official HTI-5 Federal Register text was opened. Effective dates are secondary.
- The CMS FAQ primary text, the CA statute text, Texas and Utah statute pages, and HHS OCR's 1557 page were blocked or not opened.
- The four FDA non-device CDS criteria were not confirmed from the FDA page itself.
- No source addressed whether a vendor such as Agere, as opposed to its covered-entity customer, is itself regulated under §92.210. The rule applies to covered entities; vendors are affected contractually.
