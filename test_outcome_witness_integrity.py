"""Exact event copies preserve received witnesses without inventing clocks/truth."""
from copy import deepcopy
from itertools import permutations
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.contracts import digest
from clinical_core.agere.clinical import outcomes as O, decision_landmarks as D, outcome_readiness as R
from test_longitudinal import event, ASOF
from test_outcomes import parameters, mock_connection
from test_decision_landmarks import params, ev
import test_baseline_evidence as baselines


def copies(*, role='A03', day='2026-08-10', encounter='stay', clinical=False):
    kw={'event':role,'day':day,'encounter_id':encounter}
    if clinical:kw['discharge_date' if role=='A03' else 'admission_date']=day
    known=event(rid='received',**kw)
    unknown=[event(rid='unknown-'+str(i),available_date=None,**kw) for i in range(128)]
    return known,next(e for e in unknown if e['id']<known['id']),next(e for e in unknown if e['id']>known['id'])


def outcome(indices=None, ends=None, spec=None):
    return O.evaluate('p',indices if indices is not None else [event(encounter_id='stay')],ends or [],
                      spec or O.definition('admission_discharge_source_30d'),parameters())


def readiness(row):
    row={**row,'id':digest(row)}
    return R.assess({'patient_id':'p','outcome_receipt_id':row['id'],'id':'baseline',
                    'status':'baseline_observations_available','cutoff':'2026-07-31'},row)


class OutcomeWitnesses(unittest.TestCase):
    def test_native_hash_order_and_permutation_do_not_hide_known_endpoint(self):
        known,lower,higher=copies()
        for unknown in (lower,higher):
            for ends in permutations([known,unknown]):
                with self.subTest(unknown=unknown['id'],order=[e['id'] for e in ends]):
                    before=deepcopy(ends);r=outcome(ends=list(ends))
                    self.assertEqual(r['recorded_label'],1);self.assertEqual(len(r['matches']),1)
                    self.assertEqual(r['matches'][0]['qualifying_witness_ids'],[known['id']])
                    self.assertEqual(set(r['matches'][0]['evidence_ids']),{known['id'],unknown['id']})
                    self.assertEqual(ends,before);self.assertIsNone(r['evidence'][unknown['id']]['arrival_date'])
                    self.assertTrue(readiness(r)['timing_eligible_observed_target'])
                    self.assertFalse(r['model_training_authorized_by_receipt'])

    def test_unknown_only_cannot_become_received(self):
        _,u,_=copies();r=outcome(ends=[u])
        self.assertIsNone(r['recorded_label']);self.assertFalse(readiness(r)['timing_eligible_observed_target'])

    def test_future_only_and_known_plus_future(self):
        known,_,_=copies();future=event(rid='future',event='A03',day='2026-08-10',encounter_id='stay',available_date='2026-10-01')
        self.assertEqual(outcome(ends=[future])['recorded_label'],0)
        r=outcome(ends=[future,known]);self.assertEqual(len(r['matches']),1)
        self.assertEqual(r['matches'][0]['qualifying_witness_ids'],[known['id']])
        self.assertEqual(r['excluded_endpoint_evidence'][0]['reason'],'not_received_by_cutoff')

    def test_two_received_copies_are_one_endpoint(self):
        a,_,_=copies();b=event(rid='received-later',event='A03',day='2026-08-10',encounter_id='stay',available_date='2026-08-11')
        r=outcome(ends=[b,a]);self.assertEqual(len(r['matches']),1)
        self.assertEqual(set(r['matches'][0]['qualifying_witness_ids']),{a['id'],b['id']})

    def test_index_unknown_exact_copy_does_not_change_selected_source_day(self):
        a,u,_=copies(role='A01',day='2026-08-01')
        r=outcome([u,a]);self.assertTrue(r['indexed']);self.assertEqual(r['index']['date'],'2026-08-01')
        self.assertEqual(r['index']['qualifying_witness_ids'],[a['id']]);self.assertTrue(readiness(r)['index_known_by_index_day'])

    def test_unknown_different_index_identity_remains_unresolved(self):
        a,u,_=copies(role='A01',day='2026-08-01');u=deepcopy(u);u['encounter_id']='other'
        self.assertEqual(outcome([a,u])['status'],'unresolved_index_timing')

    def test_index_conflicting_dates_remain_ambiguous(self):
        r=outcome([event(encounter_id='stay'),event(rid='conflict',day='2026-08-02',encounter_id='stay')])
        self.assertEqual(r['status'],'ambiguous_index_identity')

    def test_clinical_clock_exact_copy_preserves_known_witness(self):
        spec={**O.definition('admission_discharge_source_30d'),'basis':'clinical'}
        a=event(encounter_id='stay',admission_date='2026-08-01');k,u,_=copies(clinical=True)
        self.assertEqual(outcome([a],[u,k],spec)['recorded_label'],1)
        self.assertIsNone(outcome([a],[event(rid='missing',event='A03')],spec)['recorded_label'])

    def test_source_encounter_and_same_day_controls(self):
        for kw,reason in [({'organization_id':'other'},'different_source_organization'),({'encounter_id':'other'},'different_encounter')]:
            e=event(rid='wrong',event='A03',day='2026-08-10',**kw);r=outcome(ends=[e])
            self.assertEqual(r['recorded_label'],0);self.assertEqual(r['excluded_endpoint_evidence'][0]['reason'],reason)
        spec={**O.definition('admission_discharge_source_30d'),'min_days':0}
        k,u,_=copies(day='2026-08-01');self.assertIsNone(outcome(ends=[k,u],spec=spec)['recorded_label'])

    def test_unlinked_and_cross_patient_records_do_not_coalesce(self):
        k,u,_=copies(encounter=None);self.assertEqual(len(O.deduplicate([k,u],'source_date')),2)
        wrong=deepcopy(k);wrong['patient_id']='other'
        self.assertEqual(len(O.deduplicate([k,wrong],'source_date')),2)
        with self.assertRaisesRegex(ValueError,'patient'):outcome(ends=[wrong])

    def test_same_record_exclusion_uses_all_index_copies(self):
        a=event(rid='a',encounter_id='stay');b=event(rid='b',encounter_id='stay')
        # Neither arbitrary ID ordering nor a second index copy may hide the join.
        for index in (a,b):
            end=event(rid=index['source']['record_id'],event='A03',day='2026-08-10',encounter_id='stay')
            self.assertEqual(outcome([a,b],[end])['recorded_label'],0)

    def test_prepare_storage_inspection_export_and_readiness(self):
        k,u,_=copies();events=[event(encounter_id='stay'),k,u]
        with tempfile.TemporaryDirectory() as tmp,patch.object(O.ledger,'connect',side_effect=lambda p:mock_connection(events)):
            prep=O.prepare(tmp,parameters());self.assertEqual(len(prep['endpoint']['p']),2)
            yes,unknown,summary=O.execute(tmp,['p'],parameters(),prepared=prep)
            self.assertEqual(yes,{'p'});self.assertFalse(unknown)
            row=O.inspect(tmp,summary['patients'][0]['id']);self.assertEqual(row['matches'][0]['qualifying_witness_ids'],[k['id']])
            self.assertEqual(O.execute(tmp,['p'],parameters())[2]['processing_counts']['patient_cache_hits'],1)
            path=Path(tmp)/'targets.jsonl';O.export_labels(tmp,['p'],parameters(),path)
            target=json.loads(path.read_text());self.assertEqual(target['recorded_label'],1);self.assertIsNone(target['clinical_label'])
            self.assertTrue(readiness(row)['timing_eligible_observed_target']);self.assertFalse(row['clinical_outcome_established'])


