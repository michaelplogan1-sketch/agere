"""Streaming layer (clinical_core/agere/streaming): incremental ingest, change log, dependent replay. Synthetic fixtures only.

A tiny native delivery of payer flat files (eligibility, medical, pharmacy) is built in a temp folder:
- base (delivered 2026-10-05): five members at two payers; three diabetics on payer claims, two of them with statin fills;
  'Ann Lee' at payer C2 and 'Ann Leigh' at payer C3 are two charts (demographic keys differ).
- tick 1 (delivered 2026-11-05): a pharmacy reversal of one statin fill, a new visit, and a new C3 member 'Ann Lee' whose
  subscriber id is Ann Leigh's member id (a new key that links the two charts).
- tick 2 (delivered 2026-11-10): a medical file for May 2026 (late file, late records).
A full rebuild of all files and the base build + two deltas must give identical stores and semantic layers.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

os.environ.setdefault('AGERE_STREAMING', 'on')

ELIG = 'Member Id,Subscriber Id,First Name,Last Name,Dob,Gender,Address,City,State,Zip,Product,Plan Id,Eff Date,Term Date,Pcp Npi,Pcp Name'
MED = ('Claim Id,Claim Line,Member Id,Claim Type,Bill Type,Place Of Service,From Date,To Date,Paid Date,Admit Date,Discharge Date,Drg,'
       'Dx1,Dx2,Dx3,Dx4,Dx5,Dx6,Dx7,Dx8,Dx9,Dx10,Dx11,Dx12,Procedure Code,Revenue Code,Units,Billed,Allowed,Paid,Member Liability,'
       'Claim Status,Frequency Code,Original Claim Id,Billing Npi,Rendering Npi,Network')
RX = 'Claim Id,Member Id,Fill Date,Ndc,Drug Name,Quantity,Days Supply,Fill Number,Pharmacy Npi,Prescriber Npi,Rx Number,Status,Plan Paid,Member Paid'
ATORVA = 'atorvastatin 40 MG Oral Tablet'


def _elig(mid, sub, first, last, dob, sex, zipc, eff='09/01/2026', state='KY', city='Florence'):
    return f'{mid},{sub},{first},{last},{dob},{sex},1 Main St,{city},{state},{zipc},commercial,PLAN-1,{eff},,1189911575,WHN-PRACTICE-KY-1'


def _med(cid, mid, day, dx, cpt='99213', status='paid', freq='1', pos='11'):
    dxs = [dx] + [''] * 11
    return ','.join([cid, '1', mid, 'P', '', pos, day, day, day, '', '', '', *dxs, cpt, '', '1', '150.00', '90.00', '80.00', '10.00',
                     status, freq, '', '1517019877', '1373579154', 'IN'])


def _rx(cid, mid, day, status='paid', paid='100.00', days='90'):
    return f'{cid},{mid},{day},00093505698,{ATORVA},90,{days},0,1676246311,1512225214,3331788750,{status},{paid},10.00'


FILES = {
    # base delivery
    'payers/WH-PAYER-C2/2026-09/C2_ELIGIBILITY_20261005.csv': ('2026-10-05', [ELIG,
        _elig('C21000000A1', 'C21000000A1', 'John', 'Doe', '03/01/1970', 'M', '41042'),
        _elig('C21000000B2', 'C21000000B2', 'Mary', 'Roe', '07/12/1965', 'F', '63006'),
        _elig('C21000000C3', 'C21000000C3', 'Paul', 'Poe', '01/05/1980', 'M', '95502'),
        _elig('C21000000D4', 'C21000000D4', 'Ann', 'Lee', '01/01/1960', 'F', '10001', state='NY', city='New York')]),
    'payers/WH-PAYER-C2/2026-09/C2_MEDICAL_20261005.csv': ('2026-10-05', [MED,
        _med('WHN-CLM-00000000000000000000a1', 'C21000000A1', '09/02/2026', 'E119'),
        _med('WHN-CLM-00000000000000000000b2', 'C21000000B2', '09/03/2026', 'E119'),
        _med('WHN-CLM-00000000000000000000c3', 'C21000000C3', '09/04/2026', 'E119')]),
    'payers/WH-PAYER-C2/2026-09/C2_PHARMACY_20261005.csv': ('2026-10-05', [RX,
        _rx('WHN-RXC-0000000000000000000001', 'C21000000A1', '09/05/2026'),
        _rx('WHN-RXC-0000000000000000000002', 'C21000000B2', '09/06/2026')]),
    'payers/WH-PAYER-C3/2026-09/C3_ELIGIBILITY_20261005.csv': ('2026-10-05', [ELIG,
        _elig('C31000000E5', 'C31000000E5', 'Ann', 'Leigh', '01/01/1960', 'F', '10001', state='NY', city='New York')]),
    # tick 1: reversal, new visit, linking key
    'payers/WH-PAYER-C2/2026-10/C2_PHARMACY_20261105.csv': ('2026-11-05', [RX,
        _rx('WHN-RXC-0000000000000000000001', 'C21000000A1', '09/05/2026', status='reversed', paid='-100.00')]),
    'payers/WH-PAYER-C2/2026-10/C2_MEDICAL_20261105.csv': ('2026-11-05', [MED,
        _med('WHN-CLM-00000000000000000000b9', 'C21000000B2', '10/10/2026', 'E119')]),
    'payers/WH-PAYER-C3/2026-10/C3_ELIGIBILITY_20261105.csv': ('2026-11-05', [ELIG,
        _elig('C31000000E5', 'C31000000E5', 'Ann', 'Leigh', '01/01/1960', 'F', '10001', state='NY', city='New York'),
        _elig('C31000000F6', 'C31000000E5', 'Ann', 'Lee', '01/01/1960', 'F', '10001', eff='10/01/2026', state='NY', city='New York')]),
    # tick 2: a late file (May 2026 claims delivered in November)
    'payers/WH-PAYER-C2/2026-05/C2_MEDICAL_20260605.csv': ('2026-11-10', [MED,
        _med('WHN-CLM-00000000000000000000c5', 'C21000000C3', '05/10/2026', 'E119')]),
}
BASE = [p for p, (d, _) in FILES.items() if d == '2026-10-05']
TICK1 = [p for p, (d, _) in FILES.items() if d == '2026-11-05']
TICK2 = [p for p, (d, _) in FILES.items() if d == '2026-11-10']
SPD = ('Which diabetic patients aged 40 to 75 are not on a statin? Definition: Diabetes = E10*/E11* on a condition record or '
       'payer claim; age 40-75 at as_of; alive at as_of. Not on a statin = no active statin on the medication list and no statin '
       'fill covering any of the 120 days before as_of.')


def write_source(root):
    src = Path(root) / 'source'
    for rel, (day, lines) in FILES.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('\n'.join(lines) + '\n')
    (src / 'manifest.json').write_text(json.dumps({'files': [{'path': 'raw/' + rel, 'delivered': day, 'sender': rel.split('/')[1]}
                                                             for rel, (day, _) in FILES.items()]}))
    return src


def entries(src, rels):
    data = json.loads((Path(src) / 'manifest.json').read_text())['files']
    return [e for e in data if e['path'][4:] in rels]


def build(root, name, src, rels, raw_mode='all'):
    from clinical_core.agere.ingest import flat
    from clinical_core.agere.onboarding.workspace import Workspace
    from clinical_core.agere.streaming import delivery_drop as dd
    drop = Path(root) / f'{name}-drop'
    dd.make_drop(src, drop, entries(src, rels))
    ws = Workspace.create(name, 'native://' + str(drop), root=Path(root) / 'workspaces', raw=raw_mode)
    flat.reset_schema_registry()  # a full build is one fresh process
    ws.run()
    return ws, drop


def person_of(ws, key):
    db = sqlite3.connect(ws.landing_path)
    try:
        r = db.execute('SELECT person_id FROM stg_person_map WHERE source_key = ?', (key,)).fetchone()
        return r[0] if r else None
    finally:
        db.close()


class StreamingFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from clinical_core.agere.streaming import delivery_drop as dd, replay
        # not under the system temp dir: on macOS it resolves to /private/var/..., and parsers refuse any path with a
        # 'private' folder (a delivery's private/ folder is never read)
        scratch = Path(__file__).resolve().parent / 'tmp'
        scratch.mkdir(exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(dir=scratch, prefix='streaming-test-')
        root = Path(cls.tmp.name)
        cls.src = write_source(root)
        cls.full, _ = build(root, 'full', cls.src, BASE + TICK1 + TICK2)
        cls.ws, drop = build(root, 'stream', cls.src, BASE)
        from clinical_core.agere.streaming import pipeline
        digest = json.loads((cls.ws.dir / 'stack' / 'run.json').read_text())['source_digest']
        cls.boot = pipeline.bootstrap(cls.ws)  # streaming state, profile value counts, text-reader cache
        cls.digest_kept = digest == json.loads((cls.ws.dir / 'stack' / 'run.json').read_text())['source_digest']
        cls.before = {k: person_of(cls.ws, k) for k in ('member:WH-PAYER-C2:C21000000D4', 'member:WH-PAYER-C3:C31000000E5')}
        spec, sql, ids = replay.run_cohort_family(SPD, cls.ws.state['as_of'], cls.ws.semantic_path)
        cls.spd_before = ids
        replay.register(cls.ws.dir, 'spd', SPD, kind='cohort_family', semantic_path=cls.ws.semantic_path, as_of=cls.ws.state['as_of'],
                        patient_ids=ids, spec=spec, query=sql)
        replay.register(cls.ws.dir, 'model-q', 'How many members look high risk?', kind='model', semantic_path=cls.ws.semantic_path,
                        as_of=cls.ws.state['as_of'], count=3, tables=['payer_claim'])
        cls.tick1 = cls.ws.ingest_delta(dd.add_files(cls.src, drop, entries(cls.src, TICK1)), refresh=True)
        cls.tick2 = cls.ws.ingest_delta(dd.add_files(cls.src, drop, entries(cls.src, TICK2)), refresh=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def flags(self, tick):
        from clinical_core.agere.streaming import ticks
        db = ticks.connect(self.ws.dir)
        try:
            return [dict(zip(('kind', 'patient_id', 'stream', 'relpath', 'detail'), r)) for r in
                    db.execute('SELECT kind, patient_id, stream, relpath, detail FROM tick_flag WHERE tick_id = ?', (tick,))]
        finally:
            db.close()

    def test_delta_equals_full_rebuild(self):
        from clinical_core.agere.streaming import equivalence
        rep = equivalence.compare_workspaces(self.full.dir, self.ws.dir)
        self.assertTrue(rep['equal'], equivalence.summarize(rep))
        self.assertGreater(len(rep['stack_semantic']['tables']), 40)

    def test_reversal_tick_invalidates_and_refresh_changes_count(self):
        from clinical_core.agere.streaming import replay
        stale = {s['answer_id']: s for s in self.tick1['stale_answers']}
        self.assertIn('spd', stale)
        self.assertIn('fill', stale['spd']['reason'])
        self.assertTrue(any(f['kind'] == 'claim_reversal' for f in self.flags(self.tick1['tick'])))
        spd = next(a for a in self.tick1['refresh']['answers'] if a['answer_id'] == 'spd')
        self.assertEqual(spd['status'], 'refreshed')
        self.assertEqual(spd['count_after'], spd['count_before'] + 1)
        john = person_of(self.ws, 'member:WH-PAYER-C2:C21000000A1')
        self.assertNotIn(john, self.spd_before)
        self.assertIn(john, replay.run_cohort_family(SPD, self.ws.state['as_of'], self.ws.semantic_path)[2])
        # a model-written answer is flagged for re-ask, never re-run
        model = next(a for a in self.tick1['refresh']['answers'] if a['answer_id'] == 'model-q')
        self.assertEqual(model['status'], 'needs_reask')
        # action-backbone hook: John is an affected patient of tick 1
        self.assertIn(john, replay.affected_patients(self.ws.dir, self.tick1['tick']))

    def test_late_file_is_flagged(self):
        fl = self.flags(self.tick2['tick'])
        late_files = [f for f in fl if f['kind'] == 'late_file']
        self.assertEqual([f['relpath'] for f in late_files], ['payers/WH-PAYER-C2/2026-05/C2_MEDICAL_20260605.csv'])
        self.assertTrue(any(f['kind'] == 'late_record' for f in fl))
        self.assertFalse(any(f['kind'] == 'late_file' for f in self.flags(self.tick1['tick'])))

    def test_new_key_links_two_charts(self):
        a, b = self.before['member:WH-PAYER-C2:C21000000D4'], self.before['member:WH-PAYER-C3:C31000000E5']
        self.assertNotEqual(a, b)
        after = {k: person_of(self.ws, k) for k in self.before}
        self.assertEqual(len(set(after.values())), 1)
        self.assertEqual(person_of(self.ws, 'member:WH-PAYER-C3:C31000000F6'), after['member:WH-PAYER-C2:C21000000D4'])
        merges = [f for f in self.flags(self.tick1['tick']) if f['kind'] == 'identity_merge']
        self.assertEqual(len(merges), 1)
        self.assertEqual(sorted(json.loads(merges[0]['detail'])['merged_from']), sorted([a, b]))
        db = sqlite3.connect(self.ws.dir / 'stack' / 'stack.sqlite3')
        try:
            ids = {r[0] for r in db.execute('SELECT id FROM patients')}
        finally:
            db.close()
        self.assertIn(after['member:WH-PAYER-C2:C21000000D4'], ids)
        self.assertLessEqual(len({a, b} & ids), 1)


    def test_bootstrap_touches_nothing_and_profile_counts_match(self):
        from clinical_core.agere.clinical import source_profile
        from clinical_core.agere.streaming import profile_state, semantic, stage
        self.assertEqual(self.boot.get('touched_on_bootstrap'), 0)
        self.assertTrue(self.digest_kept)  # a no-op restage keeps the release identity
        stack = self.ws.dir / 'stack'
        db = sqlite3.connect(stack / 'stack.sqlite3')
        try:
            streams = {r[0] for r in db.execute('SELECT DISTINCT stream FROM records')}
            full, meas = semantic.profile_streams(db, streams)
        finally:
            db.close()
        fresh, meas2, recount = profile_state.profile_streams(stage.state_path(stack), stack / 'stack.sqlite3', streams)
        self.assertEqual(recount, set())
        self.assertEqual(json.dumps(fresh, sort_keys=True), json.dumps(full, sort_keys=True))
        self.assertEqual(json.dumps(meas2, sort_keys=True), json.dumps(meas, sort_keys=True))
        built = source_profile.build(stack / 'stack.sqlite3')
        self.assertEqual(json.dumps(built['streams'], sort_keys=True), json.dumps(full, sort_keys=True))


    def test_answer_hook_registers_what_an_answer_read(self):
        from clinical_core.agere.streaming import hooks, replay
        out = {'route': 'local_compiler', 'decision': 'local_compiler', 'answer': {'count': 3},
               'result': {'graph': {'nodes': [{'operation': 'sql', 'parameters': {'query': 'SELECT count(*) FROM payer_claim c JOIN patient p USING (patient_id)'}}]}}}
        reg = hooks.on_answer(self.ws, 'How many payer claims?', out)
        self.assertEqual(reg['tables'], ['patient', 'payer_claim'])
        db = replay.connect(self.ws.dir)
        try:
            kind, status = db.execute('SELECT kind, status FROM answers WHERE answer_id = ?', (reg['answer_id'],)).fetchone()
        finally:
            db.close()
        self.assertEqual((kind, status), ('sql', 'fresh'))
        model = hooks.on_answer(self.ws, 'Summarise the risk', {'route': 'hosted_verified_sql', 'answer': {'value': 'x'}, 'result': {}})
        self.assertIsNotNone(model)


class StreamingNoRawCopies(unittest.TestCase):
    """raw='none' workspaces: the workspace semantic layer is re-made as a clone of the canonical one after each tick."""

    def test_delta_equals_full_rebuild_without_raw_copies(self):
        from clinical_core.agere.streaming import delivery_drop as dd, equivalence
        scratch = Path(__file__).resolve().parent / 'tmp'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch, prefix='streaming-test-') as t:
            root = Path(t)
            src = write_source(root)
            full, _ = build(root, 'full-none', src, BASE + TICK1 + TICK2, raw_mode='none')
            ws, drop = build(root, 'stream-none', src, BASE, raw_mode='none')
            # crash after restage (the semantic update fails once): the next ingest_delta finishes the journaled tick
            from unittest import mock
            from clinical_core.agere.streaming import pipeline, semantic
            real = semantic._update
            calls = []

            def fail_once(*a, **k):
                if not calls:
                    calls.append(1)
                    raise RuntimeError('simulated crash')
                return real(*a, **k)
            rels = dd.add_files(src, drop, entries(src, TICK1 + TICK2))
            with mock.patch.object(semantic, '_update', fail_once):
                with self.assertRaises(RuntimeError):
                    ws.ingest_delta(rels, record_store=False)
            self.assertIsNotNone(pipeline.pending_tick(ws))
            done = ws.ingest_delta([], record_store=False)
            self.assertTrue(done['resumed'])
            self.assertIsNone(pipeline.pending_tick(ws))
            rep = equivalence.compare_workspaces(full.dir, ws.dir)
            self.assertTrue(rep['equal'], equivalence.summarize(rep))
            fa, fb = equivalence.fingerprint_workspace(full.dir), equivalence.fingerprint_workspace(ws.dir)
            self.assertTrue(equivalence.compare_fingerprints(fa, fb)['equal'])


class StreamingUnits(unittest.TestCase):
    def test_tables_read_and_batches(self):
        from clinical_core.agere.streaming import delivery_drop as dd, replay
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / 's.sqlite3'
            db = sqlite3.connect(p)
            db.executescript('CREATE TABLE fill(patient_id); CREATE TABLE patient(patient_id); CREATE TABLE other(x);')
            db.close()
            self.assertEqual(replay.tables_read('SELECT p.patient_id FROM patient p JOIN fill f USING (patient_id)', p), ['fill', 'patient'])
        groups = dd.batches([{'delivered': '2030-04-01'}, {'delivered': '2030-04-06'}, {'delivered': '2030-04-08'}], 'week')
        self.assertEqual([len(v) for v in groups.values()], [2, 1])

    def test_switch_off_refuses_new_methods(self):
        from clinical_core.agere import streaming
        old = os.environ.get('AGERE_STREAMING')
        os.environ['AGERE_STREAMING'] = 'off'
        try:
            with self.assertRaises(streaming.StreamingDisabled):
                streaming.require()
        finally:
            os.environ['AGERE_STREAMING'] = old or 'on'


if __name__ == '__main__':
    unittest.main()
