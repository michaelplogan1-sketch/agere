import json
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import protected_channels as PC
from clinical_core.agere.clinical import reranker_bakeoff as RB


def row(i, text, patient='P1'):
    return {'id': str(i), 'text': text, 'source': {'patient_id': patient, 'record_id': f'r{i}', 'field': 'note'}, 'protected': False}


class ProtectedChannelTests(unittest.TestCase):
    def test_kinds_and_subject_terms(self):
        self.assertEqual(PC.kinds('Metformin stopped for GI intolerance'), ['discontinuation', 'intolerance'])
        self.assertEqual(PC.kinds('Patient doing well'), [])
        self.assertIn('metformin', PC.subject_terms('Why did the patient stop metformin?'))

    def test_polarity(self):
        self.assertEqual(PC.polarity('declined statin therapy', 'statin'), 'negated')
        self.assertEqual(PC.polarity('continues statin daily', 'statin'), 'affirmed')
        self.assertIsNone(PC.polarity('no related text', 'statin'))

    def test_mark_cue_and_contradiction(self):
        rows = [row(1, 'Continues metformin 1000 mg twice daily'), row(2, 'Metformin discontinued after GI intolerance'),
                row(3, 'Unrelated visit', 'P2')]
        n = PC.mark('Is the patient on metformin?', rows)
        self.assertGreaterEqual(n, 2)
        self.assertEqual(rows[0]['protected_channel']['channel'], 'contradiction')
        self.assertEqual(rows[1]['protected_channel']['channel'], 'contradiction')
        self.assertNotIn('protected_channel', rows[2])

    def test_contradiction_channel_marks_best_affirmed_and_at_most_two_negated(self):
        rows = [row(i, f'Continues metformin report {i}') for i in range(1, 6)] + [row(i, f'Metformin stopped report {i}') for i in range(6, 10)]
        PC.mark('Is the patient on metformin?', rows)
        by = {r['id']: r['protected_channel']['polarity'] for r in rows if (r.get('protected_channel') or {}).get('channel') == 'contradiction'}
        self.assertEqual(sorted(by.values()), ['affirmed', 'negated', 'negated'])
        self.assertEqual(by['1'], 'affirmed')

    def test_select_reserves_slots_for_protected_rows(self):
        ranked = [row(i, f'metformin note {i}') for i in range(1, 9)]
        ranked[7]['protected_channel'] = {'channel': 'exception', 'kinds': ['intolerance'], 'terms': ['metformin']}
        seen_groups = set()

        def accept(r, seen, by_patient):
            g = r['source']['record_id']
            if g in seen: return False
            seen.add(g); return True
        chosen = PC.select(ranked, 4, 'P1', accept)
        self.assertEqual(len(chosen), 4)
        self.assertIn('r8', [r['source']['record_id'] for r in chosen])
        self.assertTrue(next(r for r in chosen if r['source']['record_id'] == 'r8')['protected'])
        # relevance order is kept
        self.assertEqual([r['id'] for r in chosen], sorted([r['id'] for r in chosen]))

    def test_channel_query(self):
        q = PC.channel_query('Why did the patient stop metformin?')
        self.assertIn('"metformin"', q); self.assertIn('"intolerance"', q)
        self.assertIsNone(PC.channel_query('how many patients'))
        self.assertEqual(PC.reserve(8), 2); self.assertEqual(PC.reserve(2), 1)


class BakeoffPolicyTests(unittest.TestCase):
    def test_query_type(self):
        self.assertEqual(RB.query_type('Why did she stop metformin?'), 'exception')
        self.assertEqual(RB.query_type('latest A1c result'), 'lab')
        self.assertEqual(RB.query_type('Is the patient on a statin?'), 'medication')
        self.assertEqual(RB.query_type('transportation barriers'), 'social')
        self.assertEqual(RB.query_type('hello'), 'general')

    def test_choice_reads_measured_file_and_defaults_to_incumbent(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'c.json'
            self.assertEqual(RB.choice('latest A1c', path=p)['reranker'], 'bge')
            p.write_text(json.dumps({'by_type': {'lab': {'reranker': 'qwen', 'graph_fallback': True, 'basis': 'measured'}}}))
            c = RB.choice('latest A1c', path=p)
            self.assertEqual((c['reranker'], c['graph_fallback']), ('qwen', True))

    def test_summarize_ties_keep_incumbent(self):
        m = lambda v: {'ndcg8': v, 'mrr': v, 'p8': v}
        rows = [{'type': 'lab', 'metrics': {'rrf': m(.5), 'bge': m(.8), 'qwen': m(.802)}, 'recall8': {'hybrid': .5, 'hybrid_plus_graph': .5}},
                {'type': 'medication', 'metrics': {'rrf': m(.5), 'bge': m(.6), 'qwen': m(.9)}, 'recall8': {'hybrid': .4, 'hybrid_plus_graph': .7}}]
        s = RB.summarize(rows, ['bge', 'qwen'])
        self.assertEqual(s['adopted'], {'lab': 'bge', 'medication': 'qwen'})
        self.assertEqual(s['graph_fallback_types'], ['medication'])


if __name__ == '__main__':
    unittest.main()
