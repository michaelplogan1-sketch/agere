"""Build original research annotations and a normalized CMS inventory.

No patient data, model training, paid APIs, or changes to the live registry.
Annotations are an analyst synthesis, not a systematic review or clinical rules.
"""
import collections
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AS_OF = '2026-10-08'

def save(name, value):
    (ROOT/name).write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')

# ID | scope actually inspected | design/authority | supported lesson | limitation
NOTES = '''ORG01|Press release body|Company-reported MSSP performance|Primary-care enablement needs clinical data, contract accounting and practice implementation support.|Benchmark savings and company estimates of avoided events are not independent causal estimates of the software effect.
ORG02|Public case-study landing page; gated report not obtained|Vendor case study|Claims, EHR, ADT, lab and HIE integration can support locating transition and care gaps.|No independently evaluated attribution of reported improvement to software; underlying case report gated.
ORG03|Company analysis and methods description|Company observational comparison|Cohort maturity, risk adjustment and comparator construction must be explicit when evaluating an enablement program.|A randomly sampled comparison population is not random assignment to the intervention; analysis identified as not peer reviewed.
ORG04|Results announcement body|Company-reported preliminary performance|Hospital-led and rural ACOs need separate financial and operational views.|Selective benchmarks and reported gross savings do not establish causal patient benefit or net organizational profit.
ORG05|Historical release results and care-model description|Company impact report announcement, 2022|Prevention, access, behavioral health and delivery investment belong in the mechanism map.|Historical company comparisons with Medicare benchmarks are not current company performance or randomized evidence.
ORG06|Case-study results, care-model and technology sections|Vendor/provider case study|Kidney pathways require transplant, dialysis preparation, education and outside-provider evidence.|Single-practice promotional example cannot establish the effect of analytics independently of staffing and other changes.
PAY01|Press release including comparator definitions|Payer observational report|Keep within-plan VBC comparisons separate from comparisons against Original Medicare.|Population, benefit and selection differences can explain part of observed utilization differences.
PAY02|Hospital and health-system partnership description|Payer program description|Contract types have different targets, incentives and delivery responsibilities.|Describes available arrangements, not the terms of a particular signed contract or causal outcomes.
PAY03|Historical public summary|Payer program analysis summary, 2016|Provider readiness and implementation support vary; do not assume identical intervention exposure.|Historical, payer-authored summary; not current contract specifications.
PAY04|Primary article abstract through web retrieval|Observational difference-in-differences, NEJM 2019|Longer follow-up can separate price changes, utilization changes and incentive payments in global-budget evaluation.|Quality comparisons and causal identification have different limitations; no universal effect size for other contracts.
PAY05|Administrative guide capitation/delegation and eligibility sections, printed pages 182-184|Payer administrative manual, 2026|Capitation, delegation, enrollment and benefit-effective dates are separate dimensions.|Public guide does not reveal a provider's negotiated rates, risk corridors or complete contract economics.
MED01|State financial-results release|State administrative descriptive analysis|Decompose spending by prices, access, utilization and enrollment before calling it waste.|Spending or margin changes alone do not identify intervention effects.
MED02|State quality-results release|State administrative descriptive analysis|Screening and referral improvement can coexist with unchanged treatment engagement.|Process improvement is not interchangeable with patient outcome improvement.
CMS01|Directory API contents and pagination comparison|Official model directory snapshot|Model identity and stage must be retained alongside source links and as-of date.|Directory metadata is not executable eligibility, all CMS programs, or all technical documentation.
CMS02|Abstract, selected methods and conclusions|CMS synthesis of evaluations of 21 Medicare models|Separate gross spending effects, payments and net effects; patient outcomes require their own evidence.|Models, periods and evaluation methods differ; the synthesis is historical, not the current entire portfolio.
CMS03|Executive-summary implementation and policy lessons|CMS State Innovation Models evaluation synthesis|Multi-payer alignment, data readiness and implementation timing influence feasibility.|Mixed state results and historical policy context cannot prescribe current eligibility or promise savings.
CMS04|Termination FAQ body|Official CMS explanation of evaluation findings|Separate risk-score and payment effects from social-needs intervention effects.|CMS states there was no evidence linking the cost increases to HRSN interventions; termination does not prove those interventions failed.
CMS05|Executive-summary design and findings, selected physical PDF pages 7-13|CMS final evaluation; randomized Assistance Track and nonrandomized Alignment Track|Navigation, utilization, net spending and survey-reported need resolution can move differently.|Do not merge the tracks' evidence strength or infer the mechanism solely from utilization effects.
CMS06|Official program-data listing through web search|CMS public data catalog|ACO results can be checked against official financial and quality public-use data.|This project identified the datasets; it did not independently recompute or reconcile each company's results.
CMS07|Public program overview|CMS program index|Inventory standing payment/quality programs separately from Innovation Center demonstrations.|Page contains historical program names; it is not a current measure specification library.
CMS08|JavaScript shell only|Not substantively reviewed|No substantive finding assigned.|Requires accessible current QPP documentation before training or rule compilation.
FOUND01|Harvard VBHCD initiative description|Porter/Kaplan conceptual framework|Connect outcomes that matter to patients with resources consumed over the full care cycle.|A framework is not experimental evidence that every implementation reduces cost.
FOUND02|Harvard healthcare strategy overview|Conceptual framework|Organize analysis around patient value and care delivery rather than reimbursement totals alone.|Claims spending, prices and provider resource costs are different constructs.
FOUND03|Unusable retrieved page|Not substantively reviewed|No additional finding assigned.|Alternative Porter papers identified but not treated as fully reviewed here.
EVAL01|Primary randomized-trial abstract via PubMed/PMC search|Randomized controlled trial, Camden, 2020|High prior utilization can fall without an intervention effect; include regression-to-the-mean counterexamples.|Null result concerns the studied program and population, not all care management.
EVAL02|Primary article key findings and discussion|Observational difference-in-differences, JAMA 2025|MSSP savings may develop over time and differ by organizational structure; test attribution sensitivity.|Selection, changing attribution and modeling choices affect estimates; not a randomized software trial.
EVAL03|Discovery snippet only|Not substantively reviewed|No additional finding assigned.|Do not use the paper's title alone as a training label about mechanisms of savings.
EVAL04|Trial methods, results and limitations excerpts|Randomized trial, JAMA Network Open 2021|A community-health-worker intervention had a favorable overall readmission result in its studied setting.|Rehabilitation subgroup was post hoc; recruitment stopped short of target; deaths and withdrawals were excluded from final analysis. Do not transport effect blindly.
QI01|AHRQ report introduction and measurement excerpts|National quality-improvement implementation report|Use exposure denominators and implementation context when evaluating infection prevention.|Not a randomized comparison; detailed result tables not reanalyzed.
QI02|AHRQ RED monitoring-tool measure definitions and outcome section|Implementation and measurement guidance|Specify target population, intervention reach, follow-through and balancing outcomes.|A completed discharge checklist alone is not proof of reduced readmission.
QI03|IHI measurement guidance|Quality-improvement methodology|Pair outcome measures with process and balancing measures and stratify for inequity.|A control chart detects change; it does not alone prove the cause of change.
QI04|Primary abstract and conflicts-of-interest declaration|Multicenter prospective implementation, before-after|Structured handoff content and implementation fidelity can be measured across settings.|Reported harms and nonrandomized design limit attribution; disclosed investigator financial interests must remain visible.
INT01|Official NHS case-study text returned in search|Implementation case study, England|Linked primary, hospital and social-care data enables population-level pathway questions.|Describes infrastructure and practice; not causal evidence that linkage itself improves outcomes.
INT02|Ministry response defining intended indicators|Singapore policy design, 2022|Distinguish early implementation indicators from later disease and utilization outcomes.|Historical intended evaluation framework, not mature effectiveness findings.
INT03|OECD PaRIS executive-summary body|International patient/practice survey, 2025|Patient-reported function, experience, continuity and multimorbidity complement administrative measures.|Associations and country averages do not identify causal country-model superiority.
INT04|Primary abstract via web search|Germany synthetic-control evaluation, 2021|Large disease-management enrollment does not guarantee detectable population mortality benefit.|Wide confidence intervals did not exclude benefit; not proof the programs never help individuals.
INT05|Source discovery only; local response unusable|Not substantively reviewed|No substantive finding assigned.|Thailand/UHC source remains a coverage gap; do not treat a failed PDF as a reviewed paper.
INT06|Discovery and page outline only|Not substantively reviewed|No additional finding assigned.|Ten-country survey identified for follow-up; not included in substantive findings here.
INT07|Primary abstract via web retrieval|Brazil longitudinal municipal observational study|Primary-care coverage and local governance should be considered jointly.|Municipal association is not an individual treatment effect or evidence of US transferability.
INT08|Primary abstract via web retrieval|Netherlands difference-in-differences study, 2021|Disease-specific bundled payment can increase overall spending, particularly with multimorbidity.|One setting and period; does not establish that all bundled payment fails.
INT09|Primary abstract via web search|Early Netherlands bundled-payment evaluation, 2012|Better coordination/processes can precede uncertain cost and outcome findings.|Early study cannot establish long-term financial success.
INT10|Public digital-infrastructure article|Denmark public/private promotional description, 2026|Shared infrastructure, governance and citizen access are organizational design inputs.|Promotional infrastructure account is not an independent outcomes evaluation.
INT11|Award listing and introductory description only|Singapore ministry award announcement|Useful lead to local integrated primary-care implementation examples.|Award selection is not effectiveness evidence; detailed outcome claims not used.
ENG01|Multiwindow alerting sections|Google SRE engineering guidance|Combine rapid and persistent deterioration signals to reduce noisy data-quality alerts.|Transfer to feed reliability and alert operations, not a permissible patient-harm budget.
ENG02|NIST process-control guidance|Statistical engineering reference|Use baseline variation, control charts and investigation rather than ranking every fluctuation.|Clinical applications require stable definitions, adequate denominators and risk adjustment.
ENG03|Repository overview and license, including prior engineering review|Process-mining software documentation|Event logs can reveal bottlenecks, pathway variation and missing transitions.|Process sequence is not causal evidence; review AGPL/commercial licensing before integration.
ENG04|Motivation and causal-inference documentation, including prior review|Causal ML documentation|Treatment-effect estimation targets a different question from outcome-risk prediction.|Requires defensible treatment assignment, overlap and confounding assumptions; cannot manufacture causal evidence.
ENG05|NASA dataset-repository descriptions|Engineering benchmark catalog|Borrow temporal evaluation, degradation detection and independently measured failure labels.|Engineering failure labels do not train clinical judgments or define a person's worth.
ENG06|Scheduling problem description|OR-Tools constraint-optimization documentation|Test capacity, skills and preference constraints before presenting a feasible allocation.|A mathematically optimal allocation is only as appropriate as its clinical objective and constraints.
'''

