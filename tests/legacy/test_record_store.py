"""Equivalence of the stream-clustered record store with the original records queries (old vs new outputs)."""
import json, os, random, sqlite3, unittest
from unittest.mock import patch

from clinical_core.agere.clinical import aggregate, cohorts, explain, record_store
from test_aggregate_explain import build, pcp, visit

WINDOW = {'basis': 'all_observed', 'start': '', 'end': ''}


def fixture():
    rng = random.Random(7)
    patients = {f'p{i}': {'age_reported': 70 if i % 2 else 40, 'sex_reported': 'F' if i % 4 < 2 else 'M', 'coverage': 'Medicaid' if i % 3 else 'Medicare'}
                for i in range(14)}
    recs = [pcp(f'p{i}', 'drA' if i < 7 else 'drB', 'PR1' if i < 7 else 'PR2') for i in range(14)]
    recs += [(f'p{i}', 'coverage', {'coverage_type': 'medicaid' if i % 3 else 'medicare', 'event_date': f'202{6 + i % 3}-01-01'}) for i in range(14)]
    for i in range(14):
        for k in range(i % 4):
            recs.append(visit(f'p{i}', rng.choice(['emergency', 'ED', 'inpatient', 'urgentcare']), f'2028-0{1 + k}-1{i % 9}', rng.choice(['H1', 'H2']),
                              f'2028-0{1 + k}-2{i % 9}'))
        recs.append((f'p{i}', 'labs', {'test': rng.choice(['HbA1c', 'Hemoglobin A1c/Hemoglobin.total']), 'value': 6 + rng.random() * 4,
                                        'unit': '%', 'event_date': f'2028-0{1 + i % 8}-01', 'available_date': f'2028-0{1 + i % 8}-03'}))
        recs.append((f'p{i}', 'conditions', {'description': rng.choice(['Type 2 diabetes mellitus', 'Essential hypertension', 'Asthma']),
                                              'icd10': rng.choice(['E11.9', 'I10', 'J45.909']), 'event_date': '2027-05-01'}))
    rng.shuffle(recs)  # rowid order differs from stream, patient and id order
    path = build(recs, patients)
    db = sqlite3.connect(path / 'stack.sqlite3')
    with db:  # record ids in a different order than rowids
        for (rid,) in db.execute('SELECT id FROM records').fetchall():
            db.execute('UPDATE records SET id=? WHERE id=?', (f'x{rng.randrange(10 ** 6):06d}-{rid}', rid))
    db.close()
    return path, sorted(patients)


