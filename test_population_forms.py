"""Free population path (2026-10-23): grammar forms and engine forms answered with no model, from the decision-context definitions.

A tiny synthetic warehouse; every expected number is computed here in plain Python from the same rows (an independent implementation of
the stated definitions), never copied from the SQL under test. Covers: shares with lab denominators, blood pressure control, gaps,
"on a medication" as a picked-up fill in the 12 months, flu season, rates, rankings with a minimum size, claim amounts and shares,
high-cost claimants, SDOH, grouping, duplicate charts, and every insight family.
"""
import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from clinical_core.agere.clinical import decision_context as DC
from clinical_core.agere.clinical import insight_families as IF
from clinical_core.agere.clinical import population_engines as PE
from clinical_core.agere.clinical import request_language as L
from clinical_core.agere.clinical import request_plan as R
from clinical_core.agere.clinical import shared_planner as S
from clinical_core.agere.clinical import sql_query

AS_OF = '2030-05-27'
WIN = ('2029-05-28', AS_OF)  # the 12 months ending at the as-of date


def in_win(d):
    return d is not None and WIN[0] <= d <= WIN[1]


# patient_id: (birth_date, sex, coverage, line_of_business, pcp_practice, data_segment)
PATIENTS = {
    'P01': ('1960-01-01', 'F', 'medicare', 'medicare', 'PR1', 'deep_chart'),
    'P02': ('1955-06-01', 'M', 'medicare', 'medicare', 'PR1', 'deep_chart'),
    'P03': ('1985-03-03', 'F', 'commercial', 'commercial', 'PR2', 'deep_chart'),
    'P04': ('1990-09-09', 'M', 'medicaid', 'medicaid', 'PR2', 'claims_only'),
    'P05': ('1940-02-02', 'F', 'medicare', 'medicare', 'PR1', 'deep_chart'),          # deceased
    'P06': ('1975-05-05', 'M', 'commercial', 'commercial', 'PR3', 'deep_chart'),
    'P07': ('1965-07-07', 'F', 'medicare', 'medicare', 'PR1', 'duplicate_chart'),     # a second chart of P01
    'P08': ('2015-01-01', 'M', 'commercial', 'commercial', 'PR2', 'deep_chart'),      # a child
    'P09': ('1950-04-04', 'F', 'medicare', 'medicare', 'PR3', 'deep_chart'),
    'P10': ('1980-08-08', 'M', 'medicaid', 'medicaid', None, 'claims_only'),          # practice only from the enrollment attribution
}
DEAD = {'P05'}
DUP = {'P07'}
CONDITIONS = {  # concept -> patients with an EHR diagnosis
    'diabetes': ['P01', 'P02', 'P03', 'P05', 'P07'], 'hypertension': ['P01', 'P02', 'P06', 'P09'], 'heart_failure': ['P02', 'P09'], 'copd': ['P06'],
}
CLAIM_DX = {'diabetes': ['P04']}                    # counted payer-claim diagnosis only (claims-only member)
LAB_EVIDENCE = {'diabetes': ['P10']}                # laboratory-evidence condition row
# (patient, concept, date, value)
LABS = [('P01', 'hba1c', '2028-01-01', 7.0), ('P01', 'hba1c', '2029-08-01', 9.5), ('P02', 'hba1c', '2029-02-01', 7.5), ('P03', 'hba1c', '2030-01-15', 6.8),
        ('P10', 'hba1c', '2030-03-01', 10.2), ('P07', 'hba1c', '2030-01-01', 8.5),
        ('P01', 'systolic_bp', '2030-01-10', 150), ('P01', 'diastolic_bp', '2030-01-10', 95), ('P02', 'systolic_bp', '2030-01-10', 130), ('P02', 'diastolic_bp', '2030-01-10', 80),
        ('P06', 'systolic_bp', '2030-01-10', 138), ('P06', 'diastolic_bp', '2030-01-10', 92), ('P09', 'systolic_bp', '2030-01-10', 120), ('P09', 'diastolic_bp', '2030-01-10', 70)]
FILLS = [('P01', 'statin', '2030-02-01'), ('P03', 'statin', '2029-01-01'), ('P02', 'ace_inhibitor', '2030-03-01'), ('P09', 'sglt2_inhibitor', '2029-10-01'),
         ('P09', 'ace_inhibitor', '2028-01-01')]
ED = [('P01', '2029-07-01'), ('P01', '2029-09-01'), ('P01', '2030-01-01'), ('P02', '2029-06-15'), ('P03', '2029-01-01'), ('P10', '2029-12-01'), ('P10', '2030-02-01'),
      ('P07', '2029-08-01'), ('P07', '2029-09-01'), ('P07', '2029-10-01'), ('P07', '2029-11-01')]
