"""Customer definitions (onboarding): views created on every connection, shown to the SQL writer, given precedence over the
built-in definitions, and a broken one skipped and reported. Uses a throwaway definitions folder; no model is called."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.tiered import catalogue, customer, governed, store
from tests.core.needs_data import needs_warehouse

GOOD = {"schema": "agere-customer-definitions/1", "organization": "Test Health",
        "definitions": [
            {"id": "own_admission", "name": "Inpatient admission (Test Health)", "applies_when": r"\badmission",
             "text": "An admission is an inpatient stay at one of our own hospitals, counted once per stay.",
             "view": {"name": "cust_own_admission", "sql": "SELECT patient_id, admit_date FROM stay_episode WHERE organization_id IN (SELECT organization_id FROM own_organization)",
                      "columns": "patient_id, admit_date"}},
            {"id": "broken", "name": "Broken", "applies_when": "never matches anything here", "text": "x",
             "view": {"name": "cust_broken", "sql": "SELECT no_such_column FROM patient"}},
            {"id": "writer", "name": "Writer", "applies_when": "x", "text": "x", "view": {"name": "cust_writer", "sql": "DELETE FROM patient"}}]}


@needs_warehouse
class Customer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        Path(self.tmp.name, 'test-health.json').write_text(json.dumps(GOOD))
        self.patch = mock.patch.object(customer, 'DIRECTORY', Path(self.tmp.name)); self.patch.start()
        customer._problems.clear()

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup(); customer._problems.clear()

    def test_views_are_created_and_shown(self):
        db = store.connect()
        n = db.execute("SELECT COUNT(*) FROM cust_own_admission WHERE substr(admit_date, 1, 4) = '2028'").fetchone()[0]
        db.close()
        self.assertEqual(n, 116)
        self.assertIn('cust_own_admission', store.tables())

    def test_broken_or_writing_views_are_skipped_and_reported(self):
        store.connect().close()
        problems = ' '.join(customer.problems())
        self.assertIn('cust_broken', problems)
        self.assertIn('writer', problems)

    def test_customer_definitions_come_first_and_govern(self):
        q = 'How many admissions did we have in 2028?'
        block = catalogue.block(q)
        self.assertLess(block.index("customer's own definitions"), block.index('Governed definitions'))
        self.assertIn('cust_own_admission', block)
        self.assertEqual(governed.check(q, 'SELECT COUNT(*) FROM cust_own_admission'), [])
        self.assertTrue(catalogue.stated(q).startswith('Definitions used: Inpatient admission (Test Health)'))


if __name__ == '__main__':
    unittest.main()