MODEL_LESSONS = {
1:'Social-needs screening, navigation and actual need resolution require separate evidence.',
3:'ACO REACH performance years and financial-methodology changes must remain versioned.',
10:'Historical CJR evidence and successor CJR-X requirements must not be merged.',
13:'Multi-payer primary-care investment requires payer alignment and implementation measurement.',
15:'Oncology episodes need symptom burden, treatment context and program-specific definitions.',
17:'Home-health quality requires functional outcomes and program-year measure versions.',
20:'Frontier hospitals require geographic and service-capability context.',
29:'Long-stay nursing-facility residents differ from short-stay rehabilitation populations.',
30:'Clinical support and payment changes are distinct intervention components.',
31:'Children need family, behavioral-health and community context rather than an adult-risk template.',
33:'Quality gains and net Medicare financial loss can coexist; options changed across years.',
35:'Historical Maryland TCOC and transition to AHEAD need distinct periods.',
41:'Ended VBID rules must not be recommended as a currently enrolling opportunity.',
42:'Supportive care and disease-directed care can coexist in a program design.',
53:'Historical ACO designs are evaluation evidence rather than currently available enrollment.',
64:'An ended performance period overrides older descriptions of intended duration.',
66:'Announced status does not supply a performance start date; the page describes a delayed model.',
67:'Rural viability and patient access are separate outcomes from short-term spending reduction.',
79:'All-payer alignment is a design feature; it does not erase different benefit and attribution rules.',
82:'Early termination must supersede the originally advertised multi-year duration.',
83:'Dementia analysis needs caregiver burden and supports as well as patient clinical state.',
84:'State/global-budget and geographic-accountability layers need distinct financial attribution.',
85:'Maternal health requires longitudinal pregnancy/postpartum and infant-linked measures.',
86:'Behavioral-health practices can be the entry point for whole-person care.',
89:'Outcome-based therapy agreements require long follow-up and treatment-access evidence.',
90:'Prospective primary-care payments and shared-savings reconciliation are different cash flows.',
91:'TEAM episode windows, exclusions and ACO overlap require explicit deterministic rules.',
92:'Transplant access needs referral, evaluation and waitlist transitions, not referral counts alone.',
95:'Prior authorization is an administrative process distinct from new clinical criteria.',
98:'Service appropriateness and clinical review must remain separate from a denial-rate objective.',
99:'Future specialist-model performance periods cannot be applied to current-year payment.',
100:'Directory stage and page launch language differ; reconcile against effective program documents.',
101:'Initial ACCESS tracks and later tracks need separate effective dates and qualifying evidence.',
102:'Funding opportunities and planned interventions are not proven effectiveness results.',
104:'Successor ACO design needs new benchmarking and participation versions.',
105:'Proposed manufacturer rebates are not finalized patient-level eligibility rules.',
106:'Directory Active status is not the same as an already-started performance period.',
107:'Medicaid, Part D and bridge availability have different timelines and eligibility scopes.',
108:'Pediatric program design requires family-level outcomes and state participation context.',
109:'CJR-X and TEAM have different episode durations and participant exclusions.'}

