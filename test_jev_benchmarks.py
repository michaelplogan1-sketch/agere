"""Jev normalization second check (dictionary merge, incremental cache) and benchmark matching/research (mock HTTP)."""
import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.economics import usage_cost
from clinical_core.agere.clinical import benchmarks as bm
from tools.normalization import jev_check as jc
from tools.benchmarks import build_benchmarks as bb


class FakeHTTP:
    """urlopen stand-in: records request bodies and answers with a callable."""
    def __init__(self, reply):
        self.reply, self.bodies = reply, []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data)
        self.bodies.append(body)
        payload = json.dumps(self.reply(body)).encode()

        class R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return R(payload)


def jev_reply(prefer):
    """Answer every choice with the first available label from `prefer`."""
    def reply(body):
        answers = {}
        for key, q in body['questions'].items():
            choice = next(l for l in prefer if l in q['criteria'])
            ps = {l: (0.9 if l == choice else 0.1 / (len(q['criteria']) - 1)) for l in q['criteria']}
            answers[key] = {'choice': choice, 'probabilities': ps}
        return {'model': jc.MODEL, 'answers': answers, 'usage': {'input_tokens': 1000, 'output_tokens': 0}}
    return reply


LIB = {'benchmarks': {
    'ed_per_1000_medicare': {'id': 'ed_per_1000_medicare', 'family': 'ed_per_1000', 'payer': 'medicare', 'label': 'ED Medicare',
                             'value': 600.0, 'low': None, 'high': None, 'unit': 'visits per 1,000 member-years', 'population': 'Medicare FFS',
                             'year': 2023, 'status': 'cited', 'sources': [{'title': 'CMS GV PUF', 'url': 'https://data.cms.gov/x', 'publisher': 'data.cms.gov'}]},
    'ed_per_1000_medicaid': {'id': 'ed_per_1000_medicaid', 'family': 'ed_per_1000', 'payer': 'medicaid', 'label': 'ED Medicaid',
                             'value': 900.0, 'unit': 'visits per 1,000 member-years', 'year': 2022, 'status': 'cited',
                             'sources': [{'title': 'CDC', 'url': 'https://cdc.gov/y', 'publisher': 'cdc.gov'}]},
    'ed_per_1000_commercial': {'id': 'ed_per_1000_commercial', 'family': 'ed_per_1000', 'payer': 'commercial', 'label': 'ED commercial',
                               'value': 170.0, 'unit': 'visits per 1,000 member-years', 'year': 2022, 'status': 'uncited', 'sources': []},
    'readmission_30d_all_payer': {'id': 'readmission_30d_all_payer', 'family': 'readmission_30d', 'payer': None, 'label': 'Readmissions',
                                  'value': 13.9, 'low': 13.5, 'high': 14.5, 'unit': '%', 'year': 2020, 'status': 'cited',
                                  'sources': [{'title': 'HCUP', 'url': 'https://hcup-us.ahrq.gov/z', 'publisher': 'hcup-us.ahrq.gov'}]},
    'a1c_poor_control': {'id': 'a1c_poor_control', 'family': 'a1c_poor_control', 'payer': None, 'label': 'A1c >9', 'value': 30.0,
                         'unit': '%', 'year': 2023, 'status': 'cited', 'sources': [{'title': 'NCQA', 'url': 'https://ncqa.org', 'publisher': 'ncqa.org'}]},
    'no_show': {'id': 'no_show', 'family': 'no_show', 'payer': None, 'label': 'No-show', 'value': 50.0, 'unit': '%', 'year': 2019,
                'status': 'implausible', 'sources': [{'title': 'x', 'url': 'https://x', 'publisher': 'x'}]}}}


