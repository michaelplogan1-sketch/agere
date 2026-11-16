import json
import unittest
from unittest.mock import patch
import test_need_candidates as base
import test_financial_accounts as fixture
from clinical_core.agere.clinical import need_accounts as a


class NeedAccounts(unittest.TestCase):
    setUpBase=base.Candidates.setUpBase
    put=base.Candidates.put
    chain=base.Candidates.chain
    create=base.Candidates.create
    hit=base.Candidates.hit

    def setUp(self):
        base.Candidates.setUp(self)
        rows=[fixture.source('attempt','medical_payment_attempts'),fixture.snapshot('snapshot'),
            fixture.source('payment','patient_payments',amount_cents=300,status='received'),fixture.snapshot('unrelated',account_id='other')]
        for r in rows:
            r['patient_id']='P-1';r['_warehouse_fields']=[k for k in r if not k.startswith('_')]
        self.put(*rows);self.answer['context_id']=self.create(['attempt'])['id']
        self.answer['review_steps'][0]['question']='Which records show account balances?'
        self.need_receipt=a.candidates.needs.prepare(self.path,self.answer['id'])
        self.request={'receipt_id':self.need_receipt['id'],'need_id':'need1'}

    def test_exact_account_group_reuses_integer_cent_projection(self):
        r=a.prepare(self.path,self.request);self.assertEqual(r['matched_accounts'],1);account=r['accounts'][0]
        self.assertEqual(account['account_id'],'a');self.assertEqual(account['anchor_record_ids'],['attempt'])
        self.assertEqual(account['outside_original_scope'],2);self.assertEqual(account['snapshot_amounts']['balance_cents'],700)
        self.assertEqual(account['status'],'reconciled_received_records');self.assertNotIn('unrelated',account['source_record_ids'])
        self.assertEqual(account['cash_comparisons'][0]['received_transactions_cents'],300)
        self.assertFalse(r['meaning_resolved']);self.assertFalse(r['hardship_established']);self.assertFalse(r['realized_savings_established'])

    def test_cached_replay_preserves_needs_and_selects_complete_received_group(self):
        r=a.prepare(self.path,self.request);self.assertTrue(a.prepare(self.path,self.request)['cache_hit'])
        selected=a.context(self.path,{'account_receipt_id':r['id'],'account_id':'a'})
        self.assertEqual(a.inspect_context(self.path,selected['id']),selected)
        self.assertEqual(selected['record_scope']['record_ids'],['attempt','payment','snapshot']);self.assertFalse(selected['capture_complete'])
        self.assertEqual(a.candidates.needs.inspect(self.path,self.need_receipt['id']),self.need_receipt)
        with self.assertRaisesRegex(ValueError,'not bound'):a.context(self.path,{'account_receipt_id':r['id'],'account_id':'other'})

    def test_nonfinancial_scope_has_no_inferred_account(self):
        self.answer['context_id']=self.create(['extra'])['id'];r=a.candidates.needs.prepare(self.path,self.answer['id'])
        result=a.prepare(self.path,{'receipt_id':r['id'],'need_id':'need1'})
        self.assertEqual(result['accounts'],[]);self.assertEqual(result['status'],'no_registered_account_binding_in_original_scope')

    def test_source_change_and_tampered_projection_invalidates(self):
        r=a.prepare(self.path,self.request);target=a.candidates.location(self.path,r['id'],'need-accounts')
        raw=json.loads(target.read_text());raw['accounts'][0]['snapshot_amounts']['balance_cents']=0;target.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ValueError,'identity'):a.inspect(self.path,r['id'])
        raw['accounts'][0]['snapshot_amounts']['balance_cents']=700;target.write_text(json.dumps(raw))
        self.answer['review_steps'][0]['question']='Changed question'
        with self.assertRaises(ValueError):a.inspect(self.path,r['id'])

    def test_source_change_during_projection_cannot_save(self):
        original=a.finance.project
        def changed(*args,**kwargs):
            report=original(*args,**kwargs);self.answer['review_steps'][0]['question']='Changed question';return report
        with patch.object(a.finance,'project',side_effect=changed):
            with self.assertRaises(ValueError):a.prepare(self.path,self.request)
        self.assertFalse((self.path/'need-accounts').exists())


if __name__=='__main__':unittest.main()
