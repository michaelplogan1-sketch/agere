import unittest
from clinical_core.agere.clinical import source_scope as S,literal_check as L

class Scope(unittest.TestCase):
 def test_actual_served_year_as_data_segment_regression(self):
  q='How much did the plan pay for pharmacy claims in 2029, in dollars?'
  sql="SELECT SUM(t.plan_paid_cents)/100.0 FROM payer_pharmacy_claim t JOIN patient p ON p.patient_id=t.patient_id WHERE p.data_segment='2029' AND t.fill_date>='2029-01-01' AND t.fill_date<'2030-01-01'"
  self.assertIsNotNone(S.problem(q,sql));self.assertTrue(any('partition'in s for s in L.missing(q,sql)))
 def test_calendar_and_clinical_filters_are_available(self):
  self.assertIsNone(S.problem('Find diabetes patients in 2029',"SELECT * FROM patient p JOIN condition c USING(patient_id) WHERE c.code='E11' AND c.event_date>='2029-01-01'"))
 def test_explicit_coverage_grouping_and_filter_are_available(self):
  for q in ['Compare data segments','Find claims-only patients','Count the deep chart sample']:
   self.assertIsNone(S.problem(q,"SELECT COUNT(*)FROM patient WHERE data_segment='claims_only'"))
 def test_grouping_without_narrowing_is_not_blocked(self):
  self.assertIsNone(S.problem('Show patient source coverage',"SELECT data_segment,COUNT(*)FROM patient GROUP BY data_segment"))
 def test_cte_alias_does_not_hide_unasked_source_restriction(self):
  sql="WITH a AS(SELECT patient_id,data_segment AS bucket FROM patient)SELECT COUNT(*)FROM a WHERE bucket='2029'"
  self.assertIsNotNone(S.problem('Count patients in 2029',sql))
 def test_star_subquery_keeps_physical_role(self):
  self.assertIsNotNone(S.problem('Count patients',"SELECT COUNT(*) FROM(SELECT * FROM patient) p WHERE p.data_segment='deep_chart'"))
 def test_constant_with_same_alias_is_not_a_source_partition(self):
  self.assertIsNone(S.problem('Count the result',"WITH a AS(SELECT 'ok' AS data_segment)SELECT COUNT(*)FROM a WHERE data_segment='ok'"))
 def test_same_text_in_comment_or_string_is_not_a_column_read(self):
  self.assertIsNone(S.problem('Count patients',"SELECT COUNT(*) FROM patient WHERE patient_id='data_segment' -- data_segment='2029'"))
 def test_ignore_all_source_segments_does_not_authorize_narrowing(self):
  self.assertIsNotNone(S.problem('Total spend regardless of data segment',"SELECT SUM(cost)FROM patient WHERE data_segment='deep_chart'"))
 def test_conditional_numerator_source_filter_is_not_silent(self):
  self.assertIsNotNone(S.problem('Total pharmacy cost',"SELECT SUM(CASE WHEN data_segment='deep_chart'THEN cost ELSE 0 END)FROM patient"))
 def test_join_source_restriction_is_checked(self):
  self.assertIsNotNone(S.problem('Total cost',"SELECT SUM(c.cost)FROM claims c JOIN patient p ON c.patient_id=p.patient_id AND p.data_segment='2029'"))

class OwnerScope(unittest.TestCase):
 def test_versioned_duplicate_chart_exclusion_is_retained(self):
  from clinical_core.agere.clinical.decision_context import COUNTED_PREDICATE
  for sql in ["SELECT COUNT(*)FROM patient WHERE data_segment<>'duplicate_chart'",'SELECT COUNT(*)FROM patient WHERE patient_id '+COUNTED_PREDICATE]:
   self.assertIsNone(S.problem('How many patients?',sql))
 def test_positive_duplicate_selection_needs_requested_partition(self):
  self.assertIsNotNone(S.problem('How many patients?',"SELECT COUNT(*)FROM patient WHERE data_segment='duplicate_chart'"))
 def test_negating_an_exclusion_does_not_authorize_positive_source_subset(self):
  self.assertIsNotNone(S.problem('How many patients?',"SELECT COUNT(*)FROM patient WHERE NOT(data_segment<>'duplicate_chart')"))

class ExtraPartition(unittest.TestCase):
 def test_owner_exclusion_cannot_hide_additional_unasked_source_partition(self):
  self.assertIsNotNone(S.problem('How many patients?',"SELECT COUNT(*)FROM patient WHERE data_segment NOT IN('duplicate_chart','claims_only')"))
 def test_partition_transform_is_not_the_owner_duplicate_rule(self):
  self.assertIsNotNone(S.problem('How many patients?',"SELECT COUNT(*)FROM patient WHERE data_segment||'x'<>'duplicate_chart'"))

if __name__=='__main__':unittest.main()
