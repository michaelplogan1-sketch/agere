"""System One unit tests (mocked gateway) + an opt-in live smoke (AGERE_LIVE_SYSTEM_ONE=1, <= 30 calls)."""
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.clinical import system_one as s1
from clinical_core.agere.gateway import BudgetLedger

OPTS = {'yes': 'documented', 'no': 'denied', 'unknown': 'not addressed'}
AS_OF = '2026-10-01'


def dist(top, p, labels=('yes', 'no', 'unknown')):
    rest = (1 - p) / (len(labels) - 1)
    return {k: (p if k == top else rest) for k in labels}


class FakeGateway:
    """classify(provider, task) -> rule(provider, task) dict; raise when the rule returns an Exception."""
    calls = []

    def __init__(self, rule, allow_live=True):
        self.rule, self.allow_live = rule, allow_live

    def classify(self, provider, task):
        FakeGateway.calls.append((provider, task))
        if not self.allow_live:
            return {'status': 'prepared', 'provider': provider, 'request_hash': 'h-prep'}
        out = self.rule(provider, task)
        if isinstance(out, Exception):
            raise out
        return out


def answer(label, p, *, cache=False, cost=0.0002, ids=()):
    return {'label': label, 'probabilities': dist(label, p), 'evidence_ids': list(ids), 'model': 'm', 'request_hash': 'h',
            **({'cache_hit': True, 'new_paid_api_usd': 0} if cache else {'cost_estimate': {'estimated_usd': cost}})}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tmp.name) / 'ledger.sqlite3'
        self.cal = Path(self.tmp.name) / 'cal.json'
        self.env = mock.patch.dict(os.environ, {'AGERE_SYSTEM_ONE_CALIBRATION': str(self.cal), 'AGERE_LEDGER_PATH': str(self.ledger_path),
                                                'AGERE_SYSTEM_ONE_DECIDERS': str(Path(self.tmp.name) / 'deciders.json'),
                                                'AGERE_SYSTEM_ONE_STUDENTS': str(Path(self.tmp.name) / 'students'),
                                                'AGERE_SYSTEM_ONE_DISTILL': str(Path(self.tmp.name) / 'distill.sqlite3')})
        self.env.start()
        FakeGateway.calls = []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def rule(self, fn):
        return mock.patch.object(s1, '_gateway', lambda ledger, allow_live: FakeGateway(fn, allow_live))


class ChoiceTests(Base):
    def test_live_result_shape_and_cost(self):
        with self.rule(lambda p, t: answer('yes', 0.9, ids=['n1'])):
            r = s1.choice('Statin intolerance?', OPTS, [{'id': 'n1', 'text': 'myalgia on atorvastatin', 'date': '2026-09-01'}], as_of=AS_OF)
        self.assertEqual(r['label'], 'yes'); self.assertEqual(r['evidence_ids'], ['n1'])
        self.assertAlmostEqual(r['cost_usd'], 0.0002); self.assertFalse(r['cache_hit'])
        self.assertEqual(r['calibrated_probabilities'], r['probabilities'])  # no task_key -> identity
        task = FakeGateway.calls[0][1]
        self.assertEqual(task.evidence[0], {'id': 'n1', 'text': 'myalgia on atorvastatin', 'date': '2026-09-01', 'note_id': 'n1'})

    def test_cache_hit_is_free(self):
        with self.rule(lambda p, t: answer('no', 0.7, cache=True)):
            r = s1.choice('q', OPTS, [], as_of=AS_OF)
        self.assertTrue(r['cache_hit']); self.assertEqual(r['cost_usd'], 0.0)

    def test_unknown_option_added(self):
        with self.rule(lambda p, t: answer('unknown', 0.6)):
            s1.choice('q', {'yes': 'a', 'no': 'b'}, [], as_of=AS_OF)
        self.assertIn('unknown', FakeGateway.calls[0][1].options)

    def test_prepared_never_calls_real_gateway(self):
        # Real PersistentGateway, allow_live=False: no key needed, no transport touched.
        def boom(*a, **k): raise AssertionError('network used')
        with mock.patch('urllib.request.urlopen', boom), mock.patch.dict(os.environ, {'TYPESAFE_API_KEY': ''}):
            r = s1.choice('q', OPTS, [{'id': 'a', 'text': 't', 'date': '2026-01-01'}], as_of=AS_OF, allow_live=False,
                          ledger=self.ledger_path)
        self.assertEqual(r['status'], 'prepared'); self.assertIsNone(r['label']); self.assertTrue(r['request_hash'])

    def test_bad_provider(self):
        with self.assertRaises(ValueError):
            s1.choice('q', OPTS, [], as_of=AS_OF, provider='claude')

    def test_available_reports_presence_only(self):
        with mock.patch.dict(os.environ, {'TYPESAFE_API_KEY': 'x', 'PERPLEXITY_API_KEY': ''}):
            self.assertEqual(s1.available(), {'jev': True, 'perplexity': False})


