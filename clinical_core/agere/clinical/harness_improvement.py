"""Fixed-weight recursive harness improvement at the existing model/tool boundary.

Completed trajectories propose literal action prefixes. A fresh native executor
validates their outputs before selection; complete downstream outcomes activate
them. The next outcome feeds the next revision. Source/meaning changes abstain.
This learns execution, never clinical definitions or a supposed correct answer.
Only these new operational tables are read; legacy learning/answer stores are not.
"""
import json
import os
import sqlite3
from pathlib import Path
from ...contracts import digest
from ..request_lineage import execution_observation

from contextvars import ContextVar
from contextlib import contextmanager

_OWNED_RECOVERY = ContextVar('agere_owned_investigation_recovery',default=None)

@contextmanager
def owned_recovery(actions,question=None,feedback=None):
    """Explicit unfinished-run recovery; never select a learned configuration or reuse an answer."""
    token=_OWNED_RECOVERY.set({'actions':actions,'question':question,'feedback':feedback} if actions is not None else None)
    try: yield
    finally: _OWNED_RECOVERY.reset(token)


VERSION='recursive-harness/1'
READ_ONLY={'query_sql','list_documents','read_document','review_documents','execute_metric','list_metrics',
           'list_analyses','describe_analysis','execute_analysis'}
VOLATILE={'accounting','seconds','elapsed_s','elapsed_seconds','cache_hit','compute','new_paid_api_calls','known_api_estimate_usd',
          'estimated_cost_usd','provider_receipts','typed_checks'}


def enabled():
    from .evaluation import active
    return not active() and os.environ.get('AGERE_RECURSIVE_IMPROVEMENT','off')=='on'


def semantic_result(value):
    if isinstance(value,dict):return {k:semantic_result(v) for k,v in value.items() if k not in VOLATILE}
    if isinstance(value,list):return [semantic_result(v) for v in value]
    return value


def database(path):
    from .question_decision import database as operational
    db=operational(path)
    db.executescript('''CREATE TABLE IF NOT EXISTS harness_candidates(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS harness_candidate_events(seq INTEGER PRIMARY KEY,candidate TEXT,kind TEXT,payload TEXT NOT NULL);
      CREATE TRIGGER IF NOT EXISTS harness_candidates_immutable BEFORE UPDATE ON harness_candidates BEGIN SELECT RAISE(ABORT,'Immutable harness revision');END;
      CREATE TRIGGER IF NOT EXISTS harness_events_immutable BEFORE UPDATE ON harness_candidate_events BEGIN SELECT RAISE(ABORT,'Immutable harness event');END;
      CREATE TRIGGER IF NOT EXISTS harness_candidates_no_delete BEFORE DELETE ON harness_candidates BEGIN SELECT RAISE(ABORT,'Immutable harness revision');END;
      CREATE TRIGGER IF NOT EXISTS harness_events_no_delete BEFORE DELETE ON harness_candidate_events BEGIN SELECT RAISE(ABORT,'Immutable harness event');END;''')
    return db


def event(db,ident,kind,payload):
    db.execute('INSERT INTO harness_candidate_events(candidate,kind,payload) VALUES (?,?,?)',
               (ident,kind,json.dumps(payload,sort_keys=True)))


def identity(tools,question,context,model):
    db=database(tools.stack.path)
    try:feedback_revision=db.execute("SELECT COALESCE(MAX(seq),0) FROM harness_candidate_events WHERE kind='physician_feedback'").fetchone()[0]
    finally:db.close()
    return digest({'schema':VERSION,'question':question,'context':context,'model':model,
                   'guard':tools.continuation_identity(),
                   'physician_feedback_revision':feedback_revision,
                   'controller':digest(Path(__file__).read_text())})


def latest(path,key,*,selected=True):
    db=database(path)
    try:
        found=[]
        for ident,payload in db.execute('SELECT id,payload FROM harness_candidates'):
            row=json.loads(payload)
            if row['identity']!=key:continue
            states=db.execute("SELECT kind FROM harness_candidate_events WHERE candidate=? AND kind IN ('selected','quarantined') ORDER BY seq DESC LIMIT 1",(ident,)).fetchone()
            if not selected or states and states[0]=='selected':found.append({**row,'id':ident})
        return max(found,key=lambda r:(r['round'],len(r['actions']))) if found else None
    finally:db.close()


