"""Boundary tests for source isolation, observed-only hypotheses and monitoring."""
import copy
import json
import tempfile
import unittest
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

from clinical_core.contracts import digest
from clinical_core.hybrid_retrieval import retrieve
from clinical_core.joint_observation import FEATURES, fit_joint, infer_joint, observed_features
from clinical_core.local_encoders import verified_manifest
from clinical_core.process_monitoring import monitor, source_volume_monitors


class FakeEncoder:
    identity = "fixture-only"
    def windows(self, text):
        return [{"start": 0, "end": len(text), "text": text, "token_start": 0, "token_end": 1}]
    def encode(self, texts):
        return np.array([[1.0, 0.0] for _ in texts])


class NeuralStackTests(unittest.TestCase):
    def note(self, text="Synthetic spironolactone hold.", **kwargs):
        return {"patient_id": "P", "note_id": "n", "date": "2026-09-01", "text": text, **kwargs}

    def test_cross_patient_and_future_evidence_do_not_enter_retrieval(self):
        with self.assertRaisesRegex(ValueError, "Cross-patient"):
            retrieve(FakeEncoder(), "Q", [self.note()], "hold", "2026-09-30")
        result = retrieve(FakeEncoder(), "P", [self.note(available_date="2026-10-01")], "hold", "2026-09-30")
        self.assertEqual(result["status"], "no_visible_text")
        self.assertEqual(result["excluded_notes"], 1)

    def test_retrieval_protection_and_exact_source_quote(self):
        notes = [self.note(note_id="a", text="Heart failure therapy."), self.note(note_id="b", text="Stopped for hyperkalemia.")]
        result = retrieve(FakeEncoder(), "P", notes, "Heart failure therapy", "2026-09-30", top_k=1,
                          protected=[{"note_id": "b", "start": 0, "end": 5}],
                          provenance={digest(n): {"version": "v1"} for n in notes})
        selected = result["evidence"][0]
        self.assertEqual(selected["note_id"], "b")
        self.assertEqual(selected["text"], notes[1]["text"][selected["start"]:selected["end"]])
        self.assertEqual(selected["source_origin"]["version"], "v1")
        self.assertFalse(result["complete_evidence_guarantee"])

    def test_protected_overflow_is_explicit(self):
        notes = [self.note(note_id=str(i)) for i in range(3)]
        result = retrieve(FakeEncoder(), "P", notes, "therapy", "2026-09-30", top_k=1,
                          protected=[{"note_id": n["note_id"], "start": 0, "end": 3} for n in notes])
        self.assertEqual(result["status"], "protected_evidence_overflow")

    def test_joint_preserves_observational_ambiguity(self):
        x = [0] * len(FEATURES)
        a = {"care": "therapy_present", "channel": "outside", "documentation": "sparse"}
        b = {"care": "therapy_absent", "channel": "no_outside_marker", "documentation": "sparse"}
        model = fit_joint([{"features": x, "target": a}, {"features": x, "target": b}])
        result = infer_joint(model, x)
        self.assertAlmostEqual(sum(s["probability"] for s in result["joint_distribution"]), 1)
        self.assertAlmostEqual(result["marginals"]["care"]["therapy_present"], 0.5)
        self.assertEqual(result["distinct_states_with_same_observed_features"], 2)
        self.assertFalse(result["care_gap_established"])
        self.assertFalse(result["automatic_action"])
        changed = copy.deepcopy(model)
        changed["states"][0]["prior"] = 0.9
        with self.assertRaisesRegex(ValueError, "mismatch"):
            infer_joint(changed, x)

    def test_observed_features_ignore_oracle_and_repeated_evidence_votes(self):
        assertion = {"kind": "reported_outside", "available_date": None}
        case = {"subject": "mra", "evidence": [assertion], "older_assertion_ids": [],
                "observability": [{"source": "notes", "status": "unknown", "supplied_records": 4}]}
        first = observed_features(case)
        case["truth"] = {"care": "therapy_absent"}
        case["evidence"] *= 20
        self.assertEqual(first, observed_features(case))

    def test_process_monitor_finds_shift_but_makes_no_clinical_conclusion(self):
        stable = monitor([10] * 10, reference_mean=10, reference_sd=1)
        self.assertFalse(any(p["investigate_source_change"] for p in stable["points"]))
        changed = monitor([10, 10, 20, 20], reference_mean=10, reference_sd=1)
        self.assertTrue(changed["points"][-1]["investigate_source_change"])
        self.assertIn("not evidence", changed["interpretation"])
        with self.assertRaises(ValueError):
            monitor([float("nan")], reference_mean=0, reference_sd=1)

    def test_feed_monitor_excludes_late_and_partial_month_records(self):
        patient = {"notes": [self.note(date="2026-08-01"), self.note(date="2026-09-01"),
                             self.note(date="2026-07-01", available_date="2026-10-01")],
                   "labs": [], "pharmacy_claims": []}
        reports = source_volume_monitors({"P": patient}, "2026-09-15")
        self.assertEqual(reports[0]["months"], ["2026-08"])
        self.assertEqual(reports[0]["record_counts"], [1])

    def test_model_bytes_are_checked_before_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "weights").write_bytes(b"modified")
            (path / "agere-model.json").write_text(json.dumps({"files": {"weights": {"bytes": 8, "sha256": "bad"}}}))
            with self.assertRaisesRegex(ValueError, "content changed"):
                verified_manifest(path)

    def test_lexical_scores_are_reproducible_across_process_hash_seeds(self):
        code = "from clinical_core.hybrid_retrieval import lexical_scores; print(lexical_scores('hold outside spironolactone low potassium care data', ['outside spironolactone care data hold potassium low', 'low potassium low data']))"
        outputs = [subprocess.check_output([sys.executable, "-c", code],
                   env={**os.environ, "PYTHONHASHSEED": seed}) for seed in ("1", "92", "370")]
        self.assertEqual(len(set(outputs)), 1)


if __name__ == "__main__":
    unittest.main()
