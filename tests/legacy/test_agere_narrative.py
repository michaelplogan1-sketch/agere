"""Synthetic adversarial source-context and end-to-end retrieval regression cases."""
import json
from pathlib import Path
import tempfile
import unittest
from clinical_core.agere.narrative import read, review_actions
from clinical_core.agere.narrative_sources import text_fields, retrieval_notes
from clinical_core.agere.operators import patient_analysis
from clinical_core.agere.evidence_index import build, search


def record(text='', **kw):
    return {'record_id':'r','patient_id':'p','source_table':'notes','event_date':'2026-09-01',
            'available_date':'2026-09-02','identity_status':'matched','text':text,**kw}

def facts(text):
    return patient_analysis({'patient_id':'p'},[record(text)],'2026-09-30',[])


class NarrativeContextTest(unittest.TestCase):
    def test_retracted_ef_not_reduced_ef_patient(self):
        a=facts('Prior EF 30% was entered in error; current EF 60%.')
        self.assertEqual([e['value'] for e in a['clinical']['ef_candidates']],[60])
        self.assertNotIn('reduced_ef_documentation',[f['kind'] for f in a['flags']])
        self.assertEqual(a['clinical']['narrative']['mentions'][0]['assertion'],'retracted')
    def test_relative_ef_not_patient(self):
        a=facts('The patient reports that her brother has EF 25%; her EF is 60%.')
        self.assertEqual([e['value'] for e in a['clinical']['ef_candidates']],[60])
        self.assertEqual(a['clinical']['narrative']['mentions'][0]['subject'],'other_person')
    def test_family_heading_carries_but_assessment_resets(self):
        a=facts('Family history:\nEF 25%.\nAssessment:\nCurrent EF is 65%.')
        self.assertEqual([e['value'] for e in a['clinical']['ef_candidates']],[65])
    def test_local_negation_does_not_leak(self):
        a=facts('No cost barrier; patient cannot afford the new prescription.')
        ms=a['clinical']['narrative']['mentions']
        self.assertEqual([m['assertion'] for m in ms],['negated','affirmed'])
        self.assertEqual(len(a['narrative_review_actions']),1)
        self.assertFalse(a['narrative_review_actions'][0]['current_need_established'])
    def test_internal_negation_is_a_positive_barrier(self):
        a=facts('The patient has no transportation and cannot afford the prescription.')
        self.assertEqual({x['kind'] for x in a['narrative_review_actions']},{'verify_transport_access','verify_financial_support_need'})
    def test_hypothetical_and_uncertain_not_confirmed(self):
        for text in ('If EF 30%, consider a new plan.','Cannot exclude EF 30%.','Possible EF 30%.'):
            with self.subTest(text=text):
                self.assertEqual(facts(text)['clinical']['ef_candidates'],[])
    def test_no_ef_inference_from_unresolved_pronoun(self):
        self.assertEqual(facts('She has EF 30%.')['clinical']['ef_candidates'],[])
    def test_range_must_be_entirely_low_for_low_screen(self):
        self.assertNotIn('reduced_ef_documentation',[x['kind'] for x in facts('EF is 35-45%.')['flags']])
        self.assertIn('reduced_ef_documentation',[x['kind'] for x in facts('LVEF was 30–35%.')['flags']])
    def test_decimal_ef_and_invalid_values(self):
        self.assertEqual(facts('Ejection fraction estimated at 39.5%.')['clinical']['ef_candidates'][0]['value'],39.5)
        self.assertEqual(facts('EF 120%. EF 50-30%.')['clinical']['ef_candidates'],[])
    def test_history_preserved_not_current(self):
        out=facts('Prior EF 30%; current EF is 60%.')['clinical']['ef_candidates']
        self.assertEqual([m['temporality'] for m in out],['historical','current_statement'])
    def test_resolved_barrier_not_active_proposal(self):
        self.assertEqual(facts('Cost barrier has been resolved.')['narrative_review_actions'],[])
    def test_narrative_screen_false_and_unknown_not_affirmative(self):
        for value in ('False', 'No', 'Unknown', 'not assessed'):
            with self.subTest(value=value):
                a=facts('Difficulty paying medical bills reported: '+value+'.')
                self.assertEqual(a['narrative_review_actions'],[])
        self.assertEqual(len(facts('Difficulty paying medical bills reported: True.')['narrative_review_actions']),1)
    def test_negative_different_field_does_not_negate_previous(self):
        a=facts('Difficulty paying: True; transportation problem: False.')
        self.assertEqual(len(a['narrative_review_actions']),1)
    def test_source_caveats_and_coverage_changes_reach_retrieval(self):
        rows=[record('',source_table='medication_history',coverage_caveat='Contributing sources only.'),
              record('',record_id='c',source_table='coverage',change_context='Prescription benefit administrator changed.')]
        self.assertEqual({n['source_field'] for n in retrieval_notes('p',rows)},{'coverage_caveat','change_context'})
    def test_exact_quotes_offsets_nested_fields(self):
        r=record('',assessment=[{'text':'The patient cannot afford therapy.'}])
        m=read([r])['mentions'][0];source=r['assessment'][0]['text']
        self.assertEqual(m['evidence']['field'],'assessment[0].text')
        self.assertEqual(source[m['start']:m['end']],m['quote'])
        self.assertEqual(source[m['context_start']:m['context_end']],m['context'])
    def test_copies_retain_provenance_not_independent_votes(self):
        rows=[record('Patient cannot afford therapy.',record_id=str(i)) for i in range(3)]
        out=read(rows);self.assertEqual(len(out['mentions']),1)
        self.assertEqual(out['mentions'][0]['repeat_count'],3)
        self.assertIsNone(out['mentions'][0]['independent_confirmation_count'])
    def test_referral_and_order_free_text_reach_reader(self):
        rows=[record('',source_table='referrals',indication='Patient cannot afford therapy.'),
              record('',record_id='m',source_table='medication_orders',order_context='Medication held because of intolerance.')]
        out=read(rows)
        self.assertEqual({m['evidence']['field'] for m in out['mentions']},{'indication','order_context'})
        self.assertEqual(len(review_actions(out)),2)
    def test_document_original_and_metadata_never_read(self):
        r=record('EF 25%',source_table='documents',original_text='EF 30%',target='cannot afford',extracted_text='EF 60%')
        self.assertEqual([p['text'] for p in text_fields(r)],['EF 60%'])
        self.assertEqual([m['value'] for m in read([r])['mentions']],[60])
    def test_unread_and_unmapped_sources_are_visible(self):
        r=record('',source_table='documents',original_text='EF 30%',extracted_text='',unknown_summary='something')
        out=read([r]);self.assertEqual(out['mentions'],[])
        self.assertEqual(out['coverage']['documents_without_received_text'],['r'])
        self.assertIn('documents.unknown_summary',out['coverage']['unmapped_string_fields'])
    def test_free_text_does_not_override_structured_financial(self):
        a=patient_analysis({'patient_id':'p'},[record('Cannot afford medication.'),
          record('',record_id='f',source_table='financial',difficulty_paying_medical_bills=False)],'2026-09-30',[])
        self.assertIs(a['clinical']['facts']['difficulty_paying'],False)
        self.assertEqual(a['narrative_review_actions'][0]['kind'],'verify_financial_support_need')
        self.assertFalse(a['care_gap_from_absence'])
        self.assertTrue(a['narrative_review_actions'][0]['reconciliation_required'])
        self.assertFalse(a['narrative_review_actions'][0]['structured_counterevidence']['difficulty_paying'])
    def test_retrieval_has_each_field_and_source_identity(self):
        notes=retrieval_notes('p',[record('Review today.',plan='Request outside records.')])
        self.assertEqual(len(notes),2);self.assertEqual(len({n['note_id'] for n in notes}),2)
        self.assertEqual({n['source_field'] for n in notes},{'text','plan'})
        with self.assertRaises(ValueError):retrieval_notes('other',[record()])


class NarrativeIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'evidence.sqlite3'
        self.snapshot={'as_of':'2026-09-30','release':'synthetic-test'}
    def tearDown(self):self.tmp.cleanup()
    def patients(self,rows):return {'p':{'patient':{'patient_id':'p'},'records':rows}}
    def test_buried_free_text_retrieval_and_offsets(self):
        txt=('Routine boilerplate. '*1600)+'Patient cannot afford dapagliflozin.'+(' Routine text.'*200)
        r=record('',source_table='referrals',indication=txt)
        build(self.path,self.patients([r]),self.snapshot)
        out=search(self.path,'dapagliflozin',patient_id='p')
        e=out['evidence'][0];self.assertIn('cannot afford dapagliflozin',e['quote'])
        self.assertEqual(e['quote'],txt[e['start']:e['end']]);self.assertEqual(e['field'],'indication')
        self.assertFalse(out['question_fully_answered']);self.assertIsNone(out['cohort_count'])
    def test_no_cross_patient_or_hidden_text(self):
        p=self.patients([record('Patient cannot afford therapy.')])
        p['q']={'records':[record('',patient_id='q',record_id='q',source_table='documents',original_text='hiddenword',extracted_text='received')],'patient':{'patient_id':'q'}}
        build(self.path,p,self.snapshot)
        self.assertEqual(search(self.path,'afford',patient_id='q')['evidence'],[])
        self.assertEqual(search(self.path,'hiddenword')['evidence'],[])
    def test_future_identity_inputs_rejected(self):
        for changes in ({'available_date':'2026-10-01'},{'identity_status':'mismatched'}):
            with self.subTest(changes=changes):
                path=self.path.with_name(str(len(changes))+next(iter(changes))+'.db')
                with self.assertRaises(ValueError):build(path,self.patients([record(**changes)]),self.snapshot)
    def test_query_operators_not_executed_and_empty_bounded(self):
        build(self.path,self.patients([record('Patient cannot afford therapy.')]),self.snapshot)
        self.assertEqual(search(self.path,'" OR * NOT ( ) --')['evidence'],[])
        for q in ('','a'*2001):
            with self.assertRaises(ValueError):search(self.path,q)
    def test_snapshot_immutable(self):
        build(self.path,self.patients([record('text')]),self.snapshot)
        with self.assertRaises(ValueError):build(self.path,self.patients([record('changed')]),self.snapshot)

if __name__=='__main__':unittest.main()
