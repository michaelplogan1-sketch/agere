"""Pre-index availability, source semantics, mathematical exposure and lineage."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zlib
from unittest.mock import patch

from clinical_core.contracts import digest
from clinical_core.agere.clinical import baseline_evidence as B, outcomes as O, longitudinal as L
from clinical_core.agere.clinical.analysis_graph import validate, execute
from clinical_core.agere.clinical.graph_workbench import baseline_examples
from clinical_core.agere.clinical.economical_planner import local_plan
from test_clinical_state import record, ASOF
import test_analysis_graph as graph_tests


def rec(rid='lab',day='2026-08-01',arrival=None,**kw):
    return {**record(rid=rid,table='labs',test='egfr',value=25,unit='ml/min/1.73m2',status='final'),
            'event_date':day,'available_date':arrival or day,**kw}


def index_record(pid='p'):
    return rec('index','2026-08-20',patient_id=pid,source_table='adt',event='A01')


def anchored(r=None):
    r=r or index_record();e=next(L.record_events(r,ASOF))
    return {'status':'indexed','index':{'event_id':e['id'],'date':'2026-08-20','evidence_ids':[e['id']],
        'encounter_identity':'unavailable'},'source_evidence':[e],'basis':'source_event',
        'contract_id':'admission_discharge_source_30d','contract_hash':'test','prospective_ascertainment_established':False}


def text_event(r,quote='dyspnea',**axes):
    start=r['text'].index(quote)
    return L.annotation_event('e'+r['record_id'],'p',{'source':{**r,'field':'text','start':start,'end':start+len(quote),'quote':quote},
        'input':{'concept':'symptom dyspnea'},'axes':{'assertion':'affirmed','subject':'patient','time':'current','relation':'clinical_observation',**axes},
        'model_identity':'fixture'},r,ASOF)


def evaluate(rows=None,texts=None,analyses=None):
    r=index_record()
    return B.evaluate('p',[r]+(rows or []),analyses or [],texts or [],anchored(r),B.definition('received_prior_180d'))


class TemporalInputs(unittest.TestCase):
    def test_late_arrival_same_day_future_event_and_old_data_excluded(self):
        rows=[rec(),rec('late',arrival='2026-09-01'),rec('same','2026-08-20'),
              rec('future','2026-08-21',arrival='2026-08-01'),rec('old','2025-01-01'),
              rec('nodate',event_date=None),rec('noreceipt',available_date=None)]
        r=evaluate(rows)
        self.assertEqual([e['record_id'] for e in r['included_sources']],['lab'])
        self.assertEqual(r['cutoff'],'2026-08-19');self.assertEqual(r['record_accounting']['considered'],8)
        self.assertEqual(sum(r['record_accounting']['excluded'].values()),7)
        self.assertEqual(r['features']['egfr:source_statement_date:lower'],25)
        self.assertIsNone(r['features']['egfr:explicit_clinical_date:lower'])

    def test_adding_post_index_information_cannot_change_feature_values(self):
        base=evaluate([rec()])
        future=rec('future','2026-08-29',source_table='new_future_source',drug_class='new_future_drug',value=90)
        changed=evaluate([rec(),future])
        self.assertEqual(base['features'],changed['features'])
        self.assertEqual(base['feature_evidence'],changed['feature_evidence'])

    def test_no_index_is_no_features_not_zero_risk(self):
        r=B.evaluate('p',[rec()],[],[],{'status':'no_observed_index','index':None},B.definition('received_prior_180d'))
        self.assertEqual(r['features'],{});self.assertIsNone(r['cutoff']);self.assertFalse(r['model_training_authorized'])

    def test_empty_baseline_is_separate_from_unresolved_index(self):
        r=evaluate();self.assertEqual(r['status'],'no_baseline_observations')
        self.assertEqual(r['features']['labs:received_records'],0);self.assertIsNone(r['features']['egfr:source_statement_date:lower'])

    def test_source_conflict_is_not_an_average_or_old_value(self):
        r=evaluate([rec(),rec('conflict',value=60),rec('prior','2026-07-01',value=40)])
        self.assertEqual(r['features']['egfr:source_statement_date:status'],'conflicting')
        self.assertIsNone(r['features']['egfr:source_statement_date:lower'])

    def test_medication_uses_pre_index_transaction_state(self):
        paid=rec('paid','2026-08-01',source_table='pharmacy_claims',drug_class='bb',drug='metoprolol',
                 dose='25 mg daily',dispense_id='rx',transaction_type='paid',recorded_date='2026-08-01',
                 fill_date='2026-08-01',days_supply=30,status='dispensed')
        reversed_={**paid,'record_id':'reversal','event_date':'2026-08-25','available_date':'2026-08-25',
                   'recorded_date':'2026-08-25','transaction_type':'reversed'}
        r=evaluate([paid,reversed_])
        self.assertEqual(r['features']['medication:bb:supply_status'],'supply_documented')
        self.assertFalse(r['medications']['bb']['ingestion_established'])
        # An inconsistent early receipt/source stamp must not expose a later
        # adjudication transaction to the baseline supply computation.
        reversed_.update(event_date='2026-08-01',available_date='2026-08-01')
        r=evaluate([paid,reversed_])
        self.assertEqual(r['features']['medication:bb:supply_status'],'supply_documented')
        self.assertEqual(r['record_accounting']['excluded']['adjudication_recorded_on_or_after_index_day'],1)

    def test_original_note_available_but_later_note_and_signature_excluded(self):
        a=rec('n',source_table='notes',text='Assessment: dyspnea')
        b=rec('future','2026-08-22',source_table='notes',text='Assessment: dyspnea')
        c=rec('signature',source_table='notes',text='Signed by: dyspnea')
        r=evaluate([a,b,c],[text_event(a),text_event(b),text_event(c)])
        self.assertEqual(r['features']['narrative:machine_annotations'],1)
        self.assertEqual(len(r['narrative']['excluded']),2)
        self.assertEqual(r['narrative']['authority'],'unvalidated_machine_proposals')

    def test_copy_family_not_counted_as_independent_text(self):
        a=rec('a',source_table='notes',text='Assessment: dyspnea')
        b={**a,'record_id':'b'}
        r=evaluate([a,b],[text_event(a),text_event(b)])
        self.assertEqual(r['features']['narrative:machine_annotations'],2)
        self.assertEqual(r['features']['narrative:distinct_received_text_families'],1)

    def test_fact_with_future_dependency_is_not_partially_trimmed(self):
        a=rec('a',source_table='notes',text='EF 35%');b=rec('b','2026-08-22',source_table='notes',text='EF 35%')
        f={'fact_id':'ef','patient_id':'p','concept':'ef','value':{'lower':35,'upper':35,'unit':'%'},
           'assertion':'affirmed','subject':'patient','temporality':'current','method':'context_rules',
           'evidence':[{'record_id':r,'field':'text','quote':'EF 35%','start':0,'end':6} for r in ('a','b')]}
        r=evaluate([a,b],analyses=[{'patient_id':'p','source_bound_facts':[f]}])
        self.assertEqual(len(r['narrative']['fact_exclusions']),1)
        self.assertEqual(r['measurements']['lvef']['source_statement_date']['all_observations'],[])

    def test_poisson_exposure_clips_partial_month_to_declared_window(self):
        spec=B.definition('received_prior_180d');spec['lookback_days']=10
        r=B.evaluate('p',[index_record(),rec('r','2026-08-15')],[],[],anchored(),spec)
        blocks=r['source_changes']['sources']
        for b in blocks:self.assertEqual(sum(x['calendar_days'] for x in b['bins']),10)
        labs=next(b for b in blocks if b['source']=='labs')
        self.assertEqual(labs['record_counts'],[1]);self.assertEqual(labs['blocks'][0]['records_per_day'],.1)

    def test_tampered_quotes_anchor_and_patient_fail(self):
        a=rec('n',source_table='notes',text='dyspnea');e=text_event(a);e['source']['quote']='different'
        with self.assertRaises(ValueError):evaluate([a],[e])
        with self.assertRaises(ValueError):evaluate([rec(patient_id='other')])
        index=anchored();index['source_evidence'][0]['record_hash']='tampered'
        with self.assertRaises(ValueError):B.evaluate('p',[index_record()],[],[],index,B.definition('received_prior_180d'))

    def test_outcome_endpoints_do_not_cross_anchor_boundary(self):
        e=next(L.record_events(index_record(),ASOF))
        row={'indexed':True,'status':'observed','index':anchored()['index'],'evidence':{e['id']:e,'endpoint':{'secret_future':1}},
             'contract':O.definition('admission_discharge_source_30d'),'recorded_label':1}
        a=B.anchor(row);row['recorded_label']=0;row['evidence']['endpoint']={'secret_future':9}
        self.assertEqual(a,B.anchor(row));self.assertNotIn('recorded_label',a)


class Persistence(unittest.TestCase):
    P={'profile':'received_prior_180d'}
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.meta={'synthetic':True,'source_digest':'fixture','source_run':'fixture','snapshot':{'as_of':ASOF},'reader_model_identity':'test'}
        (self.root/'run.json').write_text(json.dumps(self.meta))
        db=sqlite3.connect(self.root/'stack.sqlite3')
        db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE records(id TEXT,patient TEXT,payload TEXT); CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT);')
        for pid in ('p','q'):
            db.execute('INSERT INTO patients VALUES (?,?)',(pid,json.dumps({'patient_id':pid})))
        for r in [index_record(),rec()]:db.execute('INSERT INTO records VALUES (?,?,?)',(r['record_id'],'p',json.dumps(r)))
        db.commit();db.close()
        self.patch=patch.object(L,'connect',side_effect=self.ledger);self.patch.start()
        e=next(L.record_events(index_record(),ASOF))
        self.op={'identity':'outcome-identity','dependency':self.meta,'contract':O.definition('admission_discharge_source_30d'),
            'patients':{'p','q'},'parameters':{'contract':'admission_discharge_source_30d','index_from':'2026-01-01','index_through':ASOF,'available_by':ASOF,'filter':'all'},
            'index':{'p':[e]},'endpoint':{}}
        O.execute(self.root,['p','q'],self.op['parameters'],prepared=self.op)

    def ledger(self,*args):
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE events(id TEXT,patient TEXT,kind TEXT,payload TEXT)')
        return db,self.meta

    def tearDown(self):self.patch.stop();self.tmp.cleanup()

    def test_complete_results_cache_and_changed_patient_only(self):
        prep=B.prepare(self.root,self.P,self.op);a=B.execute(self.root,['p','q'],self.P,prepared=prep)
        self.assertEqual(a['status_counts'],{'baseline_observations_available':1,'no_resolved_index':1})
        self.assertEqual(B.execute(self.root,['p','q'],self.P,prepared=prep)['processing_counts']['patient_cache_hits'],2)
        db=sqlite3.connect(self.root/'stack.sqlite3');r=rec(value=50)
        db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),'lab'));db.commit();db.close()
        fresh=B.prepare(self.root,self.P,self.op)
        self.assertNotEqual(B.receipt_id(prep,'p'),B.receipt_id(fresh,'p'))
        self.assertEqual(B.receipt_id(prep,'q'),B.receipt_id(fresh,'q'))
        r=B.execute(self.root,['p','q'],self.P,prepared=fresh)
        self.assertEqual(r['processing_counts']['patient_cache_hits'],1)

    def test_snapshot_change_during_uncached_assembly_rejected(self):
        prep=B.prepare(self.root,self.P,self.op);db=sqlite3.connect(self.root/'stack.sqlite3')
        db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(rec(value=80)),'lab'));db.commit();db.close()
        with self.assertRaisesRegex(ValueError,'changed during assembly'):B.execute(self.root,['p'],self.P,prepared=prep)

    def test_empty_selection_does_not_expand(self):
        r=B.execute(self.root,[],self.P,prepared=B.prepare(self.root,self.P,self.op))
        self.assertEqual(r['patients_evaluated'],0)

    def test_feature_target_export_separates_outcomes_and_blocks_fitting(self):
        with patch.object(O,'prepare',return_value=self.op):
            report=B.export_dataset(self.root,['p','q'],self.P,self.op['parameters'],self.root/'export')
        xs=[json.loads(line) for line in (self.root/'export/features.jsonl').read_text().splitlines()]
        ys=[json.loads(line) for line in (self.root/'export/targets.jsonl').read_text().splitlines()]
        self.assertEqual(report['counts']['rows'],2);self.assertFalse(report['fit_authorized'])
        for x,y in zip(xs,ys):
            self.assertEqual(x['baseline_receipt_id'],y['baseline_receipt_id'])
            self.assertNotIn('recorded_label',x);self.assertNotIn('outcome_status',x)
        self.assertEqual(xs[1]['values'],{})


class GraphConnection(unittest.TestCase):
    setUp=graph_tests.GraphExecution.setUp
    tearDown=graph_tests.GraphExecution.tearDown

    def test_baseline_requires_direct_outcome_and_compiles_locally(self):
        for ex in baseline_examples():
            g,route=local_plan(ex['question'],ASOF);self.assertEqual(route,'registered_example')
            self.assertEqual(validate(g,ASOF)['nodes'][-1]['operation'],'baseline')
        with self.assertRaisesRegex(ValueError,'direct upstream outcome'):
            validate(graph_tests.graph([self.base,graph_tests.node('baseline','baseline',['base'],{'profile':'received_prior_180d'})]),ASOF)

    def test_full_selected_set_and_unknown_input_accounting(self):
        g=baseline_examples()[0]['graph']
        def outcome(path,patient_ids,parameters,*,prepared):
            # Store member receipts as the real engine does; the graph binds them to the report.
            db=O.storage(path)
            try:
                with db:
                    for pid in patient_ids:
                        label=None if pid=='p2' else pid in ('p0','p1')
                        row={'id':O.receipt_id(prepared,pid),'patient_id':pid,'status':'unresolved' if label is None else 'event' if label else 'no_event',
                             'recorded_label':label,'indexed':True,'calendar_mature':True}
                        db.execute('INSERT OR REPLACE INTO receipts VALUES(?,?,?)',(row['id'],pid,zlib.compress(json.dumps(row).encode())))
            finally:db.close()
            return {'p0','p1'},{'p2'},{}
        with patch.object(O,'prepare',return_value={'identity':'outcome'}), \
             patch.object(O,'execute',side_effect=outcome), \
             patch.object(B,'prepare',return_value={'identity':'baseline'}), \
             patch.object(B,'execute',return_value={'patients_evaluated':2}) as build:
            r=execute(self.stack,g)
            self.assertEqual(build.call_args.args[1],['p0','p1'])
            self.assertEqual(r['results'][-1]['result']['input_membership']['counts']['unknown'],1)
            self.assertTrue(execute(self.stack,g)['cache_hit'])


if __name__=='__main__':unittest.main()
