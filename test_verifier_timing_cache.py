"""Stable native evidence identities and exact legacy replay; no provider calls."""
import copy,json,os,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.providers import EvidenceTask
from clinical_core.agere.gateway import BudgetLedger,PersistentGateway
from clinical_core.agere.clinical import backstop_agent as B,system_one as S,evidence_projection as E
from clinical_core.agere.deliverables.receipts import uncompact_json,readable_json

AS_OF='2030-01-31'
OPTIONS={'supported':'The received amount is supported.','unknown':'Not established.'}


def packets(elapsed=1.,*,amount=12,business_time=5,as_of=AS_OF,question='Received amount',tool='query_sql'):
    rows=[{'amount':amount,'elapsed_s':business_time,'source_record_id':'record-1','date':'2030-01-01'}]
    calls=[{'name':tool,'input':{'sql':'SELECT received_amount'},'result':{'result_view':'native_1',
        'executed_query':'SELECT received_amount','columns':list(rows[0]),'rows':rows,'elapsed_s':elapsed}}]
    answer={'headline':'Received amount','table':copy.deepcopy(rows),'table_from_tool':'native_1'}
    return B.support_packets(question,answer,calls,[],as_of),calls


class NativeIdentity(unittest.TestCase):
    def test_top_level_sql_timing_changes_neither_evidence_nor_verifier_key(self):
        first,calls=packets(1.);before=copy.deepcopy(calls);second,_=packets(9.)
        self.assertEqual(first[0]['text'],second[0]['text']);self.assertEqual(calls,before)
        self.assertEqual(calls[0]['result']['elapsed_s'],1.)
        self.assertNotEqual(first[0]['native_timing_compatibility']['legacy_text'],second[0]['native_timing_compatibility']['legacy_text'])
        state=uncompact_json(json.loads(first[0]['text']))
        self.assertEqual(state['all_final_rows'][0]['elapsed_s'],5)

    def test_business_amount_time_source_scope_and_cutoff_still_invalidate(self):
        first,_=packets()
        for change in ({'amount':13},{'business_time':6},{'as_of':'2030-02-01'},{'question':'Different population scope'}):
            with self.subTest(change=change):self.assertNotEqual(first[0]['text'],packets(**change)[0][0]['text'])
        self.assertNotEqual(packets(tool='external_business_operator')[0][0]['text'],
                            packets(2.,tool='external_business_operator')[0][0]['text'])

    def test_declared_native_legacy_view_roundtrips_and_tampering_is_not_compatible(self):
        ps,_=packets();p=ps[0];ev=[{'id':p['id'],'date':p['date'],'text':p['text']}];proof=p['native_timing_compatibility']
        legacy=E.native_timing_legacy_evidence(ev,proof);self.assertEqual(legacy[0]['text'],proof['legacy_text'])
        for change in ('source','amount','claim','date','unknown_metadata','binding','packet'):
            bad=copy.deepcopy(proof);state=uncompact_json(json.loads(bad['legacy_text']))
            if change=='source':state['shared_evidence'][0]['source_record_id']='changed'
            elif change=='amount':state['all_final_rows'][0]['amount']=99
            elif change=='claim':state['claim_manifest'][0]['field']='different'
            elif change=='date':state['as_of']='2030-01-30'
            elif change=='unknown_metadata':bad['bindings'][0]['excluded_fields']=['seconds']
            elif change=='binding':bad['bindings'][0]['current_digest']='wrong'
            else:state['id']='patient_other'
            bad['legacy_text']=json.dumps(readable_json(state));bad['legacy_fingerprint']=digest(state)
            with self.subTest(change=change):self.assertIsNone(E.native_timing_legacy_evidence(ev,bad))


