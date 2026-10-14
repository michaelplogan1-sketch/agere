"""Metric layer (clinical_core/agere/clinical/metrics.py, config/agere/metrics/*.json): schema, citations, templates on a
fixture semantic layer, lint rules on planted grain / date-field / window / value-set / population / versioning errors,
resolve() mapping, and the nl2sql / verify / answer_checks wiring. Synthetic fixture only; no network, no model."""
import json, os, sqlite3, tempfile, unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('AGERE_METRIC_ENCODER', 'off')

from clinical_core.agere.clinical import metrics, sql_query, verify, answer_checks, nl2sql

AS_OF = '2029-08-15'
TABLES = {
    'patient': 'patient_id,age_reported,sex_reported,coverage_type,coverage_status,pcp_clinician_id,pcp_practice_id,age_latest',
    'visit': 'encounter_id,patient_id,type,type_concept,status,event_date,end_date,available_date,organization_id,disposition,versions,'
             'start_time,end_time,in_person,is_practice,first_available_date,setting',
    'stay': 'patient_id,admit_date,discharge_date,type,organization_id,readmitted_30d,days_to_readmission,discharge_known,los_days',
    'appointment': 'appointment_id,patient_id,scheduled_date,booked_date,final_status,ever_no_show,ever_cancelled,ever_rescheduled,kept,'
                   'organization_id,clinician_id,purpose,versions',
    'claim': 'claim_id,patient_id,payer,service_date,first_status,final_status,ever_denied,net_payer_paid_cents,billed_cents,allowed_cents,'
             'first_available_date,first_submitted_date,first_payer_cash_date,first_adjudication_date',
    'claim_adjustment': 'claim_id,record_id,patient_id,category,amount_cents,current_version',
    'record': 'record_id,patient_id,stream,event_date,recorded_date,available_date,organization_id,payload',
    'measurement': 'record_id,patient_id,source_table,concept,name,value,unit,value_canonical,unit_canonical,result_status,event_date,'
                   'available_date,loinc,component,event_time',
    'measurement_latest': 'patient_id,concept,event_date,value_canonical,unit_canonical,record_id',
    'medication': 'record_id,patient_id,source_stream,evidence,drug_name,drug_class,status,event_date,ingredients,therapeutic_category',
    'condition': 'record_id,patient_id,concept,label,icd10,snomed,event_date',
    'concept_patient': 'concept,kind,patient_id,records,first_date,last_date',
    'social_need': 'patient_id,need,positive,instrument,event_date,record_id',
    'current_record': 'stream,key_field,business_id,record_id,patient_id,versions,available_date,event_date,payload',
    # payer feed (v2.2+) with v2.3 claim-version / reversal columns
    'payer_claim': 'payer_claim_id,patient_id,service_from,final_status,allowed_cents,counts_in_metrics,version_status,first_available_date',
    'payer_pharmacy_claim': 'record_id,patient_id,fill_date,claim_status,plan_paid_cents,patient_pay_cents,counts_as_fill',
    'member_month': 'patient_id,month,line_of_business',
    'utilization_event': 'patient_id,kind,event_date,sources',
}


