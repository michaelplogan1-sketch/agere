"""Whole-population narrative tool: complete coverage and source-bound findings, no live calls."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import document_review as D, workflow_contract as W


class Review(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript("CREATE TABLE patient(patient_id TEXT);INSERT INTO patient VALUES ('A'),('B')")
        text='The patient cannot afford the prescription. '+('Complete source context. '*500)
        self.docs=[('r1','clinical_note','notes','office','2030-01-01',text,''),
                   ('r2','clinical_note','notes','office','2030-01-01',text,''),
                   ('r3','hie_document','hie_documents','letter','2030-02-01','No current affordability concern.','')]
        self.tools=SimpleNamespace(question='',native_calls=[],db=self.db,stack=SimpleNamespace(path=self.root,ledger_path=self.root/'cost.sqlite3',receipts=self.root/'receipts',allow_live=True),
            as_of='2030-05-27',in_scope=lambda p:None,continuation_identity=lambda:{'version':1},
            _texts=lambda p:self.docs if p=='A' else [],document_reviews=[],subtool_accounting={'new_paid_api_calls':0,'estimated_cost_usd':0.0})
        self.bodies=[]

    def sender(self,stack,ledger,receipts):
        def send(body):
            self.bodies.append(body);payload=json.loads(body['messages'][0]['content']);doc=payload['whole_documents'][0]
            receipts.append({'cost_estimate':{'estimated_usd':.002}})
            result={'findings':{k:[{'value':'Historical affordability barrier; later documentation differs',
                'record_id':doc['record_id'],'quote':'The patient cannot afford the prescription.'}] for k in payload['questions']}}
            return {'content':[{'type':'text','text':json.dumps(result)}]},.002,False
        return send

    def test_all_whole_documents_and_all_patients_retained_with_quote_provenance(self):
        with patch('clinical_core.agere.clinical.together._ledgered_send',side_effect=self.sender),patch(
            'clinical_core.agere.clinical.system_one.decide',return_value={'label':'supported','decided_by':'fixture','steps':[]}):
            result=D.review(self.tools,['A','B'],{'barriers':'What financial barriers and changes are documented?'})
        self.assertEqual(len(result['rows']),2)
        self.assertEqual([r['review_status'] for r in result['rows']],['reviewed','no_documents'])
        manifest=result['coverage'][0]
        self.assertEqual((manifest['received_documents'],manifest['unique_whole_documents']),(3,2))
        supplied=json.loads(self.bodies[0]['messages'][0]['content'])['whole_documents']
        self.assertEqual(sum(len(d['text']) for d in supplied),len(self.docs[0][5])+len(self.docs[2][5]))
        finding=json.loads(result['rows'][0]['review_json'])['barriers'][0]
        self.assertEqual(finding['date'],'2030-01-01');self.assertIn(finding['quote'],self.docs[0][5])
        self.assertEqual(self.tools.subtool_accounting['new_paid_api_calls'],1)

    def test_foreign_record_quote_and_missing_fields_are_rejected(self):
        docs=[{'record_id':'r','date':'2030-01-01','text':'Patient denies cough.'}]
        for data in ({'findings':{}},{'findings':{'x':[{'value':'Cough present','record_id':'r','quote':'Patient has cough'}]}},
                     {'findings':{'x':[{'value':'Cough','record_id':'other','quote':'Patient denies cough.'}]}}):
            with self.assertRaises(ValueError):D.validate(data,{'x':'cough'},docs)

    def test_undated_document_remains_undated_in_support_review(self):
        self.docs=[(*d[:4],'',*d[5:]) for d in self.docs]
        seen=[]
        def check(q,options,evidence,**kw):
            seen.extend(evidence)
            self.assertEqual(kw['as_of'],self.tools.as_of)
            return {'label':'supported','decided_by':'scripted','steps':[]}
        with patch('clinical_core.agere.clinical.together._ledgered_send',side_effect=self.sender),patch(
                'clinical_core.agere.clinical.system_one.decide',side_effect=check):
            D.review(self.tools,['A'],{'barriers':'What is documented?'})
        self.assertTrue(seen);self.assertTrue(all(e['date'] is None for e in seen))
        self.assertTrue(all(e['snapshot_as_of']==self.tools.as_of for e in seen))

    def test_unreceived_patient_rejected_before_a_provider_call(self):
        with self.assertRaisesRegex(ValueError,'received warehouse'):D.review(self.tools,['foreign'],{'x':'question'})

    def test_empty_extraction_checks_all_source_documents_and_escalates_missed_evidence(self):
        bodies=[];evidence_seen=[]
        def sender(stack,ledger,receipts):
            def send(body):
                bodies.append(body)
                return {'content':[{'type':'text','text':json.dumps({'findings':{'barriers':[]}})}]},0,False
            return send
        def check(q,options,evidence,**kw):
            evidence_seen.append(evidence)
            return {'label':'unsupported','decided_by':'fixture','steps':[]}
        with patch('clinical_core.agere.clinical.together._ledgered_send',side_effect=sender),patch(
                'clinical_core.agere.clinical.system_one.decide',side_effect=check):
            result=D.review(self.tools,['A'],{'barriers':'Documented financial barriers?'})
        self.assertEqual(len(bodies),2)  # Haiku -> Sonnet, never silently certified empty
        self.assertEqual([len(e) for e in evidence_seen],[2,2])
        self.assertEqual(result['rows'][0]['review_status'],'needs_review')

    def test_complete_cohort_sql_empty_population_and_scope_enforcement(self):
        from clinical_core.agere.clinical import backstop_agent as B,semantic_analyst as S
        self.db.row_factory=sqlite3.Row
        self.db.set_authorizer(S.SemanticWarehouse._authorize)
        self.tools.cohort=None;self.tools.result_sources={}
        self.tools.semantic=SimpleNamespace(db=self.db,query=lambda sql,**kw:S.bounded_query(
            self.db,sql,{'as_of':kw['as_of']},30,kw['max_rows'],kw['max_chars']))
        self.tools._call=lambda use:B.Tools._call(self.tools,use)
        out=D.review(self.tools,questions={'x':'Any evidence?'},patient_query_sql='SELECT patient_id FROM patient WHERE 0')
        self.assertEqual(out['rows'],[])
        def scope(pid):
            if pid!='A':raise ValueError('outside selected population')
        self.tools.in_scope=scope
        with self.assertRaisesRegex(ValueError,'outside selected'):
            D.review(self.tools,questions={'x':'Any evidence?'},patient_query_sql='SELECT patient_id FROM patient')
        with self.assertRaises(ValueError):
            D.review(self.tools,questions={'x':'Any evidence?'},patient_query_sql='DELETE FROM patient')
        self.tools.cohort=['B'];self.tools.in_scope=lambda p:None
        out=D.review(self.tools,questions={'x':'Any evidence?'},patient_query_sql='SELECT patient_id FROM patient')
        self.assertEqual([r['patient_id'] for r in out['rows']],['B'])
        self.assertEqual(out['rows'][0]['review_status'],'no_documents')

    def test_subscription_limit_is_not_swallowed_or_retried_on_another_model(self):
        from clinical_core.agere.gateway import ProviderUnavailable
        sender=unittest.mock.Mock(side_effect=ProviderUnavailable('Claude subscription weekly limit',kind='rate_limit'))
        with patch('clinical_core.agere.clinical.together._ledgered_send',return_value=sender):
            with self.assertRaises(ProviderUnavailable):D.review(self.tools,['A'],{'x':'Any evidence?'})
        self.assertEqual(sender.call_count,1)

    def test_unknown_failed_reader_usage_preserves_known_subtotal_without_claiming_a_total(self):
        def sender(stack,ledger,receipts):
            success=self.sender(stack,ledger,receipts)
            count=0
            def send(body):
                nonlocal count
                count+=1
                if count==1:raise TimeoutError('Unreported usage')
                return success(body)
            return send
        with patch('clinical_core.agere.clinical.together._ledgered_send',side_effect=sender),patch(
            'clinical_core.agere.clinical.system_one.decide',return_value={'label':'supported','decided_by':'fixture','steps':[]}):
            result=D.review(self.tools,['A'],{'barriers':'Financial barriers?'})
        self.assertEqual(result['rows'][0]['review_status'],'reviewed')
        self.assertEqual(result['accounting']['known_api_estimate_usd'],.002)
        self.assertFalse(result['accounting']['api_cost_known'])
        self.assertEqual(self.tools.subtool_accounting['new_paid_api_calls'],2)

    def test_shared_note_and_scanned_enumeration_obeys_arrival_cutoff_including_same_day_timestamps(self):
        from clinical_core.agere.clinical import note_evidence as NE
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''CREATE TABLE record(record_id,patient_id,stream,event_date,available_date,payload);
            INSERT INTO record VALUES ('same_day','A','notes','2030-05-27T09:00','2030-05-27T10:00','{"text":"visible"}'),
              ('late','A','notes','2030-05-01','2030-06-01','{"text":"not yet available"}');
            CREATE TABLE scanned_document(record_id,document_type,event_date,available_date);
            INSERT INTO scanned_document VALUES ('scan_seen','letter','2030-05-27T08:00','2030-05-27T11:00'),
              ('scan_late','letter','2030-05-01','2030-06-01');
            CREATE TABLE scanned_document_text(record_id,patient_id,text_source,text);
            INSERT INTO scanned_document_text VALUES ('scan_seen','A','ocr','visible scan'),('scan_late','A','ocr','later scan');''')
        rows=NE._texts(self.db,'A',['clinical_note','scanned_document_text'],'2030-05-27')
        self.assertEqual({r[0] for r in rows},{'same_day','scan_seen'})

    def test_free_form_artifact_brief_does_not_change_execution_scope_or_require_a_keyword_allowlist(self):
        q='Find the cohort, then create an impressive oncology navigation service with daily support and a tablet.'
        step={'kind':'deliverable','title':'Navigation','template':'command',
              'quote':'create an impressive oncology navigation service with daily support and a tablet','unresolved':[]}
        W.validate([step],q)
        self.assertEqual(step['unresolved'],[])


if __name__=='__main__':unittest.main()
