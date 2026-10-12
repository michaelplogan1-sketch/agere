# Research foundation for healthcare data intelligence

Research snapshot: **October 8, 2026**. This is a broad, purposive evidence review and curriculum-design project, not a systematic review, meta-analysis or clinical validation. Public program descriptions and company claims are separated from evaluations. All proposed patient scenarios are **synthetic**.

The central design conclusion is that the platform must connect four things that are often analyzed separately: what the patient evidence actually supports, what a feasible intervention could change, what the applicable contract means, and whether the result improved patients' lives. Adding models can help with parts of this work. It does not supply those distinctions automatically.

## What was mined and what remains outside scope

- **105 CMS Innovation Center directory entries**, downloaded from the directory's public data API. A second request with a larger page size returned the same rows. Their raw contents, normalized inventory and retrieval provenance are saved.
- **40 CMS model pages** inspected at overview/current-status level, with additional design sections for priority programs. This includes all entries classified Active or Announced in the downloaded directory, plus selected historical models. Overview review does not mean their technical manuals have been compiled into rules.
- **89 source targets; 84 scoped reviews and five incomplete leads.** A scoped review can mean a complete short page, a primary-study abstract, selected report sections or a model overview. Each scope is recorded in `evidence_catalog.json` and `EVIDENCE_NOTES.md`.
- Companies include **Aledade, Innovaccer, Pearl Health, Wellvana, Oak Street Health and Interwell Health**. Payer sources include Humana, Cigna, Elevance, UnitedHealthcare and the Massachusetts Alternative Quality Contract evaluation. Medicaid material includes Oregon and relevant CMS state models.
- International material includes England, Singapore, Denmark, Brazil, Germany, the Netherlands and OECD patient-reported survey findings. Thailand and an additional ten-country survey remain incomplete leads, not completed country reviews.
- Hospital/QI sources include AHRQ CUSP and RED, IHI measurement guidance, I-PASS and randomized community-health-worker research. Adjacent fields include reliability engineering, process control, process mining, causal inference, prognostics and constrained optimization.

