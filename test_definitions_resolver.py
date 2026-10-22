"""definitions_resolver: detection against governed layers, cited Perplexity resolution (mocked), cache, planner context.

Live smoke (opt-in, <= 8 Perplexity Agent calls): AGERE_LIVE_SYSTEM_ONE=1 python -m unittest test_definitions_resolver.LiveSmoke
"""
import io, json, os, sys, tempfile, types, unittest
from pathlib import Path
from unittest import mock
from clinical_core.agere.clinical import definitions_resolver as dr
from clinical_core.agere.gateway import BudgetLedger


def agent_response(obj, *, results=None, cite='[1]', cost=0.0012):
    results = results if results is not None else [{'id': 1, 'title': 'CMS MSSP Specification', 'url': 'https://www.cms.gov/mssp-spec.pdf'},
                                                   {'id': 2, 'title': 'Unrelated blog', 'url': 'https://example.com/blog'}]
    return {'model': 'sonar-x', 'usage': {'cost': {'total_cost': cost}},
            'output': [{'type': 'search_results', 'results': results},
                       {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(obj) + ' ' + cite}]}]}


class Transport:
    def __init__(self, *responses):
        self.responses, self.bodies, self.headers = list(responses), [], []
    def __call__(self, request, timeout=None):
        self.bodies.append(json.loads(request.data)); self.headers.append(dict(request.header_items()))
        raw = self.responses.pop(0)
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return json.dumps(raw).encode()
        return R()


GOOD = {'term': 'HCC recapture rate', 'definition': 'Share of HCCs coded in the prior year that are coded again in the current year.',
        'numerator': 'Prior-year HCCs documented again in the current year', 'denominator': 'HCCs documented in the prior year among continuously enrolled members',
        'exclusions': ['Members who died or disenrolled', 'null'], 'period': 'Calendar year vs prior calendar year',
        'steward': 'Industry convention (CMS-HCC V28)', 'standard': False, 'source_ids': [1]}


class Detect(unittest.TestCase):
    def test_ungoverned_and_governed_terms(self):
        none = lambda *a, **k: None
        self.assertEqual(dr.detect('What is our HCC recapture rate?', confirm=none), ['HCC recapture rate'])
        self.assertEqual(dr.detect('How many attributed lives do we have?', confirm=none), ['attributed lives'])
        # Governed: measure library (bcs aliases + HEDIS steward), default definitions (PQI), plain cohort words.
        self.assertEqual(dr.detect('How many HEDIS breast cancer screening gaps do we have?', confirm=none), [])
        self.assertEqual(dr.detect('PQI admissions per 1,000 last year', confirm=none), [])
        self.assertEqual(dr.detect('AHRQ PQI 90 overall composite rate by payer', confirm=none), [])
        self.assertEqual(dr.detect('How many patients have diabetes?', confirm=none), [])

    def test_ambiguous_qualifier_uses_system_one_then_defaults_to_resolving(self):
        rows = dr.detect_details('What is our MSSP avoidable spend by practice?', confirm=lambda *a, **k: None)
        self.assertEqual((rows[0]['term'], rows[0]['status'], rows[0]['match'], rows[0]['extra']),
                         ('MSSP avoidable spend', 'ambiguous', 'default.avoidable_spend', ['mssp']))
        self.assertTrue(rows[0]['needs_definition']); self.assertEqual(rows[0]['decided_by'], 'deterministic_default')
        self.assertEqual(dr.detect('What is our MSSP avoidable spend?', confirm=lambda *a, **k: True), [])
        self.assertEqual(dr.detect('What is our MSSP avoidable spend?', confirm=lambda *a, **k: False), ['MSSP avoidable spend'])

    def test_system_one_typed_question_mock(self):
        calls = []
        def typed_question(ask, options, **kw):
            calls.append((ask, set(options), kw['task_key']))
            return {'label': 'governed', 'probabilities': {'governed': .93, 'needs_standard_definition': .05, 'unknown': .02}}
        fake = types.ModuleType('system_one'); fake.typed_question = typed_question
        with mock.patch.dict(sys.modules, {'clinical_core.agere.clinical.system_one': fake}):
            self.assertEqual(dr.detect('MSSP avoidable spend by practice'), [])
        self.assertEqual(calls[0][1], {'governed', 'needs_standard_definition', 'unknown'})
        def low(ask, options, **kw): return {'label': 'governed', 'probabilities': {'governed': .6, 'unknown': .4}}
        fake.typed_question = low
        with mock.patch.dict(sys.modules, {'clinical_core.agere.clinical.system_one': fake}):
            self.assertEqual(dr.detect('MSSP avoidable spend by practice'), ['MSSP avoidable spend'])

    def test_candidates_keep_measure_code_and_longest_span(self):
        self.assertEqual(dr.candidates('HEDIS breast cancer screening (BCS-E) rate for 2025'), ['HEDIS breast cancer screening (BCS-E) rate'])


