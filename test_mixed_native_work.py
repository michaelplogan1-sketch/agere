"""Connected mixed table dispatch, not a model-qualification campaign."""
import copy,json,unittest
from clinical_core.agere.clinical import mixed_native_work as M,backstop_agent as B,result_assembly as R
from clinical_core.agere.deliverables.receipts import uncompact_json
from test_execution_work_triage import fixture

ASOF='2030-05-27'

def mixed():
    a,c=fixture()
    doc={'name':'review_documents','input':{},'result':{'result_view':'agere_result_4',
        'columns':['patient_id','finding'],'rows':[['A','unresolved narrative'],['B','dated source claim']]}}
    c.append(doc)
    args={'base':'agere_result_3','keys':['patient_id'],'joins':[{'result':'agere_result_4','as':'documentary',
        'on':{'patient_id':'patient_id'},'mode':'one','columns':['finding']}]}
    r=R.compose({x['result']['result_view']:x['result']for x in c},**args);r['result_view']='agere_result_5'
    c.append({'name':'compose_table','input':args,'result':r})
    a.update(table=r['rows'],table_from_tool=r['result_view'],table_binding={'result_view':r['result_view'],'columns':r['columns']})
    return a,c

def judge(*args,**kwargs):
    return {'judgments':{key:{'label':'supported','probability':.95,'threshold':.85,'decided_by':'authored'}for key in args[1]},'steps':[]}

