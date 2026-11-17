"""Model-free checks for the clinical knowledge layer: every concept's own test cases, the review lifecycle, and that nothing
unreviewed reaches a live answer."""
import json
import os
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from clinical_core.agere.tiered import knowledge


class Store(unittest.TestCase):
    def test_every_concept_passes_its_own_test_cases(self):
        for c in knowledge.concepts():
            self.assertEqual(knowledge.run_tests(c), [], c['id'])

    def test_every_source_records_its_licence_and_what_is_kept(self):
        for s in knowledge.sources().values():
            for field in ('title', 'short', 'publisher', 'year', 'citation', 'url', 'licence', 'kind'):
                self.assertTrue(s.get(field), (s['id'], field))
            self.assertIn('Kept', s['licence'], s['id'])

    def test_a_malformed_proposal_is_refused(self):
        self.assertTrue(knowledge.validate({'id': 'x', 'name': 'x', 'domain': 'x', 'triggers': '(', 'summary': 'x',
                                            'sources': [{'id': 'NOT-A-SOURCE'}], 'tests': {}}))


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        for n in ('sources.json', 'concepts.json'): shutil.copy(knowledge.DIRECTORY / n, self.dir / n)
        self.saved = knowledge.DIRECTORY
        knowledge.DIRECTORY = self.dir
        knowledge._cache.clear()

    def tearDown(self):
        knowledge.DIRECTORY = self.saved
        knowledge._cache.clear()
        shutil.rmtree(self.dir)

    def test_nothing_unreviewed_is_served_by_default(self):
        with unittest.mock.patch.dict(os.environ, {'AGERE_TIERED_KNOWLEDGE': 'reviewed'}):
            self.assertEqual(knowledge.served(), [])
            self.assertEqual(knowledge.block('How many patients have stage 3 CKD by eGFR?'), '')
            self.assertEqual(knowledge.check('How many patients had stage 3 CKD by eGFR in 2028?', "SELECT 1 FROM measurement WHERE concept = 'egfr' AND value_canonical < 45"), [])
            self.assertEqual(knowledge.not_computable('What was our CAUTI rate in 2028?'), [])

    def test_candidates_are_served_only_when_an_evaluation_asks(self):
        token = knowledge.ENABLED.set('candidates')
        try:
            text = knowledge.block('How many patients have stage 3 CKD by eGFR?')
            self.assertIn('KDIGO', text); self.assertIn('candidate, not yet clinically reviewed', text)
            self.assertTrue(knowledge.check('How many patients had stage 3 CKD by eGFR in 2028?',
                                            "SELECT 1 FROM measurement WHERE concept = 'egfr' AND value_canonical < 45"))
            self.assertTrue(knowledge.not_computable('What was our CAUTI rate per 1,000 catheter days in 2028?'))
        finally:
            knowledge.ENABLED.reset(token)

    def test_approval_is_refused_through_an_agent(self):
        with unittest.mock.patch.dict(os.environ, {'CLAUDECODE': '1'}):
            with self.assertRaises(SystemExit):
                knowledge.review('ckd_egfr_categories', 'approve', 'Reviewer')

    def test_an_approval_binds_to_content_and_an_edit_returns_it_to_candidate(self):
        env = {k: v for k, v in os.environ.items() if k not in ('CLAUDECODE', 'CODEX_SANDBOX', 'AGERE_AGENT')}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            os.environ['AGERE_TIERED_KNOWLEDGE'] = 'reviewed'
            st, _ = knowledge.review('ckd_egfr_categories', 'approve', 'Test reviewer', 'test')
            self.assertEqual(st, 'enforced')
            self.assertEqual([c['id'] for c in knowledge.served()], ['ckd_egfr_categories'])
            self.assertNotIn('candidate', knowledge.block('How many patients have stage 3 CKD by eGFR?'))
            data = json.loads((self.dir / 'concepts.json').read_text())
            next(c for c in data['concepts'] if c['id'] == 'ckd_egfr_categories')['summary'] += ' Edited.'
            (self.dir / 'concepts.json').write_text(json.dumps(data))
            knowledge._cache.clear()
            st, why = knowledge.status(knowledge.concept('ckd_egfr_categories'))
            self.assertEqual(st, 'candidate'); self.assertIn('needs review again', why)
            self.assertEqual(knowledge.served(), [])

    def test_a_rejected_concept_is_never_served(self):
        knowledge.review('anemia_who', 'reject', 'Test reviewer', 'cutoffs to confirm')
        token = knowledge.ENABLED.set('candidates')
        try:
            self.assertNotIn('anemia_who', [c['id'] for c in knowledge.served()])
        finally:
            knowledge.ENABLED.reset(token)


class Checks(unittest.TestCase):
    def setUp(self):
        self.token = knowledge.ENABLED.set('candidates')

    def tearDown(self):
        knowledge.ENABLED.reset(self.token)

    def test_numbers_inside_codes_do_not_satisfy_a_threshold(self):
        # LOINC 2524-7 holds a 2 nowhere as a number: the lactate threshold is not met by the code string
        self.assertTrue(knowledge.check('How many patients had an elevated lactate in 2028?',
                                        "SELECT COUNT(*) FROM measurement WHERE loinc = '2524-7' AND value_canonical > 1"))

    def test_parameters_are_read_as_written_into_the_sql(self):
        q = 'How many patients had an elevated lactate in 2028?'
        self.assertEqual(knowledge.check(q, "SELECT COUNT(*) FROM measurement WHERE loinc = '2524-7' AND value_canonical > :t", {'t': {'type': 'int', 'value': 2}}), [])

    def test_a_question_the_concept_does_not_cover_is_not_checked(self):
        self.assertEqual(knowledge.check('Average eGFR in 2028', "SELECT AVG(value_canonical) FROM measurement WHERE concept = 'egfr'"), [])


if __name__ == '__main__':
    unittest.main()
