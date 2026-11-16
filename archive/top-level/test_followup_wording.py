"""Grouping/listing synonyms retain verified saved scope; no protected questions."""
import sqlite3
import unittest
from unittest.mock import Mock, patch
import test_shared_planner as F
from clinical_core.agere.clinical import conversation as C
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
from clinical_core.agere.deliverables import answer_context as A

class FollowupWording(unittest.TestCase):
    def setUp(self):
        self.f=F.SharedPlanner();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.stack.shared_request_planning=True
        with sqlite3.connect(self.f.path/'semantic.sqlite3') as db:
            db.execute('ALTER TABLE patient ADD COLUMN line_of_business TEXT')
            db.execute("UPDATE patient SET line_of_business=coverage_type")

    def test_whole_group_commands_and_aliases(self):
        for text in ('Now show it broken out by LOB.', 'Can you group that by line of business?',
                     'now by payer type', 'break the same measure out by payer type', 'Same thing, split by type of payer'):
            with self.subTest(text=text):
                m=C.group_edit(text);self.assertIsNotNone(m)
                self.assertIn(C.group_dimension(m['dim']),('line of business','payer type'))
                self.assertTrue(C.requires_saved_cohort(text))
        for text in ('Count claims by payer in 2029','What is the rate by practice?', 'Now by payer in 2028'):
            self.assertIsNone(C.group_edit(text))

    def test_list_and_group_preserve_saved_membership(self):
        q='List patients with diabetes';r=self.f.ask(q);saved=A.capture(self.f.path,q,r)
        expected=r['answer']['patient_ids']
        for text in ('Can I get the patient list for that group?', 'Could I see the patient list for this cohort?',
                     'Now show it broken out by LOB.', 'Can you group that by line of business?'):
            with self.subTest(text=text):
                result=A.followup(self.f.path,saved['id'],text)
                self.assertIsNotNone(result)
                self.assertEqual(result['answer']['patient_ids'],expected)
                self.assertNotIn('OUTSIDE',result['answer']['patient_ids'])
                self.assertTrue(C.requires_saved_cohort(text))
        self.assertIsNone(A.followup(self.f.path,saved['id'],'Can I get the patient list for that group excluding CKD?'))

    def test_regroup_reask_keeps_input_scope_even_when_empty(self):
        parent={'question':'Count appointments in 2029','original_question':'Count appointments in 2029',
                'answer':{'answer_type':'count','count':1}}
        for ids in ([],['A'],None):
            stack=Mock(path=self.f.path);stack.compose.return_value={'answer':{}}
            with self.subTest(ids=ids),patch.object(A,'load',return_value={**parent,'input_patient_ids':ids}):
                C.regroup(stack,'Now by practice',{'id':'saved'})
                args,kwargs=stack.compose.call_args
                self.assertEqual(args,('Count appointments in 2029, by practice',))
                self.assertEqual(kwargs,{} if ids is None else {'population':ids})

    def test_grouping_without_saved_context_never_runs_whole_warehouse(self):
        for q in ('Now by payer type','Can you group that by line of business?', 'Can I get the patient list for that group?'):
            with self.subTest(q=q):
                stack=Mock(path=self.f.path,shared_request_planning=True,allow_live=False)
                with self.assertRaises(QuestionNotUnderstood):
                    C.handle_compose(stack,{'question':q})
                stack.compose.assert_not_called()
