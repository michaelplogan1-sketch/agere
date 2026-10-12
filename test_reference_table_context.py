import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import reference_table_context as T


class ReferenceTableTests(unittest.TestCase):
    def setUp(self):
        self.edges = [{'from': 'source-a', 'to': 'source-'+str(i), 'field': 'linked_order_id',
                       'scope': 'outside_selected_context', 'status': 'received_same_patient'} for i in range(40)]
        self.wire = {'dependencies': self.edges, 'unresolved_references': [],
                     'units': [{'id': 'u1', 'text': 'Exact source quotation with "quotes" and é.'}],
                     'received_source_availability': {'typed_references': deepcopy(self.edges), 'unresolved_typed_references': [], 'received_records': 41}}
        self.body = {'system': 'Original instructions', 'messages': [{'role': 'user', 'content': json.dumps(self.wire)}], 'model': 'test'}

    def test_exact_roundtrip_including_missing_keys_and_explicit_nulls(self):
        rows = [{'a': None}, {}, {'a': [1, {'x': 'é'}], 'b': False}, {'b': 0}]
        self.assertEqual(T.untable(T.table(rows)), rows)

    def test_shared_edges_are_aliased_and_full_wire_reconstructs(self):
        body = T.compact_body(self.body); wire = json.loads(body['messages'][0]['content'])
        self.assertEqual(wire['received_source_availability']['typed_references'], {'same_as': '/dependencies'})
        self.assertEqual(T.restore_wire(wire), self.wire)
        self.assertEqual(wire['units'], self.wire['units'])
        self.assertEqual(body['system'], self.body['system'])
        self.assertLess(len(json.dumps(body)), len(json.dumps(self.body)))

    def test_different_availability_edges_not_wrongly_deduplicated(self):
        self.wire['received_source_availability']['typed_references'] = self.edges[:2]
        self.body['messages'][0]['content'] = json.dumps(self.wire)
        compact = json.loads(T.compact_body(self.body)['messages'][0]['content'])
        self.assertIn('columns', compact['received_source_availability']['typed_references'])
        self.assertEqual(T.restore_wire(compact), self.wire)

    def test_dictionary_transport_preserves_dates_strings_and_numeric_cells(self):
        value = {'columns': ['date', 'record', 'count'], 'rows': [['2029-01-01', 'record-'+str(i), i] for i in range(40)]}
        compact = T.matrix(value)
        self.assertIn('0', compact['dictionaries'])
        self.assertNotIn('2', compact['dictionaries'])
        self.assertEqual(T.unmatrix(compact), value)
        self.assertLess(len(json.dumps(compact)), len(json.dumps(value)))
        compact['rows'][0][0] = 400
        with self.assertRaisesRegex(ValueError, 'dictionary cell'): T.unmatrix(compact)

    def test_reconciliation_without_top_level_dependencies_supported(self):
        del self.wire['dependencies']; del self.wire['unresolved_references']
        self.body['messages'][0]['content'] = json.dumps(self.wire)
        self.assertEqual(T.restore_wire(json.loads(T.compact_body(self.body)['messages'][0]['content'])), self.wire)

    def test_malformed_tables_and_alias_rejected(self):
        for row in ({'columns': ['a', 'a'], 'rows': [], 'present': []},
                    {'columns': ['a'], 'rows': [[1]], 'present': [[3]]},
                    {'columns': ['a'], 'rows': [[1]], 'present': [[0, 0]]}):
            with self.assertRaises(ValueError): T.untable(row)
        wire = json.loads(T.compact_body(self.body)['messages'][0]['content'])
        wire['received_source_availability']['typed_references']['same_as'] = '/units'
        with self.assertRaises(ValueError): T.restore_wire(wire)

    def test_historical_requests_are_byte_identical_and_install_idempotent(self):
        with patch.object(T.C, 'inspect'), patch.object(T.C, 'exists'), patch.object(T.A, 'envelope', return_value=self.body):
            # A Mock's arbitrary attributes are truthy; use a real callable for the installer marker.
            T.C.inspect = lambda *a: None
            T.install(); installed = T.A.envelope; T.install(); self.assertIs(T.A.envelope, installed)
            old = T.A.envelope({}, {}, {}, {'packet': {}})
            self.assertIs(old, self.body)
            marked = {'packet': {'missing_evidence': {'record_scope': {'reference_table_transport': T.VERSION}}}}
            newer = T.A.envelope({}, {}, {}, marked)
            self.assertEqual(T.restore_wire(json.loads(newer['messages'][0]['content'])), self.wire)

    def test_context_preserves_record_scope_and_rejects_modified_parent(self):
        parent = {'id': 'a'*64, 'schema': 'selected-record-context/1', 'parameters': {'max_bytes': 60000},
                  'record_scope': {'record_ids': ['r1']}, 'missing_evidence': {'record_scope': {'kind': 'explicit_received_records'}},
                  'model_context': {'sources': {'r1': {'source': {'record_id': 'r1'}}}, 'evidence': [{'id': 'unit'}]},
                  'budget': {'used': 100}, 'dispatch': {'source_review_allowed': True}}
        with tempfile.TemporaryDirectory() as root, patch.object(T.C, 'verified', return_value=parent), patch.object(T.C, 'inspect', return_value=parent):
            row = T.create(root, parent['id'])
            self.assertEqual(row['record_scope'], parent['record_scope'])
            self.assertEqual(row['model_context']['evidence'], parent['model_context']['evidence'])
            self.assertEqual(T.inspect(root, row['id']), row)
            parent['model_context']['evidence'][0]['changed'] = True
            with self.assertRaises(ValueError): T.inspect(root, row['id'])

    def test_transport_cannot_exceed_original_context_bound(self):
        parent = {'id': 'a'*64, 'schema': 'selected-record-context/1', 'parameters': {'max_bytes': 10},
                  'missing_evidence': {'record_scope': {}}, 'model_context': {}, 'budget': {}}
        with self.assertRaisesRegex(ValueError, 'bound'): T.transform(parent)


if __name__ == '__main__': unittest.main()
