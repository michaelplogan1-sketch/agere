# Accuracy and cost proof for natural-language analytics and text-to-SQL (enterprise and healthcare, 2025-2026)

Research date: 2026-11-15. Public web sources only. Vendor-reported figures are labelled [VENDOR]. Leaderboard figures are self-submitted unless stated otherwise.

## Published accuracy on realistic benchmarks, the public-to-enterprise gap, and healthcare-specific benchmarks

### Takeaway
On academic benchmarks, top systems now score about 80% on BIRD and 70-97% on Spider 2.0, depending on the track. On benchmarks built from real enterprise warehouses (BEAVER) or interactive multi-turn tasks (BIRD-Interact, LiveSQLBench), frontier agents fall to about 10-40%. In healthcare, single-turn EHR text-to-SQL is close to solved on answerable questions (about 94% on an EHRSQL subset), but agentic and interactive EHR tasks remain much lower and inconsistent across repeated runs.

### Cited Findings
**Enterprise and general text-to-SQL**
- Spider 2.0 (ICLR 2025 oral, XLANG Lab) has 632 enterprise workflow problems. At release, the best traditional method (DAIL-SQL + GPT-4o) reached 5.68% execution accuracy on Spider 2.0-Lite — [Spider 2.0 paper, arXiv 2411.07763](https://arxiv.org/abs/2411.07763)
- Current Spider 2.0 leaderboard (self-submitted, entries undated, validation status not stated per entry):
  - Spider 2.0-Snow (547 tasks): Genloop Sentinel Agent v2 Pro 96.70; Native mini 96.53; QUVI-3 + Gemini-3-pro-preview 94.15; Tencent TCDataAgent-SQL 93.97; AT&T CDO and RelationalAI "Ask Data with Relational Knowledge Graph" 86.28.
  - Spider 2.0-Lite: Tencent Tianqiong + GLM 5.2 76.23; DecisionX 74.95; Snowflake AI Research x UCSD DivSkill-SQL 73.13; Oracle SOMA-SQL 72.02; JetBrains Databao 69.65.
  - Spider 2.0-DBT (68 repository-level code-agent tasks): best 65.6 (SignalPilot); GPT-5-based agents 35-41.
  - The site notes that scores may shift as evaluation metrics are re-checked, and that oracle-table entries are excluded from ranking — [Spider 2.0 leaderboard](https://spider2-sql.github.io/)
- Oracle markets its SOMA-SQL result as 72.02% EX@1 and "#1 on Spider 2.0-Lite". It has since been overtaken on the same leaderboard [VENDOR] — [Oracle blog](https://blogs.oracle.com/cloud-infrastructure/oci-gen-ai-tops-spider-2-lite)
- BIRD: the human baseline (data engineers plus database students) is 92.96% execution accuracy. Top AI entries are about 80-82%, e.g. Agentar-Scale-SQL 81.67%, top of the official leaderboard as of a September 2025 snapshot — [Agentar-Scale-SQL, arXiv 2509.24403](https://arxiv.org/pdf/2509.24403v6); [benchmarklist BIRD](https://benchmarklist.com/benchmarks/bird_sql/)
- BEAVER (MIT; built by anonymising two real enterprise data warehouses):
  - v1: GPT-4o and Llama3-70B reached close to 0% end-to-end execution accuracy.
  - Later version: agentic frameworks on GPT-5.2 reach only 10.8% execution accuracy, and 30.1% even with oracle subtask hints.
  - Question count differs across sources (93 in v1 versus 203 in a review); not resolved — [BEAVER, arXiv 2409.02038](https://arxiv.org/html/2409.02038v2)
- LiveSQLBench (contamination-resistant, date-windowed releases):
  - 2024-11 to 2026-03 window: o3 29.54% success at about $0.18 per task; Claude Sonnet 4 27.01% at about $0.06 per task; GPT-5 and o3-mini 31.15%.
  - 2024-11 to 2026-02 window: Claude Opus 4.6 with OpenHands 41.60%.
  - Success rate is a sample-weighted micro-average. Third-party mirror; verify on the official board — [benchmarklist LiveSQLBench](https://benchmarklist.com/benchmarks/livesqlbench/)
- BIRD-Interact (ICLR 2026; 600 full and 300 lite interactive tasks): GPT-5 achieved 8.67% success in conversational mode (c-Interact) and 17.00% in agentic mode (a-Interact). Claude Sonnet 4 achieved 14.17% and 12.67% — [BIRD-Interact, arXiv 2510.05318](https://arxiv.org/html/2510.05318v3); [project site](https://bird-interact.github.io/)

**Healthcare**
- EHRSQL (MIMIC-based) in the M3 paper: Claude Sonnet 4 answered 94 of 100 (94.0%) questions in an answerable-only subset of the EHRSQL 2024 test set correctly. gpt-oss-20B got 93 (difference not significant). Abstention was evaluated separately — [M3, arXiv 2507.01053](https://arxiv.org/pdf/2507.01053)
- EHR-ChatQA (ICLR 2026; MIMIC-IV-demo and eICU-demo; simulated users with vague or shifting questions): agents reach 90-95% Pass@5 on IncreQA and 60-80% on AdaptQA. Pass^5 (success in all five trials) is 35-60 points lower. Failures are attributed mainly to context management and SQL generation. Models named include o4-mini and Gemini-2.5-Flash — [EHR-ChatQA, arXiv 2509.23415](https://arxiv.org/abs/2509.23415)
- MedAgentBench (Stanford; 300 tasks, 100 patient profiles, FHIR environment):
  - At release (2025), the best model was Claude 3.5 Sonnet v2 with a 69.67% success rate — [MedAgentBench, arXiv 2501.14654](https://arxiv.org/pdf/2501.14654v2)
  - Stanford HAI (Oct 2026) says frontier models now exceed 90%, without naming models — [Stanford HAI](https://hai.stanford.edu/news/measuring-what-medical-ai-can-actually-do)
- Harder 2026 clinical agent benchmarks, all from the same HAI article:
  - PhysicianBench (100 long-horizon tasks, 21 specialties): GPT-5.5 completes 46% on a single attempt and only 28% across all three runs. 62% of GPT-5.5's failures, and 73% of Claude Opus 4.6's, were correct notes whose order was never placed.
  - HealthAdminBench (135 prior-authorisation, appeal and DME tasks): Claude Opus 4.6 reaches 36.3% end to end. GPT-5.4 passed 82.8% of subtasks but completed only 26.7% of whole tasks — [Stanford HAI](https://hai.stanford.edu/news/measuring-what-medical-ai-can-actually-do)

### Inferences
- "Spider 2.0-Snow 90%+" and "BEAVER 10.8%" both describe frontier agents on enterprise-style SQL. The gap points to benchmark construction (public schemas, curated questions, available documentation) as the main driver, not model choice. A buyer should treat any public-benchmark number as an upper bound for their own warehouse.
- Population-health analytics resembles BEAVER and BIRD-Interact (proprietary schemas, ambiguous questions, multi-step definitions) more than Spider 1.0 or BIRD dev.

### Gaps
- No entry dates are shown on the Spider 2.0 leaderboard, and it does not mark which entries are officially validated.
- BEAVER's final question count is unresolved.
- I found no published payer-claims or VBC-specific text-to-SQL benchmark. EHRSQL, MIMIC and eICU are inpatient and ICU clinical data, not claims or attribution data.

## What makes an accuracy claim credible

### Takeaway
Credible claims use held-out or contamination-controlled questions, execution-result matching against audited gold answers, repeated runs reported as consistency (pass^k), explicit scoring of abstention on unanswerable questions, and published error analysis. Benchmark gold labels themselves have high error rates, so independent re-auditing matters.

### Cited Findings
- Gold-label error rates: a UIUC audit found that 52.8% of BIRD Mini-Dev examples contain annotation errors, against a previously reported 32.3%. The audit was human-led, assisted by an AI reviewer with 83% precision — [Jin et al., PVLDB vol. 19, arXiv 2601.08778](https://arxiv.org/abs/2601.08778)
- Effect on rankings: the same audit re-evaluated 16 open-source BIRD agents on a corrected 100-example dev subset.
  - Relative accuracy changes ranged from -7% to +31%, and ranks moved by up to 9 positions. An earlier arXiv version reported -4% to +19%.
  - Rankings on corrected data correlated weakly with the original rankings (Spearman 0.32 / 0.23, versus 0.85 on uncorrected data) — [Jin et al.](https://arxiv.org/html/2601.08778v3); [author summary](https://ddkang.substack.com/p/pervasive-annotation-errors-break)
- Repeated runs: EHR-ChatQA reports pass@5 against pass^5 and finds gaps of 35-60 points — [EHR-ChatQA](https://arxiv.org/abs/2509.23415). PhysicianBench reports 46% single-attempt against 28% all-three-runs for GPT-5.5 — [Stanford HAI](https://hai.stanford.edu/news/measuring-what-medical-ai-can-actually-do)
- Abstention scoring: the EHRSQL 2024 shared task used TrustSQL's Reliability Score.
  - A correct answer or a correct abstention earns credit. A wrong answer on an answerable question, or any answer to an unanswerable one, is penalised.
  - The penalty is 0, 10 or N, where N is the test-set size, so a single error can outweigh all correct answers. RS(10) was the primary ranking metric.
  - The test set mixed original EHRSQL unanswerable questions with adversarial TrustSQL ones. Eight teams completed; LG AI Research and KAIST won — [EHRSQL 2024 overview, arXiv 2405.06673](https://arxiv.org/pdf/2405.06673); [ProbGate team paper](https://arxiv.org/pdf/2404.16659); [LG AI Research and KAIST paper](https://arxiv.org/pdf/2405.11162)
- Contamination control: LiveSQLBench reports success within date windows of newly released tasks — [benchmarklist LiveSQLBench](https://benchmarklist.com/benchmarks/livesqlbench/)
- Subtask scoring versus end-to-end scoring changes conclusions. HealthAdminBench used 1,698 verifiers (1,177 deterministic, 521 LLM-judge) and found that 82.8% of subtasks passed while only 26.7% of tasks completed — [Stanford HAI](https://hai.stanford.edu/news/measuring-what-medical-ai-can-actually-do)
- Vendor evaluation tooling: Databricks Genie benchmarks compare Genie's result to the result of an optional gold SQL statement; questions without gold SQL are marked for human review.
  - Databricks recommends two to four phrasings per question.
  - Benchmark SQL is not given to Genie as context, which keeps the evaluation set held out — [Databricks Genie benchmarks docs](https://docs.databricks.com/aws/en/genie/benchmarks)
- Recommended process: measure a baseline, run all benchmark questions, and document which pass and fail and why — [Databricks blog](https://www.databricks.com/blog/how-build-production-ready-genie-spaces-and-build-trust-along-way)
- Independent verification is largely absent for vendor headline numbers. An analysis of Genie One notes that its figures were measured by Databricks, on its own product, with no third-party verification — [Pebblous analysis](https://blog.pebblous.ai/blog/databricks-genie-one-governed-data/en/)

### Inferences
- A buyer-acceptable claim for "gets the right answer" would require:
  - a frozen, held-out question set authored from the buyer's own question intents;
  - result-set comparison against SQL audited by a second person;
  - multiple phrasings per intent;
  - at least three runs reported as all-runs consistency;
  - unanswerable or ambiguous questions included and scored with an abstention-aware metric such as RS(10);
  - a published per-question error taxonomy.
- Single-run execution accuracy on a public dev set is the weakest form of evidence.

### Gaps
- I could not extract the official per-team RS(0), RS(10) and RS(N) scores from the EHRSQL 2024 overview, because the PDF did not parse.
- I found no third-party audit of Snowflake's or Databricks' internal benchmark questions.

## Cost per question and latency for analytics agents and whole-chart reading

### Takeaway
Published cost figures are sparse and mostly incidental. LiveSQLBench reports about $0.06-0.18 per task for frontier models. BIRD methods range from about $0.04 to $0.46 per query. Whole-chart prompts run to roughly 75K-108K tokens on average per inpatient stay, and up to 1.4M. Retrieval cuts input to under about 8K tokens with small accuracy changes. No peer-reviewed per-chart dollar study was found.

### Cited Findings
- LiveSQLBench: o3 about $0.18 per task (29.54% success); Claude Sonnet 4 about $0.06 per task (27.01%) — [benchmarklist LiveSQLBench](https://benchmarklist.com/benchmarks/livesqlbench/)
- On BIRD, expensive multi-call methods cost up to $0.46 per query, versus $0.039 per query for a consistency-based method — summarised in search results, original paper not verified; listed among [arXiv 2510.10885](https://arxiv.org/html/2510.10885v1) and related work
- Datalake Agent (NeurIPS 2025): about 4,264 tokens per task at 319 tables, against 34,602 for a direct solver. That is roughly $12 against $100 per 1,000 tasks, and agent token use is nearly flat as table count grows. From a secondary summary — [papernotes summary](https://en.papernotes.org/NeurIPS2025/llm_agent/agentic_nl2sql_to_reduce_computational_costs/)
- Warehouse compute is a separate cost axis. LLM-generated queries showed up to 3.4x cloud-cost variance, with outliers above 36GB scanned per query. On BigQuery, execution time does not track cost — [Cost-Aware Text-to-SQL, arXiv 2512.22364](https://arxiv.org/html/2512.22364v1)
- Spider 2.0 evaluates correctness, not cost efficiency — [arXiv 2512.22364](https://arxiv.org/html/2512.22364v1)
- Databricks says its techniques "significantly" reduce cost and latency against a coding agent, and that parallel thinking adds tokens and latency. No figures are given [VENDOR] — [Databricks: pushing frontier data agents](https://www.databricks.com/blog/pushing-frontier-data-agents-genie)
- Chart length (RAG against long context over EHRs, 200 hospitalisations per task, o4-mini, GPT-4o-mini and DeepSeek-R1):
  - Average input per stay: imaging 74K tokens (range 17K-401K); antibiotics 108K (16K-1.4M); diagnosis 75K (20K-389K).
  - RAG with 60 x 128-token chunks (at most about 7.7K tokens) against the 128K-token recent-notes condition, for o4-mini: imaging F1 73.46 against 77.02; antibiotic medication F1 80.09 against 77.54; diagnosis F1 39.81 against 41.29. A rule-based antibiotic baseline scored Jaccard 0.465 against 0.59-0.62 for the LLMs.
  - The paper gives no dollar costs. Its text and tables disagree on one gap (5.56 against 3.56) — [Evaluating RAG vs Long-Context Input, arXiv 2508.14817](https://arxiv.org/html/2508.14817v1)
- Accuracy falls as pre-decision EHR history gets longer — [ObGynLongBench, arXiv 2609.07601](https://arxiv.org/pdf/2609.07601). Agents often peak using only recent information, with a lost-in-the-middle effect — [Long-Horizon Medical Reasoning](https://www.alphaxiv.org/overview/2607.09322)
- TRACE removed 47.3% of chart text while preserving extraction and outcome-prediction performance. The authors estimate about $9.5M per year in inference savings at a large academic medical center, assuming one query per encounter — [TRACE, arXiv 2604.16364](https://arxiv.org/abs/2604.16364)
- Lower-confidence vendor estimate: complex record summarisation uses 50K-150K input tokens and 1.5K-3K output tokens — [Taction Software blog](https://www.tactionsoft.com/blog/llm-cost-per-clinical-task/)

### Inferences
- Token cost scales roughly linearly with chart length for whole-chart reading. On the RAG study's averages, one stay is about 75K-108K input tokens, so a population-level question over thousands of patients is several orders of magnitude more expensive than one SQL generation, which runs to a few thousand tokens. To price a run, multiply token counts by the current model price; no published dollar figure covers this.
- A cost claim for analytics needs to include warehouse compute (bytes scanned), not only LLM tokens.

### Gaps
- No peer-reviewed dollar cost per patient chart for frontier models was found.
- No published latency distribution (p50/p95) for commercial analytics assistants was found.
- I did not verify the original source of the $0.46 and $0.039 BIRD per-query figures.

## How vendors publish accuracy, and what independent tests found

### Takeaway
Snowflake and Databricks publish headline accuracy from small internal question suites that are not released, graded by undisclosed methods, and compared against anonymised or generic baselines. The only public-benchmark ablation (Snowflake on BIRD subsets) is still vendor-run. No neutral audit of either product's claims was found.

### Cited Findings
- Snowflake Cortex Analyst [VENDOR]:
  - About 90% accuracy on an internal suite of about 150 business-style questions; GPT-4o reached 51% on the same set (search-result summary of the Snowflake engineering blog; the page returned 403 when fetched) — [Snowflake engineering blog](https://www.snowflake.com/en/blog/engineering/cortex-analyst-text-to-sql-accuracy-bi/)
  - Later materials say "~90% or higher" across customer evaluations — [Snowflake blog](https://www.snowflake.com/de/blog/cortex-analyst-ai-self-service-analytics/)
- Snowflake BIRD ablation [VENDOR, relayed by Atlan]: with the same model (Claude 3.5 Sonnet), accuracy rose from 57% without a semantic model to 78% with one. By dataset: Debit Card 52 to 83; California Schools 63 to 80; Thrombosis 45 to 70; Toxicology 69 to 79. Question count and judge are not stated — [Atlan](https://atlan.com/know/snowflake/cortex-analyst-vs-text-to-sql/)
- Snowflake CoWork (June 2026) [VENDOR, relayed]: coding agents via Snowflake MCP alone 23%; CoWork without context 47%; with Snowflake's runtime context layer 83% — [Atlan](https://atlan.com/know/snowflake/cortex-analyst-vs-text-to-sql/)
- Databricks Genie One [VENDOR]: 84.5% first-attempt accuracy against 52.4% for the strongest general-purpose coding agent (weakest 25%), on an internal 28-question real-world data-analysis suite, June 2026. Competitor agents are anonymised. The best agent without the ontology context reached about 50% — [Databricks Genie One blog](https://www.databricks.com/blog/introducing-genie-one-genie-ontology-and-genie-agents); [Atlan on Genie One](https://atlan.com/know/ai-agent/databricks/genie-one/)
- Databricks earlier claimed accuracy went from 32% to over 90% against a leading coding agent on an internal benchmark. Question count, grading and repeated runs were not stated [VENDOR] — [Databricks](https://www.databricks.com/blog/pushing-frontier-data-agents-genie)
- Genie "Trusted" label: a response is marked Trusted when it uses the exact text of a parameterised example query or SQL function the space author has curated. This is a provenance signal, not a measured accuracy — [Databricks Genie docs](https://docs.databricks.com/aws/genie/)
- AtScale claims 100% on its own NLQ benchmark [VENDOR, semantic-layer vendor] — [AtScale](https://atscale.com/blog/semantic-layer-cortex-analyst-accuracy/)
- Atlan, with Snowflake Intelligence [VENDOR-affiliated]: enriched metadata gave a 3x accuracy gain over schema-only prompts on 145 queries (p < 2e-10). An earlier study found a 38% relative gain on 522 queries — [Atlan](https://atlan.com/know/snowflake/cortex-analyst-vs-text-to-sql/)
- Independence: one analysis notes there is "no third-party independent verification" of Genie One's numbers — [Pebblous](https://blog.pebblous.ai/blog/databricks-genie-one-governed-data/en/). Atlan notes that vendors quote numbers from different evaluation methodologies — [Atlan](https://atlan.com/know/snowflake/cortex-analyst-vs-text-to-sql/)

### Inferences
- The prevailing vendor standard is an internal suite of tens to about 150 questions, an anonymised competitor, and a single headline number. A rigorous evidence pack (released questions, repeated runs, abstention scoring, error taxonomy) would exceed the current market bar, not merely match it.

### Gaps
- The Snowflake engineering blog returned 403, so its grading method (execution match or LLM judge) was not directly confirmed.
- No analyst firm head-to-head test with a disclosed method was found.

## Evidence on reusing verified queries, semantic layers and caching

### Takeaway
Adding curated semantics consistently raises accuracy in vendor and vendor-adjacent studies: 16.7% to 54.2% (knowledge graph), 57% to 78% (Snowflake on BIRD), 64.5% to 98-100% for top models (dbt, 2026). These studies use small question sets. Caching research shows high safe hit rates for analytical query reuse and large latency cuts, but no peer-reviewed end-to-end dollar comparison of cache or semantic reuse against calling a frontier model every time was found.

### Cited Findings
- data.world (2023, arXiv preprint): GPT-4 zero-shot text-to-SQL over an insurance enterprise schema scored 16.7%; with a knowledge-graph representation, 54.2% — [BigDATAwire](https://www.bigdatawire.com/2023/12/13/new-data-world-report-finds-a-technique-for-making-llms-3x-more-accurate-in-answering-business-questions)
- dbt replication (2023, GPT-4, few-shot dbt Semantic Layer syntax): 83% on the eight addressable questions. Delphi later reported 100%, excluding questions that need multi-hop joins — [dbt-labs/semantic-layer-llm-benchmarking](https://github.com/dbt-labs/semantic-layer-llm-benchmarking); [Delphi](https://delphihq.substack.com/p/delphi-at-100-dbt-semantic-layer)
- dbt 2026 benchmark (11 questions, 20 runs per question, four configurations), as relayed by Atlan:
  - Claude Sonnet 4.6: raw 90.0%, modeled semantic layer 98.2%. GPT-5.3-codex: raw 84.1%, modeled 100%.
  - Overall raw text-to-SQL rose from 32.7% (2023) to 64.5% (2026). The overall modeled-layer figure is 72.7%; how it relates to the per-model results is not explained.
  - The "modeled" configuration added dbt models built for questions the semantic layer could not otherwise express — [Atlan](https://atlan.com/know/snowflake/cortex-analyst-vs-text-to-sql/); [dbt blog](https://www.getdbt.com/blog/semantic-layer-as-the-data-interface-for-llms)
- Semantic caching for OLAP (arXiv 2602.19811, Feb 2026):
  - Canonical "OLAP Intent Signature" keys achieved an 82% hit rate, against 28% for text keys and 56% for AST keys, with zero false hits on TPC-DS, SSB and NYC TLC.
  - Reuse requires exact intent equality, and low-confidence natural-language translations bypass the cache — [arXiv 2602.19811](https://arxiv.org/html/2602.19811v1)
- TableCache (arXiv 2601.08743): precomputed schema KV caches give up to 3.62x faster time to first token with negligible accuracy loss — [arXiv 2601.08743](https://web3.arxiv.org/abs/2601.08743)
- An industry post reports that parameterised query templates cut Text2SQL latency by 80% and tokens by over 50% (unverified) — [thenote.app](https://thenote.app/post/en/reducing-text2sql-latency-with-parameterized-query-templates-xt1qehuksk)
- Context or ontology reuse is the main variable in vendor ablations: Genie One rose from about 50% to 84.5% with context — [Atlan](https://atlan.com/know/ai-agent/databricks/genie-one/). Snowflake rose from 47% to 83% with its context layer — [Atlan](https://atlan.com/know/snowflake/cortex-analyst-vs-text-to-sql/)
- In the EHR setting, retrieval of targeted context matches or approaches full-context accuracy with about 10-15x fewer input tokens — [arXiv 2508.14817](https://arxiv.org/html/2508.14817v1)

### Inferences
- The defensible claim for reuse is twofold. Reuse raises correctness because verified definitions remove ambiguity. Its cost advantage follows mechanically from avoiding LLM calls on cache hits, at about $0.06-0.18 per agent task on LiveSQLBench pricing, plus avoided warehouse scans. A cost-fraction claim would need the buyer's own hit rate, measured on held-out traffic, with false-hit rate reported as in arXiv 2602.19811.

### Gaps
- No peer-reviewed total-cost comparison of a semantic layer or verified-query cache against calling a frontier model on every question was found.
- All semantic-layer accuracy studies use 8-150 questions and are vendor-run or vendor-adjacent.
