import copy
import sqlite3
import unittest
from unittest.mock import patch
import test_need_candidates as fixture
import test_need_accounts as account_fixture
from clinical_core.agere.clinical import evidence_answers as a


def child_answer(test,selected):
    test.child={'id':'b'*64,'status':'complete','patient_id':'P-1','context_id':selected['context_id'],
        'passes':[{}],'findings':[],'review_steps':[{'id':'child','question':'What remains unresolved?','finding_ids':[]}]}
    original=a.source_synthesis.inspect
    def lookup(path,key):return copy.deepcopy(test.child) if key=='b'*64 else original(path,key)
    p=patch.object(a.source_synthesis,'inspect',side_effect=lookup);p.start();test.addCleanup(p.stop)
    test.link={'receipt_id':test.need_receipt['id'],'need_id':'need1','selection_kind':'candidates','selection_id':selected['id'],'answer_id':test.child['id']}


class Answers(unittest.TestCase):
    setUpBase=fixture.Candidates.setUpBase
    put=fixture.Candidates.put
    chain=fixture.Candidates.chain
    create=fixture.Candidates.create
    hit=fixture.Candidates.hit
    def setUp(self):
        fixture.Candidates.setUp(self)
        self.discovery=a.candidates.prepare(self.path,self.request,self.retriever)
        selected=a.candidates.select(self.path,{'candidate_id':self.discovery['id'],'record_ids':['a']})
        child_answer(self,selected)

    def test_exact_attachment_replay_and_original_need_unchanged(self):
        row=a.attach(self.path,self.link);self.assertEqual(a.attach(self.path,self.link),row)
        history=a.inspect(self.path,{'receipt_id':self.link['receipt_id'],'need_id':'need1'})
        self.assertEqual(len(history['answers']),1);self.assertTrue(history['answers'][0]['current'])
        self.assertFalse(row['requirement_resolved']);self.assertFalse(row['human_review_recorded']);self.assertFalse(row['clinical_execution_authorized'])
        self.assertEqual(a.evidence_needs.inspect(self.path,self.need_receipt['id']),self.need_receipt)

    def test_wrong_context_patient_question_and_forged_resolution_rejected(self):
        for field,value in [('context_id',self.context['id']),('patient_id','other'),('status','incomplete')]:
            old=self.child[field];self.child[field]=value
            with self.assertRaises(ValueError):a.attach(self.path,self.link)
            self.child[field]=old
        for change in [{'need_id':'other'},{'selection_kind':'unregistered'},{'requirement_resolved':True}]:
            with self.assertRaises(ValueError):a.attach(self.path,{**self.link,**change})

    def test_reconciliation_is_required_for_multiple_source_passes(self):
        self.child['passes']=[{},{}]
        with self.assertRaisesRegex(ValueError,'reconciliation'):a.attach(self.path,self.link)
        self.child['reconciliation']={'groups':[{}]};self.assertFalse(a.attach(self.path,self.link)['requirement_resolved'])

    def test_original_question_change_leaves_stale_immutable_history(self):
        a.attach(self.path,self.link);self.answer['review_steps'][0]['question']='Changed original question'
        state=a.inspect(self.path,{'receipt_id':self.link['receipt_id'],'need_id':'need1'})
        self.assertFalse(state['answers'][0]['current']);self.assertTrue(state['answers'][0]['stale_reasons'])
        with self.assertRaises(ValueError):a.attach(self.path,self.link)

    def test_changed_saved_answer_marks_attachment_stale(self):
        a.attach(self.path,self.link);self.child['findings']=[{'id':'f','citations':[],'text':'Changed result'}]
        self.assertFalse(a.inspect(self.path,{'receipt_id':self.link['receipt_id'],'need_id':'need1'})['answers'][0]['current'])

    def test_append_only_storage_and_source_race(self):
        row=a.attach(self.path,self.link);db=a.database(self.path)
        try:
            for sql in ['DELETE FROM answers','UPDATE answers SET need="other"']:
                with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
        finally:db.close()
        original=a.evidence_needs.prepare
        def changed(*args):
            result=original(*args);self.child['status']='incomplete';return result
        with patch.object(a.evidence_needs,'prepare',side_effect=changed):
            with self.assertRaises(ValueError):a.attach(self.path,self.link)

    def test_dated_candidate_selection_retains_question_origin(self):
        t=a.timing.prepare(self.path,{'kind':'candidates','id':self.discovery['id']})
        w=a.timing.window(self.path,{'timing_id':t['id'],'window':{'basis':'source_event','start':'2028-01-01','end':'2028-09-30'}})
        selected=a.timing.select(self.path,{'window_id':w['id'],'include_unresolved':True})
        self.child['context_id']=selected['context_id'];self.link.update(selection_kind='candidate_window',selection_id=selected['id'])
        self.assertEqual(a.attach(self.path,self.link)['dependencies']['context_id'],selected['context_id'])


class AccountAnswers(unittest.TestCase):
    setUpBase=account_fixture.NeedAccounts.setUpBase
    put=account_fixture.NeedAccounts.put
    chain=account_fixture.NeedAccounts.chain
    create=account_fixture.NeedAccounts.create
    hit=account_fixture.NeedAccounts.hit
    def setUp(self):
        account_fixture.NeedAccounts.setUp(self)
        report=a.accounts.prepare(self.path,self.request)
        selected=a.accounts.context(self.path,{'account_receipt_id':report['id'],'account_id':'a'})
        child_answer(self,selected);self.link['selection_kind']='account'
    def test_account_selection_must_share_exact_question_and_saved_context(self):
        row=a.attach(self.path,self.link);self.assertFalse(row['requirement_resolved'])
        self.child['context_id']=self.answer['context_id']
        with self.assertRaisesRegex(ValueError,'exact selected context'):a.attach(self.path,self.link)


if __name__=='__main__':unittest.main()
