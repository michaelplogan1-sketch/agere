"""Human-attributed corrections must remain distinct from machine proposals."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from clinical_core.contracts import SourceBatch, digest
from clinical_core.corpus import family_split
from clinical_core.learning import LABELS, VERSION, LearningStore, make_input, prepare_tasks
from clinical_core.review_view import write_review_view


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = LearningStore(self.root / "reviews.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def task(self, split="train", text="Spironolactone supplied by an outside pharmacy."):
        family = next(f"family{i}" for i in range(1000) if family_split(f"family{i}") == split)
        task = {"schema_version": VERSION, "synthetic": True,
                "origin": {"world": "dev", "family": family, "patient_id": family, "split": split,
                           "snapshot_id": "synthetic-test", "run_id": "synthetic-test"},
                "input": {"subject": "mra", "evidence": [{"id": "e0", "record": {"text": text}}]},
                "source_map": {"e0": {"source_hash": digest(text)}}, "label_options": LABELS,
                "proposal": {"state": "outside_record_reconciliation", "provenance": "unreviewed"}}
        task["task_id"] = digest(task)
        self.store.register([task])
        return task

    def review(self, task, **overrides):
        return {"task_id": task["task_id"], "previous_revision": None, "decision": "accept",
                "label": "outside_record_reconciliation", "support_ids": ["e0"],
                "reviewer": "unit-test reviewer (fictional)", "reviewer_kind": "human",
                "reviewed_at": "2026-01-01T00:00:00+00:00", "rationale": "Synthetic test fixture only.", **overrides}

    def test_proposals_alone_cannot_become_training_labels(self):
        self.task()
        manifest = self.store.export(self.root / "export")
        self.assertEqual(manifest["counts"], {"train": 0, "calibration": 0})
        self.assertEqual(manifest["pending_review"], 1)
        self.assertFalse(manifest["model_weights_trained"])

    def test_model_attribution_and_invented_support_are_rejected(self):
        task = self.task()
        with self.assertRaisesRegex(ValueError, "Model proposals"):
            self.store.import_reviews([self.review(task, reviewer_kind="model")])
        with self.assertRaisesRegex(ValueError, "support reference"):
            self.store.import_reviews([self.review(task, support_ids=["invented"])])
        with self.assertRaisesRegex(ValueError, "supporting records"):
            self.store.import_reviews([self.review(task, support_ids=[])])

    def test_corrections_are_immutable_and_stale_writes_fail(self):
        task = self.task()
        original = self.review(task)
        first = self.store.import_reviews([original])[0]
        self.assertEqual(self.store.import_reviews([original]), [first])
        correction = self.review(task, previous_revision=first, decision="correct", label="insufficient_evidence", support_ids=[])
        second = self.store.import_reviews([correction])[0]
        self.assertNotEqual(first, second)
        with self.assertRaisesRegex(ValueError, "Stale"):
            self.store.import_reviews([self.review(task, rationale="Stale correction.")])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM learning_reviews").fetchone()[0], 2)
        self.store.export(self.root / "export")
        row = json.loads((self.root / "export/train.jsonl").read_text())
        self.assertEqual(row["target"]["state"], "insufficient_evidence")
        self.assertNotIn("proposal", row)
        self.assertEqual(row["provenance"]["review_revision"], second)

    def test_batch_import_rolls_back_on_bad_second_review(self):
        task = self.task()
        with self.assertRaises(ValueError):
            self.store.import_reviews([self.review(task), self.review(task, task_id="missing")])
        self.assertIsNone(self.store.latest(task["task_id"])[0])

    def test_exact_cross_split_inputs_are_blocked_from_both_exports(self):
        train, calibration = self.task("train"), self.task("calibration")
        self.store.import_reviews([self.review(train), self.review(calibration)])
        result = self.store.export(self.root / "export")
        self.assertEqual(result["blocked_cross_split_or_conflicting_input"], 2)
        self.assertEqual(result["counts"], {"train": 0, "calibration": 0})

    def test_family_and_world_boundaries_and_task_hash_are_checked(self):
        task = self.task()
        task["origin"]["world"] = "hard16_llm"
        with self.assertRaisesRegex(ValueError, "hash"):
            self.store.register([task])
        task["task_id"] = digest({k: v for k, v in task.items() if k != "task_id"})
        with self.assertRaisesRegex(ValueError, "partition"):
            self.store.register([task])

    def test_exclusion_supersedes_training_label_and_old_export_is_preserved(self):
        task = self.task()
        first = self.store.import_reviews([self.review(task)])[0]
        self.store.export(self.root / "v1")
        self.store.import_reviews([self.review(task, previous_revision=first, decision="exclude", label=None, support_ids=[])])
        result = self.store.export(self.root / "v2")
        self.assertEqual(result["excluded_by_reviewer"], 1)
        self.assertEqual(result["counts"]["train"], 0)
        self.assertTrue((self.root / "v1/train.jsonl").read_text())
        with self.assertRaisesRegex(ValueError, "empty"):
            self.store.export(self.root / "v1")

    def test_raw_input_does_not_include_truth_identity_or_late_evidence(self):
        notes = [{"patient_id": "P", "note_id": "n1", "date": "2026-09-01", "note_type": "pcp",
                  "text": "Patient declined spironolactone.", "truth": "POISON", "engine": "POISON"},
                 {"patient_id": "P", "note_id": "late", "date": "2026-09-01", "note_type": "pcp",
                  "text": "LATE_POISON", "available_date": "2026-10-01"}]
        model_input, mapping = make_input({"notes": notes, "pharmacy_claims": [], "labs": []}, "mra", "2026-09-30", "2026-06-02")
        text = json.dumps(model_input)
        self.assertNotIn("POISON", text)
        self.assertNotIn("patient_id", text)
        self.assertNotIn("note_id", text)
        self.assertEqual(len(mapping), 1)

    def test_queue_refuses_evaluation_world_and_keeps_clones_together(self):
        family = next(f"family{i}" for i in range(1000) if family_split(f"family{i}") == "train")
        feeds = {"notes": [], "pharmacy_claims": [], "labs": []}
        patients = {"P": feeds, "P-clone": feeds}
        batch = SourceBatch(patients, "2026-09-30", digest({"patients": patients, "as_of": "2026-09-30"}), 0)
        items = [{"patient_id": pid, "subject": "mra", "state": "insufficient_evidence", "window_start": "2026-06-02"} for pid in patients]
        run = {"run_id": "r", "request": {"snapshot_id": batch.snapshot_id}, "agere": {"items": items}}
        with self.assertRaisesRegex(ValueError, "development"):
            prepare_tasks(batch, run, {}, "hard16_llm")
        tasks = prepare_tasks(batch, run, {pid: family for pid in patients}, "dev")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["origin"]["split"], "train")

    def test_review_html_escapes_records_and_emits_real_newline_delimiters(self):
        task = self.task(text="</script><script>BAD()</script>")
        path = self.root / "review.html"
        write_review_view([task], path)
        html = path.read_text()
        self.assertNotIn("</script><script>BAD", html)
        self.assertIn("\\u003c/script>", html)
        self.assertIn(".join('\\n')+'\\n'", html)
        self.assertNotIn(".join('\\\\n')", html)


if __name__ == "__main__":
    unittest.main()
