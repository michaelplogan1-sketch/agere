# University work on hospital operations analytics, deployed prediction models, and causal program evaluation (2022-2026 preferred)

Notes for the Agere report writer. Source-access status is recorded per item: **[opened]** means the page was fetched and read in this session; **[search]** means the claim comes from search-result summaries of that URL and the page itself was not opened. A Hopkins primary page returned HTTP 403 and could not be opened. No PDFs were downloaded. Vendor names are left out of the product comparisons. Where a study title or URL contains one, it is cited only as a reference.

Product context: Agere already has causal forests, double ML, conformal intervals, Erlang queueing, epidemic models, foundation-model forecasters and Monte Carlo contract valuation. Its own admission, discharge and no-show predictors are archived because 4 of 5 tasks could not be fitted, and LACE-style baselines are the comparator. The entry rule is: a named buyer problem, plus a win over a simple baseline and over an LLM alone, on both accuracy and cost.

## Q1. Hospital operations analytics: capacity command centres, bed/OR/ED flow, discharge prediction with measured operational outcomes (Johns Hopkins, MIT/MIT Sloan, Stanford/Mayo)

### Takeaway
University-linked operations work has good predictive accuracy, but measured operational outcomes are scarce and come from weak designs. The Johns Hopkins Capacity Command Center's gains are self-reported pre-post figures from 2017-2021 with no peer-reviewed evaluation. The strongest MIT-linked deployment is at Hartford HealthCare, an MIT ORC/Sloan collaboration (Bertsimas, Pauphilet et al.). It reports a 0.63-0.67 day drop in average LOS across seven hospitals, but the abstract does not describe a control-group design. I found no Stanford or Mayo bed/discharge deployment with measured LOS or boarding-hour outcomes in this pass.

