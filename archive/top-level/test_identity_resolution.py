"""Reversible identity resolution: contradiction checks, split / reject / confirm / undo ledger, constrained recompute."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import identity_resolution as IR, record_linkage as RL, engine_suite as E
from clinical_core.agere.clinical.analysis_graph import validate as graph_validate
from clinical_core.agere.clinical.capability_registry import PRODUCT
from clinical_core.agere.clinical.graph_planner import CONTRACTS
from clinical_core.agere.clinical import graph_contracts
from test_record_linkage import fixture

AS_OF = '2030-05-27'


def stack(root):
    fixture(root)
    se = sqlite3.connect(Path(root) / 'semantic.sqlite3')
    se.executescript("ALTER TABLE patient ADD COLUMN sex_reported TEXT; ALTER TABLE patient ADD COLUMN birth_date TEXT;"
                     "CREATE TABLE death(patient_id TEXT, death_date TEXT); CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, organization_id TEXT);"
                     "CREATE TABLE visit(patient_id TEXT, event_date TEXT);")
    rows = {'P-1': ('F', '1948-08-15'), 'P-2': ('F', '1948-08-15'), 'P-3': ('M', '1960-02-03'), 'P-4': ('M', '1960-03-02'),
            'P-5': ('F', '1975-05-05'), 'P-6': ('M', '1975-05-05'), 'P-7': ('F', '1990-01-01'), 'P-8': ('M', '1955-07-07')}
    for pid, (sex, dob) in rows.items(): se.execute('UPDATE patient SET sex_reported=?, birth_date=? WHERE patient_id=?', (sex, dob, pid))
    se.commit(); se.close()
    return Path(root)


class Contradictions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = stack(self.tmp.name)

    def test_member_id_merge_of_a_woman_and_a_man_is_flagged_not_split(self):
        rep = IR.execute(self.path, None, {'output': 'review'}, AS_OF)
        kinds = {(r['chart_a'], r['chart_b'], r['conflict']) for r in rep['table']}
        self.assertEqual(kinds, {('P-5', 'P-6', 'sex_conflict')})
        self.assertEqual(rep['proposals'][0]['groups'], [['P-5'], ['P-6']])
        self.assertEqual(rep['ledger_events'], 0)               # nothing split automatically
        self.assertIn('P-5', RL.enterprise_person_ids(self.path))  # still linked until a person decides

    def test_keying_error_birth_date_is_not_a_conflict(self):
        f = lambda sex, dob: {'sex': sex, 'birth_date': dob, 'death': None, 'last_care': None, 'stays': []}
        self.assertEqual(IR.pair_conflicts(f('M', '1960-02-03'), f('M', '1960-03-02')), [])     # month/day swap
        self.assertEqual(IR.pair_conflicts(f('M', '1960-02-03'), f('M', '1960-02-04')), [])     # one digit
        self.assertEqual([c['type'] for c in IR.pair_conflicts(f('M', '1960-02-03'), f('M', '1971-09-21'))], ['birth_date_conflict'])

    def test_same_stay_under_two_organisation_keys_is_not_a_contradiction(self):
        a = {'sex': 'F', 'birth_date': None, 'death': None, 'last_care': None, 'stays': [('2029-07-23', '2029-07-24', 'WHN-HOSPITAL-GA-0')]}
        b = {'sex': 'F', 'birth_date': None, 'death': None, 'last_care': None, 'stays': [('2029-07-23', '2029-07-24', '1018288179')]}  # the same admission keyed by NPI
        self.assertEqual(IR.pair_conflicts(a, b), [])

    def test_lifetime_and_overlapping_stays(self):
        a = {'sex': 'F', 'birth_date': None, 'death': '2029-01-10', 'last_care': '2029-01-05', 'stays': [('2028-03-01', '2028-03-10', 'H1')]}
        b = {'sex': 'F', 'birth_date': None, 'death': None, 'last_care': '2029-06-01', 'stays': [('2028-03-08', '2028-03-12', 'H2')]}
        self.assertEqual({c['type'] for c in IR.pair_conflicts(a, b)}, {'lifetime_conflict', 'overlapping_stays'})
        b['last_care'] = '2029-02-01'; b['stays'] = [('2028-03-10', '2028-03-12', 'H2'), ('2028-03-02', '2028-03-04', 'H1')]
        self.assertEqual(IR.pair_conflicts(a, b), [])           # within 30 days of death; back-to-back; same organisation


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = stack(self.tmp.name)

    def persons(self):
        return RL.execute(self.path, None, {'output': 'summary'}, AS_OF)

    def test_split_recomputes_and_undo_restores(self):
        before = self.persons()
        ev = IR.append(self.path, 'split', actor='reviewer', reason='P-6 is a different (male) patient', person_id='AGP-P-5', groups=[['P-5'], ['P-6']])
        after = self.persons()
        self.assertEqual(before['persons_with_duplicates'] - after['persons_with_duplicates'], 1)
        self.assertNotIn('P-5', RL.enterprise_person_ids(self.path))
        self.assertEqual(IR.execute(self.path, None, {'output': 'review'}, AS_OF)['conflicts'], 0)
        IR.append(self.path, 'undo', actor='reviewer', reason='split was wrong', target=ev['id'])
        restored = self.persons()
        self.assertEqual(restored['patient_ids'], before['patient_ids']); self.assertEqual(RL.enterprise_person_ids(self.path)['P-6'], 'AGP-P-5')
        self.assertEqual(IR.execute(self.path, None, {'output': 'review'}, AS_OF)['conflicts'], 1)

    def test_split_holds_against_a_third_chart_bridging_the_pair(self):
        scored = RL.build(self.path)
        bridge = {'a': 'P-5', 'b': 'P-9', 'weight': 20.0, 'levels': {}, 'weights': {}, 'shared_member_id': False, 'shared_mrn': False}
        bridge2 = {'a': 'P-6', 'b': 'P-9', 'weight': 20.0, 'levels': {}, 'weights': {}, 'shared_member_id': False, 'shared_mrn': False}
        scored = {**scored, 'pairs': scored['pairs'] + [bridge, bridge2]}
        plain, _ = IR.decide(scored, 15.0, None)
        self.assertEqual(plain['P-5'], plain['P-6'])
        over = {'reject': [('P-5', 'P-6')], 'confirm': []}
        split, _ = IR.decide(scored, 15.0, over)
        self.assertNotEqual(split.get('P-5'), split.get('P-6'))   # transitive closure through P-9 may not re-merge them

    def test_confirm_links_two_charts_regardless_of_score(self):
        IR.append(self.path, 'confirm', actor='reviewer', reason='same person per registration desk', a='P-7', b='P-8')
        self.assertEqual(RL.enterprise_person_ids(self.path)['P-8'], 'AGP-P-7')
        rep = RL.execute(self.path, None, {'output': 'pairs'}, AS_OF)
        self.assertIn('confirmed', {r['rule'] for r in rep['rows']})

    def test_ledger_is_append_only_hash_chained_and_validated(self):
        IR.append(self.path, 'reject', actor='a', reason='r', a='P-1', b='P-2')
        e2 = IR.append(self.path, 'confirm', actor='a', reason='r', a='P-3', b='P-4')
        self.assertEqual(IR.verify_ledger(self.path), (True, None))
        f = self.path / IR.LEDGER
        lines = f.read_text().splitlines(); first = json.loads(lines[0]); first['reason'] = 'rewritten'; lines[0] = json.dumps(first, sort_keys=True)
        f.write_text('\n'.join(lines) + '\n')
        self.assertEqual(IR.verify_ledger(self.path), (False, 0))
        for bad in (dict(kind='split', person_id='X', groups=[['a']]), dict(kind='reject', a='x', b='x'), dict(kind='undo', target='ID-9999'), dict(kind='nope')):
            with self.assertRaises(ValueError): IR.append(self.path, bad.pop('kind'), actor='a', reason='r', **bad)
        with self.assertRaises(ValueError): IR.append(self.path, 'confirm', actor='', reason='r', a='x', b='y')
        self.assertEqual(IR.overrides(self.path)['confirm'], [('P-3', 'P-4')]); self.assertTrue(e2['id'])


class Engine(unittest.TestCase):
    def test_wiring_and_routes(self):
        self.assertIn('identity_resolution', E.MODULES)
        self.assertIn('identity_resolution', PRODUCT); self.assertIn('identity_resolution', CONTRACTS); self.assertIn('identity_resolution', graph_contracts.TYPED)
        for q, out in (('Which linked patient charts are probably false merges?', 'review'), ('Show identity contradictions in the linked charts', 'review'),
                       ('List the recorded identity split decisions and check the ledger', 'ledger'),
                       ('How many effective enterprise persons are there after the splits?', 'persons')):
            g, _ = E.compile_route(q, {}, AS_OF)
            self.assertEqual(g['nodes'][0]['operation'], 'identity_resolution'); self.assertEqual(g['nodes'][0]['parameters']['output'], out, q)
            graph_validate(g, AS_OF)
        g, _ = E.compile_route('How many duplicate patient records do we have?', {}, AS_OF) or (None, None)
        self.assertIsNone(g)  # plain duplicate counts stay with record_linkage

    def test_answers(self):
        with tempfile.TemporaryDirectory() as d:
            path = stack(d)
            a = IR.answer(IR.execute(path, None, {'output': 'review'}, AS_OF), {'coverage': 'supported_observation'}, 'q')
            self.assertIn('hard contradiction', a['value']); self.assertEqual(a['count'], 1)
            a = IR.answer(IR.execute(path, None, {'output': 'ledger'}, AS_OF), {'coverage': 'supported_observation'}, 'q')
            self.assertIn('intact', a['value'])


if __name__ == '__main__':
    unittest.main()
