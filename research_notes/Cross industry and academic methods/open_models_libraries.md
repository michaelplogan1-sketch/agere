# Open models and libraries for Agere: clinical NLP, text-to-SQL, coding, forecasting, tabular

Research date: 2026-11-17. Only web pages were opened; no weights or datasets were downloaded. Licence flags: **[COMMERCIAL OK]**, **[RESTRICTED]** (attribution, revenue threshold or custom terms), **[NON-COMMERCIAL]**. Recommendations use Agere's admission rule: an item must fix a named buyer problem and beat a simple baseline and an LLM-alone approach on accuracy and cost. Benchmark numbers are as published by the model authors or the benchmark maintainers. None of them was reproduced on Agere data.

## Which tabular foundation models beat tuned GBDTs on TabArena or similar, at what row/feature limits, and with what licences?

### Takeaway
Tabular foundation models (TFMs) now top TabArena over tuned and ensembled GBDTs on small and medium tables. The strongest weights carry non-commercial licences, though: TabPFN-2.5, 2.6, 3 and 3.5, LimiX-2 and Moirai-2.0. Options with commercial-OK weights are TabPFN v2 (Apache-2.0 plus attribution), TabICL/TabICLv2 (stated permissive, exact licence unverified), Mitra (Apache-2.0) and the small LimiX-2M/16M models (custom licence). Each should be trialled head-to-head against tuned CatBoost/LightGBM on Agere's own tables, with GBDTs kept as the default.

