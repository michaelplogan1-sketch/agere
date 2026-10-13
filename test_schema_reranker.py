"""Schema reranker: gold extraction from SQL, recall computation, learned ranking, prompt-size bound, fallbacks."""
import os, sys, tempfile, unittest, zlib
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tools'))
from clinical_core.agere.clinical import nl2sql, schema_rerank as R  # noqa: E402
import schema_rerank_eval as E  # noqa: E402


def _field(n, values=None):
    spec = {'populated': n, 'types': {'text': n}, 'distinct': len(values or {}) or n}
    if values is not None: spec['values'] = values
    return spec


PROFILE = {'source_digest': 'fixture', 'streams': {
    'labs': {'records': 50, 'fields': {'name': _field(50, {'Ferritin': 20, 'Hemoglobin A1c': 30}), 'value': _field(50),
                                       'status': _field(50, {'final': 45, 'corrected': 5})}},
    'pharmacy_dispenses': {'records': 40, 'fields': {'drug_name': _field(40), 'status': _field(40, {'completed': 30, 'returned_to_stock': 10})}},
    'appointments': {'records': 30, 'fields': {'status': _field(30, {'kept': 20, 'cancelled': 6, 'no_show': 4}), 'scheduled_date': _field(30)}},
    'encounters': {'records': 60, 'fields': {'type': _field(60, {'office': 40, 'telehealth': 20})}},
    'referrals': {'records': 20, 'fields': {'specialty': _field(20, {'cardiology': 12, 'nephrology': 8})}},
}}
VOCAB = {('measurement', 'component'): ['Ferritin', 'Hemoglobin A1c', 'Sodium'], ('appointment', 'purpose'): ['follow_up', 'annual_wellness']}


class FakeEncoder:
    """Deterministic bag-of-words hashing encoder (stands in for the local MiniLM)."""
    identity = 'fake-encoder'

    def encode(self, texts, batch_size=4):
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for n, t in enumerate(texts):
            for w in R.tokens(t): out[n, zlib.crc32(w.encode()) % 64] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


def catalog():
    return R.Catalog(PROFILE, nl2sql.TABLES, VOCAB)


class GoldExtraction(unittest.TestCase):
    def setUp(self): self.cat = catalog()

    def test_semantic_tables_aliases_values(self):
        sql = ("SELECT count(DISTINCT c.patient_id) FROM condition c JOIN measurement m ON m.patient_id = c.patient_id "
               "WHERE c.concept = 'diabetes' AND m.concept = 'hba1c' AND m.source_table = 'labs' AND m.component LIKE 'Ferr%'")
        g = R.gold_items(sql, self.cat)['items']
        for i in ('table:condition', 'table:measurement', 'column:condition.concept', 'column:measurement.source_table',
                  'concept:diabetes', 'concept:hba1c', 'value:measurement.source_table=labs', 'value:measurement.component=Ferritin'):
            self.assertIn(i, g)
        self.assertNotIn('table:patient', g)

    def test_streams_payload_fields_and_in_lists(self):
        sql = ("SELECT count(*) FROM record r WHERE r.stream IN ('appointments') AND json_extract(r.payload,'$.status') IN ('cancelled','no_show') "
               "AND r.patient_id IN (SELECT patient_id FROM medication WHERE drug_class = 'statin')")
        g = R.gold_items(sql, self.cat)['items']
        for i in ('stream:appointments', 'field:appointments.status', 'value:appointments.status=cancelled',
                  'value:appointments.status=no_show', 'drug_class:statin', 'table:record', 'table:medication'):
            self.assertIn(i, g)
        self.assertFalse([i for i in g if i.startswith('value:') and 'patient_id' in i])  # a subquery is not a literal list

    def test_comments_and_ctes_are_ignored(self):
        sql = "-- DEFINITION: from patient where stuff\nWITH stay AS (SELECT 1) SELECT * FROM stay JOIN visit v ON 1=1 WHERE v.type_concept = 'urgent_care_visit'"
        g = R.gold_items(sql, self.cat)['items']
        self.assertNotIn('table:stay', g); self.assertNotIn('table:patient', g)
        self.assertIn('concept:urgent_care_visit', g)


