"""Prospective, append-only dispatch lineage in the existing work database.

No model inputs, credentials or responses are copied into this journal. Execution
IDs are unique attempts, never cache keys. Historical reuse is not a new dispatch.
"""
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import time
import uuid
from ..contracts import digest

ACTIVE = ContextVar('agere_request_execution', default=None)
VERSION = 'question-request-lineage/1'
OPERATIONS = {
 '/api/stack/ask', '/api/stack/compose', '/api/stack/context/synthesize',
 '/api/stack/claims/reconcile', '/api/stack/source-plan-answers/compose',
 '/api/stack/decision/prepare', '/api/stack/decision/population-prepare',
 '/api/stack/decision/scoped-prepare',
 '/api/stack/decision/statement-review',
 '/api/stack/decision/statement-review-attach',
}
KEY = re.compile('[a-f0-9]{64}')


def database(path):
    from .clinical.question_decision import database as existing
    db = existing(path)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS question_request_events(
        execution TEXT, sequence INTEGER, payload TEXT NOT NULL,
        PRIMARY KEY(execution,sequence));
      CREATE TRIGGER IF NOT EXISTS question_request_events_update BEFORE UPDATE ON question_request_events
        BEGIN SELECT RAISE(ABORT,'Request history is immutable'); END;
      CREATE TRIGGER IF NOT EXISTS question_request_events_delete BEFORE DELETE ON question_request_events
        BEGIN SELECT RAISE(ABORT,'Request history is immutable'); END;
    ''')
    return db


def links(value):
    return {k:v for k,v in value.items() if k in ('answer_id','context_id','decision_id')
            and isinstance(v,str) and KEY.fullmatch(v)}


class Execution:
    def __init__(self,path,operation,request):
        self.path=Path(path);self.id=uuid.uuid4().hex;self.sequence=0
        self.started=time.perf_counter();self.cpu_started=time.thread_time()
        bound=links(request)
        if operation=='/api/stack/decision/statement-review-attach':
            from .clinical.question_decision import saved_followup
            try:bound.update(links(saved_followup(path,request.get('result_id'))))
            except (ValueError,TypeError):pass
        self.token=ACTIVE.set(self);self.closed=False
        try:self.append('started', operation=operation, request_digest=digest(request), input_links=bound,
                        received_body_bytes=len(json.dumps(request,allow_nan=False).encode()),
                        body_size_scope='Reserialized local API input; not outbound provider payload bytes')
        except BaseException:ACTIVE.reset(self.token);raise

    def append(self,kind,**data):
        event={'schema':VERSION,'execution_id':self.id,'sequence':self.sequence,
               'at':datetime.now(timezone.utc).isoformat(),'kind':kind,**data}
        db=database(self.path)
        try:
            with db:db.execute('INSERT INTO question_request_events VALUES (?,?,?)',
                (self.id,self.sequence,json.dumps(event,allow_nan=False)))
        finally:db.close()
        self.sequence+=1

    def finish(self,result=None):
        effort={'elapsed_seconds':time.perf_counter()-self.started,
                'server_thread_cpu_seconds':time.thread_time()-self.cpu_started,
                'scope':'Server handler after lock acquisition through result assembly; excludes queue wait, child processes, GPU and human effort.',
                'local_compute_cost_usd':None,'human_review_minutes':None,'human_review_cost_usd':None}
        if result is not None:
            bound=links(result)
            if result.get('schema') in ('original-question-decision/1','population-question-decision/1'):
                bound['decision_id']=result['id']
            planner=result.get('planner_receipt',result.get('planning',{}))
            route={k:planner[k] for k in ('route','original_route','method','escalation_reason','frontier_called','cache_hit') if k in planner}
            if result.get('route'):route['execution_route']=result['route']
            if result.get('schema')=='decision-statement-contract/1':
                route.update(role=result['role'],disposition=result['status'],
                             method='full_statement_contract',automatic_acceptance=False)
            if result.get('schema')=='decision-statement-review-outcome/1':
                route.update(method='existing_source_review_attachment',disposition=result['status'],
                             automatic_acceptance=False)
            reused=set()
            def collect(value):
                if isinstance(value,dict):
                    key=value.get('request_hash')
                    if isinstance(key,str) and KEY.fullmatch(key):reused.add(key)
                    for name,child in value.items():
                        if name!='token_check':collect(child)
                elif isinstance(value,list):
                    for child in value:collect(child)
            collect(result.get('usage',[]))
            collect(planner)
            self.append('completed',output_links=bound,route=route,
                returned_receipt_keys=sorted(reused),result_digest=digest(result),
                historical_receipts_are_new_dispatches=False,effort=effort,
                local_reading_id=result.get('reading_id'),
                review_outcome=({k:result[k] for k in ('result_id','reading_id','review_revision',
                                 'review_digest','task_id','task_revision','status','effort')}
                               if result.get('schema')=='decision-statement-review-outcome/1' else None),
                review_creation_reported=result.get('human_review_created'),
                local_reading_usage_scope='Reading identity only; cache/new inference and complete hardware cost are not inferred.')
        else:self.append('operation_failed', error_details='See original operation response; no inference success implied',effort=effort)

    def close(self):
        if not self.closed:ACTIVE.reset(self.token);self.closed=True


def begin(path,operation,request):
    return Execution(path,operation,request) if operation in OPERATIONS else None


def observe(kind,**data):
    active=ACTIVE.get()
    if active is not None:active.append(kind,**data)


def ledger_event(ledger,kind,key,**data):
    if ACTIVE.get() is None:return
    ledger_file=ledger.db.execute('PRAGMA database_list').fetchone()[2]
    observe(kind,request_hash=key,ledger_path=str(Path(ledger_file).resolve()),**data)


def for_decision(retriever,request,*,ledger_path=None):
    """Exact saved answer/context/decision joins; never question or patient guesses."""
    from .clinical import question_decision as q, decision_economics as costs
    q.care.exact(request,'decision_id')
    row=q.load(retriever.path,request['decision_id'])
    wanted={('decision_id',row['id'])}
    for name in ('answer_id','context_id'):
        if row.get(name):wanted.add((name,row[name]))
    # Saved group IDs are read from the immutable answer, not reconstructed bodies.
    if row.get('answer_id'):
        answer,_,_=q.source_binding(retriever,row['answer_id'])
        wanted.add(('context_id',answer['context_id']))
        for parent in answer.get('group_lineage',[]):
            wanted.update((k,parent[k]) for k in ('answer_id','context_id') if parent.get(k))
    db=database(retriever.path)
    try:raw=db.execute('SELECT execution,payload FROM question_request_events ORDER BY rowid').fetchall()
    finally:db.close()
    executions={}
    for execution,payload in raw:executions.setdefault(execution,[]).append(json.loads(payload))
    traces=[];refs={};expected=str(Path(ledger_path or costs.LEDGER).resolve())
    origins={};alternative_refs={};alternative_origins={}
    def include(key,execution,origin,was_inference=True,alternative=False):
        target=alternative_refs if alternative else refs
        origin_map=alternative_origins if alternative else origins
        ref=target.setdefault(key,{'roles':set(),'request_bytes':set(),'receipt_files':set(),'evidence':set()})
        ref['roles'].add(origin+':'+execution)
        if was_inference:ref['evidence'].add('saved_inference_usage')
        origin_map.setdefault(key,set()).add(origin)
        return ref
    for execution,events in executions.items():
        anchors=set()
        for event in events:
            anchors.update(event.get('input_links',{}).items());anchors.update(event.get('output_links',{}).items())
        if not anchors & wanted:continue
        decision_ids={v for k,v in anchors if k=='decision_id'}
        relation=('current_decision' if row['id'] in decision_ids else
                  'alternative_decision_same_evidence' if decision_ids else 'shared_upstream_evidence')
        alternative=relation=='alternative_decision_same_evidence'
        for event in events:
            # A saved model response still has a cost/history when its current
            # execution makes no reservation. Never count reuse as a new call.
            for key in event.get('returned_receipt_keys',[]):
                if isinstance(key,str) and KEY.fullmatch(key):include(key,execution,'returned_historical_receipt',alternative=alternative)
            key=event.get('request_hash')
            if key and event.get('ledger_path')==expected:
                include(key,execution,'observed_execution',event['kind'] in ('reserved','request_finished','cache_reused','retry_blocked'),alternative)
        terminal=events[-1]['kind']
        traces.append({'execution_id':execution,'status':terminal if terminal in ('completed','operation_failed') else 'interrupted_or_running',
                       'relation':relation,'matched_links':sorted(anchors & wanted),'events':events})
    requests,cost,binding=costs.account(refs,ledger_path or costs.LEDGER)
    alternative_requests,alternative_cost,alternative_binding=costs.account(alternative_refs,ledger_path or costs.LEDGER)
    for request_row in requests:request_row['lineage_origins']=sorted(origins[request_row['request_hash']])
    terminals=[t['events'][-1] for t in traces]
    measured=[e['effort'] for e in terminals if e.get('effort')]
    observations={'linked_executions':len(traces),'executions_with_measured_effort':len(measured),
        'execution_relations':{relation:sum(t['relation']==relation for t in traces) for relation in
                              ('current_decision','shared_upstream_evidence','alternative_decision_same_evidence')},
        'executions_without_measured_effort':len(traces)-len(measured),
        'recorded_elapsed_seconds':sum(e['elapsed_seconds'] for e in measured),
        'recorded_server_thread_cpu_seconds':sum(e['server_thread_cpu_seconds'] for e in measured),
        'observed_reservations':sum(e['kind']=='reserved' for t in traces for e in t['events']),
        'unique_returned_historical_keys':sum('returned_historical_receipt' in v for v in origins.values()),
        'recorded_no_review_created_executions':sum(e.get('review_creation_reported') is False for e in terminals),
        'review_creation_unreported_executions':sum(e.get('review_creation_reported') is None for e in terminals),
        'scope':'Measured handler effort across all displayed executions, including separately identified alternative decisions; historical/unfinished operations, subprocess/GPU work and reviewer time remain unknown. Not comparative latency or complete compute cost.',
        'full_cost_and_review_accounting_complete':False}
    return {'schema':VERSION,'decision_id':row['id'],'traces':traces,'requests':requests,'cost':cost,
        'ledger_binding':binding,'new_paid_api_calls':0,'execution_observations':observations,
        'alternative_routes':{'requests':alternative_requests,'cost':alternative_cost,'ledger_binding':alternative_binding,
            'overlapping_request_keys':sorted(set(refs)&set(alternative_refs)),
            'scope':'Other decisions on the same evidence. Excluded from this decision cost; overlapping shared keys mean these totals must not be summed.'},
        'scope':'Current-decision and shared-upstream observed attempts and returned historical inference keys are joined once to ledger usage. Other decisions on the same evidence are shown separately. Historical cost is not new spend. Missing ledger/usage, uninstrumented operations, local hardware and reviewer effort stay unknown. Pending means reserved, not proof a provider received it. Outbound body sizes are not captured by ledger instrumentation.',
        'clinical_or_model_qualification':False}
