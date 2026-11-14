# Self-improving agent systems, reusable libraries, learning from logs, and clinical document reading (arXiv 2025 to November 2026)

Reading depth legend used throughout: **[FULL]** = arXiv HTML full text read (via an automated page reader; long pages were truncated at 100k characters, noted where relevant). **[ABS]** = abstract page only. **[SNIPPET]** = search-result snippet only, not opened. All results are author-reported, not independently verified. "Test here" lines state how the technique would be compared against the current simple mechanism (two-model row agreement + regression set for recipes; typed-judgment reader + small/frontier resolution + mandatory quote + code counting for documents).

Excluded as already reviewed: 2609.37588, 2610.21412, 2610.32813, 2609.15779, 2606.23301, 2608.03451, 2607.24343, 2610.35868, 2310.19791, 2606.02994. No direct successors to those were found in this pass.

## 1. What methods build and maintain reusable skill, tool or program libraries from experience, and what do they report when the library grows large?

### Takeaway
The strongest 2026 evidence says the bottleneck is library governance, not authoring: unmanaged growth can push performance below a no-library baseline, outcome-based retirement with a generous trial minimum helps, over-eager retirement is worse than none, and embedding-similarity routing alone captures only about a quarter of the gain of a gated "use one or none" router. For SQL specifically, exact-equality matching on a structured intent key gave zero false reuse on controlled workloads, while natural-language-to-key mapping was the weak link (44 to 51% accuracy on ambiguous inputs).

### Cited Findings

