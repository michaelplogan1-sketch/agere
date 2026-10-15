"""Second pinned encoder (UltraBERT-Embed-150M): hash-pinned offline loading, catalog dense re-ranking in answer_guard, the
lab/condition terminology fallback (never overriding exact code or alias matches) and the drug brand -> generic table.

Fixture profiles and stub encoders for the ranking/terminology logic; the tests that need the real weights load them offline
(sockets blocked) and are skipped when the model directory is absent. No network, no paid call."""
import json, os, shutil, socket, tempfile, unittest
from pathlib import Path
from unittest import mock

import numpy as np

from clinical_core import local_encoders as L
from clinical_core.agere.clinical import answer_guard as G, concepts, drug_classes, grounding

MODEL = L.second_encoder_path()
REAL = bool(MODEL) and os.environ.get('AGERE_SECOND_ENCODER', 'on') != 'off'  # integrated paths with the real weights
MANIFEST = json.loads(L.SECOND_ENCODER_MANIFEST.read_text())
V28 = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v28-sample-0.30/stack/source-profile.json')


class Stub:
    """A deterministic dense encoder: each text maps to a fixed direction chosen by keyword."""
    identity = 'stub-encoder-identity-0000'

    def __init__(self, directions, query_dirs=None, fail=False):
        self.directions, self.query_dirs, self.fail, self.calls = directions, query_dirs or directions, fail, 0

    def _vec(self, text, table):
        v = np.zeros(8, dtype=np.float32)
        for key, i in table.items():
            if key in text.lower(): v[i] += 1
        v[7] += 0.01
        return v / np.linalg.norm(v)

    def encode_documents(self, texts):
        if self.fail: raise RuntimeError('encoder down')
        return np.stack([self._vec(t, self.directions) for t in texts])

    def encode_queries(self, texts):
        if self.fail: raise RuntimeError('encoder down')
        self.calls += 1
        return np.stack([self._vec(t, self.query_dirs) for t in texts])


def fake_model_dir(root, pooling='cls', query='Q: '):
    root = Path(root)
    (root / '1_Pooling').mkdir(parents=True)
    (root / 'model.safetensors').write_bytes(b'not really weights')
    (root / '1_Pooling' / 'config.json').write_text(json.dumps({'pooling_mode': pooling}))
    (root / 'config_sentence_transformers.json').write_text(json.dumps({'prompts': {'query': query, 'document': ''}}))
    files = {}
    for name in ('model.safetensors', '1_Pooling/config.json', 'config_sentence_transformers.json'):
        files[name] = {'sha256': L._sha256(root / name), 'bytes': (root / name).stat().st_size}
    manifest = {'files': files, 'pooling': 'cls', 'normalize': True, 'query_prompt': 'Q: ', 'document_prompt': '',
                'remote_code': {'files': []}}
    (root / 'manifest.json').write_text(json.dumps(manifest))
    return root / 'manifest.json'


