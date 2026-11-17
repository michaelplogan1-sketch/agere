# Frontier lab healthcare evaluations: lab-published claims vs independent evaluations (2023-2026)

Compiled 2026-11-17. Notes on evidence quality:
- "Opened" means the page was fetched and read. "Snippet only" means the claim was seen only in a search-result summary and the page itself was not read.
- Many 2026 model names below (Claude Sonnet/Opus/Haiku 5.5, GPT-5.6 / GPT-6.1 "Sol/Terra/Luna", Gemini 3.x, Muse Spark) come from aggregator leaderboards that mark the entries as self-reported and unverified. I did not open the lab system cards that would confirm them. Treat these as pointers to check against the primary card before citing.
- openai.com returned HTTP 403, so OpenAI's HealthBench blog and system cards were read through the arXiv paper and aggregators only.

## 1. Lab-published healthcare benchmarks and claims (what each lab measured, and what it did not)

### Takeaway
Each lab measures what it built or what shows it in a good light. OpenAI uses HealthBench: rubric-graded multi-turn chat, with a model as the grader. Microsoft uses MAI-DxO on NEJM case puzzles. Anthropic uses MedCalc and MedAgentBench (agentic EHR tasks) and reports results mostly as charts. Google uses MedQA and EHRQA for its open MedGemma models. No lab publishes text-to-SQL-over-EHR, coding-accuracy or prior-authorization accuracy numbers for its frontier model, and those are the tasks Agere's frontier rung actually performs.

