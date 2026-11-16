import unittest
from clinical_core.agere.clinical import reader_target_dates as D


class TargetDateTests(unittest.TestCase):
    def packet(self,text='7.2 on 2027-01-28',source='2027-08-16'):
        return {'text':text,'target_start':0,'target_end':len(text),'target_quote':text,'source_date':source,'literal_reference_date':None}
    def target(self,text='7.2 on 2027-01-28'):
        return {'field':'text','start':789,'end':789+len(text)}
    def test_exact_date_and_relative_source_day(self):
        r=D.audit(self.packet(),self.target());self.assertEqual(r['dates'][0],{'quote':'2027-01-28','field':'text','start':796,'end':806,'date':'2027-01-28','days_relative_to_source':-200,'relative_to_source':'before_source_date'})
        self.assertFalse(r['event_date_established']);self.assertFalse(r['model_labels_changed'])
    def test_unknown_source_does_not_infer_cutoff(self):
        r=D.audit(self.packet(source=None),self.target());self.assertIsNone(r['dates'][0]['days_relative_to_source']);self.assertEqual(r['dates'][0]['relative_to_source'],'unknown_source_date')
    def test_invalid_source_date(self):
        self.assertEqual(D.audit(self.packet(source='2027-02-30'),self.target())['dates'][0]['relative_to_source'],'unknown_source_date')
    def test_same_and_future(self):
        for source,label in [('2027-01-28','same_source_date'),('2026-01-01','after_source_date')]:
            self.assertEqual(D.audit(self.packet(source=source),self.target())['dates'][0]['relative_to_source'],label)
    def test_date_outside_target_not_transferred(self):
        p=self.packet('2027-01-28 current report');p.update(target_start=11,target_quote='current report')
        r=D.audit(p,{'field':'text','start':11,'end':len(p['text'])});self.assertEqual(r['dates'],[])
    def test_invalid_literal_retained(self):
        text='on 2027-02-30';r=D.audit(self.packet(text),self.target(text));self.assertEqual(r['dates'],[]);self.assertEqual(len(r['invalid_literals']),1)
    def test_leap_day(self):
        text='on 2028-02-29';self.assertEqual(D.audit(self.packet(text),self.target(text))['dates'][0]['date'],'2028-02-29')
    def test_multiple_dates_remain_distinct(self):
        text='2027-01-28 to 2027-05-14';self.assertEqual(len(D.audit(self.packet(text),self.target(text))['dates']),2)
    def test_larger_digit_sequence_not_partial_date(self):
        text='12027-01-280';self.assertEqual(D.audit(self.packet(text),self.target(text))['dates'],[])
    def test_changed_quote_rejected(self):
        p=self.packet();p['target_quote']='changed'
        with self.assertRaises(ValueError):D.audit(p,self.target())
    def test_changed_offsets_rejected(self):
        target=self.target();target['end']+=1
        with self.assertRaises(ValueError):D.audit(self.packet(),target)
    def test_other_formats_remain_unassessed(self):
        text='January 28, 2027 yesterday';self.assertEqual(D.audit(self.packet(text),self.target(text))['dates'],[])


if __name__=='__main__':unittest.main()
