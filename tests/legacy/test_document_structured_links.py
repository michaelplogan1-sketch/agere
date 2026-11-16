"""Candidate AGERE_DOCUMENT_STRUCTURED_LINKS: a note is linked to a requested service when its
recorded author is the receiving clinician of the patient's own referral for that specialty."""
import json,sqlite3,unittest
from clinical_core.agere.clinical import document_review as D,typed_document_review as T

Q={'question':'Does any document state that the patient attended a cardiology visit?','relation':'referral_attendance','subject':'patient',
   'entity':{'kind':'service','id':'cardiology_consultation','aliases':['cardiology','cardiologist']}}
TEXT='Interval and examination: Office pressure 118/94 mmHg; pulse 93. Plan: recheck in six weeks.'


def db():
    c=sqlite3.connect(':memory:')
    c.execute('create table record(record_id,patient_id,stream,event_date,payload)')
    def add(rid,pid,stream,day,**payload):c.execute('insert into record values(?,?,?,?,?)',(rid,pid,stream,day,json.dumps(payload)))
    add('REF1','P1','referrals','2029-09-13',specialty='Cardiology',receiving_clinician_id='C7')
    add('REF2','P1','referrals','2031-01-01',specialty='Nephrology',receiving_clinician_id='C9')
    add('REF3','P2','referrals','2029-01-01',specialty='Cardiology',receiving_clinician_id='C8')
    add('N1','P1','notes','2029-09-26',author_id='C7',text=TEXT)
    add('N2','P1','notes','2029-09-27',author_id='C8',text=TEXT)   # C8 is another patient's cardiologist
    add('N3','P1','notes','2029-09-28',text=TEXT)                   # no recorded author
    add('N4','P1','notes','2029-09-29',author_id='C9',text=TEXT)   # referral not yet visible
    return c


class StructuredLinks(unittest.TestCase):
    def test_only_the_patients_own_visible_referral_links_a_note(self):
        links=D.structured_source_links(db(),'P1','2030-05-27')
        self.assertEqual(list(links),['N1'])
        self.assertEqual((links['N1'][0]['label'],links['N1'][0]['referral_record_id'],links['N1'][0]['clinician_id']),('Cardiology','REF1','C7'))

    def test_link_removes_the_binding_refusal_only_for_a_matching_entity(self):
        doc={'record_id':'N1','date':'2029-09-26','text':TEXT}
        self.assertIsNotNone(D.entity_source_link_issue(Q,doc))
        linked={**doc,'structured_source_links':D.structured_source_links(db(),'P1','2030-05-27')['N1']}
        self.assertIsNone(D.entity_source_link_issue(Q,linked))
        other={**Q,'entity':{'kind':'service','id':'nephrology_consultation','aliases':['nephrology']}}
        self.assertIsNotNone(D.entity_source_link_issue(other,linked))

    def test_reader_request_carries_the_link_and_is_unchanged_without_one(self):
        doc={'record_id':'N1','date':'2029-09-26','text':TEXT}
        span={'start':0,'end':len(TEXT),'text':TEXT}
        _,plain,_=T._source_field_request([('f',Q,span,doc)],'2030-05-27')
        self.assertNotIn('structured_source_links',plain['f0']['instructions'])
        link=D.structured_source_links(db(),'P1','2030-05-27')['N1']
        _,named,evidence=T._source_field_request([('f',Q,span,{**doc,'structured_source_links':link})],'2030-05-27')
        self.assertEqual(named['f0']['instructions']['structured_source_links'],link)
        self.assertEqual({k:v for k,v in named['f0']['instructions'].items()if k!='structured_source_links'},plain['f0']['instructions'])
        self.assertNotIn('structured_source_links',evidence[0])


if __name__=='__main__':unittest.main()
