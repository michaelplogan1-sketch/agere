"""Prediction exporter for the sealed v2.8 evaluation scorer, and the Agere modules it adds (synthetic fixtures only).

Pure-function tests for linkage, suspects, HCC recapture, SDOH corroboration, planned admissions and the contract
validator; an end-to-end export over a tiny received store (semantic layer built from records) checks that every file
is written, validates and carries provenance.
"""
import json, shutil, sqlite3, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path

from clinical_core.agere.clinical import (acute_episodes, chronic_conditions, condition_suspects, eval_contract, hcc_capture, measures,
                                          person_linkage as pl, sdoh_needs, semantic_layer, therapy_courses)
from test_aggregate_explain import build
from tools import eval_predictions


def ident(pid, name, dob, sex='F', addr='1 Elm St', zip_='01000', phone='4135550100', members=()):
    x = pl.identity(pid, {'display_name': name, 'birth_date': dob, 'sex_reported': sex, 'address_line': addr, 'postal_code': zip_, 'phone': phone})
    x['member_ids'] = set(members)
    return x


class Linkage(unittest.TestCase):
    def crowd(self, n=600):
        """A background population (u-probabilities are estimated from the data, so the crowd must be realistic in size)."""
        first = ['Mary', 'John', 'Linda', 'James', 'Karen', 'Robert', 'Nancy', 'Paul', 'Lisa', 'Mark', 'Betty', 'Gary', 'Donna', 'Larry', 'Carol',
                 'Frank', 'Susan', 'Peter', 'Joan', 'Henry', 'Alice', 'Victor', 'Grace', 'Oscar']
        last = ['Smith', 'Brown', 'Davis', 'Wilson', 'Moore', 'Taylor', 'White', 'Harris', 'Clark', 'Lewis', 'Young', 'King', 'Scott', 'Green',
                'Adams', 'Baker', 'Nelson', 'Hill', 'Ramirez', 'Campbell', 'Mitchell', 'Roberts', 'Carter', 'Phillips', 'Evans']
        d0 = date(1935, 1, 1)
        return [ident(f'X{i:04d}', f'{first[i % 24]} {last[(i * 7) % 25]}', (d0 + timedelta(days=37 * i + i % 11)).isoformat(),
                      'F' if i % 2 else 'M', f'{100 + i} Oak Ave', f'0{1000 + i % 90}', f'617555{i:04d}') for i in range(n)]

    def test_string_tools(self):
        self.assertAlmostEqual(pl.jaro_winkler('MARTHA', 'MARHTA'), 0.961, places=3)
        self.assertEqual(pl.soundex('Robert'), 'R163'); self.assertEqual(pl.soundex('Rupert'), 'R163')
        self.assertEqual(pl.split_name('Evans, Dorothy M'), ('DOROTHY', 'EVANS'))
        self.assertEqual(pl.split_name('Dorothy Evans-Cole'), ('DOROTHY', 'EVANSCOLE'))
        self.assertEqual(pl.split_name('J. R. Smith Jr'), ('J', 'SMITH'))
        self.assertEqual(pl.norm_address('745 Center Street Apartment 4'), '745 CENTER ST APT 4')
        self.assertEqual(pl._dob_level('1950-03-07', '1950-07-03'), 'partial')
        self.assertEqual(pl._dob_level('1950-03-07', '1951-03-07'), 'partial')
        self.assertEqual(pl._dob_level('1950-03-07', '1962-11-21'), 'disagree')

    def test_married_name_duplicate_links_and_neighbours_do_not(self):
        people = self.crowd() + [ident('A1', 'Dorothy Evans', '1948-08-15', addr='745 Center St', zip_='01059', phone='4135550197'),
                                 ident('A2', 'Dorothy Kowalski', '1948-08-15', addr='12 Pine Rd', zip_='01059', phone='4135550111'),
                                 ident('N1', 'Helen Ortiz', '1971-02-02', addr='20 Main St', zip_='03000', phone='6035550001'),
                                 ident('N2', 'Helen Ortiz', '1985-09-30', addr='22 Main St', zip_='03000', phone='6035550002')]
        assign, report, links = pl.link(people)
        self.assertEqual(assign.get('A1'), 'AGP-A1'); self.assertEqual(assign.get('A2'), 'AGP-A1')
        self.assertNotIn('N1', assign)  # same name, neighbouring house, different birth date: two people
        self.assertEqual(report['persons'], 1)

    def test_shared_member_id_with_compatible_birth_date_is_deterministic(self):
        people = self.crowd() + [ident('M1', 'Kim Lo', '1960-01-02', members={'P1|123'}), ident('M2', 'Kimberly Law', '1960-02-01', addr='9 Bay Rd',
                                                                                              zip_='09999', phone='', members={'P1|123'})]
        assign, _, links = pl.link(people)
        self.assertEqual(assign['M1'], assign['M2'])
        assign2, _, _ = pl.link(people, {'member_id_link': False, 'threshold': 40})
        self.assertNotIn('M1', assign2)


    def test_shared_facility_mrn_links_with_compatible_birth_date(self):
        a = ident('R1', 'Steven Gonzalez', '1947-03-17', addr='4014 Elm St', zip_='92602', phone='7275550179')
        b = ident('R2', 'Steven Allen', '1947-03-17', addr='1345 River St', zip_='92602', phone='2365550124')
        c = ident('R3', 'Maria Lopez', '1990-01-01', addr='1 Bay Rd', zip_='92602', phone='7275550111')
        a['mrns'] = {'HOSP-1|L-123'}; b['mrns'] = {'HOSP-1|L-123'}; c['mrns'] = {'HOSP-1|L-123'}
        assign, _, links = pl.link(self.crowd() + [a, b, c], {'threshold': 40})
        self.assertEqual(assign['R1'], assign['R2'])
        self.assertNotIn('R3', assign)                                 # same MRN, different birth date: not linked
        self.assertEqual({l['rule'] for l in links}, {'mrn'})
        assign2, _, _ = pl.link(self.crowd() + [a, b], {'threshold': 40, 'mrn_link': False})
        self.assertNotIn('R1', assign2)


