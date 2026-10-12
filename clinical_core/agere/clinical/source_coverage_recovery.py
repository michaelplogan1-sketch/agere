"""Retain valid source findings while marking omitted model coverage unassessed.

Omitted dispositions never become model judgments or assessed source coverage.
Original returned responses and failed ledger entries remain unchanged.
"""
from collections import Counter
from pathlib import Path
import copy,hashlib,json
from ...contracts import digest
from . import availability_source_stage as legacy,case_passes as passes,case_synthesis as single
from . import synthesis_availability as availability,record_context,record_synthesis

VERSION='source-coverage-recovery/1'
ERROR='ValueError: Every source unit must have a coverage disposition'
CONFLICT_ERROR='ValueError: A conflict needs two distinct evidence units'
RECOVERABLE_ERRORS={ERROR,CONFLICT_ERROR}
def identity():return digest([VERSION,legacy.identity(),hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
def location(path,key):return single.receipt_path(path,key).parent.parent/'source-coverage-recovery'/(key+'.json')
def exists(path,key):return location(path,key).exists()
def request_key(body):return digest({'contract':'context-student/1','provider':'claude','body':body})
def failed(ledger,body):
    old=ledger.db.execute('SELECT status FROM calls WHERE id=?',(request_key(body),)).fetchone()
    return bool(old and old[0]=='failed')
def parse(raw,body,units,row):
    value=json.loads(''.join(v['text'] for v in raw.get('content',[]) if v.get('type')=='text'))
    reviews=value.get('unit_reviews');missing=[]
    if isinstance(reviews,list) and len(reviews)<len(units):
        names=[r.get('unit') for r in reviews if isinstance(r,dict)]
        if len(names)==len(reviews) and all(isinstance(n,str) and n in units for n in names) and len(names)==len(set(names)):
            missing=sorted(set(units)-set(names))
    revised=copy.deepcopy(value)
    for name in missing:revised['unit_reviews'].append({'unit':name,'status':'unresolved','reason':'System accounting: provider omitted this unit disposition. Meaning and relevance remain unassessed.'})
    cooked={**raw,'content':[{'type':'text','text':json.dumps(revised)}]}
    quarantine=None
    try:result=passes.parse_pass(cooked,body,units,row)
    except ValueError as exc:
        # Do not reinterpret a rejected clinical claim. Quarantine the entire
        # pass output; its sources stay explicitly unassessed and raw response
        # remains retained. No finding or proposed review from it is accepted.
        if str(exc)!='A conflict needs two distinct evidence units':raise
        quarantine=str(exc);missing=sorted(units)
        rejected={'findings':[],'review_steps':[],
            'unanswered':['This source pass output was rejected for an invalid conflict citation. Its source meaning remains unassessed; review the retained raw response and original sources.'],
            'unit_reviews':[{'unit':name,'status':'unresolved','reason':'System quarantine: rejected source-pass output; no findings accepted. Meaning and relevance remain unassessed.'} for name in missing]}
        result=passes.parse_pass({**raw,'content':[{'type':'text','text':json.dumps(rejected)}]},body,units,row)
    for review in result['unit_reviews']:
        review['disposition_authority']='system_missing_model_disposition' if review['unit'] in missing else 'provider_returned_disposition'
    result['coverage_recovery']={'omitted_unit_names':missing,'raw_response_digest':digest(raw),'model_reported_dispositions':len(units)-len(missing),
        'system_unassessed_dispositions':len(missing),'quarantined_output_reason':quarantine,'clinical_meaning_validated':False}
    return result

def prepare(path,context_id):
    row,bundle,links,packets,_=legacy.prepare(path,context_id)
    return row,bundle,links,packets,digest([VERSION,identity(),digest(row),digest(links)])
def recoverable(path,context_id,body,packets,row):
    *_,oldkey=legacy.prepare(path,context_id);out=legacy.location(path,oldkey).parent/'provider';key=request_key(body)
    failure=out/(key+'-failure.json');saved=out/(key+'.json')
    if not failure.exists() or not saved.exists() or json.loads(failure.read_text()).get('error') not in RECOVERABLE_ERRORS:return False
    units=next((units for candidate,units in packets if candidate==body),None)
    if units is None:return False
    try:result=parse(json.loads(saved.read_text())['response'],body,units,row)
    except (ValueError,KeyError,TypeError):return False
    return bool(result['coverage_recovery']['omitted_unit_names'])

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
        from . import availability_reconciliation as stable_reconciliation
        reconciliation=stable_reconciliation.preview(path,key);count=reconciliation['reconciliation_groups']
    return {'schema':VERSION,'context_id':context_id,'patient_id':row['patient_id'],'source_units':bundle['counts']['source_units'],
        'source_passes':n,'maximum_inference_calls':n+count,'reservation_ceiling_usd':.5*(n+count),
        'request_bytes':[passes.request_bytes(body) for body,_ in packets],
        'source_availability':[json.loads(body['messages'][0]['content'])['received_source_availability'] for body,_ in packets],'dependencies':links,
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
        if old and old[0]!='validated':
            if old[0]!='failed' or not recoverable(path,context_id,body,packets,row):raise ValueError('Prior source pass pending/failed; no silent paid retry')
        return bool(old)
    count=0 if len(packets)==1 else min(8,len(passes.source_components(row,links)))
    budget=ledger.receipt()['remaining_reservation_usd']
    required=.5*(sum(not cached(body) for body,_ in packets)+count)
    if budget is not None and budget<required:raise ValueError('Authorized ledger cannot cover declared source scope')
    out=legacy.location(path,legacy.prepare(path,context_id)[-1]).parent/'provider';out.mkdir(parents=True,exist_ok=True)
    state={'schema':VERSION,'id':key,'context_id':context_id,'status':'pending','passes':[],'new_paid_api_calls':0,'new_token_count_calls':0}
    progress_path=target.with_name(key+'-progress.json')
    def fresh():
        *_,current=prepare(path,context_id)
        if current!=key:raise ValueError('Source-stage input changed during interpretation')
    try:
        for packet,(body,units) in zip(bundle['passes'],packets):
            if passes.request_bytes(body)>20000:raise ValueError('Source pass exceeds gateway bound')
            cached(body);fresh();is_failed=failed(ledger,body);tokens=single.token_check(body,out,allow_live=False if is_failed else allow_live)
            state['new_token_count_calls']+=int(not tokens['provider_count_cached']);fresh()
            result,usage=single.send(body,'claude',ledger,out,parse=lambda raw:parse(raw,body,units,row),allow_failed_response_revalidation=is_failed,revalidation_reason=VERSION)
            state['new_paid_api_calls']+=int(not usage.get('cache_hit',False))
            if usage.get('cache_hit') and not usage.get('offline_revalidation'):
                from ..economics import usage_cost
                raw=json.loads(ledger.db.execute('SELECT response FROM calls WHERE id=?',(request_key(body),)).fetchone()[0])
                usage={**usage,'usage':raw.get('usage'),'cost_estimate':usage_cost(raw['model'],raw.get('usage'))}
            fresh();state['passes'].append({'pass_number':packet['pass_number'],'source_units':[u['id'] for u in packet['evidence']],
                'result':passes._rename(result,'p'+str(packet['pass_number'])+'_'),
                'source_availability':json.loads(body['messages'][0]['content'])['received_source_availability'],'usage':usage,'token_check':tokens,'request_bytes':passes.request_bytes(body)})
            passes.atomic_write(progress_path,state)
        stages=state['passes'];reviews=[r for p in stages for r in p['result']['unit_reviews']]
        if sorted(r['source_unit_id'] for r in reviews)!=sorted(bundle['unit_manifest']):raise ValueError('Source-stage coverage mismatch')
        unanswered=list(dict.fromkeys(u for p in stages for u in p['result']['unanswered']))
        if len(stages)>1:unanswered.append('Cross-pass relationships have not yet been reconciled in this source-stage receipt.')
        saved={'schema':VERSION,'id':key,'status':'complete','synthetic':True,'patient_id':row['patient_id'],
            'question':row['parameters']['question'],'context_id':context_id,'context_digest':digest(row),'code_identity':identity(),
            'source_availability':{'schema':availability.VERSION,'snapshot_digest':links['availability_dependency'],
                'received_records':stages[0]['source_availability']['received_records'],
                'selected_records':len(row['model_context']['sources']),
                'received_records_outside_selection':stages[0]['source_availability']['received_records_outside_selection'],
                'clinical_meaning_validated':False},
            'source_scope':row['parameters']['streams'],'record_scope':row.get('record_scope'),'dependencies':links,'passes':stages,
            'findings':[f for p in stages for f in p['result']['findings']],'unanswered':unanswered,
            'review_steps':[{**s,'id':digest([key,i,s]),'status':'proposed','execution_authorized':False}
                for i,s in enumerate(s for p in stages for s in p['result']['review_steps'])],
            'reconciliation':{'result':{'findings':[],'unanswered':[],'review_steps':[]},'groups':[]},
            'coverage':{'received_units':len(reviews),'submitted_units':len(reviews),'model_accounted_units':sum(r.get('disposition_authority')!='system_missing_model_disposition' for r in reviews),'system_unassessed_units':sum(r.get('disposition_authority')=='system_missing_model_disposition' for r in reviews),
                'unit_dispositions':dict(Counter(r['status'] for r in reviews)),'unassessed_units':[r for r in reviews if r['status']=='unresolved'],
                'complete_declared_scope':True,'complete_categories':'record_scope' not in row,'complete_chart':False,
                'reconciliation_groups':0,'cross_group_relations_assessed':len(stages)==1,
                'interpretation_accuracy_assessed':False,'retrieval_recall_assessed':False},
            'quarantined_source_passes':[{'pass_number':p['pass_number'],**p['result']['coverage_recovery']} for p in stages if p['result'].get('coverage_recovery',{}).get('quarantined_output_reason')],
            'interpretation_status':'partial_unassessed_source_units' if any(p['result'].get('coverage_recovery',{}).get('system_unassessed_dispositions') for p in stages) else 'model_accounted_source_units',
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


def install():
    from . import source_synthesis as S
    if getattr(S.inspect,'source_coverage_recovery',False):return
    original=S.inspect
    def inspect_any(path,key):return inspect(path,key) if exists(path,key) else original(path,key)
    inspect_any.__dict__.update(getattr(original,'__dict__',{}))
    inspect_any.source_coverage_recovery=True;S.inspect=inspect_any