class ReceiptWitnesses(unittest.TestCase):
    def assess(self,ends,indices=None):
        return D.evaluate('p',indices if indices is not None else [ev(encounter_id='stay')],ends,D.definitions()[params()['contract']],params())

    def test_unknown_endpoint_copy_cannot_hide_received_future_observation(self):
        k=ev('received','2026-08-23','2026-08-24',event='A03',encounter_id='stay')
        for i in range(16):
            u=ev('unknown-'+str(i),'2026-08-23',None,event='A03',encounter_id='stay')
            for ends in ([k,u],[u,k]):
                r=self.assess(ends);self.assertEqual(r['recorded_label'],1)
                self.assertEqual(r['matches'][0]['qualifying_witness_ids'],[k['id']]);self.assertEqual(len(r['matches']),1)

    def test_unknown_and_future_only_stay_unresolved_or_excluded(self):
        self.assertIsNone(self.assess([ev('u','2026-08-23',None,event='A03')])['recorded_label'])
        self.assertEqual(self.assess([ev('f','2026-08-23','2026-10-01',event='A03')])['recorded_label'],0)

    def test_prior_received_endpoint_vetoes_later_duplicate_for_all_orders(self):
        early=ev('early','2026-08-23','2026-08-20',event='A03',encounter_id='stay')
        late=ev('late','2026-08-23','2026-08-24',event='A03',encounter_id='stay')
        for ends in permutations([early,late]):
            r=self.assess(list(ends));self.assertEqual(r['recorded_label'],0);self.assertFalse(r['matches'])
            self.assertIn('duplicate_of_endpoint_record_available_at_decision',{e['reason'] for e in r['excluded_endpoints']})

    def test_clock_unavailable_prior_record_is_traced_without_inventing_new_receipt(self):
        early=ev('early','2026-08-23','2026-08-20',event='A03',encounter_id='stay')
        early['clinical_date_evidence']=[{'patient_id':'p','arrival_date':'2026-08-25'}]
        late=ev('late','2026-08-23','2026-08-26',event='A03',encounter_id='stay')
        r=self.assess([late,early]);self.assertEqual(r['recorded_label'],0)
        a=next(a for a in r['excluded_endpoints'][0]['copy_assessments'] if a['event_id']==early['id'])
        self.assertFalse(a['clinical_clock_available_at_decision']);self.assertTrue(a['clinical_clock_available_at_evaluation'])

    def test_endpoint_clock_witness_must_be_available_at_evaluation(self):
        k=ev('k','2026-08-23','2026-08-24',event='A03',encounter_id='stay')
        bad=ev('bad','2026-08-23','2026-08-25',event='A03',encounter_id='stay')
        bad['clinical_date_evidence']=[{'patient_id':'p','arrival_date':'2026-10-01'}]
        r=self.assess([bad,k]);self.assertEqual(r['recorded_label'],1);self.assertEqual(r['matches'][0]['qualifying_witness_ids'],[k['id']])
        self.assertIsNone(self.assess([bad])['recorded_label'])

    def test_unknown_index_receipt_still_can_change_decision_day(self):
        k=ev('k',encounter_id='stay');u=ev('u',arrival=None,encounter_id='stay')
        self.assertEqual(self.assess([],[k,u])['status'],'unresolved_index_arrival')

    def test_available_clock_at_first_receipt_can_support_index(self):
        k=ev('k',encounter_id='stay');bad=ev('bad',encounter_id='stay')
        bad['clinical_date_evidence']=[{'patient_id':'p','arrival_date':'2026-08-25'}]
        for indices in ([k,bad],[bad,k]):
            r=self.assess([],indices);self.assertEqual(r['decision']['date'],'2026-08-21')
            self.assertEqual(r['index']['clock_available_witness_ids'],[k['id']])

    def test_late_clock_cannot_move_original_decision(self):
        early=ev('early',encounter_id='stay');early['clinical_date_evidence']=[{'patient_id':'p','arrival_date':'2026-08-25'}]
        later=ev('later',arrival='2026-08-26',encounter_id='stay')
        r=self.assess([],[later,early]);self.assertEqual(r['status'],'index_clinical_clock_unavailable_at_receipt');self.assertIsNone(r['decision'])

    def test_wrong_patient_in_unused_clock_dependency_rejected(self):
        bad=ev('bad',None,'2026-10-01',event='A03');bad['clinical_date_evidence']=[{'patient_id':'other','arrival_date':None}]
        with self.assertRaisesRegex(ValueError,'patient'):self.assess([bad])


