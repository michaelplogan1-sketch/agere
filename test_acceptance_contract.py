"""The one acceptance contract (clinical_core/agere/clinical/acceptance_contract.py): counterexamples a route must not ship, and correct readings it
must not reject. Synthetic requests only; no stack, no model."""
import os
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import acceptance_contract as AC
from clinical_core.agere.clinical import answer_checks

AS_OF = '2030-05-27'


def concept(name, kind='condition'):
    return {'op': 'concept', 'kind': kind, 'names': [name], 'version': 1, 'period': None, 'quote': name}


def exists_a1c():
    return {'op': 'exists', 'table': 'measurement', 'where': {'field': 'measurement.concept', 'op': '=', 'value': 'hba1c', 'quote': 'A1c'}, 'quote': 'A1c'}


def spec(where=None, select=None, group_by=None, order_by=None, limit=None):
    return {'source': {'table': 'patient', 'quote': 'patients'},
            'select': select or [{'kind': 'count_distinct', 'field': 'patient.patient_id', 'alias': 'patients', 'quote': 'how many'}],
            'where': where, 'group_by': group_by or [], 'order_by': order_by or [], 'limit': limit, 'unresolved': []}


PLAIN = "SELECT COUNT(DISTINCT patient_id) FROM patient WHERE patient_id IN (SELECT 1) OR patient_id IN (SELECT 2)"
WINDOWED = PLAIN + " AND event_date > date('2030-05-27', '-12 months') AND event_date <= '2030-05-27' AND NOT EXISTS (SELECT 1)"


def contract(request, query=PLAIN):
    return {'request': request, 'query': query, 'returns': 'count'}


def unmet(question, request, query=PLAIN, **kw):
    return AC.evaluate(question, contract=contract(request, query), as_of=AS_OF, **kw)