def record_feedback(path,candidate_id,*,accepted,reason):
    """Explicit physician feedback invalidates prior reuse guards; never infer it from model grades."""
    if type(accepted) is not bool or not isinstance(reason,str) or not reason.strip():
        raise ValueError('Physician feedback needs an explicit judgment and reason')
    db=database(path)
    try:
        if not db.execute('SELECT 1 FROM harness_candidates WHERE id=?',(candidate_id,)).fetchone():
            raise ValueError('Unknown harness candidate')
        with db:
            event(db,candidate_id,'physician_feedback',{'accepted':accepted,'reason':reason})
            if not accepted:event(db,candidate_id,'quarantined',{'reason':reason})
    finally:db.close()


def record_question_correction(path,question,reason):
    """Retire old reuse even when an older saved answer lacks candidate lineage."""
    db=database(path)
    try:
        with db:event(db,None,'physician_feedback',{'accepted':False,'question_digest':digest(question),'reason':reason})
    finally:db.close()


def recover_actions(tools,question):
    saved=_OWNED_RECOVERY.get()
    if saved is None or saved['question']!=question:return None
    actions=saved['actions']
    guard=digest(tools.continuation_identity())
    out={'used':None,'calls':[],'certificates':[],'final_answer':None,'reason':'Explicit owned-run recovery: every literal tool is re-executed; only exact provider requests may hit cache.'}
    for action in actions:
        if action.get('name') not in READ_ONLY or set(action)!={'name','input'}:
            raise ValueError('Owned recovery accepts registered read-only actions only')
        if digest(tools.continuation_identity())!=guard:raise ValueError('Source/scope changed during owned recovery')
        try: value=tools(action)
        except (ValueError,sqlite3.Error) as exc:value={'error':str(exc)}
        out['calls'].append({'name':action['name'],'input':action['input'],'result':value,
                             'sql':value.get('executed_query') if isinstance(value,dict) else None,'owned_run_recovery':True})
        if digest(tools.continuation_identity())!=guard:raise ValueError('Source/scope changed during owned recovery')
        if isinstance(value,dict) and value.get('error'):break
    out['recovered_read_only_actions']=len(out['calls'])
    out['downstream_review_feedback']=saved.get('feedback') or []
    if out['downstream_review_feedback']:
        out['feedback_instruction']='Investigate these prior presentation-review error reports against current sources and existing engines. Repair upstream facts/calculations here, not by rewording a dashboard. The reports are machine observations, not clinical truth. Do not invent eligibility, enrollment or shipment to satisfy a proposed design.'
    return out


def attempt_replay(tools,*args,**kwargs):
    recovery=recover_actions(tools,args[0] if args else kwargs.get('question'))
    if recovery is not None:return recovery
    report={}
    try:return replay(tools,*args,**kwargs,_report=report)
    except (sqlite3.Error,OSError) as exc:
        # Reuse bookkeeping is optional. Preserve any native executions that
        # completed before an outage so the actor sees them, without reusing a draft.
        for key,value in {'used':None,'calls':[],'certificates':[]}.items():report.setdefault(key,value)
        report.update(final_answer=None,reason='Harness storage unavailable; ordinary actor execution: '+str(exc))
        return report


def attempt_propose(*args,**kwargs):
    try:return propose(*args,**kwargs)
    except (sqlite3.Error,OSError) as exc:
        return {'status':'unavailable','reason':'Harness storage unavailable: '+str(exc)}


def failed(path,question,error):
    """Retain failed-request feedback without deriving actions or answer keys from it."""
    if not enabled():return
    try:
        db=database(path)
        try:
            with db:event(db,None,'failed_request',{'question_digest':digest(question),'error_type':type(error).__name__,
                'kind':getattr(error,'kind',None),'reason':str(error)[:1000]})
        finally:db.close()
    except (sqlite3.Error,OSError) as exc:
        error.harness_bookkeeping_error=str(exc)