### Cited Findings
- **TabArena v0.1 (NeurIPS 2025 paper, arXiv v4 dated 03 Nov 2025):** 51 datasets, with default, tuned and tuned+ensembled regimes and Elo scoring. CatBoost ranked first under conventional tuning. After post-hoc ensembling the top three were TabM, LightGBM and RealMLP. In v0.1, TabPFNv2 was evaluated only on datasets with ≤10,000 training samples, ≤500 features and ≤10 classes (33 datasets). TabICL was evaluated only on classification with ≤100,000 samples and ≤500 features (36 datasets). On its compatible subset, TabPFNv2 outperformed the other foundation models "by a large margin". — [TabArena arXiv 2506.16791v4](https://arxiv.org/html/2506.16791v4)
- **Live leaderboard numbers:** the TabArena HF Space did not render when fetched. A third-party snapshot labelled v0.1.4 shows TFMs in the top four single-model slots (TabPFN-3, TabPFN-2.6, RealTabPFN-2.5, TabICLv2), with RealTabPFN-2.5 tuned+ensembled at about 1599 Elo and TabICLv2 default at 1575, against LightGBM tuned+ensembled at 1433. It puts the best GBDT about 240 Elo below the top. This is unverified secondary data. — [Codesota tabular snapshot](https://www.codesota.com/tasks/tabular-ml); [TabArena Space (did not render)](https://huggingface.co/spaces/TabArena/leaderboard)
- **TabPFN-2.5** (Prior Labs, paper 11 Nov 2025, revised 5 Feb 2026):
  - Claims 100% win rate vs default XGBoost on datasets ≤10K rows and ≤500 features, and 87% (classification) or 85% (regression) on datasets up to 100K rows and 2K features.
  - Claims to match AutoGluon 1.4, a 4-hour tuned ensemble.
  - Claims to be "leading method on TabArena".
  - Includes a distillation engine to an MLP or tree ensemble.
  - The paper gives no win rates against tuned GBDTs. — [arXiv 2511.08667](https://arxiv.org/abs/2511.08667)
- **TabPFN-2.5 licence and limits:** "tabpfn-2.5-license-v1.1" allows testing, evaluation and internal benchmarking only. It does not permit commercial or production use of the model, its derivatives or its outputs; that requires a Commercial Enterprise License. Limits are 50,000 samples and 2,000 features. Architecture is 18–24 layers. **[NON-COMMERCIAL]** — [HF card Prior-Labs/tabpfn_2_5](https://huggingface.co/Prior-Labs/tabpfn_2_5)
- **TabPFN repo:** code is Apache-2.0.
  - TabPFN v2 weights use the "Prior Labs License", which is Apache-2.0 plus an attribution requirement. **[COMMERCIAL OK, attribution]**
  - TabPFN-2.5, 2.6, 3, 3.5 and 3.5-Fast weights are under non-commercial licences, with an enterprise licence needed for production. **[NON-COMMERCIAL]**
  - TabPFN-3.5 supports up to 1,000,000 rows and 20,000 features. On CPU the limit is 5,000 samples for v3+ and 1,000 for older versions. — [GitHub PriorLabs/TabPFN](https://github.com/PriorLabs/TabPFN)
- **TabICL / TabICLv2** (INRIA SODA):
  - v1 was ICML 2025, with the checkpoint dated 2025-02-08. v2 is ICML 2026 (arXiv 2602.11139), with the checkpoint dated 2026-02-12.
  - v2 adds regression.
  - Benchmarks span 300–100K training rows and up to 2,000 features. Pretraining used 2–100 columns, and accuracy "can degrade beyond 100 features". CPU/disk offload handles about 500K rows.
  - The authors claim that, untuned, it beats heavily tuned XGBoost/CatBoost/LightGBM on about 80% of TabArena datasets.
  - They claim it is 10x faster than TabPFN-2.5 (50K×100 in under 10 seconds on an H100).
  - The README says only "permissive license" (the exact licence was not read). — [GitHub soda-inria/tabicl](https://github.com/soda-inria/tabicl)
- **LimiX** (Stable AI Technology):
  - Code is under the "Stable AI Technology Co., Ltd. License v1.0", an Apache-2.0 derivative.
  - LimiX-2 (400M, 16 Sep 2026) is under the "StableAI LimiX Non-Commercial License". **[NON-COMMERCIAL]**
  - LimiX-2M (10 Nov 2025) and LimiX-16M use the custom Stable AI licence. **[RESTRICTED, custom; terms not read]**
  - Self-reported Elo: TabArena LimiX-2 1935 vs TabPFN-3 1632. TALENT 1506 vs TabPFN-3 1363 vs CatBoost 1094. BCCO 1432 vs 1295 vs 1140.
  - No row or feature limits are stated. — [GitHub limix-ldm/LimiX](https://github.com/limix-ldm/LimiX)
- **Mitra** (AutoGluon team, paper 24 Oct 2025): Apache-2.0, about 72–76M parameters, 12-layer transformer pretrained on synthetic priors mixed from random classifiers. Available through `autogluon.tabular[mitra]`. The card has no benchmark numbers or limits. **[COMMERCIAL OK]** — [HF autogluon/mitra-classifier](https://huggingface.co/autogluon/mitra-classifier)
- **TabM** (Yandex Research, ICLR 2025): Apache-2.0. It is an MLP ensemble, not a TFM. It placed in top Kaggle solutions and ranked #1 after ensembling in TabArena v0.1. It is a strong non-GBDT baseline. **[COMMERCIAL OK]** — [GitHub yandex-research/tabm](https://github.com/yandex-research/tabm); [TabArena paper](https://arxiv.org/html/2506.16791v4)
- **CARTE:** not researched in this pass (see Gaps).

### Inferences
- **Trial: TabICLv2**, once its LICENSE is confirmed. It is the best-documented TFM with commercially usable weights, handles up to about 100K rows, and is fast. Agere's typical risk tables are cohort-level feature sets with thousands to tens of thousands of rows (readmission risk, rising-risk member stratification, HCC gap propensity). These fit inside its comfortable range.
- **Trial: TabPFN v2 and Mitra** as cheaper ensemble members inside AutoGluon next to CatBoost. Measure lift over tuned CatBoost with paired bootstrap on AUROC and calibration (Brier, ECE).
- **Park: TabPFN-2.5, 2.6, 3 and 3.5, and LimiX-2.** They are non-commercial unless Agere buys an enterprise licence. Benchmarking them internally is allowed for TabPFN-2.5 ("internal benchmarking"), but product use is not. Note also that the TabPFN-2.5 licence bars commercial use of *outputs*, so even distilled models derived from it would be restricted.
- Default stays tuned CatBoost/LightGBM. The TabArena v0.1 paper's own conclusion is that well-tuned and ensembled GBDTs and MLPs remain near the top. TFM advantages concentrate in small-data regimes, where clinical cohorts often sit.

### Gaps
- The official live TabArena Elo table was not readable (JavaScript Space). The v0.1.4 numbers come from a secondary aggregator, and LimiX numbers are self-reported.
- TabICL's exact licence string was not read (the README says permissive; prior knowledge suggests BSD-3-Clause, unverified).
- CARTE (INRIA, string-entry tabular model) was not opened. Licence and results are unknown here.
- The LimiX-2M/16M custom licence commercial terms were not read.

## Which time-series foundation models lead GIFT-Eval / fev-bench as of 2025-2026 and by how much over seasonal naive / AutoETS?

### Takeaway
On fev-bench (100 tasks), Chronos-2 leads with SQL skill 47.3% over seasonal naive and a 91.4% win rate. TiRex (42.6%) and TimesFM-2.5 (42.2%) follow and are statistically indistinguishable from each other. Toto, TabPFN-TS and Moirai-2 sit about 39–42%. GIFT-Eval top entries (MASE ≈0.65–0.70 relative to seasonal naive = 1.0) shift monthly and carry leakage caveats. Agere already has Chronos-2 and TimesFM. The licence-clean additions worth a shadow trial are TiRex (revenue-threshold licence) and Toto (Apache-2.0). Moirai-2 is non-commercial.

### Cited Findings
- **fev-bench (arXiv 2509.26468, v5 stamped 04 Nov 2026):** 100 tasks from 96 datasets across 7 domains, of which 46 tasks have covariates and 35 are multivariate. Skill is % improvement over Seasonal Naive. — [fev-bench arXiv](https://arxiv.org/html/2509.26468)

  | Model | SQL win % | SQL skill % | MASE win % | MASE skill % |
  |---|---|---|---|---|
  | Chronos-2 | 91.4 | 47.3 | 87.2 | 35.5 |
  | TiRex | 83.3 | 42.6 | 77.0 | 30.0 |
  | TimesFM-2.5 | 79.6 | 42.2 | 76.1 | 30.2 |
  | Toto-1.0 | 72.4 | 40.8 | 66.6 | 28.2 |
  | TabPFN-TS | 69.7 | 41.5 | 63.9 | 29.8 |
  | Moirai-2.0 | 67.4 | 39.3 | 62.4 | 27.2 |
  | Chronos-Bolt | 67.0 | 38.9 | 61.3 | 26.5 |
  | Seasonal Naive | 19.6 | 0.0 | 20.0 | 0.0 |

  AutoETS, AutoARIMA and AutoTheta were evaluated, but their rows were not in the portion of the paper read.
- **Covariates on fev-bench:** on 42 tasks with dynamic covariates, covariates raise SQL skill for Chronos-2 from 40.9% to 47.0% and for TabPFN-TS from 34.1% to 42.5%. Most other pretrained models ignore covariates, and multivariate gains are small (Chronos-2 54.4% to 55.0%). — [fev-bench arXiv](https://arxiv.org/html/2509.26468)
- **Newer fev entries:** a search summary says a later leaderboard view lists TiRex-2 and Toto-2.0 above TimesFM-2.5. It also mentions a claim that TimesFM-3 ranks ahead of Chronos-2, which is unverified. — [fev-bench Space CSV (search result)](https://huggingface.co/spaces/autogluon/fev-bench/blob/0d4449c7b5c5e721513a5d74d0104a72c1c9a8fe/tables/leaderboard_MASE.csv)
- **GIFT-Eval:** the Salesforce HF Space did not render. Secondary sources report the following, and they conflict with each other. Seasonal naive is the normalising anchor (= 1.0). Treat all of these as unverified.
  - Top entries cluster around MASE 0.646–0.698 and CRPS 0.446–0.467.
  - TiRex-2, Toto-2.0 and Chronos-2 sit around MASE 0.696–0.698.
  - A "TimesFM 3.0" entry at MASE 0.667, CRPS 0.456 is reported as non-commercial.

  — [presenc.ai summary](https://presenc.ai/research/best-open-weight-time-series-models-2026); [TDS comparison](https://towardsdatascience.com/timestral-t0-tabby-and-morpheus-20-compared-on-gift-eval/); [GIFT-Eval Space](https://huggingface.co/spaces/Salesforce/GIFT-Eval)
- **Leakage caveat:** GIFT-Eval publishes a pretraining corpus. Moirai-2.0 was pretrained on subsets of GIFT-Eval Pretrain and Train, and TimesFM-2.5 lists GiftEvalPretrain as pretraining data. A secondary source warns of partial test overlap for some foundation models. — [Moirai-2.0 card](https://huggingface.co/Salesforce/moirai-2.0-R-small); [TimesFM-2.5 card](https://huggingface.co/google/timesfm-2.5-200m-pytorch)
- **Chronos-2** (Amazon, paper 17 Oct 2025):
  - Apache-2.0, 120M encoder.
  - Past and known-future covariates (real and categorical), plus multivariate.
  - Over 300 forecasts per second on an A10G, and CPU-capable. **[COMMERCIAL OK]** — [HF amazon/chronos-2](https://huggingface.co/amazon/chronos-2)
- **TimesFM-2.5** (Google): Apache-2.0, 200M parameters, structure update on 2 Oct 2025. The card has no benchmark numbers. **[COMMERCIAL OK]** — [HF google/timesfm-2.5-200m-pytorch](https://huggingface.co/google/timesfm-2.5-200m-pytorch)
- **TiRex** (NX-AI):
  - 35M parameters, xLSTM-based (paper 29 May 2025).
  - TiRex-2 adds past and future covariates.
  - CUDA kernels need compute capability 8.0 or higher. An ONNX export exists. — [HF NX-AI/TiRex](https://huggingface.co/NX-AI/TiRex)
- **TiRex licence ("NXAI Community License"):**
  - Royalty-free commercial use is allowed. Organisations with annual revenue above €100M (group-wide) that put it in a commercial product need a separate NXAI commercial licence, granted at NXAI's discretion.
  - Distributors must display "Built with technology from NXAI".
  - There is an IP-suit termination clause, and Austrian law governs. **[RESTRICTED: revenue threshold]** — [TiRex LICENSE](https://github.com/NX-AI/tirex/blob/main/LICENSE)
- **Toto-Open-Base-1.0** (Datadog, paper 20 May 2025): Apache-2.0, 151M parameters. GIFT-Eval CRPS 0.517 and MASE 0.750; BOOM (observability) CRPS 0.375 and MASE 0.617. **[COMMERCIAL OK]** — [HF Datadog/Toto-Open-Base-1.0](https://huggingface.co/Datadog/Toto-Open-Base-1.0)
- **Moirai-2.0** (Salesforce, paper 12 Nov 2025): CC-BY-NC-4.0, "research purposes only", 11.4M parameters (small). **[NON-COMMERCIAL]** — [HF Salesforce/moirai-2.0-R-small](https://huggingface.co/Salesforce/moirai-2.0-R-small)
- **Sundial** (Tsinghua THUML, paper 2 Feb 2025): Apache-2.0, 128M parameters, pretrained on about 1 trillion time points. It claimed "1st MASE" on GIFT-Eval in May 2025. **[COMMERCIAL OK]** — [HF thuml/sundial-base-128m](https://huggingface.co/thuml/sundial-base-128m)
- **TabPFN-TS** (Prior Labs, v1.3.0 on 15 Sep 2026):
  - Code is Apache-2.0, but by default it runs TabPFN-3.5 weights, which are non-commercial.
  - Uses known dynamic covariates; past and static covariates are dropped.
  - Claimed #1 on GIFT-Eval on 27 Jan 2025. **[NON-COMMERCIAL in practice via weights]** — [GitHub PriorLabs/tabpfn-time-series](https://github.com/PriorLabs/tabpfn-time-series); [TabPFN licences](https://github.com/PriorLabs/TabPFN)
- **Lag-Llama, and TimeGPT-style alternatives:** not opened in this pass (see Gaps).

### Inferences
- Skill scores near 40–47% over seasonal naive are averages over generic domains such as energy, retail and web. Healthcare series behave differently: monthly admissions per service line, ED arrivals, PMPM cost, and utilisation per 1,000 members. These are short, have regime changes (COVID, contract changes) and carry calendar covariates, so the margins may shrink. Agere's rule (beat seasonal-naive/ETS) should be tested with rolling-origin backtests per series.
- Chronos-2's covariate advantage, shown on the subset with covariates, is the most relevant feature for planning questions. Examples are staffing driven by known holidays and attributed-member counts as known-future covariates. That supports keeping Chronos-2 as primary.
- **Shadow trial: TiRex or TiRex-2.** It is small at 35M parameters. Commercial use is fine below the €100M revenue threshold, but Agere's licensee revenue and its hospital customers matter; a large health system embedding it might trigger the threshold. That needs legal review.
- **Shadow trial: Toto, if cost matters.** It is Apache-2.0, but it was tuned for observability data.
- **Skip for product: Moirai-2 and TabPFN-TS with default weights**, because of non-commercial weights.
- **Optional: Sundial.** It is Apache-2.0 but not ahead of Chronos-2 on fev-bench (it was not in the read portion of the table).

### Gaps
- Live GIFT-Eval values and leakage flags could not be read from the official Space. Secondary GIFT-Eval numbers conflict.
- The AutoETS, AutoARIMA and AutoTheta rows on fev-bench were not captured, so foundation-model margins over AutoETS specifically are unconfirmed here. Margins are reported only over seasonal naive.
- Not verified: Lag-Llama (licence and results), TimeGPT-style open alternatives (e.g., Timer, Time-MoE, MOMENT), TimesFM-3's licence and release, and Toto-2.0 / TiRex-2 numbers.

## State of open automated ICD coding (PLM-ICD, LLM-based coding): measured accuracy on MIMIC-IV, and evidence LLMs are poor coders

### Takeaway
The best reproducible open coder, PLM-ICD, reaches micro-F1 58.5 but only macro-F1 21.1 on MIMIC-IV ICD-10, and it never correctly predicts about 52% of codes. LLMs used alone reproduce codes poorly: GPT-4 exact match was 33.9% for ICD-10-CM in NEJM AI 2024. Retrieval or agentic tool use with code-set lookup closes much of that gap in newer studies. For HCC work, deterministic open mappers (hccpy, hccinfhir) are the baseline any model must beat.

### Cited Findings
- **Edin et al. (SIGIR 2023) replicability study:** results are the mean of 10 seeds, in %. — [arXiv 2304.10909](https://arxiv.org/html/2304.10909)

  | Model | MIMIC-IV ICD-10 micro-F1 / macro-F1 / MAP | MIMIC-IV ICD-9 micro / macro / MAP | MIMIC-III clean micro / macro |
  |---|---|---|---|
  | PLM-ICD | 58.5 / 21.1 / 61.9 | 62.6 / 29.8 / 68.0 | 59.6 / 26.6 |
  | LAAT | 57.9 / 20.3 / 60.6 | 61.7 / 26.4 / 66.3 | 57.8 / 22.6 |
  | MultiResCNN | 56.9 / 21.1 / 59.3 | 60.4 / 27.7 / 64.7 | 56.4 / 22.9 |
  | CAML | 55.4 / 16.0 / 57.4 | 58.6 / 19.3 / 62.4 | 55.4 / 20.4 |
  | CNN (baseline) | 47.2 / 8.0 / 48.2 | 52.4 / 12.6 / 55.2 | 48.0 / 9.9 |

- **Rare codes in the same study:**
  - PLM-ICD never correctly predicted 51.8% of ICD-10 codes and 39.3% of ICD-9 codes in MIMIC-IV.
  - It scored 0% F1 on Z72.0 (tobacco use) despite 1,914 training examples.
  - Code frequency correlates with F1 (r = 0.47–0.61).
  - Tuning the decision boundary matters: a fixed 0.5 threshold dropped PLM-ICD macro-F1 from 26.6 to 23.0. — [arXiv 2304.10909](https://arxiv.org/html/2304.10909)
- **Repo licence:** code is Apache-2.0, but trained checkpoints "can't be used commercially due to the MIMIC License". **[Code COMMERCIAL OK; MIMIC-trained weights NON-COMMERCIAL / PhysioNet credentialed]** — [GitHub JoakimEdin/medical-coding-reproducibility](https://github.com/JoakimEdin/medical-coding-reproducibility)
- **Soroush et al., NEJM AI (April 2024), "Large Language Models Are Poor Medical Coders":**
  - Tested GPT-3.5, GPT-4, Gemini Pro and Llama2-70b on more than 27,000 unique codes from one year of Mount Sinai data.
  - Best exact match was GPT-4: ICD-9-CM 45.9%, ICD-10-CM 33.9%, CPT 49.8%.
  - Among misses, GPT-4 produced equivalent codes 7.0% (ICD-9) and 10.9% (ICD-10) of the time.
  - Human reviewer agreement was moderate (κ 0.46–0.60).
  - DOI 10.1056/AIdbp2300040. — [ScienceDaily summary](https://www.sciencedaily.com/releases/2024/04/240422120603.htm); [journal-club PDF](https://www.rama.mahidol.ac.th/ceb/sites/default/files/public/pdf/journal_club/2025/LLMs%20Are%20Poor%20Medical%20Coders.pdf)
- **Tool-augmented rebuttal (arXiv 2407.12849, July 2024):** a Retrieve-Rank method reached 100% vs 6% for vanilla GPT-3.5 on 100 *single-term* conditions. The author notes the inputs were simplified, so this is not note-level coding. — [arXiv 2407.12849](https://arxiv.org/abs/2407.12849)
- **Boyle et al. 2023 (off-the-shelf LLM tree search, CodiEsp):** PLM-ICD micro-F1 0.219 vs LLM tree-search 0.157. The LLM method did better on rare codes, with macro-F1 0.225 vs 0.216. — [arXiv 2310.06552](https://arxiv.org/pdf/2310.06552)
- **EMNLP 2026 rarity-stratified MIMIC-IV benchmark** (snippet only; the PDF could not be parsed): PLM-ICD scored 0.227 on rare codes vs 0.656 on common codes. An agentic LLM system ("CodeSeeker" with a frontier model) reached 0.560 on rare codes. **Unverified beyond the search snippet.** — [ACL Anthology 2026.emnlp-main.1583](https://aclanthology.org/2026.emnlp-main.1583.pdf)
- **HCC mappers:**
  - hccpy (Apache-2.0) supports CMS-HCC V22/V23/V24/V28 and HHS-HCC V05/V07. **[COMMERCIAL OK]**
  - hccinfhir (mimilabs) maps FHIR EOBs, service-level data or diagnosis lists to V28 HCCs. Licence not checked.
  - Tuva Project has a CMS-HCC data mart. — [PyPI hccpy](https://pypi.org/project/hccpy); [PyPI hccinfhir](https://pypi.org/project/hccinfhir/0.1.1/); [Tuva CMS-HCC](https://thetuvaproject.com/data-marts/cms-hccs)

### Inferences
- **Park: full-note autonomous ICD-10 assignment.** The open state of the art (micro-F1 about 58, macro about 21) is too weak for that, and MIMIC-trained weights cannot ship commercially.
- The buyer problems that fit current maturity are narrower: "Which HCCs are documented in notes but not on claims?" (risk-adjustment gap or recapture) and "Which coded HCCs lack MEAT support?" (audit defence). Both suit a retrieve, verify and rule design. The rules baseline maps candidate ICD-10 to V28 HCC with hccpy. A model, an encoder or an LLM with code-set lookup, only proposes evidence spans and candidates for review.
- The rules-plus-deterministic-mapper baseline is the Agere "rules" baseline. Any LLM component must show it beats the baseline on precision of suggested gaps, at measured cost per chart.
- **Trial: retrieval-augmented candidate suggestion over the ICD-10-CM tabular index**, evaluated on synthetic or credentialed data only. **Skip: plain LLM code generation**, given the NEJM AI evidence.

### Gaps
- The EMNLP 2026 paper's full numbers and its open-model results could not be read.
- No direct, verified MIMIC-IV comparison of open LLMs (e.g., Llama or Qwen) zero-shot vs PLM-ICD was found.
- No open HCC-from-notes benchmark with published accuracy was found.
- The hccinfhir licence and the accuracy of both HCC libraries against current CMS files were not verified.

## Open text-to-SQL models: numbers on BIRD/Spider/EHRSQL, and licences

### Takeaway
Open 7–32B SQL models (OmniSQL, Arctic-Text2SQL-R1, XiYanSQL-QwenCoder) reach about 64–74% BIRD execution accuracy and about 88% Spider test, matching or beating GPT-4o on Spider and BIRD. Two caveats matter: they trail on Spider2.0 (enterprise-scale schemas, 10–16%), and on EHRSQL OmniSQL-7B (34.9) trails GPT-4o (44.9). The best agentic closed systems sit at about 83% on BIRD test, against 93% for humans.

### Cited Findings
- **OmniSQL (Renmin Univ. / ByteDance, 4 Mar 2025):** 7B, 14B and 32B sizes. Model card licence Apache-2.0, and the SynSQL-2.5M training set is Apache-2.0. The paper itself is CC-BY-NC-ND (text only). **[COMMERCIAL OK]** — [HF seeklhy/OmniSQL-7B](https://huggingface.co/seeklhy/OmniSQL-7B); [GitHub RUCKBReasoning/OmniSQL](https://github.com/RUCKBReasoning/OmniSQL)
- **OmniSQL greedy results:** columns are Spider dev / Spider test / BIRD dev / Spider2.0-SQLite / ScienceBenchmark / EHRSQL. — [arXiv 2503.02240](https://arxiv.org/html/2503.02240)
  - OmniSQL-7B: 81.2 / 87.9 / 63.9 / 10.4 / 50.2 / 34.9
  - OmniSQL-14B: 81.4 / 88.3 / 64.2 / 10.4 / 56.9 / 39.9
  - OmniSQL-32B: 80.9 / 87.6 / 64.5 / 11.9 / 57.2 / 42.4
  - GPT-4o: 70.9 / 83.2 / 61.9 / 14.8 / 55.5 / 44.9
  - DeepSeek-V3: 73.1 / 85.5 / 63.2 / 12.6 / 56.2 / 43.2
  - Qwen2.5-Coder-32B-Instruct: 77.7 / 87.5 / 64.5 / 5.9 / 54.8 / 36.4
- **Arctic-Text2SQL-R1-7B** (Snowflake, paper 22 May 2025):
  - Apache-2.0, built on Qwen2.5-Coder-7B-Instruct.
  - BIRD-dev 68.9, BIRD-test 68.5, Spider-test 88.8, Spider2.0-DK 15.6, EHRSQL 36.7. **[COMMERCIAL OK]**
  - Conflict: the BIRD leaderboard lists R1-7B at 70.43 test, possibly a different track. — [HF Snowflake/Arctic-Text2SQL-R1-7B](https://huggingface.co/Snowflake/Arctic-Text2SQL-R1-7B); [BIRD leaderboard](https://bird-bench.github.io/)
- **BIRD leaderboard (fetched 2026-11-17):**
  - Top overall entries (closed or agentic systems) score 81.7–83.4 test EX. Human performance is 92.96.
  - Likely open-weight entries: DeepEye-SQL (27B) 78.42; XiYan-SQL 75.63 (some versions closed, so the open status is inferred); Arctic-Text2SQL-R1-32B 73.84; CSC-SQL + XiYanSQL-QwenCoder-32B 73.67; Arctic-R1-14B 72.22; OmniSQL-32B 72.05; XiYanSQL-QwenCoder-32B 69.03. — [BIRD leaderboard](https://bird-bench.github.io/)
- **Curated list:** Awesome-Text2SQL (eosphoros-ai, MIT, about 3.7k stars). The same org's DB-GPT-Hub covers fine-tuning. — [awesome.ecosyste.ms entry](https://awesome.ecosyste.ms/projects/github.com%2Feosphoros-ai%2FAwesome-Text2SQL); [DB-GPT-Hub README](https://raw.githubusercontent.com/eosphoros-ai/DB-GPT-Hub/main/README.md)

### Inferences
- EHRSQL is the closest public analogue to Agere's governed warehouse questions, such as "readmissions by payer for CHF in Q3". Open 7B models score about 35–37% there, with a gap to frontier LLMs on clinical schemas. Raw BIRD wins do not carry over.
- Agere's existing customer-definition governance (definition-bound SQL, the rung ladder) is likely the larger driver of accuracy. A small open SQL model is worth a trial only as a cheap first-rung writer under a definition and schema contract. It should be benchmarked on Agere's own question set against (a) a templated or semantic-layer baseline and (b) the current frontier LLM writer, scoring execution-match and cost per question.
- **Trial: Arctic-Text2SQL-R1-7B or OmniSQL-7B**, both Apache-2.0, small enough to run locally. **Skip: models fine-tuned on MIMIC-based EHRSQL data for product use**, because of PhysioNet terms.

### Gaps
- XiYanSQL-QwenCoder licence and DeepEye-SQL weight availability were not verified.
- No SQLCoder-class (Defog) model card was opened.
- No Spider2.0 numbers were found for the newest open models.

## Clinical de-identification, NER and encoders (Philter, medspaCy, Presidio, scispaCy, GLiNER-biomed, clinical encoders, medical LLMs)

### Takeaway
For de-identification, a transformer de-id model plus a rule layer is the realistic open stack. The model is StanfordAIMI stanford-deidentifier-base (MIT, F1 98.9 on i2b2 2014). The rule layer is Philter (BSD-3-Clause per an aggregator, recall 99.92% on i2b2 2014 under its own Safe-Harbor preprocessing) or Presidio (MIT, no published accuracy, explicitly "no guarantee"). For extraction, medspaCy (MIT) covers negation and sections with rules. GLiNER-biomed (Apache-2.0) gives zero-shot biomedical NER at F1 about 60. BioClinical ModernBERT (MIT, already in Agere shadow) is the leading open clinical encoder. MedGemma uses Google's custom HAI-DEF terms.

### Cited Findings
- **Philter (UCSF BCHSI):**
  - i2b2 2014 test recall 99.92% and F2 94.77%; UCSF corpus recall 99.46%. Baselines: Physionet deid 69.84%, Scrubber 87.80%.
  - Caveat: gold annotations were preprocessed to Safe-Harbor-only entities, which complicates comparison.
  - Licence is listed as BSD-3-Clause by an aggregator (unverified at source). The philter-lite fork breaks the evaluation code. — [npj Digit Med 2020 (PMC7156708)](https://pmc.ncbi.nlm.nih.gov/articles/PMC7156708); [reproducibility note arXiv 2311.02748](https://arxiv.org/pdf/2311.02748); [ecosyste.ms licence listing](https://science.ecosyste.ms/projects/62474); [philter-lite](https://github.com/SironaMedical/philter-lite)
- **stanford-deidentifier-base (Stanford AIMI, JAMIA Nov 2022):**
  - MIT, PubMedBERT-based, trained on about 6,193 radiology and medical documents.
  - F1 97.9 at a known institution, 99.6 at a new institution, 99.5 on i2b2 2006 and 98.9 on i2b2 2014. **[COMMERCIAL OK]** — [HF StanfordAIMI/stanford-deidentifier-base](https://huggingface.co/StanfordAIMI/stanford-deidentifier-base)
- **Presidio (Microsoft):** MIT. It detects PII with NER, regex, rules and checksums, and includes DICOM image redaction. No accuracy figures are published, and the README says "there is no guarantee that Presidio will find all sensitive information". The project has moved to a new home. **[COMMERCIAL OK]** — [GitHub microsoft/presidio](https://github.com/microsoft/presidio)
- **medspaCy:** MIT. Provides ConText (negation, uncertainty, historical and family attributes), a sectionizer and a target matcher. Latest release 1.3.1 (21 Nov 2024), still "beta", with no benchmarks. **[COMMERCIAL OK]** — [GitHub medspacy/medspacy](https://github.com/medspacy/medspacy)
- **GLiNER-biomed-large-v1.0 (paper 1 Apr 2025):** Apache-2.0, built on DeBERTa-v3-large. Zero-shot NER F1 across 8 biomedical datasets is 59.77 (macro 40.67), vs GLiNER v2.5 53.81 and NuNER Zero 40.87. **[COMMERCIAL OK]** — [HF Ihor/gliner-biomed-large-v1.0](https://huggingface.co/Ihor/gliner-biomed-large-v1.0)
- **BioClinical ModernBERT (paper 12 Jun 2025):**
  - MIT. Base is about 150M parameters and large about 396M, with 8,192 context. Trained on 50.7B biomedical and 2.8B clinical tokens, including MIMIC-III and MIMIC-IV notes.
  - Base results: ChemProt 89.9, Phenotype 58.1, COS 95.1, Social History 58.5, DEID 82.7. Comparisons: ModernBERT 48.4 Phenotype and 78.3 DEID; Clinical BERT 25.8 Phenotype and 74.2 DEID.
  - Note: the weights are MIT-licensed despite MIMIC in pretraining. Data-use-agreement implications are a legal question. — [HF thomas-sounack/BioClinical-ModernBERT-base](https://huggingface.co/thomas-sounack/BioClinical-ModernBERT-base)
- **MedGemma 27B text (Google, 20 May 2025; tech report 7 Jul 2025):**
  - Governed by the "Health AI Developer Foundations terms of use", a custom licence whose commercial terms are not stated on the card. **[RESTRICTED, custom]**
  - MedQA 87.7 (0-shot) vs Gemma 3 27B 74.9; MedXpertQA 26.7 vs 15.7.
  - 128K context.
  - No EHR-task numbers. — [HF google/medgemma-27b-text-it](https://huggingface.co/google/medgemma-27b-text-it)
- **scispaCy:** not opened in this pass (see Gaps).

### Inferences
- De-identification is a deployment prerequisite, not a buyer feature. **Trial: a layered pipeline.** The Stanford de-id model for recall, Philter or Presidio regex for structured identifiers (MRN, phone, dates), and a residual-PHI audit sample.
- The baseline is a rules-only system (Philter). The model must show a recall gain at acceptable precision on synthetic notes.
- **Trial: GLiNER-biomed** as a zero-shot candidate extractor for note-reason spans, against the existing BioClinical ModernBERT heads and medspaCy rules. F1 of about 60 zero-shot suggests it will not beat a fine-tuned head but may cut labelling cost.
- **Park: MedGemma** until the HAI-DEF terms are reviewed. QA benchmark gains (MedQA) are not evidence for Agere's note-reading calibration failures. The earlier small readers failed at 20/28 and 7/28, so any reader needs the same 28-item calibration check.

### Gaps
- scispaCy (Allen AI) licence and scores were not opened; prior knowledge suggests Apache-2.0, unverified.
- The Philter licence was not read at source.
- No de-id model scores on non-MIMIC US hospital notes beyond the Stanford paper were found.
- MedGemma commercial terms were not read.
- No other medical LLM cards (e.g., Meditron, OpenBioLLM) were opened.

## Curated "awesome" lists worth tracking

### Takeaway
There are three worth tracking. Awesome-Text2SQL (MIT, about 3.7k stars) is active and broad. Awesome-TimeSeries-SpatioTemporal-LM-LLM (about 1.2k stars) appears to have stopped updating around 2024–2025. eugeneyan/open-llms (about 12.9k stars) is useful for commercial-licence screening of LLMs. No authoritative, actively maintained awesome-clinical-NLP list was verified. For live state of the art, leaderboards (fev-bench, GIFT-Eval, TabArena, BIRD) are better trackers than lists.

### Cited Findings
- **Awesome-Text2SQL (eosphoros-ai):** MIT, about 3.7k stars, 115 resources across 9 categories. — [awesome.ecosyste.ms](https://awesome.ecosyste.ms/projects/github.com%2Feosphoros-ai%2FAwesome-Text2SQL)
- **Awesome-TimeSeries-SpatioTemporal-LM-LLM:** about 1.2k stars and 44 commits. The newest cited works are from 2024 plus AAAI 2025, so it is possibly stale. — [GitHub qingsongedu](https://github.com/qingsongedu/Awesome-TimeSeries-SpatioTemporal-LM-LLM)
- **open-llms (eugeneyan):** Apache-2.0, about 12.9k stars. Lists LLMs licensed for commercial use, with their licences. — [GitHub eugeneyan/open-llms](https://github.com/eugeneyan/open-llms)
- **Leaderboards to watch:**
  - [fev-bench Space](https://huggingface.co/spaces/autogluon/fev-bench)
  - [GIFT-Eval Space](https://huggingface.co/spaces/Salesforce/GIFT-Eval)
  - [TabArena Space](https://huggingface.co/spaces/TabArena/leaderboard)
  - [BIRD](https://bird-bench.github.io/)

### Inferences
- Licence status changes between model generations: TabPFN v2 is commercial-OK, while 2.5 and later are non-commercial; LimiX-2M is custom, while LimiX-2 is non-commercial. Tracking should therefore record a licence per checkpoint, not per family.

### Gaps
- No specific awesome-clinical-NLP or awesome-time-series-foundation-models repository was opened or verified, and no awesome-medical-coding list was found.

## Summary recommendation table (for the report writer)

| Item | Licence | Buyer question | Rec |
|---|---|---|---|
| TabICLv2 | permissive (exact TBC) | Risk stratification and propensity on ≤100K-row cohorts | Trial vs tuned CatBoost |
| TabPFN v2 | Prior Labs (Apache-2.0 + attribution) | Same, small cohorts ≤10K | Trial as ensemble member |
| Mitra | Apache-2.0 | Same, classification | Trial within AutoGluon |
| TabPFN-2.5/2.6/3/3.5, LimiX-2 | Non-commercial | — | Park (licence) |
| TabM | Apache-2.0 | Strong deep-learning baseline | Use as baseline |
| Chronos-2 | Apache-2.0 | Volume, census, PMPM forecasts with covariates | Keep (in product) |
| TimesFM-2.5 | Apache-2.0 | Same | Keep (in product) |
| TiRex / TiRex-2 | NXAI Community (€100M revenue threshold) | Same, small model | Shadow trial pending legal review |
| Toto-1.0 | Apache-2.0 | Same | Optional shadow |
| Sundial | Apache-2.0 | Same | Park (no edge over Chronos-2 shown) |
| Moirai-2.0 | CC-BY-NC-4.0 | — | Skip (licence) |
| TabPFN-TS | Apache-2.0 code / non-commercial weights | — | Skip unless licensed |
| PLM-ICD (code) | Apache-2.0; MIMIC weights non-commercial | Coding suggestions | Park (accuracy, weights) |
| hccpy | Apache-2.0 | HCC gap and risk-score calculation | Trial as the rules baseline |
| Plain LLM ICD coding | — | — | Skip (NEJM AI evidence) |
| Arctic-Text2SQL-R1-7B / OmniSQL-7B | Apache-2.0 | Governed NL to SQL first rung | Trial vs semantic-layer and frontier LLM |
| stanford-deidentifier-base | MIT | De-id prerequisite | Trial in layered pipeline |
| Philter | BSD-3 (TBC) | De-id rule layer | Trial |
| Presidio | MIT | Structured PII rule layer | Trial (no published accuracy) |
| medspaCy | MIT | Negation and section rules for note reasons | Use as rules baseline |
| GLiNER-biomed | Apache-2.0 | Zero-shot span extraction | Trial vs existing heads |
| BioClinical ModernBERT | MIT | Note encoders | Keep (shadow); check MIMIC data-use terms |
| MedGemma 27B | HAI-DEF custom terms | Note reader | Park (terms; needs calibration test) |
