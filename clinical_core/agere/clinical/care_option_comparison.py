"""Source-backed alternatives compared against exact internal work dependencies.

This is an unranked review comparison. Declared service mappings and source
estimates cannot establish clinical fit, entitlement or individualized benefit.
"""
from pathlib import Path
import hashlib,json,math,sqlite3
from ...contracts import digest
from . import reference_applicability as R,reference_lifecycle as L
from . import care_capabilities as care,coverage_work,patient_context,participation_evidence as participation

VERSION='care-option-comparison/1'
CATALOG=Path(__file__).with_name('care_option_evidence.json')
LIMITS={'clinical_applicability_established':False,'individual_benefit_estimated':False,'coverage_established':False,
        'clinical_review_recorded':False,'clinical_execution_authorized':False,'provider_calls':0,'external_actions':0}

def evidence():
    e=json.loads(CATALOG.read_text())
    if e['schema']!='care-option-evidence/1' or e['review_status']!='machine_curated_unreviewed':raise ValueError('Unreviewed option evidence required')
    if len({x['key'] for x in e['alternatives']})!=len(e['alternatives']):raise ValueError('Distinct alternatives required')
    for x in e['estimates']:
        values=[x['point'],*x['interval'],x['null_value']]
        if len(x['interval'])!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in values):raise ValueError('Finite evidence estimate and interval required')
        if not x['interval'][0]<=x['point']<=x['interval'][1] or not 0<x['interval_level']<1:raise ValueError('Ordered evidence interval required')
    return e

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),evidence()])

