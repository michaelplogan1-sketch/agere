"""Coverage/support dependency closure from authored evidence; no providers."""
import copy,json,unittest
from clinical_core.agere.clinical import evidence_projection as E,backstop_agent as B
from clinical_core.agere.deliverables.receipts import uncompact_json


def finding(record,quote):
    return {'record_id':record,'date':'2030-01-02','quote':quote,'value':'Candidate source interpretation'}


class Projection(unittest.TestCase):
    def test_exact_string_cells_and_episode_grain_restore_without_mutation(self):
        table=[{'patient_id':'A','episode':'e1','findings':json.dumps([finding('r1','Could not afford it.')],indent=2),'cost':42},
               {'patient_id':'A','episode':'e2','findings':[finding('r2','Later paid fill.')],'cost':0},
               {'patient_id':'B','episode':'e3','findings':[],'cost':None}]
        original=copy.deepcopy(table);view,edges=E.table_view(table)
        leaves={'patient_A':table[:2],'patient_B':table[2:]}
        self.assertEqual(E.restore_table(view,edges,leaves),original);self.assertEqual(table,original)
        self.assertEqual([e['answer_row']for e in edges],[0,1]);self.assertEqual(view[2],table[2])

    def test_financial_and_provider_scalars_are_kept_without_entity_guessing(self):
        table=[{'provider':'C1','year':2030,'paid_dollars':120,'quality_percent':80},
               {'provider':'C2','year':2030,'paid_dollars':0,'quality_percent':None}]
        view,edges=E.table_view(table);self.assertEqual(view,table);self.assertEqual(edges,[])

    def test_arbitrary_bracket_text_and_marker_like_source_data_remain_literal(self):
        table=[{'patient_id':'A','text':'[not JSON]','data':{'clinical_evidence_dependency':'literal source'},'other':[{'record_id':'r','value':'No quote'}]}]
        view,edges=E.table_view(table);self.assertEqual(view,table);self.assertEqual(edges,[])

    def test_changed_source_or_entity_cannot_satisfy_a_dependency(self):
        table=[{'patient_id':'A','finding':[finding('r','Documented refusal.')]}];view,edges=E.table_view(table)
        for change in ('quote','patient'):
            bad=copy.deepcopy(table);changed_edges=copy.deepcopy(edges)
            if change=='quote':bad[0]['finding'][0]['quote']='Invented statement.'
            else:changed_edges[0]['patient_id']='B'
            with self.assertRaises(ValueError):E.restore_table(view,changed_edges,{'patient_A':bad})
        with self.assertRaises(KeyError):E.restore_table(view,edges,{})

    def test_coverage_keeps_uncertainty_and_native_executable_provenance(self):
        answer={'status':'partial','table':[{'patient_id':'A','findings':[finding('r','Dose unknown.')]}],
                'table_binding':{'result_view':'v','rows':1,'query':'SELECT '+('1,'*20000)+'1'},
                'missing':['Current medication use unknown'],'investigation_review':{'status':'repair','steps':['old operational receipt']}}
        old=copy.deepcopy(answer);view=E.coverage_answer(answer)
        self.assertLess(len(json.dumps(view)),5000);self.assertEqual(view['missing'],answer['missing'])
        self.assertEqual(view['table_binding']['result_view'],'v');self.assertIn('native_executable_provenance',view['table_binding'])
        self.assertEqual(view['coverage_contract']['dependency_edges'][0]['packet'],'patient_A');self.assertEqual(answer,old)

    def test_every_patient_dependency_keeps_full_findings_and_global_claim_context(self):
        table=[{'patient_id':f'P{i}','findings':[finding(f'r{i}','A long dated source statement. '*200)]}for i in range(64)]
        answer={'table':table,'facts':{'scope':'Current responsibility is unknown.'}}
        packets=B.support_packets('Care gaps and access',answer,[],[],'2030-01-02')
        native={p['id']:uncompact_json(json.loads(p['text']))for p in packets}
        self.assertEqual(len(packets),65)
        leaves={key:p['answer_rows']for key,p in native.items()if key.startswith('patient_')}
        summary=native['summary'];self.assertEqual(E.restore_table(summary['all_final_rows'],summary['required_clinical_dependencies'],leaves),table)
        for i,row in enumerate(table):
            self.assertEqual(native[f'patient_P{i}']['answer_rows'],[row])
            self.assertEqual(native[f'patient_P{i}']['global_claims_context']['facts'],answer['facts'])

    def test_supported_summary_cannot_override_one_contradicted_clinical_dependency(self):
        packets=B.support_packets('Care gaps',{'table':[{'patient_id':'A','finding':[finding('r','Therapy was stopped.')]}]},[],[],'2030-01-02')
        def decide(question,options,evidence,**kwargs):
            return {'label':'contradicted'if evidence[0]['id']=='patient_A'else'supported','decided_by':'scripted','steps':[]}
        result=B.check_support_packets(packets,decide,as_of='2030-01-02',ledger=None)
        self.assertEqual(result['label'],'contradicted');self.assertEqual(result['failed_packet_ids'],['patient_A'])


if __name__=='__main__':unittest.main()
