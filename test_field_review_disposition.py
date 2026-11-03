"""Observed uncertainty must not manufacture a missing-source diagnosis."""
import copy,json,unittest
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import readable_json


class FieldReviewDisposition(unittest.TestCase):
    def setUp(self):
        self.state={'id':'patient_A','as_of':'2030-01-31',
            'answer_rows':[{'patient_id':'A','documented_role':{'record_id':'authored-note','statement':'A dated author, with current assignment unknown.'}}],
            'source_contexts':[{'record_id':'authored-note','patient_id':'A','date':'2030-01-10','text':'Dr Noor authored this document. No ongoing assignment is recorded.'}]}
        text=json.dumps(readable_json(self.state))
        self.packet={'id':'patient_A','date':'2030-01-31','text':text,'bytes':len(text),
            'claim_manifest':[{'id':'claim_0','field':'documented_role','path':['answer_rows',0,'documented_role']}]}
        self.reading={'label':'supported','probabilities':{'supported':.75,'unknown':.25},'probability':.75,
            'threshold':.8,'provider':'jev','model':'jev-1.13.0','decided_by':None}
        self.steps=[{'provider':'jev','status':'answered','request_hash':'authored-valid-attempt','fields':['claim_0']},
            {'provider':'perplexity','status':'error','error':'Authored unavailable fallback'}]

    def localize(self,reading=None,steps=None):
        value=self.reading if reading is None else reading;attempts=self.steps if steps is None else steps
        self.inputs=[]
        def judge(*args,**kw):
            self.inputs.append((copy.deepcopy(args),copy.deepcopy(kw)))
            return {'judgments':{'claim_0':copy.deepcopy(value)},'steps':copy.deepcopy(attempts)}
        return B.localize_support_failure(copy.deepcopy(self.packet),judge,as_of='2030-01-31',ledger=None)

    def test_below_threshold_support_keeps_its_actual_reading_without_inventing_a_source_gap(self):
        before=copy.deepcopy(self.packet);result=self.localize();feedback=result['feedback'][0]
        self.assertEqual(feedback['label'],'unknown');self.assertIsNone(feedback['decided_by'])
        observation=feedback['verification_observation']
        self.assertEqual(observation['state'],'below_acceptance_threshold')
        self.assertEqual(observation['model_judgment'],self.reading)
        self.assertIn('0.75',feedback['reason']);self.assertIn('0.8',feedback['reason'])
        self.assertNotIn('source does not settle',feedback['reason'])
        self.assertEqual(feedback['source_record_ids'],['authored-note']);self.assertEqual(self.packet,before)
        self.assertEqual(observation['attempts'][1],{'provider':'perplexity','status':'error'})
        self.assertEqual(len(self.inputs),1)

    def test_below_threshold_fault_vote_stays_unknown(self):
        reading={**self.reading,'label':'wrong_role','probabilities':{'wrong_role':.75,'supported':.25}}
        feedback=self.localize(reading)['feedback'][0]
        self.assertEqual(feedback['label'],'unknown');self.assertIsNone(feedback['decided_by'])
        self.assertEqual(feedback['verification_observation']['model_judgment']['label'],'wrong_role')

    def test_explicit_unknown_is_an_abstention_even_with_concentrated_probability(self):
        reading={**self.reading,'label':'unknown','probabilities':{'unknown':.95,'supported':.05},'probability':.95}
        feedback=self.localize(reading)['feedback'][0]
        self.assertEqual(feedback['verification_observation']['state'],'model_abstained')
        self.assertEqual(feedback['label'],'unknown');self.assertIsNone(feedback['decided_by'])

    def test_provider_outage_does_not_become_a_source_finding(self):
        feedback=self.localize({},[{'provider':'jev','status':'error','error':'Authored service failure'}])['feedback'][0]
        observation=feedback['verification_observation'];self.assertEqual(observation['state'],'provider_unavailable')
        self.assertEqual(observation['model_judgment'],{});self.assertEqual(feedback['label'],'unknown')

    def test_prepared_check_remains_unexecuted(self):
        feedback=self.localize({},[{'provider':'jev','status':'prepared','request_hash':'authored-unexecuted'}])['feedback'][0]
        self.assertEqual(feedback['verification_observation']['state'],'not_executed')
        self.assertNotIn('model_judgment',feedback)

    def test_invalid_field_response_has_its_own_disposition(self):
        feedback=self.localize({},[{'provider':'jev','status':'partial','fields':['claim_0'],'invalid_fields':{'claim_0':'Invalid typed value'}}])['feedback'][0]
        self.assertEqual(feedback['verification_observation']['state'],'invalid_provider_response')

    def test_missing_metadata_stays_explicitly_undetermined(self):
        feedback=self.localize({'label':'unknown','decided_by':None},[])['feedback'][0]
        self.assertEqual(feedback['verification_observation']['state'],'undetermined')

    def test_accepted_support_stays_accepted_without_unresolved_feedback(self):
        result=self.localize({**self.reading,'probability':.9,'probabilities':{'supported':.9,'unknown':.1},'decided_by':'jev'})
        self.assertEqual(result['feedback'],[])

    def test_accepted_fault_keeps_its_original_fault_meaning(self):
        feedback=self.localize({**self.reading,'label':'wrong_role','probability':.9,
            'probabilities':{'wrong_role':.9,'supported':.1},'decided_by':'jev'})['feedback'][0]
        self.assertEqual(feedback['label'],'wrong_role');self.assertEqual(feedback['decided_by'],'jev')
        self.assertEqual(feedback['verification_observation']['state'],'decided_fault')
        self.assertIn('conflates authorship',feedback['reason'])

    def test_existing_packet_path_reports_dispositions_without_accepting_unknown_fields(self):
        def decider(*args,**kw):return {'label':'supported','decided_by':None,'probabilities':{'supported':.75,'unknown':.25},'steps':[]}
        def judge(*args,**kw):return {'judgments':{'claim_0':self.reading},'steps':self.steps}
        result=B.check_support_packets([self.packet],decider,as_of='2030-01-31',ledger=None,field_decider=judge)
        self.assertEqual(result['label'],'unknown');self.assertIsNone(result['decided_by'])
        self.assertEqual(result['field_review_states'],{'below_acceptance_threshold':1})
        self.assertEqual(result['failed_packet_ids'],[])
        legacy=copy.deepcopy(result)
        legacy.pop('field_review_states')
        for feedback in legacy['claim_feedback']+legacy['packets'][0]['claim_feedback']:
            feedback.pop('verification_observation',None)
            feedback['reason']='The source does not settle whether this field is supported.'
        coverage={'label':'answers_fully'}
        self.assertIsNotNone(B.verification_repair_state(coverage,result))
        self.assertEqual(B.verification_repair_state(coverage,result),B.verification_repair_state(coverage,legacy))


if __name__=='__main__':unittest.main()
