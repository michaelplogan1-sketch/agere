"""Longitudinal evidence behavior: clocks, provenance, missingness and pairing."""
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import longitudinal as L
from clinical_core.agere.clinical.analysis_graph import validate
from clinical_core.agere.clinical.cohorts import empty

ASOF='2026-09-30'


def record(rid='a',patient='p',event='A01',day='2026-08-01',**extra):
    return {'record_id':rid,'patient_id':patient,'source_table':'adt','source_system':'hospital',
            'organization_id':'h1','event':event,'event_date':day,'available_date':day,**extra}


def event(**kw):return next(L.record_events(record(**kw),ASOF))


def narrative(text='Patient reports hardship. [2026-07-04]',rid='n',**axes):
    r=record(rid=rid,source_table='notes',text=text)
    start=text.index('hardship');end=start+len('hardship')
    p={'source':{**L.source(r),'field':'text','start':start,'end':end,'quote':'hardship'},
       'axes':{'assertion':'affirmed','subject':'patient','time':'current','relation':'clinical_observation',**axes},
       'input':{'concept':'financial hardship'},'model_identity':'unvalidated-reader'}
    return L.annotation_event(rid,'p',p,r,ASOF)


def parameters(basis='source_event',**changes):
    return {'first':{'kind':'record','event_type':'admission'},'second':{'kind':'record','event_type':'discharge'},
            'basis':basis,'available_by':ASOF,'min_days':0,'max_days':30,'same_organization':True,**changes}


def connection(events):
    db=sqlite3.connect(':memory:')
    fields=['kind','stream','event_type','concept','assertion','subject','relation','time',*L.CLOCKS.values()]
    db.execute('CREATE TABLE events(id TEXT,patient TEXT,'+','.join(k+' TEXT' for k in fields)+',payload TEXT)')
    for e in events:
        db.execute('INSERT INTO events VALUES ('+','.join('?' for _ in range(len(fields)+3))+')',
                   [e['id'],e['patient_id'],*[e[k] for k in fields],json.dumps(e)])
    vocab={k:sorted({e[k] for e in events if e[k]}) for k in L.FILTERS}
    return db,{'snapshot':{'as_of':ASOF},'vocabulary':vocab}


class NarrativeBindings(unittest.TestCase):
    def test_reference_is_not_clinical_date_or_document_header(self):
        e=narrative('DOB 1950-01-01\nSigned: 2026-08-01\nPatient reports hardship. [2026-07-04]')
        self.assertEqual(e['reference_date'],'2026-07-04')
        self.assertIsNone(e['clinical_date'])
        self.assertEqual(e['source_date'],'2026-08-01')
        self.assertEqual(narrative('DOB 1950-01-01\nPatient reports hardship.')['reference_status'],'not_observed')

    def test_prior_note_is_scoped_and_not_current_confirmation(self):
        e=narrative('Prior note (2024-12-19):\nPatient reports hardship.\n\nNew account today.')
        self.assertTrue(e['copied_history']);self.assertEqual(e['reference_date'],'2024-12-19')
        e=narrative('Prior note (2024-12-19):\nStable.\n\nPatient reports hardship.')
        self.assertFalse(e['copied_history']);self.assertIsNone(e['reference_date'])

    def test_conflicting_and_future_reference_does_not_fall_back_to_current(self):
        for text in ('hardship [2026-08-01] [2026-07-04]','hardship [2027-01-01]','hardship [2026-02-31]'):
            e=narrative(text)
            self.assertIsNone(e['reference_date'])
            self.assertEqual(L.reconcile_accounts([e])['dated_accounts'],[])

    def test_binding_rejects_wrong_patient_span_and_original_document_text(self):
        r=record(source_table='notes',text='hardship')
        p={'source':{**L.source(r),'field':'text','start':0,'end':8,'quote':'hardship'},
           'axes':{},'input':{'concept':'financial hardship'},'model_identity':'reader'}
        with self.assertRaisesRegex(ValueError,'binding'):L.annotation_event('e','other',p,r,ASOF)
        p['source']['quote']='not hardship'
        with self.assertRaisesRegex(ValueError,'binding'):L.annotation_event('e','p',p,r,ASOF)
        r=record(source_table='documents',original_text='hardship',extracted_text='garbled')
        p['source']={**L.source(r),'field':'original_text','start':0,'end':8,'quote':'hardship'}
        with self.assertRaisesRegex(ValueError,'binding'):L.annotation_event('e','p',p,r,ASOF)

    def test_opposing_reports_preserved_repeats_not_independent_and_requests_excluded(self):
        a=narrative(rid='a');copy=narrative(rid='b');neg=narrative(rid='c',assertion='negated')
        request=narrative(rid='d',relation='record_request')
        r=L.reconcile_accounts([a,copy,neg,request])
        self.assertEqual(r['dated_accounts'][0]['status'],'conflicting_reports')
        self.assertEqual(r['dated_accounts'][0]['text_families'],2)
        self.assertEqual(r['repeated_text_families'][0]['observations'],2)
        self.assertEqual(r['excluded_from_state_evidence_ids'],['d'])
        self.assertFalse(r['probabilities_assigned'])