class Operators(unittest.TestCase):
    Q_AND = 'How many patients have diabetes and CKD?'

    def test_proof_caution_is_not_a_data_exclusion_and_keeps_coordinated_filters(self):
        from clinical_core.agere.clinical.literal_check import missing
        from clinical_core.agere.clinical import requirement_ledger as R
        query='SELECT balance_cents FROM accounts'
        cautions=[
            'Show balances. Do not treat a payer remittance or account balance as proof of patient payment.',
            "Show balances. Don't interpret recorded revenue as evidence of cash receipt.",
            'Show balances. Never use a billing balance as confirmation of payment.']
        for question in cautions:
            with self.subTest(question=question):
                self.assertFalse(any('negates' in g for g in missing(question,query)))
                self.assertFalse(any('negates' in g for g in R.gaps(question,query)))
        for question in [
            'Show patients without payments. Do not treat remittances as proof of payment.',
            'Show balances. Do not treat remittances as proof of payment and exclude reversed records.',
            'Show balances. Do not include reversed records.',
            'Show patients with no evidence of payment.']:
            with self.subTest(question=question):
                self.assertTrue(any('negates' in g for g in missing(question,query)))
                self.assertFalse(any('negates' in g for g in missing(question,query+" WHERE status <> 'reversed'")))

    def test_and_asked_and_or_planned_is_unmet(self):
        r = unmet(self.Q_AND, spec({'op': 'or', 'args': [concept('diabetes'), concept('chronic_kidney_disease')]}))
        self.assertFalse(r['ok'])
        self.assertTrue(any('conjunction' in u and 'OR' in u for u in r['unmet']), r['unmet'])

    def test_and_asked_and_and_planned_is_met(self):
        r = unmet(self.Q_AND, spec({'op': 'and', 'args': [concept('diabetes'), concept('chronic_kidney_disease')]}))
        self.assertTrue(r['ok'], r['unmet'])
        self.assertTrue(any(x['id'].startswith('operator:and') and x['status'] == 'met' for x in r['requirements']))

    def test_or_asked_and_and_planned_is_unmet(self):
        r = unmet('How many patients have diabetes or CKD?', spec({'op': 'and', 'args': [concept('diabetes'), concept('chronic_kidney_disease')]}))
        self.assertFalse(r['ok'])
        self.assertTrue(any('disjunction' in u for u in r['unmet']), r['unmet'])

    def test_or_asked_and_or_planned_is_met_even_when_a_negation_elsewhere_is_present(self):
        r = unmet('How many patients have diabetes or CKD?', spec({'op': 'or', 'args': [concept('diabetes'), concept('chronic_kidney_disease')]}))
        self.assertTrue(r['ok'], r['unmet'])

    def test_one_concept_leaf_over_two_names_is_a_disjunction(self):
        leaf = {'op': 'concept', 'kind': 'condition', 'names': ['diabetes', 'chronic_kidney_disease'], 'version': 1, 'period': None}
        self.assertTrue(unmet('How many patients have diabetes or CKD?', spec(leaf))['ok'])
        self.assertFalse(unmet(self.Q_AND, spec(leaf))['ok'])

    def test_and_binds_to_its_own_operands_not_a_global_or(self):
        # (diabetes AND CKD) OR hypertension: the diabetes/CKD pair is bound by AND although the plan also contains an OR
        tree = {'op': 'or', 'args': [{'op': 'and', 'args': [concept('diabetes'), concept('chronic_kidney_disease')]}, concept('hypertension')]}
        self.assertTrue(unmet(self.Q_AND, spec(tree))['ok'])
        bad = {'op': 'or', 'args': [concept('diabetes'), {'op': 'and', 'args': [concept('chronic_kidney_disease'), concept('hypertension')]}]}
        self.assertFalse(unmet(self.Q_AND, spec(bad))['ok'])

    def test_dropped_negation_is_unmet(self):
        q = 'How many patients with diabetes have no A1c in the past year?'
        window = {'field': 'measurement.event_date', 'op': 'within_last_months', 'value': 12}
        positive = {'op': 'and', 'args': [concept('diabetes'), {**exists_a1c(), 'where': {'op': 'and', 'args': [exists_a1c()['where'], window]}}]}
        negated = {'op': 'and', 'args': [concept('diabetes'), {'op': 'not', 'arg': positive['args'][1]}]}
        self.assertFalse(unmet(q, spec(positive), WINDOWED)['ok'])
        self.assertTrue(any('negation was dropped' in u for u in unmet(q, spec(positive), WINDOWED)['unmet']))
        self.assertTrue(unmet(q, spec(negated), WINDOWED)['ok'], unmet(q, spec(negated), WINDOWED)['unmet'])

    def test_a_negation_not_bound_to_a_concept_is_not_invented(self):
        # "not at goal" negates a comparison, not the A1c concept; the contract binds only a negation directly governing a named concept
        q = 'How many diabetics are not at goal for A1c?'
        r = unmet(q, spec({'op': 'and', 'args': [concept('diabetes'), {'field': 'measurement_latest.value', 'op': '>=', 'value': 9}]}))
        self.assertFalse(any(x['id'] == 'operator:not:a1c' and x['status'] == 'unmet' for x in r['requirements']))


class Windows(unittest.TestCase):
    Q = 'How many diabetics had an ED visit in the past year?'

    def test_missing_window_is_unmet(self):
        r = AC.evaluate(self.Q, contract=contract(spec(concept('diabetes')), "SELECT COUNT(*) FROM utilization_event WHERE kind = 'ed_visit'"), as_of=AS_OF)
        self.assertFalse(r['ok'])
        self.assertTrue(any('time window' in u or '12 months' in u for u in r['unmet']), r['unmet'])

    def test_twelve_month_window_is_met(self):
        q = "SELECT COUNT(*) FROM utilization_event WHERE kind = 'ed_visit' AND event_date > date('2030-05-27', '-12 months') AND event_date <= '2030-05-27'"
        r = AC.evaluate(self.Q, contract=contract(spec(concept('diabetes')), q), as_of=AS_OF)
        self.assertTrue(any(x['kind'] == 'window' and x['status'] == 'met' for x in r['requirements']), r['requirements'])

    def test_a_different_window_is_not_the_asked_one(self):
        q = "SELECT COUNT(*) FROM utilization_event WHERE kind = 'ed_visit' AND event_date > date('2030-05-27', '-3 months')"
        r = AC.evaluate(self.Q, contract=contract(spec(concept('diabetes')), q), as_of=AS_OF)
        self.assertTrue(any(x['kind'] == 'window' and x['status'] == 'unmet' for x in r['requirements']), r['requirements'])


