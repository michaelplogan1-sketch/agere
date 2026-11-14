"""Pure parse reuse must never reuse a source/schema acceptance decision."""
import unittest
from unittest.mock import patch
import sqlglot
from clinical_core.agere.clinical import plan_fence as F

class PremiseParseReuse(unittest.TestCase):
 def setUp(self):F._parsed_dependencies.cache_clear()
 def graph(self,sql):return {'nodes':[{'parameters':{'query':sql}}]}

 def test_identical_sql_reuses_only_parsing_and_returns_independent_values(self):
  g=self.graph('SELECT c.patient_id FROM condition c')
  with patch('sqlglot.parse_one',wraps=sqlglot.parse_one)as parse:
   first=F.premises(g);first['tables'].clear();first['columns'].clear()
   second=F.premises(g)
  self.assertEqual(parse.call_count,1)
  self.assertEqual(second['tables'],['condition'])
  self.assertEqual(second['columns'],[('condition','patient_id')])

 def test_current_schema_is_rechecked_after_a_warm_parse(self):
  g=self.graph('SELECT c.patient_id FROM condition c')
  self.assertEqual(F.premise_issues(None,'Inspect source',g,schema={'condition':['patient_id']}),[])
  issues=F.premise_issues(None,'Inspect source',g,schema={'condition':['different_column']})
  self.assertTrue(any('condition.patient_id'in issue for issue in issues))
  self.assertTrue(any('no longer exist'in issue for issue in F.premise_issues(None,'Inspect source',g,schema={})))

 def test_changed_sql_and_current_non_sql_parameters_are_not_inherited(self):
  a=self.graph('SELECT c.patient_id FROM condition c');a['nodes'][0]['parameters']['definition']='prior meaning'
  first=F.premises(a)
  a['nodes'][0]['parameters']['definition']='current meaning'
  self.assertNotEqual(first['text'],F.premises(a)['text'])
  a['nodes'][0]['parameters']['query']='SELECT p.id FROM patient p'
  self.assertEqual(F.premises(a)['tables'],['patient'])

if __name__=='__main__':unittest.main()
