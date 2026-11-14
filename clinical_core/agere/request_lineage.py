"""Prospective, append-only dispatch lineage in the existing work database.

No model inputs, credentials or responses are copied into this journal. Execution
IDs are unique attempts, never cache keys. Historical reuse is not a new dispatch.
"""
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re
import sqlite3
import time
import threading
import uuid
from ..contracts import digest

ACTIVE = ContextVar('agere_request_execution', default=None)
VERSION = 'question-request-lineage/1'
OPERATIONS = {
 '/api/stack/ask', '/api/stack/compose', '/api/stack/investigation/resume', '/api/stack/context/synthesize',
 '/api/stack/claims/reconcile', '/api/stack/source-plan-answers/compose',
 '/api/stack/decision/prepare', '/api/stack/decision/population-prepare',
 '/api/stack/decision/scoped-prepare',
 '/api/stack/decision/statement-review',
 '/api/stack/decision/statement-review-attach',
}
KEY = re.compile('[a-f0-9]{64}')
_TRACE_LOCK = threading.Lock()


def execution_observation(kind, **data):
    """Operational observations, never inference success or clinical acceptance.

    The optional JSONL sink also records worker threads, where a request context
    may be absent. Exact SQL is opt-in and stays in the local diagnostic sink.
    Failure of optional instrumentation must not change the tool's behavior.
    """
    active = ACTIVE.get()
    if active is not None:
        try:
            observe(kind, **data)
        except (OSError, sqlite3.Error) as exc:
            data = {**data, 'lineage_recording_error': type(exc).__name__}
    target = os.environ.get('AGERE_EXECUTION_TRACE')
    if target:
        event = {'at': datetime.now(timezone.utc).isoformat(), 'kind': kind,
                 'execution_id': active.id if active is not None else None,
                 'thread_id': threading.get_ident(), **data}
        try:
            with _TRACE_LOCK:
                path = Path(target)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('a') as stream:
                    stream.write(json.dumps(event, allow_nan=False) + '\n')
        except (OSError, TypeError, ValueError):
            pass  # optional local diagnostics cannot turn a successful tool into a failure


@contextmanager
def component(name, **identity):
    """Record actual invocation and completion; loading a model is not usage."""
    metrics = {}
    if ACTIVE.get() is None and not os.environ.get('AGERE_EXECUTION_TRACE'):
        yield metrics
        return
    invocation = uuid.uuid4().hex
    execution_observation('component_started', component=name, invocation_id=invocation, **identity)
    started = time.perf_counter()
    error = None
    try:
        yield metrics
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        execution_observation('component_finished', component=name, invocation_id=invocation,
                              status='failed' if error else 'completed', error_type=error,
                              elapsed_seconds=time.perf_counter()-started, metrics=metrics, **identity)


def trace_sql(db):
    """Opt-in expanded SQL before execution, including a statement that stalls.

    No progress handler, deadline or query transformation is installed here.
    Callback text is local diagnostics, not an answer or a computation receipt.
    """
    if not os.environ.get('AGERE_EXECUTION_TRACE') or os.environ.get('AGERE_EXECUTION_SQL_TRACE') != 'on':
        return db
    def started(sql):
        if sql.lstrip().upper().startswith(('SELECT', 'WITH')) and 'sqlite_master' not in sql:
            execution_observation('sql_statement_started', component='sqlite', sql=sql,
                                  query_digest=digest(sql), completion_not_established=True)
    db.set_trace_callback(started)
    return db


def database(path):
    from .clinical.question_decision import database as existing
    db = existing(path)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS question_request_events(
        execution TEXT, sequence INTEGER, payload TEXT NOT NULL,
        PRIMARY KEY(execution,sequence));
      CREATE TABLE IF NOT EXISTS question_progress_keys(tracking TEXT PRIMARY KEY,execution TEXT NOT NULL UNIQUE);
      CREATE TRIGGER IF NOT EXISTS question_progress_keys_update BEFORE UPDATE ON question_progress_keys
        BEGIN SELECT RAISE(ABORT,'Request progress binding is immutable'); END;
      CREATE TRIGGER IF NOT EXISTS question_progress_keys_delete BEFORE DELETE ON question_progress_keys
        BEGIN SELECT RAISE(ABORT,'Request progress binding is immutable'); END;
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
    def __init__(self,path,operation,request,*,tracking=None):
        self.path=Path(path);self.id=uuid.uuid4().hex;self.sequence=0
        self._append_lock=threading.Lock()
        self.started=time.perf_counter();self.cpu_started=time.thread_time()
        bound=links(request)
        if operation=='/api/stack/decision/statement-review-attach':
            from .clinical.question_decision import saved_followup
            try:bound.update(links(saved_followup(path,request.get('result_id'))))
            except (ValueError,TypeError):pass
        # ACTIVE is set only by `with` (__enter__) and always reset by __exit__ in a finally: constructing an Execution
        # never touches it, so a failing caller cannot leave ACTIVE pointing at a finished (or deleted) execution.
        self.token=None
        if tracking is not None:
            tracking_key(tracking)
            db=database(self.path)
            try:
                with db:db.execute('INSERT INTO question_progress_keys VALUES (?,?)',(digest(tracking),self.id))
            except sqlite3.IntegrityError:
                raise ValueError('This progress key already belongs to a recorded attempt; the request was not repeated')from None
            finally:db.close()
        self.append('started', operation=operation, request_digest=digest(request), input_links=bound,
                    received_body_bytes=len(json.dumps(request,allow_nan=False).encode()),
                    body_size_scope='Reserialized local API input; not outbound provider payload bytes')

    @property
    def closed(self):return self.token is None

    def __enter__(self):
        if self.token is None:self.token=ACTIVE.set(self)
        return self

    def __exit__(self,*exc):
        self.close();return False

    def append(self,kind,**data):
        # A copied request context can accompany independent source workers.
        # Reserve and commit each sequence together; the journal stays append-only.
        with self._append_lock:
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
                answer_status=(result.get('answer')or{}).get('status'),
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
        token,self.token=self.token,None
        if token is not None:ACTIVE.reset(token)


