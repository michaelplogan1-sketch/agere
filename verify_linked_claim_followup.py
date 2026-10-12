"""Return a narrow source expansion to the new consolidated review question."""
from verify_linked_claim_reconciliation_http import call,write,OUT
import json,sys

def prepare():
 a=json.loads((OUT/'P-1001-answer.json').read_text())
 n=call('evidence-needs',{'answer_id':a['id'],'selectors':[]});write('followup-needs.json',n)
 need=next(r for r in n['needs'] if "Obtain the use_statement fields of p4:u2 and p4:u3" in r['question'])
 target={'kind':'evidence_need','receipt_id':n['id'],'need_id':need['id']}
 roots=sorted({a['reference_manifest'][name]['record_id'] for name in ('p4:u2','p4:u3')})
 p=call('reference-scope/preview',{'target':target,'record_ids':roots});write('followup-preview.json',p)
 s=call('reference-scope/select',{'preview_id':p['id'],'record_ids':roots});write('followup-selection.json',s)
 assert s['status']=='source_context_prepared' and len(s['selected_record_ids'])==2 and s['unselected_record_ids']
 plan=call('context/plan',{'context_id':s['context_id']});write('followup-plan.json',plan)
 assert plan['source_passes']==1 and max(plan['request_bytes'])<=20000
 print('Exact follow-up prepared:',plan['source_units'],'units;',len(s['unselected_record_ids']),'linked records explicitly excluded;',plan['request_bytes'],'request bytes',flush=True)
def run():
 s=json.loads((OUT/'followup-selection.json').read_text());a=call('context/synthesize',{'context_id':s['context_id']});write('followup-answer.json',a)
 assert a['status']=='complete' and len(a.get('passes',[]))<=1
 attached=call('reference-scope/answer',{'selection_id':s['id'],'answer_id':a['id']});write('followup-attachment.json',attached)
 state=call('reference-scope/list',{'target':s['target']['target']});write('followup-state.json',state)
 assert len(state['answers'])==1 and state['answers'][0]['current'] and not attached['requirement_resolved']
 replay=call('context/synthesize',{'context_id':s['context_id']});assert replay['cache_hit'] and replay['new_paid_api_calls']==0
 write('followup-verification.json',{'passed':True,'selected_records':2,'question':s['target']['question'],'connected_records_explicitly_excluded':len(s['unselected_record_ids']),'answer_id':a['id'],'attachment_current':True,'requirement_resolved':False,'new_paid_api_calls':a['new_paid_api_calls'],'replay_zero_new_calls':True})
 print('Exact question → two complete source records → saved answer → original-question attachment verified.',flush=True)
if __name__=='__main__':{'prepare':prepare,'run':run}[sys.argv[1]]()
