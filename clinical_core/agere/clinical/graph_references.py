"""Bind dated scientific questions to existing graph evidence, never eligibility.

Uses the protected reference registry/lifecycle and existing member receipts.
Evidence links are proposed context for review, not adjudicated applicability.
"""
from collections import Counter
from datetime import date
from pathlib import Path
import hashlib,json,sqlite3
from ...contracts import digest
from . import reference_applicability as R,reference_lifecycle as L

OPERATION='reference_review'
EVIDENCE_OPERATIONS=('source_state','pre_event_state','requirements','protocol_review','program_document',
                     'measure_review','financial_accounts','accountability_review','outcome')
VERSION='graph-reference-review/1'
LIMITS={'clinical_applicability_established':False,'clinical_execution_authorized':False,
        'individual_benefit_estimated':False,'human_review_recorded':False,'new_paid_api_calls':0}

def identity():
    return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),R.identity(),L.identity()])

def catalog(path):
    return [{k:r[k] for k in ('id','key','version','title','published_on','checked_on','questions','review_status')}
            for r in L.available(path)]

def validate(p,as_of,inputs,earlier):
    R.care.exact(p,'reference_id available_by bindings')
    if not isinstance(p['reference_id'],str) or len(p['reference_id'])!=64:raise ValueError('Exact registered reference hash required')
    try:day=date.fromisoformat(p['available_by'])
    except (TypeError,ValueError):raise ValueError('Reference receipt cutoff must be an ISO date')
    if day.isoformat()!=p['available_by'] or p['available_by']>as_of:raise ValueError('Reference cutoff exceeds snapshot')
    bindings=p['bindings']
    if not isinstance(bindings,list) or len(bindings)>20:raise ValueError('Bounded reference question bindings required')
    keys=[];used=set()
    for b in bindings:
        R.care.exact(b,'question_key node_ids')
        R.care.text(b['question_key'],'reference question key',120);keys.append(b['question_key'])
        ids=b['node_ids']
        if not isinstance(ids,list) or not 0<=len(ids)<=9 or any(not isinstance(x,str) for x in ids) or len(ids)!=len(set(ids)):
            raise ValueError('Distinct earlier evidence node IDs required')
        if any(x not in inputs[1:] for x in ids):raise ValueError('Reference bindings must use declared earlier evidence inputs')
        used.update(ids)
    if len(keys)!=len(set(keys)) or used!=set(inputs[1:]):raise ValueError('Distinct question bindings must account for all evidence inputs')
    for node in earlier:
        if node['id'] not in used:continue
        cutoff=node['parameters'].get('available_by',as_of)
        if node['operation']=='pre_event_state':
            cutoff=next(n for n in earlier if n['id']==node['inputs'][0])['parameters']['available_by']
        if cutoff>p['available_by']:raise ValueError('Bound evidence exceeds the reference review cutoff')
    return p

def prepare(path,p):
    ref=L.archived(path,p['reference_id']);state=L.state(path,ref['id'])
    if max(ref['published_on'],ref['checked_on'])>p['available_by']:
        raise ValueError('Reference publication or recorded inspection is later than the question cutoff')
    if set(b['question_key'] for b in p['bindings'])-{q['key'] for q in ref['questions']}:
        raise ValueError('Question binding is not in this exact reference version')
    row={'reference':ref,'lifecycle':state,'available_by':p['available_by'],'implementation':identity()}
    row['id']=digest(row);return row

def project(pid,prep,p,evidence_nodes,question):
    bindings={b['question_key']:b['node_ids'] for b in p['bindings']};questions=[]
    for q in prep['reference']['questions']:
        links=[]
        for ident in bindings.get(q['key'],[]):
            node,members=evidence_nodes[ident];ref=members.get(pid)
            links.append({'node_id':ident,'operation':node['operation'],'parameters':node['parameters'],
                          'patient_id':pid,'receipt':ref,'status':'linked_documentary_context' if ref else 'patient_not_evaluated_by_evidence_node'})
        questions.append({**q,'status':'applicability_unresolved','evidence_links':links,
            'evidence_status':'context_linked_requires_review' if any(x['receipt'] for x in links) else 'no_linked_patient_evidence',
            'binding_authority':'Question-plan proposal; relevance, sufficiency and applicability unadjudicated'})
    row={'schema':VERSION,'patient_id':pid,'original_question':question,'parameters':p,'reference':prep['reference'],
         'reference_dependency':prep['id'],'lifecycle':prep['lifecycle'],'questions':questions,
         'status':'applicability_unresolved' if prep['lifecycle']['status']=='available_unreviewed' else 'reference_unavailable_requires_reassessment',
         'reference_currency':{'checked_on':prep['reference']['checked_on'],'patient_decision_date':p['available_by'],
             'current_at_patient_decision':False,'reason':'A dated inspection and local lifecycle do not establish systematic update coverage at the decision date.'},
         'meaning':'Reference statements and reported evidence quality are separate from patient observations. No clinical eligibility, service nomination or effect estimate follows from a link.',**LIMITS}
    row['id']=digest(row);return row

