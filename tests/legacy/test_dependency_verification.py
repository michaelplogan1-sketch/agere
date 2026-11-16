"""An answer delivered after the repair rounds are spent says which rows were verified and which were not."""
import unittest
from clinical_core.agere.clinical import semantic_analyst as S


class DependencyVerification(unittest.TestCase):
    def test_rows_are_split_by_their_own_checks(self):
        out=S.dependency_verification({'coverage':{'label':'answers_fully'},'support':{'all_dependencies_checked':True,'packets':[
            {'id':'patient_A','label':'supported','decided_by':'complete_typed_claim_composition'},
            {'id':'patient_B','label':'not_established','decided_by':'typed_claim_checks','claim_feedback':[{'field':'documentary_findings'},{'field':'documentary_findings'}]},
            {'id':'patient_C','label':'supported','decided_by':None},
            {'id':'patient_D','label':'unknown','decided_by':None,'status':'awaiting_dependency_repair'}]}})
        self.assertEqual(out['verified'],['patient_A'])
        self.assertEqual([(f['id'],f['state'],f['fields'])for f in out['flagged']],
            [('patient_B','not_established',['documentary_findings']),('patient_C','unchecked',[]),('patient_D','awaiting_dependency_repair',[])])
        self.assertTrue(out['all_dependencies_checked']);self.assertEqual(out['coverage'],'answers_fully')

    def test_missing_review_yields_nothing_verified(self):
        out=S.dependency_verification({});self.assertEqual((out['verified'],out['flagged'],out['all_dependencies_checked']),([],[],False))
        self.assertEqual(S.SOURCE_REPAIR_ROUNDS,2)


if __name__=='__main__':unittest.main()