class CachedNativeRequests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'ledger.sqlite3';self.ledger=BudgetLedger(self.path)
        self.no_network=patch('urllib.request.urlopen',side_effect=AssertionError('No provider dispatch'));self.no_network.start()
        self.env=patch.dict(os.environ,{'TYPESAFE_API_KEY':'authored-test-only'});self.env.start()

    def tearDown(self):
        self.env.stop();self.no_network.stop();self.ledger.close();self.tmp.cleanup()

    def evidence(self,p):return [{'id':p['id'],'date':p['date'],'text':p['text']}]

    def seed(self,p,*,canonical=False,named=None,status='validated',p_yes=.9):
        text=p['text']if canonical else p['native_timing_compatibility']['legacy_text']
        task=EvidenceTask('Check the received amount',AS_OF,{'unknown':S.UNKNOWN}if named else OPTIONS,
            S._evidence([{'id':p['id'],'date':p['date'],'text':text}],AS_OF),select_source=False,questions=named)
        prepared=PersistentGateway(self.ledger,allow_live=False).classify('jev',task);key=prepared['request_hash'];model=PersistentGateway.MODELS['jev']
        self.ledger.reserve(key,'jev',model)
        raw={'model':model,'answers':{key:{'type':'choice','choice':'supported','probabilities':{'supported':p_yes,'unknown':1-p_yes}}
             for key in (named or {'reason':None})},'usage':{'input_tokens':100,'output_tokens':10}}
        self.ledger.finish(key,response=raw if status=='validated'else None,error='authored failure'if status=='failed'else None)
        return key

    def rows(self):return list(self.ledger.db.execute('SELECT * FROM calls ORDER BY id'))

    def choose(self,p,*,live=False):
        return S.choice('Check the received amount',OPTIONS,self.evidence(p),as_of=AS_OF,ledger=self.ledger,
            allow_live=live,select_source=False,cache_evidence=p['native_timing_compatibility'])

    def test_legacy_judgment_seeds_stable_lookup_for_a_fresh_execution_without_a_charge(self):
        p,_=packets(1.);key=self.seed(p[0]);before=self.rows()
        first=self.choose(p[0]);fresh,_=packets(8.);second=self.choose(fresh[0])
        self.assertEqual(first['request_hash'],key);self.assertEqual(second['request_hash'],key)
        self.assertTrue(second['cache_hit']);self.assertEqual(second['cost_usd'],0.)
        self.assertEqual(first['cache_compatibility']['canonical_request_hash'],second['cache_compatibility']['canonical_request_hash'])
        self.assertEqual(self.rows(),before);self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM request_equivalences').fetchone()[0],1)

    def test_named_field_checks_keep_their_actual_provider_and_threshold(self):
        p=packets()[0][0];named={'amount':{'type':'choice','instructions':'Check the amount.','criteria':OPTIONS}};key=self.seed(p,named=named);before=self.rows()
        result=S.judge_fields('Check the received amount',named,self.evidence(p),as_of=AS_OF,ledger=self.ledger,
            allow_live=False,order=('jev',),cache_evidence=p['native_timing_compatibility'])
        self.assertEqual(result['judgments']['amount']['decided_by'],'jev');self.assertEqual(result['judgments']['amount']['threshold'],.8)
        self.assertEqual(result['steps'][0]['request_hash'],key);self.assertEqual(result['cost_usd'],0.);self.assertEqual(self.rows(),before)

    def test_changed_business_values_do_not_reuse_an_equivalent_cache(self):
        p=packets()[0][0];self.seed(p);self.choose(p);before=self.rows()
        for kwargs in ({'amount':99},{'business_time':8},{'question':'Different scope'}):
            changed=packets(**kwargs)[0][0]
            with self.subTest(kwargs=kwargs):self.assertEqual(self.choose(changed)['status'],'prepared')
        self.assertEqual(self.rows(),before)

    def test_source_query_and_claim_changes_invalidate_the_stable_cache(self):
        p,calls=packets();self.seed(p[0]);self.choose(p[0]);before=self.rows()
        for change in ('source','query','claim'):
            native=copy.deepcopy(calls)
            answer={'headline':'Received amount','table':copy.deepcopy(native[0]['result']['rows']),'table_from_tool':'native_1'}
            if change=='source':
                native[0]['result']['rows'][0]['source_record_id']='record-2'
                answer['table'][0]['source_record_id']='record-2'
            elif change=='query':native[0]['result']['executed_query']='SELECT different_definition'
            else:answer['headline']='A changed claim'
            changed=B.support_packets('Received amount',answer,native,[],AS_OF)[0]
            with self.subTest(change=change):self.assertEqual(self.choose(changed)['status'],'prepared')
        self.assertEqual(self.rows(),before)

    def test_model_transport_and_option_contract_changes_do_not_reuse_the_index(self):
        p=packets()[0][0];self.seed(p);self.choose(p);before=self.rows()
        with patch.dict(PersistentGateway.MODELS,{'jev':'authored-different-model'}):
            self.assertEqual(self.choose(p)['status'],'prepared')
        for change in ({'long_context':True},{'provider':'perplexity'}):
            result=S.choice('Check the received amount',OPTIONS,self.evidence(p),as_of=AS_OF,
                ledger=self.ledger,allow_live=False,select_source=False,
                cache_evidence=p['native_timing_compatibility'],**change)
            self.assertEqual(result['status'],'prepared')
        result=S.choice('Check the received amount',{'supported':'A changed meaning','unknown':'Not established.'},
            self.evidence(p),as_of=AS_OF,ledger=self.ledger,allow_live=False,select_source=False,
            cache_evidence=p['native_timing_compatibility'])
        self.assertEqual(result['status'],'prepared');self.assertEqual(self.rows(),before)

    def test_exact_canonical_response_precedes_legacy_without_selecting_the_higher_probability(self):
        p=packets()[0][0];self.seed(p,p_yes=.95);key=self.seed(p,canonical=True,p_yes=.85)
        result=self.choose(p);self.assertEqual(result['request_hash'],key);self.assertAlmostEqual(result['probabilities']['supported'],.85)
        self.assertNotIn('cache_compatibility',result)

    def test_failed_canonical_and_failed_legacy_requests_are_not_bypassed(self):
        p=packets()[0][0];self.seed(p);self.choose(p);self.seed(p,canonical=True,status='failed');before=self.rows()
        with self.assertRaisesRegex(ValueError,'Prior request pending/failed'):self.choose(p,live=True)
        self.assertEqual(self.rows(),before)
        other=packets(amount=34)[0][0];self.seed(other,status='failed');before=self.rows()
        with self.assertRaisesRegex(ValueError,'Prior compatible request pending/failed'):self.choose(other,live=True)
        self.assertEqual(self.rows(),before)

    def test_retry_authorization_remains_a_distinct_attempt_not_an_alias(self):
        p=packets()[0][0];self.seed(p);canonical=self.seed(p,canonical=True,status='failed')
        retry=self.ledger.authorize_retry(canonical,'Authored test authorization');before=self.rows()
        result=self.choose(p);self.assertEqual(result['status'],'prepared');self.assertEqual(result['request_hash'],retry['request_hash']);self.assertEqual(self.rows(),before)

    def test_pending_canonical_and_pending_legacy_attempts_stay_pending_without_dispatch(self):
        p=packets()[0][0];self.seed(p);key=self.seed(p,canonical=True,status='failed')
        with self.ledger.db:self.ledger.db.execute("UPDATE calls SET status='pending'WHERE id=?",(key,))
        before=self.rows()
        with self.assertRaisesRegex(ValueError,'Prior request pending/failed'):self.choose(p,live=True)
        self.assertEqual(self.rows(),before)
        other=packets(amount=36)[0][0];key=self.seed(other,status='failed')
        with self.ledger.db:self.ledger.db.execute("UPDATE calls SET status='pending'WHERE id=?",(key,))
        before=self.rows()
        with self.assertRaisesRegex(ValueError,'Prior compatible request pending/failed'):self.choose(other,live=True)
        self.assertEqual(self.rows(),before)

    def test_readonly_legacy_lookup_preserves_probabilities_without_writing_an_index(self):
        p=packets()[0][0];key=self.seed(p);db=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)
        try:
            custom=SimpleNamespace(path=self.path,db=db,resolve_request_key=lambda k:k)
            result=S.choice('Check the received amount',OPTIONS,self.evidence(p),as_of=AS_OF,ledger=custom,
                allow_live=False,select_source=False,cache_evidence=p['native_timing_compatibility'])
            self.assertEqual(result['request_hash'],key);self.assertFalse(result['cache_compatibility']['binding_persisted'])
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='request_equivalences'").fetchone())
        finally:db.close()

    def test_corrupt_equivalence_or_changed_original_response_cannot_return_a_success(self):
        p=packets()[0][0];key=self.seed(p);result=self.choose(p);canonical=result['cache_compatibility']['canonical_request_hash']
        with self.ledger.db:self.ledger.db.execute('UPDATE calls SET seconds=99 WHERE id=?',(key,))
        with self.assertRaisesRegex(ValueError,'target changed'):self.choose(p)
        other=packets(amount=35)[0][0];self.seed(other);r=self.choose(other)
        with self.ledger.db:self.ledger.db.execute("UPDATE request_equivalences SET payload='{}' WHERE request_key=?",(r['cache_compatibility']['canonical_request_hash'],))
        with self.assertRaisesRegex(ValueError,'binding changed'):self.choose(other)


if __name__=='__main__':unittest.main()
