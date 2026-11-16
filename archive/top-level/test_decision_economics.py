import json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import decision_economics as E,question_decision as q
from clinical_core.agere.economics import usage_cost
import test_question_decision as D


class Accounting(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.ledger=self.root/'ledger.sqlite3'
        with sqlite3.connect(self.ledger) as db:db.execute('CREATE TABLE calls(id TEXT PRIMARY KEY,provider TEXT,model TEXT,reserved REAL,status TEXT,response TEXT,error TEXT,seconds REAL)')
    def call(self,key,status='validated',usage=None):
        raw={'model':'claude-opus-5-5','usage':usage or {'input_tokens':1000,'output_tokens':100}}
        with sqlite3.connect(self.ledger) as db:db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?)',(key,'claude',raw['model'],.5,status,json.dumps(raw) if status=='validated' else None,None,2.5))
        return raw
    def test_duplicate_cache_refs_recover_original_usage_once(self):
        key='a'*64;raw=self.call(key);refs={}
        E.request_refs([{'request_hash':key,'cache_hit':True},{'usage':{'request_hash':key}}],'source',refs)
        E.request_refs({'request_hash':key},'reconciliation',refs)
        rows,cost,binding=E.account(refs,self.ledger)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['roles'],['reconciliation','source'])
        self.assertEqual(cost['known_linked_api_estimate_usd'],usage_cost(raw['model'],raw['usage'])['estimated_usd'])
        self.assertFalse(cost['reservations_are_spend']);self.assertIsNone(cost['total_cost_usd']);self.assertEqual(cost['new_paid_api_calls'],0)
    def test_failed_pending_and_unpriced_are_not_zero_or_retried(self):
        refs={}
        for i,status in enumerate(('failed','pending','validated')):
            key=str(i)*64;self.call(key,status,{'input_tokens':4})
            E.request_refs({'request_hash':key},status,refs)
        E.request_refs({'request_hash':'f'*64},'not_dispatched',refs)
        rows,cost,_=E.account(refs,self.ledger)
        self.assertEqual(cost['unpriced_dispatched_requests'],4);self.assertIsNone(cost['linked_api_estimate_usd'])
        self.assertEqual(cost['statuses'],{'failed':1,'pending':1,'validated':1,'missing_ledger_entry':1})
        with sqlite3.connect(self.ledger) as db:self.assertEqual(db.execute('SELECT count(*) FROM calls').fetchone()[0],3)
    def test_failed_returned_usage_priced_and_bound_without_revalidation(self):
        key='a'*64;raw=self.call(key,'failed');file=self.root/(key+'.json');file.write_text(json.dumps({'response':raw}))
        refs={};E.request_refs({'request_hash':key},'source',refs);refs[key]['receipt_files'].add(str(file))
        rows,cost,binding=E.account(refs,self.ledger)
        self.assertEqual(rows[0]['usage_basis'],'retained_failed_response');self.assertEqual(rows[0]['status'],'failed')
        self.assertEqual(cost['unpriced_dispatched_requests'],0);self.assertTrue(binding[0]['failed_receipt_files'])
        with sqlite3.connect(self.ledger) as db:self.assertEqual(db.execute('SELECT status FROM calls').fetchone()[0],'failed')
    def test_exact_body_reconstruction_uses_real_key_and_bound(self):
        body={'model':'fixture','messages':[{'content':'small'}]};refs={};E.add_body(body,'source',self.root,refs)
        key=next(iter(refs));self.assertEqual(len(key),64)
        rows,cost,_=E.account(refs,self.ledger);self.assertTrue(rows[0]['bounded_request_verified']);self.assertEqual(rows[0]['status'],'not_dispatched')
        self.assertEqual(cost['dispatched_requests'],0)


class SavedQuestion(unittest.TestCase):
    setUpBase=D.Decision.setUp
    patch=D.Decision.patch
    prepare=D.Decision.prepare
    queue=D.Decision.queue
    def setUp(self):
        self.setUpBase();self.answer['context_id']=self.binding['context_id'];self.answer['usage']=[{'request_hash':'a'*64,'cache_hit':True}]
        self.ledger=self.path/'test-cost-ledger.sqlite3'
        with sqlite3.connect(self.ledger) as db:
            db.execute('CREATE TABLE calls(id TEXT PRIMARY KEY,provider TEXT,model TEXT,reserved REAL,status TEXT,response TEXT,error TEXT,seconds REAL)')
            db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?)',('a'*64,'claude','claude-opus-5-5',.5,'validated',json.dumps({'usage':{'input_tokens':100,'output_tokens':10}}),None,1.0))
        self.patch(q.answers,'stages',side_effect=ValueError('fixture does not reconstruct historical source bodies'))
    def run_report(self,d):return E.run(self.retriever,{'decision_id':d['id']},ledger_path=self.ledger)
    def open(self,r):return E.reopen(self.retriever,{'decision_id':r['decision_id'],'result_id':r['result_id']},ledger_path=self.ledger)
    def test_saved_route_cost_replay_and_same_answer_baseline(self):
        self.answer['token_check']={'request_hash':'f'*64,'input_tokens':100}
        d=self.prepare();self.queue(d);r=self.run_report(d);self.assertEqual(r,self.run_report(d));self.assertTrue(self.open(r)['current'])
        self.assertEqual(r['route']['input_digest'],d['answer_digest']);self.assertEqual(r['route']['baseline_mapped_findings'],1)
        self.assertEqual(len(r['work']),1);self.assertFalse(r['full_cost_accounting_complete']);self.assertEqual(r['cost']['dispatched_requests'],1)
        self.assertEqual([r['request_hash'] for r in r['requests']],['a'*64])
    def test_ledger_change_makes_historical_no_mutation(self):
        d=self.prepare();r=self.run_report(d)
        with sqlite3.connect(self.ledger) as db:db.execute("UPDATE calls SET status='failed',response=NULL")
        self.assertFalse(self.open(r)['current']);self.assertEqual(q.saved_followup(self.path,r['result_id']),r)
        new=self.run_report(d);self.assertIsNone(new['cost']['linked_api_estimate_usd']);self.assertNotEqual(new['result_id'],r['result_id'])
    def test_other_decision_and_extra_ledger_path_rejected(self):
        d=self.prepare();r=self.run_report(d)
        with self.assertRaises(ValueError):E.run(self.retriever,{'decision_id':d['id'],'ledger_path':'anything'},ledger_path=self.ledger)
        with self.assertRaises(ValueError):E.reopen(self.retriever,{'decision_id':'other','result_id':r['result_id']},ledger_path=self.ledger)
    def test_question_or_work_changes_mark_historical(self):
        d=self.prepare();task=self.queue(d);r=self.run_report(d)
        q.event(self.retriever,{'decision_id':d['id'],'task_id':task['id'],'expected_revision':task['revision'],'request_id':'update','action':'prepare_handoff','actor':'fixture','note':'Internal only'})
        self.assertFalse(self.open(r)['current'])
        self.answer['findings'][0]['text']='Different answer';self.assertFalse(self.open(r)['current'])
    def test_mid_audit_ledger_change_prevents_save(self):
        d=self.prepare();real=E.prepare;count=0
        def changing(*args,**kwargs):
            nonlocal count
            result=real(*args,**kwargs);count+=1
            if count==2:result['cost']['statuses']={}
            return result
        with patch.object(E,'prepare',side_effect=changing):
            with self.assertRaises(ValueError):self.run_report(d)
        with q.database(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM question_decision_followups').fetchone()[0],0)


if __name__=='__main__':unittest.main()
