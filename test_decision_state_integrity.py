"""Full typed-decision inputs and unique teaching units; authored local data only."""
from copy import deepcopy
import json,os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import numpy as np
from clinical_core.agere.clinical import system_one as S,system_one_student as T

Q='Is the patient on therapy at the cutoff?'
OPTIONS={'yes':'On therapy','no':'Not on therapy','unknown':'Cannot determine from the record'}
CUTOFF='2026-10-20'
EV=[{'id':'n','note_id':'note','date':'2026-09-20','text':'Patient stopped medication because of cost.'}]

def row(label='yes',**kw):return T.example(Q,OPTIONS,EV,label,as_of=CUTOFF,**kw)

class Inputs(unittest.TestCase):
    def test_late_text_dates_cutoff_and_option_meanings_survive(self):
        first=[{**EV[0],'text':'Routine review. '*400+'Medication was stopped.'}]
        other=[{**first[0],'text':'Routine review. '*400+'Medication was restarted.'}]
        base=T.featurize(Q,OPTIONS,first,as_of=CUTOFF)
        self.assertIn('Medication was stopped.',base)
        self.assertNotEqual(base,T.featurize(Q,OPTIONS,other,as_of=CUTOFF))
        self.assertNotEqual(base,T.featurize(Q,OPTIONS,[{**first[0],'date':'2026-10-10'}],as_of=CUTOFF))
        self.assertNotEqual(base,T.featurize(Q,OPTIONS,first,as_of='2026-09-25'))
        self.assertNotEqual(base,T.featurize(Q,{**OPTIONS,'yes':'Not on therapy','no':'On therapy'},first,as_of=CUTOFF))

    def test_same_provider_state_normalization_and_no_mutation(self):
        original=deepcopy(EV);state=T.decision_state(Q,OPTIONS,EV,as_of=CUTOFF)
        self.assertEqual(state,{'subject':Q,'as_of':CUTOFF,'options':S._options(OPTIONS),'evidence':S._evidence(EV,CUTOFF)})
        self.assertEqual(original,EV)
        self.assertEqual(T.decision_state(Q,OPTIONS,[{'record_id':'r','text':'Unknown-date record'}])['evidence'][0]['date'],'unknown')
        self.assertIsNone(T.decision_state(Q,OPTIONS,EV)['as_of'])

    def test_supplied_source_and_cutoff_dates_keep_provider_boundaries(self):
        with self.assertRaises(ValueError):T.decision_state(Q,OPTIONS,EV,as_of='not-a-date')
        with self.assertRaises(ValueError):T.decision_state(Q,OPTIONS,[{**EV[0],'date':'2026-10-21'}],as_of=CUTOFF)
        with self.assertRaises(ValueError):T.decision_state(Q,OPTIONS,[{**EV[0],'date':'2026-02-31'}],as_of=CUTOFF)

    def test_dictionary_order_does_not_create_a_different_case(self):
        a=row();b=T.example(Q,dict(reversed(list(OPTIONS.items()))),EV,'yes',as_of=CUTOFF)
        self.assertEqual(a['decision_key'],b['decision_key']);self.assertEqual(a['text'],b['text'])

    def test_evidence_order_and_ids_remain_explicit(self):
        ev=EV+[{'id':'fill','note_id':'claim','date':'2026-10-10','text':'Paid medication fill.'}]
        self.assertNotEqual(T.example(Q,OPTIONS,ev,'yes',as_of=CUTOFF)['decision_key'],T.example(Q,OPTIONS,ev[::-1],'yes',as_of=CUTOFF)['decision_key'])
        self.assertIn('claim',T.featurize(Q,OPTIONS,ev,as_of=CUTOFF))

    def test_unseen_option_meaning_cannot_reuse_classifier(self):
        pipeline=Mock();pipeline.predict_proba.return_value=np.array([[.99,.01]])
        model={'input_contract':T.input_contract(),'options':sorted(OPTIONS),'option_contracts':[json.dumps(OPTIONS,sort_keys=True)],
               'pipeline':pipeline,'classes':['yes','no']}
        self.assertIsNotNone(T.probabilities(model,Q,OPTIONS,EV,as_of=CUTOFF))
        pipeline.reset_mock()
        self.assertIsNone(T.probabilities(model,Q,{**OPTIONS,'yes':'Explicitly absent'},EV,as_of=CUTOFF));pipeline.predict_proba.assert_not_called()
        model['input_contract']='old';self.assertIsNone(T.probabilities(model,Q,OPTIONS,EV,as_of=CUTOFF))

    def test_old_manifest_falls_through_without_inference(self):
        model=Mock()
        with patch.object(T,'enabled',return_value=True),patch.object(T,'load',return_value={'manifest':{'schema':'system-one-student/1'},'model':model}):
            self.assertIsNone(T.predict('task',Q,OPTIONS,EV,as_of=CUTOFF))
        model.predict_proba.assert_not_called()