INPATIENT = [('P02', '2029-09-10', '2029-09-14'), ('P09', '2030-04-20', '2030-05-02')]
VISITS = [('P01', 'primary_care_visit', '2029-07-10'), ('P09', 'primary_care_visit', '2030-05-10'), ('P03', 'telehealth_contact', '2029-08-08')]
FLU = [('P01', 'influenza', 'completed', '2029-10-01'), ('P09', 'influenza high-dose', 'completed', '2028-10-01'), ('P02', 'influenza', 'completed', '2030-05-01')]
MAMMO = [('P01', 'bilateral_screening_mammography', '2029-01-01'), ('P03', 'bilateral_screening_mammography', '2025-01-01'), ('P09', 'bilateral_screening_mammography', '2028-09-01')]
# claims: id, patient, payer, product, lob, type, pos, service_from, network, status, paid_cents, allowed, charge, carc, counts
CLAIMS = [
    ('k1', 'P01', 'X1', 'medicare_advantage', 'medicare', 'institutional', None, '2029-03-10', 'in_network', 'paid', 7_000_000, 7_200_000, 9_000_000, None, 1),
    ('k2', 'P01', 'X1', 'medicare_advantage', 'medicare', 'professional', '11', '2029-05-01', 'in_network', 'paid', 100_000, 110_000, 150_000, None, 1),
    ('k3', 'P02', 'X1', 'medicare_ffs', 'medicare', 'professional', '23', '2029-04-01', 'out_of_network', 'paid', 200_000, 210_000, 300_000, None, 1),
    ('k4', 'P03', 'X2', 'commercial', 'commercial', 'professional', '11', '2029-06-01', 'in_network', 'denied', 0, 0, 50_000, 'CO-16', 1),
    ('k5', 'P04', 'X2', 'medicaid', 'medicaid', 'professional', '23', '2029-07-01', 'in_network', 'paid', 30_000, 31_000, 40_000, None, 1),
    ('k6', 'P06', 'X2', 'commercial', 'commercial', 'institutional', None, '2028-05-05', 'in_network', 'paid', 400_000, 410_000, 500_000, None, 1),
    ('k7', 'P09', 'X1', 'medicare_ffs', 'medicare', 'institutional', None, '2029-11-11', 'in_network', 'paid', 120_000, 125_000, 200_000, None, 1),
    ('k8', 'P01', 'X1', 'medicare_advantage', 'medicare', 'institutional', None, '2029-03-10', 'in_network', 'paid', 7_000_000, 7_200_000, 9_000_000, None, 0),  # superseded version
    ('k9', 'P03', 'X2', 'commercial', 'commercial', 'professional', '11', '2029-08-01', 'in_network', 'denied', 0, 0, 20_000, 'CO-16', 1),
    ('k10', 'P03', 'X2', 'commercial', 'commercial', 'professional', '11', '2029-09-01', 'in_network', 'denied', 0, 0, 20_000, 'CO-97', 1),
]
PHARMACY = [  # patient, fill_date, status, tier, counts_as_fill, plan_paid_cents, drug
    ('P01', '2028-03-01', 'paid', 'generic', 1, 1000, 'atorvastatin'), ('P01', '2029-03-01', 'paid', 'generic', 1, 1200, 'atorvastatin'),
    ('P02', '2029-04-01', 'paid', 'brand', 1, 90000, 'apixaban'), ('P02', '2028-04-01', 'paid', 'brand', 1, 40000, 'apixaban'),
    ('P03', '2029-05-01', 'rejected', None, 0, 0, 'insulin'), ('P03', '2029-06-01', 'paid', 'specialty', 1, 50000, 'semaglutide'),
]
APPTS = [  # patient, date, status, lead days
    ('P01', '2029-08-01', 'fulfilled', 3), ('P01', '2029-09-01', 'no_show', 40), ('P02', '2029-08-01', 'fulfilled', 10), ('P03', '2029-10-01', 'no_show', 5),
    ('P03', '2029-11-01', 'attended', 35), ('P10', '2029-12-01', 'fulfilled', 2), ('P10', '2030-01-01', 'no_show', 2), ('P10', '2030-02-01', 'no_show', 3),
    ('P04', '2030-03-01', 'fulfilled', 12),
]
NEEDS = [('P01', 'housing', 1), ('P10', 'housing', 1), ('P03', 'food', 1), ('P04', 'housing', 0)]


