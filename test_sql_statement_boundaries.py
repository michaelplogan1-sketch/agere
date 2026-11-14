import sqlite3,unittest
from clinical_core.agere.clinical import sql_query as S

class Statements(unittest.TestCase):
 def test_source_punctuation_is_not_an_extra_statement(self):
  for sql in ["SELECT 'Dose unchanged; cost barrier.' AS note","SELECT 'O''Malley; follow-up' AS clinician", "SELECT json_object('quote','continue; review') AS finding", "SELECT 1 AS [semi;column]", 'SELECT 1 AS "semi;column"']:
   with self.subTest(sql=sql):
    self.assertEqual(S.validate({'query':sql,'returns':'table'})['query'],sql);sqlite3.connect(':memory:').execute(sql).fetchall()
 def test_comments_do_not_create_or_hide_real_statements(self):
  for sql in ["/* ; DROP TABLE patient; */ SELECT 1", "SELECT '--not a comment; still source' AS note -- trailing ;\n", "SELECT '/* source ; */'AS note"]:
   self.assertEqual(len(sqlite3.connect(':memory:').execute(S.validate({'query':sql,'returns':'table'})['query']).fetchall()),1)
  for sql in ["SELECT 1; SELECT 2", "SELECT ';' ; /* comment */ SELECT 2", "SELECT 'safe';--comment\nDROP TABLE patient", "SELECT 1;ATTACH DATABASE 'x'AS other"]:
   with self.assertRaises(ValueError):S.validate({'query':sql,'returns':'table'})
 def test_writes_and_unterminated_tokens_still_fail(self):
  for sql in ["DELETE FROM patient", "PRAGMA journal_mode", "SELECT 'unterminated", 'SELECT "unterminated', 'SELECT 1 /* unterminated']:
   with self.assertRaises(ValueError):S.validate({'query':sql,'returns':'table'})
 def test_quoted_stale_age_identifier_is_still_checked(self):
  with self.assertRaisesRegex(ValueError,'Stale'):S.validate({'query':'SELECT "age_latest" FROM patient','returns':'table'})

if __name__=='__main__':unittest.main()
