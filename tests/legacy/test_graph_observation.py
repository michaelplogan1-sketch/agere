import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import graph_observation as B,graph_reports as R,graph_contracts as C,analysis_graph as A
from clinical_core.contracts import digest

P={'stream':'received_hospital_summaries','months':3,'available_by':'2029-03-15'}
def record(key,pid='p',arrival='2029-03-01',event='2029-01-01',**kw):
 return {'record_id':key,'patient_id':pid,'source_table':P['stream'],'source_system':'hospital','organization_id':'hospital',
         'available_date':arrival,'event_date':event,'synthetic':True,'identity_status':'matched','text':key,**kw}

class Clocks(unittest.TestCase):
 def test_batched_arrivals_do_not_become_dated_events(self):
  r=B.clocks([record(str(i)) for i in range(4)],['p','absent'],P)
  self.assertEqual(r['arrival_comparison']['counts'],[0,0,4]);self.assertEqual(r['documented_date_comparison']['counts'],[4,0,0])
  self.assertEqual(r['denominators']['selected_members'],2);self.assertEqual(r['denominators']['members_without_included_records'],1)
  self.assertFalse(r['clinical_change_established']);self.assertFalse(r['feed_outage_established'])
 def test_cutoff_unknown_dates_and_source_after_arrival_explicit(self):
  rows=[record('a'),record('future',arrival='2029-03-16'),record('unknown',arrival=None),record('nodate',event=None),record('futureevent',event='2029-04-01')]
  r=B.clocks(rows,['p'],P);self.assertEqual(sum(r['arrival_comparison']['counts']),3)
  self.assertEqual(sum(r['documented_date_comparison']['counts']),1)
  self.assertEqual(r['documented_date_comparison']['excluded_visible_records'],{'unknown_event_date':1,'event_after_cutoff':1})
  self.assertEqual(r['source_date_sensitivity']['clock_counts']['source_after_arrival'],1)
 def test_source_date_series_includes_earlier_arrivals_explicitly(self):
  r=B.clocks([record('pre',arrival='2028-12-30',event='2029-01-02')],['p'],P)
  self.assertEqual(r['arrival_comparison']['counts'],[0,0,0]);self.assertEqual(r['documented_date_comparison']['counts'],[1,0,0])
  self.assertEqual(r['source_date_sensitivity']['counts'],[0,0,0])
 def test_same_text_sensitivity_never_removes_records_or_crosses_patients(self):
  rows=[record('a',text='same'),record('b',text='same'),record('c',pid='q',text='same')]
  r=B.clocks(rows,['p','q'],P)
  self.assertEqual(sum(r['repeat_sensitivity']['repeat_counts']),1);self.assertEqual(r['denominators']['included_records'],3)
  self.assertEqual(sum(len(m['records']) for m in r['members']),3)
 def test_correction_pointers_not_automatic_replacements(self):
  rows=[record('a'),record('same',supersedes_record_id='a'),record('cross',pid='q',supersedes_record_id='a'),record('missing',supersedes_record_id='unreceived'),record('source',source_system='other',supersedes_record_id='a')]
  r=B.clocks(rows,['p','q'],P);edges=r['correction_pointers']
  self.assertEqual([e['status'] for e in edges],['same_patient_source_pointer','cross_patient_pointer','target_not_in_scoped_stream','cross_source_pointer'])
  self.assertTrue(all(not e['replacement_applied'] for e in edges));self.assertEqual(r['denominators']['included_records'],5)
 def test_literal_source_strata_preserve_whole_population_denominator(self):
  r=B.clocks([record('a'),record('b',source_system='other')],['p','q'],P)
  self.assertEqual(len(r['source_groups']),2);self.assertEqual(sum(g['denominators']['included_records'] for g in r['source_groups']),2)
  self.assertTrue(all(g['denominators']['selected_members']==2 for g in r['source_groups']))
  with patch.object(B,'MAX_GROUPS',1),self.assertRaises(ValueError):B.clocks([record('a'),record('b',source_system='other')],['p'],P)
 def test_report_does_not_become_cohort_or_service_nomination(self):
  C.validate_inputs(B.OPERATION,['all'],{'all':'cohort'})
  with self.assertRaises(ValueError):C.validate_inputs('outcome',['r'],{'r':B.OPERATION})
  self.assertEqual(R.status_value(B.OPERATION,{'status':'no_arrivals_in_window'}),('no_arrivals_in_window',None))
 def test_invalid_windows_fail_before_execution(self):
  for bad in [{**P,'months':True},{**P,'months':25},{**P,'available_by':'snapshot'},{**P,'available_by':'2029-03-16'}]:
   with self.subTest(bad=bad),self.assertRaises(ValueError):B.validate(bad,'2029-03-15')

