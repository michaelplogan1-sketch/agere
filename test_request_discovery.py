"""Independent discovery fixtures; no provider, hidden references or generator truth."""
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_shared_planner as fixtures
from clinical_core.agere.clinical import request_discovery as D, request_plan as R, shared_planner as S, conversation
from clinical_core.agere.deliverables import answer_context as A


class RequestDiscovery(unittest.TestCase):
    def setUp(self):
        fixtures.SharedPlanner.setUp(self)
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executescript("""
                CREATE TABLE received_notes(record_id TEXT PRIMARY KEY, patient_id TEXT, narrative TEXT, event_date TEXT, recorded_date TEXT);
                INSERT INTO received_notes VALUES
                  ('N1','A','taxi requested','2029-01-01','2029-01-04'),
                  ('N2','B','denies needing a taxi','2029-01-01','2029-01-02'),
                  ('N3','OUTSIDE','taxi requested','2029-01-01','2029-01-03');
                CREATE TABLE remit_detail(record_id TEXT PRIMARY KEY, patient_id TEXT, adjustment_reason TEXT, adjustment_cents INTEGER);
                INSERT INTO remit_detail VALUES('R1','A','duplicate',1200),('R2','C','contract',300),('R3','OUTSIDE','duplicate',9000);
                CREATE TABLE device_delivery(record_id TEXT PRIMARY KEY, patient_id TEXT, parcel_state TEXT, delivery_date TEXT);
                INSERT INTO device_delivery VALUES('D1','B','delayed','2029-02-01'),('D2','A','delivered','2029-01-01'),('D3','OUTSIDE','delayed','2029-03-01');
                CREATE TABLE record(record_id TEXT PRIMARY KEY,patient_id TEXT,stream TEXT,event_date TEXT,recorded_date TEXT,payload TEXT);
                INSERT INTO record VALUES('X1','A','intake','2029-01-01','2029-01-05','{"preference":"morning"}'),
                  ('X2','B','intake','2029-01-01','2029-01-05','{"preference":"afternoon"}'),
                  ('X3','OUTSIDE','intake','2029-01-01','2029-01-05','{"preference":"morning"}');
                CREATE TABLE policy_document(record_id TEXT,description TEXT);
                CREATE TABLE note_fact(record_id TEXT,patient_id TEXT,concept TEXT);
            """)
        profile = {'source_digest': self.catalog.identity['source_digest'], 'streams': {
            'intake': {'records': 3, 'fields': {'preference': {'populated': 3, 'types': {'text': 3}, 'distinct': 2,
                                                            'values': {'morning': 2, 'afternoon': 1}}}},
            'other_feed': {'records': 0, 'fields': {'different_field': {'populated': 0}}}}}
        (self.path/'source-profile.json').write_text(json.dumps(profile))
        self.catalog = R.Catalog.load(self.path)

    def test_new_sources_and_derived_limits_are_metadata_driven(self):
        for term, name in [('narrative', 'received_notes'), ('adjustment', 'remit_detail'), ('parcel', 'device_delivery')]:
            found = D.search(self.catalog, [term])
            self.assertIn(name, [c['name'] for c in found['candidates']])
            self.assertFalse(found['data_absence_established'])
        meta = D.describe(self.catalog, ['received_notes', 'remit_detail', 'note_fact'], ['intake'])
        self.assertEqual(meta['relations']['received_notes']['declared_unique_keys'][0], ['record_id'])
        self.assertEqual(meta['relations']['received_notes']['clock_fields'], ['event_date', 'recorded_date'])
        self.assertEqual(meta['relations']['remit_detail']['columns']['adjustment_cents']['unit'], 'cents')
        self.assertIn('missing extracted facts', meta['relations']['note_fact']['extraction_coverage'])
        self.assertEqual(meta['received_streams']['intake']['fields']['preference']['populated'], 3)
        values = D.search(self.catalog, ['morning'])
        self.assertIn('intake', [c['name'] for c in values['candidates']])
        self.assertIn('received_notes', [c['name'] for c in D.search(self.catalog, ['narratives'])['candidates']])
        self.assertFalse(values['record_search_performed'])
        self.catalog.profile['source_digest'] = 'stale'
        self.assertEqual(D.search(self.catalog, ['morning'])['candidates'], [])

    def test_probe_scope_lineage_negation_and_raw_stream_preserved(self):
        r = D.probe(self.catalog, {'table': 'received_notes', 'field': 'received_notes.narrative',
                                  'mode': 'excerpts', 'contains': 'taxi'}, population=['A', 'B'])
        self.assertEqual({x['record_id'] for x in r['rows']}, {'N1', 'N2'})
        self.assertIn('denies needing a taxi', [x['probe_value'] for x in r['rows']])
        self.assertEqual(r['scope']['patient_count'], 2)
        self.assertFalse(r['data_absence_established'])
        self.assertIn('negation', r['limitation'])
        r = D.probe(self.catalog, {'table': 'record', 'field': 'record.payload.preference',
                                  'stream': 'intake', 'mode': 'values'}, population=['A'])
        self.assertEqual([x['probe_value'] for x in r['rows']], ['morning'])
        empty = D.probe(self.catalog, {'table': 'record', 'field': 'record.payload.preference',
                                      'stream': 'intake', 'mode': 'values'}, population=[])
        self.assertEqual(empty['rows'], [])
        self.assertTrue(empty['scan_complete'])
        self.assertFalse(empty['data_absence_established'])

    def test_empty_metadata_probe_and_timeout_never_claim_global_absence(self):
        self.assertEqual(D.search(self.catalog, ['nonexistent_signal'])['candidates'], [])
        empty = D.probe(self.catalog, {'table': 'received_notes', 'field': 'received_notes.narrative',
                                      'mode': 'values', 'contains': 'airplane'})
        self.assertEqual(empty['status'], 'no_matching_received_values_in_probe_scope')
        self.assertFalse(empty['data_absence_established'])
        with sqlite3.connect(self.catalog.target) as db:
            db.executemany('INSERT INTO received_notes VALUES (?,?,?,?,?)',
                           [(f'L{i}', 'A', 'long scan', None, None) for i in range(1000)])
        timed = D.probe(self.catalog, {'table': 'received_notes', 'field': 'received_notes.narrative',
                                      'mode': 'excerpts', 'contains': 'absent'}, seconds=-1)
        self.assertEqual(timed['status'], 'search_incomplete')
        self.assertFalse(timed['scan_complete'])
        self.assertFalse(timed['data_absence_established'])

    def test_truncated_samples_do_not_become_complete_populations(self):
        with sqlite3.connect(self.catalog.target) as db:
            db.executemany('INSERT INTO received_notes VALUES (?,?,?,?,?)',
                           [(f'L{i}', 'A', 'x'*1200, None, None) for i in range(9)])
        found = D.probe(self.catalog, {'table': 'received_notes', 'field': 'received_notes.narrative', 'mode': 'excerpts'})
        self.assertEqual(len(found['rows']), 5)
        self.assertTrue(found['truncated'])
        self.assertFalse(found['scan_complete'])
        self.assertEqual(found['rows'][-1]['truncated_fields'], ['probe_value'])

    def test_unknown_fields_streams_scope_and_stale_identity_fail_closed(self):
        bad = [
            {'table': 'record', 'field': 'record.payload.preference', 'stream': 'other_feed', 'mode': 'values'},
            {'table': 'received_notes', 'field': 'remit_detail.adjustment_reason', 'mode': 'values'},
            {'table': 'received_notes', 'field': 'received_notes.narrative', 'mode': 'values', 'query': 'DELETE'},
        ]
        for spec in bad:
            with self.subTest(spec=spec), self.assertRaises(R.PlanError):
                D.probe(self.catalog, spec)
        with self.assertRaises(R.PlanError):
            D.probe(self.catalog, {'table': 'policy_document', 'field': 'policy_document.description', 'mode': 'values'}, population=['A'])
        with sqlite3.connect(self.catalog.target) as db:
            db.execute("UPDATE meta SET value='changed' WHERE key='source_digest'")
        with self.assertRaises(R.PlanError):
            D.probe(self.catalog, {'table': 'received_notes', 'field': 'received_notes.narrative', 'mode': 'values'})
        refreshed = R.Catalog.load(self.path)
        self.assertEqual(refreshed.stream_fields, {})
        self.assertEqual(refreshed.profile, {})

    def test_prompt_projection_bounds_detail_without_changing_receipts(self):
        from copy import deepcopy
        r = D.run(self.catalog, {'terms': ['narrative'], 'probes': [
            {'table': 'received_notes', 'field': 'received_notes.narrative', 'mode': 'excerpts'}]})
        r['probes'][0]['source_identity']['verbose_metadata'] = 'x' * 50000
        receipts = [deepcopy(r) for _ in range(6)]
        original = deepcopy(receipts)
        compact = D.prompt_view(receipts)
        self.assertLessEqual(len(json.dumps(compact, separators=(',', ':')).encode()), 12000)
        self.assertEqual(receipts, original)
        self.assertTrue(all(not p['data_absence_established'] for item in compact for p in item.get('probes', [])))
        minimal = D.prompt_view(receipts, max_bytes=2000)
        self.assertLessEqual(len(json.dumps(minimal, separators=(',', ':')).encode()), 2000)
        self.assertTrue(all('prompt_detail_omitted' in item for item in minimal))

    def test_discovery_survives_exhaustion_and_later_provider_failure(self):
        from clinical_core.agere.clinical.question_input import failure, QuestionNotUnderstood
        reply = {'need_discovery': {'probes': [
            {'table': 'received_notes', 'field': 'received_notes.narrative', 'mode': 'values'}]}}
        calls = []
        def model(*args, **kwargs):
            calls.append(1)
            return json.dumps(reply), {'api_usd': .01, 'api_cost_known': True}
        with self.assertRaises(QuestionNotUnderstood) as exhausted:
            S.checked_interpret(self.catalog, 'Inspect received notes', model=model, force_model=True)
        result = failure(exhausted.exception)
        self.assertEqual(len(result['evidence_discovery']), 6)
        self.assertEqual(result['new_paid_api_calls'], 6)
        self.assertFalse(result['data_absence_established'])
        calls.clear()
        def broken(*args, **kwargs):
            if calls: raise RuntimeError('fixture provider unavailable')
            return model(*args, **kwargs)
        with self.assertRaises(RuntimeError) as unavailable:
            S.checked_interpret(self.catalog, 'Inspect received notes', model=broken, force_model=True)
        result = failure(unavailable.exception)
        self.assertEqual(len(result['evidence_discovery']), 1)
        self.assertEqual(result['planning_economics']['known_api_estimate_usd'], .01)
        self.assertIsNone(result['planning_economics']['new_api_estimate_usd'])
        calls.clear()
        self.stack.compose = lambda q: S.compose(self.stack, q, model=model)
        with self.assertRaises(QuestionNotUnderstood):
            conversation.handle_compose(self.stack, {'question': 'Inspect received notes', 'conversation_id': 'discovery-failure'})
        with sqlite3.connect(self.path/'conversations.sqlite3') as db:
            saved = json.loads(db.execute('SELECT answer_summary FROM turns WHERE conversation_id=?',
                                         ('discovery-failure',)).fetchone()[0])
        self.assertEqual(len(saved['evidence_discovery']), 6)
        self.assertFalse(saved['data_absence_established'])

    def test_discovery_composes_and_saves_three_distinct_domains(self):
        cases = [
            ('received_notes', 'narrative', 'taxi', 'like', '%taxi%', ['A','B']),
            ('remit_detail', 'adjustment_reason', 'duplicate', '=', 'duplicate', ['A']),
            ('device_delivery', 'parcel_state', 'delayed', '=', 'delayed', ['B']),
        ]
        self.stack.allow_live = True
        self.stack.receipts = self.path/'receipts'
        for table, field, term, op, value, expected in cases:
            predicate = f'{field} {op} "{value}"'
            q = f'Count {table} where {predicate}'
            proposal = {'source': {'table': table, 'quote': table},
                        'select': [{'kind': 'count', 'alias': 'records', 'quote': 'Count'}],
                        'where': {'op': op, 'field': f'{table}.{field}', 'value': value,
                                  'quote': predicate},
                        'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}
            calls = []
            def model(system, user, **kwargs):
                supplied = json.loads(user)
                calls.append(supplied)
                if len(calls) == 1:
                    obj = {'need_discovery': {'terms': [field], 'probes': [
                        {'table': table, 'field': f'{table}.{field}', 'mode': 'excerpts', 'contains': term}]}}
                elif len(calls) == 2:
                    self.assertEqual({x['patient_id'] for x in supplied['discovery'][0]['probes'][0]['rows']}, set(expected))
                    obj = proposal
                else:
                    self.assertTrue(supplied['discovery'])
                    obj = {'equivalent': True, 'issues': []}
                return json.dumps(obj), {'fixture_no_network': True}
            # Exercise the real compose/save path. Only the model is a fixture;
            # no provider request or default/global usage ledger is touched.
            with patch('clinical_core.agere.clinical.nl2sql.HostedModel', return_value=model), \
                 patch('clinical_core.agere.gateway.default_ledger_path', return_value=self.path/'test-ledger.sqlite'):
                result = S.compose(self.stack, q, population=['A','B','C'], planning='frontier')
            self.assertEqual(result['answer']['status'], 'answered')
            self.assertEqual(result['answer']['patient_ids'], expected)
            self.assertEqual(result['answer']['count'], len(expected))
            self.assertEqual(result['new_paid_api_calls'], 0)
            key = A.capture(self.path, q, result)['id']
            saved = A.load(self.path, key)
            self.assertEqual(saved['answer']['evidence_discovery'][0]['probes'][0]['scope']['patient_count'], 3)
            self.assertEqual(saved['patient_ids'], expected)
            self.assertEqual(len(calls), 3)
            listed = A.followup(self.path, key, 'list them')
            child = A.capture(self.path, 'list them', listed)
            reopened, bundle = A.evidence_bundle(self.path, child['id'])
            self.assertEqual(reopened['answer']['evidence_discovery'], saved['answer']['evidence_discovery'])
            self.assertEqual(reopened['answer']['inherited_evidence_context']['source'], saved['source'])
            self.assertIn('evidence_discovery', json.dumps(bundle.results))
            self.assertEqual(reopened['planner_receipt']['origin_request_contract']['id'],
                             saved['planner_receipt']['request_contract']['id'])
            narrowed = S.compose(self.stack, 'List patients over 65', population=expected, parent=saved,
                                 model=lambda *a, **kw: self.fail('Unexpected model'))
            self.assertEqual(narrowed['answer']['evidence_discovery'], saved['answer']['evidence_discovery'])


if __name__ == '__main__':
    unittest.main()
