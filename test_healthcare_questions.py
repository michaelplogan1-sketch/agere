"""All-of-healthcare question library: schema, no duplicates against the VBC library, and the coverage tool on fixtures."""
import json, re, sys, unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tools'))
import question_coverage as qc  # noqa: E402
from clinical_core.agere.clinical.capability_registry import PRODUCT  # noqa: E402

VBC, HC = qc.load_libraries(ROOT)
ENGINES = {*PRODUCT, 'literal_lookup', 'knowledge'}


class Schema(unittest.TestCase):
    def test_top_level_and_groups(self):
        for key in ('schema', 'as_of', 'status', 'sources_note', 'domains', 'question_count', 'questions', 'data_elements', 'new_engines', 'domain_groups'):
            self.assertIn(key, HC)
        self.assertEqual(HC['question_count'], len(HC['questions']))
        self.assertTrue(500 <= len(HC['questions']) <= 700)
        group = HC['domain_groups']['value_based_care']
        self.assertEqual(group['domains'], VBC['domains'])
        self.assertEqual(group['question_count'], VBC['question_count'])
        self.assertFalse(set(HC['domains']) & set(VBC['domains']))
        counts = Counter(q['domain'] for q in HC['questions'])
        self.assertEqual(set(counts), set(HC['domains']))
        self.assertGreaterEqual(min(counts.values()), 35)

    def test_question_fields_compatible_with_vbc(self):
        vbc_keys = {'id', 'question', 'persona', 'domain', 'expected_engine', 'source', 'origin'}
        self.assertTrue(vbc_keys <= set(VBC['questions'][0]))
        ids = [q['id'] for q in HC['questions']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(set(ids) & {q['id'] for q in VBC['questions']})
        for q in HC['questions']:
            self.assertTrue(vbc_keys <= set(q), q['id'])
            self.assertTrue(q['question'].endswith('?'), q['id'])
            for k in ('title', 'url', 'publisher', 'note'):
                self.assertTrue(q['source'].get(k), (q['id'], k))
            self.assertTrue(q['source']['url'].startswith('https://'), q['id'])
            self.assertLessEqual(len(q['source']['note'].split()), 40, q['id'])  # paraphrase, never a long quote

    def test_engines_and_data_elements(self):
        E = HC['data_elements']
        for q in HC['questions']:
            for k in q['required_data']:
                self.assertIn(k, E, (q['id'], k))
            missing = [k for k in q['required_data'] if not E[k]['checks']]
            e = q['expected_engine']
            if missing:
                self.assertEqual(e, 'unsupported', q['id'])
                self.assertEqual(q['missing_elements'], missing, q['id'])
                self.assertTrue(q['missing_data'], q['id'])
                e = q['engine_when_data_arrives']
            else:
                self.assertNotIn('missing_elements', q, q['id'])
            if e.startswith('new_engine:'):
                self.assertIn(e.split(':', 1)[1], HC['new_engines'], q['id'])
            else:
                self.assertIn(e, ENGINES, q['id'])
            if e == 'knowledge':
                self.assertEqual(q['required_data'], [], q['id'])
            else:
                self.assertTrue(q['required_data'], q['id'])
        for k, el in E.items():
            self.assertIn(el['kind'], ('warehouse', 'reference'), k)
            for stream, fields in el['checks']:
                self.assertTrue(stream and fields, k)


class NoDuplicates(unittest.TestCase):
    def test_no_duplicates_against_vbc_library(self):
        self.assertEqual(qc.near_duplicates(HC['questions'], VBC['questions'], 0.6), [])

    def test_no_duplicates_within_library(self):
        self.assertEqual(qc.near_duplicates(HC['questions'], HC['questions'], 0.6), [])

    def test_detector_finds_paraphrase(self):
        a = [{'id': 'a', 'question': 'What is our denial rate by payer this year?'}]
        b = [{'id': 'b', 'question': 'What is the denial rate by payer for this year?'}]
        self.assertEqual([(x, y) for x, y, _ in qc.near_duplicates(a, b)], [('a', 'b')])


PROFILE = {'patients': {'fields': {'age_reported': {}, 'county_fips': {}, 'postal_code': {}}},
           'streams': {'encounters': {'records': 10, 'fields': {'start_time': {}, 'end_time': {}, 'type': {}, 'disposition': {}}},
                       'labs': {'records': 0, 'fields': {'name': {}, 'collected_time': {}}}}}
ELEMENTS = {'encounters': {'checks': [['encounters', ['start_time', 'end_time', 'type', 'disposition']]], 'kind': 'warehouse', 'description': 'enc'},
            'labs': {'checks': [['labs', ['name', 'collected_time']]], 'kind': 'warehouse', 'description': 'labs'},
            'geography': {'checks': [['__patients__', ['county_fips']]], 'kind': 'warehouse', 'description': 'geo'},
            'ed_timestamps': {'checks': [], 'kind': 'warehouse', 'description': 'ED timestamps'}}
FIXTURE = {'schema': 'agere-healthcare-questions/1', 'domains': ['ops'], 'question_count': 7, 'data_elements': ELEMENTS,
           'new_engines': {'capacity_simulation': 'queueing'},
           'questions': [
               {'id': 'F1', 'question': 'How many ED visits per week?', 'domain': 'ops', 'persona': 'p', 'expected_engine': 'demand', 'required_data': ['encounters']},
               {'id': 'F2', 'question': 'Count ED visits by county', 'domain': 'ops', 'persona': 'p', 'expected_engine': 'literal_lookup', 'required_data': ['encounters', 'geography']},
               {'id': 'F3', 'question': 'Why is LOS long?', 'domain': 'ops', 'persona': 'p', 'expected_engine': 'explain', 'required_data': ['encounters']},
               {'id': 'F4', 'question': 'Which labs are late?', 'domain': 'ops', 'persona': 'p', 'expected_engine': 'literal_lookup', 'required_data': ['labs']},
               {'id': 'F5', 'question': 'Simulate more beds', 'domain': 'ops', 'persona': 'p', 'expected_engine': 'unsupported',
                'engine_when_data_arrives': 'new_engine:capacity_simulation', 'required_data': ['ed_timestamps'], 'missing_elements': ['ed_timestamps']},
               {'id': 'F6', 'question': 'What is HRRP?', 'domain': 'ops', 'persona': 'p', 'expected_engine': 'knowledge', 'required_data': []},
               {'id': 'F7', 'question': 'Declined please', 'domain': 'ops', 'persona': 'p', 'expected_engine': 'survival', 'required_data': ['encounters']}]}
VBC_FIXTURE = {'schema': 'agere-vbc-questions/1', 'domains': ['utilization'], 'question_count': 1,
               'questions': [{'id': 'V1', 'question': 'Admissions per 1,000?', 'domain': 'utilization', 'persona': 'p', 'expected_engine': 'unsupported',
                              'missing_data': 'regional rates (not received)'}]}


def fake_compile(question, profile, as_of):
    if question.startswith('How many ED'):
        return {'nodes': [{'operation': 'demand', 'parameters': {}}]}, {'route': 'local'}
    if question.startswith('Count'):
        return {'nodes': [{'operation': 'count', 'parameters': {}}]}, {'route': 'local'}
    if question.startswith('Why'):
        return {'nodes': [{'operation': 'discover', 'parameters': {}}]}, {'route': 'local'}
    if question.startswith('Simulate'):
        return {'nodes': [{'operation': 'demand', 'parameters': {}}]}, {'route': 'local'}
    if question.startswith('Declined'):
        return {'coverage': 'unsupported', 'nodes': []}, {'route': 'local_unsupported'}
    if question.startswith('Admissions'):
        raise RuntimeError('compiler crash')
    return None, {'route': 'fallback'}


class CoverageToolOnFixtures(unittest.TestCase):
    def setUp(self):
        self.results = qc.run([VBC_FIXTURE, FIXTURE], {'fx': (PROFILE, ELEMENTS)}, fake_compile, '2029-08-15', set(PRODUCT))
        self.rows = {r['id']: r for r in self.results['fx']}

    def test_element_availability(self):
        self.assertTrue(qc.element_available(ELEMENTS['encounters'], PROFILE))
        self.assertTrue(qc.element_available(ELEMENTS['geography'], PROFILE))
        self.assertFalse(qc.element_available(ELEMENTS['labs'], PROFILE))  # stream present but empty
        self.assertFalse(qc.element_available(ELEMENTS['ed_timestamps'], PROFILE))

    def test_buckets(self):
        b = {k: r['bucket'] for k, r in self.rows.items()}
        self.assertEqual(b, {'V1': 'unsupported (data missing)', 'F1': 'answered locally', 'F2': 'answered locally', 'F3': 'wrong engine',
                             'F4': 'unsupported (data missing)', 'F5': 'unsupported (data missing)', 'F6': 'knowledge', 'F7': 'declined locally'})
        self.assertEqual(self.rows['F4']['missing'], ['labs'])
        self.assertTrue(self.rows['F5']['correctness_risk'])  # compiled a data graph for an unsupported question
        self.assertEqual(self.rows['F5']['engine'], 'new_engine:capacity_simulation')

    def test_new_engine_bucket_when_data_present(self):
        profile = {**PROFILE, 'streams': {**PROFILE['streams'], 'ed': {'records': 1, 'fields': {'t': {}}}}}
        els = {**ELEMENTS, 'ed_timestamps': {'checks': [['ed', ['t']]], 'kind': 'warehouse', 'description': 'ED'}}
        r = qc.classify(FIXTURE['questions'][4], els, profile, fake_compile, '2029-08-15', set(PRODUCT))
        self.assertEqual(r['bucket'], 'needs new engine')

    def test_reports_render(self):
        doc = qc.coverage_report(self.results, VBC_FIXTURE, FIXTURE, {'fx': 'fixture'}, '2029-08-15')
        self.assertIn('| value_based_care | utilization | 1 |', doc)
        self.assertIn('| ops | ops | 7 | 2 | 0 | 1 | 1 | 0 | 1 | 2 | labs (1), ed_timestamps (1) |', doc)
        back = qc.backlog(FIXTURE, {'fx': PROFILE})
        self.assertIn('`capacity_simulation`', back)
        self.assertIn('| 1 | `ed_timestamps` | warehouse | ED timestamps | 1 | 0 | ops (1) |', back)
        cases = qc.acceptance_cases(FIXTURE, set(PRODUCT))
        self.assertEqual([c['id'] for c in cases], ['F1', 'F3', 'F6', 'F7'])  # knowledge is a PRODUCT operator

    def test_generated_acceptance_file_matches_library(self):
        cases = json.loads((ROOT / 'bench/acceptance/healthcare_library.json').read_text())
        self.assertEqual(cases, qc.acceptance_cases(HC, set(PRODUCT)))
        for c in cases:
            self.assertIn(c['expect_operation'], PRODUCT)


if __name__ == '__main__':
    unittest.main()
