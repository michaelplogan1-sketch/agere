"""Second live development question: source observation + financial/contract reports."""
import json
from pathlib import Path
import verify_graph_decision as V
OUT=Path(__file__).resolve().parent/'artifacts/agere-graph-observation-v1';V.OUT=OUT
QUESTION=('For ACO planning across the entire original patient population, review referral_status_updates over six calendar months ending 2029-08-15, received by that cutoff. Compare arrival and recorded source dates, repeated text, literal source organizations and the constant-rate baseline. Alongside this reporting analysis, reconcile received financial accounts and show unresolved program-payment prerequisites at the same snapshot. Keep these as separate reports under this original question, preserve the full population, exact member sources and missing evidence. Do not infer provider attribution, referral completion, clinical change, payment eligibility, savings or a causal relationship between financial accounts and reporting patterns.')

def main():
 g=V.keep('composition-question','ask',{'question':QUESTION,'live':True})
 ops={n['node']['operation'] for n in g['results']};assert {'observation_process','financial_accounts','accountability_review'}<=ops,ops
 n=next(n for n in g['results'] if n['node']['operation']=='observation_process');r=n['result']
 assert n['node']['parameters']=={'stream':'referral_status_updates','months':6,'available_by':'2029-08-15'}
 assert r['patients_evaluated']==1614 and not r['clinical_change_established'] and not r['provider_performance_established']
 d=V.keep('composition-decision','decision/graph-prepare',{'result_id':g['result_id'],'buyer_id':V.BUYER})
 assert d['question']==QUESTION and d['population']['evaluated']==1614
 assert {'observation_process','financial_accounts','accountability_review'}<={n['node']['operation'] for n in d['nodes']}
 m=V.keep('composition-member','decision/graph-member',{'decision_id':d['id'],'patient_id':'P-1001'})
 assert {'observation_process','financial_accounts','accountability_review'}<={f['operation'] for f in m['findings']}
 replay=V.keep('composition-replay','ask',{'question':QUESTION,'live':True});assert replay['new_paid_api_calls']==0 and replay['result_id']==g['result_id']
 summary={'status':'passed','kind':'Second declared development composition, not independent generalization qualification','question':QUESTION,'decision_id':d['id'],'member_id':m['id'],'result_id':g['result_id'],'operations':sorted(ops),'population':d['population'],'denominators':r['denominators'],'arrivals':r['arrival_comparison']['counts'],'recorded_dates':r['documented_date_comparison']['counts'],'new_paid_api_calls':g['new_paid_api_calls'],'planning_economics':g.get('planning_economics'),'zero_call_replay':True,'reports_joined_under_original_question':True}
 (OUT/'composition-verification.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
