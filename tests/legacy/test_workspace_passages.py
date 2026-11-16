"""Workspace passage index: incremental FTS5 sync, lazy dense vectors, scoped search in the warehouse_retrieval shape."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import numpy as np

from clinical_core.agere.clinical import workspace_passages as WP

AS_OF = '2030-05-27'


class BagEncoder:
    """Deterministic hashed bag-of-words vectors (384-d, normalized); counts what it encodes."""
    identity = 'bag-encoder/1'

    def __init__(self):
        self.encoded = 0

    def encode(self, texts, batch_size=4):
        out = np.zeros((len(texts), WP.DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in t.lower().split():
                out[i, int(hashlib.md5(w.strip('.,:').encode()).hexdigest(), 16) % WP.DIM] += 1
        self.encoded += len(texts)
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)


def _rec(rid, pid, stream, day, **f):
    return {'record_id': rid, 'patient_id': pid, 'source_table': stream, 'source_system': 'epic:X', 'organization_id': 'ORG',
            'event_date': day, 'available_date': day, 'synthetic': True, 'identity_status': 'matched', **f}


def pipeline(d, records):
    d = Path(d); d.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(d / 'stack.sqlite3')
    db.executescript('CREATE TABLE IF NOT EXISTS patients(id TEXT PRIMARY KEY, payload TEXT);'
                     'CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, patient TEXT, stream TEXT, payload TEXT);')
    for r in records:
        db.execute('INSERT OR IGNORE INTO patients VALUES (?,?)', (r['patient_id'], '{}'))
        db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)', (r['record_id'], r['patient_id'], r['source_table'], json.dumps(r)))
    db.commit(); db.close()
    (d / 'run.json').write_text(json.dumps({'source_digest': 'x', 'source_run': 'x', 'snapshot': {'release': 't', 'as_of': AS_OF}}))
    return d


LONG = ('Patient reports worsening shortness of breath on exertion. ' * 20 + 'Started furosemide for heart failure. ' +
        'Follow up with cardiology in two weeks. ' * 10)
RECORDS = [
    _rec('n1', 'P1', 'notes', '2029-01-05', text=LONG, note_type='progress_note'),
    _rec('n2', 'P2', 'notes', '2029-03-01', text='Cannot afford insulin; financial assistance referral placed.'),
    _rec('l1', 'P1', 'labs', '2029-02-01', test='HEMOGLOBIN A1C', value=8.1, unit='%'),
    _rec('l2', 'P3', 'labs', '2029-06-01', test='CREATININE', value=2.4, unit='mg/dL'),
    _rec('d1', 'P3', 'scanned_documents', '2029-07-01', document_type='Outside Records', has_text_layer=False, ocr_text=None),
]


class WindowTests(unittest.TestCase):
    def test_windows_cover_text_with_offsets(self):
        w = WP.windows(LONG)
        self.assertGreater(len(w), 1)
        self.assertEqual(w[0][0], 0); self.assertEqual(w[-1][1], len(LONG))
        self.assertTrue(all(b - a <= WP.WINDOW for a, b in w))
        self.assertTrue(all(w[i + 1][0] < w[i][1] for i in range(len(w) - 1)))  # overlap, no gaps
        self.assertEqual(WP.windows('   '), [])

    def test_parts_split_structured_from_narrative(self):
        p = WP.parts(RECORDS[2])
        self.assertEqual([x['kind'] for x in p], ['structured_rendering'])
        self.assertIn('test=HEMOGLOBIN A1C', p[0]['text']); self.assertNotIn('record_id', p[0]['text'])
        n = WP.parts(RECORDS[1])
        self.assertEqual([(x['field'], x['dense']) for x in n if x['kind'] != 'structured_rendering'], [('text', True)])
        ex = WP.parts(RECORDS[4], {'text': 'ECHO REPORT ejection fraction 30%', 'method': 'apple_vision_accurate'})
        self.assertEqual(ex[-1]['kind'], 'extracted_text'); self.assertTrue(ex[-1]['dense'])


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pipeline(Path(self.tmp.name) / 'pipeline', RECORDS)

    def tearDown(self):
        self.tmp.cleanup()

    def test_update_is_incremental_and_collects_removed_records(self):
        first = WP.update(self.path, page=2)
        self.assertEqual(first['status'], 'updated'); self.assertEqual(first['records'], 5); self.assertEqual(first['reindexed'], 5)
        self.assertEqual(first['embedded_contents'], 0)  # vectors are lazy
        self.assertEqual(WP.update(self.path)['status'], 'current')
        db = sqlite3.connect(self.path / 'stack.sqlite3')
        db.execute("UPDATE records SET payload=? WHERE id='n2'", (json.dumps({**RECORDS[1], 'text': 'Patient now on metformin.'}),))
        db.execute("DELETE FROM records WHERE id='l2'"); db.commit(); db.close()
        again = WP.update(self.path)
        self.assertEqual((again['reindexed'], again['removed'], again['records']), (1, 1, 4))
        self.assertGreaterEqual(again['orphan_contents_collected'], 2)
        idx = WP.PassageIndex(self.path)
        try:
            self.assertEqual(idx.search('afford insulin')['evidence'], [])
            self.assertEqual(idx.search('creatinine')['evidence'], [])
            self.assertEqual(idx.search('metformin')['evidence'][0]['source']['record_id'], 'n2')
        finally:
            idx.close()

    def test_partial_update_resumes(self):
        part = WP.update(self.path, page=2, max_records=2)
        self.assertEqual(part['status'], 'partial'); self.assertEqual(part['records'], 2)
        done = WP.update(self.path, page=2)
        self.assertEqual((done['status'], done['records'], done['reindexed']), ('updated', 5, 3))

    def test_lexical_search_keeps_offsets_and_scope(self):
        idx = WP.PassageIndex(self.path)
        try:
            r = idx.search('furosemide heart failure')
            self.assertEqual(r['schema'], WP.VERSION); self.assertEqual(r['new_paid_api_calls'], 0)
            top = r['evidence'][0]; s = top['source']
            self.assertEqual(s['record_id'], 'n1'); self.assertIn('furosemide', top['text'])
            self.assertEqual(LONG[s['start']:s['end']], top['text'])  # source offsets reproduce the quote
            self.assertTrue(idx.search('furosemide heart failure')['cache_hit'])
            self.assertEqual(idx.search('furosemide', patient_ids=['P2'])['evidence'], [])
            self.assertEqual(idx.search('A1C', patient='P1')['evidence'][0]['source']['record_id'], 'l1')
            win = {'basis': 'source_event', 'start': '2029-02-15', 'end': '2029-12-31'}
            self.assertEqual({e['source']['record_id'] for e in idx.search('hemoglobin financial insulin', analysis_window=win)['evidence']}, {'n2'})
            with self.assertRaises(ValueError):
                idx.search('x', analysis_window={'basis': 'clinical', 'start': '', 'end': ''})
            with self.assertRaises(ValueError):
                idx.search('x', patient='NOPE')
        finally:
            idx.close()

    def test_dense_vectors_are_lazy_and_bounded(self):
        enc = BagEncoder()
        idx = WP.PassageIndex(self.path, encoder=enc)
        try:
            r = idx.search('shortness of breath on exertion')
            self.assertGreater(r['index']['embedded_this_query'], 0)
            self.assertLess(r['index']['embedded_contents'] + r['index']['embedded_this_query'], r['index']['contents'] + 1)
            self.assertEqual(r['evidence'][0]['source']['record_id'], 'n1')
            self.assertIn('MiniLM', r['method'])
        finally:
            idx.close()
        filled = WP.embed_pending(self.path, enc)
        self.assertEqual(filled['pending'], 0)
        db = WP.connect(self.path)
        try:  # only narrative windows get vectors; structured renderings stay lexical
            self.assertEqual(db.execute('SELECT count(*) FROM contents WHERE dense=0 AND vector IS NOT NULL').fetchone()[0], 0)
        finally:
            db.close()

    def test_extractions_land_in_the_index(self):
        WP.update(self.path)
        WP.add_extractions(self.path, [{'record_id': 'd1', 'patient_id': 'P3', 'text': 'ECHOCARDIOGRAM ejection fraction 30 percent',
                                        'method': 'apple_vision_accurate'}])
        out = WP.update(self.path)
        self.assertEqual(out['reindexed'], 1)
        idx = WP.PassageIndex(self.path)
        try:
            hit = idx.search('echocardiogram ejection fraction')['evidence'][0]
            self.assertEqual((hit['source']['record_id'], hit['source']['kind']), ('d1', 'extracted_text'))
            self.assertEqual(hit['source']['extraction_method'], 'apple_vision_accurate')
        finally:
            idx.close()


if __name__ == '__main__':
    unittest.main()