def begin(path,operation,request,*,tracking=None):
    return Execution(path,operation,request,tracking=tracking) if operation in OPERATIONS else None


def tracking_key(value):
    if not isinstance(value,str)or not re.fullmatch('[0-9a-f]{32}',value):
        raise ValueError('A random 32-character analysis progress key is required')
    return value


def progress(path,tracking,*,after=-1):
    """Read-only incremental presentation of this attempt's actual journal.

    Random per-attempt capability; never match by patient, question or another
    conversation. No SQL, prompts, record text, ledger paths or credentials leave
    this projection. A reserved provider call is not proof of a received response.
    """
    from .clinical.evaluation import operational_path
    tracking_key(tracking)
    if type(after)is not int or not -1<=after<2**63:raise ValueError('Invalid progress sequence')
    target=operational_path(path,'program-work.sqlite3')
    empty={'schema':'analysis-progress/1','status':'waiting','sequence':after,'updates':[],
           'answer_status':None,'observed_only':True}
    if not target.is_file():return empty
    db=sqlite3.connect(target.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_progress_keys'").fetchone():return empty
        row=db.execute('SELECT execution FROM question_progress_keys WHERE tracking=?',(digest(tracking),)).fetchone()
        if not row:return empty
        execution=row[0]
        rows=[json.loads(r[0])for r in db.execute('SELECT payload FROM question_request_events WHERE execution=? AND sequence>? ORDER BY sequence',(execution,after))]
        last=db.execute('SELECT payload FROM question_request_events WHERE execution=? ORDER BY sequence DESC LIMIT 1',(execution,)).fetchone()
    finally:db.close()
    if not last:return empty
    last=json.loads(last[0]);updates=[]
    labels={'proposer_tier':('understand','Checking an analysis approach'),
        'investigator_sql':('compute','Querying the connected records'),
        'governed_sql':('compute','Computing with the source data'),
        'analytical_engine':('compute','Running the requested analysis'),
        'claim_local_source_review':('verify','Checking statements against their sources')}
    for event in rows:
        kind=event['kind'];component=event.get('component');group,label,state=None,None,None
        if kind=='started':group,label,state='understand','Question received','completed'
        elif kind in ('component_started','component_finished')and component in labels:
            group,label=labels[component];state='running'if kind=='component_started'else event.get('status','completed')
            if state=='completed':label={'proposer_tier':'Analysis approach checked','investigator_sql':'Source query finished',
                'governed_sql':'Calculation finished','analytical_engine':'Analysis step finished',
                'claim_local_source_review':'Source check finished'}[component]
            elif state=='failed':label+=' · step stopped'
            metrics=event.get('metrics')or{}
            if component in ('investigator_sql','governed_sql')and state=='completed'and type(metrics.get('rows'))is int and metrics['rows']>=0:
                label+=f" · {metrics['rows']:,} result "+('row'if metrics['rows']==1 else'rows')+(' (partial)'if metrics.get('truncated')is True else'')
        elif kind in ('reserved','request_finished','cache_reused'):
            group='reason';state='running'if kind=='reserved'else'failed'if event.get('status')=='failed'else'completed'
            label={'reserved':'Model work scheduled','request_finished':'Model attempt returned','cache_reused':'Reused a saved model response'}[kind]
        elif kind=='completed':group,label,state='respond','Analysis returned','completed'
        elif kind=='operation_failed':group,label,state='respond','The request stopped; completed work remains recorded','failed'
        if label:
            updates.append({'sequence':event['sequence'],'at':event['at'],'group':group,'label':label,'state':state,
                'key':(event.get('invocation_id')or digest(['progress',event.get('request_hash')]))if kind in ('reserved','request_finished','cache_reused')else event.get('invocation_id'),
                'kind':kind,'component':component if component in labels else None})
    return {**empty,'sequence':last['sequence'],'status':'complete'if last['kind']=='completed'else'failed'if last['kind']=='operation_failed'else'active_or_interrupted',
            'last_observed_at':last['at'],'answer_status':last.get('answer_status'),'updates':updates}


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
    traces=[];refs={};expected=str(Path(ledger_path or costs._ledger()).resolve())
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
    requests,cost,binding=costs.account(refs,ledger_path or costs._ledger())
    alternative_requests,alternative_cost,alternative_binding=costs.account(alternative_refs,ledger_path or costs._ledger())
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
