"""Complete-question integrity checks over the actual saved synthetic payer selection."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import plan_answer as P, source_synthesis as S, question_decision as Q

class CompleteQuestion(unittest.TestCase):
    def setUp(self):
        self.result=json.loads(Path('artifacts/agere-complete-question-v1/payer/snapshot.json').read_text())
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.r=SimpleNamespace(path=Path(self.temp.name));self.request={'snapshot_id':self.result['snapshot']['id']}
        self.mock=patch.object(P.A,'inspect',side_effect=lambda *a:deepcopy(self.result));self.mock.start();self.addCleanup(self.mock.stop)
        S.configure_plan_answers(self.r)

    def test_entire_original_question_reopens_through_existing_contracts(self):
        a=P.prepare(self.r,self.request);c=S.context(self.r.path,a['context_id'])
        self.assertEqual(S.inspect(self.r.path,a['id']),a)
        self.assertEqual(P.prepare(self.r,self.request),a)
        self.assertEqual(a['question'],self.result['snapshot']['original_question'])
        self.assertEqual(len(c['model_context']['sources']),24)
        self.assertEqual(a['coverage']['submitted_units'],53)
        self.assertEqual(a['coverage']['unassessed_units'],[])
        self.assertFalse(a['coverage']['cross_group_relations_assessed'])
        self.assertFalse(c['dispatch']['source_review_allowed'])
        self.assertEqual(a['new_paid_api_calls'],0)
        self.assertTrue(any('Cross-group relationships remain unassessed' in u for u in a['unanswered']))
        bound,_,b=Q.source_binding(self.r,a['id']);self.assertEqual(bound,a)
        self.assertEqual(b['source_versions']['answer_selection_id'],self.request['snapshot_id'])
        planning=Q.review_plan(a,c,b)
        self.assertIn('care_access',[t['need_tag'] for t in planning['review_topics']])
        self.assertEqual(planning['unresolved'],a['unanswered'])

    def test_group_aliases_are_preserved_and_scoped(self):
        a=P.assemble(self.result)['answer']
        for n,parent in enumerate(self.result['answers'],1):
            selected=[f for f in a['findings']+a['retained_source_findings'] if f['reference_scope']['group_number']==n]
            self.assertEqual(len(selected),len(parent['findings']))
            for new in selected:
                old=next(f for f in parent['findings'] if f['id']==new['original_finding_id'])
                self.assertEqual(new['text'],old['text']);self.assertEqual(new['citations'],old['citations'])
                self.assertEqual(new['original_finding_id'],old['id'])
        self.assertEqual(len({f['id'] for f in a['findings']}),len(a['findings']))
        self.assertTrue(a['retained_source_findings'])
        self.assertTrue(all(not f.get('stage','').startswith('p') for f in a['findings']))
        self.assertTrue(all(f.get('stage','').startswith('p') for f in a['retained_source_findings']))

    def test_missing_and_extra_group_answers_rejected(self):
        for field in ['answers','contexts']:
            for extra in [False,True]:
                r=deepcopy(self.result)
                r[field]=r[field]+[r[field][0]] if extra else r[field][:-1]
                with self.assertRaisesRegex(ValueError,'Every declared group'):P.assemble(r)

    def test_cross_snapshot_and_citation_substitution_rejected(self):
        r=deepcopy(self.result);r['contexts'][0]['model_context']['snapshot']['as_of']='2030-01-01'
        with self.assertRaisesRegex(ValueError,'snapshots differ'):P.assemble(r)
        r=deepcopy(self.result);r['answers'][0]['findings'][0]['citations'][0]['source']['record_hash']='changed'
        with self.assertRaisesRegex(ValueError,'citation differs'):P.assemble(r)

    def test_changed_saved_parent_cannot_reopen(self):
        a=P.prepare(self.r,self.request)
        self.result['answers'][0]['findings'][0]['text']='changed'
        with self.assertRaisesRegex(ValueError,'dependencies changed'):P.inspect(self.r,a['id'])

    def test_reconciliation_from_other_selection_rejected(self):
        with self.assertRaisesRegex(ValueError,'another complete answer selection'):
            P.assemble(self.result,{'snapshot_id':'other','plan_id':'other','status':'complete'})

    def test_multiple_answer_versions_can_share_exact_context(self):
        first=P.prepare(self.r,self.request)
        r=P.assemble(self.result)['answer']
        review={'id':digest('review'),'snapshot_id':self.request['snapshot_id'],'plan_id':self.result['plan']['id'],'status':'complete',
                'result':{'findings':r['findings'],'unanswered':['Relationships still uncertain'],'review_steps':[]},
                'usage':{},'token_check':{'input_tokens':1}}
        with patch.object(P.R,'inspect',return_value=review):
            second=P.prepare(self.r,{**self.request,'reconciliation_id':review['id']})
            self.assertEqual(first['context_id'],second['context_id']);self.assertNotEqual(first['id'],second['id'])
            self.assertEqual(P.inspect(self.r,second['id']),second)
            self.assertEqual(P.inspect(self.r,first['id']),first)


class SelectionAcceptance(unittest.TestCase):
    def setUp(self):
        from clinical_core.agere.workflow_scorecard import complete_selection_valid
        self.check=complete_selection_valid
        folder=Path('artifacts/agere-complete-question-v1/payer')
        snapshot=json.loads((folder/'snapshot.json').read_text())
        composed=P.assemble(snapshot);self.a=composed['answer'];c=composed['context']
        self.row={'snapshot':snapshot['snapshot'],'plan':snapshot['plan'],'context':c}
        self.case={'patient':self.a['patient_id'],'question':self.a['question'],'required_categories_if_received':['coverage','coverage_verifications']}
        self.scope={'context_id':c['id']}
    def test_whole_selected_groups_qualify_received_categories(self):
        self.assertTrue(self.check(self.case,self.row,self.a,self.scope))
    def test_missing_group_wrong_question_changed_context_or_hash_rejected(self):
        for change in ['missing_group','question','context','hash','missing_category']:
            r=deepcopy(self.row);a=deepcopy(self.a);case=deepcopy(self.case)
            if change=='missing_group':r['snapshot']['groups'].pop()
            if change=='question':case['question']='other'
            if change=='context':r['context']['model_context']['sources'].pop(next(iter(r['context']['model_context']['sources'])))
            if change=='hash':r['snapshot']['plan_digest']='stale'
            if change=='missing_category':case['required_categories_if_received'].append('medication_orders')
            self.assertFalse(self.check(case,r,a,self.scope),change)

if __name__=='__main__':unittest.main()
