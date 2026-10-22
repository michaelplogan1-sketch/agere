"""atomic_questions: plan -> per-patient System One decisions -> code aggregation -> cited answer (providers mocked).

Live smoke (opt-in, <= 50 calls): AGERE_LIVE_SYSTEM_ONE=1 python -m unittest test_atomic_questions.LiveSmoke
"""
import os
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('AGERE_LOCAL_TYPED', 'off')
os.environ.setdefault('AGERE_LOCAL_NORMALIZER', 'off')

from clinical_core.agere.clinical import atomic_questions as aq

S1 = 'clinical_core.agere.clinical.system_one'
GDMT_Q = 'For HFrEF patients, which of the four GDMT pillars is each patient on?'
BB_Q = 'Does the chart support that P-1007 is intolerant to beta blockers?'


class Retriever:
    def __init__(self, patients, texts=None):
        self.meta = {'snapshot': {'as_of': '2030-05-27'}}
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE patients(id TEXT)')
        self.db.executemany('INSERT INTO patients VALUES (?)', [(p,) for p in patients])
        self.texts = texts or {}
        self.searches = 0

    def search(self, question, *, patient=None, limit=8, patient_ids=None, analysis_window=None):
        self.searches += 1
        rows = self.texts.get(patient) or [('2030-01-0%d' % (i + 1), f'{patient} note {i}: {question[:40]}') for i in range(2)]
        return {'evidence': [{'id': f'{patient}-{i}', 'text': t, 'note_id': f'r-{patient}-{i}',
                              'source': {'patient_id': patient, 'record_id': f'r-{patient}-{i}', 'field': 'note_text',
                                         'event_date': d, 'start': 0, 'end': len(t)}} for i, (d, t) in enumerate(rows[:limit])]}


class Stack:
    def __init__(self, patients, allow_live=True, texts=None):
        self.path = Path('/nonexistent-atomic-stack')
        self.retriever = Retriever(patients, texts)
        self.allow_live = allow_live
        self.receipts = self.path


def fake_system_one(rule, calls):
    """A stub system_one module whose decide() applies rule(question, evidence) -> label."""
    mod = types.ModuleType(S1)

    def decide(question, options, evidence, *, as_of, order=('jev', 'perplexity'), threshold=0.8, task_key=None, ledger=None, allow_live=True):
        calls.append((question, tuple(e['id'] for e in evidence)))
        label = rule(question, evidence)
        probs = {k: (0.9 if k == label else 0.1 / max(1, len(options) - 1)) for k in options}
        return {'label': label, 'probabilities': probs, 'calibrated_probabilities': probs,
                'evidence_ids': [evidence[0]['id']] if evidence else [], 'provider': 'jev', 'model': 'jev-test',
                'cost_usd': 0.0002, 'cache_hit': False, 'request_hash': 'h', 'decided_by': 'jev',
                'steps': [{'provider': 'jev', 'label': label, 'probabilities': probs, 'cost_usd': 0.0002, 'cache_hit': False}]}
    mod.decide = decide
    return mod


def pillar_rule(question, evidence):
    if 'beta blocker' in question: return 'intolerant'
    if 'SGLT2' in question: return 'not_on'
    return 'on'


class Base(unittest.TestCase):
    def setUp(self):
        aq._MEMO.clear()
        self.calls = []
        self.meds = mock.patch.object(aq, 'medication_rows', return_value={})
        self.meds.start()
        self.addCleanup(self.meds.stop)

    def system_one(self, rule):
        return mock.patch.dict(sys.modules, {S1: fake_system_one(rule, self.calls)})


