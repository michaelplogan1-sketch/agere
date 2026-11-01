"""Regressions for the observed ordinary-run render and prose repair failures."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from pypdf import PdfReader
from clinical_core.agere.deliverables import patient_evidence as P, patient_report_pdf as PDF,workflow as W,saved_report
from clinical_core.agere.clinical import execution_state as E,backstop_agent as B,system_one_gate as G
import test_computed_answer_prose as prose_fixture
import test_patient_review_profiles as profile_fixture

class ClinicalInputs(unittest.TestCase):
 def test_missing_name_is_unbound_but_received_null_is_a_supported_unknown(self):
  fill={'drug_class':'mra','latest_fill_date_in_window':'2030-05-08','observed_fill_count':10}
  a={'table':[{'patient_id':'A','recent_gdmt_fill_observations':[fill]}]}
  self.assertTrue(P.clinical_collection_issues(a))
  fill['drug_name']=None;self.assertEqual(P.clinical_collection_issues(a),[])
  a['table'][0]['recent_gdmt_fill_observations']=json.dumps([fill]);self.assertEqual(P.clinical_collection_issues(a),[])
  self.assertNotEqual(P.fill_name({'drug_class':'mra'}),P.fill_name(fill))
 def test_malformed_collections_fail_before_paid_review(self):
  for value in (["mra"],[{'drug_name':'Example'}],{'drug_name':'Example'},'[bad'):
   row={'patient_id':'A','recent_gdmt_fill_observations':value}
   calls=[{'result':{'result_view':'agere_result_1','columns':list(row),'rows':[row],'truncated':False}}]
   a={'status':'partial','answer_type':'table','table_from_tool':'agere_result_1','headline':'Source review remains pending.'}
   with patch.object(G,'check',side_effect=AssertionError('Structural failure is free')):
    review=B.review_final(SimpleNamespace(),'Review medication evidence',a,calls,SimpleNamespace(read={}),ledger=None,as_of='2030-05-27')
   self.assertEqual(review['status'],'repair');self.assertEqual(review['calls'],0)
   self.assertIn('drug_name',review['feedback'])
 def test_historical_missing_name_renders_without_inventing_a_medicine(self):
  c=profile_fixture.CurrentNativeCardContracts().context();c['id']='ANS-authored';c['question']='Review received medicines';c['source']={'as_of':'2030-05-27'}
  c['answer']['table'][0]['recent_gdmt_fill_observations']=[{'drug_class':'ace_inhibitor','latest_fill_date_in_window':'2030-05-08','observed_fill_count':10}]
  with tempfile.TemporaryDirectory()as t:
   output=Path(t)/'report.pdf';PDF.render(c,output)
   text='\n'.join(p.extract_text()or''for p in PdfReader(output).pages)
  self.assertIn('Medicine name not returned in this result',text)
  self.assertIn('Alex Example',text)

class RetainedProse(unittest.TestCase):
 def setUp(self):
  self.f=prose_fixture.ComputedProse();self.f.setUp()
 def test_unchanged_compiled_objects_round_trip_and_are_recomputed(self):
  a=self.f.answer();B.bind_computed_prose(a,self.f.calls);before=copy.deepcopy(a)
  draft=E.draft_state(a)['last_draft'];self.assertEqual(draft['headline'],self.f.template());self.assertEqual(a,before)
  draft['table_binding']=a['table_binding'];B.bind_computed_prose(draft,self.f.calls);self.assertEqual(draft['headline'],a['headline'])
  calls=copy.deepcopy(self.f.calls);calls[0]['result']['rows'][0][1]=19
  draft=E.draft_state(a)['last_draft'];draft['table_binding']=a['table_binding'];B.bind_computed_prose(draft,calls)
  self.assertIn('19 visits',draft['headline'])
 def test_edited_fields_cannot_inherit_stale_proof_or_bypass_number_words(self):
  for text in ('all four pillars','current_responsibility_verified=0','not an absent one'):
   a=self.f.answer();B.bind_computed_prose(a,self.f.calls);a['headline']=text
   draft=E.draft_state(a)['last_draft'];self.assertEqual(draft['headline'],text)
   draft['table_binding']=a['table_binding']
   with self.assertRaisesRegex(ValueError,'unbound literals'):B.bind_computed_prose(draft,self.f.calls)

class LocalFailureBoundary(unittest.TestCase):
 def context(self):
  return {'answer':{'status':'partial','missing':['Unresolved interpretation']},'patient_ids':['A'],
   'workflow':[{'kind':'deliverable','title':'Received evidence','template':'patient_review','purpose':'inspection','unresolved':[]}]}
 def test_failure_keeps_context_costs_and_explicit_retry_never_uses_frontier(self):
  records=[]
  with patch.object(W.A,'load',return_value=self.context()),patch.object(W,'read',side_effect=lambda *a:copy.deepcopy(records[-1])if records else{}),patch.object(W,'_save',side_effect=lambda *a:records.append(copy.deepcopy(a[-1]))),patch.object(W.A,'compose_frontier',side_effect=AssertionError('Local retry cannot buy inference')):
   with patch.object(saved_report,'create',side_effect=KeyError('drug_name')):
    first=W.materialize(Path('/authored'),'ANS-authored',generation='saved_report')
   self.assertTrue(first['initialized']);self.assertEqual(first['steps'][0]['render_error']['context_id'],'ANS-authored')
   self.assertEqual(first['new_paid_api_calls'],0);self.assertEqual(first['known_api_estimate_usd'],0)
   with patch.object(saved_report,'create',return_value={'id':'D-fixed','status':'delivered','costs':{'known_api_estimate_usd':0,'api_cost_known':True},'new_paid_api_calls':0}):
    fixed=W.retry_deliverable(Path('/authored'),{'context_id':'ANS-authored','index':0,'attempt':2})
   step=fixed['steps'][0];self.assertEqual(step['status'],'ready');self.assertNotIn('render_error',step)
   self.assertEqual(step['attempts'][0]['render_error']['type'],'KeyError');self.assertEqual(len(step['attempts']),2)
   self.assertEqual(fixed['new_paid_api_calls'],0)
 def test_frontier_unexpected_error_is_not_swallowed_as_local_rendering(self):
  with patch.object(W.A,'load',return_value=self.context()),patch.object(W,'read',return_value={}),patch.object(W.A,'compose_frontier',side_effect=KeyError('provider response')):
   with self.assertRaises(KeyError):W.materialize(Path('/authored'),'ANS-authored',allow_live=True,generation='frontier')

if __name__=='__main__':unittest.main()