class Conditions(unittest.TestCase):
    def test_claims_confirmation_rule(self):
        rows = [('p', 'E119', '2027-01-05', 'claim'), ('p', 'E119', '2027-04-05', 'claim'),  # two claim dates -> E11
                ('p', 'I10', '2027-01-05', 'claim'),                                         # one claim date -> not confirmed
                ('p', 'J449', '2027-02-01', 'claim_inpatient'),                              # inpatient -> J44
                ('p', 'M170', None, 'ehr')]                                                  # problem list -> M17
        doc = chronic_conditions.documented(rows, 2)
        self.assertEqual(sorted(doc['p']), ['E11', 'J44', 'M17'])
        self.assertIn('I10', chronic_conditions.documented(rows, 1)['p'])

    def test_visit_diagnoses_count_as_encounters(self):
        # an EHR visit diagnosis is an encounter fact like a claim line, not problem-list confirmation (CCW two dates)
        rows = [('p', 'K0510', '2027-03-01', 'visit'),                                  # one dental visit -> not K05
                ('p', 'I10', '2027-01-05', 'visit'), ('p', 'I10', '2027-01-05', 'claim'),  # visit + its claim: one date
                ('p', 'E785', '2027-01-05', 'visit'), ('p', 'E785', '2027-06-05', 'claim'),  # two dates across sources
                ('p', 'J449', '2027-02-01', 'visit_inpatient'),                         # EHR admission -> J44
                ('q', 'K0510', '2027-03-01', 'ehr')]                                    # problem list -> K05
        doc = chronic_conditions.documented(rows, 2)
        self.assertEqual(sorted(doc['p']), ['E78', 'J44'])
        self.assertEqual(sorted(doc['q']), ['K05'])

    def test_acute_and_symptom_codes_never_chronic_from_encounters(self):
        rows = [('p', 'S069XAA', '2027-01-01', 'claim'), ('p', 'S069XAA', '2027-02-01', 'visit'), ('p', 'S069XAA', '2027-01-01', 'claim_inpatient'),
                ('p', 'A419', '2027-03-01', 'claim_inpatient'), ('p', 'M79609', '2027-01-01', 'visit'), ('p', 'M79609', '2027-05-01', 'visit'),
                ('p', 'M797', '2027-01-01', 'visit'), ('p', 'M797', '2027-05-01', 'claim'),     # fibromyalgia is chronic: two dates -> M79
                ('q', 'S069XAA', '2027-01-01', 'ehr'), ('q', 'M79609', '2027-01-01', 'ehr')]  # a problem-list entry still documents
        doc = chronic_conditions.documented(rows, 2)
        self.assertEqual(sorted(doc['p']), ['M79'])
        self.assertEqual(doc['p']['M79']['codes'], {'M797', 'M79609'})
        self.assertEqual(sorted(doc['q']), ['M79', 'S06'])

    def test_osteoarthritis_needs_two_dates(self):
        # CCW RA/OA: two claims at least a day apart; an elective arthroplasty admission alone does not qualify
        rows = [('p', 'M1711', '2027-04-01', 'claim_inpatient'), ('p', 'M1711', '2027-04-01', 'claim'),
                ('q', 'M170', '2027-04-01', 'claim_inpatient'), ('q', 'M170', '2027-09-01', 'claim'),
                ('r', 'I5021', '2027-04-01', 'claim_inpatient')]                                # other categories: one stay confirms
        doc = chronic_conditions.documented(rows, 2)
        self.assertNotIn('p', doc)
        self.assertEqual(sorted(doc['q']), ['M17'])
        self.assertEqual(sorted(doc['r']), ['I50'])


