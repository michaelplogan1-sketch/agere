"""Mike 2026-10-25: deterministic definitions, on authored synthetic rows only. No providers or servers."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import analyst_insights as AI
from clinical_core.agere.clinical import aggregate, cohorts, monitor, survival, trial_feasibility
from clinical_core.agere.clinical import cohort_families as CF
from clinical_core.agere.clinical import decision_context as DC
from clinical_core.agere.clinical import insight_families as IF
from clinical_core.agere.clinical import local_compiler as LC
from clinical_core.agere.clinical import demand, pathway, population_sql, request_language as L, request_plan as R, sql_query, vbc, vbc_families as VF


AS_OF = '2030-01-01'


class Definitions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.target = self.path / 'semantic.sqlite3'
        with sqlite3.connect(self.target) as db:
            db.executescript("""
                CREATE TABLE patient(patient_id TEXT, birth_date TEXT, data_segment TEXT, line_of_business TEXT);
                INSERT INTO patient VALUES ('A','1980-01-01','primary','commercial'), ('D','1970-01-01','primary','medicare');
                CREATE TABLE death(patient_id TEXT, death_date TEXT);
                INSERT INTO death VALUES ('D','2030-01-01');
                CREATE TABLE meta(key TEXT, value TEXT);
                INSERT INTO meta VALUES ('as_of','2030-01-01');
                CREATE TABLE stay(patient_id TEXT, type TEXT, admit_date TEXT, discharge_date TEXT, discharge_known INTEGER);
                INSERT INTO stay VALUES ('A','inpatient','2029-02-01','2029-02-02',1),
                    ('D','inpatient','2029-03-01','2029-03-02',1), ('D','observation','2029-04-01','2029-04-02',1);
                CREATE TABLE utilization_event(patient_id TEXT, kind TEXT, event_date TEXT, organization_id TEXT, readmitted_30d INTEGER);
                INSERT INTO utilization_event SELECT patient_id, type, admit_date, 'H', 0 FROM stay;
                INSERT INTO utilization_event VALUES ('A','ed_visit','2029-08-01','H',0),
                    ('D','ed_visit','2029-09-01','H',0), ('D','ed_visit','2029-10-01','H',0), ('D','ed_visit','2029-11-01','H',0);
                CREATE TABLE appointment(patient_id TEXT, scheduled_date TEXT, final_status TEXT);
                INSERT INTO appointment VALUES ('A','2029-02-01','fulfilled'),('D','2029-02-01','no_show');
                CREATE TABLE fill(patient_id TEXT, fill_date TEXT, drug_class TEXT, drug_name TEXT);
                INSERT INTO fill VALUES ('A','2029-02-01','statin','atorvastatin'),('D','2029-03-01','statin','atorvastatin');
                CREATE TABLE payer_pharmacy_claim(patient_id TEXT, fill_date TEXT, plan_paid_cents INTEGER, counts_as_fill INTEGER, drug_name TEXT,
                    patient_pay_cents INTEGER, available_date TEXT, claim_status TEXT);
                INSERT INTO payer_pharmacy_claim VALUES ('A','2029-01-01',999999,1,'atorvastatin',0,'2029-01-01','paid');
                CREATE TABLE pharmacy_claim_event(patient_id TEXT, event_date TEXT, payer_paid_delta_cents INTEGER, drug_name TEXT, status TEXT, source TEXT);
                INSERT INTO pharmacy_claim_event VALUES ('A','2028-12-31',800,'atorvastatin','paid','pharmacy_claims'),
                    ('A','2029-02-01',10000,'atorvastatin','paid','pharmacy_claims'),
                    ('A','2029-03-01',-10000,'atorvastatin','reversed','pharmacy_claims'),
                    ('D','2029-04-01',4000,'atorvastatin','paid','payer_pharmacy_claims'),
                    ('D','2029-05-01',-500,'atorvastatin','recouped','payer_pharmacy_claims'),
                    ('A','2030-01-01',-800,'atorvastatin','reversed','pharmacy_claims');
                CREATE TABLE payer_claim(patient_id TEXT, service_from TEXT, paid_cents INTEGER, counts_in_metrics INTEGER,
                    final_status TEXT, claim_type TEXT, place_of_service TEXT, allowed_cents INTEGER, charge_cents INTEGER,
                    last_available_date TEXT, patient_responsibility_cents INTEGER, drg TEXT);
                INSERT INTO payer_claim VALUES ('A','2029-02-01',10000,1,'paid','professional','11',12000,20000,'2030-01-01',2000,NULL),
                    ('D','2029-03-01',4000,1,'paid','professional','11',5000,6000,'2030-01-01',1000,NULL),
                    ('D','2029-04-01',0,1,'denied','professional','11',0,8000,'2030-01-01',0,NULL),
                    ('A','2029-05-01',900000,0,'paid','professional','11',900000,900000,'2030-01-01',0,NULL),
                    ('A','2028-12-01',300000,1,'paid','professional','11',300000,300000,'2029-02-01',0,NULL);
                CREATE TABLE record(patient_id TEXT, payload TEXT, available_date TEXT, stream TEXT);
            """)
        self.catalog = R.Catalog.load(self.path)

    def ctx(self, question):
        return population_sql.Ctx(self.path, self.target, AS_OF, question)

    def contract(self, question):
        return R.compile_request(self.catalog, question, L.parse(self.catalog, question))

    def value(self, question):
        c = self.contract(question)
        # Exercise SQL enforcement too; it must not reintroduce a living filter on event counts.
        with DC.scope(question, AS_OF):
            r = sql_query.execute(self.path, {'query': c['query'], 'returns': c['returns']}, target=self.target)
        return r.get('value', r.get('rows'))

    def test_admissions_include_observation_and_deceased_in_compiled_execution(self):
        for noun in ('hospital admissions', 'admissions', 'hospital stays', 'hospitalizations'):
            q = f'How many {noun} were there in 2029?'
            with self.subTest(noun=noun):
                c = self.contract(q)
                self.assertNotIn('FROM death', c['query'])
                self.assertEqual(self.value(q), 3)

    def test_explicit_inpatient_still_excludes_observation(self):
        self.assertEqual(self.value('How many inpatient admissions were there in 2029?'), 2)

    def test_patients_with_admissions_still_living(self):
        q = 'How many patients had an admission in 2029?'
        self.assertIn('FROM death', self.contract(q)['query'])
        self.assertEqual(self.value(q), 1)

    def test_living_patient_with_only_observation_had_an_admission(self):
        with sqlite3.connect(self.target) as db:
            db.execute("INSERT INTO patient VALUES ('B','1980-01-01','primary','commercial')")
            db.execute("INSERT INTO stay VALUES ('B','observation','2029-06-01','2029-06-02',1)")
        self.assertEqual(self.value('How many patients had an admission in 2029?'), 2)

    def test_no_shows_include_deceased(self):
        self.assertEqual(self.value('How many no-shows were there in 2029?'), 1)

    def test_event_rate_keeps_every_patient_through_sql_enforcement(self):
        self.assertEqual(self.value('ED visits per 1,000 patients in 2029'), 2000)

    def test_population_helper_respects_scope(self):
        self.assertEqual(self.ctx('How many hospital admissions were there in 2029?').living, '1')
        self.assertIn('FROM death', self.ctx('How many patients had an admission in 2029?').living)
        self.assertIn('FROM death', self.ctx('How many living patients had an admission?').event_population)

    def test_generic_and_inpatient_compiler_vocabulary_are_distinct(self):
        self.assertIn('admissions', LC.ENCOUNTER_ALIASES['hospital_stay'])
        self.assertNotIn('admissions', LC.ENCOUNTER_ALIASES['inpatient_admission'])
        self.assertIn('hospital admission', dict(LC.PATHWAY_STATES)['hospital_stay'])
        self.assertIn('inpatient admission', dict(LC.PATHWAY_STATES)['inpatient'])

    def test_aggregate_hospital_stays_include_claims_only_and_do_not_duplicate_encounters(self):
        from test_aggregate_explain import build, visit
        patients = {p: {'birth_date': '1980-01-01'} for p in ('A', 'C')}
        records = [visit('A', 'inpatient', '2028-01-01', 'H', '2028-01-03'),
                   visit('A', 'observation', '2028-02-01', 'H', '2028-02-02'),
                   ('C', 'payer_medical_claims', {'payer_claim_id': 'claim-only', 'claim_status': 'paid',
                    'claim_type': 'institutional', 'type_of_bill': '0111', 'service_from': '2028-03-01',
                    'service_to': '2028-03-03', 'admission_date': '2028-03-01', 'discharge_date': '2028-03-03',
                    'paid_amount': 100, 'event_date': '2028-03-01'})]
        path = build(records, patients)
        r = aggregate.execute(path, sorted(patients), {'measure': 'events', 'concept': 'hospital_stay', 'statistic': 'count'})
        self.assertEqual(r['rows'][0]['numerator'], 3)
        selected = cohorts.select(path, {'all': [{'kind': 'concept', 'concept': 'hospital_stay',
                                   'window': {'basis': 'all_observed', 'start': '', 'end': ''}}], 'any': [], 'exclude': []})
        self.assertEqual(selected['patient_ids'], ['A', 'C'])
        with sqlite3.connect(Path(path) / 'semantic.sqlite3') as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM encounter WHERE type_concept = 'hospital_stay'").fetchone()[0], 0)

    def test_demand_counts_all_source_hospital_admissions(self):
        with sqlite3.connect(self.target) as db:
            events, _ = demand.events(db, 'hospital_admissions', None, AS_OF)
            scoped, _ = demand.events(db, 'hospital_admissions', ['A'], AS_OF)
        self.assertEqual(len(events), 3)
        self.assertEqual(len(scoped), 1)

    def test_survival_and_trial_encounter_selectors_include_observation(self):
        with sqlite3.connect(self.target) as db:
            db.execute("INSERT INTO utilization_event VALUES ('B','observation','2029-02-02','H',0)")
            p = survival.validate({'index': {'type': 'date', 'date': '2029-01-01'},
                                   'event': {'type': 'encounter', 'concept': 'hospital_stay'}, 'horizon_days': 365})
            rows, _ = survival.units(db, ['B'], p, AS_OF)
            self.assertEqual(rows[0][2:4], (32, 1))
            criterion = {'type': 'encounter', 'concept': 'hospital_stay', 'min_count': 1, 'within_days': 365}
            selected = trial_feasibility.evaluate(db, criterion, {'B': {}}, trial_feasibility._day(AS_OF), {})
            self.assertTrue(selected['B'][0])

    def test_admission_monitor_includes_observation_in_its_coding_channel(self):
        with sqlite3.connect(self.target) as db:
            db.executescript("CREATE TABLE visit(patient_id TEXT, event_date TEXT, type_concept TEXT); "
                             "INSERT INTO visit VALUES ('A','2029-02-01','observation_stay'),('D','2029-03-01','inpatient_admission');")
            r = monitor.event_type_gaps(db, ['2029-01', '2029-02', '2029-03'], 'admit_rate')
        self.assertEqual(r['events'], [0, 1, 1])

    def test_admission_trend_includes_everyone_and_observation(self):
        ctx = self.ctx('Did hospital admissions go up or down from 2028 to 2029?')
        r = IF.YoyTrend.run(ctx, {'metric': 'admissions', 'y0': 2028, 'y1': 2029, 'movers': False})
        self.assertEqual(r['facts'][1]['value'], 3)
        self.assertFalse(any('FROM death' in q for q in ctx.queries))

    def test_event_concentration_includes_deceased(self):
        r = AI.UseConcentration.run(self.ctx('Is our ED volume driven by a small group of patients?'), {'kind': 'ed_visit', 'label': 'ED visits'})
        self.assertEqual(r['facts'][0]['visits'], 4)

    def test_frequent_users_share_includes_deceased_but_patient_list_does_not(self):
        ctx = self.ctx('Who are our frequent ED users and how much ED volume do they drive?')
        # Avoid unrelated condition evidence requirements in this small fixture.
        ctx.cond = lambda name: '0'
        r = IF.FrequentUsers.run(ctx, {'minimum': 3})
        self.assertEqual(r['patient_ids'], [])
        visits = next(f for f in r['facts'] if f['id'] == 'visits')
        self.assertEqual((visits['numerator'], visits['denominator']), (3, 4))

    def test_cohort_hospital_sql_reads_both_kinds(self):
        self.assertIn("kind IN ('inpatient', 'observation')", CF.admissions_sql())
        sql = CF.render_utilization_threshold({'event': 'hospital_stay', 'min_count': 1, 'window': {'year': 2029}}, AS_OF)
        with sqlite3.connect(self.target) as db:
            self.assertEqual({r[0] for r in db.execute(sql)}, {'A', 'D'})

    def test_pharmacy_spend_uses_all_transactions_and_event_clock(self):
        q = 'What was pharmacy spend in 2029?'
        c = self.contract(q)
        self.assertIn('pharmacy_claim_event', c['query'])
        self.assertIn('payer_paid_delta_cents', c['query'])
        self.assertNotIn('counts_as_fill', c['query'])
        self.assertEqual(self.value(q), 3500)

    def test_pharmacy_trend_nets_reversals_and_drug_movers(self):
        r = IF.YoyTrend.run(self.ctx('Did pharmacy spend go up or down from 2028 to 2029?'),
                            {'metric': 'pharmacy', 'y0': 2028, 'y1': 2029, 'movers': True})
        self.assertEqual([f['value'] for f in r['facts'][:2]], [8, 35])
        self.assertEqual(r['tables']['drug_movers'][0]['spend_2029'], 35)

    def test_vbc_pharmacy_and_medical_finance(self):
        with sqlite3.connect(self.target) as db:
            rx = db.execute(VF.render_claims_finance({'measure': 'pharmacy_paid', 'year': 2029}, AS_OF)).fetchone()[0]
            med = db.execute(VF.render_claims_finance({'measure': 'paid', 'year': 2029}, AS_OF)).fetchone()[0]
        self.assertEqual((rx, med), (35, 140))

    def test_vbc_pmpm_input_nets_all_pharmacy_transactions(self):
        with sqlite3.connect(self.target) as db:
            rows = vbc._claims(db, '2029-01-01', '2029-12-31')
        self.assertEqual(sum(r[3] for r in rows if r[4] == 'pharmacy'), 3500)
        self.assertEqual(sum(r[3] for r in rows if r[4] == 'medical'), 14000)

    def test_missing_pharmacy_transaction_relation_cannot_fall_back_to_counted_fills(self):
        with sqlite3.connect(self.target) as db:
            db.execute('DROP TABLE pharmacy_claim_event')
            with self.assertRaisesRegex(ValueError, 'transaction deltas'):
                vbc._claims(db, '2029-01-01', '2029-12-31')

    def test_medical_spend_and_average_use_counted_service_date_claims(self):
        self.assertEqual(self.value('What was medical spend in 2029?'), 14000)
        q = 'What was the average paid amount per medical claim in 2029?'
        self.assertNotIn("final_status = 'paid'", self.contract(q)['query'])
        self.assertAlmostEqual(self.value(q), 14000 / 3)

    def test_governed_generic_admission_metric_preserves_inpatient_measure(self):
        d = json.loads((Path(__file__).resolve().parents[2] / 'config/agere/metrics/utilization.json').read_text())
        metrics = {m['id']: m for m in d['metrics']}
        generic = metrics['hospital_admissions_per_1000']['sql_template']
        inpatient = metrics['inpatient_admissions_per_1000']['sql_template']
        self.assertIn("type IN ('inpatient', 'observation')", generic)
        self.assertIn("type = 'inpatient'", inpatient)

    def test_pathway_hospital_state_pools_observation_without_changing_inpatient_state(self):
        seqs = {'A': [('2029-01-01', 'emergency'), ('2029-01-02', 'observation')],
                'D': [('2029-01-01', 'emergency'), ('2029-01-02', 'inpatient')]}
        attrs = {p: {'practice': 'P', 'coverage_type': 'commercial'} for p in seqs}
        p = pathway.validate({'start': 'emergency', 'targets': ['hospital_stay']})
        r = pathway.analyse(seqs, attrs, p, AS_OF)
        transitions = r['table']
        hospital = next(t for t in transitions if t['from'] == 'emergency' and t['to'] == 'hospital_stay')
        self.assertEqual(hospital['count'], 2)


if __name__ == '__main__':
    unittest.main()
