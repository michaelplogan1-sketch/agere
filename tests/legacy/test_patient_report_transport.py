"""Large complete reports stay portable without dropping evidence or changing exports."""
import base64
import gzip
import json
import re
import unittest

from clinical_core.agere.deliverables import patient_dashboard as H


class ReportTransport(unittest.TestCase):
    def context(self, large=False):
        note = 'Renée — source text </script> & full evidence. ' * (30000 if large else 1)
        return {'id': 'ANS-fixture', 'question': 'Review the saved evidence',
                'source': {'as_of': '2030-05-27'},
                'answer': {'status': 'partial', 'missing': ['Source interpretation remains unresolved'],
                           'table': [{'patient_id': 'A', 'received_note': note},
                                     {'patient_id': 'B', 'received_note': 'Other patient remains present'}]}}

    def embedded(self, body):
        return json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', body, re.S).group(1))

    def test_large_report_round_trips_complete_unicode_and_source_scope(self):
        context = self.context(large=True)
        expected = H.payload(context)
        body = H.render(context)
        packed = self.embedded(body)
        self.assertEqual(packed['encoding'], 'gzip+base64')
        decoded = json.loads(gzip.decompress(base64.b64decode(packed['data'])))
        self.assertEqual(decoded, expected)
        self.assertEqual(decoded['answer'], context['answer'])
        self.assertEqual([r['patient_id'] for r in decoded['rows']], ['A', 'B'])
        self.assertLess(len(body.encode()), len(json.dumps(expected).encode()) / 10)
        self.assertNotIn('source text </script>', body)
        self.assertEqual(body.count('</script>'), 2)
        self.assertIn("JSON.stringify(D,null,2)", body)

    def test_small_report_remains_direct_json_and_escapes_source_markup(self):
        context = self.context()
        body = H.render(context)
        self.assertEqual(self.embedded(body), H.payload(context))
        self.assertNotIn('source text </script>', body)
        self.assertEqual(body.count('</script>'), 2)


if __name__ == '__main__':
    unittest.main()