class ManifestRefusal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'model'
        self.cache = Path(self.tmp.name) / 'v.sqlite3'

    def tearDown(self):
        self.tmp.cleanup()

    def _refuses(self, manifest, pattern):
        with mock.patch('transformers.AutoModel.from_pretrained', side_effect=AssertionError('loaded before verification')) as load:
            with self.assertRaisesRegex(ValueError, pattern):
                L.PinnedSentenceEncoder(self.root, manifest, cache=self.cache)
            load.assert_not_called()

    def test_changed_file_is_refused_before_any_model_code_loads(self):
        m = fake_model_dir(self.root)
        (self.root / 'model.safetensors').write_bytes(b'tampered weights!!')
        self._refuses(m, 'changed: model.safetensors')

    def test_same_size_different_bytes_is_refused(self):
        m = fake_model_dir(self.root)
        data = (self.root / 'model.safetensors').read_bytes()
        (self.root / 'model.safetensors').write_bytes(data[::-1])
        self._refuses(m, 'changed: model.safetensors')

    def test_missing_pinned_file_is_refused(self):
        m = fake_model_dir(self.root)
        (self.root / 'config_sentence_transformers.json').unlink()
        self._refuses(m, 'missing: config_sentence_transformers.json')

    def test_pooling_or_prompt_disagreeing_with_manifest_is_refused(self):
        m = fake_model_dir(self.root, pooling='mean')
        self._refuses(m, 'Pooling config')
        shutil.rmtree(self.root)
        m = fake_model_dir(self.root, query='Different prompt: ')
        self._refuses(m, 'Prompt config')

    def test_shipped_manifest_pins_weights_remote_code_tokenizer_and_st_configs(self):
        need = {'model.safetensors', 'modeling_ultrabert.py', 'configuration_ultrabert.py', 'config.json', 'tokenizer.json',
                'tokenizer_config.json', 'config_sentence_transformers.json', 'modules.json', 'sentence_bert_config.json',
                '1_Pooling/config.json'}
        self.assertEqual(set(MANIFEST['files']), need)
        self.assertTrue(all(len(v['sha256']) == 64 for v in MANIFEST['files'].values()))
        self.assertEqual(set(MANIFEST['remote_code']['files']), {'modeling_ultrabert.py', 'configuration_ultrabert.py'})
        self.assertEqual(MANIFEST['pooling'], 'cls')

    def test_getter_falls_back_to_none_with_reason_on_refusal(self):
        fake_model_dir(self.root)
        L.reset_second_encoder()
        try:
            with mock.patch.dict(os.environ, {'AGERE_SECOND_ENCODER_PATH': str(self.root), 'AGERE_SECOND_ENCODER_CACHE': str(self.cache),
                                              'AGERE_SECOND_ENCODER': 'on'}):
                self.assertIsNone(L.second_encoder())  # the shipped manifest's hashes do not match the fake files
                self.assertTrue(L.second_encoder_status()['status'].startswith('refused: ValueError'))
            with mock.patch.dict(os.environ, {'AGERE_SECOND_ENCODER': 'off'}):
                self.assertIsNone(L.second_encoder())
        finally:
            L.reset_second_encoder()

    @unittest.skipUnless(MODEL, 'UltraBERT-Embed-150M not installed')
    def test_real_model_with_one_wrong_hash_is_refused(self):
        bad = json.loads(json.dumps(MANIFEST))
        bad['files']['modeling_ultrabert.py']['sha256'] = '0' * 64
        path = Path(self.tmp.name) / 'bad.json'
        path.write_text(json.dumps(bad))
        with mock.patch('transformers.AutoModel.from_pretrained', side_effect=AssertionError('remote code loaded')) as load:
            with self.assertRaisesRegex(ValueError, 'changed: modeling_ultrabert.py'):
                L.PinnedSentenceEncoder(MODEL, path, cache=self.cache)
            load.assert_not_called()