class RecordStore(unittest.TestCase):
    def setUp(self):
        record_store.clear_memo()
        self.path, self.ids = fixture()
        record_store.build(self.path)

    def both(self, fn):
        with patch.dict(os.environ, {'AGERE_RECORD_STORE': 'off'}): old = fn()
        record_store.clear_memo()
        with patch.dict(os.environ, {'AGERE_RECORD_STORE': 'on'}):
            new = fn(); again = fn()  # second call is served from the memo
        return old, new, again

    def test_load_matches_original_query_in_both_orders(self):
        sets = [['encounters'], ['labs', 'coverage'], ['conditions', 'encounters', 'care_team_relationships'], ['nope']]
        for streams in sets:
            for order in ('stream', 'id'):
                for kw in ({}, {'members': set(self.ids[:5])}, {'patient': 'p3'}):
                    old, new, again = self.both(lambda: dict(record_store.load(self.path, streams, order=order, **kw)))
                    self.assertEqual(old, new, (streams, order, kw)); self.assertEqual(old, again)
        self.assertGreater(record_store.stats()['memo_hits'], 0)

    def test_live_index_layout_order(self):
        """The served stack has stream-leading indexes (warehouse_concurrency); plan order changes and must still match."""
        db = sqlite3.connect(self.path / 'stack.sqlite3')
        with db:
            db.execute('CREATE INDEX received_stream_patients ON records(stream,patient)')
            db.execute('CREATE INDEX medication_source_records ON records(stream,patient,id)')
            db.execute('ANALYZE')
        db.close()
        for streams in (['labs', 'coverage'], ['encounters', 'conditions', 'care_team_relationships']):
            for kw in ({}, {'patient': 'p4'}, {'members': set(self.ids[3:])}):
                old, new, _ = self.both(lambda: dict(record_store.load(self.path, streams, **kw)))
                self.assertEqual(old, new, (streams, kw))
        self.assertGreater(record_store.stats()['store_reads'], 0)

    def test_original_sql_order_is_what_store_reproduces(self):
        db = sqlite3.connect(self.path / 'stack.sqlite3')
        raw = [json.loads(p)['record_id'] for pid, p in db.execute(
            "SELECT patient, payload FROM records WHERE stream IN ('labs','encounters') ORDER BY id") if pid == 'p3']
        db.close()
        self.assertEqual([r['record_id'] for r in record_store.load(self.path, ['labs', 'encounters'], order='id')['p3']], raw)

    def test_stale_store_is_never_used(self):
        self.assertIsNotNone(record_store.token(self.path))
        db = sqlite3.connect(self.path / 'stack.sqlite3')
        rid, payload = db.execute("SELECT id, payload FROM records WHERE stream='labs' LIMIT 1").fetchone()
        changed = {**json.loads(payload), 'value': 99.0}
        with db: db.execute('UPDATE records SET payload=? WHERE id=?', (json.dumps(changed), rid))
        db.close()
        self.assertIsNone(record_store.token(self.path))
        values = [r['value'] for rs in record_store.load(self.path, ['labs']).values() for r in rs]
        self.assertIn(99.0, values)
        record_store.build(self.path)
        self.assertIsNotNone(record_store.token(self.path))
        self.assertIn(99.0, [r['value'] for rs in record_store.load(self.path, ['labs']).values() for r in rs])

    def test_memo_budget_zero_disables_memo(self):
        with patch.dict(os.environ, {'AGERE_RECORD_MEMO_MB': '0'}):
            record_store.load(self.path, ['labs']); record_store.load(self.path, ['labs'])
        self.assertEqual(record_store.stats()['memo_streams'], 0)

    def test_callers_cannot_corrupt_shared_lists(self):
        a = record_store.load(self.path, ['labs']); a['p1'].append({'junk': True}); a['p1'].clear()
        b = record_store.load(self.path, ['labs'])
        self.assertTrue(b['p1'])

    def test_aggregate_identical(self):
        for p in [dict(measure='events', concept='emergency_visit', statistic='count', group_by='pcp'),
                  dict(measure='events', concept='emergency_visit', statistic='rate_per_1000', group_by='practice', adjust='age_sex_conditions'),
                  dict(measure='value', concept='hba1c', statistic='mean', group_by='coverage_type'),
                  dict(measure='patients', concept='diabetes', statistic='share', group_by='age_band'),
                  dict(measure='readmissions', statistic='rate', group_by='facility')]:
            for fast in ('on', 'off'):
                with patch.dict(os.environ, {'AGERE_AGG_FAST': fast}):
                    old, new, again = self.both(lambda: aggregate.execute(self.path, self.ids, p))
                self.assertEqual(old, new, p); self.assertEqual(old, again, p)

    def test_explain_identical(self):
        for p in [{'outcome': {'type': 'readmission'}}, {'outcome': {'type': 'concept', 'concept': 'emergency_visit'}},
                  {'outcome': {'type': 'measurement', 'concept': 'hba1c', 'op': 'ge', 'value': 8}}]:
            with patch.dict(os.environ, {'AGERE_RESULT_CACHE': 'off'}):
                old, new, again = self.both(lambda: explain.execute(self.path, self.ids, p))
            self.assertEqual(old, new, p); self.assertEqual(old, again, p)

    def test_cohort_selection_identical_including_traces(self):
        specs = [
            {'all': [{'kind': 'concept', 'concept': 'diabetes', 'window': WINDOW}], 'any': [], 'exclude': []},
            {'all': [{'kind': 'measurement', 'concept': 'hba1c', 'op': 'ge', 'value': 8, 'aggregate': 'latest',
                      'window': {'basis': 'source_event', 'start': '2028-03-01', 'end': ''}}], 'any': [], 'exclude': []},
            {'all': [{'kind': 'record', 'stream': 'encounters', 'conditions': [{'field': 'type', 'op': 'eq', 'value': 'emergency'}],
                      'window': {'basis': 'arrival', 'start': '2028-01-01', 'end': ''}}],
             'any': [{'kind': 'patient', 'field': 'age', 'op': 'ge', 'value': 50}, {'kind': 'concept', 'concept': 'hypertension', 'window': WINDOW}],
             'exclude': [{'kind': 'concept', 'concept': 'asthma', 'window': WINDOW}]},
        ]
        for spec in specs:
            for kw in ({}, {'patient': 'p5'}, {'patient_ids': self.ids[2:9]}):
                with patch.dict(os.environ, {'AGERE_RESULT_CACHE': 'off'}):
                    old, new, again = self.both(lambda: cohorts.select(self.path, spec, **kw))
                self.assertEqual(old, new, (spec, kw)); self.assertEqual(old, again)

    def test_result_cache_off_writes_nothing(self):
        spec = {'all': [{'kind': 'concept', 'concept': 'diabetes', 'window': WINDOW}], 'any': [], 'exclude': []}
        with patch.dict(os.environ, {'AGERE_RESULT_CACHE': 'off'}):
            first = cohorts.select(self.path, spec); second = cohorts.select(self.path, spec)
        self.assertFalse(first['cache_hit']); self.assertFalse(second['cache_hit'])
        db = sqlite3.connect(self.path / 'stack.sqlite3')
        self.assertEqual(db.execute('SELECT count(*) FROM result_cache').fetchone()[0], 0)
        db.close()


if __name__ == '__main__':
    unittest.main()