class Suspects(unittest.TestCase):
    def test_hypertension_is_the_average_level(self):
        def bp(days):
            return ([(d, s, 'r') for d, s, _ in days], [(d, di, 'r') for d, _, di in days])
        normal = [(f'2027-{m:02d}-10', 124, 78) for m in range(1, 11)]
        spikes = [('2027-11-10', 152, 88), ('2027-12-10', 146, 92)]          # two high days among ten normal ones
        self.assertEqual(condition_suspects.hypertension_days(*bp(normal + spikes)), [])
        self.assertEqual(condition_suspects.hypertension_days(*bp(normal + spikes), {'hypertension_average': False}), ['2027-11-10', '2027-12-10'])
        high = [('2027-01-10', 150, 94), ('2027-03-10', 146, 92), ('2027-05-10', 136, 86)]
        self.assertEqual(condition_suspects.hypertension_days(*bp(high)), ['2027-01-10', '2027-03-10'])
        self.assertEqual(condition_suspects.hypertension_days(*bp(high[:1])), [])  # one occasion is not enough


class HCC(unittest.TestCase):
    people = {'p': {'birth_date': '1950-06-01', 'sex': 2}}

    def test_chronic_carry_forward_hierarchy_and_acute_exclusion(self):
        rows = [('p', 'E119', '2027-03-01', 'claim'), ('p', 'I214', '2027-05-01', 'claim_inpatient'), ('p', 'E1122', '2028-02-01', 'ehr'),
                ('p', 'I5021', '2027-06-01', 'claim_inpatient')]
        res = hcc_capture.capture(rows, self.people, [2027, 2028, 2029], '2026-09-01', enrolled_years={'p': {2027, 2028, 2029}})
        self.assertIn(228, res[('p', 2027)]['hccs'])                   # acute MI in its own year
        self.assertNotIn(228, res[('p', 2028)]['hccs'])                # ... never carried
        self.assertEqual([h for h in res[('p', 2028)]['hccs'] if h in (36, 37, 38)], [37])  # carried E11.9 sits under current E11.22
        self.assertIn(226, res[('p', 2028)]['hccs'])                   # acute HF carried as chronic HF
        self.assertEqual(res[('p', 2029)]['claims_hccs'], [])
        self.assertTrue({37, 226} <= set(res[('p', 2029)]['recaptured']))

    def test_scope_death_and_suspects(self):
        rows = [('p', 'E119', '2027-03-01', 'claim')]
        res = hcc_capture.capture(rows, self.people, [2027, 2028, 2029], '2026-09-01', enrolled_years={'p': {2027, 2028}},
                                  deaths={'p': '2028-06-01'}, suspects={'p': [{'code': 'N1831', 'first_evidence': '2028-02-01'}]})
        self.assertNotIn(('p', 2029), res)                             # not enrolled, no activity, dead
        self.assertNotIn(329, res[('p', 2027)]['hccs'])
        self.assertEqual(res[('p', 2028)]['suspected'], [329])
        res2 = hcc_capture.capture(rows, self.people, [2027, 2028], '2026-09-01', enrolled_years={'p': {2027, 2028}}, params={'carry_forward': False})
        self.assertNotIn(('p', 2028), res2)


