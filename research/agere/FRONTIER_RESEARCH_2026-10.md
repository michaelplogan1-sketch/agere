# Agere frontier research sweep, October 2026

October 13, 2026. A broad, deliberately skeptical sweep of research that could make Agere measurably better at
answering healthcare analytics questions: more accurate, deeper, cheaper per correct answer. All Agere data remains
synthetic. Nothing in this report was implemented; it is an intake and ranking document.

**Scope and method.** About 110 sources were located and reviewed through web search and abstract/page fetches. Most
were read at abstract or summary level; where only an abstract (or a secondary summary) was seen, the bibliography marks
it **[abs]** or **[sec]**. Numbers quoted are the authors' own unless stated. Prior Agere notes are not repeated: embedding
and reranker choices (Granite R2, BGE v2-m3, Qwen3-Reranker), MedGemma's role and local SLM selection are covered in
`MODEL_SELECTION_REVIEW.md`, `EDGE_MODELS.md`, `CLAUDE_SLM_EVALUATION_2026-10-09.md` and `FOUNDATION_DECISION.md`;
astronomy/weather/information-bottleneck tracks are in `RESEARCH_PROGRAM.md`; trading ideas are in
`CODEX_TRADING_RESEARCH_2026-10-09.md`.

**Reading the stack first changed the ranking.** Three facts from the repository shaped every verdict below:

1. **The latest scorecard says the product path is broken, not inaccurate.** `bench/SCORECARD.md` (Oct 12) records
   Agere at 1 correct of 50 with 39 execution errors, against 49/50 for a plain frontier-SQL baseline at $0.43 per
   correct answer. If that run reflects the current product, the highest-value work is reliability of the hosted route,
   and no technique below matters until it is fixed. If it reflects a harness fault, the scorecard should be rerun before
   any research claim is measured against it.
2. **The top FMEA risk (FM-13, correlated two-model agreement, RPN 720) is structurally baked in.** The verified SQL
   ladder agrees Haiku against Sonnet (`economical_planner.py`, lines 292-296), two models from the same developer and
   training lineage. The best located evidence (Kim et al., ICML 2025) says same-developer models agree on the same
   wrong answer far above chance. Agreement between them is weak evidence of correctness.
3. **FM-23 (wrong answers captured as training pairs) has a concrete code cause.** `learning.record_verified` is called
   immediately after `verified_answer` returns (planner lines 300-302), before `analysis_graph.execute` runs the
   `answer_checks` controls. A pair that later draws QC-VERSION or QC-CLOCK is already in the verified store.

---

## 1. Executive table

Scoring: **Impact** (expected measurable effect on Agere accuracy, depth or cost per correct answer, 1-5) ×
**Feasibility** (one Apple Silicon Mac plus Anthropic API, synthetic data, effort, 1-5). Verdicts: **build** (implement
next), **test** (bounded experiment with a kill criterion), **watch** (track, no work), **skip**.

Metric abbreviations: *blind EX* = strict correct rate on blind sets with adjudication; *escape rate* = share of
adjudicated engine errors that pass all verification (FM-13 occurrence); *sel-acc@cov* = accuracy on answered
questions at a fixed answer coverage; *$/correct* = known API USD per correct answer; *pass^k* = all k reruns correct.

