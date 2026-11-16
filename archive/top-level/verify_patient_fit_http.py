"""Actual saved buyer packet -> exact questions -> bounded received evidence."""
import json,sqlite3
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import care_capabilities as care

OUT=Path('artifacts/agere-patient-fit-v1')
TASK='693081077ff08e07f21bdb3203a57511d9b0e45b754c5612a50b4674809de1a4'
def api(route,body=None):
    with urlopen(Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'}),timeout=180) as r:return json.load(r)
def write(name,data):(OUT/(name+'.json')).write_text(json.dumps(data,indent=2)+'\n')
def reject(route,body):
    try:api(route,body)
    except HTTPError as exc:
        assert exc.code in (400,403,409);return {'status':exc.code,'body':exc.read().decode()[:1200]}
    raise AssertionError('Invalid request succeeded')
def main():
    with care.db_open(stack_path()) as db:task=care.load(db,'capability_tasks',TASK)
    before=api('/api/stack/work-state?id='+TASK)
    req={'task_id':TASK,'expected_revision':task['revision']}
    manifest=api('/api/stack/patient-fit/prepare',req);assert manifest==api('/api/stack/patient-fit/prepare',req)
    assert len(manifest['questions'])==6 and manifest['owner']==task['owner']
    view=api('/api/stack/patient-fit?id='+TASK);current=next(s for s in view['sets'] if s['current'])
    prepared=[];rejections=[]
    for q in current['questions']:
        if q['record_ids']:
            acquire={'target':q['target'],'record_ids':q['record_ids']}
            acquisition=api('/api/stack/readiness/context',acquire)
            assert acquisition==api('/api/stack/readiness/context',acquire)
            context=api('/api/stack/context?id='+acquisition['context_id'])
            assert context['parameters']['question']==q['review_question']
            assert not context['omissions'] and context['dispatch']['source_review_allowed']
            assert set(context['model_context']['sources'])==set(context['record_scope']['record_ids'])
            prepared.append({'question_kind':q['kind'],'question_id':q['id'],'context_id':context['id'],
                'root_records':q['record_ids'],'record_count':len(context['record_scope']['record_ids']),
                'unit_count':len(context['model_context']['evidence']),'context_bytes':context['budget']['used']})
            write('context-'+q['kind'],context)
        else:rejections.append(reject('/api/stack/readiness/context',{'target':q['target'],'record_ids':['WHL-R-0bb9ee548fed1ae8ee2bb0']}))
        rejections.append(reject('/api/stack/readiness/review',{'target':q['target'],'disposition':'addressed','actor_kind':'operator'}))
    helper=next(q for q in current['questions'] if q['kind']=='helper_scope')
    rejections.append(reject('/api/stack/readiness/context',{'target':helper['target'],'record_ids':['WHL-R-2076245da12171489d7129']}))
    rejections.append(reject('/api/stack/patient-fit/prepare',{**req,'expected_revision':999}))
    view=api('/api/stack/patient-fit?id='+TASK)
    assert sum(q['work_state']=='source_context_prepared' for q in view['sets'][0]['questions'])==3
    assert all(not q['requirement_resolved'] for q in view['sets'][0]['questions'])
    packet=api('/api/stack/packet-followup?id='+TASK);assert not packet['prepare_available'] and not packet['followups']
    assert before==api('/api/stack/work-state?id='+TASK)
    with care.db_open(stack_path()) as db:
        for name,rows in json.loads((OUT/'history-baseline.json').read_text()).items():
            assert [list(row) for row in db.execute('SELECT * FROM '+name+' ORDER BY 1,2')]==rows,name
    write('manifest',manifest);write('work-state',view);write('acquisitions',prepared);write('rejections',rejections)
    write('http-verification',{'status':'passed','questions_tracked':6,'source_contexts_prepared':prepared,
        'current_confirmation_questions':3,'rejected_invalid_requests':len(rejections),'existing_work_and_packets_unchanged':True,
        'new_human_or_packet_reviews':0,'external_actions':0,'clinical_validation':False})
    print(json.dumps({'status':'passed','questions':6,'contexts':prepared,'work_preserved':True},indent=2))
if __name__=='__main__':main()
