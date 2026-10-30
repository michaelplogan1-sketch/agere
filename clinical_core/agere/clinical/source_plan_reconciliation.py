"""Review every saved source-pass claim across explicitly selected plan answers.

Reuses the bounded claim/excerpt gateway, exact citation parser and ledger.
Original prose and original answers remain unchanged. This cannot enlarge source
coverage, infer omitted care, or authorize a clinical/work action.
"""
from .case_synthesis import token_count_calls
from pathlib import Path
import copy
import hashlib
import json
from ...contracts import digest
from . import source_plan_answers as A, linked_claim_reconciliation as L
from . import linked_claim_gateway as G, stable_reconciliation as R
from . import case_synthesis as C, case_passes as P, synthesis_availability as V

VERSION = 'source-plan-reconciliation/1'


def identity():
    return digest([VERSION, A.identity(), L.identity(), hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def location(path, key):
    A.validate({'id': key}, ('id',))
    return Path(path)/'source-plan-reconciliations'/(key+'.json')


def assemble(result):
    snap = result['snapshot']; contexts = result['contexts']; answers = result['answers']
    row = copy.deepcopy(contexts[0]); packet = row['model_context']
    packet['sources'] = {}; packet['evidence'] = []
    stages = []; aliases = []; claims = []; all_edges = []; unresolved = []
    seen = set()
    for attachment, answer in zip(snap['groups'], answers):
        context = next(c for c in contexts if c['id'] == attachment['context_id'])
        if context['model_context']['snapshot'] != packet['snapshot']:
            raise ValueError('Source-group snapshots differ')
        if set(packet['sources']) & set(context['model_context']['sources']):
            raise ValueError('Source groups overlap')
        packet['sources'].update(copy.deepcopy(context['model_context']['sources']))
        packet['evidence'].extend(copy.deepcopy(context['model_context']['evidence']))
        group = attachment['group_number']
        for original in answer['passes']:
            stage = copy.deepcopy(original); number = len(stages)+1
            aliases.append({'group': group, 'answer_id': answer['id'],
                'original_pass': original['pass_number'], 'global_pass': number})
            stage['pass_number'] = number
            for f in stage['result']['findings']:
                old = f['id']; f['id'] = 'g'+str(group)+'_'+old
                if f['id'] in seen: raise ValueError('Repeated original finding identity')
                seen.add(f['id']); claims.append({'input_id':f['id'], 'answer_id':answer['id'], 'group':group, 'original_id':old})
            stages.append(stage)
        all_edges.extend(answer['dependencies']['edges'])
        unresolved.extend(answer['dependencies']['unresolved_references'])
    ids = [u['id'] for u in packet['evidence']]
    if len(ids) != len(set(ids)): raise ValueError('Source unit IDs overlap between groups')
    if len(ids) != snap['source_units'] or len(packet['sources']) != snap['selected_records']:
        raise ValueError('Combined plan coverage differs')
    row['patient_id'] = snap['patient_id']; row['parameters']['question'] = snap['original_question']
    # These are saved documentary links, not clinical relationships. Previously
    # unresolved references stay unresolved; merging groups is not adjudication.
    edges = list({digest(e):e for e in all_edges}.values())
    pending = list({digest(e):e for e in unresolved}.values())
    parent = {'passes': stages, 'coverage': {'received_units':len(ids), 'submitted_units':len(ids),
        'model_accounted_units':len(ids), 'system_unassessed_units':0},
        'dependencies':{'edges':edges,'unresolved_references':pending}}
    return row, parent, aliases, claims


def prepare(retriever, body):
    A.validate(body, ('snapshot_id',)); result = A.inspect(retriever, {'id':body['snapshot_id']})
    snap = result['snapshot']
    if len(snap['groups']) < 2: raise ValueError('Cross-group reconciliation needs at least two groups')
    row, parent, aliases, claims = assemble(result)
    parent['dependencies'] = P.dependencies(retriever.path, row)
    refs = R.manifest(parent,row)
    received = V.snapshot(retriever.path,result['contexts'][0])
    job = L.request(parent,row,refs,received)
    # Only cited units are actually supplied in this excerpt request. Reject
    # even syntactically valid global aliases for uncited/unsent source units.
    supplied = {v['unit'] for v in job['excerpt_pool'].values()}
    job['units'] = {k:v for k,v in job['units'].items() if k in supplied}
    job['excerpts'] = {k:v for k,v in job['excerpts'].items() if k in supplied}
    wire = json.loads(job['body']['messages'][0]['content'])
    wire['source_plan'] = {'snapshot_id':snap['id'],'plan_id':snap['plan_id'],
        'analysis_window':snap['analysis_window'],'review_groups':len(snap['groups']),
        'original_prose_reference_scopes':aliases,
        'note':'Input claim prose is unchanged. Its original pN:uM or bare uM references belong to its original group and pass. Use this map to interpret them; unresolved or unknown references stay unresolved. Output references must use the supplied global pN:uM names.',
        'included':'Every original source-pass claim and its qualifications and exact cited excerpts from all selected group answers.',
        'excluded':'Uncited source text, group reconciliation conclusions, prior review questions and all records outside the declared plan. Original group answers remain available unchanged.'}
    job['body']['system'] += ' The input contains multiple source-plan review groups. Interpret original claim prose references only in their original group/pass scope using source_plan.original_prose_reference_scopes. Never conflate equal local aliases in different groups. All output unit references use global supplied names. Review cross-group relationships only to the extent supported by supplied excerpts; explicitly preserve remaining unknowns.'
    job['body']['messages'][0]['content'] = P.context.encode(wire).decode()
    G.validate(job['body'])
    key = digest([VERSION,identity(),snap,job['body'],refs,claims])
    return result, refs, job, claims, key


def preview(retriever, body):
    result, refs, job, claims, key = prepare(retriever,body)
    prior=saved_for_snapshot(retriever,result['snapshot']['id'])
    return {'schema':VERSION,'id':prior['id']if prior else key,'snapshot_id':result['snapshot']['id'],
        'input_claims':len(claims),'cited_excerpts':len(job['excerpt_pool']),
        'review_groups':len(result['snapshot']['groups']),'source_units':result['snapshot']['source_units'],
        'request_bytes':P.request_bytes(job['body']),'maximum_inference_calls':1,
        'saved_answer_available':prior is not None,'saved_answer_id':prior['id']if prior else None,
        'complete_source_units_submitted':False,'clinical_validated':False,'new_paid_api_calls':0,
        'scope':json.loads(job['body']['messages'][0]['content'])['source_plan']}


def inspect(retriever, body):
    A.validate(body, ('id',)); saved=json.loads(location(retriever.path,body['id']).read_text())
    if (saved.get('id')!=body['id'] or saved.get('status')!='complete'
            or saved.get('result_digest')!=digest({k:v for k,v in saved.items() if k!='result_digest'})):
        raise ValueError('Source-plan reconciliation integrity changed')
    result,refs,job,claims,key=prepare(retriever,{'snapshot_id':saved['snapshot_id']})
    historical=digest([VERSION,saved.get('code_identity'),result['snapshot'],job['body'],refs,claims])
    if historical!=body['id']: raise ValueError('Source-plan reconciliation dependencies changed')
    if saved['code_identity']!=identity():
        usage=saved['usage'];request=G.request_key(job['body'])
        if usage.get('request_hash')!=request:raise ValueError('Historical source-plan wire request changed')
        raw=json.loads((location(retriever.path,body['id']).parent/'provider'/(request+'.json')).read_text())['response']
        output=L.parse(raw,job,refs)
        rebuilt=receipt(result,refs,job,claims,body['id'],output,usage,saved['token_check'])
        rebuilt['code_identity']=saved['code_identity']
        rebuilt['result_digest']=digest({k:v for k,v in rebuilt.items()if k!='result_digest'})
        if rebuilt!=saved:raise ValueError('Historical source-plan output differs from current native parser or composition')
        proof={'schema':'source-plan-current-revalidation/1','status':'passed','reconciliation_id':body['id'],
            'historical_code_identity':saved['code_identity'],'current_code_identity':identity(),
            'raw_response_digest':digest(raw),'result_digest':saved['result_digest'],'provider_calls':0}
        target=location(retriever.path,body['id']).with_name(body['id']+'-current-revalidation.json')
        if not target.exists()or json.loads(target.read_text())!=proof:P.atomic_write(target,proof)
    return saved


def saved_for_snapshot(retriever,snapshot_id):
    for p in sorted(location(retriever.path,'0'*64).parent.glob('*.json'),key=lambda p:(p.stat().st_mtime_ns,p.name)):
        if len(p.stem)!=64 or any(c not in '0123456789abcdef'for c in p.stem):continue
        value=json.loads(p.read_text())
        if value.get('schema')==VERSION and value.get('status')=='complete'and value.get('snapshot_id')==snapshot_id:
            return inspect(retriever,{'id':p.stem})
    return None


def receipt(result,refs,job,claims,key,output,usage,tokens):
    saved={'schema':VERSION,'id':key,'snapshot_id':result['snapshot']['id'],'plan_id':result['plan']['id'],
        'code_identity':identity(),'status':'complete','patient_id':result['snapshot']['patient_id'],
        'original_question':result['snapshot']['original_question'],'result':output,
        'input_claim_manifest':claims,'reference_manifest':refs,
        'scope':json.loads(job['body']['messages'][0]['content'])['source_plan'],
        'coverage':{'group_answers':len(result['answers']),'input_claims':len(claims),'accounted_claims':len(output['claim_reviews']),
            'cited_excerpts':len(job['excerpt_pool']),'source_units':result['snapshot']['source_units'],
            'source_coverage_upgraded':False,'complete_chart':False,'clinical_validated':False},
        'request_bytes':P.request_bytes(job['body']),'usage':usage,'token_check':tokens,
        'new_paid_api_calls':int(not usage.get('cache_hit',False)),
        'new_token_count_calls':token_count_calls(tokens),'cache_hit':False,
        'clinical_execution_authorized':False,'human_review_created':False}
    saved['result_digest']=digest(saved)
    return saved


def reconcile(retriever, body, ledger, *, allow_live=False):
    result, refs, job, claims, key = prepare(retriever,body); target=location(retriever.path,key)
    prior=saved_for_snapshot(retriever,result['snapshot']['id'])
    if prior is not None:return {**prior,'cache_hit':True,'new_paid_api_calls':0,'new_token_count_calls':0}
    if not allow_live: raise ValueError('Live source-plan reconciliation is disabled')
    prior=ledger.db.execute('SELECT status FROM calls WHERE id=?',(G.request_key(job['body']),)).fetchone()
    if prior and prior[0]!='validated': raise ValueError('Prior plan reconciliation pending/failed; no silent retry')
    out=target.parent/'provider';out.mkdir(parents=True,exist_ok=True)
    progress={'schema':VERSION,'id':key,'snapshot_id':body['snapshot_id'],'status':'pending'}
    progress_path=target.with_name(key+'-progress.json')
    def fresh():
        if A.inspect(retriever,{'id':body['snapshot_id']}) != result:
            raise ValueError('Plan answer or source changed during reconciliation')
    try:
        fresh(); tokens=C.token_check(job['body'],out,allow_live=allow_live);fresh()
        output,usage=G.send(job['body'],ledger,out,parse=lambda raw:L.parse(raw,job,refs));fresh()
        saved=receipt(result,refs,job,claims,key,output,usage,tokens);P.atomic_write(target,saved)
        progress['status']='complete';P.atomic_write(progress_path,progress);return saved
    except Exception as exc:
        progress.update(status='incomplete',error=str(exc));P.atomic_write(progress_path,progress);raise
