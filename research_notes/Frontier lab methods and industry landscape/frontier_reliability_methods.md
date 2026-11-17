# Frontier lab methods for reliability, calibration, abstention, evaluation and agentic data analysis (for Agere)

Scope note: research date 2026-11-17. Pages marked "opened" were fetched directly. Claims marked [snippet-only] come from search-result summaries because the primary page refused automated fetching (openai.com returned HTTP 403; nature.com and PMC redirected to login/bot checks). arXiv abstract pages rarely list affiliations; lab attribution is noted where the page or repository showed it, otherwise stated as "attribution not confirmed on page".

Agere context used for judging fit: saved recipes, built-in SQL, classifier, small model, frontier text-to-SQL rung, governed definitions, clarifying conversation, note-reading ladder, conformal intervals already built; planned wrong-answer-penalised reliability score with agreement-gated abstention; every method must beat a simple baseline and LLM-alone on accuracy and cost.

## Calibration and "knowing what you know" (P(IK), abstention-aware grading, DeepMind abstention, semantic entropy)

### Takeaway
The strongest lab-backed idea Agere can adopt directly is OpenAI's 2025 confidence-target grading rule. It awards +1 for a correct answer, 0 for "I don't know" and -t/(1-t) for a wrong answer. It matches Agere's planned wrong-answer-penalised score almost exactly and needs no new model. A DeepMind-authored 2024 paper on conformal abstention puts a formal error-rate bound on agreement-gated abstention, which fits Agere's existing conformal machinery. Sampling-based semantic entropy (Oxford) and Anthropic's P(True)/P(IK) are useful baselines, but they cost 5-10x more compute or need logits and training, and the pages I opened report no numbers that justify them over simple agreement.

