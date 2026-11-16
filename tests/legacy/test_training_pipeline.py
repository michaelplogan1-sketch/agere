"""Training pipeline: deterministic question factory, exact labels with provenance, template-family splits, gated retraining.

Runs on a tiny synthetic fixture warehouse (no active warehouse, no MLX, no network)."""
import json
import os
import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('AGERE_LOCAL_TYPED', 'off')
os.environ.setdefault('AGERE_LOCAL_NORMALIZER', 'off')

from tools.training import question_factory as F, label as Lb, datasets as D, train_all as T  # noqa: E402


def fixture(root):
    """A small semantic warehouse: patients, deaths, labs, latest labs and appointments."""
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'semantic.sqlite3')
    db.executescript('''
      CREATE TABLE meta(key TEXT, value TEXT);
      CREATE TABLE patient(patient_id TEXT, sex_reported TEXT, coverage_type TEXT, birth_date TEXT, payer_id TEXT,
                           preferred_language TEXT, state TEXT, race TEXT, ethnicity TEXT, pcp_clinician_id TEXT);
      CREATE TABLE death(patient_id TEXT, death_date TEXT);
      CREATE TABLE measurement(record_id TEXT, patient_id TEXT, concept TEXT, value_canonical REAL, event_date TEXT);
      CREATE TABLE measurement_latest(patient_id TEXT, concept TEXT, value_canonical REAL, event_date TEXT);
      CREATE TABLE appointment(appointment_id TEXT, patient_id TEXT, outcome TEXT, purpose TEXT, scheduled_date TEXT, lead_time_days INTEGER);
    ''')
    db.executemany('INSERT INTO meta VALUES (?,?)', [('as_of', '2030-05-27'), ('source_digest', 'fixture-digest'), ('version', 'fixture/1')])
    rng = random.Random(4)
    for i in range(60):
        pid = f'p{i:03d}'
        db.execute('INSERT INTO patient VALUES (?,?,?,?,?,?,?,?,?,?)', (pid, 'F' if i % 2 else 'M', ['medicaid', 'medicare', 'commercial'][i % 3],
                   f'{1940 + i % 70}-03-01', f'payer{i % 4}', 'English' if i % 5 else 'Spanish', 'TN', 'White', 'Not Hispanic', None))
        for concept, lo, hi in (('hba1c', 5, 12), ('egfr', 10, 110), ('ldl', 50, 220)):
            v = round(rng.uniform(lo, hi), 1)
            date = f'{2027 + i % 3}-0{1 + i % 9}-15'
            db.execute('INSERT INTO measurement VALUES (?,?,?,?,?)', (f'm{i}{concept}', pid, concept, v, date))
            db.execute('INSERT INTO measurement_latest VALUES (?,?,?,?)', (pid, concept, v, date))
        db.execute('INSERT INTO appointment VALUES (?,?,?,?,?,?)', (f'a{i}', pid, ['kept', 'cancelled', 'no_show'][i % 3],
                   ['follow-up', 'annual'][i % 2], f'{2027 + i % 3}-0{1 + i % 9}-10', 3 + i % 40))
    db.execute("INSERT INTO death VALUES ('p001','2029-06-01')")
    db.commit(); db.close()
    return root


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.stack = fixture(Path(cls.tmp.name) / 'stack')
        cls.vocab = F.Vocab(stack=cls.stack, out=Path(cls.tmp.name) / 'out')
        cls.ctx = Lb.Context(cls.stack, grammar_check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class FactoryTests(Base):
    def test_stream_is_deterministic_unique_and_paraphrased(self):
        from clinical_core.agere.clinical import local_normalizer as N
        a = list(F.generate(80, seed=3, vocab=self.vocab))
        b = list(F.generate(80, seed=3, vocab=self.vocab))
        c = list(F.generate(80, seed=4, vocab=self.vocab))
        self.assertEqual([x['id'] for x in a], [x['id'] for x in b])
        self.assertNotEqual([x['id'] for x in a], [x['id'] for x in c])
        self.assertEqual(len({x['id'] for x in a}), 80)
        for item in a:
            self.assertTrue(item['template_family'].startswith(item['family'] + '/'))
            self.assertGreaterEqual(len(item['paraphrases']), 3); self.assertLessEqual(len(item['paraphrases']), 10)
            if not item.get('chain'):
                for p in item['paraphrases']:
                    self.assertIsNone(N.guard(p['text'], item['canonical']), (p, item['canonical']))

    def test_every_family_generates(self):
        for name in F.FAMILIES:
            items = list(F.generate(3, seed=1, vocab=self.vocab, families=[name]))
            self.assertTrue(items, name)
            self.assertEqual({i['request_source'] for i in items}, {F.FAMILIES[name][2]})

    def test_typed_requests_compile_with_literal_quotes(self):
        from clinical_core.agere.clinical import request_plan as R
        items = [i for i in F.generate(60, seed=2, vocab=self.vocab, families=['lab_threshold', 'table_aggregate'])
                 if not (i['slots'] or {}).get('condition')]
        compiled = 0
        for i in items:
            try:
                R.compile_request(self.vocab.catalog, i['canonical'], i['request']['value']); compiled += 1
            except R.PlanError as exc:
                self.fail(f"{i['canonical']}: {exc}")
        self.assertGreater(compiled, 20)

    def test_shards_and_manifest(self):
        out = Path(self.tmp.name) / 'q-shards'
        m = F.write_shards(out, 25, seed=9, shard_size=10, vocab=self.vocab)
        self.assertEqual((m['n'], m['shards']), (25, 3))
        self.assertEqual(sum(len(p.read_text().splitlines()) for p in out.glob('questions-*.jsonl')), 25)

    def test_private_paths_are_refused(self):
        with self.assertRaises(ValueError): F.assert_public('/Users/x/repo/private/truth.json')
        with self.assertRaises(ValueError): F.assert_public('/Users/mikel/agere-sealed-eval/a.json')
        F.assert_public('/private/tmp/ok')  # the macOS root alias is not a private/ directory

    def test_llm_hook_is_off_without_flag_or_key(self):
        self.assertIsNone(F.llm_paraphrase_hook(False))
        old = os.environ.pop('ANTHROPIC_API_KEY', None)
        try: self.assertIsNone(F.llm_paraphrase_hook(True))
        finally:
            if old: os.environ['ANTHROPIC_API_KEY'] = old


class LabelTests(Base):
    def item(self, canonical, request=None, family='lab_threshold', source='typed'):
        return {'id': F.stable(canonical)[:24], 'family': family, 'form': 'f', 'template_family': f'{family}/f', 'canonical': canonical,
                'request_source': source, 'request': request, 'paraphrases': [{'text': canonical.lower(), 'styles': ['casing']}]}

    def test_typed_lab_label_is_exact_with_provenance(self):
        q = 'How many women had an HbA1c above 9 in 2029?'
        req = F.REQ('patient', 'women', [{'kind': 'count_distinct', 'alias': 'patients', 'quote': 'How many', 'field': 'patient.patient_id'}],
                    F.AND([F.P('patient.sex_reported', '=', 'F', 'women'),
                           F.EXISTS('measurement', F.AND([F.P('measurement.concept', '=', 'hba1c', 'HbA1c'),
                                                          F.P('measurement.value_canonical', '>', 9, 'above 9'),
                                                          *F.YEAR('measurement.event_date', '2029', 'in 2029')], 'HbA1c above 9 in 2029'),
                                    'HbA1c above 9 in 2029')], q))
        row = Lb.label_item(self.ctx, self.item(q, {'kind': 'request', 'value': req}))
        self.assertEqual(row['status'], 'ok', row)
        with sqlite3.connect(self.stack / 'semantic.sqlite3') as db:
            want = db.execute("SELECT count(DISTINCT p.patient_id) FROM patient p JOIN measurement m USING(patient_id) WHERE p.sex_reported='F' "
                              "AND m.concept='hba1c' AND m.value_canonical>9 AND m.event_date>='2029-01-01' AND m.event_date<'2030-01-01'").fetchone()[0]
        self.assertEqual(row['answer']['value'], want)
        self.assertIn('measurement', row['provenance']['tables'])
        self.assertIn('measurement.value_canonical', row['provenance']['columns'])
        self.assertIn('SELECT', row['provenance']['query'])

    def test_grammar_item_and_rejections(self):
        ok = Lb.label_item(self.ctx, self.item('How many women are there?', family='demographics', source='grammar'))
        self.assertEqual(ok['status'], 'ok', ok)
        self.assertEqual(ok['answer']['value'], 29)  # 30 women on the roster, one of them deceased: patients are living patients (decision context)
        self.assertTrue(ok['grammar_reads'])
        bad = Lb.label_item(self.ctx, self.item('Blorp the frobnicated widgets sideways', family='vbc_seed', source='grammar'))
        self.assertEqual(bad['status'], 'not_parsed')
        broken = Lb.label_item(self.ctx, self.item('How many women?', {'kind': 'request', 'value': {'source': {'table': 'nope', 'quote': 'women'}}}))
        self.assertEqual(broken['status'], 'compile_error')

    def test_units_are_resumable_and_summarized(self):
        qdir, ldir = Path(self.tmp.name) / 'q-units', Path(self.tmp.name) / 'l-units'
        F.write_shards(qdir, 12, seed=11, shard_size=12, vocab=self.vocab, families=['lab_threshold', 'table_aggregate', 'demographics'])
        todo = Lb.units(qdir, unit=5)
        self.assertEqual([(a, b) for _, a, b in todo], [(0, 5), (5, 10), (10, 12)])
        first = [Lb.label_unit(s, a, b, ldir, str(self.stack)) for s, a, b in todo]
        again = [Lb.label_unit(s, a, b, ldir, str(self.stack)) for s, a, b in todo]
        self.assertEqual(first, again)  # .done markers short-circuit
        done = next(ldir.glob('*.done')); done.unlink()  # a partial unit resumes without duplicating rows
        s, a, b = todo[0]
        Lb.label_unit(s, a, b, ldir, str(self.stack))
        ids = [json.loads(l)['id'] for p in Lb.labeled_files(ldir) + Lb.reject_files(ldir) for l in p.read_text().splitlines()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 12)
        summary = Lb.summarize(ldir, wall=1.0, workers=2)
        self.assertEqual(summary['items'], 12)
        self.assertIn('estimate_1M', summary)


class DatasetTests(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.qdir, cls.ldir = Path(cls.tmp.name) / 'q-ds', Path(cls.tmp.name) / 'l-ds'
        F.write_shards(cls.qdir, 80, seed=21, shard_size=80, vocab=cls.vocab, families=['lab_threshold', 'table_aggregate', 'demographics'])
        Lb.run(cls.qdir, cls.ldir, stack=cls.stack, workers=1, unit=40)
        cls.out = Path(cls.tmp.name) / 'datasets'
        cls.manifest = D.build(cls.ldir, cls.out, catalog_tables=cls.vocab.catalog.tables, bench=D.Bench(['How many women are there']))

    def test_template_families_never_leak(self):
        self.assertEqual(set(self.manifest['template_family_leakage'].values()), {0})
        self.assertGreater(self.manifest['stats']['labeled_items'], 40)

    def test_learner_sets(self):
        typed = [json.loads(l) for s in ('train', 'valid', 'heldout') for l in (self.out / 'typed' / f'{s}.jsonl').read_text().splitlines()]
        self.assertTrue(typed)
        for r in typed: self.assertIn('select', r['target'])
        norm = [json.loads(l) for s in ('train', 'valid', 'heldout') for l in (self.out / 'normalizer' / f'{s}.jsonl').read_text().splitlines()]
        for r in norm:  # only targets the free parser reads, in the runtime prompt format
            self.assertEqual(r['messages'][-1]['content'], r['target'])
            self.assertEqual(r['messages'][0]['role'], 'system')
        router = [json.loads(l) for l in (self.out / 'router' / 'train.jsonl').read_text().splitlines()]
        self.assertTrue({'literal_lookup'} <= {r['label'] for r in router})

    def test_benchmarks_are_excluded(self):
        texts = [json.loads(l).get('question') or json.loads(l).get('input') for p in self.out.glob('*/*.jsonl') for l in p.read_text().splitlines()]
        self.assertNotIn('How many women are there?', texts)
        self.assertGreaterEqual(self.manifest['stats'].get('benchmark_excluded', 0), 0)

    def test_mlx_materialization_and_deferred_training(self):
        counts = D.mlx_typed(self.out / 'typed', Path(self.tmp.name) / 'mlx-typed', self.vocab.catalog.tables, limit=5)
        self.assertLessEqual(counts['train'], 5)
        r = T.train_lora('typed', self.out, Path(self.tmp.name) / 'runs', self.stack, smoke=True, allow_mlx=False, eval_limit=5)
        self.assertEqual(r['decision'], 'deferred')
        self.assertIn('mlx_lm lora', r['command'])
        self.assertTrue(Path(r['receipt']).exists())

    def test_seed_stores_dry_run_skips_grammar_readable(self):
        rows = [json.loads(l) for l in (self.out / 'cache' / 'train.jsonl').read_text().splitlines()]
        counts = D.seed_stores(self.stack, rows, dry_run=True)
        self.assertEqual(counts.get('seed', 0) + counts.get('skipped_grammar_reads', 0), len(rows))


class PromotionTests(unittest.TestCase):
    def test_decide(self):
        self.assertEqual(T.decide(0.9, None)[0], 'promoted')
        self.assertEqual(T.decide(0.9, 0.8)[0], 'promoted')
        self.assertEqual(T.decide(0.801, 0.8)[0], 'not_promoted')
        self.assertEqual(T.decide(None, 0.8)[0], 'not_promoted')

    def test_receipt_written(self):
        with tempfile.TemporaryDirectory() as d:
            r = T.receipt(d, 'router', 'not_promoted', reason='x')
            self.assertEqual(json.loads(Path(r['receipt']).read_text())['decision'], 'not_promoted')

    def test_lora_command_is_caffeinated_and_small_in_smoke(self):
        cmd = T.lora_command('/d', '/a', 'normalizer', smoke=True)
        if T.shutil.which('caffeinate'): self.assertEqual(cmd[:2], ['caffeinate', '-i'])
        self.assertEqual(cmd[cmd.index('--iters') + 1], '3')


if __name__ == '__main__':
    unittest.main()