class BenchmarkMatching(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()); self.path = self.dir / 'benchmarks.json'
        self.path.write_text(json.dumps(LIB)); bm._CACHE.clear()

    def tearDown(self):
        shutil.rmtree(self.dir); bm._CACHE.clear()

    def ask(self, answer, question):
        return bm.benchmarks(answer, question, path=self.path)

    def test_payer_table_gets_payer_specific_cited_references(self):
        answer = {'status': 'answered', 'answer_type': 'table',
                  'table': [{'group': 'Medicare', 'ed_per_1000': 712.0}, {'group': 'Medicaid', 'ed_per_1000': 950.0},
                            {'group': 'Commercial', 'ed_per_1000': 210.0}]}
        before = copy.deepcopy(answer)
        out = self.ask(answer, 'ED visits per 1,000 by payer')
        self.assertEqual([x['id'] for x in out], ['ed_per_1000_medicare', 'ed_per_1000_medicaid'])  # uncited commercial withheld
        self.assertEqual(answer, before)  # the computed answer is never altered
        self.assertIn('national/peer reference: 600', out[0]['display'])
        self.assertIn('(data.cms.gov, 2023)', out[0]['display'])

    def test_named_payer_restricts_to_that_payer(self):
        answer = {'status': 'answered', 'answer_type': 'value', 'value': 712.0}
        out = self.ask(answer, 'What is the Medicare ED visit rate per 1,000 member-years?')
        self.assertEqual([x['id'] for x in out], ['ed_per_1000_medicare'])

    def test_range_display_and_family_from_columns(self):
        answer = {'status': 'answered', 'answer_type': 'table', 'table': [{'group': 'PCP A', 'readmission_rate': 0.12}]}
        out = self.ask(answer, 'Which practices have the most returns to hospital?')
        self.assertEqual(out[0]['id'], 'readmission_30d_all_payer')
        self.assertIn('13.9 (range 13.5–14.5)%', out[0]['display'])

    def test_no_match_unsupported_and_implausible(self):
        self.assertEqual(self.ask({'status': 'answered', 'answer_type': 'count', 'count': 3}, 'How many patients have diabetes?'), [])
        self.assertEqual(self.ask({'status': 'answered', 'answer_type': 'count', 'count': 3}, 'How many diabetics have A1c over 9 (poor control)?'), [])
        self.assertEqual(self.ask({'status': 'unsupported', 'answer_type': 'unsupported'}, 'ED visits per 1,000?'), [])
        self.assertEqual(self.ask({'status': 'answered', 'answer_type': 'value', 'value': 9.0}, 'What is our no-show rate?'), [])
        out = self.ask({'status': 'answered', 'answer_type': 'value', 'value': 22.0}, 'What is the A1c poor control rate (A1c over 9)?')
        self.assertEqual([x['id'] for x in out], ['a1c_poor_control'])

    def test_primary_text_wins_over_method_mentions(self):
        answer = {'status': 'answered', 'answer_type': 'value', 'value': 0.14,
                  'method': 'ED visits per 1,000 member-years were also reviewed', 'table': [{'readmission_rate': 0.14}]}
        self.assertEqual([x['family'] for x in self.ask(answer, 'What is our readmission rate?')], ['readmission_30d'])

    def test_missing_library_is_empty(self):
        self.assertEqual(bm.benchmarks({'status': 'answered'}, 'ED per 1,000', path=self.dir / 'absent.json'), [])


