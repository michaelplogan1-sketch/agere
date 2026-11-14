"""Filling a question spec three ways in parallel and choosing one (spec_fill.py): fake providers, tiny warehouse, no network."""
import copy
import json
import time
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import request_language as L, request_plan as R, spec_fill as F, terminology
from spec_test_support import Warehouse, FakeS1, FakeModel, AS_OF, spec_count_hf

Q = 'How many patients have heart failure?'
CONDITION_ROWS = {'source': {'table': 'condition', 'quote': 'patients'},
                  'select': [{'kind': 'count', 'alias': 'n', 'quote': 'How many'}],
                  'where': {'field': 'condition.concept', 'op': '=', 'value': 'heart_failure', 'quote': 'heart failure'},
                  'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}


def note_reader(ids_true=()):
    return lambda stack, predicate, ids, as_of, ledger: {i: {'status': 'documented' if i in ids_true else 'denied', 'detail': {}, 'evidence': [], 'p': 0.9} for i in ids}


class Base(unittest.TestCase):
    def setUp(self):
        self.w = Warehouse()
        self.addCleanup(self.w.close)
        self.addCleanup(lambda: R.register_executor('note_frames.evaluate', None))
        self.addCleanup(lambda: R.register_executor('chart_reader.resolve_subject', None))

    def fill(self, q, haiku=None, s1=None, sonnet=None, **kw):
        models = {'haiku': haiku or FakeModel([]), 'sonnet': sonnet or FakeModel([], name='claude-sonnet-5-5', usd=0.03)}
        kw.setdefault('grace', 0.05)
        return F.fill(self.w.stack, self.w.catalog, q, models=models, decide=s1 or FakeS1(), **kw), models


class GrammarFirst(Base):
    def test_a_spec_the_grammar_fills_starts_no_paid_filler(self):
        s1 = FakeS1()
        out, models = self.fill(Q, s1=s1)
        self.assertEqual(out['winner'], 'grammar')
        self.assertEqual(out['agreement'], 'single')
        self.assertEqual(models['haiku'].calls, [])
        self.assertEqual(s1.calls, [])
        self.assertEqual(out['receipt']['calls'], 0)
        self.assertEqual(out['receipt']['usd'], 0.0)
        self.assertEqual(out['receipt']['fillers']['grammar']['status'], 'valid')
        self.assertNotIn('haiku', out['receipt']['fillers'])

    def test_receipts_say_which_filler_won_and_how_long_each_took(self):
        out, _ = self.fill(Q)
        r = out['receipt']
        self.assertEqual(r['winner'], 'grammar')
        self.assertIn('seconds', r['fillers']['grammar'])
        self.assertIn('seconds', r)


class HaikuFiller(Base):
    UNKNOWN = 'How many patients have pump failure?'  # a term the grammar does not know

    def test_failed_typed_proposals_and_each_failure_survive_the_filler_receipt(self):
        bad=spec_count_hf(Q);bad['where']['quote']='not in this question'
        other=copy.deepcopy(bad);other['source']['table']='not_received'
        with self.assertRaises(F.SpecNotFilled) as caught:
            self.fill(Q,haiku=FakeModel([json.dumps(bad),json.dumps(other)]),fillers=('haiku',))
        receipt=caught.exception.receipt;notes=receipt['fillers']['haiku']['notes']
        self.assertEqual(receipt['calls'],2)
        self.assertEqual([r['proposal']for r in notes['rejected_proposals']],[bad,other])
        self.assertEqual(len(notes['calls']),2)
        for item in notes['rejected_proposals']:
            self.assertEqual(item['question_fingerprint'],R.fingerprint(Q))
            self.assertEqual(item['proposal_fingerprint'],R.fingerprint(item['proposal']))
            self.assertEqual(item['authority'],'unverified_proposal')
            self.assertFalse(item['executed']);self.assertTrue(item['validation_error'])

    def spec_for(self, q):
        spec = spec_count_hf(q)
        spec['where']['quote'] = 'pump failure'
        return spec

    def test_one_haiku_call_writes_the_spec_with_the_decision_context_and_no_sql(self):
        q = 'How many patients have heart failure and diabetes?'
        spec = spec_count_hf(q)
        spec['where'] = {'op': 'and', 'quote': q, 'args': [spec['where'], {'op': 'concept', 'kind': 'condition', 'names': ['diabetes'], 'version': 1, 'period': None, 'quote': 'diabetes'}]}
        haiku = FakeModel([json.dumps(spec)])
        out, _ = self.fill(q, haiku=haiku, fillers=('haiku',))
        self.assertEqual(out['winner'], 'haiku')
        self.assertEqual(len(haiku.calls), 1)
        system, user = haiku.calls[0]['system'], haiku.calls[0]['user']
        self.assertIn('DECISION CONTEXT', system)
        self.assertIn('QUESTION SPEC', system)
        self.assertIn('never SQL', system)
        self.assertNotIn('Evidence discovery is available', system)  # the free analysis / program / discovery parts are not in the spec prompt
        self.assertIn('as_of', user)
        self.assertEqual(out['receipt']['calls'], 1)
        self.assertAlmostEqual(out['receipt']['usd'], 0.01)
        self.assertEqual(out['contract']['returns'], 'count')

    def test_a_repair_from_validator_feedback_is_one_more_haiku_call(self):
        bad = spec_count_hf(Q)
        bad['where']['quote'] = 'heart failure patients with'  # not a literal fragment bound to a leaf
        haiku = FakeModel([json.dumps(bad), json.dumps(spec_count_hf(Q))])
        out, models = self.fill(Q, haiku=haiku, fillers=('haiku',))
        self.assertEqual(out['winner'], 'haiku')
        self.assertEqual(len(haiku.calls), 2)
        self.assertIn('validator_feedback', haiku.calls[1]['user'])
        self.assertIn('error', haiku.calls[1]['user'])
        self.assertEqual(models['sonnet'].calls, [])
        self.assertEqual(out['candidate']['notes']['attempt'], 2)

    def test_sonnet_is_only_the_escalation_rung_after_haiku_failed_validation_twice_and_never_opus(self):
        from clinical_core.agere import model_policy
        bad = json.dumps({'source': {'table': 'patient', 'quote': 'nothing'}})
        haiku = FakeModel([bad, bad])
        sonnet = FakeModel([json.dumps(spec_count_hf(Q))], name='claude-sonnet-5-5', usd=0.03)
        with self.assertRaises(F.SpecNotFilled) as ctx:
            self.fill(Q, haiku=haiku, sonnet=sonnet, fillers=('haiku',))
        self.assertEqual((len(haiku.calls), len(sonnet.calls)), (2, 0))  # the Haiku rung never reaches Sonnet
        self.assertEqual(ctx.exception.receipt['calls'], 2)
        with model_policy.escalation():  # the cascade runs this only after every tier's Haiku attempt failed
            out, _ = self.fill(Q, haiku=FakeModel([]), sonnet=sonnet, fillers=('haiku',))
        self.assertEqual(len(sonnet.calls), 1)
        self.assertAlmostEqual(out['receipt']['usd'], 0.03)
        names = [c['model'] for c in out['candidate']['notes']['calls']]
        self.assertEqual(names, ['claude-sonnet-5-5'])
        self.assertFalse(any('opus' in n for n in names))

    def test_haiku_failing_twice_and_then_sonnet_failing_leaves_no_spec_and_says_why(self):
        from clinical_core.agere import model_policy
        bad = json.dumps({'source': {'table': 'patient', 'quote': 'nothing'}})
        with self.assertRaises(F.SpecNotFilled) as ctx:
            self.fill(Q, haiku=FakeModel([bad, bad]), sonnet=FakeModel([bad], name='claude-sonnet-5-5'), fillers=('haiku',))
        self.assertIn('haiku', str(ctx.exception))
        self.assertEqual(ctx.exception.receipt['fillers']['haiku']['status'], 'invalid')
        self.assertEqual(ctx.exception.receipt['calls'], 2)
        with model_policy.escalation(), self.assertRaises(F.SpecNotFilled) as again:
            self.fill(Q, haiku=FakeModel([]), sonnet=FakeModel([bad], name='claude-sonnet-5-5'), fillers=('haiku',))
        self.assertEqual(again.exception.receipt['calls'], 1)

    def test_an_analysis_or_program_reply_is_refused_with_feedback(self):
        haiku = FakeModel([json.dumps({'analysis': {'x': 1}}), json.dumps(spec_count_hf(Q))])
        out, _ = self.fill(Q, haiku=haiku, fillers=('haiku',))
        self.assertEqual(len(haiku.calls), 2)
        self.assertIn('typed request', haiku.calls[1]['user'])

    def test_a_schema_request_is_part_of_the_call_not_a_failed_validation(self):
        haiku = FakeModel([json.dumps({'need_tables': ['condition']}), json.dumps(spec_count_hf(Q))])
        out, models = self.fill(Q, haiku=haiku, sonnet=FakeModel([]), fillers=('haiku',))
        self.assertEqual(out['candidate']['notes']['attempt'], 1)
        self.assertEqual(models['sonnet'].calls, [])

    def test_an_unresolved_spec_is_held_not_executed(self):
        spec = spec_count_hf(Q)
        spec['unresolved'] = [{'quote': 'heart failure', 'reason': 'no such definition'}]
        spec['where'] = None
        with self.assertRaises(F.SpecNotFilled) as ctx:
            self.fill(Q, haiku=FakeModel([json.dumps(spec)]), fillers=('haiku',))
        self.assertIsNotNone(ctx.exception.held)
        self.assertIn('unresolved', str(ctx.exception))

    def test_the_prompt_carries_the_note_frames_only_when_a_note_reader_exists(self):
        haiku = FakeModel([json.dumps(spec_count_hf(Q))])
        with patch.object(R, 'executor', lambda name: None):  # builder A's real module is importable now: take it away
            self.fill(Q, haiku=haiku, fillers=('haiku',))
        self.assertIn('no note reader', haiku.calls[0]['system'])
        R.register_executor('note_frames.evaluate', note_reader())
        haiku = FakeModel([json.dumps(spec_count_hf(Q))])
        self.fill(Q, haiku=haiku, fillers=('haiku',))
        system = haiku.calls[0]['system']
        for needle in ('medication_event', 'note_contradiction', 'not_mentioned', 'planned_not_done', 'conflict', 'scanned_document_text'):
            self.assertIn(needle, system)

    def test_a_subject_hint_from_the_chart_reader_reaches_the_prompt(self):
        R.register_executor('chart_reader.resolve_subject', lambda stack, text: {'patient_id': 'SYN-A', 'quote': 'SYN-A'})
        haiku = FakeModel([json.dumps(spec_count_hf(Q))])
        self.fill(Q, haiku=haiku, fillers=('haiku',))
        self.assertIn('SYN-A', haiku.calls[0]['user'])


class SystemOneFiller(Base):
    def test_an_unknown_clinical_term_is_mapped_by_a_typed_choice_then_the_grammar_recompiles(self):
        q = 'How many patients have pump failure?'
        with self.assertRaises(R.PlanError):
            L.parse(self.w.catalog, q)  # the grammar alone cannot read it
        mapped = {'label': 'pump failure', 'kind': 'condition', 'concept': 'heart_failure', 'classes': None, 'codes': {'codes': ['I50']}, 'method': 'system_one',
                  'candidates': [], 'cost_usd': 0.0002, 'calls': 1}
        with patch.object(terminology, 'map_label', return_value=mapped):
            out, models = self.fill(q, fillers=('grammar', 's1_slots'))
        self.assertEqual(out['winner'], 's1_slots')
        self.assertEqual(out['plan']['where']['names'], ['heart_failure'])
        self.assertIn('pump failure', out['plan']['where']['quote'])
        self.assertEqual(out['receipt']['fillers']['s1_slots']['calls'], 1)
        self.assertEqual(models['haiku'].calls, [])

    def test_a_trailing_note_criterion_is_filled_from_closed_lists(self):
        R.register_executor('note_frames.evaluate', note_reader())
        q = 'How many patients with diabetes had metformin stopped because of side effects in the last year?'
        def choices(prompt, options, evidence):
            if 'What kind of condition is this clause' in prompt: return ('note_criterion', 0.9)
            if 'Which kind of statement in the notes' in prompt: return ('medication_event', 0.9)
            if 'What must the notes say' in prompt: return ('documented', 0.9)
            return ('not_stated', 0.9)
        s1 = FakeS1(choices)
        out, _ = self.fill(q, s1=s1, fillers=('grammar', 's1_slots'))
        self.assertEqual(out['winner'], 's1_slots')
        note = out['plan']['where']['args'][1]
        self.assertEqual((note['op'], note['frame'], note['statuses'], note['combine']), ('note', 'medication_event', ['documented'], 'any'))
        self.assertEqual(note['window'], {'start': '2029-05-28', 'end': AS_OF, 'quote': 'in the last year'})
        self.assertIn('metformin', note['concept']['term'])
        self.assertEqual(out['plan']['where']['args'][0]['kind'], 'condition')
        self.assertEqual(out['contract']['note_predicates'][next(iter(out['contract']['note_predicates']))]['frame'], 'medication_event')
        self.assertGreaterEqual(len(s1.calls), 3)  # clause kind, frame, status, then any detail field the clause cues

    def test_without_a_note_reader_a_note_clause_is_not_invented(self):
        q = 'How many patients with diabetes had metformin stopped because of side effects in the last year?'
        s1 = FakeS1(lambda *a: ('note_criterion', 0.9))
        none = {'label': 'x', 'kind': None, 'concept': None, 'classes': None, 'codes': None, 'method': 'abstained', 'candidates': [], 'cost_usd': 0.0, 'calls': 0}
        with patch.object(terminology, 'map_label', return_value=none), patch.object(R, 'executor', lambda name: None), self.assertRaises(F.SpecNotFilled):
            self.fill(q, s1=s1, fillers=('grammar', 's1_slots'))
        self.assertEqual(s1.calls, [])  # no note clause was offered to System One

    def test_an_unsure_choice_declines_instead_of_guessing(self):
        R.register_executor('note_frames.evaluate', note_reader())
        q = 'How many patients with diabetes had metformin stopped because of side effects in the last year?'
        with self.assertRaises(F.SpecNotFilled):
            self.fill(q, s1=FakeS1(lambda *a: ('note_criterion', 0.4)), fillers=('grammar', 's1_slots'))


class Choosing(Base):
    def two(self, a, b, **kw):
        """S1 slots returns spec a (via patched fill function), Haiku returns spec b."""
        kw.setdefault('grace', 5.0)  # the point is the choice rule, not the race
        with patch.object(F, 's1_fill', lambda ctx: (copy.deepcopy(a), {})):
            return self.fill(Q, haiku=FakeModel([json.dumps(b)]), fillers=('s1_slots', 'haiku'), **kw)

    def test_first_valid_wins_when_only_one_validates(self):
        out, _ = self.two(spec_count_hf(Q), {'source': {'table': 'patient', 'quote': 'x'}})
        self.assertEqual(out['winner'], 's1_slots')
        self.assertEqual(out['agreement'], 'single')
        self.assertEqual(out['receipt']['fillers']['haiku']['status'], 'invalid')

    def test_agreeing_specs_prefer_the_cheaper_filler(self):
        other = spec_count_hf(Q)
        other['select'][0]['alias'] = 'n_patients'  # wording differs, meaning does not
        out, _ = self.two(spec_count_hf(Q), other)
        self.assertEqual(out['winner'], 's1_slots')
        self.assertEqual(out['agreement'], 'agree')

    def test_disagreeing_readings_go_to_system_one_verification(self):
        scores = {'s1_slots': {'confirmed': False, 'failed': ['measure.0'], 'score': 0.4}, 'haiku': {'confirmed': True, 'failed': [], 'score': 0.9}}
        verify = lambda cand: scores[cand['name']]
        out, _ = self.two(CONDITION_ROWS, spec_count_hf(Q), verify=verify)
        self.assertEqual(out['winner'], 'haiku')
        self.assertEqual(out['agreement'], 'disagree_system_one')
        self.assertEqual(len(out['readings']), 2)
        self.assertEqual(out['candidate']['verification']['score'], 0.9)

    def test_a_tie_reports_both_readings_instead_of_picking(self):
        verify = lambda cand: {'confirmed': True, 'failed': [], 'score': 0.9}
        with self.assertRaises(F.SpecNotFilled) as ctx:
            self.two(CONDITION_ROWS, spec_count_hf(Q), verify=verify)
        self.assertEqual(len(ctx.exception.readings), 2)
        text = str(ctx.exception)
        self.assertIn('s1_slots:', text)
        self.assertIn('haiku:', text)
        self.assertIn('Counted living patients', text)

    def test_with_no_verifier_a_disagreement_is_reported(self):
        with self.assertRaises(F.SpecNotFilled) as ctx:
            self.two(CONDITION_ROWS, spec_count_hf(Q))
        self.assertEqual(ctx.exception.receipt['agreement'], 'ambiguous')

    def test_the_grammar_is_preferred_when_it_disagrees(self):
        c = F.validate(self.w.catalog, Q, spec_count_hf(Q))
        grammar = {'name': 'grammar', 'plan': L.parse(self.w.catalog, Q), 'contract': F.validate(self.w.catalog, Q, L.parse(self.w.catalog, Q))}
        other = {'name': 'haiku', 'plan': copy.deepcopy(CONDITION_ROWS), 'contract': F.validate(self.w.catalog, Q, CONDITION_ROWS)}
        ctx = F.Ctx(self.w.stack, self.w.catalog, Q)
        chosen, how, readings = F.choose(ctx, [other, grammar])
        self.assertEqual((chosen['name'], how), ('grammar', 'disagree_grammar_preferred'))
        self.assertEqual(len(readings), 2)

    def test_a_slow_second_filler_is_cancelled_after_the_grace_window(self):
        slow = FakeModel([json.dumps(spec_count_hf(Q))], delay=1.5)
        started = time.perf_counter()
        with patch.object(F, 's1_fill', lambda ctx: (spec_count_hf(Q), {})):
            out, _ = self.fill(Q, haiku=slow, fillers=('s1_slots', 'haiku'), grace=0.1)
        self.assertLess(time.perf_counter() - started, 1.2)
        self.assertEqual(out['winner'], 's1_slots')
        self.assertEqual(out['receipt']['fillers']['haiku']['status'], 'cancelled')

    def test_a_filler_past_its_own_deadline_is_a_timeout_and_does_not_hold_the_question(self):
        slow = FakeModel([json.dumps(spec_count_hf(Q))], delay=1.5)
        started = time.perf_counter()
        with patch.dict('os.environ', {'AGERE_SPEC_HAIKU_SECONDS': '0.3'}), self.assertRaises(F.SpecNotFilled) as ctx:
            self.fill(Q, haiku=slow, fillers=('s1_slots', 'haiku'), grace=2.0)  # S1 slots has nothing to fill; Haiku outlives its own deadline
        self.assertLess(time.perf_counter() - started, 1.2)
        self.assertEqual(ctx.exception.receipt['fillers']['haiku']['status'], 'timeout')


class Validation(Base):
    def invalid(self, q, spec, needle):
        with self.assertRaises(F.SpecInvalid) as ctx:
            F.validate(self.w.catalog, q, spec)
        self.assertIn(needle, str(ctx.exception))

    def test_a_dropped_requirement_is_caught_by_the_compiler_and_by_the_literal_check(self):
        q = 'How many patients have heart failure and diabetes?'
        self.invalid(q, spec_count_hf(q), 'Unaccounted original request text: diabetes')
        from clinical_core.agere.clinical import requirement_ledger
        with patch.object(requirement_ledger, 'gaps', return_value=['an AND turned into an OR']):
            self.invalid(Q, spec_count_hf(Q), 'literal check: the spec drops an AND turned into an OR')

    def test_the_decision_context_window_must_reach_the_spec(self):
        q = 'How many patients have heart failure in the last year?'
        spec = spec_count_hf(q)
        spec['where'] = {'op': 'and', 'quote': q, 'args': [spec['where'], {'op': '>', 'field': 'patient.age', 'value': 0, 'quote': 'in the last year'}]}
        self.invalid(q, spec, 'window')

    def test_a_note_predicate_is_validated_against_the_closed_lists(self):
        q = 'How many patients with diabetes had metformin stopped?'
        base = L.parse(self.w.catalog, 'How many patients with diabetes')
        def with_note(**changes):
            note = {'op': 'note', 'frame': 'medication_event', 'concept': {'term': 'metformin', 'quote': 'metformin'}, 'statuses': ['documented'], 'quote': 'had metformin stopped'}
            note.update(changes)
            spec = copy.deepcopy(base)
            spec['where'] = L.combine('and', [spec['where'], note], q)
            return spec
        self.assertTrue(F.validate(self.w.catalog, q, with_note())['note_predicates'])
        self.invalid(q, with_note(frame='made_up'), 'Unknown note frame')
        self.invalid(q, with_note(statuses=['maybe']), 'statuses')
        self.invalid(q, with_note(combine='sometimes'), 'combine')
        self.invalid(q, with_note(sources=['tweets']), 'sources')
        self.invalid(q, with_note(window={'start': '2031-01-01', 'end': '2031-12-31'}), 'snapshot')
        self.invalid(q, with_note(concept={'term': ''}), 'concept')

    def test_a_rolling_note_window_is_normalized_to_the_decision_context(self):
        q = 'How many patients with diabetes had metformin stopped in the last year?'
        base = L.parse(self.w.catalog, 'How many patients with diabetes')
        note = {'op': 'note', 'frame': 'medication_event', 'concept': {'term': 'metformin', 'quote': 'metformin'}, 'statuses': ['documented'],
                'window': {'start': '2029-01-01', 'end': '2029-12-31', 'quote': 'in the last year'}, 'quote': 'had metformin stopped in the last year'}
        base['where'] = L.combine('and', [base['where'], note], q)
        contract = F.validate(self.w.catalog, q, base)
        stored = next(iter(contract['note_predicates'].values()))
        self.assertEqual((stored['window']['start'], stored['window']['end']), ('2029-05-28', AS_OF))

    def test_a_concept_quote_wider_than_the_concept_is_refit_to_the_grammars_own_reading(self):
        q = 'Patients with a statin fill in the last year, how many?'
        spec = {'source': {'table': 'patient', 'quote': 'Patients'}, 'select': [{'kind': 'count_distinct', 'field': 'patient.patient_id', 'alias': 'n', 'quote': 'how many'}],
                'where': {'op': 'concept', 'kind': 'drug_fill', 'names': ['statin'], 'version': 1, 'period': {'start': '2029-05-28', 'end': AS_OF},
                          'quote': 'a statin fill in the last year'}, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}
        contract = F.validate(self.w.catalog, q, spec)
        leaf = contract['request']['where']
        self.assertEqual(leaf['op'], 'exists')  # the grammar's own form of "a statin fill in the last year"
        self.assertIn('statin', json.dumps(leaf))
        self.assertIn('within_last_months', json.dumps(leaf))
        wrong = copy.deepcopy(spec)
        wrong['where']['names'] = ['arb']  # a different drug is never swapped in
        with self.assertRaises(F.SpecInvalid):
            F.validate(self.w.catalog, q, wrong)

    def test_a_subject_must_be_a_patient_in_the_warehouse(self):
        q = 'What is the latest HbA1c for SYN-A?'
        spec = {'source': {'table': 'patient', 'quote': 'SYN-A'}, 'select': [{'kind': 'field', 'field': 'patient.sex_reported', 'alias': 'sex', 'quote': 'HbA1c'}],
                'where': None, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': [], 'subject': {'kind': 'patient', 'patient_id': 'SYN-NOPE', 'quote': 'SYN-A'}}
        self.invalid(q, spec, 'not in the received warehouse')

    def test_a_subject_binds_the_patient_and_skips_the_living_rule(self):
        q = 'What is the sex of SYN-E?'
        spec = {'source': {'table': 'patient', 'quote': 'SYN-E'}, 'select': [{'kind': 'field', 'field': 'patient.sex_reported', 'alias': 'sex', 'quote': 'sex'}],
                'where': None, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': [],
                'subject': {'kind': 'patient', 'patient_id': 'SYN-E', 'quote': 'SYN-E'}}
        contract = F.validate(self.w.catalog, q, spec)
        self.assertIn("b.patient_id = 'SYN-E'", contract['query'])
        self.assertNotIn('death', contract['query'])
        self.assertEqual(contract['subject']['patient_id'], 'SYN-E')


if __name__ == '__main__':
    unittest.main()