**Library Drift: Diagnosing and Fixing a Silent Failure Mode in Self-Evolving LLM Skill Libraries (arXiv 2605.19576, AWS + HSBC, ICML 2026 listing) [FULL]** and its companion **Ratchet: A Minimal Hygiene Recipe for Self-Evolving LLM Agents (arXiv 2605.22148) [FULL]**. The two papers report the same MBPP+ experiment; treat as one evidence source.
- Defines "library drift": accumulated skills lower performance below a no-skill baseline through three sub-modes: stagnation (skills never reach the solver), bloat (unbounded growth makes injections harmful), erosion (over-aggressive retirement collapses the bank) — [2605.19576](https://arxiv.org/html/2605.19576v1)
- Method: frozen model writes, routes, curates and retires natural-language skills. Per-skill contribution score = (successes − failures) / trials from an append-only evidence log; a critic labels each failure helped / hurt / neutral / inapplicable; new skills are synthesised only from clusters of at least 3 failures sharing a pattern — [2605.19576](https://arxiv.org/html/2605.19576v1)
- Retirement rule: retire only after at least 100 trials and contribution ≤ −0.10; hard cap of 50 active skills with lowest-contribution eviction; retired skills are flagged deprecated, never deleted, and evidence stays addressable for reinstatement; version field increments on resynthesis — [2605.22148](https://arxiv.org/html/2605.22148v1)
- Reuse guard: router picks exactly one skill or none; rollback to the best snapshot only after five consecutive regressions more than 0.10 below the running best — [2605.22148](https://arxiv.org/html/2605.22148v1)
- Result scope: MBPP+ "hard-100" (100 tasks left after removing those solved by all 5 baseline seeds; 60 train / 40 eval), Claude Opus 4.7 for all calls, 100 rounds, 3 seeds. Default: held-out pass@1 gain +0.328 ± 0.018 over baseline 0.258; router engaged on about 73% of eval tasks; about 152 skills synthesised, 89 deprecated, 50 active — [2605.22148](https://arxiv.org/html/2605.22148v1)
- Ablations (same scope): no injection +0.002; retrieval-only routing (no model gate, 98% engagement) +0.077 ± 0.065; no authoring meta-skill +0.187; cap raised to 100 gives +0.317 ± 0.110 (same mean, far higher variance) — [2605.19576](https://arxiv.org/html/2605.19576v1)
- **Negative result:** harsh retirement (minimum 20 trials, threshold 0) fell below the no-skill baseline (−0.019 ± 0.010); the bank collapsed to 2 skills — [2605.19576](https://arxiv.org/html/2605.19576v1)
- **Negative result:** removing pattern canonicalisation or the "already covered" guard (both cosine 0.85) did not hurt (+0.374 and +0.363 vs +0.328, within noise per authors); explicit deduplication was unnecessary at 50-skill scale — [2605.19576](https://arxiv.org/html/2605.19576v1)
- Cost: about 14.5k model calls per 100 rounds, 43% more than no-skill; wall time 6.5 h vs 2.3 h — [2605.19576](https://arxiv.org/html/2605.19576v1)
- Second benchmark (described by authors as preliminary): SWE-bench Verified hard-150, Claude Code solver, 60 eval tasks, 20 rounds, 3 seeds: baseline 0.65, peak 0.87 — [2605.22148](https://arxiv.org/html/2605.22148v1)
- Author limitations: one model; thresholds empirical; retirement measures correlation not causation; critic noise can become persistent skills; 3 seeds — [2605.22148](https://arxiv.org/html/2605.22148v1)
- Test here: add a per-recipe evidence log (serves, later contradictions, regression-set flips) and compare "retire after N≥100 serves with net-negative contribution" against the current never-retire policy on replayed logged questions, measuring wrong-answer rate and no-model answer share.

**How Well Do Agentic Skills Work in the Wild: Benchmarking LLM Skill Usage in Realistic Settings (arXiv 2604.04323, Liu, Ji, An, Jaakkola, Zhang, Chang) [ABS]**
- Agents must retrieve from a collection of 34k real-world skills rather than hand-curated ones; gains from skills degrade as settings become more realistic, approaching no-skill baselines in the hardest scenarios; authors call skill benefits "fragile" — [2604.04323](https://arxiv.org/abs/2604.04323)
- Query-specific refinement of retrieved skills substantially recovers performance when the initial skills are reasonably relevant; on Terminal-Bench 2.0, Claude Opus 4.6 pass rate rises from 57.7% to 65.5% with retrieval and refinement — [2604.04323](https://arxiv.org/abs/2604.04323)
- Abstract gives no per-setting pass rates for curated versus retrieved skills.
- Test here: measure recipe-match precision as a function of library size by replaying held-out questions against libraries of 50, 100, 180 and (bulk-learned) 500+ recipes, versus the current assumption that match precision is constant.

**SE-GoS: Self-Evolving Graph-of-Skills for Skill Library at Scale (arXiv 2609.08228) [ABS]**
- Training-free: keeps skills, retriever and model fixed and edits only the retrieval graph from execution traces (adds relations from evidence, removes an "avoid" edge only after repeated successful co-use, down-weights unsupported semantic edges, rewrites retrieval-facing descriptions of skills ranked too low) — [2609.08228](https://arxiv.org/abs/2609.08228)
- SkillsBench, one evolution round: average reward 52.4% to 59.4%; held-out split not used for evolution 52.9% to 58.3%; about two-thirds the input tokens of full-library loading; three backbones (unnamed in abstract) — [2609.08228](https://arxiv.org/abs/2609.08228)
- **Negative result:** repeating the evolution round does not improve results — [2609.08228](https://arxiv.org/abs/2609.08228)
- Test here: rewrite the matching description/pattern of recipes that should have fired but were missed (found from takeover logs), and compare recipe hit rate and false-hit rate on a held-out question family against unedited patterns.

**Semantic Caching for OLAP via LLM-Based Query Canonicalization (arXiv 2602.19811, Bindschaedler, MPI-SWS; extended DOLAP 2026) [FULL]**
- Both SQL and natural-language requests are mapped to one structured "intent signature" (measures, grouping levels, filters, time window, post-aggregation operators), hashed; reuse requires exact signature equality; requests failing validation (measure/dimension existence, time-window resolution, unambiguous join path) bypass the cache — [2602.19811](https://arxiv.org/html/2602.19811v1)
- Hit rate averaged over NYC TLC, SSB, TPC-DS: 82.0% vs 28.2% normalised text, 55.6% AST canonicalisation, 77.3% NL-to-SQL+AST; zero false hits across 1,395 controlled queries (945 SQL, 450 NL) — [2602.19811](https://arxiv.org/html/2602.19811v1)
- Roll-up and filter-down derivations (answer from a cached finer result under stated preconditions; disabled under ORDER BY/LIMIT and non-additive measures) raised hit rate 37% to 80% on a hierarchical SSB workload with zero false hits — [2602.19811](https://arxiv.org/html/2602.19811v1)
- **Negative result:** NL-to-signature accuracy was 44.4% on 63 adversarial ambiguous queries and 51.3% on 150 BIRD questions; model self-reported confidence is uncalibrated (threshold 0.5: 76.9% precision at 36.5% coverage); schema heuristics raised precision 48.3% to 69.0% while bypassing 54% of queries — [2602.19811](https://arxiv.org/html/2602.19811v1)
- Limitations: star/snowflake aggregations only (14 of 99 TPC-DS templates qualify); synthetic controlled variants may overstate hit rates; invalidation under updates not characterised — [2602.19811](https://arxiv.org/html/2602.19811v1)
- Test here: derive a canonical signature (measure, grain, filters, window) for each of the 180 recipes and check (a) how many recipe pairs collapse to one signature (deduplication) and (b) whether signature-equality matching produces fewer wrong reuses than the current typed-slot pattern match on paraphrase sets.

**Semantic Caching and Intent-Driven Context Optimization for Multi-Agent Natural Language to Code Systems (arXiv 2601.11687, Singh) [ABS]**
- Production enterprise analytics deployment: dual-threshold decision separating exact reuse from "reference-guided generation" (a near-miss cached result is given to the generator as a hint rather than served); 67% cache hit rate over more than 10,000 production queries, 40 to 60% token reduction, 94.3% "semantic accuracy" — [2601.11687](https://arxiv.org/abs/2601.11687)
- No false-hit rate is reported in the abstract; single deployment, single author.
- Test here: add a middle band where a near-match recipe is passed to the takeover model as a starting query instead of being either served or ignored; compare takeover cost and agreement rate against cold generation.

**Not only a helper, but also a teacher: Interactive LLM Cascade (arXiv 2509.22984, Wu et al.) [FULL, truncated]** (library built from the strong model's answers; also relevant to section 3)
- When the strong model answers a deferred query it also writes a generalised "strategy"; (query, strategy) pairs are stored and the top-2 most similar are prepended for the weak model next time — [2509.22984](https://arxiv.org/html/2509.22984v1)
- GPT-3.5-turbo weak / Gemini-2.0-flash strong: strong-model call rate fell 59.37% to 30.84% (GSM-Symbolic), 46.29% to 32.44% (GSM-Plus), 49.22% to 34.98% (MetaMath), 26.68% to 22.54% (NASA-History); pipeline accuracy rose 0.8 to 5.5 points — [2509.22984](https://arxiv.org/html/2509.22984v1)
- Limitations: strategies are auto-accepted without verification; one model pair; repository pruning under distribution shift discussed only briefly — [2509.22984](https://arxiv.org/html/2509.22984v1)
- Test here: when the frontier takeover resolves a question that does not bottle, store its short reasoning note and feed it to the small model on similar questions; compare takeover rate against no notes.

**Search-snippet-only leads (not opened; verify before citing):** TeCoD (IIT Bombay; converts verified NL-SQL pairs to parameterised templates with typed slots, reportedly ~60% to ~90% accuracy on recurring queries; needs a labelled workload) — [project page](https://sslab-cse-iitb.github.io/tecod/). SkillRL (arXiv 2602.08234), hierarchical skill library co-evolved with policy — [2602.08234](https://arxiv.org/pdf/2602.08234).

### Inferences
- The current bottling mechanism has a promotion gate but, as described, no outcome-based lifecycle after promotion. Library Drift's evidence is that this is the stage where libraries go wrong, and that the fix is cheap bookkeeping (trial counts and contribution per entry), not better authoring.
- The most transferable single number is the retirement asymmetry: retiring on thin evidence (20 trials) was worse than never retiring; 100 trials was safe. Recipes served fewer than ~100 times should not be retired on outcome statistics alone.
- Dedup machinery was not needed at 50 entries; nothing in these papers shows whether it matters at 180 to 1,000 entries. The 34k-skill benchmark says retrieval-based matching degrades badly at very large scale, but that is embedding retrieval over heterogeneous skills, not typed-slot pattern matching.
- Exact-equality on a structured key is the only approach here reporting zero false reuse, and it achieved that by refusing ambiguous natural-language inputs (over half bypassed). That supports keeping recipe matching strict and sending ambiguous questions to the takeover path.

### Gaps
- No paper found that measures wrong-reuse rate of a SQL recipe/template library on real (non-controlled) traffic as the library grows past a few hundred entries.
- No paper found that tests two-different-model row agreement specifically as a promotion gate for reusable SQL; closest evidence is in section 3.
- Library Drift/Ratchet thresholds come from one model on one code benchmark; whether "100 trials" is the right order of magnitude for SQL recipes is untested.

## 2. How do recent systems generate useful synthetic training or curriculum questions when no gold answers exist, and how do they validate what was learned?

### Takeaway
Three patterns recur: (a) build the answer first and write the question from it, so validity holds by construction; (b) target difficulty to the solver's current frontier and condition new questions on analysed failures rather than on schema alone; (c) penalise template-level repetition to stop diversity collapse. Validation is almost always on an external labelled benchmark; where it is not, one paper reports that performance on unseen task types degraded while average reward rose.

### Cited Findings

**SQL-Zero: Self-Evolving Text-to-SQL (NeurIPS 2026 workshop poster, OpenReview noIVUIuOSA; no arXiv ID found) [ABS]**
- Proposer-solver self-play from one base model; the only supervision is execution against the database; the challenger generates question/SQL pairs calibrated to be "hard but solvable" for the current solver; template-level repetition penalty on the challenger prevents diversity collapse; both roles trained with GRPO (this trains weights, unlike the system here) — [SQL-Zero](https://neurips.cc/virtual/2026/170457)
- BIRD dev, trained on BIRD databases with no labels: +5.1 points at 3B and +6.3 at 7B over zero-shot base; scores higher than a matched control trained on human BIRD gold over the same databases (stated significant under an exact paired test); degrades less under Spider-Syn lexical perturbation than the BIRD-gold control — [SQL-Zero](https://neurips.cc/virtual/2026/170457)
- Abstract gives no seeds, variance, compute, or description of how generated questions are checked beyond execution.
- Test here: have the question generator write the SQL first (executable, non-empty result) then the question, and add a repetition penalty keyed on existing recipe shapes; compare new distinct shapes promoted per 100 generated questions against the current schema-names-only generator.

**SENTINEL: Failure-Driven Reinforcement Learning for Training Tool-Using Language Model Agents (arXiv 2606.12908) [FULL]**
- Every 12 rollout batches a controller deduplicates trajectories, splits failed/successful, has a model name recurring error categories, keeps a cross-round history to prioritise persistent weaknesses, and issues a directive; the proposer first executes a feasible reference tool-call trajectory, then writes a user request from it that hides the solution path; up to 10 new tasks per trigger — [2606.12908](https://arxiv.org/html/2606.12908)
- Validity by construction: the reference trajectory is executed before the request is written, and evaluation criteria are inherited from it; no separate validation filter described — [2606.12908](https://arxiv.org/html/2606.12908)
- Tau2-Bench Retail, Qwen3-4B: Pass^1 66.4 (base), 69.0 (general RL), 74.9 (SENTINEL); with SFT init 74.3 to 78.1 — [2606.12908](https://arxiv.org/html/2606.12908)
- **Negative result:** general RL on top of the SFT model reduced Pass^1 from 74.3 to 68.1 — [2606.12908](https://arxiv.org/html/2606.12908)
- Limitations: one domain, one model, no seeds/variance; coverage limited to failures the solver happens to expose, so rare scenarios are missed; authors suggest adding designed stress tests — [2606.12908](https://arxiv.org/html/2606.12908)
- Test here: cluster logged batch failures (disagreement between the two models, empty results, takeover needed) into named categories and generate the next batch from the top persistent categories; compare promotion yield and regression-set movement against uniform schema-driven generation.

**Library Drift / Ratchet (arXiv 2605.19576, 2605.22148) [FULL]** (failure-clustered authoring)
- New skills are authored only from clusters of at least 3 failures sharing a pattern; validation is held-out pass@1 on a fixed 40-task eval split that never feeds synthesis — [2605.19576](https://arxiv.org/html/2605.19576v1)

**ClinicalBench (arXiv 2605.11143, Stinard) [ABS]** (cautionary evidence on auto-generated references)
- Physician adjudication found 56% of auto-generated reference answers defective, in a 400-question benchmark over 43 MIMIC-IV patients — [2605.11143](https://arxiv.org/abs/2605.11143)

**Search-snippet-only leads (not opened; verify before citing):**
- Self-Challenging Agents (NeurIPS 2025; arXiv ID not confirmed in this pass): a generated task is accepted only if its example solution passes verification and all supplied failure cases fail; reported that average reward rises but performance on task types unseen during self-play degrades — snippet via [Emergent Mind topic page](https://www.emergentmind.com/topics/agentic-self-learning-asl)
- OpenSkill (arXiv 2606.06741): builds skills and verification signals from open resources and refines against self-generated virtual tasks without target supervision — [2606.06741](https://arxiv.org/pdf/2606.06741)
- Skill Self-Play (listed as 2607.22529 on an aggregator; ID unconfirmed): proposer, solver and skill controller co-evolve; candidates checked for schema compliance, contract validity and probe consistency — [hyper.ai](https://hyper.ai/en/papers/2607.22529)
- "When a Fixed Task Generator Falls Behind: Toward Adaptive Curricula for Training Terminal Agents" (NeurIPS 2026) — [listing](https://neurips.cc/virtual/2026/165657)
- RingSQL (arXiv 2601.05451): schema-independent SQL templates with model-written questions, average +2.3% accuracy across six benchmarks (trained models) — [2601.05451](https://arxiv.org/abs/2601.05451v1)
- SynSQL (arXiv 2604.27261): synthesises test databases conditioned on question-schema alignment rather than gold SQL — [2604.27261](https://huggingface.co/papers/2604.27261)
- SPFT-SQL (arXiv 2509.03937): self-play fine-tuning that iteratively synthesises text-to-SQL pairs — [2509.03937](https://arxiv.org/html/2509.03937v2)

### Inferences
- The current generator (small model, table and column names only, no answers) is the weakest of the designs seen: it neither guarantees answerability nor targets what the library lacks. SQL-first generation and failure-conditioned generation are both drop-in changes that need no weight training.
- The repetition penalty in SQL-Zero and the "already covered" guard in Ratchet point at the same risk for bulk bottling: generating many questions that map to existing shapes. Shape-level novelty should be a selection criterion before spending model calls.
- The Self-Challenging Agents snippet and the 56% defective-reference figure both argue that what was learned from synthetic questions must be validated on a human-checked, never-generated-from set split by question family; a regression set built from the same generator would not catch narrowing.

### Gaps
- SQL-Zero full text was not available; how it rejects ill-posed or ambiguous generated questions is unknown.
- No paper found on schema-coverage-targeted question generation for healthcare warehouses specifically with no gold answers.
- All validated gains in this section involve weight training; evidence that failure-driven curricula help a no-training recipe library is limited to Library Drift/Ratchet on code tasks.

## 3. What is the evidence for learning from unlabeled execution logs (agreement pseudo-labels, weak supervision, active selection for a stronger model or human)?

### Takeaway
Agreement is a useful but imperfect label: same-model self-consistency can converge on shared errors, cross-paradigm checks (SQL versus an independent Python computation on a small distinguishing database) add about 2 points over self-consistency, and unanimous juries of models cut false accepts only at the cost of rejecting correct queries. Routing only the disagreeing or structurally uncorroborated cases to a stronger judge, then calibrating that judge with a small human sample, is the pattern with the largest-scale clinical evidence.

### Cited Findings

**DPC: Training-Free Text-to-SQL Candidate Selection via Dual-Paradigm Consistency (arXiv 2604.15163, ACL 2026 main) [ABS]**
- Argues plain self-consistency can converge on consistently hallucinated answers; builds a Minimal Distinguishing Database (small, fully visible, designed to expose logical differences between candidates) and checks each SQL candidate against a parallel Python/Pandas solution — [2604.15163](https://arxiv.org/abs/2604.15163)
- Up to 2.2% absolute accuracy gain over strong selection baselines such as Self-Consistency on BIRD and Spider across multiple models — [2604.15163](https://arxiv.org/abs/2604.15163)
- Test here: for a sample of promoted recipes and of rejected candidates, generate a small distinguishing dataset and a Pandas solution; count cases where the two SQL writers agreed on warehouse rows but diverge from each other or from Pandas on the distinguishing data, versus the current same-rows gate.

**Vibe Coding on Trial: Operating Characteristics of Unanimous LLM Juries (arXiv 2602.18492) [FULL]**
- 82 prompts, one MySQL e-commerce schema, 492 candidate queries from 6 generators; correctness = result match with human reference on all ten seeded database instances; 63 committees of 1 to 6 open models under a unanimous-accept rule — [2602.18492](https://arxiv.org/html/2602.18492v1)
- Single judges: best Youden's J just below 0.4; some judges reach true-positive rate near 1.0 with false-positive rate above 0.6. Committees of 4 to 5: low, uniform false-positive rate but falling true-positive rate. Self-judging by one model gave J ≈ 0. (Values read from charts; approximate.) — [2602.18492](https://arxiv.org/html/2602.18492v1)
- Limitations: one schema, 82 prompts, temperature 0, only unanimity tested; correlated judge errors named as motivation but not measured — [2602.18492](https://arxiv.org/html/2602.18492v1)
- Design point worth copying: correctness checked on ten differently seeded database instances, not one — [2602.18492](https://arxiv.org/html/2602.18492v1)
- Test here: require two-model row agreement on several perturbed or resampled copies of the warehouse slice (different date windows, shuffled populations) instead of one snapshot; measure how many currently promoted recipes fail on a second instance.

**SIRIUS-SQL (arXiv 2606.01246) [SNIPPET]**
- Hybrid selector combining execution-result agreement with pairwise SQL-form judgment, escalating near-tied cases to a structural check; reported 75.88% BIRD dev, 91.20% Spider test — [2606.01246](https://arxiv.org/abs/2606.01246)

**An Efficient and Effective Evaluator for Text2SQL Models on Unseen and Unlabeled Data (FusionSQL, arXiv 2603.07841) [ABS]**
- Estimates a text-to-SQL system's dataset-level accuracy without labels from patterns in its own outputs; intended for pre-release checks and drift monitoring; abstract reports no figures — [2603.07841](https://arxiv.org/abs/2603.07841)

**Semantic Agreement Enables Efficient Open-Ended LLM Cascades (arXiv 2509.21837, Soiffer, Kolawole, Smith; EMNLP 2025 Industry) [ABS]**
- Meaning-level agreement among an ensemble of small models as a training-free deferral signal, usable with black-box APIs; matches or surpasses the target large model's quality at about 40% of its cost with up to 60% lower latency; models from 500M to 70B parameters — [2509.21837](https://arxiv.org/abs/2509.21837v3)
- Datasets and quality metric not named in abstract.
- Test here: on undecided documents, defer to the frontier reader only when two cheap readers disagree in meaning; compare frontier-call share and final-count error against the current small-then-frontier sequence.

**Inter-Cascade (arXiv 2509.22984) [FULL, truncated]**
- Deferral threshold calibrated with fixed-sequence testing to give a risk bound at a chosen error level (follows Jung et al. 2025); strong-model call rates fell 16 to 48% relative (see section 1) — [2509.22984](https://arxiv.org/html/2509.22984v1)

**A Multi-Stage Validation Framework for Trustworthy Large-scale Clinical Information Extraction using LLMs (arXiv 2604.06028, VA) [FULL]** (active selection; detail in section 4)
- Only model-positive cases with no corroborating structured code (80,006 notes) went to a judge model; a random 100-note sample of those went to a subject-matter expert; expert-judge agreement 92%, Gwet's AC1 0.91 — [2604.06028](https://arxiv.org/html/2604.06028v1)

**Self Iterative Label Refinement via Robust Unlabeled Learning (arXiv 2502.12565, NeurIPS 2025) [SNIPPET]**
- Model-generated pseudo-labels on an unlabelled corpus refined with unlabeled-unlabeled learning for binary classification — [2502.12565](https://arxiv.org/abs/2502.12565v2)

### Inferences
- The promotion gate here already uses a different model as the second writer, which is stronger than same-model self-consistency. The remaining exposure is shared misreading of the question (both writers pick the same wrong column or grain). DPC's cross-paradigm check and the multi-instance check both target exactly that and neither needs labels.
- The jury paper's result that adding judges lowers false accepts but also lowers true accepts implies the two-writer gate has a measurable rejection cost; the share of correct recipes it rejects is knowable only from a labelled sample.
- The VA pattern maps directly to logs: structured-data corroboration (a code that agrees with a document reading) is a free weak label; spend the stronger model only on uncorroborated positives, and spend human time only on a random sample of the stronger model's decisions to estimate its error.

### Gaps
- No paper found that directly quantifies the false-promotion rate of "two different models return identical rows" on unlabelled real questions.
- FusionSQL and SIRIUS-SQL were not read in full; numbers above for SIRIUS-SQL are from a search snippet.
- No 2025 to 2026 paper found on active selection of logged analytics questions for human review with a stated label budget and measured gain.

## 4. What methods improve reliable extraction and counting from many clinical or long documents at low cost?

### Takeaway
Evidence favours staged pipelines that are precision-first: cheap reader, deterministic grounding checks on the quoted span, corroboration against structured codes, stronger model only on uncorroborated positives, small human sample. Grounding filters removed about 15% of model-positive extractions at 920k-note scale. For cost, the best-documented gain beyond a two-model cascade is reading only the relevant fraction of each document and asking cheaper surrogate questions first (about 41% lower cost than a model cascade at a 90% accuracy target). No paper found gives a statistical guarantee on an aggregate count over hundreds of documents.

### Cited Findings

**A Multi-Stage Validation Framework for Trustworthy Large-scale Clinical Information Extraction using LLMs (arXiv 2604.06028, Mahbub et al., VA) [FULL]**
- Scope: substance use disorder diagnoses, 11 categories, 919,783 notes, 178,467 patients; primary reader Llama-3.1-8B-Instruct, judge gpt-oss-20b, eight A100 GPUs — [2604.06028](https://arxiv.org/html/2604.06028v1)
- Stages: prompt calibration on 923 expert-annotated notes (zero-shot chain-of-thought chosen: relaxed precision 0.9189, recall 0.8973, F1 0.9004; strict F1 0.8582); rule-based inclusion/exclusion phrase lists on the short output span; semantic grounding (max sentence-embedding cosine between the extraction and sliding note windows, threshold 0.65, results stable across 0.50 to 0.70); agreement with ICD-10; judge model on model-positive/ICD-negative cases; expert sample; predictive validity — [2604.06028](https://arxiv.org/html/2604.06028v1)
- Rule filtering plus grounding removed 14.59% of 622,551 model-positive extractions as unsupported or implausible — [2604.06028](https://arxiv.org/html/2604.06028v1)
- Encounter-level agreement with ICD-10: 75.28% alcohol, 86.26% nicotine, 91.15 to 99.91% other categories — [2604.06028](https://arxiv.org/html/2604.06028v1)
- On the uncorroborated subset the primary reader is much weaker: strict F1 0.7735 against the judge (range 0.5761 inhalant to 0.8446 alcohol); against the expert on 100 notes relaxed F1 0.7825 — [2604.06028](https://arxiv.org/html/2604.06028v1)
- Narrative-only alcohol cohort (7,014 patients with no code): note-derived features predicted later specialty care at AUC 0.67 versus 0.50 for ICD-10 — [2604.06028](https://arxiv.org/html/2604.06028v1)
- Limitations: no monetary or time cost reported; expert review covered only 100 high-uncertainty notes; one health system; depends on structured data being available for corroboration — [2604.06028](https://arxiv.org/html/2604.06028v1)
- Test here: add an embedding-similarity grounding score between each "yes" quote and the source window plus a structured-code corroboration flag; measure what share of current quoted "yes" judgments would be removed and whether removed items are true errors on a hand-checked sample, versus the current exact-quote requirement alone.

**VERGE: Verification-Enhanced Refinement for Grounded Extraction of Early-Onset Colorectal Cancer Symptoms in Clinical Notes (arXiv 2609.04366) [ABS]**
- Initial label and evidence proposed with retrieval; a bounded verify-refine cycle checks textual grounding and clinical validity and rechecks each claim until resolved or a limit is hit; unresolved claims go to human review — [2609.04366](https://arxiv.org/abs/2609.04366)
- 4,033 clinician-labelled note-finding pairs (six red-flag symptoms plus family-history risk): precision 0.764 (single-agent baseline) to 0.849; MCC 0.681 to 0.730; human review needed for 1.5% of claims — [2609.04366](https://arxiv.org/abs/2609.04366)
- From a search snippet of the PDF (not confirmed in the abstract): a negative claim is kept as verified unless grounded affirmative evidence is found, and evidence quotes must contain a minimum number of tokens — [2609.04366](https://arxiv.org/pdf/2609.04366)
- Test here: add a minimum quote length and a single bounded recheck of each "yes" (does the quote, read alone, support the statement for this patient, now, not negated) before counting; compare precision on a labelled sample against the current one-pass quote rule.

**Task Cascades for Efficient Unstructured Data Processing (arXiv 2601.05536, Shankar, Zeighami, Parameswaran; SIGMOD 2026) [FULL, truncated]**
- Generalises model cascades: each stage chooses a model, an operation (the original question or a cheaper surrogate such as a prerequisite check), a document fraction (0.1, 0.25, 0.5, 1.0 after reordering the document so relevant content comes first), and confidence thresholds; unresolved documents fall through to the oracle model — [2601.05536](https://arxiv.org/html/2601.05536v1)
- Relevance reordering: oracle labels relevant line ranges on a development set; logistic regression on small embeddings ranks chunks — [2601.05536](https://arxiv.org/html/2601.05536v1)
- Guarantees: 200 development documents split train/validation; thresholds shifted conservatively and checked with a concentration-bound estimator at failure probability 0.25 — [2601.05536](https://arxiv.org/html/2601.05536v1)
- Results at a 90% accuracy target, GPT-4o oracle, GPT-4o-mini proxy, eight classification workloads (including pubmed, legal, court) of about 1,000 documents each: average cost 0.59x of a two-model cascade; 86.2% below oracle-only; a "Lite" variant reaches 0.62x with about 75% less optimisation spend — [2601.05536](https://arxiv.org/html/2601.05536v1)
- **Internal inconsistency:** the abstract cites 36% savings while the body tables give 41% (48.5% in the guaranteed comparison) — [2601.05536](https://arxiv.org/html/2601.05536v1)
- **Negative results:** without surrogates cost is 1.21x the model-cascade baseline; without document filtering 1.55x; reading the top 25% without surrogates fails to cut cost on 6 of 8 workloads; guarantees cost about 1.43x more than the unguaranteed version; one workload missed the target (court 88.0%); savings shrink at higher accuracy targets and where the proxy is already strong; optimisation overhead is not negligible on small workloads — [2601.05536](https://arxiv.org/html/2601.05536v1)
- Test here: for the top recurring statements, order document sections by learned relevance and let the cheap reader judge the first 25% before the full text; compare cost per decided document and agreement with the frontier reader against full-document cheap reading.

**ScaleDoc: Scaling LLM-based Predicates over Large Document Collections (arXiv 2509.12610) [ABS]**
- Offline, a model produces a semantic representation per document; per query, a lightweight proxy trained with contrastive learning filters most documents and an adaptive cascade picks the policy meeting an accuracy target; over 2x end-to-end speedup and up to 85% fewer expensive model calls on three datasets — [2509.12610](https://arxiv.org/abs/2509.12610)
- Test here: train a per-statement proxy on already-decided documents for statements asked repeatedly; compare cheap-reader calls saved and count error against no proxy.

**ClinicalBench: Stress-Testing Assertion-Aware Retrieval for Cross-Admission Clinical QA on MIMIC-IV (arXiv 2605.11143, Stinard; under review at JAMIA) [ABS]**
- EpiKG: every extracted fact carries an assertion label (negation, family versus patient) and a temporality tag; retrieval routed by question intent; 400 questions, 43 patients, 9 assertion-sensitive categories, six models — [2605.11143](https://arxiv.org/abs/2605.11143)
- Primary endpoint (two external physicians, 50 unanimous-strict items): +22.0 points (95% CI +5.1 to +31.5, p=0.0192). Versus Contriever dense retrieval (n=362): +8.84 points; under oracle intent +12.43 — [2605.11143](https://arxiv.org/abs/2605.11143)
- **Negative/limiting result:** the gain shrinks as the model-alone baseline rises (r=−0.921 across 6 models); author says with n=6 this looks more like regression to the mean than encoding substituting for model size. 56% of auto-generated references were defective. Single author who is also a rater; 43 patients — [2605.11143](https://arxiv.org/abs/2605.11143)
- Test here: have the typed-judgment reader emit explicit assertion (affirmed / negated / family / hypothetical) and time (current / historical) fields with each "yes", and count only affirmed-patient-in-window; compare error on a hand-labelled set of negation and history traps against the current single yes/no.

**Search-snippet-only leads (not opened):** Diagnostic Uncertainty Preservation in Clinical Text (arXiv 2606.18471): 1,200 MIMIC-IV-Note documents with 9,184 uncertainty annotations at five levels; reports models systematically lose hedging when summarising or revising notes — [2606.18471](https://www.alphaxiv.org/abs/2606.18471.md). SNOW (arXiv 2508.01956): multi-agent cohort feature generation from notes, 147-patient prostate cohort — [2508.01956](https://arxiv.org/pdf/2508.01956v2). RDMA (arXiv 2507.15867): rare disease phenotype extraction with verification — [2507.15867](https://arxiv.org/html/2507.15867v1).

### Inferences
- The current design (quote required for "yes", code counts) already matches the direction of the evidence. What the papers add is (a) a second, deterministic check that the quote supports the claim rather than merely exists, (b) structured-code corroboration as a routing signal, and (c) reading less of each document.
- The VA numbers show the reader's accuracy on the easy, code-corroborated majority (F1 about 0.90) overstates accuracy on the uncorroborated minority (about 0.77 to 0.78), which is exactly the population where document reading adds value over SQL. Accuracy should be reported separately for corroborated and uncorroborated documents.
- Because counts are sums of per-document judgments, a per-document precision of about 0.85 (VERGE) biases counts upward by a predictable factor; none of the papers correct aggregate counts for measured reader error.

### Gaps
- No paper found that gives a confidence interval or statistical guarantee on an aggregate count or rate computed by a cascaded reader over hundreds of clinical documents (prediction-powered style correction applied to this setting).
- No full-text read of a 2025 to 2026 paper comparing small models with large ones on negation and temporality; ClinicalBench per-category results were not in the abstract.
- VERGE quote rules and negation handling come from a search snippet, not the abstract; full text not read.
- No cost-per-document figures in any clinical paper read.

## 5. What evaluation practices do recent papers recommend for agentic data-analysis systems?

### Takeaway
Recent benchmarks report repeated trials per question (pass@k), stratified averages, dollar cost and latency alongside accuracy, an explicit failure taxonomy, and correctness checked on several database instances; held-out splits that never feed the learning loop are standard in the self-improving library papers. Lenient answer matching and unaudited auto-generated references are the two flagged measurement hazards.

### Cited Findings

**Can AI Agents Answer Your Data Questions? A Benchmark for Data Agents (DAB, arXiv 2603.20576, UC Berkeley + Hasura) [FULL, truncated]**
- 54 queries, 12 datasets, 9 domains, 4 database systems; every query needs multi-database integration plus ill-formatted join keys, unstructured text transformation or domain knowledge; derived from a formative study of enterprise queries in six industries — [2603.20576](https://arxiv.org/html/2603.20576v1)
- Practice: 50 trials per query per agent (13,500 trials, about $3,150); pass@k with an unbiased estimator; averages stratified per query, then dataset, then across datasets; cost in USD, latency, iterations and tool calls reported per agent — [2603.20576](https://arxiv.org/html/2603.20576v1)
- Results: pass@1 Gemini-3-Pro 38% ($1,355 total), GPT-5-mini 30% ($67), GPT-5.2 25% ($283), Kimi-K2 23% ($1,304), Gemini-2.5-Flash 9% ($140); best pass@50 is 69%; no agent solved any query on one dataset in any trial — [2603.20576](https://arxiv.org/html/2603.20576v1)
- Failure taxonomy among completed wrong answers: incorrect implementation 45%, incorrect plan 40%, incorrect data selection 15%; more iterations did not rescue hard queries — [2603.20576](https://arxiv.org/html/2603.20576v1)
- Authors' recommendations: push aggregation into SQL; agents relied almost entirely on regular expressions for text extraction, so dedicated extraction tools and semantic layers are suggested — [2603.20576](https://arxiv.org/html/2603.20576v1)
- **Stated weakness of its own grading:** answers pass if ground-truth values appear in the free text; set answers need every element; extra wrong values are not penalised (recall-biased) — [2603.20576](https://arxiv.org/html/2603.20576v1)
- Other limits: only 54 queries; failure-mode labels rely on a model judge beyond 30 human-annotated trajectories — [2603.20576](https://arxiv.org/html/2603.20576v1)
- Test here: report every evaluation as repeated-trial pass rate with cost and latency per question and per rung, stratified by question family, and grade by complete-table equality rather than value containment; compare how many currently "passing" answers fail under complete-table grading.

**Library Drift / Ratchet (arXiv 2605.19576, 2605.22148) [FULL]**
- Fixed 60/40 train/eval task split with the eval split never used for synthesis; tasks solved by every baseline seed removed so headroom is real; ablations each run over 3 seeds with authors stating which differences fall within noise; a no-injection control isolates the library's contribution from repeated-attempt effects — [2605.19576](https://arxiv.org/html/2605.19576v1)

**SE-GoS (arXiv 2609.08228) [ABS]**
- Reports results separately on a split not used for graph evolution (52.9% to 58.3%) alongside the in-loop number (52.4% to 59.4%) — [2609.08228](https://arxiv.org/abs/2609.08228)

**Vibe Coding on Trial (arXiv 2602.18492) [FULL]**
- Correctness requires result match on all ten seeded database instances, not one — [2602.18492](https://arxiv.org/html/2602.18492v1)

**ClinicalBench (arXiv 2605.11143) [ABS]**
- Physician adjudication found 56% of auto-generated reference answers defective; primary endpoint restricted to items where external raters were unanimous, with the author excluded from the rater set — [2605.11143](https://arxiv.org/abs/2605.11143)

**Semantic Caching for OLAP (arXiv 2602.19811) [FULL]**
- Reports false-hit count alongside hit rate, and separately reports accuracy on an adversarial ambiguous set (44.4% of 63) rather than only on clean paraphrases; author notes controlled variants may overstate production hit rates — [2602.19811](https://arxiv.org/html/2602.19811v1)

**Search-snippet-only leads (not opened):** Scaling Generalist Data-Analytic Agents (arXiv 2509.25084): contamination check by table-header overlap between training data and each benchmark, reported at or near 0% — [2509.25084](https://arxiv.org/pdf/2509.25084). KramaBench (arXiv 2506.06541, ICLR 2026): best system 55% end-to-end on data-lake pipelines — [2506.06541](https://arxiv.org/html/2506.06541v2).

### Inferences
- For a system whose learning loop consumes questions, the minimum credible protocol from these papers is: a held-out set split by question family that never enters bottling or generation; a no-library control run on the same set; several seeds or trials; and a statement of which differences are within noise.
- The regression set that gates promotion is a tuned set by construction (recipes are selected for not worsening it). Library Drift's separation of the synthesis split from the eval split implies a second, untouched set is needed to report progress.
- Reporting wrong-reuse count (false hits) next to no-model answer share mirrors the hit-rate/false-hit pairing in the caching paper and is the direct analogue for recipes.

### Gaps
- No paper found that explicitly studies contamination of a tuned/regression set by a self-improving loop and quantifies the optimism it causes.
- No healthcare-specific agentic analytics benchmark from 2025 to 2026 beyond the already-reviewed EHR-Complex was read in this pass.
- Complete-table comparison as a recommended metric was not found stated as such; DAB uses value containment and flags it as lenient.

## 6. Any 2025 to 2026 results on fine-tuning small models from an agent's own verified traces (volume, gains, regressions)?

### Takeaway
Evidence exists that about 2,000 filtered teacher-agent trajectories lift small models by roughly 8 to 9 points over reasoning-only distillation, but it comes from a larger teacher's traces, not a system's own verified production traces. Iterated self-distillation is reported to collapse without safeguards, and reinforcement fine-tuning on top of a supervised model regressed in one study. No paper was found that fine-tunes a small SQL model on a deployed system's agreement-verified queries and reports regressions on held-out question families.

### Cited Findings

**Distilling LLM Agent into Small Models with Retrieval and Code Tools (arXiv 2505.17612, KAIST/KRAFTON) [FULL, truncated]**
- Volume: about 2,000 filtered trajectories (from 1,000 HotpotQA and 2,000 MATH training questions, one trajectory each); LoRA rank 64, 2 epochs; teacher Qwen2.5-32B; students Qwen2.5 0.5B, 1.5B, 3B, 7B — [2505.17612](https://arxiv.org/html/2505.17612v2)
- Average over 8 tasks, agent distillation with both add-ons versus chain-of-thought distillation: 0.5B 21.90 vs 13.64; 1.5B 30.55 vs 21.28; 3B 36.60 vs 27.72; 7B 42.68 vs 33.54; each student roughly matches the next-size-up reasoning-distilled model; best student (42.68) still trails the teacher in agent mode (46.00) — [2505.17612](https://arxiv.org/html/2505.17612v2)
- Without distillation, prompted small agents score 1.80 (0.5B), 7.94 (1.5B), 21.20 (3B) — [2505.17612](https://arxiv.org/html/2505.17612v2)
- **Regressions:** on MATH500 agent distillation trails reasoning distillation at 3B (60.2 vs 61.8) and 7B (67.8 vs 72.6); test-time self-consistent action voting dropped one 3B task score to 0.0; single runs on subsampled test sets, so small differences may not be meaningful — [2505.17612](https://arxiv.org/html/2505.17612v2)
- Test here: fine-tune (LoRA) a small SQL writer on about 2,000 agreement-verified question/query pairs from the bottling log and compare first-attempt agreement with the frontier writer, on a held-out question-family split, against the same small model with retrieved recipe examples in the prompt.

**ReSAIL: Mitigating Collapse in Iterative Agent Self-Distillation (arXiv 2609.39306) [ABS]**
- Reports that naive iterative self-distillation collapses in deployment performance across cycles, and that performance with privileged information also declines; fix selects steps where privileged information most changes the teacher's prediction, balances losses across trajectories, and regularises the student toward the frozen teacher — [2609.39306](https://arxiv.org/abs/2609.39306)
- Average absolute gain of 22.5% in final-cycle success on ALFWorld and TextCraft over three cycles when added to self-distillation baselines — [2609.39306](https://arxiv.org/abs/2609.39306)
- Test here: if a small model is retrained on its own verified traces more than once, hold the first-cycle checkpoint as a fixed reference and track held-out agreement per cycle; stop at the first decline.

**SENTINEL (arXiv 2606.12908) [FULL]**
- Qwen3-4B on Tau2-Bench Retail: supervised fine-tuning alone 74.3 Pass^1; adding general reinforcement learning dropped it to 68.1; adding failure-targeted tasks raised it to 78.1; no seeds reported — [2606.12908](https://arxiv.org/html/2606.12908)

**SQL-Zero (NeurIPS 2026 workshop) [ABS]**
- Label-free self-play training at 3B and 7B gave +5.1 and +6.3 points on BIRD dev and exceeded a matched human-gold control on the same databases — [SQL-Zero](https://neurips.cc/virtual/2026/170457)

**MTSQL-R1: Towards Long-Horizon Multi-Turn Text-to-SQL via Agentic Training (arXiv 2510.12831, ACL 2026 main) [ABS + SNIPPET]**
- Abstract: propose-execute-verify-refine agent trained for multi-turn text-to-SQL; "consistently outperforms strong baselines" on CoSQL and SParC; no numbers in abstract — [2510.12831](https://arxiv.org/abs/2510.12831)
- From a search snippet of the PDF (not confirmed): self-taught loop keeps only trajectories that yield correct SQL, fine-tunes, repeats; difficulty-aware filter keeps longer trajectories for hard questions — [2510.12831](https://arxiv.org/pdf/2510.12831)

**Search-snippet-only leads (not opened):** ReToolSQL (listed as arXiv 2608.27796 on secondary sites): supervised warm start on rejection-sampled traces from a privileged teacher plus agentic reinforcement fine-tuning, 74.32% BIRD dev single pass — [secondary listing](https://deeplearn.org/arxiv/814611/retoolsql:-agentic-reinforcement-learning-for-robust-text-to-sql). AGRO-SQL (arXiv 2512.23366): teacher-synthesised trajectories then agentic RL — [2512.23366](https://arxiv.org/html/2512.23366v1). SCoRe (ICML 2026): student generates trajectories, teacher corrects only the earliest error, student fine-tuned on corrected trajectories — [PMLR](https://proceedings.mlr.press/v306/lyu26j.html). Progress-Aware Distillation (arXiv 2610.19439): distilled small agents stall on long trajectories — [secondary listing](https://www.opentrain.ai/papers/progress-aware-distillation-for-mitigating-stagnation-in-small-language-model-ag--arxiv-2610.19439/). Self-distillation fine-tuning for sequential skill accumulation without regression (arXiv 2601.19897) — [2601.19897](https://arxiv.org/abs/2601.19897v1).

### Inferences
- The volume question has one concrete anchor: about 2,000 filtered trajectories with LoRA produced a one-model-size-class improvement. The system's bottling log (180 recipes plus bulk-run verified pairs) is likely near or below this order of magnitude for distinct shapes, so in-prompt retrieval of recipes is the appropriate baseline to beat before any training.
- Every regression reported here appeared when training continued past a first supervised pass (RL on SFT; repeated self-distillation cycles) or on a task type where the trace format did not suit the task (MATH500). A single supervised pass with a frozen reference checkpoint is the lowest-risk design.
- Since the system currently trains no weights, the practical finding is negative: nothing read here shows that self-trace fine-tuning beats a verified recipe library on recurring question shapes.

### Gaps
- No paper found that fine-tunes on a deployed system's own agreement-verified (not gold-labelled) SQL traces and reports held-out-family regressions.
- No data-volume scaling curve (gain versus number of verified traces) was found in any paper read.
- MTSQL-R1, ReToolSQL, SCoRe and Progress-Aware Distillation were not read in full; their trace volumes and regression tables are unknown.
