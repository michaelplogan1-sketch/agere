"""Exact source closure when final claims cross a provider context boundary."""
import copy,json,unittest
from unittest.mock import Mock
from clinical_core.agere.clinical.evidence_projection import partition_support_packet
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import readable_json,uncompact_json

class ClaimPartitions(unittest.TestCase):
    def packet(self):
        row={'patient_id':'A',**{f'field_{i}':str(i)+chr(65+i)*30000 for i in range(10)}}
        claims=[{'id':f'claim_{i}','field':name,'path':['answer_rows',0,name]} for i,name in enumerate(row) if name!='patient_id']
        state={'id':'patient_A','scope':'All final fields','answer_rows':[row],
               'source_contexts':[{'record_id':'source','text':'Complete received source.','literal':{'$':7}}],
               'evidence':[{'native':'all input rows','values':[True,1,None,{'$agere_shared':['literal']}]}],
               'claim_manifest':claims}
        text=json.dumps(readable_json(state),ensure_ascii=False,separators=(',',':'))
        return {'id':'patient_A','date':'2030-05-27','text':text,'bytes':len(text.encode()),
                'claim_manifest':claims,'source_context_complete':True,'result_view':'final'}

    def test_every_claim_once_and_every_source_unchanged_in_every_part(self):
        packet=self.packet();original=uncompact_json(json.loads(packet['text']))
        parts=partition_support_packet(packet);self.assertGreater(len(parts),1)
        seen=[]
        for part in parts:
            self.assertLessEqual(part['bytes'],230000)
            state=uncompact_json(json.loads(part['text']))
            self.assertEqual(state['source_contexts'],original['source_contexts'])
            self.assertEqual(state['evidence'],original['evidence'])
            self.assertEqual(state['answer_rows'][0]['patient_id'],'A')
            for claim in part['claim_manifest']:
                self.assertEqual(state['answer_rows'][0][claim['field']],original['answer_rows'][0][claim['field']])
                seen.append(claim['id'])
        self.assertEqual(seen,[c['id']for c in packet['claim_manifest']])
        self.assertEqual(len(seen),len(set(seen)))

    def test_a_fault_in_a_later_partition_still_rejects_the_dependency(self):
        def judge(task,options,evidence,**kwargs):
            state=uncompact_json(json.loads(evidence[0]['text']))
            fault=any(c['field']=='field_9'for c in state['claim_manifest'])
            return {'label':'contradicted'if fault else 'supported','decided_by':'fixture','steps':[]}
        out=B.check_support_packets([self.packet()],judge,as_of='2030-05-27',ledger=None)
        self.assertEqual(out['label'],'contradicted');self.assertEqual(out['complete_dependency_packets'],1)
        self.assertGreater(out['scheduled_source_packets'],1);self.assertEqual(len(out['failed_packet_ids']),1)

    def test_uncertain_and_missing_source_checks_cannot_be_promoted(self):
        out=B.check_support_packets([self.packet()],lambda *a,**kw:{'label':'unknown','decided_by':None},as_of='2030-05-27',ledger=None)
        self.assertEqual(out['label'],'unknown');self.assertIsNone(out['decided_by'])
        packet=self.packet();packet['source_context_complete']=False;judge=Mock(side_effect=AssertionError('Missing source cannot be voted through'))
        out=B.check_support_packets([packet],judge,as_of='2030-05-27',ledger=None)
        self.assertEqual(out['label'],'unknown');judge.assert_not_called()

    def test_changed_or_duplicate_manifest_and_an_unfit_single_claim_fail_closed(self):
        packet=self.packet();packet['claim_manifest']=copy.deepcopy(packet['claim_manifest'])[:-1]
        with self.assertRaisesRegex(ValueError,'exact complete'):partition_support_packet(packet)
        packet=self.packet()
        with self.assertRaisesRegex(ValueError,'single-claim'):partition_support_packet(packet,max_bytes=1000)

if __name__=='__main__':unittest.main()
