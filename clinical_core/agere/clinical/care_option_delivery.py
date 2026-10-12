"""Explicit component-level buyer proposals for an existing option comparison.

No clinical choice, service reservation, staff assignment or care task is made.
One declared service may be proposed for multiple components only explicitly;
that does not establish that it supplies either component.
"""
from pathlib import Path
import hashlib,json,sqlite3
from ...contracts import digest
from . import care_option_context as X,care_option_comparison as C
VERSION='care-option-delivery-review/1'
CATALOG=Path(__file__).with_name('care_delivery_components.json')
LIMITS={'clinical_alternative_selected':False,'component_capabilities_verified':False,'coverage_established':False,
        'care_tasks_created':0,'service_slots_reserved':0,'provider_calls':0,'external_actions':0}

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),json.loads(CATALOG.read_text())])

def inputs(path,key):
    c=X.comparison(path,key);data=json.loads(CATALOG.read_text())
    if data['schema']!='care-delivery-components/1':raise ValueError('Unknown component mapping schema')
    matches=[m for m in data['mappings'] if m['reference_doi']==c['reference']['citation']['doi'] and m['option_evidence_key']==c['option_evidence']['key']]
    if len(matches)!=1:raise ValueError('No unique component mapping for this exact option evidence')
    m=matches[0];keys=[x['key'] for x in m['components']]
    if len(keys)!=len(set(keys)):raise ValueError('Distinct component keys required')
    for alt,cs in m['alternatives'].items():
        if alt not in {a['key'] for a in c['alternatives']} or not cs or len(cs)!=len(set(cs)) or not set(cs)<=set(keys):raise ValueError('Exact alternative and distinct known components required')
    plan=C.care.inspect_plan(path,c['plan_id']);return c,m,plan

def choices(path,request):
    C.care.exact(request,'comparison_id');c,m,plan=inputs(path,request['comparison_id'])
    return {'comparison_id':c['id'],'alternatives':[{'key':a['key'],'label':a['label'],'components':[x for x in m['components'] if x['key'] in m['alternatives'][a['key']]]} for a in c['alternatives'] if a['key'] in m['alternatives']],
      'services':[{'option_id':o['id'],'capability':o['capability']} for o in plan['options']],'basis':m['basis'],**LIMITS}