class Episodes(unittest.TestCase):
    def test_planned_admission_rules(self):
        self.assertTrue(acute_episodes.planned('Z51.11', None))
        self.assertTrue(acute_episodes.planned('M17.11', '470'))
        self.assertFalse(acute_episodes.planned('S72.001A', '470'))
        self.assertFalse(acute_episodes.planned('I50.21', '291'))

    def test_readmission_orientation(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE utilization_event(patient_id, kind, event_date, end_date, sources)')
        db.executemany('INSERT INTO utilization_event VALUES (?,?,?,?,?)', [
            ('p', 'inpatient', '2028-01-01', '2028-01-05', 'claim'), ('p', 'inpatient', '2028-01-20', '2028-01-22', 'claim'),
            ('p', 'inpatient', '2028-01-22', '2028-01-25', 'claim'), ('p', 'ed_visit', '2028-03-01', None, 'claim')])
        ev = acute_episodes.episodes(db)
        flags = [(e['start'], e['unplanned_readmission_30d']) for e in ev if e['kind'] == 'inpatient']
        self.assertEqual(flags, [('2028-01-01', False), ('2028-01-20', True), ('2028-01-22', False)])  # same-day = transfer
        idx = [(e['start'], e['unplanned_readmission_30d']) for e in acute_episodes.episodes(db, orientation='index') if e['kind'] == 'inpatient']
        self.assertEqual(idx[0], ('2028-01-01', True))


class SDOH(unittest.TestCase):
    def test_corroboration_scores(self):
        self.assertEqual(sdoh_needs.score({'z_code': {'2028-01-01'}}), 2)
        self.assertEqual(sdoh_needs.score({'vendor': {'x'}}), 1)
        self.assertEqual(sdoh_needs.score({'screen': {'a', 'b', 'c'}}), 2)
        self.assertEqual(sdoh_needs.score({'screen': {'a'}, 'vendor': {'x'}}), 2)
        self.assertEqual(sdoh_needs.score({'work_answer': {'a'}}), 2)
        self.assertEqual(sdoh_needs.score({'z_code_weak': {'2028-01-01'}}), 1)              # Z56.9 once
        self.assertEqual(sdoh_needs.score({'z_code_weak': {'2028-01-01', '2028-05-01', '2029-01-01'}}), 2)  # on separate dates


class Contract(unittest.TestCase):
    def test_rows(self):
        ok = {'conditions': {'patient_id': 'p', 'icd10': 'E11', 'basis': 'suspect'}, 'hcc': {'patient_id': 'p', 'year': 2028, 'hccs': [37, 'HCC38']},
              'quality_measures': {'patient_id': 'p', 'year': 2028, 'measure': 'PDC-STA', 'eligible': True, 'met': False, 'pdc': 0.5},
              'acute_events': {'patient_id': 'p', 'kind': 'ed', 'start': '2028-02-29'},
              'medications': {'patient_id': 'p', 'drug_class': 'statin', 'year': 2028, 'pdc': 1, 'therapy_event': 'initiation', 'first_fill': '2028-03-01'},
              'deaths': {'patient_id': 'p', 'date_of_death': '2029-01-01'}, 'duplicates': {'patient_id': 'p', 'person_id': 'AGP-p'},
              'sdoh': {'patient_id': 'p', 'needs': ['food']}}
        for name, r in ok.items(): self.assertEqual(eval_contract.check_row(name, r), [], name)
        self.assertTrue(eval_contract.check_row('quality_measures', {**ok['quality_measures'], 'measure': 'GSD'}))
        self.assertTrue(eval_contract.check_row('acute_events', {**ok['acute_events'], 'start': '2028-02-30'}))
        self.assertTrue(eval_contract.check_row('medications', {**ok['medications'], 'pdc': 1.2}))
        self.assertTrue(eval_contract.check_row('sdoh', {'patient_id': 'p', 'needs': ['money']}))
        self.assertTrue(eval_contract.check_row('conditions', {'patient_id': 'p', 'icd10': 'diabetes'}))


TOY_U = {'first': 0.01, 'last': 0.005, 'birth_date': 0.0001, 'sex': 0.5, 'address': 0.001, 'postal': 0.02, 'phone': 0.0005}


def _monthly(pid, name, start, n, stream='pharmacy_dispenses'):
    d0 = date.fromisoformat(start)
    return [(pid, stream, {'medication_name': name, 'status': 'dispensed', 'days_supply_reported': 30,
                           'event_date': (d0 + timedelta(days=30 * i)).isoformat()}) for i in range(n)]


class EndToEnd(unittest.TestCase):
    """A tiny received store: a claims-only diabetic (payer claims), an EHR patient with lab-only diabetes, a married-name
    duplicate chart, a death record, ED visit and readmission, social screens and Z codes."""
    @classmethod
    def setUpClass(cls):
        def payer_claim(pid, cid, day, dx, tob=None, pos='11', lines=('99214',)):
            out = [(pid, 'payer_medical_claims', {'payer_claim_id': cid, 'member_id': 'M-' + pid, 'payer_id': 'PAY1', 'product': 'medicare_advantage',
                                                   'claim_type': 'institutional' if tob else 'professional', 'type_of_bill': tob, 'place_of_service': None if tob else pos,
                                                   'service_from': day, 'service_to': day, 'admission_date': day if tob else None,
                                                   'discharge_date': (date.fromisoformat(day) + timedelta(days=3)).isoformat() if tob else None,
                                                   'principal_diagnosis': dx[0], 'diagnosis_codes': list(dx), 'claim_status': 'paid',
                                                   'adjudication_sequence': 1, 'claim_frequency_code': '1', 'event_date': day})]
            out += [(pid, 'payer_medical_claim_lines', {'payer_claim_id': cid, 'line_number': i + 1, 'procedure_code': c, 'procedure_code_system': 'CPT',
                                                        'service_date': day, 'event_date': day}) for i, c in enumerate(lines)]
            return out
        patients = {'c1': {'age_reported': 70, 'sex_reported': 'F', 'birth_date': '1958-04-02', 'display_name': 'Rosa Diaz', 'address_line': '5 Elm St',
                           'postal_code': '02111', 'phone': '617-555-0101', 'data_segment': 'claims_only'},
                    'c2': {'age_reported': 70, 'sex_reported': 'F', 'birth_date': '1958-04-02', 'display_name': 'Rosa Martinez', 'address_line': '80 Oak Ave',
                           'postal_code': '02111', 'phone': '617-555-0199', 'data_segment': 'claims_only'},
                    'e1': {'age_reported': 61, 'sex_reported': 'M', 'birth_date': '1967-09-09', 'display_name': 'Tom Hale', 'address_line': '9 Pine Rd',
                           'postal_code': '01060', 'phone': '413-555-0102', 'data_segment': 'claims_plus_ehr'},
                    'd1': {'age_reported': 80, 'sex_reported': 'M', 'birth_date': '1948-01-01', 'display_name': 'Ed Ames', 'address_line': '1 Main St',
                           'postal_code': '01002', 'phone': '413-555-0103', 'data_segment': 'deep_chart'}}
        recs = [('c1', 'enrollment_spans', {'member_id': 'M-c1', 'payer_id': 'PAY1', 'product': 'medicare_advantage', 'coverage_start': '2026-09-01',
                                             'coverage_end': '2029-12-31', 'event_date': '2026-09-01'})]
        recs += payer_claim('c1', 'PC1', '2027-02-01', ['E11.9', 'I10']) + payer_claim('c1', 'PC2', '2027-05-01', ['E11.9'])
        recs += payer_claim('c1', 'PC3', '2028-03-01', ['I50.9'], tob='0111', lines=()) + payer_claim('c1', 'PC4', '2028-03-20', ['J18.9'], tob='0111', lines=())
        recs += payer_claim('c1', 'PC5', '2028-06-01', ['Z59.41', 'E11.9'])
        recs += [('c1', 'social_screens', {'instrument': 'AHC_HRSN', 'food_difficulty_reported': True, 'event_date': '2028-06-01'})]
        recs += _monthly('c1', 'Metformin 500 mg tablet', '2027-01-15', 30, 'pharmacy_dispenses')
        recs += [('e1', 'coverage', {'coverage_type': 'commercial', 'status': 'active', 'effective_start': '2026-01-01', 'event_date': '2026-01-01'}),
                 ('e1', 'labs', {'test': 'HbA1c', 'value': 6.9, 'unit': '%', 'event_date': '2027-04-01', 'loinc': '4548-4'}),
                 ('e1', 'labs', {'test': 'HbA1c', 'value': 7.1, 'unit': '%', 'event_date': '2027-10-01', 'loinc': '4548-4'}),
                 ('e1', 'labs', {'test': 'HbA1c', 'value': 7.0, 'unit': '%', 'event_date': '2028-10-01', 'loinc': '4548-4'}),
                 ('e1', 'encounters', {'type': 'emergency', 'event_date': '2028-08-08', 'organization_id': 'H1'}),
                 ('e1', 'encounters', {'type': 'emergency', 'event_date': '2026-11-03', 'organization_id': 'H1'}),
                 ('e1', 'encounters', {'type': 'emergency', 'event_date': '2030-02-10', 'organization_id': 'H1'}),
                 ('e1', 'social_screens', {'instrument': 'AHC_HRSN', 'transport_difficulty_reported': True, 'event_date': '2028-01-01'})]
        recs += _monthly('e1', 'Atorvastatin 20 mg tablet', '2027-06-01', 10)
        recs += [('d1', 'death_records', {'date_of_death': '2029-02-03', 'source': 'payer_enrollment_death_indicator', 'event_date': '2029-02-03'}),
                 ('d1', 'conditions', {'description': 'Hypertension', 'icd10': 'I10', 'event_date': '2027-01-01'})]
        cls.tmp = Path(tempfile.mkdtemp())
        stack = build(recs, patients)
        semantic_layer.build(stack)
        cls.build = cls.tmp / 'build'
        cls.build.mkdir()
        shutil.move(str(stack), str(cls.build / 'stack'))
        cls.out = cls.tmp / 'out'
        # four charts cannot estimate u-probabilities: state them (typical values for a regional population)
        cls.prov, cls.validation = eval_predictions.export(cls.build, cls.out, '2030-05-27', cfg={'linkage': {'u_override': TOY_U}}, log=lambda *a: None)
        cls.files = {n: [json.loads(l) for l in (cls.out / f'{n}.jsonl').read_text().splitlines()] for n in eval_contract.FILES}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_file_written_and_valid(self):
        self.assertTrue(self.validation['ok'], self.validation)
        self.assertEqual(set(self.prov['row_counts']), set(eval_contract.FILES))
        self.assertEqual(self.prov['paid_api_calls'], 0)
        mods = self.prov['code']['modules']
        self.assertIn('clinical_core.agere.clinical.person_linkage', mods)
        self.assertTrue(all(len(m['sha256']) == 64 for m in mods.values()))
        self.assertEqual(self.prov['producers']['hcc'][0], 'clinical_core.agere.clinical.hcc_capture')

    def test_conditions_documented_and_suspect(self):
        c = {(r['patient_id'], r['icd10']): r['basis'] for r in self.files['conditions']}
        self.assertEqual(c[('c1', 'E11')], 'documented')             # three claim dates
        self.assertNotIn(('c1', 'I10'), c)                            # one claim date: unconfirmed
        self.assertEqual(c[('c1', 'I50')], 'documented')              # inpatient claim
        self.assertEqual(c[('e1', 'E11')], 'suspect')                 # two HbA1c >= 6.5 on different dates, no diagnosis
        self.assertEqual(c[('d1', 'I10')], 'documented')              # deep charts are in scope for conditions

    def test_hcc_recapture_and_population_scope(self):
        h = {(r['patient_id'], r['year']): set(r['hccs']) for r in self.files['hcc']}
        self.assertIn(38, h[('c1', 2027)])
        self.assertTrue({38, 226} <= h[('c1', 2029)])                 # no 2029 claims: chronic diabetes and HF recaptured
        self.assertFalse(any(p == 'd1' for p, _ in h))                # deep chart excluded from population-only tasks

    def test_acute_events_and_readmission(self):
        a = {(r['patient_id'], r['kind'], r['start']): r for r in self.files['acute_events']}
        self.assertIn(('e1', 'ed', '2028-08-08'), a)
        self.assertIn(('e1', 'ed', '2026-11-03'), a)                  # every event from the feed start to as_of ...
        self.assertIn(('e1', 'ed', '2030-02-10'), a)                  # ... not only the 2027-2029 quality years
        self.assertFalse(a[('c1', 'inpatient', '2028-03-01')]['unplanned_readmission_30d'])
        self.assertTrue(a[('c1', 'inpatient', '2028-03-20')]['unplanned_readmission_30d'])

    def test_quality_and_medications(self):
        q = {(r['patient_id'], r['year'], r['measure']): r for r in self.files['quality_measures']}
        self.assertTrue(q[('c1', 2028, 'PDC-DR')]['eligible'])
        self.assertGreater(q[('c1', 2028, 'PDC-DR')]['pdc'], 0.9)    # diabetes denominator from payer claim diagnoses
        self.assertFalse(q[('c1', 2028, 'HBD')]['met'])                # claims-only: no HbA1c received
        m = {(r['patient_id'], r['drug_class'], r['year']): r for r in self.files['medications']}
        self.assertEqual(m[('e1', 'statin', 2027)]['therapy_event'], 'initiation')
        self.assertEqual(m[('e1', 'statin', 2027)]['first_fill'], '2027-06-01')
        self.assertNotIn('therapy_event', m[('c1', 'metformin', 2027)])  # first fill within the washout: maybe prevalent

    def test_deaths_duplicates_sdoh(self):
        self.assertEqual(self.files['deaths'], [{'patient_id': 'd1', 'date_of_death': '2029-02-03'}])
        d = {r['patient_id']: r['person_id'] for r in self.files['duplicates']}
        self.assertEqual(d.get('c1'), d.get('c2')); self.assertIsNotNone(d.get('c1'))
        s = {r['patient_id']: r['needs'] for r in self.files['sdoh']}
        self.assertEqual(s.get('c1'), ['food'])                       # Z59.41 + positive screen
        self.assertNotIn('e1', s)                                     # one positive screen only


class MeasurePatientStatus(unittest.TestCase):
    def test_patient_rows_carry_pdc(self):
        recs = [('a', 'coverage', {'coverage_type': 'medicare', 'status': 'active', 'effective_start': '2027-01-01', 'event_date': '2027-01-01'})]
        recs += _monthly('a', 'Metformin 500 mg tablet', '2028-01-01', 12)
        path = build(recs, {'a': {'age_reported': 70, 'sex_reported': 'F', 'birth_date': '1958-01-01'}})
        semantic_layer.build(path)
        db = sqlite3.connect(f'file:{path}/semantic.sqlite3?mode=ro', uri=True)
        rows, _ = measures.patient_status(db, measures.library()['pdc_diabetes'], '2028-01-01', '2028-12-31', '2029-08-15')
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['met']); self.assertAlmostEqual(rows[0]['pdc'], 360 / 366, places=3)
        shutil.rmtree(path, ignore_errors=True)