class PlanTest(Base):
    def test_gdmt_per_pillar_plan(self):
        p = aq.plan(Stack(['P-1001']), GDMT_Q)
        self.assertEqual([s['key'] for s in p['sub_questions']], ['rasi', 'beta_blocker', 'mra', 'sglt2i'])
        self.assertEqual((p['aggregation'], p['returns'], p['unit'], p['planner']), ('latest', 'table', 'patient', 'deterministic'))
        self.assertEqual(p['scope']['population_concept'], 'heart_failure_reduced_ef')
        self.assertIsNone(p['scope']['patients'])
        self.assertIn('medication_state', p['scope']['sources'])
        self.assertTrue(all('unknown' in s['options'] for s in p['sub_questions']))

    def test_named_patient_assertion(self):
        p = aq.plan(Stack(['P-1007']), BB_Q)
        self.assertEqual(p['scope']['patients'], ['P-1007'])
        self.assertEqual((p['aggregation'], p['returns']), ('any', 'verdict'))
        sub = p['sub_questions'][0]
        self.assertEqual(sub['subject'], 'beta_blocker')
        self.assertNotIn('P-1007', sub['atom'])
        self.assertIn('intolerant to beta blockers', sub['atom'])

    def test_why_becomes_candidate_factors(self):
        p = aq.plan(Stack(['P-1007']), 'Why is P-1007 not on an SGLT2 inhibitor?')
        self.assertEqual(len(p['sub_questions']), len(aq.WHY_FACTORS))
        self.assertTrue(all(s['subject'] == 'sglt2_inhibitor' for s in p['sub_questions']))
        self.assertEqual((p['aggregation'], p['returns']), ('any', 'table'))

    def test_population_list_and_count_and_conflict(self):
        st = Stack(['P-1001'])
        p = aq.plan(st, 'Which HFrEF patients are not on an SGLT2 inhibitor?')
        self.assertEqual((p['aggregation'], p['returns']), ('any', 'patient_set'))
        self.assertIn('not on an SGLT2', p['sub_questions'][0]['atom'])
        self.assertEqual(aq.plan(st, 'How many HFrEF patients are intolerant to beta blockers?')['returns'], 'count')
        p = aq.plan(st, 'Do the notes conflict about whether P-1001 is on a statin?')
        self.assertEqual((p['aggregation'], p['unit']), ('conflict', 'document'))

    def test_population_and_cap(self):
        p = aq.plan(Stack(['P-1001']), 'Which patients are intolerant to statins?', population={'patient_ids': ['P-2', 'P-1']},
                    maximum_patients=7)
        self.assertEqual(p['scope']['patients'], ['P-1', 'P-2'])
        self.assertEqual(p['maximum_patients'], 7)

    def test_unmatched_question_declines_without_claude(self):
        self.assertIsNone(aq.plan(Stack(['P-1001'], allow_live=False), 'What is the average PMPM by practice?'))

    def test_hosted_planner_outage_continues(self):
        from clinical_core.agere.gateway import ProviderUnavailable

        class Hosted:
            def __init__(self, *a, **k): pass
            def __call__(self, *a, **k): raise ProviderUnavailable('credit balance too low', kind='billing')
        with mock.patch('clinical_core.agere.clinical.nl2sql.HostedModel', Hosted):
            self.assertIsNone(aq.plan(Stack(['P-1001']), 'What is the average PMPM by practice?', ledger=object()))

    def test_hosted_planner_plan_is_validated(self):
        text = ('{"sub_questions":[{"key":"falls","atom":"Has this patient fallen?","options":{"yes":"y","no":"n","unknown":"u"}}],'
                '"aggregation":"count","returns":"count","select_labels":["yes"],"population_concept":"heart_failure"}')

        class Hosted:
            def __init__(self, *a, **k): pass
            def __call__(self, *a, **k): return text, {'cost_estimate': {'estimated_usd': 0.01}}
        with mock.patch('clinical_core.agere.clinical.nl2sql.HostedModel', Hosted):
            p = aq.plan(Stack(['P-1001']), 'Tally the falls among heart failure folks', ledger=object())
        self.assertEqual((p['planner'], p['aggregation'], p['scope']['population_concept']), ('hosted_claude', 'count', 'heart_failure'))
        aq.validate(aq.parameters(p))