class DecideTests(Base):
    def test_jev_confident_stops(self):
        with self.rule(lambda p, t: answer('yes', 0.95)):
            r = s1.decide('q', OPTS, [], as_of=AS_OF)
        self.assertEqual(r['decided_by'], 'jev'); self.assertEqual([c[0] for c in FakeGateway.calls], ['jev'])

    def test_unknown_escalates_to_perplexity(self):
        with self.rule(lambda p, t: answer('unknown', 0.9) if p == 'jev' else answer('no', 0.85, cost=0.0001)):
            r = s1.decide('q', OPTS, [], as_of=AS_OF)
        self.assertEqual((r['decided_by'], r['label']), ('perplexity', 'no'))
        self.assertAlmostEqual(r['cost_usd'], 0.0003); self.assertEqual(len(r['steps']), 2)

    def test_provider_failure_falls_back(self):
        with self.rule(lambda p, t: RuntimeError('HTTP 503') if p == 'jev' else answer('yes', 0.9)):
            r = s1.decide('q', OPTS, [], as_of=AS_OF)
        self.assertEqual(r['decided_by'], 'perplexity'); self.assertEqual(r['steps'][0]['status'], 'error')

    def test_all_fail_returns_unknown(self):
        with self.rule(lambda p, t: ValueError('Missing key')):
            r = s1.decide('q', OPTS, [], as_of=AS_OF)
        self.assertEqual((r['label'], r['status'], r['decided_by']), ('unknown', 'providers_failed', None))

    def test_below_threshold_returns_best_known(self):
        with self.rule(lambda p, t: answer('yes', 0.6) if p == 'jev' else answer('no', 0.7)):
            r = s1.decide('q', OPTS, [], as_of=AS_OF)
        self.assertIsNone(r['decided_by']); self.assertEqual(r['label'], 'no')

    def test_prepared_when_not_live(self):
        with self.rule(lambda p, t: answer('yes', 0.9)):
            r = s1.decide('q', OPTS, [], as_of=AS_OF, allow_live=False)
        self.assertEqual(r['status'], 'prepared'); self.assertEqual(len(r['steps']), 2)

    def test_typed_question_has_no_evidence(self):
        with self.rule(lambda p, t: answer('yes', 0.9)):
            r = s1.typed_question('Is this about a medication?', OPTS, as_of=AS_OF)
        self.assertEqual(FakeGateway.calls[0][1].evidence, []); self.assertEqual(r['label'], 'yes')

    def test_threshold_uses_calibrated_probability(self):
        s1.fit_calibration('t', [{'probabilities': dist('yes', 0.9), 'label': 'no'}] * 5 + [{'probabilities': dist('yes', 0.9), 'label': 'yes'}] * 5)
        with self.rule(lambda p, t: answer('yes', 0.9)):
            r = s1.decide('q', OPTS, [], as_of=AS_OF, task_key='t')
        self.assertIsNone(r['decided_by'])  # raw 0.9 >= 0.8 but calibrated ~0.5 is not


