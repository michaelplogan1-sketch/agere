"""Source changes affect declared review inputs, preserving the original answer."""
import copy,json,sqlite3,unittest
import test_shared_planner as fixtures
from clinical_core.agere.deliverables import answer_context as A

class ReviewDependencies(unittest.TestCase):
    def setUp(self):
        fixtures.SharedPlanner.setUp(self)
        with sqlite3.connect(self.path/'semantic.sqlite3')as db:
            db.execute('CREATE TABLE record(record_id TEXT,patient_id TEXT,stream TEXT,event_date TEXT,available_date TEXT,payload TEXT)')
            for pid in ['A','B']:
                value={'record_id':pid+'1','patient_id':pid,'status':'received','synthetic':True}
                db.execute('INSERT INTO record VALUES (?,?,?,?,?,?)',(pid+'1',pid,'requests','2029-06-01','2029-06-02',json.dumps(value)))
        self.context=A.capture(self.path,'Inspect received request dependencies.',{'answer':{'status':'partial','answer_type':'table',
            'table':[{'patient_id':p,'record_id':p+'1','status':'received'}for p in ['A','B']],
            'missing':['Payer authorization is unresolved.']}})
        self.saved_answer=A.load(self.path,self.context['id'])['answer']
        self.request={'context_id':self.context['id'],'program':'evidence_review','patient_ids':['A','B'],
                      'owner':'Synthetic source review','note':'Internal evidence only.',
                      'review_dependencies':[{'patient_id':p,'key':key,'question':question,'streams':[stream],
                         'start':'2029-01-01','end':'2029-12-31'}for p in ['A','B']for key,question,stream in [
                          ('received_request','Reconcile the received request version.','requests'),
                          ('payer_authorization','Obtain the missing payer authorization source.','authorizations')]]}

    def change(self,patient='A',stream='requests',available='2029-06-04'):
        with sqlite3.connect(self.path/'semantic.sqlite3')as db:
            row={'record_id':patient+'2','patient_id':patient,'status':'revised','supersedes_record_id':patient+'1','synthetic':True}
            db.execute('INSERT INTO record VALUES (?,?,?,?,?,?)',(patient+'2',patient,stream,'2029-06-03',available,json.dumps(row)))
            db.execute("UPDATE meta SET value='authored-overlay-v2'WHERE key='source_digest'")

    def test_only_dependent_work_changes_and_previous_version_remains(self):
        first=A.create_work(self.path,self.request)
        again=A.create_work(self.path,self.request)
        self.assertEqual(first['items'],again['items'])
        before={x['patient_id']:x for x in first['items']}
        self.assertEqual(sum(len(x['review_dependency_status'])for x in before.values()),4)
        self.change()
        after=A.inspect(self.path,self.context['id']);items={x['patient_id']:x for x in after['items']}
        changed=[d for x in items.values()for d in x['review_dependency_status']if not d['source_current']]
        self.assertEqual([d['key']for d in changed],['received_request'])
        self.assertEqual(items['A']['id'],before['A']['id']);self.assertEqual(items['B']['id'],before['B']['id'])
        self.assertEqual(items['B']['revision'],before['B']['revision']);self.assertFalse(items['B']['source_review_required'])
        self.assertTrue(items['A']['source_review_required']);self.assertTrue(after['context']['stale'])
        self.assertEqual(after['context']['answer'],self.saved_answer)
        with sqlite3.connect(self.path/'semantic.sqlite3')as db:
            self.assertEqual(db.execute("SELECT COUNT(*)FROM record WHERE record_id IN('A1','A2')").fetchone()[0],2)
        def update(item,state):
            return A.update_work(self.path,{'context_id':self.context['id'],'item_id':item['id'],'revision':item['revision'],
                                          'state':state,'owner':item['owner'],'note':'Authored internal workflow transition.'})
        active=update(items['B'],'in_review');b=next(x for x in active['items']if x['patient_id']=='B')
        self.assertEqual(b['revision'],2)
        active=update(next(x for x in active['items']if x['patient_id']=='A'),'in_review')
        a=next(x for x in active['items']if x['patient_id']=='A')
        with self.assertRaisesRegex(ValueError,'dependent source changed'):update(a,'review_complete')

    def test_future_arrival_does_not_invalidate_pinned_source_scope(self):
        A.create_work(self.path,self.request);self.change(available='2031-01-01')
        after=A.inspect(self.path,self.context['id'])
        self.assertTrue(all(d['source_current']for x in after['items']for d in x['review_dependency_status']))

    def test_saved_declarations_use_the_existing_work_action_for_selected_patients(self):
        answer={**self.saved_answer,'review_dependencies':self.request['review_dependencies']}
        context=A.capture(self.path,'Inspect source review dependencies.',{'answer':answer})
        request={k:v for k,v in self.request.items()if k!='review_dependencies'}
        request.update(context_id=context['id'],patient_ids=['B'])
        result=A.create_work(self.path,request)
        self.assertEqual([x['patient_id']for x in result['items']],['B'])
        self.assertEqual(len(result['items'][0]['review_dependency_status']),2)

    def test_new_record_for_missing_source_invalidates_only_its_request(self):
        A.create_work(self.path,self.request);self.change(stream='authorizations')
        after=A.inspect(self.path,self.context['id'])
        changed=[d for x in after['items']for d in x['review_dependency_status']if not d['source_current']]
        self.assertEqual([d['key']for d in changed],['payer_authorization'])

    def test_unknown_or_partial_source_clocks_are_not_silently_excluded(self):
        A.create_work(self.path,self.request);self.change()
        with sqlite3.connect(self.path/'semantic.sqlite3')as db:
            db.execute("UPDATE record SET event_date='2029-06',available_date=NULL WHERE record_id='A2'")
        after=A.inspect(self.path,self.context['id'])
        a=next(x for x in after['items']if x['patient_id']=='A')
        self.assertTrue(any(not d['source_current']for d in a['review_dependency_status']))
        selection=a['evidence_review']['dependencies'][0]['selection']
        current=A.review_source_receipt(self.path,selection)
        self.assertEqual(current['unknown_event_dates'],1);self.assertEqual(current['unknown_reported_availability_dates'],1)

    def test_dependency_scope_changes_do_not_reset_work(self):
        A.create_work(self.path,self.request);different=copy.deepcopy(self.request)
        different['review_dependencies'][0]['question']='A different source question.'
        with self.assertRaisesRegex(ValueError,'selection changed'):A.create_work(self.path,different)

    def test_named_finding_fields_are_selectively_invalidated_and_can_refresh(self):
        request=copy.deepcopy(self.request)
        for dep in request['review_dependencies']:
            if dep['key']=='received_request':dep['fields']=['status']
        first=A.create_work(self.path,request);self.change()
        changed=A.inspect(self.path,self.context['id'])
        self.assertFalse(changed['finding_freshness']['A']['status']['source_current'])
        self.assertTrue(changed['finding_freshness']['B']['status']['source_current'])
        item=next(x for x in changed['items']if x['patient_id']=='A')
        old_aid=item['assessment_id'];deps=[d['id']for d in item['review_dependency_status']if not d['source_current']]
        refresh={'context_id':self.context['id'],'item_id':item['id'],'revision':item['revision'],
                 'dependency_ids':deps,'owner':item['owner'],'note':'Authored fixture source refresh, not clinical review.'}
        after=A.refresh_review_sources(self.path,refresh)
        a=next(x for x in after['items']if x['patient_id']=='A')
        self.assertEqual(a['id'],item['id']);self.assertEqual(a['state'],'in_review')
        self.assertNotEqual(a['assessment_id'],old_aid)
        self.assertEqual(after['context']['answer'],self.saved_answer);self.assertTrue(after['context']['stale'])
        # Refreshing work inputs must never restamp the old finding as current.
        self.assertFalse(after['finding_freshness']['A']['status']['source_current'])
        self.assertTrue(all(d['source_current']for d in a['review_dependency_status']))
        with self.assertRaisesRegex(ValueError,'reload'):A.refresh_review_sources(self.path,refresh)
        refresh['revision']=a['revision']
        self.assertEqual(A.refresh_review_sources(self.path,refresh)['items'],after['items'])
        with A._db(self.path,after['context'])as db:
            self.assertIsNotNone(db.execute('SELECT payload FROM work_assessments WHERE id=?',(old_aid,)).fetchone())
        db.close()

    def test_missing_output_field_cannot_be_given_a_source_dependency(self):
        request=copy.deepcopy(self.request);request['review_dependencies'][0]['fields']=['invented_field']
        with self.assertRaisesRegex(ValueError,'absent output field'):A.create_work(self.path,request)

    def test_cross_patient_duplicates_and_clinical_program_inputs_rejected(self):
        for edit in ['outside','duplicate','program','stream_type']:
            request=copy.deepcopy(self.request)
            if edit=='outside':request['review_dependencies'][0]['patient_id']='OUTSIDE'
            elif edit=='duplicate':request['review_dependencies'].append(request['review_dependencies'][0])
            elif edit=='program':request['program']='custom'
            else:request['review_dependencies'][0]['streams']='requests'
            with self.subTest(edit=edit),self.assertRaises(ValueError):A.create_work(self.path,request)

    def test_legacy_work_keeps_global_stale_boundary(self):
        request={k:v for k,v in self.request.items()if k!='review_dependencies'}
        first=A.create_work(self.path,request);item=first['items'][0];self.change()
        with self.assertRaisesRegex(ValueError,'data changed'):
            A.update_work(self.path,{'context_id':self.context['id'],'item_id':item['id'],'revision':1,
                                   'state':'in_review','owner':item['owner'],'note':'Legacy unchanged boundary.'})

if __name__=='__main__':unittest.main()
