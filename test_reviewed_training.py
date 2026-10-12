"""Fictional fixtures test training plumbing, not clinical performance."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from clinical_core.contracts import digest
from clinical_core.corpus import family_split
from clinical_core.learning import LABELS, VERSION, LearningStore
from clinical_core.reviewed_training import feature_text, inspect_export, train


class ReviewedTrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.database = self.root / "reviews.sqlite3"
        self.store = LearningStore(self.database)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def example(self, split, label, index=0, text=None, reviewed=True):
        family = [f"family{i}" for i in range(1000) if family_split(f"family{i}") == split][index]
        text = text or f"Synthetic {label} evidence example {index} in {split}."
        task = {"schema_version": VERSION, "synthetic": True,
                "origin": {"world": "dev", "family": family, "patient_id": family,
                           "split": split, "snapshot_id": "fixture", "run_id": "fixture"},
                "input": {"subject": "mra", "as_of": "2026-09-30", "window_start": "2026-06-01",
                          "source_coverage": "unknown", "evidence": [{"id": "e0", "source": "notes",
                          "record": {"date": "2026-09-01", "text": text}}]},
                "source_map": {"e0": {"source_hash": digest(text)}}, "label_options": LABELS,
                "proposal": {"state": label, "provenance": "fictional test proposal"}}
        task["task_id"] = digest(task)
        self.store.register([task])
        review = {"task_id": task["task_id"], "previous_revision": None, "decision": "accept",
                  "label": label, "support_ids": ["e0"], "reviewer": "FICTIONAL UNIT TEST ONLY",
                  "reviewer_kind": "human", "reviewed_at": "2026-01-01T00:00:00+00:00",
                  "rationale": "Software fixture; not a clinical review."}
        if reviewed:
            self.store.import_reviews([review])
        return task, review

    def export(self):
        target = self.root / "dataset"
        self.store.export(target)
        return target

    def ready(self):
        for split in ("train", "calibration"):
            self.example(split, "exception_review", 0)
            self.example(split, "outside_record_reconciliation", 1)
        return self.export()

    def rewrite_manifest(self, target):
        manifest = json.loads((target / "manifest.json").read_text())
        for split in ("train", "calibration"):
            payload = (target / (split + ".jsonl")).read_text()
            manifest["files"][split + ".jsonl"]["content_digest"] = digest(payload)
        (target / "manifest.json").write_text(json.dumps(manifest))

    def test_unreviewed_queue_blocks_fitting_and_creates_no_weights(self):
        self.example("train", "exception_review", reviewed=False)
        result = train(self.export(), self.database, self.root / "model")
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["model_weights_trained"])
        self.assertFalse((self.root / "model").exists())

    def test_file_tampering_fails_digest_and_forged_manifest_fails_review_match(self):
        target = self.ready()
        path = target / "train.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[0]["target"]["state"] = "access_review"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        with self.assertRaisesRegex(ValueError, "digest"):
            inspect_export(target, self.database)
        self.rewrite_manifest(target)
        with self.assertRaisesRegex(ValueError, "latest attributed review"):
            inspect_export(target, self.database)

    def test_superseded_review_invalidates_export(self):
        task, review = self.example("train", "exception_review")
        target = self.export()
        previous, _ = self.store.latest(task["task_id"])
        self.store.import_reviews([{**review, "previous_revision": previous, "decision": "exclude",
                                    "label": None, "support_ids": []}])
        with self.assertRaisesRegex(ValueError, "Excluded"):
            inspect_export(target, self.database)

    def test_missing_calibration_state_is_reported_not_silently_dropped(self):
        self.example("train", "exception_review")
        self.example("train", "outside_record_reconciliation", 1)
        self.example("calibration", "access_review")
        report, _ = inspect_export(self.export(), self.database)
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any("absent from training" in b for b in report["blockers"]))

    def test_features_exclude_attribution_and_reject_future_or_hidden_fields(self):
        task, _ = self.example("train", "exception_review")
        model_input = copy.deepcopy(task["input"])
        model_input["proposal"] = "POISON"
        model_input["reviewer"] = "POISON"
        self.assertNotIn("POISON", feature_text(model_input))
        model_input["evidence"][0]["record"]["truth"] = "POISON"
        with self.assertRaisesRegex(ValueError, "leakage"):
            feature_text(model_input)
        del model_input["evidence"][0]["record"]["truth"]
        model_input["evidence"][0]["record"]["available_date"] = "2026-10-01"
        with self.assertRaisesRegex(ValueError, "Future or late"):
            feature_text(model_input)

    def test_feature_duplicate_across_splits_rejected_even_with_different_evidence_ids(self):
        self.example("train", "exception_review", text="Spironolactone held.")
        task, review = self.example("calibration", "exception_review", text="Spironolactone held.", reviewed=False)
        task["input"]["evidence"][0]["id"] = "e9"
        task["source_map"] = {"e9": task["source_map"]["e0"]}
        task["task_id"] = digest({k: v for k, v in task.items() if k != "task_id"})
        self.store.register([task])
        self.store.import_reviews([{**review, "task_id": task["task_id"], "support_ids": ["e9"]}])
        with self.assertRaisesRegex(ValueError, "Repeated model features"):
            inspect_export(self.export(), self.database)

    @unittest.skipUnless(importlib.util.find_spec("sklearn"), "Optional training dependencies unavailable")
    def test_fit_uses_training_only_and_stays_out_of_runtime(self):
        import joblib
        target = self.ready()
        # Canary exists only in calibration evidence, never in the fitted vocabulary.
        # Both partitions are independently varied fixtures with separate families.
        output = self.root / "model"
        report = train(target, self.database, output)
        self.assertTrue(report["model_weights_trained"])
        self.assertFalse(report["runtime_promoted"])
        self.assertEqual(report["external_model_calls"], 0)
        self.assertTrue(set(report["training_task_ids"]).isdisjoint(report["calibration_task_ids"]))
        model = joblib.load(output / "model.joblib")
        self.assertNotIn("calib", model["tfidf"].vocabulary_)
        self.assertTrue((output / "calibration_predictions.jsonl").exists())
        with self.assertRaisesRegex(ValueError, "immutable"):
            train(target, self.database, output)


if __name__ == "__main__":
    unittest.main()
