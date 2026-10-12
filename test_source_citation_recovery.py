"""Local transport fixtures only; no clinical or provider-quality evaluation."""
import copy
import json
import unittest
from unittest.mock import patch
import test_source_meaning as F
from clinical_core.agere.clinical import source_citation_recovery as R, source_synthesis as S


class CitationRecovery(unittest.TestCase):
    raw = F.Meaning.raw
    answer = F.Meaning.answer

    def setUp(self):
        F.Meaning.setUp(self)
        from clinical_core.agere.clinical import context_builder as C
        for record in self.records:
            record.update(alpha='one', beta='two', gamma='three')
            self.db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(record),record['record_id']))
        self.db.commit()
        revised = C.pack('p',self.params,C.units('p',self.records,[],self.params,'2026-03-01'),{'snapshot':{'as_of':'2026-03-01'}})
        revised.update(id=self.row['id'],identity='source-v1',parameters=self.params)
        self.row.clear();self.row.update(revised)
        self.row0,self.bundle,self.links,self.packets,self.oldkey = R.O.M.prepare(self.path,self.row['id'])
        self.source_out = R.O.M.location(self.path,self.oldkey).parent/'provider'
        self.source_out.mkdir(parents=True)
        self.network = []
        for index,(body,units) in enumerate(self.packets[:2]):
            key = R.O.request_key(body);raw = self.answer(body)
            raw['usage'] = {'input_tokens':500,'output_tokens':200}
            self.ledger.reserve(key,'claude',body['model'])
            if index == 0:
                self.ledger.finish(key,response=raw)
            else:
                unit = next(k for k,u in units.items() if u['unit']['kind']=='structured')
                fields = json.loads(units[unit]['text'])
                keys = list(fields)
                quote = ','.join(json.dumps(k)+':'+json.dumps(fields[k],separators=(',',':'),ensure_ascii=False) for k in (keys[0],keys[-1]))
                value = json.loads(raw['content'][0]['text'])
                value['findings'][0]['citations'].append({'unit':unit,'quote':quote})
                raw = {**raw,'content':[{'type':'text','text':json.dumps(value)}]}
                failure = {'status':'failed','request_hash':key,'error':'ValueError: '+R.ERROR}
                (self.source_out/(key+'-failure.json')).write_text(json.dumps(failure))
                self.ledger.finish(key,error=failure['error'])
                self.bad_body,self.bad_units,self.bad_raw,self.failure = body,units,raw,failure
            (self.source_out/(key+'.json')).write_text(json.dumps({'response':raw}))
        self.before = {f.name:f.read_bytes() for f in self.source_out.glob('*')}
        p = patch.object(S,'stages',return_value=(R.O.M,None));p.start();self.addCleanup(p.stop)
        self.plan = R.preview(self.path,{'context_id':self.row['id']})
        self.request = {'context_id':self.row['id'],'recovery_id':self.plan['id']}

    def send(self,body,provider,ledger,out,*,parse):
        key = R.O.request_key(body)
        old = ledger.db.execute('SELECT status,response FROM calls WHERE id=?',(key,)).fetchone()
        if old:
            self.assertEqual(old[0],'validated')
            return parse(json.loads(old[1])),{'request_hash':key,'cache_hit':True}
        self.network.append(key);ledger.reserve(key,provider,body['model'])
        raw = self.answer(body);raw['usage'] = {'input_tokens':500,'output_tokens':200}
        result = parse(raw);ledger.finish(key,response=raw)
        (out/(key+'.json')).write_text(json.dumps({'response':raw}))
        return result,{'request_hash':key,'cache_hit':False,'usage':raw['usage']}

    def execute(self):
        with patch.object(R.O.single,'send',side_effect=self.send),patch.object(R.O.single,'token_check',return_value={'input_tokens':500,'provider_count_cached':True}):
            return R.run(self.path,self.request,self.ledger,allow_live=True)

    def test_correction_changes_only_instruction_preserves_raw_and_strict_parser(self):
        body,issues = R.diagnose(self.bad_body,self.bad_units,self.row,self.failure,self.bad_raw)
        self.assertEqual(body,{**self.bad_body,'system':self.bad_body['system']+R.INSTRUCTION})
        self.assertEqual(len(issues),1)
        self.assertNotEqual(R.O.request_key(body),R.O.request_key(self.bad_body))
        with self.assertRaisesRegex(ValueError,R.ERROR):
            R.O.parsed(self.bad_raw,body,self.bad_units,self.row)

    def test_reuse_full_coverage_saved_reopen_and_failed_key_preserved(self):
        answer = self.execute()
        self.assertEqual(answer['reused_source_passes'],1)
        self.assertEqual(answer['new_paid_api_calls'],len(self.packets)-1)
        self.assertEqual(answer['coverage']['received_units'],len(self.bundle['unit_manifest']))
        self.assertFalse(answer['coverage']['cross_group_relations_assessed'])
        self.assertEqual(R.inspect(self.path,answer['id']),answer)
        self.assertEqual(R.saved_for_context(self.path,self.row['id']),answer)
        self.assertEqual({f.name:f.read_bytes() for f in self.source_out.glob('*')},self.before)
        failed = self.plan['failures'][0]['failed_request_hash']
        self.assertNotIn(failed,self.network)
        self.assertEqual(self.ledger.db.execute('SELECT status FROM calls WHERE id=?',(failed,)).fetchone()[0],'failed')
        with patch.object(R.O.single,'send') as send:
            self.assertEqual(R.run(self.path,self.request,self.ledger)['new_paid_api_calls'],0)
            send.assert_not_called()

    def test_unrelated_or_unreproducible_failures_do_not_dispatch(self):
        for raw,failure in [(dict(self.bad_raw,stop_reason='max_tokens'),self.failure),
                (dict(self.bad_raw,model='other'),self.failure),
                (self.bad_raw,dict(self.failure,request_hash='wrong')),
                (self.bad_raw,dict(self.failure,error='ValueError: other')),
                (self.answer(self.bad_body),self.failure)]:
            with self.subTest(raw=raw),self.assertRaises(ValueError):
                R.diagnose(self.bad_body,self.bad_units,self.row,failure,raw)
        value = json.loads(self.bad_raw['content'][0]['text'])
        value['findings'][0]['citations'][-1]['quote'] = '"invented":"field","another":"absent"'
        with self.assertRaises(ValueError):
            R.diagnose(self.bad_body,self.bad_units,self.row,self.failure,self.raw(self.bad_body,value))

    def test_stale_plan_disabled_live_and_failed_corrected_key_block(self):
        with patch.object(R.O.single,'send') as send:
            with self.assertRaisesRegex(ValueError,'plan changed'):
                R.run(self.path,{**self.request,'recovery_id':'wrong'},self.ledger,allow_live=True)
            with self.assertRaisesRegex(ValueError,'disabled'):
                R.run(self.path,self.request,self.ledger)
            send.assert_not_called()
        key = self.plan['failures'][0]['corrected_request_hash']
        self.ledger.reserve(key,'claude',self.bad_body['model']);self.ledger.finish(key,error='corrected failure')
        with self.assertRaisesRegex(ValueError,'no silent retry'):
            self.execute()
        self.assertEqual(self.network,[])

    def test_source_race_and_raw_tamper_invalidate(self):
        answer = self.execute()
        target = R.location(self.path,answer['id']).parent/'provider'/(self.plan['failures'][0]['corrected_request_hash']+'.json')
        saved = json.loads(target.read_text());saved['response']['stop_reason']='max_tokens';target.write_text(json.dumps(saved))
        with self.assertRaises(ValueError):R.inspect(self.path,answer['id'])
        self.db.execute("DELETE FROM records WHERE id='r0'");self.db.commit()
        with patch.object(R.O.single,'send') as send:
            with self.assertRaises(ValueError):R.run(self.path,self.request,self.ledger,allow_live=True)
            send.assert_not_called()

    def test_recovery_dispatch_preserves_prior_output_contract(self):
        self.assertIs(R.recovery_engine(self.path,self.row['id']),R)
        with patch.object(R.O,'prepare',return_value=()):
            self.assertIs(R.recovery_engine(self.path,self.row['id']),R.O)


if __name__=='__main__':
    unittest.main()