def build_fixture(path):
    db = sqlite3.connect(path)
    for t, cols in TABLES.items():
        db.execute(f"CREATE TABLE {t}({cols})")
    ins = lambda t, rows: db.executemany(f"INSERT INTO {t}({','.join(rows[0])}) VALUES ({','.join('?' * len(rows[0]))})",
                                         [tuple(r.values()) for r in rows])
    ins('patient', [dict(patient_id=f'P{i}', age_reported=40 + i * 5, sex_reported='F', coverage_type='medicare', coverage_status='active',
                         pcp_clinician_id='C1', pcp_practice_id='WH-PRACTICE-0', age_latest=41 + i * 5) for i in range(8)])
    v = lambda eid, pid, typ, tc, d, **kw: dict(encounter_id=eid, patient_id=pid, type=typ, type_concept=tc, status=kw.get('status', 'completed'),
                                                event_date=d, end_date=d, available_date=d, organization_id=kw.get('org', 'WH-PRACTICE-0'),
                                                disposition=kw.get('dispo'), versions=2, start_time=f'{d}T10:00:00', end_time=f'{d}T20:00:00',
                                                in_person=kw.get('ip', 1), is_practice=kw.get('prac', 1), first_available_date=d, setting=None)
    ins('visit', [v('E1', 'P0', 'emergency', 'emergency_visit', '2029-02-01', dispo='admitted', prac=0, org='WH-HOSPITAL-0'),
                  v('E2', 'P1', 'emergency', 'emergency_visit', '2029-03-01', dispo='home', prac=0, org='WH-HOSPITAL-0'),
                  v('E3', 'P1', 'emergency', 'emergency_visit', '2028-12-01', dispo='observation', prac=0, org='WH-HOSPITAL-0'),
                  v('V1', 'P0', 'office', 'primary_care_visit', '2029-02-05'),
                  v('V2', 'P2', 'primary_care', 'primary_care_visit', '2028-08-15'),
                  v('V3', 'P3', 'wellness', 'primary_care_visit', '2029-01-10', prac=0, org='WH-DENTAL-0'),
                  v('V4', 'P4', 'telephone', 'telehealth_contact', '2029-02-03', ip=0),
                  v('V5', 'P5', 'urgent_care', 'urgent_care_visit', '2029-03-02', prac=0, org='WH-UC-0'),
                  v('V6', 'P6', 'specialty_followup', 'specialist_visit', '2029-04-04', prac=0, org='WH-SPEC-0'),
                  v('V7', 'P7', 'observation', 'observation_stay', '2029-04-01', prac=0, org='WH-HOSPITAL-0'),
                  v('V8', 'P0', 'inpatient', 'inpatient_admission', '2029-01-28', prac=0, org='WH-HOSPITAL-0')])
    ins('stay', [dict(patient_id='P0', admit_date='2029-01-28', discharge_date='2029-02-01', type='inpatient', organization_id='WH-HOSPITAL-0',
                      readmitted_30d=0, days_to_readmission=None, discharge_known=1, los_days=4),
                 dict(patient_id='P7', admit_date='2029-04-01', discharge_date='2029-04-02', type='observation', organization_id='WH-HOSPITAL-0',
                      readmitted_30d=1, days_to_readmission=10, discharge_known=1, los_days=1)])
    ins('appointment', [dict(appointment_id=f'A{i}', patient_id=f'P{i}', scheduled_date='2029-05-0' + str(i + 1), booked_date='2029-04-01',
                             final_status=s, ever_no_show=int(s == 'no_show'), ever_cancelled=0, ever_rescheduled=0, kept=int(s == 'fulfilled'),
                             organization_id='WH-PRACTICE-0', clinician_id='C1', purpose='visit', versions=2)
                        for i, s in enumerate(['fulfilled', 'no_show', 'booked', 'fulfilled'])])
    ins('claim', [dict(claim_id=f'CL{i}', patient_id=f'P{i}', payer='WH-PAYER-0', service_date='2028-03-0' + str(i + 1), first_status=s,
                       final_status='paid', ever_denied=int(s == 'denied'), net_payer_paid_cents=10000, billed_cents=20000, allowed_cents=15000,
                       first_available_date='2028-03-20', first_submitted_date='2028-03-05', first_payer_cash_date='2028-04-01',
                       first_adjudication_date='2028-03-20') for i, s in enumerate(['paid', 'denied', 'paid'])])
    rec = lambda rid, pid, stream, d, payload: dict(record_id=rid, patient_id=pid, stream=stream, event_date=d, recorded_date=d, available_date=d,
                                                    organization_id='WH-PRACTICE-0', payload=json.dumps(payload))
    ins('record', [
        rec('R1', 'P0', 'appointments', '2029-04-01', {'appointment_id': 'A9', 'status': 'booked', 'scheduled_date': '2029-05-01', 'event_time': '2029-04-01T10:00'}),
        rec('R2', 'P0', 'appointments', '2029-05-02', {'appointment_id': 'A9', 'status': 'fulfilled', 'scheduled_date': '2029-05-01', 'event_time': '2029-05-02T10:00'}),
        rec('R3', 'P1', 'appointments', '2029-04-01', {'appointment_id': 'A8', 'status': 'booked', 'scheduled_date': '2029-05-01', 'event_time': '2029-04-01T10:00'}),
        rec('R4', 'P1', 'patient_billing_accounts', '2029-01-01', {'account_id': 'AC1', 'balance_cents': 5000, 'event_time': '2029-01-01T00:00'}),
        rec('R5', 'P1', 'patient_billing_accounts', '2029-02-01', {'account_id': 'AC1', 'balance_cents': 3000, 'event_time': '2029-02-01T00:00'}),
        rec('R6', 'P2', 'patient_billing_accounts', '2029-02-01', {'account_id': 'AC2', 'balance_cents': -2000, 'event_time': '2029-02-01T00:00'}),
        rec('R7', 'P0', 'medical_claims', '2028-03-20', {'claim_id': 'CL0', 'service_date': '2028-03-01', 'service_code': 'office', 'status': 'paid',
                                                         'billed_cents': 20000, 'allowed_cents': 15000, 'payer_paid_cents': 10000, 'event_time': '2028-03-20T00:00',
                                                         'financial_deltas_cents': {'contractual': 5000}}),
        rec('R8', 'P0', 'medical_claims', '2028-04-20', {'claim_id': 'CL0', 'service_date': '2028-03-01', 'service_code': 'office', 'status': 'reversed',
                                                         'billed_cents': 20000, 'allowed_cents': 15000, 'payer_paid_cents': 10000, 'event_time': '2028-04-20T00:00',
                                                         'financial_deltas_cents': {'contractual': -5000}}),
        rec('R9', 'P0', 'payer_cash_allocations', '2028-04-01', {'claim_id': 'CL0', 'allocated_cents': 10000}),
        rec('R10', 'P0', 'community_service_referrals', '2029-01-01', {'service_requested': 'transport'}),
        rec('R11', 'P0', 'community_service_outcomes', '2029-01-09', {'referral_record_id': 'R10', 'service_delivered': True}),
        rec('R12', 'P0', 'adt', '2029-02-01', {'event': 'discharge', 'encounter_id': 'V8'}),
        rec('R13', 'P7', 'adt', '2029-04-01', {'event': 'discharge', 'encounter_id': 'V7'}),
    ])
    ins('measurement', [dict(record_id='M1', patient_id='P0', source_table='labs', concept='ldl', name='LDL cholesterol', value=195, unit='mg/dL',
                             value_canonical=195, unit_canonical='mg/dL', result_status='final', event_date=None, available_date='2029-01-01',
                             loinc='13457-7', component=None, event_time=None),
                        dict(record_id='M2', patient_id='P0', source_table='labs', concept='egfr', name='eGFR', value=70, unit='mL/min/1.73m2',
                             value_canonical=70, unit_canonical='mL/min/1.73m2', result_status='final', event_date='2028-05-01',
                             available_date='2028-05-02', loinc='33914-3', component=None, event_time='2028-05-01T09:00'),
                        dict(record_id='M3', patient_id='P0', source_table='labs', concept='uacr', name='Urine albumin/creatinine ratio', value=12,
                             unit='mg/g', value_canonical=12, unit_canonical='mg/g', result_status='final', event_date='2028-05-01',
                             available_date='2028-05-02', loinc='9318-7', component=None, event_time='2028-05-01T09:00'),
                        dict(record_id='M4', patient_id='P0', source_table='labs', concept='egfr', name='Glomerular filtration rate', value=60, unit='mL/min',
                             value_canonical=60, unit_canonical='mL/min', result_status='final', event_date=None, available_date='2028-05-02',
                             loinc=None, component=None, event_time=None),
                        dict(record_id='M5', patient_id='P0', source_table='vitals', concept=None, name='height_cm', value=160, unit='cm',
                             value_canonical=None, unit_canonical=None, result_status=None, event_date=None, available_date='2026-01-01',
                             loinc=None, component=None, event_time=None),
                        dict(record_id='M6', patient_id='P0', source_table='vitals', concept='weight', name='weight_kg', value=90, unit='kg',
                             value_canonical=90, unit_canonical='kg', result_status=None, event_date='2029-01-01', available_date='2029-01-01',
                             loinc=None, component=None, event_time=None)])
    ins('measurement_latest', [dict(patient_id='P0', concept='weight', event_date='2029-01-01', value_canonical=90, unit_canonical='kg', record_id='M6')])
    med = lambda rid, pid, name, cls, ing, cat: dict(record_id=rid, patient_id=pid, source_stream='pharmacy_dispenses', evidence='dispensed',
                                                     drug_name=name, drug_class=cls, status='dispensed', event_date='2029-01-01', ingredients=ing,
                                                     therapeutic_category=cat)
    ins('medication', [med('D1', 'P7', 'Diphenhydramine 25 MG', 'first_generation_antihistamine', 'diphenhydramine', 'antihistamine'),
                       med('D2', 'P6', 'Loratadine 10 MG', 'second_generation_antihistamine', 'loratadine', 'antihistamine'),
                       med('D3', 'P7', 'Diazepam 5 MG', 'benzodiazepine', 'diazepam', 'anxiolytic'),
                       med('D4', 'P0', 'Metoprolol 25 MG', 'beta_blocker', 'metoprolol', 'beta blocker'),
                       med('D5', 'P1', 'Albuterol inhaler', 'short_acting_beta_agonist', 'albuterol', 'beta agonist bronchodilator')])
    ins('condition', [dict(record_id='C1', patient_id='P0', concept='diabetes', label='Diabetes mellitus type 2', icd10='E11.9', snomed=None, event_date=None),
                      dict(record_id='C2', patient_id='P1', concept='heart_failure', label='Chronic congestive heart failure', icd10=None, snomed='88805009', event_date=None),
                      dict(record_id='C3', patient_id='P2', concept='asthma', label='Asthma', icd10='J45.909', snomed=None, event_date=None)])
    ins('concept_patient', [dict(concept='diabetes', kind='condition', patient_id='P0', records=1, first_date=None, last_date=None),
                            dict(concept='heart_failure', kind='condition', patient_id='P1', records=1, first_date=None, last_date=None),
                            dict(concept='asthma', kind='condition', patient_id='P2', records=1, first_date=None, last_date=None)])
    ins('social_need', [dict(patient_id='P0', need='food', positive=1, instrument='ahc', event_date='2029-01-01', record_id='S1'),
                        dict(patient_id='P1', need='food', positive=0, instrument='ahc', event_date='2029-01-01', record_id='S2')])
    ins('claim_adjustment', [dict(claim_id='CL0', record_id='R7', patient_id='P0', category='contractual_adjustment', amount_cents=5000, current_version=0)])
    ins('current_record', [dict(stream='patient_billing_accounts', key_field='account_id', business_id='AC1', record_id='R5', patient_id='P1', versions=2,
                                available_date='2029-02-01', event_date='2029-02-01', payload='{}')])
    pc = lambda cid, status, allowed, counts, vs: dict(payer_claim_id=cid, patient_id='M1', service_from='2029-03-01', final_status=status,
                                                     allowed_cents=allowed, counts_in_metrics=counts, version_status=vs, first_available_date='2029-03-20')
    ins('payer_claim', [pc('X1', 'paid', 10000, 1, 'current'), pc('X2', 'denied', 0, 1, 'current'),
                        pc('X3-D', 'denied', 0, 0, 'duplicate_submission'),           # CARC 18 duplicate: not a claim
                        pc('X4', 'voided', 0, 0, 'superseded_by_replacement'), pc('X4-R', 'paid', 6000, 1, 'current')])
    ins('payer_pharmacy_claim', [dict(record_id='RX1', patient_id='M1', fill_date='2029-03-05', claim_status='paid', plan_paid_cents=800,
                                      patient_pay_cents=200, counts_as_fill=1),
                                 dict(record_id='RX2', patient_id='M1', fill_date='2029-04-05', claim_status='paid', plan_paid_cents=800,
                                      patient_pay_cents=200, counts_as_fill=0)])  # reversed by a B2 return to stock
    ins('member_month', [dict(patient_id='M1', month=f'2029-0{m}', line_of_business='medicaid') for m in range(1, 5)])
    ins('utilization_event', [dict(patient_id='M1', kind='ed_visit', event_date='2029-02-10', sources='adt,claim'),
                              dict(patient_id='M1', kind='inpatient', event_date='2029-02-10', sources='claim')])
    db.commit(); db.close()


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.stack = Path(cls.tmp.name)
        cls.db_path = cls.stack / 'semantic.sqlite3'
        build_fixture(cls.db_path)
        cls.db = sqlite3.connect(f'file:{cls.db_path}?mode=ro', uri=True)
        cls.universe = {d: metrics.universe(cls.db, d) for d in metrics.DIMENSIONS}

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.tmp.cleanup()

    def lint(self, question, sql, rules=None):
        found = metrics.lint(question, sql, AS_OF, source=self.universe)
        return [f['rule'] for f in found] if rules is None else [f for f in found if f['rule'] in rules]


