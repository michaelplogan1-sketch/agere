"""Authored orchestration fixtures, not language/model qualification. No provider calls."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from clinical_core.agere.clinical import request_program as P, request_plan as R, shared_planner as S
from clinical_core.agere.clinical import conversation, request_discovery, sql_query
from clinical_core.agere.deliverables import answer_context as A, workflow as W


CLAUSES = ['Show invoice totals by account', 'show the average total from those totals',
           'count shipments', 'email the account managers']
QUESTION = '; '.join(CLAUSES)


def analysis(question, sql, returns='table'):
    return {'steps': [{'name': 'computed', 'purpose': question, 'sql': sql}], 'final': 'computed',
            'returns': returns, 'requirements': [{'quote': question, 'applied_in': ['computed'], 'how': sql}],
            'unresolved': []}


def program():
    return {'nodes': [
        {'id': 'totals', 'quote': CLAUSES[0], 'inputs': [],
         'analysis': analysis(CLAUSES[0], 'SELECT account_id, SUM(amount_cents) AS total FROM invoice GROUP BY account_id')},
        {'id': 'average', 'quote': CLAUSES[1], 'inputs': ['totals'],
         'analysis': analysis(CLAUSES[1], 'SELECT AVG(total) AS average FROM {{result:totals}} AS totals', 'value')},
        {'id': 'shipping', 'quote': CLAUSES[2], 'inputs': [],
         'analysis': analysis(CLAUSES[2], 'SELECT COUNT(*) AS shipments FROM shipment', 'count')},
        {'id': 'contact', 'quote': CLAUSES[3], 'inputs': [],
         'unresolved': ['Email delivery and recipient authorization are not connected.']},
    ], 'unresolved': []}


def audit(spec):
    return {'equivalent': True, 'issues': [], 'nodes': [
        {'id': n['id'], 'equivalent': True, 'issues': []} for n in spec['nodes']]}


class RequestProgram(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''
                CREATE TABLE invoice(invoice_id TEXT, account_id TEXT, amount_cents INTEGER);
                INSERT INTO invoice VALUES ('I1','A',100), ('I2','A',20), ('I3','B',80);
                CREATE TABLE shipment(shipment_id TEXT, account_id TEXT, status TEXT);
                INSERT INTO shipment VALUES ('S1','A','shipped'),('S2','B','shipped'),('S3','B','pending');
            ''')
        self.catalog = R.Catalog.load(self.path)
        self.stack = SimpleNamespace(path=self.path, allow_live=False, shared_request_planning=True)
        self.stack.compose = lambda q, **kw: S.compose(self.stack, q, **kw)

    def execute(self, spec=None, verdict=None, accepted=True):
        spec = spec or program()
        contract = P.prepare(self.catalog, QUESTION, spec)
        receipt = {'version': S.VERSION, 'policy_identity': S.policy_identity(), 'request_contract': contract,
                   'interpretation': {'execution_accepted': accepted, 'interpreter': 'authored_fixture',
                                      'semantic_review': verdict or audit(spec)},
                   'feedback_identity': R.fingerprint([]), 'new_paid_api_calls': 0,
                   'known_api_estimate_usd': 0, 'api_cost_known': True}
        return P.execute(self.stack, QUESTION, P.graph_for(contract), receipt)

    def test_multi_grain_dependencies_and_unavailable_command_keep_independent_results(self):
        result = self.execute()
        self.assertEqual([s['id'] for s in result['answer']['sections']], ['totals', 'average', 'shipping', 'contact'])
        sections = {s['id']: s['answer'] for s in result['answer']['sections']}
        self.assertEqual(sections['totals']['table'], [{'account_id': 'A', 'total': 120}, {'account_id': 'B', 'total': 80}])
        self.assertEqual(sections['average']['value'], 100)
        self.assertEqual(sections['shipping']['count'], 3)
        self.assertEqual(sections['shipping']['status'], 'answered')
        self.assertEqual(sections['contact']['status'], 'partial')
        self.assertIn('not connected', sections['contact']['missing'][0])
        self.assertEqual(result['answer']['status'], 'partial')
        self.assertEqual(result['answer']['patient_ids'], [])
        self.assertEqual(result['new_paid_api_calls'], 0)
        self.assertEqual(result['question'], QUESTION)

    def test_compiler_failure_is_not_mislabeled_missing_data_or_lost_output(self):
        spec = program()
        spec['nodes'][0]['analysis']['steps'][0]['sql'] = 'SELECT * FROM not_a_table'
        result = self.execute(spec)
        sections = {s['id']: s['answer'] for s in result['answer']['sections']}
        self.assertIn('Compilation failed', sections['totals']['missing'][0])
        self.assertIn('required input', sections['average']['missing'][0].lower())
        self.assertEqual(sections['shipping']['count'], 3)

    def test_review_failure_blocks_dependents_but_keeps_independent_output(self):
        verdict = audit(program())
        verdict['nodes'][0].update(equivalent=False, issues=['Incorrect account grouping'])
        result = self.execute(verdict=verdict)
        sections = {s['id']: s['answer'] for s in result['answer']['sections']}
        self.assertEqual(sections['totals']['status'], 'partial')
        self.assertNotIn('value', sections['average'])
        self.assertEqual(sections['shipping']['status'], 'answered')
        self.assertFalse(result['answer']['actionable_cohort'])

    def test_unaccepted_program_cannot_authorize_work(self):
        result = self.execute(accepted=False)
        self.assertTrue(all(s['answer']['status'] == 'partial' for s in result['answer']['sections']))
        self.assertEqual(result['answer']['sections'][1]['answer']['value'], 100)
        self.assertFalse(result['answer']['actionable_cohort'])

    def test_cycles_unknown_dependencies_and_unmapped_original_requirement_rejected(self):
        spec = program()
        spec['nodes'][0]['inputs'] = ['average']
        with self.assertRaisesRegex(R.PlanError, 'cycle'):
            P.prepare(self.catalog, QUESTION, spec)
        spec = program()
        spec['nodes'][1]['inputs'] = ['invented']
        with self.assertRaisesRegex(R.PlanError, 'unknown input'):
            P.prepare(self.catalog, QUESTION, spec)
        spec = program()
        spec['nodes'].pop()
        with self.assertRaisesRegex(R.PlanError, 'missing.*email'):
            P.prepare(self.catalog, QUESTION, spec)

    def test_audit_must_name_every_node_once(self):
        contract = P.prepare(self.catalog, QUESTION, program())
        verdict = audit(program())
        verdict['nodes'][1]['id'] = 'totals'
        with self.assertRaisesRegex(R.PlanError, 'every node exactly once'):
            P.validate_audit(verdict, contract)

    def test_integrity_and_source_changes_reject_mixed_snapshot_result(self):
        result = self.execute()
        contract = copy.deepcopy(result['request_contract'])
        contract['nodes'][0]['contract']['query'] = 'SELECT 999'
        with self.assertRaisesRegex(R.PlanError, 'sealed'):
            P.sealed(contract)
        ctx = A.capture(self.path, QUESTION, result)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.execute("INSERT INTO shipment VALUES ('S4','C','pending')")
        with self.assertRaisesRegex(ValueError, 'data changed'):
            A.load(self.path, ctx['id'], current=True)
        with self.assertRaisesRegex(R.PlanError, 'changed during planning'):
            P.execute(self.stack, QUESTION, result['graph'], result['planner_receipt'])

    def test_shared_compose_and_conversation_save_each_output_without_union(self):
        model = Mock(side_effect=[(json.dumps({'program': program()}), {}), (json.dumps(audit(program())), {})])
        result = S.compose(self.stack, QUESTION, model=model)
        self.assertEqual(result['schema'], P.VERSION)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(json.loads(model.call_args_list[1].args[1])['original_question'], QUESTION)
        # Exercise the actual conversation integration with an authored accepted
        # result; no model/provider or synthetic patient action is invoked.
        accepted = self.execute()
        with patch.object(self.stack, 'compose', return_value=accepted):
            saved = conversation.handle_compose(self.stack, {'question': QUESTION})
        self.assertNotIn('action_context_error', saved)
        self.assertEqual(len([s for s in saved['answer']['sections'] if s.get('action_context')]), 3)
        context = A.load(self.path, saved['action_context']['id'], current=True)
        self.assertEqual(context['patient_ids'], [])
        self.assertFalse(A.supports_patient_work(context))

    def test_plain_warehouse_discovery_and_no_inferred_clock(self):
        self.assertIsNone(self.catalog.identity['as_of'])
        self.assertTrue(self.catalog.identity['source_digest'].startswith('sqlite-snapshot:'))
        discovered = request_discovery.relation_values(self.catalog)
        self.assertTrue(discovered['fields'])
        self.assertEqual(A.source_identity(self.path)['source_digest'], self.catalog.identity['source_digest'])
        spec = program()
        spec['nodes'][0]['analysis']['steps'][0]['sql'] = 'SELECT {{as_of}} AS business_date'
        contract = P.prepare(self.catalog, QUESTION, spec)
        self.assertIn('no declared as-of date', contract['nodes'][0]['unresolved'][0])

    def test_scalar_shape_never_discards_additional_rows_or_columns(self):
        for query in ('SELECT amount_cents FROM invoice', 'SELECT 1 AS a, 2 AS b'):
            with self.subTest(query=query), self.assertRaisesRegex(ValueError, 'exactly one row and one column'):
                sql_query.execute(self.path, {'query': query, 'returns': 'value'}, target=self.catalog.target)

    def test_arbitrary_rate_is_not_assumed_to_be_a_percentage(self):
        spec = program()
        spec['nodes'][0]['analysis']['steps'][0]['sql'] = 'SELECT 200 AS processing_rate FROM invoice LIMIT 1'
        self.assertEqual(self.execute(spec)['answer']['sections'][0]['answer']['status'], 'answered')

    def test_growth_percentage_can_exceed_one_hundred(self):
        spec = program()
        spec['nodes'][0]['analysis']['steps'][0]['sql'] = 'SELECT 200 AS growth_percent FROM invoice LIMIT 1'
        self.assertEqual(self.execute(spec)['answer']['sections'][0]['answer']['status'], 'answered')

    def test_unscoped_unknown_cannot_authorize_independent_work(self):
        spec = program()
        spec['unresolved'] = [{'quote': CLAUSES[0], 'reason': 'Account scope is unresolved for this request.'}]
        result = self.execute(spec)
        self.assertTrue(all(s['answer']['status'] == 'partial' for s in result['answer']['sections']))

    def test_complete_reviewed_program_cache_reuses_sealed_contract(self):
        spec=program();spec['nodes'].pop();q='; '.join(CLAUSES[:3])
        contract=P.prepare(self.catalog,q,spec)
        interpretation={'execution_accepted':True,'semantic_review':audit(spec),'review':'authored_review_fixture','attempts':[]}
        with patch.object(S,'checked_interpret',return_value=(contract,interpretation)) as interpreted:
            first=S.compose(self.stack,q)
            again=S.compose(self.stack,q)
        self.assertEqual(first['answer']['status'],'answered')
        self.assertEqual(again['answer']['status'],'answered')
        self.assertEqual(interpreted.call_count,1)
        self.assertTrue(again['planner_receipt']['interpretation']['exact_contract_cache_hit'])
        self.assertEqual(again['new_paid_api_calls'],0)


class ScopedProgram(unittest.TestCase):
    from test_shared_planner import SharedPlanner
    setUp = SharedPlanner.setUp

    def test_each_output_owns_exact_work_view_and_monitor_without_population_union(self):
        clauses = [f'List patients {pid}; create a brief; prepare a simulated outreach program; monitor agent progress'
                   for pid in ('A', 'B')]
        question = '; '.join(clauses)
        nodes = []
        for pid, clause in zip(('A', 'B'), clauses):
            s = analysis('List patients ' + pid, f"SELECT patient_id FROM patient WHERE patient_id='{pid}'")
            s['patient_selection'] = {'column': 'patient_id', 'quote': 'List patients'}
            s['workflow'] = [
                {'kind': 'deliverable', 'title': 'Requested brief', 'template': 'brief', 'quote': 'create a brief', 'unresolved': []},
                {'kind': 'simulation', 'title': 'Requested synthetic review', 'program': 'custom',
                 'quote': 'prepare a simulated outreach program', 'unresolved': []},
                {'kind': 'monitor', 'title': 'Agent progress', 'mode': 'work', 'scope': 'result',
                 'outputs': ['work_status'], 'quote': 'monitor agent progress', 'unresolved': []},
            ]
            nodes.append({'id': pid.lower(), 'quote': clause, 'inputs': [], 'analysis': s})
        spec = {'nodes': nodes, 'unresolved': []}
        contract = P.prepare(self.catalog, question, spec)
        receipt = {'version': S.VERSION, 'policy_identity': S.policy_identity(), 'request_contract': contract,
                   'interpretation': {'execution_accepted': True, 'interpreter': 'authored_fixture',
                                      'semantic_review': audit(spec)}, 'feedback_identity': R.fingerprint([]),
                   'new_paid_api_calls': 0, 'known_api_estimate_usd': 0, 'api_cost_known': True}
        result = P.execute(self.stack, question, P.graph_for(contract), receipt)
        original = W.materialize
        def local_view(*args, **kwargs):
            return original(*args, **{**kwargs, 'generation': 'local_template'})
        with patch.object(self.stack, 'compose', return_value=result), patch.object(W, 'materialize', side_effect=local_view):
            result = conversation.handle_compose(self.stack, {'question': question})
        self.assertNotIn('action_context_error', result)
        root = A.load(self.path, result['action_context']['id'], current=True)
        self.assertEqual(root['patient_ids'], [])
        self.assertFalse(A.supports_patient_work(root))
        for section, pid in zip(result['answer']['sections'], ('A', 'B')):
            child = A.load(self.path, section['action_context']['id'], current=True)
            self.assertEqual(child['patient_ids'], [pid])
            self.assertEqual({i['patient_id'] for i in A.work(self.path, child)}, {pid})
            steps = W.read(self.path, child['id'])['steps']
            self.assertEqual([s['status'] for s in steps], ['ready', 'ready', 'watching'])
        linked = W.read(self.path, root['id'])
        self.assertTrue(linked['program_links'])
        self.assertEqual(len(linked['steps']), 6)
        with self.assertRaisesRegex(ValueError, 'producing output'):
            W.refresh(self.stack, {'context_id': root['id'], 'index': 2})


if __name__ == '__main__':
    unittest.main()
