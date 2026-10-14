"""Release-diff guard on fixture builds that reproduce the four v2.3 drifts: a new free-text evidence source widening
diabetes, R73.09 bound under prediabetes, place_of_service's tail form capturing service_date / service_from, and an
upper-case lab name; plus growth normalisation, metric value-set case variants, the gate and the CLI."""
import contextlib, io, json, sqlite3, tempfile, unittest
from pathlib import Path

from clinical_core.agere import release_diff as RD

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE patient(patient_id TEXT PRIMARY KEY);
CREATE TABLE record(stream TEXT, patient_id TEXT);
CREATE TABLE concept_patient(concept TEXT, kind TEXT, patient_id TEXT, records INTEGER, first_date TEXT, last_date TEXT);
CREATE TABLE condition(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, label TEXT, icd10 TEXT, snomed TEXT,
                       diagnosis_basis TEXT);
CREATE TABLE measurement(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, name TEXT, value REAL, unit TEXT,
                         loinc TEXT, component TEXT);
CREATE TABLE encounter(record_id TEXT, patient_id TEXT, source_table TEXT, type TEXT, type_concept TEXT);
CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, type TEXT, type_concept TEXT, setting TEXT);
"""

METRIC = {'id': 'a1c_tested', 'version': 1, 'name': 'A1c tested', 'status': 'internal', 'internal_reason': 'fixture',
          'summary': 'Patients with an A1c result.', 'grain': {'entity': 'patient', 'key': 'patient_id'},
          'population': {'summary': 'roster'}, 'denominator': {'summary': 'none'}, 'numerator': {'summary': 'A1c'},
          'time_anchor': {'field': 'measurement.event_date', 'mode': 'any_time'}, 'unit': 'patients', 'expected_range': [0, 10 ** 6],
          'versioning': {'rule': 'none'}, 'output': {'returns': 'count'},
          'value_sets': {'a1c': {'dimension': 'measurement.name', 'concept': 'hba1c', 'members': ['Hemoglobin A1c'],
                                 'label_patterns': ['a1c'], 'provenance': 'fixture'}},
          'sql_template': "SELECT count(DISTINCT patient_id) FROM measurement WHERE name IN ('Hemoglobin A1c')"}


def make_build(root, name, *, patients, conditions=(), labs=(), encounters=(), streams=None, as_of='2030-05-27'):
    """conditions: (pid, stream, concept, label, icd10, basis); labs: (pid, name, concept); encounters: (pid, stream, type, concept);
    streams: {stream: {field: populated}} extra profile streams (for field bindings)."""
    stack = Path(root) / name / 'stack'
    stack.mkdir(parents=True)
    digest = f'digest-{name}'
    (stack / 'run.json').write_text(json.dumps({'source_digest': digest, 'snapshot': {'as_of': as_of}}))
    db = sqlite3.connect(stack / 'semantic.sqlite3')
    db.executescript(SCHEMA)
    db.executemany('INSERT INTO meta VALUES (?,?)', [('version', 'semantic-layer/fixture'), ('as_of', as_of)])
    db.executemany('INSERT INTO patient VALUES (?)', [(p,) for p in patients])
    cp, rec = set(), set()
    for i, (pid, st, c, label, icd, basis) in enumerate(conditions):
        db.execute('INSERT INTO condition VALUES (?,?,?,?,?,?,?,?)', (f'c{i}', pid, st, c, label, icd, None, basis))
        if c: cp.add((c, 'condition', pid))
        rec.add((st, pid))
    for i, (pid, lab, c) in enumerate(labs):
        db.execute('INSERT INTO measurement VALUES (?,?,?,?,?,?,?,?,?)', (f'm{i}', pid, 'labs', c, lab, 7.0, '%', None, None))
        if c: cp.add((c, 'measurement', pid))
        rec.add(('labs', pid))
    for i, (pid, st, typ, c) in enumerate(encounters):
        db.execute('INSERT INTO encounter VALUES (?,?,?,?,?)', (f'e{i}', pid, st, typ, c))
        db.execute('INSERT INTO visit VALUES (?,?,?,?,?)', (f'e{i}', pid, typ, c, None))
        if c: cp.add((c, 'encounter', pid))
        rec.add((st, pid))
    db.executemany('INSERT INTO concept_patient VALUES (?,?,?,1,NULL,NULL)', sorted(cp))
    db.executemany('INSERT INTO record VALUES (?,?)', sorted(rec))
    db.commit(); db.close()
    prof_streams = {}
    for st in {r[1] for r in conditions} | ({'labs'} if labs else set()) | {r[1] for r in encounters}:
        pids = {r[0] for r in conditions if r[1] == st} | ({r[0] for r in labs} if st == 'labs' else set()) | {r[0] for r in encounters if r[1] == st}
        fields = {'patient_id': {'populated': len(pids), 'distinct': len(pids)}}
        if st in ('conditions', 'condition_assessments'):
            codes = {}
            for r in conditions:
                if r[1] == st and r[4]: codes[r[4]] = codes.get(r[4], 0) + 1
            fields['icd10'] = {'populated': sum(codes.values()), 'distinct': len(codes), 'values': codes}
        prof_streams[st] = {'records': len(pids), 'fields': fields}
    for st, fs in (streams or {}).items():
        prof_streams[st] = {'records': 50, 'fields': {f: {'populated': n, 'distinct': n} for f, n in fs.items()}}
    names = {}
    for _, lab, _ in labs: names[lab] = names.get(lab, 0) + 1
    profile = {'schema': 'source-profile/1', 'source_digest': digest, 'patients': {'records': len(patients)}, 'streams': prof_streams,
               'measurements': {'labs': [{'name': n, 'unit': '%', 'records': k} for n, k in names.items()], 'vitals': []}}
    (stack / 'source-profile.json').write_text(json.dumps(profile))
    return stack.parent


class ReleaseDiffGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.metrics = self.root / 'config' / 'metrics'; self.metrics.mkdir(parents=True)
        (self.metrics / 'fixture.json').write_text(json.dumps({'schema': 'agere-metric-file/1', 'domain': 'fixture', 'metrics': [METRIC]}))
        ten = [f'P{i}' for i in range(10)]
        claims_v20 = {'medical_claims': {'service_date': 40, 'place_of_service': 30, 'service_organization_id': 40, 'allowed_cents': 40}}
        self.prior = make_build(self.root, 'v20', patients=ten,
            conditions=[(p, 'conditions', 'diabetes', 'Type 2 diabetes', 'E11.9', None) for p in ten[:4]]
                     + [('P6', 'conditions', 'prediabetes', 'Prediabetes', 'R73.03', None)],
            labs=[(p, 'Hemoglobin A1c', 'hba1c') for p in ten[:6]],
            encounters=[(p, 'encounters', 'emergency', 'emergency_visit') for p in ten[:3]],
            streams=claims_v20)
        twenty = ten + [f'N{i}' for i in range(10)]
        self.new = make_build(self.root, 'v23', patients=twenty,
            conditions=[(p, 'conditions', 'diabetes', 'Type 2 diabetes', 'E11.9', 'clinical_encounter') for p in ten[:4] + twenty[10:14]]
                     + [('P5', 'conditions', 'diabetes', 'IDDM', None, 'ehr_problem_list')]
                     + [('P6', 'conditions', 'prediabetes', 'Prediabetes', 'R73.03', 'clinical_encounter'),
                        ('P7', 'conditions', 'prediabetes', 'Elevated hemoglobin A1c', 'R73.09', 'laboratory_evidence')],
            labs=[(p, 'Hemoglobin A1c', 'hba1c') for p in ten[:6] + twenty[10:16]] + [('N7', 'HEMOGLOBIN A1C', 'hba1c')],
            encounters=[(p, 'encounters', 'emergency', 'emergency_visit') for p in ten[:3] + twenty[10:13]],
            streams={**claims_v20, 'payer_medical_claims': {'service_from': 50, 'service_to': 50, 'place_of_service': 50}})

    def tearDown(self): self.tmp.cleanup()

    def run_diff(self, **kw):
        return RD.diff(self.prior, self.new, metrics_directory=self.metrics, **kw)

    def kinds(self, report, severity='flag'):
        return [(f['kind'], f['subject']) for f in report['findings'] if f['severity'] == severity]

    def test_four_v23_drifts_are_flagged(self):
        r = self.run_diff(resolver='pre-c3c6f59')
        flags = self.kinds(r)
        # 1. diabetes gained uncoded free text: one patient reached by nothing diabetes used before
        src = [f for f in r['findings'] if f['kind'] == 'new_evidence_source' and f['subject']['concept'] == 'diabetes']
        self.assertEqual([(f['subject']['source'], f['added_patients']) for f in src], [('conditions/free_text', 1)])
        # 2. R73.09 under prediabetes
        self.assertIn(('clinically_distinct_code', {'concept': 'prediabetes', 'code': 'R73.09'}), flags)
        # 3. place_of_service's tail 'service' captures service_date (prior, standing) and service_from (new stream)
        mis = {(s['stream'], s['form']) for k, s in flags if k == 'alias_misbinding'}
        self.assertIn(('payer_medical_claims', 'service_from'), mis)
        self.assertNotIn(('medical_claims', 'service_date'), mis)  # misbound already in the prior release: review, not a new flag
        standing = [f for f in r['findings'] if f['kind'] == 'alias_misbinding_standing']
        self.assertEqual([f['subject']['stream'] for f in standing], ['medical_claims'])
        # 4. upper-case lab name, and the metric value set that adopts the mixed-case spelling
        self.assertIn(('lab_name_variant', {'stream': 'labs', 'name': 'HEMOGLOBIN A1C'}), flags)
        self.assertIn(('metric_member_case_variant', {'metric': 'a1c_tested', 'value_set': 'a1c', 'member': 'Hemoglobin A1c'}), flags)
        self.assertIn(('metric_literal_case_variant', {'metric': 'a1c_tested', 'literal': 'Hemoglobin A1c'}), flags)

    def test_live_resolver_fixes_the_field_hijack(self):
        r = self.run_diff()
        self.assertEqual(r['field_bindings']['misbound_new'], [])
        self.assertFalse([k for k, _ in self.kinds(r, 'review') if k == 'alias_misbinding_standing'])

    def test_binding_change_between_releases(self):
        # a field that arrives in an existing stream changes what an existing form binds to
        pr = make_build(self.root, 'a', patients=['P0'], streams={'claims': {'service_date': 5}})
        nw = make_build(self.root, 'b', patients=['P0'], streams={'claims': {'service_date': 5, 'place_of_service': 5}})
        r = RD.diff(pr, nw, metrics_directory=self.metrics, resolver='pre-c3c6f59')
        changed = {(x['form'], tuple(x['prior']), tuple(x['new'])) for x in r['field_bindings']['changed']}
        self.assertIn(('service_date', ('service_date',), ('place_of_service',)), changed)
        self.assertEqual(RD.diff(pr, nw, metrics_directory=self.metrics)['field_bindings']['changed'], [])

    def test_growth_is_normalised_per_segment(self):
        r = self.run_diff()
        dm = r['concepts']['diabetes']
        # new patients N0-N3 carry diabetes but are a new segment: the same-patient count moves 4 -> 5 only
        self.assertEqual((dm['segment'], dm['prior_patients'], dm['new_patients'], dm['new_segment']['patients']), ('identity', 4, 5, 4))
        self.assertNotIn('concept_widened', [k for k, s in self.kinds(r) if s.get('concept') == 'diabetes'])
        self.assertNotIn(('concept_widened', {'concept': 'emergency_visit'}), self.kinds(r))
        # roster mode (a regenerated population) scales by coverage growth instead: 4 -> 9 with coverage x2 is in tolerance
        rr = self.run_diff(segment_mode='roster')
        self.assertEqual(rr['concepts']['diabetes']['prior_patients'], 4)
        self.assertEqual(rr['concepts']['diabetes']['new_patients'], 9)
        self.assertNotIn(('concept_widened', {'concept': 'diabetes'}), self.kinds(rr))

    def test_silent_widening_is_flagged(self):
        pr = make_build(self.root, 'w0', patients=[f'P{i}' for i in range(40)],
                        conditions=[(f'P{i}', 'conditions', 'hypertension', 'Essential hypertension', 'I10', None) for i in range(40)]
                        + [(f'P{i}', 'conditions', 'heart_failure', 'Heart failure', 'I50.9', None) for i in range(10)])
        nw = make_build(self.root, 'w1', patients=[f'P{i}' for i in range(40)],
                        conditions=[(f'P{i}', 'conditions', 'hypertension', 'Essential hypertension', 'I10', None) for i in range(40)]
                        + [(f'P{i}', 'conditions', 'heart_failure', 'Heart failure', 'I50.9', None) for i in range(25)])
        r = RD.diff(pr, nw, metrics_directory=self.metrics)
        self.assertEqual([s for k, s in self.kinds(r) if k == 'concept_widened'], [{'concept': 'heart_failure'}])
        self.assertFalse([k for k, _ in self.kinds(RD.diff(pr, nw, metrics_directory=self.metrics, tolerance=2.0)) if k == 'concept_widened'])

    def test_concept_definitions_from_a_revision_drive_the_family_check(self):
        old = type('C', (), {})()
        from clinical_core.agere.clinical import concepts
        for k in ('MEASUREMENTS', 'ENCOUNTERS', 'CONDITION_STREAMS', 'concept_for_label'): setattr(old, k, getattr(concepts, k))
        old.CONDITIONS = {**concepts.CONDITIONS, 'prediabetes': {**concepts.CONDITIONS['prediabetes'], 'icd10': ['R73.0']}}
        fam = RD.diff(self.prior, self.new, metrics_directory=self.metrics)['code_families']['prediabetes']
        self.assertEqual(fam['new_codes'], [])  # the live family (R73.01-R73.03) does not contain R73.09
        orig = RD.concepts_module
        RD.concepts_module = lambda rev=None, root=None: old if rev == 'old' else orig(rev, root)
        try:
            fam = RD.diff(self.prior, self.new, metrics_directory=self.metrics, concepts_rev='old')['code_families']['prediabetes']
        finally:
            RD.concepts_module = orig
        self.assertEqual(fam['new_codes'], ['R73.09'])

    def test_gate_accepts_reviewed_flags_and_cli_writes_reports(self):
        report, g = RD.run(self.new, self.prior, self.root / 'out', gate_on=True, metrics_directory=self.metrics)
        self.assertFalse(g['passed'])
        accept = self.root / 'accept.json'
        accept.write_text(json.dumps({'accepted': g['open_flags']}))
        self.assertTrue(RD.gate(report, json.loads(accept.read_text())['accepted'])['passed'])
        self.assertTrue(RD.gate(report, sorted({f['kind'] for f in report['findings']}))['passed'])
        md = (self.root / 'out' / 'release-diff.md').read_text()
        self.assertIn('# Release diff', md)
        self.assertIn('BLOCKED', md)
        saved = json.loads((self.root / 'out' / 'release-diff.json').read_text())
        self.assertEqual(saved['gate']['enabled'], True)
        from tools import release_diff_guard as cli
        orig = RD.diff
        RD.diff = lambda prior, new, **kw: orig(prior, new, metrics_directory=self.metrics, **{k: v for k, v in kw.items() if k != 'metrics_directory'})
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                gated = cli.main(['--candidate', str(self.new), '--prior', str(self.prior), '--out', str(self.root / 'cli'), '--gate'])
                plain = cli.main(['--candidate', str(self.new), '--prior', str(self.prior), '--out', str(self.root / 'cli')])
                accepted = cli.main(['--candidate', str(self.new), '--prior', str(self.prior), '--out', str(self.root / 'cli'),
                                     '--gate', '--accept', str(accept)])
        finally:
            RD.diff = orig
        self.assertEqual((gated, plain, accepted), (2, 0, 0))
        self.assertIn('BLOCKED', out.getvalue())

    def test_activation_gate_reads_the_candidate_report(self):
        from clinical_core.agere import warehouse_activation as WA
        with self.assertRaisesRegex(ValueError, 'Release-diff report missing'):
            WA.release_diff_gate(self.new)
        report, g = RD.run(self.new, self.prior, self.new, metrics_directory=self.metrics)
        with self.assertRaisesRegex(ValueError, 'unacknowledged flags'):
            WA.release_diff_gate(self.new)
        self.assertTrue(WA.release_diff_gate(self.new, g['open_flags'])['passed'])
        saved = json.loads((self.new / 'release-diff.json').read_text())
        saved['candidate']['digest'] = 'another'
        (self.new / 'release-diff.json').write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, 'another source digest'):
            WA.release_diff_gate(self.new, g['open_flags'])

    def test_read_only_on_both_builds(self):
        before = {p: p.stat().st_mtime_ns for b in (self.prior, self.new) for p in (b / 'stack').iterdir()}
        self.run_diff()
        self.assertEqual(before, {p: p.stat().st_mtime_ns for b in (self.prior, self.new) for p in (b / 'stack').iterdir()})


if __name__ == '__main__':
    unittest.main()
