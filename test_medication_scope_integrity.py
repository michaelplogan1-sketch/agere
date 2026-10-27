"""Authored arithmetic/scope regressions through existing engines; no clinical qualification."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clinical_core.agere.clinical import dose as D, fill_events as F, ddi_screening as I
from clinical_core.agere.clinical import request_program as P, shared_planner as S, request_plan as R
from clinical_core.agere.deliverables import answer_context as A
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
from clinical_core.agere.plans import router
from test_fill_events import _db
from test_request_program import analysis, audit
import test_shared_planner as shared_fixture
from test_plans import build_fixture


class DoseArithmetic(unittest.TestCase):
    def row(self, instruction, *, stream='medication_lists', **extra):
        db = D.register(sqlite3.connect(':memory:')); db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.executescript('CREATE TABLE record(record_id,patient_id,stream,payload);'
                         'CREATE TABLE note_therapy_change(patient_id,record_id,event_date,target_kind,target,action,evidence_text,extractor);')
        payload = dict(event_date='2030-01-10', name='Authoredagent 5 mg tablet',
                       list_status='active', dose=instruction, frequency='BID', **extra)
        db.execute('INSERT INTO record VALUES(?,?,?,?)', ('received-instruction', 'A', stream, json.dumps(payload)))
        return dict(db.execute('SELECT * FROM '+D.current_dose_sql('2030-01-31',365,note_changes=True)).fetchone())

    def test_explicit_tablet_counts_change_the_daily_quantity_in_both_directions(self):
        for count, expected in [('2',20),('0.5',5),('1/2',5),('two',20)]:
            with self.subTest(count=count):
                r = self.row(f'5 mg/tablet; take {count} tablets twice daily')
                self.assertEqual((r['daily_mg'],r['documented_daily_mg']), (expected,expected))
                self.assertEqual(r['current_use_established'],0)

    def test_exact_mg_and_structured_quantity_controls(self):
        self.assertEqual(self.row('10 mg twice daily')['daily_mg'],20)
        p = dict(strength=5,strength_unit='mg/tablet',dose=2,dose_unit='tablet',times_per_day=2)
        self.assertEqual(D.statement_mg(json.dumps(p),'medication_orders')*2,20)
        p.pop('dose'); self.assertIsNone(D.statement_mg(json.dumps(p),'medication_orders'))
        self.assertEqual(D.dose_mg('Authoredagent 5 mg tablet'),5)  # Strength helper retains its meaning.

    def test_product_strength_without_count_and_ambiguous_instructions_stay_unknown(self):
        for instruction in ('5 mg/tablet twice daily','5 mg tablet twice daily',
                            'take 1 or 2 tablets twice daily','take -0.5 tablet twice daily',
                            'take 0 tablets twice daily','5 mg/tablet; take 2 tablets as needed',
                            'take 1 tablet in the morning and 2 tablets at night'):
            with self.subTest(instruction=instruction):
                self.assertIsNone(self.row(instruction)['daily_mg'])

    def test_named_product_strength_and_calculation_survive_current_dose_projection(self):
        r = self.row('take 2 tablets twice daily')
        self.assertEqual(r['daily_mg'],20)
        evidence, = json.loads(r.get('dose_evidence') or '[]')
        self.assertEqual(evidence['record_id'],'received-instruction')
        self.assertEqual(evidence['daily_mg'],20)
        calc = evidence['calculation']
        self.assertEqual((calc['unit_count'],calc['strength_mg_per_unit'],calc['per_admin_mg']),(2,5,10))
        self.assertEqual(calc['received']['dose'],'take 2 tablets twice daily')


class FillContinuity(unittest.TestCase):
    def db(self, duration):
        db = _db([('first','A','payer_pharmacy_claims','2030-01-01','opioid','oxycodone',duration),
                  ('second','A','payer_pharmacy_claims','2030-01-10','opioid','oxycodone',30)])
        self.addCleanup(db.close); F.build(db); return db

    def test_unknown_supply_never_establishes_a_gap_but_dates_and_counts_remain(self):
        for duration in (None,0,-1,2.5,'NaN','Infinity'):
            with self.subTest(duration=duration):
                db = self.db(duration)
                rows = db.execute('SELECT fill_date,supply_end,gap_after_prev_supply FROM fill ORDER BY fill_date').fetchall()
                self.assertEqual(rows[0][:2],('2030-01-01',None))
                self.assertIsNone(rows[1][2])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM fill WHERE gap_after_prev_supply>=7').fetchone()[0],0)
                self.assertEqual(len(rows),2)

    def test_reported_one_and_thirty_day_controls_and_conflicting_merge(self):
        for duration,end,gap in [(1,'2030-01-01',8),(30,'2030-01-30',-21)]:
            db = self.db(duration)
            self.assertEqual(db.execute('SELECT supply_end FROM fill ORDER BY fill_date').fetchone()[0],end)
            self.assertEqual(db.execute('SELECT gap_after_prev_supply FROM fill ORDER BY fill_date DESC').fetchone()[0],gap)
        db = _db([('payer','B','payer_pharmacy_claims','2030-01-01','opioid','oxycodone',30),
                  ('dispense','B','pharmacy_dispenses','2030-01-01','opioid','oxycodone',7)])
        self.addCleanup(db.close); F.build(db)
        self.assertEqual(db.execute('SELECT days_supply,supply_end FROM fill').fetchone(),(None,None))

    def test_interaction_reader_exposes_unknown_concurrency_without_losing_known_pairs(self):
        db = self.db(None)
        db.execute("UPDATE fill SET drug_name='diazepam',drug_class='benzodiazepine',class_key='benzodiazepine' WHERE record_ids='second'")
        p = I.validate({'months':12})
        r = I.run(db,['A'],p,'2030-01-31')
        self.assertEqual(r.get('status'),'partial')
        self.assertEqual(len(r.get('unassessed_fills',[])),1)
        self.assertEqual(r['total_pairs'],0)
        a = I.answer(r,{'coverage':'supported_observation'},'Review interacting fills')
        self.assertEqual(a['status'],'partial'); self.assertTrue(a['missing'])
        db.execute("UPDATE fill SET supply_end='2030-01-30' WHERE record_ids='first'")
        known = I.run(db,['A'],p,'2030-01-31')
        self.assertEqual(known['total_pairs'],1)
        self.assertFalse(known.get('unassessed_fills'))

    def test_unknown_supply_is_not_classified_as_either_present_or_missing_therapy(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t)/'semantic.sqlite3'; build_fixture(path)
            with sqlite3.connect(path) as db:
                db.execute("UPDATE fill SET supply_end=NULL,days_supply=NULL WHERE patient_id='HF1' AND drug_class='arb'")
                # The existing fixture uses ACE inhibition for this patient's RASI pillar.
                db.execute("UPDATE fill SET supply_end=NULL,days_supply=NULL WHERE patient_id='HF1' AND drug_class='ace_inhibitor'")
            r = router.route(path,['HF1'],'pharmacy_gdmt','2030-05-27')['patients'][0]['gap']
            self.assertIsNone(r['pillars']['rasi']['on_therapy']['value'])
            self.assertNotIn('rasi',r['missing_pillars']['value'])
            self.assertIn('rasi',r.get('unknown_pillars',{}).get('value',[]))


class CompositeScope(unittest.TestCase):
    setUp = shared_fixture.SharedPlanner.setUp

    def execute_sets(self, first, second, *, summary=False):
        clauses = ['List the first selected population','Show the second output']
        question = '; '.join(clauses)
        nodes = []
        for i, ids in enumerate((first,second)):
            values = ','.join("'"+v+"'" for v in ids) or "'not-a-patient'"
            spec = analysis(clauses[i],f'SELECT patient_id FROM patient WHERE patient_id IN ({values})','patient_set')
            if summary and i==1:
                spec = analysis(clauses[i],'SELECT COUNT(*) AS total FROM patient','count')
            nodes.append({'id': ('first','second')[i], 'quote':clauses[i], 'inputs':[], 'analysis':spec})
        spec = {'nodes':nodes,'unresolved':[]}
        contract = P.prepare(self.catalog,question,spec)
        receipt = {'version':S.VERSION,'policy_identity':S.policy_identity(),'request_contract':contract,
                   'interpretation':{'execution_accepted':True,'interpreter':'authored_fixture','semantic_review':audit(spec)},
                   'feedback_identity':R.fingerprint([]),'new_paid_api_calls':0,'known_api_estimate_usd':0,'api_cost_known':True}
        result = P.execute(self.stack,question,P.graph_for(contract),receipt)
        self.assertEqual(result['answer']['status'],'answered')
        saved = A.capture(self.path,question,result)
        context = A.load(self.path,saved['id'],current=True)
        self.assertFalse(A.supports_patient_work(context))
        return context

    def test_empty_and_nonempty_outputs_cannot_become_a_shared_saved_cohort(self):
        c = self.execute_sets([],['B'])
        self.assertEqual(c['patient_ids'],[])
        self.assertFalse(A.scope_options(c)['result_scope_available'])
        with self.assertRaises(QuestionNotUnderstood): A.reference_scope(c)

    def test_matching_empty_population_remains_an_explicit_empty_followup(self):
        c = self.execute_sets([],[])
        self.assertTrue(A.scope_options(c)['result_scope_available'])
        self.assertEqual(A.reference_scope(c)['patient_ids'],[])

    def test_matching_nonempty_different_nonempty_and_aggregate_controls(self):
        same = self.execute_sets(['A'],['A'])
        self.assertEqual(A.reference_scope(same)['patient_ids'],['A'])
        different = self.execute_sets(['A'],['B'])
        self.assertFalse(A.scope_options(different)['result_scope_available'])
        mixed = self.execute_sets(['A'],[],summary=True)
        self.assertEqual(A.reference_scope(mixed)['patient_ids'],['A'])


if __name__ == '__main__': unittest.main()