| # | Idea | Agere layer / engine | Evidence (paper, year, venue, key number) | Expected gain and how measured | Cost / compute | Main risks | I×F | Verdict |
|---:|---|---|---|---|---|---|---:|---|
| 1 | Restore the hosted route before any research; rerun the 50-question scorecard | `economical_planner`, `nl2sql`, bench harness | Own `bench/SCORECARD.md`: Agere 1/50, 39 execution errors; frontier_sql 49/50 | From 2% to the level of the plain frontier baseline (≈90%+); scorecard EX and error count | Engineering only | Masks research gains if skipped | 25 | **build** |
| 2 | Execution-result clustering with confidence-gated selection, replacing "two models agree" as the acceptance rule | verified SQL ladder (`nl2sql.verified_answer`) | DeepEye-SQL 2025 [abs]: cluster share as confidence, 73.5% BIRD-dev with 30B-A3B open model; SIRIUS-SQL 2026 [abs]: confidence-gated selector, tuple-level consensus, 75.88% BIRD-dev; Query and Conquer 2025 [abs]: small models match o1-class at up to 30× lower cost; ReFoRCE (Spider 2.0) defers low-consensus cases | Lower escape rate (FM-13); higher sel-acc@cov; abstain when top cluster share is low | 4-8 extra local samples or 2-4 extra Haiku calls (~$0.002-0.01/question) | Correlated samples cluster on the same wrong answer (documented by DeepEye, CSC-SQL) | 20 | **build** |
| 3 | N-version generation across *independent representations and families*: Anthropic model + local SQL-specialist + compiler/semantic-layer plan; agreement counts only across different families or representations | verified SQL ladder; `learned_router` | Kim, Garg et al., ICML 2025: when two models are both wrong they pick the same wrong answer ~60% (HELM), higher for same developer; DeepEye's "N-version programming"; XiYan-SQL multi-generator (75.63% BIRD) | Escape rate on adjudicated sets M/N/O (24 hosted-route engine errors / 132 today); FMEA O for FM-13 | Local model inference only | Independent generators may be individually weaker, lowering coverage | 20 | **build** |
| 4 | Encode metric mechanics in the semantic layer: measure denominators (HEDIS age bands, continuous enrollment), attribution rules, clocks and value sets as typed definitions the SQL writer must call | `semantic_layer.py`, `config/agere/definitions.json`, `vbc` | dbt Labs 2026 [sec]: Sonnet 4.6 90.0%→98.2%, GPT-5.3-Codex 84.1%→100% with semantic layer; Sequeda et al. (data.world): 16%→54% over KG; Snowflake 2024 [sec]: Cortex Analyst 90%+ vs ~51% single-prompt GPT-4o (internal 150 Qs) | FM-05/06/07 occurrence (13 adjudicated / 132 combined); blind EX on measure questions | Medium engineering; $0 runtime | Vendor benchmarks are small (11-150 Qs) and self-graded; definitions can be wrong once and wrong everywhere | 20 | **build** |
| 5 | Quarantined, statistically audited learning store: capture a verified pair only after answer checks run, quarantine any pair with a QC warning, and estimate store error rate with prediction-powered inference on a small audited sample | `learning.py`, planner capture site, `tools/bottling` | ReViSQL, arXiv 2026 [abs]: errors corrected in 61.1% of an examined BIRD-train subset; cleaning alone lifted single-generation accuracy 8.2-13.9 points under the same RL; PPI++ (Angelopoulos, Duchi, Zrnic 2023): valid intervals combining few gold labels with many predictions | FM-23 residual RPN (640 today); audited error rate of the verified store with a confidence interval; downstream bottled-model EX | Small; audit labels from Opus + human on ~100 pairs | Audit sample too small for rare error types | 20 | **build** |
| 6 | Learn-then-Test / conformal calibration of every accept threshold (planner `DECISION_THRESHOLD=.95`, learned-router threshold, selection confidence) to a stated error rate | `economical_planner`, `learned_router`, selection gate | Learn then Test (Angelopoulos, Bates, Candès, Jordan, Lei; Ann. Appl. Stat. 2025): finite-sample risk control for any model, non-monotone risks; Conformal abstention (Abbasi-Yadkori et al., Google DeepMind 2024): bounded hallucination rate via self-consistency scores | Replace "engineering cutoff" with "≤ x% wrong among answered, with 1-δ confidence"; measure realized error on held-out blind sets | Needs a few hundred labeled outcomes per gate; $0 runtime | Exchangeability breaks when question mix shifts; guarantees are marginal, not per-question | 16 | **build** |
| 7 | Question-side metamorphic tests: perturb the numbers, years, qualifiers and comparators in the question with the local model and require the SQL/result to change in the predicted direction | `answer_checks.py` (generalizes QC-NUMBERS/QC-PARTS) | SQLHD, arXiv 2025 [abs]: 17 metamorphic relations, F1 69.4-82.8% hallucination detection without gold SQL, beats self-evaluation; MRSQLGen [sec]; MT-Teql 2020 | Detection (D) for FM-01, FM-12, FM-13; replay on preserved blind answers like the existing controls (fired-on-wrong vs fired-on-right) | 2-4 extra local generations + executions per answer | False alarms when a perturbation legitimately leaves the answer unchanged | 16 | **build** |
| 8 | Ternary-logic partition invariants on answer queries: `count(p)+count(NOT p)+count(p IS NULL)=count(*)`, group totals = ungrouped total, DISTINCT-key count ≤ row count | `answer_checks.py` (new QC-PARTITION) | Rigger & Su, OOPSLA 2020 (TLP): 175 DBMS logic bugs found by partition oracles; SQLancer NoREC (ESEC/FSE 2020) | Detection for FM-09 (undated/null handling) and FM-08 (snapshot duplicates); replay counts | Pure SQL, milliseconds, $0 | Only catches internal inconsistency, not wrong intent | 16 | **build** |
| 9 | Treat candidate disagreement as an ambiguity signal and show the alternative interpretations (or ask one information-maximizing question) instead of silently picking | answer rendering, `conversation.py`, FM-19 | AMBROSIA (NeurIPS 2024 D&B): models struggle to recognise ambiguity; BIRD-INTERACT (ICLR 2026): GPT-5 completes 8.67% / 17.00% of interactive tasks; Liu & Erdelt, VLDB AIDB 2026 [abs]: introspection ∪ sampling disagreement raises clarification recall ≥6 points; AmbiSQL demo [sec]; info-gain clarification (arXiv 2507.06467) [abs] | FM-19 occurrence (14 adjudicated / 132, the most frequent mode); share of "defensible alternative" adjudications that become visible | Reuses samples from #2 | Too many clarifications annoy executives; needs a disclosure-first default | 16 | **build** |
| 10 | Template-constrained decoding for recurring questions: abstract verified SQL into slot templates; an NLI matcher selects or rejects a template; the local model fills slots under grammar constraint | `semantic_cache`, `learning`, local `nl2sql` | TeCoD, PACMMOD 2025 [abs]: up to 36% higher EX than in-context learning and 2.2× lower latency on matched queries | Local-rung coverage (share answered at $0) at equal blind EX; $/correct | Moderate: template extraction + small NLI model (local) | Template drift when the semantic layer changes (FM-22); reject threshold must be calibrated (#6) | 16 | **build** |
| 11 | Report pass^k (all k reruns correct) and answer stability as first-class bench metrics | `bench/score.py`, SCORECARD | EHR-ChatQA (ICLR 2026): best agents Pass@5 >90% but Pass^5 up to ~60 points lower | A truthful reliability number; exposes stochastic routes | k× bench cost on hosted routes | Bench cost; needs cost caps per run | 15 | **build** |
| 12 | Audit the benchmark itself: search for distinguishing databases where reference SQL and candidate differ; re-adjudicate disagreements | `bench/adjudicate.py`, reference SQL | Jin, Choi, Zhu, Kang (CIDR/PVLDB 2026): 52.8% of BIRD Mini-Dev and 62.8% of Spider 2.0-Snow problems carry annotation errors; CHESS moved 62%→81% after correction; SpotIt (arXiv 2025) [abs]: when prediction and gold disagree, gold is often wrong; FLEX (NAACL 2025): EX false positives/negatives, LLM judge kappa 62→87 | Corrected reference set; fewer phantom failures/passes; trustworthy FMEA O values | Low; bounded-equivalence tooling for SQLite is limited, so start with mutation of small fixture DBs | Over-correcting the gold toward the model | 15 | **build** |
| 13 | Fix the judge, not add judges: cross-family judge, position-swapped pairwise, rubric from FMEA modes; judge accuracy measured against saved adjudications | Opus judge, Perplexity decider | Self-preference/family bias literature [sec] ("Preference Leakage" 2025); Stechly, Valmeekam, Kambhampati 2024: self-critique degrades reasoning, external sound verifiers help | Judge precision/recall vs adjudications; FM-13 D value | Small | Different family still correlates (Kim et al.) | 12 | **test** |
| 14 | Learned outcome reward model / pairwise selector trained on Agere's own candidates labeled by execution against references (non-benchmark questions only) | selection step after #2 | GradeSQL, PVLDB [abs]: ORM selection +4.33 BIRD / +2.10 Spider over execution best-of-N, +2.91 over majority vote; CHASE-SQL (ICLR 2025): pairwise fine-tuned selector, 73.0% BIRD test | sel-acc@cov over #2 alone; escape rate | LoRA on a 4-7B model on the Mac; needs ~2-5k labeled candidate pairs | Learns Agere's generator quirks; benchmark leakage if labels touch blind sets | 12 | **test** |
| 15 | Swap the bottled base to a SQL-specialist open model and compare against Qwen3-4B on Agere's own set | `tools/bottling`, local `nl2sql` | OmniSQL (PVLDB 2025): 7B +8.4 avg points over Qwen2.5-Coder-7B (52.8→61.2), Spider test 87.9%; Arctic-Text2SQL-R1 (ACL Findings 2026) [abs]: execution-only reward RL, 7B beats earlier 70B systems | Local EX on non-benchmark held-out set; local coverage at fixed error | 7B 4-bit ≈5 GB on MLX; LoRA feasible | Benchmark-tuned models may not transfer to Agere's semantic tables; licenses must be checked | 12 | **test** |
| 16 | GEPA reflective prompt optimization of the Haiku SQL prompt and operator-router prompt on a non-benchmark development split | `nl2sql` prompts, `operator_router` | GEPA (ICLR 2026 oral): beats GRPO by 6% avg with up to 35× fewer rollouts, beats MIPROv2 by >10% | Blind EX and $/correct with the same models | Hundreds of Haiku calls per optimization run (~$1-5) | Overfits the dev split; any blind-set exposure invalidates results | 12 | **test** |
| 17 | Give frontier rungs the whole semantic schema; keep the reranker for the local model only | `schema_rerank`, `nl2sql.schema_for` | "The Death of Schema Linking?" (Distyl, NeurIPS 2024 workshop): no pruning when schema fits context, 71.83% BIRD; authors keep linking for small models and huge warehouses | Ablation on blind sets: EX with full vs reranked schema for Haiku/Sonnet | More input tokens, mitigated by prompt caching (#24) | Agere's received streams may exceed context; keep hybrid | 12 | **test** |
| 18 | Retrieve-then-classify value sets: candidate codes from curated sets and terminology hierarchy, LLM only as constrained adjudicator | concept library, `drug_classes`, FM-06 | RASC (ML4H/PMLR 2026) [abs] on 11,803 VSAC sets; RASC+ (arXiv 2026) [abs]: zero-shot generation poorly suited; OHDSI 2025 poster: VSAC itself contains missing/extraneous codes | FM-06 occurrence (6 adjudicated / 132); value-set precision/recall against a curated reference | Local retrieval + occasional Sonnet adjudication | Public value sets carry their own errors | 12 | **test** |
| 19 | MacroBase-style DIFF explanations (risk ratio with minimum support) as the core of `discover`/`explain` | `discover.py`, `explain` | Abuzaid, Bailis, Zaharia et al., VLDB 2019: DIFF relational operator, up to 10× faster than prior engines; defaults min ratio 1.5, min support 0.2 | Precision of planted hotspots in synthetic data; InsightEval-style coverage | Pure SQL/pandas | Association ≠ cause; multiple-comparison inflation needs control | 12 | **build** |
| 20 | Bayesian-surprise ranking for maps, tickers and briefings so small denominators and base rates do not dominate | `market`, `discover`, `briefing_rank` | Correll & Heer, IEEE TVCG 2017: KL-divergence surprise counters base-rate, sampling-error and renormalization biases | Fraction of top-ranked "moves" that are planted true changes vs small-n noise | $0 | Prior model space choice drives results | 12 | **build** |
| 21 | Risk-adjusted CUSUM with simulated control limits per volume and case mix | `monitor` | Steiner et al., Biostatistics 2000; BMC HSR 2020 (Bavarian hospitals): control limits depend on volume, baseline risk and case mix; case-mix drift can nearly double false alarms; Steiner & Woodall 2016: RA-CUSUM beats VLAD | False-alarm probability and detection delay on planted shifts in the synthetic generator | $0 | Risk model miscalibration masquerades as performance change | 12 | **build** |
| 22 | Uplift-based outreach targeting, evaluated with RATE/Qini, not risk ranking | `outreach` | Marafino et al. (KPNC, 1.54M hospitalizations, arXiv 2005.14409): targeting by estimated benefit could prevent nearly 4× more readmissions than risk-based; benefit fell at highest risk; Yadlowsky et al., JASA 2025: RATE metrics with valid inference | RATE/Qini on synthetic worlds with known heterogeneous effects | Causal forest (grf/EconML) locally | Observational CATE is fragile; synthetic effects are generator assumptions | 12 | **build** |
| 23 | Label-choice audit for every risk score and worklist (state what is predicted; never cost as proxy for need) | `outreach`, `profile`, `risk_adjustment` | Obermeyer et al., Science 2019: cost-label algorithm; fixing the label raised Black patients flagged from 17.7% to 46.5% | Disclosure present on 100% of risk outputs; subgroup calibration table | $0 | None material | 12 | **build** |
| 24 | Byte-stable prompt prefixes (schema, rules first) for Anthropic prompt caching and local prefix KV reuse | `nl2sql`, `operator_router`, MLX runner | NVIDIA NIM docs [sec]: ~2× TTFT with shared prefixes; KV recycling study (arXiv 2512.11851) [abs]: reuse only on exact prefix | $/correct and p50 latency | Small refactor | None material | 12 | **build** |
| 25 | Hierarchical forecast reconciliation (MinT) across practice → region → network | `demand`, `market` | Wickramasuriya, Athanasopoulos, Hyndman, JASA 2019: coherent minimum-variance reconciliation; short-series covariance estimation is volatile (arXiv 2409.18550) | Backtest MASE/CRPS at each level vs unreconciled | $0 (statsforecast/hierarchicalforecast) | W-matrix estimation on short series; use shrinkage variant | 12 | **build** |
| 26 | Modern staggered-adoption estimators for `program_effect`: SDID, Callaway-Sant'Anna, augmented/staggered synthetic control, with DML adjustment | `program_effect` | Arkhangelsky et al., AER 2021 (SDID); Ben-Michael, Feller, Rothstein, JRSS-B 2022; Arkhangelsky & Samkov (sequential SDID, arXiv 2404.00164); Chernozhukov et al., Econometrics J. 2018 (DML) | Bias and CI coverage on synthetic programs with known effects and staggered rollouts | $0; python ports exist | Estimator disagreement must be shown, not hidden | 12 | **build** |
| 27 | Make target-trial-emulation protocol objects (TARGET's eight components) the contract for every causal question | `program_effect`, analysis graph contracts | TARGET statement (BMJ/JAMA 2025) [sec]; THESEUS (medRxiv 2026) [abs]: LLM → JSON spec → OHDSI code, standardization accuracy 0.93-0.97; Dahabreh et al. NEJM AI editorial: automation cannot fix unmeasured confounding | Share of causal answers with complete protocol; time-zero/immortal-time errors caught | Medium | Protocol completeness can look like validity | 10 | **test** |
| 28 | Learned cascade router: predict which rung will succeed from the outcomes table, not only which operator | `learned_router`, `learning.outcomes` | RouteLLM (ICLR 2025): >2× cost reduction without quality loss (85% on MT-Bench at 95% GPT-4 quality); FrugalGPT (2023) [sec] | $/correct at fixed blind EX | Local logistic head | Router learns from a broken route (#1) | 10 | **test** |
| 29 | Semantic entropy probes on the local model's hidden states as a free abstention feature | local `nl2sql`, gates in #6 | Farquhar et al., Nature 2024 (semantic entropy); Kossen et al., 2024: probes approximate it from one generation at near-zero cost, generalize better OOD than accuracy probes | AUROC of probe for local-SQL correctness; sel-acc@cov | Training a linear probe; MLX hidden-state access | Probes trained on QA may not transfer to SQL | 10 | **test** |
| 30 | vCache-style per-entry learned similarity thresholds under a user-set error bound | `semantic_cache.py` | vCache (ICLR 2026): meets error bound, up to 12.5× higher hit rate and 26× lower error than static thresholds | Cache hit rate at zero false reuse on the existing `semantic_cache_eval` | Small | Agere's slot signature already does the safety work; gain may be small | 9 | **test** |
| 31 | Grammar-constrained decoding plus beam search for the local SQL model | local `nl2sql` | GRID (arXiv 2026) [abs]: +13 EX at 0.5B on Spider; StructureBench (IJCAI 2026) [abs]: syntax guaranteed, semantic accuracy not reliably improved for small models; arXiv 2608.25761 [abs]: beam beats sample+vote at matched budget under constraints, but scaling model size beats scaling inference | Execution-error rate of local rung; EX | llguidance/XGrammar integration with MLX | May reduce accuracy at 4B | 9 | **test** |
| 32 | Conformalized survival lower bounds for `survival`/`calibration` | `survival`, `calibration` | Candès, Lei, Ren, JRSS-B 2023: distribution-free lower predictive bounds under censoring, doubly robust coverage | Empirical coverage on synthetic cohorts | $0 | Lower bounds only; can be wide | 9 | **test** |
| 33 | Risk-adjustment fairness diagnostics: group-importance search for undercompensated groups; constrained/fair regression option | `risk_adjustment` | Zink & Rose, Biometrics 2020: up to 98% group-fairness gain for ~4% fit loss (MarketScan); follow-up on multi-attribute undercompensated groups (arXiv 2105.08493) | Predictive ratio by group on synthetic members | $0 | Policy choice belongs to the user; upcoding incentives | 9 | **test** |
| 34 | Benchmark ratchet and rebasing simulator in `vbc` (MSSP regional blend, early renewal, LEAD's locked baseline) | `vbc` | MedPAC 2021 transcript; Brookings (Fiedler) comment; Milliman rebasing analysis [sec]; LEAD no-rebasing [sec, vendor] | Scenario answers to "should we renew early" with cited rule terms | Medium (rulebook encoding) | Program rules change annually; mostly grey literature | 9 | **test** |
| 35 | Chronos-2 / TimesFM-2.5 zero-shot challengers in `demand` backtests vs ETS/NB-GLM | `demand` | Chronos-2 (arXiv 2510.15821): GIFT-Eval WQL skill 46.6 vs TimesFM-2.5 42.4; LiveHouse-TS 2026 [abs] splits ranking by metric; no located study on ED/hospital volumes vs tuned classical | MASE/CRPS on synthetic demand backtests; keep only if it beats current engine | CPU/MPS inference feasible for small models | Synthetic series favor whatever generated them; partial train-set overlap with GIFT-Eval | 8 | **test** |
| 36 | TabPFN v2/2.5 as a challenger model for small-cohort tabular tasks | `explain`, `profile`, `claims_review` | Hollmann et al., Nature 2025: beats tuned baselines ≤10k rows; TabArena (NeurIPS 2025 D&B): GBDTs still strong, foundation models best on small data; clinical evidence mixed (992-patient SCD study: no advantage on held-out test; perioperative study: comparable) | AUROC/calibration vs tuned GBM on synthetic tasks | Inference cheap on CPU/MPS for small tables | License terms; synthetic-data advantage may not transfer | 8 | **test** |
| 37 | Off-policy evaluation (doubly robust) of outreach and bandit policies from logs before any recommendation | `outreach` (bandits) | Dudík, Langford, Li (DR policy evaluation); Su et al. 2019 (DR with shrinkage); CANDOR (CHIL 2026) [abs] | OPE estimate vs ground-truth policy value in simulator | $0 | Requires logged propensities, which synthetic logs must include | 8 | **test** |
| 38 | Unsupervised overbilling detection with peer comparison, validated against known cases | `claims_review` | Shekhar, Leder-Luis, Akoglu, JPAM 2026 [sec]: ~5× lift over random targeting, validated on DOJ lawsuits; GNN fraud papers (IJCAI 2023, Sci. Rep. 2025) [abs]; no agreed public benchmark | Precision@k on planted schemes in synthetic claims | $0 | Planted fraud is easy to find by design | 8 | **test** |
| 39 | Clinical NER candidates (GLiNER-BioMed, OpenMed NER) feeding the existing rule-distillation in `note_facts`, with PPI for prevalence estimates | `note_facts.py`, `tools/notes_extraction` | GLiNER-BioMed (Bioinformatics 2026): zero-shot micro-F1 59.77, +5.96 over best GLiNER; OpenMed NER (arXiv 2025): SOTA on 10/12 datasets, LoRA <1.5% params; PPI++ | Held-out precision per fact type; admitted fact types count | Local CPU inference (<0.5B models) | Benchmarks are biomedical literature, not synthetic notes | 8 | **test** |
| 40 | Active distillation: send only uncertain/diverse units to the frontier labeler | `tools/notes_extraction`, `tools/bottling` | M-RARU (arXiv 2511.11574) [abs]; LLKD (arXiv 2411.08028) [abs] | Labels needed to reach the admission bar; frontier $ per admitted fact type | $0 extra | Small published effect sizes | 8 | **test** |
| 41 | Data-quality detectors for source defects (FM-25): Raha/Baran-style ensembles plus LLM-proposed functional dependencies | ingestion, `source_profile`, `monitor` | Raha/Baran (SIGMOD/VLDB) ~20 labeled tuples; LLMClean (arXiv 2404.18681): F1 +23% over Raha on Hospital; LLM agents miss multi-row/distributional errors (arXiv 2503.06664) | Detection of planted generator defects; FM-25 D value | Local | Planted defects bias evaluation | 8 | **test** |
| 42 | Retrieve-then-rank ICD-10/HCC suspecting; never free generation | `risk_adjustment`, `note_facts` | NEJM AI 2024 (Mount Sinai) [sec]: GPT-4 exact match 33.9% ICD-10-CM; retrieve-rank (arXiv 2407.12849): 100% on 100 simplified terms vs 6% vanilla; full-label-space MIMIC-IV remains hard (KREL 2026) [abs] | HCC suspect precision on synthetic notes with known codes | Local retrieval + Sonnet adjudication | Synthetic notes too clean | 8 | **test** |
| 43 | Interactive benchmarks as external yardsticks for follow-ups (FM-20/21) | bench | BIRD-INTERACT (ICLR 2026); EHR-ChatQA (ICLR 2026) on MIMIC-IV/eICU, public | First measured occurrence for FM-20/21 | Adapter work; MIMIC demo licensing for eICU/MIMIC credentialed data must be checked | Credentialed-data terms | 8 | **test** |
| 44 | Object-centric event logs (OCEL 2.0) for `pathway`, plus guideline-rule conformance where rules are LLM-extracted then human-checked | `pathway` | OCEL 2.0 spec (arXiv 2403.01975): flattening to one case per event misleads; Leonardi/Montani stroke conformance (AIME 2025, Applied Sciences 2025) [abs] | Conformance metrics on synthetic pathways with planted deviations | Medium (pm4py supports OCEL) | Guideline extraction errors | 8 | **test** |
| 45 | Empirical-Bayes provider profiling that reports misclassification probability, not only shrunken ranks | `profile` | Classification accuracy of profiling methods (PMC6400472); total-variation profiling (arXiv 1907.07809); benchmarks from hierarchical models (PMC4231585) | Expected misclassification on synthetic providers with known quality | $0 | Over-shrinkage hides true outliers | 8 | **test** |
| 46 | Peer-comparison action template for clinician-facing outreach | `outreach`, action layer | Sacarny et al., JAMA Psychiatry 2018 RCT: 5,055 high-volume prescribers, 11.1% fewer quetiapine days over 9 months, no detected harm | Product capability, measured only in real deployments | $0 | Synthetic data cannot measure behavioural effect | 6 | **test** |
| 47 | Splink (Fellegi-Sunter, EM) for patient/provider linkage across sources | ingestion | Splink (UK MoJ, IJPDS 2022); ABS internal evaluation [sec]: comparable quality, more efficient; OpenSanctions Pairs (arXiv 2026): LLMs 98.95 F1 vs rule baseline 91.33 on sanctions entities | Pairwise F1 on synthetic duplicates | Local, DuckDB backend | Synthetic IDs are already clean | 6 | **watch** |
| 48 | Discharge-count forecasting (TEND-style) and discrete-event digital twins for capacity questions | `demand`, `scheduling` | TEND, J Hosp Med 2018; JMIR 2025 multisite discharge forecasting (174,799 discharges); PUNDIT digital twin (NIHR 2024 feasibility) | Bed-day forecast error; occupancy scenario answers | Medium (simpy) | Synthetic generator lacks real operational dynamics | 6 | **watch** |
| 49 | Overbooking with fairness audit by age/payer | `scheduling` | Rural Wisconsin no-show model (BMC HSR 2023): AUC 0.83, 1 overbook per 6 at-risk appointments; no located 2024-25 fairness study | Overtime/wait trade-off and burden by subgroup | $0 | Equity burden falls on groups with higher no-show | 6 | **test** |
| 50 | Speculative / prompt-lookup decoding for the local SQL model | MLX runner | Consumer-hardware study (arXiv 2607.17283) [abs]: best 1.61×, 3 of 5 configs slower; Apple ReDrafter: up to 2.3× in MLX; EAGLE-3 MLX prototype 1.05× [sec] | Local p50 latency | Low | Often slower on Apple Silicon | 6 | **watch** |
| 51 | LoRA settings for bottling: adapters on all layers including MLP, higher LR than full fine-tune, more than 16 of 36 layers | `tools/bottling/lora.yaml` (currently `num_layers: 16`, LR 1e-4) | Thinking Machines "LoRA Without Regret" (2025) [sec]: LoRA matches full fine-tune when applied to all layers with ~10× LR; Biderman et al. (ICLR 2025): LoRA learns less, forgets less | Local EX on held-out non-bench set per config | Hours on the Mac | Overfit with small verified sets | 6 | **test** |
| 52 | EHR foundation models (MOTOR, CoMET/Curiosity, ETHOS, Delphi-2M, Context Clues/Mamba) for trajectory and survival | `trajectory`, `survival` | MOTOR (ICLR 2024): +4.6% time-dependent C, 95% label efficiency, 143M weights public; CoMET (arXiv 2508.12104): 118M patients, matches/beats supervised on 78 tasks; ETHOS (npj DM 2024) zero-shot; Delphi-2M (Nature 2025) 1,000+ diseases; Context Clues (ICLR 2025): 16k-context Mamba best on 9/14 EHRSHOT tasks | Not measurable on synthetic data without circularity | Large | Synthetic data makes evaluation meaningless; real-data weights trained on populations Agere does not see | 4 | **watch** |
| 53 | LLM-serialized EHR text as patient embeddings (cheap challenger to bespoke encoders) | `context_*`, `trajectory` | Hegselmann et al. (arXiv 2502.17403): general LLM embeddings over Markdown-serialized records as EHR encoders on EHRSHOT and UK Biobank [abs] | AUROC vs current features on synthetic tasks | Local embedding model | Same synthetic-data caveat | 4 | **watch** |
| 54 | Open-ended data-science agents as an answer path | analysis graph | DSBench (ICLR 2025): ≤34.12% on analysis tasks; DA-Code (EMNLP 2024): 30.5%; DS-Agent (ICML 2024) case-based reuse works when cases exist; Data Interpreter (2024) | Not recommended as answer path; Agere's typed-operator design already matches the CBR lesson | High | Unverifiable outputs | 4 | **skip** |
| 55 | GraphRAG for the `knowledge` engine | `knowledge` | "When to use Graphs in RAG" (ICLR 2026): GraphRAG frequently underperforms vanilla RAG; cited ~13% lower on NQ, +4.5% on HotpotQA multi-hop at 2.3× latency | None expected | Medium | Cost without gain | 3 | **skip** |
| 56 | In-context causal PFNs (Do-PFN, CausalPFN) for `program_effect` | `program_effect` | Do-PFN (NeurIPS 2025) synthetic/semi-synthetic only; independent clinical simulation (arXiv 2603.15928): biased g-computation, CausalPFN under-covers 95% intervals | None until coverage is fixed | Low | Silent bias | 3 | **skip** |
| 57 | Same-model self-critique / "reflect and fix" loops as verification | verified ladder | Stechly, Valmeekam, Kambhampati (arXiv 2402.08115; NeurIPS 2023): self-critique worsens results; gains come from sound external verifiers | Remove any such loop; use #2, #7, #8 instead | Saves calls | None | 3 | **skip** |

---

## 2. Area summaries (critical)

### 2.1 Text-to-SQL state of the art: what moves accuracy on messy warehouses

**Where the field is.** BIRD test execution accuracy has climbed from DIN-SQL's 55.9% (NeurIPS 2023) to 73.0% for
CHASE-SQL (ICLR 2025), 75.63% for XiYan-SQL (2025), 81.67% for Agentar-Scale-SQL (Ant Group, Nov 2025 leaderboard)
and a reported 82.39% for Huawei's DataGallery (secondary coverage, Sept 2026; semantic layer plus data agent). Human
performance is cited at 92.96%. ReViSQL (UIUC, Mar 2026) reports 93.2% on an expert-verified Mini-Dev, which is not
comparable to the public leaderboard.

**What actually drives those gains**, read across CHASE, XiYan, DeepEye, SIRIUS, Agentar and ReFoRCE:

1. *Diverse candidate generation* (different prompts, models, SQL styles, decompositions), then
2. *execution-grounded filtering* (drop errors, empties; cluster by result), then
3. *a selector* that is either learned (CHASE pairwise model, XiYan selection model, GradeSQL ORM) or confidence-gated
   (DeepEye cluster share, SIRIUS confidence gate with tuple-level consensus), and
4. *value retrieval* for literal matching (CHESS LSH over millions of values; Arctic-R1 also adds value retrieval).

Schema linking matters less than it did: Distyl's "Death of Schema Linking?" shows that when the full schema fits, a
strong model does better without pruning; pruning remains useful for small models and very large warehouses.

**Enterprise reality is far worse than BIRD.** BEAVER (real university warehouses, 99-366 tables) reports about 10.8%
for agentic GPT-5.2 frameworks, 30.1% even with oracle subtask hints. Spider 2.0 tops out in the 30s-50s
(ReFoRCE 35.8-36.6; DeepEye 50.5 Snow / 38.2 Lite; LinkAlign 33.09 Lite with open models). The gap is joins, business
definitions and warehouse-specific knowledge, exactly the FM-05/06/07 family in Agere's FMEA.

**The benchmarks themselves are noisy.** Jin, Choi, Zhu and Kang (CIDR 2026 / PVLDB 19) judge 52.8% of BIRD Mini-Dev and
62.8% of Spider 2.0-Snow problems to contain annotation errors; correcting 100 dev examples reordered agents (CHESS
62%→81%), and the uncorrected ranking correlates only weakly with the corrected one (Spearman 0.32). FLEX (NAACL 2025)
finds execution accuracy produces both false positives and false negatives. **Skeptical read:** leaderboard deltas under
~3 points are within label noise; architecture papers often encode benchmark quirks. ReViSQL's strongest finding is that
cleaning training data alone gave +8.2 to +13.9 points under identical RL, which argues for Agere spending effort on its
own verified data (#5, #12) before adopting heavier pipelines.

**Implication for Agere.** Agere's semantic layer already removes most of the schema-linking problem that dominates
BIRD and Spider. The transferable lessons are candidate diversity across *independent* generators, execution-grounded
selection with abstention, and treating the training/verified store as the asset (items 2, 3, 5, 10, 14).

### 2.2 Verification and reliability

- **Correlated errors are the central problem and the literature confirms it.** Kim, Garg et al. (ICML 2025) measured
  350+ models: when two models are both wrong, they choose the same wrong answer about 60% of the time on HELM, more for
  same-developer pairs, and larger, more accurate models are *more* correlated. This directly undercuts Haiku+Sonnet
  agreement (FM-13). Execution-voting papers (DeepEye, CSC-SQL, XiYan) independently report that the majority can be
  confidently wrong, and the self-consistency vs pass@k gap widens with more samples.
- **Repeated sampling raises coverage; selection is the bottleneck.** "Large Language Monkeys" (Brown et al., 2024):
  coverage grows log-linearly with samples (SWE-bench Lite 15.9%→56% at 250 samples), but majority vote and reward models
  saturate below 100 samples when no automatic verifier exists. SQL has a partial verifier (execution), which is why
  investment belongs in *oracles that do not share the generator's blind spots*: partition invariants (TLP), question-side
  metamorphic relations (SQLHD), counterexample databases (VeriEQL/SpotIt), and typed definitions.
- **Self-critique is not verification.** Stechly, Valmeekam and Kambhampati show self-critique loops underperform
  guessing plus a sound external verifier. Any judge step that re-reads the same evidence the generator saw should be
  treated as weak.
- **Uncertainty.** Semantic entropy (Farquhar et al., Nature 2024) detects confabulation by clustering sampled meanings;
  for SQL, "meaning" can be the execution result, so cluster entropy is semantic entropy almost for free (item 2).
  Semantic entropy probes (Kossen et al. 2024) give a single-pass approximation from hidden states, worth testing on the
  local model only.
- **Calibrated abstention with guarantees.** Learn-then-Test and conformal abstention give finite-sample error-rate
  control for any score. Agere's current thresholds (`DECISION_THRESHOLD=.95`, router "≥98% held-out") are engineering
  cutoffs; LTT would turn them into stated guarantees. Caveat: guarantees are marginal over an exchangeable question
  stream; a shifted question mix (new buyer, new domain pack) needs recalibration.
- **LLM judges** carry self-preference and family bias (preference leakage, MLLM-judge studies; mostly secondary sources
  here). FLEX shows an LLM judge can beat execution accuracy against expert labels (kappa 62→87) when given explicit
  criteria; that supports using FMEA modes as the judge rubric and measuring the judge against saved adjudications.

### 2.3 Agentic data analysis, semantic layers, insight discovery

- **Open-ended agents are not ready as an answer path.** DSBench (≤34.12% on analysis tasks), DA-Code (30.5%),
  InsightBench and its critique InsightEval (format inconsistencies, redundant insights), and MedInsightBench (poor
  multimodal performance) all point the same way. DS-Agent's lesson (case-based reuse of solved tasks) is the one Agere
  already embodies with typed operators and verified reuse.
- **Semantic layers carry most of the enterprise accuracy.** Every located number is vendor-reported or small-sample
  (dbt 2026; data.world; Snowflake Cortex internal 150 questions; Databricks Genie "32% to over 90%" secondary), but they
  agree in direction and size. For Agere this is the strongest argument for item 4: push HEDIS denominators, attribution,
  clocks and value sets into typed, testable definitions so that SQL writers compose them instead of re-deriving them.
- **Insight discovery that survives scrutiny is statistical, not generative.** MacroBase DIFF (risk ratio with support
  thresholds) and Correll-Heer Bayesian surprise are old, cheap and well-founded; they address the two classic failure
  modes of automated "insights": tiny-denominator noise and base-rate artefacts.
- **Visualization recommendation** (LIDA, LLM4Vis, VisJudge-Bench) lacks a shared benchmark; models read basic charts
  well and misleading charts poorly. Low priority for Agere's typed views.

### 2.4 Distillation, small models, efficiency

- **SQL-specialist open models** beat general models of the same size on public benchmarks (OmniSQL-7B 61.2 avg vs 52.8
  base; Arctic-Text2SQL-R1 with execution-only reward). Whether that transfers to Agere's semantic tables is the
  experiment (item 15); prior Agere notes already caution against committing to Qwen.
- **Local DB workloads favour small open models economically.** Glenn & Samuel (Capital One code, arXiv 2606.31808)
  report 390× lower cost and 3.8× lower latency than a proprietary API for LM-in-DB operators at matched or better
  accuracy, on 16 GB VRAM.
- **Prompt optimization** (GEPA, ICLR 2026) is sample-efficient enough to run against Haiku on a development split for a
  few dollars. The risk is leakage: Agere's blind sets must never be in the optimization loop.
- **Constrained decoding** guarantees syntax, not semantics; for 4B models it may even hurt (StructureBench). Under
  constraints, beam beats sample+vote at matched budget, but a bigger model beats more inference (arXiv 2608.25761).
- **Routing and caching.** RouteLLM shows learned routers from preference data cut cost >2× at equal quality; vCache
  shows per-entry thresholds outperform a single cosine cutoff with an explicit error bound. Agere's slot-signature cache
  is already conservative; the expected gain is in hit rate, not safety.
- **Speculative decoding on Apple Silicon** is unreliable (1.05×-2.3×, often slower); prompt-lookup decoding for SQL
  repair is the only variant worth a quick test.
- **LoRA.** Agere's `lora.yaml` adapts 16 layers at LR 1e-4. Thinking Machines (secondary read) and Biderman et al. both
  suggest full-layer adapters including MLP and a higher learning rate are needed for LoRA to match full fine-tuning;
  cheap to test.

### 2.5 Retrieval

Schema retrieval has its own benchmark now (SQL Schema Retrieval, Zeng et al. 2025: column-level nDCG/recall) and
CRUSH4SQL's insight that schema retrieval must rank *sets* of items still holds. Agere's `schema_rerank.py` (BM25 +
encoder + per-type logistic heads trained on verified SQL) is close to the published state of practice; the open question
is whether frontier rungs should see the whole semantic schema (item 17). ColBERT/PLAID late interaction is a credible
upgrade for passage retrieval in `knowledge` but no located study applies it to text-to-SQL schemas. GraphRAG is a skip
for now: the ICLR 2026 analysis finds it frequently underperforms vanilla RAG, with gains limited to some multi-hop
questions at 2.3× latency. Medical ontologies matter more as *candidate generators* for value sets (RASC uses ICD-10
children/siblings, RxNorm relations and SNOMED neighbours) than as graphs to traverse at answer time.

### 2.6 Healthcare-specific models

- **EHR foundation models** are maturing fast: MOTOR (ICLR 2024, 55M records, public 143M model), CoMET/Curiosity (Epic
  Cosmos, 118M patients, power-law scaling, matches supervised models on 78 tasks), ETHOS (zero-shot, MIMIC-IV), Delphi-2M
  (Nature 2025, 1,000+ diseases, validated on Danish registries), Context Clues (16k-context Mamba best on 9/14 EHRSHOT
  tasks; copy-forwarding and irregular timing hurt all models). **Skeptical read for Agere:** with synthetic data only,
  any evaluation measures fit to the generator, not to medicine. These are watch items until real data or a credentialed
  public dataset is in scope.
- **Clinical LLMs.** MedGemma 27B scores 90.5% on EHRQA versus 94.9-95.2% for Gemini 3 Flash/Pro (MedGemma 1.5 report);
  MedHELM (Nature Medicine) finds reasoning models lead with Claude 3.5 Sonnet comparable at 15% lower cost; HealthBench
  provides 48,562 physician-written rubric criteria. These are evaluation resources for the `knowledge` engine, not
  components to adopt.
- **Coding automation.** Direct generation is poor (GPT-4 exact match 33.9% for ICD-10-CM in the Mount Sinai NEJM AI
  study, secondary read); retrieval-constrained ranking works on simplified inputs and remains hard over the full
  72,750-code space (KREL 2026). Same conclusion for value sets (RASC/RASC+).
- **Clinical NER** small models (GLiNER-BioMed, OpenMed NER) are strong, permissive, CPU-sized candidate generators; they
  fit Agere's "frontier labels once, rules run free" pattern in `note_facts.py`.
- **Agent benchmarks in EHRs.** MedAgentBench (Stanford, 300 tasks, FHIR; Claude 3.5 Sonnet v2 69.67%), FHIR-AgentBench
  (Verily/KAIST/MIT, 2,931 questions on MIMIC-IV-FHIR), EHR-ChatQA (ICLR 2026, Pass^5 far below Pass@5) and the EHRSQL 2024
  shared task (abstention scored with penalties; no team positive under the strictest setting) are the most relevant
  external yardsticks for Agere's refusal and reliability behavior.
- **Synthetic data evaluation.** Benchmarks of synthetic EHR generators find a utility-privacy trade-off and no single
  best method; Synthea-specific evaluations (OHDSI poster) find unrealistic medication distributions and weak chronic
  disease tracking. Every engine result in this report that is measured on synthetic data measures engineering
  correctness, not clinical truth.

### 2.7 Tabular and time-series foundation models

TabPFN v2 (Nature 2025) beats tuned baselines on ≤10k-row tables, and TabArena (NeurIPS 2025) confirms foundation models
excel on small data while tuned GBDTs remain strong elsewhere. Clinical replications are mixed: a 992-patient sudden
cardiac death study found no small-data advantage on the independent test set and rank reversals between CV and test;
a perioperative study found TabPFN-2.5 comparable to XGBoost. For time series, Chronos-2 leads GIFT-Eval on win rate and
skill (79.8%/46.6 WQL vs TimesFM-2.5 70.0%/42.4), but GIFT-Eval normalizes to Seasonal Naive and no located study
compares these models with tuned ETS/ARIMA on hospital volumes; Chronos-2's pretraining overlaps some GIFT-Eval training
portions. **Verdict:** challengers inside existing backtests, adopted only on Agere's own backtest wins, with licenses
checked (one secondary source reports Moirai 2.0 weights are non-commercial).

### 2.8 Causal inference and policy

The strongest practical result for Agere's buyers is Marafino et al.'s KPNC analysis: targeting by estimated benefit
rather than risk could prevent nearly four times as many readmissions, and the highest-risk patients appeared to benefit
least. It is observational, so the right evaluation tool is RATE/Qini (Yadlowsky et al., JASA 2025), which compares a
risk-based rule and a benefit-based rule on equal footing with valid inference. Obermeyer et al. is the cautionary
companion: the label chosen for a risk score can encode access inequity. For `program_effect`, modern staggered-adoption
estimators (SDID, Callaway-Sant'Anna, augmented and staggered synthetic control, sequential SDID) are standard and cheap;
Agere's DiD/synthetic-control engine should report their disagreement. Target trial emulation is the right contract for
any "did it work" question; LLM-assisted protocol extraction is promising (THESEUS 0.93-0.97 standardization accuracy on
OHDSI studies) but the NEJM AI editorial is right that automation cannot fix unmeasured confounding. In-context causal
PFNs are not ready (bias and under-coverage in an independent clinical simulation).

### 2.9 Anomaly, payment integrity, linkage, data quality

There is no agreed public fraud benchmark; studies use CMS public files with LEIE labels and their own splits. The most
credible recent result is unsupervised: Shekhar, Leder-Luis and Akoglu (JPAM 2026) report about 5× lift over random
targeting for overbilling hospitals, validated against DOJ lawsuits. GNN fraud papers exist (IJCAI 2023 RDPGL; Scientific
Reports 2025) but are hard to compare. Splink is the practical Fellegi-Sunter tool; LLM entity matching now beats rules
on sanctions data (98.95 vs 91.33 F1). For data quality, Raha/Baran remain strong low-label baselines; LLM-generated
functional dependencies (LLMClean) beat Raha on Hospital and IoT data, while LLM agents miss distribution-level errors,
which is where Agere's `monitor` (SPC, Benford) is the better tool.

### 2.10 Hospital operations and health economics

Discharge forecasting (TEND, 2018; JMIR 2025 multisite study) and discrete-event digital twins (Seattle Children's,
NIHR PUNDIT) are established practice but thinly evaluated in peer-reviewed OR literature found here. No-show-driven
overbooking works (AUC 0.83; one overbook per six at-risk slots in a rural Wisconsin system) but no located 2024-25 study
audits who bears the overbooking burden. Health economics offers two strong, cheap additions: risk-adjustment fairness
diagnostics (Zink & Rose: large group-fairness gains for small fit loss) and an explicit benchmark-ratchet simulator
(MedPAC and Brookings describe how rebasing penalizes ACOs for their own savings; LEAD reportedly locks a 3-year baseline
for 10 years; secondary).

### 2.11 Outside the box (clearly marked as speculative where no direct evidence exists)

- **Database testing oracles as answer checks (credible, direct transfer).** SQLancer's TLP and NoREC were built to find
  logic bugs in DBMSs without knowing the right answer. The same partition identities catch a model's NULL and duplicate
  handling errors in Agere's queries (item 8).
- **Distinguishing databases (credible transfer).** VeriEQL/SpotIt synthesize a small database on which two queries
  differ. Agere can use the idea in reverse: when two candidate SQLs coincide on the snapshot but differ structurally,
  generate a tiny counterexample and show the user the case that separates the two readings (feeds item 9).
- **Provenance semirings (speculative build).** Green, Karvounarakis and Tannen (PODS 2007) give every result tuple an
  algebraic record of which source rows produced it. Carrying why-provenance on each headline number would let a reader
  open the rows behind a figure, let audits sample by provenance, and let incremental recomputation touch only affected
  answers. ProvSQL shows it is implementable; cost on SQLite is unknown.
- **Units-of-measure types in the semantic layer (speculative; no paper located).** Compiler-style dimensional typing
  (dollars vs cents, days vs hours, fraction vs percent) would make FM-14 a type error instead of a runtime warning. This is
  Agere's own idea extrapolated from typed languages, not a located research result.
- **Bayesian surprise for executive attention (credible).** Correll and Heer show viewers over-read raw counts; ranking
  moves by surprise relative to a base-rate model is a cognitive-science fix for briefing pages.
- **Mechanism design for incentive programs (credible, slow).** Fair regression and group-importance search treat
  risk adjustment as a mechanism; the ratchet literature does the same for benchmarks. Agere can answer "what does this
  rule reward" questions rather than only "what happened".
- **Behavioral science for actions (credible).** A three-letter peer-comparison RCT cut targeted prescribing 11.1% with
  no detected harm; outreach to clinicians may matter more than outreach lists.
- **Information-gain clarification (credible, early).** Choosing the one question with the highest expected information
  gain (arXiv 2507.06467, abstract) is the decision-theoretic version of FM-19 disclosure.

---

## 3. Top 10 to build next

Order reflects dependency as well as score: 1 unblocks measurement; 2-5 attack the two highest RPN modes (FM-13, FM-23)
and the most frequent mode (FM-19); 6-10 deepen engines at low cost.

### 1. Restore and re-baseline the hosted route
- **What.** Diagnose the 39 execution errors in the Oct 12 scorecard run (harness, route exceptions, budget, model ids
  `claude-*-5-5` resolving), fix, and rerun the 50-question suite plus blind sets with cost recording complete.
- **Files.** `clinical_core/agere/clinical/economical_planner.py`, `nl2sql.py`, `bench/run.py`, `bench/adapters.py`.
- **Success.** Execution errors ≤2/50; Agere strict correct within 5 points of `frontier_sql`; `Cost complete = True`;
  $/correct below `frontier_sql`'s $0.43.

### 2. Capture only after checks; quarantine and audit the verified store (FM-23)
- **What.** Move `learning.record_verified` from the planner's hosted-SQL branch to after `analysis_graph.execute` and
  `answer_checks`; write pairs with any QC warning to a `quarantine` table; add a monthly audit that samples ~100 verified
  pairs, adjudicates them (Opus with saved adjudication format plus human spot-check), and reports the store's error rate
  with a PPI/PPI++ interval. Bottling (`tools/bottling/harvest.py`) reads only non-quarantined, audited-era pairs.
- **Files.** `economical_planner.py` (lines ~300-302), `learning.py`, `analysis_graph.py`, `tools/bottling/harvest.py`,
  `docs/agere/fmea.json`.
- **Success.** FM-23 gains a control and a measured occurrence; audited verified-store error rate published with a 90%
  interval; no QC-flagged pair reaches bottling data.

### 3. Independent N-version generation plus execution-cluster selection (FM-13)
- **What.** Generate candidates from three independent sources: (a) the hosted Anthropic model, (b) the local SQL model,
  (c) the semantic-layer/compiler plan where the operator router can express the question. Execute all, drop errors and
  empties, cluster by normalized result (tuple-level, order- and alias-insensitive), and accept only when the winning
  cluster contains at least two *independent* sources or its share clears a calibrated threshold (#5). Haiku+Sonnet
  agreement alone no longer counts as verification.
- **Files.** `nl2sql.py` (`verified_answer`), `verify.py`, `economical_planner.py`.
- **Success.** On adjudicated sets M/N/O, escape rate of the 24 hosted-route engine errors falls by ≥50% at ≤10% loss
  of coverage; FMEA FM-13 O and D re-scored from replay.

### 4. Two new deterministic controls: partition invariants and question-side metamorphic checks
- **What.** QC-PARTITION: for count/sum answers, run the TLP identities and group-total identity against the answer
  SQL. QC-METAMORPHIC: perturb numbers/years/qualifiers in the question (local model or rule-based), regenerate, and flag
  when the SQL does not change or changes in the wrong direction. Both heal-not-block: warnings only, like existing controls.
- **Files.** `answer_checks.py`, `docs/agere/FMEA.md`, `tools/quality/defects.py`.
- **Success.** Replay over all preserved blind answers shows fired-on-wrong ≥3× fired-on-right for each control (the bar
  QC-CLOCK and QC-UNITS already meet); FM-08, FM-09, FM-01 detection scores improve.

### 5. Calibrated gates via Learn-then-Test
- **What.** Replace `DECISION_THRESHOLD=.95`, the learned-router threshold and the new selection threshold with values
  chosen by LTT for a stated target (e.g. ≤2% wrong among answered at δ=0.1), fitted on non-benchmark labeled outcomes from
  `learning.outcomes` and adjudications; record the guarantee in the answer receipt.
- **Files.** `economical_planner.py`, `learned_router.py`, `conformal.py` (existing module), `tools/learned_routing/`.
- **Success.** Realized error among answered on the next blind run stays within the stated bound; coverage reported
  alongside.

### 6. Typed metric definitions in the semantic layer (FM-05/06/07)
- **What.** Add measure objects for the top recurring measures (GSD, CBP, KED, readmission, ED per 1,000, PMPM, no-show,
  denial rate) with explicit denominator, age band, enrollment rule, attribution, clock field and value-set id; expose
  them as views the SQL writers call by name; value sets built retrieve-then-classify from the concept library with an
  LLM only adjudicating candidates.
- **Files.** `semantic_layer.py`, `config/agere/definitions.json`, `concepts.py`, `drug_classes.py`, `nl2sql.TABLES`.
- **Success.** FM-05/06/07 adjudicated occurrence on blind reruns drops by half; QC-DENOM reports the encoded denominator
  name rather than one read from SQL.

### 7. Disclose alternatives when candidates disagree (FM-19)
- **What.** When the top two execution clusters both have support, render both with their defining difference
  (e.g. "visit date vs arrival date", "all members vs continuously enrolled") and the default chosen, plus one clarifying
  question chosen by expected information gain when the user is interactive.
- **Files.** `analysis_graph.py` (`build_answer`, `headline`), `conversation.py`, `nl2sql.py`.
- **Success.** Share of FM-19 adjudications where the alternative was invisible falls from 14/132 toward zero; no rise in
  FM-02 (wrong shape).

### 8. Benchmark hygiene: reference audit and pass^k
- **What.** For every blind question where Agere and reference disagree, mutate a small fixture database (insert NULLs,
  duplicates, boundary dates) to find a distinguishing case and record which reading the question supports; add pass^3 for
  hosted routes to the scorecard.
- **Files.** `bench/adjudicate.py`, `bench/score.py`, `bench/SCORECARD.md`, `bench/sql/`.
- **Success.** A corrected reference set with a change log; scorecard shows pass@1 and pass^3 side by side.

### 9. `discover`/`explain`/`market` statistics upgrade
- **What.** DIFF-style risk-ratio explanations with minimum support and multiplicity control; Bayesian-surprise ranking
  for market moves and briefing order; empirical-Bayes misclassification probability in `profile`.
- **Files.** `discover.py`, `briefing_rank.py`, market engine module, `profile` engine.
- **Success.** On synthetic worlds with planted hotspots and planted noise, precision@10 of reported findings ≥0.8 and
  planted-noise share in top 10 ≤10%.

### 10. `monitor`, `demand`, `outreach`, `program_effect` method upgrades
- **What.** RA-CUSUM with simulated control limits; MinT reconciliation in `demand`; uplift ranking evaluated with RATE
  and a label-choice disclosure in `outreach`; SDID and Callaway-Sant'Anna alongside existing DiD/synthetic control with
  their disagreement shown.
- **Files.** `monitor` engine, `demand.py`, outreach engine, program-effect engine, `capability_registry.py` (use text).
- **Success.** Each engine passes a planted-truth test in the synthetic generator: monitor false-alarm probability within
  target and detection delay reported; reconciled forecasts beat unreconciled MASE at ≥2 of 3 levels; uplift ranking
  RATE > risk ranking RATE when effects are heterogeneous; causal estimators' CIs cover the planted effect ≥90%.

**Next tier (test, with kill criteria):** SQL-specialist local base (#15; kill if no gain over Qwen3-4B on non-bench
held-out), learned ORM selector (#14; kill if <2 points sel-acc over cluster selection), GEPA on prompts (#16; kill if
blind gain <2 points), full-schema ablation (#17), template-constrained decoding (#10), LoRA config sweep (#51).

---

## 4. Bibliography

Marks: **[abs]** read at abstract/listing level only; **[sec]** known only through a secondary source (blog, aggregator,
review site, news); unmarked items were read at abstract plus additional page content or results summaries. Links are
those located during this sweep.

### Text-to-SQL
1. Pourreza & Rafiei, DIN-SQL, NeurIPS 2023. https://neurips.cc/virtual/2023/poster/70430 [abs]
2. Wang et al., MAC-SQL, COLING 2025. https://arxiv.org/abs/2312.11242 [abs]
3. Gao et al., Text-to-SQL Empowered by LLMs: A Benchmark Evaluation (DAIL-SQL), PVLDB 17, 2024. https://www.vldb.org/pvldb/vol17/p1132-gao.pdf [abs]
4. Talaei et al., CHESS, arXiv 2405.16755 (MAS@ICML 2025). https://arxiv.org/abs/2405.16755
5. Pourreza et al., CHASE-SQL, ICLR 2025. https://arxiv.org/abs/2410.01943
6. Gao/Liu et al., XiYan-SQL, arXiv 2411.08599 and 2507.04701. https://arxiv.org/abs/2507.04701
7. Li et al., Alpha-SQL, ICML 2025. https://arxiv.org/abs/2502.17248
8. Deng et al., ReFoRCE, arXiv 2502.00675 (v5). https://arxiv.org/abs/2502.00675
9. Agentar-Scale-SQL, Ant Group, arXiv 2509.24403. https://arxiv.org/abs/2509.24403
10. DeepEye-SQL, arXiv 2510.17586. https://arxiv.org/abs/2510.17586 [abs]
11. SIRIUS-SQL, arXiv 2606.01246. https://arxiv.org/abs/2606.01246 [abs]
12. CSC-SQL: Corrective Self-Consistency, arXiv 2505.13271. https://arxiv.org/abs/2505.13271 [sec]
13. Borchmann & Wydmuch, Query and Conquer: Execution-Guided SQL Generation, arXiv 2503.24364. https://arxiv.org/abs/2503.24364 [abs]
14. Maamari et al., The Death of Schema Linking?, NeurIPS 2024 workshop. https://arxiv.org/abs/2408.07702
15. Li et al., OmniSQL, PVLDB 18. https://arxiv.org/abs/2503.02240
16. Snowflake AI Research, Arctic-Text2SQL-R1, ACL Findings 2026. https://arxiv.org/abs/2505.20315 [abs]
17. Zhu, Jin, Choi, Kang, ReViSQL, arXiv 2603.20004. https://arxiv.org/abs/2603.20004 [abs]
18. Jin, Choi, Zhu, Kang, Pervasive Annotation Errors Break Text-to-SQL Benchmarks and Leaderboards, PVLDB 19 / CIDR 2026. https://arxiv.org/abs/2601.08778
19. Chen et al., BEAVER: An Enterprise Benchmark for Text-to-SQL, arXiv 2409.02038 (v2). https://arxiv.org/abs/2409.02038
20. Spider 2.0 project repository and leaderboard notes. https://github.com/LY-Tri/Spider2 [sec]
21. LinkAlign, EMNLP 2025. https://underline.io/lecture/129803-linkalign-scalable-schema-linking-for-real-world-large-scale-multi-database-text-to-sql [abs]
22. CRED-SQL, arXiv 2508.12769. https://arxiv.org/abs/2508.12769 [abs]
23. Kothyari et al., CRUSH4SQL, EMNLP 2023. https://arxiv.org/abs/2311.01173 [abs]
24. Zeng et al., SQL Schema Retrieval benchmark. https://huggingface.co/datasets/pxyu/SQL-Schema-Retrieval [abs]
25. TARGET: Benchmarking Table Retrieval for Generative Tasks, arXiv 2505.11545. https://arxiv.org/abs/2505.11545 [abs]
26. Saparina & Lapata, AMBROSIA, NeurIPS 2024 D&B. https://arxiv.org/abs/2406.19073
27. BIRD-INTERACT, ICLR 2026. https://arxiv.org/abs/2510.05318
28. Liu & Erdelt, ambiguity detection on BIRD-Interact-Lite, VLDB AIDB 2026. https://www.vldb.org/2026/Workshops/VLDB-Workshops-2026/AIDB/aidb26_10.pdf [abs]
29. Interactive Text-to-SQL via Expected Information Gain, arXiv 2507.06467. https://arxiv.org/abs/2507.06467 [abs]
30. AmbiSQL / PleaSQLarify (interactive disambiguation), arXiv 2508.15276; 2603.01795. https://arxiv.org/html/2508.15276v2 [sec]
31. PRACTIQ, NAACL 2025. https://aclanthology.org/2025.naacl-long.13 [sec]
32. Lee et al., Overview of the EHRSQL 2024 Shared Task, ClinicalNLP 2024. https://arxiv.org/abs/2405.06673
33. Lee et al., EHR-ChatQA, ICLR 2026. https://arxiv.org/abs/2509.23415
34. TeCoD: Template Constrained Decoding, PACMMOD 2025. https://arxiv.org/abs/2604.28028 [abs]
35. GRID: Grammar-Railed Decoding for Enterprise SQL, arXiv 2607.11951. https://arxiv.org/abs/2607.11951 [abs]
36. Chermsirivatana & MacCormick, Beam search vs self-consistency under grammar constraints, arXiv 2608.25761. https://arxiv.org/abs/2608.25761 [abs]
37. StructureBench, IJCAI 2026. https://www.ijcai.org/proceedings/2026/267 [abs]
38. XGrammar, arXiv 2411.15100; XGrammar-2, arXiv 2601.04426. https://arxiv.org/abs/2411.15100 [abs]
39. llguidance. https://github.com/guidance-ai/llguidance [sec]
40. Glenn & Samuel, Large Databases Need Small, Open-Weight Language Models, arXiv 2606.31808. https://arxiv.org/abs/2606.31808 [abs]
41. BIRD leaderboard coverage (Gemini-SQL2, DataGallery). https://aiweekly.co/alerts/googles-gemini-sql2-tops-bird-text-to-sql-at-8004 ; https://technologymagazine.com/news/huawei-datagallery-tops-benchmark-for-enterprise-text2sql-ai [sec]

### Verification, uncertainty, evaluation
42. Kim, Garg, Peng, Garg, Correlated Errors in Large Language Models, ICML 2025. https://arxiv.org/abs/2506.07962
43. Brown et al., Large Language Monkeys, arXiv 2407.21787. https://arxiv.org/abs/2407.21787
44. Farquhar, Kossen, Kuhn, Gal, Semantic entropy, Nature 2024. https://pmc.ncbi.nlm.nih.gov/articles/PMC11186750
45. Kossen et al., Semantic Entropy Probes, arXiv 2406.15927. https://arxiv.org/abs/2406.15927
46. Abbasi-Yadkori et al., Mitigating LLM Hallucinations via Conformal Abstention, arXiv 2405.01563. https://arxiv.org/abs/2405.01563
47. The Reliability Gap in LLMs (conformal abstention), arXiv 2604.27914. https://www.alphaxiv.org/overview/2604.27914 [sec]
48. Angelopoulos, Bates, Candès, Jordan, Lei, Learn then Test, Ann. Appl. Stat. 2025. https://arxiv.org/abs/2110.01052
49. Mohri & Hashimoto, Language Models with Conformal Factuality Guarantees, ICML 2024. https://proceedings.mlr.press/v235/mohri24a.html
50. Quach et al., Conformal Language Modeling, ICLR 2024. https://arxiv.org/abs/2306.10193
51. Tritto et al., GradeSQL, PVLDB; and Test-Time Verification for Text-to-SQL via ORMs, arXiv 2606.30851. https://arxiv.org/abs/2509.01308 [abs]
52. STaR-SQL, arXiv 2502.13550. https://arxiv.org/abs/2502.13550 [abs]
53. Zhang et al., Generative Verifiers (GenRM), ICLR. https://liner.com/review/generative-verifiers-reward-modeling-as-nexttoken-prediction [sec]
54. Stechly, Valmeekam, Kambhampati, On the Self-Verification Limitations of LLMs, arXiv 2402.08115. https://arxiv.org/abs/2402.08115
55. Valmeekam et al., Investigating self-critiquing in planning, NeurIPS 2023. https://neurips.cc/virtual/2023/82801 [abs]
56. FLEX: False-Less EXecution metric, NAACL 2025. https://aclanthology.org/2025.naacl-long.228
57. ETM: SQL evaluation false positives, arXiv 2407.07313. https://arxiv.org/abs/2407.07313 [abs]
58. SpotIt / SpotIt+: verification-based text-to-SQL evaluation, arXiv 2510.26840. https://arxiv.org/abs/2510.26840 [abs]
59. VeriEQL bounded SQL equivalence (SFU announcement, PVLDB 17 p3602). https://vldb.org/pvldb/vol17/p3602-wang.pdf [abs]
60. QED query equivalence decider. https://github.com/qed-solver [sec]
61. SQLHD: two-stage metamorphic testing for text-to-SQL hallucinations, arXiv 2512.22250. https://arxiv.org/abs/2512.22250 [abs]
62. MT-Teql, arXiv 2012.11163. https://arxiv.org/abs/2012.11163 [abs]
63. MRSQLGen: metamorphic prompting (ACM DOI 10.1145/3797146). https://www.citedrive.com/en/discovery/validating-llm-generated-sql-queries-through-metamorphic-prompting/ [sec]
64. Metamorphic testing and LLMs survey, arXiv 2605.13898. https://arxiv.org/abs/2605.13898 [abs]
65. LLM-as-judge self-preference and family bias (practitioner summaries). https://www.promptlayer.com/glossary/self-preference-bias [sec]

### Agentic analysis, semantic layers, insight
66. Hong et al., Data Interpreter, arXiv 2402.18679. https://arxiv.org/abs/2402.18679
67. Guo et al., DS-Agent, ICML 2024. https://proceedings.mlr.press/v235/guo24b.html
68. Jing et al., DSBench, ICLR 2025. https://arxiv.org/abs/2409.07703
69. Huang et al., DA-Code, EMNLP 2024. https://aclanthology.org/2024.emnlp-main.748
70. InsightBench, arXiv 2407.06423; InsightEval, ACL Findings 2026, arXiv 2511.22884. https://arxiv.org/abs/2511.22884 [abs]
71. MedInsightBench, arXiv 2512.13297. https://arxiv.org/abs/2512.13297 [abs]
72. dbt Labs, Semantic layer vs text-to-SQL 2026. https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026 [sec]
73. Snowflake engineering blog, Cortex Analyst accuracy. https://www.snowflake.com/engineering-blog/cortex-analyst-text-to-sql-accuracy-bi/ [sec]
74. Sequeda et al. (data.world) knowledge-graph text-to-SQL benchmark (via author page). https://www.catalyzex.com/author/Juan%20Sequeda [sec]
75. Abuzaid et al., DIFF: A Relational Interface for Large-Scale Data Explanation, VLDB 2019. https://people.eecs.berkeley.edu/~matei/papers/2019/vldb_macrobase_diff.pdf
76. Correll & Heer, Surprise! Bayesian Weighting for De-Biasing Thematic Maps, IEEE TVCG 2017 (via package docs). https://cran.csiro.au/web/packages/bayesiansurpriser/readme/README.html [sec]
77. Dibia, LIDA, ACL 2023 demos. https://aclanthology.org/2023.acl-demo.11 [abs]
78. VisJudge-Bench, ICLR 2026. https://arxiv.org/abs/2510.22373 [abs]
79. State of the Art of LLM-Enabled Interaction with Visualization, arXiv 2601.14943. https://arxiv.org/abs/2601.14943 [abs]

### Efficiency, routing, caching, fine-tuning
80. Ong et al., RouteLLM, ICLR 2025. https://arxiv.org/abs/2406.18665
81. Chen, Zaharia, Zou, FrugalGPT, 2023 (via secondary). https://neuraltrust.ai/blog/llm-model-routing [sec]
82. Schroeder et al., vCache: Verified Semantic Prompt Caching, ICLR 2026. https://arxiv.org/abs/2502.03771
83. Agrawal et al., GEPA, ICLR 2026. https://arxiv.org/abs/2507.19457
84. Biderman et al., LoRA Learns Less and Forgets Less, ICLR 2025. https://arxiv.org/abs/2405.09673
85. Thinking Machines, LoRA Without Regret. https://thinkingmachines.ai/blog/lora/ [sec]
86. Lossless but Not Free: Speculative Decoding on Consumer Hardware, arXiv 2607.17283. https://arxiv.org/abs/2607.17283 [abs]
87. Apple, Recurrent Drafter. https://machinelearning.apple.com/research/recurrent-drafter [abs]
88. EAGLE-3 MLX prototype discussion. https://github.com/ml-explore/mlx-lm/discussions/890 [sec]
89. KV cache recycling in low-parameter LLMs, arXiv 2512.11851. https://arxiv.org/abs/2512.11851 [abs]
90. NVIDIA NIM KV cache reuse docs. https://docs.nvidia.com/nim/large-language-models/1.7.0/kv-cache-reuse.html [sec]
91. M-RARU: LLM on a Budget, Active Knowledge Distillation, arXiv 2511.11574. https://arxiv.org/abs/2511.11574 [abs]
92. LLKD, arXiv 2411.08028. https://arxiv.org/abs/2411.08028 [abs]
93. Angelopoulos, Duchi, Zrnic, PPI++, arXiv 2311.01453. https://arxiv.org/abs/2311.01453

### Retrieval and knowledge
94. Santhanam et al., PLAID, arXiv 2205.09707. https://arxiv.org/abs/2205.09707 [abs]
95. KET-QA (bi-encoder + cross-encoder), arXiv 2405.08099. https://arxiv.org/abs/2405.08099 [abs]
96. When to use Graphs in RAG, ICLR 2026. https://arxiv.org/abs/2506.05690

### Healthcare models and benchmarks
97. Steinberg, Fries, Xu, Shah, MOTOR, ICLR 2024. https://arxiv.org/abs/2301.03150
98. Wornow et al., EHRSHOT, NeurIPS 2023 D&B. https://arxiv.org/abs/2307.02028
99. Wornow et al., Context Clues, ICLR 2025. https://arxiv.org/abs/2412.16178
100. Fallahpour et al., EhrMamba, arXiv 2405.14567. https://arxiv.org/abs/2405.14567 [abs]
101. CoMET / Curiosity: Generative Medical Event Models Improve with Scale, arXiv 2508.12104. https://arxiv.org/abs/2508.12104
102. Renc et al., ETHOS, npj Digital Medicine 2024. https://arxiv.org/abs/2407.21124
103. Shmatko et al., Delphi-2M, Nature 2025. https://www.ukbiobank.ac.uk/publications/learning-the-natural-history-of-human-disease-with-generative-transformers/ [abs]
104. Hegselmann et al., LLMs are Powerful EHR Encoders, arXiv 2502.17403. https://arxiv.org/abs/2502.17403 [abs]
105. Japanese nationwide claims foundation model, arXiv 2604.22348. https://www.alphaxiv.org/abs/2604.22348 [abs]
106. MedGemma Technical Report, arXiv 2507.05201; MedGemma 1.5, arXiv 2604.05081. https://arxiv.org/abs/2604.05081 [abs]
107. MedHELM, Nature Medicine (Stanford HAI). https://hai.stanford.edu/news/holistic-evaluation-of-large-language-models-for-medical-applications [abs]
108. HealthBench, arXiv 2505.08775. https://arxiv.org/abs/2505.08775
109. MedAgentBench, arXiv 2501.14654. https://arxiv.org/abs/2501.14654
110. FHIR-AgentBench, ML4H 2025, arXiv 2509.19319. https://arxiv.org/abs/2509.19319 [abs]
111. GLiNER-BioMed, arXiv 2504.00676 / Bioinformatics 2026. https://arxiv.org/abs/2504.00676
112. OpenMed NER, arXiv 2508.01630. https://arxiv.org/abs/2508.01630
113. Kwan, Large language models are good medical coders if provided with tools, arXiv 2407.12849. https://arxiv.org/abs/2407.12849 [abs]
114. KREL automatic medical coding, arXiv 2608.20887. https://arxiv.org/abs/2608.20887 [abs]
115. GAVS ICD-10 coding on MIMIC-IV, JMIR Biomed Eng 2025. https://biomedeng.jmir.org/2025/1/e66691 [abs]
116. RASC: Retrieve, Then Classify for value sets, PMLR v340 / arXiv 2604.14616; RASC+, arXiv 2606.23992. https://arxiv.org/abs/2604.14616 [abs]
117. OHDSI 2025 VSAC-to-OMOP with LLM poster. https://www.ohdsi.org/wp-content/uploads/2025/10/603-barrett-robert_VSAC_to_OMOP_LLM-Robert-Barrett.pdf [abs]
118. Yan et al., Multifaceted Benchmarking of Synthetic EHR Generation, arXiv 2208.01230; review with benchmarking, arXiv 2411.04281. https://arxiv.org/abs/2411.04281 [abs]
119. Wagner & Blacketer, Evaluating Synthea (OHDSI 2024 poster). https://ohdsi.org/wp-content/uploads/2024/10/41-Wagner-Evaluating_Synthea-Clair-Blacketer.pdf [abs]

### Tabular and time series
120. Hollmann et al., TabPFN v2, Nature 2025. https://pubmed.ncbi.nlm.nih.gov/39780007/ [abs]
121. Erickson et al., TabArena, NeurIPS 2025 D&B. https://arxiv.org/abs/2506.16791 [abs]
122. Kim et al., CARTE, ICML 2024. https://proceedings.mlr.press/v235/kim24d.html [abs]
123. Retrieval-aligned tabular FMs for clinical risk, arXiv 2604.01841. https://arxiv.org/abs/2604.01841 [abs]
124. TabPFN in perioperative classification (UTS). https://opus.lib.uts.edu.au/bitstream/10453/195405/2/ [abs]
125. Ansari et al., Chronos-2, arXiv 2510.15821. https://arxiv.org/abs/2510.15821
126. TimesFM-2.5 release coverage. https://www.marktechpost.com/2025/09/16/google-ai-ships-timesfm-2-5-smaller-longer-context-foundation-model-that-now-leads-gift-eval-zero-shot-forecasting/ [sec]
127. Cisco Time Series Model technical report, arXiv 2511.19841. https://arxiv.org/abs/2511.19841 [abs]
128. LiveHouse-TS living benchmark, arXiv 2608.17299. https://arxiv.org/abs/2608.17299 [abs]
129. Wickramasuriya, Athanasopoulos, Hyndman, MinT, JASA 2019 (via hts docs). https://rdrr.io/pkg/hts/man/MinT.html
130. Iterative trace minimization for short hierarchies, arXiv 2409.18550. https://arxiv.org/abs/2409.18550 [abs]
131. ED arrivals SARIMAX/XGBoost/LSTM (Australian tertiary hospital), arXiv 2601.15481. https://arxiv.org/abs/2601.15481 [abs]

### Causal inference and policy
132. Chernozhukov et al., Double/Debiased ML, Econometrics Journal 2018. https://www.nber.org/papers/w23564
133. Athey, Tibshirani, Wager, Generalized Random Forests, Annals of Statistics 2019. https://arxiv.org/abs/1610.01271
134. Yadlowsky et al., RATE, JASA 2025. https://arxiv.org/abs/2111.07966
135. Marafino et al., Causal ML for preventable readmissions (KPNC), arXiv 2005.14409. https://arxiv.org/abs/2005.14409
136. Finkelstein et al., Camden Hotspotting RCT (J-PAL summary). https://www.povertyactionlab.org/ [sec]
137. Obermeyer, Powers, Vogeli, Mullainathan, Science 2019. https://chmi.berkeley.edu/news/dissecting-racial-bias-in-an-algorithm-used-to-manage-the-health-of-populations
138. Arkhangelsky & Samkov, Sequential Synthetic DiD, arXiv 2404.00164. https://arxiv.org/abs/2404.00164 [abs]
139. Ben-Michael, Feller, Rothstein, Synthetic controls with staggered adoption, JRSS-B 2022. https://ideas.repec.org/a/bla/jorssb/v84y2022i2p351-381.html [abs]
140. Synthetic control inference for staggered adoption, arXiv 1912.06320. https://arxiv.org/abs/1912.06320 [abs]
141. Robertson et al., Do-PFN, NeurIPS 2025. https://arxiv.org/abs/2603.15928 (independent evaluation) ; https://neurips.cc/virtual/2025/poster/118284 [abs]
142. THESEUS LLM-driven target trial specification, medRxiv 2026. https://www.medrxiv.org/content/10.64898/2026.03.13.26348306 [abs]
143. Dey, Qureshi, Shyu, LLM-driven TTE with human-in-the-loop, medRxiv 2026. https://www.medrxiv.org/content/10.64898/2026.04.09.26350523 [abs]
144. Dahabreh et al., NEJM AI editorial on automated TTE. https://hsph.harvard.edu/causalab/news/nejm-ai-editorial-publication [sec]
145. Dudík, Langford, Li, Doubly Robust Policy Evaluation. https://alphaxiv.org/abs/1503.02834 [abs]
146. Su et al., DR off-policy evaluation with shrinkage, arXiv 1907.09623. https://arxiv.org/abs/1907.09623 [abs]
147. CANDOR, CHIL 2026, PMLR v333. https://proceedings.mlr.press/v333/mandyam26a.html [abs]
148. Candès, Lei, Ren, Conformalized Survival Analysis, JRSS-B 2023. https://arxiv.org/abs/2103.09763

### Payment integrity, linkage, data quality
149. Shekhar, Leder-Luis, Akoglu, unsupervised overbilling detection, JPAM 2026. https://sites.bu.edu/jetson/files/2025/12/anomaly-JPAM.pdf [sec]
150. RDPGL risk-diffusion GNN for organized fraud, IJCAI 2023. https://www.ijcai.org/proceedings/2023/681 [abs]
151. Benchmarking TabPFN against relational models for provider fraud, ISSF 2026. https://www.atlantis-press.com/proceedings/issf-26/126025139 [abs]
152. CMS Crushing Fraud Chili Cook-Off. https://www.nextgov.com/artificial-intelligence/2025/08/cms-launches-chili-cook-competition-source-ai-can-detect-fraud/407698 [sec]
153. Splink (UK MoJ). https://www.gov.uk/government/publications/joined-up-data-in-government-the-future-of-data-linking-methods/splink-mojs-open-source-library-for-probabilistic-record-linkage-at-scale [abs]
154. OpenSanctions Pairs, arXiv 2603.11051. https://arxiv.org/abs/2603.11051 [abs]
155. LLMClean, arXiv 2404.18681. https://arxiv.org/abs/2404.18681 [abs]
156. Exploring LLM agents for cleaning tabular ML datasets, arXiv 2503.06664. https://arxiv.org/abs/2503.06664 [abs]
157. TableEG authentic error generation, arXiv 2507.10934. https://arxiv.org/abs/2507.10934 [abs]
158. Raha and Baran overview. https://rmarcus.info/dbscholar/papers/h67f819e1db6e3046 [sec]

### Operations, monitoring, economics, behavior
159. Steiner et al., risk-adjusted CUSUM, Biostatistics 2000; Steiner & Woodall debate, BMC Surgery 2016. https://pmc.ncbi.nlm.nih.gov/articles/PMC4820954
160. Statistical process monitoring of inpatient care (Bavaria), BMC HSR 2020. https://pmc.ncbi.nlm.nih.gov/articles/PMC6947979
161. JMIR 2025 multisite discharge forecasting. https://www.jmir.org/2025/1/e63765
162. TEND expected next-day discharges, J Hosp Med 2018. https://cdn.mdedge.com/files/s3fs-public/Document/February-2018/jhm013030158.pdf [abs]
163. NIHR PUNDIT hospital digital twin. https://arc-wx.nihr.ac.uk/research-areas-list/pundit-%E2%80%93-predicting-hospital-usage-numbers-via-a-digital-twin [abs]
164. Rural no-show prediction and overbooking, BMC HSR 2023. https://pmc.ncbi.nlm.nih.gov/articles/PMC10503036/
165. Zink & Rose, Fair regression for health care spending, Biometrics 2020 (policy brief). https://hai.stanford.edu/sites/default/files/2022-11/HAI_Balancing%20Fairness%20and%20Efficiency%20in%20Health%20Plan%20Payments%20_v2.pdf [abs]
166. Identifying Undercompensated Groups in Risk Adjustment, arXiv 2105.08493. https://arxiv.org/abs/2105.08493 [abs]
167. MedPAC November 2021 transcript (ACO benchmarks). https://www.medpac.gov/wp-content/uploads/2021/11/november21_medpac_transcript_sec.pdf [abs]
168. Fiedler, Brookings comments on MSSP provisions. https://www.brookings.edu/wp-content/uploads/2022/09/FiedlerCommentsOnMSSPProvisions.pdf [abs]
169. Milliman, MSSP rebasing considerations for early renewal. https://ae.milliman.com/en/insight/mssp-rebasing-considerations-early-renewal [sec]
170. Sacarny et al., Peer comparison letters for quetiapine prescribers, JAMA Psychiatry 2018. https://oes.gsa.gov/projects/quetiapine-prescribing
171. Provider profiling: classification accuracy (PMC6400472); total variation (arXiv 1907.07809); hierarchical benchmarks (PMC4231585). https://pmc.ncbi.nlm.nih.gov/articles/PMC6400472 [abs]

### Outside the box
172. Rigger & Su, Finding Bugs in Database Systems via Query Partitioning (TLP), OOPSLA 2020; NoREC, ESEC/FSE 2020. https://2020.splashcon.org/details/splash-2020-oopsla/87/Finding-Bugs-in-Database-Systems-via-Query-Partitioning
173. Green, Karvounarakis, Tannen, Provenance Semirings, PODS 2007. https://www.cs.ucdavis.edu/~green/papers/pods07.pdf [abs]
174. OCEL 2.0 specification, arXiv 2403.01975. https://arxiv.org/abs/2403.01975 [abs]
175. Leonardi, Montani, Striani et al., LLM-supported medical process conformance checking (AIME 2025; Applied Sciences 2025). https://research.uniupo.it/en/publications/checking-medical-process-conformance-by-exploiting-llms/ [abs]
176. LLM-orchestrated conformance checking in stroke care, arXiv 2606.09489. https://tldr.takara.ai/p/2606.09489 [sec]