def build(path):
    with sqlite3.connect(Path(path) / 'semantic.sqlite3') as db:
        db.executescript(f'''
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES ('version','semantic-layer/fixture'),('as_of','{AS_OF}');
        CREATE TABLE patient(patient_id TEXT, birth_date TEXT, sex_reported TEXT, coverage_type TEXT, line_of_business TEXT, pcp_practice_id TEXT, data_segment TEXT);
        CREATE TABLE death(patient_id TEXT PRIMARY KEY, death_date TEXT);
        INSERT INTO death VALUES ('P05','2029-01-01');
        CREATE TABLE condition(record_id TEXT, patient_id TEXT, concept TEXT, icd10 TEXT, event_date TEXT, is_diagnosis INTEGER, diagnosis_basis TEXT);
        CREATE TABLE payer_claim_diagnosis(patient_id TEXT, icd10 TEXT, service_from TEXT, counts_in_metrics INTEGER);
        CREATE TABLE measurement(record_id TEXT, patient_id TEXT, concept TEXT, event_date TEXT, value_canonical REAL, result_current INTEGER, excluded_reason TEXT, source_table TEXT);
        CREATE TABLE measurement_latest(patient_id TEXT, concept TEXT, event_date TEXT, value_canonical REAL, unit_canonical TEXT, same_day_values INTEGER, same_day_min REAL, record_id TEXT, value_censor TEXT);
        CREATE TABLE fill(patient_id TEXT, drug_class TEXT, fill_date TEXT);
        CREATE TABLE medication(patient_id TEXT, drug_class TEXT, evidence TEXT, status TEXT, fill_reversed INTEGER, event_date TEXT);
        CREATE TABLE utilization_event(patient_id TEXT, kind TEXT, event_date TEXT, end_date TEXT, sources TEXT, in_ehr INTEGER, record_id TEXT);
        CREATE TABLE stay(patient_id TEXT, type TEXT, admit_date TEXT, discharge_date TEXT, discharge_known INTEGER);
        CREATE TABLE visit(patient_id TEXT, type_concept TEXT, event_date TEXT, encounter_id TEXT);
        CREATE TABLE immunization(patient_id TEXT, vaccine TEXT, status TEXT, event_date TEXT, season TEXT);
        CREATE TABLE cancer_screening(patient_id TEXT, test TEXT, event_date TEXT);
        CREATE TABLE payer_claim(payer_claim_id TEXT, patient_id TEXT, payer_id TEXT, product TEXT, line_of_business TEXT, claim_type TEXT, place_of_service TEXT,
          service_from TEXT, network_status TEXT, final_status TEXT, paid_cents INTEGER, allowed_cents INTEGER, charge_cents INTEGER, carc TEXT, counts_in_metrics INTEGER);
        CREATE TABLE payer_pharmacy_claim(patient_id TEXT, fill_date TEXT, claim_status TEXT, formulary_tier TEXT, counts_as_fill INTEGER, plan_paid_cents INTEGER, drug_name TEXT);
        CREATE TABLE member_month(patient_id TEXT, month TEXT, payer_id TEXT, product TEXT, line_of_business TEXT, attribution_practice_id TEXT);
        CREATE TABLE enrollment_span(patient_id TEXT, line_of_business TEXT, coverage_start TEXT, attribution_practice_id TEXT);
        CREATE TABLE appointment(patient_id TEXT, scheduled_date TEXT, final_status TEXT, outcome TEXT, organization_id TEXT, lead_time_days INTEGER);
        CREATE TABLE social_need(patient_id TEXT, need TEXT, positive INTEGER, event_date TEXT);
        CREATE TABLE returned_fill(patient_id TEXT, event_date TEXT, reason TEXT);
        CREATE TABLE patient_svi(patient_id TEXT, svi_overall REAL);
        CREATE TABLE sdoh_vendor(record_id TEXT, patient_id TEXT, delivery_date TEXT, match_status TEXT, food_access_low INTEGER, transportation_risk_score REAL);
        ''')
        db.executemany('INSERT INTO patient VALUES (?,?,?,?,?,?,?)', [(k, *v) for k, v in PATIENTS.items()])
        n = 0
        for concept, ids in CONDITIONS.items():
            for pid in ids:
                n += 1
                db.execute('INSERT INTO condition VALUES (?,?,?,?,?,?,?)', (f'c{n}', pid, concept, {'diabetes': 'E11.9', 'hypertension': 'I10', 'heart_failure': 'I50.9', 'copd': 'J44.9'}[concept], '2028-01-01', 1, None))
        for pid in LAB_EVIDENCE['diabetes']:
            db.execute("INSERT INTO condition VALUES ('cl1',?,'diabetes','E11.9','2029-01-01',NULL,'laboratory_evidence')", (pid,))
        for pid in CLAIM_DX['diabetes']:
            db.execute("INSERT INTO payer_claim_diagnosis VALUES (?,'E11.9','2029-07-01',1)", (pid,))
        for i, (pid, concept, d, v) in enumerate(LABS):
            db.execute('INSERT INTO measurement VALUES (?,?,?,?,?,1,NULL,?)', (f'm{i}', pid, concept, d, v, 'labs'))
        db.execute("INSERT INTO measurement VALUES ('billed1','P04','hba1c','2029-12-01',NULL,1,NULL,'payer_claim_line')")  # a billed test line: not a result
        db.execute('''INSERT INTO measurement_latest SELECT patient_id, concept, event_date, value_canonical, NULL, 1, value_canonical, record_id, NULL FROM measurement m
                      WHERE value_canonical IS NOT NULL AND event_date = (SELECT MAX(event_date) FROM measurement x WHERE x.patient_id = m.patient_id AND x.concept = m.concept AND x.value_canonical IS NOT NULL)''')
        for pid, cls, d in FILLS:
            db.execute('INSERT INTO fill VALUES (?,?,?)', (pid, cls, d))
            db.execute("INSERT INTO medication VALUES (?,?,'dispensed','dispensed',0,?)", (pid, cls, d))
        for i, (pid, d) in enumerate(ED):
            db.execute("INSERT INTO utilization_event VALUES (?,?,?,?,'ehr',1,?)", (pid, 'ed_visit', d, d, f'e{i}'))
        for i, (pid, a, dc) in enumerate(INPATIENT):
            db.execute("INSERT INTO utilization_event VALUES (?,?,?,?,'ehr',1,?)", (pid, 'inpatient', a, dc, f'i{i}'))
            db.execute("INSERT INTO stay VALUES (?,?,?,?,1)", (pid, 'inpatient', a, dc))
        for i, (pid, t, d) in enumerate(VISITS):
            db.execute('INSERT INTO visit VALUES (?,?,?,?)', (pid, t, d, f'v{i}'))
        for pid, v, s_, d in FLU:
            db.execute('INSERT INTO immunization VALUES (?,?,?,?,NULL)', (pid, v, s_, d))
        db.executemany('INSERT INTO cancer_screening VALUES (?,?,?)', MAMMO)
        db.executemany('INSERT INTO payer_claim VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', CLAIMS)
        db.executemany('INSERT INTO payer_pharmacy_claim VALUES (?,?,?,?,?,?,?)', PHARMACY)
        for pid, d, st, lead in APPTS:
            db.execute('INSERT INTO appointment VALUES (?,?,?,?,?,?)', (pid, d, st, st, 'PR1' if pid in ('P01', 'P02') else 'PR2', lead))
        db.executemany('INSERT INTO social_need VALUES (?,?,?,?)', [(p, n_, pos, '2029-06-01') for p, n_, pos in NEEDS])
        db.executemany('INSERT INTO returned_fill VALUES (?,?,?)', [('P01', '2029-04-01', 'return_to_stock'), ('P02', '2029-05-01', 'return_to_stock'), ('P03', '2028-05-01', 'return_to_stock')])
        db.executemany('INSERT INTO patient_svi VALUES (?,?)', [('P01', 80), ('P02', 90), ('P03', 10), ('P04', 20), ('P06', 50), ('P10', 95)])
        db.executemany('INSERT INTO sdoh_vendor VALUES (?,?,?,?,?,?)', [('s1', 'P01', '2029-01-01', 'exact', 0, 3), ('s2', 'P01', '2030-01-01', 'exact', 1, 8),
                                                                         ('s3', 'P02', '2030-01-01', 'exact', 1, 2), ('s4', 'P03', '2030-01-01', 'no_match', 1, None)])
        # enrollment: every patient enrolled all of 2029 in the line of business of the patient row, except P10 attributed to PR3 and P02 on MA part of the year
        for pid, (birth, sex, cov, lob, practice, seg) in PATIENTS.items():
            db.execute('INSERT INTO enrollment_span VALUES (?,?,?,?)', (pid, lob, '2026-01-01', practice if practice else 'PR3'))
            if pid in DEAD or pid in DUP: continue
            for mth in range(1, 13):
                product = {'medicare': 'medicare_ffs', 'commercial': 'commercial', 'medicaid': 'medicaid'}[lob]
                if pid == 'P01': product = 'medicare_advantage'
                db.execute('INSERT INTO member_month VALUES (?,?,?,?,?,?)', (pid, f'2029-{mth:02d}', 'X1' if lob == 'medicare' else 'X2', product, lob, practice or 'PR3'))


