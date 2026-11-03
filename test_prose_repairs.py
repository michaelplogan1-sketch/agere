"""Localized edits retain the entire candidate and receive complete validation."""
import copy,json,unittest
from clinical_core.agere.clinical import execution_state as E,backstop_agent as B,semantic_analyst as S
from clinical_core.agere import model_policy as M
from clinical_core.contracts import digest


class ProseRepairs(unittest.TestCase):
    def setUp(self):
        self.calls=[{'name':'query_sql','input':{'sql':'SELECT patient_id,events FROM scoped'},
            'result':{'result_view':'agere_result_1','columns':['patient_id','events'],
                      'rows':[['A',2],['B',None]],'truncated':False}}]
        self.base={'status':'answered','answer_type':'table','table_from_tool':'agere_result_1',
            'headline':{'template':'Received count: {n}.','bindings':{'n':{'result_view':'agere_result_1','row':0,'column':'events'}}},
            'sources':[{'record_id':'note_A','quote':'Dated observation','date':'2030-01-01'}],
            'missing':['Current responsibility is unestablished.'],
            'row_prose':{'keys':['patient_id'],'rows':[
                {'key':{'patient_id':pid},'basis':[{'result_view':'agere_result_1','row':i}],
                 'fields':{'explanation':'A dated observation; current responsibility is unknown.',
                           'review':'Confirm current responsibility.'}}for i,pid in enumerate(['A','B'])]}}
        self.compile(self.base)

    def compile(self,a):
        B.materialize_table(a,self.calls);B.bind_computed_prose(a,self.calls)

    def patch(self,**fields):
        return {'repair_draft':{'draft_digest':digest(self.base),'rows':[{'key':{'patient_id':'A'},
            'fields':fields or {'review':'Review the dated observation before confirming current responsibility.'}}]}}

    def test_only_named_prose_changes_and_all_native_values_sources_bases_survive(self):
        original=copy.deepcopy(self.base);patch=self.patch();before=copy.deepcopy(patch)
        merged=E.apply_prose_repair(patch,self.base)
        self.assertIsInstance(merged['headline'],dict)
        self.compile(merged)
        for i in range(2):
            for key,value in original['table'][i].items():
                if i==0 and key=='review':continue
                self.assertEqual(merged['table'][i][key],value)
        self.assertEqual(merged['sources'],original['sources'])
        self.assertEqual(merged['missing'],original['missing'])
        self.assertEqual(merged['headline'],original['headline'])
        self.assertEqual([r['basis']for r in merged['row_prose']['rows']],[r['basis']for r in original['row_prose']['rows']])
        self.assertEqual(self.base,original);self.assertEqual(patch,before)
        self.assertEqual(E.draft_state(self.base)['localized_prose_repair']['draft_digest'],digest(self.base))

    def test_stale_digest_unknown_entities_duplicates_native_fields_and_deletions_rejected(self):
        for kind in ('stale','entity','duplicate','native','delete','basis','extra_top','key_type'):
            p=self.patch();r=p['repair_draft'];row=r['rows'][0]
            if kind=='stale':r['draft_digest']='stale'
            if kind=='entity':row['key']['patient_id']='C'
            if kind=='duplicate':r['rows'].append(copy.deepcopy(row))
            if kind=='native':row['fields']={'events':'changed'}
            if kind=='delete':row['fields']={'review':None}
            if kind=='basis':row['basis']=[]
            if kind=='extra_top':p['status']='answered'
            if kind=='key_type':row['key']['patient_id']=1
            before=copy.deepcopy(self.base)
            with self.subTest(kind=kind),self.assertRaises(ValueError):E.apply_prose_repair(p,self.base)
            self.assertEqual(self.base,before)

    def test_patch_cannot_bypass_numeric_or_same_entity_compiler_checks(self):
        for value in ('There were 42 events.',{'template':'Events: {n}.','bindings':{'n':{'result_view':'agere_result_1','row':1,'column':'events'}}}):
            merged=E.apply_prose_repair(self.patch(explanation=value),self.base)
            with self.subTest(value=value),self.assertRaises(ValueError):self.compile(merged)

    def test_noop_patch_is_not_a_new_repair(self):
        with self.assertRaisesRegex(ValueError,'no changed fields'):
            E.apply_prose_repair(self.patch(review=self.base['row_prose']['rows'][0]['fields']['review']),self.base)

    def test_changed_native_values_are_read_again_by_the_compiler(self):
        merged=E.apply_prose_repair(self.patch(),self.base)
        self.calls[0]['result']['rows'][0][1]=3
        self.compile(merged)
        self.assertEqual(merged['table'][0]['events'],3)
        self.assertIn('3',merged['headline'])

    def test_loop_merges_then_reviews_complete_answer_without_reexecuting_sources(self):
        sent=[];reviewed=[]
        original=copy.deepcopy(self.base)
        def review(a,c):
            self.assertEqual(c,self.calls);self.compile(a);reviewed.append(copy.deepcopy(a))
            if len(reviewed)==1:return {'status':'repair','feedback':'Repair the proposed review for A.'}
            self.assertEqual(len(a['table']),2)
            self.assertEqual(a['table'][1],original['table'][1])
            self.assertEqual(a['sources'],original['sources'])
            return {'status':'confirmed'}
        def send(body):
            sent.append(body)
            proposal=E.draft_state(self.base)['last_draft'] if len(sent)==1 else self.patch()
            if len(sent)==1:proposal['sources']=copy.deepcopy(self.base['sources'])
            else:proposal['repair_draft']['draft_digest']=digest(reviewed[-1])
            return {'content':[{'type':'text','text':json.dumps(proposal)}]},.001,False
        out=S.tool_loop('Explain each entity',[],[],lambda use:self.fail('Unexpected source execution'),send=send,
            model=M.SONNET,max_tools=None,initial_calls=self.calls,review=review)
        self.assertEqual(len(reviewed),2);self.assertEqual(len(sent),2)
        self.assertIn('repair_draft',sent[1]['messages'][-1]['content'])
        self.assertEqual(out['prose_repairs'][0]['changed_fields'],1)
        self.assertTrue(out['prose_repairs'][0]['complete_review_required'])
        self.assertEqual(out['answer']['status'],'answered')

    def test_repeated_invalid_patch_keeps_partial_answer_and_does_not_run_source_review(self):
        sent=[];checks=[]
        def send(body):
            sent.append(body)
            p=E.draft_state(self.base)['last_draft'] if len(sent)==1 else self.patch()
            if len(sent)>1:p['repair_draft']['draft_digest']='stale'
            return {'content':[{'type':'text','text':json.dumps(p)}]},.001,False
        def review(a,c):checks.append(a);return {'status':'repair','feedback':'Inspect the proposed review.'}
        out=S.tool_loop('Explain each entity',[],[],lambda use:None,send=send,model=M.SONNET,
            max_tools=None,initial_calls=self.calls,review=review)
        self.assertEqual(len(sent),3);self.assertEqual(len(checks),1)
        self.assertEqual(out['review_stopped'],'no_progress')
        self.assertEqual(out['answer']['status'],'partial')
        self.assertEqual(out['answer']['verification'],'unverified')


if __name__=='__main__':unittest.main()
