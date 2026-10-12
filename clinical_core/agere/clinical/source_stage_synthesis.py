"""Reusable source-pass stage for new contexts; reconciliation runs separately.

Uses the existing exact pass request/parse contracts, so validated cached source
calls can be reused. This stage never makes a legacy reconciliation request.
"""
from collections import Counter
from pathlib import Path
import hashlib
import json
from ...contracts import digest
from . import case_passes as passes, case_synthesis as single, record_context, record_synthesis

VERSION='source-stage-synthesis/1'


def identity():
    return digest([VERSION,passes.code_identity(),record_synthesis.code_identity(),hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def location(path,key):return single.receipt_path(path,key).parent.parent/'source-stage-synthesis'/(key+'.json')
def exists(path,key):return location(path,key).exists()


def prepare(path,context_id):
    scoped=record_context.exists(path,context_id)
    row=record_context.verified(path,context_id) if scoped else single.verified_context(path,context_id)
    links=passes.dependencies(path,row)
    bundle,links=(record_synthesis.plan(row,links) if scoped else passes.plan(row,links))
    packets=[record_synthesis.pass_request(p,links,row['missing_evidence']['record_scope']) if scoped else passes.pass_request(p,links) for p in bundle['passes']]
    key=digest([VERSION,identity(),digest(row),digest(links)])
    return row,bundle,links,packets,key


def inspect(path,key):
    saved=json.loads(location(path,key).read_text())
    if saved.get('id')!=key or saved.get('code_identity')!=identity() or saved.get('status')!='complete':raise ValueError('Source-stage identity changed')
    if saved.get('source_stage_digest')!=digest({k:v for k,v in saved.items() if k!='source_stage_digest'}):raise ValueError('Source-stage result integrity changed')
    *_,expected=prepare(path,saved['context_id'])
    if key!=expected:raise ValueError('Source-stage input dependencies changed')
    return saved


def preview(path,context_id):
    row,bundle,links,packets,key=prepare(path,context_id);n=len(packets)
    count=0 if n==1 else min(8,len(passes.source_components(row,links)))
    reconciliation=None
    if exists(path,key) and n>1:
        from . import stable_reconciliation
        reconciliation=stable_reconciliation.preview(path,key);count=reconciliation['reconciliation_groups']
    return {'schema':VERSION,'context_id':context_id,'patient_id':row['patient_id'],'source_units':bundle['counts']['source_units'],
        'source_passes':n,'maximum_inference_calls':n+count,'reservation_ceiling_usd':.5*(n+count),
        'request_bytes':[passes.request_bytes(body) for body,_ in packets],'dependencies':links,
        'record_scope':row.get('record_scope'),'new_paid_api_calls':0,'stable_reconciliation':reconciliation,
        'reference_mode':'pass_qualified_reconciliation','scope':'Whole declared source units, then bounded stable-reference reconciliation. Selection sufficiency and interpretation quality remain unassessed.'}


def progress(path,context_id):
    *_,key=prepare(path,context_id)
    return json.loads(location(path,key).with_name(key+'-progress.json').read_text())


def synthesize(path,context_id,ledger,*,allow_live=False):
    row,bundle,links,packets,key=prepare(path,context_id);target=location(path,key)
    if target.exists():return {**inspect(path,key),'cache_hit':True,'new_paid_api_calls':0,'new_token_count_calls':0}
    if not allow_live:raise ValueError('Live synthesis route disabled; no saved source-stage answer')
    def request_key(body):return digest({'contract':'context-student/1','provider':'claude','body':body})
    def cached(body):
        old=ledger.db.execute('SELECT status FROM calls WHERE id=?',(request_key(body),)).fetchone()
        if old and old[0]!='validated':raise ValueError('Prior source pass pending/failed; no silent paid retry')
        return bool(old)
    count=0 if len(packets)==1 else min(8,len(passes.source_components(row,links)))
    budget=ledger.receipt()['remaining_reservation_usd']
    required=.5*(sum(not cached(body) for body,_ in packets)+count)
    if budget is not None and budget<required:raise ValueError('Authorized ledger cannot cover declared source scope')
    out=target.parent/'provider';out.mkdir(parents=True,exist_ok=True)
    state={'schema':VERSION,'id':key,'context_id':context_id,'status':'pending','passes':[],'new_paid_api_calls':0,'new_token_count_calls':0}
    progress_path=target.with_name(key+'-progress.json')
    def fresh():
        *_,current=prepare(path,context_id)
        if current!=key:raise ValueError('Source-stage input changed during interpretation')
    try:
        for packet,(body,units) in zip(bundle['passes'],packets):
            if passes.request_bytes(body)>20000:raise ValueError('Source pass exceeds gateway bound')
            cached(body);fresh();tokens=single.token_check(body,out,allow_live=allow_live)
            state['new_token_count_calls']+=int(not tokens['provider_count_cached']);fresh()
            result,usage=single.send(body,'claude',ledger,out,parse=lambda raw:passes.parse_pass(raw,body,units,row))
            state['new_paid_api_calls']+=int(not usage.get('cache_hit',False))
            if usage.get('cache_hit'):
                from ..economics import usage_cost
                raw=json.loads(ledger.db.execute('SELECT response FROM calls WHERE id=?',(request_key(body),)).fetchone()[0])
                usage={**usage,'usage':raw.get('usage'),'cost_estimate':usage_cost(raw['model'],raw.get('usage'))}
            fresh();state['passes'].append({'pass_number':packet['pass_number'],'source_units':[u['id'] for u in packet['evidence']],
                'result':passes._rename(result,'p'+str(packet['pass_number'])+'_'),'usage':usage,'token_check':tokens,'request_bytes':passes.request_bytes(body)})
            passes.atomic_write(progress_path,state)
        stages=state['passes'];reviews=[r for p in stages for r in p['result']['unit_reviews']]
        if sorted(r['source_unit_id'] for r in reviews)!=sorted(bundle['unit_manifest']):raise ValueError('Source-stage coverage mismatch')
        unanswered=list(dict.fromkeys(u for p in stages for u in p['result']['unanswered']))
        if len(stages)>1:unanswered.append('Cross-pass relationships have not yet been reconciled in this source-stage receipt.')
        saved={'schema':VERSION,'id':key,'status':'complete','synthetic':True,'patient_id':row['patient_id'],
            'question':row['parameters']['question'],'context_id':context_id,'context_digest':digest(row),'code_identity':identity(),
            'source_scope':row['parameters']['streams'],'record_scope':row.get('record_scope'),'dependencies':links,'passes':stages,
            'findings':[f for p in stages for f in p['result']['findings']],'unanswered':unanswered,
            'review_steps':[{**s,'id':digest([key,i,s]),'status':'proposed','execution_authorized':False}
                for i,s in enumerate(s for p in stages for s in p['result']['review_steps'])],
            'reconciliation':{'result':{'findings':[],'unanswered':[],'review_steps':[]},'groups':[]},
            'coverage':{'received_units':len(reviews),'submitted_units':len(reviews),'model_accounted_units':len(reviews),
                'unit_dispositions':dict(Counter(r['status'] for r in reviews)),'unassessed_units':[r for r in reviews if r['status']=='unresolved'],
                'complete_declared_scope':True,'complete_categories':'record_scope' not in row,'complete_chart':False,
                'reconciliation_groups':0,'cross_group_relations_assessed':len(stages)==1,
                'interpretation_accuracy_assessed':False,'retrieval_recall_assessed':False},
            'provider':'claude','model':packets[0][0]['model'],'usage':[p['usage'] for p in stages],
            'token_check':{'input_tokens':sum(p['token_check']['input_tokens'] for p in stages)},
            'new_paid_api_calls':state['new_paid_api_calls'],'new_token_count_calls':state['new_token_count_calls'],'cache_hit':False,
            'clinical_state_established':False,'clinical_execution_authorized':False,
            'limits':single.LIMITS+['Source-stage completion accounts for submitted units only; multi-pass reconciliation is a separate stage.']}
        if saved['record_scope'] is None:saved.pop('record_scope')
        saved['source_stage_digest']=digest(saved);fresh();passes.atomic_write(target,saved)
        state['status']='complete';passes.atomic_write(progress_path,state);return saved
    except Exception as exc:
        state.update(status='incomplete',error=str(exc));passes.atomic_write(progress_path,state);raise
