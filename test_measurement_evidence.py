"""Authored literal measurement fixtures; no clinical or model qualification."""
import json,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from clinical_core.agere.clinical import clinical_inputs as C,sql_query as Q,general_analysis as G,request_plan as R
from clinical_core.agere.deliverables import answer_context as A,workflow as W
from tools.learning_loop.temporal_development import fixture
import test_general_analysis_workflow as existing_workflow

NOTE_DDL='''CREATE TABLE note_fact(patient_id,record_id,stream,fact_type,value,unit,negated,temporality,event_date,evidence_text,extractor,confidence,qualifier,experiencer,source_field,span_start,span_end)'''
def record(rid='echo1',patient='A',stream='echo',**values):
 r=dict(record_id=rid,patient_id=patient,source_table=stream,synthetic=True,identity_status='matched',
  event_date='2030-05-20',available_date='2030-05-21',observation_date='2030-05-01',status='final',unit='%',ejection_fraction=35)
 r.update(values);return r

def note(db,rid='note1',patient='A',text='EF 30-40% reported.',**values):
 r=record(rid,patient,'notes',text=text);db.execute('INSERT INTO record VALUES(?,?,?,?,?)',(rid,patient,'notes',r['event_date'],json.dumps(r)))
 n=dict(patient_id=patient,record_id=rid,stream='notes',fact_type='ejection_fraction',value=35,unit='%',negated=0,temporality='current',
  event_date=r['event_date'],evidence_text=text,extractor='existing-test-extractor',confidence=.9,qualifier='reported',experiencer='patient',source_field='text',span_start=0,span_end=len(text))
 n.update(values);db.execute('INSERT INTO note_fact VALUES('+','.join('?' for _ in n)+')',tuple(n.values()))

class MeasurementEvidence(unittest.TestCase):
 def setUp(self):
  self.db=Q.register(sqlite3.connect(':memory:'));self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
  self.db.execute('CREATE TABLE record(record_id,patient_id,stream,event_date,payload)');self.db.execute(NOTE_DDL)
 def add(self,**kw):
  r=record(**kw);self.db.execute('INSERT INTO record VALUES(?,?,?,?,?)',(r['record_id'],r['patient_id'],r['source_table'],r['event_date'],json.dumps(r)))
 def rows(self,concept='lvef',notes=True):return [dict(r) for r in self.db.execute('SELECT * FROM '+C.measurement_sql(concept,'2030-05-27',note_facts=notes))]
 def test_three_clocks_and_no_current_phenotype(self):
  self.add();r,=self.rows();self.assertEqual((r['lower'],r['upper'],r['unit']),(35,35,'%'))
  self.assertEqual((r['clinical_date'],r['source_date'],r['arrival_date']),('2030-05-01','2030-05-20','2030-05-21'))
  self.assertEqual(r['current_state_established'],0);self.assertIsNone(r['exclusion'])
  self.assertEqual(json.loads(r['evidence'])['evidence'][0]['record_id'],'echo1')
 def test_missing_measurement_date_is_not_document_date(self):
  self.add(observation_date=None);r,=self.rows();self.assertIsNone(r['clinical_date']);self.assertEqual(r['source_date'],'2030-05-20')
 def test_units_status_quality_and_dependencies_remain_visible(self):
  self.add(unit='fraction');self.add(rid='draft',status='preliminary');self.add(rid='native',stream='diagnostic_test_reports',
   _warehouse_source={'adapter':'original-cohort-warehouse-adapter/1'},test_kind='echocardiogram',measurements={'LVEF_percent':33},report_status='final',test_quality='adequate')
  rows=self.rows();self.assertEqual({r['exclusion'] for r in rows},{'unit_not_mapped','unfinalized_result','unresolved_measurement_dependency'})
 def test_future_unreceived_measurement_is_excluded_and_unknown_availability_fails_closed(self):
  self.add(available_date='2030-06-01');self.assertEqual(self.rows(),[])
  self.add(rid='unknown',available_date=None)
  with self.assertRaises(sqlite3.OperationalError):self.rows()
 def test_egfr_and_creatinine_reuse_same_mapped_units_and_clocks(self):
  self.add(stream='labs',rid='creat',test='creatinine',value=88.4,unit='umol/l',collection_date='2030-04-01')
  self.add(stream='labs',rid='egfr',test='egfr',value=45,unit='mL/min/1.73m2',collection_date='2030-04-02')
  c,=self.rows('creatinine');e,=self.rows('egfr');self.assertAlmostEqual(c['lower'],1.0);self.assertEqual(e['lower'],45)
  self.assertEqual(c['clinical_date'],'2030-04-01');self.assertEqual(e['narrative_scan_available'],0)
 def test_tied_and_different_patient_measurements_are_not_collapsed(self):
  self.add();self.add(rid='echo2',ejection_fraction=55);self.add(rid='echo3',patient='B',ejection_fraction=20)
  self.assertEqual(len(self.rows()),3)
 def test_narrative_midpoint_negation_history_and_subject_are_candidates_only(self):
  note(self.db);note(self.db,rid='other',patient='B',text='Mother previously had EF 35%.',temporality='historical',negated=1,experiencer='family')
  rows=self.rows();self.assertEqual(len(rows),2)
  for r in rows:
   self.assertIsNone(r['lower']);self.assertIsNone(r['upper']);self.assertIsNone(r['clinical_date']);self.assertEqual(r['semantic_status'],'interpretation_required')
   self.assertEqual(r['exclusion'],'unreviewed_extracted_value');self.assertEqual(r['candidate_value'],35)
  evidence=[json.loads(r['evidence']) for r in rows];self.assertTrue(any(e['negated']==1 and e['experiencer']=='family' for e in evidence))
 def test_narrative_orphan_and_wrong_span_are_not_verified(self):
  note(self.db,span_end=1);note(self.db,rid='orphan');self.db.execute("DELETE FROM record WHERE record_id='orphan'")
  self.assertEqual({r['exclusion'] for r in self.rows()},{'source_span_unverified','source_record_unavailable'})
 def test_absent_note_table_supported_and_unknown_concept_rejected(self):
  self.db.execute('DROP TABLE note_fact');self.add();self.assertEqual(self.rows(notes=False)[0]['narrative_scan_available'],0)
  with self.assertRaises(ValueError):C.measurement_sql('made_up','2030-05-27')

