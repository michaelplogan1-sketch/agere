"""Integration boundaries for the standalone synthetic engine."""
import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clinical_core.bridge import InsightBridge
from clinical_core.contracts import ReaderSpec
from clinical_core.evaluate import evaluate_sample
from clinical_core.inputs import load_sample, project_sample
from clinical_core.runtime import Runtime


HOME = Path(__file__).resolve().parent.parent
SAMPLE = HOME / "caresignal-mvp/public/demo/patients_sample.json"


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = InsightBridge(HOME / "insight-engine")
        cls.sample = json.loads(SAMPLE.read_text())[:3]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(Path(self.tmp.name) / "core.sqlite3", self.bridge)

    def tearDown(self):
        self.runtime.close()
        self.tmp.cleanup()

    def test_oracle_saved_answers_and_unknown_fields_cannot_affect_inference(self):
        original = project_sample(self.sample, "2026-09-30")
        changed = copy.deepcopy(self.sample)
        for p in changed:
            p["truth"] = {"poison": "ORACLE_SENTINEL"}
            p["engine"] = {"answer": "ORACLE_SENTINEL"}
            p["measure_platform_answers"] = {"answer": "ORACLE_SENTINEL"}
            p["roster"]["truth"] = "ORACLE_SENTINEL"
            for feed in p["feeds"].values():
                for record in feed:
                    record["truth"] = "ORACLE_SENTINEL"
        projected = project_sample(changed, "2026-09-30")
        self.assertEqual(original.snapshot_id, projected.snapshot_id)
        self.assertNotIn("ORACLE_SENTINEL", json.dumps(projected.patients))
        first = self.runtime.run(original)
        second = self.runtime.run(projected)
        self.assertEqual(first["patient_results"], second["patient_results"])
        self.assertTrue(second["telemetry"]["cache_hit"])

    def test_default_reader_preserves_legacy_results_without_network(self):
        batch = project_sample(self.sample, "2026-09-30")
        expected = self.bridge.judge.judge_world(batch.patients, batch.as_of)
        with patch.object(socket.socket, "connect", side_effect=AssertionError("Unexpected network call")):
            result = self.runtime.run(batch)
        self.assertEqual(result["patient_results"], expected)
        self.assertEqual(result["telemetry"]["model_calls"], 0)
        self.assertIsNone(result["telemetry"]["cost_usd"])

    def test_source_change_invalidates_cache_and_preserves_previous_snapshot(self):
        batch = project_sample(self.sample, "2026-09-30")
        first = self.runtime.run(batch)
        changed = copy.deepcopy(self.sample)
        changed[0]["feeds"]["notes"][0]["text"] += "\nAdditional synthetic source observation."
        next_batch = project_sample(changed, "2026-09-30")
        second = self.runtime.run(next_batch)
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertFalse(second["telemetry"]["cache_hit"])
        self.assertEqual(self.runtime.db.execute("SELECT count(*) FROM snapshots").fetchone()[0], 2)
        next_batch.patients[changed[0]["id"]]["notes"][0]["text"] += " Mutated after hashing."
        with self.assertRaisesRegex(ValueError, "changed after snapshot"):
            self.runtime.run(next_batch)

    def test_explicit_reader_can_replace_chart_evidence(self):
        base = self.bridge.reader

        class EmptyEvidenceReader:
            read_chart = staticmethod(base.read_chart)
            ef_values = staticmethod(base.ef_values)
            problem_text = staticmethod(base.problem_text)

            @staticmethod
            def evidence(chart, subject):
                return []

        batch = project_sample(self.sample, "2026-09-30")
        original = self.runtime.run(batch)
        altered = self.runtime.run(batch, reader=EmptyEvidenceReader(),
                                   spec=ReaderSpec("empty-test-reader", "fixture-v1", "test", False))
        self.assertNotEqual(original["run_id"], altered["run_id"])
        self.assertNotEqual(original["patient_results"], altered["patient_results"])
        self.assertIsNone(altered["telemetry"]["model_calls"])
        with self.assertRaisesRegex(ValueError, "versioned ReaderSpec"):
            self.runtime.run(batch, reader=EmptyEvidenceReader())
        with self.assertRaisesRegex(ValueError, "network-dependent"):
            self.runtime.run(batch, reader=EmptyEvidenceReader(),
                             spec=ReaderSpec("cloud", "fixture-v1", "test", True))

    def test_missing_feed_and_cross_patient_record_fail_closed(self):
        broken = copy.deepcopy(self.sample)
        del broken[0]["feeds"]["pharmacy_claims"]
        with self.assertRaisesRegex(ValueError, "Missing source feed"):
            project_sample(broken, "2026-09-30")
        broken = copy.deepcopy(self.sample)
        broken[0]["feeds"]["notes"][0]["patient_id"] = "someone-else"
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            project_sample(broken, "2026-09-30")

    def test_future_event_is_excluded_and_does_not_change_snapshot(self):
        original = project_sample(self.sample, "2026-09-30")
        changed = copy.deepcopy(self.sample)
        future = dict(changed[0]["feeds"]["notes"][0], note_id="FUTURE", date="2026-10-01")
        changed[0]["feeds"]["notes"].append(future)
        projected = project_sample(changed, "2026-09-30")
        self.assertEqual(projected.snapshot_id, original.snapshot_id)
        self.assertEqual(projected.excluded_future_records, original.excluded_future_records + 1)

    def test_exact_evidence_offsets_recover_source_quotes(self):
        batch = project_sample(self.sample, "2026-09-30")
        result = self.runtime.run(batch)
        self.assertTrue(result["note_evidence"])
        for ref in result["note_evidence"]:
            self.assertEqual(ref["status"], "exact_quote_found")
            notes = {n["note_id"]: n for n in batch.patients[ref["patient_id"]]["notes"]}
            for occurrence in ref["occurrences"]:
                text = notes[occurrence["note_id"]]["text"]
                self.assertEqual(text[occurrence["start"]:occurrence["end"]], ref["quote"])

    def test_scoring_is_separate_and_rejects_changed_cutoff_or_population(self):
        path = Path(self.tmp.name) / "sample.json"
        path.write_text(json.dumps(self.sample))
        batch = load_sample(path, "2026-09-30")
        result = self.runtime.run(batch)
        evaluation = evaluate_sample(result, path)
        self.assertEqual(evaluation["run_id"], result["run_id"])
        self.assertNotIn("reader", result)  # scores live in their separate evaluation artifact
        changed = copy.deepcopy(result)
        changed["request"]["as_of"] = "2026-09-29"
        with self.assertRaisesRegex(ValueError, "cutoff"):
            evaluate_sample(changed, path)
        changed = copy.deepcopy(self.sample)
        changed[0]["feeds"]["notes"][0]["text"] += " Changed."
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "snapshot"):
            evaluate_sample(result, path)


if __name__ == "__main__":
    unittest.main()
