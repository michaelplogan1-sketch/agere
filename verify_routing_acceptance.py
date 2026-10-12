"""Actual saved-question routing checks. Hosted inference must be disabled for first run.
Historical receipts are reused; no clinical scoring, fitting or external execution.
"""
import json, os, sqlite3, urllib.error
from pathlib import Path
import verify_scoped_review_compiler as H
from clinical_core.contracts import digest
OUT=Path(os.environ.get('AGERE_ROUTING_OUT','artifacts/agere-routing-acceptance-v1/http'))
OUT.mkdir(exist_ok=True)
H.BASE=os.environ.get('AGERE_ROUTING_BASE','http://localhost:8887/api/stack/')
def save(name,value):
 p=OUT/(name+'.json');assert not p.exists();p.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');return value
def load(path):return json.loads(Path(path).read_text())
def current_payload(r):return {k:v for k,v in r.items() if k!='request_execution_id'}
def main():
 ledger_before=H.ledger();results=[];rows={};views={}
 source='artifacts/agere-request-lineage-v1/'
 for name in ['baseline','candidate','population']:
  old=load(source+name+'-replay.json')
  prior=save(name+'-before',H.call('decision?id='+old['id']));assert prior['dependencies_current']
  row=save(name+'-replay',H.call('decision/'+('population-prepare' if name=='population' else 'prepare'),old['request']))
  assert current_payload(row)==prior['decision']
  trace=save(name+'-lineage',H.call('decision/lineage',{'decision_id':row['id']}))
  execution=next(t for t in trace['traces'] if t['execution_id']==row['request_execution_id'])
  assert execution['status']=='completed' and execution['events'][-1]['effort']['elapsed_seconds']>=0
  assert not any(e['kind']=='reserved' for e in execution['events'])
  keys={k for t in trace['traces'] if t['relation']!='alternative_decision_same_evidence' for e in t['events'] for k in e.get('returned_receipt_keys',[])}
  assert keys <= {r['request_hash'] for r in trace['requests']}
  assert len({r['request_hash'] for r in trace['requests']})==len(trace['requests'])
  if name=='candidate':assert len(keys)==10 and len(trace['requests'])==10
  if name=='baseline':assert not trace['requests'] and len(trace['alternative_routes']['requests'])==10
  assert not trace['execution_observations']['full_cost_and_review_accounting_complete']
  assert trace['cost']['total_cost_usd'] is None and trace['cost']['human_review_minutes'] is None
  after=save(name+'-after',H.call('decision?id='+old['id']))
  assert after['tasks']==prior['tasks'] and after['decision']==prior['decision']
  rows[name]=row;views[name]=after
  results.append({'case':name,'decision_id':row['id'],'execution_id':row['request_execution_id'],'linked_requests':len(trace['requests']),'historical_estimated_api_usd':trace['cost']['linked_api_estimate_usd'],'work_unchanged':True})
 assert rows['baseline']['answer_id']==rows['candidate']['answer_id']
 assert rows['baseline']['source_versions']==rows['candidate']['source_versions']
 req=load('artifacts/agere-reader-contract-v1/workflow-request.json')
 prior=H.call('decision?id='+req['decision_id'])
 local=[]
 for role in ['cohort_lexical','kev']:
  r=save(role+'-statement',H.call('decision/statement-review',{**req,'role':role}))
  local.append(r['request_execution_id'])
  assert r['status']==('abstained_unsupported_contract' if role=='cohort_lexical' else 'experimental_proposal_only')
  opened=H.call('decision/statement-review-open',{'result_id':r['result_id']})
  assert opened==current_payload(r)
 trace=save('statement-lineage',H.call('decision/lineage',{'decision_id':req['decision_id']}))
 for key in local:
  t=next(t for t in trace['traces'] if t['execution_id']==key)
  assert t['events'][-1]['review_creation_reported'] is False
  assert t['events'][-1]['route']['automatic_acceptance'] is False
 assert H.call('decision?id='+req['decision_id'])['tasks']==prior['tasks']
 local_plan=save('local-question',H.call('compose',{'question':'Inspect source completeness'}))
 assert local_plan['planner_receipt']['route']=='local_compiler' and local_plan['new_paid_api_calls']==0
 # A prior complete graph may no longer share current capability identity. Do
 # not dispatch in that case, and do not call it an exact cache hit.
 # Verify disabled-hosted fallback only on the test server.
 if ':8887/' in H.BASE:
  try:H.call('compose',{'question':'Compare a new unsupported complex outcome and clinical subgroup'})
  except urllib.error.HTTPError as exc:
   error=save('unsupported-hosted',{'status':exc.code,'response':json.load(exc)})
   assert exc.code==400 and 'disabled' in error['response']['error']
  else:raise AssertionError('Expected disabled-hosted abstention')
 # Independent join of the returned keys to stored raw usage (no inference).
 trace=load(OUT/'candidate-lineage.json');prices=load('config/agere/pricing.json');estimate=0
 with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:
  for row in trace['requests']:
   status,model,raw=db.execute('select status,model,response from calls where id=?',(row['request_hash'],)).fetchone()
   assert status==row['status']=='validated';u=json.loads(raw)['usage'];assert u==row['recorded_usage']
   p=prices[model];c=u.get('cache_creation',{})
   estimate+=(u['input_tokens']*p['input']+u['output_tokens']*p['output']+c.get('ephemeral_5m_input_tokens',u.get('cache_creation_input_tokens',0))*p.get('cache_write_5m',0)+c.get('ephemeral_1h_input_tokens',0)*p.get('cache_write_1h',0)+u.get('cache_read_input_tokens',0)*p.get('cache_read',0))/1000000
 assert abs(estimate-trace['cost']['linked_api_estimate_usd'])<1e-12
 save('independent-usage-check',{'unique_keys':len(trace['requests']),'raw_ledger_estimate_usd':estimate,'matches':True,'price_basis':'Existing pinned rates; estimate, not invoice or fresh pricing qualification.'})
 assert H.ledger()==ledger_before
 save('verification',{'status':'passed','cases':results,'same_answer_and_source_versions':True,'local_statement_execution_ids':local,'zero_new_paid_calls':True,'provider_ledger_digest':ledger_before,'original_work_unchanged':True,'actual_code_route':True,'local_reader_route':'existing reading and classifier abstention; no new semantic quality measurement','hosted_route':'ten existing bounded documentary-planner receipts reused; no live new inference','complete_cost_and_review_accounting':False,'limitations':['Historical uninstrumented operations, local subprocess/GPU cost and actual reviewer effort unknown.','Specialist-to-frontier failure/escalation tested with isolated transport fixtures, not newly qualified live semantics.'],'timings':H.TIMINGS})
 print(json.dumps({'status':'passed','cases':results}))
if __name__=='__main__':main()
