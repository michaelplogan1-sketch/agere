"""Scanned-document text: engine chain (cheapest first, skip on failure), v2.3 warehouse OCR table, landing parser,
workspace passage binding, and a real Apple Vision read when the machine and a synthetic fax PDF are present."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.ingest import ocr, documents as doc_parser, landing
from test_warehouse_v23 import pdf, v23_release

FAX = Path(__file__).resolve().parent / 'artifacts/healthcare-warehouse-v2.8-sample-0.30/documents/outside/WHN-DOC-001abc117418b92519c9.pdf'
if not FAX.exists():
    FAX = Path('/Users/mikel/clinical-insights-local/artifacts/healthcare-warehouse-v2.8-sample-0.30/documents/outside/WHN-DOC-001abc117418b92519c9.pdf')


def vision_stub(text):
    return lambda path: (text, [{'page': 1, 'method': 'apple_vision_accurate', 'text': text, 'confidence': 0.9}], None)


def broken(path):
    raise RuntimeError('engine crashed')


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_text_layer_wins_without_ocr(self):
        p = self.d / 'a.pdf'; p.write_bytes(pdf(['CONSULT LETTER', 'Date of service: 03/01/2028', 'Stop atorvastatin 20 mg due to muscle aches.']))
        got = ocr.read_document(p, engines_override={'apple_vision': broken})
        self.assertEqual((got['status'], got['engine']), ('text_layer', 'pdf_text_layer'))
        self.assertIn('atorvastatin', got['text']); self.assertEqual(len(got['attempts']), 1)

    def test_image_only_falls_through_failing_engines(self):
        p = self.d / 'scan.pdf'; p.write_bytes(pdf(image=True))
        got = ocr.read_document(p, engines_override={'apple_vision': broken, 'chandra': vision_stub('ECHO REPORT: LVEF 30 percent, severe MR noted.'),
                                                     'tesseract': broken})
        self.assertEqual((got['status'], got['engine']), ('ocr', 'chandra'))
        self.assertEqual([a['status'] for a in got['attempts']], ['empty', 'skipped', 'ok'])  # stops at the first sufficient engine
        self.assertFalse(got['clinical_correctness_verified'])

    def test_short_result_keeps_the_longest_and_never_raises(self):
        p = self.d / 'scan.png'; p.write_bytes(b'\x89PNG fake')
        got = ocr.read_document(p, engines_override={'apple_vision': vision_stub('EF 30'), 'chandra': broken, 'tesseract': broken})
        self.assertEqual((got['status'], got['text']), ('ocr', 'EF 30'))
        none = ocr.read_document(p, engines_override={'apple_vision': broken, 'chandra': broken, 'tesseract': broken})
        self.assertEqual(none['status'], 'empty')
        self.assertEqual(ocr.read_document(self.d / 'nope.pdf')['status'], 'missing')
        (self.d / 'x.docx').write_text('x'); self.assertEqual(ocr.read_document(self.d / 'x.docx')['status'], 'unsupported')

    def test_engines_report_availability(self):
        names = [e['engine'] for e in ocr.engines()]
        self.assertEqual(names, list(ocr.CHAIN))
        with patch.object(ocr.importlib.util, 'find_spec', return_value=None):
            self.assertFalse(next(e for e in ocr.engines() if e['engine'] == 'chandra')['available'])

    def test_document_path_stays_inside_documents(self):
        self.assertIsNone(ocr._document_path(self.d, 'private/eval.pdf'))
        self.assertIsNone(ocr._document_path(self.d, 'documents/../private/x.pdf'))
        self.assertEqual(ocr._document_path(self.d, 'documents/outside/a.pdf'), (self.d / 'documents/outside/a.pdf').resolve())


class WiringTests(unittest.TestCase):
    def test_v23_image_only_documents_get_ocr_rows_and_extractions(self):
        from build_healthcare_warehouse_stack import ingest as build_ingest
        from clinical_core.agere.warehouse import Warehouse
        d = Path(tempfile.mkdtemp()); v23_release(d)
        w = Warehouse(d / 'derived.sqlite3')
        try: rel, _ = build_ingest(w, d)
        finally: w.close()
        calls = []
        def reader(path):
            calls.append(path.name)
            return ocr.read_document(path, engines_override={'apple_vision': vision_stub('OUTSIDE RECORDS: no acute process on chest film.')})
        out = ocr.ocr_scanned_documents(d / 'derived.sqlite3', d, rel, reader=reader)
        self.assertEqual(out['by_status'], {'ocr': 2}); self.assertEqual(calls, ['DOC-IMAGE.pdf'])  # one read per shared file
        self.assertEqual(ocr.ocr_scanned_documents(d / 'derived.sqlite3', d, rel, reader=reader)['records'], 0)  # resume: nothing left
        ex = ocr.extractions(d / 'derived.sqlite3', rel)
        self.assertEqual({e['record_id'] for e in ex}, {'sd2', 'sd3'}); self.assertEqual(ex[0]['method'], 'apple_vision')
        self.assertEqual(ocr.write_extractions(d / 'derived.sqlite3', rel, d / 'ex.json'), 2)
        self.assertEqual(len(json.loads((d / 'ex.json').read_text())['records']), 2)

    def test_landing_parser_lands_document_text(self):
        self.assertIn('documents', [m.__name__.rsplit('.', 1)[-1] for m in landing.parsers()])
        raw = Path(tempfile.mkdtemp()) / 'raw'; (raw / 'outside_docs' / '2029-06').mkdir(parents=True)
        (raw / 'outside_docs' / '2029-06' / 'letter.pdf').write_bytes(pdf(['CONSULT LETTER', 'Date of service: 03/01/2028', 'Plan: start lisinopril.']))
        land = raw.parent / 'landing.sqlite'
        stats = landing.ingest(raw, land)
        self.assertEqual(stats['by_parser'].get('documents'), 1)
        db = sqlite3.connect(land)
        row = db.execute('SELECT status, engine, text, date_of_service, _feed FROM document_text').fetchone()
        self.assertEqual(row[:2], ('text_layer', 'pdf_text_layer')); self.assertIn('lisinopril', row[2])
        self.assertEqual((row[3], row[4]), ('2028-03-01', 'outside_docs'))

    def test_workspace_documents_bind_into_passage_index(self):
        from test_workspace_passages import pipeline, _rec
        from clinical_core.agere.clinical import workspace_passages as WP
        root = Path(tempfile.mkdtemp()); (root / 'documents' / 'outside').mkdir(parents=True)
        (root / 'documents' / 'outside' / 'S1.pdf').write_bytes(pdf(image=True))
        path = pipeline(root / 'pipeline', [
            _rec('d1', 'P1', 'scanned_documents', '2029-07-01', relative_path='documents/outside/S1.pdf', has_text_layer=False, ocr_text=None),
            _rec('d2', 'P2', 'scanned_documents', '2029-07-02', relative_path='documents/outside/S2.pdf', ocr_text='front office text')])
        WP.update(path)
        reader = lambda p: ocr.read_document(p, engines_override={'apple_vision': vision_stub('OPERATIVE REPORT right knee arthroscopy meniscectomy')})
        out = ocr.index_workspace_documents(path, root, reader=reader)
        self.assertEqual((out['documents'], out['bound']), (1, 1)); self.assertEqual(out['index']['reindexed'], 1)
        idx = WP.PassageIndex(path)
        try:
            hit = idx.search('knee arthroscopy')['evidence'][0]['source']
            self.assertEqual((hit['record_id'], hit['kind'], hit['extraction_method']), ('d1', 'extracted_text', 'apple_vision'))
        finally:
            idx.close()
        self.assertEqual(ocr.index_workspace_documents(path, root, reader=reader)['documents'], 0)


@unittest.skipUnless(os.uname().sysname == 'Darwin' and FAX.is_file(), 'needs macOS and the synthetic v2.8 fax sample')
class AppleVisionSmoke(unittest.TestCase):
    def test_real_image_only_fax_is_read(self):
        got = ocr.read_document(FAX, chain=('pdf_text_layer', 'apple_vision'))
        self.assertEqual((got['status'], got['engine']), ('ocr', 'apple_vision'))
        self.assertIn('OPERATIVE REPORT', got['text'])


if __name__ == '__main__':
    unittest.main()