class Recall(unittest.TestCase):
    def test_recall_and_coverage(self):
        rows = [{'gold': {'stream:labs', 'value:labs.status=final'}, 'shown': {'stream:labs'}, 'full': {'stream:labs'}, 'chars': 10,
                 'ranked': {'stream': ['stream:referrals', 'stream:labs'], 'value': ['value:labs.status=final']}},
                {'gold': {'stream:referrals'}, 'shown': {'stream:referrals'}, 'full': set(), 'chars': 20,
                 'ranked': {'stream': ['stream:referrals']}}]
        s = E.summarize(rows, ks=(1, 2))
        self.assertEqual(s['stream']['recall@1'], 0.5)
        self.assertEqual(s['stream']['recall@2'], 1.0)
        self.assertEqual(s['stream']['mrr'], 0.75)
        self.assertEqual(s['stream']['slice_coverage'], 1.0)
        self.assertEqual(s['stream']['slice_full_doc'], 0.5)
        self.assertEqual(s['value']['slice_coverage'], 0.0)
        self.assertEqual(s['all']['questions_fully_covered'], 0.5)
        self.assertEqual(s['all']['mean_slice_chars'], 15.0)


TRAIN = [('How many lab results came back for diabetics?', {'value:measurement.source_table=labs', 'table:measurement'}),
         ('Count lab results drawn in 2028', {'value:measurement.source_table=labs', 'table:measurement'}),
         ('Average lab results per patient', {'value:measurement.source_table=labs', 'table:measurement'}),
         ('How many vital signs were recorded?', {'value:measurement.source_table=vitals', 'table:measurement'}),
         ('Vital signs taken at home visits', {'value:measurement.source_table=vitals', 'table:measurement'}),
         ('Cancelled appointments by month', {'value:appointments.status=cancelled', 'stream:appointments'}),
         ('How many appointments were cancelled last year?', {'value:appointments.status=cancelled', 'stream:appointments'}),
         ('Dispenses returned to stock', {'value:pharmacy_dispenses.status=returned_to_stock', 'stream:pharmacy_dispenses'})] * 3


class Ranking(unittest.TestCase):
    def test_learned_ranking_beats_lexical_only(self):
        cat = catalog()
        rr = R.Reranker(cat, FakeEncoder(), priors=R.Priors(TRAIN))
        rr.weights = R.train(rr, TRAIN)
        q = 'What share of lab results arrived late?'
        gold = 'value:measurement.source_table=labs'
        learned = [i for i, _ in rr.rank(q, 'value')]
        X = rr.features(q)
        lex = X[:, 2] + 2 * X[:, 9] + X[:, 10]
        vidx = [k for k in np.argsort(-lex) if R.GROUPS[rr.group[k]] == 'value']
        lexical = [cat.items[k]['id'] for k in vidx]
        self.assertLess(learned.index(gold), lexical.index(gold))
        self.assertEqual(learned[0], gold)

    def test_model_round_trip(self):
        cat = catalog()
        rr = R.Reranker(cat, None, priors=R.Priors(TRAIN))
        rr.weights = R.train(rr, TRAIN)
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'm.json'
            R.save_model(rr, rr.weights, target)
            data = R.load_model(target)
        again = R.Reranker(cat, None, model=data['lexical_weights'], priors=R.Priors.from_json(data['priors']))
        q = 'cancelled appointments in 2029'
        self.assertEqual(rr.rank(q, 'value')[0][0], again.rank(q, 'value')[0][0])


