"""VBC knowledge base: corpus schema, retrieval on held-out paraphrases, routing, answer contract.

Held-out questions below were written separately from the entries' own `questions` lists (paraphrases, different
vocabulary); a hit means one of the acceptable entry ids is ranked first (top-1) or in the first three (top-3).
Retrieval runs BM25-only always, and BM25 + the pinned local MiniLM encoder when it is installed."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import (analysis_graph, capability_registry, graph_contracts, graph_planner, local_compiler,
                                          operator_router, source_profile, vbc_knowledge)

AS_OF = '2029-08-15'
ROOT = Path(__file__).resolve().parent
MINILM = [p for p in (ROOT / 'models' / 'minilm-l6-v2', Path('/Users/mikel/clinical-insights-local/models/minilm-l6-v2'))
          if (p / 'agere-model.json').exists()]

# (question, acceptable entry ids) - paraphrases not copied from any entry's questions list
HELD_OUT = [
    ("Why do insurers like UnitedHealth want to own doctors' offices?", ['cmp-payer-owned-primary-care-vertical-integration']),
    ('How much did CVS pay to buy Oak Street?', ['co-oak-street-cvs-acquisition']),
    ("What happened to agilon's medical margins?", ['co-agilon-financial-pressure']),
    ('Did the Camden program for frequent hospital users lower readmissions?', ['ev-hotspotting-rct-finkelstein-2020']),
    ('How many condition categories are in the newest Medicare risk model versus the prior one?', ['ra-v28-model-structure', 'ra-v28-constrained-hccs']),
    ('When does a Medicare Advantage plan get a quality bonus?', ['q-ma-quality-bonus-payments', 'mech-ma-quality-bonus-coding-payment-level']),
    ('What did the court decide about the CMS audit rule for MA overpayments?', ['ra-radv-litigation-vacatur']),
    ('Which Innovation Center models were cut early in 2025?', ['cms-strategy-2025-model-terminations']),
    ('What will replace REACH when it ends?', ['model-lead', 'ind-cms-lead-model', 'mech-reach-successor-lead']),
    ("Porter's idea of organizing care around a medical condition with a dedicated team", ['tl-porter-integrated-practice-units']),
    ('Who leads the population health school at Thomas Jefferson University?', ['tl-david-nash-population-health']),
    ('Do physician-run ACOs save more than hospital ACOs?', ['ev-mssp-physician-vs-hospital-aco-synthesis', 'ind-mssp-physician-led-vs-hospital-led', 'ev-mssp-three-years-mcwilliams-2018']),
    ('What portion of US health care payments flows through alternative payment models?', ['ind-hcplan-apm-measurement-cy2024', 'ind-hcplan-apm-trend-2021-2024', 'mech-hcplan-apm-measurement-goals']),
    ('Does the dementia care model pay for caregiver support?', ['model-guide-dementia']),
    ('How does Evolent take financial risk for cancer care?', ['co-evolent-performance-suite-risk', 'co-evolent-overview-model', 'co-evolent-new-century-health-acquisition']),
    ('How are Star thresholds kept from swinging too much year to year?', ['q-star-cut-points-tukey']),
    ('Who bought One Medical and for how much?', ['co-amazon-one-medical-acquisition']),
    ('According to the budget office, did CMMI save the government money in its first ten years?', ['cms-strategy-cbo-2023-cmmi-spending', 'ev-cbo-cmmi-2023']),
    ("Can a Medicare beneficiary pick their own clinician so they land in that doctor's ACO?", ['attr-mssp-voluntary-alignment', 'attr-reach-voluntary-alignment']),
    ('Is the Shared Savings Program roster fixed at the start of the year or set after it ends?', ['attr-mssp-prospective-vs-preliminary']),
]

KNOWLEDGE_PHRASINGS = [
    "How does ACO REACH's benchmark work?",
    'How do patients get attributed to an MSSP ACO?',
    "What's the difference between Aledade's and agilon's models?",
    "What is Porter's value agenda?",
    'Explain the CMS-HCC V28 transition',
    'What is RADV?',
    'Does hotspotting work?',
    'How are Medicare Advantage Star Ratings calculated?',
    'What is voluntary alignment in REACH?',
    'Compare upside-only and two-sided risk arrangements',
    'What is the CMS Innovation Center 2030 accountable care goal?',
    'Who owns Oak Street Health?',
    'Is there evidence that care management programs reduce admissions?',
    'Tell me about Lumeris',
]
DATA_PHRASINGS = [
    'How many patients are attributed to each practice?',
    'What is our PMPM by practice?',
    'What is the average risk score of attributed members?',
    'Which practices have truly high ED use after adjusting for panel size?',
    'Show me patients with A1c over 9',
    'What would our shared savings be with a benchmark of $90 PMPM and a 50% sharing rate?',
    'How many Medicare Advantage members had an ED visit last month?',
    'What is the readmission rate by payer?',
    'List members with no PCP visit this year',
    'What is the distribution of risk scores?',
    'Compare ED visit rates between practices',
    'Why did ED visits increase?',
    'How does our MSSP benchmark compare to actual spend?',
    'Did the care management program reduce ED visits?',
]


class Corpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = vbc_knowledge.load()

    def test_every_entry_is_valid_and_cited(self):
        count, problems = vbc_knowledge.check_files(vbc_knowledge.corpus_files())
        self.assertEqual(problems, [])
        self.assertGreaterEqual(count, 150)
        for e in self.entries:
            self.assertTrue(e['citations'], e['id'])
            self.assertTrue(all(c['url'].startswith('http') for c in e['citations']), e['id'])
            self.assertLessEqual(len(e['summary'].split()), 260, e['id'])

    def test_quotes_are_short(self):
        for e in self.entries:
            for c in e['citations']:
                self.assertLessEqual(len(str(c.get('quote', '')).split()), 25, e['id'])

    def test_every_topic_is_covered(self):
        topics = {e['topic'] for e in self.entries}
        self.assertEqual(topics, set(vbc_knowledge.TOPICS))

    def test_contradictions_are_recorded(self):
        self.assertGreater(sum(bool(e.get('contradictions')) for e in self.entries), 5)

    def test_schema_rejects_bad_entries(self):
        good = dict(self.entries[0])
        self.assertEqual(vbc_knowledge.entry_problems(good), [])
        self.assertIn('no citations', vbc_knowledge.entry_problems({**good, 'citations': []}))
        self.assertTrue(any('summary words' in p for p in vbc_knowledge.entry_problems({**good, 'summary': 'word ' * 300})))
        long_quote = [{**good['citations'][0], 'quote': 'x ' * 30}]
        self.assertIn('quote over 25 words', vbc_knowledge.entry_problems({**good, 'citations': long_quote}))


def _accuracy(encoder):
    idx = vbc_knowledge.Index(vbc_knowledge.load(), vbc_knowledge.private_encoder(encoder) if encoder else None)
    top1 = top3 = 0
    misses = []
    for q, ok in HELD_OUT:
        hits, _ = idx.search(q, 3)
        ids = [h['entry']['id'] for h in hits]
        top1 += ids[0] in ok
        top3 += any(i in ok for i in ids)
        if ids[0] not in ok: misses.append((q, ids))
    return top1, top3, misses


class Retrieval(unittest.TestCase):
    def test_held_out_ids_exist(self):
        ids = {e['id'] for e in vbc_knowledge.load()}
        self.assertEqual(len(HELD_OUT), 20)
        for q, ok in HELD_OUT:
            self.assertTrue(set(ok) <= ids, (q, set(ok) - ids))

    def test_bm25_held_out(self):
        top1, top3, misses = _accuracy(None)
        print(f'\n[vbc_knowledge] BM25 held-out top-1 {top1}/20, top-3 {top3}/20; misses: {misses}')
        self.assertGreaterEqual(top3, 16)
        self.assertGreaterEqual(top1, 13)

    @unittest.skipUnless(MINILM, 'local MiniLM encoder not installed')
    def test_hybrid_held_out(self):
        os.environ.setdefault('AGERE_KNOWLEDGE_ENCODER_PATH', str(MINILM[0]))
        enc = vbc_knowledge.default_encoder()
        self.assertIsNotNone(enc)
        top1, top3, misses = _accuracy(enc)
        print(f'\n[vbc_knowledge] BM25+MiniLM held-out top-1 {top1}/20, top-3 {top3}/20; misses: {misses}')
        self.assertGreaterEqual(top3, 16)
        self.assertGreaterEqual(top1, 12)

    def test_answer_returns_cited_entries(self):
        r = vbc_knowledge.answer('How are beneficiaries assigned to a Shared Savings Program ACO?', encoder=None)
        self.assertTrue(r['answered'])
        self.assertTrue(r['sources'])
        self.assertTrue(all(h['citations'] for h in r['hits']))
        self.assertIn('[1', r['answer'])
        self.assertEqual(r['new_paid_api_calls'], 0)

    def test_off_topic_is_not_answered(self):
        r = vbc_knowledge.answer('What is the boiling point of tungsten in kelvin?', encoder=None)
        self.assertFalse(r['answered'])
        self.assertIn('No knowledge entry', vbc_knowledge.summary(r))

    def test_topic_filter(self):
        r = vbc_knowledge.answer('benchmark', topic='market_players', encoder=None)
        self.assertTrue(all(h['topic'] == 'market_players' for h in r['hits']))

    def test_synthesis_is_off_by_default(self):
        os.environ.pop('AGERE_KNOWLEDGE_SYNTHESIS', None)
        self.assertFalse(vbc_knowledge.synthesis_enabled())
        self.assertIsNone(vbc_knowledge.synthesize('q', [], allow_live=True, ledger=object(), out='/nonexistent'))


class Validation(unittest.TestCase):
    def test_parameters(self):
        self.assertEqual(vbc_knowledge.validate({'query': ' what is MSSP? '}), {'query': 'what is MSSP?', 'top': 5, 'topic': 'any'})
        for bad in ({}, {'query': 'x'}, {'query': 'what is mssp', 'top': 0}, {'query': 'what is mssp', 'topic': 'zebra'},
                    {'query': 'what is mssp', 'population': {}}):
            with self.assertRaises(ValueError): vbc_knowledge.validate(bad)

    def test_registered_everywhere(self):
        self.assertIn('knowledge', capability_registry.PRODUCT)
        self.assertEqual(capability_registry.PRODUCT['knowledge']['population'], 'none')
        self.assertIn('knowledge', graph_contracts.TYPED)
        self.assertIn('knowledge', graph_planner.CONTRACTS)
        self.assertIn('knowledge', analysis_graph.OPERATIONS)
        self.assertEqual(graph_contracts.input_contract('knowledge')['count'], [0, 0])
        schema = graph_contracts.parameter_schema('knowledge', {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}})
        self.assertEqual(set(schema['properties']), {'query', 'top', 'topic'})
        tool = operator_router.tool('knowledge')
        self.assertNotIn('population', tool['input_schema']['properties'])

    def test_router_graph_validates(self):
        g = operator_router.to_graph('What is RADV?', {'name': 'knowledge', 'input': {'parameters': {'query': 'What is RADV?'}}})
        plan = analysis_graph.validate(g, AS_OF)
        self.assertEqual([n['operation'] for n in plan['nodes']], ['knowledge'])


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_profile import Fixture
        Fixture.setUpClass()
        cls.profile = source_profile.load(Fixture.path)

    def test_explanations_go_to_knowledge(self):
        for q in KNOWLEDGE_PHRASINGS:
            with self.subTest(q=q):
                g, info = local_compiler.compile(q, self.profile, AS_OF)
                self.assertIsNotNone(g, info.get('reason'))
                self.assertEqual(info['route'], 'local_compiler')
                self.assertEqual([n['operation'] for n in g['nodes']], ['knowledge'])
                self.assertEqual(g['nodes'][0]['parameters']['query'], q)
                analysis_graph.validate(g, AS_OF)

    def test_data_questions_never_go_to_knowledge(self):
        for q in DATA_PHRASINGS:
            with self.subTest(q=q):
                self.assertIsNone(local_compiler._knowledge_question(q))
                g, info = local_compiler.compile(q, self.profile, AS_OF)
                ops = [n['operation'] for n in (g or {}).get('nodes', [])]
                self.assertNotIn('knowledge', ops)

    def test_bench_questions_are_not_stolen(self):
        stolen = []
        for f in sorted((ROOT / 'bench').rglob('*.json')):
            if f.parent.name == 'acceptance' and f.name == 'knowledge.json': continue
            try:
                data = json.loads(f.read_text())
            except ValueError:
                continue
            stack = [data]
            while stack:
                x = stack.pop()
                if isinstance(x, dict):
                    for k, v in x.items():
                        if k == 'question' and isinstance(v, str):
                            if local_compiler._knowledge_question(v): stolen.append((f.name, v))
                        else: stack.append(v)
                elif isinstance(x, list): stack.extend(x)
        self.assertEqual(stolen, [])

    def test_acceptance_phrasings(self):
        rows = json.loads((ROOT / 'bench' / 'acceptance' / 'knowledge.json').read_text())
        self.assertGreaterEqual(len(rows), 10)
        for row in rows:
            with self.subTest(q=row['question']):
                g, info = local_compiler.compile(row['question'], self.profile, AS_OF)
                self.assertEqual(g['nodes'][-1]['operation'], row['expect_operation'])


class AnswerContract(unittest.TestCase):
    def plan(self, q):
        g, _ = local_compiler.compile(q, {}, AS_OF)
        return analysis_graph.validate(g, AS_OF)

    def test_answered(self):
        q = 'How do patients get attributed to an MSSP ACO?'
        plan = self.plan(q)
        report = vbc_knowledge.execute(plan['nodes'][0]['parameters'])
        answer = analysis_graph.build_answer(plan, [{'node': plan['nodes'][0], 'result': report}], {}, q)
        answer['headline'] = analysis_graph.headline(answer)
        answer['benchmark'] = analysis_graph.benchmark_references(answer, q, 'knowledge')
        answer = json.loads(json.dumps(answer, allow_nan=False, default=str))
        self.assertEqual((answer['status'], answer['answer_type']), ('answered', 'table'))
        self.assertEqual(answer['benchmark'], [])
        self.assertTrue(answer['headline'])
        self.assertIn('[1', answer['value'])
        k = answer['overall']['knowledge']
        self.assertTrue(k['sources'])
        self.assertIsNone(k['synthesis'])
        for row in answer['table']:
            self.assertTrue({'n', 'id', 'title', 'summary', 'citations', 'confidence', 'last_verified'} <= set(row))
            self.assertTrue(row['citations'])

    def test_unanswerable(self):
        q = 'What is the capital of Mongolia?'
        plan = analysis_graph.validate({'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
                                        'nodes': [{'id': 'answer', 'operation': 'knowledge', 'inputs': [], 'purpose': 'x',
                                                   'parameters': {'query': q}}]}, AS_OF)
        report = vbc_knowledge.execute(plan['nodes'][0]['parameters'])
        answer = analysis_graph.build_answer(plan, [{'node': plan['nodes'][0], 'result': report}], {}, q)
        self.assertEqual(answer['status'], 'unsupported')
        self.assertTrue(answer['missing'])

    def test_view_renders_knowledge(self):
        from clinical_core.agere import ask_view
        self.assertIn('function vizKnowledge', ask_view.PAGE)
        self.assertIn('if(o.knowledge)return vizKnowledge(a)', ask_view.PAGE)

    def test_compose_with_fixture_corpus(self):
        d = Path(tempfile.mkdtemp())
        cite = {'url': 'https://example.org/a', 'title': 'A', 'publisher': 'P', 'date': '2024', 'retrieved': '2026-10-13', 'source_type': 'government'}
        base = {'facts': {}, 'last_verified': '2026-10-13', 'confidence': 'high'}
        rows = [{**base, 'id': 'kb-alpha', 'topic': 'attribution', 'title': 'Alpha assignment', 'questions': ['How does alpha assignment work?'],
                 'summary': 'Alpha assignment places each beneficiary with the clinician who billed the plurality of their primary care services in the window, and the roster is refreshed every quarter for the group.',
                 'citations': [cite], 'contradictions': [{'claim': 'Window length', 'sources': ['https://example.org/a'], 'note': 'one source says 12 months, another 24'}]},
                {**base, 'id': 'kb-beta', 'topic': 'quality', 'title': 'Beta measures', 'questions': ['What are beta measures?'],
                 'summary': 'Beta measures are the quality yardsticks reported each year and they determine part of the shared savings rate for the group in the year that follows.',
                 'citations': [{**cite, 'url': 'https://example.org/b'}]}]
        (d / 'x.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
        r = vbc_knowledge.execute({'query': 'how does alpha assignment work'}, directory=d)
        self.assertEqual(r['hits'][0]['id'], 'kb-alpha')
        self.assertIn('Sources disagree', r['answer'])
        self.assertIn('[1]', r['answer'])
        (d / 'y.jsonl').write_text(json.dumps({**rows[1], 'citations': []}))
        with self.assertRaises(ValueError): vbc_knowledge.load(d)


if __name__ == '__main__':
    unittest.main()
