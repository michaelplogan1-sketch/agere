"""Saved-answer frontier delivery; independent fixtures, no benchmark data."""
import importlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from clinical_core.agere.deliverables import answer_context as A, workflow as W, runtime, verify
from clinical_core.agere.deliverables.receipts import Bundle, fingerprint

C = importlib.import_module('clinical_core.agere.deliverables.compose')


class FrontierDelivery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript("""
                CREATE TABLE meta(key,value);
                INSERT INTO meta VALUES('as_of','2030-05-27'),('source_digest','frontier-fixture'),('version','fixture');
                CREATE TABLE patient(patient_id PRIMARY KEY);
                INSERT INTO patient VALUES('A'),('B'),('OUTSIDE');
            """)
        self.question = 'List the selected patients and create an interactive evidence explorer'
        result = {'answer': {'status': 'answered', 'answer_type': 'table', 'count': 2,
                            'headline': 'Selected patients', 'patient_ids': ['A', 'B'],
                            'table': [{'patient_id': 'A', 'observed_visits': 17},
                                      {'patient_id': 'B', 'observed_visits': 23}],
                            'missing': [], 'limitations': ['Synthetic observed records; no eligibility established.']},
                  'workflow': [{'kind': 'deliverable', 'title': 'Evidence explorer', 'template': 'explore',
                                'quote': 'create an interactive evidence explorer', 'unresolved': []}]}
        self.key = A.capture(self.path, self.question, result)['id']
        self.calls = []

    def sender(self, body):
        self.calls.append(body)
        self.assertEqual(body['tool_choice'], {'type': 'none'})
        text = body['messages'][0]['content']
        self.assertIn(self.question, text)
        self.assertNotIn('OUTSIDE', text)
        if 'Assess the COMPLETE artifact' in body['messages'][0]['content']:
            return {'content':[{'type':'text','text':json.dumps({'coverage':'complete','support':'supported','issues':[]})}]},{'cost_estimate':{'estimated_usd':.01}}
        rid = re.search(r'"receipt":"(R-[0-9a-f]{10})","tool":"saved_answer"', text).group(1)
        return {'content': [{'type': 'text', 'text': (
            f'TITLE: Evidence lens\n```html\n<h1>Evidence lens</h1><p>Synthetic saved evidence</p>'
            f'<p>Patients <span data-bind="{rid}:answer.count"></span></p>'
            f'<button id="details">Show evidence</button><section id="evidence" hidden>'
            f'<span data-bind="{rid}:answer.table.0.observed_visits"></span></section>'
            '<script>document.getElementById("details").onclick=()=>{document.getElementById("evidence").hidden=false;};</script>\n```')}],
            'usage': {'input_tokens': 100, 'output_tokens': 200}}, {'cost_estimate': {'estimated_usd': 0.25}}

    def test_workflow_calls_frontier_with_exact_evidence_and_reuses_saved_result(self):
        with patch.object(C.toolsmod.ToolContext, 'call', side_effect=AssertionError('No new cohort')):
            result = W.materialize(self.path, self.key, allow_live=True, generation='frontier', send=self.sender)
            repeated = W.materialize(self.path, self.key, allow_live=True, generation='frontier',
                                     send=Mock(side_effect=AssertionError('Duplicate generation')))
        self.assertEqual(result['steps'], repeated['steps'])
        self.assertEqual(repeated['new_paid_api_calls'], 0)
        self.assertEqual(repeated['known_api_estimate_usd'], 0)
        self.assertEqual(result['steps'][0]['status'], 'ready')
        self.assertEqual(result['steps'][0]['generation'], 'frontier')
        self.assertEqual(result['new_paid_api_calls'], 2)
        self.assertEqual(result['known_api_estimate_usd'], 0.26)
        self.assertFalse(result['external_dispatch_performed'])
        self.assertIn('REQUEST: create an interactive evidence explorer', self.calls[0]['messages'][0]['content'])
        ws = A.workspace(self.path, A.load(self.path, self.key))
        spec, bundle, _, doc = C.load(ws, result['steps'][0]['deliverable_id'])
        self.assertEqual(spec['mode'], 'composed')
        self.assertTrue(bundle.meta['frozen_evidence'])
        saved = next(v for rid, v in bundle.results.items() if bundle.receipts[rid]['tool'] == 'saved_answer')
        self.assertEqual(saved['patient_ids'], ['A', 'B'])
        self.assertIn('Show evidence', doc)
        self.assertEqual(C.listing(ws)[0]['generation'], 'frontier')
        self.assertEqual([c['model'] for c in self.calls], [C.RENDER_MODELS[0], C.SMALL_MODEL])

    def test_proposal_stage_survives_real_saved_answer_to_composer_boundary(self):
        c = A.load(self.path, self.key)
        result = {'answer': {**c['answer'], 'status': 'partial',
                             'missing': ['Patient eligibility is unavailable']},
                  'workflow': [{**c['workflow'][0], 'purpose': 'proposal'}]}
        key = A.capture(self.path, self.question, result)['id']
        out = W.materialize(self.path, key, allow_live=True, send=self.sender)
        self.assertEqual(out['steps'][0]['status'], 'ready')
        writer, reviewer = self.calls
        self.assertIn('PROPOSED DESIGN CONTRACT', '\n'.join(b['text'] for b in writer['system']))
        self.assertIn('Do not demand actual enrollment', reviewer['system'])
        ws = A.workspace(self.path, A.load(self.path, key))
        _, bundle, _, _ = C.load(ws, out['steps'][0]['deliverable_id'])
        self.assertEqual(bundle.meta['deliverable_purpose'], 'proposal')
        saved = next(v for rid, v in bundle.results.items() if bundle.receipts[rid]['tool'] == 'saved_answer')
        self.assertEqual(saved['answer']['status'], 'partial')
        self.assertEqual(saved['answer']['missing'], ['Patient eligibility is unavailable'])
        self.assertFalse(out['external_dispatch_performed'])

    def test_inspection_stage_and_final_stage_remain_distinct(self):
        c = A.load(self.path, self.key)
        for purpose in ('inspection', 'final'):
            with self.subTest(purpose=purpose):
                key = A.capture(self.path, self.question, {'answer': c['answer'],
                      'workflow': [{**c['workflow'][0], 'purpose': purpose}]})['id']
                self.calls.clear()
                out = W.materialize(self.path, key, allow_live=True, send=self.sender)
                self.assertEqual(out['steps'][0]['status'], 'ready')
                self.assertEqual('Do not demand actual enrollment' in self.calls[-1]['system'], purpose == 'inspection')
                ws = A.workspace(self.path, A.load(self.path, key))
                self.assertEqual(C.load(ws, out['steps'][0]['deliverable_id'])[1].meta['deliverable_purpose'], purpose)

    def test_compact_address_error_does_not_purchase_a_stronger_writer(self):
        def sender(body):
            raw, receipt = self.sender(body)
            if 'Assess the COMPLETE artifact' not in body['messages'][0]['content']:
                raw['content'][0]['text'] = raw['content'][0]['text'].replace('answer.table.0.observed_visits', 'answer.table.0.1')
            return raw, receipt
        out = W.materialize(self.path, self.key, allow_live=True, send=sender)
        self.assertEqual(out['steps'][0]['status'], 'ready')
        self.assertEqual([c['model'] for c in self.calls], [C.RENDER_MODELS[0], C.SMALL_MODEL])
        ws = A.workspace(self.path, A.load(self.path, self.key))
        spec, bundle, body, doc = C.load(ws, out['steps'][0]['deliverable_id'])
        self.assertEqual(len(spec['binding_repairs']), 1)
        self.assertTrue(spec['binding_repairs'][0]['to'].endswith('answer.table.0.observed_visits'))
        self.assertTrue(spec['verification']['passed'])
        self.assertIn('answer.table.0.observed_visits', body)
        self.assertEqual(len(spec['render_attempts']), 1)

    def test_retry_edits_retained_draft_and_rechecks_meaning_instead_of_restarting(self):
        def reject(body):
            if 'Assess the COMPLETE artifact' in body['messages'][0]['content']:
                return {'content':[{'type':'text','text':json.dumps({'coverage':'incomplete','support':'supported',
                    'issues':[{'requirement':'Historical evidence','reason':'Preserve observation dates'}]})}]},{'cache_hit':True}
            return self.sender(body)
        first=W.materialize(self.path,self.key,allow_live=True,send=reject)
        old=first['steps'][0]['deliverable_id'];ws=A.workspace(self.path,A.load(self.path,self.key))
        old_files={p.name:p.read_bytes() for p in (Path(ws.dir)/'deliverables'/old).iterdir() if p.is_file()}
        self.calls.clear()
        out=W.retry_deliverable(self.path,{'context_id':self.key,'index':0,'attempt':2},allow_live=True,send=self.sender)
        self.assertEqual(out['steps'][0]['status'],'ready')
        self.assertEqual(len(self.calls),2)
        self.assertIn('CURRENT BODY FRAGMENT',self.calls[0]['messages'][0]['content'])
        self.assertIn('Preserve observation dates',self.calls[0]['messages'][0]['content'])
        self.assertIn('hypothesis to investigate, not clinical truth',self.calls[0]['messages'][0]['content'])
        self.assertIn('Assess the COMPLETE artifact',self.calls[1]['messages'][0]['content'])
        spec,bundle,_,_=C.load(ws,out['steps'][0]['deliverable_id'])
        self.assertEqual(spec['draft_reuse']['artifact'],old)
        self.assertEqual(spec['draft_reuse']['evidence_fingerprint'],fingerprint(bundle.results))
        self.assertEqual(old_files,{p.name:p.read_bytes() for p in (Path(ws.dir)/'deliverables'/old).iterdir() if p.is_file()})

    def test_saved_independent_user_acceptance_reaches_writer_and_technical_checker(self):
        c=A.load(self.path,self.key);ws=A.workspace(self.path,c)
        review={'schema':'independent-answer-review/1','accepted':True,'reviewer':'Fixture physician',
                'verbatim':'I independently reviewed the results; no further clinician review is needed.',
                'recorded_at':'2030-05-28','context_id':self.key,
                'answer_fingerprint':fingerprint(c['answer']),'source_fingerprint':fingerprint(c['source']),
                'policy_identity':c['context_policy_identity']}
        (Path(ws.dir)/'answer-review.json').write_text(json.dumps(review))
        out=W.materialize(self.path,self.key,allow_live=True,send=self.sender)
        self.assertEqual(out['steps'][0]['status'],'ready')
        self.assertIn('do not run a new clinical assessment','\n'.join(b['text']for b in self.calls[0]['system']))
        self.assertIn('do not run a new clinical assessment',self.calls[1]['system'])
        _,bundle,_,_=C.load(ws,out['steps'][0]['deliverable_id'])
        self.assertEqual(bundle.meta['user_answer_review'],review)
        self.assertFalse(out['external_dispatch_performed'])
        self.assertEqual(C.context_review(self.path,c),review)

    def test_workspace_acceptance_display_preserves_status_and_rejects_changed_premises(self):
        c=A.load(self.path,self.key);ws=A.workspace(self.path,c)
        review={'schema':'independent-answer-review/1','accepted':True,'reviewer':'Fixture physician',
                'verbatim':'Accepted independently','recorded_at':'2030-05-28','context_id':self.key,
                'answer_fingerprint':fingerprint(c['answer']),'source_fingerprint':fingerprint(c['source']),
                'policy_identity':c['context_policy_identity']}
        (Path(ws.dir)/'answer-review.json').write_text(json.dumps(review));before=fingerprint(c)
        self.assertTrue(C.context_review(self.path,c)['accepted']);self.assertEqual(fingerprint(c),before)
        for changed in (dict(c,stale=True),dict(c,invalidated=True),dict(c,id='../other'),
                        dict(c,answer={**c['answer'],'headline':'Changed findings'})):
            self.assertIsNone(C.context_review(self.path,changed))

    def test_resolved_static_failure_rechecks_retained_page_without_another_writer(self):
        def writer(body):
            raw,receipt=self.sender(body)
            if 'Assess the COMPLETE artifact' not in body['messages'][0]['content']:
                raw['content'][0]['text']=raw['content'][0]['text'].replace('<h1>Evidence lens</h1>',
                    '<h1>Evidence lens</h1><p>Units mL/min/1.73m²</p>')
            return raw,receipt
        # Reproduce the old parser's actual numeric-unit rejection.
        with patch.object(verify,'INDEXED_RATE_UNIT_RE',re.compile(r'(?!)')):
            first=W.materialize(self.path,self.key,allow_live=True,send=writer)
        self.assertEqual(first['steps'][0]['status'],'blocked')
        old=first['steps'][0]['deliverable_id'];self.calls.clear()
        def technical_check_only(body):
            self.assertIn('Assess the COMPLETE artifact',body['messages'][0]['content'])
            return self.sender(body)
        out=W.retry_deliverable(self.path,{'context_id':self.key,'index':0,'attempt':2},
                                allow_live=True,send=technical_check_only)
        self.assertEqual(out['steps'][0]['status'],'ready')
        self.assertEqual(len(self.calls),1);self.assertEqual(out['new_paid_api_calls'],1)
        ws=A.workspace(self.path,A.load(self.path,self.key));spec,_,_,_=C.load(ws,out['steps'][0]['deliverable_id'])
        self.assertEqual(spec['draft_reuse']['artifact'],old)
        self.assertTrue(spec['technical_revalidation']['writer_call_avoided'])
        self.assertEqual(spec['costs']['frontier']['calls'],0)

    def test_review_contract_migration_reclassifies_optional_notes_once_without_rewriting(self):
        def old_review(body):
            if 'Assess the COMPLETE artifact' in body['messages'][0]['content']:
                return {'content':[{'type':'text','text':json.dumps({'coverage':'complete','support':'supported',
                    'issues':[{'requirement':'Optional summary','reason':'A non-blocking suggestion'}]})}]},{'cache_hit':True}
            return self.sender(body)
        first=W.materialize(self.path,self.key,allow_live=True,send=old_review)
        old=first['steps'][0]['deliverable_id'];c=A.load(self.path,self.key);ws=A.workspace(self.path,c)
        file=Path(ws.dir)/'deliverables'/old/'spec.json';s=json.loads(file.read_text())
        s['verification'].pop('semantic_review_version');file.write_text(json.dumps(s))
        review={'schema':'independent-answer-review/1','accepted':True,'reviewer':'Fixture physician',
                'verbatim':'Independently reviewed and accepted','recorded_at':'2030-05-28','context_id':self.key,
                'answer_fingerprint':fingerprint(c['answer']),'source_fingerprint':fingerprint(c['source']),
                'policy_identity':c['context_policy_identity']}
        (Path(ws.dir)/'answer-review.json').write_text(json.dumps(review));self.calls.clear()
        def new_check(body):
            self.assertIn('Assess the COMPLETE artifact',body['messages'][0]['content']);self.calls.append(body)
            return {'content':[{'type':'text','text':json.dumps({'coverage':'complete','support':'supported',
                'issues':[{'severity':'advisory','requirement':'Optional summary','reason':'Complete data remains available'}]})}]},{'cost_estimate':{'estimated_usd':.01}}
        out=W.retry_deliverable(self.path,{'context_id':self.key,'index':0,'attempt':2},allow_live=True,send=new_check)
        self.assertEqual(out['steps'][0]['status'],'ready');self.assertEqual(len(self.calls),1)
        spec,_,_,_=C.load(ws,out['steps'][0]['deliverable_id'])
        self.assertTrue(spec['technical_revalidation']['review_contract_changed'])
        self.assertEqual(spec['verification']['semantic_review_version'],C.PRESENTATION_REVIEW_VERSION)
        self.assertEqual(len(spec['verification']['advisories']),1)
        self.assertEqual(spec['costs']['frontier']['calls'],0)

    def test_writer_escalates_only_after_failed_checks_with_same_source(self):
        seen=[]
        def sender(body):
            seen.append(body)
            if 'Assess the COMPLETE artifact' in body['messages'][0]['content']:
                writers=[b for b in seen if 'Assess the COMPLETE artifact' not in b['messages'][0]['content']]
                if len(writers)<len(C.RENDER_MODELS):  # the reviewer rejects until the top of the writer ladder has written
                    return {'content':[{'type':'text','text':json.dumps({'coverage':'incomplete','support':'not_established','issues':[{'requirement':'Evidence detail','reason':'Omitted available exception'}]})}]},{'cost_estimate':{'estimated_usd':.01}}
            return self.sender(body)
        r=W.materialize(self.path,self.key,allow_live=True,send=sender)
        self.assertEqual(r['steps'][0]['status'],'ready')
        writers=[b for b in seen if 'Assess the COMPLETE artifact' not in b['messages'][0]['content']]
        self.assertEqual([b['model'] for b in writers],list(C.RENDER_MODELS))
        self.assertTrue(all(b['tool_choice']=={'type':'none'} for b in writers))
        self.assertTrue(all('Omitted available exception' in b['messages'][0]['content'] for b in writers[1:]))
        ws=A.workspace(self.path,A.load(self.path,self.key));spec,bundle,_,_=C.load(ws,r['steps'][0]['deliverable_id'])
        self.assertEqual([a['passed'] for a in spec['render_attempts']],[False,False,True])
        self.assertEqual(spec['costs']['known_api_estimate_usd'],.78)

    def test_explicit_writer_override_is_honored(self):
        with patch.dict(__import__('os').environ,{'AGERE_DELIVERABLE_MODEL':'claude-sonnet-5-5'}):
            r=W.materialize(self.path,self.key,allow_live=True,send=self.sender)
        self.assertEqual(r['steps'][0]['status'],'ready')
        self.assertEqual(self.calls[0]['model'],'claude-sonnet-5-5')

    def test_disabled_explicit_frontier_never_falls_back_to_template(self):
        sender = Mock(side_effect=AssertionError('No authority'))
        result = W.materialize(self.path, self.key, generation='frontier', send=sender)
        self.assertEqual(result['steps'][0]['status'], 'blocked')
        self.assertIn('disabled', result['steps'][0]['reason'])
        self.assertNotIn('deliverable_id', result['steps'][0])
        sender.assert_not_called()

    def test_local_template_remains_explicit_and_free(self):
        with patch.object(C, 'ledgered_sender', side_effect=AssertionError('No model')):
            result = W.materialize(self.path, self.key, generation='local_template', allow_live=True)
        self.assertEqual(result['steps'][0]['generation'], 'local_template')
        self.assertEqual(result['new_paid_api_calls'], 0)
        self.assertEqual(result['steps'][0]['costs']['total_usd'], 0)

    def test_transport_failure_is_saved_with_unknown_cost_and_no_retry(self):
        sender = Mock(side_effect=TimeoutError('Secret transport detail'))
        result = W.materialize(self.path, self.key, allow_live=True, generation='frontier', send=sender)
        step = result['steps'][0]
        self.assertEqual(step['status'], 'blocked')
        self.assertNotIn('Secret transport detail', json.dumps(result))
        self.assertIsNone(step['costs']['total_usd'])
        self.assertFalse(result['api_cost_known'])
        self.assertEqual(result['new_paid_api_calls'], 1)
        W.materialize(self.path, self.key, allow_live=True, send=sender)
        self.assertEqual(sender.call_count, 1)
        ws = A.workspace(self.path, A.load(self.path, self.key))
        spec, _, _, doc = C.load(ws, step['deliverable_id'])
        self.assertEqual(spec['status'], 'rejected')
        self.assertIsNone(doc)
        self.assertIn('TimeoutError', C.listing(ws)[0]['failure'])

    def test_failed_verification_retains_all_attempt_costs(self):
        bad = {'content': [{'type': 'text', 'text': 'TITLE: Bad\n```html\n<p>Saved $987,654.</p>\n```'}]}
        sender = Mock(return_value=(bad, {'cost_estimate': {'estimated_usd': 0.1}}))
        result = W.materialize(self.path, self.key, allow_live=True, send=sender)
        self.assertEqual(result['steps'][0]['status'], 'blocked')
        self.assertEqual(result['new_paid_api_calls'], len(C.RENDER_MODELS))  # one attempt per rung of the writer ladder
        self.assertAlmostEqual(result['known_api_estimate_usd'], 0.1 * len(C.RENDER_MODELS))
        self.assertFalse(list(A.root(self.path).glob('*/deliverables/DLV-*/artifact.html')))

    def test_subscription_limit_preserves_failed_artifact_and_stops_later_deliverables(self):
        from clinical_core.agere.gateway import ProviderUnavailable
        c=A.load(self.path,self.key)
        result={'answer':c['answer'],'workflow':c['workflow']+[dict(c['workflow'][0],title='Second view')]}
        key=A.capture(self.path,self.question,result)['id']
        sender=Mock(side_effect=ProviderUnavailable('Subscription weekly limit',kind='rate_limit'))
        with patch.dict(__import__('os').environ,{'AGERE_CLAUDE_TRANSPORT':'subscription'}):
            out=W.materialize(self.path,key,allow_live=True,generation='frontier',send=sender)
        sender.assert_called_once()
        self.assertEqual(out['steps'][0]['provider_failure']['kind'],'rate_limit')
        self.assertEqual(out['steps'][1]['status'],'blocked')
        self.assertNotIn('deliverable_id',out['steps'][1]);self.assertFalse(out['api_cost_known'])
        self.assertEqual(out['new_paid_api_calls'],1)

    def test_frozen_revision_cannot_fetch_new_population(self):
        out = A.compose_frontier(self.path, {'context_id': self.key, 'request': 'Create an evidence lens'},
                                 allow_live=True, send=self.sender)
        ws = A.workspace(self.path, A.load(self.path, self.key))
        _, bundle, _, _ = C.load(ws, out['id'])
        # Older saved-answer bundles did not carry the explicit frozen flag.
        bundle_file = Path(out['path']).with_name('bundle.json')
        legacy = json.loads(bundle_file.read_text())
        legacy['meta'].pop('frozen_evidence')
        bundle_file.write_text(json.dumps(legacy))
        def revise(body):
            self.assertEqual(body['tool_choice'], {'type': 'none'})
            if 'Assess the COMPLETE artifact' in body['messages'][0]['content']:
                return {'content':[{'type':'text','text':json.dumps({'coverage':'complete','support':'supported','issues':[]})}]},{'cache_hit':True}
            return {'content': [{'type': 'text', 'text': '```json\n[{"find":"Evidence lens</h1>","replace":"Saved evidence</h1>"}]\n```'}]}, {'cache_hit': True}
        with patch.object(C.toolsmod.ToolContext, 'call', side_effect=AssertionError('No new evidence')):
            revised = C.revise(ws, out['id'], 'Add a pharmacy plan for everyone and rename the view', send=revise)
        self.assertEqual(revised['status'], 'delivered')
        self.assertEqual(revised['revision'], 2)
        self.assertEqual(revised['new_paid_api_calls'], 0)
        self.assertEqual(C.load(ws, out['id'])[1].results, bundle.results)
        self.assertTrue(C.load(ws, out['id'])[1].meta['frozen_evidence'])
        self.assertTrue((Path(out['path']).parent / 'revisions/r1.html').exists())

    def test_stale_answer_is_rejected_before_provider_use(self):
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.execute("UPDATE meta SET value='changed' WHERE key='source_digest'")
        sender = Mock()
        with self.assertRaisesRegex(ValueError, 'data changed'):
            A.compose_frontier(self.path, {'context_id': self.key, 'request': 'Build a view'}, allow_live=True, send=sender)
        sender.assert_not_called()

    def test_distinct_creations_do_not_overwrite_same_second(self):
        with patch.object(C, '_now', return_value='fixed'):
            first = C._new_id(None, 'same', None, {})
            second = C._new_id(None, 'same', None, {})
        self.assertNotEqual(first, second)

    def test_rejected_revision_archives_previous_delivery_but_has_no_current_html(self):
        out = A.compose_frontier(self.path, {'context_id': self.key, 'request': 'Create an evidence lens'},
                                 allow_live=True, send=self.sender)
        ws = A.workspace(self.path, A.load(self.path, self.key))
        bad = {'content': [{'type': 'text', 'text': 'TITLE: Bad\n```html\n<p>Saved $987,654.</p>\n```'}]}
        rev = C.revise(ws, out['id'], 'Change the design', send=Mock(return_value=(bad, {'cost_estimate': {'estimated_usd': 0.1}})))
        self.assertEqual(rev['status'], 'rejected')
        self.assertIsNone(C.load(ws, out['id'])[3])
        self.assertFalse(Path(out['path']).exists())
        self.assertTrue((Path(out['path']).parent / 'revisions/r1.html').exists())
        self.assertAlmostEqual(rev['costs']['total_usd'], 0.1 * len(C.RENDER_MODELS))

    def test_frozen_verifier_rejects_unbound_values_and_representative_network_cases(self):
        b = Bundle({'frozen_evidence': True})
        rid = b.add('saved_answer', {}, {'patient_count': 12, 'spend': 50, 'age': 45})
        bad = [
            '<p>Patients 50</p>', '<p>Readmission rate 45%</p>', '<p>Readmissions fell by 7</p>',
            '<script>const u="inert";fetch(u)</script>',
            '<script>location="//example.invalid"</script>',
            '<script>new Image().src="inert"</script>',
            '<script>window["fe"+"tch"]("inert")</script>',
            '<form action="/unexpected"><button>Send</button></form>',
            '<img src="data:image/png;base64,AA==" onerror="fetch(u)">',
        ]
        for body in bad:
            with self.subTest(body=body):
                report = verify.verify(runtime.wrap(body, b, 'Fixture'), b)
                self.assertFalse(report['passed'])
        good = runtime.wrap(f'<p>Patients <span data-bind="{rid}:patient_count"></span></p>'
                            '<a href="#evidence">Inspect evidence</a><section id="evidence">Saved facts</section>', b, 'Fixture')
        self.assertTrue(verify.verify(good, b)['passed'])
        self.assertIn('Content-Security-Policy', good)
        # Provenance checks cannot establish the truth of the surrounding label.
        mislabeled = runtime.wrap(f'<p>Rate <span data-bind="{rid}:age"></span>%</p>', b.to_json(), 'Fixture')
        self.assertTrue(verify.verify(mislabeled, b)['passed'])
        layout = runtime.wrap('<script>(function(){document.body.insertAdjacentHTML("beforeend",'
                              '\'<h3 style="margin:0;width:100%">Saved evidence</h3>\');})();</script>', b, 'Fixture')
        self.assertTrue(verify.verify(layout, b)['passed'])

    def test_unknown_cost_never_becomes_zero_total(self):
        ledger = C.Ledger()
        ledger.add('frontier', 'compose', {'model': 'fixture'}, {'usage': {'input_tokens': 123}}, {})
        summary = ledger.summary()
        self.assertIsNone(summary['total_usd'])
        self.assertFalse(summary['api_cost_known'])
        self.assertEqual(summary['known_api_estimate_usd'], 0)
        self.assertIsNone(C.Ledger().summary([{'tool': 'ask', 'model_cost_usd': None}])['total_usd'])
        self.assertEqual(C.Ledger().summary([{'tool': 'ask', 'cached': True}])['total_usd'], 0)

    def test_failed_revision_and_failed_repair_preserve_attempt_accounting(self):
        for repair in (False, True):
            with self.subTest(repair=repair):
                out = A.compose_frontier(self.path, {'context_id': self.key, 'request': 'Create an evidence lens'},
                                         allow_live=True, send=self.sender)
                ws = A.workspace(self.path, A.load(self.path, self.key))
                bad = ({'content': [{'type': 'text', 'text': 'TITLE: Bad\n```html\n<p>Saved $987,654.</p>\n```'}]},
                       {'cost_estimate': {'estimated_usd': 0.1}})
                sender = Mock(side_effect=([bad] if repair else []) + [TimeoutError('private detail')])
                rev = C.revise(ws, out['id'], 'Revise it', send=sender)
                self.assertEqual(rev['status'], 'rejected')
                self.assertEqual(rev['new_paid_api_calls'], 2 if repair else 1)
                self.assertEqual(rev['costs']['known_api_estimate_usd'], 0.1 if repair else 0)
                self.assertIsNone(rev['costs']['total_usd'])
                self.assertIsNone(C.load(ws, out['id'])[3])
                self.assertTrue((Path(out['path']).parent / 'revisions/r1.html').exists())


if __name__ == '__main__':
    unittest.main()
