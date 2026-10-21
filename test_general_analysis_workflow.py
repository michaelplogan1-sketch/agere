"""Connected general-analysis checks on authored tiny sources; no evaluation keys or APIs."""
import copy
import json
import importlib
import re
import sqlite3
import unittest
from unittest.mock import Mock, patch

import test_shared_planner as fixtures
from clinical_core.agere.clinical import general_analysis as G, request_plan as R, shared_planner as S
from clinical_core.agere.deliverables import answer_context as A, workflow as W
from clinical_core.agere.clinical import conversation


def patient_spec():
    return {
        'steps': [{'name': 'selected', 'purpose': 'Patient rows with received ED utilization',
                   'sql': "SELECT p.patient_id, p.payer_id, COUNT(e.patient_id) AS ed_visits FROM patient p "
                          "JOIN utilization_event e ON e.patient_id=p.patient_id AND e.kind='ed_visit' "
                          "WHERE e.event_date BETWEEN {{parameter:year}} || '-01-01' AND {{parameter:year}} || '-12-31' "
                          "GROUP BY p.patient_id, p.payer_id ORDER BY p.patient_id"}],
        'returns': 'table', 'final': 'selected',
        'patient_selection': {'column': 'patient_id', 'quote': 'List patients'},
        'requirements': [{'quote': 'List patients with ED visits in 2029 and their payer',
                          'applied_in': ['selected'], 'how': 'One row per patient with ED count and payer'}],
        'unresolved': [], 'parameters': [{'name': 'year', 'value': 2029, 'quote': '2029'}],
        'workflow': [
            {'kind': 'deliverable', 'title': 'Utilization brief', 'template': 'brief',
             'quote': 'create a brief', 'unresolved': []},
            {'kind': 'simulation', 'title': 'Synthetic review', 'program': 'custom',
             'quote': 'prepare a simulated outreach program', 'unresolved': []},
            {'kind': 'monitor', 'title': 'Received ED utilization', 'mode': 'answer', 'scope': 'result',
             'outputs': ['ed_visits'], 'quote': 'monitor these patients', 'unresolved': []},
        ],
    }


QUESTION = ('List patients with ED visits in 2029 and their payer; create a brief; '
            'prepare a simulated outreach program; monitor these patients')