def replay(tools,question,context,model,*,_report=None):
    report=_report if _report is not None else {}
    report.update(schema=VERSION,used=None,calls=[],certificates=[],final_answer=None,reason='No validated configuration for this exact source/meaning/model')
    if not enabled():return report
    key=identity(tools,question,context,model);config=latest(tools.stack.path,key)
    if not config:return report
    report.update(used=config['id'],round=config['round'],reason=None)
    for action in config['actions']:
        if identity(tools,question,context,model)!=key:
            report['reason']='Source or meaning changed; actor resumes';break
        try:out=tools({'name':action['name'],'input':action['input']})
        except (ValueError,sqlite3.Error) as exc:out={'error':str(exc)}
        except OSError as exc:
            report['reason']=str(exc);break
        call={'name':action['name'],'input':action['input'],'result':out,
              'sql':out.get('executed_query') if isinstance(out,dict) else None,
              'actor_callback_id':action['actor_callback_id'],'replayed':True}
        report['calls'].append(call)
        if identity(tools,question,context,model)!=key:
            report['reason']='Source or meaning changed during execution; actor must recheck retained results';break
        if digest(semantic_result(out))!=action['expected_result']:
            report['reason']='Native receipt differs from validation; actor resumes with actual results';break
        certificate={'candidate':config['id'],'substituted_callback_id':action['actor_callback_id'],
                     'literal_action_digest':digest([action['name'],action['input']]),'receipt_digest':action['expected_result'],
                     'identity':key,'native_executor_used':True,'actor_called_between_actions':False}
        report['certificates'].append(certificate)
        execution_observation('certified_callback_substitution',**certificate)
    if report['reason']:
        db=database(tools.stack.path)
        try:
            with db:event(db,config['id'],'quarantined',{'reason':report['reason']})
        finally:db.close()
    else:
        report['final_answer']=config.get('final_answer')
    return report


def propose(tools,question,context,model,calls,make_executor,final_answer=None,baseline_metrics=None):
    """Training feedback -> candidate -> fresh-executor validation -> pending selection.

    No model callback is removed until validation has passed AND the full request
    finishes. The final actor and evidence reviews always run after replay.
    """
    if not enabled():return None
    key=identity(tools,question,context,model);selected=latest(tools.stack.path,key);parent=latest(tools.stack.path,key,selected=False)
    actions=[]
    for c in calls:
        out=c.get('result')
        if c.get('name') not in READ_ONLY or not c.get('actor_callback_id') or not isinstance(out,dict):break
        actions.append({'name':c['name'],'input':c['input'],'expected_result':digest(semantic_result(out)),
                        'actor_callback_id':c['actor_callback_id']})
    if not actions:return None
    if selected and len(actions)<=len(selected['actions']) and (selected.get('final_answer') or final_answer is None):return None
    candidate={'schema':VERSION,'identity':key,'parent':parent['id'] if parent else None,
               'round':parent['round']+1 if parent else 1,'actions':actions,
               'final_answer':final_answer if len(actions)==len(calls) else None,
               'workflow_contract':(final_answer or {}).get('workflow') or [],
               'baseline_actor_metrics':baseline_metrics or {},
               'selection_order':['identical native tool outputs under exact guards','fewer actor callbacks','elapsed time'],
               'qualification':'Execution equivalence only; no clinical accuracy claim or model-weight change'}
    ident=digest(candidate);db=database(tools.stack.path)
    try:
        with db:
            db.execute('INSERT OR IGNORE INTO harness_candidates VALUES (?,?)',(ident,json.dumps(candidate,sort_keys=True)))
            event(db,ident,'candidate',{'training_feedback':'Complete checked analysis supplied the current native action trajectory',
                                      'failed_request_feedback_events':[seq for seq,payload in db.execute(
                                          "SELECT seq,payload FROM harness_candidate_events WHERE kind='failed_request'")
                                          if json.loads(payload).get('question_digest')==digest(question)],
                                      'parent_callbacks':len({a['actor_callback_id'] for a in parent['actions']}) if parent else 0})
        fresh=make_executor()
        validation=[]
        try:
            for action in actions:
                if identity(fresh,question,context,model)!=key:raise ValueError('Source/meaning changed during validation')
                try:out=fresh({'name':action['name'],'input':action['input']})
                except (ValueError,sqlite3.Error) as exc:out={'error':str(exc)}
                same=digest(semantic_result(out))==action['expected_result']
                validation.append({'action':digest([action['name'],action['input']]),'same_output':same})
                if not same:raise ValueError('Candidate changed a native tool output')
                if identity(fresh,question,context,model)!=key:raise ValueError('Source/meaning changed during validation')
            with db:event(db,ident,'validated',{'receipts':validation,
                'callbacks_eligible_for_substitution':len({a['actor_callback_id'] for a in actions}),
                'new_paid_api_calls':fresh.subtool_accounting['new_paid_api_calls']})
        except Exception as exc:
            if getattr(exc,'kind',None)=='rate_limit':raise
            with db:event(db,ident,'quarantined',{'reason':type(exc).__name__+': '+str(exc)[:500]})
            return {'id':ident,'status':'quarantined','validation':validation}
        finally:
            tools.subtool_accounting['new_paid_api_calls']+=fresh.subtool_accounting['new_paid_api_calls']
            tools.subtool_accounting['estimated_cost_usd']+=fresh.subtool_accounting['estimated_cost_usd']
            tools.subtool_accounting['api_cost_known']=tools.subtool_accounting.get('api_cost_known',True) and fresh.subtool_accounting.get('api_cost_known',True)
            fresh.db.close();fresh.semantic.close()
        return {'id':ident,'status':'validated_pending_complete_workflow','round':candidate['round'],
                'actions':len(actions),'callbacks':len({a['actor_callback_id'] for a in actions}),'validation':validation}
    finally:db.close()