def spec(concept='lvef'):
 question=f'List patients and their documented {concept} evidence; simulate source review for patients with unresolved measurement evidence; monitor loop closure'
 sql=("SELECT p.patient_id,count(m.observation_id) AS observations,group_concat(m.lower) AS documented_values,"
      "group_concat(m.clinical_date) AS measurement_dates,group_concat(m.source_date) AS document_dates,"
      "group_concat(m.evidence_text,' | ') AS narrative_evidence,"
      "sum(CASE WHEN m.observation_id IS NOT NULL AND (m.exclusion IS NOT NULL OR m.clinical_date IS NULL) THEN 1 ELSE 0 END)>0 AS needs_review "
      "FROM patient p LEFT JOIN {{measurement_evidence:"+concept+"}} m ON m.patient_id=p.patient_id GROUP BY p.patient_id ORDER BY p.patient_id")
 return question,dict(steps=[dict(name='measurements',purpose='Dated received measurement evidence with explicit uncertainty',sql=sql)],final='measurements',returns='table',
  patient_selection=dict(column='patient_id',quote='List patients'),requirements=[dict(quote=f'List patients and their documented {concept} evidence',applied_in=['measurements'],how='Mapped observations and source dates'),
   dict(quote='patients with unresolved measurement evidence',applied_in=['measurements'],how='Missing measurement clock or exclusion',outputs=['needs_review'])],unresolved=[],workflow=[
   dict(kind='simulation',title='Synthetic measurement review',program='custom',quote='simulate source review for patients with unresolved measurement evidence',unresolved=[],selection=dict(output='needs_review',quote='patients with unresolved measurement evidence')),
   dict(kind='monitor',title='Measurement review progress',mode='work',scope='result',outputs=['work_status'],quote='monitor loop closure',unresolved=[])])

class MeasurementWorkflow(unittest.TestCase):
 execute=existing_workflow.GeneralAnalysisWorkflow.execute
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=fixture(Path(t.name)/'fixture')
  with sqlite3.connect(self.path/'semantic.sqlite3') as db:
   db.execute(NOTE_DDL)
   for pid in ['A','B']:
    r=record(patient=pid,rid=pid,observation_date='2030-05-01' if pid=='A' else None)
    db.execute('INSERT INTO record VALUES(?,?,?,?,?)',(pid,pid,'echo',r['event_date'],json.dumps(r)))
   note(db,patient='C')
  self.catalog=R.Catalog.load(self.path);self.stack=SimpleNamespace(path=self.path,allow_live=False,shared_request_planning=True)
 def test_saved_work_selects_exact_unresolved_subset(self):
  q,s=spec();result,ctx=self.execute(s,q);rows={r['patient_id']:r for r in result['answer']['table']}
  self.assertEqual(rows['A']['needs_review'],0);self.assertEqual(rows['B']['needs_review'],1);self.assertEqual(rows['C']['needs_review'],1)
  self.assertIsNone(rows['C']['documented_values']);self.assertEqual(rows['D']['observations'],0)
  self.assertTrue(any('Source and arrival dates are not measurement dates' in s for s in result['answer']['limitations']))
  W.materialize(self.path,ctx['id']);self.assertEqual({i['patient_id'] for i in A.inspect(self.path,ctx['id'])['items']},{'B','C'})
 def test_empty_and_saved_population_do_not_broaden(self):
  q,s=spec()
  for population in (['A'],[]):
   result,ctx=self.execute(s,q,population=population);self.assertEqual(result['answer']['patient_ids'],population)
   W.materialize(self.path,ctx['id']);self.assertEqual(A.inspect(self.path,ctx['id'])['items'],[])
 def test_source_change_invalidates_saved_answer(self):
  q,s=spec();_,ctx=self.execute(s,q)
  with sqlite3.connect(self.path/'semantic.sqlite3') as db:db.execute("UPDATE record SET payload=json_set(payload,'$.ejection_fraction',55) WHERE record_id='A'")
  with self.assertRaises(ValueError):A.load(self.path,ctx['id'],current=True)

if __name__=='__main__':unittest.main()
