import copy
import json
import unittest
from clinical_core.agere.clinical import decision_information as I


def raw(value):
    return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(value)}]}


class InformationContracts(unittest.TestCase):
    def setUp(self):
        self.model = {'summary': 'Explicit hypothetical review map, not clinical eligibility.',
            'facts': [{'id':'a','missing_index':1,'statement':'The proposed route needs technology.'},
                      {'id':'b','missing_index':0,'statement':'Compatible technology is reported.'},
                      {'id':'c','missing_index':1,'statement':'The proposed route needs centre travel.'}],
            'choices': [{'id':'remote','title':'Review remote access','rationale':'Investigate the reported route and its technology.',
                         'conditions':[{'fact_id':'a','value':True},{'fact_id':'b','value':True}]},
                        {'id':'home','title':'Review access without centre travel','rationale':'Investigate the specified delivery route.',
                         'conditions':[{'fact_id':'a','value':True},{'fact_id':'c','value':False}]}], 'unmapped':[]}
        self.proposal={'result_id':'proposal','patient_id':'p','model':self.model,'requests':[
            {'id':'tech','missing_index':0,'fact_ids':['b']},
            {'id':'route','missing_index':1,'fact_ids':['a','c']}]}

    def test_shared_request_compared_with_original_question_order(self):
        I.parse_model(raw(self.model), [{},{}])
        result=I.calculate(self.proposal,1)
        self.assertEqual(result['comparison']['optimal']['request_ids'],['route'])
        self.assertEqual(result['comparison']['optimal']['covered_checks'],2)
        self.assertEqual(result['original_question_order_baseline']['covered_checks'],1)
        self.assertEqual(result['comparison']['optimal']['slots_used'],1)
        self.assertTrue(all(x['resolution']['current_value'] is None for x in result['choices']))

    def test_observation_changes_only_bound_propositions(self):
        after=I.calculate(self.proposal,1,{'a':True,'b':None,'c':True})
        values={c['contract_id']:c['resolution']['current_value'] for c in after['choices']}
        self.assertEqual(values,{'remote':None,'home':False})
        self.assertEqual(after['comparison']['optimal']['request_ids'],['tech'])
        self.assertFalse(I.LIMITS['task_state_changed'])

    def test_zero_slots_does_not_invent_coverage(self):
        self.assertEqual(I.calculate(self.proposal,0)['comparison']['optimal']['covered_checks'],0)

    def test_missing_question_cannot_disappear(self):
        with self.assertRaisesRegex(ValueError,'Every missing question'):
            I.parse_model(raw(self.model),[{},{},{}])
        m=copy.deepcopy(self.model);m['unmapped']=[{'missing_index':2,'reason':'No Boolean conditional effect supported.'}]
        self.assertEqual(I.parse_model(raw(m),[{},{},{}]),m)

    def test_invented_duplicate_unused_or_conflicting_conditions_rejected(self):
        for edit in ['invented','duplicate','unused','conflicting']:
            m=copy.deepcopy(self.model)
            if edit=='invented':m['choices'][0]['conditions'][0]['fact_id']='other'
            elif edit=='duplicate':m['facts'].append(m['facts'][0])
            elif edit=='unused':m['facts'].append({'id':'extra','missing_index':1,'statement':'Unused.'})
            else:m['choices'][0]['conditions'].append({'fact_id':'a','value':False})
            with self.subTest(edit=edit),self.assertRaises(ValueError):I.parse_model(raw(m),[{},{}])

    def test_all_requested_observations_required(self):
        obs={'values':[{'fact_id':'a','value':None,'reason':'Unknown.','citation_ids':[]}],'remaining':'Route unresolved.'}
        with self.assertRaises(ValueError):I.parse_observation(raw(obs),self.model['facts'],[])

    def test_unobserved_is_unresolved_and_cannot_resolve_without_citation(self):
        f=self.model['facts'][:1]
        for value in [True,False,1]:
            obs={'values':[{'fact_id':'a','value':value,'reason':'No supplied report.','citation_ids':[]}],'remaining':'Route unresolved.'}
            with self.subTest(value=value),self.assertRaises(ValueError):I.parse_observation(raw(obs),f,[])
        obs['values'][0]['value']=None
        self.assertIsNone(I.parse_observation(raw(obs),f,[])['values'][0]['value'])

    def test_foreign_and_duplicate_citations_rejected(self):
        for cs in [['foreign'],['c','c']]:
            obs={'values':[{'fact_id':'a','value':True,'reason':'Reported.','citation_ids':cs}],'remaining':'Other evidence unresolved.'}
            with self.subTest(cs=cs),self.assertRaises(ValueError):I.parse_observation(raw(obs),self.model['facts'][:1],[{'id':'c'}])

    def test_exact_cited_observation_retained(self):
        obs={'values':[{'fact_id':'a','value':True,'reason':'Patient reported the route at the stated date.','citation_ids':['c']}],'remaining':'Other evidence unresolved.'}
        self.assertEqual(I.parse_observation(raw(obs),self.model['facts'][:1],[{'id':'c'}]),obs)

    def test_truncated_output_rejected(self):
        with self.assertRaises(ValueError):I.parse_model({**raw(self.model),'stop_reason':'max_tokens'},[{},{}])

    def test_boolean_not_numeric_scenarios(self):
        with self.assertRaises(ValueError):I.calculate(self.proposal,1,{'a':1,'b':None,'c':False})


