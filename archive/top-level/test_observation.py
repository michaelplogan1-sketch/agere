"""Decision-significant missingness, temporal and cache boundaries."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.bridge import InsightBridge
from clinical_core.inputs import project_sample
from clinical_core.observation import ObservationStore, validate_coverage
from clinical_core.product_view import write_report
from clinical_core.questions import answer
from clinical_core.runtime import Runtime
from clinical_core.service import CorpusService

ROOT = Path(__file__).resolve().parent


class ObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = InsightBridge(ROOT.parent / "insight-engine")
        cls.sample = json.loads((ROOT.parent / "caresignal-mvp/public/demo/patients_sample.json").read_text())[0]

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.store = ObservationStore(self.db)

    def tearDown(self):
        self.db.close()

    def feeds(self, notes=(), rx=()):
        return {"notes": [{"note_id": f"n{i}", "patient_id": "P", "date": "2026-09-01",
                           "text": text, "note_type": "cardiology"} for i, text in enumerate(notes)],
                "pharmacy_claims": list(rx), "labs": []}

    def build(self, feeds, subject="mra", coverage=None, patients=None):
        patients = patients or {"P": feeds}
        rows = [{"patient_id": pid, "practice": "Synthetic site", "answers": {subject: {}}} for pid in patients]
        return self.store.build(patients, rows, "2026-09-30", self.bridge.reader, self.bridge.version, coverage)

    def complete(self):
        return {"status": "complete_for_scope", "known_at": "2026-09-01",
                "window_start": "2025-01-01", "window_end": "2026-09-30",
                "concepts": ["mra"], "evidence_reference": "synthetic coverage manifest"}

    def paid(self):
        return {"rx_id": "rx1", "patient_id": "P", "fill_date": "2026-09-05",
                "drug_class": "mra", "status": "paid"}

    def test_absent_records_are_not_negative_clinical_facts(self):
        result = self.build(self.feeds())["items"][0]
        self.assertEqual(result["state"], "insufficient_evidence")
        self.assertFalse(result["confirmed_care_gap"])
        self.assertIsNone(result["care_gap_probability"])
        self.assertEqual({c["status"] for c in result["observability"]}, {"unknown"})
        self.assertTrue(result["information_needs"])

    def test_declared_coverage_does_not_manufacture_noncompletion(self):
        coverage = {"P": {"notes": self.complete(), "pharmacy_claims": self.complete()}}
        result = self.build(self.feeds(), coverage=coverage)["items"][0]
        self.assertEqual(result["state"], "insufficient_evidence")
        self.assertEqual({c["status"] for c in result["observability"]}, {"complete_for_scope"})
        self.assertFalse(result["automatic_outreach"])

    def test_note_exception_survives_paid_claim_precedence(self):
        feeds = self.feeds(["Spironolactone held due to potassium 5.8."], [self.paid()])
        result = self.build(feeds)["items"][0]
        self.assertEqual(result["state"], "reconcile_timeline")
        self.assertEqual({a["kind"] for a in result["evidence"]}, {"dispense_record", "documented_exception"})
        note = next(a for a in result["evidence"] if a["reference"]["quote"])
        ref = note["reference"]
        self.assertEqual(feeds["notes"][0]["text"][ref["start"]:ref["end"]], ref["quote"])

    def test_outside_report_is_not_an_observed_fill_or_result(self):
        result = self.build(self.feeds(["Spironolactone supplied by the VA."]))["items"][0]
        self.assertEqual(result["state"], "outside_record_reconciliation")
        self.assertEqual(result["evidence"][0]["kind"], "reported_outside")

    def test_reversal_does_not_imply_financial_barrier(self):
        result = self.build(self.feeds(rx=[dict(self.paid(), status="reversed")]))["items"][0]
        self.assertEqual(result["state"], "transaction_review")
        self.assertNotIn("documented_barrier", {a["kind"] for a in result["evidence"]})

    def test_old_copied_hold_is_not_refreshed(self):
        feeds = self.feeds(["Spironolactone held due to potassium 5.8."] * 2)
        feeds["notes"][0]["date"] = "2025-01-01"
        result = self.build(feeds)["items"][0]
        self.assertEqual(result["state"], "insufficient_evidence")
        self.assertEqual(len(result["older_assertion_ids"]), 1)

    def test_future_arrival_and_future_coverage_never_support_current_answer(self):
        feeds = self.feeds(["Spironolactone supplied by the VA."])
        feeds["notes"][0]["available_date"] = "2026-10-01"
        coverage = {"P": {"notes": dict(self.complete(), known_at="2026-10-01")}}
        result = self.build(feeds, coverage=coverage)["items"][0]
        self.assertEqual(result["state"], "insufficient_evidence")
        self.assertFalse(result["evidence"])
        self.assertNotIn("2026-10-01", json.dumps(result))

    def test_patient_cache_invalidates_only_changed_dependencies_and_keeps_versions(self):
        patients = {"P": self.feeds(), "Q": self.feeds()}
        first = self.build(None, patients=patients)
        second = self.build(None, patients=patients)
        self.assertEqual(second["reuse"], {"patient_hits": 2, "patient_misses": 0})
        third = self.build(None, patients=patients, coverage={"P": {"notes": self.complete()}})
        self.assertEqual(third["reuse"], {"patient_hits": 1, "patient_misses": 1})
        self.assertNotEqual(first["items"][0]["assessment_snapshot"], third["items"][0]["assessment_snapshot"])
        patients["P"] = self.feeds(["Spironolactone supplied by the VA."])
        fourth = self.build(None, patients=patients)
        self.assertEqual(fourth["reuse"], {"patient_hits": 1, "patient_misses": 1})
        self.assertEqual(self.db.execute("SELECT count(*) FROM agere_assessments").fetchone()[0], 4)
        self.assertEqual(self.db.execute("SELECT count(*) FROM agere_assertions").fetchone()[0], 1)

    def test_coverage_scope_and_dates_are_enforced(self):
        bad = {"P": {"notes": dict(self.complete(), window_start="2026-10-01")}}
        with self.assertRaises(ValueError):
            validate_coverage(bad, {"P": self.feeds()})
        wrong = {"P": {"notes": dict(self.complete(), concepts=["a1c"])}}
        result = self.build(self.feeds(), coverage=wrong)["items"][0]
        self.assertEqual(result["observability"][0]["status"], "unknown")

    def test_known_late_source_is_excluded_before_legacy_reader(self):
        sample = copy.deepcopy(self.sample)
        note = dict(sample["feeds"]["notes"][0], note_id="late", date="2026-09-01",
                    available_date="2026-10-01", text="Spironolactone supplied by the VA.")
        base = project_sample([sample], "2026-09-30")
        sample["feeds"]["notes"].append(note)
        batch = project_sample([sample], "2026-09-30")
        self.assertEqual(batch.snapshot_id, base.snapshot_id)
        self.assertEqual(batch.excluded_late_records, 1)
        self.assertGreater(batch.arrival_time_missing_records, 0)

    def test_runtime_question_and_report_use_new_evidence_contract(self):
        batch = project_sample([self.sample], "2026-09-30")
        with tempfile.TemporaryDirectory() as d:
            runtime = Runtime(Path(d) / "core.sqlite3", self.bridge)
            try:
                run = runtime.run(batch)
                again = runtime.run(batch)
                self.assertTrue(again["agere"]["reuse"]["whole_run_hit"])
            finally:
                runtime.close()
            response = answer("HF-01", run, batch.patients)
            self.assertIn("not confirmed care gaps", response["interpretation"])
            run["agere"]["items"][0]["next_action"] = "</script><script>alert(1)</script>"
            path = Path(d) / "report.html"
            write_report(run, path)
            self.assertNotIn("</script><script>alert", path.read_text())
            self.assertIn("\\u003c/script>", path.read_text())

    def test_read_only_service_exposes_configured_evidence_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "summary.json").write_text(json.dumps({"worlds": []}))
            path = root / "run.json"
            path.write_text(json.dumps({"run_id": "r", "request": {"as_of": "2026-09-30"},
                                        "agere": {"synthetic": True, "items": []}}))
            result = CorpusService(root, path).get("/agere", {})
            self.assertEqual(result["run_id"], "r")
            self.assertTrue(result["synthetic"])
            with self.assertRaisesRegex(ValueError, "No Agere run"):
                CorpusService(root).get("/agere", {})


if __name__ == "__main__":
    unittest.main()
