"""Checks and routing wave (2026-10-22): cross-family position-swapped judge, graded Learn-then-Test certificates, verify
instead of first-claim-wins, learned cascade router labelled by outcome, verified-store quarantine + PPI audit, PlanFence,
requirement ledger, System One-first answer-guard cross-check, pass^k stability, release-drift warnings.
System One is mocked (no network); no model or warehouse is loaded."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import (answer_checks as A, answer_stability as S, calibrated_thresholds as CT, cascade_router as R,
                                          claim_verification as CV, judge as J, plan_fence as F, proposers as P, release_drift as D,
                                          requirement_ledger as L, store_audit as SA, system_one_cross_check as X)


class FakeChoice:
    """system_one.choice stand-in: picks(question, evidence, provider) -> label; records calls."""
    def __init__(self, picks, p=0.9):
        self.picks, self.p, self.calls = picks, p, []

    def available(self): return {'jev': True, 'perplexity': True}

    def choice(self, question, options, evidence, *, provider, **kw):
        self.calls.append((provider, [e['id'] for e in evidence], [e['text'][:40] for e in evidence]))
        label = self.picks(question, evidence, provider)
        rest = (1 - self.p) / max(1, len(options))
        probs = {k: (self.p if k == label else rest) for k in list(options) + ['unknown']}
        return {'label': label, 'probabilities': probs, 'calibrated_probabilities': probs, 'provider': provider,
                'cost_usd': 0.0002, 'cache_hit': False}


def cand(name, value, sql='SELECT count(*) FROM condition'):
    return {'name': name, 'graph': {'nodes': [{'operation': 'sql', 'parameters': {'query': sql}}]},
            'result': {'answer': {'status': 'answered', 'answer_type': 'count', 'count': value, 'headline': str(value)}}}


class Judge(unittest.TestCase):
    def test_position_consistent_cross_family_pick(self):
        right = lambda q, ev, p: 'first' if 'count: 42' in ev[0]['text'] else 'second'  # both families pick the 42 candidate
        v = J.compare('How many?', cand('a', 42), cand('b', 7), as_of='2030-01-01', s1=FakeChoice(right))
        self.assertEqual(v['verdict'], 'A')
        self.assertEqual(v['position_consistency'], 1.0)
        self.assertEqual(v['calls'], 4)  # 2 families x 2 orders

    def test_position_bias_is_dropped(self):
        always_first = lambda q, ev, p: 'first'  # flips with the order: no consistent vote
        v = J.compare('How many?', cand('a', 42), cand('b', 7), as_of='2030-01-01', s1=FakeChoice(always_first))
        self.assertEqual(v['verdict'], 'undecided')
        self.assertEqual(v['position_consistency'], 0.0)

    def test_families_disagree_is_split(self):
        def picks(q, ev, p):
            want = 'count: 42' if p == 'jev' else 'count: 7'
            return 'first' if want in ev[0]['text'] else 'second'
        self.assertEqual(J.compare('q', cand('a', 42), cand('b', 7), as_of='2030-01-01', s1=FakeChoice(picks))['verdict'], 'split')

    def test_measure_reports_accuracy_and_consistency(self):
        right = lambda q, ev, p: 'first' if 'count: 42' in ev[0]['text'] else 'second'
        pairs = [{'question': 'q1', 'a': cand('a', 42), 'b': cand('b', 7), 'right': 'A'},
                 {'question': 'q2', 'a': cand('a', 7), 'b': cand('b', 42), 'right': 'A'}]  # second pair: the judge is wrong
        m = J.measure(pairs, s1=FakeChoice(right))
        self.assertEqual((m['n'], m['decided'], m['accuracy']), (2, 2, 0.5))
        self.assertEqual(m['providers']['jev']['position_consistency'], 1.0)


class Ledger(unittest.TestCase):
    Q = 'How many male patients over 65 with diabetes or CKD had 3 or more ED visits in the past 12 months?'
    GOOD = ("SELECT count(*) FROM p WHERE sex='M' AND age > 65 AND (c='diabetes' OR c='ckd') AND v='ed' "
            "AND d >= date('2030-05-27','-12 months') GROUP BY patient_id HAVING count(*) >= 3")

    def test_build_lists_spans(self):
        ids = {r['id']: r['span'] for r in L.build(self.Q)}
        self.assertIn('cmp:3:>=', ids); self.assertIn('cmp:65:>', ids); self.assertIn('bool:or:diabetes|ckd', ids)
        self.assertEqual(self.Q[ids['cmp:3:>='][0]:ids['cmp:3:>='][1]], '3 or more')
        self.assertNotIn('cmp:12:>', ids)  # 'past 12 months' is a span, not a threshold

    def test_complete_plan_passes(self):
        self.assertEqual(L.gaps(self.Q, self.GOOD), [])
        self.assertTrue(L.check(self.Q, self.GOOD)['complete'])

    def test_wrong_direction_and_dropped_or(self):
        bad = self.GOOD.replace('>= 3', '> 3').replace("(c='diabetes' OR c='ckd')", "c='diabetes' AND c='ckd'")
        g = L.gaps(self.Q, bad)
        self.assertTrue(any("asks for >= 3" in x for x in g)); self.assertTrue(any('disjunction' in x for x in g))

    def test_equivalent_integer_bound_accepted(self):
        ledger = L.check(self.Q, self.GOOD.replace('>= 3', '> 2'))
        self.assertEqual(next(r for r in ledger['requirements'] if r['id'] == 'cmp:3:>=')['status'], 'covered')

    def test_literal_gaps_flow_through(self):
        self.assertTrue(any('number(s) 65' in x for x in L.gaps(self.Q, self.GOOD.replace('> 65', '> 60'))))

    def test_cascade_gaps_use_the_ledger(self):
        c = P.Cascade(SimpleNamespace(allow_live=False, path='/nonexistent'), None, self.Q)
        self.assertTrue(c.gaps(self.GOOD.replace('>= 3', '> 3')))


class Fence(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        db = sqlite3.connect(self.d / 'semantic.sqlite3')
        db.execute('CREATE TABLE condition(patient_id, concept, event_date)'); db.commit(); db.close()

    def graph(self, sql): return {'nodes': [{'operation': 'sql', 'parameters': {'query': sql}}]}

    def test_current_premises(self):
        v = F.validate(SimpleNamespace(path=str(self.d)), 'q', self.graph('SELECT count(*) FROM condition c WHERE c.concept = 1'), tier='bottled_sql')
        self.assertTrue(v['current']); self.assertEqual(v['premises']['tables'], ['condition'])

    def test_missing_table_or_column_fails(self):
        v = F.validate(SimpleNamespace(path=str(self.d)), 'q', self.graph('SELECT count(*) FROM condition c JOIN gone g ON 1 WHERE c.icd = 1'),
                       tier='bottled_sql')
        self.assertFalse(v['current'])
        self.assertTrue(any('gone' in i for i in v['issues'])); self.assertTrue(any('condition.icd' in i for i in v['issues']))

    def test_drift_retires_a_pair_saved_before_the_release(self):
        idx = self.d / 'release-drift.json'
        idx.write_text(json.dumps({'schema': D.VERSION, 'generated_at': '2030-01-01T00:00:00Z', 'candidate': {}, 'prior': {},
                                   'entries': [{'id': 'x', 'kind': 'concept_widened', 'severity': 'flag', 'subject': {'concept': 'phq9'},
                                                'message': 'phq9 widened', 'terms': ['phq-?9']}]}))
        with patch.dict(os.environ, {'AGERE_RELEASE_DRIFT': str(idx)}):
            issues = F.premise_issues(str(self.d), 'q', self.graph("SELECT count(*) FROM condition WHERE concept='phq9'"),
                                      saved={'created': 1000.0})
        self.assertTrue(any(i.startswith('drift:') for i in issues))

    def test_cascade_rejects_a_fenced_reuse(self):
        c = P.Cascade(SimpleNamespace(allow_live=False, path=str(self.d)), None, 'How many patients?')
        out = c.graph('semantic_cache', (self.graph('SELECT count(*) FROM gone'), {'route': 'semantic_cache'}))
        self.assertIsNone(out)
        self.assertTrue(c.log[-1]['detail'].startswith('PlanFence: schema'))


class Drift(unittest.TestCase):
    REPORT = {'candidate': {'release': 'v2.8'}, 'prior': {'release': 'v2.3'}, 'findings': [
        {'id': 'new_evidence_source:1', 'kind': 'new_evidence_source', 'severity': 'flag', 'subject': {'concept': 'hba1c', 'source': 'payer_claim_line'},
         'message': 'hba1c gained an evidence source: payer_claim_line'},
        {'id': 'free:1', 'kind': 'free_text_channel_new', 'severity': 'review', 'subject': {'concept': 'diabetes'}, 'message': 'x'}]}

    def test_index_and_answer_warning(self):
        d = Path(tempfile.mkdtemp())
        (d / 'release-diff.json').write_text(json.dumps(self.REPORT))
        (d / 'stack').mkdir()
        out = D.install(d)
        self.assertEqual(out['entries'], 1)  # review-only free-text findings are not scope changes
        answer = {'status': 'answered', 'answer_type': 'count', 'count': 5}
        graph = {'nodes': [{'operation': 'sql', 'parameters': {'query': "SELECT count(*) FROM measurement WHERE loinc = '4548-4'"}}]}
        hits = D.annotate(SimpleNamespace(path=str(d / 'stack')), 'How many patients had an A1c test?', graph, answer)
        self.assertEqual(hits, ['new_evidence_source:1'])
        self.assertTrue(any('Definition drift in v2.8' in w for w in answer['warnings']))
        self.assertEqual(answer['quality_checks'][0]['control'], 'QC-DRIFT')


class CrossCheck(unittest.TestCase):
    def run_check(self, jev, pplx):
        fake = FakeChoice(lambda q, ev, p: jev if p == 'jev' else pplx)
        return X.cross_check(SimpleNamespace(allow_live=True), 'How many?', cand('a', 4)['graph'], cand('a', 4)['result'],
                             as_of_date='2030-01-01', s1=fake)

    def test_verdicts(self):
        self.assertEqual(self.run_check('answers_fully', 'answers_partially')['verdict'], 'confirmed')
        self.assertEqual(self.run_check('answers_a_different_question', 'answers_a_different_question')['verdict'], 'refuted')
        self.assertEqual(self.run_check('answers_fully', 'answers_a_different_question')['verdict'], 'split')
        self.assertEqual(self.run_check('unknown', 'answers_fully')['verdict'], 'unavailable')

    def verdict(self, s1_verdict, claude_ok=True):
        v = {'accept': True, 'reasons': [], 'annotations': [], 'cost': {'new_paid_api_calls': 0, 'usd': 0.0}}
        stack = SimpleNamespace(allow_live=True, path='/nonexistent')
        out = {'verdict': s1_verdict, 'votes': {'jev': {'label': 'answers_fully', 'p': 0.9}, 'perplexity': {'label': 'answers_fully', 'p': 0.9}},
               'calls': 2, 'cost_usd': 0.0004}
        with patch.object(A._s1_gate, 'ready', return_value=True), patch.object(X, 'cross_check', return_value=out), \
                patch('clinical_core.agere.clinical.answer_guard.PendingCheck') as claude:
            claude.return_value.result.return_value = None  # the Claude path times out in the test
            r = A._cross_check(stack, 'How many?', {}, {}, {'status': 'answered', 'answer_type': 'count', 'count': 4}, None, object(), None, {}, v,
                               claude_ok=claude_ok)
        return r, v, claude

    def test_system_one_confirms_without_claude(self):
        r, v, claude = self.verdict('confirmed')
        self.assertEqual(r, 'agree'); claude.assert_not_called(); self.assertEqual(v['cost']['new_paid_api_calls'], 2)

    def test_refuted_rejects_this_proposal(self):
        r, v, claude = self.verdict('refuted')
        self.assertFalse(v['accept']); claude.assert_not_called()

    def test_split_escalates_to_claude_only_when_available(self):
        r, v, claude = self.verdict('split', claude_ok=False)
        self.assertEqual(r, 'split'); claude.assert_not_called()
        r, v, claude = self.verdict('split', claude_ok=True)
        claude.assert_called_once()


class Stability(unittest.TestCase):
    def test_pass_k(self):
        a = {'status': 'answered', 'answer_type': 'count', 'count': 4}
        self.assertTrue(S.stability([a, dict(a), dict(a)])['pass_k'])
        s = S.stability([a, {**a, 'count': 5}, dict(a)])
        self.assertEqual((s['k'], s['distinct'], s['pass_k'], s['majority_share']), (3, 2, False, 0.6667))

    def test_record_and_report(self):
        d = tempfile.mkdtemp()
        S.record(d, 'q1', 'atomic_system_one', True, {'k': 2, 'distinct': 1, 'pass_k': True, 'majority_share': 1.0})
        S.record(d, 'q2', 'atomic_system_one', True, {'k': 2, 'distinct': 2, 'pass_k': False, 'majority_share': 0.5})
        self.assertEqual(S.report(d)['pass_k_rate'], 0.5)


class FakeCascade:
    def __init__(self, tiers, allow_live=True):
        self.stack = SimpleNamespace(allow_live=allow_live, path='/nonexistent')
        self.question, self.population, self.claude_down, self.budget, self.ledger = 'How many patients have heart failure?', None, None, None, None
        self.log, self.spent = [], []
        for name, value in tiers.items(): setattr(self, 'tier_' + name, (lambda v: lambda: v)(value))

    def note(self, name, outcome, detail, started): self.log.append({'proposer': name, 'outcome': outcome, 'detail': detail})
    def spend(self, calls, usd, known=True): self.spent.append((calls, usd))


def contract(value, cert='uncalibrated'):
    return ({'kind': 'checked_graph', 'graph': {'nodes': []}, 'legacy_receipt': {},
             'executed': {'answer': {'status': 'answered', 'answer_type': 'count', 'count': value}, 'answer_checks': {'certificate': {'status': cert}}}},
            {'interpreter': 'x'})


class ClaimVerification(unittest.TestCase):
    ORDER = ('semantic_cache', 'learned_verified_sql', 'evidence_retrieval', 'atomic_system_one', 'hosted_verified_sql')

    def test_verifier_order_independent_cheapest_first(self):
        v = CV.verifiers(self.ORDER, 'semantic_cache')
        self.assertNotIn('learned_verified_sql', v)  # same family: saved work never verifies saved work
        self.assertEqual(v, ['evidence_retrieval', 'atomic_system_one', 'hosted_verified_sql'])
        self.assertNotIn('hosted_verified_sql', CV.verifiers(self.ORDER, 'semantic_cache', claude_down=True))

    def test_agreement_confirms(self):
        c = FakeCascade({'evidence_retrieval': contract(4)})
        out, interp = CV.verify(c, self.ORDER, 'semantic_cache', contract(4))
        self.assertEqual(interp['claim_verification']['status'], 'confirmed')
        self.assertTrue(interp['answer_stability']['pass_k'])

    def test_certified_answer_is_not_reverified(self):
        c = FakeCascade({'evidence_retrieval': contract(4)})
        out, interp = CV.verify(c, self.ORDER, 'semantic_cache', contract(4, cert='certified'))
        self.assertNotIn('claim_verification', interp)

    def test_judge_replaces_the_first_claim(self):
        c = FakeCascade({'evidence_retrieval': contract(5)})
        with patch.object(J, 'compare', return_value={'verdict': 'B', 'calls': 4, 'cost_usd': 0.0008}):
            out, interp = CV.verify(c, self.ORDER, 'semantic_cache', contract(4))
        self.assertEqual(out['executed']['answer']['count'], 5)
        self.assertEqual((interp['verified_proposer'], interp['claim_verification']['status']), ('evidence_retrieval', 'replaced_by_verifier'))
        self.assertEqual(c.spent, [(4, 0.0008)])

    def test_unreconciled_keeps_first_with_readings(self):
        c = FakeCascade({'evidence_retrieval': contract(5)})
        with patch.object(J, 'compare', return_value={'verdict': 'split', 'calls': 4, 'cost_usd': 0.0}):
            out, interp = CV.verify(c, self.ORDER, 'semantic_cache', contract(4))
        a = out['executed']['answer']
        self.assertEqual((a['count'], a['confidence'], len(a['readings'])), (4, 'low', 2))
        self.assertFalse(interp['answer_stability']['pass_k'])

    def test_cascade_run_verifies(self):
        class C(P.Cascade):
            def order(self): return ('semantic_cache', 'evidence_retrieval')
            def tier_semantic_cache(self): return contract(4)
            def tier_evidence_retrieval(self): return contract(4)
        _, interp = C(SimpleNamespace(allow_live=False, path='/nonexistent'), None, 'How many patients have heart failure?',
                      ledger=SimpleNamespace(close=lambda: None)).run()
        self.assertEqual(interp['claim_verification']['status'], 'confirmed')
        self.assertIn('requirement_ledger', interp)
        self.assertEqual(interp['proposer']['name'], 'semantic_cache')


class StoreAuditAndQuarantine(unittest.TestCase):
    def test_ppi_mean(self):
        out = SA.ppi_mean([0, 0, 1, 0, 0, 0, 0, 1, 0, 0], [(1, 0), (0, 0), (1, 1), (0, 0)])
        self.assertAlmostEqual(out['estimate'], 0.2 + 0.25)
        self.assertFalse(out['prediction_only'])
        self.assertTrue(SA.ppi_mean([0.5, 0.5], [])['prediction_only'])

    def test_check_flags_block_saving_and_quarantine(self):
        from clinical_core.agere.clinical import learning_hooks as H
        result = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 3},
                  'answer_checks': {'system_one_cross_check': {'verdict': 'split'}},
                  'planner_receipt': {'interpretation': {'proposer': {'name': 'evidence_retrieval'}, 'execution_accepted': True,
                                                         'answer_stability': {'k': 2, 'pass_k': False}}},
                  'graph': {'nodes': [{'operation': 'sql', 'parameters': {'query': 'SELECT 3', 'returns': 'count'}}]}}
        flags = H.check_flags(result)
        self.assertIn('System One families disagree', flags); self.assertIn('unstable answer (not pass^2)', flags)
        d = Path(tempfile.mkdtemp()); (d / 'run.json').write_text(json.dumps({'source_digest': 'x'}))
        H.quarantine_pair(SimpleNamespace(path=str(d)), 'q', result, flags)
        with sqlite3.connect(d / 'learning.sqlite3') as db:
            self.assertEqual(db.execute('SELECT route, controls FROM quarantined').fetchone()[0], 'cascade:evidence_retrieval')


class Router(unittest.TestCase):
    def rows(self):
        out = []
        for i in range(20):
            out.append({'question': f'what does the chart say about patient P-{i} statin intolerance', 'tier': 'evidence_review', 'right': True, 'source': 't'})
            out.append({'question': f'how many patients with diabetes had an a1c in 20{i:02d}', 'tier': 'evidence_review', 'right': False, 'source': 't'})
            out.append({'question': f'how many patients with diabetes had an a1c in 20{i:02d}', 'tier': 'semantic_cache', 'right': True, 'source': 't'})
            out.append({'question': f'what does the chart say about patient P-{i} statin intolerance', 'tier': 'semantic_cache', 'right': False, 'source': 't'})
        return out

    def test_train_predict_and_reorder_never_drops(self):
        model = R.train(self.rows())
        p = R.predict('what does the chart say about patient P-99 statin intolerance', model)
        q = R.predict('how many patients with diabetes had an a1c in 2031', model)
        self.assertGreater(p['evidence_review'], q['evidence_review'])
        order = ('exact_local_plan', 'semantic_cache', 'evidence_review', 'typed_decider', 'hosted_verified_sql')
        out, rec = R.reorder(SimpleNamespace(), 'what does the chart say about patient P-99 statin intolerance', order, model=model)
        self.assertEqual(sorted(out), sorted(order)); self.assertEqual(out[0], 'exact_local_plan')
        self.assertLess(out.index('evidence_review'), out.index('semantic_cache'))
        self.assertEqual(out[-1], 'hosted_verified_sql')  # a Claude tier never jumps ahead of free / System One tiers

    def test_outcome_labels(self):
        d = Path(tempfile.mkdtemp())
        with sqlite3.connect(d / 'learning.sqlite3') as db:
            db.executescript("CREATE TABLE outcomes(question, route, status, reason, paid_calls, usd, created);"
                             "CREATE TABLE corrections(question, note, expected, created); CREATE TABLE quarantined(question);"
                             "INSERT INTO outcomes VALUES ('q1','metric_router','answered','',0,0,0),('q2','metric_router','escalated','',0,0,0),"
                             "('q3','metric_router','answered','',0,0,0); INSERT INTO corrections VALUES ('q3','wrong',null,0);")
        rows = {r['question']: r['right'] for r in R.outcome_labels([d])}
        self.assertEqual(rows, {'q1': True, 'q2': False, 'q3': False})


class Graded(unittest.TestCase):
    def test_graded_certificate_used_when_target_fails(self):
        scores, errors = [1.0] * 100, [i < 6 for i in range(100)]  # 6% error: 2% impossible, 15% certifiable
        g = CT.fit_graded(scores, errors, grid=[1.0])
        self.assertIsNotNone(g); self.assertGreaterEqual(g['alpha'], 0.10)
        d = Path(tempfile.mkdtemp()) / 't.json'
        rec = {**CT.fit('x', scores, errors, 0.5, {}, grid=[1.0]), 'graded': g}
        CT.write([rec], d)
        got = CT.get('x', 0.5, path=d)
        self.assertEqual((got['threshold'], got['calibrated'], got['graded']), (1.0, False, True))
        self.assertIn('graded: error among accepted', got['guarantee'])


if __name__ == '__main__':
    unittest.main()