@unittest.skipUnless(MODEL, 'UltraBERT-Embed-150M not installed')
class OfflineLoad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        def no_network(*a, **k): raise OSError('network blocked in test')
        with mock.patch.object(socket.socket, 'connect', no_network), mock.patch('socket.create_connection', no_network):
            cls.enc = L.PinnedSentenceEncoder(MODEL, cache=Path(cls.tmp.name) / 'v.sqlite3')
            cls.q = cls.enc.encode_queries(['HbA1c'])
            cls.d = cls.enc.encode_documents(['HbA1c'])

    @classmethod
    def tearDownClass(cls):
        cls.enc.close(); cls.tmp.cleanup()

    def test_loads_offline_and_verifies_imported_code(self):
        self.assertEqual(os.environ.get('HF_HUB_OFFLINE'), '1')
        self.assertEqual(os.environ.get('TRANSFORMERS_OFFLINE'), '1')
        self.assertEqual(self.enc.pooling, 'cls')
        self.assertEqual(self.enc.query_prompt, MANIFEST['query_prompt'])
        self.assertEqual(self.q.shape, (1, 768))
        self.assertAlmostEqual(float(np.linalg.norm(self.q)), 1.0, places=5)

    def test_query_prompt_only_on_question_side_and_in_identity(self):
        self.assertLess(float(self.q[0] @ self.d[0]), 0.9999)  # the prompt changes the question vector
        self.assertEqual(self.enc.encode([MANIFEST['document_prompt'] + 'HbA1c'])[0].tolist(), self.d[0].tolist())
        ident = L.digest({'files': {k: v['sha256'] for k, v in MANIFEST['files'].items()}, 'repo': MANIFEST['repo'],
                          'revision': MANIFEST['revision'], 'pooling': 'cls', 'normalize': True, 'query_prompt': MANIFEST['query_prompt'],
                          'document_prompt': '', 'attn_implementation': 'sdpa', 'version': 1})
        self.assertEqual(self.enc.identity, ident)

    def test_cache_hit_returns_identical_vector(self):
        hits = self.enc.stats['cache_hits']
        self.assertEqual(self.enc.encode_queries(['HbA1c']).tolist(), self.q.tolist())
        self.assertEqual(self.enc.stats['cache_hits'], hits + 1)


def catalog_profile():
    f = lambda **vals: {'populated': 10, 'values': vals, 'distinct': len(vals)}
    return {'source_digest': 'fixture-catalog-1', 'streams': {
        'labs': {'records': 140, 'fields': {'test': f(**{'PHQ panel': 100, 'LDL cholesterol': 40}), 'value': {'populated': 140}}},
        'mood_questionnaires': {'records': 20, 'fields': {'instrument': f(**{'depression questionnaire': 20}), 'total': {'populated': 20}}},
        'encounters': {'records': 80, 'fields': {'type': f(office=80), 'status': f(completed=80), 'visit_score': {'populated': 80}}},
        'wellness_visit_services': {'records': 3, 'fields': {'hcpcs_code': f(G0438=3), 'visit_type': f(medicare_annual_wellness=3)}},
    }}


DIRS = {'questionnaire': 0, 'labs': 1, 'ldl': 1, 'encounter': 2, 'wellness': 3}
PHQ_Q = 'PHQ questionnaire score above 10'


