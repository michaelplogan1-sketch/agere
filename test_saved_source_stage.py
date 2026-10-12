"""Completed source progress survives a separate reconciliation failure."""
import copy
import unittest
from unittest.mock import Mock,patch
from clinical_core.agere.clinical import source_synthesis as S
from clinical_core.contracts import digest


class SavedSourceStage(unittest.TestCase):
    def setUp(self):
        self.row={'id':'ctx','patient_id':'p','parameters':{'question':'Original question'},
                  'model_context':{'evidence':[{'id':'u1'},{'id':'u2'}]}}
        self.answer={'id':'answer','context_id':'ctx','context_digest':digest(self.row),
            'patient_id':'p','question':'Original question','status':'complete',
            'passes':[{'result':{'unit_reviews':[{'source_unit_id':'u1'},{'source_unit_id':'u2'}]}}],
            'coverage':{'received_units':2,'submitted_units':2,'model_accounted_units':2,
                'complete_declared_scope':True,'unassessed_units':[{'source_unit_id':'u2'}],
                'cross_group_relations_assessed':False}}
        self.stage=Mock();self.stage.prepare.return_value=(self.row,{},[],[],'answer')
        self.stage.exists.return_value=True;self.stage.inspect.side_effect=lambda *_:copy.deepcopy(self.answer)
        for name,value in [('context',self.row),('stages',(self.stage,Mock()))]:
            p=patch.object(S,name,return_value=value);p.start();self.addCleanup(p.stop)

    def test_reopen_preserves_unknowns_and_never_runs_inference(self):
        result=S.saved_source_stage('path','ctx')
        self.assertEqual(result['answer'],self.answer)
        self.assertEqual(result['unassessed_units'],[{'source_unit_id':'u2'}])
        self.assertFalse(result['reconciliation_completed_by_reopen'])
        self.assertEqual(result['new_paid_api_calls'],0)
        self.stage.synthesize.assert_not_called()

    def test_incomplete_has_no_substitute_answer(self):
        self.stage.exists.return_value=False
        self.assertIsNone(S.saved_source_stage('path','ctx')['answer'])
        self.stage.inspect.assert_not_called();self.stage.synthesize.assert_not_called()

    def test_exact_context_patient_question_and_hash_required(self):
        for field in ('context_id','context_digest','patient_id','question','status'):
            with self.subTest(field=field):
                old=self.answer[field];self.answer[field]='different'
                with self.assertRaises(ValueError):S.saved_source_stage('path','ctx')
                self.answer[field]=old

    def test_all_units_once_and_accounted_counts_required(self):
        for reviews in ([{'source_unit_id':'u1'}],[{'source_unit_id':'u1'},{'source_unit_id':'u1'}],
                        [{'source_unit_id':'u1'},{'source_unit_id':'other'}]):
            self.answer['passes'][0]['result']['unit_reviews']=reviews
            with self.assertRaises(ValueError):S.saved_source_stage('path','ctx')
        self.answer['passes'][0]['result']['unit_reviews']=[{'source_unit_id':'u1'},{'source_unit_id':'u2'}]
        for field in ('received_units','submitted_units','model_accounted_units','complete_declared_scope'):
            old=self.answer['coverage'][field];self.answer['coverage'][field]=False
            with self.assertRaises(ValueError):S.saved_source_stage('path','ctx')
            self.answer['coverage'][field]=old

    def test_stale_saved_parent_is_not_recovered(self):
        self.stage.inspect.side_effect=ValueError('Source changed')
        with self.assertRaisesRegex(ValueError,'Source changed'):S.saved_source_stage('path','ctx')

if __name__=='__main__':unittest.main()