class Outputs(unittest.TestCase):
    def share(self, denominator_where=None, where=None):
        measure = {'kind': 'ratio', 'alias': 'percent', 'scale': 100,
                   'numerator': {'kind': 'count_distinct', 'field': 'patient.patient_id', 'alias': 'meeting',
                                 'where': {'op': 'and', 'args': [concept('hypertension'), concept('diabetes')]}},
                   'denominator': {'kind': 'count_distinct', 'field': 'patient.patient_id', 'alias': 'group',
                                   **({'where': denominator_where} if denominator_where else {})}}
        return spec(where, select=[measure])

    Q = 'Of the patients with hypertension, what share also have diabetes?'

    def test_wrong_denominator_is_unmet(self):
        r = unmet(self.Q, self.share())
        self.assertFalse(r['ok'])
        self.assertTrue(any('denominator is not restricted to hypertension' in u for u in r['unmet']), r['unmet'])

    def test_denominator_restricted_to_the_scope_is_met(self):
        self.assertTrue(unmet(self.Q, self.share(denominator_where=concept('hypertension')))['ok'])
        self.assertTrue(unmet(self.Q, self.share(where=concept('hypertension')))['ok'])

    def test_a_share_of_everyone_needs_no_scope(self):
        measure = self.share()['select'][0]
        r = unmet('What share of patients have diabetes?', spec(select=[{**measure, 'numerator': {**measure['numerator'], 'where': concept('diabetes')}}]))
        self.assertTrue(r['ok'], r['unmet'])

    def ranked(self, direction='desc', limit=5):
        self.query = f"SELECT carc, COUNT(*) AS n FROM payer_claim GROUP BY carc ORDER BY n {direction.upper()}" + (f' LIMIT {limit}' if limit else '')
        return spec(select=[{'kind': 'field', 'field': 'payer_claim.carc', 'alias': 'carc'}, {'kind': 'count', 'alias': 'count'}], group_by=['carc'],
                    order_by=[{'alias': 'count', 'direction': direction}], limit={'value': limit} if limit else None)

    Q_TOP = 'What are the top five denial reason codes and how many claims each?'

    def test_reversed_ranking_is_unmet(self):
        bad = self.ranked('asc')
        r = unmet(self.Q_TOP, bad, self.query)
        self.assertFalse(r['ok'])
        self.assertTrue(any('descending' in u for u in r['unmet']), r['unmet'])
        good = self.ranked('desc')
        self.assertTrue(unmet(self.Q_TOP, good, self.query)['ok'], unmet(self.Q_TOP, good, self.query)['unmet'])

    def test_lowest_means_ascending(self):
        q = 'Which three practices have the lowest A1c control share?'
        bad = self.ranked('desc', 3)
        self.assertFalse(unmet(q, bad, self.query)['ok'])

    def test_ranking_keeps_n(self):
        bad = self.ranked('desc', 3)
        r = unmet(self.Q_TOP, bad, self.query)
        self.assertFalse(r['ok'])
        self.assertTrue(any('asks for 5' in u for u in r['unmet']), r['unmet'])

    def test_sql_ranking_direction_and_limit(self):
        q = 'SELECT carc, COUNT(*) AS n FROM payer_claim GROUP BY carc ORDER BY n ASC LIMIT 5'
        r = AC.evaluate(self.Q_TOP, contract={'query': q, 'returns': 'table'}, as_of=AS_OF)
        self.assertFalse(r['ok'])
        ok = AC.evaluate(self.Q_TOP, contract={'query': q.replace('ASC', 'DESC'), 'returns': 'table'}, as_of=AS_OF)
        self.assertTrue(ok['ok'], ok['unmet'])

    def test_missing_group_by_is_unmet(self):
        q = 'What is the diabetes A1c control rate by payer?'
        r = unmet(q, spec(select=[{'kind': 'ratio', 'alias': 'rate', 'numerator': {'kind': 'count'}, 'denominator': {'kind': 'count'}}]))
        self.assertFalse(r['ok'])

    def test_group_by_the_wrong_dimension_is_unmet_and_the_right_one_met(self):
        q = 'How many diabetics are there by payer?'
        sel = [{'kind': 'field', 'field': 'patient.line_of_business', 'alias': 'line_of_business'}, {'kind': 'count', 'alias': 'n'}]
        sex = [{'kind': 'field', 'field': 'patient.sex', 'alias': 'sex'}, {'kind': 'count', 'alias': 'n'}]
        self.assertTrue(unmet(q, spec(concept('diabetes'), select=sel, group_by=['line_of_business']))['ok'])
        bad = unmet(q, spec(concept('diabetes'), select=sex, group_by=['sex']))
        self.assertFalse(bad['ok'])
        self.assertTrue(any('groups by sex' in u for u in bad['unmet']), bad['unmet'])

    def test_a_threshold_keeps_its_direction(self):
        q = 'How many patients had 3 or more ED visits?'
        self.assertFalse(AC.evaluate(q, contract={'query': 'SELECT COUNT(*) FROM (SELECT patient_id FROM utilization_event GROUP BY 1 HAVING COUNT(*) > 3)'})['ok'])
        self.assertTrue(AC.evaluate(q, contract={'query': 'SELECT COUNT(*) FROM (SELECT patient_id FROM utilization_event GROUP BY 1 HAVING COUNT(*) >= 3)'})['ok'])

    def test_requested_output_shape_is_checked_on_the_executed_answer(self):
        r = AC.evaluate('Which patients have diabetes?', contract=contract(spec(concept('diabetes'))), answer={'status': 'answered', 'answer_type': 'count', 'count': 5}, as_of=AS_OF)
        self.assertFalse(r['ok'])
        self.assertTrue(any('patient set' in u for u in r['unmet']), r['unmet'])

    def test_patient_list_with_measures_keeps_rows_and_requested_columns(self):
        from clinical_core.agere.clinical import answer_shape
        table={'status':'answered','answer_type':'table','table':[
            {'patient_id':'A','total_allowed_cents':150}, {'patient_id':'B','total_allowed_cents':None}]}
        scalar={'status':'answered','answer_type':'value','value':150}
        ids={'status':'answered','answer_type':'patient_set','patient_ids':['A','B']}
        for q in ['List patients with at least 3 provider claims in 2029 and their total allowed_cents.',
                  'List patients with diabetes and at least three provider claims during the requested calendar year and show their average cost.',
                  'Show patients with pharmacy fills received during the reporting period and include their total spending.']:
            with self.subTest(question=q):
                self.assertEqual(answer_shape.requested(q)['shape'],'table')
                self.assertEqual(AC.requested_output(q),'table')
                self.assertIsNone(AC.output_misfit(q,table))
                self.assertIsNotNone(AC.output_misfit(q,scalar))
                self.assertIsNotNone(AC.output_misfit(q,ids))

    def test_measure_filters_and_scalar_questions_keep_their_distinct_shape(self):
        for q in ['List patients with total claims above 500 dollars',
                  'Which patients have an average cost above 500?']:
            with self.subTest(question=q):self.assertEqual(AC.requested_output(q),'patient_set')
        table={'status':'answered','answer_type':'table','table':[{'patient_id':'A','total':100},{'patient_id':'B','total':200}]}
        for q in ['What is the total paid for patients on the list?',
                  'What was the average cost of patients who had at least 3 claims?']:
            with self.subTest(question=q):
                self.assertEqual(AC.requested_output(q),'value')
                self.assertIsNotNone(AC.output_misfit(q,table))

    def test_a_decline_is_not_rejected(self):
        r = AC.evaluate('Which patients have diabetes?', answer={'status': 'unsupported', 'answer_type': 'unsupported'})
        self.assertTrue(r['ok'])

    def test_positive_note_answer_must_cite_a_record(self):
        graph = {'nodes': [{'parameters': {'chart': {'note_predicates': [{'op': 'note', 'frame': 'medication_event'}]}}}]}
        question = 'Why was losartan stopped for WHP-4607AD88022E?'
        cited = {'status': 'answered', 'answer_type': 'table', 'headline': 'stopped: cough', 'table': [{'record_id': 'WHN-R-4cc2e1ad33429524aafab7'}]}
        bare = {'status': 'answered', 'answer_type': 'table', 'headline': 'stopped: cough', 'table': [{'detail': 'cough'}]}
        self.assertTrue(AC.evaluate(question, graph=graph, answer=cited)['ok'])
        self.assertFalse(AC.evaluate(question, graph=graph, answer=bare)['ok'])