class SchemaTests(Fixture):
    def test_library_loads_and_validates(self):
        lib = metrics.library()
        templated = {k: m for k, m in lib.items() if m.get('sql_template')}
        self.assertGreaterEqual(len(templated), 30)
        self.assertGreaterEqual(len(lib), len(templated) + 20)  # one measure-backed metric per measure
        for mid, m in lib.items():
            metrics.validate_metric(m, mid)
            self.assertIn(m['grain']['entity'], metrics.GRAINS)
        domains = {m['domain'] for m in templated.values()}
        self.assertTrue({'utilization', 'operations', 'revenue_cycle', 'clinical_quality'} <= domains)

    def test_every_metric_cited_or_internal(self):
        for mid, m in metrics.library().items():
            if m['status'] == 'cited':
                cites = metrics.citations(m)
                self.assertTrue(cites and all(c['url'].startswith('http') for c in cites), mid)
            else:
                self.assertEqual(m['status'], 'internal', mid)
                self.assertTrue(len(m['internal_reason']) > 20, mid)

    def test_definitions_links_resolve(self):
        defs = json.loads((metrics.DEFINITIONS_FILE).read_text())['definitions']
        linked = {d for m in metrics.library().values() for d in m.get('definitions') or []}
        self.assertTrue(linked <= set(defs))
        self.assertTrue({'emergency_visit', 'primary_care_visit', 'utilization_per_1000', 'readmission_30d', 'no_show_rate'} <= linked)

    def test_bad_metric_rejected(self):
        good = dict(metrics.get('ed_visits'))
        for broken in ({**good, 'grain': {'entity': 'galaxy', 'key': 'x'}}, {**good, 'status': 'internal'},
                       {**good, 'time_anchor': {'field': 'visit.event_date', 'mode': 'trailing'}},
                       {**good, 'value_sets': {'x': {'dimension': 'visit.type', 'members': ['a']}}},
                       {**good, 'sql_template': 'DELETE FROM visit'}):
            with self.assertRaises(ValueError): metrics.validate_metric(broken)