class MixedNativeWork(unittest.TestCase):
    def test_mixed_fields_reuse_one_program_and_preserve_narrative_obligation(self):
        a,c=mixed();before=copy.deepcopy((a,c));branches=M.candidates(a,c,ASOF)
        self.assertEqual(len(branches),1);self.assertNotIn('documentary',branches[0]['fields'])
        cache={};r=M.review('Show received values and source findings',branches,judge=judge,ledger=None,cache=cache)
        ps=B.support_packets('Show received values and source findings',a,c,[],ASOF)
        M.attach(ps,a,branches,r);p=next(p for p in ps if p['id']=='patient_A')
        state=uncompact_json(json.loads(p['text']))
        self.assertIsNone(B.native_field_proof(p,next(c for c in p['claim_manifest']if c['field']=='documentary'),state))
        self.assertIsNotNone(B.native_field_proof(p,next(c for c in p['claim_manifest']if c['field']=='value'),state))
        seen=[]
        def unresolved(*args,**kw):seen.extend(v['instructions']['claim']['field']for v in args[1].values());return {'judgments':{},'steps':[]}
        out=B.localize_support_failure(p,unresolved,as_of=ASOF,ledger=None)
        self.assertEqual(seen,['documentary']);self.assertFalse(out['field_review']['all_fields_supported'])
        seen.clear()
        support=B.check_support_packets([p],lambda *a,**k:self.fail('native fields must use field obligations first'),as_of=ASOF,ledger=None,field_decider=unresolved)
        self.assertEqual(seen,['documentary']);self.assertEqual(support['label'],'unknown')
        self.assertEqual((a,c),before)
        a['headline']='Different prose, unchanged evidence'
        again=M.review('Show received values and source findings',M.candidates(a,c,ASOF),judge=lambda *a,**k:self.fail('unchanged program was rejudged'),ledger=None,cache=cache)
        self.assertTrue(again[0]['reused'])
        M.review('Different question',branches,judge=judge,ledger=None,cache=cache)
        self.assertEqual(len(cache),2)

    def test_unknown_or_narrative_route_never_creates_field_support(self):
        a,c=mixed();bs=M.candidates(a,c,ASOF)
        for label in ['unknown','source_reading','wrong_program']:
            def abstain(*args,**kw):return {'judgments':{key:{'label':label,'probability':.99,'threshold':.85,'decided_by':None if label=='unknown'else'authored'}for key in args[1]},'steps':[]}
            reviews=M.review('Show sources',bs,judge=abstain,ledger=None,cache={})
            self.assertFalse(reviews[0]['supported_fields'])
            ps=B.support_packets('Show sources',a,c,[],ASOF);M.attach(ps,a,bs,reviews)
            self.assertFalse(any(p.get('shared_program_fields')for p in ps))

    def test_changed_copy_source_date_or_assembly_cannot_carry_proof(self):
        a,c=mixed();bs=M.candidates(a,c,ASOF)
        for change in ('cell','source','clock','assembly'):
            aa,cc=copy.deepcopy((a,c))
            if change=='cell':aa['table'][0]['value']=0
            elif change=='source':cc[0]['result']['source_contracts']['source_digest']='changed'
            elif change=='clock':cc[0]['result']['source_contracts']['as_of']='2028-01-01'
            else:cc[-1]['input']['joins'][0]['mode']='collect'
            self.assertEqual(M.candidates(aa,cc,ASOF),[],change)
        reviews=M.review('Show sources',bs,judge=judge,ledger=None,cache={})
        ps=B.support_packets('Show sources',a,c,[],ASOF);M.attach(ps,a,bs,reviews)
        p=next(p for p in ps if p.get('shared_program_fields'));state=uncompact_json(json.loads(p['text']));claim=next(c for c in p['claim_manifest']if c['field']=='value')
        state['answer_rows'][0]['value']=123
        self.assertIsNone(M.field_proof(p,claim,state))
        p['text']+=' '
        self.assertIsNone(M.field_proof(p,claim,uncompact_json(json.loads(p['text']))))

    def test_bound_row_prose_keeps_unchanged_native_fields_separate(self):
        a,c=fixture()
        a['table']=copy.deepcopy(a['table'])
        for row in a['table']:row['interpretation']='Unreviewed narrative statement'
        a['row_prose']={'rows':[]}
        a['table_binding']['interpreted_columns']=['interpretation']
        bs=M.candidates(a,c,ASOF)
        self.assertEqual(len(bs),1);self.assertIn('value',bs[0]['fields'])
        self.assertNotIn('interpretation',bs[0]['fields'])
        a['table'][0]['value']=999
        self.assertEqual(M.candidates(a,c,ASOF),[])

    def test_shared_fault_and_unknown_do_not_become_patient_review_campaigns(self):
        a,c=mixed();bs=M.candidates(a,c,ASOF)
        for label in ('wrong_program','unknown','source_reading'):
            def typed(*args,**kw):return {'judgments':{key:{'label':label,'probability':.95,'threshold':.85,'decided_by':None if label=='unknown'else'authored'}for key in args[1]},'steps':[]}
            rs=M.review('Show sources',bs,judge=typed,ledger=None,cache={})
            work=M.pending_program_work(bs,rs)
            self.assertEqual(bool(work['faults']),label=='wrong_program')
            self.assertEqual(bool(work['unresolved']),label=='unknown')

    def test_frontier_program_role_receives_only_undecided_fields_without_coverage(self):
        import os
        from unittest.mock import patch
        from types import SimpleNamespace
        a,c=mixed();bs=M.candidates(a,c,ASOF);seen=[]
        def partly(*args,**kw):
            r=judge(*args,**kw);r['judgments']['program_1'].update(label='unknown',decided_by=None,probability=.5)
            return r
        def frontier(packet,ledger):
            seen.append(packet)
            self.assertEqual(len(packet['claim_manifest']),1)
            verdict={'supported_claim_ids':[packet['claim_manifest'][0]['id']],
                'findings':[],'joint_support':'supported','joint_reason':'Authored program-role control'}
            def send_loop(*args,**kw):
                self.assertNotIn('coverage',kw['extra']['output_config']['format']['schema']['properties'])
                return {'answer':verdict,'model':'authored','new_paid_api_calls':1,'estimated_cost_usd':None}
            with patch.dict(os.environ,{'AGERE_CLAUDE_TRANSPORT':'subscription','ANTHROPIC_API_KEY':'subscription-transport'}),patch(
                'clinical_core.agere.clinical.legacy_proposers._send',return_value=lambda _:None),patch(
                'clinical_core.agere.clinical.semantic_analyst.tool_loop',side_effect=send_loop):
                return B.frontier_packet_review(SimpleNamespace(),'Show sources',packet,ledger=None,scope='program')
        rs=M.review('Show sources',bs,judge=partly,ledger=None,cache={},frontier=frontier)
        self.assertEqual(len(seen),1);self.assertEqual(len(rs[0]['supported_fields']),len(bs[0]['fields']))
        self.assertIsNone(rs[0]['frontier_cost_usd']);self.assertFalse(M.pending_program_work(bs,rs)['unresolved'])
        ps=B.support_packets('Show sources',a,c,[],ASOF);M.attach(ps,a,bs,rs)
        self.assertTrue(any(p.get('shared_program_fields')for p in ps))

    def test_rate_limit_preserves_typed_partial_and_never_falls_through(self):
        from clinical_core.agere.gateway import ProviderUnavailable
        a,c=mixed();bs=M.candidates(a,c,ASOF);cache={}
        def partly(*args,**kw):
            r=judge(*args,**kw);r['judgments']['program_1'].update(label='unknown',decided_by=None,probability=.5)
            return r
        def limited(*args):raise ProviderUnavailable('account rate limit',kind='rate_limit',attempts=[{'status':'failed'}])
        with self.assertRaises(ProviderUnavailable):M.review('Show sources',bs,judge=partly,ledger=None,cache=cache,frontier=limited)
        self.assertEqual(len(cache),1)
        record=next(iter(cache.values()))
        self.assertTrue(record['supported_fields']);self.assertIsNone(record['frontier_cost_usd'])
        self.assertIn('rate limit',record['frontier_failure']['error'])

    def test_metadata_predicates_are_candidates_not_answer_acceptance(self):
        a,c=fixture();r=c[0]['result'];r['executed_query']="SELECT patient_id,code,category,value FROM records WHERE LOWER(COALESCE(json_extract(payload,'$.specialty'),json_extract(payload,'$.referral_type'),'')) LIKE '%cardio%'"
        # Rebuild assembly receipts after changing their native source.
        c[-1]['result']={**R.compose({x['result']['result_view']:x['result']for x in c[:-1]},**c[-1]['input']),'result_view':'agere_result_3'}
        proof=B.native_program_candidate(a,c,[],ASOF);self.assertIsNotNone(proof)
        self.assertNotIn('accepted',proof)
        r['executed_query']=r['executed_query'].replace('$.specialty','$.note_text')
        self.assertIsNone(B.native_program_candidate(a,c,[],ASOF))

if __name__=='__main__':unittest.main()
