"""Recorded code enum semantics must not become a Yes for documentation presence."""
import unittest
from types import SimpleNamespace
from clinical_core.agere.clinical import chart_notes as C

class CodeStatusProposition(unittest.TestCase):
    def answer(self,q,code,status='documented'):
        return C.code_status_proposition(q,[{'frame':'care_process','concept':'code status','status':status,'detail':{'code_status':code}}])

    def test_interventions_follow_the_recorded_order(self):
        for code,cpr,tube in [('full_code',True,True),('dnr',False,None),('dni',None,False),('dnr_dni',False,False),('unknown',None,None)]:
            for q,want in [('Does the patient want CPR?',cpr),('Does the patient want intubation?',tube)]:
                with self.subTest(code=code,q=q):self.assertIs(self.answer(q,code)['value'],want)
        self.assertEqual(self.answer('If their heart stops, do they want CPR or intubation?','dnr_dni')['lead'],'No')
        self.assertEqual(self.answer('If their heart stops, do they want CPR or intubation?','full_code')['lead'],'Yes')

    def test_named_code_status_and_three_valued_combinations(self):
        self.assertIs(self.answer('Is their code status DNR?','dnr_dni')['value'],True)
        self.assertIs(self.answer('Is their code status DNR?','full_code')['value'],False)
        self.assertIs(self.answer('Is their code status DNI?','dnr')['value'],None)
        self.assertIs(self.answer('Are they DNR and DNI?','dnr')['value'],None)
        self.assertIs(self.answer('Are they DNR or DNI?','dnr')['value'],True)
        self.assertIs(self.answer('Are they full code?','dnr_dni')['value'],False)

    def test_documentation_presence_is_a_different_proposition(self):
        self.assertIsNone(self.answer('Is a code status documented?','dnr_dni'))
        self.assertIsNone(self.answer('Is a DNR order or code status documented?','unknown','not_mentioned'))
        self.assertIsNone(self.answer('Was DNR discussed?','full_code'))
        self.assertIsNone(self.answer('Do the notes mention DNR?','full_code'))
        self.assertIsNone(C.code_status_proposition('Does the patient want CPR?',[]))
        self.assertIs(self.answer('Does the patient want CPR?','dnr_dni','planned_not_done')['value'],None)
        self.assertIs(self.answer('Does the patient not want CPR?','dnr_dni')['value'],None)

    def test_conflicting_status_does_not_pick_a_convenient_reading(self):
        rows=[{'frame':'care_process','concept':'code status','status':'documented','detail':{'code_status':x}}for x in ('dnr_dni','full_code')]
        self.assertIs(C.code_status_proposition('Does the patient want CPR or intubation?',rows)['value'],None)


    def test_rendered_answer_preserves_record_and_answers_the_intervention(self):
        q='If P-QA has a cardiac arrest, do they want CPR or intubation?'
        pred={'op':'note','frame':'care_process','concept':{'term':'code status','quote':'CPR'},'statuses':['documented'],'quote':q}
        res={'status':'documented','holds':True,'p':1.0,'detail':{'code_status':'dnr_dni'},
             'evidence':[{'status':'documented','supports':True,'record_id':'N-CODE-1','date':'2029-03-04','note_type':'goals_of_care',
                          'quote':'Patient elects DNR/DNI.','detail':{'code_status':'dnr_dni'}}]}
        nf=SimpleNamespace(answer_patient=lambda *a,**kw:res)
        answer=C.render(None,[pred],'P-QA','2030-05-27',nf=nf,yes_no=True,question=q,allow_live=False)
        self.assertEqual(answer['facts']['lead'],'No')
        self.assertTrue(answer['headline'].startswith('No: Recorded code status: DNR/DNI'))
        self.assertIn('no CPR; no intubation',answer['headline'])
        self.assertEqual(answer['sources'][0]['record_id'],'N-CODE-1')
        open_question='What are their wishes about chest compressions and a breathing tube?'
        answer=C.render(None,[pred],'P-QA','2030-05-27',nf=nf,yes_no=False,question=open_question,allow_live=False)
        self.assertTrue(answer['headline'].startswith('Recorded code status: DNR/DNI'))
        self.assertIsNone(answer['facts']['lead'])

    def test_explicit_code_context_keeps_cpr_procedure_questions_separate(self):
        predicates=C.build('Per the code status, does P-QA want CPR?', '2030-05-27')
        self.assertTrue(any(p.get('frame')=='care_process' and p.get('concept',{}).get('term')=='code status' for p in predicates))
        self.assertIs(self.answer('Does the patient want no CPR?','dnr_dni')['value'],None)
        self.assertNotIn('care_process',C.frames_of('Was CPR performed?'))


    def test_unrelated_predicate_is_not_replaced_by_code_status(self):
        rows=[{'frame':'care_process','concept':'code status','status':'documented','detail':{'code_status':'dnr_dni'}},
              {'frame':'preference_refusal','concept':'dialysis','status':'documented','detail':{'decision':'accepted'}}]
        self.assertIsNone(C.code_status_proposition('Does the patient want CPR or accept dialysis?',rows))

    def test_context_connectors_do_not_change_intervention_operators(self):
        self.assertIs(self.answer('Does the patient want CPR and intubation for cardiac arrest or trauma?','dnr')['value'],False)
        self.assertIs(self.answer('Is the patient DNR and DNI in the clinic or the hospital?','dnr')['value'],None)
        self.assertIs(self.answer('Is the patient DNR and DNI or full code?','dnr')['value'],None)


    def test_explicit_preference_conflict_is_not_overridden_by_the_order(self):
        rows=[{'frame':'care_process','concept':'code status','status':'documented','detail':{'code_status':'dnr_dni'}},
              {'frame':'preference_refusal','concept':'chest compressions','status':'documented','detail':{'decision':'accepted'}}]
        result=C.code_status_proposition('Does the patient want CPR?',rows)
        self.assertIsNone(result['value'])
        self.assertTrue(result['preference_conflict'])
        rows[1]['detail']['decision']=['declined']
        self.assertIs(C.code_status_proposition('Did the patient agree to CPR?',rows)['value'],False)

    def test_explicit_preference_can_fill_an_unspecified_intervention(self):
        rows=[{'frame':'care_process','concept':'code status','status':'documented','detail':{'code_status':'dnr'}},
              {'frame':'preference_refusal','concept':'intubation','status':'documented','detail':{'decision':'declined'}}]
        self.assertIs(C.code_status_proposition('Does the patient want intubation?',rows)['value'],False)
