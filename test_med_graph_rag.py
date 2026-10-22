"""MedGraphRAG challenger: concept graph over a mini stack, graph retrieval with source offsets, no second truth store."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import med_graph_rag as MG


def mini(root, n=20):
    root = Path(root)
    (root / 'run.json').write_text(json.dumps({'source_digest': 'd1'}))
    se = sqlite3.connect(root / 'semantic.sqlite3')
    se.executescript('''CREATE TABLE condition(record_id TEXT, patient_id TEXT, concept TEXT, label TEXT, normalized_label TEXT, icd10_category TEXT);
        CREATE TABLE medication(record_id TEXT, patient_id TEXT, drug_class TEXT, drug_name TEXT);
        CREATE TABLE measurement(record_id TEXT, patient_id TEXT, concept TEXT, name TEXT);''')
    st = sqlite3.connect(root / 'stack.sqlite3')
    st.executescript('''CREATE TABLE records(id TEXT, patient TEXT, stream TEXT, payload TEXT);
        CREATE TABLE contents(id INTEGER PRIMARY KEY, text TEXT);
        CREATE TABLE passages(id INTEGER PRIMARY KEY, patient TEXT, content INTEGER, source TEXT);''')
    pid_i = 0
    for i in range(n):
        p = f'P{i}'
        se.execute("INSERT INTO condition VALUES (?,?,?,?,?,?)", (f'c{i}', p, 'diabetes', 'Type 2 diabetes', None, 'E11'))
        se.execute("INSERT INTO medication VALUES (?,?,?,?)", (f'm{i}', p, 'biguanide', 'metformin 500 mg'))
        se.execute("INSERT INTO measurement VALUES (?,?,?,?)", (f'l{i}', p, 'hba1c', 'Hemoglobin A1c'))
        text = f'Patient {p}: metformin stopped for GI intolerance; A1c reviewed.' if i % 2 == 0 else f'Patient {p}: routine visit, no concerns.'
        st.execute("INSERT INTO records VALUES (?,?,?,?)", (f'n{i}', p, 'notes', json.dumps({'record_id': f'n{i}', 'patient_id': p, 'source_table': 'notes', 'text': text})))
        for rid in (f'c{i}', f'm{i}', f'l{i}', f'n{i}'):
            pid_i += 1
            st.execute('INSERT INTO contents VALUES (?,?)', (pid_i, text if rid.startswith('n') else f'structured {rid}'))
            st.execute('INSERT INTO passages VALUES (?,?,?,?)', (pid_i, p, pid_i, json.dumps({'record_id': rid, 'start': 0, 'end': 10, 'event_date': '2029-05-0%d' % (1 + i % 9)})))
    se.commit(); st.commit(); se.close(); st.close()
    return root


class Graph(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = mini(self.tmp.name)

    def test_build_nodes_links_edges_and_cache(self):
        r = MG.build(self.root)
        self.assertGreaterEqual(r['nodes'], 3); self.assertGreater(r['links'], 60); self.assertEqual(r['patients'], 20)
        again = MG.build(self.root)
        self.assertEqual(again['source_digest'], r['source_digest'])
        db = sqlite3.connect(self.root / MG.FILE)
        nodes = {x[0] for x in db.execute('SELECT id FROM node')}
        self.assertTrue({'cond:diabetes', 'drug:biguanide', 'lab:hba1c'} <= nodes)
        self.assertTrue(db.execute("SELECT 1 FROM link WHERE basis='text_mention' AND node='ing:metformin'").fetchone())  # narrative mentions link too

    def test_search_returns_stack_passages_with_offsets_scoped_to_patient(self):
        MG.build(self.root)
        g = MG.GraphRetriever(self.root)
        try:
            out = g.search('why was metformin stopped', patient='P0', limit=10)
            self.assertIn('ing:metformin', out['nodes'])
            self.assertTrue(out['candidates'])
            for c in out['candidates']:
                self.assertEqual(c['source']['record_id'][0] in 'cmln', True); self.assertIn('start', c['source'])
                self.assertEqual(c['protected'], False); self.assertGreater(c['graph_score'], 0)
            self.assertTrue(all(c['source']['record_id'].endswith('0') or c['source']['record_id'][1:] == '0' for c in out['candidates']))
            self.assertEqual(g.search('nothing medical here', patient='P0')['candidates'], [])
            scoped = g.search('metformin', patient_ids=['P1', 'P2'], limit=24)
            self.assertTrue({c['source']['record_id'] for c in scoped['candidates']} <= {'c1', 'm1', 'l1', 'n1', 'c2', 'm2', 'l2', 'n2'})
        finally:
            g.close()

    def test_rebuild_when_digest_changes(self):
        MG.build(self.root)
        (self.root / 'run.json').write_text(json.dumps({'source_digest': 'd2'}))
        r = MG.build(self.root)
        self.assertEqual(r['source_digest'], 'd2')


if __name__ == '__main__':
    unittest.main()
