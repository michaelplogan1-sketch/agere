"""A request-local documentation lookup is not a disappearing source table."""
import sqlite3,tempfile,unittest
from pathlib import Path
from clinical_core.agere.clinical import plan_fence as F,analyst_context as A

class DocumentationPremise(unittest.TestCase):
 def test_engine_catalog_aliases_keep_their_actual_column_contract(self):
  with tempfile.TemporaryDirectory()as temp:
   path=Path(temp)
   with sqlite3.connect(path/'semantic.sqlite3')as db:
    db.execute('CREATE TABLE patient(patient_id)')
    catalogs={name:[c[1]for c in db.execute('PRAGMA table_info("'+name+'")')]
              for name in ('sqlite_master','sqlite_schema','sqlite_temp_master','sqlite_temp_schema')}
   schema=F._schema(path)
   for name,columns in catalogs.items():
    with self.subTest(catalog=name):
     self.assertTrue(columns)
     self.assertEqual(schema.get(name),columns)
     query='SELECT m.name,m.type,m.sql FROM '+name+' m'
     self.assertEqual(F.premise_issues(path,'Inspect source schema',{'nodes':[{'parameters':{'query':query}}]},schema=schema),[])

 def test_catalog_inspection_still_rejects_invented_columns_and_sources(self):
  with tempfile.TemporaryDirectory()as temp:
   path=Path(temp)
   with sqlite3.connect(path/'semantic.sqlite3')as db:db.execute('CREATE TABLE patient(patient_id)')
   schema=F._schema(path)
   graph=lambda q:{'nodes':[{'parameters':{'query':q}}]}
   self.assertTrue(any('sqlite_master.invented'in issue for issue in F.premise_issues(
    path,'Inspect source schema',graph('SELECT m.invented FROM sqlite_master m'),schema=schema)))
   for name in ('sqlite_master_extra','sqlite_arbitrary','missing_patient'):
    with self.subTest(source=name):
     self.assertTrue(any('no longer exist'in issue for issue in F.premise_issues(
      path,'Inspect source schema',graph('SELECT * FROM '+name),schema=schema)))

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