def snapshot(path,request):
    C.care.exact(request,'comparison_id alternative_key component_bindings coordination_note')
    C.care.text(request['coordination_note'],'coordination note',2000);c,m,plan=inputs(path,request['comparison_id'])
    key=request['alternative_key'];a=next((a for a in c['alternatives'] if a['key']==key),None)
    if a is None or key not in m['alternatives']:raise ValueError('Select an exact alternative with component mapping')
    required=m['alternatives'][key];bindings=request['component_bindings']
    if not isinstance(bindings,list) or len(bindings)>len(required):raise ValueError('At most one proposed service per component')
    selected={}
    for b in bindings:
        C.care.exact(b,'component_key option_id rationale');C.care.text(b['rationale'],'component mapping rationale',2000)
        if b['component_key'] not in required or b['component_key'] in selected:raise ValueError('Distinct exact components for this alternative required')
        option=next((o for o in plan['options'] if o['id']==b['option_id']),None)
        if option is None:raise ValueError('Propose an exact service from this comparison buyer plan')
        selected[b['component_key']]=(b,option)
    components=[]
    for component in [x for x in m['components'] if x['key'] in required]:
        proposed=None;report=None;blocks=[];status='no_service_proposed'
        if component['key'] in selected:
            b,o=selected[component['key']];cap=o['capability'];proposed={'binding':b,'option_id':o['id'],'capability':cap,'routes':o['routes']}
            if cap['modality'] not in a['compatible_modalities']:blocks.append('Declared delivery modality differs from this alternative')
            blocked_routes=[r for r in o['routes'] if r['blocked_reasons']]
            if all(r['status']=='blocked' for r in o['routes']):blocks.append('All declared service/funding routes have blockers')
            report=C.participation.prepare(path,plan['id'],o['id'])
            status='declared_constraints_blocked' if blocks else 'proposed_requires_verification'
        else:blocked_routes=[]
        components.append({**component,'status':status,'proposed_service':proposed,'declared_blockers':blocks,'route_blockers':blocked_routes,'participation':report,'verified_delivery_capability':False})
    byservice={}
    for x in components:
        if x['proposed_service']:byservice.setdefault(x['proposed_service']['option_id'],[]).append(x['key'])
    unassigned=[x['key'] for x in components if x['proposed_service'] is None]
    row={'schema':VERSION,'request':request,'comparison_id':c['id'],'comparison_hash':digest(c),'patient_id':c['patient_id'],'as_of':c['as_of'],
       'task_id':c['task_id'],'owner':c['owner'],'buyer_id':c['buyer_id'],'plan_id':plan['id'],'plan_hash':digest(plan),
       'question':c['question'],'original_service_question':c['original_service_question'],'reference':c['reference'],
       'reference_currency':c['reference_currency'],'known_update':c['option_evidence'].get('currency_evidence'),
       'review_alternative':{'key':a['key'],'label':a['label']},'mapping_basis':m['basis'],'components':components,
       'coverage_of_proposal':{'required_components':len(components),'components_with_proposals':len(selected),'components_without_proposals':unassigned,'distinct_proposed_services':len(byservice),
         'all_components_have_proposals':not unassigned,'all_components_verified':False},
       'coordination':{'note':request['coordination_note'],'same_service_multiple_components':[{'option_id':k,'components':v} for k,v in byservice.items() if len(v)>1],
          'checks':['Verify each component separately even when the same service is proposed for both','Confirm accountable clinicians, handoffs, monitoring, capacity, patient agreement and benefits for every component'],
          'coordination_verified':False},
       'comparison_baseline':{'original_alternative_service_proposal':a['proposed_service'],'original_comparison_changed':False,
          'meaning':'Component proposals expose separate delivery needs; original alternative mapping and clinical selection are unchanged.'},
       'review_targets':[q['target'] for q in c['questions']],'implementation':identity(),**LIMITS};row['id']=digest(row);return row

def database(path):
    db=sqlite3.connect(Path(path)/'care-option-delivery.sqlite3',timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,comparison TEXT,payload TEXT)')
    for op in ('UPDATE','DELETE'):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_proposals_{op} BEFORE {op} ON proposals BEGIN SELECT RAISE(ABORT,'Delivery proposal history immutable'); END")
    return db

def preview(path,request):return snapshot(path,request)

def prepare(path,request):
    C.care.exact(request,'proposal preview_id');row=snapshot(path,request['proposal'])
    if row['id']!=request['preview_id']:raise C.care.work.WorkConflict('Delivery proposal dependencies changed; preview again')
    db=database(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if snapshot(path,request['proposal'])!=row:raise C.care.work.WorkConflict('Delivery inputs changed while saving')
            db.execute('INSERT OR IGNORE INTO proposals VALUES (?,?,?)',(row['id'],row['comparison_id'],json.dumps(row,allow_nan=False)))
            return C.R.verified(json.loads(db.execute('SELECT payload FROM proposals WHERE id=?',(row['id'],)).fetchone()[0]))
    finally:db.close()

def listing(path,request):
    C.care.exact(request,'comparison_id');C.care.text(request['comparison_id'],'comparison ID',64);out=[]
    for row in C.L.payloads(path,'care-option-delivery.sqlite3','proposals',' WHERE comparison=?',(request['comparison_id'],)):
        C.R.verified(row);reasons=[]
        try:
            if snapshot(path,row['request'])!=row:reasons.append('Delivery proposal dependencies changed; preserve history and preview again')
        except (ValueError,KeyError,FileNotFoundError) as exc:reasons.append(str(exc))
        out.append({**row,'current':not reasons,'stale_reasons':reasons})
    return {'comparison_id':request['comparison_id'],'proposals':out,**LIMITS}
