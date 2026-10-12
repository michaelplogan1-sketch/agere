"""Recover ambiguous citations using unique already-verified supplied excerpts.

No fuzzy matching, new evidence, semantic rewriting or silent provider retry.
The original quote, expanded context and returned provider response remain auditable.
"""
from pathlib import Path
import copy, hashlib, json, sqlite3
from ...contracts import digest
from . import availability_reconciliation as legacy, case_passes as passes, case_synthesis as single
from . import stable_reconciliation as stable

VERSION='citation-context-recovery/1'
ERROR='ValueError: Citation is absent or ambiguous in its exact source unit'

def identity():return digest([VERSION,legacy.identity(),hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
def location(path,key):return single.receipt_path(path,key).parent.parent/'citation-context-recovery'/(key+'.json')
def exists(path,key):return location(path,key).exists()
def request_key(body):return digest({'contract':'context-student/1','provider':'claude','body':body})
def failed(ledger,body):
    old=ledger.db.execute('SELECT status FROM calls WHERE id=?',(request_key(body),)).fetchone()
    return bool(old and old[0]=='failed')

def parse(raw,job,refs):
    value=json.loads(''.join(v['text'] for v in raw.get('content',[]) if v.get('type')=='text'))
    expanded=copy.deepcopy(value);changes=[]
    for finding in expanded.get('findings',[]):
        for pos,citation in enumerate(finding.get('citations',[])):
            if not isinstance(citation,dict) or set(citation)!={'unit','quote'}:continue
            name=citation['unit'];quote=citation['quote']
            if name not in job['units'] or not isinstance(quote,str) or not quote:continue
            text=job['units'][name]['text']
            if text.count(quote)<=1:continue
            candidates=list(dict.fromkeys(q for q in job['excerpts'].get(name,{}).get('excerpts',[]) if quote in q))
            if len(candidates)!=1:continue
            full=candidates[0]
            if len(full)>1800 or text.count(full)!=1:continue
            citation['quote']=full
            changes.append({'finding_id':finding['id'],'citation_index':pos,'unit':name,
                'original_model_quote':quote,'expanded_source_quote':full,'basis':'unique_previously_verified_supplied_excerpt',
                'original_quote_occurrences':text.count(quote),'expanded_quote_occurrences':1,'raw_response_digest':digest(raw)})
    cooked={**raw,'content':[{'type':'text','text':json.dumps(expanded)}]}
    result=stable.parse(cooked,job,refs) # Every ordinary structure, identity, excerpt and exact-span check still applies.
    for change in changes:
        finding=next(f for f in result['findings'] if f['id']==change['finding_id'])
        finding['citations'][change['citation_index']]['citation_context_expansion']=change
    result['citation_context_expansions']=changes
    return result

def prepare(path,answer_id):
    parent,row,refs,jobs,_=legacy.prepare(path,answer_id)
    return parent,row,refs,jobs,digest([VERSION,identity(),parent['id'],digest(parent),digest(row),refs])

def recoverable(path,answer_id,body,jobs):
    *_,oldkey=legacy.prepare(path,answer_id)
    out=legacy.location(path,oldkey).parent/'provider';key=request_key(body)
    failure=out/(key+'-failure.json');saved=out/(key+'.json')
    if not failure.exists() or not saved.exists():return False
    if json.loads(failure.read_text()).get('error')!=ERROR:return False
    job=next((j for j in jobs if j['body']==body),None)
    if job is None:return False
    raw=json.loads(saved.read_text())['response']
    try:result=parse(raw,job,legacy.prepare(path,answer_id)[2])
    except (ValueError,KeyError,TypeError):return False
    return bool(result['citation_context_expansions'])

def preview(path,answer_id):
    parent,row,refs,jobs,key=prepare(path,answer_id)
    progress=location(path,key).with_name(key+'-progress.json')
    return {'schema':VERSION,'parent_answer_id':answer_id,'answer_id':key,'request_bytes':[passes.request_bytes(j['body']) for j in jobs],
        'reconciliation_groups':len(jobs),'reused_source_passes':len(parent['passes']),
        'recoverable_failed_groups':[i for i,j in enumerate(jobs,1) if recoverable(path,answer_id,j['body'],jobs)],
        'saved_answer_available':exists(path,key),'saved_progress':json.loads(progress.read_text()) if progress.exists() else None,
        'original_failed_calls_preserved':True,'new_paid_api_calls':0,'clinical_meaning_validated':False}

def inspect(path, key):
    saved = json.loads(location(path,key).read_text())
    if saved['id'] != key or saved['code_identity'] != identity() or saved['status'] != 'complete': raise ValueError('Recovered reconciliation identity changed')
    if saved.get('result_digest') != digest({k:v for k,v in saved.items() if k!='result_digest'}): raise ValueError('Stable reconciliation result integrity changed')
    _, row, _, _, expected = prepare(path, saved['parent_answer_id'])
    if expected != key or digest(row) != saved['context_digest']: raise ValueError('Recovered reconciliation source/parent changed')
    return saved


def synthesize(path, answer_id, ledger, *, allow_live=False):
    from . import source_synthesis
    parent, row, refs, planned, key = prepare(path, answer_id); target = location(path,key)
    if target.exists(): return {**inspect(path,key),'cache_hit':True,'new_paid_api_calls':0,'new_token_count_calls':0}
    if not allow_live: raise ValueError('Live stable reconciliation disabled; no saved result')
    def request_key(body): return digest({'contract':'context-student/1','provider':'claude','body':body})
    def cached(body):
        old = ledger.db.execute('SELECT status FROM calls WHERE id=?',(request_key(body),)).fetchone()
        if old and old[0] != 'validated':
            if old[0] != 'failed' or not recoverable(path, answer_id, body, planned):
                raise ValueError('Prior reconciliation pending/failed; no silent paid retry')
        return bool(old)
    pending = sum(not cached(j['body']) for j in planned); budget = ledger.receipt()['remaining_reservation_usd']
    if budget is not None and budget < .5*pending: raise ValueError('Authorized ledger cannot cover planned reconciliation')
    def fresh():
        if digest(source_synthesis.inspect(path,answer_id)) != digest(parent) or digest(source_synthesis.context(path,parent['context_id'])) != digest(row):
            raise ValueError('Source or saved interpretation changed during reconciliation')
    out=legacy.location(path,legacy.prepare(path,answer_id)[-1]).parent/'provider';out.mkdir(parents=True,exist_ok=True)
    progress={'schema':VERSION,'id':key,'parent_answer_id':answer_id,'status':'pending','groups':[],'new_paid_api_calls':0,'new_token_count_calls':0}
    progress_target=target.with_name(key+'-progress.json')
    try:
        for i, job in enumerate(planned,1):
            body=job['body'];fresh();cached(body);is_failed=failed(ledger,body);tokens=single.token_check(body,out,allow_live=False if is_failed else allow_live)
            progress['new_token_count_calls']+=int(not tokens['provider_count_cached']);fresh()
            result,usage=single.send(body,'claude',ledger,out,parse=lambda raw:parse(raw,job,refs),allow_failed_response_revalidation=is_failed,revalidation_reason=VERSION)
            progress['new_paid_api_calls']+=int(not usage.get('cache_hit',False))
            if usage.get('cache_hit') and not usage.get('offline_revalidation'):
                from ..economics import usage_cost
                raw=json.loads(ledger.db.execute('SELECT response FROM calls WHERE id=?',(request_key(body),)).fetchone()[0])
                usage={**usage,'usage':raw.get('usage'),'cost_estimate':usage_cost(raw['model'],raw.get('usage'))}
            fresh();progress['groups'].append({'result':passes._rename(result,'r'+str(i)+'_'),'source_records':job['source_records'],
                'input_finding_ids':job['finding_ids'],
                'source_availability':json.loads(body['messages'][0]['content'])['received_source_availability'],'usage':usage,'token_check':tokens,'request_bytes':passes.request_bytes(body)})
            passes.atomic_write(progress_target,progress)
        groups=progress['groups'];source_passes=parent['passes'];stages=source_passes+groups
        final={k:[v for g in groups for v in g['result'][k]] for k in ('findings','unanswered','review_steps')}
        if len(groups)>1:final['unanswered'].append('Relations between reconciliation groups remain unassessed; documentary grouping does not establish clinical independence.')
        saved={**parent,'schema':VERSION,'id':key,'parent_answer_id':answer_id,'parent_answer_hash':digest(parent),
            'code_identity':identity(),'context_digest':digest(row),'status':'complete','reference_manifest':refs,'citation_context_expansions':[e for g in groups for e in g['result'].get('citation_context_expansions',[])],
            'recovery_policy':'Only uniquely supplied parent excerpts may expand ambiguous model quotes; strict original validation reruns. Failed ledger rows and source passes remain unchanged.',
            'findings':[f for p in stages for f in p['result']['findings']],
            'review_steps':[{**s,'id':digest([key,i,s]),'status':'proposed','execution_authorized':False}
                            for i,s in enumerate(s for p in stages for s in p['result']['review_steps'])],
            'unanswered':list(dict.fromkeys([u for p in source_passes for u in p['result']['unanswered']]+final['unanswered'])),
            'reconciliation':{'result':final,'groups':groups},'usage':[p['usage'] for p in stages],
            'token_check':{'input_tokens':sum(p['token_check']['input_tokens'] for p in stages)},
            'coverage':{**parent['coverage'],'reconciliation_groups':len(groups),'cross_group_relations_assessed':len(groups)<=1},
            'new_paid_api_calls':progress['new_paid_api_calls'],'new_token_count_calls':progress['new_token_count_calls'],'cache_hit':False,
            'reused_source_passes':len(source_passes),'limits':parent['limits']+['Pass-qualified references verify transport identity, not semantic or clinical correctness. Previous reconciliation remains in the immutable parent answer.']}
        saved['result_digest']=digest(saved)
        fresh();passes.atomic_write(target,saved);progress['status']='complete';passes.atomic_write(progress_target,progress)
        return saved
    except Exception as exc:
        progress.update(status='incomplete',error=str(exc));passes.atomic_write(progress_target,progress);raise


def install():
    from . import source_synthesis as S
    if getattr(S.inspect,'citation_context_recovery',False):return
    original=S.inspect
    def inspect_any(path,key):return inspect(path,key) if exists(path,key) else original(path,key)
    inspect_any.__dict__.update(getattr(original,'__dict__',{}))
    inspect_any.citation_context_recovery=True;S.inspect=inspect_any