### Cited Findings
**OpenAI: HealthBench (arXiv 2505.08775, submitted 13 May 2025; opened)**
- Scale: 5,000 multi-turn conversations, rubrics written by 262 physicians, 48,562 unique rubric criteria — [arXiv abs](https://arxiv.org/abs/2505.08775)
- Overall scores (Table 7): o3 0.599, GPT-4.1 0.478, o1 0.420, GPT-4o (Aug 2024) 0.323, GPT-3.5 Turbo 0.155 — [arXiv HTML](https://arxiv.org/html/2505.08775v1)
- Grok 3 and Gemini 2.5 Pro "performed strongly" and beat Claude 3.7 Sonnet and Llama 4 Maverick. Non-OpenAI numbers appear only in figures, not in the text — [arXiv HTML](https://arxiv.org/html/2505.08775v1)
- HealthBench Hard: the top score at launch was 32% (o3) — [arXiv abs](https://arxiv.org/abs/2505.08775)
- HealthBench Consensus: 34 physician-consensus-validated dimensions. Error rates fell more than 4x from GPT-3.5 to GPT-4.1; no per-model table appears in the text — [arXiv HTML](https://arxiv.org/html/2505.08775v1)
- Cost: o3, o4-mini and GPT-4.1 form the cost-performance Pareto frontier. GPT-4.1 nano beats GPT-4o at 25x lower cost. No dollar figures are given — [arXiv HTML](https://arxiv.org/html/2505.08775v1)
- Physician baseline: physicians writing without model help scored below models. Given April 2025 model responses as references, physicians improved them 46.8% of the time and made them worse 47.7% of the time — [arXiv HTML](https://arxiv.org/html/2505.08775v1)
- Reliability: o3's worst-at-16 score is about one-third below its 60% mean (roughly 0.40, inferred by the page reader, not stated as a number) — [arXiv HTML](https://arxiv.org/html/2505.08775v1)
- What it did not measure: structured-data or SQL tasks, coding and billing, or prior authorization. Scoring depends on a model grader (see section 4).
- GPT-5 era (Aug 2025): gpt-5-thinking scored 46.2% on HealthBench Hard per the GPT-5 system card — snippet only, via [Klu](https://klu.ai/benchmarks/healthbench) and other aggregators; the OpenAI card was not opened (403).

**Microsoft: MAI-DxO / Sequential Diagnosis Benchmark (arXiv 2506.22405, 27 Jun 2025, rev. 2 Jul 2025; abstract opened)**
- 304 NEJM clinicopathological conference cases. MAI-DxO paired with o3 reached 80% accuracy, 4x the roughly 20% of generalist physicians. The maximum-accuracy configuration reached 85.5% — [arXiv abs](https://arxiv.org/abs/2506.22405)
- Cost claims: about 20% lower diagnostic cost than physicians and about 70% lower than off-the-shelf o3. The abstract claims the gains generalize across OpenAI, Gemini, Claude, Grok, DeepSeek and Llama families but gives no per-model numbers — [arXiv abs](https://arxiv.org/abs/2506.22405)
- Dollar figures (snippet only, not verified on the primary page): physicians 19.9% at $2,963 per case; off-the-shelf o3 78.6% at $7,850; budgeted MAI-DxO 79.9% at $2,397; ensemble 85.5% at $7,184 — [Pebblous](https://blog.pebblous.ai/blog/mai-dxo-sequential-diagnosis-benchmark/en/), [Emergent Mind](https://www.emergentmind.com/papers/2506.22405)
- Critiques (snippet only): physicians were a generalist, median-experience group rather than specialists. Costs are estimates. CPC cases are curated puzzles with known answers. Whether MAI-DxO looks cheaper or dearer depends on which configuration is quoted — [iatroX critique](https://www.iatrox.com/blog/mai-dxo-85-vs-20-critical-analysis), [Pebblous](https://blog.pebblous.ai/blog/mai-dxo-sequential-diagnosis-benchmark/en/)
- A claim that the work appeared in *Science* on 22 Oct 2026, with different case-level figures (83.2%, 36.1%), comes from one blog and is unverified — [Pebblous](https://blog.pebblous.ai/blog/mai-dxo-sequential-diagnosis-benchmark/en/)

**Anthropic: Claude for Healthcare and Life Sciences (11 Jan 2026; opened)**
- Launched with Claude Opus 4.5, evaluated with extended thinking (64k) and native tool use. Connectors: CMS Coverage Database (LCD/NCD), ICD-10, NPI Registry, PubMed. Agent Skills for FHIR development and a sample prior-authorization review skill. HIPAA-ready offerings for providers and payers (clarified 7 Feb 2026) — [Anthropic](https://www.anthropic.com/news/healthcare-life-sciences)
- Benchmarks shown: MedCalc (with Python execution), MedAgentBench, SpatialBench, internal life-science evaluations, and honesty/hallucination evaluations (Opus 4.5 system card pp. 48-49). Results appear only as charts, with no numbers in the text. No prior-auth accuracy metric is reported, and Anthropic describes prior-auth output as a proposed determination for payer review — [Anthropic](https://www.anthropic.com/news/healthcare-life-sciences)
- Numbers from secondary sources (snippet only): Opus 4.5 at 92.3% on MedAgentBench and 61.3% on MedCalc-Bench (with code execution). The original Stanford MedAgentBench baseline for Claude 3.5 Sonnet v2 was 69.67%. An independent reproduction found MedCalc results depend heavily on the dataset version — [AI News](https://www.artificialintelligence-news.com/news/medical-ai-diagnostics-openai-google-anthropic/), [IntuitionLabs](https://intuitionlabs.ai/articles/claude-healthcare-life-sciences-ai-capabilities-2026), [Stanford MedAgentBench](https://stanfordmlgroup.github.io/projects/medagentbench/)
- A "40% hallucination reduction / medical fine-tuning" claim for Opus 4.5 appears only in a low-quality syndicated piece and is unsupported — [FinancialContent](https://markets.financialcontent.com/stocks/article/tokenring-2026-1-19-anthropics-new-specialized-healthcare-tiers-a-new-era-for-ai-driven-diagnostics-and-medical-triage)
- Later Claude HealthBench numbers (snippet or aggregator only, self-reported, not verified on Anthropic pages): Claude Sonnet 5.5 0.654, Haiku 5.5 0.616, Opus 5.5 0.606 on overall HealthBench; Sonnet 5.5 69.2% and Opus 5.5 65.6% on HealthBench Professional — [llm-stats (opened)](https://llm-stats.com/benchmarks/healthbench), [benchlm](https://benchlm.ai/benchmarks/healthbenchprofessional)

**Google: MedGemma (open models; snippet only, report pages not opened)**
- MedGemma 1 (July 2025): the 27B text model is reported at 89.8% on MedQA, while a news summary says 87.7%, so the setting or version differs. EHRQA improved with RL fine-tuning — [MedGemma report](https://arxiv.org/html/2507.05201v3), [The Decoder](https://the-decoder.com/?p=25287)
- MedGemma 1.5 (April 2026): +5% MedQA and +22% EHRQA over MedGemma 1 — [arXiv 2604.05081](https://arxiv.org/html/2604.05081v2)
- Gemini 3.1 Pro on HealthBench Professional: 41.6 at high effort, self-reported (snippet only) — [Artificial Analysis](https://artificialanalysis.ai/evaluations/healthbench)

**Meta, xAI, Mistral:** I found no lab-published healthcare evaluations from Meta, xAI or Mistral beyond appearances on third-party leaderboards: Grok 3 in HealthBench, Llama in MedHELM, Muse Spark in MedHELM Q2 2026.

### Inferences
- The one lab claim that touches an Agere rung is Anthropic's connector set (ICD-10, CMS coverage, NPI, prior-auth skill). It comes with no accuracy numbers, so it is a wiring convenience, not evidence of quality.
- HealthBench and MAI-DxO measure patient chat and diagnostic reasoning. Neither predicts text-to-SQL or governed-definition accuracy.

### Gaps
- Google AMIE and Med-PaLM 2 results were not retrieved; the search did not surface them, so they are not covered here.
- OpenAI system-card health sections (GPT-5 and later) could not be opened (403).
- Numeric MedCalc and MedAgentBench values for Claude were not confirmed on an Anthropic page.

## 2. Independent evaluations with frontier models side by side

### Takeaway
Independent leaderboards disagree with each other and with lab claims. Rankings depend on task, snapshot and leaderboard operator. On MedHELM, administration and workflow is the weakest category for every model, and EHRSQL is singled out as hard. Text-to-SQL on EHRSQL sits around 45% execution accuracy for GPT-4o, and an open 32B SQL model matches it.

### Cited Findings
**Stanford MedHELM paper (arXiv 2505.23802 v2, 2 Jun 2025; later in Nature Medicine per search; opened)**
- 9 models. Overall win rates: DeepSeek R1 0.66, o3-mini 0.64, Claude 3.7 Sonnet 0.64, Claude 3.5 Sonnet 0.63, GPT-4o 0.57, Gemini 2.0 Flash 0.42, GPT-4o mini 0.39, Llama 3.3 70B 0.30, Gemini 1.5 Pro 0.24 — [arXiv](https://arxiv.org/html/2505.23802v2)
- Category ranges (normalized): Clinical Note Generation 0.74-0.85 (R1 and o3-mini 0.85, Claude 0.82-0.83). Patient Communication 0.76-0.89. Clinical Decision Support 0.61-0.76. Research Assistance 0.65-0.75. **Administration & Workflow 0.53-0.63, the lowest category for all models.** Models do worse on EHRSQL; the text gives no number — [arXiv](https://arxiv.org/html/2505.23802v2)
- Full-suite cost (pricing as of 12 May 2025; upper bound): R1 $1,806, o3-mini $1,722, Claude 3.5 Sonnet $1,571, Claude 3.7 Sonnet $1,537, GPT-4o $1,413, Gemini 1.5 Pro $1,131, Llama 3.3 $940, Gemini 2.0 Flash $815, GPT-4o mini $805. The abstract's "Claude 3.5 Sonnet 40% cheaper" does not match Table 3, which shows about 9-13% cheaper — [arXiv](https://arxiv.org/html/2505.23802v2)

**MedHELM Q2 2026 leaderboard (v0.7), Pacific AI, 29 Jun 2026 (opened)**
- Mean scores: Gemini 3.1 Pro 0.652, Gemini 3.5 Flash 0.642, Muse Spark 0.621, GPT-5.4 mini 0.552, GPT-5.4 0.538, Gemini 2.5 Pro 0.529, DeepSeek R1 0.485, Claude 4.6 Opus 0.456, Claude 3.7 Sonnet 0.45 — [Pacific AI](https://pacific.ai/evaluating-frontier-llms-for-healthcare-with-medhelm-40-clinical-scenarios-and-a-new-q2-2026-leaderboard/)
- The page labels Muse Spark as xAI. Muse Spark is generally reported as a Meta Superintelligence Labs model (April 2026), so this is likely an attribution error on the page.
- Task-level leaders differ from the overall ranking. Muse Spark leads ACI-Bench note generation (jury 4.969). GPT-5.4 mini leads MedEC error detection (0.722 vs Gemini 3.1 Pro 0.680). Gemini 2.5 Pro leads HeadQA (0.931). OpenAI Batch API halves OpenAI run cost — [Pacific AI](https://pacific.ai/evaluating-frontier-llms-for-healthcare-with-medhelm-40-clinical-scenarios-and-a-new-q2-2026-leaderboard/)
- The official leaderboard.medhelm.org page did not render when fetched. Its snippet reportedly shows gpt-6.1-sol 0.749, claude-opus-5.5 0.702, gemini-3.8-flash 0.694 (snippet only; conflicts with the Pacific AI snapshot, likely a later version) — [MedHELM leaderboard](https://leaderboard.medhelm.org/)

**HealthBench Hard (third-party vendor; snippet only):** GPT-5.4 High 46.2%, Gemini 3.1 Pro 45.8%, Claude Opus 4.6 44.4%. These are close enough to be within noise — [EvidenceMD](https://evidencemd.ai/blogs/evidencemd-benchmarks)

**EHRSQL / text-to-SQL over EHR (snippet only)**
- EHRSQL execution accuracy: OmniSQL-32B (open) 46.8% vs GPT-4o 45.5% with majority voting. GPT-4o scores 44.9% greedy — [OmniSQL](https://arxiv.org/html/2503.02240), [SOTA2](https://www.sota2.com/research/sota/text-to-sql-on-ehr-bench)
- EHRSQL scores abstention on unanswerable questions, so raw execution accuracy is not the full reliability measure — [CBR-to-SQL](https://arxiv.org/pdf/2603.05569)
- I found no published Claude results on EHRSQL. One EMNLP 2026 paper reports GPT-4o and Gemini 2.5 Pro at 0.31 and GPT-4.1 at 0.47-0.49 on an unconfirmed EHR benchmark — [ACL Anthology](https://aclanthology.org/2026.emnlp-main.1353.pdf)

**MedQA saturation and brittleness (snippet only)**
- Bedi et al.: in 68 clinician-validated MedQA items, the correct answer was replaced with "None of the other answers". All six models dropped significantly. Relative drops ranged from 8.82% (DeepSeek-R1, 63/68 to 57/68) to 38.24% (Llama-3.3-70B), and general models dropped more than reasoning models — [PMC12334947](https://pmc.ncbi.nlm.nih.gov/articles/PMC12334947/)
- The ReMedQA benchmark (EACL 2026) finds the "none provided" variant gives the largest consistency drop — [ReMedQA](https://disi-unibo-nlp.github.io/remedqa/)

**Hallucination in clinical summarisation (snippet only)**
- Asgari et al., npj Digital Medicine, May 2025: 18 configurations, 12,999 clinician-annotated sentences (PriMock transcripts). 1.47% hallucination rate and 3.45% omission rate. After prompt and workflow iteration, major errors fell below reported human note-taking rates. These rates are specific to that pipeline — [PMC12075489](https://pmc.ncbi.nlm.nih.gov/articles/PMC12075489/)

### Inferences
- Across 2025 and 2026 snapshots, no single lab wins every medical task. Leaderboard order flips by version and scenario, so per-rung choices need Agere's own workload tests.
- Administration and workflow tasks (closest to Agere) are where every model is weakest, so there is the most room for gains from governance and recipes.

### Gaps
- I found no independent head-to-head of current Claude, GPT and Gemini models on EHRSQL or MIMIC SQL.
- Frontier-model comparison studies of clinical note extraction (beyond MedHELM note generation) were not retrieved.

## 3. Administrative / operational / financial tasks (coding, prior auth, claims, SQL)

### Takeaway
The published evidence is thin and dated. Unaided LLMs were poor medical coders in 2024: GPT-4 reached 33.9% exact match on ICD-10-CM. Tool and retrieval augmentation helps substantially. I found no published accuracy benchmark for prior auth or claims comparing frontier models.

### Cited Findings
- Soroush et al. (NEJM AI, 2024): GPT-4 had the best exact match among GPT-3.5, GPT-4, Gemini Pro and Llama2-70b. ICD-9-CM 45.9%, ICD-10-CM 33.9%, CPT 49.8%. The authors judged this too low for reliable use (snippet only) — [NEJM AI](https://ai.nejm.org/doi/full/10.1056/AIdbp2300040)
- ICD-10-CM extraction from inpatient notes (medRxiv 2024): GPT-4 agreed with the human coder 15.2% of the time overall and 26.4% at category level. Claude 3 was best on primary diagnosis (snippet only) — [medRxiv](https://www.medrxiv.org/content/10.1101/2024.04.29.24306573v2.full)
- Tool-augmented coding preprint: "LLMs are good medical coders if provided with tools", with substantial gains over baseline LLMs (snippet only) — [arXiv 2407.12849](https://arxiv.org/pdf/2407.12849)
- MedHELM: Administration & Workflow is the lowest category (0.53-0.63) for all nine 2025 models — [arXiv](https://arxiv.org/html/2505.23802v2)
- Prior auth: Anthropic ships a sample skill but reports no metrics — [Anthropic](https://www.anthropic.com/news/healthcare-life-sciences)

### Inferences
- The coding result (lookup with tools beats generation from memory) matches Agere's design of governed definitions and code lists, and argues for keeping code lookup deterministic rather than handing it to the frontier model.

### Gaps
- No 2025-2026 frontier-model coding benchmark was found.
- No claims, denial or prior-auth accuracy benchmarks were found.
- No per-query cost figures for SQL over EHR data were found.

## 4. LLM-as-judge reliability in medicine

### Takeaway
Model graders reach roughly physician-level agreement, but the evidence base is narrow. The HealthBench grader's macro F1 against physicians is about 0.71. The MedHELM jury's ICC is 0.47, with a wide confidence interval, against a clinician-clinician ICC of 0.43, and it was validated on only 56 instances.

### Cited Findings
- HealthBench grader meta-evaluation, macro F1 vs physicians: GPT-4.1 0.709 (adopted as grader), o4-mini 0.692, o3 0.681, GPT-4.1 mini 0.661, GPT-4.1 nano 0.580 — [arXiv HTML](https://arxiv.org/html/2505.08775v1)
- MedHELM LLM-jury ICC(3,k) 0.47 (95% CI 0.10-0.69), clinician-clinician 0.43, ROUGE-L 0.36, BERTScore 0.44. Validated on only 31 ACI-Bench and 25 MEDIQA instances — [arXiv](https://arxiv.org/html/2505.23802v2)
- HealthBench is graded by an OpenAI model, which is a potential self-preference concern when it is used to compare OpenAI models (inference; the paper does not quantify this).

### Inferences
- A model judge can screen Agere's outputs, but clinician spot checks are still needed. Judge choice should itself be compared against a simple baseline (execution match, string or rule checks).

### Gaps
- No study was found that measures judge agreement for SQL or analytics outputs in healthcare.
- No study was found that quantifies cross-lab self-preference bias in medical grading.

## 5. What the 2025-2026 generation changed

### Takeaway
Scores rose but compressed. HealthBench Hard went from 32% (o3, May 2025) to about 46% (gpt-5-thinking, Aug 2025, and GPT-5.4 or Gemini 3.1 Pro in 2026). Top models now sit within about 2 points of each other, and 2026 MedHELM snapshots reorder labs between versions. Nearly all newest figures are self-reported.

### Cited Findings
- HealthBench Hard: 32% top score in May 2025 — [arXiv](https://arxiv.org/abs/2505.08775). gpt-5-thinking 46.2% (Aug 2025, snippet) — [Klu](https://klu.ai/benchmarks/healthbench). In 2026, 46.2 / 45.8 / 44.4 for GPT-5.4 High, Gemini 3.1 Pro and Claude Opus 4.6 (vendor, snippet) — [EvidenceMD](https://evidencemd.ai/blogs/evidencemd-benchmarks)
- Overall HealthBench went from 0.599 (o3, 2025) to 0.55-0.65 for late-2026 models, all self-reported and unverified. Several cheaper tiers match or beat flagships (Claude Haiku 5.5 0.616 vs Opus 5.5 0.606; GPT-5.4 mini above GPT-5.4 on MedHELM) — [llm-stats](https://llm-stats.com/benchmarks/healthbench), [Pacific AI](https://pacific.ai/evaluating-frontier-llms-for-healthcare-with-medhelm-40-clinical-scenarios-and-a-new-q2-2026-leaderboard/)
- MedHELM went from DeepSeek R1 leading in 2025 to Gemini 3.1 Pro leading in Q2 2026 (Claude 4.6 Opus 8th of 9). A later snippet shows gpt-6.1-sol and claude-opus-5.5 on top. Rankings are snapshot-dependent — [arXiv](https://arxiv.org/html/2505.23802v2), [Pacific AI](https://pacific.ai/evaluating-frontier-llms-for-healthcare-with-medhelm-40-clinical-scenarios-and-a-new-q2-2026-leaderboard/), [MedHELM leaderboard (snippet)](https://leaderboard.medhelm.org/)

### Inferences
- Smaller and cheaper tiers often match flagships on medical benchmarks, so Agere's "accuracy and cost over speed" rule favors testing mid and small tiers first at the frontier rung, escalating only on measured failures.
- Because rankings flip by snapshot, Agere should pin model versions and re-run its own workflow evaluation at each upgrade rather than rely on public leaderboards.

### Gaps
- The 2026 model names and scores could not be verified against primary system cards.
- No independent 2026 evaluation of administrative or SQL tasks was found.