### Cited Findings
- **Anthropic, "Language Models (Mostly) Know What They Know"** (Kadavath, Conerly, Askell et al.; arXiv v1 2022-07-11, v4 2022-11-21; opened). **P(True)**: the model proposes an answer, then estimates the probability that its own answer is correct. Self-evaluation gets better when the model sees many of its own samples before judging one. **P(IK)**: a trained head predicts whether the model knows the answer, without seeing a candidate. P(IK) "partially generalizes across tasks" but struggles to stay calibrated on new tasks. Larger models are well calibrated on multiple-choice and true/false questions when the format is right. The abstract gives no calibration numbers. — [arXiv 2207.05221](https://arxiv.org/abs/2207.05221)
- **OpenAI, "Why Language Models Hallucinate"** (Kalai, Nachum, Zhang of OpenAI, with Vempala of Georgia Tech; 2025-09-04; opened, arXiv HTML). Binary right/wrong grading rewards guessing, so models are "optimized to be good test-takers". The fix is to change how mainstream benchmarks are scored. The proposed prompt instruction is "Answer only if you are >t confident, since mistakes are penalized t/(1-t) points". Correct = 1, IDK = 0. Example thresholds: t=0.5 (penalty 1), t=0.75 (penalty 2), t=0.9 (penalty 9). Answering beats abstaining in expectation only when confidence exceeds t. — [arXiv 2509.04664 HTML](https://arxiv.org/html/2509.04664)
- **OpenAI SimpleQA comparison from the companion blog post** [snippet-only; the blog returned 403]: gpt-5-thinking-mini had 52% abstention, 22% accuracy and 26% error. o4-mini had 1% abstention, 24% accuracy and 75% error. Accuracy is about the same, but the error rate is nearly 3x higher when the model almost never abstains. — [OpenAI blog (search result)](https://openai.com/index/why-language-models-hallucinate/)
- **Conformal abstention, "Mitigating LLM Hallucinations via Conformal Abstention"** (Abbasi Yadkori, Kuzborskij, Stutz, György, Fisch, Doucet, ... Cemgil, Tomasev; 2024-04/05; opened; author list is DeepMind researchers, but the page shows no affiliation). The LLM judges whether its own sampled answers to a query agree (self-consistency). Conformal prediction then sets an abstention rule with a guaranteed bound on the hallucination rate, and also calibrates the threshold for when two answers count as equivalent. Results (qualitative on the abstract): the bound holds across datasets. The method abstains "significantly less" than log-probability baselines on Temporal Sequences (long answers) and is comparable on TriviaQA (short answers). The abstract gives no numbers. — [arXiv 2405.01563](https://arxiv.org/abs/2405.01563)
- **Semantic entropy** (Kuhn, Gal, Farquhar; Oxford; ICLR 2023 Spotlight; opened). Sampled answers are clustered by meaning, and entropy is computed over the meaning clusters. The method is unsupervised and works with off-the-shelf models. It is "more predictive of model accuracy ... than comparable baselines", but the abstract gives no numbers. — [arXiv 2302.09664](https://arxiv.org/abs/2302.09664). The Nature 2024 follow-up (Farquhar et al.) could not be opened because nature.com redirected to login and PMC showed a bot check. Its AUROC/AURAC numbers are therefore a gap. — [Nature s41586-024-07421-0 (not opened)](https://www.nature.com/articles/s41586-024-07421-0)

### Inferences
- **OpenAI confidence-target scoring — TRIAL, high priority, near-zero cost.** It is a scoring and policy rule, not a model. Agere should make its reliability score explicitly a t/(1-t) penalty, with t set per decision class: for example t=0.9 (penalty 9) for payer/VBC figures that drive money, and a lower t for exploratory questions. Then report accuracy, error and abstention separately, as in OpenAI's SimpleQA table. A model with the "best accuracy" can be the worst under penalised scoring. The 52%/26% vs 1%/75% example shows why.
- **Conformal abstention (DeepMind-authored) — TRIAL.** This is the formal version of Agere's planned agreement-gated abstention. Agere already has conformal intervals, so the remaining work is to calibrate an agreement threshold on held-out questions to get a user-stated error bound (for example, "at most 5% wrong among answered"). Simple baselines to beat: (a) a fixed rule such as "two of three rungs agree on the executed result set", and (b) LLM-alone with self-reported confidence. Agreement on SQL should be judged by execution-result equality, not text similarity. That is cheaper and more exact than an LLM equivalence judge.
- **P(True) / P(IK) — PARK.** P(IK) needs training a head on logits, which frontier APIs don't expose. P(True) prompting is cheap, but the paper says calibration degrades on new tasks. Use it only as a baseline arm in the abstention ablation.
- **Semantic entropy — SKIP as a production signal for SQL.** Its sampling overhead is 5-10x (see the probes paper below). For SQL, execution-result agreement is a cheaper and stricter stand-in for clustering answers by meaning. It may still be worth trialling on the free-text note-reading ladder, where answers can't be executed.

### Gaps
- Numeric AUROC/AURAC for semantic entropy vs P(True) and naive entropy (Nature 2024): the page was not accessible.
- The numeric results of the conformal abstention paper: only the abstract was read.
- I did not find or open a Google DeepMind study that measures abstention on text-to-SQL or analytics specifically.
- The OpenAI SimpleQA numbers are snippet-only and could not be verified on the primary page.

## Evaluation practice (statistical evals, eval guidance, LLM-as-judge validity)

### Takeaway
Anthropic's Nov 2024 "statistical approach to model evals" fits Agere's "beat a baseline and LLM-alone" rule directly. It calls for standard errors on every score, clustered SEs when questions share a source, paired differences between arms run on the same questions, and power analysis to size the question set. LLM-as-judge needs validation against human labels for each task.

### Cited Findings
- **Anthropic, "A statistical approach to model evaluations"** (2024-11-19; opened). Recommendations:
  1. Report the SEM, with 95% CI = mean ± 1.96·SEM.
  2. Cluster standard errors on the unit of randomization. "Clustered standard errors on popular evals can be over three times as large as naive standard errors."
  3. Reduce variance within questions by resampling each question several times and averaging (Inspect `epochs`), or by using next-token probabilities.
  4. Analyse paired differences. Frontier models' per-question scores typically correlate 0.3-0.7, and that correlation shrinks the SE of the difference. Report mean differences, SEs, CIs and correlations.
  5. Use power analysis, for example to size a set that can detect a 3-percentage-point difference.

  — [Anthropic research](https://www.anthropic.com/research/statistical-approach-to-model-evals)
- **LLM-as-judge validity** (JUDGE-BENCH; Bavaresco et al.; academic consortium, not a frontier lab; ACL 2025; v1 2024-06-26; opened). 11 LLMs were tested against human annotations on 20 datasets. Reliability "varies substantially" across models and datasets. It depends on the property judged, the expertise of the human judges and whether the text is human- or model-written. Conclusion: LLMs "should be carefully validated against human judgments before being used as evaluators." — [arXiv 2406.18403](https://arxiv.org/abs/2406.18403)

### Inferences
- **Anthropic statistical eval practice — TRIAL (adopt as the acceptance method).** Every Agere baseline comparison should be a paired test on the same question set:
  - recipe vs built-in SQL vs SLM vs frontier rung vs LLM-alone;
  - SEs clustered by source (patient, question template or warehouse table), since Agere questions share populations and templates;
  - power analysis before trials, so that "beats baseline" claims can be detected at a stated difference.

  This costs almost nothing in compute.
- **LLM-as-judge — PARK for answer correctness.** Agere's SQL answers can be scored by execution-result match, which is cheaper and valid by construction. Use an LLM judge only for free-text interpretation or clarifying-question quality, and only after measuring agreement with a human-labelled subset.

### Gaps
- I did not open OpenAI's evals guidance page (openai.com blocks automated fetching) or a lab-authored LLM-judge validity study. Google's and OpenAI's own judge-validity work is not covered.

## Agentic data analysis and text-to-SQL from labs

### Takeaway
On hard enterprise SQL, even frontier agents perform poorly: o1-preview scores 21.3% on Spider 2.0, against 91.2% on Spider 1.0 and 73.0% on BIRD. This supports Agere's design of recipes and governed definitions first, with the frontier rung as a fallback. Google's CHASE-SQL shows that generating several candidates and picking one with a trained selector reached state of the art on BIRD (73.0%). Anthropic's agent guidance argues for the simplest workflow that works.

### Cited Findings
- **CHASE-SQL** (Pourreza, Li, Sun, Chung, Talaei, Kakkar, Gan, Saberi, Ozcan, Arik; 2024-10-02; opened; author list is mostly Google Cloud AI Research and Stanford, but the page shows no affiliation). Candidate generation uses three generators: divide-and-conquer, chain-of-thought based on query execution plans, and synthetic few-shot examples tailored to each question. A fine-tuned selector LLM then picks among candidates through pairwise comparisons. Results: 73.0% on the BIRD test set and 73.01% on dev, top of the BIRD leaderboard when submitted. The abstract gives no baseline numbers. — [arXiv 2410.01943](https://arxiv.org/abs/2410.01943)
- **Spider 2.0** (Lei, ... Pengcheng Yin, ... Ruoxi Sun, ... Sida Wang, Tao Yu; v1 2024-11-12, ICLR 2025 Oral; opened; co-authors include Google and Salesforce researchers per the names, but affiliations are not on the page). The benchmark has 632 enterprise workflow tasks on BigQuery and Snowflake, often with more than 1,000 columns, dialect documentation and SQL longer than 100 lines. An o1-preview code agent succeeds on 21.3% of Spider 2.0, against 91.2% on Spider 1.0 and 73.0% on BIRD. — [arXiv 2411.07763](https://arxiv.org/abs/2411.07763)
- **Anthropic, "Building effective agents"** (2024-12-19; opened). Key points:
  - Start with the simplest solution and add complexity only when it measurably improves results.
  - Workflows (predefined code paths) suit well-defined tasks; agents trade latency and cost for flexibility and can compound errors.
  - Patterns: prompt chaining, routing, parallelization (sectioning or voting), orchestrator-workers and evaluator-optimizer.
  - Tool design: put as much effort into the agent-computer interface as into the human one, write tool docs like docstrings with examples and edge cases, and mistake-proof arguments ("poka-yoke").

  — [Anthropic engineering](https://www.anthropic.com/engineering/building-effective-agents)

### Inferences
- **CHASE-SQL-style generate-then-select — TRIAL, but only on the frontier rung and only against a baseline.** Agere's agreement-gated abstention already produces several candidates. Adding a selector (pairwise or execution-based) is a modest extension. Cost is roughly N times the generations plus selection calls. Baselines to beat on Agere's own questions, with paired SEs: single-shot frontier SQL, and majority vote on execution results. Fine-tuning a selector LLM (as CHASE did) should come after a vote-based selector fails.
- **Spider 2.0 numbers support Agere's ladder: TRIAL as the external reality check.** Real warehouse schemas are where free-form LLM SQL collapses. Governed definitions and recipes are the right primary path, and abstention matters most on the frontier rung.
- **Anthropic agent patterns — TRIAL as design discipline (no new model).** Agere's ladder is already a "routing" workflow plus "parallelization/voting". Keep it a workflow rather than an open-ended agent for governed answers. The poka-yoke advice maps onto tool contracts such as typed population/date-scope parameters and governed-definition IDs in place of free-text column names.

### Gaps
- I did not open pages on Gemini data agents (BigQuery data agents), OpenAI's data-analysis agent, or the current Spider 2.0 leaderboard, so there are no 2025-2026 numbers for lab entries.

## Clarifying-question research from labs (when to ask vs answer)

### Takeaway
Google-authored work (ACT, ICLR 2025) trains models to decide when to ask a clarifying question, with a text-to-SQL ambiguity task (AmbigSQL) included. It is relevant to Agere's clarifying conversation, but it requires preference-tuning a model, and the abstract reports no numbers.

### Cited Findings
- **ACT: Action-Based Contrastive Self-Training** (Maximillian Chen, Ruoxi Sun, Tomas Pfister, Sercan Ö. Arık; v1 2024-05-31, v2 2025-07-27; ICLR 2025; opened; the code repo is under google-research, but the page shows no affiliation). ACT is a quasi-online, DPO-based method for learning when to ask clarifying questions, and it works without action labels. Tasks: tabular question answering, machine reading comprehension and **AmbigSQL** (resolving ambiguous requests in complex SQL generation for data-analysis agents). The paper claims "substantial conversation modeling improvements" over SFT and DPO, but the abstract gives no numbers. — [arXiv 2406.00222](https://arxiv.org/abs/2406.00222)

### Inferences
- **ACT training — PARK. AmbigSQL as a test-set design pattern — TRIAL.** Agere already has a working clarifying conversation built on a frontier model. The cheap step is to build an AmbigSQL-like set of ambiguous population-health questions and score ask-vs-answer decisions with the penalised score: an unnecessary question costs a little, and a wrong answer to an ambiguous question costs a lot. Consider preference-tuning only if prompted ask/answer decisions measurably fail.

### Gaps
- I did not open Anthropic or OpenAI papers on ambiguity or clarification, and found none in this pass. I have no numbers on clarify-vs-answer trade-offs from labs.

## Interpretability and monitoring methods with practical value now (probes)

### Takeaway
Semantic entropy probes (Oxford) estimate uncertainty from one generation's hidden states at near-zero extra cost. But they need access to model internals, so they apply only to Agere's local small model, not to frontier API rungs.

### Cited Findings
- **Semantic Entropy Probes** (Kossen, Han, Razzak, Schut, Malik, Gal; Oxford; 2024-06-22; opened). Linear probes on the hidden states of a single generation approximate semantic entropy. Full semantic entropy costs 5-10x the compute, and the probes cut that overhead "to almost zero". They "retain high performance for hallucination detection" and "generalize better to out-of-distribution data" than probes that predict accuracy directly. The abstract gives no numbers. — [arXiv 2406.15927](https://arxiv.org/abs/2406.15927)

### Inferences
- **SEPs — PARK. A possible later trial on the local SLM rung only.** A probe needs labelled training data from Agere's own workload and must beat the simple baseline of agreement with the built-in SQL or recipe result. Maturity: research-grade, with no production tooling. Frontier-lab interpretability work (for example Anthropic's) is not deployable on API models by customers.

### Gaps
- No lab-published, production-ready hallucination-probe tooling was found or opened.

## Fine-tuning and distillation options from labs, with measured gains on narrow tasks

### Takeaway
Lab fine-tuning gives large gains on narrow tasks. Anthropic reports content-moderation accuracy of 81.5% → 99.6% with fine-tuned Claude 3 Haiku on Bedrock, and Distyl's fine-tuned GPT-4o reached 71.83% on the BIRD test set. Platform availability is a risk: a search snippet says OpenAI is winding down its fine-tuning platform as of May 2026.

### Cited Findings
- **Claude 3 Haiku fine-tuning in Amazon Bedrock** (preview 2024-07-10 in US West (Oregon); generally available per a 2024-11-01 update; opened). Text fine-tuning supported up to 32K context. Anthropic's internal content-moderation test improved classification accuracy from 81.5% to 99.6% and used 85% fewer tokens per query. SK Telecom reported 73% more positive feedback and a 37% improvement on telecom KPIs. The page claims fine-tuned Haiku can replace Sonnet or Opus at lower cost and latency but gives no comparison numbers. — [Claude blog](https://claude.com/blog/fine-tune-claude-3-haiku)
- **OpenAI GPT-4o fine-tuning** (launched August 2024) [snippet-only; openai.com blocks fetches]. Distyl's fine-tuned GPT-4o scored 71.83% on the BIRD test set (67.21% dev). Pricing: $25/M training tokens; inference $3.75/M input and $15/M output. A May 8, 2026 update on the page reportedly says OpenAI is winding down the fine-tuning platform: no new users, and existing users can create jobs for a limited time. — [OpenAI GPT-4o fine-tuning (search result)](https://openai.com/index/gpt-4o-fine-tuning/); [Distyl blog (search result)](https://www.distyl.ai/blog/research/distyl-takes-1-spot-on-bird-benchmark); [SiliconANGLE 2024-08-20 (search result)](https://siliconangle.com/2024/08/20/openai-makes-fine-tuning-gpt-4o-customization-generally-available/)

### Inferences
- **Hosted fine-tuning of a small frontier model (Haiku on Bedrock) for Agere's classifier and routing step — PARK until a measured failure exists.** The 81.5→99.6% moderation result shows that narrow classification can improve a lot, and the token savings bear on cost. But Agere already has a local classifier and SLM. The baseline to beat is the existing local classifier plus prompted Haiku. There is also a standing-boundary issue: Bedrock fine-tuning would be a new external service and would send training data to a cloud provider. AGENTS.md says "No ... new services", so this would need explicit user direction.
- **OpenAI fine-tuning/distillation — SKIP.** Snippet evidence says the platform is winding down (May 2026), and the BIRD gain (71.83%) is no better than CHASE-SQL's untrained-generator multi-candidate pipeline (73.0%). Neither result transfers to Spider 2.0-style enterprise schemas without evidence.

### Gaps
- No lab-published, measured fine-tuning gain for text-to-SQL on Claude Haiku/Bedrock or Gemini tuning was found. I did not open Gemini tuning pages.
- The OpenAI fine-tuning wind-down is snippet-only and should be confirmed on the primary page before it drives a decision.

### Summary table (method / lab / date / recommendation)
| Method | Lab | Date | Agere problem | Maturity | Rec. |
|---|---|---|---|---|---|
| Confidence-target penalised grading t/(1-t) | OpenAI | 2025-09 | Reliability score, abstention policy | Scoring rule, ready | Trial (high) |
| Conformal abstention via self-consistency | Google DeepMind authors | 2024-05 | Agreement-gated abstention with error bound | Research, simple to implement | Trial |
| Statistical evals (clustered SE, paired diffs, power) | Anthropic | 2024-11 | Baseline-beating acceptance tests | Ready | Trial (adopt) |
| Generate-then-select SQL (CHASE-SQL) | Google | 2024-10 | Frontier text-to-SQL rung accuracy | Research/SOTA on BIRD | Trial vs vote baseline |
| Workflow/routing patterns, tool poka-yoke | Anthropic | 2024-12 | Ladder design, tool contracts | Guidance | Trial (design) |
| Spider 2.0 as reality check | Academic + Google/Salesforce co-authors | 2024-11 | Enterprise schema difficulty | Benchmark | Trial (eval) |
| ACT clarify-vs-answer training; AmbigSQL | Google authors | 2024-05/2025 | Clarifying conversation | Research | Park (training); Trial (test-set pattern) |
| P(True)/P(IK) | Anthropic | 2022 | Self-confidence | Research; P(IK) needs logits | Park (baseline arm) |
| Semantic entropy | Oxford | 2023/2024 | Hallucination detection | Research, 5-10x cost | Skip for SQL; possible note-reading trial |
| Semantic entropy probes | Oxford | 2024-06 | Cheap uncertainty for local SLM | Research | Park |
| Claude Haiku fine-tuning (Bedrock) | Anthropic/AWS | 2024-07/11 | Classifier/router | GA | Park (new-service boundary) |
| GPT-4o fine-tuning/distillation | OpenAI | 2024-08; wind-down 2026-05 [snippet] | SQL rung | Being retired [snippet] | Skip |
