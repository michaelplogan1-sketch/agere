"""Program evidence never upgrades financial reports into clinical eligibility."""
import copy,json,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import program_accountability as A,financial_accounts as F,program_work as W
from clinical_core.agere.clinical.graph_workbench import accountability_examples
from clinical_core.agere.clinical.economical_planner import local_plan
from clinical_core.agere.clinical.analysis_graph import validate,execute
import test_financial_accounts as fixture
import test_analysis_graph as G


class Accountability(unittest.TestCase):
    def setUp(self):
        f=fixture.Persistence();f.setUp();self.addCleanup(f.tearDown);self.root=f.root
        self.p={'contract':'program_payment_evidence','available_by':'2026-09-30'}
        meta=json.loads((self.root/'run.json').read_text());meta['source_run']='fixture';(self.root/'run.json').write_text(json.dumps(meta))

    def test_full_population_queue_deduplicates_global_gaps_and_caches(self):
        r=A.execute(self.root,['q','p','p'],self.p);self.assertEqual(r['patients_evaluated'],2)
        self.assertEqual(r['request_counts'],{'program_input_review':4,'contract_input_review':3})
        self.assertEqual(A.execute(self.root,['p','q'],self.p)['processing_counts']['patient_cache_hits'],2)
        page=A.inspect(self.root,r['id'],limit=2);self.assertEqual(len(page['items']),2);self.assertEqual(page['next_offset'],2)
        row=A.inspect(self.root,r['id'],patient='p');self.assertEqual(row['financial_status_counts'],{'reconciled_received_records':1})
        self.assertEqual(row['payment_readiness'],'unresolved');self.assertFalse(row['payment_entitlement_established'])
        with self.assertRaisesRegex(ValueError,'outside'):A.inspect(self.root,r['id'],patient='other')
        self.assertEqual(A.execute(self.root,[],self.p)['requests'],0)

    def test_financial_difference_yields_exact_request_and_stale_guard(self):
        db=sqlite3.connect(self.root/'stack.sqlite3');db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(fixture.snapshot(balance_cents=5)),'s'));db.commit();db.close()
        r=A.execute(self.root,['p','q'],self.p);row=A.inspect(self.root,r['id'],patient='p')
        task=next(x for x in row['requests'] if x['kind']=='financial_account_review')
        self.assertEqual(task['account_id'],'a');self.assertIn('s',task['source_record_ids']);self.assertTrue(task['equation_differences'])
        old=A.prepare(self.root,self.p);ctx=A.contexts(self.root,r['id'],['p'])
        db=sqlite3.connect(self.root/'stack.sqlite3');db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(fixture.snapshot(balance_cents=8)),'s'));db.commit();db.close()
        new=A.prepare(self.root,self.p);self.assertNotEqual(A.receipt_id(old,'p'),A.receipt_id(new,'p'));self.assertEqual(A.receipt_id(old,'q'),A.receipt_id(new,'q'))
        with self.assertRaisesRegex(ValueError,'changed'):A.verify_contexts(self.root,list(ctx.values()))

    def test_coverage_is_context_not_attribution_and_late_rows_stay_out(self):
        f=F.project('p',[],'2026-09-30');f['id']='f'
        records=[fixture.source('c','coverage',coverage_type='medicare'),fixture.source('late','coverage',available_date='2026-10-01')]
        row=A.project('p',records,f,A.definition(self.p['contract']),self.p['available_by'])
        self.assertEqual(len(row['source_context']),1);self.assertTrue(all(r['status']=='unbound' for r in row['requirements']))
        self.assertEqual(row['excluded_context_sources'][0]['source']['record_id'],'late')
        records[0]['patient_id']='q'
        with self.assertRaisesRegex(ValueError,'same-patient'):A.project('p',records,f,A.definition(self.p['contract']),self.p['available_by'])

    def test_explicit_binding_records_inputs_without_semantic_approval(self):
        spec=copy.deepcopy(A.definition(self.p['contract']));spec['requirements'][-1]['binding']={'stream':'test_terms','fields':['contract_id','period']}
        f=F.project('p',[],'2026-09-30');f['id']='f'
        row=A.project('p',[fixture.source('r','test_terms',contract_id='c',period='2026')],f,spec,'2026-09-30')
        req=row['requirements'][-1];self.assertEqual(req['status'],'received_input_requires_review');self.assertFalse(req['semantic_validity_established'])
        self.assertEqual(req['sources'][0]['fields'],{'contract_id':'c','period':'2026'})

    def test_program_assessments_retain_context_without_creating_candidates(self):
        r=A.execute(self.root,['p','q'],self.p);ctx=A.contexts(self.root,r['id'],['p','q'])
        proposal=W.prepare(self.root,'hf_review',['p','q'],accountability_contexts=ctx)
        self.assertEqual(proposal['counts']['candidates'],0);self.assertEqual(proposal['accountability_assessments'],2)
        db=W.database(self.root)
        try:
            saved=W.load(db,'work_proposals',proposal['proposal_id'])
            for key in saved['assessment_ids']:
                a=W.load(db,'work_assessments',key);self.assertEqual(a['accountability_context'],ctx[a['patient_id']])
        finally:db.close()
        with self.assertRaisesRegex(ValueError,'exactly'):W.prepare(self.root,'hf_review',['p','q'],accountability_contexts={'p':ctx['p']})
        bad=copy.deepcopy(ctx);bad['p']['receipt_id']=bad['q']['receipt_id']
        with self.assertRaisesRegex(ValueError,'mismatch'):W.prepare(self.root,'hf_review',['p','q'],accountability_contexts=bad)
        db=sqlite3.connect(self.root/'stack.sqlite3');db.execute('DELETE FROM records WHERE id=?',('s',));db.commit();db.close()
        with self.assertRaisesRegex(ValueError,'changed'):W.materialize(self.root,proposal['proposal_id'],'engineering verification')

    def test_unchanged_patient_context_survives_other_patient_source_change(self):
        r=A.execute(self.root,['p','q'],self.p);ctx=A.contexts(self.root,r['id'],['q'])
        db=sqlite3.connect(self.root/'stack.sqlite3');db.execute('DELETE FROM records WHERE id=?',('s',));db.commit();db.close()
        A.verify_contexts(self.root,list(ctx.values()))
        self.assertEqual(A.inspect(self.root,r['id'],patient='q')['patient_id'],'q')
        with self.assertRaisesRegex(ValueError,'changed'):A.load(self.root,r['id'])

    def test_definition_change_requires_new_version(self):
        A.prepare(self.root,self.p);spec=copy.deepcopy(A.definition(self.p['contract']));spec['title']='Changed meaning';spec['hash']=A.digest(spec)
        with patch.object(A,'definition',return_value=spec):
            with self.assertRaisesRegex(ValueError,'version is immutable'):A.prepare(self.root,self.p)
            spec['version']='2';spec['hash']=A.digest(spec);A.prepare(self.root,self.p)

    def test_saved_work_event_blocks_changed_financial_evidence(self):
        db=sqlite3.connect(self.root/'stack.sqlite3')
        clinical={'synthetic':True,'patient_id':'p','source_run':'fixture','workflow':{'as_of':'2026-09-30','review_candidate':True,'status':'review_required','missing_information':[]},'source_bound_facts':[]}
        db.execute('INSERT INTO analyses VALUES (?,?,?)',('p','heart_failure_review',json.dumps(clinical)));db.commit();db.close()
        r=A.execute(self.root,['p'],self.p);proposal=W.prepare(self.root,'hf_review',['p'],accountability_contexts=A.contexts(self.root,r['id'],['p']))
        W.materialize(self.root,proposal['proposal_id'],'fixture operator');item=W.list_items(self.root)['items'][0]
        request={'item_id':item['id'],'expected_revision':item['revision'],'actor':'fixture operator','request_id':'assign','action':'assign','data':{'owner':'fixture reviewer','due_date':None}}
        assigned=W.change(self.root,request)['item'];self.assertEqual(assigned['owner'],'fixture reviewer')
        db=sqlite3.connect(self.root/'stack.sqlite3');db.execute('DELETE FROM records WHERE id=?',('s',));db.commit();db.close()
        request.update(expected_revision=assigned['revision'],request_id='stale')
        with self.assertRaisesRegex(W.WorkConflict,'changed'):W.change(self.root,request)

    def test_saved_receipts_are_immutable(self):
        r=A.execute(self.root,['p'],self.p);db=A.storage(self.root)
        try:
            for table in ('receipts','proposals'):
                with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM '+table)
                db.rollback()
        finally:db.close()


class Graph(unittest.TestCase):
    setUp=G.GraphExecution.setUp
    tearDown=G.GraphExecution.tearDown
    @patch('clinical_core.agere.clinical.graph_reports.attach', new=lambda path,op,patients,parameters,prep,result,selection: result)
    @patch('clinical_core.agere.clinical.graph_reports.dependencies', new=lambda path,graph: {})
    def test_composition_and_typed_context_no_population_coercion(self):
        for row in accountability_examples():
            p,route=local_plan(row['question'],'2026-09-30');self.assertEqual(route,'registered_example');validate(p,'2026-09-30')
        g=accountability_examples()[0]['graph']
        with patch.object(A,'prepare',return_value={'identity':'a'}),patch.object(A,'execute',return_value={'patients_evaluated':6}) as run:
            result=execute(self.stack,g);self.assertEqual(len(run.call_args.args[1]),6);self.assertEqual(result['results'][-1]['result']['patients_evaluated'],6)
        g['nodes'].append(G.node('bad','worklist',['accountability'],{'program':'hf_review'}))
        with self.assertRaisesRegex(ValueError,'membership'):validate(g,'2026-09-30')


if __name__=='__main__':unittest.main()
