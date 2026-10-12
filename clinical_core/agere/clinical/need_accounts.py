"""Exact financial-account follow-through from a saved interpretation question.

Reuses the registered account projection and integer-cent reconciliation. Shared
account identity is a documentary link, not relevance, hardship or savings.
"""
from pathlib import Path
import hashlib
import json
from ...contracts import digest
from . import need_candidates as candidates, financial_accounts as finance, record_context

VERSION='need-financial-accounts/1'


def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),finance.identity(),candidates.identity()])


def prepare(path,request):
    if not isinstance(request,dict) or set(request)!={'receipt_id','need_id'}:raise ValueError('Exact evidence need required')
    _,need,records,binding=candidates.input_state(path,**request)
    key=digest([identity(),binding]);target=candidates.location(path,key,'need-accounts')
    if target.exists():return {**inspect(path,key),'cache_hit':True}
    report=finance.project(binding['patient_id'],records,binding['snapshot']['as_of'])
    roots=set(binding['original_record_ids']);by_id={r['record_id']:r for r in records};accounts=[]
    for account in report['accounts']:
        anchors=roots&set(account['source_record_ids'])
        if not anchors:continue
        accounts.append({**account,'anchor_record_ids':sorted(anchors),
            'source_records':[{**finance.ref(by_id[r]),'original_scope':r in roots} for r in account['source_record_ids']],
            'outside_original_scope':len(set(account['source_record_ids'])-roots),
            'binding_basis':'Exact registered account ID or registered unique source-parent inheritance',
            'answers_original_question':False})
    result={'schema':VERSION,'id':key,'implementation':identity(),'binding':binding,'question':need['question'],
        'accounts':accounts,'unassigned_original_sources':[s for s in report['unassigned_sources'] if s['source']['record_id'] in roots],
        'status':'related_accounts_available' if accounts else 'no_registered_account_binding_in_original_scope',
        'matched_accounts':len(accounts),'accounting_contract':finance.mapping(),'new_paid_api_calls':0,
        'need_binding_changed':False,'meaning_resolved':False,'hardship_established':False,'realized_savings_established':False,
        'clinical_execution_authorized':False,'limits':report['limits']+[
            'Related account records are located using original answer sources, not a clinical or hardship inference.',
            'A complete received account group does not establish complete external capture or resolve the original question.']}
    if candidates.input_state(path,**request)[3]!=binding:raise ValueError('Account source dependencies changed during preparation')
    result['result_digest']=digest(result);candidates.write(target,result);return {**result,'cache_hit':False}


def inspect(path,key):
    result=json.loads(candidates.location(path,key,'need-accounts').read_text())
    if result['id']!=key or result['implementation']!=identity() or result['result_digest']!=digest({k:v for k,v in result.items() if k!='result_digest'}):raise ValueError('Financial follow-through receipt identity changed')
    binding=candidates.input_state(path,result['binding']['receipt_id'],result['binding']['need_id'])[3]
    if binding!=result['binding'] or digest([identity(),binding])!=key:raise ValueError('Financial follow-through dependencies changed')
    return result


def context(path,request):
    if not isinstance(request,dict) or set(request)!={'account_receipt_id','account_id'}:raise ValueError('Exact received account selection required')
    result=inspect(path,request['account_receipt_id'])
    account=next((a for a in result['accounts'] if a['account_id']==request['account_id']),None)
    if account is None:raise ValueError('Account is not bound to this original evidence scope')
    ids=set(account['source_record_ids'])
    # Include the exact parents used by the existing account-inheritance contract.
    for link in account['account_binding_evidence']:ids.add(link['target']['record_id'])
    row=record_context.create(path,result['binding']['patient_id'],result['question'],sorted(ids))
    if digest(inspect(path,result['id']))!=digest(result):raise ValueError('Account evidence changed during context preparation')
    receipt={'schema':VERSION,'implementation':identity(),'account_receipt_id':result['id'],'account_receipt_hash':digest(result),
        'account_id':account['account_id'],'context_id':row['id'],'record_scope':row['record_scope'],'dispatch':row['dispatch'],
        'budget':row['budget'],'omissions':row['omissions'],'all_received_account_sources_included':True,
        'capture_complete':False,'need_binding_changed':False,'meaning_resolved':False,'new_paid_api_calls':0}
    receipt['id']=digest(receipt);candidates.write(candidates.location(path,receipt['id'],'need-account-contexts'),receipt);return receipt


def inspect_context(path,key):
    result=json.loads(candidates.location(path,key,'need-account-contexts').read_text())
    if result['id']!=key or result['implementation']!=identity() or digest({k:v for k,v in result.items() if k!='id'})!=key:raise ValueError('Account context identity changed')
    report=inspect(path,result['account_receipt_id'])
    if digest(report)!=result['account_receipt_hash']:raise ValueError('Account context dependencies changed')
    row=record_context.inspect(path,result['context_id'])
    if row['patient_id']!=report['binding']['patient_id'] or row['record_scope']!=result['record_scope']:raise ValueError('Account source context changed')
    return result
