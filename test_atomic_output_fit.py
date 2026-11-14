"""A closed-choice operator must not execute missing arithmetic or grouping."""
import unittest
import os
from unittest.mock import patch
from clinical_core.agere.clinical import atomic_questions as Q,proposers as P,system_one_gate as G
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
import test_cascade_time_bound as cascade_fixture
import test_atomic_questions as atomic_fixture

FINANCIAL='For patients outside the registered HFrEF cohort, show the number of patients and received claim adjudication rows, total net payer-paid claim dollars by service year, and payer cash transactions and dollars separately by payment year. Keep the two financial lifecycles distinct. Create a saved financial review.'

class AtomicFit(unittest.TestCase):
 def test_compound_event_population_declines_before_models_or_chart_reads(self):
  questions=[
   'For patients with recorded COPD evidence and a 2029 respiratory encounter, distinguish medication intention, medication reported taken, pharmacy dispensing and explicit reported non-use. Show dated source-linked findings and the evidence needed to resolve disagreements without treating an order as a fill.',
   'Which patients discharged in 2029 are intolerant to statins?',
   'How many patients with a 2028 appointment report transportation barriers?',
   'Among members enrolled in 2029, which have documented cost barriers?']
  for question in questions:
   st=atomic_fixture.Stack(['P-1'])
   with self.subTest(question=question),patch.object(Q,'local_core')as local,patch.object(Q,'hosted_core')as hosted,patch.object(Q,'execute')as execute:
    self.assertIsNotNone(Q.population_contract_issue(question))
    self.assertIsNone(Q.tier(st,question))
    self.assertIsNone(Q.plan(st,question))
   self.assertEqual(st.retriever.searches,0)
   local.assert_not_called();hosted.assert_not_called();execute.assert_not_called()
 def test_unqualified_model_atom_planning_is_not_an_automatic_preflight(self):
  st=atomic_fixture.Stack(['P-1'])
  with patch.dict(os.environ,{'AGERE_ATOMIC_MODEL_PLANNER':'off','AGERE_ATOMIC_HOSTED_PLANNER':'off'}),patch.object(Q,'local_core')as local,patch.object(Q,'hosted_core')as hosted,patch.object(Q,'execute')as execute:
   self.assertIsNone(Q.tier(st,'Tally the falls among heart failure folks'))
  local.assert_not_called();hosted.assert_not_called();execute.assert_not_called()
  self.assertEqual(st.retriever.searches,0)
 def test_model_planning_remains_available_for_explicit_qualification(self):
  st=atomic_fixture.Stack(['P-1'])
  with patch.dict(os.environ,{'AGERE_ATOMIC_MODEL_PLANNER':'on','AGERE_ATOMIC_HOSTED_PLANNER':'off'}),patch.object(Q,'local_core',return_value=None)as local:
   self.assertIsNone(Q.tier(st,'Tally the falls among heart failure folks'))
  local.assert_called_once()
 def test_empty_population_never_expands_to_all_patients(self):
  st=atomic_fixture.Stack(['P-1'])
  with patch.object(Q,'local_core')as local,patch.object(Q,'hosted_core')as hosted,patch.object(Q,'execute')as execute:
   self.assertIsNone(Q.tier(st,atomic_fixture.GDMT_Q,{'patient_ids':[]}))
  local.assert_not_called();hosted.assert_not_called();execute.assert_not_called()
  self.assertEqual(st.retriever.searches,0)
 def test_single_condition_labels_and_dated_status_still_fit_the_population_primitive(self):
  for question in [atomic_fixture.GDMT_Q,atomic_fixture.BB_Q,
   'Which patients have documented cost barriers?',
   'For HFrEF patients, show medication status and dated encounter sources.',
   'How has statin use by the patients changed over time?']:
   with self.subTest(question=question):self.assertIsNone(Q.population_contract_issue(question))
 def test_actual_financial_request_never_reaches_planning_retrieval_or_decision(self):
  c=cascade_fixture.cascade(FINANCIAL)
  with patch.object(Q,'local_core')as local,patch.object(Q,'hosted_core')as hosted,patch.object(Q,'execute')as execute,patch.object(G,'check_plan',return_value={'label':'covers_all','p':.86,'reject':False,'calls':1,'cost_usd':.001})as judge:
   self.assertIsNone(c.tier_atomic_system_one())
   self.assertIsNone(Q.plan(c.stack,FINANCIAL))
   self.assertIn('grouping',Q.output_contract_issue(FINANCIAL))
  local.assert_not_called();hosted.assert_not_called();execute.assert_not_called();judge.assert_not_called()
 def test_other_units_and_groupings_decline_without_question_specific_terms(self):
  for question in ['What is the average A1c for patients with diabetes?',
    'What is the median number of wait days?',
    'Show total paid dollars for each patient',
    'How many patients on beta blockers by year?',
    'For each patient, show counts by year',
    'Show medication-status counts by clinic',
    'Which patients report missed statin use by clinic?',
    'What is medical PMPM?']:
   with self.subTest(question=question):self.assertIsNotNone(Q.output_contract_issue(question))
  for question in ['Show the number of patients and total net payer-paid dollars.',
    'Compare total paid dollars.','Show the number of patients and average wait days.',
    'What is the sum of charges?']:
   with self.subTest(question=question):self.assertIsNotNone(Q.output_contract_issue(question))
  # These require calendar buckets or per-pillar aggregate rows, not the
  # supported per-document dated status sequence or per-patient label columns.
  for question in ['For each patient on a statin, how did adherence change month by month?',
    'How many patients are on each GDMT pillar?']:
   self.assertIsNotNone(Q.output_contract_issue(question))
 def test_supported_counts_shares_patient_rows_and_cost_predicates_remain_available(self):
  for question in [atomic_fixture.GDMT_Q,atomic_fixture.BB_Q,
    'How many patients are taking a statin?',
    'What share of patients are taking a statin?',
    'What percentage of patients are taking a statin?',
    'What is the total number of patients taking a statin?',
    'Show one row per patient with medication status',
    'How has statin use by the patients changed over time?',
    'List each patient and the four GDMT pillars they are on',
    'Which patients have documented cost barriers?',
    "What is each patient's heart-rate control status?",
    'What is the cost-barrier status for each patient?',
    'Does the chart support that P-1007 has an elevated heart rate?',
    'Does the chart document that P-1007 declined a $100 copay?']:
   with self.subTest(question=question):self.assertIsNone(Q.output_contract_issue(question))
 def test_unavailable_optional_plan_review_yields_before_expensive_execution(self):
  c=cascade_fixture.cascade();plan={'planner':'qwen_typed_interpreter','scope':{'sources':[]}}
  with patch.object(Q,'plan',return_value=plan),patch.object(Q,'graph_of',return_value=cascade_fixture.GOOD),patch('clinical_core.agere.clinical.analysis_graph.validate',return_value=cascade_fixture.GOOD),patch.object(G,'check_plan',side_effect=TimeoutError('authored unavailable review')),patch.object(Q,'execute')as execute:
   with self.assertRaisesRegex(QuestionNotUnderstood,'coverage unavailable'):c.tier_atomic_system_one()
  execute.assert_not_called()
 def test_unconfirmed_or_incomplete_coverage_never_starts_population_reading(self):
  for label,provider in [('covers_all',None),('unknown',None),('covers_part','jev'),('not_run',None)]:
   c=cascade_fixture.cascade();plan={'planner':'qwen_typed_interpreter','scope':{'sources':[]}}
   review={'label':label,'provider':provider,'p':.86,'reject':False,'calls':0,'cost_usd':0}
   with self.subTest(label=label),patch.object(Q,'plan',return_value=plan),patch.object(Q,'graph_of',return_value=cascade_fixture.GOOD),patch('clinical_core.agere.clinical.analysis_graph.validate',return_value=cascade_fixture.GOOD),patch.object(G,'check_plan',return_value=review),patch.object(Q,'execute')as execute:
    with self.assertRaisesRegex(QuestionNotUnderstood,'not confirmed'):c.tier_atomic_system_one()
   execute.assert_not_called()
 def test_deterministic_plan_preserves_offline_or_unavailable_review_behavior(self):
  report={'report':{'cells':[],'system_one':{'errors':0,'estimated_usd':0,'new_paid_api_calls':0,'decided_by':{},'decisions':0},'population_patients':0,'evaluated_patient_ids':[],'capped':False}}
  for review in [TimeoutError('authored unavailable review'),{'label':'not_run','calls':0,'cost_usd':0,'reject':False}]:
   c=cascade_fixture.cascade();plan={'planner':'deterministic','scope':{'sources':[]}}
   with patch.object(Q,'plan',return_value=plan),patch.object(Q,'graph_of',return_value=cascade_fixture.GOOD),patch('clinical_core.agere.clinical.analysis_graph.validate',return_value=cascade_fixture.GOOD),patch.object(G,'check_plan',side_effect=review if isinstance(review,Exception)else None,return_value=review),patch.object(Q,'execute',return_value=report)as execute,patch.object(c,'graph',return_value=None):
    c.tier_atomic_system_one()
   execute.assert_called_once()

if __name__=='__main__':unittest.main()