The directory snapshot contains 61 Not Active, 20 Active, three Withdrawn, four Authorized for Expansion, nine Performance Period Ended and eight Announced entries. These are **CMS directory labels**, not inferred patient-enrollment status. Source: [CMS model directory](https://www.cms.gov/priorities/innovation/models); exact counts are reproduced from `cms_inventory.json`.

The metadata itself contains an important finding: **all 105 rows have blank Date Began and Date Ended fields**. Beneficiary/physician-impact fields are also blank throughout; blank is not zero. Model pages supply additional timing information, sometimes including future launches, early termination or overlapping historical text. `mining_findings.json` records these checks. A directory-only program recommender would therefore lack essential timing evidence.

This does not cover every CMS manual, every state Medicaid arrangement, every commercial contract or every international system. MSSP, standing hospital/clinician quality-payment programs, MA measures and private contracts require sources outside the Innovation Center directory. Private commercial rates and employer-specific terms cannot be recovered from a public administrative guide. See [CMS standing value-based programs](https://www.cms.gov/medicare/quality/value-based-programs) and [official MSSP data products](https://data.cms.gov/medicare-shared-savings-program).

## What the companies teach us

These companies combine different mixtures of data infrastructure, clinical services, practice enablement, risk management and contracting. Their results cannot be used to isolate the effect of their analytics products.

| Organization | Useful lesson for our design | Evidence boundary |
|---|---|---|
| Aledade | Connect primary-care intelligence to practice readiness, longitudinal accountability and contract reconciliation. | Company-reported MSSP performance; not an independent trial of its software. [Source](https://aledade.com/newsroom/aledade-news/aledade-acos-save-over-1-billion-in-2024) |
| Innovaccer | Reconcile claims, EHR, ADT, labs and HIE information so a downstream action is based on a coherent patient picture. | Public case-study landing page; underlying report was gated and not obtained. [Source](https://innovaccer.com/resources/case-studies/adventist-healthcare-physician-alliance-transforms-workflows-to-succeed-in-mssp) |
| Pearl Health | Evaluate cohort maturity, comparator design and time since implementation. | Company observational analysis explicitly described as not peer reviewed. A randomly sampled comparator is not random treatment assignment. [Source](https://www.pearlhealth.com/blog/outperforming-the-national-trend) |
| Wellvana | Hospital-led and rural organizations need an account of clinical opportunity, operational capacity and financial incentives. | Company preliminary results and selected comparisons; not proof of causal savings from analytics. [Source](https://wellvana.com/news/results-2024) |
| Oak Street Health | Access, prevention and behavioral health belong in the explanation of a delivery model's performance. | Historical company impact announcement, not current performance or a randomized comparison. [Source](https://www.oakstreethealth.com/news/oak-street-health-releases-inaugural-social-impact-report) |
| Interwell Health | A kidney-care pathway extends through education, outside care, transplant evaluation and dialysis preparation. | Single-practice case study combining services and technology. [Source](https://www.interwellhealth.com/resources/insights/at-eastern-nephrology-associates-value-based-care-delivers-results-that-matter) |

**Product implication:** build the intelligence that establishes what is happening and why it deserves attention. A care-coordination workflow can consume that result, but task assignment is not the core innovation the user is seeking. Every proposed action should arrive with its supporting evidence, relevant exclusions, uncertainty, applicable rule and a plausible mechanism of benefit.

The payer research also shows why one universal financial score would be misleading. Different arrangements distribute clinical, administrative and financial responsibilities differently. UnitedHealthcare's public guide distinguishes capitation, delegation and enrollment timing; it does not disclose an individual organization's full economics. Humana's release uses both within-plan comparisons and comparisons with Original Medicare, which are different comparison populations. [UnitedHealthcare guide](https://www.uhcprovider.com/content/dam/provider/docs/public/admin-guides/2026-UHC-Administrative-Guide.pdf), [Humana report announcement](https://policy.humana.com/news-and-resources/news-press/2026/humana-report-finds-value-based-care-improves-outcomes-for).

## The evidence that should change our training

**Value is broader than spending.** Porter and Kaplan's framework connects outcomes that matter to patients with resources consumed over a full care cycle. That requires keeping paid claims, prices, organizational costs and patient burden distinct. Our answer schema should include function, symptoms, patient goals and treatment burden even when those data are missing. Missing information should remain visible. [Harvard VBHCD initiative](https://www.isc.hbs.edu/about-michael-porter/affiliated-organizations-institutions/vbhcd-initiative/Pages/default.aspx).

**High risk is not the same as high opportunity to benefit.** The Camden randomized trial did not show a readmission reduction from the studied intervention, despite a before-after comparison that could have appeared favorable. That is a direct warning against selecting recent high utilizers and crediting a subsequent decline to our recommendations. [Primary trial abstract](https://pubmed.ncbi.nlm.nih.gov/31914242/).

There is also positive evidence for particular interventions in particular settings. A randomized community-health-worker study reported lower readmissions overall, but its rehabilitation subgroup analysis was post hoc and the trial had recruitment and analysis limitations. The lesson is to learn **intervention, population, context and evidence strength together**. Neither a positive nor a null trial licenses a universal conclusion about care management. [Trial](https://jamanetwork.com/journals/jamanetworkopen/fullarticle/2780265).

**Financial success has several meanings.** Benchmark-relative savings, causally estimated spending reductions, payer net savings after program payments and provider operating margin are different quantities. The CMS synthesis and longer-term global-budget research make this distinction essential. [CMS synthesis](https://www.cms.gov/priorities/innovation/data-and-reports/2022/wp-eval-synthesis-21models), [Alternative Quality Contract study abstract](https://pubmed.ncbi.nlm.nih.gov/31314969/).

**Clinical quality and net savings can diverge.** The KCC page's evaluation summary describes quality gains alongside a net Medicare loss in the evaluated period. This becomes a benchmark case: the platform should be able to say both things without forcing them into one success/failure label. The page's current model design is not itself evidence of future results. [KCC model and evaluation summary](https://www.cms.gov/priorities/innovation/innovation-models/kidney-care-choices-kcc-model).

**More spending may mean better access.** Oregon's financial reporting connects expenditure pressure with changing enrollment, service use and reimbursement. We should decompose price, volume, mix and access before describing a location as inefficient. Conversely, lower spending caused by inability to obtain needed care should not be rewarded. [Oregon financial analysis](https://www.oregon.gov/oha/ERD/Pages/Increased-health-care-usage-grew-2024-expenses-for-Oregon-Medicaid-insurers.aspx).

**A process measure cannot stand in for the outcome.** Screening, referral, engagement, delivered service and resolved need are separate states. Oregon's quality release and the AHC evaluation are useful counterexamples to collapsing them. The AHC tracks also use different evaluation designs, which must remain distinguishable. [Oregon quality report](https://www.oregon.gov/oha/ERD/Pages/Oregon-Medicaid-insurers-improved-behavioral-health-care-quality-in-2024.aspx), [AHC final evaluation](https://www.cms.gov/priorities/innovation/data-and-reports/2026/ahc-final-report).

**An apparent mechanism needs evidence.** CMS's VBID termination explanation does not support blaming social-needs interventions for the model's increased costs. A fluent model could invent that explanation unless we train explicitly against it. [CMS termination FAQ](https://www.cms.gov/priorities/innovation/innovation-models/medicare-advantage-value-based-insurance-design-model-frequently-asked-questions-about-end-model).

## International lessons worth adapting

| Setting/source | Design lesson | What cannot be inferred |
|---|---|---|
| England, linked population data | Link primary, hospital and social-care evidence around the person, with source completeness recorded. [NHS case](https://www.england.nhs.uk/long-read/linking-data-and-embedding-a-single-system-wide-population-health-management-approach/) | Data linkage alone caused better outcomes. |
| Singapore, Healthier SG indicator design | Distinguish near-term implementation from later clinical and utilization results. [Ministry response](https://www.moh.gov.sg/newsroom/targeted-versus-medium-term-outcome-indicators-of-healthier-sg-programme/) | A policy's intended benefits are already established effects. |
| Denmark, shared infrastructure | Governance, shared data services and patient access are part of the system architecture. [Infrastructure account](https://healthcaredenmark.dk/news-publications/news/how-denmark-built-a-fully-digital-healthcare-system-and-what-comes-next/) | A promotional description is an independent effectiveness evaluation. |
| Brazil, primary-care expansion | Local governance and implementation capacity matter alongside coverage. [Study abstract](https://pubmed.ncbi.nlm.nih.gov/28069858/) | Municipal associations are individual treatment effects or directly transferable US estimates. |
| Germany, disease management | Enrollment at scale does not establish a detectable population mortality benefit. The study's uncertainty also does not prove no benefit exists. [Study abstract](https://pubmed.ncbi.nlm.nih.gov/34781907/) | Disease management is universally effective or universally ineffective. |
| Netherlands, bundled chronic care | Better coordination and financially successful whole-person care are different questions, especially with multimorbidity. [Early study](https://pubmed.ncbi.nlm.nih.gov/22323174/), [longer-term spending study](https://pubmed.ncbi.nlm.nih.gov/33947604/) | Every bundle produces savings, or every bundle fails. |
| OECD PaRIS | Include patient-reported function, experience, continuity, trust and the burden of multiple conditions. [Executive summary](https://www.oecd.org/en/publications/does-healthcare-deliver_c8af05a5-en/full-report/executive-summary_e7073a79.html) | Cross-country associations establish which national system causes the best outcomes. |

This argues for a whole-person evidence model with disease-specific views. A person should not become separate, incompatible patients inside a kidney program, a heart-failure program and a behavioral-health program.

## Hospital improvement becomes a measurement discipline

For every proposed improvement, record an outcome, a process measure and a balancing measure. For example, transition information can be assessed for clinical usefulness and actual delivery; readmission can be measured alongside function and harm; staff workload can reveal a new bottleneck. AHRQ RED explicitly separates implementation reach from outcome monitoring and discusses unintended consequences. [AHRQ RED](https://www.ahrq.gov/patient-safety/settings/hospital/red/toolkit/redtool6.html), [IHI measures](https://www.ihi.org/library/model-for-improvement/establishing-measures).

Our intelligence layer should detect incomplete intervention exposure, denominator changes and missing follow-up before reporting improvement. The I-PASS implementation study supports investigating structured handoffs, but its design and disclosed interests need to travel with the finding. [Primary study abstract](https://pubmed.ncbi.nlm.nih.gov/36326255/).

## What to borrow from other fields

These are proposed engineering adaptations, not claims of demonstrated clinical efficacy.

| Field | Component to adapt | Healthcare use | Boundary |
|---|---|---|---|
| Reliability engineering | Multiwindow alerting and service objectives | Detect stale feeds, sudden mapping failures and persistent evidence-quality deterioration. [Google SRE](https://sre.google/workbook/alerting-on-slos/) | Do not translate a system error budget into tolerated patient harm. |
| Manufacturing statistics | Control charts, CUSUM/EWMA and baseline investigation | Distinguish real pathway deterioration from ordinary variation. [NIST](https://www.itl.nist.gov/div898/handbook/pmc/section1/pmc12.htm) | Stable definitions and appropriate exposure/case mix are prerequisites. |
| Process mining | Event logs and pathway conformance | Find repeated stalls between a clinical decision and delivered care. [PM4Py](https://github.com/process-intelligence-solutions/pm4py) | Sequences reveal hypotheses, not causation; licensing needs review. |
| Causal inference | Treatment-effect and policy-evaluation methods | Ask who benefits from an intervention, separate from who has high risk. [EconML](https://www.pywhy.org/EconML/spec/motivation.html) | Observational estimators need identification assumptions and overlap. |
| Prognostics | Temporal benchmarks and separately established outcomes | Test whether an early signal is available before deterioration, without future leakage. [NASA repository](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/) | Engineering labels do not supply clinical truth. |
| Operations research | Constrained optimization | Match plausible interventions to capacity, access and patient preferences. [OR-Tools](https://developers.google.com/optimization/scheduling/employee_scheduling) | The objective must protect clinical benefit and choice; optimization cannot choose those values for us. |

Claims-development methods from actuarial work are another candidate for separating immature spending from real improvement. That component remains a design proposal requiring a separate actuarial methods review; no reserving method was validated here.

## How this builds on the existing stack

The local project already has a synthetic corpus, explicit question capability states, deterministic/data-quality operations, inference boundaries, caches and hosted-reader integration work. The research supplement preserves those facts rather than pretending every listed question already runs. It does not depend on access to Lumeris's ontology or TextQL deployment.

1. **Keep the source-backed patient assertion layer central.** Extend the prior temporal evidence-store proposal to distinguish event time, arrival time, corrections, patient versus caregiver, and copied versus independent evidence.
2. **Add an evidence and intervention catalog.** Store population, intervention, comparator, outcomes, design, limitations and transportability conditions. A company claim is not interchangeable with a randomized result.
3. **Compile program rules outside model weights.** Retain document authority, proposed/final status, publication/effective dates, beneficiary/organization eligibility, measurement periods and conflict/overlap rules. Recompute only affected cohorts after reviewed changes.
4. **Keep separate clinical, patient-experience and financial outputs.** Reconcile payment accounting deterministically. Return missing outcome data explicitly. Do not reduce patient value to a revenue opportunity.
5. **Use a typed question planner and specialized operators.** SQL handles counts and joins; rules handle explicit eligibility; statistical methods handle trends and comparisons; readers resolve narrative evidence. A frontier model explains or handles difficult exceptions against this evidence.
6. **Add evaluation of interventions and feasibility.** Discovery should produce a testable hypothesis and a feasible option, with local capacity and patient preference visible. It should not fabricate causal recommendations from correlations.
7. **Measure total cost at a fixed quality target.** Include extraction, retrieval, local compute, hosted inference, review and maintenance. Reuse assertions and invalidate dependencies precisely. Route to expensive models when expected value justifies the escalation.

The prior [engineering research](/Users/mikel/clinical-insights-local/ENGINEERING_RESEARCH.md) already proposed hybrid retrieval, reranking, virtual ontology access, fine-grained caching and query compilation. This review adds the **clinical/operational curriculum and evidence distinctions** those components need to preserve. It does not establish that any particular Jev/MedGemma/frontier combination is superior.

## The most consequential new questions

The supplement contains 96 research formulations across 16 domains. Some deepen existing questions rather than introducing independent capabilities. Examples:

- Which people look low-risk because they cannot access care and therefore generate few claims?
- Which documented treatment gaps are actually gaps in our data, appropriate holds or already addressed elsewhere?
- Which interventions improve what patients value, and which merely improve a reported measure?
- Which clinical improvements remain worth doing even when they do not create short-term payer savings?
- Which apparent savings vanish after coding changes, claims runout, incentive payments and delivery costs are reconciled?
- Which program update changes a patient's eligibility now, and which describes a future or proposed opportunity?
- Which SNF or specialist is a defensible fit for this person once uncertainty, availability, capabilities and preferences are included?
- Which additional piece of evidence would most change a consequential decision?
- Which small-model or deterministic route reaches the required accuracy at the lowest total operating cost?

## What this work does not establish

The current synthetic library cannot represent all maternal, pediatric, commercial-contract, longitudinal PROM, community-capacity or program-overlap tasks. Those need deliberately generated data and later appropriate real-world validation. Repeated synthetic patient draws are not independent clinical observations.

No model was fine-tuned in this research, no clinical effectiveness experiment was run, and no competitor was benchmarked using its actual product. The 32 scenario specifications are not completed benchmark results. Source access failures, abstract-only reading, company sponsorship and incomplete coverage remain visible in the catalog.

The standard for calling this an exceptional platform should be demonstrable: more supported and useful answers at a stated coverage level, fewer consequential errors, lower total cost, and eventually better outcomes in prospective use. The research now supplies a much stronger set of tasks against which to test that ambition.
