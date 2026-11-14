"""Native work routing must transfer across domains without waiving meaning."""
import copy,json,unittest
from clinical_core.agere.clinical import backstop_agent as B,result_assembly as R
from clinical_core.agere.deliverables.receipts import uncompact_json


def fixture(entity='patient_id'):
    first={'name':'query_sql','input':{'sql':'SELECT * FROM received_codes'},'result':{
        'result_view':'agere_result_1','columns':[entity,'code','category','value'],
        'rows':[['A','X1','observed',None],['B','X2','recorded',-2]],'truncated':False,
        'native_sql_contract':B.NATIVE_SQL_CONTRACT,
        'executed_query':f"SELECT {entity},code,CASE WHEN code LIKE 'X1%' THEN 'observed' ELSE 'recorded' END category,value FROM received_codes",
        'source_contracts':{'source_digest':'authored','as_of':'2030-05-27',
            'relations':[{'name':'received_codes','columns':[entity,'code','value'],'meaning':'Received code and value, not an interpretation.'}],'definitions':[]}}}
    second=copy.deepcopy(first);second['result'].update(result_view='agere_result_2',
        columns=[entity,'event_date','status'],rows=[['B','2029-01-01','referral_only']])
    second['input']['sql']=second['result']['executed_query']=f'SELECT {entity},event_date,status FROM received_events'
    second['result']['source_contracts']['relations']=[{'name':'received_events','columns':[entity,'event_date','status'],'meaning':'Dated received status, not ongoing responsibility.'}]
    args={'base':'agere_result_1','keys':[entity],'joins':[{'result':'agere_result_2','as':'events','on':{entity:entity},
        'mode':'collect','columns':['event_date','status'],'retain_in_final':True}]}
    result=R.compose({c['result']['result_view']:c['result']for c in [first,second]},**args)
    result['result_view']='agere_result_3'
    calls=[first,second,{'name':'compose_table','input':args,'result':result}]
    answer={'headline':'Received source categories and dated events.','facts':{},'table':copy.deepcopy(result['rows']),
        'table_from_tool':'agere_result_3','table_binding':{'result_view':'agere_result_3','columns':result['columns']}}
    return answer,calls


def choose(label='program_meaning',p=.95,decider='authored'):
    return lambda *a,**k:{'label':label,'decided_by':decider,'calibrated_probabilities':{label:p},'steps':[]}


class ExecutionWorkTriage(unittest.TestCase):
    def test_same_connected_route_for_patient_account_and_inventory(self):
        for entity in ('patient_id','account_id','item_id'):
            a,c=fixture(entity);before=copy.deepcopy((a,c))
            proof,route=B.triage_native_program('Show received categories and dated events',a,c,[],'2030-05-27',decider=choose(),ledger=None)
            self.assertFalse(route['answer_accepted']);self.assertEqual(proof['rows'],2)
            ps=B.support_packets('Show received categories and dated events',a,c,[],'2030-05-27',native_program=proof)
            self.assertEqual(len(ps),1);state=uncompact_json(json.loads(ps[0]['text']))
            self.assertEqual(len(state['native_table']['calculation_contracts']),3)
            self.assertEqual((a,c),before)
            # Native fidelity and favorable routing cannot override a wrong
            # definition/population/clock found by the actual meaning reviewer.
            out=B.check_support_packets(ps,lambda *a,**k:{'label':'contradicted','decided_by':'authored','steps':[]},
                as_of='2030-05-27',ledger=None)
            self.assertEqual(out['label'],'contradicted')

    def test_source_or_uncertain_routing_keeps_individual_dependencies(self):
        a,c=fixture()
        for label,p,who in [('source_reading',.99,'authored'),('unknown',.99,None),('program_meaning',.79,'authored'),('program_meaning',.99,None)]:
            proof,_=B.triage_native_program('Interpret the findings',a,c,[],'2030-05-27',decider=choose(label,p,who),ledger=None)
            self.assertIsNone(proof)

    def test_narrative_alias_and_json_paths_cannot_be_promoted_by_a_model(self):
        for expression in ["note_text AS code", "json_extract(payload,'$.reason') AS code", "CASE WHEN label LIKE '%denies%' THEN 0 ELSE 1 END AS code",'payload AS code']:
            a,c=fixture();c[0]['result']['executed_query']='SELECT patient_id,'+expression+' FROM record'
            proof,_=B.triage_native_program('Read the notes',a,c,[],'2030-05-27',
                decider=lambda *a,**k:self.fail('Native narrative guard must run first'),ledger=None)
            self.assertIsNone(proof)

    def test_native_copy_proof_preserves_source_versions_membership_and_nulls(self):
        for change in ('value','omitted','duplicate','type','date','source','assembly','incomplete','model_origin'):
            a,c=fixture()
            if change=='value':a['table'][0]['value']=0
            elif change=='omitted':a['table'].pop()
            elif change=='duplicate':a['table'].append(copy.deepcopy(a['table'][0]))
            elif change=='type':a['table'][1]['value']=False
            elif change=='date':c[0]['result']['source_contracts']['as_of']='2029-01-01'
            elif change=='source':c[0]['result']['source_contracts']['source_digest']='another'
            elif change=='assembly':c[-1]['input']['joins'][0]['mode']='one'
            elif change=='incomplete':c[0]['result']['truncated']=True
            else:c[0]['name']='judge_documents'
            self.assertIsNone(B.native_program_candidate(a,c,[],'2030-05-27'),change)

    def test_changed_or_undecided_route_cannot_carry_to_support(self):
        a,c=fixture();proof,_=B.triage_native_program('Show values',a,c,[],'2030-05-27',decider=choose(),ledger=None)
        for change in ('proof','judgment','source'):
            p=copy.deepcopy(proof);calls=copy.deepcopy(c)
            if change=='proof':p['rows']=1
            elif change=='judgment':p['work_triage']['decided_by']=None
            else:calls[0]['result']['source_contracts']['as_of']='2029-01-01'
            with self.assertRaises(ValueError):B.support_packets('Show values',a,calls,[],'2030-05-27',native_program=p)

    def test_cache_and_cost_observations_do_not_invalidate_identical_evidence(self):
        a,c=fixture();proofs=[]
        for cached in (False,True):
            def decide(*args,**kw):
                return {**choose()(),'cache_hit':cached,'cost_usd':0 if cached else .01,
                    'steps':[{'provider':'authored','cache_hit':cached}]}
            proof,_=B.triage_native_program('Show values',a,c,[],'2030-05-27',decider=decide,ledger=None)
            proofs.append(proof)
        self.assertEqual(*proofs)


if __name__=='__main__':unittest.main()
