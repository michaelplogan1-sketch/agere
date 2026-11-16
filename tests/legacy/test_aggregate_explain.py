"""aggregate and explain over a tiny in-memory store shaped like the received warehouse."""
import json, sqlite3, tempfile, unittest
from pathlib import Path

from clinical_core.agere.clinical import aggregate, explain, source_profile

AS_OF = '2029-08-15'


def build(records, patients):
    root = Path(tempfile.mkdtemp())
    (root / 'run.json').write_text(json.dumps({'source_digest': 'fixture', 'snapshot': {'as_of': AS_OF}}))
    db = sqlite3.connect(root / 'stack.sqlite3')
    db.execute('CREATE TABLE patients(id TEXT PRIMARY KEY, payload TEXT)')
    db.execute('CREATE TABLE records(id TEXT PRIMARY KEY, patient TEXT, stream TEXT, payload TEXT)')
    db.execute('CREATE TABLE result_cache(id TEXT PRIMARY KEY, payload TEXT)')
    for pid, p in patients.items(): db.execute('INSERT INTO patients VALUES (?,?)', (pid, json.dumps({'patient_id': pid, **p})))
    for i, (pid, stream, r) in enumerate(records):
        db.execute('INSERT INTO records VALUES (?,?,?,?)', (f'r{i}', pid, stream, json.dumps(
            {'record_id': f'r{i}', 'patient_id': pid, 'source_table': stream, **r})))
    db.commit(); db.close()
    source_profile._CACHE.clear()
    return root


def pcp(pid, clin, org):
    return (pid, 'care_team_relationships', {'role': 'primary_care', 'status': 'assigned', 'clinician_id': clin,
                                             'organization_id': org, 'effective_start': '2026-01-01', 'event_date': '2026-01-01'})


def visit(pid, kind, day, org='H1', end=None):
    return (pid, 'encounters', {'type': kind, 'event_date': day, 'organization_id': org, **({'end_time': end + 'T10:00:00'} if end else {})})