class BenchmarkResearch(unittest.TestCase):
    RAW = {'model': 'openai/x', 'output': [
        {'type': 'search_results', 'results': [{'id': 1, 'title': 'CMS GV PUF', 'url': 'https://www.data.cms.gov/gv', 'date': '2025-01-01'},
                                               {'id': 2, 'title': 'Blog', 'url': 'https://blog.example/x'}]},
        {'type': 'message', 'content': [{'type': 'output_text', 'text':
            '```json\n{"value": "1,133.8", "low": null, "high": null, "unit": "USD PMPM", "population": "Medicare FFS", '
            '"year": 2023, "summary": "CMS", "source_ids": [1, 7]}\n```'}]}],
        'usage': {'input_tokens': 100, 'output_tokens': 10, 'cost': {'total_cost': 0.0016}}}

    def test_sources_only_from_provider_search_results(self):
        e = bb.entry_from('pmpm_medicare', self.RAW)
        self.assertEqual(e['status'], 'cited')
        self.assertEqual(e['value'], 1133.8)
        self.assertEqual([s['url'] for s in e['sources']], ['https://www.data.cms.gov/gv'])  # id 7 was not a search result
        self.assertEqual(e['sources'][0]['publisher'], 'data.cms.gov')

    def test_implausible_scale_is_not_cited(self):
        raw = copy.deepcopy(self.RAW)
        raw['output'][1]['content'][0]['text'] = '{"value": 9000, "unit": "USD PMPM", "year": 2023, "source_ids": [1]}'
        self.assertEqual(bb.entry_from('pmpm_medicaid', raw)['status'], 'implausible')

    def test_run_writes_library_and_replays_from_ledger(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            fake = FakeHTTP(lambda body: self.RAW)
            ledger = BudgetLedger(tmp / 'ledger.sqlite3')
            with patch.dict(os.environ, {'PERPLEXITY_API_KEY': 'test'}):
                doc, cost = bb.run(ledger=ledger, receipts=tmp / 'r', transport=fake, out=tmp / 'b.json', only=['pmpm_medicare'])
                self.assertEqual(cost['requests'], 1); self.assertEqual(fake.bodies[0]['preset'], 'fast')
                self.assertNotIn('model', fake.bodies[0])
                self.assertAlmostEqual(cost['new_estimated_usd'], 0.0016)
                doc, cost = bb.run(ledger=ledger, receipts=tmp / 'r', transport=fake, out=tmp / 'b.json', only=['pmpm_medicare'])
            self.assertEqual(len(fake.bodies), 1); self.assertEqual(cost['ledger_cache_hits'], 1)
            self.assertEqual(json.loads((tmp / 'b.json').read_text())['benchmarks']['pmpm_medicare']['status'], 'cited')
            self.assertEqual(ledger.receipt()['calls'], {'validated': 1})
        finally:
            shutil.rmtree(tmp)

    def test_provider_reported_cost(self):
        self.assertEqual(usage_cost('preset:fast', self.RAW['usage'])['estimated_usd'], 0.0016)
        self.assertIsNone(usage_cost('preset:fast', {'input_tokens': 1, 'output_tokens': 1}))


class JevDictionaryMerge(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / 'labs.json').write_text(json.dumps({'domain': 'labs', 'entries': {
            'eGFR': {'agere_concept': 'egfr', 'component': 'GFR/1.73 sq M.predicted', 'loinc': '33914-3', 'specimen': 'serum',
                     'property': 'vol rate', 'source_count': 10},
            'blood_pressure_systolic': {'agere_concept': 'systolic_bp', 'component': 'Systolic BP', 'loinc': '8480-6', 'source_count': 5},
            'TSH': {'source_count': 3, 'variants_by_unit': {'mIU/L': {'agere_concept': 'tsh', 'component': 'Thyrotropin', 'loinc': '3016-3'}}}}}))
        (self.tmp / 'medications.json').write_text(json.dumps({'domain': 'medications', 'entries': {
            'Ibuprofen 400 MG Oral Tablet': {'ingredients': ['ibuprofen'], 'drug_classes': [], 'therapeutic_category': 'nsaid', 'source_count': 4},
            'Nystatin 100000 UNT/ML Oral Suspension': {'ingredients': ['nystatin'], 'drug_classes': [], 'source_count': 2}}}))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_candidates(self):
        options, m = jc.lab_candidates('eGFR', {'agere_concept': 'egfr', 'component': 'x'})
        self.assertEqual(list(options), ['dictionary_and_rules', 'none'])
        options, m = jc.med_candidates('Ibuprofen 400 MG Oral Tablet', {'ingredients': ['ibuprofen'], 'drug_classes': []})
        self.assertEqual(list(options), ['dictionary', 'rules', 'none']); self.assertEqual(m['rule_mapping'], ['nsaid'])
        self.assertEqual(jc.rule_med('Nystatin 100000 UNT/ML'), [])
        self.assertEqual(jc.rule_encounter('office'), 'primary_care_visit')

    def test_merge_agreement_incremental_and_review(self):
        fake = FakeHTTP(jev_reply(['rules', 'dictionary_and_rules', 'dictionary']))
        ledger = BudgetLedger(self.tmp / 'ledger.sqlite3')
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'test'}):
            results, cost = jc.run(['labs', 'medications'], ledger=ledger, receipts=self.tmp / 'r', transport=fake, out=self.tmp)
        self.assertEqual(cost['requests'], 2)
        sent = json.dumps(fake.bodies)
        self.assertNotIn('source_count', sent)  # only terminology strings and candidate descriptions leave the machine
        labs = json.loads((self.tmp / 'labs.json').read_text())['entries']
        self.assertEqual(labs['eGFR']['jev_choice'], 'dictionary_and_rules'); self.assertEqual(labs['eGFR']['jev_p'], 0.9)
        self.assertEqual(labs['TSH']['variants_by_unit']['mIU/L']['jev_choice'], 'dictionary_and_rules')
        self.assertEqual(labs['blood_pressure_systolic']['jev_choice'], 'dictionary')  # rules do not map this label
        stats, rows = results['medications']
        self.assertEqual(stats['jev_sides_with_rules_when_contested'], 1)
        self.assertEqual(stats['checked'], 2); self.assertEqual(stats['jev_agrees_with_opus'], 1)
        self.assertEqual([r['raw'] for r in rows], ['Ibuprofen 400 MG Oral Tablet'])
        # Incremental: unchanged candidate sets are not asked again.
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'test'}):
            _, cost = jc.run(['labs', 'medications'], ledger=ledger, receipts=self.tmp / 'r', transport=fake, out=self.tmp)
        self.assertEqual(cost['requests'], 0); self.assertEqual(len(fake.bodies), 2)
        # A changed dictionary mapping is re-asked.
        doc = json.loads((self.tmp / 'medications.json').read_text())
        doc['entries']['Ibuprofen 400 MG Oral Tablet']['drug_classes'] = ['nsaid']
        (self.tmp / 'medications.json').write_text(json.dumps(doc))
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'test'}):
            _, cost = jc.run(['medications'], ledger=ledger, receipts=self.tmp / 'r', transport=fake, out=self.tmp)
        self.assertEqual(cost['requests'], 1); self.assertEqual(len(fake.bodies[-1]['questions']), 1)
        review = self.tmp / 'REVIEW.md'
        review.write_text('# Normalization review list\n\n## labs\n- x\n\n## Opus vs Jev vs rules\nold\n\n## Later\nkeep\n')
        jc.write_review(jc.review_section(results, 'test cost'), review)
        text = review.read_text()
        self.assertEqual(text.count('## Opus vs Jev vs rules'), 1); self.assertNotIn('\nold\n', text)
        self.assertIn('## Later\nkeep', text); self.assertIn('| medications | 2 |', text)

    def test_invalid_jev_answer_is_rejected(self):
        body = jc.request_body('labs', jc.plan('labs', json.loads((self.tmp / 'labs.json').read_text())['entries']))
        bad = {'answers': {k: {'choice': 'rules', 'probabilities': {'rules': 1.0}} for k in body['questions']}}
        with self.assertRaises(ValueError): jc.parse_answers(body)(bad)


if __name__ == '__main__':
    unittest.main()
