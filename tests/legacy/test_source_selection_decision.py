"""Currentness and evidence-boundary checks for activation diagnostics."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.workflow_scorecard import source_selection_decision

class DecisionEvidence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.report_path=self.root/'artifacts/agere-source-selection-v1/decision/report.json';self.report_path.parent.mkdir(parents=True)
        config=self.root/'config/agere';config.mkdir(parents=True);(config/'active_warehouse.json').write_text(json.dumps({'snapshot':{'release':'one'}}))
        (self.root/'source.py').write_text('candidate algorithm');(self.root/'trace.json').write_text('observed failure')
        self.report={'schema':'agere-source-selection-decision/1','snapshot':{'release':'one'},'status':'decision_complete','candidate_promoted':False,
            'code_sha256':{'source.py':hashlib.sha256((self.root/'source.py').read_bytes()).hexdigest()},
            'evidence_sha256':{'trace.json':hashlib.sha256((self.root/'trace.json').read_bytes()).hexdigest()}}
        self.seal()
    def seal(self):
        self.report['result_digest']=digest({k:v for k,v in self.report.items() if k!='result_digest'});self.report_path.write_text(json.dumps(self.report))
    def test_valid_report_remains_display_only(self):
        result=source_selection_decision(self.root);self.assertTrue(result['evidence_verified']);self.assertFalse(result['candidate_promoted']);self.assertEqual(result['use'],'engineering_display_only')
    def test_changed_source_snapshot_is_stale(self):
        self.report['snapshot']['release']='old';self.seal();self.assertEqual(source_selection_decision(self.root)['status'],'stale_or_invalid')
    def test_changed_runtime_code_is_stale(self):
        (self.root/'source.py').write_text('different candidate');self.assertEqual(source_selection_decision(self.root)['status'],'stale_or_invalid')
    def test_changed_trace_or_report_cannot_look_current(self):
        (self.root/'trace.json').write_text('invented success');self.assertFalse(source_selection_decision(self.root)['evidence_verified'])
        self.report['candidate_promoted']=True;self.report_path.write_text(json.dumps(self.report));self.assertFalse(source_selection_decision(self.root)['evidence_verified'])
    def test_outside_evidence_rejected_even_if_hash_matches(self):
        self.report['evidence_sha256']={'../outside.json':'abc'};self.seal();self.assertFalse(source_selection_decision(self.root)['evidence_verified'])
    def test_missing_report_is_unassessed(self):
        self.report_path.unlink();self.assertEqual(source_selection_decision(self.root)['status'],'unassessed')

if __name__=='__main__':unittest.main()
