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
        self.assertEqual({name:(self.source_out/name).read_bytes() for name in self.before},self.before)
        for stage in answer['passes']:
            if stage['original_request']:
                self.assertTrue((self.source_out/(stage['usage']['request_hash']+'.json')).exists())
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
        raw=self.raw(self.bad_body,value)
        revised,issues=R.diagnose(self.bad_body,self.bad_units,self.row,self.failure,raw)
        self.assertEqual(issues[-1]['diagnosis'],'absent_exact_quote')
        self.assertEqual(revised['messages'],self.bad_body['messages'])
        with self.assertRaisesRegex(ValueError,R.ERROR):
            R.O.parsed(raw,revised,self.bad_units,self.row)

    def test_ambiguous_quote_requires_provider_correction_without_local_repair(self):
        value=json.loads(self.bad_raw['content'][0]['text'])
        citation=value['findings'][0]['citations'][-1]
        citation['quote']=':'
        self.assertGreater(self.bad_units[citation['unit']]['text'].count(':'),1)
        raw=self.raw(self.bad_body,value);before=copy.deepcopy(raw)
        revised,issues=R.diagnose(self.bad_body,self.bad_units,self.row,self.failure,raw)
        self.assertEqual(raw,before)
        self.assertEqual(issues[-1]['diagnosis'],'ambiguous_exact_quote')
        self.assertGreater(issues[-1]['occurrences'],1)
        with self.assertRaisesRegex(ValueError,R.ERROR):
            R.O.parsed(raw,revised,self.bad_units,self.row)

    def test_canonical_wire_model_failure_must_reproduce_as_citation_error(self):
        failure={**self.failure,'error':'ValueError: '+R.MODEL_BINDING_ERROR}
        revised,issues=R.diagnose(self.bad_body,self.bad_units,self.row,failure,self.bad_raw)
        self.assertEqual(revised['model'],self.bad_raw['model'])
        self.assertTrue(all(i['original_failure_error']==failure['error'] for i in issues))
        for raw in [self.answer(self.bad_body),dict(self.bad_raw,model='different')]:
            with self.assertRaises(ValueError):R.diagnose(self.bad_body,self.bad_units,self.row,failure,raw)

    def test_canonical_source_lookup_reopens_corrected_receipt(self):
        saved=self.execute()
        self.assertEqual(S.inspect(self.path,saved['id']),saved)
        with patch.object(S,'context',return_value=self.row):
            view=S.saved_source_stage(self.path,self.row['id'])
        self.assertEqual(view['answer'],saved)
        self.assertEqual(view['new_paid_api_calls'],0)

    def test_synthesis_reuses_completed_correction_before_reconciliation(self):
        from unittest.mock import Mock
        saved=self.execute();reconciliation=Mock()
        reconciliation.synthesize.return_value={'id':'reconciled','new_paid_api_calls':0,
                                                'new_token_count_calls':0,'cache_hit':True}
        for error in ['Live synthesis route disabled','Prior source pass pending/failed; no silent paid retry']:
            reconciliation.reset_mock();legacy=Mock();legacy.synthesize.side_effect=ValueError(error)
            with patch.object(S,'engine',return_value=legacy),patch.object(S,'context',return_value=self.row), \
                    patch.object(S,'stages',return_value=(R.O.M,reconciliation)), \
                    patch.object(R.O.M,'synthesize')as source:
                result=S.synthesize(self.path,self.row['id'],self.ledger)
            source.assert_not_called()
            reconciliation.synthesize.assert_called_once_with(self.path,saved['id'],self.ledger,allow_live=False)
            self.assertEqual(result['new_paid_api_calls'],0)
            self.assertTrue(result['cache_hit'])

    def test_incomplete_correction_keeps_original_failed_source_guard(self):
        from unittest.mock import Mock
        legacy=Mock();legacy.synthesize.side_effect=ValueError('Prior source pass pending/failed; no silent paid retry')
        with patch.object(S,'engine',return_value=legacy),patch.object(S,'context',return_value=self.row), \
                patch.object(R.O.M,'synthesize')as source:
            with self.assertRaisesRegex(ValueError,'Prior source pass pending/failed'):
                S.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
        source.assert_not_called()

    def test_current_api_entrypoint_preview_run_and_progress_reuse_correction(self):
        from clinical_core.agere.clinical import reconciliation as api
        saved=self.execute();route=R.O.M.prepare(self.path,self.row['id'])
        route=(R.O.M,*route)
        review={'answer_id':'a'*64,'saved_answer_available':False}
        with patch.object(S,'context',return_value=self.row),patch.object(api,'context_route',return_value=route), \
                patch.object(api,'preview',return_value=review)as preview, \
                patch.object(api,'synthesize',return_value={'id':'reconciled','new_paid_api_calls':1,'new_token_count_calls':0,'cache_hit':False})as finish, \
                patch.object(R.O.M,'synthesize')as source:
            plan=api.preview_context(self.path,self.row['id'])
            self.assertEqual(plan['reused_source_passes'],len(saved['passes']))
            self.assertEqual(plan['maximum_inference_calls'],1)
            result=api.synthesize_context(self.path,self.row['id'],self.ledger,allow_live=True)
            finish.assert_called_once_with(self.path,saved['id'],self.ledger,allow_live=True)
            self.assertEqual(result['new_paid_api_calls'],1)
            progress=api.progress_context(self.path,self.row['id'])
            self.assertEqual(progress['source_stage'],saved)
            self.assertEqual(progress['status'],'source_passes_complete_reconciliation_pending')
            source.assert_not_called()
            self.assertTrue(all(call.args==(self.path,saved['id'])for call in preview.call_args_list))

    def test_new_original_pass_failure_resumes_without_repeating_completed_correction(self):
        self.assertGreater(len(self.packets),2)
        body,units=self.packets[2];failed=R.O.request_key(body)
        def fail_later(body,provider,ledger,out,*,parse):
            if R.O.request_key(body)!=failed:return self.send(body,provider,ledger,out,parse=parse)
            raw=self.answer(body);value=json.loads(raw['content'][0]['text'])
            value['findings'][0]['citations'][0]['quote']='not present in this source'
            raw={**raw,'content':[{'type':'text','text':json.dumps(value)}]}
            failure={'status':'failed','request_hash':failed,'error':'ValueError: '+R.ERROR}
            ledger.reserve(failed,provider,body['model']);ledger.finish(failed,error=failure['error'])
            (out/(failed+'.json')).write_text(json.dumps({'response':raw}))
            (out/(failed+'-failure.json')).write_text(json.dumps(failure))
            raise ValueError(R.ERROR)
        with patch.object(R.O.single,'send',side_effect=fail_later),patch.object(R.O.single,'token_check',return_value={'input_tokens':500,'provider_count_cached':True}):
            with self.assertRaisesRegex(ValueError,R.ERROR):R.run(self.path,self.request,self.ledger,allow_live=True)
        self.assertTrue((self.source_out/(failed+'-failure.json')).exists())
        first_corrected=self.plan['failures'][0]['corrected_request_hash']
        updated=R.preview(self.path,{'context_id':self.row['id']})
        self.assertEqual(len(updated['failures']),2)
        self.request['recovery_id']=updated['id'];answer=self.execute()
        self.assertEqual(self.network.count(first_corrected),1)
        self.assertNotIn(failed,self.network)
        self.assertEqual(R.inspect(self.path,answer['id']),answer)

    def failed_correction(self,body):
        key=R.O.request_key(body);raw=self.answer(body)
        value=json.loads(raw['content'][0]['text'])
        unit=next(k for k,v in self.bad_units.items()if v['unit']['kind']=='structured')
        value['findings'][0]['citations']=[{'unit':unit,'quote':':'}]
        raw={**raw,'content':[{'type':'text','text':json.dumps(value)}]}
        failure={'status':'failed','request_hash':key,'error':'ValueError: '+R.ERROR}
        out=R.location(self.path,'0'*64).parent/'provider';out.mkdir(parents=True,exist_ok=True)
        (out/(key+'.json')).write_text(json.dumps({'response':raw}))
        (out/(key+'-failure.json')).write_text(json.dumps(failure))
        self.ledger.reserve(key,'claude',body['model']);self.ledger.finish(key,error=failure['error'])
        return key

    def test_failed_correction_gets_exact_feedback_and_is_never_resent(self):
        body=R.prepare(self.path,self.row['id'])[3][1][0]
        failed=self.failed_correction(body)
        plan=R.preview(self.path,{'context_id':self.row['id']})
        chain=plan['failures'][0]['correction_failures']
        self.assertEqual([x['failed_request_hash']for x in chain],[failed])
        next_body=R.prepare(self.path,self.row['id'])[3][1][0]
        self.assertEqual(next_body['messages'],body['messages'])
        self.assertIn('Mechanical quotation rejections:',next_body['system'])
        self.assertIn('exact_occurrences_in_unit_text',next_body['system'])
        self.request['recovery_id']=plan['id'];answer=self.execute()
        self.assertNotIn(failed,self.network)
        self.assertEqual(self.ledger.db.execute('SELECT status FROM calls WHERE id=?',(failed,)).fetchone()[0],'failed')
        self.assertEqual(R.inspect(self.path,answer['id']),answer)

    def test_repeated_identical_quotation_feedback_cannot_loop(self):
        first=R.prepare(self.path,self.row['id'])[3][1][0];self.failed_correction(first)
        second=R.prepare(self.path,self.row['id'])[3][1][0];self.failed_correction(second)
        with patch.object(R.O.single,'send')as send:
            with self.assertRaisesRegex(ValueError,'Same invalid quotation'):
                R.preview(self.path,{'context_id':self.row['id']})
            send.assert_not_called()

    def test_discussed_without_citation_needs_new_complete_provider_response(self):
        raw=self.answer(self.bad_body);value=json.loads(raw['content'][0]['text'])
        cited={c['unit']for f in value['findings']for c in f['citations']}
        unit=next(r for r in value['unit_reviews']if r['unit']not in cited)
        unit['status']='discussed'
        raw={**raw,'content':[{'type':'text','text':json.dumps(value)}]}
        failure={**self.failure,'error':'ValueError: '+R.DISPOSITION_ERROR}
        revised,issues=R.diagnose(self.bad_body,self.bad_units,self.row,failure,raw)
        self.assertEqual([i['unit']for i in issues],[unit['unit']])
        self.assertEqual(issues[0]['diagnosis'],'discussed_without_citation')
        self.assertEqual(revised['messages'],self.bad_body['messages'])
        with self.assertRaisesRegex(ValueError,R.DISPOSITION_ERROR):R.O.parsed(raw,revised,self.bad_units,self.row)
        key=failure['request_hash']
        (self.source_out/(key+'.json')).write_text(json.dumps({'response':raw}))
        (self.source_out/(key+'-failure.json')).write_text(json.dumps(failure))
        plan=R.preview(self.path,{'context_id':self.row['id']});self.request['recovery_id']=plan['id']
        saved=self.execute();self.assertEqual(R.inspect(self.path,saved['id']),saved)
        self.assertNotIn(key,self.network)

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
