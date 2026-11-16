import copy,sqlite3,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_patient_context as fixture
from clinical_core.contracts import digest
from clinical_core.agere.clinical import reference_scope_work as s,patient_fit_work as f,patient_context as p

class Scope(unittest.TestCase):
    record=fixture.PatientContext.record
    context=fixture.PatientContext.context
    match=fixture.PatientContext.match
    queue=fixture.PatientContext.queue
    event=fixture.PatientContext.event
    def setUp(self):
        fixture.PatientContext.setUp(self)
        for module,name in [(f.dispositions,'resolve_target'),(f.dispositions,'acquisition_context'),(f.dispositions,'save'),(f.packet_followup,'targets')]:self.addCleanup(setattr,module,name,getattr(module,name))
        f.install();self.records[2]=self.record('c','diagnostic_access_contacts',response='book',order_record_id='o',packet_record_id='pkt')
        self.records += [self.record('o','diagnostic_orders'),self.record('pkt','diagnostic_referral_packets',order_record_id='o')]
        plan=self.match();self.task=p.task_event(self.path,self.event(self.queue(plan)))['task']
        manifest=f.materialize(self.path,{'task_id':self.task['id'],'expected_revision':self.task['revision']})
        self.q=next(q for q in manifest['questions'] if q['kind']=='prior_service_response');self.target=f.target_for(manifest,self.q)
        self.request={'target':self.target,'record_ids':['c']};self.contexts={}
        def create(path,patient,question,ids):
            c={'patient_id':patient,'parameters':{'question':question},'record_scope':{'record_ids':sorted(ids)},'dispatch':{'source_review_allowed':True},'omissions':[]}
            c['id']=digest(c);self.contexts[c['id']]=c;return copy.deepcopy(c)
        for name,fn in [('create',create),('inspect',lambda path,key:copy.deepcopy(self.contexts[key])),('verified',lambda path,key:copy.deepcopy(self.contexts[key]))]:
            patcher=patch.object(s.record_context,name,side_effect=fn);patcher.start();self.addCleanup(patcher.stop)
        self.pre=s.preview(self.path,self.request)
        self.selection_request={'preview_id':self.pre['id'],'record_ids':['c','o','pkt']}
    def listing(self):return s.listing(self.path,{'target':self.target})
    def prepare_answer(self,selection):
        self.output={'id':'d'*64,'status':'complete','patient_id':'p1','context_id':selection['context_id'],'passes':[{}],'findings':[{'citations':[{}]}],'review_steps':[]}
        needs={'id':'e'*64,'summary':{'proposed_questions':0,'questions_with_exact_located_records':0,'unbound_questions':0}}
        for module,name,value,key in [(s.source_synthesis,'inspect',self.output,'d'*64),(s.evidence_needs,'prepare',needs,'d'*64),(s.evidence_needs,'inspect',needs,'e'*64)]:
            original=getattr(module,name)
            def selected(*args,_value=value,_key=key,_original=original,**kwargs):return copy.deepcopy(_value) if args[1]==_key else _original(*args,**kwargs)
            patcher=patch.object(module,name,side_effect=selected);patcher.start();self.addCleanup(patcher.stop)
        return {'selection_id':selection['id'],'answer_id':'d'*64}
    def test_preview_shows_added_records_and_preserves_question(self):
        self.assertEqual(s.preview(self.path,self.request),self.pre);self.assertEqual(self.pre['added_record_ids'],['o','pkt'])
        self.assertEqual(self.pre['historical_typed_scope']['connected_record_ids'],['c'])
        self.assertEqual(self.pre['original_question'],self.q['review_question']);self.assertFalse(self.pre['requirement_resolved'])
    def test_patient_fit_scope_cannot_choose_unrelated_roots(self):
        with self.assertRaisesRegex(ValueError,'exact dated records'):s.preview(self.path,{**self.request,'record_ids':['o']})
    def test_expansion_is_explicit_and_subsets_expose_exclusions(self):
        small=s.select(self.path,{**self.selection_request,'record_ids':['c']});full=s.select(self.path,self.selection_request)
        self.assertEqual(small['unselected_record_ids'],['o','pkt']);self.assertNotEqual(small['context_id'],full['context_id'])
        self.assertEqual(small,s.select(self.path,{**self.selection_request,'record_ids':['c']}));self.assertEqual(len(self.listing()['selections']),2)
    def test_roots_required_and_foreign_preview_records_rejected(self):
        for ids in ([],['o'],['c','foreign'],['c','c']):
            with self.assertRaises(ValueError):s.select(self.path,{**self.selection_request,'record_ids':ids})
        self.assertEqual(self.listing()['selections'],[])
    def test_changed_source_or_target_preserves_history(self):
        s.select(self.path,self.selection_request);self.records[-1]['order_record_id']='changed'
        self.assertFalse(self.listing()['selections'][0]['current'])
        with self.assertRaises(ValueError):s.select(self.path,self.selection_request)
        self.assertEqual(len(self.listing()['previews']),1)
    def test_changed_context_is_stale(self):
        row=s.select(self.path,self.selection_request);self.contexts[row['context_id']]['extra']='changed'
        self.assertFalse(self.listing()['selections'][0]['current'])
    def test_source_race_cannot_commit_selection(self):
        original=s.record_context.create
        def changing(*args):
            c=original(*args);self.records[-1]['order_record_id']='changed';return c
        with patch.object(s.record_context,'create',side_effect=changing):
            with self.assertRaises(ValueError):s.select(self.path,self.selection_request)
        self.assertEqual(self.listing()['selections'],[])
    def test_incomplete_context_is_explicit_and_not_ready(self):
        original=s.record_context.create
        def incomplete(*args):
            c=original(*args);c['dispatch']['source_review_allowed']=False;c['omissions']=['incomplete'];self.contexts[c['id']]=copy.deepcopy(c);return c
        with patch.object(s.record_context,'create',side_effect=incomplete):
            out=s.select(self.path,self.selection_request)
        self.assertEqual(out['status'],'selected_scope_incomplete');self.assertTrue(out['omissions'])
    def test_concurrent_selection_deduplicates(self):
        with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:s.select(self.path,self.selection_request),range(2)))
        self.assertEqual(rows[0],rows[1]);self.assertEqual(len(self.listing()['selections']),1)
    def test_receipts_are_immutable(self):
        selection=s.select(self.path,self.selection_request);s.attach(self.path,self.prepare_answer(selection));db=s.database(self.path)
        try:
            for table in ('previews','selections','answers'):
                with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM '+table)
        finally:db.close()
    def test_answer_returns_to_original_question_without_resolution(self):
        row=s.select(self.path,self.selection_request);req=self.prepare_answer(row);result=s.attach(self.path,req)
        self.assertEqual(result,s.attach(self.path,req));self.assertEqual(result['question'],self.q['review_question'])
        self.assertTrue(self.listing()['answers'][0]['current']);self.assertFalse(result['requirement_resolved']);self.assertFalse(result['human_review_recorded'])
    def test_answer_wrong_patient_scope_and_incomplete_rejected(self):
        row=s.select(self.path,self.selection_request);req=self.prepare_answer(row)
        for key,value in [('context_id','other'),('patient_id','other'),('status','partial')]:
            old=self.output[key];self.output[key]=value
            with self.assertRaises(ValueError):s.attach(self.path,req)
            self.output[key]=old
        self.output['passes']=[{},{}]
        with self.assertRaisesRegex(ValueError,'reconciliation'):s.attach(self.path,req)
    def test_answer_mutation_preserves_history_as_stale(self):
        row=s.select(self.path,self.selection_request);req=self.prepare_answer(row);s.attach(self.path,req);self.output['findings'].append({'citations':[]})
        self.assertFalse(self.listing()['answers'][0]['current'])
    def test_extra_authority_fields_rejected(self):
        row=s.select(self.path,self.selection_request);req=self.prepare_answer(row)
        with self.assertRaises(ValueError):s.attach(self.path,{**req,'requirement_resolved':True})
    def test_answer_changes_during_needs_preparation_cannot_commit(self):
        row=s.select(self.path,self.selection_request);req=self.prepare_answer(row);original=s.evidence_needs.prepare
        def changing(*args):
            result=original(*args);self.output['review_steps'].append('Changed during attachment');return result
        with patch.object(s.evidence_needs,'prepare',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'changed during'):s.attach(self.path,req)
        self.assertEqual(self.listing()['answers'],[])

if __name__=='__main__':unittest.main()
