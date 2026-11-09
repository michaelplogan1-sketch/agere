"""Shared evidence must not expand repeatedly or change existing source IDs."""
import copy
from datetime import datetime
import hashlib
import json
import unittest
from unittest.mock import patch

from clinical_core.contracts import digest, json_fingerprint
from clinical_core.agere.deliverables import receipts as R


class SharedJsonValues(unittest.TestCase):
    def fixture(self, n=48):
        rows = [{'entity_id': 'INV-'+str(i), 'source_date': '2029-12-31',
                 'amount_cents': -101*i, 'currency': 'USD', 'unknown': None,
                 'source_text': 'Received café: "雪"; source remains unverified.'}
                for i in range(n)]
        rows[-1]['exception'] = 'Historical source superseded; retain this exception.'
        return rows, {'source_rows': rows,
                      'dependent_fields': [{'field': 'field_'+str(i), 'basis': rows}
                                           for i in range(n)]}

    def test_existing_strict_and_presentation_fingerprints_are_bit_identical(self):
        rows, value = self.fixture()
        fixtures = [value, [rows, rows], {'second': rows, 'first': rows},
                    (rows, rows), {3: rows, 1: rows},
                    {'flags': [None, True, False, 0, 0.0, -0.0, 1, 1.0]},
                    ['quoted\\text\n', 'café 🧬', {}, []]]
        for v in fixtures:
            with self.subTest(type=type(v).__name__):
                strict = hashlib.sha256(json.dumps(v, sort_keys=True,
                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()
                presentation = hashlib.sha256(json.dumps(v, sort_keys=True,
                    default=str).encode()).hexdigest()
                self.assertEqual(digest(v), strict)
                self.assertEqual(R.fingerprint(v), presentation)
        self.assertNotEqual(digest([0, True]), digest([False, 1]))
        self.assertNotEqual(digest([0, 0.0]), digest([0.0, 0]))

    def test_shared_default_values_and_unusual_keys_use_standard_json_rules(self):
        row = {'date': datetime(2029, 12, 31), 'amount': 10000}
        value = {'literal: "colon"': row, 'again': row}
        expected = hashlib.sha256(json.dumps(value, sort_keys=True,
                                             default=str).encode()).hexdigest()
        self.assertEqual(R.fingerprint(value), expected)
        with self.assertRaises(TypeError):
            digest(value)
        plain_row = {'date': '2029-12-31', 'amount': 10000}
        for bad in [float('nan'), float('inf'), -float('inf')]:
            with self.subTest(value=bad), self.assertRaises(ValueError):
                digest([plain_row, plain_row, bad])

    def test_shared_basis_is_serialized_once_not_once_per_dependent_field(self):
        basis, value = self.fixture()
        original = json.dumps
        for encode in (R.share_json, R.dictionary_json):
            expanded = []
            def observe(v, *args, **kwargs):
                if v is basis:
                    expanded.append(True)
                return original(v, *args, **kwargs)
            with self.subTest(encoder=encode.__name__), patch.object(json, 'dumps', side_effect=observe):
                wire = encode(value)
            self.assertLessEqual(len(expanded), 1)
            restored = R.unshare_json(json.loads(original(wire)))
            self.assertEqual(restored, value)
            self.assertEqual(len(restored['dependent_fields']), len(basis))
            self.assertTrue(all(len(v['basis']) == len(basis)
                                for v in restored['dependent_fields']))

    def test_markers_key_order_dates_amounts_unknowns_and_late_exception_survive(self):
        basis, value = self.fixture()
        value.update(markers=[{'$': 3}, {'$literal': [['key', 'value']]},
                              {'$agere_shared': ['source_rows']}],
                     order=[{'a': 'x'*500, 'b': 'y'*500},
                            {'b': 'y'*500, 'a': 'x'*500}])
        before = copy.deepcopy(value)
        for encode in (R.share_json, R.dictionary_json, R.compact_json):
            with self.subTest(encoder=encode.__name__):
                restored = R.unshare_json(json.loads(json.dumps(encode(value))))
                self.assertEqual(restored, before)
                # The dictionary and table-layout formats preserve column
                # order. Legacy plain sharing compares JSON object keys in
                # sorted order; array/value order is retained in every format.
                if encode is not R.share_json:
                    self.assertEqual(list(restored['order'][0]), ['a', 'b'])
                    self.assertEqual(list(restored['order'][1]), ['b', 'a'])
                self.assertEqual(restored['source_rows'][-1]['exception'], basis[-1]['exception'])
        self.assertEqual(value, before)

    def test_changed_source_identity_or_amount_cannot_keep_original_fingerprint(self):
        value = {'entity': 'INV-A', 'cents': 10000, 'currency': 'USD', 'unknown': None}
        for field, replacement in [('entity', 'INV-B'), ('cents', 20000), ('currency', 'EUR')]:
            encoded = R.dictionary_json(value)
            encoded['data'][field] = replacement
            with self.subTest(field=field), self.assertRaises(ValueError):
                R.undictionary_json(encoded)

    def test_cycles_fail_without_claiming_a_complete_encoding(self):
        value = []
        value.append(value)
        for encode in (digest, R.fingerprint, R.share_json, R.dictionary_json):
            with self.subTest(encoder=encode.__name__), self.assertRaises(ValueError):
                encode(value)

    def test_shared_metadata_does_not_expand_unrelated_native_rows_in_python(self):
        rows=[{'entity_id':str(i),'cents':i,'unknown':None} for i in range(100)]
        shared={'source_digest':'received-source','as_of':'2029-12-31'}
        value={'rows':rows,'first':shared,'second':shared}
        original=json.dumps
        visits=[]
        def observe(v,*args,**kwargs):
            if v is rows:visits.append(True)
            return original(v,*args,**kwargs)
        expected=hashlib.sha256(original(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        with patch.object(json,'dumps',side_effect=observe):
            self.assertEqual(digest(value),expected)
        self.assertEqual(visits,[True])


if __name__ == '__main__':
    unittest.main()
