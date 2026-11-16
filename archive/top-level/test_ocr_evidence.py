"""OCR evidence path: page/bbox spans, the side passage store, the scanned_document_search engine and its question-path wiring."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.ingest import ocr
from clinical_core.agere.clinical import ocr_passages as OP, scanned_documents as SD

ECHO_LOW = ('SYNTHETIC IMAGING\nEXAM: Transthoracic echocardiogram, complete\n\nFINDINGS: global LV syst0lic function reduced. '
            'Visually estimated LVEF 30-35%.\n\nIMPRESSION: LVEF 30-35%.')
ECHO_EDGE = 'EXAM: Transthoracic echocardiogram\n\nFINDINGS: Visually estimated LVEF 35-40%.'
ECHO_NORMAL = 'EXAM: Transthoracic echocardiogram\n\nFINDINGS: Visually estimated LVEF 60-65%.'
CT = 'EXAM: CT chest\n\nIMPRESSION: 4 mm right lower lobe pulmonary n0dule.'
TWO_PAGES = 'FAX COVER\n\nPage one text.\fEXAM: XRAY\n\nIMPRESSION: Possible nodule.'


def fixture(tmp):
    db = sqlite3.connect(Path(tmp) / 'semantic.sqlite3')
    db.executescript("""CREATE TABLE scanned_document(record_id, patient_id, document_id, document_type, received_via, event_date,
      available_date, page_count, review_status); CREATE TABLE scanned_document_text(record_id, patient_id, text_source, text);""")
    docs = [('R1', 'P1', 'D1', 'Radiology Report', 'fax', '2028-06-01', '2028-06-03', 1, ECHO_LOW),
            ('R2', 'P2', 'D2', 'Radiology Report', 'fax', '2028-07-01', '2028-07-02', 1, ECHO_EDGE),
            ('R3', 'P3', 'D3', 'Radiology Report', 'portal_upload', '2028-08-01', '2028-08-02', 1, ECHO_NORMAL),
            ('R4', 'P1', 'D4', 'Radiology Report', 'fax', '2029-01-01', '2029-01-02', 2, CT),
            ('R5', 'P4', 'D5', 'Outside Records', 'fax', '2029-02-01', '2029-02-02', 2, TWO_PAGES),
            ('R6', 'P5', 'D6', 'Radiology Report', 'fax', '2031-01-01', '2031-01-02', 1, CT)]  # after the as-of date
    for rid, pid, did, t, via, ev, av, pages, text in docs:
        db.execute('INSERT INTO scanned_document VALUES (?,?,?,?,?,?,?,?,?)', (rid, pid, did, t, via, ev, av, pages,
                                                                            'reviewed' if rid == 'R1' else 'unreviewed'))
        db.execute('INSERT INTO scanned_document_text VALUES (?,?,?,?)', (rid, pid, 'front_office_ocr', text))
    db.commit(); db.close()
    return Path(tmp)


class Spans(unittest.TestCase):
    def test_vision_regions_give_boxes_and_offsets(self):
        pages = [{'page': 1, 'text': 'EXAM: echo\nLVEF 30%', 'regions': [
            {'text': 'EXAM: echo', 'bbox': ocr.vision_bbox([0.1, 0.8, 0.5, 0.05])}, {'text': 'LVEF 30%', 'bbox': ocr.vision_bbox([0.1, 0.7, 0.3, 0.05])}]},
                 {'page': 2, 'text': 'Signed', 'regions': []}]
        spans = ocr.anchor(ocr.page_spans(pages, 'apple_vision'), 'EXAM: echo\nLVEF 30%\n\nSigned')
        self.assertEqual(spans[0]['page'], 1)
        self.assertEqual(spans[0]['bbox'], [0.1, 0.15, 0.6, 0.3])  # union, top-left origin
        self.assertEqual(spans[1], {'page': 2, 'text': 'Signed', 'bbox': None, 'engine': 'apple_vision', 'char_start': 21, 'char_end': 27})

    def test_read_document_carries_spans(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.pdf'; path.write_bytes(b'%PDF-1.4')
            fake = lambda p: ('First page text long enough to pass the minimum.\n\nSecond paragraph.',
                              [{'page': None, 'method': 'pdf_text_layer', 'text': 'First page text long enough to pass the minimum.\n\nSecond paragraph.'}], None)
            got = ocr.read_document(path, chain=('pdf_text_layer',), engines_override={'pdf_text_layer': fake})
        self.assertEqual([s['page'] for s in got['spans']], [None, None])  # the text layer reported no page number: none invented
        self.assertEqual(got['text'][got['spans'][1]['char_start']:got['spans'][1]['char_end']], 'Second paragraph.')

    def test_page_rule(self):
        self.assertEqual({p for p, _, _ in OP.document_spans('a\n\nb', 1)}, {1})
        self.assertEqual({p for p, _, _ in OP.document_spans('a\n\nb', 2)}, {None})       # no page break in the text
        self.assertEqual([p for p, _, _ in OP.document_spans('a\fb', 2)], [1, 2])

    def test_normalize_undoes_ocr_digits_only_in_words(self):
        self.assertEqual(OP.normalize('syst0lic norma1 E11319 LVEF 55-60%'), 'systolic normal e11319 lvef 55-60%')


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = fixture(self.tmp.name)

    def tearDown(self): self.tmp.cleanup()

    def test_build_search_and_citations(self):
        hits = OP.search(self.path, ['systolic'])  # OCR 'syst0lic'
        self.assertEqual([h['record_id'] for h in hits], ['R1'])
        self.assertIn('R1 page 1, chars', hits[0]['citation'])
        two = OP.search(self.path, ['nodule'], patient_ids=['P4'])
        self.assertEqual(two[0]['page'], 2)
        self.assertEqual(len(OP.search(self.path, ['nodule'], as_of='2030-05-27')), 2)  # R6 not yet available

    def test_ingest_spans_with_boxes(self):
        n = OP.add_documents(self.path, [{'record_id': 'X1', 'patient_id': 'P9', 'text': 'Thyroid nodule 1.4 cm', 'method': 'apple_vision',
                                          'spans': [{'page': 3, 'text': 'Thyroid nodule 1.4 cm', 'bbox': [0.1, 0.2, 0.5, 0.25],
                                                     'engine': 'apple_vision', 'char_start': 0, 'char_end': 21}]}])
        self.assertEqual(n, 1)
        h = OP.search(self.path, ['thyroid nodule'], patient_ids=['P9'])[0]
        self.assertEqual((h['page'], h['bbox']), (3, [0.1, 0.2, 0.5, 0.25]))

    def test_atomic_evidence_and_retrieval_citations(self):
        passages = {}
        ev = OP.atomic_evidence(self.path, 'P1', 'Is the ejection fraction reduced (LVEF)?', passages)
        self.assertTrue(ev and ev[0]['note_id'] == 'R1' and 'page 1' in ev[0]['text'])
        self.assertEqual(passages[ev[0]['id']]['page'], 1)
        cited = OP.cite_evidence(self.path, [{'source': {'record_id': 'R4'}, 'text': 'IMPRESSION: 4 mm right lower lobe pulmonary n0dule.'},
                                             {'source': {'record_id': 'NOTE-1'}, 'text': 'not a scan'}])
        self.assertEqual([c['record_id'] for c in cited], ['R4'])
        self.assertIsNone(cited[0]['page'])  # two-page document without a page break


class Engine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = fixture(self.tmp.name)

    def tearDown(self): self.tmp.cleanup()

    def run_q(self, q):
        g = SD.local_question(q)
        self.assertIsNotNone(g, q)
        report = SD.execute(self.path, None, g['nodes'][0]['parameters'], '2030-05-27')
        return g, report, SD.answer(report, g, q)

    def test_echo_ef_cut(self):
        g, report, a = self.run_q('Which patients have a faxed echo report showing an ejection fraction below 40?')
        self.assertEqual(g['nodes'][0]['parameters']['measurement'], {'name': 'lvef', 'op': '<', 'value': 40, 'unit': 'percent'})
        self.assertEqual(a['patient_ids'], ['P1'])                       # 30-35%
        self.assertEqual([d['record_id'] for d in report['indeterminate']], ['R2'])  # 35-40% straddles '< 40'
        self.assertIn('page 1', a['table'][0]['citation'])

    def test_count_documents_ocr_tolerant(self):
        _, _, a = self.run_q('How many scanned radiology reports mention a nodule?')
        self.assertEqual((a['answer_type'], a['count']), ('count', 1))   # R4 'n0dule'; R6 after as-of; R5 is Outside Records

    def test_review_state_question(self):
        g, _, a = self.run_q('How many faxed radiology reports are unreviewed?')
        p = g['nodes'][0]['parameters']
        self.assertEqual((p['review_status'], p['channels'], p['document_types'], p['terms']), ('unreviewed', ['fax'], ['Radiology Report'], []))
        self.assertEqual(a['count'], 2)                                  # R2, R4 (R1 reviewed, R3 portal, R6 after as-of)

    def test_exam_word_is_a_filter_not_a_term(self):
        g = SD.local_question('Which scanned documents mention a mammogram BI-RADS 4?')
        self.assertEqual((g['nodes'][0]['parameters']['exam'], g['nodes'][0]['parameters']['terms']), ('mammogra', ['bi-rads 4']))

    def test_declines_what_it_does_not_parse(self):
        for q in ('How many scanned reports do not mention a nodule?', 'Which patients have an EF below 40%?',
                  'How many faxed reports mention a nodule in the last year?', 'How many patients have diabetes?'):
            self.assertIsNone(SD.local_question(q), q)

    def test_registered_on_the_question_path(self):
        from clinical_core.agere.clinical import analysis_graph, capability_registry, graph_planner, economical_planner
        self.assertIn('scanned_document_search', analysis_graph.OPERATIONS)
        self.assertIn('scanned_document_search', capability_registry.PRODUCT)
        self.assertIn('scanned_document_search', graph_planner.CONTRACTS)
        graph, route = economical_planner.local_plan('How many scanned radiology reports mention a nodule?', '2030-05-27')
        self.assertEqual(route, 'exact_foundation_scanned_document_search')
        self.assertEqual(graph['nodes'][0]['operation'], 'scanned_document_search')
        from clinical_core.agere.clinical.literal_check import missing, executable_text, shape_misfit
        self.assertEqual(missing('How many scanned radiology reports mention a nodule?',
                                 executable_text([n.get('parameters') for n in graph['nodes']])), [])


if __name__ == '__main__':
    unittest.main()