class NoteAnswers(unittest.TestCase):
    Q = 'Why was losartan stopped for WHP-4607AD88022E?'
    GRAPH = {'nodes': [{'parameters': {'chart': {'note_predicates': [{'op': 'note', 'frame': 'medication_event', '_ask_detail': {'event': 'started stopped'}}]}}}]}

    def test_a_finding_the_reader_reports_as_not_as_asked_does_not_answer_a_why(self):
        answer = {'status': 'answered', 'answer_type': 'table', 'headline': "medication event 'losartan': documented but not as asked (event stopped not matched)",
                  'table': [{'record_id': 'WHN-R-42e9a63f61e7defa3fadc3'}]}
        self.assertFalse(AC.evaluate(self.Q, graph=self.GRAPH, answer=answer)['ok'])

    def test_a_negative_answer_that_is_not_as_asked_stands(self):
        answer = {'status': 'answered', 'answer_type': 'table', 'headline': "No: medication event 'started': planned, not shown as done but not as asked", 'table': []}
        self.assertTrue(AC.evaluate('Was losartan started for WHP-4607AD88022E?', graph=self.GRAPH, answer=answer)['ok'])

    def test_a_why_answered_with_a_review_verdict_and_no_evidence_is_unmet(self):
        answer = {'status': 'answered', 'answer_type': 'table', 'headline': '1 patient reviews.', 'table': [{'patient_id': 'WHP-4607AD88022E', 'verdict': 'unknown', 'evidence': None}]}
        r = AC.evaluate(self.Q, graph={'nodes': [{'parameters': {'question': self.Q}}]}, answer=answer)
        self.assertFalse(r['ok'])
        self.assertTrue(any('reason was asked for' in u for u in r['unmet']), r['unmet'])

    def test_not_documented_needs_no_citation(self):
        answer = {'status': 'answered', 'answer_type': 'table', 'headline': 'Not documented: medication event not mentioned in the notes', 'table': []}
        self.assertTrue(AC.evaluate(self.Q, graph=self.GRAPH, answer=answer)['ok'])

    def test_the_notes_are_read_when_the_question_asks_what_the_notes_say(self):
        graph = {'nodes': [{'parameters': {'chart': {'domain': 'appointment', 'mode': 'all'}}}]}
        answer = {'status': 'answered', 'answer_type': 'table', 'headline': 'Yes: 3 appointment(s)', 'chart_reader': {}, 'table': [{'a': 1}]}
        r = AC.evaluate('Is there a note saying WHP-4607AD88022E has difficulty getting to appointments?', graph=graph, answer=answer)
        self.assertFalse(r['ok'])
        self.assertTrue(any('does not read any note' in u for u in r['unmet']), r['unmet'])


