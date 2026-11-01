"""Reservation authorization stays atomic and independent of usage pricing."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.economics import usage_cost


class ReservationAuthorization(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name)/'ledger.sqlite3'

 def ledger(self,**kwargs):
  value=BudgetLedger(self.path,**kwargs);self.addCleanup(value.close);return value

 def test_uncapped_new_request_needs_no_historical_usage_pricing(self):
  b=self.ledger();raw={'model':'jev-1.13.0','usage':{'input_tokens':100,'output_tokens':10}}
  b.reserve('done','jev',raw['model']);b.finish('done',response=raw)
  old=b.db.execute('SELECT * FROM calls WHERE id=?',('done',)).fetchone()
  with patch('clinical_core.agere.economics.usage_cost',side_effect=AssertionError('Pricing unavailable')):
   self.assertIsNone(b.reserve('fresh','jev',raw['model']))
   self.assertEqual(b.reserve('done','jev',raw['model']),raw)
  self.assertEqual(b.db.execute('SELECT * FROM calls WHERE id=?',('done',)).fetchone(),old)
  receipt=b.receipt()
  self.assertIsNone(receipt['remaining_reservation_usd'])
  self.assertEqual(receipt['current_reserved_usd'],1.0)
  self.assertEqual(receipt['new_estimated_api_usd'],usage_cost(raw['model'],raw['usage'])['estimated_usd'])
  self.assertEqual(receipt['calls'],{'pending':1,'validated':1})

 def test_cap_counts_imports_and_failed_reservations_without_pricing(self):
  b=self.ledger(cap_usd=1.0)
  with b.db:b.db.execute('INSERT INTO imports VALUES (?,?)',('legacy',.25))
  b.reserve('failed','jev','jev-1.13.0',reservation=.25);b.finish('failed',error='Retained failure')
  old=b.db.execute('SELECT * FROM calls WHERE id=?',('failed',)).fetchone()
  with patch('clinical_core.agere.economics.usage_cost',side_effect=AssertionError('Pricing unavailable')):
   self.assertIsNone(b.reserve('remaining','jev','jev-1.13.0',reservation=.5))
   with self.assertRaisesRegex(ValueError,'authorization exhausted'):b.reserve('extra','jev','jev-1.13.0',reservation=.001)
  self.assertEqual(b.db.execute('SELECT * FROM calls WHERE id=?',('failed',)).fetchone(),old)
  self.assertIsNone(b.db.execute('SELECT * FROM calls WHERE id=?',('extra',)).fetchone())
  self.assertEqual(b.receipt()['remaining_reservation_usd'],0)

 def test_explicit_cap_change_after_open_is_observed(self):
  b=self.ledger();self.assertIsNone(b.cap)
  other=self.ledger()
  with other.db:other.db.execute('UPDATE authorization SET cap=.25 WHERE id=1')
  with self.assertRaisesRegex(ValueError,'authorization exhausted'):b.reserve('too_large','jev','jev-1.13.0',reservation=.5)
  self.assertEqual(b.cap,.25)
  self.assertEqual(b.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],0)

 def test_two_connections_cannot_over_reserve_the_same_remaining_cap(self):
  b=self.ledger(cap_usd=.5);barrier=threading.Barrier(2)
  def reserve(key):
   own=BudgetLedger(self.path)
   try:
    barrier.wait(timeout=5)
    try:own.reserve(key,'jev','jev-1.13.0',reservation=.5);return 'reserved'
    except ValueError as exc:
     self.assertIn('authorization exhausted',str(exc));return 'exhausted'
   finally:own.close()
  with ThreadPoolExecutor(max_workers=2)as pool:results=list(pool.map(reserve,['one','two']))
  self.assertCountEqual(results,['reserved','exhausted'])
  self.assertEqual(b.db.execute('SELECT COUNT(*),SUM(reserved) FROM calls').fetchone(),(1,.5))

 def test_pending_and_failed_keys_remain_blocked_and_unchanged(self):
  b=self.ledger();b.reserve('pending','jev','jev-1.13.0');b.reserve('failed','jev','jev-1.13.0');b.finish('failed',error='Retain this attempt')
  before=list(b.db.execute('SELECT * FROM calls ORDER BY id'))
  for key in ('pending','failed'):
   with self.assertRaisesRegex(ValueError,'explicit new attempt required'):b.reserve(key,'jev','jev-1.13.0')
  self.assertEqual(list(b.db.execute('SELECT * FROM calls ORDER BY id')),before)


if __name__=='__main__':unittest.main()