class LandmarkPersistence(unittest.TestCase):
    setUp=baselines.Persistence.setUp
    tearDown=baselines.Persistence.tearDown
    ledger=baselines.Persistence.ledger

    def test_storage_reopening_and_export_preserve_actual_witness(self):
        idx={**baselines.index_record(),'source_system':'hospital','organization_id':'h1',
             'encounter_id':'stay','admission_date':'2026-08-10'}
        with sqlite3.connect(self.root/'stack.sqlite3') as db:
            db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(idx),'index'))
        self.op['index']['p']=[next(O.ledger.record_events(idx,ASOF))]
        k=ev('received','2026-08-23','2026-08-24',event='A03',encounter_id='stay')
        u=ev('unknown','2026-08-23',None,event='A03',encounter_id='stay')
        self.op['endpoint']={'p':[k,u]}
        with patch.object(O,'prepare',return_value=self.op):
            prep=D.prepare(self.root,params())
            report=D.execute(self.root,['p'],params(),prepared=prep)
            row=D.inspect(self.root,report['patients'][0]['id'])
            self.assertEqual(row['recorded_label'],1)
            self.assertEqual(row['matches'][0]['qualifying_witness_ids'],[k['id']])
            self.assertFalse(row['clinical_outcome_established'])
            self.assertEqual(D.execute(self.root,['p'],params(),prepared=prep)['processing_counts']['patient_cache_hits'],1)
            manifest=D.export_dataset(self.root,['p'],params(),self.root/'export')
        y=json.loads((self.root/'export/targets.jsonl').read_text())
        x=json.loads((self.root/'export/features.jsonl').read_text())
        self.assertEqual(y['recorded_label'],1);self.assertIsNone(y['clinical_label'])
        self.assertNotIn('recorded_label',x['values']);self.assertFalse(manifest['model_fitting_performed'])
        self.assertFalse(manifest['clinical_prediction_authorized'])

