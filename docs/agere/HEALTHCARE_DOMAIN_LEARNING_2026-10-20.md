# CMS, MA measures and population-health learning

This candidate extends the shared-savings/downside-risk learning workflow through the existing question, knowledge, planner, correction and learning interfaces. It adds 29 reviewed facts in ten entries of the existing knowledge corpus: GUIDE, ACO REACH, TEAM, ACCESS, CBP, diabetes medication adherence, Star Ratings data periods, incidence/prevalence, PQI 90 and risk-adjusted comparisons.

Questions automatically accumulate domain and unmet-need counts in the existing learning store. In **Flag as wrong**, a user can select a cited fact. The server checks its exact value and type, entity, applicable year, reference version and source digest. Only that bounded fact becomes reusable; the correction explanation stays unverified feedback. Changed wording receives the lesson through the shared interpreter and the existing knowledge engine. Later conflicting feedback suspends the lesson and preserves a recheck obligation. Source changes, linked measure-definition changes and source withdrawal invalidate reuse. History is append-only. There is no automatic model-weight training, external-source refresh, prompt promotion or clinical approval.

The old economics API and existing financial rules remain available. The combined selector uses `/api/stack/healthcare-learning`; `/api/stack/learning` adds `healthcare_domains`. Knowledge answers expose source scope and correction reuse in their existing report. New definitions and rules participate in planner/cache identity. Measure-library caching now checks content rather than modification time and returns defensive copies.

## Source scope

The new facts were reviewed against official CMS, CDC and AHRQ sources on October 20, 2026. Each entry records the source URL, inspected section, review date and applicability. Exact-match verification at correction time checks this reviewed local record; it is not a fresh independent review of the live website. Only explicitly opted-in reviewed fields are learnable; older corpus entries and internal measure approximations are not automatically certified.

CMS model facts are bounded to performance year 2026. MA facts use the September 30, 2026 edition of the 2027 technical notes, with measurement year 2025 specified for CBP and diabetes PDC. A bare MA year is ambiguous and cannot qualify a correction. Methodological population-health references bind to their reference version, separately from patient data dates. Questions with uncovered or conflicting years request clarification instead of substituting another edition.

- [CMS GUIDE](https://www.cms.gov/priorities/innovation/innovation-models/guide), [ACO REACH](https://www.cms.gov/priorities/innovation/innovation-models/aco-reach), [TEAM](https://www.cms.gov/priorities/innovation/innovation-models/team-model), [ACCESS](https://www.cms.gov/priorities/innovation/innovation-models/access).
- [CMS 2027 Star Ratings technical notes](https://www.cms.gov/files/document/2027-tech-notes-2026-09-30.pdf): C14 pp.54–55, D08 pp.101–103 and data-period/weight attachments.
- [CDC epidemiologic methods](https://www.cdc.gov/field-epi-manual/php/chapters/describing-epi-data.html), [AHRQ PQI 90 v2025](https://qualityindicators.ahrq.gov/Downloads/Modules/PQI/V2025/TechSpecs/PQI_90_Prevention_Quality_Overall_Composite.pdf), [AHRQ empirical methods v2025](https://qualityindicators.ahrq.gov/Downloads/Resources/Publications/2025/Empirical_Methods_2025.pdf).

Public specification knowledge is distinct from operational scoring. Agere's CBP and PDC engines retain simplified code sets and incomplete exclusions; this change does not certify HEDIS or calculate CMS contract Stars. Official geographic PQI denominators differ from the existing enrolled member-year adaptation. Comparative rates and model design do not establish causal benefit, real payment entitlement or beneficiary eligibility.

## Evidence and integration

`artifacts/agere-domain-learning-v1/` retains test output, the real HTTP correction lifecycle on authored temporary data, JavaScript syntax checks and source review metadata. The fixture replaces Stack construction and language inference only; API routes, correction storage, verification, question-failure capture and knowledge retrieval execute normally. Actual hosted interpretation, browser workflow and activation remain the integration owner's checks.

The focused suite passes 141 tests, including 19 new domain regressions. Three API workflows check source-verified correction, differently worded reuse, forged-value rejection, suspension and revalidation; unsupported MA editions accumulate a question gap. Existing financial learning stays covered. Initial failures are retained: an isolated-corpus retrieval miss for the full PQI name (fixed by adding the canonical name to its title), and sandbox loopback binding (verification rerun through approved escalation).

Test audit: the first broad invocation also ran the existing `test_vbc_knowledge` module, including source-embedded paraphrase tests and its tracked benchmark-routing scan. No untracked protected evaluation corpus was copied into this checkout. Those retrieval scores are not used as qualification evidence; subsequent runs explicitly exclude the module. No generator/research files or source warehouse were changed. Candidate development and fixture verification made zero hosted calls.

G3 remains active and G5 blocked; full ten-area acceptance remains false. This increment closes scoped domain-learning wiring, not the broader clinical/time interpretation, certified scoring, automatic source maintenance or independent qualification gaps. The single owner retains primary controls, runtime, website integration and activation.
