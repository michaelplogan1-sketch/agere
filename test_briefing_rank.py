"""Briefing learning-to-rank (briefing_rank): the cold-start prior reproduces the information x impact order, simulated
feedback from users with known preferences raises NDCG@5, MMR keeps one practice/metric from filling the briefing, the
Thompson explore slot, the feedback store/endpoint in a temp stack, the 'briefing' operator route and the console hooks.
Synthetic data only; the real release is opened read-only (sqlite mode=ro / JSON read) and never rebuilt or written."""
import json, math, shutil, sqlite3, tempfile, unittest
from pathlib import Path
from unittest import mock

import numpy as np

from clinical_core.agere import ask_view
from clinical_core.agere.clinical import briefing, briefing_rank as br, local_compiler, market
from clinical_core.agere.clinical.analysis_graph import build_answer, headline, validate, OPERATIONS
from clinical_core.agere.clinical.capability_registry import PRODUCT
from clinical_core.agere.clinical.graph_contracts import TYPED, input_contract, parameter_schema
from clinical_core.agere.clinical.graph_planner import CONTRACTS

HERE = Path(__file__).resolve().parent
AS_OF = '2029-08-15'
REAL = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/stack')


def card(kind, metric, unit, bits, members, follow=None, dollars=None, **ev):
    c = {'kind': kind, 'topic': f'{metric} ({unit})', 'headline': f'{kind} {metric} {unit}', 'patients': members, 'dollars': dollars,
         'strength': bits, 'surprise_bits': bits, 'follow_up': follow or f'What happened to {metric} at {unit}?',
         'evidence': {'metric': metric, 'unit': unit, **ev}}
    return briefing.rank(c, 5000.0)


def current_order(cards):
    """The pre-briefing_rank rule: sort by information x impact score (stable)."""
    return [c['headline'] for c in sorted(cards, key=lambda c: -c['score'])]