class CalibrationTests(Base):
    def test_identity_without_params(self):
        p = dist('yes', 0.7)
        self.assertEqual(s1.calibrate('nothing', p), p)

    def test_shift_math(self):
        q = s1.calibrate(None, {'yes': 0.5, 'no': 0.5}, params={'shift': {'yes': math.log(2)}})
        self.assertAlmostEqual(q['yes'], 2 / 3); self.assertAlmostEqual(q['no'], 1 / 3)

    def test_fit_reduces_nll_and_ece_and_persists(self):
        # Overconfident 'yes': says 0.9 yes but only 60% are yes, 40% no.
        rows = [{'probabilities': dist('yes', 0.9), 'label': 'yes'}] * 6 + [{'probabilities': dist('yes', 0.9), 'label': 'no'}] * 4
        params = s1.fit_calibration('chart_assertion', rows)
        self.assertLess(params['nll_after'], params['nll_before']); self.assertLess(params['ece_after'], params['ece_before'])
        q = s1.calibrate('chart_assertion', dist('yes', 0.9))
        self.assertAlmostEqual(q['yes'], 0.6, delta=0.02); self.assertAlmostEqual(q['no'], 0.4, delta=0.02)
        saved = json.loads(self.cal.read_text())['tasks']['chart_assertion']
        self.assertEqual(saved['n'], 10); self.assertAlmostEqual(sum(saved['shift'].values()), 0, places=5)

    def test_shift_is_provider_scoped(self):
        params = {'shift': {'yes': -2.0, 'no': 1.0, 'unknown': 1.0}, 'source': {'provider': 'jev'}}
        p = dist('yes', 0.9)
        self.assertEqual(s1.calibrate(None, p, params=params, provider='perplexity'), p)
        self.assertLess(s1.calibrate(None, p, params=params, provider='jev')['yes'], 0.9)

    def test_seed_fits_from_provider_answers(self):
        truth = {t: lab for _, t, lab in s1.CHART_ASSERTION_SEED}
        with self.rule(lambda p, t: answer(truth[t.evidence[0]['text']], 0.8, cache=True)):
            params = s1.seed_chart_assertion(ledger=self.ledger_path)
        self.assertEqual(params['n'], len(s1.CHART_ASSERTION_SEED)); self.assertEqual(params['source']['raw_top_label_accuracy'], 1.0)
        self.assertLess(params['nll_after'], params['nll_before']); self.assertIn('ece_after_loo', params)

    def test_fit_needs_rows(self):
        with self.assertRaises(ValueError):
            s1.fit_calibration('x', [{'probabilities': {'yes': 1.0}, 'label': 'maybe'}], save=False)

    def test_ece(self):
        rows = [{'probabilities': {'yes': 0.8, 'unknown': 0.2}, 'label': 'yes'}] * 4 + [{'probabilities': {'yes': 0.8, 'unknown': 0.2}, 'label': 'unknown'}]
        self.assertAlmostEqual(s1.ece(rows), 0.0)