def _finish(path,result):
    """The completed request is feedback for selection and the next recursive round."""
    if not enabled():return
    info=(result.get('investigation') or {}).get('recursive_improvement') or {}
    candidate=info.get('candidate') or {};used=(info.get('replay') or {}).get('used')
    ident=candidate.get('id') or used
    # Incomplete investigations are feedback even if no configuration exists
    # yet. They cannot produce a selected execution path or a correct-answer key.
    from .workflow_contract import initialized
    complete=(result.get('answer') or {}).get('status')=='answered' and all(initialized(s) for s in result.get('workflow',[]))
    db=database(path)
    try:
        stored=db.execute('SELECT payload FROM harness_candidates WHERE id=?',(ident,)).fetchone() if ident else None
        expected=(json.loads(stored[0]).get('workflow_contract') or []) if stored else []
        actual_steps=result.get('workflow') or []
        complete=complete and [(s.get('kind'),s.get('quote')) for s in actual_steps]==[
            (s.get('kind'),s.get('quote')) for s in expected]
        regression=False
        actual=info.get('actor_metrics') or {}
        receipt=result.get('cost_receipt') or {}
        full_cost={'api_usd':receipt.get('usd'),'paid_calls':receipt.get('paid_calls'),
                   'usd_known':bool(receipt.get('usd_known')),
                   'scope':'All ledger-attributed API attempts for this workflow; local compute, development and physician review remain unpriced'}
        baseline_full={};full_regression=False
        if used:
            stored=db.execute('SELECT payload FROM harness_candidates WHERE id=?',(used,)).fetchone()
            baseline=(json.loads(stored[0]).get('baseline_actor_metrics') or {}) if stored else {}
            old,new=baseline.get('estimated_cost_usd'),actual.get('estimated_cost_usd')
            regression=old is not None and new is not None and new>old+1e-12
            previous=db.execute("SELECT payload FROM harness_candidate_events WHERE candidate=? AND kind='outcome' ORDER BY seq LIMIT 1",(used,)).fetchone()
            baseline_full=(json.loads(previous[0]).get('full_request_cost') or {}) if previous else {}
            old,new=baseline_full.get('api_usd'),full_cost.get('api_usd')
            full_regression=(baseline_full.get('usd_known') and full_cost['usd_known'] and old is not None and new is not None and new>old+1e-12)
        any_regression=bool(regression or full_regression)
        with db:
            event(db,ident,'outcome',{'complete_request':complete,'workflow_statuses':[s.get('status') for s in result.get('workflow',[])],
                                    'feedback':(result.get('answer') or {}).get('missing',[]),'actor_metrics':actual,
                                    'observed_actor_cost_regression':regression,
                                    'full_request_cost':full_cost,'baseline_full_request_cost':baseline_full,
                                    'observed_full_request_api_cost_regression':bool(full_regression),
                                    'total_cost_qualified':False,
                                    'cost_scope':'API estimates include readers, review, repair, validation and artifacts when ledger attribution is complete. Total cost remains unmeasured.'})
            if candidate.get('status')=='validated_pending_complete_workflow':
                event(db,ident,'selected' if complete and not any_regression else 'quarantined',{'reason':'Full request and native-output validation passed; total cost benefit remains unqualified' if complete and not any_regression else 'Incomplete request or observed cost regression; retain known-good configuration'})
            if used and (not complete or any_regression):
                event(db,used,'quarantined',{'reason':'Complete request not established or observed cost regressed; resume the retained actor on the next request'})
    finally:db.close()


def finish(path,result):
    try:_finish(path,result)
    except (sqlite3.Error,OSError) as exc:
        result.setdefault('investigation',{}).setdefault('recursive_improvement',{})['bookkeeping_error']=str(exc)
