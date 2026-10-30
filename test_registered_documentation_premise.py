"""A request-local documentation lookup is not a disappearing source table."""
import sqlite3,tempfile,unittest
from pathlib import Path
from clinical_core.agere.clinical import plan_fence as F,analyst_context as A

class DocumentationPremise(unittest.TestCase):
 def test_registered_documentation_contract_survives_a_fresh_connection(self):
  with tempfile.TemporaryDirectory()as temp:
   path=Path(temp)
   with sqlite3.connect(path/'semantic.sqlite3')as db:db.execute('CREATE TABLE patient(patient_id)')
   schema=F._schema(path)
   graph=lambda q:{'nodes':[{'operation':'sql','parameters':{'query':q}}]}
   self.assertEqual(schema[A.VIEW],list(A.COLUMNS))
   self.assertEqual(F.premise_issues(path,'Inspect the data model',graph('SELECT d.section_id,d.documentation_text FROM '+A.VIEW+' d'),schema=schema),[])
   self.assertTrue(any('column'in i for i in F.premise_issues(path,'Inspect',graph('SELECT d.invented FROM '+A.VIEW+' d'),schema=schema)))
   self.assertTrue(any('no longer exist'in i for i in F.premise_issues(path,'Inspect',graph('SELECT * FROM agere_result_999'),schema=schema)))
   self.assertTrue(any('no longer exist'in i for i in F.premise_issues(path,'Inspect',graph('SELECT * FROM absent_patient_table'),schema=schema)))

if __name__=='__main__':unittest.main()