def _fills_db(fills, stops=(), listed=()):
    """In-memory store with received fills [(pid, day, name, cls, supply)], note stops [(pid, day, cls, target)] and
    discontinued medication-list entries [(pid, day, name)]."""
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE medication(record_id, patient_id, event_date, drug_name, drug_class, evidence, status, fill_reversed)')
    db.execute('CREATE TABLE record(record_id, payload)')
    for i, (pid, day, name, cls, supply) in enumerate(fills):
        db.execute('INSERT INTO medication VALUES (?,?,?,?,?,?,?,0)', (f'R{i}', pid, day, name, cls, 'payer_claim', 'paid'))
        db.execute('INSERT INTO record VALUES (?,?)', (f'R{i}', json.dumps({'days_supply': supply})))
    db.execute('CREATE TABLE note_therapy_change(patient_id, event_date, action, drug_class, target)')
    db.executemany("INSERT INTO note_therapy_change VALUES (?,?,'stop',?,?)", stops)
    db.execute('CREATE TABLE medication_list_entry(patient_id, last_listed, name, list_status)')
    db.executemany("INSERT INTO medication_list_entry VALUES (?,?,?,'discontinued')", listed)
    return db


def _every(pid, name, cls, start, n, step=30, supply=30):
    d0 = date.fromisoformat(start)
    return [(pid, (d0 + timedelta(days=step * i)).isoformat(), name, cls, supply) for i in range(n)]