class ExecuteTest(Base):
    def run_plan(self, question, patients, rule, **kw):
        st = Stack(patients, texts=kw.pop('texts', None))
        p = aq.plan(st, question, population={'patient_ids': patients} if kw.pop('population', False) else None, **kw)
        with self.system_one(rule):
            return st, p, aq.execute(st, p)

    def test_gdmt_table_with_sources_and_memo(self):
        st = Stack(['P-1', 'P-2', 'P-3'])
        p = aq.plan(st, GDMT_Q, population={'patient_ids': ['P-1', 'P-2', 'P-3']})
        with self.system_one(pillar_rule):
            out = aq.execute(st, p)
            a = out['answer']
            self.assertEqual(len(self.calls), 12)
            self.assertEqual((a['status'], a['answer_type'], a['count']), ('answered', 'table', 3))
            self.assertEqual(a['table'][0]['beta_blocker'], 'intolerant')
            self.assertEqual(a['table'][0]['sglt2i'], 'not_on')
            self.assertEqual(a['table'][0]['rasi_p'], 0.9)
            self.assertTrue(all({'patient_id', 'evidence_id', 'text'} <= set(s) for s in a['sources']))
            self.assertEqual(out['report']['system_one']['new_paid_api_calls'], 12)
            self.assertAlmostEqual(out['report']['system_one']['estimated_usd'], 0.0024)
            self.assertIn('P-1', a['probabilities'])
            again = aq.execute(st, p)  # memoized: no new provider calls
            self.assertEqual(len(self.calls), 12)
            self.assertEqual(again['report']['system_one']['new_paid_api_calls'], 0)
            self.assertEqual(again['answer']['table'], a['table'])

    def test_structured_medication_state_is_evidence(self):
        self.meds.stop()
        row = {'patient_id': 'P-1007', 'subject': 'beta_blocker', 'state': 'documented_stopped_or_intolerant',
               'basis': 'documented barrier_intolerance@2029-01-02', 'last_fill_date': '2028-05-01', 'covered_through': '2028-05-30',
               'last_listed_date': None, 'stop_date': '2029-01-02', 'stop_evidence': 'barrier_intolerance@2029-01-02#r9',
               'evidence_record_ids': 'r9'}
        with mock.patch.object(aq, 'medication_rows', return_value={('P-1007', 'beta_blocker'): row}):
            st, p, out = self.run_plan(BB_Q, ['P-1007'], lambda q, ev: 'yes' if any('intolerance' in e['text'] for e in ev) else 'no')
        self.meds.start()
        a = out['answer']
        self.assertTrue(self.calls[0][1][0].startswith('medstate:P-1007'))
        self.assertEqual(a['answer_type'], 'value')
        self.assertTrue(a['value'].startswith('Yes'))
        self.assertEqual(a['table'][0]['assertion_structured'], 'documented_stopped_or_intolerant')
        self.assertEqual(a['sources'][0]['evidence_id'], 'medstate:P-1007:beta_blocker')

    def test_cap_is_stated_and_partial(self):
        st, p, out = self.run_plan('Which patients are intolerant to statins?', ['P-1', 'P-2', 'P-3', 'P-4', 'P-5'],
                                   lambda q, ev: 'yes', population=True, maximum_patients=2)
        a = out['answer']
        self.assertEqual(a['status'], 'partial')
        self.assertEqual(a['patient_ids'], ['P-1', 'P-2'])
        self.assertIn('Capped', a['value'])
        self.assertIn('first 2 of 5', a['limitations'][0])
        self.assertEqual(a['capped'], {'cap': 2, 'evaluated': 2, 'in_scope': 5})
        self.assertEqual(st.retriever.searches, 2)

    def test_count_and_share(self):
        rule = lambda q, ev: 'yes' if ev and ev[0]['id'].startswith(('passage:P-1', 'passage:P-3')) else 'no'
        _, _, out = self.run_plan('How many patients are intolerant to statins?', ['P-1', 'P-2', 'P-3'], rule, population=True)
        self.assertEqual((out['answer']['answer_type'], out['answer']['count'], out['answer']['patient_ids']), ('count', 2, ['P-1', 'P-3']))
        aq._MEMO.clear()
        _, _, out = self.run_plan('What share of patients are intolerant to statins?', ['P-1', 'P-2', 'P-3'], rule, population=True)
        self.assertEqual(out['answer']['answer_type'], 'value')
        self.assertIn('66.7%', out['answer']['value'])

    def test_conflict_and_trajectory_per_document(self):
        texts = {'P-1': [('2029-01-01', 'taking atorvastatin'), ('2029-06-01', 'stopped atorvastatin for myalgia')],
                 'P-2': [('2029-01-01', 'taking atorvastatin'), ('2029-06-01', 'continues atorvastatin')]}
        rule = lambda q, ev: 'no' if 'stopped' in ev[0]['text'] else 'yes'
        _, _, out = self.run_plan('Do the notes conflict about whether the patients are on a statin?', ['P-1', 'P-2'], rule,
                                  population=True, texts=texts)
        rows = {r['patient_id']: r for r in out['answer']['table']}
        self.assertEqual((rows['P-1']['assertion'], rows['P-2']['assertion']), ('conflict', 'yes'))
        self.assertEqual(out['answer']['patient_ids'], ['P-1'])
        aq._MEMO.clear()
        _, _, out = self.run_plan('How has statin use by the patients changed over time?', ['P-1'], rule, population=True, texts=texts)
        row = out['answer']['table'][0]
        self.assertEqual(row['assertion_trajectory'], ['2029-01-01: yes', '2029-06-01: no'])
        self.assertEqual(row['assertion'], 'no')

    def test_provider_failure_is_unknown_not_outage(self):
        def boom(q, ev): raise RuntimeError('both providers down')
        st, p, out = self.run_plan(BB_Q, ['P-1007'], boom)
        self.assertEqual(out['answer']['table'][0]['assertion'], 'unknown')
        self.assertTrue(out['report']['do_not_cache'])
        self.assertTrue(any('failed' in l for l in out['answer']['limitations']))

    def test_providers_failed_status_is_not_memoized(self):
        mod = types.ModuleType(S1)
        mod.decide = lambda *a, **k: {'label': 'unknown', 'status': 'providers_failed', 'decided_by': None,
                                      'steps': [{'provider': 'jev', 'status': 'error', 'error': 'HTTP 503'}]}
        st = Stack(['P-1007'])
        with mock.patch.dict(sys.modules, {S1: mod}):
            out = aq.execute(st, aq.plan(st, BB_Q))
        self.assertEqual(out['report']['system_one']['errors'], 1)
        self.assertEqual(out['report']['new_paid_api_calls'], 0)
        self.assertFalse(aq._MEMO)