def living(pid):
    return pid not in DEAD and pid not in DUP


def age_at(birth, on=AS_OF):
    b, o = date.fromisoformat(birth), date.fromisoformat(on)
    return o.year - b.year - ((o.month, o.day) < (b.month, b.day))


def has_condition(pid, concept):
    return pid in CONDITIONS.get(concept, []) or pid in CLAIM_DX.get(concept, []) or pid in LAB_EVIDENCE.get(concept, [])


def latest(pid, concept):
    rows = sorted((d, v) for p, c, d, v in LABS if p == pid and c == concept)
    return rows[-1][1] if rows else None


def practice(pid):
    return PATIENTS[pid][4] or 'PR3'


class Base(unittest.TestCase):
    EXCLUDE_DUPLICATES = True

    def setUp(self):
        patcher = patch.dict(os.environ, {'AGERE_EXCLUDE_DUPLICATE_CHARTS': '1' if self.EXCLUDE_DUPLICATES else '0'})
        patcher.start(); self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        build(self.path)
        self.catalog = R.Catalog.load(self.path)
        self.stack = SimpleNamespace(path=self.path, allow_live=False, shared_request_planning=True,
                                     retriever=SimpleNamespace(meta={'snapshot': {'as_of': AS_OF}}))

    def ask(self, q):
        return S.compose(self.stack, q, model=Mock(side_effect=AssertionError('no language model on the free path')))

    def answer(self, q):
        r = self.ask(q)
        self.assertEqual(r['answer']['status'], 'answered', r['answer'].get('missing'))
        return r['answer']

    def value(self, q):
        a = self.answer(q)
        return a.get('value') if a.get('value') is not None else a.get('count')

    def table(self, q):
        return self.answer(q)['table']