class CatalogDense(unittest.TestCase):
    def test_dense_reranks_lexical_candidates_without_touching_likely(self):
        p = catalog_profile()
        lex = G.probe(PHQ_Q, p, k=3, dense=None)
        fused = G.probe(PHQ_Q, p, k=3, dense=Stub(DIRS))
        self.assertEqual([c['stream'] for c in lex['candidates']][:2], ['labs', 'mood_questionnaires'])  # the 100-row 'PHQ panel'
        self.assertEqual([c['stream'] for c in fused['candidates']][:2], ['mood_questionnaires', 'labs'])
        self.assertEqual(fused['dense']['fusion'], 'rrf60')
        by = lambda r: {c['stream']: c['likely'] for c in r['candidates']}
        self.assertEqual({s: v for s, v in by(fused).items() if s in by(lex)}, by(lex))  # 'likely' stays lexical
        mood = next(c for c in fused['candidates'] if c['stream'] == 'mood_questionnaires')
        self.assertEqual(mood['dense_rank'], 1)
        self.assertAlmostEqual(mood['fused'], 1 / 62 + 1 / 61, places=6)
        extra = [c for c in fused['candidates'] if c.get('dense_only')]
        self.assertEqual([c['stream'] for c in extra], ['wellness_visit_services'])  # no lexical match: joined via the dense pool
        self.assertFalse(extra[0]['likely'])

    def test_lexically_likely_source_with_code_stays_first(self):
        p = catalog_profile()
        r = G.probe('How many G0438 visits in 2029?', p, k=3, dense=Stub(DIRS, query_dirs={'visits': 2}))
        self.assertEqual(r['candidates'][0]['stream'], 'wellness_visit_services')
        self.assertTrue(r['candidates'][0]['likely'])

    def test_encoder_failure_or_absence_keeps_lexical_ranking(self):
        p = catalog_profile()
        q = PHQ_Q
        lex = [c['stream'] for c in G.probe(q, p, k=3, dense=None)['candidates']]
        broken = G.probe(q, p, k=3, dense=Stub(DIRS, fail=True))
        self.assertEqual([c['stream'] for c in broken['candidates']], lex)
        self.assertNotIn('dense', broken)
        with mock.patch.dict(os.environ, {'AGERE_CATALOG_DENSE': 'off'}):
            self.assertIsNone(G.dense_encoder())
            self.assertEqual([c['stream'] for c in G.probe(q, p, k=3)['candidates']], lex)

    def test_catalog_embedded_once_per_source_digest(self):
        p = catalog_profile()
        stub = Stub(DIRS)
        with mock.patch.object(stub, 'encode_documents', wraps=stub.encode_documents) as docs:
            for q in (PHQ_Q, 'wellness visits', 'LDL results'):
                G.probe(q, p, k=3, dense=stub)
            self.assertEqual(docs.call_count, 1)
        self.assertEqual(stub.calls, 3)

    def test_raw_catalog_slice_shows_dense_top_stream_to_the_writer(self):
        p = catalog_profile()
        text, rec = G.raw_catalog_slice(PHQ_Q, p, dense=Stub(DIRS))
        self.assertIn('mood_questionnaires', rec['streams'])
        self.assertIn('dense', rec)
        _, rec0 = G.raw_catalog_slice(PHQ_Q, p, dense=None)
        self.assertNotIn('mood_questionnaires', rec0['streams'])

    @unittest.skipUnless(REAL and V28.is_file(), 'UltraBERT-Embed-150M (or the v28 received catalog) unavailable or switched off')
    def test_real_encoder_on_received_catalog(self):
        profile = json.loads(V28.read_text())  # read-only: 237 received streams
        q = 'patients with a PHQ-9 score above 10'
        self.assertNotEqual(G.probe(q, profile, k=4, dense=None)['candidates'][0]['stream'], 'mood_questionnaires')
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {'AGERE_SECOND_ENCODER_CACHE': str(Path(d) / 'v.sqlite3')}):
            L.reset_second_encoder()
            try:
                r = G.probe(q, profile, k=4)
                self.assertIn('dense', r)
                self.assertEqual(r['candidates'][0]['stream'], 'mood_questionnaires')
                w = G.probe('How many Medicare annual wellness visits in 2029?', profile, k=4)
                self.assertEqual(w['candidates'][0]['stream'], 'wellness_visit_services')
            finally:
                L.reset_second_encoder()


class TermStub:
    """Always proposes `concept` at `score` (to prove the encoder never wins over code or alias matches)."""
    identity = 'term-stub'

    def __init__(self, concept, score):
        self.concept, self.score = concept, score

    def encode_documents(self, texts):
        return np.eye(len(texts), dtype=np.float32)

    def encode_queries(self, texts):
        names = list(concepts.MEASUREMENTS if self.concept in concepts.MEASUREMENTS else concepts.CONDITIONS)
        v = np.zeros((len(texts), len(names)), dtype=np.float32)
        v[:, names.index(self.concept)] = self.score
        return v


