"""Bind existing analytical receipts to the shared graph decision, never to eligibility.

Report outputs remain reports. Complete member evidence and unresolved inputs can
travel with the original question without becoming a cohort or a service need.
"""
from pathlib import Path
from collections import Counter
import hashlib,json,sqlite3,zlib
from ...contracts import digest
from . import measure_composition as M,financial_accounts as F,program_accountability as A,outcomes as O
from . import graph_criteria as C,program_documents as D,patient_input_index
from . import graph_observation as B

OPERATIONS=('measure_review','financial_accounts','accountability_review','outcome',B.OPERATION)
MODULES=dict(zip(OPERATIONS,(M,F,A,O,B)))
TITLES=dict(zip(OPERATIONS,('Documentary measure','Received financial accounts','Program accountability','Recorded outcomes','Record clocks and observation process')))


def identity():
    return digest([hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),M.identity(),F.identity(),A.identity(),O.code_identity(),B.identity()])


def key(op,prep,pid):
    return M.patient_id(prep,pid) if op=='measure_review' else MODULES[op].receipt_id(prep,pid)


def read(db,op,rid):
    table='patients' if op=='measure_review' else 'receipts'
    raw=db.execute('SELECT payload FROM '+table+' WHERE id=?',(rid,)).fetchone()
    if raw is None:raise ValueError('Analytical member receipt missing')
    return json.loads(zlib.decompress(raw[0]))


def status_value(op,row):
    if op==B.OPERATION:return row['status'],None
    if op=='measure_review':return row['status'],row['effective_numerator']
    if op=='outcome':return row['status'],row['recorded_label']
    if op=='accountability_review':return row['payment_readiness'],None
    status=('review_required' if row['unassigned_sources'] or any(a['status']!='reconciled_received_records' for a in row['accounts'])
            else 'reconciled_received_records' if row['accounts'] else 'no_received_accounts')
    return status,row['status_counts']


def attach(path,op,patients,parameters,prep,result,selection):
    ids=sorted(patients);db=MODULES[op].storage(path);refs=[]
    try:
        for pid in ids:
            rid=key(op,prep,pid);row=read(db,op,rid)
            if row['id']!=rid or row['patient_id']!=pid:raise ValueError('Analytical receipt patient mismatch')
            status,value=status_value(op,row)
            refs.append({'patient_id':pid,'receipt_id':rid,'receipt_digest':digest(row),'status':status,'value':value,
                'membership':O.membership(row,parameters['filter']) if op=='outcome' else None,
                'report_only':op!='outcome','care_account_counts':{},'review_topics':[]})
    finally:db.close()
    result.update(review_members=refs,input_membership=selection,population_digest=digest(ids),
                  member_status_counts=dict(Counter(r['status'] for r in refs)))
    return result


def dependencies(path,graph):
    nodes=[x for x in graph['results'] if x['node']['operation'] in OPERATIONS]
    if not nodes:return {}
    inputs=patient_input_index.snapshot(path)['patients'] if any(x['node']['operation']!=B.OPERATION for x in nodes) else {};result={}
    for item in nodes:
        node=item['node'];op=node['operation'];prep=MODULES[op].prepare(path,node['parameters'])
        result[node['id']]={'adapter':identity(),'patients':{m['patient_id']:{'receipt_id':key(op,prep,m['patient_id']),
            'raw_input_hash':prep['patient_sources'][m['patient_id']] if op==B.OPERATION else inputs.get(m['patient_id'],{}).get('protocol_hash')} for m in item['result']['review_members']}}
    return result


def summary(op,result):
    omitted={'review_members','patients','input_membership','processing_counts','financial_processing_counts','elapsed_seconds','input_fingerprints','cache_hit'}
    return {'title':TITLES[op],**{k:v for k,v in result.items() if k not in omitted}}


def source_refs(op,row,context):
    # The event ledger stores its primary hash beside, not inside, source.
    events=[{**e['source'],'record_hash':e['record_hash']} for e in row.get('evidence',{}).values()] if op=='outcome' else []
    return C.source_refs([row,context,*events])