def execute(path,patients,p,prep,results,node,question,selection):
    evidence_nodes={}
    for item in results:
        n=item['node']
        if n['id'] not in node['inputs'][1:]:continue
        members=item['result']['review_members'];ids=[m['patient_id'] for m in members]
        if len(set(ids))!=len(ids) or digest(sorted(ids))!=item['result']['population_digest']:
            raise ValueError('Reference evidence population does not reconcile')
        evidence_nodes[n['id']]=(n,{m['patient_id']:m for m in members})
    refs=[];linked=Counter();ids=sorted(patients)
    with sqlite3.connect(Path(path)/'stack.sqlite3') as db:
        for pid in ids:
            row=project(pid,prep,p,evidence_nodes,question)
            db.execute('INSERT OR IGNORE INTO result_cache VALUES (?,?)',(row['id'],json.dumps(row,allow_nan=False)))
            refs.append({'patient_id':pid,'receipt_id':row['id'],'receipt_digest':digest(row),'status':row['status'],
                         'value':None,'membership':None,'report_only':True,'care_account_counts':{},'review_topics':[]})
            linked.update(q['key'] for q in row['questions'] if q['evidence_status']=='context_linked_requires_review')
    if prepare(path,p)!=prep:raise ValueError('Reference changed during graph execution')
    return {'patients_evaluated':len(ids),'population_digest':digest(ids),'input_membership':selection,
        'review_members':refs,'member_status_counts':dict(Counter(r['status'] for r in refs)),
        'reference':prep['reference'],'reference_dependency':prep['id'],'lifecycle':prep['lifecycle'],
        'linked_patient_counts_by_question':dict(linked),'unresolved_questions':len(ids)*len(prep['reference']['questions']),
        'meaning':'Full scoped population; each linked observation needs applicability review. Not a clinically eligible cohort.',**LIMITS}

def dependencies(path,graph):
    return {x['node']['id']:prepare(path,x['node']['parameters'])['id'] for x in graph['results'] if x['node']['operation']==OPERATION}

def evidence(path,ref,node,member_states):
    with sqlite3.connect(Path(path)/'stack.sqlite3') as db:
        raw=db.execute('SELECT payload FROM result_cache WHERE id=?',(ref['receipt_id'],)).fetchone()
    if raw is None:raise ValueError('Reference member evidence missing')
    row=json.loads(raw[0]);R.verified(row)
    if row['patient_id']!=ref['patient_id'] or digest(row)!=ref['receipt_digest'] or row['parameters']!=node['parameters']:
        raise ValueError('Reference member identity or scope changed')
    if prepare(path,node['parameters'])['id']!=row['reference_dependency']:raise ValueError('Reference lifecycle changed; reanswer original question')
    for q in row['questions']:
        for link in q['evidence_links']:
            bound=next((m for m in member_states if m['node_id']==link['node_id']),None)
            expected={k:v for k,v in bound.items() if k not in ('node_id','operation')} if bound else None
            if expected!=link['receipt']:raise ValueError('Reference evidence does not match the exact patient graph node')
    gaps=[{'node_id':node['id'],'receipt_id':row['id'],'request_id':None,'target_record_id':None,
           'status':row['status'],'binding':q['key'],'reference_id':row['reference']['id'],
           'reference_dependency':row['reference_dependency'],'question':q['question'],'basis':q['basis'],
           'evidence_status':q['evidence_status'],'requested_action':'review_reference_applicability',
           'parameters':node['parameters'],'conditional_effect':'May change applicability assessment; source presence does not establish clinical relevance, eligibility or benefit.'}
          for q in row['questions']]
    return {'node_id':node['id'],'operation':OPERATION,'receipt_id':row['id'],'receipt_digest':digest(row),
        'title':row['reference']['title'],'parameters':node['parameters'],'membership':None,'report_only':True,
        'status':row['status'],'value':None,'report':row,'report_context':{},'care_accounts':[], 'review_topics':[],
        'authority':'reference_and_patient_evidence_kept_separate'},gaps
