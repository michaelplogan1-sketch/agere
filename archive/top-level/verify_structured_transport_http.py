"""Actual original-scope reconstruction, planning and preservation via local HTTP."""
import json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.clinical import structured_transport as t,context_builder as c
OUT=Path('artifacts/agere-structured-transport-v1');OLD=Path('artifacts/agere-reference-scope-v1')
TASK='693081077ff08e07f21bdb3203a57511d9b0e45b754c5612a50b4674809de1a4'
def api(route,body=None):
 with urlopen(Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'}),timeout=180) as r:return json.load(r)
def save(name,obj):(OUT/(name+'.json')).write_text(json.dumps(obj,indent=2)+'\n')
def main():
 base=json.loads((OLD/'selection.json').read_text());original=api('/api/stack/context?id='+base['context_id']);before=api('/api/stack/work-state?id='+TASK)
 req={'selection_id':base['id']};selected=api('/api/stack/reference-scope/transport',req);assert selected==api('/api/stack/reference-scope/transport',req)
 context=api('/api/stack/context?id='+selected['context_id']);assert context['original_context_id']==original['id'];assert context['record_scope']==original['record_scope']
 assert context['model_context']['sources']==original['model_context']['sources'];assert context['parameters']['question']==original['parameters']['question']
 parents={u['id']:u for u in original['model_context']['evidence']};units={u['id']:u for u in context['model_context']['evidence']}
 for m in context['structured_transport']['parents']:
  split={'containers':m['containers'],'parts':[{'path':v['path'],'value':units[f['id']]['reported_fields'][v['json_pointer']]} for f in m['fragments'] for v in f['paths']]}
  assert c.encode(t.restore(split))==c.encode(parents[m['parent_unit_id']]['reported_fields'])
 assert [u for u in context['model_context']['evidence'] if u['kind']=='narrative']==[u for u in original['model_context']['evidence'] if u['kind']=='narrative']
 plan=api('/api/stack/context/plan',{'context_id':context['id']});assert plan['can_dispatch'] and not plan['saved_answer_id'];assert max(plan['request_bytes'])<=20000
 history=api('/api/stack/reference-scope/list',{'target':base['target']['target']});assert any(r['id']==selected['id'] and r['current'] for r in history['selections'])
 assert before==api('/api/stack/work-state?id='+TASK);assert original==api('/api/stack/context?id='+base['context_id'])
 try:api('/api/stack/reference-scope/transport',{'selection_id':selected['id']});raise AssertionError('Repeated transformation accepted')
 except HTTPError as e:assert e.code==400
 save('selection',selected);save('context',context);save('plan',plan);save('history',history)
 report={'status':'passed','context_id':context['id'],'source_records':len(context['record_scope']['record_ids']),'original_units':len(parents),'transport_units':len(units),'source_passes':plan['source_passes'],'request_bytes':plan['request_bytes'],
 'lossless_reconstruction':True,'narrative_units_unchanged':True,'original_context_and_work_unchanged':True,'new_live_answers':0,'provider_calls':0,'clinical_validation':False}
 save('http-verification',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
