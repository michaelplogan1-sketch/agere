"""Terminology mapping: deterministic exact matches, retrieval, System One adjudication (mocked), saved mappings, the
grammar consuming mapped labels, the cascade tier and the terminology_map foundation engine."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from clinical_core.agere.clinical import terminology as T, terminology_engine as TE
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood

ACTIVE = Path(json.loads(Path('config/agere/active_warehouse.json').read_text())['build']) / 'stack'


def decider(label, p, calls):
    def fake(question, options, evidence, *, as_of, threshold, task_key, ledger, allow_live):
        calls.append({'question': question, 'options': dict(options), 'task_key': task_key, 'allow_live': allow_live})
        return {'label': label, 'probabilities': {label: p}, 'calibrated_probabilities': {label: p}, 'decided_by': 'jev' if p >= threshold else None,
                'cost_usd': 0.0002, 'request_hash': 'h1', 'steps': [{'provider': 'jev', 'status': 'answered', 'cache_hit': False, 'cost_usd': 0.0002}]}
    return fake


class Deterministic(unittest.TestCase):
    def test_exact_matches(self):
        cases = {'CHF': ('condition', 'heart_failure'), 'T2DM': ('condition', 'type_2_diabetes'),
                 'high blood pressure': ('condition', 'hypertension'), 'Chronic congestive heart failure': ('condition', 'heart_failure'),
                 'Entresto': ('medication', 'arni'), 'lisinopril': ('medication', 'ace_inhibitor'),
                 'sacubitril/valsartan': ('medication', 'arni'), 'glycated hgb': ('lab', 'hba1c'), 'HbA1c': ('lab', 'hba1c')}
        for label, (kind, concept) in cases.items():
            got = T.deterministic(label)
            self.assertEqual((got['kind'], got['concept']), (kind, concept), label)

    def test_never_swallows_extra_words(self):
        self.assertIsNone(T.deterministic('heart failure readmissions'))
        self.assertIsNone(T.deterministic('family history of heart failure'))
        self.assertIsNone(T.deterministic('patients'))

    def test_codes(self):
        self.assertIn('I50', T.codes('condition', 'heart_failure')['codes'])
        self.assertIn('4548-4', T.codes('lab', 'hba1c')['codes'])

    def test_retrieval_ranks_the_right_concept(self):
        top = T.retrieve('congestive cardiac failure')
        self.assertEqual(top[0]['concept'], 'heart_failure')
        self.assertLessEqual(len(top), T.MAX_OPTIONS)


class Adjudication(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.store = Path(self.dir.name) / T.STORE_NAME

    def tearDown(self): self.dir.cleanup()

    def test_system_one_accepts_and_saves_then_free(self):
        calls = []
        r = T.map_label('congestive cardiac failure', store=self.store, question='How many patients have congestive cardiac failure?',
                        as_of='2030-05-27', decider=decider('condition__heart_failure', 0.93, calls))
        self.assertEqual((r['method'], r['kind'], r['concept']), ('system_one', 'condition', 'heart_failure'))
        self.assertIn('none', calls[0]['options']); self.assertEqual(calls[0]['task_key'], 'terminology_map')
        self.assertEqual(r['calls'], 1); self.assertAlmostEqual(r['cost_usd'], 0.0002)
        again = T.map_label('Congestive  cardiac failure', store=self.store, decider=decider('none', 0.99, calls))
        self.assertEqual((again['method'], again['concept'], len(calls)), ('saved_system_one', 'heart_failure', 1))
        self.assertEqual(T.grammar_terms('Who has congestive cardiac failure?', self.store)['condition'],
                         {'congestive cardiac failure': 'heart_failure'})

    def test_abstains_below_threshold_or_none(self):
        for label, p in (('condition__heart_failure', 0.6), ('none', 0.97)):
            r = T.map_label('congestive cardiac failure', store=self.store, decider=decider(label, p, []))
            self.assertEqual((r['method'], r['concept']), ('abstained', None))
        self.assertFalse(self.store.exists() and T.saved(self.store))

    def test_no_candidates_no_call(self):
        calls = []
        r = T.map_label('zzqx', store=self.store, decider=decider('none', 0.9, calls))
        self.assertEqual((r['method'], calls), ('no_candidates', []))


class Tier(unittest.TestCase):
    def test_maps_unconsumed_term_and_recompiles_original(self):
        with tempfile.TemporaryDirectory() as d:
            catalog = SimpleNamespace(target=str(Path(d) / 'semantic.sqlite3'))
            q = 'How many patients have congestive cardiac failure?'

            def interpret(cat, question, population=None):
                terms = T.grammar_terms(question, T.store_path(Path(cat.target).parent))
                if 'congestive cardiac failure' not in terms['condition']:
                    raise QuestionNotUnderstood('needs clarification: Unconsumed request: have congestive cardiac failure. No language model was called')
                return {'kind': 'request', 'question': question}, {'interpreter': 'compositional_grammar', 'attempts': []}
            stack = SimpleNamespace(allow_live=True, retriever=SimpleNamespace(meta={'snapshot': {'as_of': '2030-05-27'}}))
            contract, interp = T.tier(stack, catalog, q, decider=decider('condition__heart_failure', 0.95, []), interpret=interpret)
            self.assertEqual(contract['question'], q)
            self.assertEqual(interp['interpreter'], 'terminology_mapping')
            self.assertEqual(interp['terminology'][0]['concept'], 'heart_failure')
            self.assertEqual(interp['terminology_usage']['new_paid_api_calls'], 1)
            with self.assertRaises(QuestionNotUnderstood):
                T.tier(stack, catalog, 'How many patients have zzqx blorp?', decider=decider('none', 0.9, []),
                       interpret=lambda c, q, population=None: (_ for _ in ()).throw(QuestionNotUnderstood('Unconsumed request: have zzqx blorp')))

    def test_cascade_registers_the_tier(self):
        from clinical_core.agere.clinical import proposers
        self.assertIn('terminology_mapping', proposers.TIERS)
        self.assertNotIn('terminology_mapping', proposers.PAID)  # saved/deterministic mappings work offline
        self.assertTrue(hasattr(proposers.Cascade, 'tier_terminology_mapping'))


class Engine(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        db = sqlite3.connect(Path(self.dir.name) / 'semantic.sqlite3')
        db.execute('CREATE TABLE measurement(patient_id, name, loinc, concept)')
        db.executemany('INSERT INTO measurement VALUES (?,?,?,?)', [('P1', 'HbA1c', '4548-4', 'hba1c'), ('P2', 'glycated hgb', None, None),
                                                                   ('P3', 'Hematocrit', '4544-3', None), ('P3', 'HbA1c', '4548-4', 'hba1c')])
        db.commit(); db.close()

    def tearDown(self): self.dir.cleanup()

    def test_code_and_status_questions(self):
        g = TE.local_question('Which lab names in our data map to LOINC 4548-4?')
        rep = TE.execute(self.dir.name, None, g['nodes'][0]['parameters'], '2030-05-27')
        self.assertEqual({r['label']: r['status'] for r in rep['rows']}, {'HbA1c': 'mapped', 'glycated hgb': 'recoverable'})
        g = TE.local_question('Which source lab labels are unmapped?')
        rep = TE.execute(self.dir.name, None, g['nodes'][0]['parameters'], '2030-05-27')
        self.assertEqual({r['label']: r['status'] for r in rep['rows']}, {'glycated hgb': 'recoverable', 'Hematocrit': 'unmapped'})
        a = TE.answer(rep, g, 'q'); self.assertEqual(a['count'], 2)
        self.assertIsNone(TE.local_question('How many patients have diabetes?'))

    def test_registered_on_the_menu(self):
        from clinical_core.agere.clinical import analysis_graph, capability_registry, graph_planner
        self.assertIn('terminology_map', analysis_graph.OPERATIONS)
        self.assertIn('terminology_map', capability_registry.PRODUCT)
        self.assertIn('terminology_map', graph_planner.CONTRACTS)


@unittest.skipUnless((ACTIVE / 'semantic.sqlite3').exists(), 'active warehouse not present')
class GrammarOnActiveWarehouse(unittest.TestCase):
    def test_grammar_compiles_messy_labels_to_queried_codes(self):
        from clinical_core.agere.clinical import request_plan, shared_planner
        cat = request_plan.Catalog.load(ACTIVE)
        for q, needle in (('How many patients have CHF?', "concept IN ('heart_failure')"), ('How many patients are on Entresto?', "drug_class IN ('arni')"),
                          ('How many patients had a glycated hemoglobin test?', "\"concept\" = 'hba1c'")):
            contract, interp = shared_planner._interpret(cat, q)
            self.assertIn(needle, contract['query'], q)
            self.assertTrue(interp['terminology'], q)


if __name__ == '__main__':
    unittest.main()