class TerminologyFallback(unittest.TestCase):
    def setUp(self):
        concepts._TERM_VECTORS.clear()

    def test_exact_code_wins_over_any_encoder_proposal(self):
        enc = TermStub('copd', 0.99)
        m = concepts.match_label('Something else entirely', 'condition', code='E11.9', encoder=enc)
        self.assertEqual((m['concept'], m['method']), ('type_2_diabetes', 'code'))
        m = concepts.match_label('Weird local name', 'lab', code='4548-4', encoder=TermStub('ldl', 0.99))
        self.assertEqual((m['concept'], m['method']), ('hba1c', 'code'))

    def test_alias_wins_over_encoder(self):
        m = concepts.match_label('HbA1c', 'lab', encoder=TermStub('ldl', 0.99))
        self.assertEqual((m['concept'], m['method']), ('hba1c', 'alias'))
        m = concepts.match_label('Disorder of kidney due to diabetes mellitus', 'condition', encoder=TermStub('copd', 0.99))
        self.assertEqual((m['concept'], m['method']), ('chronic_kidney_disease', 'alias'))
        self.assertIn('diabetes', m['also'])

    def test_threshold_and_candidate_reporting(self):
        hi = concepts.match_label('EGFR CKD-EPI 2021', 'lab', encoder=TermStub('egfr', 0.9))
        self.assertEqual((hi['concept'], hi['method'], hi['accepted']), ('egfr', 'encoder', True))
        lo = concepts.match_label('EGFR CKD-EPI 2021', 'lab', encoder=TermStub('egfr', 0.3))
        self.assertIsNone(lo['concept'])
        self.assertEqual((lo['candidate'], lo['score'], lo['accepted'], lo['threshold']), ('egfr', 0.3, False, concepts.TERMINOLOGY_THRESHOLDS['lab']))
        self.assertIsNone(concepts.match_label('EGFR CKD-EPI 2021', 'lab', encoder=None))

    def test_grounding_repairs_unobserved_label_through_fallback_and_says_so(self):
        p = {'streams': {'labs': {'records': 5, 'fields': {'test': {'populated': 5, 'values': {'eGFR': 5}}, 'value': {'populated': 5},
                                                             'unit': {'populated': 5, 'values': {'mL/min/1.73m2': 5}}}}},
             'patients': {'records': 1, 'fields': {}},
             'measurements': {'labs': [{'name': 'eGFR', 'unit': 'mL/min/1.73m2', 'records': 5, 'numeric': 5, 'range': [20, 90]}]}}
        atom = {'kind': 'record', 'stream': 'labs', 'window': {'basis': 'all_observed', 'start': '', 'end': ''},
                'conditions': [{'field': 'test', 'op': 'eq', 'value': 'EGFR CKD-EPI 2021'}]}
        with mock.patch.object(concepts, 'terminology_encoder', return_value=TermStub('egfr', 0.8)):
            g, report = grounding.prepare({'all': [atom], 'any': [], 'exclude': []}, p)
        self.assertEqual(g['all'][0]['concept'], 'egfr')
        self.assertNotIn('_terminology', g['all'][0])
        self.assertEqual(report[0]['terminology']['method'], 'encoder')
        self.assertIn('terminology encoder', report[0]['repair'])
        with mock.patch.object(concepts, 'terminology_encoder', return_value=TermStub('egfr', 0.2)):
            with self.assertRaises(grounding.GroundingError):  # below threshold: not bound, still an error
                grounding.prepare({'all': [atom], 'any': [], 'exclude': []}, p)

    def test_grounding_code_predicates_never_use_the_encoder(self):
        with mock.patch.object(concepts, 'terminology_encoder', side_effect=AssertionError('encoder consulted')):
            atom = {'kind': 'record', 'stream': 'conditions', 'window': {'basis': 'all_observed', 'start': '', 'end': ''},
                    'conditions': [{'field': 'icd10', 'op': 'prefix', 'value': 'E11'}]}
            p = {'streams': {'conditions': {'records': 5, 'fields': {'icd10': {'populated': 5, 'values': {'E11.9': 5}}}}},
                 'patients': {'records': 1, 'fields': {}}}
            g, _ = grounding.prepare({'all': [atom], 'any': [], 'exclude': []}, p)
            self.assertEqual(g['all'][0]['kind'], 'record')

    @unittest.skipUnless(REAL, 'UltraBERT-Embed-150M unavailable or switched off')
    def test_real_encoder_maps_unaliased_lab_labels(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {'AGERE_SECOND_ENCODER_CACHE': str(Path(d) / 'v.sqlite3')}):
            L.reset_second_encoder()
            try:
                m = concepts.match_label('Cholesterol in LDL', 'lab')
                self.assertEqual((m['concept'], m['method']), ('ldl', 'encoder'))
                low = concepts.match_label('Total testosterone', 'lab')  # mapped-to-none in the dictionary
                self.assertIsNone(low['concept'])
            finally:
                L.reset_second_encoder()


