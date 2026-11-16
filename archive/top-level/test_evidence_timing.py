import copy
import json
import unittest
from unittest.mock import patch
import test_record_evidence as fixture
import test_need_candidates as candidate_fixture
from test_warehouse_interpretation import record
from clinical_core.agere.clinical import evidence_timing as t


class Timestamps(unittest.TestCase):
    def test_offset_rollover_preserves_both_calendars(self):
        r=t.timestamp('2026-12-31T20:35:00-06:00')
        self.assertEqual(r['represented_date'],'2026-12-31');self.assertEqual(r['utc_date'],'2027-01-01')
        self.assertTrue(r['offset_calendar_rollover']);self.assertEqual(r['explicit_offset_seconds'],-21600)
        self.assertEqual(r['instant_utc'],t.timestamp('2027-01-01T02:35:00Z')['instant_utc'])

    def test_unknown_invalid_naive_and_date_only_are_distinct(self):
        self.assertEqual(t.timestamp(None)['status'],'not_received')
        for value in ['2026-02-30','2026-01-01T12:00:00',False,'tomorrow']:
            self.assertEqual(t.timestamp(value)['status'],'unresolved_or_invalid_offset_timestamp')
        self.assertEqual(t.timestamp('2024-02-29')['status'],'date_only')

    def test_calendar_difference_does_not_resolve_timezone_or_contradiction(self):
        r=record('s','patient_billing_accounts',event_date='2026-12-31',event_time='2027-01-01T02:35:00Z')
        d=t.diagnose(r,'2027-02-01',t.native.index([r]))
        self.assertEqual(d['source_date_timestamp_relation'],'calendar_basis_unresolved')
        self.assertEqual(d['date_timezone_binding'],'not_established');self.assertFalse(d['calendar_difference_is_contradiction'])
        self.assertIsNone(d['financial_clock_key_under_existing_contract'])

    def test_explicit_unknown_precision_cannot_become_an_exact_window_date(self):
        r=record('s','imported_documents',event_date='2026-01-01',event_date_precision='unknown',available_time='2026-01-03T00:00:00Z',signed_time='2026-01-02T14:00:00-05:00')
        d=t.diagnose(r,'2028-09-30',t.native.index([r]))
        self.assertIsNone(d['basis_dates']['source_event']);self.assertIsNone(d['basis_dates']['clinical'])
        self.assertEqual(d['raw_clock_fields']['event_date']['value'],'2026-01-01')
        self.assertEqual(d['other_timestamps']['signed_time']['instant_utc'],'2026-01-02T19:00:00+00:00')