def build():
    manifest=json.loads((ROOT/'retrieval_manifest.json').read_text())
    annotations={}
    for line in NOTES.strip().splitlines():
        sid,scope,design,lesson,limitation=line.split('|')
        annotations[sid]=(scope,design,lesson,limitation)
    catalog=[]
    for r in manifest:
        sid=r['id']; model=sid.startswith('MODEL')
        if model:
            scope='Opening model overview and current-status notices; additional design/evaluation sections for priority models. Not a full technical-manual review.'
            design='Official program description; intended outcomes are not measured effects'
            lesson=MODEL_LESSONS[int(sid[5:])]
            limitation='Eligibility, financial formulas and effective dates require controlling rules, specifications and agreements before execution.'
        else:
            scope,design,lesson,limitation=annotations[sid]
        substantive=design!='Not substantively reviewed'
        local_usable=r.get('retrieval_status')=='retrieved' and r.get('text_characters',0)>=200
        url=r['url']
        if sid=='CMS06':url='https://data.cms.gov/medicare-shared-savings-program'
        if sid=='EVAL01':url='https://pubmed.ncbi.nlm.nih.gov/31914242/'
        catalog.append(dict(id=sid,title=r.get('title_from_page') or r['title'],url=url,
            retrieved_on=AS_OF,evidence_type=r['evidence_type'],study_design_or_authority=design,
            review_status='scoped_review' if substantive else 'discovered_not_substantively_reviewed',
            reviewed_scope=scope,original_synthesis=lesson,limitations=limitation,
            local_extraction_usable=local_usable,content_sha256=r.get('sha256'),
            publication_metadata=r.get('publication_metadata',[]),
            access_note=('No substantive review completed' if not substantive else ('Local extracted text inspected within stated scope' if local_usable else 'Web-rendered primary material/search abstract used within stated scope; local retrieval was blocked or unusable')),
            training_use='Source for retrieval and analyst-authored task design; not approved for raw-text weight training',
            authority_limit='Does not constitute a validated clinical decision rule'))
    save('evidence_catalog.json',catalog)
    raw=json.loads((ROOT/'cms_model_directory_raw.json').read_text())
    sources={r['id']:r for r in catalog}
    rows=[]
    for r in raw:
        sid='MODEL'+str(r['Unique ID'])
        rows.append(dict(id=sid,name=r['Model Name'],directory_stage=r['Stage'],category=r['Category'],
            directory_url=r['URL'],reviewed_url=sources.get(sid,{}).get('url'),
            date_began_raw=r['Date Began'],date_ended_raw=r['Date Ended'],
            review_scope='Overview reviewed' if sid in sources else 'Directory metadata only',
            analyst_learning=sources.get(sid,{}).get('original_synthesis'),
            rules_compiled=False,as_of=AS_OF))
    save('cms_inventory.json',rows)
    categories=collections.Counter()
    for r in raw:
        categories.update(c.strip() for c in r['Category'].split(','))
    save('mining_findings.json',dict(as_of=AS_OF,source='cms_model_directory_raw.json',
        rows=len(raw),unique_ids=len({r['Unique ID'] for r in raw}),
        blank_fields={k:sum(not str(r.get(k,'')).strip() for r in raw) for k in raw[0]},
        category_memberships=dict(categories),
        category_note='A model may have multiple categories; totals are not disjoint.',
        timing_examples=[
            dict(source_id='MODEL106',directory_stage='Active',page_observation='Page describes a future launch. Directory stage is not equivalent to a started performance period.'),
            dict(source_id='MODEL100',directory_stage='Announced',page_observation='Page describes a launched model. Resolve stage semantics and effective participation from controlling documents.'),
            dict(source_id='MODEL82',page_observation='Page includes early termination alongside older planned duration. Latest controlling notice must supersede historical plan.')],
        implications=['Do not infer effective dates from directory stage','Blank participant-impact fields are unknown, not zero','Preserve metadata versus operational rule differences','Resolve legacy URLs and record source versions']))
    with (ROOT/'cms_inventory.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    save('coverage_summary.json',dict(as_of=AS_OF,source_targets=len(catalog),
        scoped_reviews=sum(x['review_status']=='scoped_review' for x in catalog),
        discovery_only=[x['id'] for x in catalog if x['review_status']!='scoped_review'],
        cms_directory_entries=len(rows),cms_overviews_reviewed=sum(x['review_scope']=='Overview reviewed' for x in rows),
        cms_directory_stages=dict(collections.Counter(x['directory_stage'] for x in rows)),
        program_rules_compiled=0,systematic_review=False,models_trained=0))
    lines=['# Source-by-source evidence notes','',f'As of {AS_OF}. Scoped reading, not a systematic review. Source count is not an evidence-strength score.','']
    for x in catalog:
        lines.extend([f"## {x['id']}: {x['title']}",'',f"[Primary source]({x['url']})",'',
            f"**Read:** {x['reviewed_scope']}",f"**Evidence:** {x['study_design_or_authority']}",
            f"**Design lesson:** {x['original_synthesis']}",f"**Limit:** {x['limitations']}",''])
    (ROOT/'EVIDENCE_NOTES.md').write_text('\n'.join(lines))

if __name__=='__main__':build()