class Store(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'AGERE_SYSTEM_ONE_DISTILL':str(self.root/'lessons.sqlite3'),'AGERE_SYSTEM_ONE_STUDENTS':str(self.root/'models')})
        self.env.start();self.addCleanup(self.env.stop)

    def record(self,label='yes',provider='jev',**kw):
        return T.record('task',Q,OPTIONS,kw.pop('evidence',EV),label,.99,provider,as_of=kw.pop('as_of',CUTOFF),**kw)

    def test_complete_teacher_state_round_trips(self):
        ev=[{**EV[0],'text':'Routine review. '*400+'Medication was stopped.'}]
        self.assertTrue(self.record(evidence=ev,model='authored-model',request_hash='request'))
        with sqlite3.connect(T.store_path()) as db:
            raw=db.execute('SELECT evidence,as_of,teacher_model,request_hash,input_contract FROM labels').fetchone()
        self.assertEqual(json.loads(raw[0]),S._evidence(ev,CUTOFF));self.assertEqual(raw[1:4],(CUTOFF,'authored-model','request'))
        self.assertEqual(raw[4],T.input_contract());self.assertIn('Medication was stopped.',T.teacher_examples('task')[0]['text'])

    def test_distinct_cutoffs_and_option_meanings_have_distinct_records(self):
        self.record();self.record(as_of='2026-09-25')
        T.record('task',Q,{**OPTIONS,'yes':'Explicitly absent'},EV,'yes',.99,'jev',as_of=CUTOFF)
        with sqlite3.connect(T.store_path()) as db:self.assertEqual(db.execute('SELECT count(*) FROM labels').fetchone()[0],3)

    def test_exact_repeat_deduplicates_and_conflicting_label_is_retained(self):
        self.record();self.record();self.record('no')
        with sqlite3.connect(T.store_path()) as db:self.assertEqual(db.execute('SELECT count(*) FROM labels').fetchone()[0],2)
        units,receipt=T.learning_units([],T.teacher_examples('task'))
        self.assertFalse(units);self.assertEqual(receipt['conflicting_groups_excluded'],1)

    def test_legacy_truncated_rows_remain_stored_but_not_relabelled_current(self):
        with sqlite3.connect(T.store_path()) as db:
            db.execute('CREATE TABLE labels(id TEXT PRIMARY KEY,task_key TEXT,question TEXT,options TEXT,evidence TEXT,label TEXT,probability REAL,provider TEXT,recorded_at TEXT)')
            db.execute('INSERT INTO labels VALUES (?,?,?,?,?,?,?,?,?)',('legacy','task',Q,json.dumps(OPTIONS),json.dumps(EV),'yes',.99,'jev','old'))
        self.assertEqual(T.teacher_examples('task'),[])
        with sqlite3.connect(T.store_path()) as db:
            self.assertEqual(db.execute('SELECT id,input_contract FROM labels').fetchall(),[('legacy',None)])
        self.record();self.assertEqual(len(T.teacher_examples('task')),1)

    def test_paid_decision_records_full_cutoff_and_teacher_identity(self):
        probs={'yes':.99,'no':.005,'unknown':.005}
        answer={'label':'yes','probabilities':probs,'calibrated_probabilities':probs,'cost_usd':0.001,'cache_hit':False,
                'request_hash':'fixture-request','model':'fixture-model','evidence_ids':['n']}
        with patch.object(T,'predict',return_value=None) as student,patch.object(S,'choice',return_value=answer):
            r=S.decide(Q,OPTIONS,EV,as_of=CUTOFF,order=('jev','perplexity'),task_key='task')
        self.assertEqual(r['decided_by'],'jev');self.assertEqual(student.call_args.kwargs['as_of'],CUTOFF)
        with sqlite3.connect(T.store_path()) as db:self.assertEqual(db.execute('SELECT as_of,teacher_model,request_hash FROM labels').fetchone(),(CUTOFF,'fixture-model','fixture-request'))

    def test_incompatible_student_heals_to_existing_provider_sequence(self):
        def answer(provider):
            lab='unknown' if provider=='jev' else 'yes';p={k:(.99 if k==lab else .005) for k in OPTIONS}
            return {'label':lab,'probabilities':p,'calibrated_probabilities':p,'cost_usd':0.,'cache_hit':True,'request_hash':None,'model':'fixture','evidence_ids':[]}
        with patch.object(T,'enabled',return_value=True),patch.object(T,'load',return_value={'manifest':{'schema':'old'},'model':Mock()}),\
             patch.object(S,'choice',side_effect=lambda *a,**kw:answer(kw['provider'])) as choice:
            r=S.decide(Q,OPTIONS,EV,as_of=CUTOFF,order=('jev','perplexity'),task_key='task')
        self.assertEqual(r['decided_by'],'perplexity');self.assertEqual(choice.call_count,2)