def database(path):
    db=sqlite3.connect(Path(path)/'care-option-comparisons.sqlite3',timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS comparisons(id TEXT PRIMARY KEY,manifest TEXT,payload TEXT)')
    for op in ('UPDATE','DELETE'):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_comparisons_{op} BEFORE {op} ON comparisons BEGIN SELECT RAISE(ABORT,'Option comparison history immutable'); END")
    return db

def inputs(path,manifest_id):
    manifest=R.load(path,manifest_id);R.current(path,manifest)
    task=R.care_readiness.task(path,manifest['task_id']);plan=care.inspect_plan(path,task['plan_id']);e=evidence()
    if manifest['reference']['citation']['doi']!=e['reference_doi']:raise ValueError('No option evidence mapping exists for this exact reference')
    if task['patient_id']!=manifest['patient_id'] or task['option_id']!=manifest['option_id']:raise ValueError('Reference patient or service task changed')
    return manifest,task,plan,e

def choices(path,request):
    care.exact(request,'manifest_id');m,task,plan,e=inputs(path,request['manifest_id'])
    return {'manifest_id':m['id'],'question':m['original_target']['question'],'alternatives':e['alternatives'],
       'buyer_id':plan['buyer_id'],'services':[{'option_id':o['id'],'capability':o['capability'],'status':o['status']} for o in plan['options']],
       'mapping_authority':'An explicit proposed mapping is for review; neither tags nor service names establish delivery capability.',**LIMITS}

def snapshot(path,request):
    care.exact(request,'manifest_id question rationale service_bindings')
    care.text(request['question'],'comparison question',2000);care.text(request['rationale'],'comparison rationale',2000)
    m,task,plan,e=inputs(path,request['manifest_id']);bindings=request['service_bindings'];keys={a['key'] for a in e['alternatives']}
    if not isinstance(bindings,list) or len(bindings)>len(keys):raise ValueError('At most one proposed service mapping per alternative')
    mapped={}
    for b in bindings:
        care.exact(b,'alternative_key option_id rationale');care.text(b['rationale'],'service mapping rationale',2000)
        if b['alternative_key'] not in keys or b['alternative_key'] in mapped:raise ValueError('Distinct known alternative required')
        option=next((o for o in plan['options'] if o['id']==b['option_id']),None)
        if option is None:raise ValueError('Select an exact option from this task buyer plan')
        mapped[b['alternative_key']]=(b,option)
    coverage=coverage_work.association(path,plan);patient=patient_context.association(path,plan)
    set_row=next(s for s in R.inspect(path,task['id'])['sets'] if s['manifest']['id']==m['id'])
    questions=[{'key':q['key'],'target':q['target'],'question':q['question'],'state':'applicability_unresolved',
                'source_contexts':[{'receipt_id':a['id'],'receipt_hash':digest({k:v for k,v in a.items() if k not in ('current','stale_reasons')}),
                   'context_id':a['acquisition']['context_id'],'current':a['current'],'stale_reasons':a['stale_reasons']} for a in q['acquisitions']]} for q in set_row['questions']]
    alternatives=[]
    for a in e['alternatives']:
        reasons=[];route_blocks=[];proposed=None;participation_report=None;checks=[]
        if a['key'] in mapped:
            binding,option=mapped[a['key']];cap=option['capability'];proposed={'binding':binding,'option_id':option['id'],'capability':cap,'routes':option['routes']}
            if cap['modality'] not in a['compatible_modalities']:reasons.append('Declared delivery modality differs from this alternative; exact program arrangements require review')
            route_blocks=[{'pathway_id':r['pathway_id'],'reasons':r['blocked_reasons']} for r in option['routes'] if r['blocked_reasons']]
            if all(r['status']=='blocked' for r in option['routes']):reasons.append('All declared funding/service routes have blockers')
            participation_report=participation.prepare(path,plan['id'],option['id'])
            checks=patient_context.review_checks(patient['context'],option) if patient else []
            mapping_status='declared_constraints_blocked' if reasons else 'proposed_mapping_requires_verification'
        else:mapping_status='no_service_proposed'
        alternatives.append({**a,'mapping_status':mapping_status,'proposed_service':proposed,'declared_blockers':reasons,'route_blockers':route_blocks,
           'participation':participation_report,'patient_access_checks':checks,'requirements':[
             'Verify the exact intervention and program components; service tags and names do not prove capability',
             'Resolve clinical appropriateness, contraindications, patient goals and service-specific consent',
             'Verify current plan, provider participation, authorization, cost sharing and modality-specific payment',
             'Verify actual capacity, accountable staff and delivery arrangements'],
           'review_targets':[q['target'] for q in questions], 'individual_benefit':None,'individual_harm':None,'financial_effect':None,'rank':None})
    estimates=[{**x,'interval_contains_null':x['interval'][0]<=x['null_value']<=x['interval'][1],
       'interpretation':'Reported population contrast, not a patient prediction; an interval containing the null does not prove equivalence.'} for x in e['estimates']]
    row={'schema':VERSION,'request':request,'manifest_id':m['id'],'manifest_hash':digest(m),'task_id':task['id'],
         'task_revision':task['revision'],'patient_id':m['patient_id'],'owner':task['owner'],'buyer_id':plan['buyer_id'],
         'plan_id':plan['id'],'plan_hash':digest(plan),'question':request['question'],'original_service_question':m['original_target']['question'],
         'as_of':m['original_target']['as_of'],'reference':m['reference'],'reference_currency':m['reference_currency'],
         'option_evidence':e,'reported_comparative_estimates':estimates,'evidence_families':sorted({x['evidence_family'] for x in e['estimates']}),
         'study_counts_aggregated':False,'alternatives':alternatives,'questions':questions,
         'reported_coverage':plan['coverage'],'received_coverage':coverage,'patient_context':patient,
         'baseline':{'policy':'retain_current_internal_review_unranked','clinical_treatment_comparator':False,'selected_alternative':None,
           'reason':'Individual benefit, clinical fit, patient preference and executable service feasibility are not established.'},
         'implementation':identity(),**LIMITS};row['id']=digest(row);return row

def preview(path,request):return snapshot(path,request)

def prepare(path,request):
    care.exact(request,'comparison preview_id');row=snapshot(path,request['comparison'])
    if row['id']!=request['preview_id']:raise care.work.WorkConflict('Comparison dependencies changed; preview again')
    db=database(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if snapshot(path,request['comparison'])!=row:raise care.work.WorkConflict('Comparison inputs changed while saving')
            db.execute('INSERT OR IGNORE INTO comparisons VALUES (?,?,?)',(row['id'],row['manifest_id'],json.dumps(row,allow_nan=False)))
            return R.verified(json.loads(db.execute('SELECT payload FROM comparisons WHERE id=?',(row['id'],)).fetchone()[0]))
    finally:db.close()

def listing(path,request):
    care.exact(request,'manifest_id');care.text(request['manifest_id'],'manifest ID',64)
    out=[]
    for row in L.payloads(path,'care-option-comparisons.sqlite3','comparisons',' WHERE manifest=?',(request['manifest_id'],)):
        R.verified(row);reasons=[]
        try:
            if snapshot(path,row['request'])!=row:reasons.append('Comparison dependencies changed; retain history and prepare a new comparison')
        except (ValueError,KeyError,FileNotFoundError) as exc:reasons.append(str(exc))
        out.append({**row,'current':not reasons,'stale_reasons':reasons})
    return {'manifest_id':request['manifest_id'],'comparisons':out,**LIMITS}
