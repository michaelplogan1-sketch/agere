"""Private later-source reveal for original-question acquisition, no source mutation.

Invented scenario is engineering evidence, not a held-out clinical evaluation.
Start only with the cooperating original server stopped for a consistent clone.
"""
from pathlib import Path
import json,sqlite3,subprocess,tempfile,time
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical import decision_information as I, reference_assessment as A, question_decision as Q, care_readiness
from verify_reference_assessment_changes import install
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'artifacts/agere-decision-information-v1'

def save(name,value):
    p=OUT/(name+'.json')
    if p.exists():raise ValueError('Evidence capture exists: '+str(p))
    p.write_text(json.dumps(value,indent=2)+'\n')

def main():
    from run_integrated_stack import keys
    keys()
    s=json.loads((OUT/'workflow-verification.json').read_text())
    with tempfile.TemporaryDirectory(prefix='agere-information-reveal-') as tmp:
        path=Path(tmp)/'stack';subprocess.run(['/bin/cp','-cR',str(ROOT/'artifacts/agere-healthcare-v123/stack'),str(path)],check=True)
        print('consistent_private_clone_ready',flush=True)
        stack=Stack(path,allow_live=True);r=stack.retriever;install(path,r)
        old=Q.saved_followup(path,s['assessment_id']);proposal=Q.saved_followup(path,s['proposal_id']);request=Q.saved_followup(path,s['information_request_id'])
        original_task=care_readiness.task(path,s['task_id']);original_decision=Q.load(path,s['member_id'])
        assert I.reopen(r,{'result_id':request['result_id']})['current']
        with sqlite3.connect(path/'stack.sqlite3') as db:
            template=json.loads(db.execute("SELECT payload FROM records WHERE patient=? AND stream='telephone_messages' LIMIT 1",(old['patient_id'],)).fetchone()[0])
            fixture={**template,'record_id':'WHL-R-PRIVATE-INFORMATION-REVEAL',
              'text':'Synthetic engineering reveal, patient telephone report on 2029-08-14: The proposed option is home video rehabilitation, with sessions on Tuesdays. The program told me the sessions can be done entirely at home and do not require centre attendance. I have a working camera device and home broadband internet. I completed a video connection check with the program coordinator today. My daughter can help with setup every Tuesday. I can use the device independently after setup. Since July 24 my walking and home support have not changed, but I have lost access to transport and cannot travel to the centre now. The program has not discussed any other equipment or supervision requirements. This is my report, not documentation of program approval or clinical suitability.',
              'event_date':'2029-08-14','recorded_date':'2029-08-14','available_date':'2029-08-15',
              'event_time':'2029-08-14T16:00:00+00:00','recorded_time':'2029-08-14T16:00:00+00:00','signed_time':'2029-08-14T16:00:00+00:00','available_time':'2029-08-15T10:00:00+00:00',
              'direction':'incoming','communication_result':'patient_reached'}
            columns=[x[1] for x in db.execute('pragma table_info(records)')];values=dict(zip(columns,db.execute('SELECT * FROM records WHERE id=?',(template['record_id'],)).fetchone()));values.update(id=fixture['record_id'],payload=json.dumps(fixture));db.execute('INSERT INTO records('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',[values[k] for k in columns])
        save('private-reveal-source',{'evaluation_kind':'Invented synthetic development reveal; no generator truth','record':fixture})
        old_status=I.reopen(r,{'result_id':request['result_id']});assert not old_status['current'];save('private-old-request-historical',old_status)
        try:I.request(r,{'comparison_id':s['comparison_id'],'request_id':request['request']['id'],'actor':'Must reject stale acquisition'})
        except ValueError:pass
        else:raise AssertionError('Stale comparison generated current acquisition')
        body={'request_id':old['request_id'],'additional_record_ids':[],'previous_assessment_id':old['result_id']}
        p=A.preview(r,body);assert fixture['record_id'] in p['selected_record_ids'];save('private-reassessment-preview',p)
        prep=A.prepare(r,{'request':body,'preview_id':p['id']});save('private-reassessment-prepared',prep)
        ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3');before={x[0] for x in ledger.db.execute('SELECT id FROM calls')};start=time.perf_counter()
        try:
            after=A.assess(r,{'preparation_id':prep['result_id']},ledger,allow_live=True);save('private-reassessment',after)
            obs=I.observe(r,{'request_id':request['result_id'],'assessment_id':after['assessment']['result_id']},ledger,allow_live=True);save('private-observation',obs)
            replay=I.observe(r,{'request_id':request['result_id'],'assessment_id':after['assessment']['result_id']},ledger,allow_live=True);save('private-observation-replay',replay)
            assert replay['observation']==obs['observation'] and replay['new_paid_api_calls']==0
            calls=len({x[0] for x in ledger.db.execute('SELECT id FROM calls')}-before)
            assert calls==after['new_paid_api_calls']+obs['new_paid_api_calls']
        finally:ledger.db.close()
        o=obs['observation'];assert o['exact_citations'] and any(v['value'] is not None for v in o['observation']['values'])
        assert any(c['record_id']==fixture['record_id'] for c in o['exact_citations'])
        assert I.reopen(r,{'result_id':o['result_id']})['current']
        assert all(Q.saved_followup(path,k)==v for k,v in [(old['result_id'],old),(proposal['result_id'],proposal),(request['result_id'],request)])
        assert Q.load(path,s['member_id'])==original_decision and care_readiness.task(path,s['task_id'])==original_task
        before_values={c['contract_id']:c['resolution']['current_value'] for c in o['before']['choices']};after_values={c['contract_id']:c['resolution']['current_value'] for c in o['after']['choices']}
        save('private-answer-snapshot',A.A.inspect(r,{'id':after['assessment']['answer_snapshot_id']}))
        # Reference invalidation also invalidates the observation and its map.
        import uuid
        L=A.L;change={'reference_id':old['reference']['id'],'expected_revision':L.state(path,old['reference']['id'])['revision'],'event_kind':'local_withdrawal','event_date':'2026-10-12','actor':'Private engineering verifier','actor_kind':'synthetic_test','reason':'Isolated invalidation test, no publisher withdrawal asserted','notice':None,'question_changes':{}}
        preview=L.preview(path,change);L.commit(path,{'change':change,'preview_id':preview['id'],'request_id':str(uuid.uuid4())})
        withdrawn=I.reopen(r,{'result_id':o['result_id']});assert not withdrawn['current'];save('private-withdrawn-observation',withdrawn)
        summary={'status':'passed','old_request_becomes_historical':True,'stale_acquisition_rejected':True,'new_record_selected_and_cited':True,'earlier_question_assessment_map_request_work_unchanged':True,'request_sent':False,'clinical_approval':False,'new_paid_calls':calls,'seconds':time.perf_counter()-start,'observed_facts':o['observation']['values'],'review_values_before':before_values,'review_values_after':after_values,'review_values_changed':before_values!=after_values,'replay_calls':0,'withdrawal_invalidates_observation':True,'evaluation_kind':'Declared synthetic engineering reveal; conditional-map semantics and clinical usefulness unqualified'}
        save('change-verification',summary);print(json.dumps(summary),flush=True);stack.close()

if __name__=='__main__':main()
