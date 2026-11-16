import copy,json,sys,urllib.request,urllib.error
from pathlib import Path
from clinical_core.agere.clinical import conditional_payment as P
out=Path('artifacts/agere-conditional-payment-v1');bridgeout=Path('artifacts/agere-payment-comparison-v1')
cost=json.loads(Path('artifacts/agere-work-cost-v1/scenario.json').read_text())
def post(route,body):
 req=urllib.request.Request('http://127.0.0.1:8885/api/stack/'+route,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'},method='POST')
 with urllib.request.urlopen(req,timeout=120) as r:result=json.load(r)
 return result

def reject(route,body):
 try:post(route,body)
 except urllib.error.HTTPError as e:
  assert e.code==400,(e.code,e.read());return json.loads(e.read())
 raise AssertionError('Unexpected acceptance')
def write(path,row):path.write_text(json.dumps(row,indent=2)+'\n')

if sys.argv[1]=='prepare':
 scope=post('conditional-payment/scope',{'cost_id':cost['id']});assert scope['patient_ids']==['P-7193','SYN-3C114EFC-C0C'];assert len(scope['units']['selected_task'])==4
 req={'cost_id':cost['id'],'question':'What would USD180 per assumed payable patient cover for this selected internal work?',
  'terms':{'id':'synthetic-internal-work-rate','version':'1','label':'Synthetic internal work payment example','source':'Explicit synthetic engineering assumption for this comparison; not a signed payer contract or CMS rate.','currency':'USD','unit_kind':'selected_patient','unit_definition':'One payment per distinct selected patient for this fixed internal work batch.','payment_per_unit':'180.00','period_start':'2029-01-01','period_end':'2029-12-31'},
  'cost_window':'whole_batch','cost_basis':'available_staff_time','alignment_rationale':'Assume this fixed internal work batch falls within the supplied annual period solely for engineering arithmetic; actual period alignment is unverified.',
  'assumed_payable_unit_ids':scope['patient_ids'],'eligibility_assumption':'Assume both selected patients payable only for this hypothetical calculation. No contract, attribution, outcome or eligibility evidence is verified.'}
 assert post('conditional-payment/history',{'cost_id':cost['id']})['scenarios']==[]
 row=post('conditional-payment/compare',req);assert row==post('conditional-payment/compare',req);assert post('conditional-payment/inspect',{'id':row['id']})['current']
 central=row['comparisons']['central']['proposed'];assert P.C.exact(central['modeled_cost'])==31000;assert P.C.exact(central['hypothetical_receipts'])==36000;assert P.C.exact(central['receipts_minus_modeled_cost'])==5000;assert P.C.exact(central['minimum_whole_cent_rate'])==15500
 errors=[]
 for change in ({'assumed_payable_unit_ids':['foreign']},{'signed_contract_established':True},{'cost_basis':'observed_revenue'}):errors.append(reject('conditional-payment/compare',{**req,**change}))
 changed=copy.deepcopy(req);changed['terms']['payment_per_unit']='200.00';errors.append(reject('conditional-payment/compare',changed))
 assert len(post('conditional-payment/history',{'cost_id':cost['id']})['scenarios'])==1
 assert P.strip(post('work-cost/inspect',{'id':cost['id']}))==cost
 write(out/'scope.json',scope);write(out/'request.json',req);write(out/'scenario.json',row);write(out/'rejected-inputs.json',errors)
 print(json.dumps({'status':'passed','scenario_id':row['id'],'assumed_units':2,'central_cost':'310.00','hypothetical_receipts':'360.00','conditional_balance':'50.00','rejected_inputs':len(errors)}))
elif sys.argv[1]=='verify':
 rows=post('conditional-payment/history',{'cost_id':cost['id']})['scenarios'];assert len(rows)==2 and all(r['current'] for r in rows)
 a,b=rows;assert a['request']['terms']['version']=='1' and b['request']['terms']['version']=='2';assert b['assumed_payable_units']==1
 assert P.strip(a)==json.loads((out/'scenario.json').read_text());write(out/'browser-scenario.json',P.strip(b));write(out/'state-after.json',rows)
 central=b['comparisons']['central']['proposed'];assert P.C.exact(central['hypothetical_receipts'])==20000;assert P.C.exact(central['receipts_minus_modeled_cost'])==-11000;assert P.C.exact(central['minimum_whole_cent_rate'])==31000
 saved=post('payment-comparison/history',{'cost_id':cost['id']})['comparisons'];assert len(saved)==1 and saved[0]['current'];comparison=P.strip(saved[0]);assert comparison['left_id']==a['id'] and comparison['right_id']==b['id']
 assert P.C.exact(comparison['rate_effect_at_original_count'])==4000;assert P.C.exact(comparison['count_effect_at_compared_rate'])==-20000;assert P.C.exact(comparison['total_hypothetical_receipt_difference'])==-16000
 assert P.strip(post('payment-comparison/inspect',{'id':comparison['id']}))==comparison
 assert post('payment-comparison/compare',{k:comparison[k] for k in ('left_id','right_id','question')})==comparison
 reject('payment-comparison/compare',{'left_id':a['id'],'right_id':a['id'],'question':'Same scenario rejected'})
 contexts=post('work-cost-context/history',{'cost_id':cost['id']})['contexts'];assert len(contexts)==1 and contexts[0]['current'] and contexts[0]['summary']['accounts']==87
 assert P.strip(post('work-cost/inspect',{'id':cost['id']}))==cost
 for r in rows:
  assert len(r['requirements'])==7 and all(x['status']=='unresolved' for x in r['requirements'])
  assert r['verified_eligible_units'] is None and not r['observed_amounts_netted'] and not r['clinical_benefit_established'] and not r['signed_contract_established']
 write(bridgeout/'comparison.json',comparison);write(bridgeout/'state-after.json',saved)
 receipt={'status':'passed','cost_id':cost['id'],'payment_scenario_ids':[r['id'] for r in rows],'comparison_id':comparison['id'],'current_scenarios':2,'current_differences':1,'fixed_tasks':4,'fixed_patients':2,'unresolved_prerequisites_per_scenario':7,'rate_effect_USD':'40.00','count_effect_USD':'-200.00','total_receipt_difference_USD':'-160.00','original_cost_preserved':True,'received_accounts_preserved':87,'signed_contract_established':False,'clinical_benefit_established':False,'new_provider_calls':0,'external_actions':0}
 write(out/'http-verification.json',receipt);write(bridgeout/'http-verification.json',receipt);print(json.dumps(receipt,indent=2))
