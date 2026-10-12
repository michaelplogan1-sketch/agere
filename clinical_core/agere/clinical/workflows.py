"""Three explicit review workflows, not autonomous treatment recommendations."""
from ..operators import medication,labs,trend,MEDS
from .facts import eligible

VERSION='clinical-workflows/1'
NAMES={'heart_failure':'Heart failure treatment evidence review',
       'kidney':'Kidney trajectory and referral evidence review',
       'affordability':'Affordability evidence and assistance review'}


def _result(key,state,status,included,support,excluded,missing,actions,**extra):
    return {'workflow':key,'workflow_version':VERSION,'patient_id':state['patient_id'],
            'as_of':state['as_of'],'status':status,'review_candidate':included,
            'support_fact_ids':list(dict.fromkeys(support)),'excluded_fact_ids':list(dict.fromkeys(excluded)),
            'missing_information':missing,'proposed_actions':actions,
            'care_gap_established':False,'execution_authorized':False,**extra}


def heart_failure(state,records):
    ef=state['ef'];facts=state['facts'];cut=state['as_of']
    diagnosis=[f for f in facts if (f['concept'].startswith('diagnosis:I50') or f['concept']=='diagnosis:heart_failure') and eligible(f)]
    drug_facts=[f for f in facts if f['concept'].startswith('medication:')]
    medicines=[];needs=[]
    for drug in MEDS:
        supply=medication(records,drug,cut)
        text=[f for f in drug_facts if f['concept']=='medication:'+drug and f['method']=='context_rules' and eligible(f)]
        holds=[f for f in text if f.get('relationship')=='hold_or_exception_mention']
        use=[f for f in text if f.get('relationship')=='reported_use']
        order=[f for f in drug_facts if f['concept']=='medication:'+drug and f.get('relationship')=='ordered']
        observed='exception_needs_reconciliation' if holds else 'supply_documented' if supply['supplies'] else 'reported_use_needs_verification' if use else 'order_only' if order else 'not_observed'
        medicines.append({'drug_class':drug,'evidence_state':observed,'structured_supply':supply,
            'text_fact_ids':[f['fact_id'] for f in text],
            'dose_sources':[{'value':f['value'],'fact_id':f['fact_id']} for f in drug_facts if f['concept']=='medication:'+drug],
            'maximum_tolerated_dose':'unknown','actual_current_use':'unknown',
            'appropriate_to_start_or_titrate':'not_determined'})
        if observed!='supply_documented':needs.append(f'{drug}: reconcile actual use, exceptions, outside fills and current treatment intent')
    rng=ef.get('latest_range')
    if ef['status']=='conflicting_reports':status='resolve_ef_conflict';included=True
    elif rng and rng[1]<=40:status='low_ef_treatment_evidence_review';included=True
    elif rng and rng[0]>40:status='latest_ef_not_in_low_ef_screen';included=False
    elif diagnosis or ef['status']=='historical_only':status='current_phenotype_unresolved';included=True
    else:status='insufficient_phenotype_evidence';included=False
    missing=['Verify echo observation date and current clinical phenotype','Titration history, tolerability, preferences and clinician assessment']+needs
    actions=['Reconcile EF, medication use and class-specific exceptions before judging undertreatment'] if included else []
    return _result('heart_failure',state,status,included,ef['support']+[f['fact_id'] for f in diagnosis],ef['excluded'],missing,actions,
                   ef_state=ef,medications=medicines,
                   question_boundary='Treatment evidence review; no confirmed HFrEF diagnosis, GDMT eligibility, adherence or maximum tolerated dose judgment')


def kidney(state,records,analysis):
    e=labs(records,'egfr');cr=labs(records,'creatinine');et=trend(e);ct=trend(cr)
    rules=[p for p in analysis['policies'] if p.get('policy_id')=='kidney_specialist_review']
    screen=next(iter(rules),{'status':'unknown','missing':['effective referral-review policy']})
    referrals=[f for f in state['facts'] if f['concept'].startswith('referral:') and 'nephro' in f['concept']]
    declining=et['status']=='computed' and et['slope_interval_per_year'][1]<0
    rising=ct['status']=='computed' and ct['slope_interval_per_year'][0]>0
    referral_candidate=screen['status']=='criteria_met'
    if referral_candidate:status='verify_referral_followthrough' if referrals else 'referral_context_review'
    elif declining or rising:status='trajectory_review'
    else:status='criteria_not_met_in_observed_data' if e else 'insufficient_kidney_data'
    missing=[]
    if not cr:missing.append('Creatinine observations are absent; eGFR trend is not a substitute for a creatinine trajectory')
    if not referrals:missing.append('Nephrology attendance and outside-specialist records are unknown')
    elif not any(f['value']=='completed' for f in referrals):missing.append('A referral record does not establish completed nephrology care')
    missing+=['Clinical cause, albuminuria and other referral indications are not fully assessed by this screen']
    support=[f['fact_id'] for f in state['facts'] if f['concept'].startswith('lab:') and any(x in f['concept'] for x in ('egfr','creatinine'))]+[f['fact_id'] for f in referrals]
    actions=['Review longitudinal results and reconcile specialist attendance before deciding a referral is missing'] if referral_candidate or declining or rising else []
    return _result('kidney',state,status,bool(referral_candidate or declining or rising),support,[],missing,actions,
        referral_policy=screen,egfr_trend=et,creatinine_trend=ct,
        question_boundary='Runs the existing versioned referral-review criterion and descriptive trends, not the full CKD guideline or an AKI diagnosis')


def affordability(state):
    f=state['affordability'];val=f['value'];status=f['status']
    if status=='conflicting_reports':status='reconcile_conflicting_affordability_reports';included=True
    elif val is True:status='assistance_needs_review';included=True
    elif val is False:status='latest_report_denies_difficulty';included=False
    else:status='affordability_unknown';included=False
    missing=['Current need and patient consent to assistance','Actual program eligibility, benefits and application requirements']
    if val is None:missing.append('Reconciled patient-reported affordability assessment')
    actions=['Resolve conflicting reports before assessing assistance need'] if f['status']=='conflicting_reports' else ['Verify current affordability concern and applicable assistance options'] if included else []
    return _result('affordability',state,status,included,f['support'],f['excluded'],missing,actions,
                   reported_state=f,question_boundary='Reported need for review, not qualification for an assistance program')


def execute(state,records,analysis):
    return {'heart_failure':heart_failure(state,records),'kidney':kidney(state,records,analysis),'affordability':affordability(state)}
