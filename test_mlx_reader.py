"""Exercise unsafe model output boundaries without loading weights."""
import json
import tempfile
import unittest
from pathlib import Path

from clinical_core.bridge import InsightBridge
from clinical_core.mlx_reader import ModelReader, validate_output


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = InsightBridge(Path(__file__).resolve().parent.parent / "insight-engine")

    def test_rejects_invented_references_labels_and_schema(self):
        valid = {"evidence": [{"subject": "statin", "type": "declined", "sentence_id": 0}], "review_subjects": []}
        self.assertEqual(validate_output(json.dumps(valid), [{}]), valid)
        for entry in [dict(valid["evidence"][0], sentence_id=9),
                      dict(valid["evidence"][0], sentence_id=True),
                      dict(valid["evidence"][0], type="cured"),
                      dict(valid["evidence"][0], subject="unknown"),
                      dict(valid["evidence"][0], quote="made-up evidence")]:
            with self.assertRaises(ValueError):
                validate_output(json.dumps(dict(valid, evidence=[entry])), [{}])
        with self.assertRaises(ValueError):
            validate_output('```json\n{}\n```', [])

    def test_invalid_or_truncated_output_routes_to_review_and_caches(self):
        for raw, finish in [('nonsense', 'stop'), ('{"evidence":[],"review_subjects":[]}', 'length')]:
            with self.subTest(finish=finish), tempfile.TemporaryDirectory() as d:
                calls = []
                def generate(sentences):
                    calls.append(sentences)
                    return raw, {"finish_reason": finish}
                r = ModelReader(self.bridge.reader, generate, {"test": True}, Path(d))
                c = r.read_chart([])
                self.assertEqual(r.evidence(c, "statin"), [])
                row = r.finalize({"answers": {"statin": {"answer": "gap:never_offered"}, "a1c": {"answer": "done"}}})
                self.assertEqual(row["answers"]["statin"]["answer"], "review:uncertain")
                self.assertEqual(row["answers"]["a1c"]["answer"], "done")
                r.evidence(r.read_chart([]), "statin")
                self.assertEqual(len(calls), 1)

    def test_evidence_uses_source_text_and_dedupe_date(self):
        notes = [{"text": "Patient declined statin.", "note_id": f"n{i}", "date": f"2026-0{i}-01", "note_type": "pcp"} for i in (1, 2)]
        raw = json.dumps({"evidence": [{"subject": "statin", "type": "declined", "sentence_id": 0}], "review_subjects": []})
        with tempfile.TemporaryDirectory() as d:
            r = ModelReader(self.bridge.reader, lambda s: (raw, {"finish_reason": "stop"}), {}, Path(d))
            c = r.read_chart(notes)
            evidence = r.evidence(c, "statin")
            self.assertEqual(evidence[0]["text"], notes[0]["text"])
            self.assertEqual(evidence[0]["date"], notes[0]["date"])
            self.assertEqual(c["echo_sentences"], 1)
            r.evidence(c, "a1c")
            self.assertEqual(r.calls, 1)


if __name__ == "__main__":
    unittest.main()
