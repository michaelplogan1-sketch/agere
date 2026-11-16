"""Received profiles are available before answer composition, with exact scope."""
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B,semantic_analyst as S,harness_improvement as H

class NativeProfiles(unittest.TestCase):
    def setUp(self):
        from test_patient_review_profiles import Profiles
        Profiles.setUp(self)

    def tools(self,cohort=None):
        wh=S.SemanticWarehouse(self.path/'semantic.sqlite3');self.addCleanup(wh.close)
        stack=SimpleNamespace(path=self.path,allow_live=False)
        t=B.Tools(stack,wh,'2030-05-27',cohort=cohort);self.addCleanup(t.db.close)
        return t

    def test_exact_native_profiles_join_into_existing_patient_table(self):
        t=self.tools(['A','B'])
        base=t({'name':'query_sql','input':{'sql':'SELECT patient_id, coverage_type FROM patient'}})
        result=t({'name':'read_patient_profiles','input':{'patient_query_sql':'SELECT patient_id FROM '+base['result_view']}})
        self.assertEqual([r['patient_id']for r in result['rows']],['A','B'])
        self.assertEqual(result['rows'][0]['patient_name'],'Alex Example')
        self.assertEqual(result['rows'][0]['profile_source_date'],'2026-01-01')
        self.assertIsNone(result['rows'][1]['patient_name'])
        self.assertEqual(result['rows'][1]['profile_status'],'profile_after_snapshot')
        joined=t({'name':'compose_table','input':{'base':base['result_view'],'keys':['patient_id'],
            'joins':[{'result':result['result_view'],'as':'profile','on':{'patient_id':'patient_id'},'mode':'one','columns':['patient_name','profile_source_date','profile_status']}]}})
        answer={'table_from_tool':joined['result_view']};B.materialize_table(answer,t.native_calls)
        self.assertEqual(len(answer['table']),2)
        self.assertEqual(answer['table'][0]['profile']['patient_name'],'Alex Example')
        self.assertEqual(result['new_paid_api_calls'],0)
        self.assertNotIn('current_cardiologist',result['columns'])
        self.assertIn('read_patient_profiles',H.READ_ONLY)

    def test_empty_inherited_population_is_never_broadened(self):
        t=self.tools([])
        with patch('clinical_core.agere.deliverables.patient_evidence.capture_profiles',return_value=None)as read:
            result=t({'name':'read_patient_profiles','input':{}})
        read.assert_called_once_with(self.path,[])
        self.assertEqual(result['rows'],[]);self.assertEqual(result['input_patient_ids'],[])

    def test_unknown_foreign_and_ambiguous_populations_rejected(self):
        t=self.tools(['A'])
        for args in [{'patient_ids':['B']},{'patient_ids':['UNKNOWN']},
                     {'patient_ids':['A'],'patient_query_sql':'SELECT patient_id FROM patient'},
                     {'patient_query_sql':'SELECT patient_id,coverage_type FROM patient'}]:
            with self.subTest(args=args),self.assertRaises(ValueError):t({'name':'read_patient_profiles','input':args})

    def test_missing_profile_keeps_patient_and_explicit_unknown(self):
        with sqlite3.connect(self.path/'stack.sqlite3')as db:db.execute("DELETE FROM patients WHERE id='A'")
        result=self.tools(['A'])({'name':'read_patient_profiles','input':{}})
        self.assertEqual(len(result['rows']),1);self.assertEqual(result['rows'][0]['patient_id'],'A')
        self.assertIsNone(result['rows'][0]['patient_name'])
        self.assertEqual(result['rows'][0]['profile_status'],'profile_unavailable')

    def test_snapshot_and_source_identity_errors_fail_without_inference(self):
        t=self.tools(['A']);t.as_of='2025-01-01'
        with self.assertRaisesRegex(ValueError,'snapshot'):t({'name':'read_patient_profiles','input':{}})
        t.as_of='2030-05-27'
        metadata=json.loads((self.path/'run.json').read_text());metadata['source_digest']='another-source'
        (self.path/'run.json').write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError,'source differs'):t({'name':'read_patient_profiles','input':{}})
        metadata['source_digest']='source';(self.path/'run.json').write_text(json.dumps(metadata))
        with sqlite3.connect(self.path/'stack.sqlite3')as db:
            db.execute("UPDATE patients SET payload=? WHERE id='A'",(json.dumps({'patient_id':'B','display_name':'Wrong'}),))
        with self.assertRaisesRegex(ValueError,'differs'):t({'name':'read_patient_profiles','input':{}})

if __name__=='__main__':unittest.main()