class EpisodeBindings(unittest.TestCase):
    def test_two_explicit_dates_create_distinct_roles(self):
        events=list(L.record_events(record(admission_date='2026-07-28',discharge_date='2026-08-01'),ASOF))
        self.assertEqual([(e['event_type'],e['clinical_date']) for e in events],
                         [('admission','2026-07-28'),('discharge','2026-08-01')])
        self.assertNotEqual(events[0]['id'],events[1]['id'])

    def test_missing_clinical_dates_not_replaced_by_source_dates(self):
        a=event();d=event(rid='d',event='A03',day='2026-08-05')
        self.assertEqual(L.provisional_episodes([a,d],'source_event')['episodes'][0]['observed_span_days'],4)
        self.assertEqual(L.provisional_episodes([a,d],'clinical')['episodes'][0]['status'],'unresolved_pair')

    def test_no_cross_patient_cross_source_or_ambiguous_pairing(self):
        a=event();d=event(rid='d',event='A03',day='2026-08-05')
        for changes in ({'patient_id':'other'},{'source':{**d['source'],'organization_id':'other'}},
                        {'source':{**d['source'],'source_system':'other'}}):
            self.assertIsNone(L.provisional_episodes([a,{**d,**changes}],'source_event')['episodes'][0]['discharge'])
        other={**d,'id':'duplicate'}
        self.assertIsNone(L.provisional_episodes([a,d,other],'source_event')['episodes'][0]['discharge'])

    def test_explicit_encounter_link_overrides_intervening_other_encounter(self):
        a=event(encounter_id='stay1');b=event(rid='b',day='2026-08-03',encounter_id='stay2')
        d=event(rid='d',event='A03',day='2026-08-05',encounter_id='stay1')
        r=L.provisional_episodes([a,b,d],'source_event')
        self.assertEqual(r['episodes'][0]['status'],'explicit_encounter_link')
        self.assertIsNone(r['episodes'][1]['discharge'])


class PopulationSequences(unittest.TestCase):
    def run_sequence(self,events,ids,p):
        with patch.object(L,'connect',side_effect=lambda path:connection(events)):
            return L.sequence('unused',ids,p)

    def test_full_membership_is_not_limited_by_evidence_preview(self):
        events=[e for i in range(25) for e in (event(rid='a'+str(i),patient=str(i)),
                   event(rid='d'+str(i),patient=str(i),event='A03',day='2026-08-05'))]
        yes,unknown,r=self.run_sequence(events,[str(i) for i in range(25)],parameters())
        self.assertEqual(len(yes),25);self.assertEqual(len(r['patient_examples']),10)
        self.assertEqual(r['observed_pairs'],25);self.assertEqual(unknown,set())

    def test_unresolved_clinical_timing_and_late_arrivals(self):
        events=[event(),event(rid='d',event='A03',day='2026-08-05',available_date='2026-09-15')]
        self.assertEqual(self.run_sequence(events,['p'],parameters('clinical'))[:2],(set(),{'p'}))
        self.assertEqual(self.run_sequence(events,['p'],parameters(available_by='2026-09-01'))[:2],(set(),set()))
        self.assertEqual(self.run_sequence(events,[],parameters())[:2],(set(),set()))

    def test_one_record_not_self_sequence_and_same_day_order_unknown(self):
        a=event();d=event(rid='d',event='A03')
        yes,unknown,r=self.run_sequence([a,d],['p'],parameters())
        self.assertEqual(yes,{'p'});self.assertFalse(r['patient_examples'][0]['examples'][0]['within_day_order_known'])
        self.assertEqual(self.run_sequence([a],['p'],parameters(second={'kind':'record','event_type':'admission'}))[:2],(set(),set()))

    def test_known_outside_window_is_false_even_when_organization_unknown(self):
        a=event(organization_id=None);d=event(rid='d',event='A03',day='2026-09-05')
        self.assertEqual(self.run_sequence([a,d],['p'],parameters())[:2],(set(),set()))
        d=event(rid='d',event='A03',day='2026-08-05')
        self.assertEqual(self.run_sequence([a,d],['p'],parameters())[:2],(set(),{'p'}))

    def test_future_window_unmapped_concept_and_invalid_selector_rejected(self):
        for p in (parameters(available_by='2027-01-01'),parameters(min_days=10,max_days=5),
                  parameters(first={'kind':'record','event_type':'admission','assertion':'affirmed'})):
            with self.assertRaises(ValueError):L.validate_parameters(p,ASOF,'sequence')
        with self.assertRaisesRegex(ValueError,'Unmapped'):
            self.run_sequence([event()],['p'],parameters(first={'kind':'text','concept':'unknown'}))

    def test_context_reports_undated_anchors_without_invented_state(self):
        events=[event(),event(rid='d',event='A03',day='2026-08-05'),narrative()]
        p={'anchor':{'kind':'record','event_type':'admission'},'basis':'clinical','available_by':ASOF,
           'before_days':30,'after_days':30,'concepts':[]}
        with patch.object(L,'connect',side_effect=lambda path:connection(events)):
            r=L.context('unused',['p'],p)
        self.assertEqual(r['summary']['anchors_without_selected_date'],1)
        self.assertEqual(r['summary']['context_observations'],0)
        self.assertEqual(r['patients'][0]['undated_total'],3)
        self.assertFalse(r['patients'][0]['clinical_cause_established'])

    def test_graph_accepts_sequence_as_membership_but_not_context(self):
        def n(ident,op,inputs,p):return {'id':ident,'operation':op,'inputs':inputs,'parameters':p,'purpose':ident}
        nodes=[n('base','cohort',[],{'specification':empty()}),n('sequence','sequence',['base'],parameters()),
               n('both','intersect',['base','sequence'],{})]
        g={'interpretation':'Source chronology','coverage':'supported_observation','unmet_requirements':[],'nodes':nodes}
        self.assertEqual(len(validate(g,ASOF)['nodes']),3)


if __name__=='__main__':unittest.main()
