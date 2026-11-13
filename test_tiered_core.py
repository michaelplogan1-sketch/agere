import json
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
        from clinical_core.agere.tiered import models as M  # any unpatched model call fails the suite's promise of no model calls
        cls.no_model=mock.patch.object(M,'_claude',side_effect=RuntimeError('a test reached a model'));cls.no_model.start()

    @classmethod
    def tearDownClass(cls):
        cls.no_model.stop();cls.env.stop();cls.dir.cleanup();R._vocab.clear()

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
        with mock.patch.object(RD,'MAX_DOCUMENTS',2),mock.patch.object(RD,'MAX_SCREENED',2),self.assertRaises(store.RecipeError):  # the bound for a count is now the screening limit
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
        with mock.patch.object(F.models,'_claude',side_effect=AssertionError('no model for a relative period')):
            self.assertEqual(F.rewrite('next year',prev)[0],'How many ED visits were there in 2030?');self.assertEqual(F.rewrite('the year before?',prev)[0],'How many ED visits were there in 2028?')
            self.assertEqual(F.rewrite('and the year after?',prev)[1]['method'],'relative_period_by_code');self.assertEqual(F.rewrite('two years earlier',prev)[0],'How many ED visits were there in 2027?')
            self.assertEqual(F.relative_period('three months later','Members per payer in 2029-11'),'Members per payer in 2030-02');self.assertEqual(F.relative_period('the month before','Members per payer in 2029-01'),'Members per payer in 2028-12')
            self.assertIsNone(F.relative_period('next month',prev));self.assertIsNone(F.relative_period('next year','ED visits in 2028 and 2029'));self.assertIsNone(F.relative_period('next year for women',prev))
        said=lambda text:mock.patch.object(F.models,'_claude',return_value=(text,{'usd':0.001,'tokens':50}))
        s1=lambda label:mock.patch('clinical_core.agere.clinical.system_one.choice',return_value={'label':label,'probabilities':{label:0.95},'cost_usd':0.0})
        with said('{"standalone": "How many hospital admissions were there in 2029?"}'),s1('yes'):
            text,how=F.rewrite('what about hospital admissions?',prev);self.assertEqual((text,how['method']),('How many hospital admissions were there in 2029?','small_model_rewrite_confirmed_by_system_one'))
        # System One does not confirm: the frontier model rewrites independently; used only if both have the same words
        both=lambda small,frontier:mock.patch.object(F.models,'_claude',side_effect=lambda model,system,user:((small if model==F.models.SLM else frontier),{'usd':0.001,'tokens':50}))
        with both('{"standalone": "How many hospital admissions were there in 2028?"}','{"standalone": "How many hospital admissions were there in 2029?"}'),s1('no'):
            text,how=F.rewrite('what about hospital admissions?',prev);self.assertEqual((text,how['method'],how['frontier_called']),(None,'rewrite_not_confirmed',True))
        with both('{"standalone": "How many hospital admissions were there in 2029?"}','{"standalone": null}'),s1('no'):
            self.assertEqual(F.rewrite('what about hospital admissions?',prev)[1]['method'],'rewrite_not_confirmed')
        with both('{"standalone": "How many hospital admissions were there in 2029?"}','{"standalone": "how many hospital admissions were there in 2029"}'),s1('no'):
            text,how=F.rewrite('what about hospital admissions?',prev);self.assertEqual((text,how['method']),('How many hospital admissions were there in 2029?','small_model_rewrite_matched_by_frontier_model'))
        with said('{"standalone": null}'):self.assertEqual(F.rewrite('why is that?',prev)[1]['method'],'not_an_edit_of_the_previous_question')
        stack=mock.Mock(spec=[])
        with tempfile.TemporaryDirectory() as d,mock.patch.dict(os.environ,{'AGERE_TIERED_CORE':'on','AGERE_TIERED_BOTTLES':d+'/b.sqlite3','AGERE_TIERED_LEDGER':d+'/l.sqlite3'}):
            with s1('depends'):result,standalone,note=A.try_in_conversation(stack,'and in 2028?',prev,True)
            self.assertEqual((standalone,result['answer']['count']),('How many ED visits were there in 2028?',1));self.assertIn('swapped',result['answer']['facts']['follow_up'])
            result,standalone,note=A.try_in_conversation(stack,'Show monthly ED visits in 2029',prev,True)  # complete on its own: never rewritten
            self.assertEqual((standalone,len(result['answer']['table'])),('Show monthly ED visits in 2029',2))
            with both('{"standalone": "How many hospital admissions were there in 2028?"}','{"standalone": "How many hospital admissions were there in 2029?"}'),s1('no'):
                self.assertIsNone(A.try_in_conversation(stack,'what about hospital admissions?',prev,True))
            with s1('complete'),mock.patch.object(F,'rewrite',side_effect=AssertionError('a complete question is never rewritten')):
                self.assertEqual(F.complete_question('List the 5 most common conditions',prev,True)[1]['method'],'complete_by_system_one')
            tie=mock.patch('clinical_core.agere.clinical.system_one.choice',return_value={'label':'complete','probabilities':{'complete':0.485,'depends':0.468},'cost_usd':0.0})
            with tie,mock.patch.object(F,'rewrite',return_value=('Show the denial rate by payer in 2029',{'method':'small_model_rewrite_confirmed_by_system_one','usd':0.0,'tokens':0})):
                self.assertEqual(F.complete_question('just the denial rate',prev,True)[0],'Show the denial rate by payer in 2029')  # a near-tie is not "complete"

    def test_a_message_with_separate_questions_is_split_and_answered_in_sections_only_when_confirmed(self):
        from clinical_core.agere.tiered import app as A,split as S
        q='How many ED visits were there in 2029, and also show monthly ED visits in 2029?'
        self.assertFalse(S.candidate('How many ED visits were there in 2029?'));self.assertTrue(S.candidate(q))
        said=lambda text:mock.patch.object(S.models,'_claude',return_value=(text,{'usd':0.001,'tokens':50}))
        s1=lambda label:mock.patch('clinical_core.agere.clinical.system_one.choice',return_value={'label':label,'probabilities':{label:0.95},'cost_usd':0.0})
        two='{"parts": ["How many ED visits were there in 2029?", "Show monthly ED visits in 2029"]}'
        with said(two),s1('yes'):self.assertEqual(S.parts(q)[1]['method'],'small_model_split_confirmed_by_system_one')
        with said(two),s1('no'):self.assertEqual(S.parts(q),(None,mock.ANY));self.assertEqual(S.parts(q)[1]['method'],'split_not_confirmed')
        with said('{"parts": null}'):self.assertEqual(S.parts(q)[1]['method'],'one_question')
        with said('{"parts": ["How many ED visits were there in 2029?", "Show monthly ED visits in 2028"]}'):self.assertEqual(S.parts(q)[1]['method'],'parts_add_values')
        with said('{"parts": ["How many ED visits were there in 2029?"]}'):self.assertEqual(S.parts(q)[1]['method'],'parts_unusable')
        stack=mock.Mock(spec=[])
        with tempfile.TemporaryDirectory() as d,mock.patch.dict(os.environ,{'AGERE_TIERED_CORE':'on','AGERE_TIERED_BOTTLES':d+'/b.sqlite3','AGERE_TIERED_LEDGER':d+'/l.sqlite3'}):
            with said(two),s1('yes'):out=A.try_answer(stack,q)
            a=out['answer'];self.assertEqual((a['answer_type'],[x['title'] for x in a['sections']],a['sections'][0]['answer']['answer_type'],len(out['graph']['nodes'])),
                                             ('composite',['How many ED visits were there in 2029?','Show monthly ED visits in 2029'],'count',2))
            self.assertEqual(out['planner_receipt']['split']['parts'][1],'Show monthly ED visits in 2029')
            with said('{"parts": ["How many ED visits were there in 2029?", "What is the meaning of it all in 2029?"]}'),s1('yes'),mock.patch.object(A.router,'answer',wraps=A.router.answer) as ran:
                with mock.patch.object(A,'_single',side_effect=lambda st,part:None if 'meaning' in part or part==q else {'answer':{}}) as single:
                    self.assertIsNone(A.try_answer(stack,q))  # one part unanswered: not answered in pieces; tried once more as one question
                    self.assertEqual([c.args[1] for c in single.call_args_list][-1],q)

    def test_known_wrong_answer_paths_are_closed_by_code(self):
        from clinical_core.agere.tiered import followup as F,recipes as R,store as ST,bottles as BS
        self.assertTrue(R.SECOND_QUESTION.search('a stress test was ordered, and how many recorded deaths occurred in 2029'))
        self.assertFalse(R.SECOND_QUESTION.search('a fracture and a dislocation'))
        for bad in ("SELECT COUNT(*) FROM concept_patient WHERE concept LIKE '%diabetes%'","SELECT 1 FROM fill WHERE lower(drug_class) LIKE 'stat%'"):
            with self.assertRaises(ST.RecipeError):ST.run(bad)
        vocab=mock.patch.object(R.store,'run',return_value=(['v'],[['diabetes'],['type_2_diabetes'],['hypertension']]))
        with vocab:
            self.assertIn('narrower',R.narrowed_vocabulary('patients with both hypertension and diabetes',{'a':'hypertension','b':'type_2_diabetes'}))
            self.assertIsNone(R.narrowed_vocabulary('patients with type 2 diabetes',{'b':'type_2_diabetes'}));self.assertIsNone(R.narrowed_vocabulary('patients with sugar problems',{'b':'type_2_diabetes'}))
        prev='List inpatient stays admitted in 2028 by organization'
        self.assertEqual(F.borrowed_period('what was the 30-day readmission rate that year?',prev),'what was the 30-day readmission rate in 2028?')
        self.assertIsNone(F.borrowed_period('how many of those were readmitted that year?',prev));self.assertIsNone(F.borrowed_period('deaths that month?',prev))
        with mock.patch.object(F.models,'_claude',side_effect=AssertionError('no model')),mock.patch('clinical_core.agere.clinical.system_one.choice',side_effect=AssertionError('no System One')):
            self.assertEqual(F.complete_question('what was the 30-day readmission rate that year?',prev,True),('what was the 30-day readmission rate in 2028?',{'method':'period_borrowed_by_code','usd':0.0,'tokens':0}))
        with tempfile.TemporaryDirectory() as d:
            b=BS.Bottles(Path(d)/'b.sqlite3');q='How many widgets in 2029?'
            plan=lambda sql:{'sql':sql,'params':{'year':{'type':'year','value':'2029'}},'grain':[],'question_template':'How many widgets in {year}?'}
            bid,why=b.add(q,plan("SELECT 1 WHERE :year = '2029'"),{'ok':True},{'tier':'llm'});self.assertIsNone(why)
            self.assertEqual(b.add(q,plan("SELECT 2 WHERE :year = '2029'"),{'ok':True},{'tier':'llm'}),(None,'a bottle with this pattern already exists'))
            b.set_status(bid,'rejected','test')
            self.assertEqual(b.add(q,plan("SELECT 2 WHERE :year = '2029'"),{'ok':True},{'tier':'llm'}),(bid,None))  # a rejected plan is replaced
            hit=b.match(q);self.assertEqual((hit[0]['sql'],b.match(q,statuses=('promoted',))),("SELECT 2 WHERE :year = '2029'",None))

    def test_a_follow_up_on_selected_patients_runs_inside_that_group_by_code(self):
        from clinical_core.agere.tiered import app as A,followup as F,store as ST
        whole=router.answer('How many ED visits were there in 2029?',tiers=('recipes',))['rows'][0][0]
        db=ST.connect();ids=[r[0] for r in db.execute("SELECT DISTINCT patient_id FROM utilization_event WHERE kind='ed_visit' AND substr(event_date,1,4)='2029' ORDER BY 1 LIMIT 1")]
        sql="SELECT COUNT(*) FROM utilization_event WHERE kind='ed_visit' AND substr(event_date,1,4)='2029'"
        own=db.execute(sql+' AND patient_id=?',ids).fetchone()[0];self.assertTrue(0<own<=whole)
        inside=router.answer('How many ED visits were there in 2029?',tiers=('recipes',),cohort=ids)
        self.assertEqual((inside['status'],inside['rows']),('answered',[[own]]))
        self.assertEqual(db.execute(ST.portable(sql,ids)).fetchall(),[(own,)])  # the saved SQL gives the group's number on a plain connection
        self.assertEqual(db.execute(ST.portable(sql,[])).fetchall(),[(0,)]);self.assertEqual(router.answer('How many ED visits were there in 2029?',tiers=('recipes',),cohort=[])['rows'],[[0]])
        with mock.patch.object(F.models,'_claude',side_effect=AssertionError('no model when code removes the pointing words')):
            self.assertEqual(F.group_question('how many ED visits were there in 2029 among them?')[1]['method'],'group_words_removed_by_code')
        said=lambda text:mock.patch.object(F.models,'_claude',return_value=(text,{'usd':0.001,'tokens':50}))
        s1=lambda label:mock.patch('clinical_core.agere.clinical.system_one.choice',return_value={'label':label,'probabilities':{label:0.95},'cost_usd':0.0})
        with said('{"standalone": null}'):self.assertEqual(F.group_question('why are they in this group?'),(None,mock.ANY))
        with said('{"standalone": "How many ED visits were there in 2028?"}'),s1('no'):self.assertEqual(F.group_question('and their ED visits the year before?')[1]['method'],'rewrite_not_confirmed')
        stack=mock.Mock(spec=[]);parent={'patient_ids':ids,'source':{'x':1}}
        with tempfile.TemporaryDirectory() as d,mock.patch.dict(os.environ,{'AGERE_TIERED_CORE':'on','AGERE_TIERED_BOTTLES':d+'/b.sqlite3','AGERE_TIERED_LEDGER':d+'/l.sqlite3'}):
            out=A.try_in_cohort(stack,'how many ED visits were there in 2029 among them?',parent,'ANS-1')
            self.assertEqual((out['answer']['count'],out['cohort_transform']['parent_context_id'],out['cohort_transform']['input_patient_ids'],out['question']),(own,'ANS-1',ids,'how many ED visits were there in 2029 among them?'))
            self.assertIn('cohort(patient_id)',out['graph']['nodes'][0]['parameters']['query']);self.assertIn('only over the 1 selected patients',out['answer']['limitations'][0])
            self.assertIsNone(A.try_in_cohort(stack,'x',{'patient_ids':[]},'ANS-1'))
            with said('{"standalone": null}'):self.assertIsNone(A.try_in_cohort(stack,'why are they in this group?',parent,'ANS-1'))

    def test_batch_learning_confirms_a_recipe_only_when_a_second_model_reproduces_its_rows(self):
        from clinical_core.agere.tiered import curriculum as C,bottles as BS,models as M
        with tempfile.TemporaryDirectory() as d:
            home=Path(d);names={'HOME':home,'QUESTIONS':home/'q.jsonl','BOTTLES':home/'b.sqlite3','LEDGER':home/'l.sqlite3','S1':home/'s.sqlite3'}
            with mock.patch.multiple(C,**names),mock.patch.object(C,'_keys'),mock.patch.dict(os.environ,{'AGERE_TIERED_BOTTLES':d+'/main.sqlite3'}):
                wrote=lambda qs:mock.patch.object(M,'_claude',return_value=(json.dumps({'questions':qs}),{'usd':0.001,'tokens':10}))
                with wrote(['How many ED visits were there in 2029?','how many ed visits were there in 2029','Show monthly ED visits in 2029','x']):
                    self.assertEqual(C.generate(2)['new'],2)  # duplicates and unusable lines dropped
                b=BS.Bottles(C.BOTTLES);sql="SELECT COUNT(*) AS n FROM utilization_event WHERE kind = 'ed_visit' AND substr(event_date, 1, 4) = :year"
                plan=lambda q,t,s:b.add(q,{'sql':s,'params':{'year':{'type':'year','value':'2029'}},'grain':[],'question_template':t},{'ok':True},{'tier':'llm','question':q})[0]
                good=plan('Count ED events in 2029','Count ED events in {year}',sql);bad=plan('Count the ED events of 2029','Count the ED events of {year}',sql)
                second=lambda s:(({'sql':s,'params':{'year':{'type':'year','value':'2029'}},'grain':[],'question_template':'x'},{'usd':0.0}))
                with mock.patch.object(M,'llm_recipe',side_effect=[second("SELECT COUNT(*) FROM utilization_event WHERE kind='ed_visit' AND event_date LIKE :year || '%'"),second("SELECT COUNT(*) FROM utilization_event WHERE substr(event_date,1,4) = :year")]):
                    self.assertEqual(C.confirm(),{'confirmed':1,'unconfirmed':1})
                status={x['id']:x['status'] for x in BS.Bottles(C.BOTTLES).full()};self.assertEqual((status[good],status[bad]),('confirmed','provisional'))
                self.assertEqual(C.merge(),{'merged as provisional':1})
                main=BS.Bottles().full();self.assertEqual((len(main),main[0]['status'],main[0]['provenance']['confirmed_by_second_model']),(1,'provisional',True))

    def test_a_large_document_set_is_screened_by_system_one_and_only_the_undecided_go_to_models(self):
        from clinical_core.agere.tiered import read as RD,recipes as R,learn as LN
        self.assertEqual(R.match('How many scanned radiology reports dated in 2029 report a nodule?')[0],'scanned_documents_stating_in_year')
        self.assertEqual(R.match('How many scanned radiology reports dated in 2029-06 report a nodule?')[0],'scanned_documents_stating')
        docs=[{'record_id':f'd{i}','text':'x','event_date':'2029-01-01','source':'scanned_document'} for i in range(6)]
        calls=[]
        def judge(statement,doc,*,readers,s1_ledger=None,as_of=None):
            calls.append((doc['record_id'],readers));n=int(doc['record_id'][1:])
            label='yes' if n<2 else 'no' if n<4 else ('yes' if len(readers)>1 else None)
            return {'record_id':doc['record_id'],'label':label,'by':readers[-1] if label and len(readers)>1 and n>=4 else 'system1','attempts':[{'by':'system1','label':label if n<4 else None,'usd':0.0,'tokens':0}]}
        common=[mock.patch.object(RD,'MAX_DOCUMENTS',3),mock.patch.object(RD,'judge',side_effect=judge),mock.patch.object(RD.store,'run',return_value=(['record_id'],[[d['record_id']] for d in docs])),
                mock.patch.object(RD.store,'texts',return_value={d['record_id']:d for d in docs}),mock.patch.object(RD.store,'as_of',return_value='2030-01-01')]
        for c in common:c.start()
        try:
            cols,rows,val,steps,ev=RD.execute('How many in 2029?','SELECT record_id FROM document WHERE substr(event_date,1,4)=:year',{'year':'2029'},{'statement':'It says x.','mode':'count'},None)
            self.assertEqual(rows,[[6,4,2,0]]);self.assertEqual(sum(1 for c in calls if len(c[1])>1),2)  # only d4 and d5 went past System One
            calls.clear()
            with mock.patch.object(RD,'MAX_MODEL_READS',1):
                rows=RD.execute('How many in 2029?','SELECT record_id FROM document WHERE substr(event_date,1,4)=:year',{'year':'2029'},{'statement':'It says x.','mode':'count'},None)
                self.assertEqual((rows[1],rows[2]['ok']),([[6,2,2,2]],False))  # over the model budget: reported with its undecided documents, never guessed
            with self.assertRaises(RD.store.RecipeError):RD.execute('q','SELECT record_id FROM document',{},{'statement':'It gives x.','mode':'extract','value':'the x'},None)
            with mock.patch.object(RD,'MAX_SCREENED',5),self.assertRaises(RD.store.RecipeError):RD.execute('q','SELECT record_id FROM document',{},{'statement':'It says x.','mode':'count'},None)
        finally:
            for c in common:c.stop()
        with tempfile.TemporaryDirectory() as d,mock.patch.object(LN,'OUT',Path(d)),mock.patch.object(LN,'LOCK',Path(d)/'lock'):
            (Path(d)/'lock').write_text('1');self.assertEqual(LN.auto(),{'decision':'already_running'});self.assertFalse(LN.spawn_auto())

    def test_medication_reasons_are_typed_judgments_on_passages_code_finds(self):
        from clinical_core.agere.tiered import reasons as RS,split as SP
        texts={'d1':{'text':'Meds reviewed. carvedilol 12.5 mg tablet: No replacement prescription authorized at this review. Reports running out. furosemide 40 mg tablet: taking daily.','event_date':'2029-10-01'},
               'd2':{'text':'Prior entry dated 2026-06-15: MRA held for K 5.2 — CKD3 complicates the pillar math. History on file.','event_date':'2029-11-01'},
               'd3':{'text':'Prior entry dated 2026-06-15: MRA held for K 5.2 — CKD3 complicates the pillar math. History on file.','event_date':'2029-12-01'},
               'd4':{'text':'Ankle sprain. Ice and elevation.','event_date':'2029-12-02'}}
        found=RS.passages(texts);self.assertEqual(sorted((p['drug'],len(p['record_ids'])) for p in found),[('carvedilol',1),('mra',2)])  # a repeated passage is judged once
        self.assertNotIn('furosemide',next(p['text'] for p in found if p['drug']=='carvedilol'))  # the next drug's entry ends this one's
        answers={'carvedilol':'cost_or_supply','mra':None}
        s1=lambda passage,ledger,as_of:{'by':'system1','label':answers[passage['drug']],'usd':0.0,'tokens':0}
        slm=lambda tier,passage:{'by':tier,'label':'lab_or_vital_limit' if tier=='slm' else None,'usd':0.001,'tokens':20}
        with mock.patch.object(RS,'_system_one',side_effect=s1),mock.patch.object(RS,'_model',side_effect=slm) as model,mock.patch.object(RS.store,'run',return_value=(['record_id'],[[k] for k in texts])),\
             mock.patch.object(RS.store,'texts',return_value=texts),mock.patch.object(RS.store,'as_of',return_value='2030-01-01'):
            cols,rows,val,steps,ev=RS.execute('q','SELECT record_id FROM document WHERE patient_id=:p',{'p':'x'},{'mode':'medication_reasons'},None)
        self.assertEqual([(r[1],r[2],r[6]) for r in rows],[('carvedilol','cost_or_supply',1),('mra','lab_or_vital_limit',2)]);self.assertEqual((val['ok'],model.call_count),(True,1))  # only the undecided passage reached a model
        self.assertTrue(all(any(r[3] in ' '.join(t['text'].split()) for t in texts.values()) for r in rows))  # every passage is the record's own words
        self.assertEqual(RS.by_code({'text':'carvedilol 12.5 mg tablet: reports use on 0 of 14 days; 0 tablet reported left.'})['label'],'not_taken_no_reason')
        self.assertIsNone(RS.by_code({'text':'carvedilol 12.5 mg tablet: reports use on 0 of 14 days. Reports running out.'}))
        parts,how=SP.known("Why isn't patient P-HF07 on all four GDMT pillars?");self.assertEqual((len(parts),how['usd']),(4,0.0));self.assertIsNone(SP.known('Why is the sky blue?'))

    def test_narrative_numbers_come_only_from_the_result_and_a_bad_text_is_discarded(self):
        from clinical_core.agere.tiered import narrative as N
        cols,rows=['month','events'],[['2029-01',120],['2029-02',95],['2029-03',1430]]
        f=N.facts(cols,rows);self.assertEqual((f['events']['total'],f['events']['highest'],f['events']['lowest']['row']),(1645,{'value':1430,'row':'2029-03'},'2029-02'))
        with mock.patch.object(N.models,'_claude',side_effect=AssertionError('one row needs no model')):
            self.assertEqual(N.write('How many?',['events'],[[1501]]),('events: 1,501.',{'by':'code','usd':0.0,'tokens':0}))
            self.assertIn('2 of the 18 documents read state it',N.write('q',['a','b','c','d'],[[18,2,16,0]],{'mode':'count'},'X.')[0])
        said=lambda text:mock.patch.object(N.models,'_claude',return_value=('{"summary": "%s"}'%text,{'usd':0.001,'tokens':40}))
        with said('ED visits in 2029 totalled 1,645 across 3 months, peaking in 2029-03 at 1,430.'):
            text,how=N.write('Show monthly ED visits in 2029',cols,rows);self.assertEqual(how['by'],'small_model');self.assertIn('1,645',text)
        for bad in ('Visits rose 87% to 1,430 in 2029-03 after 120 in 2029-01.','About 1,600 visits in total were recorded across the three months shown.'):
            with said(bad):
                text,how=N.write('Show monthly ED visits in 2029',cols,rows)
                self.assertEqual(how['by'],'code');self.assertIn('discarded',how['small_model']);self.assertIn('Total events: 1,645',text)
        self.assertEqual(N.unsupported_numbers('rate 0.1234 over 2 rows','q',['p','rate'],[['a',0.1234],['b',0.2]],{'rows':2}),[])

    def test_eval_scoring_ignores_names_and_order_but_not_values_or_row_count(self):
        ref=[['a',1],['b',2]]
        self.assertTrue(EV.correct(ref,[[2,'b','extra'],[1.0,'a','x']]))
        self.assertFalse(EV.correct(ref,[['a',1]]));self.assertFalse(EV.correct(ref,[['a',1],['b',3]]));self.assertFalse(EV.correct(ref,None))


if __name__=='__main__':unittest.main()