class TemplateTests(Fixture):
    def test_templates_validate_and_run_on_fixture(self):
        for mid, m in metrics.library().items():
            if not m.get('sql_template'): continue
            for variant in [None, *[k for k, v in (m.get('variants') or {}).items() if v.get('sql_template')]]:
                mm = metrics.apply_variant(m, variant) if variant else m
                sql = metrics.render_sql(mm, metrics.parameters(mm, 'in 2029', AS_OF))
                sql_query.validate({'query': sql, 'returns': 'table'})
                try:
                    self.db.execute(sql).fetchall()
                except sqlite3.Error as exc:
                    self.fail(f'{mid}/{variant}: {exc}')

    def test_templates_lint_clean_against_their_own_metric(self):
        for mid, m in metrics.library().items():
            if not m.get('sql_template'): continue
            r = metrics.resolve({'metric': mid, 'question': '', 'as_of': AS_OF})
            found = metrics.lint('', r['sql'], AS_OF, source=self.universe, resolution=r)
            self.assertEqual(found, [], f'{mid}: {found}')

    def test_template_values_on_fixture(self):
        val = lambda mid, q='': self.db.execute(metrics.resolve({'metric': mid, 'question': q, 'as_of': AS_OF})['sql']).fetchall()
        self.assertEqual(val('ed_admit_obs_share', 'this year'), [(50.0,)])            # E1 admitted / E1+E2 (E3 is 2028)
        self.assertEqual(val('stale_booked_appointments'), [(1,)])                     # A8 only booked; A9 later fulfilled
        # payer feed: CARC 18 duplicate and superseded original never count; B2-reversed fill is not spend
        self.assertEqual(val('payer_claim_denial_rate', 'in 2029'), [(33.3,)])         # X2 of X1, X2, X4-R
        self.assertEqual(val('payer_allowed_pmpm', 'in 2029'), [(round((10000 + 6000 + 1000) / 100 / 4, 2),)])
        self.assertEqual(val('all_source_ed_visits_per_1000', 'in 2029'), [(3000.0,)])  # 1 ED visit / (4 member months / 12)
        self.assertEqual(val('patient_ar_outstanding'), [(30.0,)])                     # latest AC1 = 3000c, AC2 credit not netted
        self.assertEqual(val('contractual_writeoffs', 'in 2028'), [(0.0,)])            # reversal nets out
        self.assertEqual(val('pcp_visit_gap_12m'), [(6,)])                             # P0 (office) and P3 (wellness) seen; V2 is 1 day early
        self.assertEqual(val('attributed_pcp_visit_gap_12m'), [(7,)])                  # P3's wellness visit is not at a practice
        self.assertEqual(val('community_referral_delivery'), [('transport', 1, 1)])
        self.assertEqual(val('ldl_190_ever'), [('P0',)])                               # undated LDL counts
        self.assertEqual(val('beers_high_risk_medications_65'), [(1,)])                # P7 (76): diphenhydramine + diazepam; loratadine not

    def test_parameters_and_periods(self):
        m = metrics.get('pcp_visit_gap_12m')
        p = metrics.parameters(m, 'in the last 12 months', AS_OF)
        self.assertEqual((p['window_start'], p['period_start'], p['period_end']), ('2028-08-16', '2028-08-16', AS_OF))
        p = metrics.parameters(metrics.get('net_payer_paid'), 'payers paid on 2028 dates of service', AS_OF)
        self.assertEqual((p['period_start'], p['period_end']), ('2028-01-01', '2028-12-31'))
        p = metrics.parameters(metrics.get('ed_visits'), 'ED visits this year', AS_OF)
        self.assertEqual((p['period_start'], p['period_end']), ('2029-01-01', AS_OF))
        self.assertEqual(metrics.parameters(metrics.get('post_discharge_followup_7d'), '', AS_OF)['index_end'], '2029-08-08')


