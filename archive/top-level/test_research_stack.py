"""Behavioral checks for the opt-in training additions; not clinical validation."""
import json
import unittest
from pathlib import Path
from clinical_core.bridge import InsightBridge
from clinical_core.evidence_selection import EvidenceSelector,LocalTokenCounter,prepare_hosted_task
from clinical_core.signal_math import gaussian_change_points
from clinical_core.token_economics import estimate_cost,cache_break_even

ROOT=Path(__file__).resolve().parent

class ResearchStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selector=EvidenceSelector(ROOT/'artifacts/learned-evidence',LocalTokenCounter(ROOT/'models/qwen3-4b/tokenizer.json'))
        cls.reader=InsightBridge(Path('/Users/mikel/insight-engine')).reader

    def notes(self):
        return [dict(note_id='n1',note_type='progress',date='2026-09-01',text='Discussed spironolactone. It was held because of potassium. Her daughter has diabetes.'),
                dict(note_id='n2',note_type='progress',date='2026-10-01',text='Future note: medication restarted.')]

    def test_provenance_is_exact_and_future_evidence_excluded(self):
        notes=self.notes();p=self.selector.packet(notes,self.reader,as_of='2026-09-30',subject='mra')
        self.assertEqual(p['excluded_future_notes'],1)
        self.assertLessEqual(p['packet_tokens'],p['token_budget'])
        self.assertFalse(p['evidence_exhaustive'])
        refs={r['id']:r for r in p['provenance']}
        for e in p['evidence']:
            r=refs[e['id']];self.assertEqual(e['text'],notes[0]['text'][r['start']:r['end']])
        self.assertTrue(any('Discussed spironolactone. It was held' in e['text'] for e in p['evidence']))
        self.assertFalse(any('Future' in e['text'] for e in p['evidence']))

    def test_protected_overflow_cannot_be_sent_as_task(self):
        p=self.selector.packet(self.notes(),self.reader,as_of='2026-09-30',subject='mra',token_budget=5)
        self.assertEqual(p['status'],'review_required');self.assertEqual(p['evidence'],[])
        with self.assertRaises(ValueError):prepare_hosted_task(p,{'unknown':'Unknown'})

    def test_provider_adapter_preparation_is_typed_and_local(self):
        p=self.selector.packet(self.notes(),self.reader,as_of='2026-09-30',subject='mra')
        task=prepare_hosted_task(p,{'held':'Documented hold','unknown':'Unknown'})
        self.assertTrue(task.state())
        self.assertEqual(task.evidence,p['evidence'])

    def test_ambiguous_sources_rejected(self):
        notes=self.notes()
        with self.assertRaises(ValueError):self.selector.packet([notes[0],notes[0]],self.reader,as_of='2026-09-30',subject='mra')
        with self.assertRaises(ValueError):self.selector.packet(notes,self.reader,as_of='bad-date',subject='mra')

    def test_change_detection_finds_a_clear_step_without_diagnosing(self):
        p=gaussian_change_points([0,1,4,8,9,15],[1,1,1,3,3,3],[.1]*6)
        self.assertEqual(p['change_indices'],[3]);self.assertIsNone(p['diagnosis'])

    def test_large_uncertainty_prevents_same_step_from_being_forced(self):
        p=gaussian_change_points([0,1,4,8,9,15],[1,1,1,3,3,3],[10]*6)
        self.assertEqual(p['change_indices'],[])

    def test_invalid_or_unbounded_series_rejected(self):
        for t,x,e in [([0,0],[1,2],[1,1]),([0],[1],[0]),([0],[float('nan')],[1])]:
            with self.assertRaises(ValueError):gaussian_change_points(t,x,e)
        with self.assertRaises(ValueError):gaussian_change_points([0,1],[1,2],[1,1],max_points=1)

    def test_cost_categories_are_disjoint_and_include_non_token_costs(self):
        p=estimate_cost(uncached_input_tokens=1000000,cached_input_tokens=1000000,output_tokens=1000000,
            input_per_million=4,cached_per_million=.2,output_per_million=20,local_compute_usd=1,review_usd=2)
        self.assertAlmostEqual(p['estimated_api_usd'],24.2);self.assertAlmostEqual(p['estimated_total_usd'],27.2)
        with self.assertRaises(ValueError):estimate_cost(uncached_input_tokens=-1,cached_input_tokens=0,output_tokens=0,
            input_per_million=1,cached_per_million=0,output_per_million=1)

    def test_cache_write_and_ttl_economics(self):
        self.assertEqual(cache_break_even(normal_rate=4,write_rate=5,read_rate=.2),2)
        self.assertEqual(cache_break_even(normal_rate=4,write_rate=8,read_rate=.2),3)
        self.assertIsNone(cache_break_even(normal_rate=4,write_rate=8,read_rate=4))

    def test_research_training_targets_do_not_invent_patient_answers(self):
        rows=[json.loads(x) for x in (ROOT/'artifacts/research-training/planner_training.jsonl').read_text().splitlines()]
        self.assertEqual(len({r['id'] for r in rows}),len(rows))
        for row in rows:
            target=json.loads(row['messages'][-1]['content'])
            self.assertIsNone(target['clinical_answer']);self.assertFalse(target['automatic_execution'])
            self.assertTrue(row['source_refs']);self.assertTrue(target['required_data'])

if __name__=='__main__':unittest.main()
