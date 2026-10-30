"""Dedicated bounded excerpt-only reconciliation transport.

The historical 20 KB source-pass gateways stay unchanged. This explicit route
allows at most 32 KiB for all linked saved claims, never complete chart units.
It retains the shared ledger, native token checks and failed-request protection.
"""
from pathlib import Path
import json, os, time, urllib.request, urllib.error
from ...contracts import digest
from ..gateway import PersistentGateway
from ..economics import usage_cost
from .case_synthesis import valid_token_preflight

MAX_BYTES=32768
CONTRACT='linked-claim-reconciliation/1'

def request_key(body):return digest({'contract':CONTRACT,'provider':'claude','body':body})

def validate(body):
    wire=json.loads(body['messages'][0]['content'])
    if wire.get('schema')!=CONTRACT or wire.get('stage')!='saved_claims_and_exact_excerpts' or wire.get('synthetic') is not True:
        raise ValueError('Exact synthetic claim-reconciliation contract required')
    if len(json.dumps(body,allow_nan=False).encode())>MAX_BYTES:raise ValueError('Linked claim request exceeds 32768 bytes')
    if body['model']!=PersistentGateway.MODELS['claude'] or body['max_tokens']!=8192:raise ValueError('Unexpected model or output bound')

def send(body,ledger,out,*,parse):
    validate(body);key=request_key(body)
    old=ledger.db.execute('SELECT status,response FROM calls WHERE id=?',(key,)).fetchone()
    if old:
        if old[0]!='validated':raise ValueError('Prior claim review pending/failed; no silent retry')
        raw=json.loads(old[1]);return parse(raw),{'cache_hit':True,'request_hash':key,'usage':raw.get('usage'),'cost_estimate':usage_cost(raw['model'],raw.get('usage'))}
    # The exact complete request must already have a successful native token check.
    count_key=digest({k:body[k] for k in ('model','messages','system','output_config')})
    tokens=json.loads((out/(count_key+'-tokens.json')).read_text())
    if not valid_token_preflight(body,tokens):
        raise ValueError('Native token or subscription preflight receipt required')
    endpoint,var=PersistentGateway.ENDPOINTS['claude']
    if not os.environ.get(var):raise ValueError('Missing '+var)
    raced=ledger.reserve(key,'claude',body['model'])
    if raced is not None:
        return parse(raced),{'cache_hit':True,'request_hash':key,'usage':raced.get('usage'),'cost_estimate':usage_cost(raced['model'],raced.get('usage'))}
    raw=None;start=time.perf_counter()
    try:
        request=urllib.request.Request(endpoint,data=json.dumps(body,allow_nan=False).encode(),headers={'Content-Type':'application/json','x-api-key':os.environ[var],'anthropic-version':'2023-06-01'})
        with urllib.request.urlopen(request,timeout=150) as response:raw=json.load(response)
        (out/(key+'.json')).write_text(json.dumps({'response':raw,'status':'received_pending_validation'},indent=2))
        result=parse(raw);seconds=time.perf_counter()-start;ledger.finish(key,response=raw,seconds=seconds)
        receipt={'request_hash':key,'model':raw['model'],'seconds':seconds,'usage':raw.get('usage'),'cost_estimate':usage_cost(raw['model'],raw.get('usage'))}
        (out/(key+'.json')).write_text(json.dumps({'response':raw,'receipt':receipt},indent=2));return result,receipt
    except Exception as exc:
        # Do not retain potentially sensitive transport exception text or headers.
        error='HTTP '+str(exc.code) if isinstance(exc,urllib.error.HTTPError) else (str(exc)[:240] if isinstance(exc,ValueError) else type(exc).__name__)
        (out/(key+'-failure.json')).write_text(json.dumps({'request_hash':key,'error':error,'returned_usage':raw.get('usage') if isinstance(raw,dict) else None},indent=2))
        ledger.finish(key,error=error,seconds=time.perf_counter()-start);raise RuntimeError(error) from None
