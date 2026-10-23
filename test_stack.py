"""Corpus, capability and hosted inference boundaries."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from clinical_core.corpus import family_mapping, family_split, load_world, partition_ids
from clinical_core.inputs import FIELDS
from clinical_core.providers import EvidenceTask, HostedGateway, JevProvider, ClaudeProvider, Cascade
from clinical_core.questions import catalog, answer
from clinical_core.service import CorpusService


class StackTests(unittest.TestCase):
    def task(self):
        return EvidenceTask("statin", "2026-09-30", {"declined": "Documented refusal", "unknown": "No evidence"},
                            [{"id": "s0", "text": "Patient declined statin.", "date": "2026-09-01", "note_id": "n0"}])

    def test_families_keep_clones_together_and_block_world_leakage(self):
        mapping = family_mapping([{"id": "P-1", "runnable": True}, {"id": "P-2", "runnable": False}])
        self.assertEqual({family_split(mapping[p]) for p in ("P-1", "P-1-2", "P-1-3")}, {family_split("P-1")})
        self.assertNotIn("P-2", mapping)
        with self.assertRaises(ValueError):
            family_mapping([{"id": "P", "runnable": True}, {"id": "P-2", "runnable": True}])
        with self.assertRaisesRegex(ValueError, "Evaluation worlds"):
            partition_ids({"worlds": [{"name": "test", "role": "evaluation"}]}, "test", "train")

    def test_world_adapter_ignores_truth_and_rejects_orphans(self):
        sample = json.loads((Path(__file__).resolve().parent / "tests/fixtures/world_patient_sample.json").read_text())  # one synthetic patient: roster and feeds only
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "feeds").mkdir()
            (root / "truth").mkdir()
            (root / "truth/patients.jsonl").write_text("not even valid JSON ORACLE_SENTINEL")
            for feed in FIELDS:
                rows = [sample["roster"]] if feed == "roster" else sample["feeds"][feed]
                (root / f"feeds/{feed}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            result = load_world(root, "2026-09-30")
            self.assertNotIn("ORACLE_SENTINEL", json.dumps(result.patients))
            with (root / "feeds/notes.jsonl").open("a") as out:
                out.write(json.dumps({"patient_id": "OTHER"}) + "\n")
            with self.assertRaisesRegex(ValueError, "no roster"):
                load_world(root, "2026-09-30")

    def test_question_catalog_preserves_originals_and_abstains(self):
        questions = [q for g in catalog()["groups"] for q in g["questions"]]
        self.assertEqual(len(questions), 140)
        self.assertEqual(len({q["id"] for q in questions}), 140)
        self.assertEqual(sum(q["origin"] == "existing user-derived catalog" for q in questions), 76)
        result = answer("SNF-01", {"run_id": "r", "request": {"as_of": "2026-09-30"}, "coverage": {"evaluated": 0}}, {})
        self.assertEqual(result["status"], "not_implemented")
        self.assertIsNone(result["result"])
        self.assertTrue(result["required_data"])

    def test_gateway_defaults_off_and_enforces_attempt_budget(self):
        def transport(*args, **kwargs):
            return io.BytesIO(json.dumps({"model": "jev-1.13.0", "answers": {}, "usage": {"input_tokens": 100, "output_tokens": 0}}).encode())
        with self.assertRaisesRegex(ValueError, "disabled"):
            HostedGateway(transport=transport).send("jev", {"model": "jev-1.13.0"})
        gateway = HostedGateway(allow_live=True, max_calls=2, max_usd=0.5, transport=transport)
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "TEST-NOT-A-SECRET"}):
            gateway.send("jev", {"model": "jev-1.13.0"})
            with self.assertRaisesRegex(ValueError, "reservation"):
                gateway.send("jev", {"model": "jev-1.13.0"})
        self.assertNotIn("TEST-NOT-A-SECRET", json.dumps(gateway.events))

    def test_provider_requests_contain_no_truth_or_arbitrary_fields(self):
        task = self.task()
        for provider in (JevProvider(None), ClaudeProvider(None)):
            body = provider.request(task)
            self.assertNotIn("truth", json.dumps(body))
        bad = self.task()
        bad.evidence[0]["truth"] = "poison"
        with self.assertRaises(ValueError):
            JevProvider(None).request(bad)

    def test_frontier_invented_reference_and_truncation_rejected(self):
        class Fake:
            def send(self, *args):
                return {"model": "test", "stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps({"label": "declined", "evidence_ids": ["invented"], "uncertain": False})}]}
        with self.assertRaisesRegex(ValueError, "invented"):
            ClaudeProvider(Fake()).classify(self.task())
        class Truncated:
            def send(self, *args):
                return {"stop_reason": "max_tokens"}
        with self.assertRaisesRegex(ValueError, "incomplete"):
            ClaudeProvider(Truncated()).classify(self.task())

    def test_jev_rejects_invalid_distribution(self):
        class Fake:
            def send(self, *args):
                return {"answers": {"reason": {"type": "choice", "choice": "declined", "probabilities": {"declined": 1.5, "unknown": -0.5}}, "support": {}}}
        with self.assertRaisesRegex(ValueError, "distribution"):
            JevProvider(Fake()).classify(self.task())

    def test_uncalibrated_routing_does_not_auto_accept(self):
        class Primary:
            def classify(self, task):
                return {"label": "declined", "probabilities": {"declined": 1}, "uncertain": False}
        result = Cascade(Primary(), None).classify(self.task())
        self.assertEqual(result["route"], "review")
        self.assertTrue(result["uncertain"])

    def test_provider_overload_routes_without_fabricating_a_clinical_answer(self):
        class Busy:
            name = "jev"
            def classify(self, task):
                raise urllib.error.HTTPError("https://example.test", 529, "overloaded", {}, None)
        class Frontier:
            def classify(self, task):
                return {"label": "declined", "evidence_ids": ["s0"], "uncertain": False}
        result = Cascade(Busy(), Frontier()).classify(self.task())
        self.assertEqual(result["route"], "frontier_service_fallback")
        self.assertEqual(result["service_failure"]["service_error"], 529)
        review = Cascade(Busy(), None).classify(self.task())
        self.assertEqual(review["label"], "unknown")
        self.assertEqual(review["route"], "review")

    def test_hosted_cache_reuses_response_without_spending(self):
        calls = []
        def transport(*args, **kwargs):
            calls.append(1)
            return io.BytesIO(b'{"model":"jev-1.13.0","answers":{}}')
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"TYPESAFE_API_KEY": "fake"}):
            gateway = HostedGateway(allow_live=True, max_calls=1, max_usd=0.5, cache_dir=d, transport=transport)
            body = {"model": "jev-1.13.0"}
            self.assertEqual(gateway.send("jev", body), gateway.send("jev", body))
            self.assertEqual(len(calls), 1)
            self.assertEqual(gateway.reserved_usd, 0.5)
        with self.assertRaises(ValueError):
            HostedGateway(max_usd=float("nan"))

    def test_api_paths_cannot_escape_world_registry(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "summary.json").write_text(json.dumps({"worlds": [{"world": "dev"}]}))
            service = CorpusService(root)
            with self.assertRaises(ValueError):
                service.get("/answers/HF-01", {"world": ["../../secret"]})
            self.assertFalse(service.get("/health", {})["hosted_inference_enabled"])


if __name__ == "__main__":
    unittest.main()