class DrugBrands(unittest.TestCase):
    def test_table_is_valid_and_covers_every_registered_class(self):
        table = drug_classes.brands()
        self.assertGreater(len(table), 250)
        covered = {c for v in table.values() for c in v['classes']}
        self.assertEqual(set(drug_classes.CLASSES) - covered, set())
        doc = json.loads(drug_classes.BRANDS_PATH.read_text())
        self.assertIn('RxNorm', doc['naming_standard'])

    def test_brand_only_names_classify(self):
        cases = {'Jardiance': ['sglt2_inhibitor'], 'FARXIGA 10 MG': ['sglt2_inhibitor'], 'Ozempic': ['glp1_agonist'],
                 'Mounjaro': ['glp1_agonist'], 'Eliquis': ['anticoagulant'], 'Xarelto': ['anticoagulant'], 'Entresto': ['arni'],
                 'Januvia': ['dpp4_inhibitor'], 'Lipitor': ['statin'], 'Plavix': ['antiplatelet'], 'Brilinta': ['antiplatelet'],
                 'Lantus': ['insulin'], 'Zestril': ['ace_inhibitor'], 'Cozaar': ['arb'],
                 'Coreg': ['beta_blocker', 'hf_evidence_based_beta_blocker'], 'Toprol XL': ['beta_blocker', 'hf_evidence_based_beta_blocker'],
                 'Lopressor': ['beta_blocker'], 'Hyzaar': ['arb', 'thiazide_diuretic'], 'Janumet XR': ['metformin', 'dpp4_inhibitor']}
        for name, want in cases.items():
            self.assertEqual(drug_classes.classify(name), [k for k in drug_classes.CLASSES if k in want], name)
        self.assertEqual(drug_classes.generic_name('Entresto 24-26 MG'), ['sacubitril', 'valsartan'])
        self.assertEqual(drug_classes.generic_name('toprol-xl'), ['metoprolol succinate'])

    def test_ingredient_classification_is_never_changed_by_a_brand(self):
        self.assertEqual(drug_classes.classify('metformin 500 MG [Januvia]'), ['metformin'])  # brand does not add a class
        self.assertEqual(drug_classes.classify('sacubitril 24 MG / valsartan 26 MG Oral Tablet [Entresto]'), ['arni'])
        self.assertEqual(drug_classes.classify('nystatin 100000 UNT/ML'), [])
        self.assertEqual(drug_classes.classify('Somatropin'), [])  # 'Soma' only as a whole word

    def test_terminology_matching_uses_brand_table(self):
        m = concepts.match_label('[Jardiance]', 'medication')
        self.assertEqual((m['concept'], m['method'], m['classes']), ('empagliflozin', 'brand', ['sglt2_inhibitor']))
        m = concepts.match_label('rosuvastatin calcium 10 MG Oral Tablet', 'medication')
        self.assertEqual((m['concept'], m['method']), ('rosuvastatin', 'ingredient'))
        self.assertEqual(concepts.match_label('alendronic acid 10 MG Oral Tablet', 'medication')['concept'], 'alendronate')
        self.assertIsNone(concepts.match_label('Unknownium 5 MG', 'medication'))


if __name__ == '__main__':
    unittest.main()
