"""More VBC metric families (vbc_families.py): no-show rate by scheduling source system, distinct persons hospitalized,
generic share of paid fills, persons with a CMS-HCC V28 category from paid claim diagnoses, deaths by the product of the
primary span the day before death, ended PCP roster affiliations; the undelivered-input decline (delivered_inputs.py) in
the planner, the hybrid core and together.decide; and the risk-adjustment negative-age crash regression.

Small synthetic sqlite fixtures with hand-computed answers. No model calls."""
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from clinical_core.agere.clinical import delivered_inputs as di, economical_planner, risk_adjustment as ra, together, vbc_families as vb
from clinical_core.agere.clinical.sql_query import execute

AS_OF = '2030-05-27'
SCOPE = ' Scope: every patient in the (sampled) delivery, duplicate charts counted as their person.'
APPT_RULES = (" Appointments: each appointment_id once, in its latest delivered version, from every delivered EHR scheduling "
              "record (each EHR's appointment table or FHIR Appointment resources, including the deep-chart panel; monthly "
              "extracts are incremental, latest month wins); slot date = slot_start, else scheduled_time/scheduled_date, else "
              "the appointment's date (some deep-chart panel appointments carry only a date); attended and arrived count as "
              "fulfilled, rescheduled as cancelled.")
STAY_RULE = (" Inpatient stay = distinct person and admission date on non-voided institutional payer claims with bill type 011x, "
             "final adjudication.")
SPANS = (" Delivered enrollment spans (primary coverage; Medigap and COB secondary excluded; a span superseded by a later version "
         "of the same coverage is replaced by it, while a span superseded only by a member_id_reissued continuation is kept) "
         "and delivered death records.")

Q_ACC = ("Which practices have the worst no-show rates? Definition: Scheduling source systems with >= 2 fulfilled-or-no-show "
         "appointments with 2028-2029 slots: top 5 by no-show rate, percent." + APPT_RULES + SCOPE)
Q_HOS = "How many different members were hospitalized at least once in 2028? Definition: Distinct persons with >= 1 inpatient stay admitted in 2028." + STAY_RULE + SCOPE
Q_RX = ("What share of 2028 fills were generics? Definition: All patients. Paid payer pharmacy claims in 2028 with "
        "formulary_tier generic, percent of paid claims.")
Q_HCC = ("How many members had at least one HCC captured in 2029? Definition: Persons with >= 1 CMS-HCC V28 category from 2029 "
         "paid claim diagnoses (service in 2029)." + SCOPE)
Q_DEATH = ("How many members died, by line of business? Definition: Persons with a delivered death record through as_of, by the "
           "product of the primary span active the day before death (uninsured if none)." + SPANS + SCOPE)
Q_PCP = ("How many primary care providers left their practice during the period? Definition: Release-wide. PCP affiliations "
         "that ended (provider departed or moved practices) through as_of.")
Q_DQ10 = ("How many of our records were later restated by a newer version? Definition: All patients. Source records that a "
          "later record supersedes (supersedes_record_id points to them).")

SCHEMA = '''
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, payload TEXT);
CREATE TABLE appointment(appointment_id TEXT PRIMARY KEY, patient_id TEXT, scheduled_date TEXT, final_status TEXT, organization_id TEXT);
CREATE TABLE patient(patient_id TEXT PRIMARY KEY, birth_date TEXT, age_reported INTEGER, sex_reported TEXT);
CREATE TABLE payer_claim(payer_claim_id TEXT PRIMARY KEY, patient_id TEXT, product TEXT, line_of_business TEXT, claim_type TEXT,
  type_of_bill TEXT, service_from TEXT, service_to TEXT, admission_date TEXT, discharge_date TEXT, final_status TEXT,
  counts_in_metrics INTEGER, record_id TEXT);
CREATE TABLE payer_claim_diagnosis(payer_claim_id TEXT, patient_id TEXT, service_from TEXT, rank INTEGER, icd10 TEXT,
  counts_in_metrics INTEGER);
CREATE TABLE payer_pharmacy_claim(record_id TEXT PRIMARY KEY, patient_id TEXT, fill_date TEXT, claim_status TEXT, drug_name TEXT,
  formulary_tier TEXT);
CREATE TABLE enrollment_span(record_id TEXT PRIMARY KEY, patient_id TEXT, product TEXT, line_of_business TEXT, coverage_start TEXT,
  coverage_end TEXT);
CREATE TABLE death(patient_id TEXT PRIMARY KEY, death_date TEXT);
CREATE TABLE raw_rosters_aco_provider_roster(_file TEXT, _drop TEXT, _row INTEGER, practice_id TEXT, provider_npi TEXT,
  pcp_flag TEXT, term_date TEXT);
CREATE TABLE delivery_file(relpath TEXT PRIMARY KEY, feed TEXT, period TEXT);
'''


