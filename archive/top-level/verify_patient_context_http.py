"""Active question/assessment -> received patient context -> internal buyer packet."""
import hashlib,json,sqlite3
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import care_capabilities as care,patient_context,answer_retirement
from clinical_core.agere.gateway import BudgetLedger

OUT=Path('artifacts/agere-patient-context-v1');BUYER='synthetic-patient-access-review';ACTOR='Automated synthetic patient-context verification'
def api(route,body=None):
    with urlopen(Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'}),timeout=120) as response:return json.load(response)
def write(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
def old_rows():
    with care.db_open(stack_path()) as db:
        return {name:sorted(db.execute(sql).fetchall()) for name,sql in {
            'work_items':'SELECT * FROM work_items','work_events':'SELECT * FROM work_events',
            'prior_buyer_tasks':"SELECT * FROM capability_tasks WHERE buyer!='"+BUYER+"'",
            'prior_buyer_events':"SELECT e.* FROM capability_events e JOIN capability_tasks t ON t.id=e.task WHERE t.buyer!='"+BUYER+"'"}.items()}
def main():
    before=old_rows();ledger=BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt()
    with care.db_open(stack_path()) as db:
        old_task=care.load(db,'capability_tasks','560ae2b3627f98ce001e7bbf3806512ec6d85756e70446ac6f6eaa08e5dfd3ac')
        old_plan=care.load(db,'capability_plans',old_task['plan_id'])
    # Explicitly synthetic local agent capability, not a new clinical service or buyer claim.
    service=next(o['capability'] for o in old_plan['options'] if o['id']==old_task['option_id'])
    service={**service,'id':'patient-access-evidence-review','name':'Synthetic patient priorities and access evidence review',
             'provenance':'Automated synthetic integration fixture; no real buyer capability or external care service asserted'}
    current=care.portfolio(stack_path(),BUYER)
    if current['revision']==0:api('/api/stack/capabilities/save',{'buyer_id':BUYER,'expected_revision':0,'actor':ACTOR,'capabilities':[service]})
    else:assert current['capabilities']==[service]
    context=api('/api/stack/patient-context',{'patient_id':old_task['patient_id'],'as_of':'2029-08-15'});assert len(context['reports'])==7
    assert context==api('/api/stack/patient-context',context['parameters'])
    # Compare every projected field to the actual received account, not model truth.
    with sqlite3.connect(stack_path()/'stack.sqlite3') as db:
        for report in context['reports']:
            record=json.loads(db.execute('SELECT payload FROM records WHERE id=?',(report['record_id'],)).fetchone()[0])
            assert report['record_hash']==care.digest(record)
            for key,value in report['fields'].items():assert key in record['_warehouse_fields'] and type(value)==type(record[key]) and value==record[key]
    request={**old_plan['request'],'buyer_id':BUYER,'patient_context_id':context['id'],
             'rationale':'Review exact dated patient and practical-access reports alongside the source-bound functional assessment. Confirm current patient goals and wishes; no service recommendation, consent or clinical execution inferred.'}
    plan=api('/api/stack/capabilities/prepare',request);assert plan==api('/api/stack/capabilities/prepare',request)
    option=plan['options'][0];assert not option['clinical_execution_authorized']
    checks=plan['patient_context']['option_review_checks'][option['id']];assert {'patient_goals','service_consent','digital_access','helper_scope'}<={c['kind'] for c in checks}
    queue={'plan_id':plan['id'],'option_id':option['id'],'actor':ACTOR,'participation_context_id':plan['participation_contexts'][option['id']]['id']}
    task=api('/api/stack/capabilities/queue',queue)['task'];assert api('/api/stack/capabilities/queue',queue)['idempotent_replay']
    if task['state']=='queued_for_internal_review':
        state=api('/api/stack/work-state?id='+task['id']);assert not state['packet_matches_recorded_dependencies']
        task=api('/api/stack/capabilities/event',{'task_id':task['id'],'expected_revision':task['revision'],'request_id':'patient-access-context-packet-v1','action':'prepare_handoff','actor':ACTOR,'note':'Internal synthetic evidence packet. No patient contact, clinical judgment, consent or service delivery.'})['task']
    view=api('/api/stack/patient-context/task?id='+task['id']);state=api('/api/stack/work-state?id='+task['id'])
    assert view['handoff_context_current'] and state['layers']['patient_context']['packet_current'] and state['packet_matches_recorded_dependencies']
    assert view['handoff_context']['context']==context and not view['current_patient_consent_established']
    assert before==old_rows();assert BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt()==ledger
    write('context',context);write('matching-plan',plan);write('task',task);write('task-context',view);write('work-state',state)
    write('http-verification',{'patient_id':task['patient_id'],'buyer_id':BUYER,'task_id':task['id'],'context_id':context['id'],'plan_id':plan['id'],'report_count':len(context['reports']),'review_questions':len(checks),'all_projected_values_match_received_sources':True,'old_work_and_task_history_unchanged':True,'new_synthetic_internal_tasks':1,'new_provider_calls':0,'external_actions':0,'clinical_validation':False,'ledger':ledger})
    print(json.dumps({'status':'passed','source_reports':7,'review_questions':len(checks),'task_id':task['id'],'prior_work_unchanged':True},indent=2))
if __name__=='__main__':main()