class GeneralAnalysisWorkflow(unittest.TestCase):
    setUp = fixtures.SharedPlanner.setUp

    def execute(self, spec=None, question=QUESTION, population=None, accepted=True, relaxed=False):
        contract = G.prepare(self.catalog, question, spec or patient_spec(), population=population, relaxed=relaxed)
        receipt = {'version': S.VERSION, 'policy_identity': S.policy_identity(), 'request_contract': contract,
                   'interpretation': {'execution_accepted': accepted, 'review': 'checked_hosted_interpretation', 'semantic_review': {'equivalent': True, 'issues': []}}, 'new_paid_api_calls': 0,
                   'known_api_estimate_usd': 0, 'api_cost_known': True}
        result = G.execute(self.stack, question, G.graph_for(contract), receipt,
                           population=population, economics=S.economics)
        return result, A.capture(self.path, question, result)

    def change_source(self, statements):
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript(statements)
            db.execute("UPDATE meta SET value=value||'-changed' WHERE key='source_digest'")

    def test_merged_patient_nouns_keep_listing_intent_and_aggregate_boundary(self):
        for noun in ('patient', 'patients', 'member', 'members', 'person', 'people', 'individual', 'beneficiary', 'enrollee'):
            phrase = 'Find every ' + noun
            self.assertTrue(G._patient_listing_quote(phrase + ' with recorded evidence', phrase))
            self.assertFalse(G._patient_listing_quote('Count ' + noun + '; create a brief', noun))
            self.assertFalse(G._patient_listing_quote('Do not find ' + noun, noun))
        self.assertTrue(G._patient_listing_quote('List patients with evidence and I want their care team', ['List', 'patients']))

    def test_patient_table_creates_exact_work_and_deliverable_and_monitor(self):
        result, ctx = self.execute(population=['A', 'B', 'C'])
        self.assertEqual(result['answer']['patient_ids'], ['A', 'B'])
        self.assertTrue(result['answer']['actionable_cohort'])
        saved = A.load(self.path, ctx['id'])
        self.assertEqual(saved['original_question'], QUESTION)
        self.assertEqual(saved['planner_receipt']['request_contract']['population'], ['A', 'B', 'C'])
        flow = W.materialize(self.path, ctx['id'])
        self.assertEqual([s['status'] for s in flow['steps']], ['ready', 'ready', 'watching'])
        self.assertEqual({i['patient_id'] for i in A.inspect(self.path, ctx['id'])['items']}, {'A', 'B'})
        self.assertEqual(flow['steps'][2]['patient_scope'], ['A', 'B'])
        self.assertFalse(flow['external_dispatch_performed'])
        self.assertEqual(W.materialize(self.path, ctx['id']), flow)

    def test_general_monitor_reexecutes_without_reinterpretation_and_freezes_result_scope(self):
        _, ctx = self.execute(population=['A', 'B', 'C'])
        W.materialize(self.path, ctx['id'])
        self.change_source("INSERT INTO utilization_event VALUES('C','ed_visit','2029-05-01','ehr',1);"
                           "INSERT INTO utilization_event VALUES('A','ed_visit','2029-05-02','ehr',1);")
        self.stack.compose = Mock(side_effect=AssertionError('Monitor cannot reinterpret'))
        out = W.refresh(self.stack, {'context_id': ctx['id'], 'index': 2})
        obs = out['steps'][2]['observations']
        self.assertEqual(len(obs), 2)
        self.assertEqual(obs[-1]['measurement']['patient_ids'], ['A', 'B'])
        self.assertEqual(obs[-1]['measurement']['table'][0]['ed_visits'], 3)
        self.assertEqual(obs[-1]['new_paid_api_calls'], 0)
        self.assertEqual(len(W.refresh(self.stack, {'context_id': ctx['id'], 'index': 2})['steps'][2]['observations']), 2)

    def test_monitor_reports_no_new_cost_without_erasing_original_generation_cost(self):
        for mode in ('answer', 'work'):
            spec = patient_spec()
            q = QUESTION
            if mode == 'work':
                q = q.replace('monitor these patients', 'monitor loop closure')
                spec['workflow'][2].update(mode='work', outputs=['work_status'], quote='monitor loop closure')
            _, ctx = self.execute(spec, q)
            saved = W.materialize(self.path, ctx['id'])
            saved.update(new_paid_api_calls=2, known_api_estimate_usd=0.25)
            saved['steps'][0].update(new_paid_api_calls=2, costs={'known_api_estimate_usd': 0.25})
            W._save(self.path, ctx['id'], saved)
            refreshed = W.refresh(self.stack, {'context_id': ctx['id'], 'index': 2})
            self.assertEqual(refreshed['new_paid_api_calls'], 0)
            self.assertEqual(refreshed['known_api_estimate_usd'], 0)
            self.assertEqual(refreshed['steps'][0]['costs']['known_api_estimate_usd'], 0.25)
            self.assertEqual(W.read(self.path, ctx['id'])['known_api_estimate_usd'], 0.25)

    def test_query_monitor_keeps_original_input_scope_including_new_matches(self):
        spec = patient_spec(); spec['workflow'][2]['scope'] = 'query'
        _, ctx = self.execute(spec, population=['A', 'B', 'C'])
        W.materialize(self.path, ctx['id'])
        self.change_source("INSERT INTO utilization_event VALUES('C','ed_visit','2029-05-01','ehr',1);")
        out = W.refresh(self.stack, {'context_id': ctx['id'], 'index': 2})
        self.assertEqual(out['steps'][2]['observations'][-1]['measurement']['patient_ids'], ['A', 'B', 'C'])

    def test_aggregate_financial_answer_keeps_brief_monitor_and_never_creates_patient_work(self):
        q = ('Show allowed claim amounts by payer; create a brief; '
             'prepare a simulated outreach program; monitor this answer')
        spec = patient_spec(); spec.pop('patient_selection')
        spec['parameters'] = []
        spec['steps'] = [{'name': 'selected', 'purpose': 'Payer totals',
                          'sql': 'SELECT payer, SUM(allowed_cents) AS allowed FROM claim GROUP BY payer ORDER BY payer'}]
        spec['requirements'] = [{'quote': 'Show allowed claim amounts by payer', 'applied_in': ['selected'], 'how': 'Sum received amounts'}]
        spec['workflow'][2].update(scope='query', outputs=['allowed'], quote='monitor this answer')
        result, ctx = self.execute(spec, q, population=['A', 'B'])
        self.assertEqual(result['answer']['table'], [{'payer': 'P1', 'allowed': 35000}])
        self.assertFalse(result['answer']['actionable_cohort'])
        flow = W.materialize(self.path, ctx['id'])
        self.assertEqual([s['status'] for s in flow['steps']], ['ready', 'needs_definition', 'watching'])
        self.assertEqual(A.inspect(self.path, ctx['id'])['items'], [])
        self.change_source("UPDATE claim SET allowed_cents=20000 WHERE claim_id='Q1';")
        flow = W.refresh(self.stack, {'context_id': ctx['id'], 'index': 2})
        self.assertEqual(flow['steps'][2]['observations'][-1]['measurement']['table'], [{'payer': 'P1', 'allowed': 45000}])

    def test_table_ids_alone_are_not_action_authority(self):
        spec = patient_spec(); spec.pop('patient_selection')
        result, ctx = self.execute(spec)
        self.assertFalse(result['answer']['actionable_cohort'])
        self.assertEqual(W.materialize(self.path, ctx['id'])['steps'][1]['status'], 'blocked')

    def test_unaccepted_or_unresolved_analysis_cannot_launch_work(self):
        for accepted in (True, False):
            spec = patient_spec()
            if accepted:
                spec['unresolved'] = [{'quote': 'ED visits', 'reason': 'Incomplete source definition'}]
            result, ctx = self.execute(spec, accepted=accepted)
            self.assertEqual(result['answer']['status'], 'partial')
            self.assertFalse(result['answer']['actionable_cohort'])
            flow = W.materialize(self.path, ctx['id'])
            self.assertEqual([s['status'] for s in flow['steps']], ['ready', 'blocked', 'blocked'])
            self.assertEqual(A.inspect(self.path, ctx['id'])['items'], [])
            self.assertIn('patient work remains blocked', flow['steps'][0]['reason'])

    def test_uncomputed_metric_and_downstream_filter_remain_unresolved(self):
        spec = patient_spec()
        spec['workflow'][1]['quote'] += ' for uninsured patients'
        spec['workflow'][2]['quote'] = 'monitor mortality'
        q = QUESTION.replace('prepare a simulated outreach program', spec['workflow'][1]['quote']).replace('monitor these patients', 'monitor mortality')
        result, ctx = self.execute(spec, q)
        self.assertEqual(result['answer']['status'], 'answered')
        self.assertEqual([s['status'] for s in W.materialize(self.path, ctx['id'])['steps']], ['ready', 'needs_definition', 'needs_definition'])

    def test_workflow_quotes_and_output_bindings_are_verified(self):
        for mutate in (lambda s: s['workflow'][0].update(quote='make an invented request'),
                       lambda s: s['workflow'][2].update(outputs=['imaginary_metric']),
                       lambda s: s['patient_selection'].update(quote='Count patients'),
                       lambda s: s['patient_selection'].update(column='payer_id')):
            spec = patient_spec(); mutate(spec)
            with self.assertRaises(R.PlanError): G.prepare(self.catalog, QUESTION, spec)

    def test_patient_noun_phrase_and_ordered_fragments_bind_original_listing(self):
        q = QUESTION.replace('List patients', 'I want you to find all patients')
        for quote in ('patients', 'all patients', ['find all', 'patients']):
            with self.subTest(quote=quote):
                spec = patient_spec()
                spec['requirements'][0]['quote'] = q.split(';')[0]
                spec['patient_selection']['quote'] = quote
                result, ctx = self.execute(spec, q, population=['A', 'B'])
                self.assertTrue(result['answer']['actionable_cohort'])
                self.assertEqual(W.materialize(self.path, ctx['id'])['steps'][1]['patients'], 2)

    def test_aggregate_or_recipient_wording_does_not_authorize_a_patient_list(self):
        for prefix, quote in (('Find the number of patients', 'patients'),
                              ('Show counts by payer for patients', 'patients'),
                              ('Count patients', 'patients')):
            q = QUESTION.replace('List patients', prefix)
            spec = patient_spec()
            spec['requirements'][0]['quote'] = q.split(';')[0]
            spec['patient_selection']['quote'] = quote
            with self.subTest(question=q), self.assertRaisesRegex(R.PlanError, 'Patient work needs'):
                self.execute(spec, q)
        q = QUESTION.replace('prepare a simulated outreach program', 'prepare a simulated outreach program for patients')
        spec = patient_spec()
        spec['workflow'][1]['quote'] += ' for patients'
        spec['patient_selection']['quote'] = 'patients'  # first literal occurrence is the listing
        self.assertTrue(self.execute(spec, q)[0]['answer']['actionable_cohort'])
        spec['patient_selection']['quote'] = 'for patients'  # recipient only
        with self.assertRaisesRegex(R.PlanError, 'Patient work needs'):
            self.execute(spec, q)

    def test_invented_or_reversed_fragments_are_rejected(self):
        for quote in (['patients', 'List'], ['List', 'invented patients'], [], [None]):
            spec = patient_spec(); spec['patient_selection']['quote'] = quote
            with self.subTest(quote=quote), self.assertRaisesRegex(R.PlanError, 'Patient work needs'):
                self.execute(spec)

    def test_attached_request_listing_phrase_has_literal_provenance(self):
        question = ("I want you to find all HFrEF patients who aren't on all 4 pillars of GDMT medications or aren't max dosage, "
                    "who their pcp is, who their cardiologist is, and create an interactive HTML that will track them for loop closure "
                    "and I want you to launch agents to sign them up for our virtual pharmD med titration program, "
                    "they may need in home labs to be done as well as RPM.")
        for quote in ('find all HFrEF patients', 'all HFrEF patients', ['find all', 'HFrEF patients']):
            with self.subTest(quote=quote):
                self.assertTrue(G._patient_listing_quote(question, quote))
        for quote in ('who their pcp is', 'launch agents', 'them', 'patients with confirmed eligibility'):
            with self.subTest(quote=quote):
                self.assertFalse(G._patient_listing_quote(question, quote))
        self.assertFalse(G._patient_listing_quote('Do not list patients; show counts by payer', 'list patients'))

    def test_final_recovery_retains_analytical_rows_without_patient_work_authority(self):
        spec = patient_spec(); spec['patient_selection']['quote'] = 'invented patients'
        result, ctx = self.execute(spec, relaxed=True)
        self.assertEqual(result['answer']['status'], 'partial')
        self.assertEqual(result['answer']['patient_ids'], ['A', 'B', 'OUTSIDE'])
        self.assertFalse(result['answer']['actionable_cohort'])
        self.assertIsNone(result['request_contract']['analysis']['patient_selection'])
        self.assertEqual(result['answer']['evidence_completeness']['status'], 'incomplete')
        self.assertIn('patient work is blocked', ' '.join(result['answer']['missing']))
        self.assertEqual(A.inspect(self.path, ctx['id'])['items'], [])
        flow = W.materialize(self.path, ctx['id'])
        self.assertEqual([s['status'] for s in flow['steps']], ['ready', 'blocked', 'blocked'])
        compose = importlib.import_module('clinical_core.agere.deliverables.compose')
        _, _, _, doc = compose.load(A.workspace(self.path, A.load(self.path, ctx['id'])), flow['steps'][0]['deliverable_id'])
        self.assertIn('Partial evidence · patient work blocked', doc)

    def test_partial_answer_cannot_trigger_frontier_generation_or_work(self):
        spec = patient_spec(); spec['patient_selection']['quote'] = 'invented patients'
        _, ctx = self.execute(spec, relaxed=True)
        send = Mock(side_effect=AssertionError('No provider call is permitted'))
        flow = W.materialize(self.path, ctx['id'], generation='frontier', allow_live=True, send=send)
        self.assertTrue(all(s['status'] == 'blocked' for s in flow['steps']))
        send.assert_not_called()
        self.assertEqual(A.inspect(self.path, ctx['id'])['items'], [])

    def test_final_workflow_error_preserves_analysis_and_requested_inspection_view(self):
        spec = patient_spec(); spec['workflow'][1]['program'] = 'unavailable'
        with self.assertRaisesRegex(R.PlanError, 'Choose custom'):
            self.execute(spec)
        result, ctx = self.execute(spec, relaxed=True)
        self.assertEqual(result['answer']['status'], 'partial')
        self.assertEqual(result['answer']['patient_ids'], ['A', 'B', 'OUTSIDE'])
        self.assertIn('Requested workflow step was not compiled', ' '.join(result['answer']['missing']))
        flow = W.materialize(self.path, ctx['id'])
        self.assertEqual([s['kind'] for s in flow['steps']], ['deliverable', 'monitor'])
        self.assertEqual([s['status'] for s in flow['steps']], ['ready', 'blocked'])
        self.assertEqual(A.inspect(self.path, ctx['id'])['items'], [])

    def test_scope_is_enforced_even_when_model_omits_population(self):
        result, _ = self.execute(population=[])
        self.assertEqual(result['answer']['table'], [])
        self.assertEqual(result['answer']['patient_ids'], [])
        for prefix in ('main.', 'main/**/.', '"main".', '[main] .'):
            spec = patient_spec(); spec['steps'][0]['sql'] = spec['steps'][0]['sql'].replace('FROM patient', 'FROM '+prefix+'patient')
            with self.assertRaises(R.PlanError): G.prepare(self.catalog, QUESTION, spec, population=['A'])

    def test_explicit_population_cte_works_for_single_step_select(self):
        spec = patient_spec()
        spec['steps'][0]['sql'] = spec['steps'][0]['sql'].replace("WHERE e.event_date", "WHERE p.patient_id IN (SELECT patient_id FROM population) AND e.event_date")
        result, _ = self.execute(spec, population=['B'])
        self.assertEqual(result['answer']['patient_ids'], ['B'])

    def test_crafted_outside_identity_is_rejected_before_capture(self):
        spec = patient_spec()
        spec['steps'][0]['sql'] = "SELECT 'OUTSIDE' AS patient_id, 1 AS ed_visits"
        with self.assertRaisesRegex(R.PlanError, 'escaped'):
            self.execute(spec, population=['A'])

    def test_duplicate_patient_rows_do_not_authorize_work(self):
        spec = patient_spec()
        spec['steps'][0]['sql'] = "SELECT patient_id, 1 AS ed_visits FROM utilization_event WHERE kind='ed_visit'"
        result, ctx = self.execute(spec)
        self.assertFalse(result['answer']['actionable_cohort'])
        self.assertEqual(W.materialize(self.path, ctx['id'])['steps'][1]['status'], 'blocked')

    def test_changed_year_shape_preserves_workflow_and_selection_quotes(self):
        result, _ = self.execute()
        reused = G.lookup_shape(self.path, self.catalog, QUESTION.replace('2029', '2028'), S.policy_identity())
        self.assertIsNotNone(reused)
        self.assertEqual(reused[0]['analysis']['workflow'], result['request_contract']['analysis']['workflow'])
        self.assertEqual(reused[0]['analysis']['patient_selection']['quote'], 'List patients')

    def subset_work(self):
        spec = patient_spec()
        spec['steps'][0]['sql'] = spec['steps'][0]['sql'].replace(' AS ed_visits FROM', ' AS ed_visits, '
            "CASE WHEN COUNT(e.patient_id)>=2 THEN 1 WHEN p.coverage_type='commercial' THEN 0 ELSE NULL END AS needs_outreach FROM")
        selection = 'patients with at least two ED visits'
        spec['requirements'].append({'quote': selection, 'applied_in': ['selected'],
                                     'outputs': ['needs_outreach'], 'how': 'Computed per-patient selection'})
        spec['workflow'][1]['quote'] += ' for ' + selection
        spec['workflow'][1]['selection'] = {'output': 'needs_outreach', 'quote': selection}
        spec['workflow'][2].update(mode='work', outputs=['work_status'], quote='monitor loop closure')
        question = QUESTION.replace('prepare a simulated outreach program', spec['workflow'][1]['quote']).replace('monitor these patients', 'monitor loop closure')
        return spec, question

    def test_conditional_work_uses_only_confirmed_subset_and_monitor_reads_task_history(self):
        from clinical_core.agere.deliverables import synthetic_actions
        spec, question = self.subset_work()
        result, ctx = self.execute(spec, question)
        flow = W.materialize(self.path, ctx['id'])
        self.assertEqual(result['answer']['patient_ids'], ['A', 'B', 'OUTSIDE'])
        self.assertEqual(flow['steps'][1]['selection_summary'], {
            'output': 'needs_outreach', 'selected': 1, 'not_selected': 1, 'unknown': 1,
            'quote': 'patients with at least two ED visits'})
        items = A.inspect(self.path, ctx['id'])['items']
        self.assertEqual([i['patient_id'] for i in items], ['A'])
        self.assertEqual(flow['steps'][2]['observations'][0]['measurement']['completed'], 0)
        item = items[0]
        for _ in synthetic_actions.STEPS['custom']:
            item = synthetic_actions.advance(self.path, {'context_id': ctx['id'], 'item_id': item['id'],
                       'revision': item['revision'], 'scenario': 'happy_path'})['items'][0]
        refreshed = W.refresh(self.stack, {'context_id': ctx['id'], 'index': 2})
        observed = refreshed['steps'][2]['observations']
        self.assertEqual(len(observed), 2)
        self.assertEqual(observed[-1]['measurement']['completed'], 1)
        self.assertTrue(observed[-1]['measurement']['synthetic'])
        self.assertEqual(observed[-1]['source'], observed[0]['source'])
        self.assertEqual(len(W.refresh(self.stack, {'context_id': ctx['id'], 'index': 2})['steps'][2]['observations']), 2)

    def test_simulated_agents_can_work_with_the_bound_patient_subset(self):
        spec, question = self.subset_work()
        old = spec['workflow'][1]['quote']
        new = ('launch agents for patients with at least two ED visits to work with patients '
               'to sign up for our pharmD medication titration program')
        spec['workflow'][1].update(quote=new, program='pharmd')
        _, ctx = self.execute(spec, question.replace(old, new))
        flow = W.materialize(self.path, ctx['id'])
        self.assertEqual(flow['steps'][1]['status'], 'ready')
        self.assertEqual(flow['steps'][1]['patients'], 1)
        self.assertEqual(flow['steps'][2]['status'], 'watching')

    def test_virtual_pharmd_signup_wording_keeps_the_bound_subset(self):
        spec, question = self.subset_work()
        old = spec['workflow'][1]['quote']
        new = ('launch agents for patients with at least two ED visits to sign them up '
               'for our virtual pharmD med titration program')
        spec['workflow'][1].update(quote=new, program='pharmd')
        _, ctx = self.execute(spec, question.replace(old, new))
        flow = W.materialize(self.path, ctx['id'])
        self.assertEqual(flow['steps'][1]['status'], 'ready')
        self.assertEqual({i['patient_id'] for i in A.inspect(self.path, ctx['id'])['items']}, {'A'})
        self.assertEqual(flow['steps'][1]['selection_summary']['unknown'], 1)

    def test_signup_pronoun_binds_earlier_computed_subset(self):
        spec, question = self.subset_work()
        criterion = 'patients with at least two ED visits'
        action = 'I want you to launch agents to sign them up for our virtual pharmD med titration program'
        question = ('I want you to List patients with ED visits in 2029 and their payer and flag ' + criterion +
                    '; create a brief; ' + action + '; monitor loop closure')
        spec['requirements'][-1]['quote'] = 'flag ' + criterion
        spec['workflow'][1].update(quote=action, program='pharmd')
        result, ctx = self.execute(spec, question)
        flow = W.materialize(self.path, ctx['id'])
        self.assertEqual(result['answer']['patient_ids'], ['A', 'B', 'OUTSIDE'])
        self.assertEqual(flow['steps'][1]['status'], 'ready')
        self.assertEqual(flow['steps'][1]['selection_summary']['unknown'], 1)
        self.assertEqual({i['patient_id'] for i in A.inspect(self.path, ctx['id'])['items']}, {'A'})

    def test_cross_clause_selection_needs_an_unambiguous_patient_antecedent(self):
        from clinical_core.agere.clinical import workflow_contract as C
        for action, middle, selection in (
                ('launch agents for patients', '', {'column': 'patient_id', 'quote': 'List patients'}),
                ('launch agents to sign them up', '; identify other patients', {'column': 'patient_id', 'quote': 'List patients'}),
                ('launch agents to sign them up', '', None)):
            question = 'List patients with a computed gap' + middle + '; ' + action
            step = dict(kind='simulation', title='Synthetic signup', program='custom', quote=action, unresolved=[],
                        selection=dict(output='gap', quote='with a computed gap'))
            with self.subTest(question=question), self.assertRaisesRegex(R.PlanError, 'earlier patient list'):
                C.validate([step], question, outputs=['gap'], output_bindings={'gap':['with a computed gap']},
                           patient_selection=selection)

    def test_request_scaffolding_does_not_hide_patient_preferences(self):
        self.assertEqual(G.uncovered('I want you to List patients', ['List patients']), [])
        self.assertEqual(G.uncovered('I want you to List patients who want home visits',
                                     ['List patients', 'home visits']), ['want'])

    def test_subset_cannot_use_unbound_or_nonboolean_flag(self):
        spec, question = self.subset_work()
        spec['requirements'][-1].pop('outputs')
        with self.assertRaisesRegex(R.PlanError, 'computing'):
            self.execute(spec, question)
        spec, question = self.subset_work()
        spec['steps'][0]['sql'] = spec['steps'][0]['sql'].replace('THEN 1', 'THEN 8')
        _, ctx = self.execute(spec, question)
        self.assertEqual(W.materialize(self.path, ctx['id'])['steps'][1]['status'], 'blocked')
        self.assertEqual(A.inspect(self.path, ctx['id'])['items'], [])

    def test_work_monitor_reports_exception_without_claiming_completion(self):
        from clinical_core.agere.deliverables import synthetic_actions
        spec, question = self.subset_work()
        _, ctx = self.execute(spec, question)
        W.materialize(self.path, ctx['id'])
        item = A.inspect(self.path, ctx['id'])['items'][0]
        synthetic_actions.advance(self.path, {'context_id': ctx['id'], 'item_id': item['id'],
                                  'revision': item['revision'], 'scenario': 'records_needed'})
        observed = W.refresh(self.stack, {'context_id': ctx['id'], 'index': 2})['steps'][2]['observations'][-1]['measurement']
        self.assertEqual((observed['needs_attention'], observed['completed']), (1, 0))

    def test_conversation_preserves_general_workflow_costs_reopen_and_simulation(self):
        composer = importlib.import_module('clinical_core.agere.deliverables.compose')
        from clinical_core.agere.deliverables import synthetic_actions
        model = Mock(side_effect=[(json.dumps({'analysis': patient_spec()}), {'api_usd': .2, 'api_cost_known': True}),
                                  (json.dumps({'equivalent': True, 'issues': []}), {'api_usd': .1, 'api_cost_known': True})])
        model.identity = 'configured-frontier'
        self.stack.allow_live = True; self.stack.receipts = self.path
        def sender(body):
            prompt = body['messages'][0]['content']
            rid = re.search(r'"receipt":"(R-[0-9a-f]{10})","tool":"saved_answer"', prompt).group(1)
            return {'content': [{'type': 'text', 'text': 'TITLE: Utilization brief\n```html\n<h1>Utilization brief</h1>'
                    '<p>Synthetic evidence</p><p data-bind="'+rid+':answer.headline"></p>\n```'}],
                    'usage': {'input_tokens': 100, 'output_tokens': 100}}, {'cost_estimate': {'estimated_usd': .25}}
        with patch.object(S,'checked_interpret',side_effect=lambda c,q,**kw:
                fixtures.authored_review_fixture(c,q,model,population=kw.get('population'))), \
             patch.object(composer, 'ledgered_sender', return_value=sender):
            result = conversation.handle_compose(self.stack, {'question': QUESTION, 'scope': 'warehouse'})
            replay = conversation.handle_compose(self.stack, {'question': QUESTION, 'scope': 'warehouse'})
        self.assertNotIn('action_context_error', result)
        self.assertEqual([s['status'] for s in result['workflow']], ['ready', 'ready', 'watching'])
        self.assertEqual(result['new_paid_api_calls'], 3)
        self.assertAlmostEqual(result['planning_economics']['new_api_estimate_usd'], .55)
        self.assertEqual(replay['new_paid_api_calls'], 0)
        self.assertEqual(replay['action_context']['id'], result['action_context']['id'])
        ctx = result['action_context']['id']
        item = next(i for i in A.inspect(self.path, ctx)['items'] if i['patient_id'] == 'A')
        for _ in synthetic_actions.STEPS['custom']:
            changed = synthetic_actions.advance(self.path, {'context_id': ctx, 'item_id': item['id'],
                                               'revision': item['revision'], 'scenario': 'happy_path'})
            item = next(i for i in changed['items'] if i['patient_id'] == 'A')
        self.assertEqual(item['simulation']['status'], 'complete')
        self.assertEqual(item['state'], 'needs_review')
        self.assertTrue(all(r['simulated'] and not r['external_dispatch_performed'] for r in item['simulation']['receipts']))


if __name__ == '__main__':
    unittest.main()
