"""Typed global statements cannot be hidden by a favorable joint packet vote."""
import copy
import json
import unittest
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import uncompact_json,readable_json


class GlobalProseReview(unittest.TestCase):
    def packets(self):
        rows=[{'period':'2028','prior_period':'2027','amount':12},
              {'period':'2029','prior_period':'2028','amount':15}]
        calls=[{'name':'query_sql','input':{'sql':'SELECT period,prior_period,amount FROM invoice_totals'},
                'result':{'result_view':'native','columns':list(rows[0]),'rows':rows,'read_views':[]}}]
        answer={'headline':'Received invoice amounts',
                'facts':{'comparison':'Every period uses a prior period of 2027.'},
                'table':rows,'table_from_tool':'native'}
        return B.support_packets('Compare invoice amounts by period',answer,calls,[],'2030-01-01')

    def check(self,packets,*,fault=None,joint='supported',adapter=True):
        seen=[];states=[]
        def fields(subject,questions,evidence,**kwargs):
            seen.append(('fields',list(questions)))
            state=json.loads(evidence[0]['text'])
            if 'encoding'in state:state=uncompact_json(state)
            states.append(state)
            return {'steps':[], 'judgments':{key:{'label':fault if key=='fact_0'and fault else'supported',
                'decided_by':None if key=='fact_0'and fault=='unknown'else'fixture',
                'probability':.95,'threshold':.8,'probabilities':{'supported':.95}}
                for key in questions}}
        def decide(*args,**kwargs):
            seen.append(('joint',None));return {'label':joint,'decided_by':'fixture','steps':[]}
        result=B.check_support_packets(packets,decide,as_of='2030-01-01',ledger=None,
                                       field_decider=fields if adapter else None)
        return result,seen,states

    def test_located_wrong_time_prevents_a_favorable_joint_override(self):
        packets=self.packets();before=copy.deepcopy(packets)
        result,seen,states=self.check(packets,fault='wrong_time')
        self.assertEqual(result['label'],'contradicted')
        self.assertEqual(seen,[('fields',['headline','fact_0'])])
        self.assertEqual(result['claim_feedback'][0]['path'],['answer','facts','comparison'])
        self.assertEqual(states[0]['all_final_rows'][1]['prior_period'],'2028')
        self.assertEqual(packets,before)

    def test_uncertainty_stays_unsettled_without_inventing_a_fault(self):
        result,seen,_=self.check(self.packets(),fault='unknown')
        self.assertEqual(result['label'],'unknown');self.assertIsNone(result['decided_by'])
        self.assertEqual(result['packets'][0]['field_review']['fault_count'],0)
        self.assertEqual(len(seen),1)

    def test_supported_fields_do_not_replace_joint_scope_review(self):
        result,seen,_=self.check(self.packets(),joint='contradicted')
        self.assertEqual(seen,[('fields',['headline','fact_0']),('joint',None),('fields',['headline','fact_0','table_2','table_3','table_4','table_5','table_6','table_7'])])
        self.assertNotEqual(result['label'],'supported')

    def test_supported_fields_and_joint_review_are_both_retained(self):
        result,seen,_=self.check(self.packets())
        self.assertEqual(result['label'],'supported');self.assertEqual(len(seen),2)
        review=result['packets'][0]['global_narrative_review']
        self.assertEqual(review['supported_count'],2)
        self.assertTrue(review['joint_dependency_review_still_required'])
        self.assertFalse(review['clinical_qualification'])

    def test_unavailable_field_reviewer_is_not_silently_bypassed(self):
        result,seen,_=self.check(self.packets(),adapter=False)
        self.assertEqual(result['label'],'unknown');self.assertFalse(seen)
        self.assertEqual(result['packets'][0]['status'],'global_narrative_review_unavailable')

    def test_omitted_or_mutated_manifest_cannot_skip_a_global_field(self):
        for mutation in ['omit','path','duplicate']:
            packets=self.packets();packet=packets[0]
            if mutation=='omit':packet['claim_manifest']=packet['claim_manifest'][1:]
            if mutation=='path':packet['claim_manifest'][1]['path']=['answer','headline']
            if mutation=='duplicate':packet['claim_manifest'].append(packet['claim_manifest'][0])
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.check(packets)

    def test_a_single_global_statement_keeps_the_joint_review(self):
        packet=self.packets()[0];state=uncompact_json(json.loads(packet['text']))
        state['answer'].pop('facts');state['claim_manifest']=[c for c in state['claim_manifest']if c['id']!='fact_0']
        packet.update(text=json.dumps(readable_json(state)),claim_manifest=state['claim_manifest'])
        result,seen,_=self.check([packet])
        self.assertEqual(result['label'],'supported');self.assertEqual(seen,[('joint',None)])


if __name__=='__main__':unittest.main()
