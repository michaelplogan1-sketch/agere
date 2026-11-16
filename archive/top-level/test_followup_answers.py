"""Machine follow-up interpretations cannot close clinical or internal reviews."""
import copy
import unittest
from unittest.mock import patch
import test_packet_followup as fixture
from clinical_core.agere.clinical import followup_answers as a


class FollowupAnswers(unittest.TestCase):
    put=fixture.Followup.put
    update=fixture.Followup.update
    save=fixture.Followup.save
    match=fixture.Followup.match
    context=fixture.Followup.context
    queue=fixture.Followup.queue
    event=fixture.Followup.event
    credential=fixture.Followup.credential
    bearer=fixture.Followup.bearer
    run_packet=fixture.Followup.run_packet
    review_packet=fixture.Followup.review_packet
    prepare=fixture.Followup.prepare

    def setUp(self):
        fixture.Followup.setUp(self)
        self.followup=self.prepare()
        self.followup_answer={'id':'d'*64,'schema':'stable-reference-reconciliation/1','status':'complete',
            'context_id':self.acquisition['context_id'],'patient_id':'p1','passes':[{},{}],
            'reconciliation':{'groups':[{}]},'findings':[{'citations':[{}]}],'review_steps':[{}]}
        self.needs={'id':'b'*64,'summary':{'proposed_questions':1,'questions_with_exact_located_records':1,'unbound_questions':0}}
        for module,name,value,key in [(a.source_synthesis,'inspect',self.followup_answer,'d'*64),(a.evidence_needs,'prepare',self.needs,'d'*64),(a.evidence_needs,'inspect',self.needs,'b'*64)]:
            original=getattr(module,name)
            def selected(*args,_value=value,_key=key,_original=original,**kwargs):
                return copy.deepcopy(_value) if args[1]==_key else _original(*args,**kwargs)
            p=patch.object(module,name,side_effect=selected);p.start();self.addCleanup(p.stop)
        self.attach_request={'task_id':self.task['id'],'followup_id':self.followup['id'],'answer_id':self.followup_answer['id']}

    def test_attachment_replays_and_does_not_close_work(self):
        before=a.followup.execution.inspect(self.path,self.task['id'])
        result=a.attach(self.path,self.attach_request)
        self.assertEqual(result,a.attach(self.path,self.attach_request))
        state=a.inspect(self.path,self.task['id']);self.assertEqual(len(state['answers']),1);self.assertTrue(state['answers'][0]['current'])
        self.assertEqual(before,a.followup.execution.inspect(self.path,self.task['id']))
        for flag in ['requirement_resolved','human_review_recorded','care_delivered','clinical_execution_authorized']:self.assertFalse(result[flag])

    def test_wrong_context_patient_and_task_rejected(self):
        for field in ['context_id','patient_id']:
            value=self.followup_answer[field];self.followup_answer[field]='different'
            with self.assertRaisesRegex(ValueError,'exact follow-up'):a.attach(self.path,self.attach_request)
            self.followup_answer[field]=value
        with self.assertRaisesRegex(ValueError,'does not belong'):a.attach(self.path,{**self.attach_request,'followup_id':'e'*64})
        self.assertEqual(a.inspect(self.path,self.task['id'])['answers'],[])

    def test_source_stage_with_pending_reconciliation_rejected(self):
        self.followup_answer['reconciliation']['groups']=[]
        with self.assertRaisesRegex(ValueError,'pending'):a.attach(self.path,self.attach_request)
        self.followup_answer['passes']=[{}]
        self.assertEqual(a.attach(self.path,self.attach_request)['status'],'interpretation_attached_for_review')

    def test_changed_review_preserves_but_stales_attachment(self):
        a.attach(self.path,self.attach_request)
        self.review_packet(self.run,expected_revision=1,request_id='review-next')
        row=a.inspect(self.path,self.task['id'])['answers'][0]
        self.assertFalse(row['current']);self.assertIn('stale',' '.join(row['stale_reasons']))
        with self.assertRaisesRegex(ValueError,'stale'):a.attach(self.path,self.attach_request)

    def test_source_answer_or_needs_change_stales_attachment(self):
        a.attach(self.path,self.attach_request);self.followup_answer['findings'][0]['citations'].append({})
        self.assertFalse(a.inspect(self.path,self.task['id'])['answers'][0]['current'])
        self.followup_answer['findings'][0]['citations'].pop()
        with patch.object(a.evidence_needs,'inspect',side_effect=ValueError('Evidence sources changed')):
            row=a.inspect(self.path,self.task['id'])['answers'][0];self.assertFalse(row['current']);self.assertIn('Evidence sources changed',row['stale_reasons'])

    def test_dependency_change_during_attachment_has_no_record(self):
        original=a.binding;calls=0
        def changing(*args,**kwargs):
            nonlocal calls
            item,answer,deps=original(*args,**kwargs);calls+=1
            return item,answer,{**deps,'answer_hash':'changed'} if calls>1 else deps
        with patch.object(a,'binding',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'during attachment'):a.attach(self.path,self.attach_request)
        self.assertEqual(a.inspect(self.path,self.task['id'])['answers'],[])

    def test_no_role_or_resolution_fields_accepted(self):
        for key in ['reviewer','requirement_resolved','credential']:
            with self.assertRaises(ValueError):a.attach(self.path,{**self.attach_request,key:'forged'})


if __name__=='__main__':unittest.main()
