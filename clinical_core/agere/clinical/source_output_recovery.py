"""Explicit, bounded recovery of diagnosed source-pass output truncation.

The original request, failed response and source engine remain immutable. Only
max_tokens changes; every returned unit and citation is checked by the original
meaning contract. This is not an automatic retry or a new interpretation default.
"""
from collections import Counter
from pathlib import Path
import copy
import hashlib
import json
from ...contracts import digest
from . import meaning_source_stage as M, source_meaning as meaning
from . import case_passes as passes, case_synthesis as single, synthesis_availability as availability

VERSION = 'source-output-recovery/1'
OUTPUT_TOKENS = 8192


def identity():
    return digest([VERSION, M.identity(), hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def location(path, key):
    return single.receipt_path(path, key).parent.parent/'source-output-recovery'/(key+'.json')


def exists(path, key):
    return location(path, key).exists()


def request_key(body):
    return digest({'contract':'context-student/1', 'provider':'claude', 'body':body})


def diagnose(body, failure, raw):
    if (failure.get('status') != 'failed' or failure.get('request_hash') != request_key(body)
            or raw.get('model') != body['model'] or raw.get('stop_reason') != 'max_tokens'
            or type(raw.get('usage', {}).get('output_tokens')) is not int
            or raw['usage']['output_tokens'] < body['max_tokens']
            or body['max_tokens'] >= OUTPUT_TOKENS
            or not failure.get('error', '').startswith('JSONDecodeError:')):
        raise ValueError('No diagnosed JSON output-token truncation eligible for this bounded correction')
    corrected = copy.deepcopy(body)
    corrected['max_tokens'] = OUTPUT_TOKENS
    if passes.request_bytes(corrected) > 20000:
        raise ValueError('Corrected source request exceeds existing input bound')
    return corrected


def prepare(path, context_id):
    row, bundle, links, packets, original_id = M.prepare(path, context_id)
    source_out = M.location(path, original_id).parent/'provider'
    failures = []
    corrected = []
    for number, (body, units) in enumerate(packets, 1):
        key = request_key(body)
        failure_path, raw_path = source_out/(key+'-failure.json'), source_out/(key+'.json')
        if failure_path.exists():
            failure = json.loads(failure_path.read_text())
            raw = json.loads(raw_path.read_text())['response']
            revised = diagnose(body, failure, raw)
            failures.append({'pass_number':number, 'failed_request_hash':key,
                'corrected_request_hash':request_key(revised), 'failure_digest':digest(failure),
                'raw_response_digest':digest(raw), 'original_max_tokens':body['max_tokens'],
                'corrected_max_tokens':OUTPUT_TOKENS})
            corrected.append((revised, units))
        else:
            corrected.append((body, units))
    if not failures:
        raise ValueError('No saved source output-token failure to recover')
    binding = {'original_stage_id':original_id, 'context_id':context_id,
        'context_digest':digest(row), 'dependencies':links, 'failures':failures}
    key = digest([VERSION, identity(), binding])
    return row, bundle, links, corrected, key, binding, source_out


def preview(path, request):
    if not isinstance(request, dict) or set(request) != {'context_id'}:
        raise ValueError('Exact source context required')
    from . import source_synthesis as S
    if S.stages(path, request['context_id'])[0] is not M:
        raise ValueError('Historical source engines retain their original recovery contracts')
    row, bundle, _, packets, key, binding, _ = prepare(path, request['context_id'])
    return {'schema':VERSION, 'id':key, **binding, 'patient_id':row['patient_id'],
        'source_units':len(bundle['unit_manifest']), 'source_passes':len(packets),
        'request_bytes':[passes.request_bytes(body) for body, _ in packets],
        'saved_answer_available':exists(path,key), 'new_paid_api_calls':0,
        'failed_requests_resent':0, 'clinical_validated':False,
        'scope':'Correct only diagnosed truncated passes at 8192 output tokens; retain identical evidence, model and parser. Reuse validated passes. Cross-pass reconciliation remains pending.'}


def parsed(raw, body, units, row):
    if raw.get('model') != body['model'] or raw.get('stop_reason') == 'max_tokens':
        raise ValueError('Recovery output is incomplete or from another model')
    return meaning.parse(raw, body, units, row)


def assemble(row, bundle, links, packets, key, binding, stages, counts):
    reviews = [r for p in stages for r in p['result']['unit_reviews']]
    if sorted(r['source_unit_id'] for r in reviews) != sorted(bundle['unit_manifest']):
        raise ValueError('Recovered source-stage coverage mismatch')
    unanswered = list(dict.fromkeys(u for p in stages for u in p['result']['unanswered']))
    if len(stages)>1:
        unanswered.append('Cross-pass relationships have not yet been reconciled in this source-stage receipt.')
    saved = {'schema':VERSION, 'id':key, 'status':'complete', 'synthetic':True,
        'patient_id':row['patient_id'], 'question':row['parameters']['question'],
        'context_id':binding['context_id'], 'context_digest':digest(row), 'code_identity':identity(),
        'recovery':binding, 'source_scope':row['parameters']['streams'], 'dependencies':links,
        'source_availability':{'schema':availability.VERSION, 'snapshot_digest':links['availability_dependency'],
            'received_records':stages[0]['source_availability']['received_records'],
            'selected_records':len(row['model_context']['sources']),
            'received_records_outside_selection':stages[0]['source_availability']['received_records_outside_selection'],
            'clinical_meaning_validated':False},
        'passes':stages, 'findings':[f for p in stages for f in p['result']['findings']],
        'unanswered':unanswered, 'review_steps':[{**s,'id':digest([key,i,s]),'status':'proposed','execution_authorized':False}
            for i,s in enumerate(s for p in stages for s in p['result']['review_steps'])],
        'reconciliation':{'result':{'findings':[],'unanswered':[],'review_steps':[]},'groups':[]},
        'coverage':{'received_units':len(reviews),'submitted_units':len(reviews),'model_accounted_units':len(reviews),
            'unit_dispositions':dict(Counter(r['status'] for r in reviews)),
            'unassessed_units':[r for r in reviews if r['status']=='unresolved'],
            'complete_declared_scope':True,'complete_categories':'record_scope' not in row,'complete_chart':False,
            'reconciliation_groups':0,'cross_group_relations_assessed':len(stages)==1,
            'interpretation_accuracy_assessed':False,'retrieval_recall_assessed':False},
        'provider':'claude','model':packets[0][0]['model'],'usage':[p['usage'] for p in stages],
        'token_check':{'input_tokens':sum(p['token_check']['input_tokens'] for p in stages)},
        **counts, 'cache_hit':False, 'failed_requests_resent':0,
        'source_meaning_contract':meaning.VERSION,'meaning_judgments_validated':False,
        'clinical_state_established':False,'clinical_execution_authorized':False,
        'limits':single.LIMITS+['Explicit output-limit recovery; original failed requests preserved. Cross-pass reconciliation remains separate.']}
    if 'record_scope' in row:saved['record_scope']=row['record_scope']
    saved['source_stage_digest']=digest(saved)
    return saved


def inspect(path,key):
    saved=json.loads(location(path,key).read_text())
    if (saved.get('id')!=key or saved.get('code_identity')!=identity() or saved.get('status')!='complete'
            or saved.get('source_stage_digest')!=digest({k:v for k,v in saved.items() if k!='source_stage_digest'})):
        raise ValueError('Output recovery receipt integrity changed')
    row,bundle,links,packets,expected,binding,source_out=prepare(path,saved['context_id'])
    if expected!=key:raise ValueError('Output recovery input or failure dependencies changed')
    if len(saved['passes'])!=len(packets):raise ValueError('Incomplete recovered source passes')
    for packet,stage,(body,units) in zip(bundle['passes'],saved['passes'],packets):
        if stage['pass_number']!=packet['pass_number'] or stage['source_units']!=[u['id'] for u in packet['evidence']]:
            raise ValueError('Recovered source pass order or units changed')
        request=request_key(body)
        origin=source_out if stage['reused_original_request'] else location(path,key).parent/'provider'
        raw=json.loads((origin/(request+'.json')).read_text())['response']
        expected_result=passes._rename(parsed(raw,body,units,row),'p'+str(stage['pass_number'])+'_')
        if stage['usage']['request_hash']!=request or stage['result']!=expected_result:
            raise ValueError('Recovery source result differs from validated provider output')
    counts={k:saved[k] for k in ('new_paid_api_calls','new_token_count_calls','reused_source_passes')}
    if assemble(row,bundle,links,packets,key,binding,saved['passes'],counts)!=saved:
        raise ValueError('Recovered source answer no longer matches its exact passes')
    return saved


def saved_for_context(path,context_id):
    # Read-only discovery; unsupported failures remain on the original path.
    try:*_,key,binding,source_out=prepare(path,context_id)
    except (ValueError,FileNotFoundError):return None
    return inspect(path,key) if exists(path,key) else None


def run(path,request,ledger,*,allow_live=False):
    if not isinstance(request,dict) or set(request)!={'context_id','recovery_id'}:
        raise ValueError('Exact context and inspected recovery identity required')
    plan=preview(path,{'context_id':request['context_id']})
    if request['recovery_id']!=plan['id']:raise ValueError('Recovery plan changed; inspect exact current failure')
    row,bundle,links,packets,key,binding,source_out=prepare(path,request['context_id'])
    if exists(path,key):return {**inspect(path,key),'cache_hit':True,'new_paid_api_calls':0,'new_token_count_calls':0}
    if not allow_live:raise ValueError('Live output recovery disabled')
    originals=M.prepare(path,request['context_id'])[3]
    for failed in binding['failures']:
        old=ledger.db.execute('SELECT status FROM calls WHERE id=?',(failed['failed_request_hash'],)).fetchone()
        if not old or old[0]!='failed':raise ValueError('Original request must remain failed in the shared ledger')
    for body,_ in packets:
        old=ledger.db.execute('SELECT status FROM calls WHERE id=?',(request_key(body),)).fetchone()
        if old and old[0]!='validated':raise ValueError('Corrected request pending/failed; no silent retry')
    out=location(path,key).parent/'provider';out.mkdir(parents=True,exist_ok=True)
    state={'id':key,'context_id':request['context_id'],'status':'pending','passes':[]}
    counts={'new_paid_api_calls':0,'new_token_count_calls':0,'reused_source_passes':0}
    progress=location(path,key).with_name(key+'-progress.json')
    try:
        for packet,(body,units),(original,_) in zip(bundle['passes'],packets,originals):
            if prepare(path,request['context_id'])[4]!=key:raise ValueError('Recovery inputs changed during execution')
            old=ledger.db.execute('SELECT status,response FROM calls WHERE id=?',(request_key(body),)).fetchone()
            if old and old[0]!='validated':raise ValueError('Corrected request pending/failed; no silent retry')
            reused=bool(old and body==original and (source_out/(request_key(body)+'.json')).exists())
            origin=source_out if reused else out
            # Preserve original token receipts too; count identity excludes the
            # output allowance, while this receipt records the corrected limit.
            count_key=digest({k:body[k] for k in ('model','messages','system','output_config')})
            token_target=out/(count_key+'-tokens.json')
            original_token=source_out/(count_key+'-tokens.json')
            if not token_target.exists() and original_token.exists():
                token_target.write_bytes(original_token.read_bytes())
            tokens=single.token_check(body,out,allow_live=False if reused else allow_live)
            counts['new_token_count_calls']+=int(not tokens['provider_count_cached'])
            result,usage=single.send(body,'claude',ledger,origin,parse=lambda raw:parsed(raw,body,units,row))
            counts['new_paid_api_calls']+=int(not usage.get('cache_hit',False))
            counts['reused_source_passes']+=int(reused)
            if usage.get('cache_hit'):
                from ..economics import usage_cost
                raw=json.loads(old[1]);usage={**usage,'usage':raw.get('usage'),'cost_estimate':usage_cost(raw['model'],raw.get('usage'))}
            wire=json.loads(body['messages'][0]['content'])
            state['passes'].append({'pass_number':packet['pass_number'],'source_units':[u['id'] for u in packet['evidence']],
                'result':passes._rename(result,'p'+str(packet['pass_number'])+'_'),
                'canonical_identity':wire['canonical_identity'],'source_availability':wire['received_source_availability'],
                'usage':usage,'token_check':tokens,'request_bytes':passes.request_bytes(body),'reused_original_request':reused})
            passes.atomic_write(progress,{**state,**counts})
        saved=assemble(row,bundle,links,packets,key,binding,state['passes'],counts)
        if prepare(path,request['context_id'])[4]!=key:raise ValueError('Recovery inputs changed before save')
        passes.atomic_write(location(path,key),saved)
        state['status']='complete';passes.atomic_write(progress,{**state,**counts})
        return inspect(path,key)
    except Exception as exc:
        passes.atomic_write(progress,{**state,**counts,'status':'incomplete','error':str(exc)})
        raise
