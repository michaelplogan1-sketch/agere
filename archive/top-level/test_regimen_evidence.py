"""Authored source-conflict development cases; no clinical or model qualification."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from clinical_core.agere.clinical import dose as D, request_plan as R
from clinical_core.agere.deliverables import answer_context as A, workflow as W
from tools.learning_loop.temporal_development import fixture
import test_general_analysis_workflow as existing_workflow

NOTE_SCHEMA = '''CREATE TABLE note_therapy_change(
    patient_id,record_id,event_date,target_kind,target,action,evidence_text,extractor)'''


class RegimenEvidence(unittest.TestCase):
    def setUp(self):
        self.db = D.register(sqlite3.connect(':memory:'))
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE record(record_id,patient_id,stream,payload)')
        self.db.execute(NOTE_SCHEMA)

    def regimen(self, patient='A', drug='carvedilol', day='2030-01-01', status='active'):
        self.db.execute('INSERT INTO record VALUES(?,?,?,?)', (
            'list-'+patient+'-'+day, patient, 'medication_lists', json.dumps(dict(
                event_date=day, name=drug, dose='25 mg', frequency='BID', list_status=status))))

    def note(self, patient='A', target='carvedilol', day='2030-05-01', action='stop',
             rid='note', text='Stop carvedilol.', kind='drug'):
        self.db.execute('INSERT INTO note_therapy_change VALUES(?,?,?,?,?,?,?,?)',
                        (patient,rid,day,kind,target,action,text,'unqualified-test-extraction'))

    def rows(self, notes=True):
        return [dict(r) for r in self.db.execute('SELECT * FROM '+D.current_dose_sql(
            '2030-05-27',180,note_changes=notes))]

    def test_later_stop_requires_interpretation_and_keeps_structured_evidence(self):
        self.regimen(); self.note()
        r, = self.rows()
        self.assertEqual((r['dose_status'],r['statement_state']), ('interpretation_required',)*2)
        self.assertIsNone(r['daily_mg'])
        self.assertEqual(r['documented_daily_mg'],50)
        self.assertEqual(r['documented_dose_status'],'at_target')
        self.assertEqual(set(json.loads(r['source_record_ids'])),{'list-A-2030-01-01','note'})
        n, = json.loads(r['note_evidence'])
        self.assertEqual((n['evidence_text'],n['extractor']),('Stop carvedilol.','unqualified-test-extraction'))
        self.assertEqual(r['current_use_established'],0)

    def test_extraction_polarity_is_not_promoted_to_a_clinical_change(self):
        self.regimen(); self.note(action='dose_increase',text='Did not increase carvedilol today.')
        r, = self.rows()
        self.assertIsNone(r['daily_mg'])
        self.assertEqual(r['dose_status'],'interpretation_required')
        self.assertIn('Did not increase',r['note_evidence'])

    def test_same_day_changes_and_note_only_drugs_remain_explicit(self):
        self.regimen(day='2030-05-01')
        self.note()
        self.note(patient='B',target='metformin',action='start',rid='diabetes')
        self.note(patient='C',target='simvastatin',action='dose_decrease',rid='lipid')
        rows={r['patient_id']:r for r in self.rows()}
        self.assertEqual(set(rows),{'A','B','C'})
        self.assertTrue(all(r['note_review_required']==1 and r['daily_mg'] is None for r in rows.values()))
        self.assertIsNone(rows['B']['documented_daily_mg'])
        self.assertIsNone(rows['C']['dose_date'])

    def test_newer_structured_statement_supersedes_older_note_candidate(self):
        self.regimen(day='2030-05-02');self.note()
        r, = self.rows()
        self.assertEqual((r['dose_status'],r['daily_mg'],r['note_review_required']),('at_target',50,0))
        self.assertEqual(json.loads(r['note_evidence']),[])

    def test_undated_or_invalid_note_cannot_be_ordered_away(self):
        for day in (None,'unknown','2030-05'):
            with self.subTest(day=day):
                self.db.execute('DELETE FROM note_therapy_change');self.db.execute('DELETE FROM record')
                self.regimen();self.note(day=day)
                r, = self.rows()
                self.assertEqual(r['note_review_required'],1)
                self.assertIsNone(r['latest_note_date'])

    def test_future_and_outside_window_notes_do_not_create_rows_or_change_regimens(self):
        self.regimen();self.note(day='2030-06-01')
        self.note(patient='B',day='2028-01-01',rid='old')
        r, = self.rows()
        self.assertEqual((r['patient_id'],r['daily_mg'],r['note_review_required']),('A',50,0))

    def test_patient_and_named_drug_boundaries(self):
        self.regimen();self.note(patient='B');self.note(target='lisinopril',rid='other')
        rows={(r['patient_id'],r['drug_key']):r for r in self.rows()}
        self.assertEqual(rows['A','carvedilol']['daily_mg'],50)
        self.assertEqual(rows['B','carvedilol']['dose_status'],'interpretation_required')
        self.assertEqual(rows['A','lisinopril']['dose_status'],'interpretation_required')

    def test_all_candidate_sources_are_retained_without_voting(self):
        self.regimen();self.note(rid='one');self.note(rid='two',action='start')
        r, = self.rows()
        self.assertEqual({n['record_id'] for n in json.loads(r['note_evidence'])},{'one','two'})
        self.assertEqual(r['dose_status'],'interpretation_required')

    def test_absent_relation_is_declared_not_inferred_as_no_changes(self):
        self.regimen();self.db.execute('DROP TABLE note_therapy_change')
        r, = self.rows(notes=False)
        self.assertEqual(r['note_scan_available'],0)
        self.assertEqual(r['documented_daily_mg'],50)


QUESTION = ('List patients and their latest documented carvedilol regimen; '
            'simulate evidence review for patients with unresolved note changes; monitor loop closure')


def analysis_spec():
    return dict(steps=[dict(name='regimens',purpose='Preserve source evidence and unresolved note changes',sql=
        "SELECT p.patient_id,d.daily_mg,d.dose_status,d.documented_daily_mg,d.note_evidence,d.source_record_ids,"
        "d.note_review_required AS needs_review FROM patient p LEFT JOIN {{current_dose:180}} d "
        "ON d.patient_id=p.patient_id AND d.drug_key='carvedilol' ORDER BY p.patient_id")],
        final='regimens',returns='table',patient_selection={'column':'patient_id','quote':'List patients'},
        requirements=[dict(quote='List patients and their latest documented carvedilol regimen',applied_in=['regimens'],
                           how='Structured documentation and unresolved note candidates, not current use'),
                      dict(quote='patients with unresolved note changes',applied_in=['regimens'],
                           how='Only explicit candidate changes select synthetic evidence review',outputs=['needs_review'])],
        unresolved=[],workflow=[dict(kind='simulation',title='Synthetic source reconciliation',program='custom',
            quote='simulate evidence review for patients with unresolved note changes',unresolved=[],
            selection={'output':'needs_review','quote':'patients with unresolved note changes'}),
            dict(kind='monitor',title='Synthetic review progress',mode='work',scope='result',outputs=['work_status'],
                 quote='monitor loop closure',unresolved=[])])


class RegimenWorkflow(unittest.TestCase):
    execute = existing_workflow.GeneralAnalysisWorkflow.execute

    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.path=fixture(Path(t.name)/'fixture')
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute(NOTE_SCHEMA)
            db.executemany('INSERT INTO note_therapy_change VALUES(?,?,?,?,?,?,?,?)',[
                ('B','note-b','2030-05-01','drug','carvedilol','stop','Stop carvedilol.','authored-candidate'),
                ('D','note-d','2030-05-01','drug','carvedilol','start','Start carvedilol.','authored-candidate')])
        self.catalog=R.Catalog.load(self.path)
        self.stack=SimpleNamespace(path=self.path,allow_live=False,shared_request_planning=True)

    def test_connected_answer_exact_review_subset_reopen_and_monitor(self):
        result,context=self.execute(analysis_spec(),QUESTION)
        rows={r['patient_id']:r for r in result['answer']['table']}
        self.assertEqual(set(rows),{'A','B','C','D'})
        self.assertEqual(rows['B']['documented_daily_mg'],12.5)
        self.assertIsNone(rows['B']['daily_mg'])
        self.assertEqual(rows['D']['dose_status'],'interpretation_required')
        self.assertTrue(any('named-drug note candidates require interpretation' in text for text in result['answer']['limitations']))
        flow=W.materialize(self.path,context['id'])
        self.assertEqual({i['patient_id'] for i in A.inspect(self.path,context['id'])['items']},{'B','D'})
        self.assertEqual(flow['steps'][1]['status'],'watching')
        self.assertEqual(W.materialize(self.path,context['id']),flow)
        self.assertEqual(A.load(self.path,context['id'],current=True)['answer']['table'],result['answer']['table'])

    def test_saved_population_and_empty_scope_remain_exact(self):
        for population,expected in [(['A','B'],{'B'}),([],set())]:
            with self.subTest(population=population):
                result,context=self.execute(analysis_spec(),QUESTION,population=population)
                self.assertEqual(result['answer']['patient_ids'],population)
                W.materialize(self.path,context['id'])
                self.assertEqual({i['patient_id'] for i in A.inspect(self.path,context['id'])['items']},expected)

    def test_changed_note_evidence_invalidates_saved_answer(self):
        _,context=self.execute(analysis_spec(),QUESTION)
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute("UPDATE note_therapy_change SET evidence_text='Do not stop carvedilol.' WHERE record_id='note-b'")
        with self.assertRaises(ValueError):A.load(self.path,context['id'],current=True)

    def test_unqualified_interpretation_cannot_launch_review(self):
        _,context=self.execute(analysis_spec(),QUESTION,accepted=False)
        flow=W.materialize(self.path,context['id'])
        self.assertTrue(all(s['status']=='blocked' for s in flow['steps']))
        self.assertEqual(A.inspect(self.path,context['id'])['items'],[])


if __name__=='__main__':unittest.main()