class Temp(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class Prior(unittest.TestCase):
    def test_prior_score_is_the_information_x_impact_rule(self):
        for c in br.synthetic_cards(np.random.default_rng(1), 30):
            if c['score'] > 0:
                x = br.features([c])[0]
                self.assertAlmostEqual(math.exp(x @ br.prior_weights()), c['score'], delta=0.01 * c['score'] + 0.01)

    def test_cold_start_reproduces_current_order(self):
        rng = np.random.default_rng(3)
        for _ in range(20):
            cards = br.synthetic_cards(rng, 25)
            ranked = br.rank(cards, explore=False, lam=1.0)            # prior alone: the information x impact order
            scores = [c['score'] for c in ranked]                      # (cards tied on the rounded score may swap)
            self.assertEqual(scores, sorted(scores, reverse=True))
            same = [a['headline'] == b for a, b in zip(ranked, current_order(cards))]
            self.assertGreaterEqual(sum(same), len(cards) - 2)
        # with diversity on, a briefing without overlapping practice/metric cards is unchanged too
        cards = [card(k, m, u, b, p) for k, m, u, b, p in [('anomaly', 'ed', 'PR1', 9, 40), ('outlier', 'pmpm', 'PR2', 6, 300),
                 ('market move', 'a1c', 'PR3', 12, 80), ('trajectory', 'egfr', 'PR4', 4, 25), ('forecast', 'bp', 'PR5', 7, 10),
                 ('metric change', 'admit', 'PR6', 2, 900)]]
        self.assertEqual([c['headline'] for c in br.rank(cards, explore=False)], current_order(cards))

    def test_cold_start_within_tolerance_with_diversity(self):
        rng, taus = np.random.default_rng(11), []
        for _ in range(30):
            cards = br.synthetic_cards(rng, 20)
            want = current_order(cards)
            got = [c['headline'] for c in br.rank(cards, explore=False)]
            pos = {h: i for i, h in enumerate(got)}
            r = [pos[h] for h in want]
            n = len(r)
            concordant = sum(1 if (r[j] - r[i]) > 0 else -1 for i in range(n) for j in range(i + 1, n))
            taus.append(concordant / (n * (n - 1) / 2))
            self.assertEqual(got[0], want[0])                      # the top card never changes at cold start
        self.assertGreater(np.mean(taus), 0.85)

    def test_every_card_explains_its_position(self):
        ranked = br.rank(br.synthetic_cards(np.random.default_rng(2), 12), explore=False)
        for c in ranked:
            self.assertTrue(c['why_text'].startswith(('Ranked by', 'Ranked at')))
            self.assertLessEqual(len(c['why']), 3)
            self.assertRegex(c['card_id'], r'^[0-9a-f]{12}(-\d+)?$')
            self.assertEqual(set(c['features']) >= {'log_surprise', 'log_impact', 'actionability', 'novelty', 'confidence',
                                                    'data_quality', 'recency'}, True)
        self.assertEqual([c['rank'] for c in ranked], list(range(1, 13)))
        self.assertIn('surprise', ranked[0]['why_text'])


class Features(unittest.TestCase):
    def test_actionability_data_quality_recency_and_novelty(self):
        cards = [card('trajectory', 'egfr', 'PR1', 5, 20, patients=[{'patient_id': 'P1'}], month='2029-07'),
                 card('market move', 'ed', 'PR2', 5, 20, month='2029-01'),
                 card('data gap', 'ed', 'feed', 5, 20)]
        X = br.features(cards, history={br.signature(cards[0]): 0.5})
        f = {name: X[:, i] for i, name in enumerate(br.FEATURES)}
        self.assertEqual(list(f['actionability']), [1.0, 0.5, 0.5])
        self.assertEqual(list(f['data_quality']), [0.0, 1.0, 0.0])    # the ED card is gap-affected; the gap card itself is not
        self.assertEqual(f['recency'][0], 1.0)
        self.assertLess(f['recency'][1], 0.2)
        self.assertEqual(f['recency'][2], 0.5)                          # undated
        self.assertEqual(list(f['novelty']), [0.5, 1.0, 1.0])
        self.assertEqual(f['type:trajectory'][0], 1.0)


class Learning(Temp):
    def test_feedback_raises_ndcg_over_the_prior(self):
        for user, n in (('care_manager', 30), ('finance_lead', 30)):
            d = self.dir / user
            d.mkdir()
            r = br.simulate(d, br.USERS[user], train_briefings=n, test_briefings=100)
            self.assertGreater(r['ndcg_learned'], r['ndcg_prior'] + 0.15, (user, r))
            self.assertGreater(r['pairs'], 10)

    def test_no_feedback_is_the_prior(self):
        m = br.train(self.dir)
        self.assertFalse(m.learned)
        np.testing.assert_allclose(m.w, br.prior_weights())
        self.assertEqual(br.Ranker.fit([]).pairs, 0)

    def test_skip_above_pairs(self):
        x = [np.eye(len(br.FEATURES))[i] for i in range(4)]
        shown = [('a', 0, x[0]), ('b', 1, x[1]), ('c', 2, x[2]), ('d', 3, x[3])]
        diffs, w = br.preference_pairs(shown, {'c': 2.0})            # a, b passed over above the click; d unseen
        self.assertEqual(len(diffs), 2)
        diffs, w = br.preference_pairs(shown, {'c': 2.0, 'd': -1.0})  # explicit dismissal counts wherever it sat
        self.assertEqual(len(diffs), 5)


class Diversity(unittest.TestCase):
    def test_mmr_keeps_one_practice_metric_from_filling_the_top(self):
        crowd = [card('market move', 'ed', 'PR1', 20 - i, 100, follow=f'ED at PR1 {i}?') for i in range(5)]
        others = [card(k, m, u, 10, 100) for k, m, u in [('anomaly', 'pmpm', 'PR2', ), ('outlier', 'admit', 'PR3'),
                                                          ('forecast', 'a1c', 'PR4'), ('trajectory', 'egfr', 'PR5')]]
        plain = br.rank(crowd + others, explore=False, lam=1.0)
        self.assertEqual(sum(c['evidence']['unit'] == 'PR1' for c in plain[:5]), 5)
        diverse = br.rank(crowd + others, explore=False, lam=0.5)
        self.assertLessEqual(sum(c['evidence']['unit'] == 'PR1' for c in diverse[:5]), 2)
        self.assertEqual(diverse[0]['headline'], plain[0]['headline'])
        self.assertTrue(any('overlap' in c['why_text'] for c in diverse))


class Explore(unittest.TestCase):
    def test_one_thompson_slot_at_the_last_displayed_position(self):
        cards = br.synthetic_cards(np.random.default_rng(5), 25)
        base = [c['headline'] for c in br.rank(cards, explore=False)]
        picked = set()
        for seed in range(40):
            ranked = br.rank(cards, seed=seed)
            flagged = [c for c in ranked if c['explore']]
            self.assertLessEqual(len(flagged), 1)
            self.assertEqual([c['headline'] for c in ranked[:br.TOP - 1]], base[:br.TOP - 1])   # the top slots are untouched
            if flagged:
                self.assertEqual(flagged[0]['rank'], br.TOP)
                self.assertNotIn(flagged[0]['headline'], base[:br.TOP])
                self.assertIn('Explore slot', flagged[0]['why_text'])
                picked.add(flagged[0]['headline'])
        self.assertGreater(len(picked), 2)                              # posterior sampling, not a fixed pick
        self.assertFalse(any(c['explore'] for c in br.rank(cards[:br.TOP], seed=1)))   # nothing to explore below the fold

    def test_learned_posterior_narrows(self):
        d = Path(tempfile.mkdtemp())
        try:
            br.simulate(d, br.USERS['care_manager'], train_briefings=15, test_briefings=1)
            m = br.train(d)
            self.assertLess(np.trace(m.cov), np.trace(br.Ranker().cov))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Store(Temp):
    def test_serve_records_history_and_feedback_endpoint_stores_events(self):
        b = {'schema': briefing.VERSION, 'cards': br.synthetic_cards(np.random.default_rng(9), 15)}
        quiet = br.serve(self.dir, b, record=False)
        self.assertIsNone(quiet['briefing_id'])
        self.assertFalse((self.dir / br.STORE).exists())                 # only the service's record=True writes
        served = br.serve(self.dir, b, record=True)
        self.assertTrue(served['briefing_id'])
        self.assertEqual(served['ranking']['explore_slot'], br.TOP)
        top = served['cards'][0]['card_id']
        r = br.handle_feedback(self.dir, {'card_id': top, 'event': 'asked_follow_up'})
        self.assertEqual((r['recorded'], r['briefing_id']), (True, served['briefing_id']))
        br.handle_feedback(self.dir, {'card_id': served['cards'][1]['card_id'], 'event': 'dismissed', 'briefing_id': served['briefing_id']})
        br.handle_feedback(self.dir, {'card_id': served['cards'][2]['card_id'], 'event': 'opened'})
        for bad in ({'card_id': top}, {'card_id': top, 'event': 'liked'}, {'card_id': 'nope', 'event': 'opened'},
                    {'card_id': 'abcdefabcdef', 'event': 'opened'}, {'card_id': top, 'event': 'opened', 'extra': 1},
                    {'card_id': top, 'event': 'opened', 'briefing_id': 'DROP TABLE'}):
            with self.assertRaises(ValueError, msg=bad): br.handle_feedback(self.dir, bad)
        db = sqlite3.connect(f'file:{self.dir / br.STORE}?mode=ro', uri=True)
        events = dict(db.execute('SELECT event, count(*) FROM feedback GROUP BY event').fetchall())
        shown = db.execute('SELECT count(*) FROM shown').fetchone()[0]
        db.close()
        self.assertEqual(events, {'asked_follow_up': 1, 'dismissed': 1, 'opened': 1})
        self.assertEqual(shown, 15)
        self.assertEqual(br.summary(self.dir)['events'], events)
        m = br.train(self.dir)
        self.assertTrue(m.learned)
        again = br.serve(self.dir, b, record=True)                       # novelty now sees the previous briefing
        self.assertTrue(again['ranking']['history_briefings'])
        self.assertLess(again['cards'][0]['features']['novelty'], 1.0)

    def test_service_routes_feedback_and_records_only_on_the_briefing_route(self):
        src = (HERE / 'clinical_core' / 'agere' / 'service.py').read_text()
        self.assertIn("path=='/api/stack/briefing-feedback'", src)
        self.assertIn('briefing_rank.handle_feedback(connected_stack.path,body)', src)
        self.assertIn('briefing_rank.serve(connected_stack.path,briefing(connected_stack.path),record=True)', src)
        graph = (HERE / 'clinical_core' / 'agere' / 'clinical' / 'analysis_graph.py').read_text()
        self.assertIn('briefing_rank.serve(path, briefing.cached(path), record=False)', graph)


class BuiltBriefing(Temp):
    """briefing.build on the market suite's synthetic stack: cards come out ranked, with ids and reasons."""

    def test_build_ranks_with_ids_and_reasons(self):
        import test_market
        path = test_market.fixture()
        try:
            report = market.execute(path, None, {}, AS_OF)
            with mock.patch.object(briefing, 'DISCOVERY', []), mock.patch.object(briefing, 'TRAJECTORIES', []), \
                    mock.patch.object(briefing, 'FORECASTS', []), mock.patch.object(briefing, 'CHANGES', []), \
                    mock.patch.object(market, 'execute', lambda p, ids, q, as_of=None: report):
                b = briefing.build(path)
            self.assertEqual(b['schema'], 'briefing/5')
            self.assertTrue(b['cards'])
            self.assertEqual([c['rank'] for c in b['cards']], list(range(1, len(b['cards']) + 1)))
            self.assertTrue(all(c['card_id'] and c['why_text'] and not c['explore'] for c in b['cards']))
            served = br.serve(path, b, record=True)
            self.assertTrue((Path(path) / br.STORE).exists())
            br.record_feedback(path, served['cards'][0]['card_id'], 'opened')
        finally:
            shutil.rmtree(path, ignore_errors=True)


class Operator(unittest.TestCase):
    def test_integrated_everywhere(self):
        self.assertIn('briefing', PRODUCT)
        self.assertEqual(PRODUCT['briefing']['population'], 'none')
        self.assertIn('briefing', OPERATIONS)
        self.assertIn('briefing', CONTRACTS)
        self.assertIn('briefing', TYPED)
        self.assertEqual(input_contract('briefing')['count'], [0, 0])
        self.assertEqual(set(parameter_schema('briefing', {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}})['properties']), {'top'})
        for bad in ({'top': 0}, {'top': 26}, {'top': True}, {'colour': 1}):
            with self.assertRaises(ValueError): br.validate(bad)

    def test_acceptance_phrasings(self):
        cases = json.loads((HERE / 'bench' / 'acceptance' / 'briefing.json').read_text())
        self.assertEqual(len(cases), 8)
        for c in cases:
            g, info = local_compiler.compile(c['question'], {}, AS_OF)
            self.assertIsNotNone(g, (c, info))
            self.assertEqual(g['nodes'][-1]['operation'], c['expect_operation'], (c, info))
            validate(g, AS_OF)
        g, _ = local_compiler.compile("Show me today's top 5 findings", {}, AS_OF)
        self.assertEqual(g['nodes'][-1]['parameters'], {'top': 5})

    def test_other_questions_are_not_taken(self):
        for q in ('What moved this month?', 'Is PMPM out of control?', 'Where are ED visits concentrated?',
                  'What should I know about the A1c of patient p-12?', 'What should we do about readmissions?'):
            self.assertIsNone(local_compiler._briefing_question(q), q)

    def test_answer_and_console(self):
        plan = validate({'interpretation': 'b', 'coverage': 'supported_observation', 'unmet_requirements': [],
                         'nodes': [{'id': 'answer', 'operation': 'briefing', 'inputs': [], 'purpose': 'b', 'parameters': {'top': 5}}]}, AS_OF)
        report = br.serve(Path(tempfile.mkdtemp()), {'cards': br.synthetic_cards(np.random.default_rng(4), 12),
                                                                              'method': 'synthetic'}, record=False)
        a = build_answer(plan, [{'node': plan['nodes'][0], 'result': report}], {}, 'What should I look at today?')
        a = json.loads(json.dumps(a, allow_nan=False, default=str))
        self.assertEqual((a['status'], a['answer_type'], len(a['table'])), ('answered', 'table', 5))
        self.assertTrue(all(r['why'] and r['card_id'] for r in a['table']))
        self.assertIn('briefing', a['overall'])
        self.assertTrue(headline(a).startswith('12 findings; top:'))
        html = ask_view.page()
        for hook in ('/api/stack/briefing-feedback', 'asked_follow_up', "'dismissed'", "'opened'", 'function vizBriefing', 'data-brief-dismiss',
                     'why_text', 'Explore slot'):
            self.assertIn(hook, html)
        self.assertNotIn("sort((x,y)=>(y.strength||0)-(x.strength||0))", html)   # the console keeps the server's ranked order


@unittest.skipUnless((REAL / 'briefing.json').exists(), 'synthetic release not present')
class RealRelease(unittest.TestCase):
    """Read-only: ranks the release's saved briefing cards in memory; nothing under the stack is written."""

    def test_saved_briefing_ranks_with_the_prior(self):
        before = (REAL / 'briefing.json').stat().st_mtime
        saved = json.loads((REAL / 'briefing.json').read_text())
        cards = [briefing.rank(dict(c)) for c in saved['cards']]
        ranked = br.rank(cards, history=br.history(REAL), explore=False, lam=1.0)
        scores = [c['score'] for c in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(before, (REAL / 'briefing.json').stat().st_mtime)


if __name__ == '__main__':
    unittest.main()