class Resolve(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.cache = self.tmp / 'resolved.json'
        self.ledger = BudgetLedger(self.tmp / 'ledger.sqlite3')
        self.env = mock.patch.dict(os.environ, {'PERPLEXITY_API_KEY': 'test-key'}); self.env.start()
        dr._MEMO.clear()
    def tearDown(self): self.env.stop(); self.ledger.close()

    def run_resolve(self, term, transport, **kw):
        return dr.resolve(term, ledger=self.ledger, cache_path=self.cache, transport=transport, receipts=self.tmp / 'r', **kw)

    def test_parses_cites_caches_and_pays_once(self):
        t = Transport(agent_response(GOOD))
        r = self.run_resolve('HCC recapture rate', t)
        self.assertEqual(set(r) >= {'term', 'definition', 'numerator', 'denominator', 'exclusions', 'period', 'sources', 'provider', 'cost_usd', 'cached'}, True)
        self.assertEqual(r['sources'], [{'title': 'CMS MSSP Specification', 'url': 'https://www.cms.gov/mssp-spec.pdf'}])
        self.assertEqual(r['exclusions'], ['Members who died or disenrolled'])
        self.assertEqual((r['provider'], r['cached'], r['cost_usd'], r['status']), ('perplexity_agent', False, 0.0012, 'cited'))
        self.assertEqual(t.bodies[0]['preset'], 'fast'); self.assertIn('HCC recapture rate', t.bodies[0]['input'])
        self.assertFalse(any('@' in v for h in t.headers for v in h.values()))  # no email or identity in headers
        doc = json.loads(self.cache.read_text())
        self.assertEqual(doc['schema'], 'agere-definitions-resolved/1')
        self.assertIn('retrieved', doc['entries']['hcc recapture rate'])
        again = self.run_resolve('HCC  Recapture rate', Transport())  # no response queued: any call would fail
        self.assertEqual((again['cached'], again['cost_usd'], again['definition']), (True, 0.0, r['definition']))

    def test_uncited_fast_escalates_to_medium(self):
        t = Transport(agent_response(GOOD, results=[]), agent_response(GOOD, cost=.004))
        r = self.run_resolve('HCC recapture rate', t)
        self.assertEqual([b['preset'] for b in t.bodies], ['fast', 'medium'])
        self.assertEqual((r['status'], r['cost_usd']), ('cited', 0.0052))

    def test_offline_missing_key_and_provider_errors_never_raise(self):
        self.assertEqual(self.run_resolve('attributed lives', None, allow_live=False)['status'], 'not_resolved_offline')
        with mock.patch.dict(os.environ, {'PERPLEXITY_API_KEY': ''}):
            self.assertEqual(self.run_resolve('attributed lives', None)['status'], 'provider_unavailable')
        bad = {'model': 'x', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'no json here'}]}]}
        r = self.run_resolve('attributed lives', Transport(bad, bad))
        self.assertEqual((r['status'], r['definition']), ('provider_error', None)); self.assertEqual(len(r['errors']), 2)
        self.assertFalse(self.cache.exists())

    def test_context_and_disclosure(self):
        t = Transport(agent_response(GOOD))
        ctx = dr.context('What is our HCC recapture rate by practice?', ledger=self.ledger, cache_path=self.cache, transport=t,
                         confirm=lambda *a, **k: None)
        self.assertEqual([x['term'] for x in ctx['terms']], ['HCC recapture rate'])
        basis = ctx['definitions'][0]['basis']
        self.assertIn('Share of HCCs', basis); self.assertIn('https://www.cms.gov/mssp-spec.pdf', basis)
        self.assertIn('not recreating a governed definition', ctx['instruction'])
        self.assertEqual(dr.context('How many patients have diabetes?', allow_live=False), {})
        answer = dr.disclose({'definitions': [{'term': 'diabetes', 'basis': 'governed'}]}, ctx)
        self.assertEqual([d['term'] for d in answer['definitions']], ['diabetes', 'HCC recapture rate'])
        self.assertEqual(len(dr.disclose(answer, ctx)['definitions']), 2)  # idempotent


    def test_industry_phrase_without_numerator_computes_with_governed_match(self):
        phrase = {'term': 'MSSP avoidable spend', 'definition': 'Industry phrase for avoidable utilization spend [1].', 'numerator': None,
                  'denominator': None, 'exclusions': [], 'standard': False, 'source_ids': [1]}
        ctx = dr.context('What is our MSSP avoidable spend by practice?', ledger=self.ledger, cache_path=self.cache,
                         transport=Transport(agent_response(phrase)), confirm=lambda *a, **k: None)
        term = ctx['terms'][0]
        self.assertEqual((term['computed_with_governed'], term['definition']), ('default.avoidable_spend', 'Industry phrase for avoidable utilization spend.'))
        self.assertIn('Computed with the governed definition default.avoidable_spend', ctx['definitions'][0]['basis'])


@unittest.skipUnless(os.environ.get('AGERE_LIVE_SYSTEM_ONE') == '1', 'live Perplexity smoke is opt-in')
class LiveSmoke(unittest.TestCase):
    TERMS = ['MSSP avoidable spend', 'HEDIS breast cancer screening (BCS-E)', 'AHRQ PQI 90 overall composite', 'HCC recapture rate']
    def test_resolve_live(self):
        from run_integrated_stack import keys
        keys()
        from clinical_core.agere.gateway import default_ledger_path
        ledger = BudgetLedger(default_ledger_path()); total = 0
        try:
            for term in self.TERMS:
                r = dr.resolve(term, ledger=ledger)
                total += r.get('cost_usd') or 0
                print(json.dumps(r, indent=1, ensure_ascii=False))
                self.assertTrue(r['definition'], term); self.assertTrue(r['sources'], term)
        finally: ledger.close()
        print('total_cost_usd', round(total, 5))


if __name__ == '__main__':
    unittest.main()