class ObservationBindings(unittest.TestCase):
    def test_complete_changed_source_retained_and_bound(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        r={'record_id':'r','patient_id':'p','source_table':'telephone_messages',
           'available_date':'2029-08-15','event_date':'2029-08-14',
           'text':'The proposed option is video. This is a patient report, not approval.'}
        a={'patient_id':'p','available_by':'2029-08-15','changes':[{'record_id':'r','current':{}}],
           'selected_record_ids':['r'],'received_inventory':{'records':{'r':{'record_hash':I.digest(r)}}}}
        with patch.object(I.A.C,'received',return_value=([r],{})):
            cs=I.changed_source_citations(SimpleNamespace(path='unused'),a)
            self.assertEqual(json.loads(cs[0]['quote']),r)
            self.assertTrue(cs[0]['citation_verified'])
            self.assertEqual(cs[0]['source']['record_hash'],I.digest(r))
            for changed in [{**r,'text':'Changed'}, {**r,'patient_id':'other'}, {**r,'available_date':'2029-08-16'}]:
                with patch.object(I.A.C,'received',return_value=([changed],{})),self.assertRaises(ValueError):
                    I.changed_source_citations(SimpleNamespace(path='unused'),a)
            self.assertEqual(I.changed_source_citations(SimpleNamespace(path='unused'),{**a,'selected_record_ids':[]}),[])

    def test_cross_question_task_cutoff_reference_and_unlinked_assessment_rejected(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        proposal={'model':{'facts':[]},'result_id':'proposal'}
        request={'proposal_id':'proposal','proposal_digest':I.digest(proposal),'assessment_id':'old',
                 'decision_id':'decision','task_id':'task','reference_id':'reference','available_by':'2029-08-15'}
        old={'request_id':'acquisition','result_id':'old'}
        now={'request_id':'acquisition','decision_id':'decision','task_id':'task',
             'reference':{'id':'reference'},'available_by':'2029-08-15','previous_assessment_id':'old'}
        for key in ['request_id','decision_id','task_id','reference','available_by','previous_assessment_id']:
            bad={**now,key:({'id':'different'} if key=='reference' else 'different')}
            with self.subTest(key=key),patch.object(I,'saved',side_effect=[request,proposal]), \
                 patch.object(I,'current_assessment',return_value=bad), \
                 patch.object(I.Q,'saved_followup',return_value=old), \
                 patch.object(I,'infer') as inference:
                with self.assertRaises(ValueError):I.observe(SimpleNamespace(path='unused'),{'request_id':'request','assessment_id':'new'},None)
                inference.assert_not_called()


if __name__=='__main__':unittest.main()
