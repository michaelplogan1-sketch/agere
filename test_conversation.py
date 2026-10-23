"""Conversations: deterministic follow-up resolution, compose body compatibility, turn persistence."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from clinical_core.agere.clinical import conversation as C

BASE = 'How many diabetics had an ED visit in 2028?'
COMPOUND_REQUEST = ('Find every patient who would qualify for the MSK ACCESS program and launch an agent '
                    'that would help them sign up and initiate the program, then create a way to monitor '
                    'them for reduced utilization and improved chronic low back pain.')
LIVE_STACK = Path(__file__).resolve().parent / 'artifacts/agere-healthcare-v123/stack'
if not LIVE_STACK.exists():
    LIVE_STACK = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/stack')


def chain(*questions):
    prior, out = None, []
    for q in questions:
        r = C.resolve(q, prior)
        out.append(r)
        prior = {'resolved_question': r['standalone'], 'frame': r['frame'], 'history': []}
    return out


class Resolver(unittest.TestCase):
    def test_brief_thread(self):
        got = [r['standalone'] for r in chain(BASE, 'now break that down by practice', 'only Medicare', 'what about 2029?',
                                              'list them', 'exclude the ones on insulin', 'why?')]
        self.assertEqual(got, [
            BASE,
            'How many patients with diabetes had an ED visit in 2028 by practice?',
            'How many Medicare patients with diabetes had an ED visit in 2028 by practice?',
            'How many Medicare patients with diabetes had an ED visit in 2029 by practice?',
            'Which Medicare patients with diabetes had an ED visit in 2029 by practice?',
            'Which Medicare patients with diabetes not on insulin had an ED visit in 2029 by practice?',
            'Why did Medicare patients with diabetes not on insulin have ED visits in 2029?'])

    def test_methods_and_edits(self):
        rows = chain(BASE, 'only Medicare')
        self.assertEqual(rows[0]['method'], 'standalone'); self.assertFalse(rows[0]['follow_up'])
        self.assertEqual(rows[1]['method'], 'deterministic'); self.assertTrue(rows[1]['follow_up'])
        self.assertIn('payer -> medicare', rows[1]['edits'])

    def test_replace_group_and_period_and_remove(self):
        got = [r['standalone'] for r in chain(BASE, 'by practice', 'by payer instead', 'overall', 'from 2027 to 2029', 'all years')]
        self.assertEqual(got[2], 'How many patients with diabetes had an ED visit in 2028 by payer?')
        self.assertEqual(got[3], 'How many patients with diabetes had an ED visit in 2028?')
        self.assertEqual(got[4], 'How many patients with diabetes had an ED visit from 2027 to 2029?')
        self.assertEqual(got[5], 'How many patients with diabetes had an ED visit?')

    def test_filters_add_and_remove(self):
        got = [r['standalone'] for r in chain('How many Medicare patients with diabetes had an ED visit in 2028?',
                                              'all payers', 'only women', 'aged 65 or older', 'with hypertension', 'without ckd',
                                              'drop the hypertension filter', 'all ages', 'both sexes')]
        self.assertEqual(got[1], 'How many patients with diabetes had an ED visit in 2028?')
        self.assertEqual(got[2], 'How many female patients with diabetes had an ED visit in 2028?')
        self.assertEqual(got[3], 'How many female patients aged 65 or older with diabetes had an ED visit in 2028?')
        self.assertEqual(got[4], 'How many female patients aged 65 or older with diabetes and hypertension had an ED visit in 2028?')
        self.assertEqual(got[5], 'How many female patients aged 65 or older with diabetes and hypertension but no chronic kidney disease had an ED visit in 2028?')
        self.assertEqual(got[6], 'How many female patients aged 65 or older with diabetes but no chronic kidney disease had an ED visit in 2028?')
        self.assertEqual(got[8], 'How many patients with diabetes but no chronic kidney disease had an ED visit in 2028?')

    def test_shapes(self):
        got = [r['standalone'] for r in chain(BASE, 'what share?', 'list them', 'how many?', 'what drives it?')]
        self.assertEqual(got[1:], ['What share of patients with diabetes had an ED visit in 2028?',
                                   'Which patients with diabetes had an ED visit in 2028?',
                                   'How many patients with diabetes had an ED visit in 2028?',
                                   'Why did patients with diabetes have ED visits in 2028?'])

    def test_why_without_event(self):
        got = [r['standalone'] for r in chain('How many patients have diabetes?', 'only medicaid', 'why?')]
        self.assertEqual(got[2], 'What drives diabetes among Medicaid patients?')

    def test_compare(self):
        self.assertEqual(chain(BASE, 'compare with 2029')[1]['standalone'],
                         'How many patients with diabetes had an ED visit from 2028 to 2029 by year?')
        self.assertEqual(chain('How many Medicare patients with diabetes had an ED visit in 2028?', 'compare with medicaid')[1]['standalone'],
                         'How many patients with diabetes had an ED visit in 2028 by payer?')
        self.assertEqual(chain(BASE, 'compare with hypertension')[1]['standalone'],
                         'How many patients with diabetes had an ED visit in 2028, compared with patients with hypertension?')
        self.assertEqual(chain(BASE, 'the year before')[1]['standalone'], 'How many patients with diabetes had an ED visit in 2027?')

    def test_switch_condition_and_event(self):
        got = [r['standalone'] for r in chain(BASE, 'what about heart failure instead?', 'what about inpatient admissions?')]
        self.assertEqual(got[1], 'How many patients with heart failure had an ED visit in 2028?')
        self.assertEqual(got[2], 'How many patients with heart failure had an inpatient admission in 2028?')

    def test_new_standalone_question_in_thread(self):
        rows = chain(BASE, 'Which CKD patients have a declining eGFR?')
        self.assertEqual(rows[1]['standalone'], 'Which CKD patients have a declining eGFR?')
        self.assertEqual(rows[1]['method'], 'standalone')

    def test_text_edits_without_frame(self):
        got = chain('Which CKD patients have a declining eGFR?', 'in 2028', 'by practice', 'what about 2029?')
        self.assertEqual([r['standalone'] for r in got[1:]], ['Which CKD patients have a declining eGFR in 2028?',
                                                             'Which CKD patients have a declining eGFR in 2028 by practice?',
                                                             'Which CKD patients have a declining eGFR in 2029 by practice?'])
        self.assertTrue(all(r['method'] == 'deterministic_text' for r in got[1:]))

    def test_unresolvable_without_hosted_declines_clearly(self):
        prior = {'resolved_question': BASE, 'frame': C.parse_frame(BASE), 'history': []}
        with self.assertRaisesRegex(ValueError, 'hosted resolution is disabled'):
            C.resolve('and the ones with a weird lab', prior)
        with self.assertRaisesRegex(ValueError, 'hosted resolution is disabled'):
            C.resolve('exclude medicare', prior)

    def test_hosted_fallback_used_only_when_not_confident(self):
        calls = []

        def hosted(prior, follow_up):
            calls.append((prior['resolved_question'], follow_up))
            return 'How many patients with diabetes and any A1c over 9 had an ED visit in 2028?\n', {'api_usd': 0.0004, 'cache_hit': False}
        prior = {'resolved_question': BASE, 'frame': C.parse_frame(BASE), 'history': []}
        r = C.resolve('only the ones with an A1c over 9', prior, hosted=hosted)
        self.assertEqual(r['method'], 'hosted')
        self.assertEqual(r['standalone'], 'How many patients with diabetes and any A1c over 9 had an ED visit in 2028?')
        self.assertEqual(calls, [(BASE, 'only the ones with an A1c over 9')])
        C.resolve('only Medicare', prior, hosted=hosted)
        self.assertEqual(len(calls), 1)  # deterministic path never pays
        with self.assertRaisesRegex(ValueError, 'could not be resolved'):
            C.resolve('and the weather?', prior, hosted=lambda p, f: ('CANNOT_RESOLVE', {}))

    def test_hosted_prompt_uses_haiku_and_static_cached_prefix(self):
        from unittest.mock import patch
        seen = {}

        class Model:
            def __init__(self, ledger, out, model):
                seen['model'] = model

            def __call__(self, system, user, max_tokens=700):
                seen['user'] = user
                return 'How many patients with diabetes had an ED visit in 2028?', {'api_usd': 0.0, 'cache_hit': True}
        with patch('clinical_core.agere.clinical.nl2sql.HostedModel', Model):
            call = C.hosted_resolver(SimpleNamespace(receipts=None), ledger=None)
            call({'resolved_question': BASE, 'history': ['a', BASE]}, 'hmm')
        self.assertEqual(seen['model'], 'claude-haiku-5-5')
        prefix, sep, rest = seen['user'].partition('\n\nEXAMPLES\n')
        self.assertTrue(prefix and sep); self.assertNotIn(BASE, prefix); self.assertIn('Follow-up: hmm', rest)

    @unittest.skipUnless((LIVE_STACK / 'stack.sqlite3').exists(), 'materialised stack not present')
    def test_resolved_questions_compile_locally_where_expected(self):
        from clinical_core.agere.clinical import local_compiler, source_profile
        profile = source_profile.load(LIVE_STACK)
        rows = chain(BASE, 'now break that down by practice', 'only Medicare', 'what about 2029?', 'list them')
        for r in rows:
            graph, info = local_compiler.compile(r['standalone'], profile, '2029-08-15')
            self.assertEqual(info.get('route'), 'local_compiler', r['standalone'])
            self.assertIn('encounter emergency_visit', info['recognised'])
        # Medication filter + encounter: the compiler must decline (verified planner), never drop the visit.
        q = chain(BASE, 'exclude the ones on insulin')[1]['standalone']
        graph, info = local_compiler.compile(q, profile, '2029-08-15')
        self.assertIsNone(graph); self.assertEqual(info['route'], 'fallback')


class FakeStack:
    def __init__(self, path, allow_live=False):
        self.path, self.allow_live, self.calls = Path(path), allow_live, []
        self.receipts = self.path

    def compose(self, question, planning='auto'):
        self.calls.append((question, planning))
        if 'boom' in question: raise ValueError('planner declined')
        return {'question': question, 'graph': {'interpretation': question}, 'planner_receipt': {'route': 'local_compiler',
                'local_compiler': {'recognised': ['count']}}, 'planning_economics': {'known_api_estimate_usd': 0},
                'new_paid_api_calls': 0, 'answer': {'status': 'answered', 'answer_type': 'count', 'headline': '42 patients'}}


class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.stack = FakeStack(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_existing_body_shapes_unchanged(self):
        r = C.handle_compose(self.stack, {'question': BASE})
        self.assertNotIn('conversation', r); self.assertEqual(self.stack.calls[-1], (BASE, 'auto'))
        C.handle_compose(self.stack, {'question': BASE, 'planning': 'frontier'})
        self.assertEqual(self.stack.calls[-1], (BASE, 'frontier'))
        for bad in ({'question': BASE, 'planning': 'scoped_local_candidate'}, {'question': BASE, 'extra': 1}, {'planning': 'x'},
                    {'question': BASE, 'conversation_id': 'bad id!'}, {'question': BASE, 'conversation_id': 7}):
            with self.assertRaises(ValueError): C.handle_compose(self.stack, bad)
        self.assertFalse((Path(self.tmp.name) / 'conversations.sqlite3').exists())

    def test_complete_compound_request_reaches_planner_unchanged(self):
        for shared in (False, True):
            self.stack.shared_request_planning = shared
            for context in ({}, {'conversation_id': 'compound'},
                            {'conversation_id': 'compound', 'scope': 'warehouse'},
                            {'scope': 'warehouse'}):
                with self.subTest(shared=shared, context=context):
                    result = C.handle_compose(self.stack, {'question': COMPOUND_REQUEST, **context})
                    self.assertEqual(self.stack.calls[-1], (COMPOUND_REQUEST, 'auto'))
                    if 'conversation' in result:
                        self.assertFalse(result['conversation']['follow_up'])
                        self.assertEqual(result['conversation']['resolved_question'], COMPOUND_REQUEST)

    def test_local_references_across_subjects_are_new_questions(self):
        for question in (
            'Find patients with diabetes and show their PCPs',
            'List claims that were denied and group them by payer',
            'Find practices with high admission rates and compare their costs',
            'Show patients with diabetes and count those over 65',
            'Find patients with hypertension and determine whether they have follow-up visits',
            'How many patients have an assigned cardiologist on their care team?',
            'How many patients are attributed to a practice by their payer?',
            'List clinicians with their practices',
        ):
            with self.subTest(question=question):
                self.assertFalse(C.requires_saved_cohort(question))
                result = C.handle_compose(self.stack, {'question': question, 'conversation_id': 'local'})
                self.assertEqual(self.stack.calls[-1], (question, 'auto'))
                self.assertFalse(result['conversation']['follow_up'])

    def test_seeming_verb_keeps_the_pronoun_local(self):  # CLN-06 was refused as a follow-up
        self.assertFalse(C.requires_saved_cohort("Which patients look like they have kidney disease from their eGFR but aren't diagnosed?"))
        self.assertFalse(C.requires_saved_cohort('Which members seem to be missing their statin?'))
        self.assertTrue(C.requires_saved_cohort('Which patients do they see most?'))
        self.assertTrue(C.requires_saved_cohort('Count claims for those patients'))

    def test_missing_external_references_cannot_query_whole_warehouse(self):
        from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
        for question in ('List them', 'Count those patients', 'Group these patients by PCP',
                         'Who are their PCPs?', 'Do they have diabetes?',
                         'Count claims for those patients', 'Find appointments for them',
                         'Count claims for their patients',
                         'Find patients from the previous result and monitor them',
                         'Count the saved cohort'):
            for context in ({}, {'conversation_id': 'missing'},
                            {'conversation_id': 'missing', 'scope': 'warehouse'}):
                with self.subTest(question=question, context=context):
                    before = len(self.stack.calls)
                    with self.assertRaises(QuestionNotUnderstood):
                        C.handle_compose(self.stack, {'question': question, **context})
                    self.assertEqual(len(self.stack.calls), before)

    def test_conversation_body_and_persistence(self):
        r1 = C.handle_compose(self.stack, {'question': BASE, 'conversation_id': 'thread-1'})
        r2 = C.handle_compose(self.stack, {'question': 'only Medicare', 'conversation_id': 'thread-1'})
        r3 = C.handle_compose(self.stack, {'question': 'by practice', 'conversation_id': 'thread-1', 'planning': 'frontier'})
        self.assertEqual(r1['conversation']['turn'], 1); self.assertEqual(r1['conversation']['resolved_question'], BASE)
        self.assertEqual(r2['conversation']['resolved_question'], 'How many Medicare patients with diabetes had an ED visit in 2028?')
        self.assertEqual(self.stack.calls[1], (r2['conversation']['resolved_question'], 'auto'))  # the planner sees the standalone question
        self.assertEqual(self.stack.calls[2][1], 'frontier')
        self.assertEqual(r3['conversation']['resolved_question'], 'How many Medicare patients with diabetes had an ED visit in 2028 by practice?')
        # persisted across a fresh read (new connection)
        rows = C.turns(self.tmp.name, 'thread-1')
        self.assertEqual([t['turn'] for t in rows], [1, 2, 3])
        self.assertEqual(rows[1]['question'], 'only Medicare'); self.assertEqual(rows[1]['method'], 'deterministic')
        self.assertEqual(rows[1]['plan_summary']['route'], 'local_compiler')
        self.assertEqual(rows[1]['answer_summary']['headline'], '42 patients')
        self.assertEqual(rows[2]['frame']['group'], 'practice')
        # other conversations are isolated
        other = C.handle_compose(self.stack, {'question': 'only Medicare', 'conversation_id': 'thread-2'})
        self.assertEqual(other['conversation']['resolved_question'], 'only Medicare')
        self.assertEqual(other['conversation']['method'], 'standalone')

    def test_null_id_starts_new_conversation(self):
        r = C.handle_compose(self.stack, {'question': BASE, 'conversation_id': None})
        cid = r['conversation']['id']
        self.assertRegex(cid, r'^[0-9a-f]{32}$')
        self.assertEqual(len(C.turns(self.tmp.name, cid)), 1)

    def test_failed_turn_is_recorded_and_thread_continues(self):
        C.handle_compose(self.stack, {'question': BASE, 'conversation_id': 't'})
        with self.assertRaises(ValueError): C.handle_compose(self.stack, {'question': 'boom question here please now', 'conversation_id': 't'})
        rows = C.turns(self.tmp.name, 't')
        self.assertEqual(len(rows), 2); self.assertIn('planner declined', rows[1]['error'])

    def test_hosted_cost_is_accounted(self):
        live = FakeStack(self.tmp.name, allow_live=True)
        factory = lambda stack: (lambda prior, f: ('How many patients with diabetes and any A1c over 9 had an ED visit in 2028?',
                                                   {'api_usd': 0.0005, 'cache_hit': False}))
        C.handle_compose(live, {'question': BASE, 'conversation_id': 'h'}, hosted_factory=factory)
        r = C.handle_compose(live, {'question': 'only the ones with an A1c over 9', 'conversation_id': 'h'}, hosted_factory=factory)
        self.assertEqual(r['conversation']['method'], 'hosted')
        self.assertEqual(r['new_paid_api_calls'], 1)
        self.assertAlmostEqual(r['planning_economics']['known_api_estimate_usd'], 0.0005)


if __name__ == '__main__':
    unittest.main()
