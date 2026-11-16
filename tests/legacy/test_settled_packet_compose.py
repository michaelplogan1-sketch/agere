"""Candidate AGERE_SETTLED_PACKET_COMPOSE: a complete patient row with no attached documents is still
eligible for field-by-field settlement and composition instead of a whole-packet frontier review."""
import json,os,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import readable_json


def packet(*,contexts=None,gaps=None,bound=True):
    row={'patient_id':'A','referral_json':'structured','attendance':'visit 2029-09-26'}
    claims=[{'id':'claim_'+str(i),'field':k,'path':['answer_rows',0,k]}for i,k in enumerate(k for k in row if k!='patient_id')]
    state={'patient_id':'A','as_of':'2030-05-27','answer_rows':[row],'claim_manifest':claims}
    if contexts is not None:state['source_contexts']=contexts
    if gaps:state['source_context_gaps']=gaps
    text=json.dumps(readable_json(state),ensure_ascii=False,separators=(',',':'))
    return {'id':'patient_A','date':'2030-05-27','text':text,'bytes':len(text),'claim_manifest':claims,
        'patient_dependency_bound':bound,'dependency_complete':True,'source_context_complete':True,'source_dependency_patient_id':'A'}


class SettledPacketCompose(unittest.TestCase):
    def test_document_free_row_is_eligible_only_with_the_flag(self):
        with patch.dict(os.environ,{'AGERE_SETTLED_PACKET_COMPOSE':'off'}):self.assertIsNone(B.complete_claim_scope(packet()))
        with patch.dict(os.environ,{'AGERE_SETTLED_PACKET_COMPOSE':'on'}):
            scope=B.complete_claim_scope(packet())
            self.assertIsNotNone(scope);self.assertEqual(scope[2],[]);self.assertEqual(len(scope[1]),2)

    def test_other_requirements_are_unchanged(self):
        with patch.dict(os.environ,{'AGERE_SETTLED_PACKET_COMPOSE':'on'}):
            self.assertIsNone(B.complete_claim_scope(packet(bound=False)))
            self.assertIsNone(B.complete_claim_scope(packet(gaps=[{'record_id':'R1'}])))
            self.assertIsNone(B.complete_claim_scope(packet(contexts=[{'patient_id':'B','record_id':'R1','text':'x','date':'2029-01-01'}])))
            self.assertIsNone(B.complete_claim_scope({**packet(),'parent_packet_id':'p'}))


if __name__=='__main__':unittest.main()
