"""Medication state macro: fills, list and notes reconciled into one governed state per patient x class/pillar."""
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from clinical_core.agere.clinical import general_analysis as G, medication_state as MS, request_plan as R, shared_planner as S

AS_OF = '2030-05-27'
PILLAR_CLASSES = ['arni', 'hf_evidence_based_beta_blocker', 'mra', 'sglt2_inhibitor']


def build(path):
    """Synthetic warehouse; P-1007 mirrors the Brief 1.4 probe (all four pillars listed 2030-05-24, no fill since 2029-02-10)."""
    db = sqlite3.connect(Path(path)/'semantic.sqlite3')
    db.executescript('''
    CREATE TABLE meta(key, value);
    INSERT INTO meta VALUES('version','fixture'),('source_digest','medication-state-1'),('as_of','2030-05-27');
    CREATE TABLE patient(patient_id TEXT, birth_date TEXT);
    CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, payload TEXT, event_date TEXT);
    CREATE TABLE medication(record_id TEXT, patient_id TEXT, source_stream TEXT, evidence TEXT, drug_name TEXT, drug_class TEXT,
      status TEXT, event_date TEXT, fill_reversed INTEGER);
    CREATE TABLE note_therapy_change(record_id TEXT, patient_id TEXT, event_date TEXT, action TEXT, drug_class TEXT, evidence_text TEXT);
    CREATE TABLE therapy_barrier(patient_id TEXT, drug_class TEXT, gdmt_pillar TEXT, barrier_type TEXT, detail TEXT, event_date TEXT,
      last_date TEXT, record_id TEXT, assertion TEXT);
    ''')
    db.executemany('INSERT INTO patient VALUES(?,?)', [(p, '1950-01-01') for p in ('P-1007', 'P-2', 'P-3', 'P-4', 'P-5', 'P-6', 'P-7')])
    meds = []
    for i, cls in enumerate(PILLAR_CLASSES):
        meds += [(f'f{i}', 'P-1007', 'pharmacy_claims', 'claim', 'x', cls, 'paid', '2029-02-10', 0),
                 (f'l{i}', 'P-1007', 'medication_lists', 'listed', 'x', cls, 'active', '2030-05-24', 0)]
    meds += [('g1', 'P-2', 'pharmacy_claims', 'claim', 'dapagliflozin', 'sglt2_inhibitor', 'paid', '2030-03-20', 0),  # 90 days -> covered
             ('g2', 'P-3', 'pharmacy_claims', 'claim', 'spironolactone', 'mra', 'paid', '2030-01-01', 0),
             ('g3', 'P-4', 'pharmacy_claims', 'claim', 'losartan', 'arb', 'paid', '2030-05-01', 0),
             ('g4', 'P-5', 'pharmacy_claims', 'claim', 'spironolactone', 'mra', 'paid', '2030-05-20', 0),   # fill after the stop
             ('g5', 'P-6', 'pharmacy_claims', 'claim', 'carvedilol', 'hf_evidence_based_beta_blocker', 'paid', '2030-05-20', 1)]  # reversed
    db.executemany('INSERT INTO medication VALUES(?,?,?,?,?,?,?,?,?)', meds)
    db.execute("INSERT INTO record VALUES('g1','P-2',?,'2030-03-20')", (json.dumps({'days_supply': 90}),))
    db.executemany('INSERT INTO note_therapy_change VALUES(?,?,?,?,?,?)', [
        ('n1', 'P-3', '2030-02-01', 'stop', 'mra', 'Stop spironolactone, K 5.9'),
        ('n2', 'P-5', '2030-04-01', 'stop', 'mra', 'Hold spironolactone')])
    db.executemany('INSERT INTO therapy_barrier VALUES(?,?,?,?,?,?,?,?,?)', [
        ('P-4', 'arb', 'rasi', 'intolerance_side_effect', 'cough', '2030-05-10', None, 'b1', 'affirmed'),
        ('P-7', 'sglt2_inhibitor', 'sglt2i', 'allergy_angioedema', 'rash', None, None, 'b2', 'historical')])
    db.commit()
    return db


