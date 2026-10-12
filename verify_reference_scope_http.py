"""Live original question -> explicit reference scope -> preserved local work."""
import json,hashlib,sqlite3
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import care_capabilities as care
OUT=Path('artifacts/agere-reference-scope-v1');OLD=Path('artifacts/agere-patient-fit-v1')
TASK='693081077ff08e07f21bdb3203a57511d9b0e45b754c5612a50b4674809de1a4'
def api(route,body=None):
 with urlopen(Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'}),timeout=180) as r:return json.load(r)
def save(name,obj):(OUT/(name+'.json')).write_text(json.dumps(obj,indent=2)+'\n')
def reject(route,body):
 try:api(route,body)
 except HTTPError as exc:
  assert exc.code in (400,403,409);return {'status':exc.code,'body':exc.read().decode()[:1200]}
 raise AssertionError('Invalid operation succeeded')
def main():
 before=api('/api/stack/work-state?id='+TASK)
 q=next(q for g in api('/api/stack/patient-fit?id='+TASK)['sets'] if g['current'] for q in g['questions'] if q['kind']=='prior_service_response')
 old=json.loads((OLD/'context-prior_service_response.json').read_text());oldcurrent=api('/api/stack/context?id='+old['id']);assert old==oldcurrent
 req={'target':q['target'],'record_ids':q['record_ids']}
 p=api('/api/stack/reference-scope/preview',req);assert p==api('/api/stack/reference-scope/preview',req)
 assert p['historical_typed_scope']['connected_record_ids']==q['record_ids'];assert len(p['added_record_ids'])>0
 required=['WHL-R-c50ceb6a28c9dd70b07da4','WHL-R-e0b62683927631842c90aa']
 assert set(required)<=set(p['added_record_ids'])
 selected=q['record_ids']+required
 request={'preview_id':p['id'],'record_ids':selected}
 selection=api('/api/stack/reference-scope/select',request);assert selection==api('/api/stack/reference-scope/select',request)
 context=api('/api/stack/context?id='+selection['context_id'])
 assert not context['omissions'] and context['dispatch']['source_review_allowed']
 assert set(context['record_scope']['record_ids'])==set(selected)==set(context['model_context']['sources'])
 assert context['parameters']['question']==q['review_question'];assert context['id']!=old['id']
 rejections=[reject('/api/stack/reference-scope/select',{'preview_id':p['id'],'record_ids':required}),reject('/api/stack/reference-scope/select',{'preview_id':p['id'],'record_ids':selected+['WHL-R-0bb9ee548fed1ae8ee2bb0']}),reject('/api/stack/reference-scope/answer',{'selection_id':selection['id'],'answer_id':'7f5f1dc9f0500592d59bdb8c64771d31a7c5746b749a6d9448f07c5057bfc36e'})]
 view=api('/api/stack/reference-scope/list',{'target':q['target']});assert any(s['id']==selection['id'] and s['current'] for s in view['selections']);assert not view['answers']
 assert before==api('/api/stack/work-state?id='+TASK);assert oldcurrent==api('/api/stack/context?id='+old['id'])
 with care.db_open(stack_path()) as db:
  for table,rows in json.loads((OLD/'history-baseline.json').read_text()).items():assert [list(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY 1,2')]==rows,table
 save('preview',p);save('selection',selection);save('context',context);save('scope-history',view);save('rejections',rejections)
 report={'status':'passed','original_question':q['target'],'original_context_preserved':old['id'],'expanded_context':context['id'],
 'historical_scope_records':len(p['historical_typed_scope']['connected_record_ids']),'reachable_records':len(p['records']),'reference_edges':len(p['reference_component']['edges']),
 'reference_issues':len(p['reference_component']['issues']),'selected_records':len(selected),'unselected_reachable_records':len(selection['unselected_record_ids']),
 'unit_count':len(context['model_context']['evidence']),'context_bytes':context['budget']['used'],'rejections':len(rejections),
 'all_program_and_buyer_task_histories_unchanged':True,'new_machine_answers':0,'new_paid_calls':0,'clinical_validation':False}
 save('http-verification',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
