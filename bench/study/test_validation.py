import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench.study import common, run, score, validation


class ReferenceGateTests(unittest.TestCase):
    def test_only_reconciled_gold10_is_permitted(self):
        result = validation.require_validated(common.locked_questions()[0])
        self.assertEqual(list(result['sets']), ['gold10'])
        for name in ('S1-lean', 'S2-gold50'):
            qs = common.read_jsonl(common.STUDY_ROOT / name / 'questions.jsonl')
            with self.assertRaisesRegex(ValueError, 'Reference validation required'):
                validation.require_validated(qs)

    def test_text_or_evidence_drift_revokes_validation(self):
        qs = copy.deepcopy(common.locked_questions()[0])
        qs[0]['question'] += ' Changed definition.'
        with self.assertRaisesRegex(ValueError, 'no matching question'):
            validation.require_validated(qs)
        registry = json.loads(validation.REGISTRY.read_text())
        registry['sets'][0]['evidence'][0]['sha256'] = 'wrong'
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'validation.json'; p.write_text(json.dumps(registry))
            with self.assertRaisesRegex(ValueError, 'evidence changed'):
                validation.require_validated(common.locked_questions()[0], registry_path=p)

    def test_unvalidated_run_refused_before_workspace_or_model_work(self):
        qs = common.read_jsonl(common.STUDY_ROOT / 'S2-gold50/questions.jsonl')
        with tempfile.TemporaryDirectory() as d, patch.object(run.isolation, 'preflight') as preflight:
            with self.assertRaisesRegex(ValueError, 'Reference validation required'):
                run.orchestrate(['A'], 'blocked', questions=qs, study_root=Path(d))
            preflight.assert_not_called()
            self.assertFalse((Path(d) / 'blocked').exists())

    def test_direct_rescore_refused_before_generator_execution(self):
        with patch.object(score.subprocess, 'run') as execute:
            with self.assertRaisesRegex(ValueError, 'Reference validation required'):
                score.score_arm_pass(common.STUDY_ROOT / 'S2-gold50', 'A', 1)
            execute.assert_not_called()

    def test_candidate_flags_cannot_leak_into_registered_arm(self):
        env = common.worker_env('A', {'AGERE_HYBRID_REVIEW': 'on', 'AGERE_HYBRID_REVIEW_MODEL': 'unexpected'})
        self.assertNotIn('AGERE_HYBRID_REVIEW', env)
        self.assertNotIn('AGERE_HYBRID_REVIEW_MODEL', env)
