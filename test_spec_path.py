"""The spec path in the cascade: order, tier behaviour with fake providers, caching, notes, subject, free-form marking."""
import json
import os
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import (answer_checks as AC, background_continuation as BG, decision_context as DC, proposers as P, request_language as L,
                                          request_plan as R, request_shapes as RS, shared_planner as S, spec_exec as X, spec_fill as F, spec_path as SP,
                                          spec_verify as V)
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
from spec_test_support import Warehouse, FakeS1, FakeModel, AS_OF, FQ, spec_count_hf

Q = 'How many patients have heart failure?'


class Order(unittest.TestCase):
    def test_the_spec_path_follows_reuse_and_precedes_everything_else(self):
        order = list(P.ORDER)
        reuse = [t for t in order if P.STAGES[P.stage_of(t)][0] == 'reuse_certified']
        self.assertEqual(order[:len(reuse)], reuse)
        self.assertEqual(order[len(reuse)], 'spec_path')
        self.assertEqual(P.stage_of('spec_path'), P.STAGE_INDEX['spec_path'])

    def test_governed_engines_and_routers_follow_the_spec_path_and_free_form_writers_are_last(self):
        order = list(P.ORDER)
        for governed in ('s1_metric_router', 's1_operator_router', 'metric_router', 'operator_router'):
            self.assertGreater(order.index(governed), order.index('spec_path'), governed)
        non_free = [order.index(t) for t in order if t not in P.FREE_FORM]
        free = [order.index(t) for t in P.FREE_FORM]
        self.assertGreater(min(free), max(non_free) - 3)  # free-form writers sit in the last stage, behind the governed Claude routers
        for t in ('hosted_verified_sql', 'semantic_analyst', 'hybrid_analysts', 'frontier_graph_planner', 'stack_planner_propose', 'legacy_cost_ladder',
                  'local_normalizer', 'qwen_rewrite_compiler', 'bottled_sql', 'qwen_typed_interpreter', 'local_free_sql'):
            self.assertIn(t, P.FREE_FORM)
            self.assertGreater(order.index(t), order.index('metric_router'), t)
            self.assertGreater(order.index(t), order.index('atomic_system_one'), t)

    def test_every_tier_is_still_registered(self):
        self.assertEqual(sorted(P.ORDER), sorted(P.TIERS))
        self.assertTrue(callable(getattr(P.Cascade, 'tier_spec_path')))

    def test_routers_cannot_move_a_tier_ahead_of_the_spec_path(self):
        shuffled = tuple(reversed(P.ORDER))
        staged = list(P.staged(shuffled))
        self.assertEqual(staged[staged.index('spec_path') - 1] in P.STAGES[0][1], True)
        reuse = [t for t in staged if P.stage_of(t) == 0]
        self.assertEqual(staged.index('spec_path'), len(reuse))

    def test_the_spec_path_runs_without_live_calls_and_is_not_a_paid_tier(self):
        self.assertNotIn('spec_path', P.PAID)
        self.assertTrue(P.enabled('spec_path', SimpleNamespace(allow_live=False)))
        with patch.dict(os.environ, {'AGERE_SPEC_PATH': 'off'}):
            self.assertFalse(SP.enabled())

    def test_background_continuation_keeps_the_local_qwen_tiers_but_not_paid_claude(self):
        cascade = SimpleNamespace(deferred=[{'tier': t} for t in ('bottled_sql', 'local_free_sql', 'hosted_verified_sql', 'hybrid_analysts', 'evidence_review')],
                                  stack=SimpleNamespace(path='/x'), catalog=None, question='q', population=None, hosted_model=None, memory_identity=None,
                                  order_used=(), log=[], claude_down=None)
        with patch.dict(os.environ, {'AGERE_BACKGROUND_CONTINUE': 'off'}):
            receipt = BG.enqueue(cascade)
        self.assertEqual(receipt['tiers'], ['bottled_sql', 'local_free_sql', 'evidence_review'])

    def test_free_form_answers_are_marked_and_say_so(self):
        c = P.Cascade(SimpleNamespace(allow_live=False, path='/x'), SimpleNamespace(identity={}), 'q', ledger=SimpleNamespace(close=lambda: None))
        done = c.finish({'interpreter': 'hosted_verified_sql'}, 'hosted_verified_sql')
        self.assertTrue(done['free_form'])
        self.assertNotIn('free_form', c.finish({'interpreter': 'spec_path'}, 'spec_path'))
        receipt = {'request_contract': {'executed': {'answer': {'status': 'answered', 'headline': '3', 'limitations': []}}}, 'interpretation': {**done},
                   'new_paid_api_calls': 0}
        shown = P.execute(SimpleNamespace(), 'q', {}, receipt, started=0.0)
        self.assertTrue(shown['answer']['free_form'])
        self.assertTrue(any('Free-form answer' in x for x in shown['answer']['limitations']))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.w = Warehouse(allow_live=True)
        self.addCleanup(self.w.close)
        self.s1 = FakeS1()
        self.addCleanup(setattr, V, 'PROVIDER', None)
        self.addCleanup(lambda: F.PROVIDERS.update(decide=None, models=None))
        for name in ('note_frames.evaluate', 'chart_reader.answer', 'chart_reader.resolve_subject'):
            self.addCleanup(R.register_executor, name, None)
        V.PROVIDER = self.s1
        F.PROVIDERS['decide'] = self.s1

    def models(self, *haiku, sonnet=()):
        self.haiku = FakeModel(list(haiku))
        self.sonnet = FakeModel(list(sonnet), name='claude-sonnet-5-5', usd=0.03)
        F.PROVIDERS['models'] = {'haiku': self.haiku, 'sonnet': self.sonnet}

    def cascade(self, q, **kw):
        c = P.Cascade(self.w.stack, self.w.catalog, q, ledger=SimpleNamespace(close=lambda: None, path=None), **kw)
        return c

    def only_spec(self):
        stubs = {'tier_' + n: (lambda self_: None) for n in P.ORDER if n != 'spec_path'}
        return patch.multiple(P.Cascade, **stubs)