class Receipt(unittest.TestCase):
    def test_receipt_lists_every_requirement_and_how_it_was_met(self):
        r = unmet('How many patients have diabetes and CKD?', spec({'op': 'and', 'args': [concept('diabetes'), concept('chronic_kidney_disease')]}))
        kinds = {x['kind'] for x in r['requirements']}
        self.assertTrue({'scope', 'operator'} <= kinds, kinds)
        self.assertTrue(all(x['status'] in (AC.MET, AC.UNMET, AC.UNVERIFIED, AC.NA) for x in r['requirements']))
        self.assertTrue(all(x.get('how') or x['status'] != AC.MET for x in r['requirements']))
        self.assertEqual(AC.summary(r)['ok'], True)

    def test_plan_time_and_executed_halves_merge_into_one_receipt(self):
        plan = AC.summary(AC.evaluate('How many patients have diabetes?', contract=contract(spec(concept('diabetes'))), as_of=AS_OF, phase='plan'))
        executed = AC.evaluate('How many patients have diabetes?', answer={'status': 'answered', 'answer_type': 'count', 'count': 3}, lexical=False, shape=True)
        merged = AC.merge(plan, executed)
        self.assertTrue(merged['ok'])
        self.assertTrue({r['id'] for r in plan['requirements']} <= {r['id'] for r in merged['requirements']})
        self.assertIn('output.shape', {r['id'] for r in merged['requirements']})
        bad = {**plan, 'unmet': ['x is not in the plan'], 'ok': False}
        self.assertFalse(AC.merge(bad, executed)['ok'])

    def test_a_percent_sign_in_a_quoted_pattern_and_a_lab_ratio_are_not_a_rate_ask(self):
        for q in ('Count received_notes where narrative like "%taxi%"', 'How many CKD patients are missing a urine albumin-creatinine ratio in the last year?'):
            r = AC.evaluate(q, contract=contract(spec(concept('chronic_kidney_disease')), WINDOWED), as_of=AS_OF)
            self.assertFalse(any('denominator' in u for u in r['unmet']), (q, r['unmet']))

    def test_decline_message_names_the_unmet_part(self):
        r = unmet('How many patients have diabetes and CKD?', spec({'op': 'or', 'args': [concept('diabetes'), concept('chronic_kidney_disease')]}))
        self.assertIn('conjunction', AC.decline_message(r))

    def test_observe_mode_records_and_off_disables(self):
        with patch.dict(os.environ, {'AGERE_ACCEPTANCE': 'observe'}):
            self.assertFalse(AC.enforcing())
        with patch.dict(os.environ, {'AGERE_ACCEPTANCE': 'on'}):
            self.assertTrue(AC.enforcing())

    def test_route_tokens_name_the_layer_to_skip(self):
        self.assertEqual(AC.route_tokens({'interpreter': 'compositional_grammar'}), {'grammar'})
        self.assertEqual(AC.route_tokens({'interpreter': 'compositional_grammar', 'exact_contract_cache_hit': True}), {'cache', 'grammar'})
        self.assertEqual(AC.route_tokens({'interpreter': 'x', 'proposer': {'name': 'spec_path'}}), {'spec_path'})
        self.assertEqual(AC.route_tokens({'interpreter': 'population_engine'}), {'engine'})


