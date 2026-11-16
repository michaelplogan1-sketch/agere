"""Actual free-form question to observation graph, member and owned evidence request."""
from pathlib import Path
import json,sqlite3
import verify_graph_decision as V
from clinical_core.agere.clinical import graph_observation as B
OUT=Path(__file__).resolve().parent/'artifacts/agere-graph-observation-v1';V.OUT=OUT
QUESTION=('For population and provider planning, investigate whether patterns in received hospital summaries could reflect reporting delays, repeated documentation or changing source capture rather than changes in care. '
 'Retain the entire original patient population. Examine received_hospital_summaries over twelve calendar months ending 2029-08-15, with that receipt cutoff. '
 'Compare arrivals with recorded source dates, exact-text sensitivity and a simple constant-rate baseline; show source-system and organization strata. '
 'Preserve people without received summaries and unknown dates. Expose competing explanations and the missing evidence needed to distinguish them. '
 'Keep this as an analytical review under the original question, with exact member sources and an owned internal evidence request. '
 'Do not infer hospital admission rates, clinical change, feed outages, provider attribution, performance or causal effects from record counts.')

def main():
 g=V.keep('question','ask',{'question':QUESTION,'live':True});nodes=[x for x in g['results'] if x['node']['operation']==B.OPERATION];assert len(nodes)==1,g['graph']
 node=nodes[0];r=node['result'];assert r['patients_evaluated']==1614
 assert node['node']['parameters']=={'stream':'received_hospital_summaries','months':12,'available_by':'2029-08-15'}
 assert not r['clinical_change_established'] and not r['feed_outage_established'] and not r['provider_performance_established']
 d=V.keep('decision','decision/graph-prepare',{'result_id':g['result_id'],'buyer_id':V.BUYER});assert d['question']==QUESTION and d['population']['evaluated']==1614
 assert all(s['candidate_count']==0 for s in d['service_scope'])
 pid=next(m['patient_id'] for m in d['members'] if any(x['status']=='received_arrivals' for x in m['states']))
 m=V.keep('member','decision/graph-member',{'decision_id':d['id'],'patient_id':pid});assert m['question']==QUESTION and m['source_versions']['records']
 req=V.keep('evidence-request','decision/observation-request',{'decision_id':d['id'],'node_id':node['node']['id'],'question_id':'feed_history','owner':'synthetic-source-operations-review','actor':'Synthetic graph observation verifier'})
 current=V.keep('request-open','decision/observation-request-open',{'request_id':req['result_id']});assert current['current'] and not req['request_sent']
 replay=V.keep('question-replay','ask',{'question':QUESTION,'live':True});assert replay['result_id']==g['result_id'] and replay['new_paid_api_calls']==0
 refresh=V.keep('unchanged-refresh','decision/graph-refresh',{'decision_id':d['id']});assert refresh['changed_member_count']==0
 # Independent raw SQL arithmetic: all 1614 people retained, exact same received stream.
 with sqlite3.connect('artifacts/agere-healthcare-v123/stack/stack.sqlite3') as db:
  rows=[json.loads(x[0]) for x in db.execute('SELECT payload FROM records WHERE stream=?',(node['node']['parameters']['stream'],))]
 arrivals=[x for x in rows if isinstance(x.get('available_date'),str) and r['window_start']<=x['available_date']<=r['window_end']]
 assert len(arrivals)==r['denominators']['included_records']
 assert [sum(x['available_date'].startswith(month) for x in arrivals) for month in r['months']]==r['arrival_comparison']['counts']
 dated=[x for x in rows if isinstance(x.get('available_date'),str) and x['available_date']<=r['window_end'] and isinstance(x.get('event_date'),str) and r['window_start']<=x['event_date']<=r['window_end']]
 assert [sum(x['event_date'].startswith(month) for x in dated) for month in r['months']]==r['documented_date_comparison']['counts']
 summary={'status':'passed','question':QUESTION,'result_id':g['result_id'],'decision_id':d['id'],'member_id':m['id'],'patient_id':pid,'node_id':node['node']['id'],'request_id':req['result_id'],'population':d['population'],'denominators':r['denominators'],'arrivals':r['arrival_comparison']['counts'],'recorded_dates':r['documented_date_comparison']['counts'],'same_arrivals_source_dates':r['source_date_sensitivity']['counts'],'lag':r['source_date_sensitivity'],'source_groups':r['source_groups'],'baseline':r['arrival_comparison']['baseline'],'partition_fit_gain':r['arrival_comparison']['penalized_objective_gain'],'independent_counts_match':True,'owned_internal_request':True,'clinical_services_inferred':False,'replay_calls':0,'unchanged_refresh_members':0,'new_paid_api_calls':g['new_paid_api_calls'],'planning_economics':g.get('planning_economics'),'qualification':'One declared development question; no independent clinical/model/competitive qualification'}
 (OUT/'workflow-verification.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps({k:summary[k] for k in ['status','decision_id','population','denominators','new_paid_api_calls']}),flush=True)
if __name__=='__main__':main()