class LintTests(Fixture):
    def test_grain_versioned_raw_records(self):
        q = 'How many observation stays have we had so far in 2029?'
        self.assertIn('ML-GRAIN', self.lint(q, "SELECT count(*) FROM record WHERE stream = 'encounters' AND json_extract(payload,'$.type') = 'observation'"))
        self.assertIn('ML-VERSION', self.lint("How many appointments are still 'booked' with a date that has already passed?",
                                              "SELECT count(*) FROM appointment WHERE final_status = 'booked' AND scheduled_date < '2029-08-15'"))

    def test_date_field_anchor(self):
        self.assertIn('ML-ANCHOR', self.lint('How many duplicate lab rows - same patient, test, draw time and value?',
                                             'SELECT sum(n - 1) FROM (SELECT count(*) n FROM measurement GROUP BY patient_id, name, event_date, value)'))
        self.assertIn('ML-ANCHOR', self.lint('Average claim lag in days from date of service to first submission for 2028?',
                                             "SELECT avg(julianday(first_available_date) - julianday(service_date)) FROM claim WHERE substr(service_date,1,4)='2028'"))
        self.assertEqual(self.lint('How many duplicate lab rows - same patient, test, draw time and value?',
                                   'SELECT sum(n - 1) FROM (SELECT count(*) n FROM measurement GROUP BY patient_id, name, event_time, value)'), [])

    def test_window_off_by_one_flagged_and_healed(self):
        q = "How many patients haven't had a primary care visit in the last 12 months?"
        bad = ("SELECT count(*) FROM patient p WHERE NOT EXISTS (SELECT 1 FROM visit v WHERE v.patient_id = p.patient_id "
               "AND v.type_concept = 'primary_care_visit' AND v.event_date >= '2028-08-15' AND v.event_date <= '2029-08-15')")
        self.assertIn('ML-WINDOW', self.lint(q, bad))
        healed, repairs = metrics.heal(q, bad, AS_OF)
        self.assertIn("'2028-08-16'", healed); self.assertEqual(len(repairs), 1)
        self.assertNotIn('ML-WINDOW', self.lint(q, healed))
        self.assertEqual(self.db.execute(bad).fetchone(), (5,))      # V2 on 2028-08-15 wrongly counts
        self.assertEqual(self.db.execute(healed).fetchone(), (6,))
        alt, _ = metrics.heal(q, bad.replace(">= '2028-08-15'", ">= date('2029-08-15', '-12 months')"), AS_OF)
        self.assertIn("> date('2029-08-15', '-12 months')", alt)

    def test_followup_window_and_index_end(self):
        q = 'What share of hospital discharges in the last 12 months got an outpatient visit within 7 days?'
        sql = ("SELECT 100.0 * count(CASE WHEN EXISTS (SELECT 1 FROM visit v WHERE v.patient_id = s.patient_id AND v.type_concept IN ('primary_care_visit') "
               "AND julianday(v.event_date) - julianday(s.discharge_date) BETWEEN 0 AND 7) THEN 1 END) / count(*) FROM stay s "
               "WHERE s.discharge_date > date('2029-08-15','-12 months') AND s.discharge_date <= '2029-08-15'")
        msgs = [f['message'] for f in self.lint(q, sql, rules={'ML-WINDOW'})]
        self.assertTrue(any('1-7 days' in m for m in msgs) and any('2029-08-08' in m for m in msgs), msgs)

    def test_value_set_over_and_under(self):
        q = 'What share of our hospital discharges in the last 12 months got an outpatient visit within 7 days?'
        sql = ("SELECT 1 FROM visit v WHERE v.type_concept IN ('primary_care_visit','urgent_care_visit','telehealth_contact') "
               "AND julianday(v.event_date) - julianday('2029-01-01') BETWEEN 1 AND 7")
        over = self.lint(q, sql, rules={'ML-VALUESET'})
        self.assertTrue(any('telephone' in (f.get('over') or []) and 'urgent_care' in f['over'] for f in over), over)
        under = self.lint("How many patients haven't had a primary care visit in the last 12 months?",
                          "SELECT count(*) FROM patient p WHERE NOT EXISTS (SELECT 1 FROM visit v WHERE v.patient_id = p.patient_id AND v.type = 'primary_care')",
                          rules={'ML-VALUESET'})
        self.assertTrue(any('office' in (f.get('under') or []) for f in under), under)
        beers = self.lint('Beers check: how many patients 65+ were dispensed a first-generation antihistamine or a benzo?',
                          "SELECT count(DISTINCT p.patient_id) FROM patient p JOIN medication m ON m.patient_id = p.patient_id WHERE p.age_latest >= 65 "
                          "AND (m.drug_class = 'benzodiazepine' OR m.therapeutic_category LIKE '%antihistamine%')", rules={'ML-VALUESET'})
        self.assertTrue(any('loratadine' in (f.get('over') or []) for f in beers), beers)
        bb = self.lint('Which heart failure patients have no beta-blocker on record?',
                       "SELECT patient_id FROM concept_patient c WHERE concept='heart_failure' AND NOT EXISTS (SELECT 1 FROM medication m "
                       "WHERE m.patient_id = c.patient_id AND lower(m.drug_class) LIKE '%beta%')", rules={'ML-VALUESET'})
        self.assertTrue(any('albuterol' in (f.get('over') or []) for f in bb), bb)

    def test_value_set_required_and_qualifier(self):
        self.assertIn('ML-VALUESET', self.lint("What share of this year's ED visits ended in an admit or obs placement?",
                                               "SELECT 100.0 * sum(EXISTS (SELECT 1 FROM stay s WHERE s.patient_id = v.patient_id)) / count(*) FROM visit v "
                                               "WHERE v.type_concept = 'emergency_visit'"))
        self.assertIn('ML-VALUESET', self.lint("How many of our attributed patients haven't had an in-person primary care visit in the past 12 months?",
                                               "SELECT count(*) FROM patient p WHERE p.pcp_practice_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM visit v "
                                               "WHERE v.patient_id = p.patient_id AND v.type_concept = 'primary_care_visit' AND v.in_person = 1)"))

    def test_population_age_band_and_attribution(self):
        q = 'Kidney health evaluation for diabetes in 2028: what percent of our diabetics got both an eGFR and a urine albumin-creatinine ratio?'
        no_age = ("SELECT 100.0 * count(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM measurement m WHERE m.patient_id = d.patient_id AND m.concept = 'egfr') "
                  "THEN d.patient_id END) / count(DISTINCT d.patient_id) FROM concept_patient d WHERE d.concept = 'diabetes'")
        self.assertIn('ML-POP', self.lint(q, no_age))
        self.assertNotIn('ML-POP', self.lint(q, no_age.replace("WHERE d.concept = 'diabetes'",
                                                               "JOIN patient p ON p.patient_id = d.patient_id WHERE d.concept = 'diabetes' AND p.age_latest BETWEEN 18 AND 85")))
        self.assertIn('ML-POP', self.lint('Payer cash posted in 2028 by payer, net of take-backs.',
                                          "SELECT json_extract(c.payload,'$.payer_organization_id'), sum(json_extract(r.payload,'$.allocated_cents')) FROM record r "
                                          "JOIN current_record c ON c.stream = 'provider_remittance_advice' WHERE r.stream = 'payer_cash_allocations' GROUP BY 1"))

    def test_versioning_rules(self):
        self.assertIn('ML-VERSION', self.lint('Total contractual write-offs on 2028 dates of service?',
                                              "SELECT sum(ca.amount_cents) / 100.0 FROM claim_adjustment ca JOIN claim c ON c.claim_id = ca.claim_id "
                                              "WHERE ca.category = 'contractual_adjustment' AND ca.current_version = 1 AND substr(c.service_date,1,4) = '2028'"))
        self.assertIn('ML-SOURCE', self.lint("What's our total outstanding patient A/R right now?",
                                             "SELECT sum(b) FROM (SELECT json_extract(payload,'$.balance_cents') b, row_number() OVER (PARTITION BY "
                                             "json_extract(payload,'$.account_id') ORDER BY json_extract(payload,'$.event_time') DESC) rn FROM record "
                                             "WHERE stream = 'patient_billing_accounts') WHERE rn = 1"))
        self.assertIn('ML-VERSION', self.lint('Billed vs allowed vs paid by service type for 2029 dates of service - current state of each claim.',
                                              "SELECT service_code, sum(billed_cents), sum(allowed_cents), sum(payer_paid_cents) FROM (SELECT json_extract(payload,'$.service_code') "
                                              "service_code, json_extract(payload,'$.billed_cents') billed_cents, json_extract(payload,'$.allowed_cents') allowed_cents, "
                                              "json_extract(payload,'$.payer_paid_cents') payer_paid_cents, row_number() OVER (PARTITION BY json_extract(payload,'$.claim_id') "
                                              "ORDER BY json_extract(payload,'$.event_time') DESC) rn FROM record WHERE stream = 'medical_claims') WHERE rn = 1 GROUP BY 1"))

    def test_comments_do_not_satisfy_rules_and_overrides(self):
        q = 'Total contractual write-offs on 2028 dates of service?'
        self.assertIn('ML-VERSION', self.lint(q, "-- DEFINITION: nets every version (reversals net out)\nSELECT sum(amount_cents) FROM claim_adjustment WHERE current_version = 1"))
        # a question that names its own follow-up types overrides the metric's value set
        q2 = 'For ED visits this year, what percent got any outpatient touch - office, PCP, specialist or a phone call - within 7 days after?'
        self.assertEqual(self.lint(q2, "SELECT 100.0 * count(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM visit b WHERE b.patient_id = e.patient_id AND "
                                       "b.type_concept IN ('primary_care_visit','specialist_visit','telehealth_contact') AND julianday(b.event_date) - "
                                       "julianday(e.event_date) BETWEEN 1 AND 7) THEN e.encounter_id END) / count(DISTINCT e.encounter_id) FROM visit e "
                                       "WHERE e.type_concept = 'emergency_visit' AND substr(e.event_date,1,4) = '2029'"), [])

    def test_no_metric_no_findings(self):
        self.assertEqual(metrics.lint('How many patients have a potassium above 5.0 on their most recent check?',
                                      "SELECT count(*) FROM measurement_latest WHERE concept='potassium' AND value_canonical > 5", AS_OF, source=self.universe), [])


