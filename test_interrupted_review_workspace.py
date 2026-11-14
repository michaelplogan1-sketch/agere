"""One interrupted source attempt -> immutable answer -> visible progress -> owned review.
All source judgments/failures are authored fixtures; no provider or clinical qualification.
"""
import copy,json,shutil,subprocess,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from clinical_core.agere import ask_view
from clinical_core.agere.clinical import backstop_agent as B,semantic_analyst as S,system_one_gate as G
from clinical_core.agere.deliverables import answer_context as A,action_center as C
from clinical_core.agere.gateway import ProviderUnavailable
from test_answer_action_center import Workflow
from test_complete_claim_composition import packet,fields
from clinical_core.agere.deliverables.receipts import uncompact_json

class InterruptedWorkspace(unittest.TestCase):
    def setUp(self):
        Workflow.setUp(self)
        p=packet();row=uncompact_json(json.loads(p['text']))['answer_rows'][0]
        candidate={'status':'partial','answer_type':'table','headline':'Received source account.',
            'table':[row],'table_from_tool':'agere_result_1','facts':{},'sources':[],'missing':['Current assignment is not established.']}
        calls=[{'name':'review_documents','input':{},'result':{'result_view':'agere_result_1',
            'columns':list(row),'rows':[list(row.values())],'truncated':False}}]
        tools=SimpleNamespace(read={},document_reviews=[],source_records={})
        def reader(task,named,evidence,**kw):
            result=fields(task,named,evidence,**kw)
            result['judgments']['claim_1'].update(label='unknown',decided_by=None,probability=.5)
            result['steps']=[{'provider':'authored','status':'answered','cost_usd':.03,'attempt_id':'fixture-only'}]
            return result
        s1=SimpleNamespace(judge_fields=reader,decide=Mock(side_effect=AssertionError('No joint vote')))
        def review(answer,calls):
            return B.review_final(SimpleNamespace(path=None),self.question,answer,calls,tools,ledger=None,as_of=p['date'])
        with patch.object(G,'ready',return_value=True),patch.object(G,'s1',return_value=s1),patch.object(G,'check',return_value={
                'label':'answers_fully','decided_by':'authored','calls':0,'cost_usd':0}),patch.object(G,'check_pending_work',return_value={'targets':[],'calls':0,'cost_usd':0}),patch.object(B,'support_packets',return_value=[p]),patch.object(
                B,'frontier_source_review',side_effect=ProviderUnavailable('Authored account limit <script>',kind='rate_limit')),patch.dict(
                'os.environ',{'AGERE_FRONTIER_SOURCE_REVIEW':'on'}):
            run=S.tool_loop(self.question,'system',[],Mock(),send=Mock(side_effect=AssertionError('No model generation')),
                model='authored',max_tools=None,review=review,initial_answer=candidate,initial_calls=calls)
        self.assertIn('interrupted_review_draft',run,str({k:run.get(k)for k in ('error','answer_reviews')}))
        self.partial={'answer':run['answer'],'investigation':{k:run[k] for k in (
            'answer_reviews','tool_calls','interrupted_review_draft','review_stopped','error')}}
        self.saved=A.capture(self.path,self.question,self.partial)
        self.key=self.saved['id']

    def test_save_reopen_inspect_and_review_keeps_service_dependency_separate(self):
        target=A.snapshot_path(A.root(self.path)/self.key/'answer.json');before=target.read_bytes()
        record=A.inspect(self.path,self.key);context=record['context'];progress=context['verification_progress']
        self.assertEqual(context['answer'],self.partial['answer']);self.assertFalse(context['supports_patient_work'])
        self.assertTrue(context['supports_evidence_review']);self.assertEqual(progress['recorded_fields']['supported'],1)
        self.assertEqual(progress['recorded_fields']['unresolved'],1)
        self.assertEqual(progress['service_dependencies'][0]['kind'],'rate_limit')
        self.assertFalse(progress['cost_known']);self.assertEqual(progress['known_cost_usd'],.03)
        self.assertFalse(progress['whole_answer_accepted']);self.assertEqual(progress['unchecked_packet_ids'],[])
        self.assertEqual(C.inspection_view(record)['context']['verification_progress'],progress)
        request={'context_id':self.key,'program':'evidence_review','patient_ids':['A'],'owner':'Authored review team','note':'Inspect saved checks; service recovery unknown.'}
        work=A.create_work(self.path,request);item=work['items'][0]
        self.assertEqual(item['evidence_review']['verification_progress'],progress)
        self.assertEqual(item['evidence_review']['unresolved_requirements'],['Current assignment is not established.'])
        for state in ['in_review','review_complete']:
            work=A.update_work(self.path,{'context_id':self.key,'item_id':item['id'],'revision':item['revision'],
                'state':state,'owner':item['owner'],'note':'Staff inspection only; no source/provider recheck.'});item=work['items'][0]
        # Staff completion never closes the machine/service dependency or authorizes care.
        self.assertEqual(work['context']['verification_progress'],progress)
        self.assertEqual(work['context']['answer']['verification'],'unverified')
        self.assertFalse(work['external_dispatch_performed']);self.assertEqual(target.read_bytes(),before)
        with self.assertRaises(ValueError):A.create_work(self.path,{**request,'program':'custom'})
        with self.assertRaises(ValueError):A.create_work(self.path,{**request,'patient_ids':['OUTSIDE']})
        script=(Path(ask_view.__file__).parent/'assets/verification_progress.js').read_text()
        check="""
const assert=require('node:assert/strict'),p=JSON.parse(process.argv[1]),before=JSON.stringify(p);
const html=verificationProgressHTML(p);
assert.match(html,/Fields with recorded support/);assert.match(html,/Fields still undecided/);
assert.match(html,/account limit/);assert.match(html,/does not establish missing clinical evidence/);
assert.match(html,/Total review cost is unknown/);assert.match(html,/0.0300/);
assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
assert.equal(JSON.stringify(p),before);assert.equal(verificationProgressHTML(null),'');
console.log(JSON.stringify({workflow:'save-reopen-inspect-owned-review',recorded_supported_fields:p.recorded_fields.supported,
unfinished_fields:p.recorded_fields.unresolved,service_dependency:p.service_dependencies[0].kind,provider_calls:0,answer_promoted:false}));
"""
        result=subprocess.run([shutil.which('node'),'-e',script+check,json.dumps(progress)],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr);print(result.stdout.strip())
        self.assertIn('verificationProgressHTML(t.data.verification_progress)',ask_view.page())
        self.assertIn('verificationProgressHTML(c.verification_progress)',C.page())

    def test_same_draft_new_interrupted_checks_get_a_distinct_immutable_snapshot(self):
        candidate=copy.deepcopy(self.partial)
        candidate['investigation']['answer_reviews'][-1]['support']['packets'][0]['field_review']['unchecked_count']=1
        saved=A.capture(self.path,self.question,candidate)
        self.assertNotEqual(saved['id'],self.key)
        self.assertEqual(A.load(self.path,self.key)['investigation'],self.partial['investigation'])
        self.assertEqual(A.load(self.path,saved['id'])['investigation'],candidate['investigation'])

    def test_latest_completed_review_does_not_inherit_a_historical_service_stop(self):
        context=A.load(self.path,self.key)
        context['investigation']['answer_reviews'].append({'status':'confirmed'})
        self.assertIsNone(A.verification_progress(context))
        context=A.load(self.path,self.key);context['answer']['answer_type']='count'
        self.assertFalse(A.supports_evidence_review(context))

    def test_saved_request_without_generated_id_still_renders_its_computation(self):
        page=ask_view.page();start=page.index('function requestPlan(');end=page.index('function renderTurn(',start)
        script="const esc=v=>String(v??'');\n"+page[start:end]+"\nconst assert=require('node:assert/strict'); const out=requestPlan({planner_receipt:{request_contract:{kind:'checked_graph',graph:{nodes:[]}}}}); assert.match(out,/Request Not recorded/); assert.match(out,/nodes/);"
        result=subprocess.run([shutil.which('node'),'-e',script],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_legacy_error_is_visible_without_borrowing_earlier_field_judgments(self):
        context=A.load(self.path,self.key)
        context['investigation']['answer_reviews'][-1].pop('review_stop_reason')
        context['investigation']['error']='Claude subscription limit reached: API Error: This request would exceed your account\'s rate limit.'
        progress=A.verification_progress(context)
        self.assertIsNone(progress['recorded_fields']);self.assertIsNone(progress['unchecked_packet_ids'])
        self.assertEqual(progress['native_reference']['path'],['investigation','error'])
        self.assertEqual(progress['service_dependencies'][0]['kind'],'rate_limit')

if __name__=='__main__':unittest.main()
