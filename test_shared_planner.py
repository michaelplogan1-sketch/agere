"""Independent tiny warehouse; no evaluation keys or generator truth.

Rows intentionally include duplicate clinical evidence, null ages, opposite drug
polarity, different event clocks, and an unrelated patient outside saved scope.
"""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from clinical_core.agere.clinical import request_plan as R, request_language as L, shared_planner as S, conversation
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood


def authored_review_fixture(catalog, question, model, *, population=None):
    """Exercise supplied proposals/cache/work accounting without runtime LLM routing.

    The callable is a mock and acceptance is an authored test fixture, never
    evidence that a provider response or a clinical/model role was qualified.
    """
    contract, interpretation = S.interpret(catalog,question,model=model,population=population,force_model=True)
    interpretation.update(execution_accepted=True,review='authored_acceptance_fixture')
    return contract, interpretation


class SharedPlanner(unittest.TestCase):
    def setUp(self):
        import os as _os; from unittest.mock import patch as _patch; _spec_off = _patch.dict(_os.environ, {'AGERE_SPEC_PATH': 'off'}); _spec_off.start(); self.addCleanup(_spec_off.stop)  # these tests exercise the hosted interpreter tier, not the spec path
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executescript('''
                CREATE TABLE meta(key,value);
                INSERT INTO meta VALUES('version','fixture'),('source_digest','independent-shared-1'),('as_of','2030-05-27');
                CREATE TABLE patient(patient_id,birth_date,sex_reported,coverage_type,payer_id,pcp_clinician_id,pcp_practice_id);
                INSERT INTO patient VALUES
                  ('A','1960-05-27','F','medicare','P1','DR1','ORG1'),('B','1965-05-27','M','commercial','P1','DR1','ORG1'),
                  ('C','1950-05-27','F','medicare','P2','DR2','ORG2'),('D',NULL,'M','medicaid','P2','DR2','ORG2'),
                  ('OUTSIDE','1940-05-27','F','medicare','P3','DR3','ORG3');
                CREATE TABLE condition(patient_id,concept,is_diagnosis);
                INSERT INTO condition VALUES('A','diabetes',1),('A','diabetes',1),('B','diabetes',1),
                  ('C','diabetes',1),('C','chronic_kidney_disease',1),('D','diabetes',0),('OUTSIDE','chronic_kidney_disease',1);
                CREATE TABLE medication(patient_id,drug_class,evidence DEFAULT 'dispensed',status DEFAULT 'dispensed',fill_reversed DEFAULT 0,event_date DEFAULT '2029-09-05');
                INSERT INTO medication(patient_id,drug_class) VALUES('A','statin'),('A','statin'),('B','statin'),('B','arb'),('C','ace_inhibitor');
                CREATE TABLE visit(patient_id,type_concept,event_date);
                INSERT INTO visit VALUES('A','emergency_visit','2029-01-02'),('A','emergency_visit','2029-03-03'),
                  ('A','emergency_visit','2028-12-31'),('B','emergency_visit','2029-01-01'),
                  ('C','office_visit','2029-01-01'),('OUTSIDE','emergency_visit','2029-01-01');
                CREATE TABLE utilization_event(patient_id,kind,event_date,sources,in_ehr);
                INSERT INTO utilization_event SELECT patient_id,'ed_visit',event_date,'ehr',1 FROM visit WHERE type_concept='emergency_visit';
                CREATE TABLE claim(claim_id,patient_id,payer,service_date,first_adjudication_date,allowed_cents,ever_denied);
                INSERT INTO claim VALUES('Q1','A','P1','2029-01-01','2030-01-01',10000,1),
                  ('Q2','A','P1','2029-01-02','2029-02-01',20000,0),('Q3','B','P1','2029-01-03','2029-02-01',5000,1),
                  ('Q4','C','P2','2029-01-01','2029-01-02',0,1),('Q5','OUTSIDE','P3','2029-01-01','2029-01-02',90000,1);
                CREATE TABLE claim_adjudication(claim_id,patient_id,status,event_date);
                INSERT INTO claim_adjudication VALUES('Q1','A','denied','2029-01-01'),('Q1','A','denied','2029-01-02');
                CREATE TABLE stay(patient_id,type,admit_date,discharge_date,readmitted_30d,los_days);
                INSERT INTO stay VALUES('A','inpatient','2029-01-01','2029-01-04',1,3),
                  ('B','inpatient','2029-02-01','2029-02-10',0,9),('C','inpatient','2029-03-01','2029-03-06',0,5);
                CREATE TABLE appointment(patient_id,scheduled_date,kept,final_status);
                INSERT INTO appointment VALUES('A','2029-03-01',1,'completed'),('B','2029-03-01',0,'no_show');
                CREATE TABLE new_sensor_feed(patient_id,battery_health,reading_date);
                INSERT INTO new_sensor_feed VALUES('A',12,'2029-01-01'),('B',98,'2029-01-02'),('OUTSIDE',5,'2029-01-01');
            ''')
        self.catalog = R.Catalog.load(self.path)
        self.stack = SimpleNamespace(path=self.path, allow_live=False, shared_request_planning=True)
        self.stack.compose = lambda q, **kw: S.compose(self.stack, q, **kw)

    def ask(self, q, **kw):
        # Every grammar test fails if it unexpectedly asks a model to rescue it.
        return S.compose(self.stack, q, model=Mock(side_effect=AssertionError('Unexpected language fallback')), **kw)

    def compose_model_fixture(self, q, model):
        with patch.object(L,'parse',side_effect=R.PlanError('Deliberate grammar decline for injected-model unit fixture')):
            return S.compose(self.stack,q,model=model)

    def test_cross_domain_event_measure_and_population(self):
        a = self.ask('How many ED visits among patients over 65 with diabetes in 2029 by payer?')['answer']
        self.assertEqual(a['table'], [{'payer_id': 'P1', 'count': 2}])
        self.assertEqual(a['patient_ids'], ['A'])
        self.assertEqual(a['status'], 'answered')

    def test_new_compositions_do_not_require_handlers(self):
        questions = [
            ('Count claims where ever_denied = 1 and allowed_cents > 0 in 2029 by payer',
             [{'payer': 'P1', 'count': 2}, {'payer': 'P3', 'count': 1}]),
            ('Total allowed_cents for claims with diabetes in 2029 by payer',
             [{'payer': 'P1', 'sum_allowed_cents': 35000}, {'payer': 'P2', 'sum_allowed_cents': 0}]),
            ('Average los_days for admissions among patients over 65 in 2029 by practice',
             [{'pcp_practice_id': 'ORG1', 'avg_los_days': 3.0}, {'pcp_practice_id': 'ORG2', 'avg_los_days': 5.0}]),
            ('Average length of stay for admissions among patients over 65 in 2029 by practice',
             [{'pcp_practice_id': 'ORG1', 'avg_los_days': 3.0}, {'pcp_practice_id': 'ORG2', 'avg_los_days': 5.0}]),
            ('Count appointments where kept = 0 and age >= 65 by PCP', [{'pcp_clinician_id': 'DR1', 'count': 1}]),
        ]
        for q, expected in questions:
            with self.subTest(q=q): self.assertEqual(self.ask(q)['answer']['table'], expected)

    def test_new_received_table_is_queryable_without_source_handler(self):
        self.assertEqual(self.ask('Count new sensor feeds where battery_health < 20')['answer']['count'], 2)

    def test_boolean_scope_and_exclusion_are_preserved(self):
        q = 'List patients with diabetes and (age > 65 or coverage = "commercial") and without ckd'
        self.assertEqual(self.ask(q)['answer']['patient_ids'], ['A', 'B'])
        q = 'List diabetics on a statin but not on an ACE inhibitor or ARB'
        self.assertEqual(self.ask(q)['answer']['patient_ids'], ['A'])
        self.assertEqual(self.ask('List diabetics')['answer']['patient_ids'], self.ask('List patients with diabetes')['answer']['patient_ids'])

    def test_constraint_mutations_change_membership_as_expected(self):
        base = 'List patients with diabetes and age {op} 65'
        self.assertEqual(self.ask(base.format(op='>'))['answer']['patient_ids'], ['A', 'C'])
        self.assertEqual(self.ask(base.format(op='>='))['answer']['patient_ids'], ['A', 'B', 'C'])
        self.assertEqual(self.ask(base.format(op='<'))['answer']['patient_ids'], [])
        self.assertEqual(self.ask('Count ED visits in 2028')['answer']['count'], 1)
        self.assertEqual(self.ask('Count ED visits in 2029')['answer']['count'], 4)

    def test_no_ignored_constraint_or_incomplete_boolean(self):
        for q in ['Count patients with diabetes who prefer morning appointments',
                  'Count patients with diabetes and', 'Count patients with diabetes or',
                  'Count patients with diabetes excluding', 'Count patients in 2029']:
            with self.subTest(q=q), self.assertRaises(R.PlanError): L.parse(self.catalog, q)

    def test_explicit_clock_changes_answer(self):
        first = self.ask('Count claims where service_date >= "2030-01-01"')['answer']['count']
        second = self.ask('Count claims where first_adjudication_date >= "2030-01-01"')['answer']['count']
        self.assertEqual((first, second), (0, 1))

    def test_compiler_rejects_changed_contract_unknown_field_and_dropped_number(self):
        q = 'Count patients where age > 65'
        plan = L.parse(self.catalog, q)
        contract = R.compile_request(self.catalog, q, plan)
        contract['query'] = 'SELECT count(*) FROM patient'
        with self.assertRaises(R.PlanError): R.assert_sealed(contract)
        plan['where']['field'] = 'patient.invented'
        with self.assertRaises(R.PlanError): R.compile_request(self.catalog, q, plan)
        plan['where']['field'] = 'patient.age'
        plan['where']['value'] = 60
        with self.assertRaisesRegex(R.PlanError, 'threshold|Numeric'): R.compile_request(self.catalog, q, plan)

    def test_missing_dimension_nulls_are_not_lost(self):
        a = self.ask('Count patients by age')['answer']['table']
        self.assertIn({'age': None, 'patients': 1}, a)
        self.assertEqual(sum(row['patients'] for row in a), 5)

    def test_exact_scope_filters_and_grouping_followup(self):
        parent = {'id': 'fixture', 'answer': {'status': 'answered', 'missing': [], 'limitations': []}}
        a = self.ask('Group them by payer', population=['A', 'B'], parent=parent)
        self.assertEqual(a['answer']['table'], [{'payer_id': 'P1', 'patients': 2}])
        a = self.ask('Only age > 65', population=['A', 'B'], parent=parent)
        self.assertEqual(a['answer']['patient_ids'], ['A'])
        self.assertEqual(a['cohort_transform']['input_patient_ids'], ['A', 'B'])

    def test_conversation_api_uses_shared_plans_without_rewriting_new_questions(self):
        first = conversation.handle_compose(self.stack, {'question':'List patients with diabetes','conversation_id':'shared'})
        self.assertEqual(first['answer']['patient_ids'], ['A','B','C'])
        second = conversation.handle_compose(self.stack, {'question':'Only age > 65','conversation_id':'shared'})
        self.assertEqual(second['answer']['patient_ids'], ['A','C'])
        grouped = conversation.handle_compose(self.stack, {'question':'Group them by PCP','conversation_id':'shared'})
        self.assertEqual(grouped['answer']['patient_ids'], ['A','C'])
        new = conversation.handle_compose(self.stack, {'question':'Count claims','conversation_id':'shared'})
        self.assertEqual(new['answer']['count'], 5)

    def test_reopened_answer_binds_exact_selected_context_not_latest_turn(self):
        first = conversation.handle_compose(self.stack, {'question': 'List patients with diabetes', 'conversation_id': 'scope'})
        saved = first['action_context']['id']
        conversation.handle_compose(self.stack, {'question': 'Only age > 65', 'conversation_id': 'scope'})
        selected = conversation.handle_compose(self.stack, {
            'question': 'Count patients', 'conversation_id': 'scope', 'answer_context_id': saved})
        self.assertEqual(selected['answer']['count'], 3)
        self.assertEqual(set(selected['answer']['patient_ids']), {'A', 'B', 'C'})
        reopened = conversation.handle_compose(self.stack, {
            'question': 'Count patients', 'conversation_id': 'new-browser', 'answer_context_id': saved})
        self.assertEqual(reopened['answer']['count'], 3)
        warehouse = conversation.handle_compose(self.stack, {
            'question': 'Count patients', 'conversation_id': 'new-browser', 'scope': 'warehouse'})
        self.assertEqual(warehouse['answer']['count'], 5)

    def test_explicit_scope_rejects_unknown_identity_or_conflicting_selection(self):
        with self.assertRaises(ValueError):
            conversation.handle_compose(self.stack, {'question': 'Count patients', 'conversation_id': 'scope',
                'answer_context_id': 'ANS-' + '0' * 24})
        for body in [
            {'question': 'Count patients', 'answer_context_id': 'ANS-' + '0' * 24},
            {'question': 'Count patients', 'conversation_id': 'scope', 'scope': 'warehouse',
             'answer_context_id': 'ANS-' + '0' * 24},
            {'question': 'Count patients', 'scope': 'all-patients-ignore-context'},
        ]:
            with self.assertRaises(ValueError):
                conversation.compose_shape(body)

    def test_compound_request_scope_is_independent_of_prior_answers(self):
        from test_conversation import COMPOUND_REQUEST
        first = conversation.handle_compose(self.stack, {
            'question': 'List patients with diabetes', 'conversation_id': 'compound-scope'})
        saved = first['action_context']['id']
        # Inspect dispatch, without claiming that a stub answered eligibility.
        result = {'answer': {'status': 'not_understood', 'patient_ids': []},
                  'cohort_transform': {'operations': []}}
        for scope in ({}, {'scope': 'warehouse'}, {'answer_context_id': saved}):
            with self.subTest(scope=scope), patch.object(self.stack, 'compose', return_value=result) as compose:
                conversation.handle_compose(self.stack, {
                    'question': COMPOUND_REQUEST, 'conversation_id': 'compound-scope', **scope})
                self.assertEqual(compose.call_args.args, (COMPOUND_REQUEST,))
                if 'answer_context_id' in scope:
                    self.assertEqual(set(compose.call_args.kwargs['population']), {'A', 'B', 'C'})
                    self.assertEqual(compose.call_args.kwargs['parent']['id'], saved)
                else:
                    self.assertNotIn('population', compose.call_args.kwargs)

    def test_warehouse_scope_never_inherits_a_pronoun_only_followup(self):
        conversation.handle_compose(self.stack, {
            'question': 'List patients with diabetes', 'conversation_id': 'orphan'})
        with patch.object(self.stack, 'compose') as compose:
            with self.assertRaises(QuestionNotUnderstood):
                conversation.handle_compose(self.stack, {
                    'question': 'Count them', 'conversation_id': 'orphan', 'scope': 'warehouse'})
            compose.assert_not_called()
        result = conversation.handle_compose(self.stack, {
            'question': 'Count them', 'conversation_id': 'orphan'})
        self.assertEqual(result['answer']['count'], 3)

    def test_related_records_for_prior_patients_keep_the_saved_membership(self):
        first = conversation.handle_compose(self.stack, {
            'question': 'List patients with diabetes', 'conversation_id': 'related'})
        result = {'answer': {'status': 'not_understood', 'patient_ids': []},
                  'cohort_transform': {'operations': []}}
        with patch.object(self.stack, 'compose', return_value=result) as compose:
            conversation.handle_compose(self.stack, {
                'question': 'Count claims for those patients', 'conversation_id': 'related'})
        self.assertEqual(set(compose.call_args.kwargs['population']), {'A', 'B', 'C'})
        self.assertEqual(compose.call_args.kwargs['parent']['id'], first['action_context']['id'])

    def test_model_review_rejection_cannot_execute_broader_cohort(self):
        q = 'Count patients who are older than 65'
        plan = {'source': {'table': 'patient', 'quote': 'patients'},
                'select': [{'kind': 'count_distinct', 'field': 'patient.patient_id', 'alias': 'patients', 'quote': 'Count'}],
                'where': {'op': '>', 'field': 'patient.age', 'value': 65, 'quote': 'older than 65'},
                'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}
        model = Mock(side_effect=[(json.dumps(plan), {}), (json.dumps({'equivalent': False, 'issues': ['A predicate is incorrect']}), {})] * 3)
        with patch.object(S.sql_query, 'execute', side_effect=AssertionError('Must not execute')):
            a = self.compose_model_fixture(q, model)
        self.assertEqual(a['answer']['status'], 'not_understood')
        self.assertEqual(a['answer']['patient_ids'], [])
        self.assertFalse(a['answer']['data_absence_established'])

    def test_model_approved_proposal_remains_labeled_candidate(self):
        q = 'Count patients who are older than 65'
        p = L.parse(self.catalog, 'Count patients where age > 65')
        p['where']['quote'] = 'older than 65'
        model = Mock(side_effect=[(json.dumps(p), {}), (json.dumps({'equivalent': True, 'issues': []}), {})])
        a = self.compose_model_fixture(q, model)['answer']
        self.assertEqual(a['count'], 3)
        self.assertEqual(a['status'], 'partial')
        self.assertFalse(a['actionable_cohort'])

    def test_hosted_enabled_runtime_still_uses_no_language_model(self):
        self.stack.allow_live = True
        with patch('clinical_core.agere.clinical.nl2sql.HostedModel',side_effect=AssertionError('No hosted interpretation')), \
             patch('clinical_core.agere.clinical.nl2sql.LocalModel.get',side_effect=AssertionError('No local LLM')):
            first=S.compose(self.stack,'Count patients who are older than 65')
            again=S.compose(self.stack,'Count patients who are older than 65')
        self.assertEqual(first['answer']['patient_ids'],['A','C','OUTSIDE'])
        self.assertEqual(first['new_paid_api_calls'],0)
        self.assertTrue(again['planner_receipt']['interpretation']['exact_contract_cache_hit'])

    def test_explicit_frontier_policy_cannot_bypass_deterministic_tier(self):
        for enabled in (False,True):
            self.stack.allow_live=enabled
            with patch('clinical_core.agere.clinical.nl2sql.HostedModel',side_effect=AssertionError('Deterministic first')):
                result=S.compose(self.stack,'Count patients',planning='frontier')
            self.assertEqual(result['answer']['count'],5)
            self.assertEqual(result['new_paid_api_calls'],0)

    def test_hosted_fallback_follows_authored_decline_and_verified_result_is_reused(self):
        # Routing/cache development fixture. The grammar miss and model review
        # are authored; this does not qualify language or clinical accuracy.
        q='Count patients who are older than 65'
        plan=L.parse(self.catalog,q)
        model=Mock(side_effect=[(json.dumps(plan),{'api_usd':.02,'api_cost_known':True}),
            (json.dumps({'equivalent':True,'issues':[]}),{'api_usd':.01,'api_cost_known':True})])
        model.identity='authored-hosted-routing-fixture'
        self.stack.allow_live=True;self.stack.receipts=self.path
        with patch.object(L,'parse',side_effect=R.PlanError('Authored deterministic decline')), \
             patch('clinical_core.agere.clinical.nl2sql.HostedModel',return_value=model) as constructor, \
             patch('clinical_core.agere.clinical.nl2sql.LocalModel.get',side_effect=AssertionError('No local guesses')), \
             patch('clinical_core.agere.gateway.default_ledger_path',return_value=self.path/'fixture-ledger.sqlite3'):
            first=S.compose(self.stack,q)
            again=S.compose(self.stack,q)
        self.assertEqual(first['answer']['count'],3)
        self.assertTrue(first['planner_receipt']['interpretation']['execution_accepted'])
        self.assertEqual(constructor.call_count,1)
        self.assertEqual(model.call_count,2)
        self.assertEqual(first['new_paid_api_calls'],2)  # Authored usage receipt, no actual network.
        self.assertEqual(again['new_paid_api_calls'],0)
        self.assertTrue(again['planner_receipt']['interpretation']['exact_contract_cache_hit'])

    def test_semantic_mismatch_gets_bounded_repair_and_never_executes_wrong_plan(self):
        q = 'Count patients who are older than 65'
        p = L.parse(self.catalog, 'Count patients where age > 65')
        p['where']['quote'] = 'older than 65'
        model = Mock(side_effect=[
            (json.dumps(p), {}), (json.dumps({'equivalent': False, 'issues': ['Check the threshold']}), {}),
            (json.dumps(p), {}), (json.dumps({'equivalent': True, 'issues': []}), {})])
        a = self.compose_model_fixture(q, model)
        self.assertEqual(a['answer']['count'], 3)
        self.assertEqual(model.call_count, 4)
        self.assertIn('semantic_review', json.loads(model.call_args_list[2].args[1])['validator_feedback'])

    def test_received_fixed_window_field_satisfies_only_its_actual_window(self):
        q = 'Average readmitted_30d for admissions in 2029'
        p = L.parse(self.catalog, q)
        p['select'][0]['quote'] = '30 day readmission proportion'
        q = '30 day readmission proportion for admissions in 2029'
        p['where']['quote'] = 'admissions in 2029'
        result, _ = self.execute_contract(q, p)
        self.assertAlmostEqual(result['value'], 1/3)
        p['select'][0]['quote'] = '90 day readmission proportion'
        with self.assertRaisesRegex(R.PlanError, 'Numeric constraints'):
            self.execute_contract(q.replace('30', '90'), p)

    def test_quoted_analytical_percentages_accept_fractions_but_not_missing_values(self):
        p = L.parse(self.catalog, 'Count patients')
        phrase = 'calculate shared savings with a $100 PMPM benchmark, 50% sharing and 2% minimum savings rate'
        q = 'Count patients and ' + phrase
        p['analyses'] = [{'operation': 'vbc', 'quote': phrase, 'parameters': {
            'metric': 'shared_savings', 'terms': {'benchmark_pmpm': 100, 'sharing_rate': .5, 'msr': .02}}}]
        contract = R.compile_request(self.catalog, q, p)
        self.assertEqual(contract['request']['analyses'][0]['parameters']['terms']['msr'], .02)
        for replacement in (None, .03):
            wrong = copy.deepcopy(p)
            if replacement is None: del wrong['analyses'][0]['parameters']['terms']['msr']
            else: wrong['analyses'][0]['parameters']['terms']['msr'] = replacement
            with self.subTest(value=replacement), self.assertRaisesRegex(R.PlanError, 'Numeric constraints.*2'):
                R.compile_request(self.catalog, q, wrong)

    def test_review_receives_actual_engine_contract_even_when_not_initially_suggested(self):
        p = L.parse(self.catalog, 'Count patients')
        q = 'Count patients and calculate shared savings with a $100 PMPM benchmark and 50% sharing'
        p['analyses'] = [{'operation': 'vbc', 'quote': q.split(' and ', 1)[1], 'parameters': {
            'metric': 'shared_savings', 'terms': {'benchmark_pmpm': 100, 'sharing_rate': .5}}}]
        model = Mock(side_effect=[(json.dumps(p), {}), (json.dumps({'equivalent': True, 'issues': []}), {})])
        original = S.schema_slice
        def sliced(catalog, question, extra, operations, streams):
            view = original(catalog, question, extra, operations, streams)
            if 'vbc' not in operations: view['selected_operator_contracts'].pop('vbc', None)
            return view
        with patch.object(S, 'schema_slice', side_effect=sliced):
            S.interpret(self.catalog, q, model=model, force_model=True)
        reviewed = json.loads(model.call_args_list[-1].args[1])['catalog']['selected_operator_contracts']['vbc']
        self.assertIn('program', reviewed['parameters'])
        self.assertIn('enrolled member-months within period', reviewed['meaning'])
        self.assertIn('not official Medicare Parts A/B reconciliation', reviewed['meaning'])

    def test_unknown_wording_is_logged_without_a_provider_attempt(self):
        self.stack.allow_live=False
        with patch('urllib.request.urlopen',side_effect=AssertionError('No provider request')):
            for q in ('Count patients who prefer morning appointments','Count patients who are universally suitable for care'):
                with self.assertRaises(QuestionNotUnderstood):
                    conversation.handle_compose(self.stack,{'question':q,'conversation_id':'no-provider'})
            result=conversation.handle_compose(self.stack,{'question':'Count patients','conversation_id':'no-provider'})
        self.assertEqual(result['answer']['count'],5)
        self.assertEqual(result['new_paid_api_calls'],0)
        with sqlite3.connect(self.path/'conversations.sqlite3') as db:
            statuses=[json.loads(r[0])['status'] for r in db.execute('SELECT answer_summary FROM turns ORDER BY rowid')]
        self.assertEqual(statuses,['not_understood','not_understood','answered'])


    def test_repairable_quote_error_does_not_force_away_from_typed_engines(self):
        q = 'Count patients who are older than 65'
        correct = L.parse(self.catalog, 'Count patients where age > 65')
        correct['where']['quote'] = 'older than 65'
        wrong = copy.deepcopy(correct); wrong['where']['quote'] = 'older than 64'
        model = Mock(side_effect=[(json.dumps(wrong), {}), (json.dumps(correct), {}),
                                  (json.dumps({'equivalent': True, 'issues': []}), {})])
        result = self.compose_model_fixture(q, model)
        self.assertEqual(result['answer']['count'], 3)
        retry = json.loads(model.call_args_list[1].args[1])['validator_feedback']
        self.assertIn('Repair this specific validation error', retry['instruction'])
        self.assertNotIn('do not retry', retry['instruction'])

    def test_separate_source_spans_do_not_become_unresolved_requirements(self):
        p = L.parse(self.catalog, 'Count patients')
        q = 'How many patients? Give the count.'
        p['select'][0]['quote'] = ['How many', 'count']
        r, _ = self.execute_contract(q, p)
        self.assertEqual(r['value'], 5)
        p['select'][0]['quote'].append('invented wording')
        with self.assertRaises(R.PlanError): self.execute_contract(q, p)

    def test_analytical_worklist_keeps_selected_subset_not_entire_population(self):
        r = {'answer': {'status': 'answered', 'patient_ids': ['B'], 'count': 1}}
        answer = S.engine_answer({}, r, ['A', 'B', 'C'])
        self.assertEqual(answer['patient_ids'], ['B'])
        self.assertEqual(answer['count'], 1)
        r['answer']['patient_ids'] = []
        self.assertEqual(S.engine_answer({}, r, ['A', 'B', 'C'])['patient_ids'], [])
        r['answer']['patient_ids'] = ['OUTSIDE']
        with self.assertRaises(R.PlanError): S.engine_answer({}, r, ['A', 'B', 'C'])

    def test_compound_analyses_retain_each_answer_and_do_not_merge_distinct_worklists(self):
        graph = {'nodes': [{'id': 'population'}, {'id': 'one', 'purpose': 'First analysis'},
                           {'id': 'two', 'purpose': 'Second analysis'}]}
        result = {'answers_by_node': {
            'one': {'status': 'answered', 'patient_ids': ['A'], 'count': 1},
            'two': {'status': 'answered', 'patient_ids': ['B'], 'count': 1}}}
        a = S.engine_answer(graph, result, ['A', 'B', 'C'])
        self.assertEqual(len(a['sections']), 2)
        self.assertEqual(a['sections'][0]['answer']['patient_ids'], ['A'])
        self.assertEqual(a['sections'][1]['answer']['patient_ids'], ['B'])
        self.assertEqual(a['patient_ids'], [])
        self.assertFalse(a['actionable_cohort'])

    def test_wording_based_metric_cannot_replace_executed_engine_definition(self):
        from clinical_core.agere.clinical import answer_checks
        definition = {'source_request_id': 'fixture', 'operation': 'vbc',
                      'definition': 'Allowed dollars divided by received covered member-months'}
        a = {'answer_type': 'value', 'value': 100, 'computed_as': definition.copy()}
        answer_checks.metric_findings(a, 'What is our allowed PMPM?', '2030-05-27')
        self.assertEqual(a['computed_as'], definition)
        self.assertNotIn('computed', a)

    def test_all_question_entrypoints_use_one_planner(self):
        from clinical_core.agere.clinical import economical_planner, together, stack_runtime
        with patch.object(S, 'compose', return_value={'shared': True}) as call:
            self.assertEqual(stack_runtime.Stack.compose(self.stack, 'q'), {'shared': True})
            self.assertEqual(together.compose_together(self.stack, 'q'), {'shared': True})
            self.assertEqual(call.call_count, 2)
        with patch.object(S, 'plan', return_value=({}, {})) as call:
            economical_planner.plan(self.stack, 'q', None, None)
            call.assert_called_once()

    def execute_contract(self, q, plan, population=None):
        c = R.compile_request(self.catalog, q, plan, population=population)
        result = S.sql_query.execute(self.path, {'query': c['query'], 'returns': c['returns']}, patient_ids=population, target=self.catalog.target)
        members = S.sql_query.execute(self.path, {'query': c['membership_query'], 'returns': 'patient_set'}, patient_ids=population, target=self.catalog.target) if c['membership_query'] else None
        return result, members

    def test_group_threshold_and_top_limit_bind_exact_contributors(self):
        q = 'Count claims by payer; only groups with count > 2'
        p = L.parse(self.catalog, 'Count claims by payer')
        p['having'] = {'field': 'result.count', 'op': '>', 'value': 2, 'quote': 'only groups with count > 2'}
        r, members = self.execute_contract(q, p)
        self.assertEqual(r['rows'], [{'payer': 'P1', 'count': 3}])
        self.assertEqual(members['patient_ids'], ['A', 'B'])
        q = 'Count claims by payer; top 1 by count descending'
        p.pop('having');p['limit'] = {'value': 1, 'quote': 'top 1'}
        p['order_by'] = [{'alias': 'count', 'direction': 'desc', 'quote': 'by count descending'}]
        r, members = self.execute_contract(q, p)
        self.assertEqual(members['patient_ids'], ['A', 'B'])

    def test_ratio_uses_same_filtered_denominator_and_keeps_zero_null_distinct(self):
        q = 'Show denial percentage for claims by payer'
        p = L.parse(self.catalog, 'Count claims by payer')
        count = {'kind': 'count', 'quote': 'claims'}
        p['select'][-1] = {'kind': 'ratio', 'alias': 'denial_percentage', 'quote': 'denial percentage', 'scale': 100,
                           'numerator': {**count, 'where': {'op': '=', 'field': 'claim.ever_denied', 'value': 1, 'quote': 'denial'}},
                           'denominator': count}
        r, _ = self.execute_contract(q, p)
        self.assertAlmostEqual(r['rows'][0]['denial_percentage'], 200/3)
        self.assertEqual(r['rows'][1]['denial_percentage'], 100)

    def test_named_claim_relation_is_not_patient_wide(self):
        q = 'Count claims with a denied adjudication'
        p = L.parse(self.catalog, 'Count claims')
        p['where'] = {'op': 'exists', 'table': 'claim_adjudication', 'quote': 'with a denied adjudication',
                      'link': {'outer': 'claim.claim_id', 'inner': 'claim_adjudication.claim_id'},
                      'where': {'op': '=', 'field': 'claim_adjudication.status', 'value': 'denied', 'quote': 'denied'}}
        r, _ = self.execute_contract(q, p)
        self.assertEqual(r['value'], 1)  # two adjudications, one claim; Q2 shares A but is not denied

    def test_unrecognized_extra_measure_fields_cannot_be_ignored(self):
        q = 'List patients'
        p = L.parse(self.catalog, q)
        p['select'][0]['where'] = {'op': '>', 'field': 'patient.age', 'value': 80, 'quote': 'patients'}
        with self.assertRaisesRegex(R.PlanError, 'measure fields'): R.compile_request(self.catalog, q, p)

    def test_partial_medication_state_cannot_create_actionable_cohort(self):
        # a medication that is only ordered or listed ("prescribed") is a received mention: current use stays unresolved
        a = self.ask('List diabetics prescribed a statin but not prescribed an ACE inhibitor or ARB')['answer']
        self.assertEqual(a['status'], 'partial')
        self.assertFalse(a['actionable_cohort'])
        self.assertIn('current use', ' '.join(a['missing']))

    def test_on_a_medication_means_a_picked_up_fill_in_the_last_twelve_months(self):
        # decision context 2026-10-23: "on" / "taking" a medication is current use -- a picked-up fill in the 12 months to the as-of date
        a = self.ask('List diabetics on a statin but not on an ACE inhibitor or ARB')['answer']
        self.assertEqual(a['status'], 'answered')
        self.assertEqual(a['patient_ids'], ['A'])
        self.assertTrue(any('picked-up fill dated 2029-05-28 through 2030-05-27' in x for x in a['limitations']))
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute("UPDATE medication SET event_date = '2028-04-05'")
        self.assertEqual(self.ask('List diabetics on a statin')['answer']['patient_ids'], [])        # filled long ago: not on it now
        self.assertEqual(self.ask('List diabetics who filled a statin')['answer']['patient_ids'], ['A', 'B'])  # "filled" alone asks whether any fill is on record

    def test_raw_received_stream_fields_do_not_require_new_code(self):
        with sqlite3.connect(self.catalog.target) as db:
            db.executescript('CREATE TABLE record(record_id,patient_id,stream,payload);')
            db.executemany('INSERT INTO record VALUES(?,?,?,?)', [('R1','A','new_survey',json.dumps({'barrier_score':7})),
                          ('R2','B','new_survey',json.dumps({'barrier_score':2})),('R3','OUTSIDE','other',json.dumps({'barrier_score':9}))])
        (self.path/'source-profile.json').write_text(json.dumps({'source_digest':'independent-shared-1', 'streams': {'new_survey': {'fields': {'barrier_score':{}}}}}))
        self.catalog = R.Catalog.load(self.path)
        q = 'Count new_survey records with barrier_score > 5'
        p = {'source': {'table':'record','stream':'new_survey','quote':'new_survey records'},
             'select':[{'kind':'count','alias':'records','quote':'Count'}],
             'where':{'op':'>','field':'record.payload.barrier_score','value':5,'quote':'barrier_score > 5'},
             'group_by':[],'order_by':[],'limit':None,'unresolved':[]}
        r,m = self.execute_contract(q,p)
        self.assertEqual(r['value'],1);self.assertEqual(m['patient_ids'],['A'])
        p['source'].pop('stream')
        with self.assertRaisesRegex(R.PlanError,'stream'): self.execute_contract(q,p)

    def test_analytical_engine_requires_exact_registered_contract_and_cohort(self):
        q = 'List patients with diabetes; count patients using the aggregate engine'
        p = L.parse(self.catalog, 'List patients with diabetes')
        p['analyses'] = [{'operation':'aggregate','quote':'count patients using the aggregate engine',
                          'parameters': {'measure':'patients','statistic':'count','group_by':'none','adjust':'none',
                                         'concept':'','window':{'start':'','end':''},'value_aggregate':'latest','top':20}}]
        c = R.compile_request(self.catalog,q,p)
        g = S.graph_for(c)
        self.assertEqual(g['nodes'][0]['operation'], 'query_population')
        self.assertEqual(g['nodes'][1]['inputs'], ['population'])
        p['analyses'][0]['parameters']['made_up'] = True
        with self.assertRaises(ValueError): R.compile_request(self.catalog,q,p)


if __name__ == '__main__': unittest.main()
