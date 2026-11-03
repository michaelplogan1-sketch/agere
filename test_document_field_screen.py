"""Candidate source scheduling preserves context and uncertain dependencies."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import typed_document_review as T
from clinical_core.contracts import digest


class DocumentFieldScreen(unittest.TestCase):
    def run_reader(self, documents, questions, screening, *, mode='candidate', candidates=None):
        calls=[]
        by_text={d['text'] for d in documents}
        def judge(subject,named,evidence,**kwargs):
            self.assertTrue(all(e['text'] in by_text for e in evidence))
            calls.append((subject,named,evidence))
            if subject.startswith('Whole-source field relevance'):
                answers={k:screening(q['instructions']) for k,q in named.items()}
            else:
                answers={k:{'label':'unknown','decided_by':None} for k in named}
            return {'judgments':answers,'steps':[],'cost_usd':0}
        tools=SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(allow_live=False))
        with patch.dict(os.environ,{'AGERE_DOCUMENT_FIELD_SCREEN':mode,'AGERE_DOCUMENT_LOCAL_READERS':'','AGERE_DOCUMENT_CROSS_BATCH':'on'}):
            result=T.read(tools,'patient',documents,questions,None,judge=judge,ranking=False,candidates=candidates)
        return result,calls

    def test_default_route_never_screens_or_changes_original_statement_work(self):
        doc={'record_id':'r','date':'2030-01-01','text':'First source statement. Second source statement.'}
        def forbidden(_):self.fail('Default route must not screen')
        out,calls=self.run_reader([doc],{'q':'What happened?'},forbidden,mode='off')
        self.assertEqual(len(out['unresolved']),2)
        self.assertEqual(out['screened_statement_units'],0)
        self.assertTrue(out['all_selected_blocks_processed'])

    def test_negative_is_source_and_field_specific_with_explicit_coverage(self):
        docs=[{'record_id':'a','date':'2030-01-01','text':'Agent A is listed. Nothing else is established.'},
              {'record_id':'b','date':'2030-01-02','text':'Agent A was withheld. Its reason needs review.'}]
        def screen(ins):
            negative=ins['source_record_id']=='a'and ins['question']=='Why withheld?'
            return {'label':'irrelevant'if negative else'needs_review','decided_by':'fixture'}
        out,calls=self.run_reader(docs,{'why':'Why withheld?','use':'What was used?'},screen)
        self.assertEqual(out['screened_statement_units'],2)
        remaining={(v['record_id'],v['field']) for v in out['unresolved']}
        self.assertEqual(remaining,{('a','use'),('b','use'),('b','why')})
        self.assertFalse(out['all_selected_blocks_processed'])
        self.assertEqual(sum(v['model_field_decisions'] for v in out['coverage']),6)
        receipts=[r for r in out['checks'] if r.get('phase')=='document_field_screen']
        self.assertTrue(receipts)
        self.assertTrue(all(r['chart_wide_absence_established']is False for r in receipts))
        self.assertEqual(out['findings'],{'why':[],'use':[]})

    def test_unknown_failed_or_quarantined_screen_keeps_all_statements(self):
        doc={'record_id':'r','date':'2030-01-01','text':'Historical statement. Ambiguous current account.'}
        for reading in [{},{'label':'unknown','decided_by':None},
                        {'label':'irrelevant','probability':0.79,'decided_by':None},
                        {'label':'unknown','decided_by':None,'source_observation_quarantined':True},
                        {'label':'needs_review','decided_by':'fixture'}]:
            with self.subTest(reading=reading):
                out,_=self.run_reader([doc],{'q':'Current account?'},lambda _:reading)
                self.assertEqual(len(out['unresolved']),2)
                self.assertEqual(out['screened_statement_units'],0)
                self.assertTrue(out['all_selected_blocks_processed'])

    def test_same_record_with_different_source_date_is_not_coalesced(self):
        docs=[{'record_id':'r','date':'2030-01-01','text':'First statement. Earlier account.'},
              {'record_id':'r','date':'2030-02-01','text':'First statement. Changed account.'}]
        out,calls=self.run_reader(docs,{'q':'What happened?'},lambda _:{'label':'unknown','decided_by':None})
        screen_calls=[c for c in calls if c[0].startswith('Whole-source field relevance')]
        self.assertEqual(sum(len(c[1])for c in screen_calls),2)
        self.assertEqual(len(out['unresolved']),4)
        for _,named,evidence in screen_calls:
            self.assertEqual(len({e['id']for e in evidence}),len(evidence))
            self.assertTrue(all(q['instructions']['source_context_id']in{e['id']for e in evidence}for q in named.values()))

    def test_retained_exact_candidate_cannot_be_discarded_by_new_screen(self):
        doc={'record_id':'r','date':'2030-01-01','text':'Exact observation. Complete surrounding context.'}
        span=T.blocks(doc['text'])[0]
        candidate={'field':'q','record_id':'r','date':doc['date'],'document_text_digest':digest(doc['text']),
                   'span_start':span['start'],'span_end':span['end'],'quote':span['text']}
        def forbidden(_):self.fail('Explicit retained candidates must be inspected directly')
        out,_=self.run_reader([doc],{'q':'What happened?'},forbidden,candidates=[candidate])
        self.assertEqual(len(out['unresolved']),1)
        self.assertTrue(out['all_candidates_processed'])
        self.assertEqual(out['screened_statement_units'],0)

    def test_single_statement_source_needs_no_extra_screen(self):
        doc={'record_id':'r','date':'2030-01-01','text':'One complete statement.'}
        def forbidden(_):self.fail('Screening would add work without reducing any')
        out,calls=self.run_reader([doc],{'q':'What happened?'},forbidden)
        self.assertEqual(len(out['unresolved']),1)
        self.assertEqual(len(calls),1)

    def test_same_text_at_different_dates_keeps_separate_coverage_counts(self):
        docs=[{'record_id':'r','date':date,'text':'First statement. Second statement.'}
              for date in ['2030-01-01','2030-02-01']]
        out,_=self.run_reader(docs,{'q':'What happened?'},lambda _:{'label':'irrelevant','decided_by':'fixture'})
        self.assertEqual(out['screened_statement_units'],4)
        self.assertEqual([c['screened_irrelevant_field_decisions']for c in out['coverage']],[2,2])
        self.assertEqual([c['model_field_decisions']for c in out['coverage']],[0,0])

    def test_partial_cached_reading_cannot_be_hidden_by_new_source_screen(self):
        from pathlib import Path
        doc={'record_id':'r','date':'2030-01-01','text':'Historical account. Current status is uncertain.'}
        span=T.blocks(doc['text'])[0]
        candidate={'field':'q','record_id':'r','date':doc['date'],'document_text_digest':digest(doc['text']),
                   'span_start':span['start'],'span_end':span['end'],'quote':span['text']}
        calls=[]
        def seed(subject,named,evidence,**kwargs):
            self.assertFalse(subject.startswith('Whole-source field relevance'))
            calls.append(subject)
            return {'judgments':{k:{'label':'unknown','probabilities':{'unknown':1.0},'decided_by':None}for k in named},
                    'steps':[{'request_hash':'authored-partial-fixture','cache_hit':True}],'cost_usd':0}
        with tempfile.TemporaryDirectory()as tmp:
            tools=SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(path=Path(tmp),allow_live=False))
            with patch.dict(os.environ,{'AGERE_DOCUMENT_FIELD_SCREEN':'off','AGERE_DOCUMENT_LOCAL_READERS':''}):
                before=T.read(tools,'patient',[doc],{'q':'Current status?'},None,judge=seed,ranking=False,candidates=[candidate])
            with patch.dict(os.environ,{'AGERE_DOCUMENT_FIELD_SCREEN':'candidate','AGERE_DOCUMENT_LOCAL_READERS':''}):
                after=T.read(tools,'patient',[doc],{'q':'Current status?'},None,judge=seed,ranking=False)
            self.assertIn(before['unresolved'][0],after['unresolved'])
            self.assertEqual(len(after['unresolved']),2)
            self.assertEqual(after['screened_statement_units'],0)
            self.assertFalse(any(c.get('phase')=='document_field_screen'for c in after['checks']))

    def test_exact_cached_unknowns_are_reused_without_new_screening(self):
        from pathlib import Path
        doc={'record_id':'r','date':'2030-01-01','text':'Historical account. Current status is uncertain.'}
        with tempfile.TemporaryDirectory()as tmp:
            tools=SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(path=Path(tmp),allow_live=False))
            def seed(subject,named,evidence,**kwargs):
                return {'judgments':{k:{'label':'unknown','probabilities':{'unknown':1.0},'decided_by':None}for k in named},
                        'steps':[{'request_hash':'authored-cached-fixture','cache_hit':True}],'cost_usd':0}
            with patch.dict(os.environ,{'AGERE_DOCUMENT_FIELD_SCREEN':'off','AGERE_DOCUMENT_LOCAL_READERS':''}):
                before=T.read(tools,'patient',[doc],{'q':'Current status?'},None,judge=seed,ranking=False)
            def forbidden(*args,**kwargs):self.fail('A cached read must not acquire a new screening judgment')
            with patch.dict(os.environ,{'AGERE_DOCUMENT_FIELD_SCREEN':'candidate','AGERE_DOCUMENT_LOCAL_READERS':''}):
                after=T.read(tools,'patient',[doc],{'q':'Current status?'},None,judge=forbidden,ranking=False)
            self.assertEqual(before['unresolved'],after['unresolved'])
            self.assertEqual(after['screened_statement_units'],0)
            self.assertFalse(any(c.get('phase')=='document_field_screen'for c in after['checks']))


if __name__=='__main__':unittest.main()