### Cited Findings
**Johns Hopkins Capacity Command Center (opened 2016; reported 2018-2025)**
- What it is: a centralised, staffed hospital capacity operations centre with real-time status displays and predictive tools. Leaders attribute about a 20% drop in ED boarding to its first two years (remarks by Jim Scheulen). — [Advisory Board 2018](https://www.advisory.com/daily-briefing/2018/06/11/command-center) [search]
- Reported improvements: 60% better ability to accept complex transfers from other hospitals; critical-care transport dispatched 63 minutes sooner; 70% fewer post-procedure OR delays; 21% more discharges before noon year-on-year. — [HFM Magazine](https://www.hfmmagazine.com/articles/2595-command-center-leads-to-more-efficient-facility-operations) [search]
- COVID year: 7,529 patient transfers managed March 2020 to March 2021. — [Hopkins Medicine news, 2021](https://www.hopkinsmedicine.org/news/articles/2021/03/capacity-command-center-celebrates-5-years-of-improving-patient-safety-access) [search; the page itself returned HTTP 403]
- 2024: a "clinical nurse expediter" role was added (August 2024) to investigate and clear specific causes of delay. Boarding is still a focus. — [Hopkins Medicine news, June 2025](https://www.hopkinsmedicine.org/news/articles/2025/06/making-room-for-care-clinical-nurse-expediter-helps-patients-move-forward) [search]
- Evidence quality: every figure is self-reported by Hopkins leadership. The search found no independent or peer-reviewed evaluation. — same sources [search]

**MIT (ORC / Sloan, Bertsimas-Pauphilet group): Hartford HealthCare deployment (preprint May 2023; INFORMS Journal on Applied Analytics 2024)**
- Predicts discharge within 24 h and 48 h, ICU transfer within 24/48 h, end-of-stay mortality, and discharge disposition. Out-of-sample AUC is 75.7%-92.5%, described as "well calibrated". — [arXiv 2305.15629](https://arxiv.org/abs/2305.15629v1) [opened]
- Deployment: more than 200 physicians, nurses and case managers across seven hospitals use it in daily patient review. The authors report a "significant" average LOS reduction of 0.67 days per patient after adoption, and they *anticipate* (project, not realise) $55-72M a year in benefit. — [arXiv 2305.15629](https://arxiv.org/abs/2305.15629v1) [opened]
- Combining the 48-hour discharge prediction with physician predictions gave 10%-28.7% more discharges and fewer 7- and 30-day readmissions (p<0.001). — [arXiv 2305.15629](https://arxiv.org/abs/2305.15629v1) [opened]
- The published version reports a 0.63-day LOS reduction. — [INFORMS, "Patient Outcome Predictions Improve Operations at Hartford HealthCare"](https://pubsonline.informs.org/doi/fpi/10.1287/inte.2024.0170) [search]
- Design caveat: the abstract does not say whether the LOS comparison used a concurrent control, a staggered rollout or a plain pre-post analysis. It also does not compare against a simple baseline such as LACE or clinician-only prediction, apart from the "combined with physician" comparison. — [arXiv 2305.15629](https://arxiv.org/abs/2305.15629v1) [opened]

**MIT: Bertsimas, Pauphilet, Stevens, Tandon, "Predicting Inpatient Flow at a Major Hospital Using Interpretable Analytics" (MSOM 24(6):2809-2824, 2022; BIDMC co-authors)**
- Uses 63,432 admissions to predict short-term discharges, long-stay patients, discharge destination and ICU inflow/outflow, with "80%+" accuracy. The pipeline was put into the hospital EHR, with dashboards for daily bed placement. No post-deployment outcome data were found. — [IDEAS/RePEc record](https://ideas.repec.org/a/inm/ormsom/v24y2022i6p2809-2824.html); [MIT DSpace 1721.1/144084](https://dspace.mit.edu/handle/1721.1/144084) [search]
- Follow-on (reported as Management Science, 2023): ML predictions paired with robust optimisation to make a hospital-wide bed plan. Tested on 7 months of data from a 600-bed centre; it solves in seconds. No adoption outcome found. — search summary via [MIT DSpace listing](https://dspace.mit.edu/handle/1721.1/144084) [search; journal and year not verified]

**MIT (Retsef Levi co-advised), MGH discharge prediction (theses 2018-2020)**
- Surgical inpatients: among the top 30 patients the algorithm ranked, 69% were discharged within 24 h and 89% within 48 h. It was piloted as a web tool at MGH, and the "preliminary" results describe potential, not a measured outcome. — [MIT DSpace 1721.1/117956](https://dspace.mit.edu/handle/1721.1/117956); [MIT LGO blog](https://lgo.mit.edu/blog/lgo-best-thesis-2018-for-predictive-modeling-project-at-massachusetts-general-hospital) [search]
- Medicine inpatients: mean AUC 0.830, precision 47.6% at 24 h and 67.4% at 48 h, recall 43.8%. — [MIT DSpace 1721.1/129850](https://dspace.mit.edu/handle/1721.1/129850) [search]
- The search summary of the Khaniyev/Zanger/Levi model mangled the baseline comparison numbers, so treat them as unverified. The model also catalogued 65 discharge barriers. — [MIT DSpace](https://dspace.mit.edu/handle/1721.1/129850) [search]
- Older MIT OR work (simulation only): scheduling same-day admits last in each OR block cut PACU wait by up to 49% in an MGH simulation. — [MIT DSpace 1721.1/73397](https://dspace.mit.edu/handle/1721.1/73397) [search]

**Non-US comparator (accuracy only)**
- Oxford (Communications Medicine, Nov 2024): an XGBoost model predicting discharge within 24 h beat logistic regression. No LOS outcome. — [PMC11574281](https://pmc.ncbi.nlm.nih.gov/articles/PMC11574281) [search]

**Recommendations (Q1)**
| Idea | Buyer question | Data needed | Maturity | Recommendation |
|---|---|---|---|---|
| Daily ward-level 24/48 h discharge forecast + case-manager review list (Hartford/MIT pattern) | Operations/CFO: "Which beds free up tomorrow, and which patients are discharge-ready but stuck?" | ADT events, orders, vitals/labs timestamps, discharge-disposition, case-management notes | One multi-hospital deployment with an uncontrolled LOS claim | **Trial, as a candidate only.** Must beat (a) a LOS-to-date/DRG-GMLOS baseline and (b) clinician estimates, on precision@k for the top-30 list, before default activation. Agere's own discharge predictor is archived, so re-entry needs fittable task data first. |
| Command-centre style capacity board (status + Erlang/queue forecasts) | Operations: boarding hours, transfer acceptance, OR hold delays | Real-time bed status, ED boarding timestamps, transfer centre logs | Widely adopted; evidence is self-reported pre-post | **Park.** Agere already has Erlang. Add value only by evaluating command-centre effects rigorously (see Q3), not by claiming them. |
| ML + robust-optimisation bed assignment | Operations | Same as above + unit capability rules | Paper-stage | **Skip for now.** |

### Inferences
- Hospital buyers will hear "20% less boarding" or "0.67 days LOS" claims. These are pre-post or uncontrolled. Q3 shows how pre-post claims can be badly biased by regression to the mean and secular trend. Agere's differentiator can be a credible counterfactual for these claims, not another predictor.
- In the Hartford results, model and physician judgment combined is the stated comparison. That fits Agere's rule: a model should be judged against the clinician or case-manager baseline, not only against AUC.

### Gaps
- I found no peer-reviewed Johns Hopkins command-centre evaluation. The Hopkins/MIT joint operations work the brief asks about could not be confirmed.
- I found no Stanford or Mayo bed or discharge deployment with measured LOS or boarding-hour outcomes; there was no time for a targeted search.
- MIT Jameel Clinic: no hospital-operations output found. Its work appears to be clinical ML (imaging, drug discovery), unconfirmed.
- The Hartford full-text design (control, adjustment) was not read because the full paper is a PDF.

## Q2. Do deployed prediction models change outcomes? External validation, net benefit/decision curves, drift monitoring, fairness

### Takeaway
Rigorous outcome evidence exists but is uncommon. A stepped-wedge cluster RCT at Penn combined an ML mortality model with clinician nudges and roughly quadrupled serious-illness conversations. A staggered-deployment study at Kaiser Permanente linked a deterioration alert to about 16% lower relative 30-day mortality. In contrast, a widely deployed proprietary sepsis model had AUC 0.63 at Michigan, against 0.76-0.83 claimed by its developer, and it alerted on 18% of inpatients while missing 67% of sepsis cases. Methods literature supports decision curves, drift monitoring and label-choice audits as governance layers.

### Cited Findings
**External validation (Michigan, JAMA Internal Medicine, June 2021)**
- Wong et al., "External Validation of a Widely Implemented Proprietary Sepsis Prediction Model in Hospitalized Patients": 27,697 patients and 38,455 hospitalisations at Michigan Medicine (Dec 2018-Oct 2019). Hospitalisation-level AUC was 0.63, against the developer-reported 0.76-0.83. — [Healthcare IT News](https://www.healthcareitnews.com/news/research-suggests-epic-sepsis-model-lacking-predictive-power) [search]
- At a threshold inside the recommended range: sensitivity 33%, specificity 83%, PPV 12%, NPV 95%. It alerted on 18% of all hospitalised patients and missed 67% of sepsis patients. The developer replied that thresholds must be set locally. An editorial called for external validation before widespread use. — [HealthDay](https://www.healthday.com/healthpro-news/infectious-disease/epic-sepsis-model-has-poor-discrimination-of-sepsis-2653467079.html); [Contagion Live](https://www.contagionlive.com/view/evaluation-of-sepsis-prediction-tool-raises-questions-about-proprietary-tools) [search]

**Drift (Michigan IHPI, JAMA Network Open 2021)**
- In 24 hospitals across four health systems, sepsis alerts spiked at COVID onset even though census fell. The authors attribute this to dataset shift, but accuracy was not measured. — [Michigan IHPI news](https://ihpi.umich.edu/news/study-24-us-hospitals-shows-onset-covid-19-led-spike-sepsis-alerts) (DOI 10.1001/jamanetworkopen.2021.35286) [search]
- A longitudinal multi-hospital study found calibration shift for delirium and sepsis models, but no loss of clinical utility on decision curve analysis. Calibration drift does not always mean utility drift. — [PMC11681292](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11681292/) [search]
- A VA CAN-score preprint (2023) found performance drift around COVID-era shifts. — [medRxiv 2023.03.26.23286718](https://medrxiv.org/content/10.1101/2023.03.26.23286718v2.full-text) [search; journal status unverified]

**Deployed models that changed outcomes**
- Penn (Manz, Parikh, Small et al., JAMA Oncology; long-term results Jan 2023): a stepped-wedge cluster RCT of an ML 6-month mortality model plus clinician nudges. 20,506 patients and more than 40,000 encounters. Serious-illness conversations in high-risk patients rose from 3.4% to 13.5%. Chemo or targeted therapy in the last 2 weeks of life fell from 10.4% to 7.5%. There was no effect on hospice enrolment, inpatient death or end-of-life ICU use. — [Penn Medicine news](https://www.pennmedicine.org/news/machine-learning-improves-end-of-life-care-for-cancer-patients); [Penn PC3I](https://pc3i.upenn.edu/nudges-plus-machine-learning-triple-patient-provider-advanced-care-conversations/) [search]
- Kaiser Permanente Advance Alert Monitor (Escobar et al., NEJM, Nov 2020; outside the date window but a reference standard): staggered deployment across 19 hospitals, 15,487 alerted patients against 28,462 comparison patients. Adjusted RR of 30-day mortality 0.84 (95% CI 0.78-0.90), 3.8 percentage points absolute. ICU admission 17.7% vs 20.9%. LOS 6.7 vs 7.5 days (another summary gives 6.5 vs 7.2 among survivors). The study is observational. A "20% mortality reduction" figure in trade press does not match the paper. — [Kaiser Permanente press release](https://about.kaiserpermanente.org/news/press-release-archive/real-time-alerts-associated-with-lower-mortality); [EurekAlert](https://www.eurekalert.org/news-releases/857927) [search]

**Net benefit / decision curve methodology**
- Vickers, Van Calster and Steyerberg (BMJ 2016 primer): net benefit puts benefits and harms on one scale using a threshold "exchange rate". The decision curve plots net benefit across thresholds against treat-all and treat-none. — [MSK Synapse record](https://synapse.mskcc.org/synapse/works/90307) [search]
- Vickers et al. 2023 (Diagnostic and Prognostic Research): significance testing of net-benefit differences is "generally harmful". Value-of-information or probability-of-benefit is recommended instead. — [MSK Synapse record](https://synapse.mskcc.org/synapse/works/216480) [search]
- Reporting guide (Van Calster et al., European Urology 2018). — [MSK Synapse record](https://synapse.mskcc.org/synapse/works/134946) [search]

**Health-system governance (Stanford, 2024)**
- Stanford Health Care's FURM (Fair, Useful, Reliable Models) assessment is required for every AI system proposed for deployment. Stage 1 covers usefulness, financial projection and ethics. Stage 2 covers implementation. Stage 3 covers observed impact. The APLUS simulation runs virtual patients through the workflow using the model's sensitivity, specificity and PPV to estimate achievable utility. This captures capacity-limited benefit, which a raw decision curve does not. — [arXiv 2403.07911](https://arxiv.org/pdf/2403.07911v2.pdf); [Stanford AIMI](https://aimi.stanford.edu/research/focal-areas/fairness-and-usefulness); [Becker's](https://www.beckershospitalreview.com/ai/stanford-wields-a-furm-test-on-ai/) [search]

**Fairness: label choice in plan care-management targeting (Obermeyer, Powers, Vogeli, Mullainathan; Science 2019, before the window but foundational for health plans)**
- A commercial risk algorithm used past cost as the proxy label for need. At equal scores, Black patients were sicker. Removing the bias would raise Black patients' auto-identification for extra care from 17.7% to 46.5%. Retraining on cost plus health measures cut the disparity by 84%. — [Scientific American](https://scientificamerican.com/article/racial-bias-found-in-a-major-health-care-risk-algorithm/); [EurekAlert](https://www.eurekalert.org/news-releases/877184) [search]

**Recommendations (Q2)**
| Idea | Buyer question | Data needed | Maturity | Recommendation |
|---|---|---|---|---|
| Local external-validation + decision-curve report for any predictor before activation (net benefit vs treat-all/none, vs LACE-style baseline) | Quality/analytics: "Does this score help at our threshold and capacity?" | Local outcomes + model scores | Established method | **Trial.** It is cheap, deterministic and directly enforces Agere's "beat a simple baseline" rule. The Michigan sepsis gap (0.63 vs 0.76-0.83) is the motivating case. |
| Capacity-constrained utility simulation (FURM/APLUS style) | CFO/operations: "With N care managers, what is the realised benefit?" | Model operating characteristics + workflow capacity + cost of action | Used in one health system | **Trial.** Reuses Agere's Monte Carlo/queueing. |
| Drift monitor: alert-rate, calibration and net-benefit over time, with event flags such as case-mix shocks | Analytics/quality | Score logs + outcomes | Established | **Trial**, scoped to utility drift (the PMC11681292 finding), not calibration alone. |
| Label-choice audit (cost vs need proxies) for plan targeting | Health plan quality/equity | Claims cost + clinical burden measures + demographics | Foundational, widely replicated | **Trial** as a governance check on any plan risk list. |
| New deterioration/sepsis predictor | Quality | Real-time EHR | Mixed evidence | **Skip.** It needs real-time clinical integration and clinical qualification outside Agere's scope. |

### Inferences
- The two credible "model changed outcomes" studies (Penn, Kaiser) each tie the model to a defined human action: a nudge to an oncologist, or a nurse-to-rapid-response pathway. Outcome evidence attaches to model plus workflow, not to the model alone. Agere should qualify workflows, not models.
- Comparing an LLM alone was not addressed in any of these studies.

### Gaps
- Primary JAMA Internal Medicine, NEJM and JAMA Oncology pages were not opened. Figures come from news and press summaries that agree with each other.
- I did not find 2024-2026 peer-reviewed RCTs of operational predictors (discharge, no-show) with outcome effects.
- TRIPOD+AI (2024) was not checked in this pass.

## Q3. Causal inference for health policy and program evaluation: target trial emulation, synthetic control/SDID, staggered DiD, double ML; what simple alternatives they beat

### Takeaway
The clearest MIT-credited evidence is Finkelstein et al.'s Camden "hotspotting" RCT (NEJM 2020; MIT Economics, MIT Sloan, J-PAL North America). Pre-post comparison suggested a 38-point drop in admissions. The randomised control group fell just as much, and 180-day readmissions were 62.3% vs 61.7%. That is direct proof that pre-post evaluation of care-management programs is biased by regression to the mean. Modern estimators address the related failures: target trial emulation for time-zero, eligibility and immortal-time errors; synthetic DiD and Callaway-Sant'Anna for non-parallel trends and staggered-adoption bias in two-way fixed effects.

### Cited Findings
**MIT: Health Care Hotspotting RCT (Finkelstein, Zhou, Taubman, Doyle; NEJM 2020;382:152-162)**
- 800 hospitalised patients with complex needs and at least one other admission in the prior 6 months, randomised to the program or usual care (2014-2017). 180-day readmission was 62.3% (intervention) vs 61.7% (control). Adjusted difference 0.82 percentage points (95% CI -5.97 to 7.61). — [MIT News, Jan 2020](https://news.mit.edu/2020/health-care-hotspotting-no-effect-0108) [opened]; [ChristianaCare research summary](https://research.christianacare.org/publications/2020/01/16/health-care-hotspotting-a-randomized-controlled-trial/) [search]
- Pre-post bias: within-group before/after comparison suggested about a 38-percentage-point decline (earlier reports said about 40%). The control group showed "the exact same pattern", because patients enrol at a time of unusually poor health or high cost. — [MIT News](https://news.mit.edu/2020/health-care-hotspotting-no-effect-0108) [opened]
- Limits: single site; not powered for smaller effects or subgroups. — [EurekAlert](https://www.eurekalert.org/news-releases/780501) [search]

**Harvard CAUSALab: target trial emulation**
- Hernán, Wang and Leaf, "Target Trial Emulation: A Framework for Causal Inference From Observational Data", JAMA 2022;328(24):2446-2447. — search summary [search; no page opened]
- TARGET reporting guideline (Cashin, Hansford, Hernán et al.), JAMA 2025;334(12):1084-1093, doi 10.1001/jama.2025.13350. It is a 21-item checklist in six sections for observational studies that emulate a target trial. A 2023 review found only 22% of such studies used a reporting guideline. — [Peer Review Congress abstract](https://peerreviewcongress.org/abstract/transparent-reporting-of-observational-studies-emulating-a-target-trial-the-target-guideline/); [TARGET site](https://target.neura.edu.au) [search]

**Stanford: synthetic difference-in-differences**
- Arkhangelsky, Athey, Hirshberg, Imbens and Wager, "Synthetic Difference-in-Differences", AER 111(12):4088-4118 (2021). It applies unit and time weights to DiD and is doubly robust: consistent under parallel trends or a latent factor model with penalised SC weights. It "performs well in settings where conventional estimators are commonly used". The Prop 99 tobacco application gives an ATT of about -15.6 packs per capita. — [AEA record](https://www.aeaweb.org/doi/10.1257/aer.20190159); [arXiv 1812.09970](https://arxiv.org/abs/1812.09970); [mlsynth replication](https://mlsynth.readthedocs.io/en/latest/replications/sdid.html) [search]

**Staggered DiD (Callaway-Sant'Anna and successors)**
- Practitioner's guide (Baker, Callaway, Cunningham, Goodman-Bacon, Sant'Anna; arXiv 2503.13323, 2025). Under staggered timing, TWFE makes "unintuitive comparisons", using already-treated units as controls. Callaway-Sant'Anna estimates cohort-by-time ATT(g,t) and aggregates them. The Goodman-Bacon decomposition is the diagnostic. The running example is Medicaid expansion and mortality. — [arXiv 2503.13323](https://arxiv.org/html/2503.13323v3) [search]
- A 2025 comparison of staggered-DiD estimators (CS, interaction-weighted, two-stage DiD, Mundlack) applies them to a real-world payment program. — [arXiv 2508.14365](https://arxiv.org/html/2508.14365v1) [search]
- 2026 preprints: a fixed-effects causal forest for staggered adoption (Medicaid expansion), and higher-order parallel trends. — [arXiv 2607.19644](https://arxiv.org/pdf/2607.19644); [arXiv 2606.17977](https://arxiv.org/pdf/2606.17977) [search; preprints]

**What simple alternative do they beat, and how**
- Pre-post (no comparison): fails through regression to the mean when enrolment is triggered by high use. In Camden, a spurious ~38-point "effect" against a true ~0. — [MIT News](https://news.mit.edu/2020/health-care-hotspotting-no-effect-0108) [opened]
- Pre-post with a matched comparison (classic DiD): fails when trends differ. SDID re-weights controls and pre-periods so parallel trends is approximately true. — [AEA](https://www.aeaweb.org/doi/10.1257/aer.20190159) [search]
- TWFE with staggered rollout (typical for ACO, VBC or program waves): biased by forbidden comparisons. Callaway-Sant'Anna avoids them. — [arXiv 2503.13323](https://arxiv.org/html/2503.13323v3) [search]
- Naive observational cohorts: fail on misaligned time-zero and immortal time. Target trial emulation fixes these through protocol specification. — [TARGET abstract](https://peerreviewcongress.org/abstract/transparent-reporting-of-observational-studies-emulating-a-target-trial-the-target-guideline/) [search]

**Recommendations (Q3)**
| Idea | Buyer question | Data needed | Maturity | Recommendation |
|---|---|---|---|---|
| Regression-to-the-mean guard: every program evaluation shows a pre-post estimate next to an eligible-but-not-enrolled comparison (the Camden lesson) | CFO/VBC: "Did our care-management program actually save money?" | Claims/utilisation panels, eligibility and enrolment dates | RCT-validated failure mode | **Trial (high priority).** Cheap, explainable, directly beats the pre-post baseline buyers use now. |
| Staggered DiD (Callaway-Sant'Anna) for wave rollouts of programs, ACO cohorts or command-centre units | VBC/quality/operations | Unit-period panel with adoption dates | Mature, with open-source implementations | **Trial.** Complements Agere's causal forests/DML; show TWFE vs CS side by side. |
| Synthetic DiD for single-site or few-unit interventions (one hospital's command centre, one plan's policy change) | Operations/CFO | Panel of donor units (other hospitals, regions, plan segments) | Mature (AER 2021) | **Trial.** Fits the "evaluate the command-centre claim" use in Q1. |
| Target-trial-emulation protocol template (TARGET 21 items) bound to the decision context | Analytics/quality | Longitudinal EHR or claims | Reporting guideline (JAMA 2025) | **Trial** as a protocol/reporting layer, not a new estimator. |
| Fixed-effects causal forest for staggered adoption | Analytics | Panel | 2026 preprint | **Park.** |
| Policy learning (who to enrol) | VBC | RCT or strong-ignorability data | Not researched in this pass | **Park.** Camden shows targeting needs a credible effect estimate first. |

### Inferences
- The causal estimators Agere already has (causal forests, DML) assume no unmeasured confounding at a time point. Care-management and VBC programs are mostly staggered panel rollouts, so CS-DiD and SDID cover a distinct, common buyer situation and do not duplicate existing capability.
- Camden is the most persuasive buyer-facing example because it is MIT-led, randomised and quantifies the size of the pre-post error. It answers "does this beat the simple method?" with real numbers.

### Gaps
- Harvard CAUSALab's own pages and a policy or care-management application of target trial emulation were not opened. Only the guideline was found.
- I found no peer-reviewed Medicare ACO or VBC evaluation using Callaway-Sant'Anna in this pass, and no head-to-head showing SDID beating matched pre-post on a care-management program.
- Double ML for policy evaluation and policy learning (e.g. Athey-Wager) were not researched, because the tool-call budget ran out.
- MIT-specific causal work beyond Camden (e.g. J-PAL health care delivery RCTs, Doyle's hospital-quality work) was not searched.
