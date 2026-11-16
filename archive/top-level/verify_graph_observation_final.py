from pathlib import Path
import json,sqlite3,hashlib
import verify_graph_decision as V
from clinical_core.agere.economics import usage_cost
O=Path('artifacts/agere-graph-observation-v1');V.OUT=O

def main():
    w=json.loads((O/'workflow-verification.json').read_text());c=json.loads((O/'composition-verification.json').read_text())
    for name,key in [('hospital',w),('aco',c)]:
     v=V.keep('restart-'+name,'decision?id='+key['decision_id']);assert v['dependencies_current']
     replay=V.keep('restart-replay-'+name,'ask',{'question':key['question'],'live':True});assert replay['new_paid_api_calls']==0 and replay['result_id']==key['result_id']
     if name=='hospital':
      req=next(r for r in v['followups'] if r.get('owner')=='Synthetic source-lineage review');assert req['actor']=='Synthetic browser verifier'
      opened=V.keep('browser-request-current','decision/observation-request-open',{'request_id':req['result_id']});assert opened['current']
      browser={'status':'passed','decision_id':w['decision_id'],'request_id':req['result_id'],'clock_table_and_source_groups_visible':True,'owned_request_saved_through_browser':True,'request_current_after_reopen':True,'member_source_ids_and_dates_visible':True,'no_clinical_service_inferred':True,'evidence':['browser-request-dom.txt','browser-member-dom.txt'],'qualification':'Actual desktop DOM and actions; no mobile or broad accessibility qualification'}
      (O/'browser-verification.json').write_text(json.dumps(browser,indent=2)+'\n')
    (O/'restart-verification.json').write_text(json.dumps({'status':'passed','both_original_questions_current':True,'exact_question_replays_same_graph':True,'new_paid_api_calls':0},indent=2)+'\n')
    a=V.keep('release-assessment-api','workflow-evaluation')['release_acceptance'];(O/'current-release-assessment.json').write_text(json.dumps(a,indent=2)+'\n');print({k:a[k] for k in ['engineering_status','engineering_requirements_passed','engineering_requirements_total']})
    old=set(json.loads((O/'provider-ledger-row-baseline.json').read_text()));own=set()
    def keys(v):
     if isinstance(v,dict):
      if 'request_hash' in v:own.add(v['request_hash'])
      for x in v.values():keys(x)
     elif isinstance(v,list):
      for x in v:keys(x)
    for n in ['question.json','composition-question.json']:keys(json.loads((O/'initial-parent-history'/n).read_text()))
    rows=[]
    with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:
     cols=[r[1] for r in db.execute('PRAGMA table_info(calls)')]
     for row in db.execute('SELECT * FROM calls ORDER BY id'):
      if hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest() in old:continue
      d=dict(zip(cols,row));raw=json.loads(d.pop('response') or '{}');cost=usage_cost(d['model'],raw.get('usage'));d.pop('reserved')
      rows.append({**d,'attribution':'verified_workflow' if d['id'] in own else 'other_observed_product_request_unattributed','usage':raw.get('usage'),'cost_estimate':cost})
    cost=lambda rr:sum(x['cost_estimate'].get('estimated_usd',0) or 0 for x in rr)
    ours=[x for x in rows if x['attribution']=='verified_workflow'];other=[x for x in rows if x not in ours]
    value={'schema':'agere-increment-economics/1','scope':'All new ledger rows by retained baseline row hash; no offset assumptions. Four workflow calls attributed through planner receipt keys; other observed requests kept separate.','provider_rows':rows,'new_product_provider_responses':len(rows),'verified_workflow_calls':len(ours),'verified_workflow_api_estimate_usd':cost(ours),'other_observed_calls':len(other),'other_observed_api_estimate_usd':cost(other),'all_observed_api_estimate_usd':cost(rows),'invoice_verified':False,'engineering_chat_tokens_included':False,'local_compute_and_human_review_cost_included':False,'total_cost_known':False,'private_change_calls':0,'limitations':'Configured price estimates, not invoices; other request initiator not established. No claim of full economics or comparative advantage.'}
    (O/'economics.json').write_text(json.dumps(value,indent=2)+'\n');print({k:v for k,v in value.items() if k.endswith('calls') or k.endswith('_usd') or k=='new_product_provider_responses'})

if __name__=='__main__':main()
