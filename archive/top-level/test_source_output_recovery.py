import copy
import json
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import source_output_recovery as R
from clinical_core.contracts import digest

class OutputRecovery(unittest.TestCase):
    def setUp(self):
        self.body={'model':'model', 'max_tokens':4000, 'messages':[{'content':'exact visible sources'}], 'system':'same contract', 'output_config':{}}
        self.failure={'status':'failed','request_hash':R.request_key(self.body),'error':'JSONDecodeError: Unterminated string'}
        self.raw={'model':'model','stop_reason':'max_tokens','usage':{'output_tokens':4000},'content':[]}

    def test_changes_only_output_allowance_and_key(self):
        before=copy.deepcopy(self.body)
        result=R.diagnose(self.body,self.failure,self.raw)
        self.assertEqual(result,{**self.body,'max_tokens':8192})
        self.assertEqual(before,self.body)
        self.assertNotEqual(R.request_key(result),R.request_key(self.body))
        self.assertEqual(digest({k:result[k] for k in ('model','messages','system','output_config')}),digest({k:self.body[k] for k in ('model','messages','system','output_config')}))

    def test_unsupported_failures_not_paid_retry(self):
        for failure,raw in [(dict(self.failure,status='pending'),self.raw),
                (dict(self.failure,request_hash='wrong'),self.raw),
                (dict(self.failure,error='ValueError: bad citation'),self.raw),
                (self.failure,dict(self.raw,model='other')),
                (self.failure,dict(self.raw,stop_reason='end_turn')),
                (self.failure,dict(self.raw,usage={'output_tokens':3000}))]:
            with self.subTest(failure=failure,raw=raw),self.assertRaises(ValueError):R.diagnose(self.body,failure,raw)
        body={**self.body,'max_tokens':8192}
        with self.assertRaises(ValueError):R.diagnose(body,{**self.failure,'request_hash':R.request_key(body)},dict(self.raw,usage={'output_tokens':8192}))

    def test_no_incomplete_or_wrong_model_output_reaches_parser(self):
        with patch.object(R.meaning,'parse') as parse:
            for raw in [self.raw,dict(self.raw,model='other',stop_reason='end_turn')]:
                with self.assertRaises(ValueError):R.parsed(raw,self.body,{}, {})
            parse.assert_not_called()

    def test_full_original_parser_propagates_coverage_and_citation_errors(self):
        raw={**self.raw,'stop_reason':'end_turn'}
        with patch.object(R.meaning,'parse',side_effect=ValueError('Missing or invalid source citation')) as parse:
            with self.assertRaises(ValueError):R.parsed(raw,self.body,{'U1':1},{'patient_id':'P'})
            parse.assert_called_once_with(raw,self.body,{'U1':1},{'patient_id':'P'})

    def test_stale_plan_and_disabled_live_make_no_calls(self):
        with patch.object(R,'preview',return_value={'id':'current'}),patch.object(R.single,'send') as send:
            with self.assertRaises(ValueError):R.run('.',{'context_id':'c','recovery_id':'stale'},None,allow_live=True)
            send.assert_not_called()

    def test_historical_routes_not_changed(self):
        with patch('clinical_core.agere.clinical.source_synthesis.stages',return_value=(object(),None)):
            with self.assertRaises(ValueError):R.preview('.',{'context_id':'c'})

if __name__=='__main__':unittest.main()

# Fully local synthetic transport integration. Fixture responses are not model
# evaluation targets, real provider calls or production runtime evidence.
from pathlib import Path
import test_source_meaning as fixture
from clinical_core.agere.clinical import source_synthesis as S

