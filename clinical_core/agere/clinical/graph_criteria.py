"""Bind existing requirement/protocol/document receipts to graph-owned review.

No second evaluator, eligibility rule, workflow store or model route is defined.
"""
import hashlib,json,sqlite3,zlib
from pathlib import Path
from ...contracts import digest
from . import evidence_requirements as R, care_protocols as C, program_documents as D
from . import requirement_work, selection_context, patient_input_index

OPERATIONS=('requirements','protocol_review','program_document')
MODULES=dict(zip(OPERATIONS,(R,C,D)))


def identity():
    return digest(['graph-criteria-binding/1',hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   selection_context.identity()])


def prepare(path,op,parameters):
    return R.snapshot(path,parameters) if op=='requirements' else MODULES[op].prepare(path,parameters)


def key(op,prep,pid,*,interpretation_code=None,requirement_code=None):
    if op=='requirements':return R.receipt_id(prep,pid,code=requirement_code,interpretation_code=interpretation_code)
    return MODULES[op].receipt_id(prep,pid,interpretation_code)


def source_refs(value):
    """Only explicit source witnesses; receipt hashes are not record hashes."""
    found={}
    def walk(v):
        if isinstance(v,dict):
            if v.get('record_id') and v.get('record_hash'):
                rid=v['record_id'];h=v['record_hash']
                if rid in found and found[rid]!=h:raise ValueError('Conflicting criterion source versions')
                found[rid]=h
            for x in v.values():walk(x)
        elif isinstance(v,list):
            for x in v:walk(x)
    walk(value)
    return found


def topics(row,op):
    # Reuse the existing pinned protocol-to-review declaration, not source
    # category inference. Other contracts remain evidence without nomination.
    from . import population_protocol_review as P
    if op!='protocol_review' or row['review_candidate'] is not True:return []
    scope=P.SCOPES.get('protocol:'+C.qualified(row['protocol']))
    if not scope or scope['protocol_hash']!=row['protocol']['hash'] or not source_refs(row):return []
    return [{'need_tag':t,'basis':scope['basis'],'rule_identity':P.rule_identity('protocol:'+C.qualified(row['protocol'])),
             'protocol_hash':row['protocol']['hash'],'authority':'documentary_protocol_review_only'} for t in scope['need_tags']]


def member_index(path,op,patients,parameters,prep,yes,unknown):
    module=MODULES[op];db=module.storage(path);result=[]
    ic=C.interpretation.code_identity();rc=R.code_identity()
    try:
        for pid in patients:
            rid=key(op,prep,pid,interpretation_code=ic,requirement_code=rc);raw=db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(rid,pid)).fetchone()
            if raw is None:raise ValueError('Evaluated criterion receipt missing')
            row=json.loads(zlib.decompress(raw[0]))
            value=row.get('value',row.get('trigger'))
            result.append({'patient_id':pid,'receipt_id':rid,'receipt_digest':digest(row),'status':row['status'],
                'value':value,'membership':True if pid in yes else None if pid in unknown else False,
                'care_account_counts':{},'review_topics':topics(row,op)})
    finally:db.close()
    return result


def dependencies(path,graph):
    nodes=[item for item in graph['results'] if item['node']['operation'] in OPERATIONS]
    if not nodes:return {}
    inputs=patient_input_index.snapshot(path)['patients'];result={}
    ic=C.interpretation.code_identity();rc=R.code_identity();adapter=identity()
    for item in nodes:
        n=item['node'];prep=prepare(path,n['operation'],n['parameters'])
        result[n['id']]={'criteria_adapter':adapter,
            'patients':{m['patient_id']:{'receipt_id':key(n['operation'],prep,m['patient_id'],interpretation_code=ic,requirement_code=rc),
                'raw_input_hash':inputs.get(m['patient_id'],{}).get('protocol_hash')}
                for m in item['result']['review_members']}}
    return result


def evidence(path,ref,node):
    op=ref['operation'];pid=ref['patient_id'];params=node['parameters'];prep=prepare(path,op,params)
    # Carry the same selection contracts already used by graph worklists.
    evaluated={node['id']:{pid}};preps={node['id']:prep}
    documentary=requirement_work.contexts(path,[pid],[node],preps,evaluated_patients=evaluated) if op=='requirements' else None
    documents=D.contexts(path,[pid],[node],preps,evaluated_patients=evaluated) if op=='program_document' else None
    if op=='requirements':context=documentary[pid]
    else:
        context=selection_context.contexts(path,[pid],[node] if op=='protocol_review' else [],preps,
                    evaluated_patients=evaluated,program_documents=documents)[pid]
    # Equivalent current-receipt check used by the existing inspectors. The
    # fresh preparation also builds the existing selection context; repeating
    # global preparation inside verify/inspect would scan identical inputs.
    if key(op,prep,pid)!=ref['receipt_id']:raise ValueError('Criterion source, rule or review changed')
    db=MODULES[op].storage(path)
    try:raw=db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(ref['receipt_id'],pid)).fetchone()
    finally:db.close()
    if raw is None:raise ValueError('Criterion receipt missing')
    row=json.loads(zlib.decompress(raw[0]))
    if row['patient_id']!=pid or digest(row)!=ref['receipt_digest']:
        raise ValueError('Criterion receipt or patient binding changed')
    refs=source_refs(row)
    with sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
        for rid,h in refs.items():
            raw=db.execute('SELECT patient,payload FROM records WHERE id=?',(rid,)).fetchone()
            if not raw or raw[0]!=pid or digest(json.loads(raw[1]))!=h:
                raise ValueError('Criterion source changed; rebuild the versioned source analysis')
    finding={'node_id':node['id'],'operation':op,'receipt_id':row['id'],'receipt_digest':digest(row),
        'parameters':params,'membership':ref['membership'],'status':row['status'],'value':row.get('value',row.get('trigger')),
        'as_of':row['as_of'],'care_accounts':[],'criteria':row,'selection_context':context,
        'review_topics':topics(row,op),'authority':'versioned_criteria_review_only_not_eligibility'}
    unresolved=row.get('acquisition',row.get('missing_inputs',[]))
    if op=='program_document':
        unresolved=[{'binding':k,'label':v['label'],'status':v.get('status'),'reason':v.get('reason')}
                    for k,v in row['inputs'].items() if v['value'] is None]
    gaps=[{'node_id':node['id'],'receipt_id':row['id'],'request_id':None,'target_record_id':None,
           'status':'criterion_input_unresolved','requested_action':'review_versioned_criterion_input',
           'binding':v['binding'],'criterion_input':v,'parameters':params,'as_of':row['as_of']} for v in unresolved]
    for requirement in row.get('unimplemented_clinical_requirements',[]):
        gaps.append({'node_id':node['id'],'receipt_id':row['id'],'request_id':None,'target_record_id':None,
            'status':'clinical_or_program_requirement_unimplemented','requested_action':'review_unimplemented_requirement',
            'requirement':requirement,'parameters':params,'as_of':row['as_of']})
    return finding,refs,gaps
