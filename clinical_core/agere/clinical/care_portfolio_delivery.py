"""Component proposals across the same buyer's complete declared portfolio.

Secondary matching plans retain the original patient, coverage and source
bindings. Selected declared tags are lookup scope, not inferred patient needs.
The saved comparison and initial component review remain unchanged.
"""
from pathlib import Path
import copy,hashlib,json,sqlite3
from ...contracts import digest
from . import care_option_delivery as D,care_option_comparison as C
from . import patient_context,coverage_work
VERSION='care-portfolio-delivery/1'
LIMITS={**D.LIMITS,'patient_needs_inferred':False,'original_review_changed':False}

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),D.identity()])

def original(path,key):
    C.care.text(key,'component review ID',64)
    rows=C.L.payloads(path,'care-option-delivery.sqlite3','proposals',' WHERE id=?',(key,))
    if not rows:raise ValueError('Unknown saved component review')
    row=C.R.verified(rows[0])
    if D.snapshot(path,row['request'])!=row:raise C.care.work.WorkConflict('Original component review changed; prepare a current review')
    return row

def inputs(path,key):
    row=original(path,key);comparison,mapping,plan=D.inputs(path,row['comparison_id'])
    port=C.care.portfolio(path,row['buyer_id'])
    if port['revision']!=plan['portfolio_revision'] or port.get('id')!=plan['portfolio_id']:raise C.care.work.WorkConflict('Buyer portfolio changed')
    return row,comparison,plan,port

def matching_request(path,plan):
    patient=patient_context.association(path,plan);coverage=coverage_work.association(path,plan)
    # The sidecars retain original forms before reserved provenance markers.
    req=copy.deepcopy((patient or coverage or plan)['request'])
    return req,patient,coverage

def choices(path,request):
    C.care.exact(request,'delivery_id');row,c,plan,port=inputs(path,request['delivery_id'])
    matched={o['capability']['id'] for o in plan['options']}
    return {'delivery_id':row['id'],'buyer_id':row['buyer_id'],'portfolio_id':port.get('id'),'portfolio_revision':port['revision'],
      'components':[{'key':x['key'],'label':x['label']} for x in row['components']],
      'services':[{'capability':cap,'in_original_match':cap['id'] in matched,'original_exclusion_reason':next((e['reason'] for e in plan['excluded'] if e['capability_id']==cap['id']),None)} for cap in port['capabilities']],
      'original_matched_services':len(plan['options']),'portfolio_services':len(port['capabilities']),
      'scope':'All same-buyer declarations are offered for explicit proposal; this is not a clinical relevance ranking or verified component capability.',**LIMITS}