class ResolveTests(Fixture):
    CASES = {
        "How many patients haven't had a primary care visit in the last 12 months?": 'pcp_visit_gap_12m',
        "How many of our attributed patients haven't had an in-person primary care visit in the past 12 months?": 'attributed_pcp_visit_gap_12m',
        'By attributed practice: how many in-person primary care visits did our attributed patients have in 2028, per 1,000 attributed patients?': 'pcp_visits_per_1000_attributed',
        "What share of this year's ED visits ended in an admit or obs placement?": 'ed_admit_obs_share',
        'Who are our repeat ED users - two or more ED visits in the past 12 months?': 'repeat_ed_users_12m',
        'ED visits per 1,000 members in 2028?': 'ed_visits_per_1000',
        'What is our 30-day readmission rate for 2028?': 'readmission_30d',
        'What was the no-show rate for appointments in 2028?': 'no_show_rate',
        "What's our total outstanding patient A/R right now?": 'patient_ar_outstanding',
        'Payer cash posted in 2028 by payer - net of any take-backs.': 'payer_cash_net',
        'Total contractual write-offs on 2028 dates of service?': 'contractual_writeoffs',
        'What was our first-pass denial rate for 2028 claims?': 'first_pass_denial_rate',
        'Allowed PMPM for 2028?': 'allowed_pmpm',
        'Average inpatient length of stay in 2028?': 'average_length_of_stay',
        'Beers list check - how many patients 65+ have been dispensed a benzo or a first-generation antihistamine?': 'beers_high_risk_medications_65',
        'How many duplicate lab rows do we have - same patient, same test, same draw time, same value?': 'duplicate_lab_rows',
        'Kidney health evaluation for diabetes in 2028: what percent of our diabetics got both an eGFR and a uACR?': 'kidney_health_evaluation_diabetes',
    }

    def test_resolve_mapping(self):
        for q, want in self.CASES.items():
            r = metrics.resolve(q)
            self.assertIsNotNone(r, q)
            self.assertEqual(r['id'], want, q)
            self.assertEqual(r['matched_by'], 'rules')

    def test_resolve_none_and_slots(self):
        self.assertIsNone(metrics.resolve('How many patients have a potassium above 5.0 on their most recent check?'))
        r = metrics.resolve({'metric': 'no_show_rate', 'as_of': AS_OF, 'question': 'no-show rate in 2028'})
        self.assertEqual((r['matched_by'], r['params']['period_start']), ('explicit', '2028-01-01'))
        self.assertIn("'2028-01-01'", r['sql'])
        r = metrics.resolve({'measure': 'ked'})
        self.assertEqual(r['id'], 'kidney_health_evaluation_diabetes')
        r = metrics.resolve({'measure': 'cbp'})
        self.assertEqual((r['id'], r['engine']), ('measure.cbp', {'measures': 'cbp'}))
        self.assertIn('metric no_show_rate v1', metrics.resolve('no-show rate in 2028?')['computed_as'])

    def test_variant(self):
        r = metrics.resolve('ED-to-hospital conversion: of our ED visits since 2026, what percent turned into an observation stay or an admission?', as_of=AS_OF)
        self.assertEqual((r['id'], r['variant']), ('ed_admit_obs_share', 'linked_conversion'))
        self.assertNotIn('admit_obs_disposition', r['metric']['value_sets'])
        self.assertIn('stay s', r['sql'])

    def test_encoder_fallback_only_for_pins(self):
        class Enc:
            def encode(self, texts, batch_size=32):
                return [[1.0, 0.0] if ('no-show' in t.lower() or 'missed appointment' in t.lower()) else [0.0, 1.0] for t in texts]
        q = 'How often do people skip their missed appointment slots?'
        self.assertIsNone(metrics.resolve(q))
        with mock.patch.dict(os.environ, {'AGERE_METRIC_ENCODER': 'on'}):
            r = metrics.resolve(q, encoder=Enc(), allow_encoder=True)
        self.assertEqual((r['id'], r['matched_by']), ('no_show_rate', 'encoder'))


