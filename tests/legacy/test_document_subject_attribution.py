"""Alternative actors cannot establish one particular person's preference."""
import os,threading,unittest
from types import SimpleNamespace
from unittest.mock import patch,Mock
from clinical_core.agere.clinical import document_review as D,typed_document_review as T,span_repair as S
from clinical_core.contracts import digest


class SubjectAttribution(unittest.TestCase):
    def setUp(self):
        self.q={'question':'What payment preference does the patient report?',
            'entity':{'kind':'topic','id':'medical_finances','aliases':['payment']},
            'relation':'reported_preference','subject':'patient'}
        self.axes={'subject':'patient','polarity':'affirmed','temporality':'current'}
        self.tools=SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(allow_live=False))

    def doc(self,text):return {'record_id':'r','date':'2029-01-01','text':text}
    def span(self,text):return {'start':0,'end':len(text),'text':text}

    def test_alternative_actors_remain_unknown_across_wording_and_question_domains(self):
        for actor in ['Patient or guardian','the guardian or the patient','patient/guardian','patient and/or their caregiver']:
            text=actor+' accepted the payment estimate.'
            with self.subTest(actor=actor),self.assertRaisesRegex(ValueError,'alternative actors'):
                D.relation_finding('P','preference',self.q,self.doc(text),self.span(text),self.axes,'fixture')
        q={**self.q,'question':'Who agreed to the appointment?','entity':{'kind':'service','id':'visit','aliases':['visit']},'relation':'agreed_to_visit'}
        text='Patient or guardian agreed to the visit.'
        with self.assertRaisesRegex(ValueError,'alternative actors'):
            D.relation_finding('P','consent',q,self.doc(text),self.span(text),self.axes,'fixture')

    def test_conjunction_and_independent_patient_statement_remain_distinct(self):
        for text in ['Patient and guardian accepted payment.','The patient prefers one payment.']:
            self.assertIsNone(D.subject_source_link_issue(self.q,self.span(text)))
        text='Patient or guardian was contacted. The patient prefers one payment.'
        quote='The patient prefers one payment.';start=text.index(quote)
        found=D.relation_finding('P','preference',self.q,self.doc(text),{'start':start,'end':len(text),'text':quote},self.axes,'fixture')
        self.assertEqual(found['quote'],quote)

    def test_typed_reader_never_sends_an_unresolvable_actor_to_models(self):
        doc=self.doc('Patient or guardian accepted payment.')
        judge=Mock(side_effect=AssertionError('Unchanged actor disjunction needs source clarification, not model rereading'))
        with patch.dict(os.environ,{'AGERE_DOCUMENT_FIELD_SCREEN':'candidate'}):
            out=T.read(self.tools,'P',[doc],{'preference':self.q},None,judge=judge,ranking=False)
        judge.assert_not_called();self.assertEqual(out['findings']['preference'],[])
        self.assertEqual(out['unresolved'][0]['native_binding_issue'],'source_subject_ambiguous')
        self.assertEqual(out['unresolved'][0]['reading']['label'],'unknown')
        self.assertEqual(out['coverage'][0]['model_field_decisions'],0)

    def test_direct_frontier_repair_preserves_source_gap_without_dispatch(self):
        doc=self.doc('Patient or guardian accepted payment.');span=self.span(doc['text'])
        unresolved=[{'field':'preference','record_id':'r','document_text_digest':digest(doc['text']),
            'quote':doc['text'],'span_start':0,'span_end':len(doc['text'])}]
        send=Mock(side_effect=AssertionError('No paid reclassification of unresolvable actor'))
        out=S.repair(self.tools,'P',unresolved,[doc],{'preference':self.q},send,threading.Event(),[])
        send.assert_not_called();self.assertEqual(out['findings']['preference'],[])
        self.assertEqual(out['receipt']['resolved_blocks'],0)
        self.assertEqual(out['receipt']['unresolved_blocks'],1)
        self.assertEqual(out['receipt']['decisions'][0]['label'],'unknown')


if __name__=='__main__':unittest.main()