class TherapyEvents(unittest.TestCase):
    YEARS, AS_OF, FEED = [2027, 2028, 2029], '2030-05-27', '2026-09-01'

    def events(self, db):
        return {(r['patient_id'], r['drug_class'], r['therapy_event']): r['first_fill']
                for r in therapy_courses.courses(db, self.YEARS, self.AS_OF, self.FEED) if r.get('therapy_event')}

    def test_washout_course_gap_and_first_fill(self):
        fills = (_every('n', 'atorvastatin 20 mg tablet', 'statin', '2027-06-01', 6)            # new user: initiation
                 + _every('n', 'atorvastatin 20 mg tablet', 'statin', '2028-01-20', 6)          # 2-month lapse: same course
                 + _every('v', 'lisinopril 10 mg tablet', 'ace_inhibitor', '2026-09-05', 30)    # under way at the data start
                 + _every('v', 'amlodipine 5 mg tablet', 'calcium_channel_blocker', '2026-10-01', 30)  # prevalent too: no add-on
                 + _every('v', 'hydrochlorothiazide 25 mg tablet', '', '2028-04-11', 10)         # a real add-on, class from the name
                 + _every('b', 'sertraline 50 mg tablet', 'ssri', '2027-05-01', 3)
                 + _every('b', '24 HR bupropion hydrochloride 150 MG', '', '2027-05-20', 6))     # joins within the regimen window
        ev = self.events(_fills_db(fills))
        self.assertEqual(ev[('n', 'statin', 'initiation')], '2027-06-01')
        self.assertNotIn(('n', 'statin', 'restart'), ev)                                   # a refill lapse is not a restart
        self.assertFalse(any(k[0] == 'v' and k[1] in ('ace_inhibitor', 'calcium_channel_blocker') for k in ev))
        self.assertEqual(ev[('v', 'thiazide_diuretic', 'add_on')], '2028-04-11')
        self.assertEqual(ev[('b', 'NDRI', 'initiation')], '2027-05-20')                    # own first fill; canonical class name
        self.assertEqual(ev[('b', 'ssri', 'initiation')], '2027-05-01')

    def test_restart_needs_a_documented_stop(self):
        fills = (_every('s', 'metformin 500 mg tablet', 'metformin', '2027-04-01', 5)
                 + _every('s', 'metformin 500 mg tablet', 'metformin', '2028-02-15', 6)         # resumes after a noted stop
                 + _every('g', 'metformin 500 mg tablet', 'metformin', '2027-04-01', 5)
                 + _every('g', 'metformin 500 mg tablet', 'metformin', '2028-02-15', 6))        # same pattern, no documented stop
        ev = self.events(_fills_db(fills, stops=[('s', '2027-09-02', 'metformin', 'metformin')]))
        self.assertEqual(ev[('s', 'metformin', 'restart')], '2028-02-15')
        self.assertNotIn(('g', 'metformin', 'restart'), ev)
        ev2 = self.events(_fills_db(fills, listed=[('g', '2027-08-30', 'metformin 500 mg tablet daily')]))
        self.assertEqual(ev2[('g', 'metformin', 'restart')], '2028-02-15')                 # discontinued list entry is a stop

    def test_course_episodes_split_only_at_stops_in_a_lapse(self):
        days = {date(2027, 1, 1).toordinal() + 30 * i: 30 for i in range(4)}
        days[date(2027, 7, 1).toordinal()] = 30
        stop_inside = [date(2027, 3, 10).toordinal()]                                     # while still supplied: no split
        stop_lapse = [date(2027, 5, 15).toordinal()]
        self.assertEqual(len(therapy_courses.course_episodes(days, 180, stop_inside)), 1)
        self.assertEqual(len(therapy_courses.course_episodes(days, 180, stop_lapse)), 2)


