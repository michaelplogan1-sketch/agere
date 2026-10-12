"""Received evidence reveal in an isolated clone; original sources/work stay intact.

The explicit invented reveal tests engineering behavior, not clinical accuracy or
held-out model quality. No generator truth or evaluation targets are used.
Run only while the owned source server is stopped for a consistent local clone.
"""
from pathlib import Path
import json, sqlite3, subprocess, tempfile, time
from clinical_core.contracts import digest
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical import reference_assessment as A, question_decision as Q, care_readiness

ROOT = Path(__file__).resolve().parent
OUT = ROOT/'artifacts/agere-reference-assessment-v1'
SOURCE = ROOT/'artifacts/agere-healthcare-v123/stack'


def save(name, row):
    (OUT/(name+'.json')).write_text(json.dumps(row, indent=2)+'\n')


def install(path, retriever):
    # The same existing runtime contracts used by the local service.
    from clinical_core.agere.clinical import (graph_decision, patient_context, patient_fit_work,
        reference_applicability, reference_lifecycle, option_evidence_registry, option_component_registry,
        cardiometabolic_components, appointment_navigation, literal_reference_runtime, structured_transport,
        reference_reconciliation_transport, linked_claim_integrity, meaning_source_dispatch, source_synthesis)
    graph_decision.install(); patient_context.install_plan_guard(); patient_fit_work.install()
    reference_applicability.install(); reference_lifecycle.install(); option_evidence_registry.install()
    option_component_registry.install(); cardiometabolic_components.install(); appointment_navigation.install()
    literal_reference_runtime.install(); structured_transport.install(); reference_reconciliation_transport.install()
    linked_claim_integrity.install(); meaning_source_dispatch.install(path)
    source_synthesis.configure_plan_answers(retriever); A.install()


def main():
    from run_integrated_stack import keys
    keys()  # Existing authorized credentials; never print values.
    original = json.loads((OUT/'delivered-assessment.json').read_text())['response']['assessment']
    with tempfile.TemporaryDirectory(prefix='agere-evidence-reveal-') as directory:
        path = Path(directory)/'stack'
        subprocess.run(['/bin/cp', '-cR', str(SOURCE), str(path)], check=True)
        print('consistent_private_clone_ready',flush=True)
        stack = Stack(path, allow_live=True); r = stack.retriever
        install(path, r)
        assert A.reopen(r, {'assessment_id': original['result_id']})['current']
        old_task = care_readiness.task(path, original['task_id'])
        old_decision = Q.load(path, original['decision_id'])
        with sqlite3.connect(path/'stack.sqlite3') as db:
            template = json.loads(db.execute("SELECT payload FROM records WHERE patient=? AND stream='telephone_messages' LIMIT 1", (original['patient_id'],)).fetchone()[0])
            fixture = {**template, 'record_id': 'WHL-R-PRIVATE-ASSESSMENT-REVEAL',
                'text': 'Synthetic engineering reveal, patient report on 2029-08-14: I have a working camera and broadband internet at home and completed a video connection check today. My daughter can help with setup on Tuesdays. I do not have a regular helper on the other days. I cannot get to the rehabilitation centre: I have no transport available now. The rehabilitation program schedule and required equipment have not been discussed with me.',
                'event_date': '2029-08-14', 'recorded_date': '2029-08-14', 'available_date': '2029-08-15',
                'event_time': '2029-08-14T16:00:00+00:00', 'recorded_time': '2029-08-14T16:00:00+00:00',
                'signed_time': '2029-08-14T16:00:00+00:00', 'available_time': '2029-08-15T10:00:00+00:00',
                'communication_result': 'patient_reached', 'direction': 'incoming'}
            columns=[x[1] for x in db.execute('PRAGMA table_info(records)')]
            old=db.execute('SELECT * FROM records WHERE id=?',(template['record_id'],)).fetchone()
            values=dict(zip(columns,old));values.update(id=fixture['record_id'],payload=json.dumps(fixture))
            db.execute('INSERT INTO records('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')', [values[k] for k in columns])
        save('private-reveal-source', {'purpose':'Invented engineering fixture, not clinical/evaluation truth', 'record':fixture})
        stale=A.reopen(r, {'assessment_id':original['result_id']});assert not stale['current'];save('private-old-assessment-stale', stale)
        request={'request_id':original['request_id'], 'additional_record_ids':[], 'previous_assessment_id':original['result_id']}
        p=A.preview(r,request);assert fixture['record_id'] in p['selected_record_ids'] and fixture['record_id'] in p['changed_record_ids']
        assert p['original_question']==original['question'] and p['available_by']==original['available_by']
        save('private-reveal-preview',p)
        prepared=A.prepare(r,{'request':request,'preview_id':p['id']});save('private-reveal-prepared',prepared)
        ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
        start=time.perf_counter()
        try: result=A.assess(r,{'preparation_id':prepared['result_id']},ledger,allow_live=True)
        finally: ledger.db.close()
        seconds=time.perf_counter()-start;save('private-reveal-assessment',result)
        now=result['assessment'];assert now['comparison']['answer_changed']
        assert now['coverage']['relation_counts'].get('supporting',0) and now['coverage']['relation_counts'].get('contrary',0)
        assert any(c['record_id']==fixture['record_id'] for e in now['evidence'] for c in e['exact_citations'])
        assert Q.saved_followup(path,original['result_id'])==original
        assert Q.load(path,original['decision_id'])==old_decision
        assert care_readiness.task(path,original['task_id'])==old_task
        current=A.reopen(r,{'assessment_id':now['result_id']});assert current['current'];save('private-current-assessment',current)
        snapshot=A.A.inspect(r,{'id':now['answer_snapshot_id']});save('private-reveal-answer-snapshot',snapshot)
        # Changing the registered reference invalidates the new assessment and
        # forbids replay/review, while retaining both answers and original work.
        import uuid
        L=A.L
        change={'reference_id':now['reference']['id'], 'expected_revision':L.state(path,now['reference']['id'])['revision'],
            'event_kind':'local_withdrawal','event_date':'2026-10-12','actor':'Private engineering verifier',
            'actor_kind':'synthetic_test','reason':'Isolated reference invalidation test; no publisher event asserted',
            'notice':None,'question_changes':{}}
        lp=L.preview(path,change);L.commit(path,{'change':change,'preview_id':lp['id'],'request_id':str(uuid.uuid4())})
        withdrawn=A.reopen(r,{'assessment_id':now['result_id']});assert not withdrawn['current'];save('private-withdrawn-reference',withdrawn)
        try:A.review(r,{'assessment_id':now['result_id'],'actor':'Synthetic verifier','disposition':'documentary_assessment_recorded','note':'Should be rejected after withdrawal'})
        except ValueError:pass
        else:raise AssertionError('Withdrawn reference was reviewed as current')
        assert care_readiness.task(path,original['task_id'])==old_task
        summary={'status':'passed','scenario':'later received access evidence and reference withdrawal',
            'original_sources_untouched':True,'original_question_cutoff_reference_and_task_retained':True,
            'old_assessment_becomes_historical':True,'new_record_automatically_included':True,
            'new_record_exactly_cited':True,'answer_changed':True,'previous_status':original['assessment']['status'],
            'current_status':now['assessment']['status'],'old_answer_and_work_unchanged':True,
            'withdrawal_blocks_current_review':True,'seconds':seconds,'new_paid_api_calls':result['new_paid_api_calls'],
            'clinical_qualification':False,'evaluation_kind':'explicit synthetic engineering reveal; not held-out quality evaluation'}
        save('change-verification',summary);print(json.dumps(summary),flush=True);stack.close()


if __name__=='__main__':main()
