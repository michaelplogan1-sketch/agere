"""Independent synthetic workflow: answer -> exact cohort -> owned review -> reopen.
No benchmark references, model calls, simulated enrollment or external actions.
"""
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from clinical_core.agere.deliverables import answer_context as A
from clinical_core.agere.clinical import answer_shape, metric_router, conversation
from clinical_core.agere import ask_view

USER_QUESTION = "Find every patient with heartfailure and reduced EF, flag which patients aren't on defect free GDMT or not on max dose, who their PCP is, and who their cardiologist is"

class Workflow(unittest.TestCase):
    def test_capture_packs_complete_evidence_when_plain_snapshot_cannot_fit(self):
        self.result['answer']['headline']='Packed source context'
        self.result['investigation']={'unverified_history':['dated evidence',{'missing':None,'observed':0}]}
        with mock.patch.object(A.shutil,'disk_usage',return_value=SimpleNamespace(free=0)):
            captured=A.capture(self.path,self.question,self.result)
        target=A.root(self.path)/captured['id']/'answer.json'
        packed=A.snapshot_path(target)
        self.assertEqual(packed.name,'answer.json.gz');self.assertFalse(target.exists())
        data=A.read_snapshot(target);self.assertEqual(data['answer'],self.result['answer'])
        self.assertEqual(data['investigation'],self.result['investigation'])
        self.assertEqual(A.load(self.path,captured['id'])['patient_ids'],['A','B','C'])
        before=packed.read_bytes()
        with mock.patch.object(A.json,'dump',side_effect=AssertionError('Do not replace first complete snapshot')):
            self.assertEqual(A.capture(self.path,self.question,self.result)['id'],captured['id'])
        self.assertEqual(packed.read_bytes(),before)
        self.assertFalse(target.exists())
        with mock.patch.object(A,'read_snapshot',side_effect=AssertionError('Catalog must use its small index')):
            row=next(r for r in A.listing(self.path)if r['id']==captured['id'])
        self.assertEqual(row['patients'],3)

    def test_packed_catalog_without_index_reads_header_without_claiming_population(self):
        self.result['answer']['headline']='Packed catalog fallback'
        with mock.patch.object(A.shutil,'disk_usage',return_value=SimpleNamespace(free=0)):
            captured=A.capture(self.path,self.question,self.result)
        target=A.snapshot_path(A.root(self.path)/captured['id']/'answer.json')
        target.with_name('listing.json').unlink()
        before=target.read_bytes()
        with mock.patch.object(A,'read_snapshot',side_effect=AssertionError('No complete native evidence decode for catalog')):
            row=next(r for r in A.listing(self.path)if r['id']==captured['id'])
        self.assertEqual(row['question'],self.question);self.assertIsNone(row['patients'])
        self.assertEqual(target.read_bytes(),before)

    def test_packed_sync_failure_retains_partial_without_publishing(self):
        import gzip
        self.result['answer']['headline']='Packed interrupted context'
        with mock.patch.object(A.shutil,'disk_usage',return_value=SimpleNamespace(free=0)),mock.patch.object(A.os,'fsync',side_effect=OSError(28,'No space left on device')):
            with self.assertRaises(OSError):A.capture(self.path,self.question,self.result)
        partial,=A.root(self.path).glob('*/.answer-*.partial')
        with gzip.open(partial,'rt')as stream:data=json.load(stream)
        self.assertEqual(data['answer'],self.result['answer'])
        self.assertFalse((partial.parent/'answer.json').exists())
        self.assertFalse((partial.parent/'answer.json.gz').exists())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        db = sqlite3.connect(self.path / 'semantic.sqlite3')
        db.executescript("CREATE TABLE meta(key,value); INSERT INTO meta VALUES ('as_of','2030-05-27'),('source_digest','independent-source'),('version','test'); CREATE TABLE patient(patient_id PRIMARY KEY); INSERT INTO patient VALUES ('A'),('B'),('C'),('OUTSIDE');")
        db.close()
        self.question = 'List patients with recorded missed appointments and show their PCP'
        self.result = {'answer': {'status':'answered','answer_type':'table','headline':'Three patients',
                       'table':[{'patient_id':'A','pcp':'Dr Test','record_id':'R-A'},
                                {'patient_id':'B','pcp':None,'record_id':'R-B'},
                                {'patient_id':'C','pcp':'Dr Other','record_id':'R-C'}], 'missing':[]},
                       'graph':{'interpretation':'Observed missed appointments'},'new_paid_api_calls':0}
        self.c = A.capture(self.path, self.question, self.result)

    def req(self, **kw):
        return {'context_id':self.c['id'],'program':'pharmd','patient_ids':['A','B'],
                'owner':'PharmD review team','note':'Review suitability; no enrollment', **kw}

    def test_different_unfinished_drafts_preserve_separate_immutable_checkpoints(self):
        import copy
        from clinical_core.contracts import digest
        before=copy.deepcopy(self.result);keys=[]
        for text in ('Unbound first draft','Progressed but still unbound draft'):
            draft={'status':'partial','headline':text}
            candidate=copy.deepcopy(before)
            candidate['investigation']={'recovery_checkpoint':{'draft':draft,'draft_digest':digest(draft)}}
            key=A.capture(self.path,self.question,candidate)['id'];keys.append(key)
            self.assertEqual(A.load(self.path,key)['investigation']['recovery_checkpoint']['draft'],draft)
        self.assertNotEqual(*keys)
        self.assertEqual(A.load(self.path,keys[0])['investigation']['recovery_checkpoint']['draft']['headline'],'Unbound first draft')
        self.assertEqual(A.load(self.path,self.c['id'])['answer'],before['answer'])

    def test_failed_compose_saves_recovery_link_and_preserves_error_and_accounting(self):
        import copy
        from clinical_core.agere.gateway import ProviderUnavailable
        from clinical_core.agere.service import _failure_receipts
        from clinical_core.agere.deliverables import workflow
        partial=copy.deepcopy(self.result);partial['answer'].update(status='partial',verification='unverified')
        partial['execution_snapshot']=A.source_identity(self.path)
        partial['investigation']={'recovery_checkpoint':{'draft_digest':'retained-compiler-draft'}}
        for blocked in (False,True):
            exc=ProviderUnavailable('Subscription limit reached',kind='rate_limit')
            exc.partial_result=copy.deepcopy(partial)
            exc.planner_receipt={'new_paid_api_calls':1,'known_api_estimate_usd':.01,'api_cost_known':False}
            if blocked:exc.partial_result['execution_snapshot']['source_digest']='changed-source'
            stack=SimpleNamespace(path=self.path,allow_live=False,compose=mock.Mock(side_effect=exc))
            with mock.patch.object(workflow,'fulfill') as fulfill:
                with self.assertRaises(ProviderUnavailable) as caught:
                    conversation.handle_compose(stack,{'question':self.question,'conversation_id':'recovery'})
            self.assertIs(caught.exception,exc);self.assertEqual(stack.compose.call_count,1);fulfill.assert_not_called()
            receipts=_failure_receipts(exc)
            self.assertIn('cost_receipt',receipts);self.assertIn('cost_telemetry',receipts)
            if blocked:
                self.assertNotIn('action_context',receipts)
                self.assertIn('Warehouse changed',receipts['action_context_error'])
            else:
                saved=A.load(self.path,receipts['action_context']['id'])
                self.assertEqual(saved['answer'],partial['answer'])
                self.assertEqual(saved['investigation'],partial['investigation'])
                self.assertEqual(saved['original_question'],self.question)
                with sqlite3.connect(self.path/'conversations.sqlite3') as db:
                    rows=db.execute('SELECT answer_summary FROM turns WHERE conversation_id=?',('recovery',)).fetchall()
                self.assertEqual(json.loads(rows[-1][0])['action_context'],receipts['action_context'])

    def test_evaluation_failure_does_not_save_its_unfinished_draft(self):
        from clinical_core.agere.gateway import ProviderUnavailable
        from clinical_core.agere.clinical import evaluation
        exc=ProviderUnavailable('Subscription limit reached',kind='rate_limit')
        exc.partial_result=self.result
        stack=SimpleNamespace(path=self.path,allow_live=False,compose=mock.Mock(side_effect=exc))
        with evaluation.scope('authored-recovery-fixture'),mock.patch.object(A,'capture') as capture:
            with self.assertRaises(ProviderUnavailable):
                conversation.handle_compose(stack,{'question':self.question})
        capture.assert_not_called();self.assertFalse(hasattr(exc,'action_context'))

    def test_interrupted_capture_preserves_partial_without_publishing_and_retries(self):
        self.result['answer']['headline'] = 'New snapshot'
        def interrupted(data, stream, **kwargs):
            stream.write('{"interrupted":')
            raise OSError(28, 'No space left on device')
        with mock.patch.object(A.json, 'dump', side_effect=interrupted):
            with self.assertRaises(OSError):
                A.capture(self.path, self.question, self.result)
        partial, = A.root(self.path).glob('*/.answer-*.partial')
        self.assertEqual(partial.read_text(), '{"interrupted":')
        self.assertFalse((partial.parent / 'answer.json').exists())
        recovered = A.capture(self.path, self.question, self.result)
        self.assertEqual(A.load(self.path, recovered['id'])['answer'], self.result['answer'])
        self.assertEqual(partial.parent.name, recovered['id'])
        self.assertEqual(partial.read_text(), '{"interrupted":')
        self.assertEqual(A.load(self.path, self.c['id'])['answer']['headline'], 'Three patients')

    def test_capture_sync_failure_does_not_publish(self):
        self.result['answer']['headline'] = 'Not durable yet'
        with mock.patch.object(A.os, 'fsync', side_effect=OSError(28, 'No space left on device')):
            with self.assertRaises(OSError):
                A.capture(self.path, self.question, self.result)
        partial, = A.root(self.path).glob('*/.answer-*.partial')
        self.assertEqual(json.loads(partial.read_text())['answer'], self.result['answer'])
        self.assertFalse((partial.parent / 'answer.json').exists())

    def test_capture_publication_keeps_concurrent_complete_snapshot_immutable(self):
        self.result['answer']['headline'] = 'Concurrent snapshot'
        winner = {}
        def competing_writer(partial, target):
            # The complete temporary snapshot is invisible until publication.
            self.assertFalse(target.exists())
            data = json.loads(partial.read_text())
            data['created_at'] = 'first complete writer'
            winner['bytes'] = json.dumps(data).encode()
            target.write_bytes(winner['bytes'])
            raise FileExistsError(target)
        with mock.patch.object(A.os, 'link', side_effect=competing_writer):
            captured = A.capture(self.path, self.question, self.result)
        target = A.root(self.path) / captured['id'] / 'answer.json'
        self.assertEqual(target.read_bytes(), winner['bytes'])
        self.assertEqual(A.load(self.path, captured['id'])['answer'], self.result['answer'])
        self.assertFalse(list(target.parent.glob('.answer-*.partial')))
        with mock.patch.object(A.json, 'dump', side_effect=AssertionError('Must not rewrite')):
            self.assertEqual(A.capture(self.path, self.question, self.result)['id'], captured['id'])
        self.assertEqual(target.read_bytes(), winner['bytes'])

    def test_connected_work_survives_reopen_and_duplicate_create(self):
        result = A.create_work(self.path, self.req())
        self.assertEqual([i['patient_id'] for i in result['items']], ['A','B'])
        item = result['items'][0]
        A.update_work(self.path, {'context_id':self.c['id'],'item_id':item['id'],'revision':1,
                                 'state':'blocked','owner':'Evidence review team','note':'Need provider attribution'})
        # A repeated click cannot reset ownership, notes or progress.
        with self.assertRaisesRegex(ValueError, 'different owner'):
            A.create_work(self.path,self.req(owner='Different team'))
        reopened = A.inspect(self.path, self.c['id'])
        self.assertEqual(reopened['context']['question'],self.question)
        self.assertEqual(reopened['counts'],{'blocked':1,'needs_review':1})
        self.assertEqual(reopened['items'][0]['owner'],'Evidence review team')
        db = A._db(self.path,reopened['context'])
        self.assertEqual(db.execute('SELECT count(*) FROM work_events').fetchone()[0],3)
        self.assertEqual(db.execute('SELECT count(*) FROM work_requests').fetchone()[0],0)
        self.assertEqual(db.execute('SELECT count(*) FROM question_decision_tasks').fetchone()[0],2)
        db.close()
        self.assertFalse(any(i['external_dispatch_performed'] for i in reopened['items']))

    def test_historical_summaries_are_not_authorized_by_legacy_flags(self):
        for shape, table, select in [
            ('value', None, [{'kind':'avg'}]),
            ('table', [{'payer':'P1','count':3}], [{'kind':'count'}]),
            ('table', [{'payer':'P1','amount':8}], [{'kind':'sum'}]),
        ]:
            context = {'answer': {'status':'answered','answer_type':shape,'table':table,
                                   'patient_ids':['A','B'],'actionable_cohort':True},
                       'planner_receipt': {'request_contract': {'request': {'select':select}}}}
            self.assertFalse(A.supports_patient_work(context))

    def test_unversioned_legacy_patient_lists_require_refresh(self):
        target = A.root(self.path)/self.c['id']/'answer.json'
        old = json.loads(target.read_text())
        old.pop('context_policy_identity')
        old['planner_receipt'] = {'route': 'local_compiler'}
        target.write_text(json.dumps(old))
        archived = A.load(self.path, self.c['id'])
        self.assertTrue(archived['invalidated'])
        self.assertEqual(archived['patient_ids'], ['A','B','C'])
        with self.assertRaisesRegex(ValueError, 'invalidated'):
            A.create_work(self.path, self.req())

    def test_wrong_cohort_stale_data_and_conflicting_revision_rejected(self):
        with self.assertRaisesRegex(ValueError,'outside'): A.create_work(self.path,self.req(patient_ids=['OUTSIDE']))
        item=A.create_work(self.path,self.req())['items'][0]
        with self.assertRaisesRegex(ValueError,'changed in another'): A.update_work(self.path,{'context_id':self.c['id'],'item_id':item['id'],'revision':0,'state':'blocked','owner':'Team','note':''})
        db=sqlite3.connect(self.path/'semantic.sqlite3');db.execute("UPDATE meta SET value='new-source' WHERE key='source_digest'");db.commit();db.close()
        self.assertTrue(A.inspect(self.path,self.c['id'])['context']['stale'])
        with self.assertRaisesRegex(ValueError,'data changed'): A.create_work(self.path,self.req())

    def test_missing_fields_and_non_roster_ids_do_not_become_work(self):
        self.result['answer']['missing']=['The PCP relation is unresolved']
        self.c=A.capture(self.path,self.question,self.result)
        with self.assertRaisesRegex(ValueError,'incomplete'): A.create_work(self.path,self.req())
        self.result['answer']['missing']=[]
        self.result['answer']['table']=[{'patient_id':'INVENTED'}]
        self.c=A.capture(self.path,self.question,self.result)
        with self.assertRaisesRegex(ValueError,'absent from the source roster'): A.create_work(self.path,self.req(patient_ids=['INVENTED']))

    def test_two_programs_preserve_exact_patient_evidence(self):
        A.create_work(self.path,self.req())
        d=A.create_work(self.path,self.req(program='rpm',patient_ids=['B','C']))
        self.assertEqual(len(d['items']),4)
        c,b=A.evidence_bundle(self.path,self.c['id'])
        self.assertEqual(c['patient_ids'],['A','B','C'])
        self.assertEqual(c['patient_rows'][1]['evidence_rows'][0]['pcp'],None)
        self.assertEqual(len(b.receipts),2)
        self.assertEqual(len(A.listing(self.path)),1)

    def test_partial_answer_can_prepare_only_source_evidence_review(self):
        from clinical_core.agere.deliverables import synthetic_actions
        for status in ('answered', 'partial', 'low_confidence'):
            with self.subTest(status=status):
                self.result['answer'].update(status=status, missing=['PCP relation is unresolved'],
                    limitations=['Machine support check inconclusive; no clinical validation'])
                self.c=A.capture(self.path,self.question,self.result)
                before=A.load(self.path,self.c['id'])
                for program in ('custom','pharmd','rpm'):
                    with self.assertRaisesRegex(ValueError,'incomplete'):
                        A.create_work(self.path,self.req(program=program))
                req=self.req(program='evidence_review',patient_ids=['A'])
                item=A.create_work(self.path,req)['items'][0]
                self.assertEqual(item['state'],'needs_review')
                self.assertEqual(item['evidence_review']['answer_status'],status)
                self.assertEqual(item['evidence_review']['unresolved_requirements'],['PCP relation is unresolved'])
                self.assertIn('inconclusive',item['evidence_review']['limitations'][0])
                self.assertFalse(item['evidence_review']['clinical_execution_authorized'])
                self.assertFalse(item['external_dispatch_performed'])
                self.assertEqual(A.create_work(self.path,req)['items'],[item])
                with self.assertRaisesRegex(ValueError,'no care simulation'):
                    synthetic_actions.advance(self.path,dict(context_id=self.c['id'],item_id=item['id'],
                        revision=1,scenario='happy_path'))
                for revision,state in ((1,'in_review'),(2,'review_complete')):
                    A.update_work(self.path,dict(context_id=self.c['id'],item_id=item['id'],revision=revision,
                        state=state,owner=item['owner'],note='Source review progress only'))
                self.assertEqual(A.load(self.path,self.c['id']),before)
                self.assertEqual(A.inspect(self.path,self.c['id'])['items'][0]['state'],'review_complete')

    def test_evidence_review_keeps_scope_freshness_and_revision_guards(self):
        self.result['answer'].update(status='partial',missing=['Source check inconclusive'])
        self.c=A.capture(self.path,self.question,self.result)
        req=self.req(program='evidence_review')
        with self.assertRaisesRegex(ValueError,'outside'):
            A.create_work(self.path,{**req,'patient_ids':['OUTSIDE']})
        item=A.create_work(self.path,req)['items'][0]
        with self.assertRaisesRegex(ValueError,'changed in another'):
            A.update_work(self.path,dict(context_id=self.c['id'],item_id=item['id'],revision=0,
                state='in_review',owner=item['owner'],note=''))
        with sqlite3.connect(self.path/'semantic.sqlite3')as db:
            db.execute("UPDATE meta SET value='changed' WHERE key='source_digest'")
        with self.assertRaisesRegex(ValueError,'data changed'):
            A.create_work(self.path,req)

    def test_evidence_review_cannot_turn_summary_membership_into_work(self):
        self.result['answer'].update(status='partial',answer_type='value',actionable_cohort=False,
                                    missing=['No actionable cohort'])
        self.c=A.capture(self.path,self.question,self.result)
        with self.assertRaisesRegex(ValueError,'analytical summary'):
            A.create_work(self.path,self.req(program='evidence_review'))

    def test_frontier_uses_same_frozen_evidence_and_no_other_data_tools(self):
        C=importlib.import_module('clinical_core.agere.deliverables.compose')
        def sender(body):
            self.assertEqual(body['tool_choice'],{'type':'none'})
            text=body['messages'][0]['content']
            self.assertIn('saved_answer',text)
            self.assertIn('Observed missed appointments',text)
            if 'You review presentation fidelity' in body.get('system',''):
                return {'content':[{'type':'text','text':json.dumps({'coverage':'complete','support':'supported','issues':[]})}]}, {'cost_estimate':{'estimated_usd':0}}
            return {'content':[{'type':'text','text':'TITLE: Review command center\n```html\n<h1>Review command center</h1><p>Clinical suitability needs review.</p>\n```'}]}, {'cost_estimate':{'estimated_usd':0}}
        with mock.patch.object(C.toolsmod.ToolContext,'call',side_effect=AssertionError('Must not fetch a new cohort')):
            r=A.compose_frontier(self.path,{'context_id':self.c['id'],'request':'Build a war room for these patients'},allow_live=True,send=sender)
        self.assertEqual(r['status'],'delivered')
        self.assertEqual(r['tool_calls'],[])
        self.assertEqual(r['costs']['frontier']['calls'],1)
        self.assertEqual(A.inspect(self.path,self.c['id'])['items'],[])
        with self.assertRaisesRegex(ValueError,'disabled'):
            A.compose_frontier(self.path,{'context_id':self.c['id'],'request':'Build a view'})

    def test_compose_route_saves_context_in_conversation_without_new_query(self):
        stack=SimpleNamespace(path=self.path,allow_live=False,compose=mock.Mock(return_value=self.result))
        with mock.patch('clinical_core.agere.clinical.learning.record_outcome'):
            r=conversation.handle_compose(stack,{'question':self.question,'conversation_id':'independent-workflow'})
        stack.compose.assert_called_once()
        self.assertIn('action_context',r)
        summary=conversation.turns(self.path,'independent-workflow')[0]['answer_summary']
        self.assertEqual(summary['action_context']['id'],r['action_context']['id'])
        self.assertIn('Open action workspace',ask_view.page())