def _claim(db, cid, pid, day, status='paid', tob=None, admit=None, disch=None, ctype='professional'):
    db.execute('INSERT INTO payer_claim VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (cid, pid, 'medicare_ffs', 'medicare', ctype, tob, day, disch or day, admit, disch, status, 1, None))


def build(root, tiers=False, native=True):
    db = sqlite3.connect(root / 'semantic.sqlite3')
    db.executescript(SCHEMA)
    db.execute("INSERT INTO meta VALUES ('as_of', ?)", (AS_OF,))
    # appointments: practice A 1 no-show of 3 fulfilled-or-no-show (arrived counts as fulfilled; cancelled ignored),
    # B 2 of 2, C below the minimum, one with no organization falls back to its records' source_system (S9: 1 of 2)
    appts = [('a1', 'A', '2028-02-01', 'fulfilled'), ('a2', 'A', '2028-03-01', 'arrived'), ('a3', 'A', '2029-01-01', 'no_show'),
             ('a4', 'A', '2029-02-01', 'cancelled'), ('b1', 'B', '2028-05-01', 'no_show'), ('b2', 'B', '2029-05-01', 'no_show'),
             ('b3', 'B', '2027-05-01', 'fulfilled'), ('c1', 'C', '2028-06-01', 'no_show'),
             ('s1', None, '2028-07-01', 'no_show'), ('s2', None, '2028-08-01', 'fulfilled')]
    for aid, org, day, st in appts:
        db.execute('INSERT INTO appointment VALUES (?,?,?,?,?)', (aid, 'P1', day, st, org))
        db.execute('INSERT INTO record VALUES (?,?,?,?,?)', ('r' + aid, 'P1', 'appointments', day,
                                                             json.dumps({'appointment_id': aid, 'source_system': 'S9' if org is None else 'ehr:' + org})))
    db.executemany('INSERT INTO patient VALUES (?,?,?,?)', [
        ('P1', '1950-01-01', None, 'F'), ('P2', '1960-06-01', None, 'M'), ('P3', '2020-03-01', None, 'M'),
        ('P4', '1955-01-01', None, 'F'), ('P5', '2030-03-01', None, 'F')])  # P5 born after Feb 1 2030 (age < 0 before the fix)
    # inpatient stays: P1 two stays in 2028, P2 one in 2028, P3 one in 2027, P4 a voided 2028 claim, a professional claim
    _claim(db, 'i1', 'P1', '2028-01-05', tob='0111', admit='2028-01-05', disch='2028-01-08', ctype='institutional')
    _claim(db, 'i2', 'P1', '2028-06-05', tob='0111', admit='2028-06-05', disch='2028-06-09', ctype='institutional')
    _claim(db, 'i3', 'P2', '2028-03-01', tob='0111', admit='2028-03-01', disch='2028-03-02', ctype='institutional')
    _claim(db, 'i4', 'P3', '2027-03-01', tob='0111', admit='2027-03-01', disch='2027-03-02', ctype='institutional')
    _claim(db, 'i5', 'P4', '2028-04-01', status='voided', tob='0111', admit='2028-04-01', disch='2028-04-02', ctype='institutional')
    _claim(db, 'i6', 'P4', '2028-04-01', tob='0131', ctype='institutional')
    # 2029 paid claim diagnoses: P1 E11.9 (HCC), P2 Z00.00 only (none), P3 F01.50 at age 9 (dementia: an age-edited code),
    # P4 E11.9 on a denied claim (not paid), P2 I50.9 in 2028 (other year)
    for cid, pid, day, st, codes in (('d1', 'P1', '2029-03-01', 'paid', ['E11.9']), ('d2', 'P2', '2029-04-01', 'paid', ['Z00.00']),
                                     ('d3', 'P3', '2029-05-01', 'paid', ['F01.50']), ('d4', 'P4', '2029-05-01', 'denied', ['E11.9']),
                                     ('d5', 'P2', '2028-05-01', 'paid', ['I50.9'])):
        _claim(db, cid, pid, day, status=st)
        for k, c in enumerate(codes, 1):
            db.execute('INSERT INTO payer_claim_diagnosis VALUES (?,?,?,?,?,1)', (cid, pid, day, k, c))
    # pharmacy: 2028 paid fills, 3 generic product names and 1 branded; rejected and 2027 fills ignored
    fills = [('f1', '2028-01-01', 'paid', 'metformin 500 MG Oral Tablet', 'generic' if tiers else None),
             ('f2', '2028-02-01', 'paid', 'lisinopril 10 MG Oral Tablet', 'brand' if tiers else None),
             ('f3', '2028-03-01', 'paid', 'atorvastatin 20 MG Oral Tablet', 'generic' if tiers else None),
             ('f4', '2028-04-01', 'paid', 'sitagliptin 100 MG Oral Tablet [Januvia]', 'brand' if tiers else None),
             ('f5', '2028-05-01', 'rejected', 'x [Y]', None), ('f6', '2027-05-01', 'paid', 'x [Y]', None)]
    db.executemany('INSERT INTO payer_pharmacy_claim VALUES (?,?,?,?,?,?)', [(r, 'P1', d, s, n, t) for r, d, s, n, t in fills])
    # deaths: P1 on medicare_ffs (a medigap span too), P2 only medigap -> uninsured, P3 span starting on the death date ->
    # uninsured, P4 after as_of (excluded)
    db.executemany('INSERT INTO enrollment_span VALUES (?,?,?,?,?,?)', [
        ('e1', 'P1', 'medicare_ffs', 'medicare', '2026-01-01', None), ('e2', 'P1', 'medigap', 'medigap', '2026-01-01', None),
        ('e3', 'P2', 'medigap', 'medigap', '2026-01-01', None), ('e4', 'P3', 'commercial', 'commercial', '2029-06-01', None),
        ('e5', 'P4', 'medicaid', 'medicaid', '2026-01-01', None)])
    db.executemany('INSERT INTO death VALUES (?,?)', [('P1', '2029-06-01'), ('P2', '2029-07-01'), ('P3', '2029-06-01'), ('P4', '2030-07-01')])
    # rosters 2030-01..03: n1@pr1 listed throughout (active); n2@pr1 term 2030-02-01 (ended); n3@pr2 only in January and
    # n3@pr3 from February (a move: pr2 ended); n4@pr4 dropped but not a PCP; a short row without an NPI
    rows = []
    for m in ('2030-01', '2030-02', '2030-03'):
        f = f'rosters/{m}/R.csv'
        rows += [(f, m, 'pr1', '1000000001', 'Y', None), (f, m, 'pr1', '1000000002', 'Y', '2030-02-01' if m >= '2030-02' else None)]
        rows.append((f, m, 'pr2' if m == '2030-01' else 'pr3', '1000000003', 'Y', None))
        if m == '2030-01': rows += [(f, m, 'pr4', '1000000004', 'N', None), (f, m, 'pr5', None, None, None)]
        db.execute('INSERT INTO delivery_file VALUES (?,?,?)', (f, 'rosters', m))
    db.executemany('INSERT INTO raw_rosters_aco_provider_roster VALUES (?,?,0,?,?,?,?)', rows)
    if not native:
        db.execute('DROP TABLE delivery_file')
    db.commit()
    db.close()
    return root


def answer(question, path, as_of=AS_OF):
    res = vb.plan(path, question, as_of)
    assert res is not None, question
    node = res['graph']['nodes'][0]['parameters']
    out = execute(None, node, target=Path(path) / 'semantic.sqlite3')
    got = out.get('value') if node['returns'] in ('count', 'value') else {r['key']: r['value'] for r in out['rows']}
    return res, got


class Families(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = build(Path(tempfile.mkdtemp()))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_noshow_by_source(self):
        res, got = answer(Q_ACC, self.root)
        self.assertEqual(res['spec']['family'], 'noshow_by_source')
        self.assertEqual(got, {'B': 100.0, 'S9': 50.0, 'A': 33.33})  # C has 1 (< 2); S9 from the records' source_system
        self.assertTrue(res['graph']['nodes'][0]['parameters']['query'].startswith('-- DEFINITION:'))
        top1 = Q_ACC.replace('top 5', 'top 1')
        self.assertEqual(answer(top1, self.root)[1], {'B': 100.0})

    def test_noshow_needs_the_appointment_rules(self):
        self.assertIsNone(vb.parse(Q_ACC.replace(APPT_RULES, ''), AS_OF))

    def test_persons_hospitalized(self):
        res, got = answer(Q_HOS, self.root)
        self.assertEqual((res['spec']['family'], res['spec']['parameters']['unit']), ('admissions', 'persons'))
        self.assertEqual(got, 2)  # P1 (two stays) and P2; P3 2027, P4 voided / outpatient
        self.assertEqual(answer(Q_HOS.replace('>= 1 inpatient stay', '>= 2 inpatient stays'), self.root)[1], 1)
        self.assertIsNone(vb.parse(Q_HOS.replace(STAY_RULE, ''), AS_OF))

    def test_admissions_stays_unchanged(self):
        q = "Definition: Inpatient stays admitted in 2028 with UB-04 admission type 3 (elective)." + STAY_RULE
        self.assertEqual(vb.parse(q, AS_OF)['parameters'], {'year': 2028, 'admission_types': [3]})

    def test_generic_share_from_product_names_when_no_tier_is_delivered(self):
        res, got = answer(Q_RX, self.root)
        self.assertEqual(res['spec']['family'], 'pharmacy_tier_share')
        self.assertEqual(got, 75.0)  # 3 of 4 paid 2028 fills carry no [Brand]
        self.assertIsNone(vb.parse(Q_RX.replace('generic', 'specialty'), AS_OF))  # not readable from a name

    def test_generic_share_uses_the_delivered_tier(self):
        root = build(Path(tempfile.mkdtemp()), tiers=True)
        try:
            self.assertEqual(answer(Q_RX, root)[1], 50.0)  # tier delivered: f2 is tiered brand despite a generic name
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_hcc_persons(self):
        res, got = answer(Q_HCC, self.root)
        self.assertEqual(res['spec']['family'], 'hcc_persons')
        self.assertEqual(got, 1)  # P1 E11.9; P3 F01.50 at 9 fails the age edit; P4 denied; P2 Z00.00 / 2028
        codes = res['spec']['parameters']['v28_codes']
        self.assertEqual(codes['any'], ['E119'])
        self.assertTrue(all(c[0] == 'F0150' and c[2] > 9 for c in codes['cond']))
        self.assertIsNone(vb.plan(None, Q_HCC, AS_OF))  # the code list needs the workspace

    def test_hcc_code_list_is_checked_against_v28(self):
        spec = {'family': 'hcc_persons', 'parameters': {'year': 2029, 'v28_codes': {'any': ['E119', 'Z0000'], 'cond': []}},
                'returns': 'count'}
        with self.assertRaises(ValueError):
            vb.validate(spec)
        spec['parameters']['v28_codes']['any'] = ['E119']
        vb.validate(spec)

    def test_hcc_persons_matches_the_engine(self):
        """Same count as scoring each person's paid-claim codes with risk_adjustment.score (any V28 HCC kept)."""
        db = sqlite3.connect(self.root / 'semantic.sqlite3')
        n = 0
        for pid, birth, sex in db.execute('SELECT patient_id, birth_date, sex_reported FROM patient'):
            cs = [r[0] for r in db.execute("SELECT d.icd10 FROM payer_claim_diagnosis d JOIN payer_claim c USING (payer_claim_id) "
                                           "WHERE d.patient_id = ? AND c.final_status = 'paid' AND c.service_from LIKE '2029%'", (pid,))]
            from datetime import date
            age = ra.scoring_age(birth, date(2030, 2, 1))  # a newborn after the age date scores in the youngest band
            n += bool(ra.score('V28', age, 2 if sex == 'F' else 1, cs)['hccs'])
        db.close()
        self.assertEqual(answer(Q_HCC, self.root)[1], n)

    def test_hcc_age_is_unknown_without_a_birth_date_never_the_reported_age(self):
        """Agere's age policy (age.py): age derives from birth_date only. A member with no birth date but a reported age of
        70 must not satisfy the age-banded F01.50 edit (it did while _age_sql fell back to age_reported)."""
        root = build(Path(tempfile.mkdtemp()))
        try:
            db = sqlite3.connect(root / 'semantic.sqlite3')
            db.execute("INSERT INTO patient VALUES ('P6', NULL, 70, 'F')")
            db.execute("INSERT INTO patient VALUES ('P7', 'not-a-date', 70, 'F')")
            for cid, pid in (('d6', 'P6'), ('d7', 'P7')):
                _claim(db, cid, pid, '2029-06-01')
                db.execute('INSERT INTO payer_claim_diagnosis VALUES (?,?,?,?,?,1)', (cid, pid, '2029-06-01', 1, 'F01.50'))
            db.commit()
            unknown = db.execute(f"SELECT p.patient_id, {vb._age_sql('2030-02-01')} FROM patient p WHERE p.patient_id IN ('P6', 'P7')").fetchall()
            db.close()
            self.assertEqual(unknown, [('P6', None), ('P7', None)])
            res, got = answer(Q_HCC, root)
            age_edit = [c for c in res['spec']['parameters']['v28_codes']['cond'] if c[0] == 'F0150']
            self.assertTrue(any(lo <= 70 <= hi for _, _, lo, hi in age_edit))  # a reported 70 would have matched
            self.assertEqual(got, 1)  # P1 only: unknown age matches no age-banded condition
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_deaths_by_product(self):
        res, got = answer(Q_DEATH, self.root)
        self.assertEqual(res['spec']['family'], 'deaths_by_product')
        self.assertEqual(got, {'medicare_ffs': 1, 'uninsured': 2})
        self.assertIsNone(vb.parse(Q_DEATH.replace(SPANS, ''), AS_OF))

    def test_pcp_affiliations_ended(self):
        res, got = answer(Q_PCP, self.root)
        self.assertEqual(res['spec']['family'], 'pcp_affiliations_ended')
        self.assertEqual(got, 2)  # n2@pr1 (term date), n3@pr2 (moved); n1 active, n4 not a PCP
        self.assertEqual(answer(Q_PCP, self.root, as_of='2030-01-20')[1], 0)  # January roster is the latest: nothing ended yet
        self.assertEqual(answer(Q_PCP.replace('through as_of', 'in 2030'), self.root)[1], 2)

    def test_other_shapes_still_decline(self):
        for q in (Q_HOS.replace('admitted in 2028', 'discharged in 2028'), Q_PCP.replace('PCP affiliations', 'Specialist affiliations'),
                  Q_HCC.replace('V28', 'V24')):
            self.assertIsNone(vb.parse(q, AS_OF), q)


class UndeliveredInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = build(Path(tempfile.mkdtemp()))
        cls.warehouse = build(Path(tempfile.mkdtemp()), tiers=True, native=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.native, ignore_errors=True)
        shutil.rmtree(cls.warehouse, ignore_errors=True)

    def test_inferred_lineage_declines_natively_only(self):
        miss = di.undelivered(self.native, Q_DQ10)
        self.assertEqual([m['source'] for m in miss], ['inferred_natively'])
        self.assertIn('supersedes_record_id', miss[0]['input'])
        self.assertEqual(di.undelivered(self.native, 'How many of our records were later restated by a newer version?')[0]['source'],
                         'inferred_natively')
        self.assertEqual(di.undelivered(self.warehouse, Q_DQ10), [])  # a build that delivers the link answers it

    def test_native_stack_marker(self):
        root = build(Path(tempfile.mkdtemp()), native=False)
        try:
            self.assertEqual(di.undelivered(root, Q_DQ10), [])
            (root / 'run.json').write_text(json.dumps({'schema': 'agere-native-stack/1'}))
            di._CACHE.clear()
            self.assertEqual(len(di.undelivered(root, Q_DQ10)), 1)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_null_everywhere_column(self):
        q = 'How many paid pharmacy claims had formulary_tier brand in 2028?'
        miss = di.undelivered(self.native, q)
        self.assertEqual([m['source'] for m in miss], ['null_everywhere'])
        self.assertIn('formulary_tier', miss[0]['input'])
        self.assertEqual(di.undelivered(self.warehouse, q), [])
        self.assertEqual(di.undelivered(self.native, Q_RX), [])  # the family states its derivation for this definition

    def test_stated_and_scope_boilerplate(self):
        miss = di.undelivered(self.native, "What's our CAHPS score? Definition: Not answerable from this delivery: patient experience survey (CAHPS) results.")
        self.assertEqual(miss[0]['input'], 'patient experience survey (CAHPS) results')
        # a recoverable definition mentioning superseded spans is not a lineage question
        self.assertEqual(di.undelivered(self.native, Q_DEATH), [])

    def test_planner_declines_before_metric_rungs(self):
        stack = SimpleNamespace(path=self.native, retriever=SimpleNamespace(meta={'snapshot': {'as_of': AS_OF}}), allow_live=True,
                                receipts=None)
        graph, info = economical_planner.plan(stack, Q_DQ10, None, None)
        self.assertEqual(info['route'], 'undelivered_input')
        self.assertEqual(graph['coverage'], 'unsupported')
        self.assertTrue(graph['unmet_requirements'][0].startswith('Missing input: record-version lineage'))
        self.assertIn('undelivered_input', together.SPECIALIST_ROUTES)

    def test_hybrid_core_declines_without_runs(self):
        from clinical_core.agere.hybrid import live

        def no_send():
            raise AssertionError('no model call expected')
        h = live.answer(Q_DQ10, AS_OF, SimpleNamespace(path=self.native / 'semantic.sqlite3'), no_send)
        self.assertEqual(h['decline']['kind'], 'unsupported')
        self.assertEqual(h['new_paid_api_calls'], 0)
        self.assertEqual(together._hybrid_contract(h)['status'], 'unsupported')

    def test_decide_keeps_the_decline_over_an_analyst_answer(self):
        fast = {'route': 'undelivered_input', 'planner_receipt': {'route': 'undelivered_input'},
                'answer': {'status': 'unsupported', 'answer_type': 'unsupported', 'missing': ['record-version lineage']}}
        analyst = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 25591}, 'tool_calls': []}
        result, receipt, _ = together.decide(None, Q_DQ10, fast, None, analyst, 'done')
        self.assertEqual(result['answer']['status'], 'unsupported')
        self.assertEqual(receipt['decision'], 'undelivered_input')


class RiskAdjustmentNegativeAge(unittest.TestCase):
    def test_band_and_score_for_a_person_born_after_the_age_date(self):
        self.assertEqual(ra._band(-1), '0_34')
        self.assertIsInstance(ra.score('V28', -1, 2, ['E119'])['raw'], float)

    def test_engine_runs_with_a_newborn(self):
        from test_risk_adjustment import fixture
        root = fixture()
        try:
            db = sqlite3.connect(root / 'semantic.sqlite3')
            db.execute("INSERT INTO patient(patient_id, birth_date, sex_reported, coverage_type) VALUES ('N', '2030-03-01', 'F', 'commercial')")
            db.commit(); db.close()
            report = ra.execute(root, None, {'year': 2029, 'coverage': 'all'}, AS_OF)
            self.assertEqual(report['status'], 'answered')
            self.assertEqual({p['patient_id']: p['age'] for p in report['patients']}['N'], 0)
            ra.summarize(report)
            # the default (Medicare) scope is unchanged by the fix
            self.assertEqual(ra.execute(root, None, {}, AS_OF)['population']['patients_in_scope'], 4)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
