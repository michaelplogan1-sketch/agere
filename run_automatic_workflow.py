"""Ordinary compose runner with an offline preflight and explicit retained cache.

Preflight never loads credentials or creates a live Stack. --execute is for a
human-authorized run after subscription availability; it never waits/retries.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import time
import traceback
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def source_fingerprint():
    h=hashlib.sha256()
    for path in sorted([*ROOT.joinpath('clinical_core').rglob('*.py'),*ROOT.joinpath('tools/training').rglob('*.py'),
                        ROOT/'config/agere/learning/offline-candidates.json',Path(__file__)]):
        h.update(str(path.relative_to(ROOT)).encode());h.update(b'\0');h.update(path.read_bytes());h.update(b'\0')
    return h.hexdigest()


def ledger_status(path):
    if not path.is_file():return {'exists':False,'calls':{}}
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        return {'exists':True,'calls':dict(db.execute('SELECT status,COUNT(*) FROM calls GROUP BY status'))}


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime',required=True,type=Path)
    p.add_argument('--request',required=True,type=Path)
    p.add_argument('--ledger',required=True,type=Path,help='Explicit shared cache ledger; retain the interrupted run ledger to reuse exact validated requests')
    p.add_argument('--output',required=True,type=Path,help='New per-attempt receipt directory')
    p.add_argument('--expect-source',help='Required with --execute; fingerprint printed by offline preflight')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--resume-attempt',type=Path,help='Explicit owned unfinished attempt; re-execute its literal read-only tools on the same question/source, never reuse its final answer')
    p.add_argument('--rsi',action='store_true',help='Opt into candidate RSI qualification; default off')
    p.add_argument('--bulk-note-ner',action='store_true',help='Explicitly enable optional warehouse-wide NER during materialization; full documents remain available either way')
    p.add_argument('--component-learning',action='store_true',help='Collect new isolated learning observations without legacy stores')
    p.add_argument('--component-release-root',type=Path,help='Explicit isolated component release registry; defaults to this runtime/component-learning when enabled')
    p.add_argument('--learning-plan',type=Path,help='Explicit offline component-learning-plan/1; no automatic provider use')
    return p


def retained_actions(directory, runtime, request):
    from clinical_core.agere.deliverables import answer_context as A
    from clinical_core.agere.clinical.harness_improvement import READ_ONLY
    result_path=directory/'result.json'
    result=json.loads(result_path.read_text())
    preflight=json.loads((directory/'preflight.json').read_text())
    if Path(preflight['runtime']).resolve()!=runtime.resolve() or result.get('question')!=request['question'] or result.get('synthetic') is not True:
        raise ValueError('Recovery question/runtime/synthetic binding differs')
    request_digest=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
    if preflight.get('request_digest'):
        if preflight['request_digest']!=request_digest:raise ValueError('Recovery input scope/context changed')
    elif set(request)!={'question','scope'} or request.get('scope')!='warehouse':
        raise ValueError('Legacy recovery requires the explicit original warehouse scope')
    context=A.load(runtime,(result.get('action_context') or {}).get('id'))
    if not preflight.get('request_digest') and context.get('input_patient_ids') is not None:
        raise ValueError('Legacy recovery cannot change the selected population')
    if context['source']!=A.source_identity(runtime) or context['question']!=request['question']:
        raise ValueError('Recovery warehouse source/question changed')
    calls=(result.get('investigation') or {}).get('tool_calls') or []
    actions=[]
    for c in calls:
        if c.get('name') not in READ_ONLY or not isinstance(c.get('input'),dict):raise ValueError('Recovery contains an unsupported tool')
        actions.append({'name':c['name'],'input':c['input']})
    if not actions:raise ValueError('Recovery has no retained native actions')
    return actions,{'attempt':str(directory.resolve()),'result_sha256':hashlib.sha256(result_path.read_bytes()).hexdigest(),
                    'read_only_actions':len(actions),'reuse_final_answer':False,'reexecute_native_tools':True}


def run(a):
    request=json.loads(a.request.read_text())
    if not isinstance(request,dict) or not isinstance(request.get('question'),str) or not request['question'].strip():
        raise ValueError('Request must be an ordinary compose body with a nonempty question')
    if not (a.runtime/'semantic.sqlite3').is_file():raise ValueError('Received-source runtime semantic warehouse missing')
    learning_plan=getattr(a,'learning_plan',None)
    if learning_plan is not None:
        if not getattr(a,'component_learning',False):raise ValueError('--learning-plan requires --component-learning')
        plan=json.loads(learning_plan.read_text())
        if plan.get('schema')!='component-learning-plan/1' or not isinstance(plan.get('jobs'),list):raise ValueError('Explicit component-learning-plan/1 required')
    fingerprint=source_fingerprint()
    if a.expect_source and a.expect_source!=fingerprint:raise ValueError('Source changed after offline verification; freeze and verify again')
    recovery_actions=None;recovery_receipt=None
    if getattr(a,'resume_attempt',None) is not None:
        recovery_actions,recovery_receipt=retained_actions(a.resume_attempt,a.runtime,request)
    before=ledger_status(a.ledger)
    release_root=getattr(a,'component_release_root',None) or (a.runtime/'component-learning' if getattr(a,'component_learning',False) else None)
    preflight={'schema':'automatic-workflow-run/1','status':'offline_preflight','source_fingerprint':fingerprint,
               'request_digest':hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest(),'runtime':str(a.runtime.resolve()),'retained_cache_ledger':str(a.ledger.resolve()),'ledger_before':before,
               'provider_calls':0,'owned_recovery':recovery_receipt,'transport':'subscription','bulk_note_ner':bool(getattr(a,'bulk_note_ner',False)),'rsi':'candidate_on' if a.rsi else 'off',
               'component_learning':bool(getattr(a,'component_learning',False)),
               'component_release_root':str(release_root.resolve()) if release_root is not None else None,
               'learning_plan':str(a.learning_plan.resolve()) if getattr(a,'learning_plan',None) is not None else None,
               'learning_plan_sha256':hashlib.sha256(learning_plan.read_bytes()).hexdigest() if learning_plan is not None else None,
               'reuse':'Exact validated request matches only; changed prompts/source requirements must be recomputed'}
    if not a.execute:return preflight
    if not a.expect_source:raise ValueError('--execute requires the verified --expect-source fingerprint')
    if before['calls'].get('pending'):raise ValueError('Retained ledger has pending work; inspect its owner before explicit recovery')
    a.output.mkdir(parents=True,exist_ok=False)
    (a.output/'preflight.json').write_text(json.dumps(preflight,indent=2)+'\n')
    # Set transport before key loading and sender imports. No external Claude
    # API fallback, scheduler, retry loop or automatic limit reset is installed.
    for k,v in {'AGERE_CLAUDE':'on','AGERE_CLAUDE_TRANSPORT':'subscription','AGERE_EVALUATION':'0','AGERE_LEARNED':'off',
                'AGERE_LOCAL_TYPED':'off','AGERE_LOCAL_NORMALIZER':'off','AGERE_LOCAL_QWEN':'off','AGERE_LOCAL_MODEL':'off',
                'AGERE_LOCAL_SQL':'off','AGERE_BOTTLED':'off',
                'AGERE_NOTE_NER':'on' if getattr(a,'bulk_note_ner',False) else 'off','AGERE_RECURSIVE_IMPROVEMENT':'on' if a.rsi else 'off',
                'AGERE_COMPONENT_LEARNING':'on' if getattr(a,'component_learning',False) else 'off',
                'AGERE_LEDGER_PATH':str(a.ledger.resolve()),'AGERE_EXECUTION_TRACE':str(a.output.resolve()/'components.jsonl'),
                'AGERE_EXECUTION_SQL_TRACE':'on','AGERE_TRAINING_DIR':str(a.output.resolve()/'own-traffic')}.items():os.environ[k]=v
    if release_root is not None:os.environ['AGERE_COMPONENT_RELEASE_ROOT']=str(release_root.resolve())
    else:os.environ.pop('AGERE_COMPONENT_RELEASE_ROOT',None)
    if getattr(a,'learning_plan',None) is not None:os.environ['AGERE_COMPONENT_LEARNING_PLAN']=str(a.learning_plan.resolve())
    else:os.environ.pop('AGERE_COMPONENT_LEARNING_PLAN',None)
    started=time.monotonic();stack=None
    try:
        from run_integrated_stack import keys
        keys()
        from clinical_core.agere import subscription_transport
        if not subscription_transport.enabled():raise ValueError('Claude subscription transport is required')
        subscription_transport.install()
        from clinical_core.agere.clinical.stack_runtime import Stack
        from clinical_core.agere.clinical.conversation import handle_compose
        from clinical_core.agere.request_lineage import Execution
        stack=Stack(a.runtime,allow_live=True);stack.ledger_path=a.ledger.resolve()
        from clinical_core.agere.clinical.harness_improvement import owned_recovery
        with owned_recovery(recovery_actions,request['question']), Execution(stack.path,'/api/stack/compose',request) as execution:
            result=handle_compose(stack,request);execution.finish(result)
        (a.output/'result.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
        summary={**preflight,'status':'returned','seconds':time.monotonic()-started,
                 'answer_status':(result.get('answer') or {}).get('status'),
                 'workflow_statuses':[s.get('status') for s in result.get('workflow') or []]}
    except Exception as exc:
        failure={'error':str(exc),'type':type(exc).__name__,'kind':getattr(exc,'kind',None),
                 'seconds':time.monotonic()-started,'traceback':traceback.format_exc(),
                 'planner_receipt':getattr(exc,'planner_receipt',None)}
        (a.output/'failure.json').write_text(json.dumps(failure,indent=2,default=str)+'\n')
        raise
    finally:
        if stack is not None:stack.close()
        (a.output/'ledger-after.json').write_text(json.dumps(ledger_status(a.ledger),indent=2)+'\n')
    summary['ledger_after']=ledger_status(a.ledger)
    summary.pop('provider_calls')  # live counts remain in the attributed provider receipts
    (a.output/'run-receipt.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


if __name__=='__main__':
    print(json.dumps(run(parser().parse_args()),indent=2))