UNKNOWN = FQ


class TierBehaviour(Fixture):
    def spec(self, q=UNKNOWN):
        return json.dumps(spec_count_hf(q, source='Patients', measure='how many'))

    def test_an_accepted_spec_is_a_typed_contract_with_read_back_verification_and_checks(self):
        self.models(self.spec())
        with self.only_spec(), DC.scope(UNKNOWN, AS_OF):
            c = self.cascade(UNKNOWN)
            contract, interpretation = c.run()
        self.assertNotIn('kind', contract)  # an ordinary typed request, executed and cached like the grammar's
        self.assertEqual(interpretation['interpreter'], 'spec_path')
        self.assertEqual(interpretation['review'], 'spec_verified')
        self.assertEqual(interpretation['spec_filler'], 'haiku')
        self.assertTrue(interpretation['spec_verification']['confirmed'])
        self.assertIn('Counted living patients', interpretation['read_back'])
        self.assertEqual(interpretation['semantic_review'], {'equivalent': True, 'issues': []})
        self.assertEqual(interpretation['proposer']['name'], 'spec_path')
        self.assertGreaterEqual(c.calls, 1 + interpretation['spec_verification']['calls'])  # one Haiku call + the verified parts
        self.assertGreater(c.usd, 0.01)
        self.assertIn('decision_context', interpretation['answer_checks']['checks_run'])
        self.assertNotIn('answer_guard_cross_check', [a for a in interpretation['answer_checks']['annotations'] if 'Cross-checked by System One' in a])
        self.assertTrue(any('Verified part by part' in a for a in interpretation['answer_checks']['annotations']))

    def test_a_refused_part_is_repaired_once_and_the_repair_is_verified_again(self):
        wrong = {'source': {'table': 'condition', 'quote': 'Patients'}, 'select': [{'kind': 'count', 'alias': 'n', 'quote': 'how many'}],  # counts diagnosis rows, not patients
                 'where': {'field': 'condition.concept', 'op': '=', 'value': 'heart_failure', 'quote': 'heart failure'},
                 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}
        refuse = lambda prompt, options, evidence: ('no', 0.95) if 'diagnosis rows' in ' '.join(e['text'] for e in evidence) else ('yes', 0.95)
        self.s1.answers = refuse
        self.models(json.dumps(wrong), self.spec())
        with self.only_spec(), DC.scope(UNKNOWN, AS_OF):
            contract, interpretation = self.cascade(UNKNOWN).run()
        self.assertEqual(len(self.haiku.calls), 2)
        self.assertIn('spec_review', self.haiku.calls[1]['user'])
        self.assertIn('diagnosis rows', self.haiku.calls[1]['user'])
        self.assertEqual(interpretation['spec_filler'], 'haiku_repair')
        self.assertTrue(interpretation['spec_repair']['first_failed'])
        self.assertTrue(interpretation['spec_verification']['confirmed'])
        self.assertEqual(contract['request']['where']['names'], ['heart_failure'])

    def test_a_part_that_still_fails_after_one_repair_is_unresolved_never_shipped(self):
        self.s1.answers = lambda prompt, options, evidence: ('no', 0.95) if 'heart failure' in ' '.join(e['text'] for e in evidence) else ('yes', 0.95)
        self.models(self.spec(), self.spec())
        with self.only_spec(), DC.scope(UNKNOWN, AS_OF):
            c = self.cascade(UNKNOWN)
            with self.assertRaises(QuestionNotUnderstood):
                c.run()
        rejected = [e for e in c.log if e['proposer'] == 'spec_path' and e['outcome'] == 'rejected']
        self.assertTrue(rejected and 'still refused' in rejected[0]['detail'])
        self.assertEqual(len(self.haiku.calls), 2)

    def test_unconfirmed_parts_make_the_answer_partial_and_say_which(self):
        self.s1.default = ('partly', 0.75)  # System One leans against the reading but cannot clear its bar
        self.models(self.spec())
        with self.only_spec(), DC.scope(UNKNOWN, AS_OF):
            contract, interpretation = self.cascade(UNKNOWN).run()
        self.assertEqual(interpretation['review'], 'spec_partly_verified')
        self.assertTrue(interpretation['partial'])
        self.assertFalse(interpretation['spec_verification']['confirmed'])
        result = {'answer': {'status': 'answered', 'limitations': []}}
        SP.attach(result, interpretation, contract, self.w.catalog, UNKNOWN)
        self.assertEqual(result['answer']['status'], 'partial')
        self.assertTrue(any('could not confirm' in x for x in result['answer']['limitations']))

    def test_parts_system_one_could_not_decide_but_did_not_contradict_stay_answered_with_low_confidence(self):
        self.s1.default = ('yes', 0.5)  # nobody cleared the bar, nothing contradicts
        self.models(self.spec())
        with self.only_spec(), DC.scope(UNKNOWN, AS_OF):
            contract, interpretation = self.cascade(UNKNOWN).run()
        self.assertEqual(interpretation['spec_unconfirmed'], 'soft')
        self.assertNotIn('partial', interpretation)
        result = {'answer': {'status': 'answered', 'limitations': []}}
        SP.attach(result, interpretation, contract, self.w.catalog, UNKNOWN)
        self.assertEqual((result['answer']['status'], result['answer']['confidence']), ('answered', 'low'))
        self.assertTrue(any('could not confirm' in x for x in result['answer']['limitations']))

    def test_two_disagreeing_valid_readings_leave_the_tier_declined_with_both(self):
        rows = {'source': {'table': 'condition', 'quote': 'Patients'}, 'select': [{'kind': 'count', 'alias': 'n', 'quote': 'how many'}],
                'where': {'field': 'condition.concept', 'op': '=', 'value': 'heart_failure', 'quote': 'heart failure'},
                'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}
        self.models(self.spec())
        with self.only_spec(), DC.scope(UNKNOWN, AS_OF), patch.object(F, 's1_fill', lambda ctx: (json.loads(json.dumps(rows)), {})), patch.dict(os.environ, {'AGERE_SPEC_GRACE_SECONDS': '5'}):
            c = self.cascade(UNKNOWN)
            with self.assertRaises(QuestionNotUnderstood):
                c.run()
        detail = next(e['detail'] for e in c.log if e['proposer'] == 'spec_path')
        self.assertIn('more than one reading', detail)

    def test_a_non_live_stack_asks_the_grammar_only(self):
        self.w.stack.allow_live = False
        self.models(self.spec())
        with self.only_spec():
            c = self.cascade(UNKNOWN)
            with self.assertRaises(QuestionNotUnderstood):
                c.run()
        self.assertEqual(self.haiku.calls, [])
        self.assertEqual(self.s1.calls, [])

    def test_an_unresolved_spec_is_held_as_the_decline(self):
        spec = json.loads(self.spec())
        spec['unresolved'] = [{'quote': 'heart failure', 'reason': 'no definition'}]
        spec['where'] = None
        self.models(json.dumps(spec))
        with self.only_spec(), DC.scope(UNKNOWN, AS_OF):
            contract, interpretation = self.cascade(UNKNOWN).run()
        self.assertEqual(contract['request']['unresolved'][0]['reason'], 'no definition')


class Compose(Fixture):
    def test_the_whole_answer_carries_the_read_back_and_a_repeat_is_free(self):
        self.models(json.dumps(spec_count_hf(UNKNOWN, source='Patients', measure='how many')))
        with self.only_spec():
            first = S.compose(self.w.stack, UNKNOWN)
            answer = first['answer']
            self.assertEqual(answer['status'], 'answered')
            self.assertEqual(answer['count'], 3)  # A, C, D: the deceased E is out by the decision context
            self.assertIn('Counted living patients with a documented heart failure diagnosis', answer['read_back'])
            self.assertTrue(answer['spec_verification']['confirmed'])
            self.assertEqual(answer['spec_fill']['winner'], 'haiku')
            self.assertIn('decision_context', answer)
            self.assertEqual(len(self.haiku.calls), 1)
            second = S.compose(self.w.stack, UNKNOWN)
        self.assertEqual(second['answer']['count'], 3)
        self.assertEqual(len(self.haiku.calls), 1)  # the verified spec was saved under this question: no filler runs again
        self.assertTrue(second['planner_receipt']['interpretation']['exact_contract_cache_hit'])

    def test_every_typed_answer_gets_a_read_back_even_from_the_grammar(self):
        self.models()
        shown = S.compose(self.w.stack, Q)
        self.assertEqual(shown['planner_receipt']['interpretation']['interpreter'], 'compositional_grammar')
        self.assertEqual(shown['answer']['count'], 3)
        self.assertIn('Counted living patients with a documented heart failure diagnosis', shown['answer']['read_back'])

    def test_evaluation_mode_saves_nothing(self):
        spec = spec_count_hf(UNKNOWN, source='Patients', measure='how many')
        self.models(json.dumps(spec), json.dumps(spec))
        with self.only_spec(), patch.dict(os.environ, {'AGERE_EVALUATION': '1'}):
            S.compose(self.w.stack, UNKNOWN)
            S.compose(self.w.stack, UNKNOWN)
        self.assertEqual(len(self.haiku.calls), 2)  # nothing was cached in evaluation mode


class Reuse(Fixture):
    QUESTION = 'How many patients are older than 60 and have heart failure?'
    REWORDED = 'How many patients are older than 70 and have heart failure?'

    def spec_for(self, q, age):
        return {'source': {'table': 'patient', 'quote': 'patients'},
                'select': [{'kind': 'count_distinct', 'field': 'patient.patient_id', 'alias': 'patients', 'quote': 'How many'}],
                'where': {'op': 'and', 'quote': q, 'args': [{'op': '>', 'field': 'patient.age', 'value': age, 'quote': f'older than {age}'},
                                                          {'op': 'concept', 'kind': 'condition', 'names': ['heart_failure'], 'version': 1, 'period': None, 'quote': 'heart failure'}]},
                'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}

    def verified(self):
        contract = R.compile_request(self.w.catalog, self.QUESTION, self.spec_for(self.QUESTION, 60))
        interpretation = {'interpreter': 'spec_path', 'review': 'spec_verified', 'execution_accepted': True, 'semantic_review': {'equivalent': True, 'issues': []}}
        return contract, interpretation

    def test_a_verified_spec_is_saved_per_shape_and_reused_for_a_reworded_literal(self):
        contract, interpretation = self.verified()
        policy = S.policy_identity()
        shape_id = RS.save(self.w.path, self.w.catalog, contract, interpretation, policy)
        self.assertTrue(shape_id)
        found = RS.lookup(self.w.path, self.w.catalog, self.REWORDED, policy)
        self.assertIsNotNone(found)
        reused, info = found
        self.assertTrue(info['spec_slot_reuse'])
        self.assertEqual(reused['request']['where']['args'][0]['value'], 70)

    def test_a_hosted_shape_is_not_marked_as_a_spec_reuse(self):
        contract, interpretation = self.verified()
        interpretation['review'] = 'checked_hosted_interpretation'
        RS.save(self.w.path, self.w.catalog, contract, interpretation, S.policy_identity())
        _, info = RS.lookup(self.w.path, self.w.catalog, self.REWORDED, S.policy_identity())
        self.assertFalse(info['spec_slot_reuse'])

    def test_system_one_confirms_the_slot_mapping_of_a_reused_spec_and_can_refuse_it(self):
        contract, _ = self.verified()
        reused = (R.compile_request(self.w.catalog, self.REWORDED, self.spec_for(self.REWORDED, 70)), {'spec_slot_reuse': True, 'execution_accepted': True})
        with patch('clinical_core.agere.clinical.system_one_gate.ready', return_value=True):
            confirmed = S.confirm_spec_reuse(self.w.stack, self.w.catalog, self.REWORDED, reused)
            self.assertTrue(confirmed[1]['slot_mapping_confirmed'])
            self.assertTrue(confirmed[1]['spec_verification']['confirmed'])
            self.assertTrue(self.s1.calls)
            self.s1.answers = lambda prompt, options, evidence: ('no', 0.95) if 'above 70' in ' '.join(e['text'] for e in evidence) else None
            self.assertIsNone(S.confirm_spec_reuse(self.w.stack, self.w.catalog, self.REWORDED, reused))  # refused: the cascade (and its spec path) answers

    def test_without_system_one_the_deterministic_reuse_guards_stand(self):
        reused = (R.compile_request(self.w.catalog, self.REWORDED, self.spec_for(self.REWORDED, 70)), {'spec_slot_reuse': True})
        self.w.stack.allow_live = False
        self.assertIs(S.confirm_spec_reuse(self.w.stack, self.w.catalog, self.REWORDED, reused), reused)


class NotesAndSubject(Fixture):
    NOTE_Q = 'How many patients with diabetes had metformin stopped because of side effects in the last year?'

    def note_spec(self, q=None, combine_with='and'):
        q = q or self.NOTE_Q
        base = L.parse(self.w.catalog, 'How many patients with diabetes')
        note = {'op': 'note', 'frame': 'medication_event', 'concept': {'term': 'metformin', 'codes': ['RxNorm:6809'], 'quote': 'metformin'},
                'statuses': ['documented'], 'detail': {}, 'window': {'start': '2029-05-28', 'end': AS_OF, 'quote': 'in the last year'}, 'combine': 'any',
                'quote': 'had metformin stopped because of side effects in the last year'}
        base['where'] = L.combine(combine_with, [base['where'], note], q)
        return base, q

    def reader(self, results):
        asked = []
        def evaluate(stack, predicate, ids, as_of, ledger):
            asked.append(list(ids))
            return {i: results[i] for i in ids if i in results}
        R.register_executor('note_frames.evaluate', evaluate)
        return asked

    def run_contract(self, plan, q):
        contract = F.validate(self.w.catalog, q, plan)
        graph = S.graph_for(contract)
        receipt = {'route': 'shared_planner', 'request_contract': contract, 'interpretation': {'execution_accepted': True}, 'new_paid_api_calls': 0,
                   'known_api_estimate_usd': 0.0, 'api_cost_known': True}
        with DC.scope(q, AS_OF):
            return S.execute_contract(self.w.stack, q, graph, receipt)

    def reading(self, status, **kw):
        return {'status': status, 'detail': kw.get('detail', {}), 'evidence': kw.get('evidence', [{'record_id': 'N1', 'date': '2029-09-01', 'note_type': 'progress', 'quote': 'stopped metformin: GI upset'}]), 'p': 0.9}

    def test_an_undecided_patient_makes_the_count_a_range_and_the_evidence_is_cited(self):
        asked = self.reader({'SYN-A': self.reading('documented'), 'SYN-B': self.reading('denied'), 'SYN-C': {'status': 'unknown'}})
        plan, q = self.note_spec()
        result = self.run_contract(plan, q)
        a = result['answer']
        self.assertEqual(a['count'], 1)
        self.assertEqual(a['patient_ids'], ['SYN-A'])
        self.assertEqual(a['unknown_patient_ids'], ['SYN-C'])
        self.assertEqual(a['count_range'], {'low': 1, 'high': 2})
        self.assertEqual(a['status'], 'partial')
        self.assertTrue(any('between 1 and 2' in x for x in a['limitations']))
        self.assertEqual(a['note_evidence']['SYN-A'][next(iter(a['note_evidence']['SYN-A']))]['evidence'][0]['record_id'], 'N1')
        self.assertEqual(sorted(asked[0]), ['SYN-A', 'SYN-B', 'SYN-C'])  # only living diabetics were read: the deceased E never reaches the reader
        self.assertEqual(a['note_reading']['steps'][0]['undecided'], 1)

    def test_no_undecided_patient_means_an_exact_answer(self):
        self.reader({'SYN-A': self.reading('documented'), 'SYN-B': self.reading('denied'), 'SYN-C': self.reading('historical')})
        plan, q = self.note_spec()
        a = self.run_contract(plan, q)['answer']
        self.assertEqual((a['status'], a['count']), ('answered', 1))
        self.assertNotIn('count_range', a)

    def test_the_wanted_statuses_and_details_decide(self):
        self.reader({'SYN-A': self.reading('documented', detail={'reason': 'cost'}), 'SYN-B': self.reading('documented', detail={'reason': 'side_effect'}),
                     'SYN-C': self.reading('possible')})
        plan, q = self.note_spec()
        note = plan['where']['args'][1]
        note['statuses'] = ['documented', 'possible']
        note['detail'] = {'reason': ['side_effect']}
        a = self.run_contract(plan, q)['answer']
        self.assertEqual(sorted(a['patient_ids']), ['SYN-B', 'SYN-C'])  # A's documented reason is cost: contradicted; C's possible reading carries no detail to contradict

    def test_a_patient_the_structured_part_already_rules_in_is_never_read_for_an_or(self):
        asked = self.reader({'SYN-D': self.reading('documented')})
        base, q = self.note_spec(q='How many patients have diabetes or had metformin stopped because of side effects in the last year?', combine_with='or')
        plan = L.parse(self.w.catalog, 'How many patients have diabetes')
        note = base['where']['args'][1]
        plan['where'] = L.combine('or', [plan['where'], note], q)
        a = self.run_contract(plan, q)['answer']
        self.assertEqual(asked[0], ['SYN-D'])  # A, B, C have diabetes (true whatever the notes say); only the living non-diabetic D can change
        self.assertEqual(sorted(a['patient_ids']), ['SYN-A', 'SYN-B', 'SYN-C', 'SYN-D'])

    def test_without_a_note_reader_every_candidate_is_undecided_not_no(self):
        plan, q = self.note_spec()
        with patch.object(R, 'executor', lambda name: None):
            a = self.run_contract(plan, q)['answer']
        self.assertEqual(a['count'], 0)
        self.assertEqual(a['unknown_count'], 3)
        self.assertEqual(a['status'], 'partial')
        self.assertFalse(a['note_reading']['steps'][0]['available'])

    def test_a_reader_that_raises_leaves_patients_undecided(self):
        R.register_executor('note_frames.evaluate', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('provider down')))
        plan, q = self.note_spec()
        a = self.run_contract(plan, q)['answer']
        self.assertEqual(a['unknown_count'], 3)
        self.assertIn('provider down', a['note_reading']['steps'][0]['error'])

    def test_substitution_is_three_valued(self):
        sql = "SELECT 1 WHERE (note_pred(b.\"patient_id\", 'n3') = 1) AND x"
        bound = {'n3': {'true': {'P1'}, 'unknown': {'P2'}}}
        out = X.substitute(sql, bound)
        self.assertIn("WHEN b.\"patient_id\" IN (SELECT value FROM json_each('[\"P1\"]')) THEN 1", out)
        self.assertIn('THEN NULL ELSE 0 END', out)
        self.assertIn('CAST(NULL AS INTEGER)', X.substitute(sql, {}))

    def test_a_named_patient_is_answered_even_if_deceased_and_the_answer_says_so(self):
        q = 'What is the sex of SYN-E?'
        spec = {'source': {'table': 'patient', 'quote': 'SYN-E'}, 'select': [{'kind': 'field', 'field': 'patient.sex_reported', 'alias': 'sex', 'quote': 'sex'}],
                'where': None, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': [], 'subject': {'kind': 'patient', 'patient_id': 'SYN-E', 'quote': 'SYN-E'}}
        a = self.run_contract(spec, q)['answer']
        self.assertEqual(a['patient_ids'], ['SYN-E'])
        self.assertTrue(any('SYN-E is deceased' in x for x in a['limitations']))

    def test_a_chart_level_note_answer_goes_to_the_chart_reader(self):
        seen = {}
        def answer(stack, spec, as_of, ledger):
            seen.update(spec=spec, as_of=as_of)
            return {'status': 'answered', 'answer_type': 'note_answer', 'headline': 'Declined statin: cost (N1)', 'value': 'declined', 'sources': [{'record_id': 'N1'}]}
        R.register_executor('chart_reader.answer', answer)
        q = 'Why did SYN-A decline the statin?'
        spec = {'source': {'table': 'patient', 'quote': 'SYN-A'}, 'select': [{'kind': 'note_answer', 'alias': 'note_answer', 'quote': 'Why'}],
                'where': {'op': 'note', 'frame': 'preference_refusal', 'concept': {'term': 'statin', 'quote': 'statin'}, 'statuses': ['documented'], 'quote': 'decline the statin'},
                'group_by': [], 'order_by': [], 'limit': None, 'unresolved': [], 'subject': {'kind': 'patient', 'patient_id': 'SYN-A', 'quote': 'SYN-A'}}
        result = self.run_contract(spec, q)
        self.assertEqual(result['answer']['headline'], 'Declined statin: cost (N1)')
        self.assertEqual(seen['as_of'], AS_OF)
        self.assertEqual(seen['spec']['subject']['patient_id'], 'SYN-A')
        self.assertEqual(result['request_contract']['returns'], 'note_answer')

    def test_a_note_answer_needs_a_subject(self):
        q = 'Why did patients decline the statin?'
        spec = {'source': {'table': 'patient', 'quote': 'patients'}, 'select': [{'kind': 'note_answer', 'alias': 'note_answer', 'quote': 'Why'}],
                'where': None, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}
        with self.assertRaises(F.SpecInvalid):
            F.validate(self.w.catalog, q, spec)


class ChecksReplaceTheCrossCheck(unittest.TestCase):
    def test_a_confirmed_spec_skips_the_result_summary_cross_check(self):
        receipt = {'spec_verification': {'confirmed': True, 'verdict': 'confirmed'}}
        self.assertTrue(AC.spec_confirmed(receipt))
        self.assertFalse(AC.spec_confirmed({'spec_verification': {'confirmed': False, 'verdict': 'unverified'}}))
        self.assertFalse(AC.spec_confirmed({}))
        verdict = {'annotations': [], 'accept': True, 'reasons': [], 'cost': {'new_paid_api_calls': 0, 'usd': 0.0}}
        stack = SimpleNamespace(allow_live=True)
        with patch('clinical_core.agere.clinical.system_one_cross_check.cross_check', side_effect=AssertionError('the cross-check must not run')):
            out = AC._cross_check(stack, 'q', {}, receipt, {'status': 'answered'}, None, object(), None, {}, verdict)
        self.assertEqual(out, 'spec_verified')
        self.assertTrue(any('Verified part by part' in a for a in verdict['annotations']))

    def test_the_other_mandatory_checks_still_run_for_a_spec_answer(self):
        self.assertIn('decision_context', AC.MANDATORY_CHECKS)
        self.assertIn('answer_shape_review', AC.MANDATORY_CHECKS)


if __name__ == '__main__':
    unittest.main()
