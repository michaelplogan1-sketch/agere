"""Quarantine ambiguous review reasons in a saved failed response; never resend.

Only claim-review reasons with ambiguous/unsupplied unit aliases are recoverable.
Every finding, citation, claim identity and other original field still passes the
original strict parser. The affected reviews become system-unassessed, not fixed
clinical statements or human adjudications.
"""
from pathlib import Path
import copy
import hashlib
import json
from ...contracts import digest
from . import source_plan_reconciliation as R, source_plan_answers as A

VERSION='source-plan-review-quarantine/1'


def identity():return digest([VERSION,R.identity(),hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def location(path,key):
    A.validate({'id':key},('id',))
    return Path(path)/'source-plan-recovery'/(key+'.json')


def parsed_recovery(raw,job,refs):
    value=json.loads(''.join(c['text'] for c in raw.get('content',[]) if c.get('type')=='text'))
    if not isinstance(value,dict) or set(value)!=set(R.L.SCHEMA['properties']):raise ValueError('Unexpected saved response fields')
    reviews=value.get('claim_reviews')
    if not isinstance(reviews,list):raise ValueError('Saved claim reviews required')
    cooked=copy.deepcopy(value);quarantined=[]
    for review in cooked['claim_reviews']:
        if (not isinstance(review,dict) or set(review)!={'input_id','status','finding_ids','reason'}
                or review['input_id'] not in job['finding_ids'] or not isinstance(review['reason'],str)):
            raise ValueError('Invalid claim identity or review structure cannot be recovered')
        reason=review['reason'];bare=R.R.BARE.findall(reason)
        unknown=[n for n in R.R.QUALIFIED.findall(reason) if n not in job['units']]
        if bare or unknown:
            quarantined.append({'input_id':review['input_id'],'original_review':copy.deepcopy(review),
                'bare_references':bare,'unsupplied_references':unknown,
                'reason':'Ambiguous or unsupplied source reference in the model review reason. No referent was inferred.'})
            review.update(status='unassessed',finding_ids=[],reason='System quarantine: the original model reason contained an ambiguous or unsupplied source reference. Meaning remains unassessed.')
    if not quarantined:raise ValueError('No supported ambiguous claim-review reason to quarantine')
    response={**raw,'content':[{'type':'text','text':json.dumps(cooked)}]}
    # Reject all other failures. This also checks unique claim coverage, exact
    # citations, stop reason, model revision and every unaffected review.
    result=R.L.parse(response,job,refs)
    return result,quarantined


def prepare(retriever,body,ledger):
    snapshot,refs,job,claims,failed_id=R.prepare(retriever,body)
    request_hash=R.G.request_key(job['body'])
    row=ledger.db.execute('SELECT status,error FROM calls WHERE id=?',(request_hash,)).fetchone()
    if not row or row[0]!='failed' or row[1]!='Ambiguous claim-review unit reference':
        raise ValueError('Exact saved ambiguous-reference failure required; no new request is sent')
    file=R.location(retriever.path,failed_id).parent/'provider'/(request_hash+'.json')
    receipt=json.loads(file.read_text())
    if receipt.get('status')!='received_pending_validation':raise ValueError('Original failed provider response required')
    raw=receipt['response'];output,quarantined=parsed_recovery(raw,job,refs)
    key=digest([VERSION,identity(),snapshot['snapshot'],request_hash,digest(raw),output,quarantined])
    return snapshot,refs,job,claims,failed_id,request_hash,raw,output,quarantined,key


def preview(retriever,body,ledger):
    *_,quarantined,key=prepare(retriever,body,ledger)
    return {'schema':VERSION,'id':key,'quarantined_claim_reviews':len(quarantined),
        'snapshot_id':body['snapshot_id'],'saved_answer_available':location(retriever.path,key).exists(),
        'scope':'Validate the unchanged saved findings and unaffected claim reviews; quarantine ambiguous review reasons as system-unassessed. No guessed reference, retry or new inference.',
        'new_paid_api_calls':0,'new_token_count_calls':0,'clinical_validated':False}


def recover(retriever,body,ledger):
    snapshot,refs,job,claims,failed_id,request_hash,raw,output,quarantined,key=prepare(retriever,body,ledger)
    target=location(retriever.path,key)
    if target.exists():return {**inspect(retriever,{'id':key},ledger),'cache_hit':True}
    from ..economics import usage_cost
    saved={'schema':VERSION,'id':key,'code_identity':identity(),'status':'complete_with_quarantined_claim_reviews',
        'snapshot_id':body['snapshot_id'],'plan_id':snapshot['plan']['id'],'patient_id':snapshot['snapshot']['patient_id'],
        'original_question':snapshot['snapshot']['original_question'],'result':output,'quarantined_claim_reviews':quarantined,
        'failed_reconciliation_id':failed_id,'failed_request_hash':request_hash,'original_provider_response_hash':digest(raw),
        'input_claim_manifest':claims,'reference_manifest':refs,
        'coverage':{'group_answers':len(snapshot['answers']),'input_claims':len(claims),'accounted_claims':len(output['claim_reviews']),
            'model_claim_reviews_retained':len(output['claim_reviews'])-len(quarantined),'system_unassessed_claim_reviews':len(quarantined),
            'cited_excerpts':len(job['excerpt_pool']),'source_units':snapshot['snapshot']['source_units'],
            'source_coverage_upgraded':False,'complete_chart':False,'clinical_validated':False},
        'scope':json.loads(job['body']['messages'][0]['content'])['source_plan'],
        'original_failed_call_cost_estimate':usage_cost(raw['model'],raw.get('usage')),
        'new_paid_api_calls':0,'new_token_count_calls':0,'failed_requests_resent':0,'cache_hit':False,
        'clinical_execution_authorized':False,'human_review_created':False}
    saved['result_digest']=digest(saved)
    # Recheck original response and source identity immediately before saving.
    if prepare(retriever,body,ledger)[-1]!=key:raise ValueError('Recovery dependencies changed')
    target.parent.mkdir(parents=True,exist_ok=True);R.P.atomic_write(target,saved);return saved


def inspect(retriever,body,ledger):
    A.validate(body,('id',));saved=json.loads(location(retriever.path,body['id']).read_text())
    if (saved.get('id')!=body['id'] or saved.get('code_identity')!=identity()
            or saved.get('result_digest')!=digest({k:v for k,v in saved.items() if k!='result_digest'})):
        raise ValueError('Saved quarantined review integrity changed')
    if prepare(retriever,{'snapshot_id':saved['snapshot_id']},ledger)[-1]!=body['id']:
        raise ValueError('Saved quarantined review dependencies changed')
    return saved
