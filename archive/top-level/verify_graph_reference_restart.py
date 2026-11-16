"""Fresh-process public API replay of saved scientific reference review and owned work."""
import json
import verify_graph_decision as V
from verify_graph_references import QUESTION,OUT
V.OUT=OUT;s=json.loads((OUT/'final-summary.json').read_text())
p=V.keep('restart-parent','decision?id='+s['decision_id']);assert p['dependencies_current']
m=V.keep('restart-member','decision?id='+s['member_id']);assert m['dependencies_current'] and any(x['task']['id']==s['task_id'] for x in m['tasks'])
a=V.keep('restart-acquisition','decision/acquisition-open',{'request_id':s['acquisition_id']});assert a['policy_current'] and a['current_dependency']['observation']['status']=='applicability_unresolved'
g=V.keep('restart-question','ask',{'question':QUESTION,'live':True});assert g['result_id']==s['result_id'] and g['new_paid_api_calls']==0
(OUT/'restart-verification.json').write_text(json.dumps({'status':'passed','parent_current':True,'member_current':True,'task_preserved':True,'applicability_unresolved':True,'same_graph_result':True,'new_paid_api_calls':0},indent=2)+'\n')
print('restart_passed',flush=True)