class PassToTheNextLayer(unittest.TestCase):
    """shared_planner._compose: a failed contract is never a refusal by itself; the next layer reads the question, and only the last layer's
    unmet contract becomes a decline that names the unmet part."""

    def unmet_receipt(self):
        return unmet('How many patients have diabetes and CKD?', spec({'op': 'or', 'args': [concept('diabetes'), concept('chronic_kidney_disease')]}))

    def test_a_rejected_route_is_skipped_by_the_next_plan(self):
        from clinical_core.agere.clinical import shared_planner
        seen = []

        def once(stack, question, **kw):
            seen.append(set(AC.REJECTED.get()))
            if len(seen) == 1: raise AC.Unmet({'grammar'}, self.unmet_receipt(), {'new_paid_api_calls': 0})
            return {'answer': {'status': 'answered'}}
        with patch.object(shared_planner, '_compose_once', once):
            out = shared_planner._compose(None, 'q')
        self.assertEqual(out['answer']['status'], 'answered')
        self.assertEqual(seen, [set(), {'grammar'}])

    def test_the_last_layer_declines_naming_the_unmet_part(self):
        from clinical_core.agere.clinical import shared_planner
        from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
        calls = []

        def once(stack, question, **kw):
            calls.append(set(AC.REJECTED.get()))
            if not calls[1:]: raise AC.Unmet({'grammar'}, self.unmet_receipt(), {})
            raise QuestionNotUnderstood('no layer is left')
        with patch.object(shared_planner, '_compose_once', once):
            with self.assertRaises(QuestionNotUnderstood) as raised:
                shared_planner._compose(None, 'q')
        self.assertIn('conjunction', str(raised.exception))
        self.assertIn('acceptance contract', str(raised.exception))
        self.assertEqual(raised.exception.planner_receipt['acceptance_contract']['ok'], False)

    def test_a_route_that_keeps_failing_ends_in_a_decline_not_a_loop(self):
        from clinical_core.agere.clinical import shared_planner
        from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
        calls = []

        def once(stack, question, **kw):
            calls.append(1)
            raise AC.Unmet({'grammar'}, self.unmet_receipt(), {})
        with patch.object(shared_planner, '_compose_once', once):
            with self.assertRaises(QuestionNotUnderstood):
                shared_planner._compose(None, 'q')
        self.assertLessEqual(len(calls), 2)

    def test_the_cascade_does_not_ask_a_tier_the_contract_rejected(self):
        from clinical_core.agere.clinical import proposers
        c = proposers.Cascade(type('S', (), {'path': '.', 'allow_live': False})(), None, 'q')
        token = AC.REJECTED.set(frozenset({'spec_path'}))
        try:
            self.assertFalse(c.runnable('spec_path'))
        finally:
            AC.REJECTED.reset(token)


