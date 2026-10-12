"""Exact saved interpretations remain machine evidence, not patient confirmation."""
import copy,sqlite3,unittest
from unittest.mock import patch
import test_patient_fit_work as fixture
from clinical_core.agere.clinical import patient_fit_answers as a

class Answers(unittest.TestCase):
    record=fixture.PatientFit.record
    context=fixture.PatientFit.context
    match=fixture.PatientFit.match
    queue=fixture.PatientFit.queue
    event=fixture.PatientFit.event
    acquire=fixture.PatientFit.acquire
    view=fixture.PatientFit.view
    question=fixture.PatientFit.question
    def setUp(self):
        fixture.PatientFit.setUp(self);self.acq=self.acquire();self.receipt=self.question()['acquisitions'][0]
        self.fit_answer={'id':'d'*64,'context_id':self.acq['context_id'],'patient_id':'p1','status':'complete',
            'passes':[{},{}],'reconciliation':{'groups':[{}]},'findings':[{'citations':[{}]}],'review_steps':[{}]}
        self.needs={'id':'e'*64,'summary':{'proposed_questions':1,'questions_with_exact_located_records':1,'unbound_questions':0}}
        for module,name,value,key in [(a.source_synthesis,'inspect',self.fit_answer,'d'*64),(a.evidence_needs,'prepare',self.needs,'d'*64),(a.evidence_needs,'inspect',self.needs,'e'*64)]:
            original=getattr(module,name)
            def selected(*args,_value=value,_key=key,_original=original,**kwargs):return copy.deepcopy(_value) if args[1]==_key else _original(*args,**kwargs)
            p=patch.object(module,name,side_effect=selected);p.start();self.addCleanup(p.stop)
        self.req={'target':self.target,'acquisition_id':self.receipt['id'],'answer_id':self.fit_answer['id']}
    def inspect(self):return a.inspect(self.path,{'target':self.target})
    def test_exact_attachment_replays_and_flags_stay_unresolved(self):
        result=a.attach(self.path,self.req);self.assertEqual(result,a.attach(self.path,self.req))
        row=self.inspect()['answers'][0];self.assertTrue(row['current']);self.assertEqual(row['question'],self.q['review_question'])
        for key in ('requirement_resolved','current_patient_goals_confirmed','current_patient_consent_established','human_review_recorded','care_delivered'):
            self.assertFalse(row[key])
    def test_wrong_context_patient_acquisition_or_question_rejected(self):
        for key in ('context_id','patient_id'):
            old=self.fit_answer[key];self.fit_answer[key]='other'
            with self.assertRaisesRegex(ValueError,'exact question context'):a.attach(self.path,self.req)
            self.fit_answer[key]=old
        for req in ({**self.req,'acquisition_id':'x'*64},{**self.req,'target':{**self.target,'question_id':'x'*64}}):
            with self.assertRaises(ValueError):a.attach(self.path,req)
        self.assertEqual(self.inspect()['answers'],[])
    def test_incomplete_or_unreconciled_answer_rejected(self):
        self.fit_answer['status']='partial'
        with self.assertRaisesRegex(ValueError,'Complete saved'):a.attach(self.path,self.req)
        self.fit_answer['status']='complete';self.fit_answer['reconciliation']['groups']=[]
        with self.assertRaisesRegex(ValueError,'reconciliation'):a.attach(self.path,self.req)
    def test_source_change_preserves_historical_answer(self):
        a.attach(self.path,self.req);self.records[0]['permission_to_contact_helper']=True
        row=self.inspect()['answers'][0];self.assertFalse(row['current']);self.assertTrue(row['stale_reasons'])
        with self.assertRaises(ValueError):a.attach(self.path,self.req)
    def test_changed_saved_answer_or_followup_needs_are_visible(self):
        a.attach(self.path,self.req);self.fit_answer['findings'].append({'citations':[]})
        self.assertFalse(self.inspect()['answers'][0]['current']);self.fit_answer['findings'].pop()
        with patch.object(a.evidence_needs,'inspect',side_effect=ValueError('Needs source changed')):
            self.assertFalse(self.inspect()['answers'][0]['current'])
    def test_answer_race_cannot_commit_attachment(self):
        real=a.binding;calls=0
        def changing(*args):
            nonlocal calls
            t,answer,deps=real(*args);calls+=1
            return t,answer,{**deps,'answer_hash':'changed'} if calls>1 else deps
        with patch.object(a,'binding',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'during attachment'):a.attach(self.path,self.req)
        self.assertEqual(self.inspect()['answers'],[])
    def test_immutable_answer_history(self):
        a.attach(self.path,self.req);db=a.database(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM answers')
        finally:db.close()
    def test_no_actor_or_clinical_authority_fields_accepted(self):
        for key in ('actor','current_patient_consent_established','requirement_resolved'):
            with self.assertRaises(ValueError):a.attach(self.path,{**self.req,key:True})

if __name__=='__main__':unittest.main()
