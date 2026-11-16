"""Live existing work and source checks for the captured original question."""
import json
from verify_appointment_graph import api,OUT
r=json.loads((OUT/'final-cross_domain.json').read_text())['response']
n=next(x for x in r['results'] if x['node']['operation']=='appointment_followthrough')
_,source=api('appointment-graph/inspect',{'receipt_id':n['result']['appointment_graph_id'],'patient_id':'P-3002'})
assert source['patient']['status']=='explicit_no_show'
request={'result_id':r['result_id'],'node_id':n['node']['id'],'patient_id':'P-3002'}
_,proposal=api('appointment-graph/work',request)
assert proposal['input_membership']['question_context']['original_question']==r['question']
assert proposal['input_membership']['question_context']['graph_result_id']==r['result_id']
_,saved=api('work/save',{'proposal_id':proposal['proposal_id'],'actor':'Agere manual engineering verification'})
_,replay=api('work/save',{'proposal_id':proposal['proposal_id'],'actor':'Agere manual engineering verification'})
assert saved['changes']['unchanged']==1 and replay['changes']['unchanged']==1
assert saved['changes']['created']==replay['changes']['created']==0
assert saved['external_actions_executed']==0
with (OUT/'work-verification.json').open('x') as f:json.dump({'status':'passed','source':source,'proposal':proposal,'save':saved,'replay':replay,'existing_task_retained':True},f,indent=2)
print(json.dumps({'status':'passed','proposal':proposal['proposal_id'],'changes':saved['changes'],'replay':replay['changes']}))