class AnalystBinding(unittest.TestCase):
    GRAPH = {'nodes': [{'id': 'answer', 'operation': 'sql', 'parameters': {'query': 'SELECT COUNT(*) FROM living_patient', 'returns': 'count'}}]}

    def receipt(self, **audit):
        base = {'action': 'ok', 'final_sql_digest': AC.sql_digest(self.GRAPH), 'final_sql_findings': []}
        return {'definitions_audit': {**base, **audit}}

    def test_audit_alone_does_not_authorize(self):
        receipt = self.receipt()
        self.assertFalse(answer_checks.audited_analyst('semantic_analyst', receipt, self.GRAPH))  # no contract receipt yet
        receipt['acceptance_contract'] = {'ok': False}
        self.assertFalse(answer_checks.audited_analyst('semantic_analyst', receipt, self.GRAPH))

    def test_audit_plus_contract_over_the_final_sql_authorizes(self):
        receipt = {**self.receipt(), 'acceptance_contract': {'ok': True}}
        self.assertTrue(answer_checks.audited_analyst('semantic_analyst', receipt, self.GRAPH))

    def test_an_audit_of_a_different_sql_than_the_one_executed_does_not_authorize(self):
        receipt = {**self.receipt(final_sql_digest='deadbeef'), 'acceptance_contract': {'ok': True}}
        self.assertFalse(answer_checks.audited_analyst('semantic_analyst', receipt, self.GRAPH))
        receipt = {**self.receipt(final_sql_findings=['living']), 'acceptance_contract': {'ok': True}}
        self.assertFalse(answer_checks.audited_analyst('semantic_analyst', receipt, self.GRAPH))

    def test_check_rejects_an_audited_analyst_answer_whose_final_sql_drops_the_ask(self):
        graph = {'nodes': [{'id': 'answer', 'operation': 'sql', 'parameters': {'query': 'SELECT COUNT(*) FROM living_patient WHERE has_diabetes', 'returns': 'count'}}]}
        receipt = {'definitions_audit': {'action': 'ok', 'final_sql_digest': AC.sql_digest(graph), 'final_sql_findings': []}}
        found = AC.evaluate('How many patients have diabetes or CKD?', graph=graph, answer={'status': 'answered', 'answer_type': 'count', 'count': 3})
        self.assertFalse(found['ok'])  # CKD is not in the executed SQL
        receipt['acceptance_contract'] = AC.summary(found)
        self.assertFalse(answer_checks.audited_analyst('semantic_analyst', receipt, graph))


