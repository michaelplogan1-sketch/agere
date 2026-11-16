import json, tempfile, unittest
from pathlib import Path
import duckdb
from tools.export import training_parquet as tp


class ExportTests(unittest.TestCase):
    def test_export_split_denylist_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'task').mkdir()
            rows = [{'question': f'How many patients in cohort number {i} had a visit in 2029?', 'sql': f'select {i}', 'template_family': f'fam{i % 20}'}
                    for i in range(200)]
            rows.append({'question': 'How many patients with CKD stage 4 were hospitalised in 2027?', 'sql': 'select 1', 'template_family': 'bad'})
            (d / 'task' / 'all.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
            (d / 'task2').mkdir()
            (d / 'task2' / 'train.jsonl').write_text(json.dumps({'messages': [{'role': 'user', 'content': 'hello there friend'}, {'role': 'assistant', 'content': 'hi'}]}) + '\n')
            (d / 'task2' / 'heldout-x.jsonl').write_text(json.dumps({'messages': [{'role': 'user', 'content': 'second unseen prompt'}, {'role': 'assistant', 'content': 'ok'}]}) + '\n')
            (d / 'deny.json').write_text(json.dumps({'items': [{'question': 'how many patients with CKD stage 4 were hospitalised in 2027'}]}))
            (d / 'sealed.sha256').write_text(tp.digests('How many patients in cohort number 7 had a visit in 2029?').pop() + '\n')
            m = tp.build({'sql': d / 'task' / 'all.jsonl', 'chat': d / 'task2'}, d / 'out', [d / 'deny.json', d / 'sealed.sha256',
                         '/Users/mikel/agere-sealed-eval', d / 'missing'], release='r1')
            self.assertEqual(m['quarantined_count'], 2)   # normalized-text hit + hash-list hit
            self.assertEqual({q['list'] for q in m['quarantined']}, {'deny.json', 'sealed.sha256'})
            self.assertTrue(any('sealed' in s['path'] and 'never read' in s['why'] for s in m['denylist']['skipped']))
            self.assertEqual(sum(m['rows']['sql'].values()), 199)
            self.assertEqual(m['rows']['chat'], {'train': 1, 'test': 1})   # builder split kept; heldout -> test
            n = duckdb.connect().execute(f"select count(*), count(distinct group_key) from '{d}/out/examples/sql.parquet'").fetchone()
            self.assertEqual(n[0], 199)
            # a template family is deterministic: same group -> same split rule
            self.assertEqual(tp.split_for('fam3'), tp.split_for('fam3'))
            self.assertTrue((d / 'out' / 'mlx' / 'sql' / 'train.jsonl').exists())
            with self.assertRaises(tp.DenylistHit):
                tp.build({'sql': d / 'task' / 'all.jsonl'}, d / 'out2', [d / 'deny.json'], strict=True)

    def test_default_denylist_reads_repo_benchmarks(self):
        h, rep = tp.load_denylist(tp.DEFAULT_DENYLIST)
        self.assertGreater(len(h), 100)


if __name__ == '__main__':
    unittest.main()
