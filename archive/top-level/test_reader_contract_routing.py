import copy
from unittest.mock import patch
from clinical_core.agere.clinical import reader_contract_routing as M
from clinical_core.agere.clinical import semantic_reader as R
from clinical_core.contracts import digest
import test_question_decision as fixtures


class ContractRoute(fixtures.Decision):
    # Reuse realistic question/work fixture, without inheriting its test cases below.
    def setUp(self):
        super().setUp()
        self.row=self.prepare();self.task=self.queue(self.row)
        self.target={'patient_id':'p1','record_id':'r1','field':'text','start':0,'end':6,'concept':'Review'}
        self.body={'decision_id':self.row['id'],'task_id':self.task['id'],'target':self.target,'cohort_id':'a'*64,'role':'cohort_lexical'}
        self.cover={'unavailable_labels':{a:[] for a in R.AXES},'selective_threshold':None,'model_identity':'m'}
        self.cover['unavailable_labels']['time']=['planned','unknown']
        self.patch(M.C,'model_specs',return_value=({'cohort_lexical':{'model_identity':'m'},'kev':{'model_identity':'k'}},[]))
        self.patch(M.V,'inspect',side_effect=lambda *a:{'roles':{'cohort_lexical':copy.deepcopy(self.cover)}})
        self.patch(M.R,'source',side_effect=lambda path,target:(self.source_record,self.source_record['text'],{'snapshot':{'as_of':'2026-10-10'}}))
        self.patch(M.R,'packet',side_effect=lambda record,text,target,date:{'synthetic':True,'text':text,'target_start':0,'target_end':6,'target_quote':text[:6],'concept':'Review','context_complete':True})
        self.patch(M.R,'request',return_value={})

    def test_missing_label_abstains_without_inference_and_keeps_work(self):
        before=M.Q.reopen(self.retriever,self.row['id'])['tasks']
        with patch.object(M.C,'prepare',side_effect=AssertionError('must not call model')):
            a=M.run(self.retriever,self.body);b=M.run(self.retriever,self.body)
        self.assertEqual(a,b);self.assertIsNone(a['proposed_labels']);self.assertIsNone(a['reading_id'])
        self.assertEqual(a['status'],'abstained_unsupported_contract')
        self.assertEqual(M.reopen(self.retriever,{'result_id':a['result_id']}),a)
        after=M.Q.reopen(self.retriever,self.row['id']);self.assertEqual(after['tasks'],before)
        self.assertEqual(len(after['followups']),1)

    def test_complete_label_set_never_qualifies(self):
        c={'unavailable_labels':{a:[] for a in R.AXES}}
        d=M.disposition(c);self.assertTrue(d['can_propose']);self.assertFalse(d['automatic_acceptance'])
        self.assertFalse(d['clinical_authority'])

    def test_every_missing_class_blocks_complete_contract(self):
        for axis,labels in R.AXES.items():
            for label in labels:
                c={'unavailable_labels':{a:[] for a in R.AXES}};c['unavailable_labels'][axis]=[label]
                self.assertFalse(M.disposition(c)['can_propose'])

    def test_arbitrary_narrower_contract_or_role_rejected(self):
        for body in [{**self.body,'required_labels':{}},{**self.body,'role':'claude'},{**self.body,'role':'invented'}]:
            with self.assertRaises(ValueError):M.run(self.retriever,body)

    def test_wrong_patient_source_task_rejected(self):
        for changed in [{'target':{**self.target,'patient_id':'other'}},{'task_id':'x'*64}]:
            with self.assertRaises(ValueError):M.run(self.retriever,{**self.body,**changed})
        self.source_record['record_id']='not-in-question'
        with self.assertRaises(ValueError):M.run(self.retriever,self.body)

    def test_changed_source_rejected(self):
        self.source_record['text']='Changed'
        with self.assertRaises(ValueError):M.run(self.retriever,self.body)

    def test_changed_model_is_stale_on_reopen(self):
        saved=M.run(self.retriever,self.body)
        with patch.object(M.V,'inspect',return_value={'roles':{'cohort_lexical':{**self.cover,'model_identity':'new'}}}):
            with self.assertRaises(ValueError):M.reopen(self.retriever,{'result_id':saved['result_id']})

    def test_stale_task_on_reopen(self):
        saved=M.run(self.retriever,self.body)
        self.fixture.cap['name']='Changed';self.fixture.update()
        with self.assertRaises(ValueError):M.reopen(self.retriever,{'result_id':saved['result_id']})

    def test_native_proposal_linked_without_automatic_state_change(self):
        req={**self.body,'role':'kev'};creq={'target':self.target,'cohort_id':'a'*64,'roles':['kev']}
        comparison={'id':'c','request':creq,'variants':{'kev':{'status':'completed','reading_id':'r','labels':{a:'unknown' for a in R.AXES}}}}
        with patch.object(M.C,'prepare',return_value={'comparison':comparison}) as call,patch.object(M.C,'inspect',return_value=comparison):
            row=M.run(self.retriever,req);self.assertEqual(call.call_args.args[1],creq)
            self.assertEqual(row['reading_id'],'r');self.assertFalse(row['automatic_acceptance'])
            self.assertEqual(M.reopen(self.retriever,{'result_id':row['result_id']}),row)
        self.assertEqual(M.Q.reopen(self.retriever,self.row['id'])['tasks'][0]['task']['revision'],1)

    def test_failed_native_remains_failure(self):
        req={**self.body,'role':'kev'}
        with patch.object(M.C,'prepare',return_value={'comparison':{'id':'c','variants':{'kev':{'status':'failed','reason':'failure'}}}}):
            row=M.run(self.retriever,req);self.assertEqual(row['status'],'reader_failed');self.assertIsNone(row['proposed_labels'])

    def test_changed_work_during_inference_not_saved(self):
        fixed,route=M.inputs(self.retriever,self.body)
        with patch.object(M,'inputs',side_effect=[(fixed,route),({**fixed,'task_revision':999},route)]):
            with self.assertRaises(ValueError):M.run(self.retriever,self.body)
        self.assertEqual(M.Q.reopen(self.retriever,self.row['id'])['followups'],[])

# Do not rerun inherited tests as additional new tests.
for name in dir(fixtures.Decision):
    if name.startswith('test_') and name not in ContractRoute.__dict__:setattr(ContractRoute,name,None)
