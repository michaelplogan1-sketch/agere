"""Availability, protected grouping, honest nonfit and local comparison checks."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import outcome_readiness as R, outcome_learning as L
from clinical_core.agere.clinical import baseline_evidence as B
import test_baseline_evidence as fixture


def pair():
    b={'id':'b','patient_id':'p','status':'baseline_observations_available','outcome_receipt_id':'o','cutoff':'2026-01-09'}
    o={'id':'o','patient_id':'p','indexed':True,'calendar_mature':True,'recorded_label':0,
       'mature_recorded_label_usable':True,'index':{'date':'2026-01-10','evidence_ids':['index']},
       'evidence':{'index':{'arrival_date':'2026-01-10'}}}
    return b,o


class TargetTiming(unittest.TestCase):
    def test_zero_means_observation_negative_not_clinical_negative(self):
        b,o=pair();r=R.assess(b,o)
        self.assertTrue(r['timing_eligible_observed_target']);self.assertIsNone(r['clinical_label'])
        self.assertFalse(r['clinical_prediction_authorized']);self.assertEqual(r['capture_status'],'unknown')

    def test_late_index_blocks_even_when_label_known_and_mature(self):
        b,o=pair();o['evidence']['index']['arrival_date']='2026-02-20';o['recorded_label']=1
        r=R.assess(b,o);self.assertFalse(r['timing_eligible_observed_target'])
        self.assertIn('index_not_known_by_index_day',r['exclusion_reasons'])

    def test_later_copy_does_not_hide_timely_original(self):
        b,o=pair();o['index']['evidence_ids'].append('copy');o['evidence']['copy']={'arrival_date':'2026-02-20'}
        self.assertTrue(R.assess(b,o)['index_known_by_index_day'])

    def test_immature_positive_not_selected_by_known_label(self):
        b,o=pair();o.update(recorded_label=1,calendar_mature=False,mature_recorded_label_usable=False)
        r=R.assess(b,o);self.assertFalse(r['timing_eligible_observed_target'])
        self.assertIn('incomplete_calendar_followup',r['exclusion_reasons'])

    def test_missing_clock_unresolved_label_invalid_cutoff_and_joins(self):
        b,o=pair();o['evidence']['index']['arrival_date']=None;o['recorded_label']=None;b['cutoff']='2026-01-10'
        r=R.assess(b,o);self.assertEqual(len(r['exclusion_reasons']),3)
        o['patient_id']='other'
        with self.assertRaises(ValueError):R.assess(b,o)
        o['patient_id']='p';o['id']='changed'
        with self.assertRaises(ValueError):R.assess(b,o)

    def test_single_class_and_missing_index_have_explicit_denominators(self):
        b,o=pair();first=R.assess(b,o);b['status']='no_resolved_index';o.update(indexed=False,index=None,recorded_label=None,calendar_mature=False,mature_recorded_label_usable=False)
        summary=R.summarize([first,R.assess(b,o)])
        self.assertEqual(summary['patients_assessed'],2);self.assertEqual(summary['timing_eligible'],1)
        self.assertFalse(summary['observed_target_has_two_classes'])
        self.assertNotIn('incomplete_calendar_followup',summary['exclusion_counts'])


def learning_fixture():
    xs=[];ys=[];policy={'lineage_source':'fixture','patient_families':{},'family_partitions':{},'additional_protected_families':[]}
    for i in range(240):
        pid='p'+str(i);family='f'+str(i);b,o=pair();b.update(id='b'+pid,patient_id=pid,outcome_receipt_id='o'+pid);o.update(id='o'+pid,patient_id=pid,recorded_label=i%2)
        xs.append({'patient_id':pid,'baseline_receipt_id':b['id'],'values':{'source:count':i%7,'source_blocks:notes:count':i%3,'measurement':None if i%5==0 else i%11,'status':'seen' if i%2 else 'missing'},'feature_cutoff':b['cutoff'],'source_template_families':['template'+pid],'input_record_ids':['r'+pid]})
        ys.append({'patient_id':pid,'baseline_receipt_id':b['id'],'outcome_receipt_id':o['id'],'recorded_label':o['recorded_label'],'clinical_label':None,'qualification':R.assess(b,o)})
        policy['patient_families'][pid]=family;policy['family_partitions'][family]='train'
    return xs,ys,policy,{'patient_ids':[],'family_ids':[]}


class ProtectedLearning(unittest.TestCase):
    def test_outer_and_reserved_families_excluded_before_fitting(self):
        x,y,p,protect=learning_fixture();protect['family_ids']=['f0'];p['family_partitions']['f1']='validation'
        q=L.qualify(x,y,p,protect);included={r['patient_id'] for r in q['rows']}
        self.assertNotIn('p0',included);self.assertNotIn('p1',included)
        self.assertFalse(q['outer_evaluation_used_for_fitting_or_scoring'])

    def test_transitive_template_household_connection_propagates_protection(self):
        x,y,p,protect=learning_fixture();protect['patient_ids']=['p0']
        x[1]['source_template_families']=x[0]['source_template_families'];x[1]['household_groups']=['h'];x[2]['household_groups']=['h']
        a=L.groups(x,p,protect)
        self.assertTrue(all(a['p'+str(i)]['partition'] is None for i in range(3)))

    def test_groups_are_independent_of_target_labels_and_stable_under_reorder(self):
        x,y,p,protect=learning_fixture();a=L.groups(x,p,protect)
        self.assertEqual(a,L.groups(list(reversed(x)),p,protect))
        q=L.qualify(x,y,p,protect)
        for row in y:row['recorded_label']=1-row['recorded_label'];row['qualification']['recorded_label']=row['recorded_label']
        qq=L.qualify(x,y,p,protect)
        self.assertEqual([(r['patient_id'],r['partition']) for r in q['rows']],[(r['patient_id'],r['partition']) for r in qq['rows']])

    def test_late_index_exclusion_and_target_receipt_tampering(self):
        x,y,p,protect=learning_fixture();y[0]['qualification'].update(timing_eligible_observed_target=False,exclusion_reasons=['index_not_known_by_index_day'])
        q=L.qualify(x,y,p,protect);self.assertNotIn('p0',{r['patient_id'] for r in q['rows']})
        y[0]['baseline_receipt_id']='tamper'
        with self.assertRaises(ValueError):L.qualify(x,y,p,protect)

    def test_no_fit_receipt_and_no_weights_for_one_class(self):
        x,y,p,protect=learning_fixture()
        for row in y:row['recorded_label']=0;row['qualification']['recorded_label']=0
        q=L.qualify(x,y,p,protect)
        with tempfile.TemporaryDirectory() as tmp:
            r=L.fit_rows(q,Path(tmp)/'models')
            self.assertEqual(r['status'],'not_fitted');self.assertFalse(list(Path(tmp).rglob('*.pkl')))

    def test_fits_comparison_without_promoting_or_using_ids(self):
        x,y,p,protect=learning_fixture();q=L.qualify(x,y,p,protect)
        self.assertTrue(q['fit_input_contract_ready'])
        with tempfile.TemporaryDirectory() as tmp:
            r=L.fit_rows(q,Path(tmp)/'models')
            self.assertEqual(set(r['models']),set(L.PLAN['candidates']))
            self.assertFalse(r['automatic_promotion']);self.assertFalse(r['clinical_prediction_authorized'])
            self.assertIn('constant_training_prevalence',r)
            self.assertTrue(all(m['training_feature_count']<20 for m in r['models'].values()))
            q['rows'][0]['component']=q['rows'][-1]['component'];q['rows'][0]['partition']='train';q['rows'][-1]['partition']='calibration'
            with self.assertRaisesRegex(ValueError,'cross partitions'):L.fit_rows(q,Path(tmp)/'bad')


class RuntimeIntegration(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Persistence();self.f.setUp();self.addCleanup(self.f.tearDown)

    def test_actual_baseline_receipts_and_export_include_timing_qualification(self):
        f=self.f;prep=B.prepare(f.root,f.P,f.op);r=B.execute(f.root,['p','q'],f.P,prepared=prep)
        self.assertEqual(r['prediction_readiness']['patients_assessed'],2)
        with patch.object(B.outcomes,'prepare',return_value=f.op):
            exported=B.export_dataset(f.root,['p','q'],f.P,f.op['parameters'],f.root/'qualified')
        xs=[json.loads(line) for line in (f.root/'qualified/features.jsonl').read_text().splitlines()]
        ys=[json.loads(line) for line in (f.root/'qualified/targets.jsonl').read_text().splitlines()]
        self.assertEqual(exported['prediction_readiness']['patients_assessed'],2)
        self.assertIn('qualification',ys[0]);self.assertNotIn('recorded_label',xs[0]);self.assertNotIn('qualification',xs[0]['values'])
        self.assertIn('source_template_families',xs[0]);self.assertIn('household_groups',xs[0])

    def test_attachment_rejects_changed_evidence_and_changed_export(self):
        import sqlite3
        f=self.f
        policy={'lineage_source':'fixture','patient_families':{'p':'p','q':'q'},
            'family_partitions':{'p':'train','q':'train'},'additional_protected_families':[]}
        protected={'patient_ids':[],'family_ids':[]}
        with patch.object(B.outcomes,'prepare',return_value=f.op), \
             patch.object(L.learning_queue,'lineage',return_value=(policy,{'policy_sha256':'fixture'})), \
             patch.object(L.semantic_learning,'protections',return_value=protected):
            B.export_dataset(f.root,['p','q'],f.P,f.op['parameters'],f.root/'data')
            L.build(f.root,f.root/'unused',f.root/'data',f.root/'learning')
            self.assertTrue(L.attach(f.root,[f.root/'learning'])['available'])
            db=sqlite3.connect(f.root/'stack.sqlite3')
            try:
                db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(fixture.rec(value=80)),'lab'));db.commit()
                with self.assertRaisesRegex(ValueError,'evidence changed'):L.attachment_status(f.root)
                db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(fixture.rec()),'lab'));db.commit()
            finally:db.close()
            file=f.root/'data/features.jsonl';file.write_text(file.read_text()+'\n')
            with self.assertRaisesRegex(ValueError,'dataset changed'):L.attachment_status(f.root)


if __name__=='__main__':unittest.main()
