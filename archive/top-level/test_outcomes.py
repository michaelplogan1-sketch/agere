"""Outcome denominator, chronology, identity and label-boundary correctness."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import outcomes as O
from clinical_core.agere.clinical.analysis_graph import validate, Membership, compare_populations
from clinical_core.agere.clinical.graph_workbench import outcome_examples
from clinical_core.agere.clinical.economical_planner import local_plan
from test_longitudinal import event, connection, ASOF


def parameters(**kw):
    return {'contract':'admission_discharge_source_30d','index_from':'2026-01-01',
            'index_through':ASOF,'available_by':ASOF,'filter':'observed',**kw}


def evaluate(aa=None, bb=None, *, spec=None, p=None):
    return O.evaluate('p',aa if aa is not None else [event()],bb or [],
                      spec or O.definition('admission_discharge_source_30d'),p or parameters())


def mock_connection(events):
    db,meta=connection(events)
    db.execute('CREATE TABLE patients(id TEXT PRIMARY KEY)')
    for pid in {'p','empty'} | {e['patient_id'] for e in events}: db.execute('INSERT INTO patients VALUES (?)',(pid,))
    meta.update(source_digest='test',code_identity='test')
    for key,values in {'kind':['record'],'stream':['adt'],'event_type':['admission','discharge']}.items():
        meta['vocabulary'][key]=sorted(set(meta['vocabulary'][key]+values))
    return db,meta


class OutcomeBoundaries(unittest.TestCase):
    def test_no_index_is_unknown_not_negative(self):
        r=evaluate([])
        self.assertIsNone(r['recorded_label']);self.assertFalse(r['indexed'])
        self.assertIsNone(O.membership(r,'observed'));self.assertTrue(O.membership(r,'unresolved'))
        self.assertFalse(O.membership(r,'indexed'))

    def test_mature_recorded_zero_does_not_become_clinical_negative(self):
        r=evaluate()
        self.assertEqual(r['status'],'not_observed_mature');self.assertEqual(r['recorded_label'],0)
        self.assertEqual(r['coverage']['status'],'unknown');self.assertFalse(r['clinical_negative_established'])
        self.assertTrue(r['mature_recorded_label_usable'])

    def test_incomplete_followup_not_negative(self):
        r=evaluate([event(day='2026-09-20')])
        self.assertEqual(r['status'],'incomplete_followup');self.assertIsNone(r['recorded_label'])

    def test_early_positive_retained_but_not_fixed_horizon_training_eligible(self):
        r=evaluate([event(day='2026-09-20')],[event(rid='d',event='A03',day='2026-09-25')])
        self.assertEqual(r['recorded_label'],1);self.assertFalse(r['calendar_mature'])
        self.assertFalse(r['mature_recorded_label_usable'])
        self.assertFalse(O.membership(r,'mature_labeled'))

    def test_inclusive_day_boundaries(self):
        for day,label in [('2026-08-01',0),('2026-08-02',1),('2026-08-31',1),('2026-09-01',0)]:
            r=evaluate(bb=[event(rid='d',event='A03',day=day)])
            self.assertEqual(r['recorded_label'],label,day)

    def test_same_day_endpoint_does_not_invent_order_when_in_window(self):
        spec={**O.definition('admission_discharge_source_30d'),'min_days':0}
        r=evaluate(bb=[event(rid='d',event='A03')],spec=spec)
        self.assertIsNone(r['recorded_label'])
        self.assertEqual(r['unresolved_endpoint_evidence'][0]['reason'],'within_day_order_unknown')

    def test_clinical_clock_never_falls_back_to_source_clock(self):
        spec=O.definition('discharge_admission_clinical_30d')
        r=evaluate([event(event='A03')],[event(rid='next',day='2026-08-10')],spec=spec)
        self.assertEqual(r['status'],'unresolved_index_timing');self.assertIsNone(r['indexed'])

    def test_explicit_clinical_timing_can_differ_from_notification_timing(self):
        spec={**O.definition('admission_discharge_source_30d'),'basis':'clinical'}
        r=evaluate([event(day='2026-08-20',admission_date='2026-07-01')],
                   [event(rid='d',event='A03',day='2026-08-21',discharge_date='2026-08-10')],spec=spec)
        self.assertEqual(r['recorded_label'],0)

    def test_one_undated_index_prevents_fabricated_first_index(self):
        spec={**O.definition('admission_discharge_source_30d'),'basis':'clinical'}
        r=evaluate([event(admission_date='2026-08-01'),event(rid='b')],spec=spec)
        self.assertEqual(r['status'],'unresolved_index_timing')

    def test_tied_unidentified_indices_are_ambiguous(self):
        r=evaluate([event(),event(rid='second')])
        self.assertEqual(r['status'],'ambiguous_index_identity')

    def test_duplicate_identified_index_preserves_all_sources(self):
        r=evaluate([event(encounter_id='stay'),event(rid='copy',encounter_id='stay')])
        self.assertTrue(r['indexed']);self.assertEqual(len(r['index']['evidence_ids']),2)

    def test_conflicting_dates_for_identified_index_unresolved(self):
        r=evaluate([event(encounter_id='stay'),event(rid='other',day='2026-08-02',encounter_id='stay')])
        self.assertEqual(r['status'],'ambiguous_index_identity')

    def test_index_selected_before_endpoint_matching(self):
        r=evaluate([event(day='2026-07-01'),event(rid='a2',day='2026-08-01')],
                   [event(rid='d',event='A03',day='2026-08-10')])
        self.assertEqual(r['index']['date'],'2026-07-01');self.assertEqual(r['recorded_label'],0)

    def test_source_and_encounter_constraints(self):
        a=event(encounter_id='stay')
        for kw,reason in [({'organization_id':'other'},'different_source_organization'),
                          ({'encounter_id':'other'},'different_encounter')]:
            r=evaluate([a],[event(rid='d',event='A03',day='2026-08-10',**kw)])
            self.assertEqual(r['recorded_label'],0);self.assertEqual(r['excluded_endpoint_evidence'][0]['reason'],reason)
        r=evaluate([a],[event(rid='d',event='A03',day='2026-08-10',organization_id=None)])
        self.assertIsNone(r['recorded_label'])

    def test_repeat_admission_cannot_be_same_identified_encounter(self):
        spec=O.definition('discharge_admission_source_30d')
        a=event(event='A03',encounter_id='stay')
        b=event(rid='new',day='2026-08-10',encounter_id='stay')
        self.assertEqual(evaluate([a],[b],spec=spec)['recorded_label'],0)
        b['source']['source_system']='different-system'
        self.assertEqual(evaluate([a],[b],spec=spec)['recorded_label'],1)

    def test_missing_encounter_identity_remains_provisional(self):
        r=evaluate(bb=[event(rid='d',event='A03',day='2026-08-10')])
        self.assertEqual(r['matches'][0]['link'],'provisional_observation_link')
        self.assertFalse(r['matches'][0]['verified_episode'])

    def test_undated_endpoint_prevents_zero_but_not_observed_positive(self):
        spec={**O.definition('admission_discharge_source_30d'),'basis':'clinical'}
        a=event(admission_date='2026-08-01');d=event(rid='d',event='A03')
        self.assertIsNone(evaluate([a],[d],spec=spec)['recorded_label'])
        d2=event(rid='d2',event='A03',discharge_date='2026-08-10')
        self.assertEqual(evaluate([a],[d,d2],spec=spec)['recorded_label'],1)

    def test_same_record_cannot_be_both_sides(self):
        a=event();b=event(event='A03',day='2026-08-10')
        self.assertEqual(evaluate([a],[b])['recorded_label'],0)

    def test_distinct_explicit_clinical_roles_in_one_record_can_link(self):
        from test_longitudinal import record
        events=list(O.ledger.record_events(record(admission_date='2026-07-28',discharge_date='2026-08-01',encounter_id='stay'),ASOF))
        spec={**O.definition('admission_discharge_source_30d'),'basis':'clinical'}
        r=evaluate([events[0]],[events[1]],spec=spec)
        self.assertEqual(r['recorded_label'],1)
        self.assertEqual(r['matches'][0]['days_after_index'],4)

    def test_wrong_patient_rejected(self):
        with self.assertRaisesRegex(ValueError,'patient'):
            evaluate(bb=[event(rid='d',patient='someone_else')])

    def test_late_arrival_and_future_date_cannot_produce_positive(self):
        for b in [event(rid='d',event='A03',day='2026-08-10',available_date='2026-10-01'),
                  event(rid='d',event='A03',day='2026-09-15')]:
            p=parameters(index_through='2026-08-31',available_by='2026-08-31')
            self.assertEqual(evaluate(bb=[b],p=p)['recorded_label'],0)

    def test_unknown_arrival_prevents_unsupported_label(self):
        a=event();a['arrival_date']=None
        self.assertIsNone(evaluate([a])['recorded_label'])
        b=event(rid='d',event='A03',day='2026-08-10');b['arrival_date']=None
        self.assertIsNone(evaluate(bb=[b])['recorded_label'])


class OutcomeIntegration(unittest.TestCase):
    def test_definitions_and_ui_examples_compile_without_model(self):
        for row in outcome_examples():
            graph,route=local_plan(row['question'],ASOF)
            self.assertEqual(graph,validate(row['graph'],ASOF));self.assertEqual(route,'registered_example')
        for p in [parameters(contract='invented'),parameters(index_from='2026-02-30'),
                  parameters(available_by='2027-01-01'),parameters(filter='no_care')]:
            with self.assertRaises(ValueError): O.validate(p,ASOF)

    def test_unreviewed_text_cannot_become_outcome_label(self):
        s={k:v for k,v in O.definition('admission_discharge_source_30d').items() if k not in ('hash','authority')}
        s['endpoint']={'kind':'text','concept':'outside care'}
        with self.assertRaisesRegex(ValueError,'narrative'): O.validate_definition(s)

    def test_full_membership_cache_inspection_and_candidate_export(self):
        events=[event(),event(rid='d',event='A03',day='2026-08-05')]
        with tempfile.TemporaryDirectory() as tmp, patch.object(O.ledger,'connect',side_effect=lambda p:mock_connection(events)):
            yes,unknown,r=O.execute(tmp,['p','empty'],parameters(),preview_limit=1)
            self.assertEqual(yes,{'p'});self.assertEqual(unknown,{'empty'})
            self.assertEqual(r['patients_evaluated'],2);self.assertEqual(len(r['patients']),1)
            self.assertEqual(O.inspect(tmp,r['patients'][0]['id'])['recorded_label'],1)
            second=O.execute(tmp,['p','empty'],parameters())[2]
            self.assertEqual(second['processing_counts'],{'patient_cache_hits':2})
            target=Path(tmp)/'labels.jsonl';report=O.export_labels(tmp,['p','empty'],parameters(),target)
            self.assertEqual(report['rows'],2);self.assertFalse(report['model_fitted'])
            labels=[json.loads(x) for x in target.read_text().splitlines()]
            self.assertIsNone(labels[0]['recorded_label']);self.assertIsNone(labels[1]['clinical_label'])
            with self.assertRaisesRegex(ValueError,'Fresh'): O.export_labels(tmp,['p'],parameters(),target)

    def test_changed_sources_invalidate_receipt(self):
        events=[event(),event(rid='d',event='A03',day='2026-08-05')]
        with tempfile.TemporaryDirectory() as tmp, patch.object(O.ledger,'connect',side_effect=lambda p:mock_connection(events)):
            row=O.execute(tmp,['p'],parameters())[2]['patients'][0]
            events[1]['source_date']='2026-09-05'
            with self.assertRaisesRegex(ValueError,'changed'): O.inspect(tmp,row['id'])

    def test_label_definition_has_filter_independent_identity(self):
        events=[event(),event(rid='d',event='A03')]
        with patch.object(O.ledger,'connect',side_effect=lambda p:mock_connection(events)):
            self.assertEqual(O.prepare('unused',parameters())['identity'],O.prepare('unused',parameters(filter='all'))['identity'])

    def test_comparison_excludes_unknown_labels_and_nonindexed_people(self):
        u={'positive','zero','incomplete','no_index'}
        base=Membership(u,{'positive','zero','incomplete'},set())
        endpoint=Membership(u,{'positive'},{'incomplete','no_index'})
        group=Membership(u,{'positive'},set())
        r=compare_populations(base,endpoint,[('financial',group)])['comparisons'][0]
        self.assertEqual(r['patients_evaluated'],2);self.assertEqual(r['excluded_unknown_endpoint_or_group'],1)


if __name__=='__main__':unittest.main()
