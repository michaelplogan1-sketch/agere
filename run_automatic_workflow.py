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
    for path in sorted([*ROOT.joinpath('clinical_core').rglob('*.py'),Path(__file__)]):
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
    p.add_argument('--rsi',action='store_true',help='Opt into candidate RSI qualification; default off')
    return p


def run(a):
    request=json.loads(a.request.read_text())
    if not isinstance(request,dict) or not isinstance(request.get('question'),str) or not request['question'].strip():
        raise ValueError('Request must be an ordinary compose body with a nonempty question')
    if not (a.runtime/'semantic.sqlite3').is_file():raise ValueError('Received-source runtime semantic warehouse missing')
    fingerprint=source_fingerprint()
    if a.expect_source and a.expect_source!=fingerprint:raise ValueError('Source changed after offline verification; freeze and verify again')
    before=ledger_status(a.ledger)
    preflight={'schema':'automatic-workflow-run/1','status':'offline_preflight','source_fingerprint':fingerprint,
               'runtime':str(a.runtime.resolve()),'retained_cache_ledger':str(a.ledger.resolve()),'ledger_before':before,
               'provider_calls':0,'transport':'subscription','rsi':'candidate_on' if a.rsi else 'off',
               'reuse':'Exact validated request matches only; changed prompts/source requirements must be recomputed'}
    if not a.execute:return preflight
    if not a.expect_source:raise ValueError('--execute requires the verified --expect-source fingerprint')
    if before['calls'].get('pending'):raise ValueError('Retained ledger has pending work; inspect its owner before explicit recovery')
    a.output.mkdir(parents=True,exist_ok=False)
    (a.output/'preflight.json').write_text(json.dumps(preflight,indent=2)+'\n')
    # Set transport before key loading and sender imports. No external Claude
    # API fallback, scheduler, retry loop or automatic limit reset is installed.
    for k,v in {'AGERE_CLAUDE_TRANSPORT':'subscription','AGERE_EVALUATION':'0','AGERE_LEARNED':'off',
                'AGERE_LOCAL_TYPED':'off','AGERE_LOCAL_NORMALIZER':'off','AGERE_LOCAL_QWEN':'off','AGERE_LOCAL_MODEL':'off',
                'AGERE_LOCAL_SQL':'off','AGERE_BOTTLED':'off','AGERE_RECURSIVE_IMPROVEMENT':'on' if a.rsi else 'off',
                'AGERE_LEDGER_PATH':str(a.ledger.resolve()),'AGERE_EXECUTION_TRACE':str(a.output.resolve()/'components.jsonl'),
                'AGERE_EXECUTION_SQL_TRACE':'on','AGERE_TRAINING_DIR':str(a.output.resolve()/'own-traffic')}.items():os.environ[k]=v
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
        with Execution(stack.path,'/api/stack/compose',request) as execution:
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