class BoundSources(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name)
  with sqlite3.connect(self.path/'stack.sqlite3') as db:
   db.execute('CREATE TABLE patients(id PRIMARY KEY)');db.executemany('INSERT INTO patients VALUES (?)',[('p',),('q',)])
   db.execute('CREATE TABLE records(id PRIMARY KEY,patient,stream,payload)')
  self.put(record('a'));self.put(record('b',pid='q'))
  def connect(path):return sqlite3.connect(Path(path)/'stack.sqlite3'),{'snapshot':{'as_of':'2029-03-15'}}
  p=patch.object(B.evidence_threads,'connect',side_effect=connect);p.start();self.addCleanup(p.stop)
 def put(self,r):
  with sqlite3.connect(self.path/'stack.sqlite3') as db:db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',(r['record_id'],r['patient_id'],r['source_table'],json.dumps(r)))
 def test_changed_member_only_gets_new_receipt_and_old_receipts_stay(self):
  p=B.prepare(self.path,P);r=B.execute(self.path,['p','q'],P,p);R.attach(self.path,B.OPERATION,['p','q'],P,p,r,{})
  ids={m['patient_id']:m['receipt_id'] for m in r['review_members']}
  self.put(record('a',arrival='2029-02-01'));later=B.prepare(self.path,P);new=B.execute(self.path,['p','q'],P,later)
  self.assertNotEqual(ids['p'],B.receipt_id(later,'p'));self.assertEqual(ids['q'],B.receipt_id(later,'q'))
  self.assertEqual(r['documented_date_comparison']['counts'],new['documented_date_comparison']['counts'])
  self.assertNotEqual(r['arrival_comparison']['counts'],new['arrival_comparison']['counts'])
  with B.storage(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM receipts').fetchone()[0],3)
  with self.assertRaises(ValueError):R.evidence(self.path,r['review_members'][0],{'id':'obs','operation':B.OPERATION,'parameters':P})
 def test_whole_selected_population_and_exact_member_evidence(self):
  self.put(record('b',pid='q',arrival=None));prep=B.prepare(self.path,P);result=B.execute(self.path,['p','q'],P,prep)
  R.attach(self.path,B.OPERATION,['p','q'],P,prep,result,{})
  f,refs,gaps=R.evidence(self.path,result['review_members'][1],{'id':'obs','operation':B.OPERATION,'parameters':P})
  self.assertEqual(refs,{'b':digest(record('b',pid='q',arrival=None))});self.assertEqual(len(gaps),3)
  self.assertEqual(f['status'],'no_arrivals_in_window');self.assertIsNone(f['membership'])
 def test_forged_identity_and_limit_rejected(self):
  with patch.object(B,'MAX_RECORDS',1),self.assertRaises(ValueError):B.prepare(self.path,P)
  self.put(record('a',identity_status='unmatched'))
  with self.assertRaises(ValueError):B.prepare(self.path,P)
 def test_source_change_during_execution_saves_nothing(self):
  prep=B.prepare(self.path,P)
  with patch.object(B,'prepare',return_value={**prep,'identity':'changed'}),self.assertRaises(ValueError):B.execute(self.path,['p'],P,prep)
  self.assertFalse((self.path/'graph-observation.sqlite3').exists())
 def test_empty_population_has_no_invented_events(self):
  r=B.execute(self.path,[],P);self.assertEqual(r['patients_evaluated'],0);self.assertEqual(r['arrival_comparison']['counts'],[0,0,0])
 def test_population_reopen_returns_its_saved_evidence_requests(self):
  from clinical_core.agere.clinical import graph_decision as G
  row={'id':'parent'};r=B.Q.save_followup(self.path,{'schema':B.REQUEST,'decision_id':'parent','evidence_question':B.QUESTIONS[0]})
  B.Q.save_followup(self.path,{'schema':B.REQUEST,'decision_id':'other','evidence_question':B.QUESTIONS[1]})
  with patch.object(G,'inspect',return_value=row):
   view=G.reopen(SimpleNamespace(path=self.path),row)
  self.assertEqual(view['followups'],[r]);self.assertTrue(view['dependencies_current'])
 def test_owned_request_retains_exact_question_and_historical_status(self):
  n={'node':{'id':'obs','operation':B.OPERATION,'parameters':P},'report_summary':{'tested':True}}
  d={'schema':'graph-question-decision/1','id':'decision','question':'Did arrivals or activity change?','population':{'evaluated':2},'nodes':[n]}
  retriever=SimpleNamespace(path=self.path);body={'decision_id':'decision','node_id':'obs','question_id':'feed_history','owner':'team','actor':'fixture'}
  with patch.object(B.Q,'inspect',return_value=d):
   request=B.request(retriever,body);self.assertEqual(request['question'],d['question']);self.assertFalse(request['request_sent'])
   self.assertTrue(B.open_request(retriever,{'request_id':request['result_id']})['current'])
   with self.assertRaises(ValueError):B.request(retriever,{**body,'node_id':'invented'})
  with patch.object(B.Q,'inspect',side_effect=ValueError('Changed source')):
   self.assertFalse(B.open_request(retriever,{'request_id':request['result_id']})['current'])

if __name__=='__main__':unittest.main()
