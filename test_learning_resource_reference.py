"""Relocated learning installations keep the same frozen exclusions; no answer keys."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere import local_release, model_policy
from clinical_core.agere.clinical import semantic_learning as learning
from test_semantic_learning import example, export, policy


class LearningResourceReference(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.resource = self.root/'external-experiment'; self.resource.mkdir()
        self.source = self.resource/'reserved-sources.json'
        self.source.write_text(json.dumps(['reserved-patient']))
        self.plan = self.resource/'experiment-plan.json'
        self.plan.write_text(json.dumps({'files': {'reserved-sources.json': self.sha(self.source)},
            'reserved_families': ['reserved-family'], 'calibration_source_families': ['calibration-family']}))
        self.env = {'AGERE_LEARNING_PROTECTION_PLAN': str(self.plan),
                    'AGERE_LEARNING_PROTECTION_PLAN_SHA256': self.sha(self.plan)}
        self.patch = patch.dict(os.environ, {}, clear=True); self.patch.start(); self.addCleanup(self.patch.stop)

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_runtime_reference_keeps_original_protection_identity_and_training_exclusions(self):
        old = self.root/'original/artifacts/agere-local-decisions-v1'; old.mkdir(parents=True)
        for path in (self.plan, self.source): (old/path.name).write_bytes(path.read_bytes())
        with patch.object(learning, 'ROOT', self.root/'original'):
            original = learning.protections()
        environment = model_policy.application_runtime({'runtime': {'environment': self.env}})['environment']
        with patch.dict(os.environ, environment), patch.object(learning, 'ROOT', self.root/'new-checkout'):
            relocated = learning.protections()
            self.assertEqual(relocated, original)
            rows = [example('reserved-patient'), example('calibration'), example('available')]
            lineage = policy(rows); lineage['patient_families']['calibration'] = 'calibration-family'
            prepared = learning.prepare(export(rows), lineage, relocated)
            self.assertEqual([r['patient_id'] for r in prepared['rows']], ['available'])
            self.assertEqual([r['reason'] for r in prepared['excluded']], ['protected_evaluation_family']*2)
            self.assertFalse(prepared['training_performed'])
        self.assertFalse((self.root/'new-checkout').exists())

    def test_external_reference_requires_both_fields_and_does_not_fall_back(self):
        for env in ({'AGERE_LEARNING_PROTECTION_PLAN': str(self.plan)},
                    {'AGERE_LEARNING_PROTECTION_PLAN_SHA256': self.sha(self.plan)}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(ValueError, 'pinned SHA-256'): learning.protections()
        with self.assertRaises(FileNotFoundError):
            learning.protections(plan_path=self.root/'missing.json', plan_sha256=self.sha(self.plan))

    def test_changed_plan_or_source_register_never_reduces_exclusions(self):
        before = self.plan.read_bytes()
        self.plan.write_text(before.decode()+'\n')
        with patch.dict(os.environ, self.env), self.assertRaisesRegex(ValueError, 'plan changed'):
            learning.protections()
        self.plan.write_bytes(before); self.source.write_text('[]')
        with patch.dict(os.environ, self.env), self.assertRaisesRegex(ValueError, 'source register changed'):
            learning.protections()

    def test_release_tracks_only_the_two_metadata_files_and_rejects_changed_dependency(self):
        build = self.root/'build'; (build/'stack').mkdir(parents=True)
        encoder = self.root/'encoder'; source = self.root/'source'; reranker = self.root/'models/bge-reranker-v2-m3'
        for path in (encoder, source, reranker):
            path.mkdir(parents=True); (path/'run.json').write_text('{"synthetic": true}')
        (build/'stack/run.json').write_text(json.dumps({'encoder_path': str(encoder)}))
        ledger = self.root/'ledger.sqlite3'; ledger.write_bytes(b'fixture')
        # Unrelated experiment content must not become a dependency or be read.
        unrelated = self.resource/'unrelated.json'; unrelated.write_bytes(b'unreadable as JSON')
        config = {'source_release': str(source), 'runtime': {'environment': self.env}}
        with patch.object(local_release, 'ROOT', self.root), patch.dict(os.environ, {'AGERE_LEDGER_PATH': str(ledger)}):
            dependencies = local_release.dependencies(build, config)
        entry = next(d for d in dependencies if d['path'] == str(self.resource))
        self.assertEqual(set(entry['files']), {'experiment-plan.json', 'reserved-sources.json'})
        local_release.check_dependencies(dependencies)
        self.source.write_text('[]')
        with self.assertRaises(ValueError): local_release.check_dependencies(dependencies)


if __name__ == '__main__': unittest.main()
