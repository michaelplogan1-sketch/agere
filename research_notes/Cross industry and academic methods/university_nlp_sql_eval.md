# University research 2023-2026: clinical NLP, clinical/claims text-to-SQL, semantic layers, healthcare LLM evaluation

Research date: 2026-11-17. Pages marked "opened" were fetched and read. Pages marked "search snippet" were seen only in search results and should be re-checked before anyone relies on them. No PDFs were downloaded. Leaderboard entries from companies are described generically, without vendor names. Model names appear only as benchmark citations.

Recommendation key: **trial** = worth a measured experiment in Agere against a simple baseline and an LLM-alone baseline; **park** = relevant but not yet actionable; **skip** = not worth Agere effort now.

## Q1. Current best results on EHRSQL (incl. abstention), BIRD and Spider 2.0, and which techniques give measured gains

### Takeaway
EHRSQL 2024 is the benchmark closest to Agere's frontier text-to-SQL rung, because it scores abstention on unanswerable questions. The top teams all used either agreement across several samples or models, or a confidence threshold, to decide when not to answer. Even the winner scores far below zero when a single wrong answer outweighs all correct ones. On general benchmarks, BIRD test execution accuracy is now about 83% (humans: about 93%). Spider 2.0-Lite tops out around 76%. Interactive and ambiguous settings remain hard: BIRD-Interact success is about 17%.