class Units(unittest.TestCase):
    def test_paired_teachers_count_as_one_case(self):
        a=row(source='teacher:jev');b=row(source='teacher:perplexity')
        units,receipt=T.learning_units([],[a,b,a,b])
        self.assertEqual((len(units),receipt['observations'],receipt['decision_groups']),(1,4,1))
        self.assertEqual(units[0]['weight'],1.)

    def test_distinct_dates_cutoffs_and_late_paragraphs_are_distinct_units(self):
        rows=[row(),T.example(Q,OPTIONS,[{**EV[0],'date':'2026-10-10'}],'yes',as_of=CUTOFF),T.example(Q,OPTIONS,EV,'yes',as_of='2026-09-25')]
        self.assertEqual(len(T.learning_units(rows,[])[0]),3)

    def test_supplied_reference_resolves_teacher_conflict_without_count_inflation(self):
        units,r=T.learning_units([row('no')],[row('yes',source='teacher:jev'),row('no',source='teacher:perplexity')])
        self.assertEqual(len(units),1);self.assertEqual(units[0]['label'],'no');self.assertEqual(r['teacher_conflicts_resolved_by_supplied_reference'],1)

    def test_conflicting_references_and_unbound_old_features_are_not_training_truth(self):
        old={'text':'truncated','label':'yes','options':list(OPTIONS),'weight':1.,'source':'gold'}
        units,r=T.learning_units([row('yes'),row('no'),old],[])
        self.assertFalse(units);self.assertEqual(r['conflicting_groups_excluded'],1);self.assertEqual(r['unbound_rows_excluded'],1)

    def test_cross_fitting_sees_unique_state_units_not_provider_rows(self):
        rows=[]
        for i in range(12):
            r=T.example('Authored query '+str(i),OPTIONS,EV,'yes' if i%2 else 'no',as_of=CUTOFF)
            rows.extend([r,{**r,'source':'teacher:perplexity'}])
        received=[]
        class Pipeline:
            def predict_proba(self,texts):return np.array([[.9,.1] for _ in texts])
        def train(task,examples):
            keys=[e['decision_key'] for e in examples];self.assertEqual(len(keys),len(set(keys)));received.append(keys)
            return {'pipeline':Pipeline(),'classes':['yes','no'],'options':sorted(OPTIONS)}
        with patch.object(T,'teacher_examples',return_value=[]),patch.object(T,'train_rows',side_effect=train):
            m=T.fit('task',gold=rows,save=False)
        self.assertEqual(m['rows'],12);self.assertEqual(m['cross_fitted']['scored'],12)
        self.assertEqual(m['cross_fitted']['split_unit'],'unique_complete_decision_state')
        for fold,trainkeys in enumerate(received[:-1]):
            units,_=T.learning_units(rows,[]);testkeys={r['decision_key'] for i,r in enumerate(units) if i%T.FOLDS==fold}
            self.assertFalse(set(trainkeys)&testkeys)