class TierTest(Base):
    def test_tier_graph_receipt_and_checks(self):
        from clinical_core.agere.clinical.analysis_graph import validate, build_answer_core
        from clinical_core.agere.clinical.literal_check import missing, executable_text
        st = Stack(['P-1007'])
        with self.system_one(lambda q, ev: 'yes'):
            graph, receipt = aq.tier(st, BB_Q)
        validate(graph, '2030-05-27')
        self.assertEqual([n['operation'] for n in graph['nodes']], ['cohort', 'atomic_questions'])
        self.assertEqual(receipt['route'], 'atomic_system_one')
        self.assertEqual(receipt['new_paid_api_calls'], 1)
        self.assertEqual(receipt['steps'][1]['receipt']['cost_estimate']['estimated_usd'], 0.0002)
        self.assertEqual(missing(BB_Q, executable_text([n['parameters'] for n in graph['nodes']])), [])
        # run_graph's node replays the memoized decisions (no new calls) and build_answer_core uses our answer
        with self.system_one(lambda q, ev: 'yes'):
            report = aq.run(st, ['P-1007'], graph['nodes'][1]['parameters'], '2030-05-27')
        self.assertEqual(report['new_paid_api_calls'], 0)
        answer = build_answer_core(graph, [{'node': graph['nodes'][0], 'result': {}}, {'node': graph['nodes'][1], 'result': report}], {}, BB_Q)
        self.assertEqual((answer['status'], answer['answer_type']), ('answered', 'value'))
        self.assertTrue(answer['sources'])

    def test_gdmt_literal_check_passes(self):
        from clinical_core.agere.clinical.literal_check import missing, executable_text
        p = aq.plan(Stack(['P-1']), GDMT_Q, population={'patient_ids': ['P-1']})
        g = aq.graph_of(GDMT_Q, p)
        self.assertEqual(missing(GDMT_Q, executable_text([n['parameters'] for n in g['nodes']])), [])
        q = 'Which HFrEF patients are not on an SGLT2 inhibitor?'
        g = aq.graph_of(q, aq.plan(Stack(['P-1']), q))
        self.assertEqual(missing(q, executable_text([n['parameters'] for n in g['nodes']])), [])

    def test_tier_declines(self):
        self.assertIsNone(aq.tier(Stack(['P-1007'], allow_live=False), BB_Q))
        def boom(q, ev): raise RuntimeError('down')
        with self.system_one(boom):
            self.assertIsNone(aq.tier(Stack(['P-1007']), BB_Q))  # every decision failed: the next proposer tries


