"""Native identity-copy proof preserves global meaning and mixed clinical checks."""
import copy,json,unittest
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import uncompact_json

class ProfileFidelity(unittest.TestCase):
    def fixture(self):
        rows=[{'patient_id':pid,'patient_name':name,'profile_source_date':'2029-01-02','profile_status':'received_profile','profile_source_fingerprint':pid+'-fingerprint'}for pid,name in [('A','Alex Example'),('B','Blair Example')]]
        source={'source_digest':'source','release':'release','as_of':'2030-01-01'}
        profile={'kind':'received_patient_profiles','result_view':'agere_result_1','columns':list(rows[0]),'rows':rows,'source':source,'input_patient_ids':['A','B']}
        result={'result_view':'agere_result_2','columns':list(rows[0]),'rows':copy.deepcopy(rows),'read_views':['agere_result_1'],
                'native_sql_contract':B.NATIVE_SQL_CONTRACT,'executed_query':'SELECT * FROM agere_result_1','source_contracts':source,'truncated':False}
        calls=[{'name':'read_patient_profiles','input':{},'result':profile},{'name':'query_sql','input':{'sql':'SELECT * FROM agere_result_1'},'result':result}]
        answer={'status':'answered','headline':'Received identities','table_from_tool':'agere_result_2'};B.materialize_table(answer,calls)
        return answer,calls
    def packets(self,a,c,docs=()):return B.support_packets('List received names and profile dates',a,c,list(docs),'2030-01-01')
    def check(self,p,decision='supported'):
        seen=[]
        def judge(q,opts,evidence,**kw):
            seen.append(evidence[0]['id']);return {'label':decision,'decided_by':None if decision=='unknown'else'fixture','steps':[]}
        return B.check_support_packets(p,judge,as_of='2030-01-01',ledger=None),seen
    def test_copy_proof_checks_all_patients_after_complete_global_review(self):
        a,c=self.fixture();p=self.packets(a,c);out,seen=self.check(p)
        self.assertEqual(seen,['summary']);self.assertEqual(out['label'],'supported')
        self.assertTrue(all(v['decided_by']=='native_profile_cell_fidelity'for v in out['packets'][1:]))
        self.assertEqual(uncompact_json(json.loads(p[0]['text']))['all_final_rows'],a['table'])
    def test_unknown_or_rejected_global_meaning_cannot_inherit_native_support(self):
        for decision in ['unknown','contradicted']:
            a,c=self.fixture();out,_=self.check(self.packets(a,c),decision)
            self.assertNotEqual(out['label'],'supported')
            self.assertTrue(all(v.get('decided_by')!='native_profile_cell_fidelity'for v in out['packets']))
    def test_changed_packet_and_changed_summary_invalidate_copy_proof(self):
        for index in [0,1]:
            a,c=self.fixture();p=self.packets(a,c);p[index]['text']+=' ';p[index]['bytes']+=1
            _,seen=self.check(p);self.assertIn('patient_A',seen)
    def test_changed_values_clocks_membership_and_clinical_fields_keep_model_review(self):
        for kind in ['name','patient','duplicate','clock','source','clinical','mixed','unbound','incomplete','wrong_producer']:
            with self.subTest(kind=kind):
                a,c=self.fixture()
                if kind=='name':a['table'][0]['patient_name']='Wrong name'
                if kind=='patient':a['table'][0]['patient_id']='C'
                if kind=='duplicate':c[0]['result']['rows'].append(copy.deepcopy(c[0]['result']['rows'][0]))
                if kind=='clock':c[0]['result']['source']={**c[0]['result']['source'],'as_of':'2029-01-01'}
                if kind=='source':c[1]['result']['source_contracts']={**c[1]['result']['source_contracts'],'source_digest':'changed'}
                if kind=='clinical':a['table'][0]['clinical_eligibility']='eligible'
                if kind=='mixed':a['table'][0]['current_pcp']='Dr Example'
                if kind=='unbound':a.pop('table_from_tool')
                if kind=='incomplete':c[0]['result']['truncated']=True
                if kind=='wrong_producer':c[0]['name']='invented_reader'
                self.assertTrue(all(not p.get('native_profile_fidelity')for p in self.packets(a,c)))
    def test_direct_profile_output_and_explicit_unknown_name_can_be_proven(self):
        a,c=self.fixture();c=c[:1];c[0]['result']['rows'][1]['patient_name']=None;c[0]['result']['rows'][1]['profile_status']='profile_after_snapshot'
        a['table_from_tool']='agere_result_1';B.materialize_table(a,c)
        out,seen=self.check(self.packets(a,c));self.assertEqual(seen,['summary']);self.assertEqual(out['label'],'supported')
        self.assertIsNone(a['table'][1]['patient_name'])

if __name__=='__main__':unittest.main()