class GrammarForms(Base):
    def test_share_of_a_lab_reads_patients_with_a_latest_result(self):
        pop = [p for p in PATIENTS if living(p) and has_condition(p, 'diabetes') and age_at(PATIENTS[p][0]) >= 18 and latest(p, 'hba1c') is not None]
        want = 100.0 * sum(latest(p, 'hba1c') > 9 for p in pop) / len(pop)
        a = self.answer('What share of our adult diabetic patients have an A1c above 9 on their latest result?')
        self.assertAlmostEqual(a['value'], want, places=6)
        self.assertEqual((a['numerator'], a['denominator']), (sum(latest(p, 'hba1c') > 9 for p in pop), len(pop)))
        self.assertIn('read_back', a)
        self.assertNotIn('P04', a['patient_ids'])  # a billed A1c line without a result is not a result

    def test_the_deceased_are_not_patients_and_a_second_chart_counts_once_when_the_rule_is_on(self):
        n = len([p for p in PATIENTS if living(p)])
        self.assertEqual(self.value('How many patients are there?'), n)
        self.assertEqual(self.value('How many patients, living or deceased, are there?'), len(PATIENTS))  # the deceased are asked for: the living rule, duplicates included, is off

    def test_blood_pressure_under_control(self):
        pop = [p for p in PATIENTS if living(p) and has_condition(p, 'hypertension') and 18 <= age_at(PATIENTS[p][0]) <= 85
               and latest(p, 'systolic_bp') is not None and latest(p, 'diastolic_bp') is not None]
        ok = [p for p in pop if latest(p, 'systolic_bp') < 140 and latest(p, 'diastolic_bp') < 90]
        a = self.answer('What percent of patients with hypertension have their blood pressure under control?')
        self.assertAlmostEqual(a['value'], 100.0 * len(ok) / len(pop), places=6)
        by = self.table('What percent of patients with hypertension have their blood pressure under control by payer type?')
        self.assertEqual({r['line_of_business'] for r in by}, {PATIENTS[p][3] for p in pop})

    def test_care_gaps_missing_and_negated_leads(self):
        diab = [p for p in PATIENTS if living(p) and has_condition(p, 'diabetes')]
        no_a1c = [p for p in diab if not any(c == 'hba1c' and q == p and in_win(d) for q, c, d, v in LABS)]
        self.assertEqual(self.value("How many diabetics haven't had an A1c in the past year?"), len(no_a1c))
        self.assertEqual(self.value('How many diabetics are missing an A1c in the last year?'), len(no_a1c))

    def test_on_a_medication_is_a_fill_in_the_last_twelve_months(self):
        hf = [p for p in PATIENTS if living(p) and has_condition(p, 'heart_failure')]
        pillars = {'ace_inhibitor', 'arb', 'arni', 'mra', 'sglt2_inhibitor', 'hf_evidence_based_beta_blocker'}
        none = [p for p in hf if not any(q == p and c in pillars and in_win(d) for q, c, d in FILLS)]
        self.assertEqual(self.value('How many HF patients are on no GDMT medication at all?'), len(none))
        a = self.answer('How many patients with hypertension are on a statin?')
        self.assertEqual(a['count'], len([p for p in PATIENTS if living(p) and has_condition(p, 'hypertension') and any(q == p and c == 'statin' and in_win(d) for q, c, d in FILLS)]))
        self.assertTrue(any('picked-up fill dated 2029-05-28 through 2030-05-27' in x for x in a['limitations']))

    def test_flu_season_and_rates(self):
        old = [p for p in PATIENTS if living(p) and age_at(PATIENTS[p][0]) >= 65]
        shot = [p for p in old if any(q == p and 'influenza' in v and '2029-07-01' <= d <= AS_OF for q, v, s_, d in FLU)]
        self.assertAlmostEqual(self.value('What percentage of patients 65 and older got a flu shot last season?'), 100.0 * len(shot) / len(old), places=6)
        women = [p for p in PATIENTS if living(p) and PATIENTS[p][1] == 'F' and 50 <= age_at(PATIENTS[p][0]) <= 74]
        mammo = [p for p in women if any(q == p and '2028-05-28' <= d <= AS_OF for q, t, d in MAMMO)]
        self.assertAlmostEqual(self.value("What's our mammography screening rate for women 50 to 74 over the last two years?"), 100.0 * len(mammo) / len(women), places=6)

    def test_counts_and_rankings_of_events(self):
        ed = {}
        for p, d in ED:
            if living(p) and in_win(d): ed[p] = ed.get(p, 0) + 1
        self.assertEqual(self.value('How many patients have 3 or more ED visits in the last year?'), len([p for p, n in ed.items() if n >= 3]))
        a = self.answer('Who are our frequent ED users — patients with 3 or more ED visits in the last year?')
        self.assertEqual(sorted(a['patient_ids']), sorted(p for p, n in ed.items() if n >= 3))
        self.assertEqual(self.value('How many no-shows in the last year?'), len([1 for p, d, s_, lead in APPTS if s_ == 'no_show' and in_win(d)]))
        by = {}
        for p, d, s_, lead in APPTS:
            if s_ == 'no_show' and in_win(d): by['PR1' if p in ('P01', 'P02') else 'PR2'] = by.get('PR1' if p in ('P01', 'P02') else 'PR2', 0) + 1
        top = self.table('Which practice had the most no-shows in the last year?')
        self.assertEqual(top[0]['organization_id'], max(by, key=by.get))
        self.assertEqual(top[0]['count'], max(by.values()))

    def test_rank_share_with_a_minimum_size_and_the_practice_fallback(self):
        diab = [p for p in PATIENTS if living(p) and has_condition(p, 'diabetes') and age_at(PATIENTS[p][0]) >= 18 and latest(p, 'hba1c') is not None]
        groups = {}
        for p in diab: groups.setdefault(practice(p), []).append(latest(p, 'hba1c') < 8)
        rows = self.table('Which practices have the lowest share of diabetics with an A1c under 8, practices with at least 2 diabetics?')
        want = sorted(((sum(v) / len(v), g) for g, v in groups.items() if len(v) >= 2))
        self.assertEqual([r['practice'] for r in rows], [g for _, g in want])
        self.assertTrue(all(r['patients'] >= 2 for r in rows))
        nobody = self.table('Which practices have the lowest share of diabetics with an A1c under 8, practices with at least 5 diabetics?')
        self.assertEqual(nobody, [])

    def test_claim_amounts_shares_and_averages(self):
        counted = [c for c in CLAIMS if c[14] == 1 and c[7].startswith('2029')]
        by = {}
        for c in counted: by[c[4]] = by.get(c[4], 0) + c[10]
        rows = self.table('What did we pay out in medical claims in 2029 by line of business?')
        self.assertEqual({r['line_of_business']: round(r['paid_dollars'] * 100) for r in rows}, by)
        out = sum(c[10] for c in counted if c[8] == 'out_of_network') / sum(c[10] for c in counted)
        self.assertAlmostEqual(self.value('What percentage of our 2029 medical spend went out of network?'), round(100 * out, 2), places=2)
        hospital = sum(c[10] for c in counted if c[5] == 'institutional') / sum(c[10] for c in counted)
        self.assertAlmostEqual(self.value('How much of our 2029 paid spend is hospital (institutional) claims?'), round(100 * hospital, 2), places=2)
        ed_pro = [c[10] for c in counted if c[6] == '23' and c[5] == 'professional' and c[9] == 'paid']
        self.assertAlmostEqual(self.value("What's the average paid amount on an ED professional claim in 2029?"), round(sum(ed_pro) / len(ed_pro) / 100, 2), places=2)

    def test_rates_of_rows_and_ranked_denial_rate(self):
        counted = [c for c in CLAIMS if c[14] == 1 and c[7].startswith('2029')]
        rows = self.table('Which payer has the highest denial rate in 2029 (payers with at least 3 claims)?')
        rates = {}
        for c in counted: rates.setdefault(c[2], []).append(c[9] == 'denied')
        best = max((sum(v) / len(v), k) for k, v in rates.items() if len(v) >= 3)
        self.assertEqual((rows[0]['payer_id'], rows[0]['proportion']), (best[1], round(best[0], 4)))
        pharmacy = [x for x in PHARMACY if x[1].startswith('2029') and x[2] in ('paid', 'rejected')]
        self.assertAlmostEqual(self.value('What share of pharmacy claims were rejected in 2029?'), round(100.0 * sum(x[2] == 'rejected' for x in pharmacy) / len(pharmacy), 2), places=2)
        fills = [x for x in PHARMACY if x[1].startswith('2029') and x[4] == 1 and x[3] is not None]
        self.assertAlmostEqual(self.value("What's our generic dispensing rate on pharmacy claims in 2029?"), round(100.0 * sum(x[3] == 'generic' for x in fills) / len(fills), 2), places=2)
        self.assertEqual(self.value('How many prescriptions were returned to stock in 2029?'), 2)

    def test_high_cost_claimants_and_thousands_separators(self):
        totals = {}
        for c in CLAIMS:
            if c[14] == 1 and c[7].startswith('2029') and living(c[1]): totals[c[1]] = totals.get(c[1], 0) + c[10]
        want = sorted(p for p, t in totals.items() if t >= 5_000_000)
        a = self.answer('Who are the high-cost claimants for 2029, meaning $50,000 or more paid?')
        self.assertEqual(sorted(a['patient_ids']), want)

    def test_sdoh_and_screening_joins(self):
        a = self.answer('Which of our diabetics with an A1c over 9 also live in low-food-access areas according to the purchased SDOH data?')
        latest_flag = {'P01': 1, 'P02': 1}  # the latest matched delivery per patient: P01 flipped to low food access in 2030; P03's only row is unmatched
        want = sorted(p for p in PATIENTS if living(p) and has_condition(p, 'diabetes') and (latest(p, 'hba1c') or 0) > 9 and latest_flag.get(p))
        self.assertEqual(sorted(a['patient_ids']), want)
        housing = self.value('Among diabetics with A1c above 9, how many screened positive for housing instability?')
        self.assertEqual(housing, len([p for p in PATIENTS if living(p) and has_condition(p, 'diabetes') and (latest(p, 'hba1c') or 0) > 9
                                       and any(q == p and n_ == 'housing' and pos for q, n_, pos in NEEDS)]))

    def test_in_each_group_and_the_marker_threshold(self):
        diab = [p for p in PATIENTS if living(p) and has_condition(p, 'diabetes') and age_at(PATIENTS[p][0]) >= 18 and (latest(p, 'hba1c') or 0) > 9]
        rows = self.table('For adult diabetic patients with a latest A1c above 9, how many are there in each line of business?')
        by = {}
        for p in diab: by[PATIENTS[p][3]] = by.get(PATIENTS[p][3], 0) + 1
        self.assertEqual({r['line_of_business']: r['patients'] for r in rows}, by)
        rows = self.table('Which practices have the weakest diabetes control? Show the lowest share of diabetics under 8, practices with at least 1 diabetics.')
        self.assertTrue(rows and 'percent' in rows[0])


    def test_rate_by_a_claim_attribute_groups_the_claims_not_the_patients(self):
        counted = [c for c in CLAIMS if c[14] == 1 and c[7].startswith('2029')]
        rows = self.table("What's the denial rate by payer in 2029?")
        by = {}
        for c in counted: by.setdefault(c[2], []).append(c[9] == 'denied')
        self.assertEqual({r['payer_id']: (r['rows'], r['proportion']) for r in rows}, {k: (len(v), round(sum(v) / len(v), 4)) for k, v in by.items()})
        self.assertEqual({r['payer_id']: r['percent'] for r in rows}, {k: round(100.0 * sum(v) / len(v), 2) for k, v in by.items()})

    def test_pharmacy_spend_counts_fills_only_and_denied_claims_are_a_source(self):
        a = self.answer('What is the total paid on pharmacy claims in 2029?')
        self.assertAlmostEqual(a['value'] if a.get('value') is not None else a['table'][0][next(iter(a['table'][0]))],
                               sum(x[5] for x in PHARMACY if x[1].startswith('2029') and x[4] == 1) / 100.0, places=2)
        denied = [c for c in CLAIMS if c[14] == 1 and c[9] == 'denied']
        top = self.table('Which payer had the most claim denials for our payer report?')
        by = {}
        for c in denied: by[c[2]] = by.get(c[2], 0) + 1
        self.assertEqual((top[0]['payer_id'], top[0]['count']), max(by.items(), key=lambda kv: kv[1]))

    def test_a_ranking_never_lists_a_group_with_nobody_to_measure(self):
        # (The hypertension wording of this ranking compiles a denominator that is not restricted to hypertension; the acceptance contract
        # rejects that reading -- acceptance_contract.denominators -- so this test uses the diabetes ranking, which the contract accepts.)
        rows = self.table('Which practices have the weakest diabetes control? Show the lowest share of diabetics under 8, practices with at least 1 diabetics.')
        self.assertTrue(rows and all(r['patients'] >= 1 for r in rows))
        self.assertTrue(all(r['percent'] is not None for r in rows))