class ValueSetTests(Fixture):
    def test_build_value_set_classifies_and_never_invents(self):
        vs = metrics.get('beers_high_risk_medications_65')['value_sets']['beers_drugs']
        built = metrics.build_value_set(vs, self.universe['medication.ingredient'])
        self.assertEqual(set(built['members']), {'diphenhydramine', 'diazepam'})
        self.assertEqual(set(built['excluded']), {'loratadine'})
        observed = {r['ingredients'] for r in self.universe['medication.ingredient']}
        self.assertTrue(set(built['members']) <= observed)  # only received values, never invented codes
        egfr = metrics.build_value_set(metrics.get('kidney_health_evaluation_diabetes')['value_sets']['egfr'], self.universe['measurement.name'],
                                       dictionary={'eGFR (cystatin)': {'agere_concept': 'egfr', 'confidence': 0.6, 'source_count': 3}})
        self.assertEqual(set(egfr['members']), {'eGFR'})
        self.assertIn('Glomerular filtration rate', egfr['review'])          # no LOINC: review, not member
        self.assertIn('eGFR (cystatin)', egfr['review'])                      # dictionary candidate: review
        self.assertEqual(egfr['concept_coverage']['non_member_labels'], ['Glomerular filtration rate'])

    def test_concept_audit(self):
        audit = metrics.audit_concepts(self.db)
        self.assertIn('egfr', audit['measurements'])
        self.assertEqual(audit['measurements']['egfr']['concept_rows_without_loinc'], {'Glomerular filtration rate': 1})


