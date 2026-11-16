"""Route arbitration: every engine TEMPLATE and every acceptance question with expect_operation arbitrates to its own operation.

Each example is scored with itself held out (the arbiter never matches a question to its own text), against every engine route that
claims it and, for acceptance rows, the native compiler's own plan. Reports how many examples more than one route claims."""
import unittest
from pathlib import Path

from clinical_core.agere.clinical import engine_suite, local_compiler, route_arbiter as ra, source_profile

AS_OF = '2030-05-27'
FILL = {'{n}': '40', '{need}': 'food insecurity', '{horizon}': 'next quarter'}
# Engines whose route is gated by context a bare profile does not carry (a non-healthcare pack, a discovered-value index): they claim nothing here.
CONTEXT_GATED = {'domain_pack', 'derived_value'}
# Templates whose own route declines the phrasing (the native chain / planner answers them); reported, and a new entry must be justified.
KNOWN_UNCLAIMED = {
    'Build the best outreach schedule across channels for the next 6 weeks', 'What is one extra hour of phone outreach capacity worth per week?',
    'Which outreach channel gets the most patients to engage?', 'is texting or calling better for getting members to enroll',
    'what has our outreach log taught us about phone versus mail', 'Use a bandit to decide how to split our next 40 outreach contacts',
    'which contact method performs best by risk tier', 'is the decline in pharmacy fills genuine or an interface problem',
    'tell me whether the lab dip is real or missing data', 'optimize the order we enroll heart failure patients into pharmacist outreach waves, simulated',
    'can a learned policy close more GDMT gaps than router order in the simulator'}
STACK = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/stack')


def fill(text):
    for k, v in FILL.items(): text = text.replace(k, v)
    return text


def templates():
    for op, m in engine_suite.available().items():
        if op in CONTEXT_GATED: continue
        for t in getattr(m, 'TEMPLATES', ()): yield op, fill(t)


def profile():
    if (STACK / 'semantic.sqlite3').exists():
        try:
            return source_profile.load(STACK)
        except Exception:  # noqa: BLE001 - an unreadable release falls back to the empty profile
            pass
    return {}


class Overlap(unittest.TestCase):
    def test_every_template_arbitrates_to_its_own_operation(self):
        total = multi = 0
        unclaimed, wrong = [], []
        for op, t in templates():
            total += 1
            claims = ra.candidates(t, {}, AS_OF, native=False)
            multi += len(claims) > 1
            if not claims:
                unclaimed.append(t); continue
            plan = ra.arbitrate(t, {}, AS_OF, native=False, held_out=t)
            if plan[0]['nodes'][0]['operation'] != op: wrong.append((op, plan[0]['nodes'][0]['operation'], t))
        print(f'\ntemplates {total}: claimed by more than one route {multi}, unclaimed {len(unclaimed)}, wrong winners {len(wrong)}')
        self.assertEqual(wrong, [])
        self.assertLessEqual(set(unclaimed), KNOWN_UNCLAIMED, 'a template its own route no longer claims')

    def test_every_acceptance_question_arbitrates_to_its_own_operation(self):
        prof = profile()
        rows = ra._acceptance()
        self.assertGreater(len(rows), 400)
        claimed = decidable = multi = 0
        wrong = []
        for op, q in rows:
            cands = ra.candidates(q, prof, AS_OF, native=True)
            if not [c for c in cands if c['source'] != 'native']: continue  # no engine route claims it: nothing to arbitrate
            claimed += 1
            multi += len(cands) > 1
            if op not in [c['op'] for c in cands]: continue  # the free routes cannot produce the expected operation here (a later tier does)
            decidable += 1
            winner, _ = ra.decide(q, cands, held_out=q)
            if winner['op'] != op: wrong.append((op, winner['op'], q))
        print(f'\nacceptance {len(rows)}: engine-claimed {claimed}, with competing candidates {multi}, decidable {decidable}, wrong winners {len(wrong)}')
        self.assertEqual(wrong, [])

    def test_receipt_and_thin_wrapper(self):
        q = 'Are diagnoses only on health risk assessments driving risk scores?'
        graph, info = engine_suite.compile_route(q, {}, AS_OF)
        self.assertEqual(graph['nodes'][0]['operation'], 'upcoding_detection')
        r = info['arbitration']
        self.assertEqual(r['winner']['operation'], 'upcoding_detection')
        self.assertTrue(r['candidates'] and r['tie_break'])
        self.assertIsNone(engine_suite.compile_route('How many patients have diabetes?', {}, AS_OF))

    def test_previous_order_stands_without_system_one_and_native_can_win(self):
        plan = lambda op: ({'nodes': [{'operation': op, 'parameters': {}}]}, {'recognised': [], 'unexplained': []})
        a = {'op': 'mlr', 'source': 'engine:mlr', 'plan': plan('mlr')}
        b = {'op': 'survival', 'source': 'native', 'plan': plan('survival')}
        winner, receipt = ra.decide('zzz unrelated words', [dict(a), dict(b)])
        self.assertEqual(winner['op'], 'mlr')  # no evidence either way: previous order's winner
        self.assertIn('previous order', receipt['tie_break'])
        winner, receipt = ra.decide('Time to readmission after discharge by payer', [dict(a), dict(b)])
        self.assertEqual(winner['op'], 'survival')  # the native plan's own examples match far better
        self.assertEqual({c['source'] for c in receipt['candidates']}, {'engine:mlr', 'native'})

    def test_native_chain_runs_without_the_engine_suite(self):
        prof = profile()
        if not prof: self.skipTest('no source profile')
        graph, info = local_compiler._compile('What is our MLR by line of business?', prof, AS_OF, engines=False)
        self.assertFalse(graph and graph['nodes'] and graph['nodes'][-1]['operation'] == 'mlr')


if __name__ == '__main__':
    unittest.main()