class Aggregate(unittest.TestCase):
    def setUp(self):
        patients = {f'p{i}': {'age_reported': 70 if i % 2 else 40, 'sex_reported': 'F' if i % 4 < 2 else 'M'} for i in range(12)}
        recs = [pcp(f'p{i}', 'drA' if i < 6 else 'drB', 'PR1' if i < 6 else 'PR2') for i in range(12)]
        recs += [(f'p{i}', 'coverage', {'coverage_type': 'medicare' if i % 2 else 'commercial', 'event_date': '2026-01-01'}) for i in range(12)]
        # drA panel: 4 ED visits (one patient twice same day via ED + ADT => counted once)
        recs += [visit('p0', 'emergency', '2028-01-01'), ('p0', 'adt', {'event': 'emergency_registration', 'event_date': '2028-01-01'}),
                 visit('p1', 'ED', '2028-02-01'), visit('p2', 'emergency', '2028-03-01'), visit('p2', 'emergency', '2028-05-01')]
        recs += [visit('p7', 'urgentcare', '2028-01-01')]
        recs += [(f'p{i}', 'labs', {'test': 'HbA1c', 'value': 7.0 + i / 10, 'unit': '%', 'event_date': '2028-06-01'}) for i in range(12)]
        recs += [('p3', 'labs', {'test': 'Hemoglobin A1c/Hemoglobin.total', 'value': 9.5, 'unit': '%', 'event_date': '2029-01-01'})]
        # stays: p4 readmitted within 30 days; p5 not
        recs += [visit('p4', 'inpatient', '2028-01-01', 'H1', '2028-01-05'), visit('p4', 'inpatient', '2028-01-01', 'H1', '2028-01-05'),
                 visit('p4', 'Inpatient HD3', '2028-01-20', 'H1', '2028-01-23'), visit('p5', 'observation', '2028-03-01', 'H2', '2028-03-02')]
        self.path = build(recs, patients)
        self.ids = sorted(patients)

    def run_agg(self, **p): return aggregate.execute(self.path, self.ids, p)

    def test_events_dedupe_patient_day_and_group_by_pcp(self):
        r = self.run_agg(measure='events', concept='emergency_visit', statistic='count', group_by='pcp')
        rows = {x['group']: x for x in r['rows']}
        self.assertEqual(rows['drA']['numerator'], 4)  # p0 once (ED + ADT same day), p1, p2 twice
        self.assertEqual(rows['drB']['numerator'], 0)

    def test_rate_and_adjustment_report_oe(self):
        r = self.run_agg(measure='events', concept='emergency_visit', statistic='rate_per_1000', group_by='practice', adjust='age_sex')
        rows = {x['group']: x for x in r['rows']}
        self.assertAlmostEqual(rows['PR1']['value'], 1000 * 4 / 6, places=1)
        self.assertGreater(rows['PR1']['oe_ratio'], 1)
        self.assertEqual(len(rows['PR1']['oe_ci95']), 2)
        self.assertTrue(rows['PR1']['small_group'])

    def test_latest_value_mean_uses_all_label_variants(self):
        r = self.run_agg(measure='value', concept='hba1c', statistic='mean', group_by='none', value_aggregate='latest')
        expected = (sum(7.0 + i / 10 for i in range(12)) - (7.0 + 0.3) + 9.5) / 12
        self.assertAlmostEqual(r['overall']['value'], round(expected, 3), places=3)

    def test_share_by_coverage(self):
        r = self.run_agg(measure='patients', concept='emergency_visit', statistic='share', group_by='coverage_type')
        rows = {x['group']: x for x in r['rows']}
        self.assertEqual(rows['commercial']['numerator'], 2)  # p0, p2
        self.assertEqual(rows['medicare']['numerator'], 1)    # p1

    def test_readmissions_collapse_same_day_records(self):
        r = self.run_agg(measure='readmissions', statistic='rate', group_by='facility')
        rows = {x['group']: x for x in r['rows']}
        self.assertEqual((rows['H1']['index_stays'], rows['H1']['numerator']), (2, 1))
        self.assertEqual((rows['H2']['index_stays'], rows['H2']['numerator']), (1, 0))

    def test_validation(self):
        for bad in ({'measure': 'value', 'concept': 'diabetes'}, {'measure': 'events', 'concept': 'hba1c'},
                    {'measure': 'patients', 'statistic': 'mean'}, {'measure': 'value', 'concept': 'hba1c', 'adjust': 'age_sex'},
                    {'measure': 'patients', 'group_by': 'facility'}):
            with self.assertRaises(ValueError): aggregate.validate(bad)


class Explain(unittest.TestCase):
    def test_few_events_returns_case_review(self):
        patients = {f'p{i}': {'age_reported': 50, 'sex_reported': 'F'} for i in range(4)}
        recs = [visit('p0', 'inpatient', '2028-01-01', 'H1', '2028-01-02'), visit('p0', 'inpatient', '2028-01-10', 'H1', '2028-01-11'),
                visit('p1', 'inpatient', '2028-01-01', 'H1', '2028-01-02')]
        r = explain.execute(build(recs, patients), sorted(patients), {'outcome': {'type': 'readmission'}})
        self.assertEqual(r['status'], 'insufficient_events')
        self.assertEqual([c['patient_id'] for c in r['cases']], ['p0'])

    def test_factor_association_detected_and_circular_factor_excluded(self):
        patients = {f'p{i}': {'age_reported': 50, 'sex_reported': 'F'} for i in range(40)}
        recs = []
        for i in range(40):
            asthma = i < 20
            if asthma: recs.append((f'p{i}', 'conditions', {'description': 'Asthma', 'event_date': '2027-01-01'}))
            if (asthma and i < 16) or (not asthma and i < 23):
                recs.append(visit(f'p{i}', 'emergency', '2028-01-01'))
        r = explain.execute(build(recs, patients), sorted(patients), {'outcome': {'type': 'concept', 'concept': 'emergency_visit'}})
        self.assertEqual(r['status'], 'estimated')
        top = r['factors'][0]
        self.assertEqual(top['factor'], 'has_asthma')
        self.assertGreater(top['risk_ratio'], 2)
        self.assertIn('prior_ed_visit', r['excluded_circular_factors'])
        self.assertNotIn('prior_ed_visit', {f['factor'] for f in r['factors']})


if __name__ == '__main__':
    unittest.main()
