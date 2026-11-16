"""Warehouse onboarding: code-system profiling, matcher precision on fixtures with known truth, transformation
correctness, approval gate, invariants that catch planted errors, and the OMOP-shaped round trip against the
native semantic build (skipped when the synthetic warehouse / native build are not on this machine).
No hosted model and no local encoder are used here (lexical matcher only) to keep CPU light."""
import json
import shutil
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from clinical_core.agere.clinical import semantic_layer
from clinical_core.agere.onboarding import (apply as apply_mod, codes, connectors, contract, fixtures, fixtures_claims_lake,
                                            matcher, omop_fixture, profile, verify)

ROOT = Path(__file__).resolve().parent


def _pipeline(path, truth=None):
    conn = connectors.SQLiteConnector(path)
    prof = profile.profile_source(conn)
    model = contract.build_model()
    mapping = matcher.propose(model, prof)
    score = matcher.score_against_truth(mapping, truth) if truth else None
    return conn, prof, model, mapping, score


def _approved(mapping):
    m = json.loads(json.dumps(mapping))
    m['approval'] = {'approved': True, 'reviewer': 'unit test'}
    return m


class CodeSystems(unittest.TestCase):
    def test_validators(self):
        self.assertTrue(all(codes.loinc_valid(c) for c in ('4548-4', '2345-7', '2160-0', '718-7', '13457-7')))
        self.assertFalse(codes.loinc_valid('4548-3'))
        self.assertTrue(codes.npi_valid('1234567893'))
        self.assertFalse(codes.npi_valid('1234567890'))
        self.assertEqual(codes.icd10_normalize('e11319'), 'E11.319')
        self.assertEqual(codes.icd10_normalize('I10'), 'I10')
        self.assertEqual(codes.ndc_normalize('0093-1048-01'), '00093104801')
        self.assertEqual(codes.parse_date('2028-03-04 00:00:00'), '2028-03-04')
        self.assertEqual(codes.parse_date('03/04/2028'), '2028-03-04')
        self.assertEqual(codes.parse_date('20280304'), '2028-03-04')
        self.assertIsNone(codes.parse_date('2028-02-30'))

    def test_column_code_detection(self):
        c = lambda vals, toks=(): codes.classify_values(vals, toks)['system']
        self.assertEqual(c(['E11.9', 'I10', 'N18.3', 'Z79.4']), 'ICD-10-CM')
        self.assertEqual(c(['E11319', 'I10', 'E785', 'N183'], ['dx', 'code']), 'ICD-10-CM')
        self.assertEqual(c(['4548-4', '2345-7', '2160-0']), 'LOINC')
        self.assertEqual(c(['1234567893', '1245319599']), 'NPI')
        self.assertEqual(c(['00093104801', '68180051301']), 'NDC')
        self.assertEqual(c(['99213', 'G0438', '93000'], ['proc', 'code']), 'CPT/HCPCS')
        self.assertEqual(c(['861007', '314076'], ['rxcui']), 'RxNorm')
        self.assertIsNone(c(['861007', '314076'], ['quantity']))            # bare integers need a drug-code hint
        self.assertIsNone(c(['Z100200', 'Z100213', 'Z100226'], ['pat', 'id']))  # one-letter-prefixed IDs are not ICD-10
        self.assertIsNone(c(['62701', '62744'], ['zip']))                   # ZIP is not CPT
        self.assertEqual(codes.classify_semantic([12.5, 100.25, 33.0], 'real', ['paid', 'amount']), 'money_dollars')
        self.assertEqual(codes.classify_semantic([1250, 33000], 'integer', ['charge', 'cents']), 'money_cents')
        self.assertEqual(codes.classify_semantic([1, 2, 3], 'integer', ['cost', 'id']), 'integer')  # an id is never money
        self.assertEqual(codes.classify_semantic(['2028-01-02 09:30:00'], 'text', ['result', 'date']), 'datetime')

    def test_profile_reports_codes_money_keys_joins(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'src.sqlite'
            fixtures.clarity_like(path, n_patients=12)
            conn = connectors.SQLiteConnector(path)
            prof = profile.profile_source(conn)
            conn.close()
        t = prof['tables']
        self.assertEqual(t['PAT_ENC_DX']['columns']['ICD10_CODE']['code']['system'], 'ICD-10-CM')
        self.assertEqual(t['ORDER_RESULTS']['columns']['COMPONENT_LOINC']['code']['system'], 'LOINC')
        self.assertEqual(t['ORDER_MED']['columns']['RXCUI']['code']['system'], 'RxNorm')
        self.assertEqual(t['CLARITY_SER']['columns']['NPI']['code']['system'], 'NPI')
        self.assertIn(t['AP_CLAIM_PX']['columns']['PROC_CODE']['code']['system'], ('CPT', 'CPT/HCPCS'))
        self.assertEqual(t['AP_CLAIM']['columns']['TOT_BILLED_AMT']['semantic'], 'money_dollars')
        self.assertEqual(t['PAT_ENC']['columns']['CONTACT_DATE']['semantic'], 'datetime')
        self.assertIsNone(t['PATIENT']['columns']['PAT_ID']['code']['system'])
        self.assertIn('PAT_ID', t['PATIENT']['candidate_keys'])
        joins = {(j['from'], j['to']) for j in prof['joins']}
        self.assertIn(('PAT_ENC.PAT_ID', 'PATIENT.PAT_ID'), joins)
        self.assertIn(('AP_CLAIM_PX.CLM_ID', 'AP_CLAIM.CLM_ID'), joins)


class Matching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        fixtures.clarity_like(d / 'clarity.sqlite')
        fixtures_claims_lake.claims_lake(d / 'lake.sqlite')
        cls.clarity = _pipeline(d / 'clarity.sqlite', fixtures.TRUTH)
        cls.lake = _pipeline(d / 'lake.sqlite', fixtures_claims_lake.TRUTH)

    @classmethod
    def tearDownClass(cls):
        cls.clarity[0].close(); cls.lake[0].close(); cls.tmp.cleanup()

    def test_clarity_like_precision_recall(self):
        _, _, _, m, s = self.clarity
        self.assertGreaterEqual(s['precision'], 0.95, s['wrong'])
        self.assertGreaterEqual(s['recall'], 0.90, s['missed'])
        for entity, (table, _) in fixtures.TRUTH.items():
            self.assertEqual(m['entities'][entity]['source_table'], table, entity)
        self.assertEqual(m['unmapped_source_tables'], fixtures.UNMAPPED_TABLES)  # diagnosis master is not patient conditions

    def test_claims_lake_precision_recall(self):
        _, _, _, m, s = self.lake
        self.assertGreaterEqual(s['precision'], 0.90, s['wrong'])
        self.assertGreaterEqual(s['recall'], 0.85, s['missed'])
        self.assertIsNone(m['entities']['visit']['source_table'])  # a claims lake has no encounter table
        self.assertIn('cpt_ref', m['unmapped_source_tables'])

    def test_proposed_transforms_and_evidence(self):
        _, _, _, m, _ = self.clarity
        ops = lambda e, f: [t['op'] for t in m['entities'][e]['fields'][f].get('transforms', [])]
        self.assertIn('dollars_to_cents', ops('claim', 'billed_cents'))
        self.assertIn('to_date', ops('visit', 'event_date'))
        self.assertEqual(m['entities']['patient']['fields']['sex_reported']['transforms'][0]['map'], {'Female': 'F', 'Male': 'M'})
        self.assertEqual(m['entities']['claim']['fields']['status']['transforms'][0]['map'], {'Paid': 'paid', 'Denied': 'denied'})
        f = m['entities']['measurement']['fields']['loinc']
        self.assertIn('LOINC', f['evidence']['value_evidence'])
        self.assertFalse(m['approval']['approved'])
        _, _, _, lake, _ = self.lake
        visit_fk = lake['entities']['claim']['fields']['patient_id']['transforms']
        self.assertEqual([t for t in visit_fk if t['op'] == 'lookup'][0]['value'], 'member_key')  # surrogate -> source member id
        self.assertEqual(lake['entities']['claim']['versioning']['rule'], 'claim_settlement')

    def test_hosted_model_hook_is_off_by_default(self):
        _, prof, model, _, _ = self.clarity
        self.assertFalse(matcher.HOSTED_MODEL['enabled'])
        with self.assertRaises(RuntimeError):
            matcher.propose(model, prof, hosted_model=lambda prompt: 'x')

    def test_mapping_round_trips_through_yaml(self):
        _, _, _, m, _ = self.clarity
        with tempfile.TemporaryDirectory() as d:
            p = matcher.save(m, Path(d) / 'mapping.yaml')
            self.assertEqual(matcher.load(p)['entities'], m['entities'])


class Transforms(unittest.TestCase):
    def test_transform_operations(self):
        st = Counter()
        T = lambda op, v: apply_mod._transform(op, v, st, 'k', {('person', 'person_id', 'person_source_value'): {'7': 'P-7'}})
        self.assertEqual(T({'op': 'dollars_to_cents'}, 19.99), 1999)
        self.assertEqual(T({'op': 'dollars_to_cents'}, 0.1 + 0.2), 30)
        self.assertEqual(T({'op': 'dollars_to_cents'}, '1,000'), None)
        self.assertEqual(st['k:unparseable'], 1)
        self.assertEqual(T({'op': 'to_date'}, '2028-03-04T10:00:00+00:00'), '2028-03-04')
        self.assertEqual(T({'op': 'icd10_normalize'}, 'e1165'), 'E11.65')
        self.assertEqual(T({'op': 'ndc_normalize'}, '68180-513-01'), '68180051301')
        self.assertEqual(T({'op': 'value_map', 'map': {'female': 'F'}}, 'female'), 'F')
        self.assertEqual(T({'op': 'to_text'}, 12.0), '12')
        self.assertEqual(T({'op': 'lookup', 'table': 'person', 'key': 'person_id', 'value': 'person_source_value'}, 7), 'P-7')
        self.assertIsNone(T({'op': 'lookup', 'table': 'person', 'key': 'person_id', 'value': 'person_source_value'}, 8))
        self.assertEqual(st['k:lookup_miss'], 1)

    def test_apply_requires_approval_and_builds_semantic_layer(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            fixtures.clarity_like(d / 'c.sqlite')
            conn, _, model, m, _ = _pipeline(d / 'c.sqlite')
            with self.assertRaises(apply_mod.NotApproved):
                apply_mod.apply(m, conn, d / 'sem.sqlite3', model)
            unsigned = json.loads(json.dumps(m)); unsigned['approval']['approved'] = True
            with self.assertRaises(apply_mod.NotApproved):  # approval needs a reviewer
                apply_mod.apply(unsigned, conn, d / 'sem.sqlite3', model)
            self.assertFalse((d / 'sem.sqlite3').exists())
            r = apply_mod.apply(_approved(m), conn, d / 'sem.sqlite3', model)
            src = sqlite3.connect(d / 'c.sqlite')
            out = sqlite3.connect(d / 'sem.sqlite3')
            self.assertEqual(r['rows']['visit'], src.execute('SELECT count(*) FROM PAT_ENC').fetchone()[0])
            self.assertEqual(out.execute('SELECT sum(billed_cents) FROM claim').fetchone()[0],
                             round(src.execute('SELECT sum(TOT_BILLED_AMT) FROM AP_CLAIM').fetchone()[0] * 100))
            self.assertEqual({r[0] for r in out.execute('SELECT DISTINCT sex_reported FROM patient')}, {'F', 'M'})
            self.assertEqual({r[0] for r in out.execute('SELECT DISTINCT final_status FROM claim')}, {'paid', 'denied'})
            self.assertEqual(out.execute('SELECT count(*) FROM claim_line WHERE patient_id IS NULL').fetchone()[0], 0)  # via claim
            self.assertGreater(out.execute("SELECT count(*) FROM measurement WHERE concept = 'hba1c' AND value_canonical > 0").fetchone()[0], 0)
            self.assertGreater(out.execute('SELECT count(*) FROM onboarding_lineage').fetchone()[0], 50)
            src.close(); out.close(); conn.close()


class Invariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.d = Path(cls.tmp.name)
        fixtures.clarity_like(d / 'c.sqlite')
        cls.conn, _, cls.model, m, _ = _pipeline(d / 'c.sqlite')
        # The reviewer's edit: the matcher proposed ORDER_MED.DESCRIPTION for drug_name below the accept threshold;
        # the completeness invariant fails until a human accepts it (see test_unaccepted_required_field_fails).
        f = m['entities']['medication']['fields']['drug_name']
        if not f.get('source'):
            f.update({'source': f['alternatives'][0]['column'], 'transforms': []})
        f['accept'] = True
        cls.mapping = _approved(m)
        apply_mod.apply(cls.mapping, cls.conn, d / 'clean.sqlite3', cls.model)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close(); cls.tmp.cleanup()

    def _planted(self, name, *sql):
        target = self.d / f'{name}.sqlite3'
        shutil.copy(self.d / 'clean.sqlite3', target)
        db = sqlite3.connect(target)
        for s in sql: db.execute(s)
        db.commit(); db.close()
        return verify.invariants(target, self.mapping, self.conn, model=self.model)

    def test_clean_build_passes(self):
        r = verify.invariants(self.d / 'clean.sqlite3', self.mapping, self.conn, model=self.model)
        self.assertTrue(r['passed'], r['failed'])

    def test_planted_errors_are_caught(self):
        cases = {
            'duplicate': (["INSERT INTO claim_event SELECT * FROM claim_event LIMIT 1"],
                          {'key_uniqueness.claim_event.record_id', 'row_counts.claim'}),
            'orphan': (["UPDATE condition SET patient_id = 'NOT-A-PATIENT' WHERE rowid = 1"], {'referential.condition.patient_id'}),
            'bad_code': (["UPDATE condition SET icd10 = 'XYZ' WHERE rowid IN (SELECT rowid FROM condition LIMIT 40)"],
                         {'code_validity.condition.icd10'}),
            'future_date': (["UPDATE measurement SET event_date = '2199-01-01' WHERE rowid = 1"], {'date_sanity.measurement.event_date'}),
            'end_before_start': (["UPDATE visit SET end_date = '1999-01-01' WHERE end_date IS NOT NULL AND rowid IN (SELECT rowid FROM visit WHERE end_date IS NOT NULL LIMIT 1)"],
                                 {'date_sanity.visit.end_before_start'}),
            'money_drift': (["UPDATE claim_event SET payer_paid_cents = payer_paid_cents + 5000 WHERE rowid = 1"],
                            {'money.claim.payer_paid_cents'}),
            'lost_rows': (["DELETE FROM measurement WHERE rowid = 1"], {'row_counts.measurement'}),
        }
        for name, (sql, expected) in cases.items():
            r = self._planted(name, *sql)
            self.assertFalse(r['passed'], name)
            self.assertTrue(expected <= set(r['failed']), (name, r['failed']))

    def test_missing_dollars_to_cents_is_caught(self):
        m = json.loads(json.dumps(self.mapping))
        for f in m['entities']['claim']['fields'].values():
            f['transforms'] = [t for t in f.get('transforms', []) if t['op'] != 'dollars_to_cents']
        apply_mod.apply(m, self.conn, self.d / 'nocents.sqlite3', self.model)
        r = verify.invariants(self.d / 'nocents.sqlite3', m, self.conn, model=self.model)
        self.assertIn('money.claim.billed_cents', r['failed'])
        self.assertIn('money.claim.billed_scale_plausible', r['failed'])

    def test_unaccepted_required_field_fails(self):
        m = json.loads(json.dumps(self.mapping))
        m['entities']['medication']['fields']['drug_name']['accept'] = False
        apply_mod.apply(m, self.conn, self.d / 'nodrug.sqlite3', self.model)
        r = verify.invariants(self.d / 'nodrug.sqlite3', m, self.conn, model=self.model)
        self.assertEqual(r['failed'], ['completeness.medication.drug_name'])

    def test_unmapped_required_field_is_caught(self):
        m = json.loads(json.dumps(self.mapping))
        m['entities']['claim_line']['fields']['service_code']['accept'] = False
        apply_mod.apply(m, self.conn, self.d / 'nocode.sqlite3', self.model)
        r = verify.invariants(self.d / 'nocode.sqlite3', m, self.conn, model=self.model)
        self.assertIn('completeness.claim_line.service_code', r['failed'])


class Contract(unittest.TestCase):
    def test_semantic_model_file_is_current(self):
        on_disk = json.loads((ROOT / 'config' / 'agere' / 'semantic_model.json').read_text())
        self.assertEqual(on_disk, json.loads(json.dumps(contract.build_model())))

    def test_contract_columns_exist_in_semantic_layer(self):
        model = contract.build_model()
        self.assertEqual(model['derived_from']['semantic_layer_version'], semantic_layer.VERSION)
        cols = contract._ddl_columns()
        for table, extra in contract._altered_columns().items():
            cols.setdefault(table, {}).update(extra)
        for name, e in contract.targets(model).items():
            if not e['table']: continue
            self.assertIn(e['table'], cols)
            for field, spec in e['fields'].items():
                if spec['column']: self.assertIn(field, cols[e['table']], f'{name}.{field}')
        # Every onboarding-target table carries its semantic-layer keys and the patient link.
        for name in ('visit', 'condition', 'measurement', 'medication', 'claim', 'claim_line'):
            self.assertIn('patient_id', model['entities'][name]['fields'])


WAREHOUSE_CFG = ROOT / 'config' / 'agere' / 'warehouse_v20.json'


def _round_trip_inputs():
    try:
        cfg = json.loads(WAREHOUSE_CFG.read_text())
        w = Path(cfg['snapshot']['source_dataset']['source']); n = Path(cfg['build']) / 'stack' / 'semantic.sqlite3'
        return (w, n) if w.is_file() and n.is_file() else None
    except (OSError, KeyError, ValueError):
        return None


@unittest.skipUnless(_round_trip_inputs(), 'synthetic warehouse v2.0 and its native semantic build are not on this machine')
class OmopRoundTrip(unittest.TestCase):
    def test_omop_shaped_source_reproduces_native_aggregates(self):
        warehouse, native = _round_trip_inputs()
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            ids = omop_fixture.sample_patients(warehouse, 25, seed='unit-test')
            omop_fixture.build(warehouse, d / 'omop.sqlite', ids)
            conn, _, model, m, score = _pipeline(d / 'omop.sqlite', omop_fixture.TRUTH)
            self.assertGreaterEqual(score['precision'], 0.95, score['wrong'])
            self.assertGreaterEqual(score['recall'], 0.95, score['missed'])
            self.assertEqual(m['unmapped_source_tables'], omop_fixture.UNMAPPED_TABLES)
            m = _approved(m)
            apply_mod.apply(m, conn, d / 'sem.sqlite3', model)
            inv = verify.invariants(d / 'sem.sqlite3', m, conn, model=model)
            self.assertTrue(inv['passed'], inv['failed'])
            rec = verify.reconcile(d / 'sem.sqlite3', verify.native_extract(native, ids, d / 'native.sqlite3'))
            bad = [r for r in rec['aggregates'] if not r['within_tolerance']]
            self.assertFalse(bad, bad)
            self.assertEqual(rec['acceptance_matched'], len(rec['acceptance']))
            conn.close()


if __name__ == '__main__':
    unittest.main()