### Cited Findings
**EHRSQL 2024 shared task** (KAIST AI organisers; ClinicalNLP workshop at NAACL 2024; arXiv May 2024; opened)
- Data: the MIMIC-IV demo database (100 patients). The test set has 934 answerable and 233 unanswerable questions (about 20% unanswerable). Extra adversarial unanswerable questions from TrustSQL were added only to the validation and test sets. — [EHRSQL 2024 overview](https://arxiv.org/html/2405.06673v2)
- Reliability Score (RS(c)):
  - +1 for a correct answer, and +1 for abstaining on an unanswerable question.
  - 0 for abstaining on an answerable question.
  - −c for a wrong answer, or for answering an unanswerable question.
  - The primary metric is RS(10), where one mistake cancels ten correct answers. — [EHRSQL 2024 overview](https://arxiv.org/html/2405.06673v2)
- Results (RS(0) / RS(10) / RS(N)) — [EHRSQL 2024 overview](https://arxiv.org/html/2405.06673v2)
  - Simple baseline, abstain on everything: 20 / 20 / 20.
  - 1st, LG AI Research & KAIST: 88.17 / 81.32 / −711.83. Method: self-training an LLM with pseudo-labelled unanswerable questions.
  - 2nd, PromptMind: 82.6 / 74.89 / −817.4. Method: three frontier LLMs (ChatGPT, GPT-4, Claude Opus) must agree on the SQL, otherwise the system abstains.
  - 3rd, ProbGate (KAIST): 81.92 / 74.21 / −818.08. Method: a fine-tuned model plus a log-probability threshold filter and error handling.
  - 4th, KU-DMIS (Korea Univ.): 72.07 / 59.21. Method: abstains when sampled SQL outputs disagree.
  - Pipeline teams that used a separate answerability classifier plus a generator (T5-3B, CodeLlama-7B) scored lower, at 36-44 RS(10).
- Organisers' conclusions — [EHRSQL 2024 overview](https://arxiv.org/html/2405.06673v2)
  - Unified approaches beat pipeline approaches.
  - A smaller gap between RS(0) and RS(10), meaning fewer wrong answers, predicted rank.
  - Fine-tuning on the domain helped.
  - Test-time pseudo-label tuning may not carry over to live deployment.
  - No team achieved a positive RS(N), so none of the systems is fully reliable.

**BIRD** (HKU-led benchmark; leaderboard opened, entries dated through Oct 2026)
- Top test execution accuracy (EX) is 83.40 (Oct 2026). The top eight all fall between 79.8 and 83.4, and all use the "oracle knowledge" evidence hints. Human performance is listed as 92.96. Nearly all top entries are industry systems. The one university-linked entry in the top eight is GrainSQL (Purdue, an individual submission) at 82.95. — [BIRD leaderboard](https://bird-bench.github.io/)
- BIRD-Interact (ICLR 2026 oral), an interactive and clarification setting: the best LLMs reached a 16.33% success rate on the full 600 tasks. GPT-5 scored 8.67% (c-Interact) and 17.00% (a-Interact), per the Oct 2025 update. — [BIRD leaderboard](https://bird-bench.github.io/)
- The "oracle knowledge" hints are effectively a per-question mini semantic layer, and every top score depends on them (inference below).

**Spider 2.0** (HKU XLANG and collaborators; ICLR 2025; leaderboard opened)
- At launch, the best o1-preview agent solved 21.3% of 632 enterprise workflow tasks. The same approach scored 91.2% on Spider 1.0 and 73.0% on BIRD. — [Spider 2.0, ICLR 2025](https://mlanthology.org/iclr/2025/lei2025iclr-spider) (search snippet)
- Current leaderboard tops (no dates given; all entries are industry agent systems) — [Spider 2.0 leaderboard](https://spider2-sql.github.io/)
  - Spider 2.0-Lite: 76.23.
  - Spider 2.0-Snow: 96.70. These scores are much higher than Lite and should be treated cautiously.
  - Spider 2.0-DBT: 65.6.
  - One academic and industry collaboration on Lite, with UCSD as the academic partner, scores 73.13.

**Techniques with measured gains**
- *Schema pruning and selection.* CHESS (Talaei, Pourreza, Mirhoseini, Saberi; Stanford affiliation per author list; May 2024, revised Nov 2024) — [CHESS arXiv](https://arxiv.org/abs/2405.16755) (opened)
  - The schema selector raised accuracy by about 2% and cut LLM tokens five-fold.
  - Full CHESS reached 71.10% on the BIRD test set, within 2% of the leading proprietary method of the time, with about 83% fewer LLM calls.
- *Self-consistency and multi-model agreement as an abstention gate.* This is the 2nd and 4th-place EHRSQL approach (scores above). — [EHRSQL 2024 overview](https://arxiv.org/html/2405.06673v2)
- *Execution feedback and executability checks.* Used by the AIRI and LTRC pipeline teams (RS(10) about 44), which still ranked below the unified approaches. — [EHRSQL 2024 overview](https://arxiv.org/html/2405.06673v2)
- *Clarification*
  - AmbiSQL (Aug 2025, revised Mar 2026) detects ambiguity and asks multiple-choice clarification questions. Its demo uses 40 ambiguous queries. — [AmbiSQL arXiv](https://arxiv.org/abs/2508.15276) (opened)
  - A search snippet reports exact match rising from 42.5% to 92.5% on BIRD databases with clarification. This was not visible on the abstract page I opened, the authors' commercial backend was used, and the test is small.
  - PRACTIQ (NAACL 2025) and AMBROSIA (NeurIPS 2024) show that LLMs handle ambiguous or unanswerable questions poorly. — search snippets: [PRACTIQ](https://preview.aclanthology.org/setup/2025.naacl-long.13), [AMBROSIA review](https://themoonlight.io/review/ambrosia-a-benchmark-for-parsing-ambiguous-questions-into-database-queries)

### Inferences
- **EHRSQL-style reliability scoring (RS(10), with abstain-all as the baseline).** **Trial (high priority).** Agere's frontier rung should report RS(c), not just accuracy, against two baselines: "abstain always" and "saved recipe only".
  - Buyer question: "can I trust a number the system gives, or will it say it doesn't know?"
  - Data needed: Agere's own question set with labelled unanswerable items, meaning out-of-scope questions, missing fields and undefined metrics.
  - Maturity: academic benchmark, cheap to adopt.
- **Agreement-gated abstention** (sample N SQL candidates or use two models; answer only when the executed results agree). **Trial.** This is the cheapest measured abstention method in EHRSQL. Compare it with a log-probability threshold and with a small answerability classifier (the System One rung). In EHRSQL, standalone pipeline classifiers did worse.
- **Schema pruning ahead of the frontier model.** **Trial.** CHESS's numbers support it on cost: about 5x fewer tokens for about +2 points of accuracy. Relevant to Agere's claims and EHR warehouse schemas.
- **Clarifying conversation.** Already in Agere. The evidence that it raises accuracy is weak and comes from small or demo studies; BIRD-Interact shows models are poor at it. **Trial** only as a measured A/B test on ambiguous questions, compared with a "ask nothing, state the assumption" baseline.
- **Spider 2.0 / BIRD leaderboard agents.** **Park.** These are tuned industry stacks on non-healthcare data. Use them as a ceiling reference only.

### Gaps
- MIMICSQL and EHRSQL results after 2024 from academic groups, beyond the shared task, were not retrieved.
- Claims-data text-to-SQL benchmarks: I found no academic claims-specific benchmark in this pass.
- The self-consistency gain on BIRD in isolation, apart from CHESS, was not separately sourced.
- The AmbiSQL before and after numbers were not confirmed on a page I opened.

## Q2. Evidence that a semantic layer, metric layer, ontology or knowledge graph raises LLM text-to-SQL accuracy

### Takeaway
The best-known study (Sequeda, Allemang, Jacob; Nov 2023) found that GPT-4 accuracy on enterprise insurance questions rose from 16% over the raw SQL schema to 54% over a knowledge graph (ontology plus mappings). Industry follow-ups report figures close to 100% for questions the layer covers, but the samples are tiny. BIRD's "oracle knowledge" setting is an academic analogue: every top BIRD score relies on it. I found no independent academic replication.

### Cited Findings
- **Sequeda, Allemang & Jacob** (data.world research; arXiv Nov 13, 2023) — [arXiv 2311.07509](https://arxiv.org/abs/2311.07509) (opened)
  - The benchmark has three parts: an insurance-domain enterprise SQL schema, enterprise questions ranging from reports to metrics, and an ontology plus mappings that define a knowledge graph.
  - Zero-shot GPT-4 over SQL scored 16% accuracy, versus 54% over the knowledge graph.
  - The per-quadrant breakdown (question and schema complexity) was not on the abstract page.
- **Same group's follow-up:** an approach raised overall accuracy to 72% with a 20% error rate. — search snippet only, via [catalyzex author page](https://www.catalyzex.com/author/Juan%20Sequeda); unverified.
- **dbt Labs semantic-layer benchmark** (April 2026) — search snippet only, [dbt blog](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026?version=2); I did not open the page.
  - 11 business questions, 20 runs per model, on the same insurance dataset.
  - Semantic-layer grounding reportedly raised accuracy from about 84-90% to 98-100%.
  - This is vendor evidence on a very small question set.
- **BIRD's oracle-knowledge hints:** all top-8 test scores (79.8-83.4) use them. — [BIRD leaderboard](https://bird-bench.github.io/)

### Inferences
- **Governed definitions and the metric layer as grounding for the frontier rung.** Already central to Agere. **Trial** a measured ablation on Agere's own question set, with RS(10) and cost per question for each arm:
  - (a) raw schema
  - (b) schema plus governed definitions
  - (c) a direct semantic-layer query for covered metrics
  - Buyer questions: VBC measure rates, PMPM, readmissions, and similar governed metrics where the definition matters more than SQL skill.
  - Data needed: definitions plus 50-200 labelled questions.
  - Maturity: published evidence is low-n and partly vendor-authored. That is a reason to measure locally, not a reason to drop the approach.
- The large gain at low baseline accuracy (16% to 54%) and the smaller gain at high baseline accuracy (about 85% to about 100%) suggest the layer matters most on messy enterprise schemas. Claims and EHR warehouses are messy schemas.

### Gaps
- No independent academic replication of the knowledge-graph or semantic-layer gain on healthcare data was found.
- The quadrant breakdown and the follow-up numbers need verification on the full paper's HTML.

## Q3. Stanford MedHELM and similar frameworks: tasks, and findings for administrative and operational tasks

### Takeaway
In MedHELM, frontier LLMs scored lowest on **Administration & Workflow**, at 0.53-0.63 normalised accuracy. Clinical Note Generation scored 0.73-0.85. Administration & Workflow is the category nearest to Agere's operational and analytics questions. Conversational evaluation (CRAFT-MD) shows accuracy falls when models must gather information through dialogue rather than read a vignette. LLM juries agree with clinicians about as well as clinicians agree with each other, which is not very high.

### Cited Findings
- **MedHELM** (Stanford; HAI news Feb 28, 2025; arXiv May-June 2025; published in Nature Medicine in 2026 per a search snippet)
  - Taxonomy: 5 categories, 22 subcategories, 121 tasks. Clinicians agreed with the category mapping 96.7% of the time. — [Stanford HAI](https://hai.stanford.edu/news/holistic-evaluation-of-large-language-models-for-medical-applications) (opened)
  - Initial release: 31 datasets, 6 models, zero-shot. — [Stanford HAI](https://hai.stanford.edu/news/holistic-evaluation-of-large-language-models-for-medical-applications)
    - Large models did best on complex reasoning such as medical calculations.
    - Medium models were competitive on prediction tasks such as readmission risk, at lower compute.
    - Small models did well on well-structured tasks.
    - On open-ended generation, BERTScore-F1 differed by less than 0.07 across models in 10 of 12 benchmarks, so automated metrics may not separate the models.
    - Some low scores came from refusals or ignored formatting instructions, not from lack of capability.
  - v2 (9 frontier models, 35 benchmarks) — [MedHELM arXiv](https://arxiv.org/abs/2505.23802) (opened)
    - Normalised accuracy by category:
      - Administration & Workflow: 0.53-0.63
      - Clinical Decision Support: 0.56-0.72
      - Note Generation: 0.73-0.85
      - Patient Communication: 0.78-0.83
    - Win rates: DeepSeek R1 66%, o3-mini 64%. Claude 3.5 Sonnet was comparable at about 40% lower estimated compute cost.
    - The LLM jury's agreement with clinicians (ICC 0.47) was above clinician-clinician agreement (0.43), ROUGE-L (0.36) and BERTScore-F1 (0.44).
- **CRAFT-MD** (Harvard Medical School & Stanford; Nature Medicine, Jan 2, 2025) — [HMS news](https://hms.harvard.edu/news/how-good-are-ai-clinicians-medical-conversations) (opened)
  - Four LLMs, 2,000 vignettes across 12 specialties.
  - Accuracy fell when moving from exam-style questions to conversation, and from multiple choice to free response.
  - Models missed key history and struggled to combine scattered information.
  - AI-plus-expert evaluation took 48-72 hours plus 15-16 expert hours for 10,000 conversations, versus about 1,150 hours for human evaluation.
- **HealthBench** (OpenAI, May 2025) — [HealthBench arXiv](https://arxiv.org/html/2505.08775v1) (search snippet; not opened in full)
  - 48,562 physician-written rubric criteria.
  - The model grader's agreement with physicians is reported as comparable to physician-physician agreement.
  - On the Hard subset, no model scored above 32%.
- **HealthBench meta-evaluation** (Feb 2026), 60,896 physician judgments — [arXiv 2602.22758](https://arxiv.org/pdf/2602.22758v1.pdf) (search snippet; a PDF link, not opened)
  - Physician-physician and model-physician agreement both fall around 55-75%.
  - Much of the disagreement comes from ambiguous content and underspecified guidance.

### Inferences
- **MedHELM-style task taxonomy and LLM-jury method as Agere's evaluation harness.** **Trial.** Map Agere's rungs to the Administration & Workflow subcategories. Use LLM-jury grading only where it is calibrated against clinician labels; MedHELM's ICC of 0.47 is the reference point.
  - Buyer question: "does it work on our operational questions, not exam questions?"
  - Maturity: published, open-source framework.
- **CRAFT-MD's lesson for the clarifying conversation.** Evaluate the multi-turn path, not only single-turn questions, because accuracy drops in dialogue. **Trial** as an evaluation design. The diagnostic content itself is out of Agere's scope.
- **HealthBench.** **Park.** It tests consumer and clinician health chat, not analytics. The rubric method is reusable, but the agreement ceiling (55-75%) limits LLM-as-judge validity.
- Use exact-match or execution scoring wherever possible. BERTScore and ROUGE did not separate the models in MedHELM.

### Gaps
- Per-task Administration & Workflow results (for example, billing code or scheduling tasks) were not itemised on the pages I opened.
- The exact HealthBench physician agreement percentages were not read.

## Q4. Clinical information extraction: LLMs vs fine-tuned small models vs rules, head to head

### Takeaway
With enough training data, instruction-tuned open LLMs beat fine-tuned BERT on clinical named-entity and relation extraction (including negation and certainty modifiers) by only about 1-4 F1 points. Their margin is larger on new institutions or with little training data. They run up to 28x slower. Adapted LLMs can match or beat clinicians on summarisation in reader studies, but both make fabrication errors.

### Cited Findings
- **Hu et al., "Are we ready to switch to LLMs?"** (Hua Xu group, Yale; UTHealth co-authors; arXiv Nov 2024, revised Jan 2025) — [arXiv 2411.10020](https://arxiv.org/abs/2411.10020v4) (opened)
  - Corpus: 1,588 notes from UT Physicians, MTSamples, MIMIC-III and i2b2, covering 4 entity types and 16 modifiers including negation and certainty.
  - With enough training data, LLaMA beat BERT by about 1% F1 on NER and 1.5-3.7% on relation extraction, with larger gains when training data was limited.
  - On unseen i2b2 data, LLaMA-3-70B beat BERT by 7% F1 on NER and 4% on relation extraction.
  - LLaMA ran up to 28x slower and needed more GPU memory.
  - The authors say the choice should depend on the task, the compute available and the intended use.
- **ISPOR Europe 2024 abstract (oncology markers):** a fine-tuned BioBERT model was preferable to LLMs, which needed extensive fine-tuning to approach it. — [ISPOR abstract](https://www.ispor.org/heor-resources/presentations-database/presentation/euro2024-4013/145013) (search snippet)
- **Van Veen et al., clinical text summarisation** (Stanford, Langlotz/Chaudhari labs; arXiv Sep 2023; Nature Medicine 2024) — [arXiv 2309.07430](https://arxiv.org/abs/2309.07430) (opened)
  - Tasks: radiology reports, patient questions, progress notes and dialogue, with eight adapted LLMs.
  - Ten physicians judged the best LLM summaries equivalent to experts' in 45% of cases and superior in 36%.
  - Both LLMs and experts made errors, which the authors categorised by potential harm.

### Inferences
- **Note-reading ladder (rules, then a small fine-tuned encoder, then an LLM).** The evidence supports keeping a fine-tuned small model as the default extractor for high-volume, well-defined fields such as problems, medications and negation. Escalate to an LLM when training data is scarce, the site is new, or a field is unusual.
  - **Trial:** compare rules/regex (for example, NegEx-style negation), a fine-tuned BERT-class model and an LLM on Agere's own note fields. Report F1 and cost per 1,000 notes for each.
  - Buyer questions: quality measure exclusions (for example, documented contraindications), HCC and risk-adjustment documentation support, and care-gap evidence in notes.
  - Data needed: annotated note sample, de-identified or synthetic.
  - Maturity: published, with an open toolkit (Kiwi, per a search snippet).
- **Clinical summarisation.** **Park** for Agere: it serves individual-case work, not population analytics. Its relevance would be evidence summaries attached to a decision.

### Gaps
- I found no 2023-2026 head-to-head study that included rule-based systems alongside fine-tuned models and LLMs on negation or timing from Mayo or UCSF in this pass.
- Absolute F1 values for Hu et al. were not on the abstract page.
- Temporal reasoning (timing) extraction benchmarks were not retrieved.

## Q5. Abstention, selective prediction and conformal methods for LLM answers

### Takeaway
Conformal abstention (Abbasi Yadkori et al., Apr 2024) uses agreement across sampled answers as the confidence signal and calibrates an abstention threshold on a held-out set, which bounds the hallucination rate. In EHRSQL, the practical winners were agreement- or threshold-gated abstention, scored with a penalty-weighted metric.

### Cited Findings
- **"Mitigating LLM Hallucinations via Conformal Abstention"** (Abbasi Yadkori, Kuzborskij, Tomasev et al.; arXiv Apr 2024; affiliation not shown on page) — [arXiv 2405.01563](https://arxiv.org/abs/2405.01563) (opened)
  - The LLM judges how similar its own sampled responses are. Conformal prediction then sets an abstention rule with a theoretical bound on the hallucination rate, calibrated on a small held-out set.
  - It bounded hallucination reliably.
  - On long answers (Temporal Sequences), it abstained significantly less than log-probability baselines. On short answers (TriviaQA), it performed comparably.
- **Follow-up:** reinforcement-learned conformal abstention policies (Feb 2025). — [arXiv 2502.06884](https://www.arxiv.org/pdf/2502.06884) (search snippet)
- **EHRSQL evidence** for agreement-gated and log-probability-gated abstention in clinical text-to-SQL. — see Q1, [EHRSQL 2024 overview](https://arxiv.org/html/2405.06673v2)

### Inferences
- **Conformal calibration of Agere's abstain/answer threshold.** **Trial.** Signal: agreement between executed results of N sampled SQL queries, or between the small and frontier models. Calibrate on a held-out labelled set to a target error rate, for example at most 2% wrong answers among those returned. Report the coverage achieved.
  - Buyer question: a "certified" answer rate for executives and compliance staff.
  - Data needed: a labelled calibration set, a few hundred questions.
  - Maturity: method published; not yet shown on healthcare SQL.
  - It fits the System One classifier rung as the gate, but it must beat simple sample agreement without calibration.

### Gaps
- No published conformal text-to-SQL study on clinical or claims data was found.
- Coverage numbers at fixed error rates were not on the abstract page.
