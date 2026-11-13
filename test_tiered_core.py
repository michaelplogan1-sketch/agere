"""Tiered core, no model calls: recipe matching, validation, bottle matching and promotion status, eval scoring,
and the document-reading tier with its readers replaced by fixed judgments."""
import os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest import mock
from clinical_core.agere.tiered import bottles as BS,evaluate as EV,read as RD,recipes as R,router,store


class Core(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir=tempfile.TemporaryDirectory();path=Path(cls.dir.name)/'semantic.sqlite3'
        db=sqlite3.connect(path)
        db.executescript('''CREATE TABLE meta(key,value);INSERT INTO meta VALUES('version','t/1'),('source_digest','abc');
            CREATE TABLE utilization_event(patient_id,kind,event_date);
            INSERT INTO utilization_event VALUES('p1','ed_visit','2029-01-03'),('p1','ed_visit','2029-02-03'),('p2','ed_visit','2028-02-03'),('p2','inpatient','2029-02-03');
            CREATE TABLE concept_patient(concept,kind,patient_id);INSERT INTO concept_patient VALUES('heart_failure','condition','p1'),('diabetes','condition','p2');
            INSERT INTO meta VALUES('as_of','2030-05-27');
            CREATE TABLE clinical_note(record_id,patient_id,note_type,event_date,organization_id,text_chars);
            CREATE TABLE hie_document(record_id,patient_id,document_type,event_date,author_organization_id,text_chars);
            CREATE TABLE record(record_id,payload);
            CREATE TABLE scanned_document(record_id,patient_id,document_type,event_date,text_chars,readable);
            CREATE TABLE scanned_document_text(record_id,text);
            INSERT INTO scanned_document VALUES('d1','P-1','Radiology Report','2029-06-02',40,1),('d2','P-1','Radiology Report','2029-06-09',40,1),
                ('d3','P-1','Radiology Report','2029-06-20',40,1),('d4','P-1','Radiology Report','2029-06-21',40,0),('d5','P-1','Radiology Report','2029-07-01',40,1);
            INSERT INTO scanned_document_text VALUES('d1','IMPRESSION: Possible early right lower lobe infiltrate.'),('d2','IMPRESSION: No acute findings.'),
                ('d3','IMPRESSION: Possible early rigbt l0wer lobe infltrate.'),('d5','IMPRESSION: Possible infiltrate.');''')
        db.commit();db.close()
        cls.env=mock.patch.dict(os.environ,{'AGERE_TIERED_DB':str(path)});cls.env.start();R._vocab.clear()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop();cls.dir.cleanup();R._vocab.clear()

    def test_tier1_answers_a_matching_question_with_no_model_and_validates_it(self):
        out=router.answer('How many ED visits were there in 2029?',tiers=('recipes',))
        self.assertEqual((out['status'],out['final_tier'],out['rows']),('answered','recipes',[[2]]))
        self.assertTrue(out['validation']['ok']);self.assertEqual(sum(s.get('tokens',0)for s in out['steps']),0)
        monthly=router.answer('Show monthly ED visits in 2029',tiers=('recipes',))
        self.assertEqual(monthly['rows'],[['2029-01',1],['2029-02',1]])
        self.assertIn('total_reconciles',[c['name']for c in monthly['validation']['checks']])

    def test_unmatched_wording_and_untyped_values_do_not_guess(self):
        self.assertIsNone(R.match('Count of emergency visits during 2029'))
        self.assertIsNone(R.match('How many patients have unicorn syndrome?'))
        self.assertIsNone(R.normalize('utilization_count',{'kind':'ed_visit','year':'29'}))
        out=router.answer('Count of emergency visits during 2029',tiers=('recipes',))
        self.assertEqual((out['status'],out['final_tier']),('unanswered',None));self.assertTrue(out['escalations'])

    def test_validation_catches_duplicate_grain_and_unbound_period_and_only_reads(self):
        v=R.validate('Show x in 2029','SELECT 1',{},['month','n'],[['a',1],['a',2]],grain=['month'])
        self.assertEqual({c['name']for c in v['checks']if not c['ok']},{'grain_unique','time_window_bound'})
        for sql in ('DELETE FROM meta','SELECT 1; SELECT 2','PRAGMA table_info(meta)'):
            with self.assertRaises(store.RecipeError):store.run(sql)

    def test_bottle_is_exact_typed_provisional_by_default_and_schema_bound(self):
        with tempfile.TemporaryDirectory() as d:
            b=BS.Bottles(Path(d)/'b.sqlite3')
            recipe={'sql':"SELECT COUNT(*) AS n FROM utilization_event WHERE kind='inpatient' AND substr(event_date,1,4)=:year",
                    'params':{'year':{'type':'year','value':'2029'}},'grain':[],'question_template':'How many admissions happened in {year}?'}
            bid,why=b.add('How many admissions happened in 2029?',recipe,{'ok':True},{'tier':'llm'});self.assertIsNone(why)
            self.assertIsNone(b.match('How many admissions happened in 2028?',statuses=('promoted',)))
            out=router.answer('How many admissions happened in 2028?',tiers=('bottles',),bottles=b)
            self.assertEqual(out['status'],'unanswered')
            b.set_status(bid,'promoted','test')
            out=router.answer('How many admissions happened in 2029?',tiers=('bottles',),bottles=b)
            self.assertEqual((out['final_tier'],out['rows']),('bottles',[[1]]))
            self.assertIsNone(b.match('How many admissions happened in twenty29?'))
            self.assertIsNone(b.match('How many admissions happened in 2029 at night?'))
            bad={**recipe,'question_template':'How many admissions happened?'}
            self.assertEqual(b.add('How many admissions happened in 2029?',bad,{},{})[0],None)
            with mock.patch.object(store,'schema_version',return_value='t/2:other'):self.assertIsNone(b.match('How many admissions happened in 2029?'))

    def test_app_bridge_answers_in_the_app_shape_and_otherwise_steps_aside(self):
        from clinical_core.agere.tiered import app as A
        stack=mock.Mock(spec=[])
        with mock.patch.dict(os.environ,{'AGERE_TIERED_CORE':'off'}):self.assertIsNone(A.try_answer(stack,'How many ED visits were there in 2029?'))
        with tempfile.TemporaryDirectory() as d,mock.patch.dict(os.environ,{'AGERE_TIERED_CORE':'on','AGERE_TIERED_BOTTLES':d+'/b.sqlite3','AGERE_TIERED_LEDGER':d+'/l.sqlite3'}):
            out=A.try_answer(stack,'How many ED visits were there in 2029?')
            self.assertEqual((out['route'],out['answer']['status'],out['answer']['verification'],out['answer']['table']),('tiered_core','answered','checked',[{'events':2}]))
            self.assertEqual((out['new_paid_api_calls'],out['planner_receipt']['final_tier']),(0,'recipes'))
            self.assertEqual((out['answer']['answer_type'],out['answer']['count'],out['answer']['unit']),('count',2,' events'))
            for status in ('unanswered','partial'):
                with mock.patch.object(router,'answer',return_value={'status':status,'final_tier':None,'steps':[],'escalations':[],'seconds':0.1}):
                    self.assertIsNone(A.try_answer(stack,'something else'))
            llm={'status':'answered','final_tier':'llm','columns':['n'],'rows':[[1]],'steps':[{'tier':'llm','model':'m','usd':0.02,'tokens':9}],
                 'escalations':[],'seconds':1.0,'recipe':{'sql':'SELECT 1 AS n'},'validation':{'ok':True,'checks':[{'name':'rows_returned','ok':True}]}}
            with mock.patch.object(router,'answer',return_value=llm):
                self.assertEqual(A.try_answer(stack,'new shape')['answer']['verification'],'unverified')
            with mock.patch.object(router,'answer',side_effect=RuntimeError('boom')):self.assertIsNone(A.try_answer(stack,'x'))

    def _readers(self,slm_quote='Possible early rigbt l0wer lobe infltrate',confirm=True,slm_label='yes'):
        def s1(statement,doc,ledger,as_of):
            self.assertEqual(as_of,'2030-05-27')  # the semantic layer's clock, never today's date
            text=doc['text']
            if 'infltrate' in text and text.startswith('IMPRESSION'):label=None  # the garbled scan: System One is not confident
            elif 'infltrate' in text:label='yes' if confirm else 'no'            # the quote check on the small model's words
            else:label='yes' if 'infiltrate' in text else 'no'
            return {'label':label,'by':'system1','model':'stub','usd':0.0,'tokens':0}
        def model(tier,statement,doc):
            base={'by':tier,'model':tier,'usd':0.0,'tokens':0}
            if tier=='llm':return {**base,'label':None,'reason':'undecided'}
            if slm_label=='yes' and RD._flat(slm_quote) in RD._flat(doc['text']):return {**base,'label':'yes','quote':slm_quote}
            return {**base,'label':None,'reason':'a yes without a verbatim quote from the document'}
        return mock.patch.object(RD,'_system_one',s1),mock.patch.object(RD,'_model',model)

    def test_reading_tier_selects_by_sql_reads_each_document_cheapest_first_and_counts_in_code(self):
        q='How many scanned radiology reports dated in 2029-06 report a possible lung infiltrate?'
        a,b=self._readers()
        with a,b:out=router.answer(q,tiers=('recipes',))
        self.assertEqual((out['status'],out['final_tier'],out['rows']),('answered','recipes',[[3,2,1,0]]))  # unreadable d4 and July d5 are not selected
        self.assertEqual({d['record_id']:d['by']for d in out['documents']},{'d1':'system1','d2':'system1','d3':'slm'})
        self.assertEqual([(s['reader'],s['calls'],s['decided'])for s in out['steps']if s['tier']=='read'],[('system1',3,2),('slm',1,1)])
        for a,b in (self._readers(slm_quote='Possible lung infiltrate'),self._readers(confirm=False)):  # an invented quote; a quote System One does not confirm
            with a,b:out=router.answer(q,tiers=('recipes',))
            self.assertEqual((out['status'],out['rows']),('partial',[[3,1,1,1]]))
            self.assertEqual([c['name']for c in out['validation']['checks']if not c['ok']],['every_document_decided'])
            self.assertEqual(out['documents'][2]['readers_tried'],['system1','slm','llm'])
        from clinical_core.agere.tiered import app as A
        a,b=self._readers()
        with tempfile.TemporaryDirectory() as d,mock.patch.dict(os.environ,{'AGERE_TIERED_CORE':'on','AGERE_TIERED_BOTTLES':d+'/b.sqlite3','AGERE_TIERED_LEDGER':d+'/l.sqlite3'}),a,b:
            shown=A.try_answer(mock.Mock(spec=[]),q)
        self.assertEqual((shown['answer']['verification'],shown['answer']['table'][0]['documents_stating'],len(shown['tiered_core']['documents'])),('unverified',2,3))
        self.assertIn('System One (2), the small model (1)',shown['answer']['facts']['documents_read'])  # what the answer view shows
        self.assertIn('built-in recipe',shown['answer']['facts']['answered_by'].lower())
        self.assertEqual((shown['answer']['count'],shown['answer']['unit']),(2,' documents stating it, of 3 read'))
        self.assertEqual([g.split(':')[0]for g in shown['answer']['grounding']],['d1','d2','d3']);self.assertIn('rigbt',shown['answer']['grounding'][2])

    def test_extraction_returns_only_the_documents_own_words_and_escalates_what_is_unconfirmed(self):
        db=store.connect();plan={'statement':'The report gives an impression.','value':'the impression','mode':'extract'}
        sql="SELECT record_id FROM document WHERE substr(event_date,1,7)='2029-06' ORDER BY 1"
        def s1(statement,doc,ledger,as_of):
            label='no' if doc['record_id']=='d2' and doc['text'].startswith('IMPRESSION') else 'yes'
            if 'rigbt' in doc['text'] and not doc['text'].startswith('IMPRESSION'):label='no'  # refuses to confirm the garbled value
            return {'label':label,'by':'system1','model':'stub','usd':0.0,'tokens':0}
        def claude(model,system,user):
            text=user.split('Document:\n')[1];value=text.split(': ')[1].rstrip('.')
            if model=='frontier':return '{"found": true, "value": "%s", "quote": "%s"}'%(value,text),{'usd':0.0,'tokens':0}
            if 'rigbt' in text:value=value.replace('rigbt','right')  # the small model "corrects" the scan: not the document's words
            return '{"found": true, "value": "%s", "quote": "%s"}'%(value,text),{'usd':0.0,'tokens':0}
        with mock.patch.object(RD,'_system_one',s1),mock.patch.object(RD.models,'_claude',claude),mock.patch.object(RD.models,'LLM','frontier'):
            columns,rows,validation,steps,docs=RD.execute('q',sql,{},plan,db)
        db.close()
        self.assertEqual(columns,['record_id','value']);self.assertTrue(validation['ok'])
        self.assertEqual(rows,[['d1','Possible early right lower lobe infiltrate'],['d3','Possible early rigbt l0wer lobe infltrate']])
        self.assertEqual({d['record_id']:(d['label'],d['by'])for d in docs},{'d1':('yes','slm'),'d2':('no','system1'),'d3':('yes','llm')})
        self.assertIsNotNone(R.match("What discharge disposition does each of patient P-1's HIE discharge summaries give?"))
        self.assertIsNone(R.match("What discharge disposition does each of patient P-999's HIE discharge summaries give?"))

    def test_reading_plans_are_bounded_never_select_text_and_bottle_with_case_kept(self):
        for sql in ("SELECT text FROM scanned_document_text","SELECT payload FROM record"):
            with self.assertRaises(store.RecipeError):store.run(sql)
        db=store.connect()
        with self.assertRaises(store.RecipeError):RD.execute('q','SELECT patient_id FROM document',{},{'statement':'anything at all','mode':'count'},db)
        with mock.patch.object(RD,'MAX_DOCUMENTS',2),self.assertRaises(store.RecipeError):
            RD.execute('q','SELECT record_id FROM document',{},{'statement':'anything at all','mode':'count'},db)
        db.close()
        with tempfile.TemporaryDirectory() as d:
            b=BS.Bottles(Path(d)/'b.sqlite3')
            plan={'sql':"SELECT record_id FROM document WHERE patient_id = :patient ORDER BY 1",'params':{'patient':{'type':'text','value':'P-1'}},'grain':[],
                  'read':{'statement':'The report describes an infiltrate.','mode':'list'},'question_template':"Which of patient {patient}'s scans report an infiltrate?"}
            bid,why=b.add("Which of patient P-1's scans report an infiltrate?",plan,{'ok':True},{'tier':'llm'});self.assertIsNone(why);b.set_status(bid,'promoted','test')
            self.assertEqual(b.match("which of patient P-1's scans report an infiltrate")[1],{'patient':'P-1'})
            a,c=self._readers()
            with a,c:out=router.answer("Which of patient P-1's scans report an infiltrate?",tiers=('bottles',),bottles=b)
            self.assertEqual((out['status'],out['final_tier'],sorted(r[0]for r in out['rows'])),('answered','bottles',['d1','d3','d5']))

    def test_follow_up_is_made_complete_cheapest_first_and_an_unconfirmed_rewrite_is_not_used(self):
        from clinical_core.agere.tiered import app as A,followup as F
        prev='How many ED visits were there in 2029?'
        with mock.patch.object(F.models,'_claude',side_effect=AssertionError('no model for a period swap')):
            self.assertEqual(F.rewrite('and in 2028?',prev)[0],'How many ED visits were there in 2028?')
            self.assertEqual(F.rewrite('what about 2028',prev)[1]['method'],'period_swapped_by_code')
        self.assertIsNone(F.rewrite('and in 2028?',None)[0])
        said=lambda text:mock.patch.object(F.models,'_claude',return_value=(text,{'usd':0.001,'tokens':50}))
        s1=lambda label:mock.patch('clinical_core.agere.clinical.system_one.choice',return_value={'label':label,'probabilities':{label:0.95},'cost_usd':0.0})
        with said('{"standalone": "How many hospital admissions were there in 2029?"}'),s1('yes'):
            text,how=F.rewrite('what about hospital admissions?',prev);self.assertEqual((text,how['method']),('How many hospital admissions were there in 2029?','small_model_rewrite_confirmed_by_system_one'))
        with said('{"standalone": "How many hospital admissions were there in 2028?"}'),s1('no'):
            self.assertEqual(F.rewrite('what about hospital admissions?',prev)[1]['method'],'rewrite_not_confirmed')
        with said('{"standalone": null}'):self.assertEqual(F.rewrite('why is that?',prev)[1]['method'],'not_an_edit_of_the_previous_question')
        stack=mock.Mock(spec=[])
        with tempfile.TemporaryDirectory() as d,mock.patch.dict(os.environ,{'AGERE_TIERED_CORE':'on','AGERE_TIERED_BOTTLES':d+'/b.sqlite3','AGERE_TIERED_LEDGER':d+'/l.sqlite3'}):
            result,standalone,note=A.try_in_conversation(stack,'and in 2028?',prev,True)
            self.assertEqual((standalone,result['answer']['count']),('How many ED visits were there in 2028?',1));self.assertIn('swapped',result['answer']['facts']['follow_up'])
            result,standalone,note=A.try_in_conversation(stack,'Show monthly ED visits in 2029',prev,True)  # complete on its own: never rewritten
            self.assertEqual((standalone,len(result['answer']['table'])),('Show monthly ED visits in 2029',2))
            with said('{"standalone": "How many hospital admissions were there in 2028?"}'),s1('no'):
                self.assertIsNone(A.try_in_conversation(stack,'what about hospital admissions?',prev,True))

    def test_eval_scoring_ignores_names_and_order_but_not_values_or_row_count(self):
        ref=[['a',1],['b',2]]
        self.assertTrue(EV.correct(ref,[[2,'b','extra'],[1.0,'a','x']]))
        self.assertFalse(EV.correct(ref,[['a',1]]));self.assertFalse(EV.correct(ref,[['a',1],['b',3]]));self.assertFalse(EV.correct(ref,None))


if __name__=='__main__':unittest.main()