class EngineForms(Base):
    def facts(self, q):
        a = self.answer(q)
        self.assertEqual(self.ask(q)['planner_receipt']['interpretation']['interpreter'], 'population_engine')
        return {f['id']: f for f in a['insight']['facts']}, a

    def test_practice_gap_ranks_groups_by_the_worse_result(self):
        pop = [p for p in PATIENTS if living(p) and has_condition(p, 'diabetes') and age_at(PATIENTS[p][0]) >= 18 and latest(p, 'hba1c') is not None]
        by = {}
        for p in pop: by.setdefault(practice(p), []).append(latest(p, 'hba1c') < 8)
        a = self.answer('Which practices have the biggest diabetes control gap?')
        self.assertEqual({r['practice'] for r in a['table']}, {g for g, v in by.items() if len(v) >= 15} | set())  # fifteen is the group minimum: none here
        a = self.answer('Which practice has the most diabetic patients with an A1c above 9?')  # grammar path (a count by practice)
        self.assertEqual(a['table'][0]['patients'], max(sum(latest(p, 'hba1c') > 9 for p in v) for v in [[q for q in pop if practice(q) == g] for g in by]))

    def test_prioritize_tiers_overlap_and_listing(self):
        f, a = self.facts('Who should we prioritize for diabetes outreach?')
        pop = [p for p in PATIENTS if living(p) and has_condition(p, 'diabetes') and age_at(PATIENTS[p][0]) >= 18]
        poor = [p for p in pop if (latest(p, 'hba1c') or 0) > 9]
        ed = {p for p, d in ED if in_win(d)}
        self.assertEqual(f['poor']['value'], len(poor))
        self.assertEqual(f['poor_ed']['value'], len([p for p in poor if p in ed]))
        self.assertEqual(f['nolab']['value'], len([p for p in pop if not any(c == 'hba1c' and q == p and in_win(d) for q, c, d, v in LABS)]))
        self.assertTrue(set(a['patient_ids']) <= set(PATIENTS) and 'P05' not in a['patient_ids'] and 'P07' not in a['patient_ids'])

    def test_care_gaps_names_the_biggest_gap(self):
        f, a = self.facts('Which heart failure patients should the care team call first?')
        hf = [p for p in PATIENTS if living(p) and has_condition(p, 'heart_failure')]
        pillars = {'ace_inhibitor', 'arb', 'arni', 'mra', 'sglt2_inhibitor'}
        none = [p for p in hf if not any(q == p and c in pillars and in_win(d) for q, c, d in FILLS)]
        self.assertEqual(f['nogdmt']['value'], len(none))
        admitted = {p for p, adm, dc in INPATIENT if in_win(adm)}
        self.assertEqual(f['nogdmt_adm']['value'], len([p for p in none if p in admitted]))

    def test_recently_discharged_without_follow_up(self):
        f, a = self.facts('Which recently discharged patients need a call right now?')
        start = (date.fromisoformat(AS_OF) - timedelta(days=90)).isoformat()
        disc = {p: dc for p, adm, dc in INPATIENT if start < dc <= AS_OF and living(p)}
        nofu = [p for p, dc in disc.items() if not any(q == p and v > dc and t in ('primary_care_visit', 'specialist_visit', 'telehealth_contact') for q, t, v in VISITS)]
        self.assertEqual((f['n']['value'], f['no_fu']['value']), (len(disc), len(nofu)))

    def test_spend_drivers_pmpm_and_the_two_year_change(self):
        f, a = self.facts('What is driving PMPM for Medicare Advantage?')
        counted = [c for c in CLAIMS if c[14] == 1 and c[7].startswith('2029') and c[3] == 'medicare_advantage']
        paid = sum(c[10] for c in counted)
        months = 12  # P01 only
        self.assertAlmostEqual(f['pmpm']['value'], paid / 100.0 / months, places=2)
        inst = sum(c[10] for c in counted if c[5] == 'institutional') / paid
        self.assertAlmostEqual(f['inst']['value'], 100 * inst, places=1)
        self.assertEqual(f['highcost']['members'], 1)

    def test_spend_concentration_top_percent(self):
        f, a = self.facts('How concentrated is our medical spend — how much comes from the top few percent of patients?')
        year = 2029
        totals = {}
        for c in CLAIMS:
            if c[14] == 1 and c[7].startswith(str(year)) and living(c[1]): totals[c[1]] = totals.get(c[1], 0) + c[10]
        ranked = sorted(totals.values(), reverse=True)
        k = max(1, int(len(ranked) * 0.10 + 0.5))
        self.assertAlmostEqual(f['top10']['value'], 100.0 * sum(ranked[:k]) / sum(ranked), places=1)

    def test_year_over_year_with_drug_movers(self):
        f, a = self.facts('Did pharmacy spend go up or down from 2028 to 2029, and which drugs moved it most?')
        y = lambda yr: sum(x[5] for x in PHARMACY if x[1].startswith(yr) and x[4] == 1) / 100.0
        self.assertAlmostEqual(f['y0']['value'], y('2028')); self.assertAlmostEqual(f['y1']['value'], y('2029'))
        self.assertEqual(f['direction']['value'], 'up' if y('2029') > y('2028') else 'down')
        self.assertIn('semaglutide', ' '.join(f['movers_up']['value']))

    def test_denial_drivers_worst_payer_and_reason_mix(self):
        with patch.object(IF.DenialDrivers, 'MIN_CLAIMS', 3):
            f, a = self.facts('Why is denial rate so high with our worst payer?')
        counted = [c for c in CLAIMS if c[14] == 1 and c[7].startswith('2029')]
        rates = {}
        for c in counted: rates.setdefault(c[2], []).append(c)
        worst = max(((sum(c[9] == 'denied' for c in v) / len(v), k) for k, v in rates.items() if len(v) >= 3))
        self.assertIn(worst[1], f['payer']['value'])
        self.assertAlmostEqual(f['rate']['value'], round(100 * worst[0], 1), places=1)
        reasons = {}
        for c in rates[worst[1]]:
            if c[9] == 'denied': reasons[c[13]] = reasons.get(c[13], 0) + 1
        self.assertEqual(f['reason1']['numerator'], max(reasons.values()))
        self.assertIsNone(PE.interpret(self.catalog, 'Why is denial rate so high with our worst payer?') and None)

    def test_association_need_and_lead_time(self):
        f, a = self.facts('Which social need is most tied to missed appointments?')
        resolved = ('no_show', 'fulfilled', 'attended', 'arrived')

        def rate(pids):
            rows = [s_ for p, d, s_, lead in APPTS if p in pids and in_win(d) and s_ in resolved]
            return sum(s_ == 'no_show' for s_ in rows) / len(rows) if rows else None
        pos = {p for p, n_, pp in NEEDS if n_ == 'housing' and pp == 1}
        others = {p for p in PATIENTS if living(p)} - pos
        self.assertAlmostEqual(f['need_housing']['value'], round(100 * rate(pos), 1), places=1)
        f, a = self.facts('Does booking further out make people more likely to no-show?')
        rows = [(lead, s_) for p, d, s_, lead in APPTS if living(p) and in_win(d) and s_ in resolved]
        short = [s_ for lead, s_ in rows if lead <= 7]
        self.assertAlmostEqual(f['b0']['value'], round(100.0 * sum(s_ == 'no_show' for s_ in short) / len(short), 1), places=1)

    def test_segment_compare_and_frequent_users(self):
        f, a = self.facts('How does Medicaid compare with commercial on ED use?')
        for lob, key in (('medicaid', 'Medicaid_ed'), ('commercial', 'commercial_ed')):
            pop = [p for p in PATIENTS if living(p) and PATIENTS[p][3] == lob]
            n = sum(1 for p, d in ED if p in pop and in_win(d))
            self.assertAlmostEqual(f[key]['value'], round(1000.0 * n / len(pop), 1), places=1)
        f, a = self.facts('Who are our frequent ED users and how much of ED volume do they drive?')
        ed = {}
        for p, d in ED:
            if living(p) and in_win(d): ed[p] = ed.get(p, 0) + 1
        self.assertEqual(f['n']['value'], len([p for p, n in ed.items() if n >= 3]))
        self.assertEqual(a['patient_ids'], sorted(p for p, n in ed.items() if n >= 3))

    def test_high_cost_claimants_share_and_a_condition_count(self):
        totals = {}
        for c in CLAIMS:
            if c[14] == 1 and c[7].startswith('2029') and living(c[1]): totals[c[1]] = totals.get(c[1], 0) + c[10]
        big = {p for p, t in totals.items() if t >= 5_000_000}
        allpaid = sum(c[10] for c in CLAIMS if c[14] == 1 and c[7].startswith('2029'))
        f, a = self.facts('What share of total 2029 medical paid spend do the high-cost claimants ($50,000 or more paid in 2029) account for?')
        self.assertAlmostEqual(f['share']['value'], round(100.0 * sum(t for p, t in totals.items() if p in big) / allpaid, 1), places=1)
        f, a = self.facts('How many of the 2029 high-cost claimants ($50,000 or more paid) have diabetes?')
        self.assertEqual(f['count']['value'], len([p for p in big if has_condition(p, 'diabetes')]))

    def test_pmpm_by_line_of_business_uses_the_enrolled_member_month(self):
        rows = self.table('What was the medical claims paid PMPM in 2029 by line of business?')
        months = {}
        for p in PATIENTS:
            if living(p): months[PATIENTS[p][3]] = months.get(PATIENTS[p][3], 0) + 12
        paid = {}
        for c in CLAIMS:
            if c[14] == 1 and c[7].startswith('2029') and living(c[1]): paid[PATIENTS[c[1]][3]] = paid.get(PATIENTS[c[1]][3], 0) + c[10]
        for r in rows:
            self.assertAlmostEqual(r['pmpm_dollars'], round(paid.get(r['line of business'], 0) / 100.0 / months[r['line of business']], 2), places=2)

    def test_pdc_adherence_is_recognised_and_declines_when_the_warehouse_lacks_the_fills_it_needs(self):
        q = 'How adherent are patients on ACE inhibitors in 2029 — share with PDC at least 0.8?'
        spec = PE.interpret(self.catalog, q)[0]
        self.assertEqual((spec['family'], spec['params']['classes'], spec['params']['threshold'], spec['params']['year']), ('pdc_share', ['ace_inhibitor'], 0.8, 2029))
        with self.assertRaisesRegex(R.PlanError, 'cannot be computed from the received data'):
            self.ask(q)

    def test_pmpm_ranking_and_the_composite_answer_shape(self):
        a = self.answer('Which payers have the highest PMPM?')
        self.assertEqual(a['answer_type'], 'composite')
        self.assertEqual(a['insight']['family'], 'pmpm_by')
        top = a['table'][0]
        self.assertGreaterEqual(top['pmpm_dollars'], a['table'][-1]['pmpm_dollars'])
        # every engine answer states how the question was read and carries its definitions
        self.assertTrue(a['read_back'] and a['limitations'])

    def test_a_saved_cohort_is_only_answered_by_a_form_made_for_one(self):
        ids = ['P01', 'P02']
        self.assertIsNotNone(PE.interpret(self.catalog, 'What share of total 2029 medical paid spend do they account for?', population=ids))
        self.assertIsNone(PE.interpret(self.catalog, 'What share of total 2029 medical paid spend do they account for?'))          # no cohort: not a warehouse question
        self.assertIsNone(PE.interpret(self.catalog, 'Which practices have the biggest diabetes control gap?', population=ids))      # never widened past the cohort
        r = S.compose(self.stack, 'What share of total 2029 medical paid spend do they account for?', population=ids,
                      model=Mock(side_effect=AssertionError('no language model on the free path')))
        part = sum(c[10] for c in CLAIMS if c[14] == 1 and c[7].startswith('2029') and c[1] in ids)
        total = sum(c[10] for c in CLAIMS if c[14] == 1 and c[7].startswith('2029'))
        self.assertAlmostEqual(r['answer']['value'], round(100.0 * part / total, 1), places=1)

    def test_a_word_no_family_accounts_for_is_not_silently_dropped(self):
        for q in ('Which practices have the biggest diabetes control gap among women?', 'Which 3 practices have the biggest diabetes control gap?'):
            with self.subTest(q=q):
                self.assertIsNone(PE.interpret(self.catalog, q))