class AttributionTests(Base):
    NOTE = ('Follow-up for hyperlipidemia. Stopped atorvastatin because of severe myalgia. '
            'Blood pressure 128/76. Will recheck lipids in three months.')

    def model(self, p, t):
        hit = any('myalgia' in e['text'].lower() for e in t.evidence)
        return {'label': 'yes' if hit else 'unknown', 'probabilities': {'yes': 0.9 if hit else 0.1, 'no': 0.0, 'unknown': 0.1 if hit else 0.9},
                'evidence_ids': [], 'model': 'm', 'cache_hit': True}

    def test_necessity_and_sufficiency(self):
        with self.rule(self.model):
            rows = s1.attribute('Statin intolerance?', OPTS, [{'id': 'n1', 'text': self.NOTE, 'date': '2026-09-01'}], 'yes',
                                as_of=AS_OF, ledger=self.ledger_path)
        self.assertEqual(len(rows), 4); self.assertEqual(len(FakeGateway.calls), 10)
        top = rows[0]
        self.assertIn('myalgia', top['sentence']); self.assertEqual(top['evidence_id'], 'n1')
        self.assertAlmostEqual(top['necessity'], 0.8); self.assertAlmostEqual(top['sufficiency'], 0.8)
        self.assertTrue(all(r['necessity'] == 0 and r['sufficiency'] == 0 for r in rows[1:]))

    def test_cap_and_failure_is_none(self):
        def flaky(p, t):
            if len(t.evidence) == 1 and t.evidence[0]['text'].startswith('Blood'):
                return RuntimeError('HTTP 500')
            return self.model(p, t)
        with self.rule(flaky):
            rows = s1.attribute('q', OPTS, [{'id': 'n1', 'text': self.NOTE, 'date': '2026-09-01'}], 'yes', as_of=AS_OF,
                                ledger=self.ledger_path, max_sentences=3, workers=1)
        self.assertEqual(len(rows), 3)
        bp = [r for r in rows if r['sentence'].startswith('Blood')][0]
        self.assertIsNone(bp['sufficiency']); self.assertEqual(bp['necessity'], 0)

    def test_multi_evidence_keeps_ids(self):
        ev = [{'id': 'a', 'text': 'No complaints. Doing well.', 'date': '2026-09-01'},
              {'id': 'b', 'text': 'Myalgia on simvastatin.', 'date': '2026-09-02'}]
        with self.rule(self.model):
            rows = s1.attribute('q', OPTS, ev, 'yes', as_of=AS_OF, ledger=self.ledger_path)
        self.assertEqual(rows[0]['evidence_id'], 'b'); self.assertEqual({r['evidence_id'] for r in rows}, {'a', 'b'})


@unittest.skipUnless(os.environ.get('AGERE_LIVE_SYSTEM_ONE') == '1', 'live smoke is opt-in (AGERE_LIVE_SYSTEM_ONE=1)')
class LiveSmoke(unittest.TestCase):
    """<= 30 real calls: 2 decides (<= 4) + one 4-sentence attribution (10)."""
    def test_live(self):
        from run_integrated_stack import keys
        keys()
        ledger = os.environ.get('AGERE_LEDGER_PATH') or None
        q = 'Does the evidence document statin intolerance for the patient?'
        yes = s1.decide(q, s1.CHART_ASSERTION_OPTIONS, [{'id': 'n1', 'date': '2026-08-01', 'text':
              'Stopped atorvastatin 40 mg in June after severe myalgias; rosuvastatin 5 mg also caused leg cramps. Statin intolerant.'}],
              as_of=AS_OF, ledger=ledger, task_key='chart_assertion')
        none = s1.decide(q, s1.CHART_ASSERTION_OPTIONS, [{'id': 'n2', 'date': '2026-08-01', 'text':
               'Seen for right knee pain after a fall while gardening. Ice, ibuprofen and follow up in two weeks.'}],
               as_of=AS_OF, ledger=ledger, task_key='chart_assertion')
        note = ('Follow-up for hyperlipidemia. Stopped atorvastatin in May because of severe myalgia. '
                'Blood pressure 128/76 today. Will recheck lipids in three months.')
        attr = s1.attribute(q, s1.CHART_ASSERTION_OPTIONS, [{'id': 'n3', 'text': note, 'date': '2026-08-01'}], 'yes',
                            as_of=AS_OF, ledger=ledger)
        report = {'yes_note': {k: yes[k] for k in ('label', 'decided_by', 'probabilities', 'calibrated_probabilities', 'evidence_ids', 'cost_usd')},
                  'no_mention_note': {k: none[k] for k in ('label', 'decided_by', 'probabilities', 'calibrated_probabilities', 'cost_usd')},
                  'attribution': attr}
        print(json.dumps(report, indent=1))
        self.assertEqual(yes['label'], 'yes')
        self.assertIn(none['label'], ('unknown', 'no'))
        self.assertIn('myalgia', attr[0]['sentence'])


if __name__ == '__main__':
    unittest.main()
