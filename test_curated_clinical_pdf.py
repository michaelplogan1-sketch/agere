"""Clinical report contracts: attribution, uncertainty and end-user presentation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from pypdf import PdfReader
from test_patient_review_profiles import CurrentNativeCardContracts
from clinical_core.agere.deliverables.patient_report_pdf import render, _note_excerpt


class CuratedClinicalPDF(unittest.TestCase):
 def context(self):
  c=CurrentNativeCardContracts().context()
  c.update(id='authored',question='Review these patients for the theoretical program',source={'as_of':'2030-05-27'})
  c['answer']['facts']={'technical_diagnostic':'SELECT * FROM private_internal_trace; old failed draft'}
  c['patient_profiles']['patients']['A']['latest_measurements']=[{'concept':'potassium','value_canonical':4.7,'unit_canonical':'mmol/L','event_date':'2030-05-20','same_day_values':2,'same_day_min':4.5}]
  return c

 def pdf(self,c):
  with tempfile.TemporaryDirectory()as folder:
   output=Path(folder)/'review.pdf';receipt=render(c,output);pdf=PdfReader(output)
   text=' '.join(' '.join(page.extract_text()for page in pdf.pages).split())
   return text,receipt,dict(pdf.attachments),output.stat().st_size

 def test_reader_gets_named_clinical_facts_and_no_engineering_dump(self):
  c=self.context();before=copy.deepcopy(c);text,r,attachments,size=self.pdf(c)
  self.assertIn('Alex Example',text);self.assertIn('Dr River Example',text);self.assertIn('Dr Lake Example',text)
  self.assertIn('Current assignment not established',text)
  self.assertIn('planned',text);self.assertIn('25 mg/day recorded',text)
  self.assertIn('2028',text);self.assertIn('2 values recorded that day',text);self.assertIn('minimum 4.5',text)
  self.assertIn('Carvedilol was held for dizziness.',text)
  self.assertIn('individual maximum tolerated at the 2028 visit',text)
  self.assertIn('current individual maximum needs confirmation',text)
  self.assertNotIn('SELECT *',text);self.assertNotIn('technical_diagnostic',text)
  self.assertEqual(c,before);self.assertFalse(attachments);self.assertLess(size,300000)
  self.assertEqual(r['new_model_calls'],0);self.assertEqual(r['distinct_patient_ids'],1)

 def test_native_raas_alias_and_declared_window_keep_the_curated_format(self):
  c=self.context();row=c['answer']['table'][0];row['on_raas']=row.pop('raas_pillar')
  row.pop('fill_window_start_exclusive');row.pop('fill_window_end_inclusive')
  c['answer']['grounding']=['Pillars use picked-up fills in the 12 months ending 2030-05-27 (agere_result_42), which replaces an old query.']
  text,r,attachments,_=self.pdf(c)
  self.assertEqual(r['presentation_version'],'curated-clinical-review/1')
  self.assertIn('2 of 4 pillars have a fill observation',text)
  self.assertIn('Fill observed in the saved analysis',text)
  self.assertIn('No fill observed in the saved analysis',text)
  self.assertIn('Declared analysis window: 12 months ending 2030-05-27',text)
  self.assertNotIn('agere_result_',text);self.assertNotIn('old query',text)
  self.assertFalse(attachments)

 def test_dated_named_cardiology_author_does_not_become_current_assignment(self):
  c=self.context();roles=[{'name':'Dr Historical Example','documented_role':'Cardiology','relationship':'signed_correspondence_author','date':'2027-04-05','current_assignment':'not_established'}]
  c['investigation']['document_reviews']=[{'questions':{},'rows':[{'patient_id':'A','source_clinicians_json':json.dumps(roles),'review_json':'{}'}]}]
  text,_,_,_=self.pdf(c)
  self.assertIn('Dr Historical Example',text);self.assertIn('assignment unconfirmed',text)
  self.assertIn('5 Apr 2027',text)

 def test_disputed_reading_remains_visible_as_unresolved(self):
  c=self.context();row=c['answer']['table'][0];findings=json.loads(row['documentary_findings']);findings[0]['assertion']={'id':'disputed'};row['documentary_findings']=json.dumps(findings)
  c['investigation']['answer_reviews']=[{'support':{'claim_feedback':[{'source_assertion_id':'disputed','label':'missing_evidence','decided_by':'jev'}]}}]
  text,_,_,_=self.pdf(c);self.assertIn('Unresolved chart interpretation',text)
  self.assertIn('Carvedilol was held for dizziness.',text)

 def test_excerpt_keeps_supply_denial_and_undocumented_reason_with_named_drug(self):
  quote='Medication discussion: metformin was continued. Carvedilol was stopped by the patient. Denies other supply. The clinical rationale remains undocumented. Ferrous sulfate was renewed.'
  out=_note_excerpt(quote,{'aliases':['carvedilol']})
  self.assertIn('Carvedilol was stopped by the patient.',out);self.assertIn('Denies other supply.',out)
  self.assertIn('clinical rationale remains undocumented',out);self.assertNotIn('Ferrous sulfate',out)
  self.assertNotIn('metformin',out)

 def test_excerpt_preserves_explicit_uncertainty_outside_decision_paragraph(self):
  quote='History: a dated prior account.\nDecision and plan: did not increase carvedilol today.\nThe rationale remains uncertain pending clarification.'
  out=_note_excerpt(quote,{'aliases':['carvedilol']})
  self.assertIn('did not increase carvedilol',out);self.assertIn('uncertain pending clarification',out)

 def test_historical_dose_passages_do_not_become_explanations_or_current_doses(self):
  c=self.context();row=c['answer']['table'][0];findings=json.loads(row['documentary_findings'])
  for day in ('2027-03-01','2028-04-02'):
   findings.append({'field':'dose_note','entity':{'kind':'medication_class','id':'medicines','aliases':['spironolactone']},
    'relation':'documented_dose','polarity':'negated','temporality':'planned','subject':'patient',
    'quote':'History: no spironolactone 25mg tablet was supplied. Dose frequency is unknown. Decision: return for reassessment; no prescribing reason recorded.',
    'record_id':'dose-'+day,'date':day})
  row['documentary_findings']=json.dumps(findings);before=copy.deepcopy(c)
  text,_,attachments,_=self.pdf(c)
  self.assertIn('Product strengths mentioned in dated notes',text)
  self.assertIn('spironolactone 25mg tablet',text)
  self.assertIn('First mentioned 1 Mar 2027; last mentioned 2 Apr 2028',text)
  self.assertIn('not established daily doses, current use, or reasons for non-use',text)
  self.assertIn('source qualifications: negated, planned',text)
  self.assertNotIn('Decision: return for reassessment',text)
  self.assertIn('Carvedilol was held for dizziness.',text)
  self.assertEqual(c,before);self.assertFalse(attachments)

 def test_outside_hf_cohort_uses_general_saved_analysis_renderer(self):
  c=self.context();c['patient_profiles']['patients']['A']['registered_cohort_evidence']=[]
  text,r,attachments,_=self.pdf(c)
  self.assertNotEqual(r.get('presentation_version'),'curated-clinical-review/1')
  self.assertIn('Agere-saved-answer.json.gz',attachments)


if __name__=='__main__':unittest.main()