class ContractTest(unittest.TestCase):
    def test_registered_everywhere(self):
        from clinical_core.agere.clinical import analysis_graph, graph_contracts, graph_planner
        self.assertIn('atomic_questions', analysis_graph.OPERATIONS)
        self.assertIn('atomic_questions', graph_contracts.TYPED)
        self.assertIn('atomic_questions', graph_planner.CONTRACTS)
        self.assertEqual(graph_contracts.input_contract('atomic_questions')['count'], [1, 1])

    def test_validator_rejects(self):
        good = {'question': 'q', 'sub_questions': [aq._sub('a', 'Is it?', aq.YES_NO)], 'aggregation': 'any', 'returns': 'verdict'}
        self.assertEqual(aq.validate(good)['unit'], 'patient')
        for bad in ({**good, 'sub_questions': [aq._sub('a', 'Is it?', {'yes': 'y', 'no': 'n'})]},
                    {**good, 'aggregation': 'conflict'},
                    {**good, 'maximum_patients': 0},
                    {**good, 'select_labels': ['maybe']},
                    {**good, 'sub_questions': [aq._sub('a', 'Is it?', aq.YES_NO, 'not_a_drug')]},
                    {**good, 'extra': 1}):
            with self.assertRaises(ValueError): aq.validate(bad)


@unittest.skipUnless(os.environ.get('AGERE_LIVE_SYSTEM_ONE') == '1', 'opt-in live System One smoke (AGERE_LIVE_SYSTEM_ONE=1)')
class LiveSmoke(unittest.TestCase):
    """Active warehouse, real Jev/Perplexity, <= 50 calls in total."""
    def test_live(self):
        import json
        from run_integrated_stack import keys
        keys()
        from clinical_core.agere.clinical.stack_runtime import Stack as Real
        build = Path(json.loads(Path('config/agere/active_warehouse.json').read_text())['build'])/'stack'
        st = Real(build, allow_live=True)
        try:
            spent = 0
            for q, cap in ((GDMT_Q, 5), (BB_Q, None)):
                graph, receipt = aq.tier(st, q, maximum_patients=cap)
                result = st.run_graph(graph, question=q)
                a = result['answer']
                spent += receipt['new_paid_api_calls']
                print('\nQ:', q, '\nreceipt:', json.dumps({k: receipt[k] for k in ('route', 'new_paid_api_calls', 'steps', 'preflight')}, default=str),
                      '\nanswer:', json.dumps({k: a.get(k) for k in ('status', 'answer_type', 'value', 'count', 'patient_ids', 'table', 'capped')}, default=str),
                      '\nsources:', json.dumps(a.get('sources', [])[:4], default=str)[:1500])
                self.assertIn(a['status'], ('answered', 'partial'))
            self.assertLessEqual(spent, 50)
        finally:
            st.close()


if __name__ == '__main__':
    unittest.main()
