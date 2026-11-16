"""Verified-store carry-forward: every prior verified pair is replayed on the new release; passing pairs carry with
provenance, the rest are quarantined with reasons (absent columns / values / streams, dates anchored to the old
snapshot, user corrections, disagreeing plans); the prior store and both builds are never written; the activation hook
fills the new stack's store so lookups hit on the new release."""
import contextlib, hashlib, io, json, sqlite3, tempfile, unittest
from pathlib import Path

from clinical_core.agere import verified_carry_forward as CF
from clinical_core.agere import warehouse_activation
from clinical_core.agere.clinical import learning

SEMANTIC = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE patient(patient_id TEXT PRIMARY KEY);
CREATE TABLE measurement(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, name TEXT, value REAL, unit TEXT,
                         event_date TEXT);
CREATE TABLE record(record_id TEXT, patient_id TEXT, stream TEXT, event_date TEXT, payload TEXT);
"""
OLD_STORE = """
CREATE TABLE verified(question TEXT, plan_kind TEXT, plan TEXT, signature TEXT, route TEXT, models TEXT, agreement TEXT,
                      created REAL, context TEXT, PRIMARY KEY(question, plan));
CREATE TABLE corrections(question TEXT, note TEXT, expected TEXT, created REAL);
"""


def make_stack(root, name, as_of, names, potassium):
    stack = Path(root) / name / 'stack'
    stack.mkdir(parents=True)
    digest = f'digest-{name}'
    (stack / 'run.json').write_text(json.dumps({'source_digest': digest, 'snapshot': {'as_of': as_of}}))
    (stack.parent / 'connection.json').write_text(json.dumps({'release': f'/releases/{name}'}))
    db = sqlite3.connect(stack / 'semantic.sqlite3')
    db.executescript(SEMANTIC)
    db.executemany('INSERT INTO meta VALUES (?,?)', [('version', 'semantic-layer/fixture'), ('as_of', as_of)])
    pids = [f'P{i}' for i in range(8)]
    db.executemany('INSERT INTO patient VALUES (?)', [(p,) for p in pids])
    rows = [(f'k{i}', pids[i % 3], 'labs', 'potassium', 'Potassium', v, 'mmol/L', d) for i, (v, d) in enumerate(potassium)]
    rows += [(f'a{i}', pids[i], 'labs', 'hba1c', n, 7.5, '%', '2029-03-01') for i, n in enumerate(names)]
    db.executemany('INSERT INTO measurement VALUES (?,?,?,?,?,?,?,?)', rows)
    db.executemany('INSERT INTO record VALUES (?,?,?,?,?)', [(r[0], r[1], 'labs', r[7], '{}') for r in rows])
    db.commit(); db.close()
    profile = {'schema': 'source-profile/1', 'source_digest': digest, 'patients': {'records': len(pids)},
               'streams': {'labs': {'records': len(rows), 'fields': {'test': {'populated': len(rows), 'values': {}}}}},
               'measurements': {'labs': [], 'vitals': []}}
    (stack / 'source-profile.json').write_text(json.dumps(profile))
    return stack


def plan(sql, returns='count'): return json.dumps({'query': sql, 'returns': returns}, sort_keys=True)


GOOD = ('How many patients have a potassium result above 5?',
        "SELECT count(DISTINCT patient_id) AS patients FROM measurement WHERE concept = 'potassium' AND value > 5")
YEAR = ('How many patients had a potassium result in 2029?',
        "SELECT count(DISTINCT patient_id) AS patients FROM measurement WHERE concept = 'potassium' "
        "AND event_date BETWEEN '2029-01-01' AND '2029-12-31'")
SCHEMA = ('How many potassium results carry a LOINC code?', "SELECT count(*) AS n FROM measurement WHERE loinc_code IS NOT NULL")
VALUE = ('How many glycohemoglobin results came in?', "SELECT count(*) AS n FROM measurement WHERE name = 'Glycohemoglobin X'")
ANCHORED = ('How many patients had a potassium result last year?',
            "SELECT count(DISTINCT patient_id) AS patients FROM measurement WHERE concept = 'potassium' "
            "AND event_date BETWEEN '2028-01-01' AND '2028-12-31'")
STREAM = ('How many old referral rows are there?', "SELECT count(*) AS n FROM record WHERE stream = 'old_referrals'")
CORRECTED = ('How many potassium results were reported?', "SELECT count(*) AS n FROM measurement WHERE concept = 'potassium'")
SPLIT_Q = 'How many potassium results do we have?'
SPLIT = [(SPLIT_Q, "SELECT count(*) AS n FROM measurement WHERE concept = 'potassium'"),
         (SPLIT_Q, "SELECT count(DISTINCT patient_id) AS n FROM measurement WHERE concept = 'potassium'")]


class CarryForward(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.root = root
        self.old = make_stack(root, 'v123', '2029-08-15', ['Glycohemoglobin X', 'Hemoglobin A1c'],
                              [(5.6, '2028-04-01'), (4.0, '2028-06-01'), (5.9, '2029-02-01')])
        self.new = make_stack(root, 'v23', '2030-05-27', ['Hemoglobin A1c', 'HEMOGLOBIN A1C'],
                              [(5.6, '2028-04-01'), (5.2, '2029-06-01'), (5.9, '2029-02-01'), (4.4, '2030-01-05'), (4.1, '2028-02-02')])
        db = sqlite3.connect(self.old / 'learning.sqlite3')
        db.executescript(OLD_STORE)
        pairs = [GOOD, YEAR, SCHEMA, VALUE, ANCHORED, STREAM, CORRECTED, *SPLIT]
        db.executemany('INSERT INTO verified VALUES (?,?,?,?,?,?,?,?,?)',
                       [(q, 'sql', plan(s), '["v", 1]', 'hosted_verified_sql', '["haiku", "sonnet"]', 'primary=second', 100.0 + i,
                         'digest-v123|semantic-layer/12') for i, (q, s) in enumerate(pairs)])
        db.execute('INSERT INTO corrections VALUES (?,?,?,?)', (CORRECTED[0], 'wrong unit', None, 1.0))
        db.commit(); db.close()
        self.old_hash = hashlib.sha256((self.old / 'learning.sqlite3').read_bytes()).hexdigest()

    def tearDown(self): self.tmp.cleanup()

    def outcomes(self, summary):
        return {o['question']: (o['status'], ' | '.join(o['reasons'])) for o in summary['outcomes']}

    def test_replay_carries_passing_pairs_and_quarantines_the_rest(self):
        out = self.root / 'scratch'
        s = CF.carry_forward(self.old.parent, self.new.parent, out)
        got = self.outcomes(s)
        self.assertEqual(got[GOOD[0]][0], 'pass', got[GOOD[0]])
        self.assertEqual(got[YEAR[0]][0], 'pass', got[YEAR[0]])
        self.assertIn('schema: no such column: loinc_code', got[SCHEMA[0]][1])
        self.assertIn("value absent in the new release: measurement.name = 'Glycohemoglobin X'", got[VALUE[0]][1])
        self.assertIn('dates anchored to the prior snapshot (2029-08-15): the SQL uses 2028', got[ANCHORED[0]][1])
        self.assertIn("lint: stream 'old_referrals' does not exist", got[STREAM[0]][1])
        self.assertEqual(got[CORRECTED[0]], ('quarantine', 'a user correction exists for this question'))
        split = [o for o in s['outcomes'] if o['question'] == SPLIT_Q]
        self.assertEqual([o['status'] for o in split], ['quarantine', 'quarantine'])
        self.assertIn('disagree on the new release', split[0]['reasons'][0])
        self.assertEqual((s['pairs'], s['carried'], s['quarantined']), (9, 2, 7))
        # the scratch store: carried pairs under the new context with provenance, quarantined ones with reasons
        db = sqlite3.connect(out / 'learning.sqlite3')
        rows = db.execute('SELECT question, context, provenance, created FROM verified ORDER BY created').fetchall()
        self.assertEqual([r[0] for r in rows], [GOOD[0], YEAR[0]])
        self.assertTrue(all(r[1] == CF.Build(self.new).context() for r in rows))
        prov = json.loads(rows[0][2])
        self.assertEqual((prov['origin_release'], prov['origin_context'], prov['origin_as_of'], prov['reverified_as_of']),
                         ('/releases/v123', 'digest-v123|semantic-layer/12', '2029-08-15', '2030-05-27'))
        self.assertEqual(prov['checks'], ['lint', 'executed', 'oracles'])
        self.assertEqual(rows[0][3], 100.0)  # original capture time kept
        q = dict(db.execute('SELECT question, controls FROM quarantined').fetchall())
        self.assertEqual(len(q), 6)  # two rows of the split question share one key here
        self.assertIn('loinc_code', q[SCHEMA[0]])
        db.close()
        # nothing written to the prior store or the new stack
        self.assertEqual(hashlib.sha256((self.old / 'learning.sqlite3').read_bytes()).hexdigest(), self.old_hash)
        self.assertFalse((self.new / 'learning.sqlite3').exists())

    def test_replay_only_writes_nothing_and_rerun_skips_present_pairs(self):
        s = CF.carry_forward(self.old, self.new, dry_run=True)
        self.assertIsNone(s['written_to'])
        self.assertFalse((self.new / 'learning.sqlite3').exists())
        CF.carry_forward(self.old, self.new)
        again = CF.carry_forward(self.old, self.new)
        self.assertEqual(again['already_present'], 2)

    def test_activation_hook_fills_the_new_store_and_lookup_hits(self):
        self.assertIsNone(learning.lookup(self.new, GOOD[0]))
        summary = warehouse_activation.carry_forward_verified(self.old.parent, self.new.parent)
        self.assertNotIn('outcomes', summary)
        self.assertEqual(summary['carried'], 2)
        self.assertTrue((self.new / 'carry-forward.json').exists())
        hit = learning.lookup(self.new, GOOD[0])
        self.assertEqual(json.loads(hit['plan'] if isinstance(hit['plan'], str) else json.dumps(hit['plan']))['query'], GOOD[1])
        self.assertIsNone(learning.lookup(self.new, ANCHORED[0]))  # quarantined pairs are never reused

    def test_question_lookup_carries_one_question_on_demand(self):
        stores = [self.old / 'learning.sqlite3']
        self.assertIsNone(learning.lookup(self.new, GOOD[0]))
        got = CF.carry_question(self.new, GOOD[0], stores=stores)
        self.assertEqual(got['plan']['query'], GOOD[1])
        self.assertEqual(got['carried_from']['context'], 'digest-v123|semantic-layer/12')
        hit = learning.lookup(self.new, GOOD[0])  # written under the new context: the next ask is a plain learned hit
        self.assertEqual(hit['plan']['query'], GOOD[1])
        prov = json.loads(sqlite3.connect(self.new / 'learning.sqlite3').execute('SELECT provenance FROM verified').fetchone()[0])
        self.assertEqual(prov['carried_on'], 'question_lookup')
        self.assertIsNone(CF.carry_question(self.new, ANCHORED[0], stores=stores))   # fails re-verification: quarantined
        q = sqlite3.connect(self.new / 'learning.sqlite3').execute('SELECT controls FROM quarantined WHERE question=?', (ANCHORED[0],)).fetchone()
        self.assertIn('dates anchored', q[0])
        self.assertIsNone(CF.carry_question(self.new, CORRECTED[0], stores=stores))  # corrected: never carried
        self.assertIsNone(CF.carry_question(self.new, 'never asked before?', stores=stores))

    def test_prior_stores_reads_activation_history(self):
        hist = self.root / 'history'; hist.mkdir()
        (hist / 'a.json').write_text(json.dumps({'build': str(self.old.parent)}))
        old, CF.HISTORY = CF.HISTORY, hist
        try:
            self.assertEqual(CF.prior_stores(self.new), [self.old / 'learning.sqlite3'])
        finally:
            CF.HISTORY = old

    def test_activation_carries_forward_by_default(self):
        import inspect
        self.assertIs(inspect.signature(warehouse_activation.activate).parameters['carry_forward'].default, True)

    def test_cli(self):
        from tools import carry_forward_verified as cli
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            cli.main(['--from', str(self.old.parent), '--to', str(self.new.parent), '--out', str(self.root / 'cli')])
        self.assertEqual(json.loads(out.getvalue())['carried'], 2)
        self.assertEqual(json.loads((self.root / 'cli' / 'carry-forward.json').read_text())['quarantined'], 7)

    def test_anchoring_rule(self):
        self.assertEqual(CF.anchored_years('in 2029?', "x BETWEEN '2029-01-01' AND '2029-12-31'", '2029-08-15', '2030-05-27'), [])
        self.assertEqual(CF.anchored_years('past 12 months', "x >= '2028-08-16'", '2029-08-15', '2030-05-27'), ['2028'])
        self.assertEqual(CF.anchored_years('2029 YTD', "x BETWEEN '2029-01-01' AND '2029-08-15'", '2029-08-15', '2030-05-27'),
                         ['2029-08-15'])
        self.assertEqual(CF.anchored_years('past 12 months', "x >= '2028-08-16'", '2029-08-15', '2029-08-15'), [])


if __name__ == '__main__':
    unittest.main()
