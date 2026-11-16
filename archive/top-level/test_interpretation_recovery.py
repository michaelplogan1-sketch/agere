import copy,json,unittest
from unittest.mock import patch
import test_case_synthesis as fixture
import test_case_passes as pass_fixture
from clinical_core.contracts import digest
from clinical_core.agere.clinical import citation_context_recovery as C,source_coverage_recovery as U

class CitationContext(unittest.TestCase):
 def setUp(self):
  fixture.CaseSynthesis.setUp(self)
  self.units={'p1:'+k:v for k,v in self.units.items()};self.a='p1:'+self.a;self.b='p1:'+self.b
  self.units[self.a]['text']='same first; same second'
  self.answer['findings'][0]['citations']=[{'unit':self.a,'quote':'same'}]
  self.job={'body':self.body,'units':self.units,'row':self.row,'excerpts':{self.a:{'excerpts':['same first']}}}
 def raw(self):return {'model':self.body['model'],'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(self.answer)}]}
 def test_expands_only_to_unique_preverified_excerpt_and_preserves_original(self):
  raw=self.raw();prior=copy.deepcopy(raw);out=C.parse(raw,self.job,{})
  self.assertEqual(raw,prior);c=out['findings'][0]['citations'][0]
  self.assertTrue(c['citation_verified']);self.assertEqual(c['quote'],'same first')
  self.assertEqual(c['citation_context_expansion']['original_model_quote'],'same');self.assertEqual(len(out['citation_context_expansions']),1)
 def test_multiple_candidate_excerpts_rejects(self):
  self.job['excerpts'][self.a]['excerpts'].append('same second')
  with self.assertRaisesRegex(ValueError,'ambiguous'):C.parse(self.raw(),self.job,{})
 def test_still_duplicate_full_excerpt_rejects(self):
  self.units[self.a]['text']='same first; same first'
  with self.assertRaisesRegex(ValueError,'ambiguous'):C.parse(self.raw(),self.job,{})
 def test_absent_quote_no_fuzzy_repair(self):
  self.answer['findings'][0]['citations'][0]['quote']='different'
  with self.assertRaises(ValueError):C.parse(self.raw(),self.job,{})
 def test_unsupplied_and_unknown_quotes_reject(self):
  self.job['excerpts'][self.a]['excerpts']=[]
  with self.assertRaises(ValueError):C.parse(self.raw(),self.job,{})
  self.answer['findings'][0]['citations'][0]['unit']='p99:u1'
  with self.assertRaises(ValueError):C.parse(self.raw(),self.job,{})
 def test_normal_exact_quote_keeps_semantic_pending(self):
  self.answer['findings'][0]['citations'][0]['quote']='same first';out=C.parse(self.raw(),self.job,{})
  self.assertFalse(out['citation_context_expansions']);self.assertEqual(out['findings'][0]['semantic_validation'],'pending')
 def test_wrong_model_and_incomplete_output_rejected(self):
  for key,value in [('model','wrong'),('stop_reason','max_tokens')]:
   raw=self.raw();raw[key]=value
   with self.assertRaises(ValueError):C.parse(raw,self.job,{})
 def test_saved_failed_response_revalidated_offline_without_changing_ledger(self):
  key=C.request_key(self.body);raw=self.raw();self.ledger.reserve(key,'claude',self.body['model']);self.ledger.finish(key,error=C.ERROR)
  (self.path/(key+'.json')).write_text(json.dumps({'response':raw}));before=self.ledger.db.execute('SELECT * FROM calls WHERE id=?',(key,)).fetchone()
  with patch('urllib.request.urlopen',side_effect=AssertionError('No network permitted')):
   result,receipt=C.single.send(self.body,'claude',self.ledger,self.path,parse=lambda raw:C.parse(raw,self.job,{}),allow_failed_response_revalidation=True,revalidation_reason=C.VERSION)
  self.assertEqual(receipt['new_paid_api_calls'],0);self.assertEqual(before,self.ledger.db.execute('SELECT * FROM calls WHERE id=?',(key,)).fetchone())
  self.assertEqual(result['citation_context_expansions'][0]['raw_response_digest'],digest(raw))

class CoverageRecovery(unittest.TestCase):
 raw=pass_fixture.CasePasses.raw
 answer=pass_fixture.CasePasses.answer
 def setUp(self):
  pass_fixture.CasePasses.setUp(self)
  bundle,links=U.passes.plan(self.row,U.passes.dependencies(self.path,self.row));self.body,self.units=U.passes.pass_request(bundle['passes'][0],links)
  self.value=json.loads(self.answer(self.body)['content'][0]['text'])
 def parse(self):return U.parse(self.raw(self.body,self.value),self.body,self.units,self.row)
 def test_empty_model_coverage_is_only_system_unassessed(self):
  self.value['unit_reviews']=[];before=copy.deepcopy(self.value);out=self.parse()
  self.assertEqual(before,self.value);self.assertEqual(out['coverage_recovery']['model_reported_dispositions'],0)
  self.assertEqual(out['coverage_recovery']['system_unassessed_dispositions'],len(self.units))
  self.assertTrue(all(r['status']=='unresolved' and r['disposition_authority']=='system_missing_model_disposition' for r in out['unit_reviews']))
 def test_existing_coverage_kept_and_only_omissions_unassessed(self):
  self.value['unit_reviews']=self.value['unit_reviews'][:1];old=copy.deepcopy(self.value['unit_reviews'][0]);out=self.parse()
  self.assertEqual({k:out['unit_reviews'][0][k] for k in old},old);self.assertEqual(out['coverage_recovery']['model_reported_dispositions'],1)
 def test_duplicate_unknown_malformed_and_extra_dispositions_rejected(self):
  original=copy.deepcopy(self.value['unit_reviews'])
  for reviews in [[original[0],original[0]],[{'unit':'foreign','status':'unresolved','reason':'unknown'}],[None],original+[original[0]]]:
   self.value['unit_reviews']=reviews
   with self.assertRaises(ValueError):self.parse()
 def test_invalid_finding_not_repaired_by_missing_coverage(self):
  self.value['unit_reviews']=[];self.value['findings'][0]['citations'][0]['quote']='Invented quote'
  with self.assertRaises(ValueError):self.parse()
 def test_invalid_conflict_quarantines_whole_output_without_accepting_claims(self):
  self.value['findings'][0]['kind']='conflicting_accounts';before=copy.deepcopy(self.value)
  out=self.parse();self.assertEqual(before,self.value);self.assertEqual(out['findings'],[]);self.assertEqual(out['review_steps'],[])
  self.assertEqual(out['coverage_recovery']['quarantined_output_reason'],'A conflict needs two distinct evidence units')
  self.assertEqual(out['coverage_recovery']['model_reported_dispositions'],0)
  self.assertTrue(all(r['status']=='unresolved' for r in out['unit_reviews']))
 def test_no_system_accounting_when_provider_covered_all(self):
  out=self.parse();self.assertEqual(out['coverage_recovery']['system_unassessed_dispositions'],0)
  self.assertTrue(all(r['disposition_authority']=='provider_returned_disposition' for r in out['unit_reviews']))
 def test_read_only_install_dispatch_preserves_other_answers(self):
  from clinical_core.agere.clinical import source_synthesis as S
  old=S.inspect;self.addCleanup(setattr,S,'inspect',old)
  with patch.object(S,'inspect',return_value={'original':True}):
   C.install();U.install();installed=S.inspect;C.install();U.install();self.assertIs(S.inspect,installed)
   self.assertEqual(S.inspect(self.path,'a'*64),{'original':True})
 def test_saved_omissions_recovery_has_zero_network_or_ledger_change(self):
  self.value['unit_reviews']=[];raw=self.raw(self.body,self.value);key=U.request_key(self.body)
  self.ledger.reserve(key,'claude',self.body['model']);self.ledger.finish(key,error=U.ERROR)
  (self.path/(key+'.json')).write_text(json.dumps({'response':raw}));before=self.ledger.db.execute('SELECT * FROM calls WHERE id=?',(key,)).fetchone()
  with patch('urllib.request.urlopen',side_effect=AssertionError('No network permitted')):
   result,receipt=U.single.send(self.body,'claude',self.ledger,self.path,parse=lambda raw:U.parse(raw,self.body,self.units,self.row),allow_failed_response_revalidation=True,revalidation_reason=U.VERSION)
  self.assertEqual(result['coverage_recovery']['model_reported_dispositions'],0);self.assertEqual(receipt['new_paid_api_calls'],0)
  self.assertEqual(before,self.ledger.db.execute('SELECT * FROM calls WHERE id=?',(key,)).fetchone())

if __name__=='__main__':unittest.main()
