"""Patient-facing reports consume reconciled source accounts without voting."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from clinical_core.agere.clinical import document_review as D
from clinical_core.agere.deliverables import patient_evidence as P


class DocumentaryCards(unittest.TestCase):
    def context(self):
        q = {'question': 'What reason does this note document for not using an MRA?',
             'entity': {'kind': 'medication_class', 'id': 'mra', 'aliases': ['MRA']},
             'relation': 'documented_reason_not_on_therapy', 'subject': 'patient'}
        docs = [{'record_id': rid, 'date': '2030-01-01', 'available_date': '2030-01-02', 'text': text}
                for rid, text in [('R1', 'MRA held for hyperkalemia.'), ('R2', 'No MRA intolerance or potassium barrier documented.')]]
        findings = [D.relation_finding('A', 'reason', q, doc,
            {'start': 0, 'end': len(doc['text']), 'text': doc['text']},
            {'polarity': polarity, 'temporality': 'current', 'subject': 'patient'}, 'fixture')
            for doc, polarity in zip(docs, ['affirmed', 'negated'])]
        source_row = D.with_correspondence([{'patient_id': 'A', 'review_json': json.dumps({'reason': findings})}],
            {'A': [docs]}, {'reason': q}, '2030-05-27')[0]
        row = {'patient_id': 'A', 'on_mra': 0, 'on_raas': 0, 'on_beta_blocker': 0, 'on_sglt2i': 0,
               'documentary_findings': source_row['source_findings_json']}
        return {'question': 'Review medication optimization', 'source': {'as_of': '2030-05-27'},
                'answer': {'status': 'partial', 'table': [row]},
                'investigation': {'document_reviews': [{'questions': {'reason': q}, 'rows': [source_row]}]}}

    def test_opposing_dated_state_reaches_patient_card_and_keeps_both_exact_readings(self):
        c = self.context(); before = copy.deepcopy(c)
        projected = P.projection(c)
        self.assertEqual(projected['notes']['A'][0]['documentary_state'],
                         json.loads(c['investigation']['document_reviews'][0]['rows'][0]['documentary_state_json']))
        card = P.review_cards(c, projected)['A']
        self.assertEqual(card['documentary_reconciliation']['groups'][0]['status'], 'same_date_conflict')
        self.assertIn('1 set of chart statements', card['documentary_reconciliation']['summary'])
        reasons = card['therapy'][2]['documented_reasons']
        self.assertEqual(len(reasons), 2)
        self.assertTrue(all('reconciliation' in g['review_status'] for g in reasons))
        self.assertTrue(all(g['documentary_accounts'][0]['dated_accounts'][0]['date'] == '2030-01-01' for g in reasons))
        self.assertFalse(card['documentary_reconciliation']['current_clinical_state_established'])
        self.assertEqual(c, before)

    def test_changed_finding_cannot_borrow_another_assertions_reconciliation(self):
        c = self.context()
        findings = json.loads(c['answer']['table'][0]['documentary_findings'])
        findings[0]['assertion']['value'] = 'Changed interpretation under the old ID'
        c['answer']['table'][0]['documentary_findings'] = json.dumps(findings)
        with self.assertRaisesRegex(ValueError, 'differs from its reconciled source assertion'):
            P.review_cards(c)

    def test_other_patient_state_does_not_qualify_this_patients_source_reading(self):
        c = self.context(); native = c['investigation']['document_reviews'][0]['rows'][0]
        state = json.loads(native['documentary_state_json'])
        for a in state['assertions']:a['patient_id'] = a['source']['patient_id'] = 'B'
        native['documentary_state_json'] = json.dumps(state)
        card = P.review_cards(c)['A']
        self.assertEqual(card['documentary_reconciliation']['groups'], [])
        self.assertFalse(any(g.get('documentary_accounts') for g in card['therapy'][2]['documented_reasons']))

    def test_pdf_shows_the_reconciliation_problem_without_printing_assertion_hashes(self):
        from pypdf import PdfReader
        from clinical_core.agere.deliverables.patient_report_pdf import render
        c = self.context()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'review.pdf'; render(c, target)
            text = ' '.join(' '.join(page.extract_text() or '' for page in PdfReader(target).pages).split())
        self.assertIn('1 set of chart statements differs', text)
        self.assertIn('MRA held for hyperkalemia.', text)
        assertion_id = json.loads(c['investigation']['document_reviews'][0]['rows'][0]['documentary_state_json'])['assertions'][0]['id']
        self.assertNotIn(assertion_id, text)


class OpenQuestionPresentation(unittest.TestCase):
    def context(self, two_questions=False):
        from test_patient_review_profiles import CurrentNativeCardContracts
        c=CurrentNativeCardContracts().context()
        c.update(question='Review dated medication findings',source={'as_of':'2030-05-27'})
        q={'question':'What reason was recorded for leaving the MRA dose unchanged at that visit?',
           'entity':{'kind':'medication_class','id':'mra','aliases':['MRA']},
           'relation':'authored_dose_limit','subject':'patient'}
        questions={'limit':q}
        if two_questions:questions['historical']={**q,'question':'What reason was recorded before the most recent visit?'}
        doc={'record_id':'CUSTOM','date':'2029-01-01','text':'MRA dose unchanged because the patient could not afford the refill.'}
        found={field:[D.relation_finding('A',field,question,doc,
            {'start':0,'end':len(doc['text']),'text':doc['text']},
            {'polarity':'affirmed','temporality':'historical','subject':'patient'},'fixture')]
            for field,question in questions.items()}
        row=D.with_correspondence([{'patient_id':'A','review_json':json.dumps(found)}],
            {'A':[[doc]]},questions,'2030-05-27')[0]
        c['answer']['table'][0]['documentary_findings']=row['source_findings_json']
        c['investigation']={'document_reviews':[{'questions':questions,'rows':[row]}]}
        return c

    def test_new_relation_and_different_question_qualifiers_remain_visible_without_aliasing(self):
        c=self.context(two_questions=True);before=copy.deepcopy(c)
        card=P.review_cards(c)['A'];groups=card['additional_documentary_accounts']
        self.assertEqual(len(groups),2)
        self.assertEqual({g['relation']for g in groups},{'authored_dose_limit'})
        self.assertEqual(len({g['source_questions'][0]['question']for g in groups}),2)
        self.assertFalse(card['dose_limiting_statements'])
        self.assertFalse(any(t['documented_reasons']for t in card['therapy']))
        self.assertEqual(c,before)
        from pypdf import PdfReader
        from clinical_core.agere.deliverables.patient_report_pdf import render
        with tempfile.TemporaryDirectory()as tmp:
            target=Path(tmp)/'review.pdf';render(c,target)
            text=' '.join(' '.join(p.extract_text()or''for p in PdfReader(target).pages).split())
        self.assertIn('MRA dose unchanged because the patient could not afford the refill.',text)
        self.assertIn('before the most recent visit?',text)
        self.assertIn('at that visit?',text)
        self.assertNotIn('No explicit non-use or titration explanation was returned',text)

    def test_replaced_audit_reading_is_not_reintroduced_as_an_additional_account(self):
        from clinical_core.contracts import digest
        c=self.context();old=c['investigation']['document_reviews'][0]
        c['investigation']['document_reviews'].append({'questions':{},'rows':[],
            'candidate_observation_review':{'source_rows_digest':digest(old['rows']),'checked_fields':['limit']}})
        self.assertFalse(P.review_cards(c)['A']['additional_documentary_accounts'])

    def test_local_recheck_retires_only_named_assertion_without_losing_other_fields_or_patients(self):
        c=self.context(two_questions=True);old=c['investigation']['document_reviews'][0]
        q=old['questions']['limit'];original=json.loads(old['rows'][0]['review_json'])['limit'][0]
        doc={'record_id':'NEIGHBOR','date':'2029-02-01','text':'MRA dose unchanged because the patient could not afford the refill.'}
        neighbor=D.relation_finding('A','limit',q,doc,{'start':0,'end':len(doc['text']),'text':doc['text']},
            {'polarity':'affirmed','temporality':'historical','subject':'patient'},'fixture')
        found=json.loads(old['rows'][0]['review_json']);found['limit'].append(neighbor)
        old['rows'][0]['review_json']=json.dumps(found)
        other=copy.deepcopy(old['rows'][0]);other['patient_id']='B';old['rows'].append(other)
        revised=copy.deepcopy(original);revised['assertion']['id']='new-machine-reading'
        newrow={'patient_id':'A','review_json':json.dumps({'limit':[revised]})}
        c['investigation']['document_reviews'].append({'questions':{'limit':q},'rows':[newrow],
            'candidate_observation_review':{'source_rows_digest':'local-subset','checked_fields':['limit'],
                'checked_assertions':[{'patient_id':'A','field':'limit','assertion_id':original['assertion']['id']}]}})
        before=copy.deepcopy(c);p=P.projection(c)
        self.assertEqual(p['notes']['A'][0]['findings']['limit'],[neighbor])
        self.assertEqual(p['notes']['A'][0]['prior_findings']['limit'],[original])
        self.assertEqual(len(p['notes']['A'][0]['findings']['historical']),1)
        self.assertEqual(len(p['notes']['B'][0]['findings']['limit']),2)
        card=P.review_cards(c,p)
        ids={(f.get('assertion')or{}).get('id')for g in card['A']['additional_documentary_accounts']for f in g['findings']}
        self.assertNotIn(original['assertion']['id'],ids);self.assertIn('new-machine-reading',ids)
        self.assertEqual(c,before)

    def test_latest_patient_support_verdict_is_local_and_does_not_certify_clinical_state(self):
        c=self.context();c['investigation']['answer_reviews']=[{'support':{'packets':[
            {'id':'patient_A','label':'unknown','decided_by':None},
            {'id':'patient_B','label':'supported','decided_by':'jev'}]}}]
        card=P.review_cards(c)['A'];self.assertEqual(card['source_verification']['label'],'unknown')
        self.assertFalse(card['documentary_reconciliation']['current_clinical_state_established'])
        c['investigation']['answer_reviews'].append({'support':{'packets':[
            {'id':'patient_A','label':'supported','decided_by':'perplexity'}]}})
        self.assertEqual(P.review_cards(c)['A']['source_verification']['label'],'supported')
        self.assertFalse(P.review_cards(c)['A']['documentary_reconciliation']['current_clinical_state_established'])

    def test_partitioned_source_verdicts_aggregate_by_their_declared_patient_parent(self):
        c=self.context();c['investigation']['answer_reviews']=[{'support':{'all_dependencies_checked':True,'packets':[
            {'id':'patient_A__sources_0','parent_packet_id':'patient_A','label':'unknown','decided_by':None},
            {'id':'patient_A__sources_1','parent_packet_id':'patient_A','label':'supported','decided_by':'jev'},
            {'id':'patient_B__sources_0','parent_packet_id':'patient_B','label':'not_established','decided_by':'jev'}]}}]
        card=P.review_cards(c)['A'];self.assertEqual(card['source_verification']['label'],'unknown')
        self.assertEqual(len(card['source_verification']['packets']),2)
        c['investigation']['answer_reviews'].append({'support':{'all_dependencies_checked':False,'packets':[
            {'id':'patient_A__sources_1','parent_packet_id':'patient_A','label':'supported','decided_by':'jev'}]}})
        self.assertEqual(P.review_cards(c)['A']['source_verification']['label'],'unknown')
        c['investigation']['answer_reviews'].append({'support':{'all_dependencies_checked':True,'packets':[
            {'id':'patient_A__sources_0','parent_packet_id':'patient_A','label':'supported','decided_by':'jev'},
            {'id':'patient_A__sources_1','parent_packet_id':'patient_A','label':'supported','decided_by':'jev'}]}})
        self.assertEqual(P.review_cards(c)['A']['source_verification']['label'],'supported')

    def test_historical_deferred_child_is_not_mistaken_for_supported_parent(self):
        c=self.context();c['investigation']['answer_reviews']=[{'support':{
            'checked_source_packets':1,'unchecked_packet_ids':['patient_A__sources_0'],'all_dependencies_checked':False,
            'packets':[{'id':'patient_A','label':'supported','decided_by':'native_source_dependency_closure'},
                       {'id':'patient_A__sources_0','label':'unknown','decided_by':None,'status':'awaiting_dependency_repair'}]}}]
        verification=P.review_cards(c)['A']['source_verification']
        self.assertEqual(verification['label'],'unknown');self.assertIsNone(verification['decided_by'])
        self.assertEqual(verification['status'],'awaiting_dependency_repair');self.assertEqual(len(verification['packets']),2)
        self.assertIn('pending',verification['notice'])
        c['investigation']['answer_reviews'].append({'support':{'packets':[
            {'id':'patient_A','label':'supported','decided_by':'native_source_dependency_closure'}]}})
        self.assertEqual(P.review_cards(c)['A']['source_verification']['label'],'unknown')


if __name__ == '__main__':
    unittest.main()
