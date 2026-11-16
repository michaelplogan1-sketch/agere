"""Existing documentary criteria reach ordinary investigation without model calls.

Authored source/definition fixtures qualify transport and uncertainty, not clinical
interpretation or program eligibility.
"""
import copy,json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch

from clinical_core.agere.clinical import analysis_tools as A,program_documents as D,care_capabilities as C
from clinical_core.agere.clinical import warehouse_interpretation as W
from clinical_core.agere.clinical import shared_planner,request_plan
from clinical_core.agere.clinical.analytic_registry import code_identity
from test_aggregate_explain import AS_OF
from test_warehouse_interpretation import record
from test_program_documents import package
import test_documentary_tool_handoff as fixture


class CriteriaTools(unittest.TestCase):
    tools=fixture.DocumentaryHandoff.tools

    def setUp(self):
        fixture.DocumentaryHandoff.setUp(self)
        self.meta['source_run']='fixture';(self.path/'run.json').write_text(json.dumps(self.meta))
        (self.path/'operators.json').write_text(json.dumps({'operators':['warehouse_interpretation'],'code_identity':code_identity()}))
        with sqlite3.connect(self.path/'stack.sqlite3')as db:
            db.execute('CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT,PRIMARY KEY(patient,operator))')
            for i,pid in enumerate(self.ids):
                rows=[] if i==2 else [record('screen-'+pid,'financial_screens',patient_id=pid,
                    difficulty_paying_medical_bills=i!=1)]
                for r in rows:db.execute('INSERT INTO records VALUES (?,?,?,?)',(r['record_id'],pid,r['source_table'],json.dumps(r)))
                raw=[json.loads(r[0])for r in db.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,))]
                native=W.interpret({'patient_id':pid},raw,AS_OF)
                db.execute('INSERT INTO analyses VALUES (?,?,?)',(pid,'warehouse_interpretation',json.dumps(native)))
        self.key=D.install(self.path,package())['id']

    def execute(self,t,op='program_document',**extra):
        params={'package':self.key,'filter':'all'} if op=='program_document' else (
            {'protocol':'affordability_action_review@2','filter':'all'} if op=='protocol_review' else
            {'contract':'native_affordability_report','filter':'all'})
        return t({'name':'execute_analysis','input':{'operation':op,'parameters':params,**extra}})

    def test_discover_existing_rules_with_dates_sources_and_unselected_buyer(self):
        t=self.tools();menu=t({'name':'list_analyses','input':{}})
        self.assertTrue(A.CRITERIA<={r['operation']for r in menu['analyses']})
        for op in A.CRITERIA:
            described=t({'name':'describe_analysis','input':{'operation':op}})
            self.assertIn('parameters',described);context=described['criteria_context']
            p=next(p for p in context['quoted_packages']if p['id']==self.key)
            self.assertEqual(p['provenance']['source_uri'],'synthetic://agere/documentary-review')
            self.assertEqual(p['effective_end'],'2030-12-31')
            self.assertEqual(p['review']['status'],'unreviewed')
            self.assertEqual(described['buyer_context']['status'],'no_buyer_portfolio_supplied')
            self.assertFalse(described['buyer_context']['service_match_established'])
        desc=t({'name':'describe_analysis','input':{'operation':'program_document'}})
        self.assertIn(self.key,desc['parameters']['properties']['package']['enum'])
        view=shared_planner.schema_slice(request_plan.Catalog.load(self.path),'Review program evidence',operations=sorted(A.CRITERIA))
        for op in A.CRITERIA:
            native=t({'name':'describe_analysis','input':{'operation':op}})
            selected=view['selected_operator_contracts'][op]
            self.assertEqual(selected['parameter_schema'],native['parameters'])
            self.assertEqual(selected['criteria_context'],native['criteria_context'])

    def test_full_population_receipts_keep_supported_contradicted_and_unknown_in_native_sql(self):
        t=self.tools()
        for op in sorted(A.CRITERIA):
            out=self.execute(t,op);self.assertEqual([r['patient_id']for r in out['rows']],self.ids)
            report=out['engine_result']['results'][-1]['result']
            self.assertEqual(len(report['patients']),6);self.assertEqual(len(report['review_members']),12)
            self.assertEqual(out['new_paid_api_calls'],0)
            rows={r['patient_id']:r['documentary_receipt']for r in out['rows']}
            self.assertFalse(rows['p00']['clinical_eligibility_established'])
            self.assertNotEqual(rows['p00']['status'],rows['p01']['status'])
            self.assertNotEqual(rows['p00']['status'],rows['p02']['status'])
            sql='SELECT patient_id,status FROM '+out['result_view']+' ORDER BY patient_id'
            self.assertEqual([r['patient_id']for r in t({'name':'query_sql','input':{'sql':sql}})['rows']],self.ids)
        p=rows['p00'];self.assertIn('screen-p00',json.dumps(p))

    def test_expired_unreceived_and_unbound_rules_cannot_become_eligibility(self):
        for mode in ('expired','unreceived','unbound'):
            p=package(mode,extra=mode=='unbound')
            if mode=='expired':p['spec']['effective_end']='2028-12-31'
            if mode=='unreceived':p['provenance']['received_on']='2030-01-01'
            self.key=D.install(self.path,p)['id'];row=self.execute(self.tools(['p00']))['rows'][0]['documentary_receipt']
            self.assertEqual(row['evaluation']['status'],{'expired':'not_effective','unreceived':'document_not_received_by_cutoff','unbound':'unknown'}[mode])
            self.assertFalse(row['clinical_eligibility_established'])
            discovery=D.discovery(self.path,AS_OF)
            item=next(v for v in discovery['quoted_packages']if v['id']==self.key)
            self.assertEqual(item['temporal_status'],{'expired':'not_effective','unreceived':'document_not_received_by_cutoff','unbound':'within_declared_period'}[mode])

    def test_no_silent_version_selection_or_scope_widening(self):
        D.install(self.path,package('2',extra=True))
        self.assertTrue(all(p['overlapping_versions_require_selection']for p in D.discovery(self.path,AS_OF)['quoted_packages']))
        t=self.tools(['p00']);self.assertEqual(self.execute(t,patient_ids=[])['rows'],[])
        with self.assertRaisesRegex(ValueError,'outside this request'):self.execute(t,patient_ids=['p01'])
        self.assertEqual(len(self.execute(t)['rows']),1)

    def test_changed_or_missing_receipt_is_rejected(self):
        out=self.execute(self.tools());report=out['engine_result']['results'][-1]['result']
        changed=copy.deepcopy(report);changed['review_members'][0]['status']='clinically_eligible'
        with self.assertRaisesRegex(ValueError,'Criterion status differs'):A.documentary_rows(self.path,'program_document',changed)
        with D.storage(self.path)as db:
            db.execute('DROP TRIGGER immutable_receipts_DELETE')
            db.execute('DELETE FROM receipts WHERE id=?',(report['review_members'][0]['receipt_id'],))
        with self.assertRaisesRegex(ValueError,'Missing documentary'):A.documentary_rows(self.path,'program_document',report)

    def test_changed_program_review_invalidates_request_continuation(self):
        t=self.tools();before=t.continuation_identity()
        D.review(self.path,{'package':self.key,'expected_revision':0,'actor':'AUTHORED TEST',
            'status':'changes_requested','note':'Authored correction fixture, not a clinical review.'})
        self.assertNotEqual(before,t.continuation_identity())

    def test_discovery_does_not_create_stores_or_infer_service_absence(self):
        import tempfile
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp)
            self.assertFalse(D.discovery(path,AS_OF)['quoted_packages'])
            self.assertEqual(C.declared_context(path,AS_OF)['status'],'no_buyer_portfolio_supplied')
            self.assertEqual(list(path.iterdir()),[])


if __name__=='__main__':unittest.main()