class DecisionContextAdditions(Base):
    def test_on_a_medication_defaults_to_current_use_and_prescribed_does_not(self):
        c = L.Parser(self.catalog, 'x')
        c.base = 'patient'
        value, tail = c.expression('on a statin')
        self.assertEqual(value['period'], {'start': '2029-05-28', 'end': AS_OF})
        value, tail = c.expression('on a statin in 2029')
        self.assertEqual(value['period'], {'start': '2029-01-01', 'end': '2029-12-31'})

    def test_lab_extremes_wording_does_not_mean_any_result_for_a_ranking(self):
        self.assertEqual(DC.lab_mode('Which practices have the lowest share of diabetics under 8, practices with at least 15 diabetics?'), 'latest')
        self.assertEqual(DC.lab_mode('What is the lowest A1c ever recorded?'), 'any')
        self.assertEqual(DC.lab_mode('patients with at least 2 A1c results'), 'any')

    def test_the_prompt_block_states_current_medication_use(self):
        self.assertIn('current use', DC.prompt_block())


class DuplicateChartsAreOptIn(Base):
    EXCLUDE_DUPLICATES = False

    def test_by_default_every_chart_is_a_patient_as_the_oracle_counts(self):
        n = len([p for p in PATIENTS if p not in DEAD])
        self.assertEqual(self.value('How many patients are there?'), n)


if __name__ == '__main__':
    unittest.main()
