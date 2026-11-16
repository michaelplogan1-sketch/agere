"""A patient source fault must not suppress independent patient verification."""
import copy,json,threading,time,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B,document_review as D,evidence_projection as E
from clinical_core.agere.deliverables.receipts import readable_json


class LocalSourceScheduling(unittest.TestCase):
    def family(self,pid):
        q={'question':'Why was this MRI deferred?', 'entity':{'kind':'service','id':'mri','aliases':['MRI']},
           'relation':'reason_for_deferral','subject':'patient'}
        docs=[];findings=[]
        for i in range(4):
            quote='MRI deferred while the appointment is verified.'
            doc={'patient_id':pid,'record_id':pid+str(i),'date':'2030-01-01','text':quote+'\n'+'a'*3000}
            docs.append(doc);findings.append(D.relation_finding(pid,'reason',q,doc,
                {'start':0,'end':len(quote),'text':quote},
                {'polarity':'affirmed','temporality':'current','subject':'patient'},'authored_test'))
        state={'id':'patient_'+pid,'patient_id':pid,'question':'Source review','as_of':'2030-05-27',
            'answer_rows':[{'patient_id':pid,'findings':findings}], 'source_contexts':docs,
            'review_scope':[{'questions':{'reason':q}}],
            'claim_manifest':[{'id':'f','field':'findings','path':['answer_rows',0,'findings']}]}
        text=json.dumps(readable_json(state),ensure_ascii=False,separators=(',',':'))
        return E.source_dependency_packets({'id':state['id'],'date':state['as_of'],'text':text,'bytes':len(text.encode()),
            'source_context_complete':True,'claim_manifest':state['claim_manifest']},max_bytes=9000)

    def packets(self):
        return [{'id':'summary','date':'2030-05-27','text':'summary','bytes':7},
                *self.family('A'),*self.family('B'),*self.family('C')]

    def run_packets(self,packets,*,parallel=False,unknown=False):
        seen=[]
        def decide(question,options,evidence,**kwargs):
            ident=evidence[0]['id'];seen.append(ident)
            return {'label':'unknown'if ident=='patient_A__sources_0'else'supported',
                'decided_by':None if ident=='patient_A__sources_0'else'fixture',
                'probabilities':{'unknown':.9},'steps':[{'provider':'fixture','request_hash':ident,'cost_usd':.01}]}
        def fields(task,named,evidence,**kwargs):
            return {'steps':[],'judgments':{k:{'label':'unknown'if unknown else'wrong_time',
                'decided_by':None if unknown else'fixture'}for k in named}}
        ledger=None if parallel else SimpleNamespace(path='not-opened.sqlite3',db=object())
        with patch.dict('os.environ',{'AGERE_SYSTEM_ONE_WORKERS':'1'}):
            out=B.check_support_packets(packets,decide,as_of='2030-05-27',ledger=ledger,field_decider=fields)
        return out,seen

    def test_local_fault_checks_other_families_and_preserves_local_pending_failure(self):
        for parallel in (False,True):
            with self.subTest(parallel=parallel):
                out,seen=self.run_packets(self.packets(),parallel=parallel)
                self.assertEqual(out['blocked_family_ids'],['patient_A']);self.assertFalse(out['global_stop'])
                self.assertIn('patient_B__sources_3',seen);self.assertIn('patient_C__sources_3',seen)
                self.assertNotIn('patient_A__sources_1',seen);self.assertFalse(out['all_dependencies_checked'])
                self.assertEqual(out['label'],'contradicted');self.assertEqual(len(seen),len(set(seen)))
                self.assertEqual(out['failed_packet_ids'],['patient_A__sources_0'])
                self.assertTrue(all(p.get('blocked_family_id')=='patient_A'for p in out['packets']if p['id']in out['unchecked_packet_ids']))

    def test_tampered_binding_or_subject_remains_a_global_barrier(self):
        for change in ('assertion_value','patient','source_patient','subject','parent_patient','missing_id','claim_path'):
            packets=self.packets();child=next(p for p in packets if p['id']=='patient_A__sources_0');a=child['source_claim_assertions']['source_0']
            if change=='assertion_value':a['value']='Tampered text'
            elif change=='patient':a['patient_id']='B'
            elif change=='source_patient':a['source']['patient_id']='B'
            elif change=='subject':a['subject']='source_entity'
            elif change=='parent_patient':packets[1]['source_dependency_patient_id']='B'
            elif change=='missing_id':child['source_claim_assertions']={}
            elif change=='claim_path':child['claim_manifest'][0]['path']=['answer_rows',1,'observation']
            with self.subTest(change=change):
                if change=='claim_path':
                    # Producer path mismatch is refused by the localizer itself.
                    with self.assertRaises((KeyError,IndexError)):self.run_packets(packets)
                else:
                    out,seen=self.run_packets(packets);self.assertTrue(out['global_stop'])
                    self.assertEqual(out['blocked_family_ids'],[]);self.assertNotIn('patient_B__sources_0',seen)

    def test_unknown_feedback_checks_every_family_without_accepting_the_answer(self):
        out,seen=self.run_packets(self.packets(),unknown=True)
        self.assertEqual(out['blocked_family_ids'],[]);self.assertFalse(out['global_stop'])
        self.assertTrue(out['all_dependencies_checked']);self.assertIn('patient_A__sources_3',seen)
        self.assertEqual(out['label'],'unknown')

    def test_opaque_packets_stay_pending_but_native_patient_binding_can_continue(self):
        packets=self.packets();packets.append({'id':'shared','date':'2030-05-27','text':'shared','bytes':6})
        packets.append({'id':'native_patient_D','date':'2030-05-27','text':'D','bytes':1,
            'source_dependency_patient_id':'D','patient_dependency_bound':True,'dependency_complete':True,
            'source_context_complete':True})
        out,seen=self.run_packets(packets)
        self.assertNotIn('shared',seen);self.assertIn('native_patient_D',seen)
        self.assertIn('shared',out['unchecked_packet_ids']);self.assertNotEqual(out['label'],'supported')

    def test_inflight_sibling_finishes_and_retains_its_receipt_after_local_fault(self):
        packets=self.packets();started=threading.Event();diagnosed=threading.Event();seen=[]
        def decide(question,options,evidence,**kwargs):
            ident=evidence[0]['id'];seen.append(ident)
            if ident=='patient_A__sources_0':self.assertTrue(started.wait(2))
            if ident=='patient_A__sources_1':started.set();self.assertTrue(diagnosed.wait(2));time.sleep(.05)
            return {'label':'unknown'if ident=='patient_A__sources_0'else'supported',
                'decided_by':None if ident=='patient_A__sources_0'else'fixture','probabilities':{'unknown':.9},
                'steps':[{'provider':'fixture','request_hash':ident,'cost_usd':.01}]}
        def fields(task,named,evidence,**kwargs):
            diagnosed.set();return {'steps':[],'judgments':{k:{'label':'wrong_time','decided_by':'fixture'}for k in named}}
        with patch.dict('os.environ',{'AGERE_SYSTEM_ONE_WORKERS':'2'}):
            out=B.check_support_packets(packets,decide,as_of='2030-05-27',ledger=None,field_decider=fields)
        self.assertIn('patient_A__sources_1',{s['request_hash']for s in out['steps']})
        self.assertIn('patient_C__sources_3',seen);self.assertFalse(out['global_stop'])
        self.assertEqual(len(seen),len(set(seen)));self.assertNotEqual(out['label'],'supported')


if __name__=='__main__':unittest.main()