class KedClaimEvidence(unittest.TestCase):
    def db(self, payer_lines, client_lines=(), results=()):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE payer_claim(payer_claim_id, final_status, service_from)')
        db.execute('CREATE TABLE payer_claim_line(payer_claim_id, patient_id, service_date, cpt, hcpcs, counts_in_metrics)')
        db.execute('CREATE TABLE claim_line(patient_id, service_date, cpt, hcpcs)')
        db.execute('CREATE TABLE measurement(patient_id, event_date, loinc, concept, value_canonical, value)')
        for i, (pid, day, cpt, status) in enumerate(payer_lines):
            db.execute('INSERT INTO payer_claim VALUES (?,?,?)', (f'C{i}', status, day))
            db.execute('INSERT INTO payer_claim_line VALUES (?,?,?,?,NULL,1)', (f'C{i}', pid, day, cpt))
        db.executemany('INSERT INTO claim_line VALUES (?,?,?,NULL)', client_lines)
        db.executemany('INSERT INTO measurement VALUES (?,?,?,NULL,NULL,NULL)', results)
        return measures.Context(db)

    def test_hedis_codes_and_pairing(self):
        ked = measures.library()['ked']
        egfr, uacr = (next(c for c in ked['numerator']['criteria'] if c['concept'] == k) for k in ('egfr', 'uacr'))
        ctx = self.db([('a', '2028-03-01', '80048', 'paid'), ('a', '2028-03-01', '82043', 'paid'),   # payer never bills 82570
                       ('d', '2028-03-01', '82043', 'denied')],
                      client_lines=[('b', '2028-04-01', '82043'), ('b', '2028-04-03', '82570'),          # billing itemizes 82570
                                    ('c', '2028-04-01', '82043'), ('e', '2028-06-01', '82043')],
                      results=[('e', '2028-06-02', '2161-8')])                                          # urine creatinine result
        rows = {k: measures._rows(ctx, c)[0] for k, c in (('egfr', egfr), ('uacr', uacr))}
        self.assertIn('a', rows['egfr'])                                    # basic metabolic panel reports an eGFR
        self.assertEqual({p for p in rows['uacr']}, {'a', 'b', 'e'})        # c: 82043 without its creatinine; d: denied
        self.assertIn('not itemized', rows['uacr']['a'][0][2])
        anchors = {'period_start': '2028-01-01', 'period_end': '2028-12-31'}
        self.assertTrue(measures._evaluate(ctx, uacr, 'b', anchors)[0])
        self.assertFalse(measures._evaluate(ctx, uacr, 'c', anchors)[0])


class PdcClassFromName(unittest.TestCase):
    def test_unclassed_pioglitazone_counts_in_pdc_dr(self):
        db = _fills_db(_every('t', 'pioglitazone 30 MG Oral Tablet', '', '2028-01-05', 12) + _every('u', 'pioglitazone 30 MG Oral Tablet', 'statin', '2028-01-05', 3))
        c = next(c for c in measures.library()['pdc_diabetes']['numerator']['criteria'] if c['type'] == 'pdc')
        self.assertIn('thiazolidinedione', c['classes'])                    # PQA PDC-DR includes TZDs
        rows, _ = measures._rows(measures.Context(db), c)
        self.assertEqual(len(rows['t']), 12)
        self.assertNotIn('u', rows)                                         # a stated class is never overridden


if __name__ == '__main__':
    unittest.main()