class Timing(unittest.TestCase):
    setUpBase=fixture.RecordContext.setUpBase
    put=fixture.RecordContext.put
    chain=fixture.RecordContext.chain
    create=fixture.RecordContext.create

    def setUp(self):
        fixture.RecordContext.setUp(self)
        self.put(record('old','imported_documents',event_date='2026-01-01',service_date='2025-12-28'),
            record('new','imported_documents',event_date='2026-08-01',service_date='2026-07-30'),
            record('unknown','imported_documents',event_date=None,service_date=None))
        self.context=self.create(['old','new','unknown']);self.request={'kind':'context','id':self.context['id']}

    def prepare(self):return t.prepare(self.path,self.request)
    def window(self,basis='source_event'):
        return t.window(self.path,{'timing_id':self.prepare()['id'],'window':{'basis':basis,'start':'2026-07-01','end':'2026-08-01'}})

    def test_exact_replay_and_bases_preserve_original_context(self):
        r=self.prepare();self.assertFalse(r['source_dates_rewritten']);self.assertTrue(self.prepare()['cache_hit'])
        self.assertEqual(t.source_synthesis.context(self.path,self.context['id']),self.context)
        row=next(v for v in r['records'] if v['record_id']=='old')
        self.assertEqual(row['basis_dates']['source_event'],'2026-01-01');self.assertEqual(row['basis_dates']['clinical'],'2025-12-28')
        self.assertEqual(t.inspect(self.path,r['id']),{k:v for k,v in r.items() if k!='cache_hit'})

    def test_window_distinguishes_inclusive_membership_and_unknown(self):
        w=self.window();self.assertEqual(w['counts'],{'within':1,'outside':1,'unresolved':1})
        self.assertEqual(w,t.inspect_window(self.path,w['id']));self.assertFalse(w['question_answerable'])

    def test_unknown_selection_is_explicit_and_replayable(self):
        w=self.window();s=t.select(self.path,{'window_id':w['id'],'include_unresolved':True})
        self.assertEqual(s['record_scope']['record_ids'],['new','unknown']);self.assertFalse(s['meaning_resolved'])
        self.assertEqual(t.inspect_selection(self.path,s['id']),s)
        excluded=t.select(self.path,{'window_id':w['id'],'include_unresolved':False})
        self.assertEqual(excluded['record_scope']['record_ids'],['new']);self.assertEqual(excluded['unresolved_roots_excluded'],['unknown'])
        self.assertNotEqual(s['id'],excluded['id'])
        with self.assertRaises(ValueError):t.select(self.path,{'window_id':w['id'],'include_unresolved':'false'})

    def test_closure_retains_linked_records_outside_window(self):
        rows,_=t.record_context.received(self.path,'P-1');a=next(r for r in rows if r['record_id']=='a');a['event_date']='2026-07-10';self.put(a)
        self.context=self.create(['a','old']);self.request={'kind':'context','id':self.context['id']}
        w=self.window();s=t.select(self.path,{'window_id':w['id'],'include_unresolved':False})
        self.assertEqual(s['selection']['retrieved_record_ids'],['a']);self.assertEqual(s['record_scope']['record_ids'],['a','b','c','d'])
        self.assertEqual(s['linked_records_outside_selected_roots'],['b','c','d'])

    def test_empty_window_does_not_establish_absence_or_create_context(self):
        w=t.window(self.path,{'timing_id':self.prepare()['id'],'window':{'basis':'clinical','start':'2026-09-01','end':'2026-09-30'}})
        with self.assertRaisesRegex(ValueError,'absent care'):t.select(self.path,{'window_id':w['id'],'include_unresolved':False})
        self.assertFalse((self.path/'evidence-window-contexts').exists())

    def test_invalid_windows_rejected(self):
        for w in [{'basis':'source_event','start':'2026-08-02','end':'2026-08-01'},
            {'basis':'arrival','start':'','end':'2030-01-01'},{'basis':'clinical','start':'','end':''},
            {'basis':'all_observed','start':'','end':''},{'basis':'guessed_timezone','start':'2026-07-01','end':''}]:
            with self.assertRaises(ValueError):t.window(self.path,{'timing_id':self.prepare()['id'],'window':w})

    def test_changed_sources_expire_every_dependent_receipt(self):
        r=self.prepare();w=self.window();s=t.select(self.path,{'window_id':w['id'],'include_unresolved':True})
        self.put(record('new','imported_documents',event_date='2026-06-01',service_date='2026-05-28'))
        for action in [lambda:t.inspect(self.path,r['id']),lambda:t.inspect_window(self.path,w['id']),lambda:t.inspect_selection(self.path,s['id'])]:
            with self.assertRaises(ValueError):action()

    def test_tampered_receipt_and_source_race_rejected(self):
        r=self.prepare();target=t.candidates.location(self.path,r['id'],'evidence-timing');saved=json.loads(target.read_text());saved['summary']['records']=0;target.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError,'integrity'):t.inspect(self.path,r['id'])
        other={'kind':'context','id':self.create(['new'])['id']};original=t.diagnose
        def changed(*args):
            result=original(*args);self.put(record('unknown','imported_documents',service_date='2026-07-01'));return result
        with patch.object(t,'diagnose',side_effect=changed):
            with self.assertRaises(ValueError):t.prepare(self.path,other)


class CandidateTiming(unittest.TestCase):
    setUpBase=candidate_fixture.Candidates.setUpBase
    put=candidate_fixture.Candidates.put
    chain=candidate_fixture.Candidates.chain
    create=candidate_fixture.Candidates.create
    hit=candidate_fixture.Candidates.hit
    def setUp(self):candidate_fixture.Candidates.setUp(self)
    def test_candidate_scope_keeps_exact_original_question_dependency(self):
        candidate=t.candidates.prepare(self.path,self.request,self.retriever)
        r=t.prepare(self.path,{'kind':'candidates','id':candidate['id']})
        self.assertEqual(r['binding']['record_ids'],['a','extra'])
        self.assertEqual(r['binding']['question'],candidate['query']);self.assertFalse(r['meaning_resolved'])
        self.answer['review_steps'][0]['question']='Changed question'
        with self.assertRaises(ValueError):t.inspect(self.path,r['id'])


if __name__=='__main__':unittest.main()
