"""Authored normalization, correction and bound semantics through native ingestion/measurement paths."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import client_data as C, semantic_layer as S, normalization_check as N, measures as M, library_measure as L
from clinical_core.agere.canonical import versions as V

ANCHORS={'period_start':'2030-01-01','period_end':'2030-12-31'}


def criterion(op='<',threshold=10,**kw):
    return {'type':'measurement','label':'Authored threshold','concept':'fixture_analyte',
            'window':{'start':'period_start','end':'period_end'},'value':{'op':op,'threshold':threshold},**kw}


def lab_db():
    db=sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE measurement(record_id,patient_id,concept,name,event_date,available_date,value REAL,
      value_canonical REAL,unit,unit_canonical,loinc,loinc_source);
      CREATE TABLE measurement_latest(patient_id,concept,event_date,value_canonical,unit_canonical,same_day_values,same_day_min,record_id);
      CREATE TABLE record(record_id,patient_id,payload,event_date,available_date,stream);''')
    return db


def add_lab(db,rid,pid,value,day='2030-01-10',**kw):
    r={'name':'AUTHORED TEST','unit':'authored_unit','loinc':'AUTHORED-CODE','result_status':'final','value':value,**kw}
    point=value if type(value) in (int,float) else None
    db.execute('INSERT INTO measurement VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               (rid,pid,'fixture_analyte',r['name'],day,day,point,point,r['unit'],'authored_unit',r['loinc'],'source'))
    db.execute('INSERT INTO record VALUES(?,?,?,?,?,?)',(rid,pid,json.dumps(r),day,day,'labs'))


def ingest(db):
    with patch.dict(C.concepts.MEASUREMENTS,{'fixture_analyte':{'units':{'authored_unit':1,'double_unit':2},'unit':'authored_unit'}}):
        return C.labs(db)


class RejectedIdentity(unittest.TestCase):
    def test_rejected_dictionary_identity_is_metadata_while_source_and_rules_remain_usable(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.executescript('''CREATE TABLE measurement(record_id,patient_id,name,loinc,loinc_source,loinc_dictionary,concept,event_date,value,value_canonical,result_current,excluded_reason);
          CREATE TABLE condition(label);
          CREATE TABLE medication(record_id,patient_id,source_stream,evidence,drug_name,drug_class,status,event_date,available_date,rxnorm_ingredient,rxnorm,rxnorm_source);
          CREATE TABLE visit(type,in_person,type_concept);''')
        entry={'loinc':'REJECTED-CODE','agere_concept':'fixture_analyte','confidence':.99,'jev_choice':'none','jev_p':.99}
        dictionaries={'labs':{'UNRESOLVED':entry},'conditions':{},'medications':{},'encounters':{}}
        for rid,pid,code,source,concept in [('u','unresolved',None,None,None),('s','source','SOURCE-CODE','source',None),('r','rule',None,None,'fixture_analyte')]:
            db.execute('INSERT INTO measurement VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(rid,pid,'UNRESOLVED',code,source,None,concept,'2030-01-10',6,6,1,None))
        with patch.object(S,'_dictionary',side_effect=lambda domain:dictionaries[domain]),patch.object(N,'entries',side_effect=lambda domain:dictionaries[domain]),patch.object(S,'_medication_codes'):
            self.assertEqual(N.withheld('labs'),{'UNRESOLVED':'rejected'});S._normalize(db)
        row=db.execute("SELECT loinc,loinc_dictionary,concept FROM measurement WHERE patient_id='unresolved'").fetchone()
        self.assertEqual(row,(None,'REJECTED-CODE',None))
        held=criterion(value=None,concept=None,loinc=['REJECTED-CODE'])
        self.assertFalse(M._evaluate(M.Context(db),held,'unresolved',ANCHORS)[0])
        self.assertTrue(M._evaluate(M.Context(db),criterion(value=None,concept=None,loinc=['SOURCE-CODE']),'source',ANCHORS)[0])
        self.assertTrue(M._evaluate(M.Context(db),criterion(value=None),'rule',ANCHORS)[0])
        self.assertEqual(db.execute("SELECT status,withheld FROM normalization_check WHERE domain='labs'").fetchone(),('rejected',1))


class CorrectionIdentity(unittest.TestCase):
    def test_cross_patient_pointer_cannot_erase_a_and_valid_same_patient_pointer_still_works(self):
        for correction_pid in ('A','B'):
            with self.subTest(correction_pid=correction_pid):
                db=lab_db();self.addCleanup(db.close)
                add_lab(db,'old','A',7);add_lab(db,'new',correction_pid,6,day='2030-01-12',result_status='corrected',replaces_result_record_id='old')
                stats=ingest(db)
                current=dict(db.execute('SELECT record_id,result_current FROM measurement'))
                if correction_pid=='B':
                    self.assertEqual(current,{'old':1,'new':1})
                    self.assertIn(('A','old'),db.execute('SELECT patient_id,record_id FROM measurement_latest').fetchall())
                    self.assertTrue(M._evaluate(M.Context(db),criterion(threshold=8),'A',ANCHORS)[0])
                    self.assertEqual(stats['unresolved_correction_reference'],1)
                    self.assertIn('patient',db.execute("SELECT correction_link_issue FROM measurement WHERE record_id='new'").fetchone()[0])
                else:self.assertEqual(current,{'old':0,'new':1})

    def test_incompatible_analyte_order_and_missing_target_are_retained_for_review(self):
        for change in ({'loinc':'OTHER-CODE'},{'laboratory_order_record_id':'OTHER-ORDER'},{'replaces_result_record_id':'absent'}):
            with self.subTest(change=change):
                db=lab_db();self.addCleanup(db.close)
                add_lab(db,'old','A',7,laboratory_order_record_id='ORDER')
                add_lab(db,'new','A',6,result_status='corrected',replaces_result_record_id='old',**({k:v for k,v in change.items() if k!='replaces_result_record_id'}))
                if 'replaces_result_record_id' in change:
                    r=json.loads(db.execute("SELECT payload FROM record WHERE record_id='new'").fetchone()[0]);r.update(change)
                    db.execute("UPDATE record SET payload=? WHERE record_id='new'",(json.dumps(r),))
                ingest(db)
                self.assertEqual(db.execute("SELECT result_current FROM measurement WHERE record_id='old'").fetchone()[0],1)
                columns={r[1] for r in db.execute('PRAGMA table_info(measurement)')}
                issue=db.execute("SELECT correction_link_issue FROM measurement WHERE record_id='new'").fetchone()[0] if 'correction_link_issue' in columns else None
                self.assertIsNotNone(issue)

    def test_pure_resolver_and_canonical_projection_validate_the_same_reference(self):
        from test_canonical import make_raw
        from clinical_core.agere.clinical import adjudication_history as H
        for pid in ('B','A'):
            obs={'name':'AUTHORED TEST','loinc':'AUTHORED-CODE'}
            versions=[V.Version('old',person_id='A'),V.Version('new',('old',),person_id=pid)]
            self.assertEqual(V.current_ids(versions),{'new'} if pid=='A' else {'old','new'})
            with tempfile.TemporaryDirectory() as d:
                raw,out=Path(d)/'raw.sqlite3',Path(d)/'versions.sqlite3'
                make_raw(raw,[('old','labs','A','2030-01-01',None,obs),('new','labs',pid,'2030-01-02',None,{**obs,'replaces_result_record_id':'old'})])
                V.build(raw,out)
                with sqlite3.connect(out) as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM superseded_record').fetchone()[0],int(pid=='A'))
                    self.assertEqual(db.execute('SELECT count(*) FROM unresolved_record_reference').fetchone()[0],int(pid=='B'))
                with sqlite3.connect(':memory:') as history:
                    history.execute('CREATE TABLE patient(patient_id)');history.executemany('INSERT INTO patient VALUES(?)',[('A',),('B',)])
                    H.build_corrections(history,raw)
                    self.assertEqual(history.execute('SELECT count(*) FROM record_correction').fetchone()[0],int(pid=='A'))
                    self.assertEqual(history.execute('SELECT count(*) FROM record_correction_unresolved').fetchone()[0],int(pid=='B'))


class BoundedMeasurements(unittest.TestCase):
    def test_point_model_reader_excludes_bounds_without_substituting_them(self):
        from clinical_core.agere.clinical import target_trial as T
        db=lab_db();self.addCleanup(db.close);add_lab(db,'bound','A','<10');add_lab(db,'point','B',3);ingest(db)
        results=T._labs(db,['fixture_analyte'],None)
        self.assertNotIn('A',results['fixture_analyte']);self.assertEqual(results['fixture_analyte']['B'][0][1],3)

    def test_interval_truth_and_exact_controls(self):
        cases=[('<10','<',10,True),('<10','>=',10,False),('<10','<',5,None),('<=10','<',10,None),('<=10','<=',10,True),
               ('>10','>',10,True),('>10','<=',10,False),('>=10','>',10,None),('>=10','>=',10,True),(9,'<',10,True),(10,'<',10,False)]
        for value,op,want,expected in cases:
            with self.subTest(value=value,op=op,want=want):
                db=lab_db();self.addCleanup(db.close);add_lab(db,'result','A',value);ingest(db)
                met,text=M._evaluate(M.Context(db),criterion(op,want),'A',ANCHORS)
                self.assertIs(met,expected)
                if isinstance(value,str):self.assertIn(value,text)

    def test_converted_bound_keeps_received_text_and_canonical_unit(self):
        db=lab_db();self.addCleanup(db.close);add_lab(db,'result','A','<5',unit='double_unit');ingest(db)
        met,text=M._evaluate(M.Context(db),criterion('<',10),'A',ANCHORS)
        self.assertIs(met,True);self.assertIn('<10 authored_unit',text);self.assertIn('<5',text)

    def test_unknown_is_retained_in_patient_and_saved_answer_without_becoming_a_gap_or_rate(self):
        from clinical_core.agere.deliverables import answer_context as A
        db=lab_db();self.addCleanup(db.close);add_lab(db,'bound','A','<10');add_lab(db,'point','B',3);ingest(db)
        c=criterion('<',5)
        measure={'id':'authored','name':'Authored threshold','status':'internal','spec_year':'authored fixture','citations':[],
                 'direction':'higher_is_better','unit':'patient','denominator':{'criteria':[],'enrollment':{'allowed_gap_months':0}},
                 'numerator':{'criteria':[c]}}
        people={pid:{'months':[f'2030-{m:02d}' for m in range(1,13)]} for pid in ('A','B')}
        report=M.evaluate_db(db,measure,'2030-01-01','2030-12-31','2030-12-31',people=people,attr={},output='patients')
        self.assertTrue(report.get('partial'));self.assertIsNone(report['overall']['rate']);self.assertIsNone(report['overall']['numerator'])
        self.assertEqual(report['known_evaluation_overall']['numerator'],1)
        self.assertEqual({r['patient_id']:r['met'] for r in report['patient_rows']},{'A':None,'B':True})
        self.assertEqual(report['care_gaps'],[])
        report.update(measure_name=measure['name'],measure_status='internal',measure_id='authored',parameters={'output':'patients','group_by':'none','follow_up_days':None})
        answer=L.answer(report,{'coverage':'supported_observation'},'List statuses')
        self.assertEqual(answer['status'],'partial');self.assertEqual(answer['patient_ids'],['A','B'])
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)
            with sqlite3.connect(path/'semantic.sqlite3') as source:
                source.executescript("CREATE TABLE meta(key,value);INSERT INTO meta VALUES('version','authored'),('as_of','2030-12-31');CREATE TABLE patient(patient_id);INSERT INTO patient VALUES('A'),('B');")
            saved=A.capture(path,'List statuses',{'answer':answer})
            self.assertEqual(A.load(path,saved['id'],current=True)['answer']['table'],answer['table'])


class CompanionEvidence(unittest.TestCase):
    def test_other_patients_companion_cannot_change_a_and_actual_ratio_evidence_can_resolve_it(self):
        db=lab_db();self.addCleanup(db.close)
        db.execute('CREATE TABLE claim_line(patient_id,service_date,cpt)')
        db.execute("INSERT INTO claim_line VALUES('A','2030-01-10','82043')")
        c=criterion(value=None,concept='fixture_analyte',claim_codes={'cpt':['82043'],'with':{'cpt':['82570'],'days':4}})
        before=M._evaluate(M.Context(db),c,'A',ANCHORS)
        db.execute("INSERT INTO claim_line VALUES('B','2030-01-10','82570')")
        after=M._evaluate(M.Context(db),c,'A',ANCHORS)
        self.assertIsNone(before[0]);self.assertEqual(before,after)
        db.execute("INSERT INTO claim_line VALUES('A','2030-01-12','82570')")
        self.assertTrue(M._evaluate(M.Context(db),c,'A',ANCHORS)[0])