def gaps(op,row,node):
    base={'node_id':node['id'],'receipt_id':row['id'],'request_id':None,'target_record_id':None,
          'parameters':node['parameters'],'requested_action':'review_versioned_analytical_input'}
    result=[]
    if op==B.OPERATION:
        result=[{**base,'status':'observation_process_unresolved','binding':q['id'],'question':q['question'],
            'conditional_effect':q['effect']} for q in B.QUESTIONS]
    elif op=='measure_review':
        for role in row['unresolved_components']:
            result.append({**base,'status':'measure_component_unresolved','binding':role,'component':row['components'][role],
                           'conditional_effect':'May change documentary denominator/numerator; no certified performance or payment inferred.'})
    elif op=='accountability_review':
        for req in row['requirements']:
            result.append({**base,'status':'accountability_input_requires_review','binding':req['id'],'scope':req['scope'],
                           'requirement':req,'conditional_effect':'Required for contract-specific assessment; not evidence of entitlement or attribution.'})
    elif op=='financial_accounts':
        for account in row['accounts']:
            if account['status']!='reconciled_received_records':
                result.append({**base,'status':'financial_account_requires_review','account_id':account['account_id'],
                    'source_record_ids':account['source_record_ids'],'account_status':account['status'],'next_step':account['next_step']})
        if row['unassigned_sources']:
            result.append({**base,'status':'financial_source_binding_unresolved','sources':row['unassigned_sources']})
    else:
        if row['recorded_label'] is None:
            result.append({**base,'status':'outcome_observation_unresolved','outcome_status':row['status'],
                'window':row['window'],'unresolved_endpoint_evidence':row['unresolved_endpoint_evidence']})
        if row['coverage']['status']=='unknown':
            result.append({**base,'status':'outcome_source_coverage_unknown','coverage':row['coverage'],
                'conditional_effect':'No matching received event is not proof of no event or complete follow-up.'})
    return result


def evidence(path,ref,node):
    op=node['operation'];pid=ref['patient_id'];prep=MODULES[op].prepare(path,node['parameters'])
    if key(op,prep,pid)!=ref['receipt_id']:raise ValueError('Analytical sources or specification changed')
    db=MODULES[op].storage(path)
    try:row=read(db,op,ref['receipt_id'])
    finally:db.close()
    if row['patient_id']!=pid or row['id']!=ref['receipt_id'] or digest(row)!=ref['receipt_digest']:
        raise ValueError('Analytical receipt integrity mismatch')
    context={}
    if op=='measure_review':
        docs={k:D.inspect(path,v['receipt_id']) for k,v in row['references'].items()}
        if any(digest(docs[k])!=v['receipt_hash'] or docs[k]['patient_id']!=pid for k,v in row['references'].items()):
            raise ValueError('Measure source receipt mismatch')
        context={'definition':node['parameters'],'source_receipts':docs}
    elif op=='accountability_review':
        finance=F.inspect(path,row['financial_receipt_id'])
        if finance['patient_id']!=pid or digest(finance)!=row['financial_receipt_hash']:raise ValueError('Accountability financial binding mismatch')
        context={'financial_evidence':finance}
    refs=source_refs(op,row,context)
    with sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
        for rid,h in refs.items():
            raw=db.execute('SELECT patient,payload FROM records WHERE id=?',(rid,)).fetchone()
            if not raw or raw[0]!=pid or digest(json.loads(raw[1]))!=h:raise ValueError('Analytical source changed; rebuild affected analysis')
    finding={'node_id':node['id'],'operation':op,'receipt_id':row['id'],'receipt_digest':digest(row),
        'title':TITLES[op],'parameters':node['parameters'],'membership':ref['membership'],'report_only':ref['report_only'],
        'status':ref['status'],'value':ref['value'],'report':row,'report_context':context,'care_accounts':[],
        'review_topics':[],'authority':'observed_analytics_not_clinical_performance_or_attribution'}
    return finding,refs,gaps(op,row,node)
