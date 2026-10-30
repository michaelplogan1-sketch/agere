"""An invalid capability must remain visible without accepting its claims."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.workflow_scorecard import release_acceptance,capability_checks,saved_report
from clinical_core.agere import workflow_scorecard
from clinical_core.agere.workflow_evaluation_view import page

class ReleaseEvidence(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.snap={'release':'synthetic'};self.spec={'id':'dependency','axis':'engineering','evidence_kind':'workflow','scope_areas':[2],'required_checks':['changed','unchanged'],'required_evidence':'Actual source-change execution'}
  self.contract={'id':'test','individual_cases':['one'],'requirements':[{'id':'individual','axis':'engineering','evidence_kind':'individual_workflows','scope_areas':[1],'required_checks':['one'],'required_evidence':'Saved execution'},self.spec]}
  self.contract_path=self.root/'contract.json';self.contract_path.write_text(json.dumps(self.contract))
  self.raw=self.root/'execution.json';self.raw.write_text('{"verified":true}')
  self.code=self.root/'implementation.py';self.code.write_text('version = 1\n')
  self.receipt={'schema':'agere-capability-acceptance/1','requirement_id':'dependency','snapshot_digest':digest(self.snap),'implementation_sha256':{'implementation.py':self.sha(self.code)},'checks':[{'id':k,'status':'passed','evidence':[{'path':'execution.json','sha256':self.sha(self.raw)}]} for k in ['changed','unchanged']]}
  self.save()
 def sha(self,p):return hashlib.sha256(p.read_bytes()).hexdigest()
 def save(self): (self.root/'receipt.json').write_text(json.dumps(self.receipt))
 def report(self):return release_acceptance([{'id':'one','acceptance':{'status':'passed'}}],self.contract_path,{'dependency':'receipt.json'},self.snap,self.root)
 def test_valid_execution_and_implementation_pass(self):
  r=self.report();self.assertEqual(r['engineering_requirements_passed'],2);self.assertEqual(r['invalid_evidence_requirements'],[])
 def test_changed_raw_evidence_blocks_all_claims_without_hiding_other_requirement(self):
  self.raw.write_text('{}');r=self.report();self.assertEqual(r['engineering_requirements_passed'],1)
  self.assertEqual(r['requirements'][0]['status'],'passed');self.assertEqual(r['requirements'][1]['status'],'blocked')
  self.assertEqual(r['invalid_evidence_requirements'],['dependency'])
  self.assertTrue(all(x['status']=='blocked' for x in r['requirements'][1]['observations']))
  self.assertIn('execution.json',r['requirements'][1]['evidence_error']['detail'])
  with self.assertRaises(ValueError):capability_checks(self.spec,'receipt.json',self.snap,self.root)
 def test_current_code_change_does_not_rewrite_historical_execution(self):
  old=self.raw.read_bytes();self.code.write_text('version = 2\n');r=self.report()
  self.assertEqual(r['engineering_status'],'blocked');self.assertIn('implementation changed',r['requirements'][1]['evidence_error']['detail']);self.assertEqual(self.raw.read_bytes(),old)
 def test_missing_malformed_wrong_snapshot_and_path_escape_fail_closed(self):
  for kind in ['missing','malformed','snapshot','escape','implementation_manifest']:
   with self.subTest(kind=kind):
    self.save();p=self.root/'receipt.json';row=json.loads(p.read_text())
    if kind=='missing':p.unlink()
    elif kind=='malformed':p.write_text('{')
    else:
     if kind=='snapshot':row['snapshot_digest']='different'
     elif kind=='escape':row['checks'][0]['evidence'][0]['path']='../execution.json'
     else:row['implementation_sha256']=[]
     p.write_text(json.dumps(row))
    self.assertEqual(self.report()['requirements'][1]['status'],'blocked')
 def test_unknown_evidence_requirement_and_case_set_still_rejected(self):
  with self.assertRaises(ValueError):release_acceptance([],self.contract_path)
  with self.assertRaises(ValueError):release_acceptance([{'id':'one','acceptance':{'status':'passed'}}],self.contract_path,{'invented':'receipt.json'})
 def test_view_retains_diagnostics_and_escapes_receipt_text(self):
  self.raw.write_text('{}');r=self.report();r['requirements'][1]['evidence_error']['detail']='</script><script>bad()</script>'
  html=page({'release_acceptance':r});self.assertNotIn('</script><script>bad()',html)
  self.assertIn('r.evidence_error.detail',html);self.assertIn('does not rerun the workflows',html)
 def test_replay_display_requires_hashed_evidence_and_matching_snapshot(self):
  p=self.root/'replay.json';r={'schema':'agere-current-workflow-matrix/1','snapshot_digest':digest(self.snap),'rows':[{'current_status':'historical'}]};p.write_text(json.dumps(r))
  self.receipt['workflow_verification']='replay.json';self.save()
  self.assertEqual(self.report()['requirements'][1]['status'],'blocked')
  self.receipt['checks'][0]['evidence'].append({'path':'replay.json','sha256':self.sha(p)});self.save()
  value=self.report()['requirements'][1];self.assertEqual(value['status'],'passed');self.assertEqual(value['workflow_verification']['rows'][0]['current_status'],'historical')
  r['snapshot_digest']='different';p.write_text(json.dumps(r));self.receipt['checks'][0]['evidence'][-1]['sha256']=self.sha(p);self.save()
  self.assertEqual(self.report()['requirements'][1]['status'],'blocked')

class CurrentReport(unittest.TestCase):
 def test_current_trace_and_snapshot_identity_with_historical_fallback(self):
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp);old=root/'artifacts/agere-workflow-evaluation-v1/scorecard.json';old.parent.mkdir(parents=True);old.write_text('{"historical":true}')
   self.assertEqual(saved_report(root),{'historical':True})
   raw=root/'execution.json';raw.write_text('{"actual":"saved"}')
   current=root/'artifacts/agere-workflow-evaluation-current/scorecard.json';current.parent.mkdir(parents=True)
   row={'snapshot':{'release':'current'},'trace_sha256':{'execution.json':hashlib.sha256(raw.read_bytes()).hexdigest()},'scorecard_implementation_sha256':hashlib.sha256(Path(workflow_scorecard.__file__).read_bytes()).hexdigest()}
   current.write_text(json.dumps(row));self.assertEqual(saved_report(root,row['snapshot']),row)
   with self.assertRaisesRegex(ValueError,'another source'):saved_report(root,{'release':'old'})
   raw.write_text('{}')
   with self.assertRaisesRegex(ValueError,'evidence changed'):saved_report(root,row['snapshot'])
   row['trace_sha256']={'../outside.json':'invented'};current.write_text(json.dumps(row))
   with self.assertRaisesRegex(ValueError,'evidence changed'):saved_report(root,row['snapshot'])

if __name__=='__main__':unittest.main()