class RecoveryTransport(unittest.TestCase):
    raw=fixture.Meaning.raw
    answer=fixture.Meaning.answer
    def setUp(self):
        fixture.Meaning.setUp(self)
        self.row0,self.bundle,self.links,self.packets,self.oldkey=R.M.prepare(self.path,self.row['id'])
        self.source_out=R.M.location(self.path,self.oldkey).parent/'provider';self.source_out.mkdir(parents=True)
        self.assertGreaterEqual(len(self.packets),3)
        self.network=[]
        for i,(body,units) in enumerate(self.packets[:2]):
            key=R.request_key(body);self.ledger.reserve(key,'claude',body['model'])
            if i==0:
                raw=self.answer(body);raw['usage']={'input_tokens':500,'output_tokens':200}
                self.ledger.finish(key,response=raw)
            else:
                raw={'model':body['model'],'stop_reason':'max_tokens','usage':{'output_tokens':body['max_tokens']},'content':[{'type':'text','text':'{"incomplete'}]}
                failure={'status':'failed','request_hash':key,'error':'JSONDecodeError: fixture truncation'}
                (self.source_out/(key+'-failure.json')).write_text(json.dumps(failure))
                self.ledger.finish(key,error=failure['error'])
            (self.source_out/(key+'.json')).write_text(json.dumps({'response':raw}))
            count_key=digest({k:body[k] for k in ('model','messages','system','output_config')})
            (self.source_out/(count_key+'-tokens.json')).write_text(json.dumps({'request_hash':count_key,'input_tokens':500}))
        self.before={f.name:f.read_bytes() for f in self.source_out.glob('*')}
        p=patch.object(S,'stages',return_value=(R.M,None));p.start();self.addCleanup(p.stop)
        self.plan=R.preview(self.path,{'context_id':self.row['id']})
        self.request={'context_id':self.row['id'],'recovery_id':self.plan['id']}

    def send(self,body,provider,ledger,out,*,parse):
        key=R.request_key(body);old=ledger.db.execute('SELECT status,response FROM calls WHERE id=?',(key,)).fetchone()
        if old:
            self.assertEqual(old[0],'validated')
            return parse(json.loads(old[1])),{'request_hash':key,'cache_hit':True}
        self.network.append(key);ledger.reserve(key,provider,body['model'])
        raw=self.answer(body);raw['usage']={'input_tokens':500,'output_tokens':200}
        result=parse(raw);ledger.finish(key,response=raw)
        (out/(key+'.json')).write_text(json.dumps({'response':raw}))
        return result,{'request_hash':key,'cache_hit':False,'usage':raw['usage']}

    def execute(self):
        with patch.object(R.single,'send',side_effect=self.send),patch.object(R.single,'token_check',return_value={'input_tokens':500,'provider_count_cached':True}):
            return R.run(self.path,self.request,self.ledger,allow_live=True)

    def test_reuse_validate_reopen_and_zero_call_replay(self):
        answer=self.execute()
        self.assertEqual(answer['reused_source_passes'],1)
        self.assertEqual(answer['new_paid_api_calls'],len(self.packets)-1)
        self.assertEqual(answer['coverage']['received_units'],len(self.bundle['unit_manifest']))
        self.assertTrue(answer['coverage']['unassessed_units'])
        self.assertFalse(answer['coverage']['cross_group_relations_assessed'])
        self.assertEqual({f.name:f.read_bytes() for f in self.source_out.glob('*')},self.before)
        failed=self.plan['failures'][0]['failed_request_hash']
        self.assertNotIn(failed,self.network)
        self.assertEqual(self.ledger.db.execute('SELECT status FROM calls WHERE id=?',(failed,)).fetchone()[0],'failed')
        self.assertEqual(R.inspect(self.path,answer['id']),answer)
        self.assertEqual(S.inspect(self.path,answer['id']),answer)
        with patch.object(R.single,'send') as send:
            replay=R.run(self.path,self.request,self.ledger)
            self.assertEqual(replay['new_paid_api_calls'],0);send.assert_not_called()
        # Raw response mutation, even with a retained stage receipt, fails closed.
        body,_=R.prepare(self.path,self.row['id'])[3][1];f=R.location(self.path,answer['id']).parent/'provider'/(R.request_key(body)+'.json')
        raw=json.loads(f.read_text());raw['response']['stop_reason']='max_tokens';f.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):R.inspect(self.path,answer['id'])

    def test_disabled_live_and_corrected_failed_request_blocked(self):
        with patch.object(R.single,'send') as send:
            with self.assertRaisesRegex(ValueError,'disabled'):R.run(self.path,self.request,self.ledger)
            send.assert_not_called()
        key=self.plan['failures'][0]['corrected_request_hash'];body=R.prepare(self.path,self.row['id'])[3][1][0]
        self.ledger.reserve(key,'claude',body['model']);self.ledger.finish(key,error='fixture second failure')
        with self.assertRaisesRegex(ValueError,'no silent retry'):self.execute()
        self.assertEqual(self.network,[])

    def test_changed_sources_block_recovery_without_calls(self):
        self.db.execute("DELETE FROM records WHERE id='r0'");self.db.commit()
        with patch.object(R.single,'send') as send:
            with self.assertRaises(ValueError):R.run(self.path,self.request,self.ledger,allow_live=True)
            send.assert_not_called()
