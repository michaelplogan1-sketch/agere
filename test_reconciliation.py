"""Current reference contract, exact source binding and historical route guards."""
import copy
import json
import unittest
from unittest.mock import patch

import test_case_passes as F
import test_linked_claim_reconciliation as T
from clinical_core.agere.clinical import reconciliation as R
from clinical_core.contracts import digest


class Reconciliation(unittest.TestCase):
    raw = F.CasePasses.raw
    answer = F.CasePasses.answer
    sender = F.CasePasses.sender
    run_synthesis = F.CasePasses.run_synthesis

    def setUp(self):
        T.LinkedClaims.setUp(self)
        self.job = R.request(self.parent, self.row, self.refs, self.received)

    def text(self, value, refs=()):
        return {'text': value, 'unit_refs': list(refs)}

    def result(self):
        excerpt = next(iter(self.job['excerpt_pool']))
        unit = self.job['excerpt_pool'][excerpt]['unit']
        return {'findings': [{'id': 'f1', 'kind': 'documented_account',
            'text': self.text('The source describes an unconfirmed request.', [unit]),
            'excerpt_ids': [excerpt], 'unresolved': [self.text('Completion is not established.')]}],
            'claim_reviews': [{'input_id': key, 'status': 'unassessed', 'finding_ids': [],
                              'reason': self.text('The excerpt cannot establish completion.')}
                             for key in self.job['finding_ids']],
            'unanswered': [self.text('Full source meaning has not been revalidated.')],
            'review_steps': [{'kind': 'review_source_meaning', 'finding_ids': ['f1'],
                              'question': self.text('What does the source establish?', [unit])}]}

    def parsed(self, value=None):
        return R.parse(self.raw(self.job['body'], value or self.result()), self.job, self.refs)

    def test_explicit_refs_render_exact_records_without_guessing(self):
        value = self.result(); original = copy.deepcopy(value); result = self.parsed(value)
        unit = value['findings'][0]['text']['unit_refs'][0]
        self.assertEqual(value, original)
        self.assertIn(unit, result['findings'][0]['text'])
        self.assertEqual(result['review_steps'][0]['evidence_references'], [self.refs[unit]])
        self.assertEqual(result['reference_bindings'][0]['unit_refs'], [unit])
        self.assertEqual(result['findings'][0]['citations'][0]['source_unit_id'], self.refs[unit]['source_unit_id'])

    def test_no_bare_or_qualified_ids_in_prose(self):
        for text in ('Review u1', 'Review p1:u1'):
            v = self.result(); v['findings'][0]['text']['text'] = text
            with self.assertRaisesRegex(ValueError, 'explicit fields'): self.parsed(v)

    def test_unknown_duplicate_and_uncited_references_rejected(self):
        uncited = next(n for n in self.refs if n not in self.job['units'])
        known = next(iter(self.job['units']))
        for names in (['p999:u1'], [known, known], [uncited], [known] * 5):
            v = self.result(); v['review_steps'][0]['question']['unit_refs'] = names
            with self.assertRaisesRegex(ValueError, 'explicit unit reference'): self.parsed(v)

    def test_strict_citation_and_claim_accounting_still_apply(self):
        v = self.result(); v['findings'][0]['excerpt_ids'] = ['invented']
        with self.assertRaisesRegex(ValueError, 'Unknown or duplicate excerpt'): self.parsed(v)
        v = self.result(); v['claim_reviews'].pop()
        with self.assertRaisesRegex(ValueError, 'Every original claim'): self.parsed(v)
        v = self.result(); v['claim_reviews'][0].update(status=R.L.STATES[0], finding_ids=[])
        with self.assertRaisesRegex(ValueError, 'need a consolidated finding'): self.parsed(v)

    def test_transport_retains_all_original_claim_text(self):
        wire = json.loads(self.job['body']['messages'][0]['content'])
        claims = {r[0]: r for r in wire['claims']['rows']}
        for p in self.parent['passes']:
            for f in p['result']['findings']:
                self.assertEqual(claims[f['id']][3:5], [f['text'], f['unresolved']])
                self.assertEqual([self.job['excerpt_pool'][n]['quote'] for n in claims[f['id']][5]],
                                 [c['quote'] for c in f['citations']])
        self.assertFalse(wire['retained_parent_scope']['source_coverage_upgraded'])
        self.assertEqual(self.parent, self.original)
        self.assertLessEqual(R.P.request_bytes(self.job['body']), R.G.MAX_BYTES)

    def fake_send(self, body, ledger, out, *, parse):
        raw = self.raw(body, self.result()); result = parse(raw); key = R.G.request_key(body)
        ledger.reserve(key, 'claude', body['model']); ledger.finish(key, response=raw)
        out.mkdir(parents=True,exist_ok=True)
        (out/(key+'.json')).write_text(json.dumps({'response':raw}))
        return result, {'cache_hit': False, 'request_hash': key, 'usage': {}}

    def test_receipt_replays_without_resend_and_preserves_parent_coverage(self):
        self.parent['result_digest'] = 'historical-parent-digest'
        self.parent['coverage'].update(system_unassessed_units=4)
        with patch.object(R.G, 'send', side_effect=self.fake_send), patch.object(R.C, 'token_check',
                return_value={'input_tokens': 750, 'provider_count_cached': False}):
            answer = R.synthesize(self.path, self.parent['id'], self.ledger, allow_live=True)
        saved = {k: v for k, v in answer.items() if k != 'result_digest'}
        self.assertEqual(answer['result_digest'], digest(saved))
        self.assertEqual(answer['coverage']['system_unassessed_units'], 4)
        self.assertFalse(answer['coverage']['source_coverage_upgraded'])
        self.assertEqual(answer['passes'], self.parent['passes'])
        self.assertEqual(R.inspect(self.path, answer['id']), answer)
        with patch.object(R.G, 'send') as send:
            replay = R.synthesize(self.path, self.parent['id'], self.ledger)
            send.assert_not_called(); self.assertTrue(replay['cache_hit'])
        self.assertEqual(replay['new_paid_api_calls'], 0)

    def test_failed_key_cannot_be_retried(self):
        *_, job, _ = R.prepare(self.path, self.parent['id'])
        key = R.G.request_key(job['body'])
        self.ledger.reserve(key, 'claude', job['body']['model']); self.ledger.finish(key, error='fixture failure')
        with patch.object(R.G, 'send') as send:
            with self.assertRaisesRegex(ValueError, 'no silent retry'):
                R.synthesize(self.path, self.parent['id'], self.ledger, allow_live=True)
            send.assert_not_called()

    def reference_count_failure(self):
        # Add one independently parsed source citation to the fixture so five
        # distinct, actually supplied units can be referenced without invention.
        units=R.P.unit_map(self.row['model_context'])
        cited={c['source_unit_id']for p in self.parent['passes']for f in p['result']['findings']for c in f['citations']}
        name,item=next((n,v)for n,v in units.items()if v['unit']['kind']=='narrative'and v['unit']['id']not in cited)
        body={'model':self.job['body']['model']}
        value={'findings':[{'id':'f1','kind':'documented_account','text':'An additional received request remains unconfirmed.',
                           'citations':[{'unit':name,'quote':item['text'].split('. ')[0]+'.'}],'unresolved':[]}],
               'unanswered':[],'review_steps':[]}
        finding=R.C.parse(self.raw(body,value),body,units,self.row)['findings'][0]
        packet=next(p for p in self.parent['passes']if any(u['source_unit_id']==item['unit']['id']for u in p['result']['unit_reviews']))
        finding['id']='additional-source-claim';packet['result']['findings'].append(finding)
        self.job=R.request(self.parent,self.row,self.refs,self.received)
        self.assertEqual(len(self.job['units']),5)
        value=self.result();value['findings'][0]['text']['unit_refs']=list(self.job['units'])
        historical=copy.deepcopy(self.job['body']);historical['system']=historical['system'].replace(R.REFERENCE_GUIDANCE,R.LEGACY_REFERENCE_BOUND)
        key=R.G.request_key(historical);out=R.location(self.path,'0'*64).parent/'provider';out.mkdir(parents=True)
        raw=self.raw(historical,value);raw['usage']={'input_tokens':750,'output_tokens':200}
        (out/(key+'.json')).write_text(json.dumps({'response':raw}))
        (out/(key+'-failure.json')).write_text(json.dumps({'request_hash':key,'error':R.REFERENCE_ERROR}))
        self.ledger.reserve(key,'claude',historical['model']);self.ledger.finish(key,error=R.REFERENCE_ERROR)
        return historical,key,out,raw

    def test_valid_five_reference_response_revalidates_offline_with_failed_key_retained(self):
        import os
        with patch.dict(os.environ,{'AGERE_CLAUDE_TRANSPORT':'subscription'}):
            historical,key,out,raw=self.reference_count_failure()
            R.C.token_check(historical,out,allow_live=True)
            before=(out/(key+'.json')).read_bytes()
            with patch.object(R.G,'send')as send:
                answer=R.synthesize(self.path,self.parent['id'],self.ledger,allow_live=False)
                send.assert_not_called()
        self.assertEqual(answer['new_paid_api_calls'],0);self.assertEqual(answer['new_token_count_calls'],0)
        self.assertTrue(answer['cache_hit']);self.assertEqual(R.inspect(self.path,answer['id']),answer)
        self.assertEqual((out/(key+'.json')).read_bytes(),before)
        self.assertEqual(self.ledger.db.execute('SELECT status FROM calls WHERE id=?',(key,)).fetchone()[0],'failed')
        usage=answer['reconciliation']['groups'][0]['usage']
        self.assertEqual(usage['request_hash'],key);self.assertEqual(usage['excess_reference_list_lengths'],[5])
        self.assertTrue(usage['prior_failed_reservation_preserved'])

    def excerpt_count_failure(self):
        self.reference_count_failure()
        value=self.result();value['findings'][0]['excerpt_ids']=list(self.job['excerpt_pool'])
        self.assertGreater(len(value['findings'][0]['excerpt_ids']),4)
        body=self.job['body'];key=R.G.request_key(body);out=R.location(self.path,'0'*64).parent/'provider'
        raw=self.raw(body,value);raw['usage']={'input_tokens':750,'output_tokens':200}
        (out/(key+'.json')).write_text(json.dumps({'response':raw}))
        (out/(key+'-failure.json')).write_text(json.dumps({'request_hash':key,'error':'Unknown or duplicate excerpt'}))
        self.ledger.reserve(key,'claude',body['model']);self.ledger.finish(key,error='Unknown or duplicate excerpt')
        return key,out,raw,value

    def test_all_extra_excerpts_validate_in_original_order_without_legacy_codec_changes(self):
        key,out,raw,value=self.excerpt_count_failure();names=value['findings'][0]['excerpt_ids']
        parsed=self.parsed(value)
        self.assertEqual([c['quote']for c in parsed['findings'][0]['citations']],[self.job['excerpt_pool'][n]['quote']for n in names])
        value['findings'][0]['kind']='conflicting_accounts'
        self.assertEqual(len(self.parsed(value)['findings'][0]['citations']),len(names))
        for bad in [names+['invented'],names+[names[0]],[]]:
            value['findings'][0]['excerpt_ids']=bad
            with self.assertRaisesRegex(ValueError,'Unknown or duplicate excerpt'):self.parsed(value)

    def test_extra_citation_quote_and_conflict_checks_are_not_waived(self):
        key,out,raw,value=self.excerpt_count_failure()
        extra=value['findings'][0]['excerpt_ids'][-1]
        self.job['excerpt_pool'][extra]['quote']='Absent exact quote'
        with self.assertRaisesRegex(ValueError,'absent or ambiguous'):self.parsed(value)
        source={'unit':'one','quote':'same'}
        self.assertEqual(R.L.citation_batches([source],'conflicting_accounts'),[[source]])

    def test_valid_extra_excerpt_response_revalidates_offline_and_retains_failed_attempt(self):
        import os
        with patch.dict(os.environ,{'AGERE_CLAUDE_TRANSPORT':'subscription'}):
            key,out,raw,value=self.excerpt_count_failure();before=(out/(key+'.json')).read_bytes()
            R.C.token_check(self.job['body'],out,allow_live=True)
            with patch.object(R.G,'send')as send:
                answer=R.synthesize(self.path,self.parent['id'],self.ledger,allow_live=False);send.assert_not_called()
        self.assertEqual(answer['new_paid_api_calls'],0)
        self.assertEqual(len(answer['findings'][0]['citations']),len(value['findings'][0]['excerpt_ids']))
        self.assertEqual(R.inspect(self.path,answer['id']),answer)
        self.assertEqual((out/(key+'.json')).read_bytes(),before)
        self.assertEqual(self.ledger.db.execute('SELECT status FROM calls WHERE id=?',(key,)).fetchone()[0],'failed')
        self.assertEqual(answer['reconciliation']['groups'][0]['usage']['offline_revalidation'],'exact_supplied_excerpt_count/1')

    def test_count_revalidation_still_rejects_unknown_or_duplicate_references(self):
        import os
        with patch.dict(os.environ,{'AGERE_CLAUDE_TRANSPORT':'subscription'}):
            historical,key,out,raw=self.reference_count_failure();R.C.token_check(historical,out,allow_live=True)
            for names in [['invented']+list(self.job['units']),list(self.job['units'])*2]:
                value=json.loads(raw['content'][0]['text']);value['findings'][0]['text']['unit_refs']=names
                (out/(key+'.json')).write_text(json.dumps({'response':self.raw(historical,value)}))
                with patch.object(R.G,'send')as send:
                    with self.assertRaisesRegex(ValueError,'Unknown, unsupplied or duplicate'):
                        R.synthesize(self.path,self.parent['id'],self.ledger,allow_live=True)
                    send.assert_not_called()

    def test_historical_context_route_is_not_replaced(self):
        with patch('clinical_core.agere.clinical.meaning_source_dispatch.pinned', return_value={'pinned'}), \
                patch.object(R.S, 'stages') as stages:
            self.assertIsNone(R.context_route(self.path, 'pinned')); stages.assert_not_called()
        with patch.object(R, 'context_route', return_value=None), patch.object(R.S, 'synthesize', return_value={'old': True}) as old:
            self.assertEqual(R.synthesize_context(self.path, 'pinned', self.ledger), {'old': True})
            old.assert_called_once()

    def failed_supported_link(self):
        parent,row,refs,job,key=R.prepare(self.path,self.parent['id']);self.job=job
        value=self.result();value['claim_reviews'][0]['status']=R.L.STATES[0]
        raw=self.raw(job['body'],value)
        with self.assertRaisesRegex(ValueError,'need a consolidated finding'):R.parse(raw,job,refs)
        request=R.G.request_key(job['body']);out=R.location(self.path,key).parent/'provider';out.mkdir(parents=True)
        (out/(request+'.json')).write_text(json.dumps({'response':raw}))
        (out/(request+'-failure.json')).write_text(json.dumps({'request_hash':request,'error':'Supported or qualified claims need a consolidated finding'}))
        self.ledger.reserve(request,'claude',job['body']['model']);self.ledger.finish(request,error='Supported or qualified claims need a consolidated finding')
        return request,out

    def test_exact_typed_recovery_preserves_sources_failures_and_reopens(self):
        failed,out=self.failed_supported_link();before=(out/(failed+'.json')).read_bytes()
        context=self.parent['context_id']
        with patch.object(R,'recovery_parent',return_value=self.parent):
            self.assertTrue(R.context_recovery_available(self.path,context))
            plan=R.preview_context_recovery(self.path,{'context_id':context})
            with patch.object(R.G,'send',side_effect=self.fake_send)as send,patch.object(R.C,'token_check',return_value={'input_tokens':750,'provider_count_cached':True}):
                saved=R.run_context_recovery(self.path,{'context_id':context,'recovery_id':plan['id']},self.ledger,allow_live=True)
                self.assertEqual(send.call_count,1)
            corrected=saved['validation_recovery']['corrected_body']
            R.validate_corrected_body(self.job['body'],corrected)
            self.assertEqual(corrected['messages'],self.job['body']['messages'])
            self.assertEqual(saved['passes'],self.parent['passes'])
            self.assertEqual(len(saved['claim_reviews']),len(self.job['finding_ids']))
            self.assertEqual(R.inspect(self.path,saved['id']),saved)
            with patch.object(R.G,'send')as send:
                self.assertEqual(R.synthesize(self.path,self.parent['id'],self.ledger)['id'],saved['id']);send.assert_not_called()
        self.assertEqual((out/(failed+'.json')).read_bytes(),before)
        self.assertEqual(self.ledger.db.execute('SELECT status FROM calls WHERE id=?',(failed,)).fetchone()[0],'failed')

    def test_typed_recovery_requires_current_plan_and_never_resends_failed_correction(self):
        self.failed_supported_link();context=self.parent['context_id']
        with patch.object(R,'recovery_parent',return_value=self.parent):
            plan=R.preview_context_recovery(self.path,{'context_id':context})
            with patch.object(R.G,'send')as send:
                with self.assertRaisesRegex(ValueError,'plan changed'):
                    R.run_context_recovery(self.path,{'context_id':context,'recovery_id':'wrong'},self.ledger,allow_live=True)
                with self.assertRaisesRegex(ValueError,'disabled'):
                    R.run_context_recovery(self.path,{'context_id':context,'recovery_id':plan['id']},self.ledger)
                send.assert_not_called()
            key=plan['corrected_request_hash'];self.ledger.reserve(key,'claude',self.job['body']['model']);self.ledger.finish(key,error='uncertain transport attempt')
            with patch.object(R.G,'send')as send:
                with self.assertRaisesRegex(ValueError,'no silent retry'):
                    R.run_context_recovery(self.path,{'context_id':context,'recovery_id':plan['id']},self.ledger,allow_live=True)
                send.assert_not_called()

    def test_typed_recovery_rejects_duplicate_diagnostic_loop_and_source_edits(self):
        self.failed_supported_link();context=self.parent['context_id']
        with patch.object(R,'recovery_parent',return_value=self.parent):
            parent,row,refs,job,key,binding,rid=R.recovery_plan(self.path,context)
            changed=copy.deepcopy(binding['corrected_body']);changed['messages'][0]['content']+=' changed source'
            with self.assertRaisesRegex(ValueError,'changed original'):R.validate_corrected_body(job['body'],changed)
            corrected=binding['corrected_body'];request=R.G.request_key(corrected);out=R.location(self.path,key).parent/'provider'
            value=self.result();value['claim_reviews'][0]['status']=R.L.STATES[0]
            (out/(request+'.json')).write_text(json.dumps({'response':self.raw(corrected,value)}))
            (out/(request+'-failure.json')).write_text(json.dumps({'request_hash':request,'error':'Supported or qualified claims need a consolidated finding'}))
            with self.assertRaisesRegex(ValueError,'Same typed validation fault'):
                R.preview_context_recovery(self.path,{'context_id':context})

    def test_current_parser_preserves_historical_typed_answer_identity(self):
        with patch.object(R.G,'send',side_effect=self.fake_send),patch.object(R.C,'token_check',return_value={'input_tokens':750,'provider_count_cached':False}):
            saved=R.synthesize(self.path,self.parent['id'],self.ledger,allow_live=True)
        before=R.location(self.path,saved['id']).read_bytes()
        with patch.object(R,'identity',return_value=digest(['new-recovery-code',R.identity()])),patch.object(R.G,'send')as send:
            self.assertEqual(R.inspect(self.path,saved['id']),saved)
            self.assertEqual(R.saved_for_parent(self.path,self.parent['id']),saved)
            self.assertEqual(R.synthesize(self.path,self.parent['id'],self.ledger)['id'],saved['id'])
            send.assert_not_called()
        self.assertEqual(R.location(self.path,saved['id']).read_bytes(),before)


if __name__ == '__main__': unittest.main()