class Slice(unittest.TestCase):
    def setUp(self):
        self.cat = catalog()
        self.rr = R.Reranker(self.cat, FakeEncoder(), priors=R.Priors(TRAIN), legacy=lambda q: nl2sql.retrieve_streams(q, PROFILE, k=20))
        self.rr.weights = R.train(self.rr, TRAIN)

    def test_prompt_never_exceeds_legacy_budget(self):
        for q in ['How many appointments were cancelled?', 'Ferritin lab results in 2028', 'returned to stock dispenses by drug',
                  'cardiology referrals', 'x']:
            budget = len(nl2sql.legacy_schema_for(q, PROFILE)[1])
            out = R.render(self.rr, q, PROFILE, budget=budget)
            self.assertLessEqual(len(out['text']), budget)
            self.assertIn('TABLES', out['text']); self.assertIn('concept ids', out['text'])
            self.assertTrue(set(out['streams']) <= set(PROFILE['streams']))

    def test_extra_streams_are_kept_and_values_surface(self):
        q = 'How many appointments were cancelled?'
        out = R.render(self.rr, q, PROFILE, budget=10 ** 6, extra=('referrals',))
        self.assertIn('referrals', out['streams'])
        self.assertIn('stream:appointments', out['shown'])
        self.assertIn('value:appointments.status=cancelled', out['shown'])

    def test_tight_budget_drops_optional_blocks(self):
        q = 'How many appointments were cancelled?'
        small = R.render(self.rr, q, PROFILE, budget=1)
        big = R.render(self.rr, q, PROFILE, budget=10 ** 6)
        self.assertLess(len(small['text']), len(big['text']))


class Fallback(unittest.TestCase):
    def test_reranker_without_encoder(self):
        rr = R.Reranker(catalog(), None, priors=R.Priors(TRAIN))
        rr.weights = R.train(rr, TRAIN)
        self.assertTrue(np.all(rr.features('lab results')[:, R.FEATURES.index('has_encoder')] == 0))
        self.assertEqual(rr.rank('cancelled appointments', 'value')[0][0], 'value:appointments.status=cancelled')

    def test_encoder_disabled_returns_none(self):
        saved = dict(R._ENCODER)
        try:
            R._ENCODER.update(registered=None, loaded=None, failed=False)
            with mock.patch.dict(os.environ, {'AGERE_SCHEMA_RERANK_ENCODER': 'off'}):
                self.assertIsNone(R.load_encoder())
        finally:
            R._ENCODER.clear(); R._ENCODER.update(saved)

    def test_schema_for_falls_back_to_legacy(self):
        q = 'cancelled appointments'
        legacy = nl2sql.legacy_schema_for(q, PROFILE)
        with mock.patch.dict(os.environ, {'AGERE_SCHEMA_RERANK': 'off'}):
            self.assertEqual(nl2sql.schema_for(q, PROFILE), legacy)
        with mock.patch.object(R, 'MODEL_FILE', Path('/nonexistent/model.json')), \
                mock.patch.object(R, 'load_model', side_effect=OSError('no model')), mock.patch.dict(R._RUNTIME, clear=True), \
                mock.patch.dict(os.environ, {'AGERE_SCHEMA_RERANK': 'on'}):
            self.assertEqual(nl2sql.schema_for(q, PROFILE), legacy)

    def test_example_ranking_keeps_current_count(self):
        scored = [(0.5, 'cancelled appointments by month', 'SELECT 1'), (0.3, 'appointments kept', 'SELECT 2'), (0.1, 'labs', 'SELECT 3')]
        saved = dict(R._ENCODER)
        try:
            R.use_encoder(FakeEncoder())
            picked = R.rank_examples('cancelled appointments', scored, k=4)
            self.assertEqual(len(picked), 2)
            R._ENCODER.update(registered=None, loaded=None, failed=True)
            self.assertIsNone(R.rank_examples('cancelled appointments', scored, k=4))
            self.assertEqual(R.rank_examples('x', [(0.1, 'a', 'b')], k=4), [])
        finally:
            R._ENCODER.clear(); R._ENCODER.update(saved)


if __name__ == '__main__':
    unittest.main()