class WiringTests(Fixture):
    def test_verify_metric_lint_and_modes(self):
        q = 'Total contractual write-offs on 2028 dates of service?'
        sql = "SELECT sum(amount_cents) FROM claim_adjustment WHERE current_version = 1"
        out = verify.metric_lint(q, sql, AS_OF, self.stack)
        self.assertTrue(out and out[0].startswith('ML-VERSION:'))
        with mock.patch.dict(os.environ, {'AGERE_METRIC_LINT': 'off'}):
            self.assertEqual(verify.metric_lint(q, sql, AS_OF, self.stack), [])
            self.assertEqual(verify.metric_heal(q, sql, AS_OF), (sql, []))

    def test_answer_checks_state_metric(self):
        q = 'Total contractual write-offs on 2028 dates of service?'
        ans = {'status': 'answered', 'answer_type': 'value', 'value': 1.0, 'computed': 'Contractual adjustments',
               'query': "SELECT sum(amount_cents) / 100.0 FROM claim_adjustment WHERE current_version = 1"}
        answer_checks.annotate(ans, q, AS_OF)
        self.assertEqual(ans['computed_as']['metric'], 'contractual_writeoffs')
        self.assertFalse(ans['computed_as']['conforms'])
        self.assertIn('[metric contractual_writeoffs v1]', ans['computed'])
        self.assertTrue(any(c['control'] == 'QC-METRIC' and c['severity'] == 'warning' for c in ans['quality_checks']))
        clean = {'status': 'answered', 'answer_type': 'value', 'value': 2.0,
                 'query': metrics.resolve({'metric': 'contractual_writeoffs', 'question': q, 'as_of': AS_OF})['sql']}
        answer_checks.annotate(clean, q, AS_OF)
        self.assertTrue(clean['computed_as']['conforms'])
        self.assertFalse(any(c['control'] == 'QC-METRIC' and c['severity'] == 'warning' for c in clean['quality_checks']))
        from clinical_core.agere.clinical import learning
        self.assertIn('QC-METRIC', learning.QUARANTINE_CONTROLS)

    def test_nl2sql_pin_and_metric_repair(self):
        q = "How many appointments are sitting in 'booked' with a date that's already passed?"
        profile = {'source_digest': 'fixture', 'streams': {'appointments': {'records': 3, 'fields': {
            'appointment_id': {'populated': 3, 'types': {}}, 'status': {'populated': 3, 'types': {}, 'values': {'booked': 2, 'fulfilled': 1}},
            'scheduled_date': {'populated': 3, 'types': {}}}}}}
        bad = "SELECT count(*) FROM appointment WHERE final_status = 'booked' AND scheduled_date < '2029-08-15'"
        good = metrics.resolve({'question': q, 'as_of': AS_OF})['sql']
        seen = []

        def model(answers):
            it = iter(answers)
            def call(system, user):
                seen.append(user)
                return f'```sql\n{next(it)}\n```', {}
            return call
        with mock.patch.object(nl2sql.source_profile, 'load', return_value=profile), \
                mock.patch.object(nl2sql, 'schema_for', return_value=(['appointments'], 'TABLES\nappointment(...)')), \
                mock.patch.object(nl2sql.sql_query.semantic_layer, 'ensure', return_value=self.db_path), \
                mock.patch.dict(os.environ, {'AGERE_HARVEST_SHOTS': 'off', 'AGERE_METRIC_LINT': 'repair'}):
            params, result, receipt = nl2sql._verified_answer(self.stack, q, AS_OF, model([bad, good]), model([good]), model([good]))
        self.assertIn('GOVERNED METRIC stale_booked_appointments v1', seen[0])
        self.assertEqual(receipt['metric']['id'], 'stale_booked_appointments')
        first = receipt['attempts'][0]
        self.assertTrue(first.get('error', '').startswith('metric: ML-VERSION'), receipt['attempts'])
        self.assertEqual(result.get('value'), 1)
        self.assertEqual(receipt['agreement'], 'primary=second')


if __name__ == '__main__':
    unittest.main()