class States(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.db = build(self.path); self.addCleanup(self.db.close)
        self.rows = {(r['patient_id'], r['subject']): r for r in MS.classify(self.db, AS_OF, 'gdmt')}

    def test_listed_without_fills_is_listed_not_filled_with_dates_never_unknown(self):
        for pillar in MS.PILLARS:
            r = self.rows[('P-1007', pillar)]
            self.assertEqual(r['state'], 'listed_not_filled', pillar)
            self.assertEqual((r['last_fill_date'], r['last_listed_date']), ('2029-02-10', '2030-05-24'))
            self.assertIn('2030-05-24', r['basis'])

    def test_each_state_and_its_evidence(self):
        self.assertEqual(self.rows[('P-2', 'sglt2i')]['state'], 'filled_current')
        self.assertEqual(self.rows[('P-2', 'sglt2i')]['covered_through'], '2030-06-17')  # 90-day supply from the claim payload
        self.assertEqual(self.rows[('P-3', 'mra')]['state'], 'documented_stopped_or_intolerant')
        self.assertIn('note_stop@2030-02-01#n1', self.rows[('P-3', 'mra')]['stop_evidence'])
        self.assertEqual(self.rows[('P-4', 'rasi')]['state'], 'documented_stopped_or_intolerant')  # barrier after the fill
        self.assertEqual(self.rows[('P-5', 'mra')]['state'], 'filled_current')  # refilled after the documented stop
        self.assertEqual(self.rows[('P-6', 'beta_blocker')]['state'], 'never_recorded')  # a reversed claim is not a fill
        self.assertEqual(self.rows[('P-7', 'sglt2i')]['state'], 'documented_stopped_or_intolerant')  # undated barrier, nothing else
        self.assertEqual(self.rows[('P-2', 'rasi')]['state'], 'never_recorded')

    def test_grace_window_and_subject_vocabulary(self):
        late = {(r['patient_id'], r['subject']): r['state'] for r in MS.classify(self.db, '2030-07-30', ['sglt2i'])}
        self.assertEqual(late[('P-2', 'sglt2i')], 'listed_not_filled')  # 2030-06-17 + 30 days < 2030-07-30
        self.assertEqual(MS.subjects('statin, rasi'), {'statin': ['statin'], 'rasi': ['ace_inhibitor', 'arb', 'arni']})
        with self.assertRaises(ValueError): MS.subjects(['not_a_class'])

    def test_governed_macro_in_a_staged_analysis(self):
        catalog = R.Catalog.load(self.path)
        question = 'Which patients have a GDMT pillar listed but not filled?'
        spec = {'steps': [{'name': 'state', 'purpose': 'Medication state per pillar',
                           'sql': "SELECT patient_id, subject, last_fill_date, last_listed_date FROM {{medication_state:gdmt}} "
                                  "WHERE state = 'listed_not_filled'"}],
                'returns': 'table', 'unresolved': [], 'requirements': [{'quote': question, 'applied_in': ['state'], 'how': 'governed macro'}]}
        contract = G.prepare(catalog, question, spec)
        self.assertIn('medication_state:gdmt', contract['governed_macros'])
        stack = SimpleNamespace(path=self.path)
        receipt = {'version': S.VERSION, 'policy_identity': S.policy_identity(), 'request_contract': contract,
                   'interpretation': {'execution_accepted': True}, 'new_paid_api_calls': 0}
        answer = G.execute(stack, question, G.graph_for(contract), receipt, economics=S.economics)['answer']
        self.assertEqual({r['patient_id'] for r in answer['table']}, {'P-1007'})
        self.assertEqual(len(answer['table']), 4)
        self.assertTrue(any('medication-state' in line for line in answer['limitations']))
        scoped = G.prepare(catalog, question, spec, population=['P-2'])
        report = G.execute(stack, question, G.graph_for(scoped), {**receipt, 'request_contract': scoped},
                           population=['P-2'], economics=S.economics)
        self.assertEqual(report['answer']['table'], [])  # the saved population scopes the macro's relations

    def test_engine_report_selects_patients_by_state(self):
        p = MS.validate({'subjects': ['gdmt'], 'states': ['listed_not_filled']})
        rep = MS.execute(self.path, None, p, AS_OF)
        self.assertEqual(rep['patient_ids'], ['P-1007'])
        self.assertEqual(rep['patients'][0]['rasi'], 'listed_not_filled')
        scoped = MS.execute(self.path, ['P-2', 'P-3'], MS.validate({'subjects': ['mra', 'sglt2i']}), AS_OF)
        self.assertEqual(scoped['patient_ids'], ['P-2', 'P-3'])
        ans = MS.answer(rep, {'coverage': 'supported_observation', 'unmet_requirements': []}, 'which patients')
        self.assertEqual((ans['status'], ans['count'], ans['patient_ids']), ('answered', 1, ['P-1007']))
        with self.assertRaises(ValueError): MS.validate({'states': ['unknown']})


class Registration(unittest.TestCase):
    def test_registered_everywhere_the_planner_reads(self):
        from clinical_core.agere.clinical import analysis_graph, capability_registry, graph_contracts, graph_planner
        stub = {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}}
        for op in ('medication_state', 'default_definition'):
            self.assertIn(op, capability_registry.PRODUCT)
            self.assertIn(op, graph_planner.CONTRACTS)
            self.assertIn(op, graph_contracts.TYPED)
            self.assertIn(op, analysis_graph.OPERATIONS)
            self.assertIsNotNone(graph_contracts.parameter_schema(op, stub))
            self.assertEqual(graph_contracts.input_contract(op)['count'], [0, 1])  # usable as a typed-request analysis

    def test_typed_request_compiles_an_analysis_route(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        build(tmp.name).close()
        catalog = R.Catalog.load(tmp.name)
        question = 'List patients and classify GDMT medication state'
        plan = {'source': {'table': 'patient', 'quote': 'patients'},
                'select': [{'kind': 'field', 'field': 'patient.patient_id', 'alias': 'patient_id', 'quote': 'List'}],
                'where': None, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': [],
                'analyses': [{'operation': 'medication_state', 'quote': 'classify GDMT medication state',
                              'parameters': {'subjects': ['gdmt'], 'states': [], 'output': 'patients', 'grace_days': 30}}]}
        contract = R.compile_request(catalog, question, plan)
        graph = S.graph_for(contract)
        self.assertEqual(graph['nodes'][-1]['operation'], 'medication_state')


if __name__ == '__main__':
    unittest.main()
