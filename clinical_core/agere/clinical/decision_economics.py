"""Exact request accounting for a saved question; never dispatch or retry a model."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from ...contracts import digest
from ..economics import usage_cost
from . import question_decision as q

VERSION = 'question-route-accounting/1'
ROOT = Path(__file__).resolve().parents[3]
LEDGER = ROOT/'artifacts/agere-full/provider-budget.sqlite3'


def identity():
    return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        hashlib.sha256((ROOT/'config/agere/pricing.json').read_bytes()).hexdigest()])


def request_refs(value, role, refs):
    if isinstance(value, dict):
        key=value.get('request_hash')
        if isinstance(key,str) and re.fullmatch('[a-f0-9]{64}',key):
            ref=refs.setdefault(key,{'roles':set(),'request_bytes':set(),'receipt_files':set(),'evidence':set()})
            ref['roles'].add(role);ref['evidence'].add('saved_inference_usage')
        for child in value.values():request_refs(child,role,refs)
    elif isinstance(value,list):
        for child in value:request_refs(child,role,refs)


def add_body(body, role, out, refs):
    key=digest({'contract':'context-student/1','provider':'claude','body':body})
    ref=refs.setdefault(key,{'roles':set(),'request_bytes':set(),'receipt_files':set(),'evidence':set()})
    ref['evidence'].add('current_request_body')
    ref['roles'].add(role);ref['request_bytes'].add(len(json.dumps(body,allow_nan=False).encode()))
    ref['receipt_files'].add(str(out/(key+'.json')))


def linked_requests(retriever, row):
    refs={};gaps=[]
    if row['schema']==q.POPULATION_VERSION:
        return refs,gaps,{'selected_route':'local_population_contracts','baseline':'same deterministic contracts',
            'input_digest':digest(row['selection']),'selected_members':len(row['members']),
            'authority':'Documentary population assessment and service matching only; no clinical allocation quality qualification.'}
    answer,context,binding=q.source_binding(retriever,row['answer_id'])
    # Token-count receipts also use request_hash but are not inference ledger calls.
    request_refs(answer.get('usage',[]),'saved_source_interpretation',refs)
    for receipt in row['planning'].get('receipts',[]):
        request_refs(receipt.get('usage',{}),'documentary_planning',refs)
    baseline=q.review_plan(answer,context,binding)
    comparison={'selected_route':row['planning']['method'],'input_digest':binding['answer_digest'],
        'baseline':'cited_source_type_baseline','same_saved_answer_and_excerpts':True,
        'baseline_mapped_findings':len(answer['findings'])-len(baseline['unmapped_finding_ids']),
        'selected_mapped_findings':len(answer['findings'])-len(row['planning']['unmapped_finding_ids']),
        'baseline_unmapped_finding_ids':baseline['unmapped_finding_ids'],
        'selected_unmapped_finding_ids':row['planning']['unmapped_finding_ids'],
        'semantic_or_clinical_accuracy':'unassessed; mapped coverage is not correctness',
        'fallback':'Unmapped findings remain unresolved. Hosted candidate is explicit and unqualified; no automatic promotion.'}
    parents=answer.get('group_lineage') or [{'answer_id':answer['id'],'context_id':answer['context_id']}]
    for parent in parents:
        cid=parent['context_id']
        try:
            stage,reconciliation=q.answers.stages(retriever.path,cid)
            _,_,_,packets,key=stage.prepare(retriever.path,cid)
            for body,_ in packets:add_body(body,'source_pass:'+cid,stage.location(retriever.path,key).parent/'provider',refs)
            # Inspect the current exact reconciliation even if it failed after successful passes.
            if stage.exists(retriever.path,key):
                source=stage.inspect(retriever.path,key)
                request_refs(source.get('usage',[]),'saved_source_pass:'+cid,refs)
                if len(source.get('passes',[]))>1:
                    try:
                        _,_,_,jobs,rkey=reconciliation.prepare(retriever.path,key)
                        for job in jobs:add_body(job['body'],'reconciliation:'+cid,reconciliation.location(retriever.path,rkey).parent/'provider',refs)
                    except (ValueError,KeyError,FileNotFoundError) as exc:
                        gaps.append({'stage':'reconciliation','context_id':cid,'reason':str(exc)[:300]})
        except (ValueError,KeyError,FileNotFoundError) as exc:
            gaps.append({'stage':'source_request_reconstruction','context_id':cid,'reason':str(exc)[:300]})
    if row['planning']['method']=='buyer_documentary_candidate':
        from . import decision_planner as P
        wire=P.inputs(answer,context,binding,q.care.portfolio(retriever.path,row['buyer_id']))
        for group in P.batches(wire):add_body(P.body(group),'documentary_planning',Path(retriever.path)/'decision-planning',refs)
    return refs,gaps,comparison


def account(refs,ledger_path):
    """Deduplicate exact calls; reservations are never treated as spent dollars."""
    rows=[];binding=[]
    with sqlite3.connect(Path(ledger_path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        for key,ref in sorted(refs.items()):
            raw=db.execute('SELECT provider,model,reserved,status,response,seconds FROM calls WHERE id=?',(key,)).fetchone()
            absent='missing_ledger_entry' if 'saved_inference_usage' in ref.get('evidence',set()) else 'not_dispatched'
            provider,model,reserved,status,response,seconds=raw if raw else (None,None,None,absent,None,None)
            usage=None;basis=None;files=[]
            if response:
                payload=json.loads(response);usage=payload.get('usage');basis='validated_ledger_response'
            elif status=='failed':
                for file in sorted(ref['receipt_files']):
                    p=Path(file)
                    if p.is_file():
                        payload=json.loads(p.read_text());files.append({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
                        returned=payload.get('response',{})
                        if returned.get('model')==model:
                            usage=returned.get('usage');basis='retained_failed_response'
                            break
            price=usage_cost(model,usage) if usage is not None else None
            cost=price['estimated_usd'] if price else None
            rows.append({'request_hash':key,'roles':sorted(ref['roles']),'provider':provider,'model':model,'status':status,
                'request_bytes':sorted(ref['request_bytes']),'bounded_request_verified':bool(ref['request_bytes']) and max(ref['request_bytes'])<=20000,
                'historical_seconds':seconds,'accounting_reservation_usd':reserved,
                'recorded_usage':usage,'usage_basis':basis,'estimated_api_usd':cost,
                'price_basis':price,'invoice_verified':False,
                'replay_policy':'validated exact cache only' if status=='validated' else 'not dispatched by this audit' if status=='not_dispatched' else 'failed or pending key cannot be retried automatically'})
            binding.append({'request_hash':key,'ledger_row_digest':digest(list(raw)) if raw else None,'failed_receipt_files':files})
    dispatched=[r for r in rows if r['status']!='not_dispatched'];known=sum(r['estimated_api_usd'] for r in dispatched if r['estimated_api_usd'] is not None)
    unknown=sum(r['estimated_api_usd'] is None for r in dispatched)
    return rows,{'unique_linked_requests':len(rows),'dispatched_requests':len(dispatched),'statuses':dict(Counter(r['status'] for r in rows)),
        'known_linked_api_estimate_usd':known,'unpriced_dispatched_requests':unknown,
        'linked_api_estimate_usd':None if unknown else known,'reservations_are_spend':False,
        'new_paid_api_calls':0,'unlinked_historical_attempt_cost_usd':None,'local_compute_cost_usd':None,
        'human_review_minutes':None,'human_review_cost_usd':None,'total_cost_usd':None},binding


def prepare(retriever,request,*,ledger_path=None):
    q.care.exact(request,'decision_id')
    row=q.inspect(retriever,request['decision_id'])
    refs,gaps,route=linked_requests(retriever,row)
    requests,cost,ledger_binding=account(refs,ledger_path or LEDGER)
    # q.inspect has validated current dependencies. Read only the existing owned
    # work here; calling full reopen would repeat costly source reconstruction.
    if row['schema']==q.POPULATION_VERSION:
        work=[{**t,'binding_current':True} for t in q.population_work_binding(retriever.path,row)]
    else:
        with q.database(retriever.path) as db:
            links=[json.loads(r[0]) for r in db.execute('SELECT payload FROM question_decision_tasks WHERE decision=? ORDER BY task',(row['id'],))]
        work=[]
        for link in links:
            task=q.care_readiness.task(retriever.path,link['task_id'])
            if task['patient_id']!=row['patient_id'] or task['plan_id']!=link['plan_id'] or task['option_id']!=link['option_id']:
                raise ValueError('Question work binding changed')
            work.append({'task':task,'decision_binding':link,'binding_current':True})
    binding={'version':identity(),'decision_id':row['id'],'decision_digest':digest(row),
        'request_refs':{k:{n:sorted(v) for n,v in ref.items()} for k,ref in sorted(refs.items())},
        'ledger':ledger_binding,'work_digest':digest(work)}
    return {'schema':VERSION,'decision_id':row['id'],'question':row['question'],'request':request,'binding':binding,
        'route':route,'requests':requests,'cost':cost,'work':work,'reconstruction_gaps':gaps,
        'scope':'Exact request keys in this saved answer/planner plus reconstructible current source/reconciliation requests. Earlier changed-scope requests, pre-index ingestion/learning and external review are not fully attributable here; their cost is unknown, not zero.',
        'coverage':row.get('answer_coverage',row.get('counts',{})),
        'stage_quality':{'retrieval':'Source selection sufficiency is not established by this cost audit.',
            'reranking':'No new reranking or relevance evaluation.',
            'reader':'Saved machine findings remain unqualified; complete source transport is not semantic accuracy.',
            'planning':'Same-answer baseline comparison only; default activation unchanged.',
            'service_matching':'Documentary match and payer prerequisites do not establish clinical need or benefits.'},
        'full_cost_accounting_complete':False,'clinical_execution_authorized':False,'new_paid_api_calls':0}


def run(retriever,request,*,ledger_path=None):
    result=prepare(retriever,request,ledger_path=ledger_path)
    if prepare(retriever,request,ledger_path=ledger_path)!=result:
        raise q.care.work.WorkConflict('Request ledger or question/work changed during accounting; nothing saved')
    return q.save_followup(retriever.path,result)


def reopen(retriever,request,*,ledger_path=None):
    q.care.exact(request,'decision_id result_id')
    saved=q.saved_followup(retriever.path,request['result_id'])
    if saved.get('schema')!=VERSION or saved['decision_id']!=request['decision_id']:raise ValueError('Accounting result belongs to another question')
    try:
        current=prepare(retriever,saved['request'],ledger_path=ledger_path)
        if current!={k:v for k,v in saved.items() if k!='result_id'}:raise ValueError('Question, ledger, prices or work changed')
        ok=True;reason=None
    except (ValueError,KeyError,FileNotFoundError) as exc:ok=False;reason=str(exc)
    return {'report':saved,'current':ok,'stale_reason':reason,'new_paid_api_calls':0}
