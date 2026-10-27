# Specialty guidelines in Agere source learning — October27,2026

Mike's example is the **2025 AHA/ACC adult high-blood-pressure guideline**, DOI `10.1161/CIR.0000000000001356`. The publisher hub and three correction records were checked. Direct publisher PDF access returned403; the correction texts have not been reviewed. No new blood-pressure thresholds were extracted or installed.

The explicit candidate library now contains **68 resources**: the existing46 academic resources and22 specialty-guidance records. Four new records contain **eight selected recommendation records**; the other18 retain publisher metadata and reading objectives. This is partial source review, not a claim that every guideline was read or that model weights learned its contents.

## Sources and actual review scope

| Source | Edition / status / type | What was reviewed |
| --- | --- | --- |
| [2025 AHA/ACC adult high blood pressure guideline](https://professional.heart.org/en/science-news/2025-high-blood-pressure-guideline) | 2025; final; guideline | publisher metadata; 0 recommendation records |
| [2022 AHA/ACC/HFSA heart failure guideline](https://professional.heart.org/en/science-news/2022-guideline-for-the-management-of-heart-failure) | 2022; final; guideline | publisher metadata; 0 recommendation records |
| [2024 ACC HFrEF treatment expert consensus pathway](https://www.acc.org/Guidelines/Guidelines/2024/03/08/15/40/Treatment-of-Heart-Failure-With-Reduced-Ejection-Fraction-ECDP) | 2024; final; consensus | publisher metadata; 0 recommendation records |
| [2026 ACC/AHA dyslipidemia guideline](https://professional.heart.org/en/guidelines-statements/2026-accahaaacvprabcacpmadaagsaphaaspcnlapcna-guideline-on-the-management-ofcir0000000000001423) | 2026; final; guideline | publisher metadata; 0 recommendation records |
| [2023 AHA/ACC chronic coronary disease guideline](https://professional.heart.org/en/guidelines-statements/2023-ahaaccaccpaspcnlapcna-guideline-for-the-management-of-patients-withcir0000000000001168) | 2023; final; guideline | publisher metadata; 0 recommendation records |
| [2026 AHA/ACC/ADA/ASN cardiovascular-kidney-metabolic syndrome guideline](https://professional.heart.org/en/science-news/2026-guideline-for-the-prevention-detection-evaluation-and-management-of-ckm-syndrome) | 2026; final; guideline | publisher metadata; 0 recommendation records |
| [ADA Standards of Care in Diabetes 2026](https://professional.diabetes.org/standards-of-care) | 2026; final; standards | publisher metadata; 0 recommendation records |
| [ADA 2026 chapter9 pharmacologic approaches to glycemic treatment](https://diabetesjournals.org/care/article/49/Supplement_1/S183/163934/9-Pharmacologic-Approaches-to-Glycemic-Treatment) | 2026 chapter9; final; standards | indexed recommendation extract; 1 recommendation records |
| [Endocrine Society primary aldosteronism guideline 2025](https://www.endocrine.org/clinical-practice-guidelines/primary-aldosteronism-2) | 2025; final; guideline | selected recommendations; 2 recommendation records |
| [Endocrine Society high-risk hypoglycemia guideline](https://www.endocrine.org/clinical-practice-guidelines/high-risk-for-hypoglycemia) | Online December2022; JCEM March2023; final; guideline | publisher metadata; 0 recommendation records |
| [AACE obesity/adiposity-based chronic disease algorithm 2025](https://pro.aace.com/clinical-guidance/aace-consensus-statement-algorithm-evaluation-and-treatment-adults) | 2025; final; consensus | publisher metadata; 0 recommendation records |
| [GOLD COPD strategy report and pocket guide 2026](https://goldcopd.org/2026-gold-report-and-pocket-guide/) | 2026; final; strategy report | publisher metadata; 0 recommendation records |
| [GINA asthma strategy report 2026](https://ginasthma.org/2026-gina-strategy-report/) | 2026; final; strategy report | publisher metadata; 0 recommendation records |
| [ATS pharmacologic management of COPD guideline 2020](https://www.thoracic.org/statements/guideline-implementation-tools/pharmacologic-mgmt-of-copd.php) | 2020; final; guideline | publisher metadata; 0 recommendation records |
| [ATS pulmonary rehabilitation guideline 2023](https://site.thoracic.org/about-us/news/pulmonary-rehabilitation-earns-strong-recommendation-in-new-clinical-practice-guideline) | 2023; final; guideline | selected recommendations; 2 recommendation records |
| [KDIGO CKD evaluation and management guideline 2024](https://kdigo.org/guidelines/ckd-evaluation-and-management/) | 2024; final; guideline | publisher metadata; 0 recommendation records |
| [KDIGO CKD medications focused update 2027 public-review draft](https://kdigo.org/guidelines/ckd-evaluation-and-management/) | 2027 public-review draft; draft; guideline | publisher metadata; 0 recommendation records |
| [KDIGO diabetes in CKD guideline 2022](https://kdigo.org/guidelines/diabetes-ckd/) | 2022; final; guideline | publisher metadata; 0 recommendation records |
| [KDIGO diabetes in CKD guideline 2026 public-review draft](https://kdigo.org/guidelines/diabetes-ckd/) | 2026 public-review draft; draft; guideline | publisher metadata; 0 recommendation records |
| [NICE NG222 adult depression guideline](https://www.nice.org.uk/guidance/ng222) | 2022; last reviewed September2024; final; guideline | publisher metadata; 0 recommendation records |
| [ASCO geriatric assessment in oncology guideline update 2023](https://www.asco.org/news-initiatives/current-initiatives/cancer-care-initiatives/geriatric-oncology/resources) | 2023 update; final; guideline | publisher metadata; 0 recommendation records |
| [USPSTF colorectal cancer screening final recommendation 2021](https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/colorectal-cancer-screening) | 2021; final; recommendation statement | selected recommendations; 3 recommendation records |

The ADA standards hub provides the chapter route for population/access factors, prediabetes, hypoglycemia, obesity, pharmacotherapy, cardiovascular and kidney care. The existing academic library retains Harvard/Porter value-based care, chronic-disease teaching, depression and social context. The ASCO record covers geriatric assessment, not every cancer's treatment recommendations. Disease-specific oncology and other specialist guideline review remain further work.

## Findings that change how the stack should reason

The Endocrine Society's [2025 primary-aldosteronism recommendations1 and3](https://www.endocrine.org/clinical-practice-guidelines/primary-aldosteronism-2) suggest broader screening across hypertension. The strength is **conditional**, with **low certainty**, and implementation depends on resources and expertise. Potassium helps interpret aldosterone; it is not itself the screening test. Source scope and feasibility must survive simplification into an analytics task.

The ADA's [2026 recommendation9.8](https://diabetesjournals.org/care/article/49/Supplement_1/S183/163934/9-Pharmacologic-Approaches-to-Glycemic-Treatment) includes an HF-related reason to consider an SGLT2 inhibitor in adults with type2 diabetes, regardless of A1C; gradeA. Only the indexed primary-source extract was reviewed. This supports a review question even when A1C is at goal; it does not establish medication suitability for a specific patient without safety and clinical context.

The ATS [pulmonary-rehabilitation summary](https://site.thoracic.org/about-us/news/pulmonary-rehabilitation-earns-strong-recommendation-in-new-clinical-practice-guideline) gives strong recommendations with moderate-certainty evidence for stable COPD and after COPD-exacerbation hospitalization. Referral, actual participation and service access remain separate evidence. The [USPSTF colorectal statement](https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/colorectal-cancer-screening) assigns different grades across age groups; ages76–85 require selective consideration, including prior screening, health and preference. A blank field is not a documented reason or an automatic inappropriate-care finding.

KDIGO's [diabetes/CKD page](https://kdigo.org/guidelines/diabetes-ckd/) still names2022 as the current published edition and2026 as a draft under revision. Its [CKD page](https://kdigo.org/guidelines/ckd-evaluation-and-management/) lists the final2024 guideline alongside a2027 medication-focused public-review draft, open through November4,2026. A future edition number is insufficient evidence of a finalized replacement. The AHA's [2026 CKM guideline](https://professional.heart.org/en/science-news/2026-guideline-for-the-prevention-detection-evaluation-and-management-of-ckm-syndrome) also illustrates why reading only older obesity material would miss current cross-condition guidance.

Three correction records linked to Mike's hypertension document are retained: [November2025](https://pubmed.ncbi.nlm.nih.gov/41212942/), [April2026](https://pubmed.ncbi.nlm.nih.gov/41973840/) and [May2026](https://pubmed.ncbi.nlm.nih.gov/42189957/). They are explicitly unresolved content reviews. No claim is made about which recommendation they changed.

## Connection to recursive learning

The existing `education_library` intake now validates edition, publisher, jurisdiction, population, document kind, final/draft status, actual reading depth, correction review and recommendation provenance. Source metadata is duplicated into the existing retriever's returned facts and checked for equality, so retrieval retains it. GRADE recommendation strength and certainty remain separate; ADA evidence letters, USPSTF recommendation grades and AHA/ACC class/level pairs cannot be relabeled as a universal grade.

Changing an edition or correction changes the existing corpus digest. The offline tests demonstrate that a subsequent lookup returns the updated provenance rather than the old cached entry. This provides the evidence identity needed for later task/replay qualification. No new retriever, disease handler, operational fact catalog or runtime service was introduced. Clinical rule approval remainsfalse, and the default knowledge directory remains unchanged.

The next learning cycle is to review the full relevant recommendation and corrections; propose original questions and source-grounded reasoning; retain missing patient information and exceptions; qualify the proposed supervision; then use the existing separated fit/calibration/verification and candidate-comparison machinery. The source summaries and questions here are Agere-authored and not physician-adjudicated clinical truth. They were not injected into the already-running analytics adapter study.

Examples of reviewable questions for that cycle, with clinically acceptable reasoning rather than exact decimal matching:

| Original question | What an acceptable explanation should preserve |
| --- | --- |
| Does normal potassium mean a hypertensive patient does not need consideration for PA screening? | The2025 suggestion is broader; retain its conditional strength, testing context and feasibility. Potassium alone does not settle screening. |
| A patient has type2 diabetes, HF and A1C at goal. Is there still a reason to review SGLT2 therapy? | Yes, there can be an HF-related reason independent of A1C; suitability still needs individual safety and treatment context. |
| Which COPD patients discharged after an exacerbation need a rehabilitation review? | Link the actual qualifying hospitalization to referral and participation evidence; preserve access barriers and clinical context. |
| Is every79-year-old without a recorded colonoscopy overdue for screening? | No automatic conclusion: average-risk scope, other accepted tests, prior screening, health and preferences matter. |
| Can a2026 draft automatically replace the published2022 diabetes/CKD recommendations? | No; distinguish a draft from the current published guideline and identify the needed final source. |
| Can Agere call a hypertension threshold current when it has found unread corrections? | Identify the edition and unresolved correction; review the relevant changed passages before claiming the specific threshold is current. |

These are proposed reading/qualification exercises, not a clinical accuracy benchmark or accepted training labels.

## Rights, verification and remaining work

Public access does not establish full-text commercial training permission. The [GINA page](https://ginasthma.org/2026-gina-strategy-report/) explicitly requires approval for commercial use; the [GOLD hub](https://goldcopd.org/2026-gold-report-and-pocket-guide/) marks teaching slides for noncommercial education. These resources remain link metadata here. ADA slide reuse also has commercial-use conditions. No full reports, slides, lecture transcripts or publisher answer sets were copied into model training. No permission request or external contact was sent.

All **nine focused offline checks passed**. All68 expected resource records retrieve with citations; all22 guideline-provenance objects survive in returned facts. No provider inference, blocked network attempt, protected question-corpus read, default model/runtime promotion or clinical-rule activation occurred. Typed/bottled training contract hashes are unchanged. The broader520-check receipt from the preceding source remains preserved; these nine checks cover this intake change and do not recertify the entire product.

Source manifest: `config/agere/learning/specialty-guidelines.json`. Integration: `tools/training/education_library.py`. Checks: `test_specialty_guidelines.py` and existing `test_education_library.py`. Local source/retrieval receipts: `artifacts/agere-guideline-learning-2026-10-27/verification.json` and `retrieval.json`. Corpus digest: `b0014cb76c995e5029f37e19c64013f3d9b1cdbfa3c86a823b71c6ec17a08681`.

The local typed/bottled two-round analytics study remains in progress, with no completed broad model-comparison claim. G3 and the full ten-area charter remain open. The ordinary automatic full-note, dose, final-answer and deliverable run still awaits Claude subscription availability; no provider retry, poll or heartbeat was created.