class Scope(unittest.TestCase):
    def test_full_user_request_is_not_a_beta_blocker_metric(self):
        library=metric_router.routable()
        metric=library['hf_no_beta_blocker']
        missing=metric_router.unaccounted_clinical_scope(USER_QUESTION,metric)
        self.assertEqual(set(missing),{'GDMT regimen assessment','ejection fraction','dose assessment'})
        self.assertEqual(answer_shape.requested(USER_QUESTION)['shape'],'table')
        self.assertIsNotNone(metric_router.shape_misfit(USER_QUESTION,'hf_no_beta_blocker',{'operation':'sql','parameters':{'returns':'patient_set'}}))
        self.assertEqual(metric_router.unaccounted_clinical_scope('Find patients with heart failure and no beta blocker',metric),[])

    def test_other_patient_details_remain_tables_and_filters_remain_sets(self):
        for q in ('List patients with CKD and include their nephrologist',
                  "Find patients with asthma and show each patient's PCP",
                  'List patients with heart failure and reduced EF who are not on maximum dose, and list each patient\'s PCP and cardiologist'):
            self.assertEqual(answer_shape.requested(q)['shape'],'table',q)
        self.assertEqual(answer_shape.requested('Which patients have a PCP?')['shape'],'patient_set')

if __name__=='__main__': unittest.main()
