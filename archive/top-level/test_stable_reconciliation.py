"""Pass-qualified transport identities, never inferred clinical meaning."""
import copy
import json
import unittest
from unittest.mock import patch
import test_case_passes as fixture
from clinical_core.agere.clinical import stable_reconciliation as s, source_synthesis, evidence_needs
from clinical_core.contracts import digest


class StableReferences(unittest.TestCase):
    raw=fixture.CasePasses.raw
    sender=fixture.CasePasses.sender
    run_synthesis=fixture.CasePasses.run_synthesis

    def answer(self,body):
        raw=fixture.CasePasses.answer(self,body)
        wire=json.loads(body['messages'][0]['content'])
        if wire['schema']==s.VERSION:
            value=json.loads(raw['content'][0]['text']);name=wire['original_source_excerpts']['rows'][0][0]
            value['review_steps']=[{'kind':'obtain_missing_evidence','finding_ids':['f1'],'question':'Does '+name+' establish completion?'}]
            raw=self.raw(body,value)
        return raw

    def setUp(self):
        fixture.CasePasses.setUp(self)
        self.parent=self.run_synthesis();self.parent_copy=copy.deepcopy(self.parent)

    def run_stable(self,sender=None):
        with patch.object(s.single,'send',side_effect=sender or self.sender),patch.object(s.single,'token_check',return_value={'input_tokens':650,'provider_count_cached':False}):
            return s.synthesize(self.path,self.parent['id'],self.ledger,allow_live=True)

    def test_manifest_names_unique_and_group_independent(self):
        p=s.preview(self.path,self.parent['id']);refs=p['reference_manifest']
        self.assertEqual(len(refs),len(self.row['model_context']['evidence']))
        self.assertIn('p1:u1',refs);self.assertIn('p2:u1',refs)
        self.assertNotEqual(refs['p1:u1']['source_unit_id'],refs['p2:u1']['source_unit_id'])
        self.assertTrue(all(n<=20000 for n in p['request_bytes']))

    def test_transport_rewrites_structured_aliases_but_preserves_source_prose(self):
        parent,row,refs,jobs,key=s.prepare(self.path,self.parent['id'])
        original={f['id']:f['text'] for p in parent['passes'] for f in p['result']['findings']}
        for job in jobs:
            wire=json.loads(job['body']['messages'][0]['content'])
            self.assertTrue(all(s.QUALIFIED.fullmatch(k) for k in job['units']))
            for finding in wire['pass_findings']['rows']:
                self.assertEqual(finding[2],original[finding[0]])
                self.assertTrue(all(c[0] in job['units'] for c in finding[4]))
            for pending in wire['pass_coverage_and_open_questions']:
                self.assertTrue(all(r[0] in job['units'] for r in pending['unit_reviews']['rows']))

    def test_group_partition_keeps_global_names_and_excludes_other_group_units(self):
        parent,row,refs,_,_=s.prepare(self.path,self.parent['id'])
        original=s.request
        def bounded(row,*args):
            if len(row['model_context']['sources'])>8:raise ValueError('Stable reconciliation request bound exceeded')
            return original(row,*args)
        with patch.object(s,'request',side_effect=bounded): groups=s.jobs(row,parent['passes'],parent['dependencies'],refs)
        self.assertEqual(len(groups),2)
        self.assertEqual(set().union(*(set(g['units']) for g in groups)),set(refs))
        self.assertFalse(set(groups[0]['units'])&set(groups[1]['units']))
        other=next(iter(groups[1]['units']));job=groups[0]
        value=json.loads(self.answer(job['body'])['content'][0]['text']);value['unanswered']=['What does '+other+' establish?']
        with self.assertRaisesRegex(ValueError,'not supplied'):s.parse(self.raw(job['body'],value),job,refs)

    def test_indivisible_group_overflow_never_calls_provider(self):
        with patch.object(s,'request',side_effect=ValueError('Stable reconciliation request bound exceeded')),patch.object(s.single,'send') as send:
            with self.assertRaises(ValueError):s.synthesize(self.path,self.parent['id'],self.ledger,allow_live=True)
            send.assert_not_called()

    def test_old_source_passes_reused_exact_and_replay_zero_calls(self):
        before=self.ledger.receipt()['calls']['validated'];out=self.run_stable()
        self.assertEqual(out['passes'],self.parent['passes']);self.assertEqual(out['parent_answer_hash'],digest(self.parent))
        self.assertEqual(out['new_paid_api_calls'],len(out['reconciliation']['groups']))
        self.assertEqual(self.ledger.receipt()['calls']['validated']-before,out['new_paid_api_calls'])
        replay=s.synthesize(self.path,self.parent['id'],self.ledger)
        self.assertTrue(replay['cache_hit']);self.assertEqual(replay['new_paid_api_calls'],0)
        self.assertEqual(source_synthesis.inspect(self.path,self.parent['id']),self.parent_copy)
        self.assertEqual(source_synthesis.inspect(self.path,out['id']),out)

    def test_bare_unknown_and_wrong_case_names_rejected(self):
        _,_,refs,jobs,_=s.prepare(self.path,self.parent['id']);job=jobs[0]
        for name in ['u1','u0','p99:u1','p0:u1','P1:U1']:
            raw=self.answer(job['body']);value=json.loads(raw['content'][0]['text']);value['unanswered']=['What does '+name+' mean?']
            with self.assertRaises(ValueError):s.parse(self.raw(job['body'],value),job,refs)

    def test_no_binding_inferred_from_citations_when_question_has_no_reference(self):
        _,_,refs,jobs,_=s.prepare(self.path,self.parent['id']);job=jobs[0]
        raw=self.answer(job['body']);value=json.loads(raw['content'][0]['text']);value['review_steps'][0]['question']='Was the requested care received?'
        out=s.parse(self.raw(job['body'],value),job,refs)
        self.assertEqual(out['review_steps'][0]['evidence_references'],[])

    def test_review_question_exact_sources_enter_existing_needs_contract(self):
        out=self.run_stable();new=next(v for v in out['review_steps'] if v.get('evidence_references'))
        self.assertIn('Exact source records from verified unit references',new['question'])
        self.assertNotEqual(new['model_question'],new['question'])
        with patch.object(evidence_needs.record_context,'received',return_value=(self.records,{'snapshot':{'as_of':'2026-03-01'}})):
            needs=evidence_needs.prepare(self.path,out['id'])
        need=next(v for v in needs['needs'] if v['id']==new['id'])
        self.assertEqual({v['record_id'] for v in need['selectors']},{v['record_id'] for v in new['evidence_references']})
        self.assertEqual(need['semantic_resolution'],'pending')

    def test_duplicate_and_missing_manifest_units_block_before_dispatch(self):
        for change in ['duplicate','missing']:
            parent=copy.deepcopy(self.parent)
            if change=='duplicate':parent['passes'][1]['result']['unit_reviews'][0]=parent['passes'][0]['result']['unit_reviews'][0]
            else:parent['passes'][0]['result']['unit_reviews'].pop()
            with self.assertRaises(ValueError):s.manifest(parent,self.row)

    def test_disabled_live_does_not_call_or_count(self):
        with patch.object(s.single,'send') as send,patch.object(s.single,'token_check') as count:
            with self.assertRaisesRegex(ValueError,'disabled'):s.synthesize(self.path,self.parent['id'],self.ledger)
            send.assert_not_called();count.assert_not_called()

    def test_failed_paid_request_not_silently_retried(self):
        def fail(body,provider,ledger,out,*,parse):
            key=digest({'contract':'context-student/1','provider':provider,'body':body});ledger.reserve(key,provider,body['model']);ledger.finish(key,error='fixture failure');raise RuntimeError('fixture failure')
        with self.assertRaises(RuntimeError):self.run_stable(fail)
        with patch.object(s.single,'send') as send:
            with self.assertRaisesRegex(ValueError,'no silent'):s.synthesize(self.path,self.parent['id'],self.ledger,allow_live=True)
            send.assert_not_called()

    def test_source_change_during_call_keeps_incomplete_progress(self):
        def mutate(*args,**kwargs):
            result=self.sender(*args,**kwargs);self.db.execute("DELETE FROM records WHERE id='r0'");self.db.commit();return result
        with self.assertRaises(ValueError):self.run_stable(mutate)
        progress=list((self.path/'stable-reconciliation').glob('*-progress.json'));self.assertEqual(len(progress),1)
        self.assertEqual(json.loads(progress[0].read_text())['status'],'incomplete')

    def test_changed_result_cannot_become_assessment_context(self):
        out=self.run_stable();out['findings'][0]['text']='Tampered'
        s.location(self.path,out['id']).write_text(json.dumps(out))
        with self.assertRaisesRegex(ValueError,'integrity'):source_synthesis.inspect(self.path,out['id'])


if __name__=='__main__':unittest.main()