def snapshot(path,request):
    C.care.exact(request,'delivery_id component_bindings coordination_note');C.care.text(request['coordination_note'],'coordination note',2000)
    prior,c,plan,port=inputs(path,request['delivery_id']);required=[x['key'] for x in prior['components']]
    bindings=request['component_bindings']
    if not isinstance(bindings,list) or len(bindings)>len(required):raise ValueError('At most one explicit service proposal per component')
    seed,patient,cov=matching_request(path,plan);selected={};prepared={}
    alt=next(a for a in c['alternatives'] if a['key']==prior['review_alternative']['key'])
    for b in bindings:
        C.care.exact(b,'component_key capability_id need_tags rationale');C.care.text(b['rationale'],'component proposal rationale',2000)
        if b['component_key'] not in required or b['component_key'] in selected:raise ValueError('Distinct known components required')
        cap=next((s for s in port['capabilities'] if s['id']==b['capability_id']),None)
        if cap is None:raise ValueError('Select an exact capability from the same buyer portfolio')
        tags=C.care.strings(b['need_tags'],'declared lookup tags',empty=False)
        if not set(tags)<=set(cap['need_tags']):raise ValueError('Lookup tags must be explicitly selected from this capability declaration')
        match_req={**seed,'need_tags':tags,'rationale':b['rationale']}
        cache_key=digest(match_req)
        if cache_key not in prepared:
            result=patient_context.prepare_match(path,match_req);secondary=C.care.inspect_plan(path,result['id'])
            secondary_patient=patient_context.association(path,secondary);secondary_cov=coverage_work.association(path,secondary)
            if secondary['patient_id']!=prior['patient_id'] or secondary['buyer_id']!=prior['buyer_id'] or secondary['origin']!=plan['origin'] or secondary['coverage']!=plan['coverage']:
                raise ValueError('Secondary matching changed patient, buyer, origin or reported coverage')
            if (patient is None)!=(secondary_patient is None) or (cov is None)!=(secondary_cov is None):raise ValueError('Secondary matching lost a source binding')
            if patient and patient['context']!=secondary_patient['context']:raise ValueError('Secondary patient context differs')
            if cov and cov['coverage_selection']!=secondary_cov['coverage_selection']:raise ValueError('Secondary coverage selection differs')
            prepared[cache_key]=(secondary,secondary_patient,secondary_cov)
        secondary,secondary_patient,secondary_cov=prepared[cache_key]
        option=next((o for o in secondary['options'] if o['capability']['id']==cap['id']),None)
        if option is None or option['capability']!=cap:raise ValueError('Selected capability absent or changed in secondary matching')
        selected[b['component_key']]=(b,option,secondary,secondary_patient,secondary_cov)
    components=[]
    for component in prior['components']:
        row={k:component[k] for k in ('key','label','checks')};proposed=None;report=None;blocks=[];routes=[];access=[];status='no_service_proposed'
        if component['key'] in selected:
            b,o,secondary,pat,coverage=selected[component['key']]
            if o['capability']['modality'] not in alt['compatible_modalities']:blocks.append('Declared delivery modality differs from this alternative')
            routes=[r for r in o['routes'] if r['blocked_reasons']]
            if all(r['status']=='blocked' for r in o['routes']):blocks.append('All declared service/funding routes have blockers')
            report=C.participation.prepare(path,secondary['id'],o['id'])
            access=patient_context.review_checks(pat['context'],o) if pat else []
            proposed={'binding':b,'option_id':o['id'],'capability':o['capability'],'routes':o['routes'],
              'secondary_plan_id':secondary['id'],'secondary_plan_hash':digest(secondary),'matching_request':secondary['request'],
              'original_bound_request':{**seed,'need_tags':C.care.strings(b['need_tags'],'tags',empty=False),'rationale':b['rationale']},
              'patient_context':pat,'received_coverage':coverage,'in_original_match':any(x['capability']['id']==o['capability']['id'] for x in plan['options'])}
            status='declared_constraints_blocked' if blocks else 'proposed_requires_verification'
        components.append({**row,'status':status,'proposed_service':proposed,'declared_blockers':blocks,'route_blockers':routes,'participation':report,'patient_access_checks':access,'verified_delivery_capability':False})
    services={}
    for component in components:
        if component['proposed_service']:services.setdefault(component['proposed_service']['capability']['id'],[]).append(component['key'])
    unassigned=[x['key'] for x in components if x['proposed_service'] is None]
    row={'schema':VERSION,'request':request,'delivery_id':prior['id'],'delivery_hash':digest(prior),'comparison_id':c['id'],'comparison_hash':digest(c),
      'patient_id':prior['patient_id'],'as_of':prior['as_of'],'task_id':prior['task_id'],'owner':prior['owner'],'buyer_id':prior['buyer_id'],
      'portfolio_id':port.get('id'),'portfolio_revision':port['revision'],'portfolio_hash':digest(port),'original_plan_id':plan['id'],'original_plan_hash':digest(plan),
      'question':prior['question'],'original_service_question':prior['original_service_question'],'review_alternative':prior['review_alternative'],
      'components':components,'coordination':{'note':request['coordination_note'],'checks':prior['coordination']['checks'],
        'same_service_multiple_components':[{'capability_id':k,'components':v} for k,v in services.items() if len(v)>1],'coordination_verified':False},
      'coverage_of_proposal':{'required_components':len(components),'components_with_proposals':len(selected),'components_without_proposals':unassigned,
        'distinct_proposed_services':len(services),'all_components_have_proposals':not unassigned,'all_components_verified':False},
      'original_match_baseline':{'matched_services':len(plan['options']),'portfolio_services':len(port['capabilities']),
        'components_with_proposals':prior['coverage_of_proposal']['components_with_proposals'],
        'proposed_components_outside_original_match':[x['key'] for x in components if x['proposed_service'] and not x['proposed_service']['in_original_match']]},
      'review_targets':prior['review_targets'],'reference':prior['reference'],'reference_currency':prior['reference_currency'],'known_update':prior['known_update'],
      'meaning':'Explicit component proposals from the same buyer portfolio. Declared tags are lookup scope, not clinical need, indication or capability verification.',
      'implementation':identity(),**LIMITS};row['id']=digest(row);return row

def database(path):
    db=sqlite3.connect(Path(path)/'care-portfolio-delivery.sqlite3',timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,delivery TEXT,payload TEXT)')
    for op in ('UPDATE','DELETE'):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_proposals_{op} BEFORE {op} ON proposals BEGIN SELECT RAISE(ABORT,'Portfolio proposal history immutable'); END")
    return db

def preview(path,request):return snapshot(path,request)

def prepare(path,request):
    C.care.exact(request,'proposal preview_id');row=snapshot(path,request['proposal'])
    if row['id']!=request['preview_id']:raise C.care.work.WorkConflict('Portfolio proposal dependencies changed; preview again')
    db=database(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if snapshot(path,request['proposal'])!=row:raise C.care.work.WorkConflict('Portfolio inputs changed while saving')
            db.execute('INSERT OR IGNORE INTO proposals VALUES (?,?,?)',(row['id'],row['delivery_id'],json.dumps(row,allow_nan=False)))
            return C.R.verified(json.loads(db.execute('SELECT payload FROM proposals WHERE id=?',(row['id'],)).fetchone()[0]))
    finally:db.close()

def listing(path,request):
    C.care.exact(request,'delivery_id');C.care.text(request['delivery_id'],'component review ID',64);out=[]
    for row in C.L.payloads(path,'care-portfolio-delivery.sqlite3','proposals',' WHERE delivery=?',(request['delivery_id'],)):
        C.R.verified(row);reasons=[]
        try:
            if snapshot(path,row['request'])!=row:reasons.append('Portfolio proposal dependencies changed; prepare a current review')
        except (ValueError,KeyError,FileNotFoundError) as exc:reasons.append(str(exc))
        out.append({**row,'current':not reasons,'stale_reasons':reasons})
    return {'delivery_id':request['delivery_id'],'proposals':out,**LIMITS}