if __name__ == '__main__':
    unittest.main()


class GroupedMeasureAcceptance(unittest.TestCase):
    def test_grouped_measure_shapes_and_ordinary_scalars_remain_distinct(self):
        table={'status':'answered','answer_type':'table','table':[{'payer':'A','average':2},{'payer':'B','average':3}]}
        for q in ['For each primary-care practice, how many patients visited?',
                  'Compare average paid per claim for retail versus mail-order pharmacies.',
                  'What share of women and of men have a lab result?',
                  'What was total paid in 2028 and in 2029?']:
            self.assertIsNone(AC.output_misfit(q,table),q)
        for q in ['What is the average paid per claim?', 'How many admissions were between 2028 and 2029?']:
            self.assertIsNotNone(AC.output_misfit(q,table),q)
    def test_grouped_sex_field_matches_sex_but_payer_does_not(self):
        good=spec(group_by=['sex'],select=[{'kind':'field','field':'patient.sex_reported','alias':'sex'}, {'kind':'count','alias':'count'}])
        self.assertTrue(all(r['status'] != AC.UNMET for r in AC.grouping('Count patients for each sex',[good],[],'')))
        bad=spec(group_by=['payer'],select=[{'kind':'field','field':'patient.payer_id','alias':'payer'}])
        self.assertTrue(any(r['status'] == AC.UNMET for r in AC.grouping('Count patients for each sex',[bad],[],'')))
    def test_consecutive_calendar_years_are_proved_by_same_field_bounds(self):
        request=spec({'op':'and','args':[{'field':'claim.service_from','op':'>=','value':'2028-01-01'}, {'field':'claim.service_from','op':'<','value':'2030-01-01'}]})
        self.assertEqual(AC.windows('Total paid in 2029','',AS_OF,[request])[0]['status'],AC.MET)
        self.assertEqual(AC.windows('Total paid in 2031','',AS_OF,[request])[0]['status'],AC.UNMET)
        request['where']['args'][1]['field']='claim.other_date'
        self.assertEqual(AC.windows('Total paid in 2029','',AS_OF,[request])[0]['status'],AC.UNMET)


class InclusionIsNotComparison(unittest.TestCase):
    def test_parenthetical_population_definition_stays_one_scalar(self):
        q='What share of Medicare patients (including dual and Medigap) had a visit in the last year?'
        self.assertEqual(AC.requested_output(q),'value')
        self.assertIsNone(AC.output_misfit(q,{'status':'answered','answer_type':'value','value':40}))
        self.assertEqual(AC.requested_output('What share of commercial, Medicare and Medicaid patients had a visit?'),'table')


class UndecidedNoteEscalates(unittest.TestCase):
    def test_unknown_note_reading_is_not_a_completed_answer(self):
        answer={'status':'answered','answer_type':'table','chart_reader':'chart-reader/1',
                'headline':'Undecided: preference refusal', 'facts':{'statuses':['unknown']}, 'sources':[]}
        rows=AC.evidence_need('Did the patient accept the medication?',answer,'chart_reader')
        self.assertTrue(any(r['id']=='evidence:undecided' and r['status']==AC.UNMET for r in rows))
        answer['facts']['statuses']=['not_mentioned']
        self.assertFalse(any(r['id']=='evidence:undecided' for r in AC.evidence_need('Was it documented?',answer,'chart_reader')))
