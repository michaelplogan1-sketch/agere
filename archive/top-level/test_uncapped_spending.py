import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical import source_synthesis as router, case_synthesis as single, case_passes, record_synthesis, stack_runtime, reconciliation
import test_case_passes as passes_fixture
import test_record_evidence as records_fixture


class UncappedLedger(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name)/'ledger.db';self.ledger=BudgetLedger(self.path,cap_usd=.5);self.addCleanup(self.ledger.close)
 def test_removal_persists_with_usage_history_and_strict_json(self):
  l=self.ledger;l.reserve('prior','provider','model');l.finish('prior',error='fixture')
  before=list(l.db.execute('SELECT * FROM calls'));entry=l.remove_spending_cap('Explicit user instruction: no caps on spend')
  self.assertTrue(entry['changed']);self.assertEqual(entry['prior_cap_usd'],.5)
  self.assertEqual(list(l.db.execute('SELECT * FROM calls')),before)
  other=BudgetLedger(self.path)
  try:
   r=other.receipt();self.assertEqual(r['spending_limit'],'uncapped');self.assertIsNone(r['remaining_reservation_usd']);self.assertIsNone(r['authorized_cap_usd'])
   self.assertEqual(r['calls'],{'failed':1});json.dumps(r,allow_nan=False)
   other.reserve('large','provider','model',reservation=1000000)
  finally:other.close()
  self.assertFalse(l.remove_spending_cap('Same authorized policy')['changed'])
  self.assertEqual(l.db.execute('SELECT COUNT(*) FROM authorization_changes').fetchone()[0],1)
  with self.assertRaisesRegex(ValueError,'silently'):BudgetLedger(self.path,cap_usd=50)
 def test_pending_failed_and_completed_request_protections_remain(self):
  l=self.ledger;l.remove_spending_cap('User removed cap')
  l.reserve('pending','p','m');l.reserve('failed','p','m');l.finish('failed',error='fixture')
  for key in ['pending','failed']:
   with self.assertRaisesRegex(ValueError,'no silent paid retry'):l.reserve(key,'p','m')
  l.reserve('done','p','m');l.finish('done',response={'model':'m','value':'cached'})
  self.assertEqual(l.reserve('done','p','m')['value'],'cached')
  self.assertEqual(l.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],3)
 def test_existing_connections_observe_removed_cap(self):
  other=BudgetLedger(self.path)
  try:
   self.ledger.reserve('old','p','m');self.ledger.remove_spending_cap('User removed cap')
   other.reserve('new','p','m');self.assertIsNone(other.receipt()['authorized_cap_usd'])
  finally:other.close()
 def test_explicit_authorization_required_and_invalid_reservations_rejected(self):
  for evidence in ['',None,False]:
   with self.assertRaisesRegex(ValueError,'Authorization'):self.ledger.remove_spending_cap(evidence)
  self.ledger.remove_spending_cap('User removed cap')
  for amount in [-1,float('inf'),float('nan'),True]:
   with self.assertRaisesRegex(ValueError,'reservation'):self.ledger.reserve('bad','p','m',amount)
 def test_explicit_finite_ledgers_still_enforce_their_policy(self):
  self.ledger.reserve('first','p','m')
  with self.assertRaisesRegex(ValueError,'exhausted'):self.ledger.reserve('second','p','m')
 def test_synthesis_plan_reports_uncapped_without_infinity(self):
  l=MagicMock();l.receipt.return_value={'remaining_reservation_usd':None,'authorized_cap_usd':None,'spending_limit':'uncapped'}
  stack=SimpleNamespace(path=self.path.parent,allow_live=True)
  with patch.object(stack_runtime,'BudgetLedger',return_value=l),patch.object(reconciliation,'preview_context',return_value={'reservation_ceiling_usd':100}),patch.object(reconciliation,'synthesize_context',side_effect=ValueError('Live synthesis route disabled')):
   result=stack_runtime.Stack.plan_context(stack,'context');self.assertTrue(result['can_dispatch']);json.dumps(result,allow_nan=False)
   stack.allow_live=False;self.assertFalse(stack_runtime.Stack.plan_context(stack,'context')['can_dispatch'])


class UncappedSynthesis(unittest.TestCase):
 def test_category_engine_runs_and_replays_without_changing_its_contract(self):
  passes_fixture.CasePasses.setUp(self)
  self.ledger.remove_spending_cap('User removed cap')
  sender=lambda *a,**kw:passes_fixture.CasePasses.sender(self,*a,**kw)
  self.answer=lambda body:passes_fixture.CasePasses.answer(self,body)
  self.raw=lambda body,value:passes_fixture.CasePasses.raw(self,body,value)
  # The router only replays historical engines; new contexts take the stage route.
  # Run the engine live through the router's uncapped adapter, then replay via the router.
  with patch.object(router,'engine',return_value=case_passes),patch.object(single,'send',side_effect=sender),patch.object(single,'token_check',return_value={'input_tokens':600,'provider_count_cached':False}):
   a=case_passes.synthesize(self.path,self.row['id'],router.DispatchLedger(self.ledger),allow_live=True)
   b=router.synthesize(self.path,self.row['id'],self.ledger)
  self.assertGreater(a['new_paid_api_calls'],0);self.assertEqual(a['id'],b['id']);self.assertEqual(b['new_paid_api_calls'],0)
  json.dumps(a,allow_nan=False);self.assertIsNone(self.ledger.receipt()['remaining_reservation_usd'])
 def test_record_engine_runs_and_replays_without_changing_its_contract(self):
  records_fixture.RecordRunner.setUp(self)
  self.ledger.remove_spending_cap('User removed cap')
  sender=lambda *a,**kw:passes_fixture.CasePasses.sender(self,*a,**kw)
  self.answer=lambda body:passes_fixture.CasePasses.answer(self,body)
  self.raw=lambda body,value:passes_fixture.CasePasses.raw(self,body,value)
  # The router only replays historical engines; new contexts take the stage route.
  # Run the engine live through the router's uncapped adapter, then replay via the router.
  with patch.object(router,'engine',return_value=record_synthesis),patch.object(single,'send',side_effect=sender),patch.object(single,'token_check',return_value={'input_tokens':600,'provider_count_cached':False}):
   a=record_synthesis.synthesize(self.path,self.row['id'],router.DispatchLedger(self.ledger),allow_live=True)
   b=router.synthesize(self.path,self.row['id'],self.ledger)
  self.assertGreater(a['new_paid_api_calls'],0);self.assertEqual(a['id'],b['id']);self.assertEqual(b['new_paid_api_calls'],0);json.dumps(a,allow_nan=False)

if __name__=='__main__':unittest.main()
