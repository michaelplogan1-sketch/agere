"""Checks for lossless packing and bounded, source-bound multi-pass review."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import context_builder as ctx, context_passes as passes
from clinical_core.agere.clinical.graph_workbench import pass_examples
from clinical_core.agere.clinical.analysis_graph import validate as validate_graph
from clinical_core.agere.clinical.economical_planner import local_plan
from clinical_core.agere.gateway import BudgetLedger
from test_task_context import record, META, PARAMS


def fixture(count=20,budget=6000,text='Explicit received statement. '):
    parameters={**PARAMS,'question':'Does the source document a medication hold?','max_bytes':budget}
    records=[record(text+str(i),rid='n'+str(i)) for i in range(count)]
    units=ctx.units('p',records,[],parameters,'2026-09-30')
    return passes.partition('p',parameters,units,META),units


def raw_answer(body,label='unknown',support='unknown'):
    answers={}
    for name,choice in [('finding',label),('support',support)]:
        answers[name]={'type':'choice','choice':choice,'probabilities':{
            key:1.0 if key==choice else 0.0 for key in body['questions'][name]['criteria']}}
    return {'model':body['model'],'answers':answers,'usage':{'input_tokens':100,'output_tokens':2}}


class PassTests(unittest.TestCase):
    def test_every_unit_once_with_bounded_choices_and_bytes(self):
        bundle,units=fixture()
        packed=[u['id'] for page in bundle['passes'] for u in page['evidence']]
        self.assertEqual(set(packed),{u['id'] for u in units})
        self.assertEqual(len(packed),len(units));self.assertGreater(len(bundle['passes']),1)
        for page in bundle['passes']:
            self.assertLessEqual(len(ctx.encode(page)),6000)
            self.assertLessEqual(len(page['evidence']),passes.MAX_UNITS)
        self.assertTrue(bundle['review_ready'])

    def test_indivisible_statement_never_silently_split_or_dropped(self):
        bundle,units=fixture(1,text='Held after a reaction. '+'x'*20000)
        self.assertFalse(bundle['review_ready']);self.assertEqual(bundle['status'],'indivisible_overflow')
        self.assertEqual(bundle['counts']['blocked_units'],1)
        blocked=bundle['blocked_units'][0]
        self.assertEqual(blocked['end']-blocked['start'],len(units[1]['text']))
        self.assertTrue(blocked['protected_reasons'])
        self.assertEqual(bundle['counts']['packed_units']+bundle['counts']['blocked_units'],len(units))

    def test_unannotated_passes_do_not_invent_missing_concepts_per_page(self):
        bundle,units=fixture()
        p={**PARAMS,'max_bytes':6000,'concepts':['outside care']}
        units[-1]['annotations']=[{'concept':'outside care','subject':'patient','assertion':'affirmed'}]
        bundle=passes.partition('p',p,units,META)
        self.assertEqual(bundle['missing_evidence']['concepts_without_annotations'],[])
        for page in bundle['passes']:
            self.assertEqual(page['missing_evidence_in_selected_sources']['concepts_without_annotations'],[])

    def test_cross_patient_and_duplicate_units_rejected(self):
        _,units=fixture(1)
        with self.assertRaises(ValueError):passes.partition('other',PARAMS,units,META)
        with self.assertRaises(ValueError):passes.partition('p',PARAMS,units+units,META)

    def test_single_pass_does_not_claim_full_patient_state(self):
        bundle,_=fixture(1)
        self.assertEqual(bundle['whole_patient_clinical_state'],'unknown')
        self.assertFalse(bundle['complete_chart_guarantee'])

    def test_empty_scope_is_not_negative_evidence(self):
        bundle=passes.partition('p',PARAMS,[],META)
        self.assertFalse(bundle['review_ready']);self.assertEqual(bundle['status'],'no_evidence')
        result=passes.reconcile(bundle,[])
        self.assertEqual(result['status'],'incomplete_review')

    def test_typed_body_support_choices_reference_state_without_repeating_text(self):
        bundle,_=fixture(1,text='Unique received source phrase.')
        body,units=passes.request(bundle['passes'][0],'perplexity')
        self.assertLessEqual(len(json.dumps(body).encode()),20000)
        self.assertIn('Unique received source phrase.',json.dumps(body['state']))
        self.assertNotIn('Unique received source phrase.',json.dumps(body['questions']['support']))
        self.assertEqual(len(units),len(bundle['passes'][0]['evidence']))

    def test_inconsistent_support_becomes_unknown(self):
        bundle,_=fixture(1);body,units=passes.request(bundle['passes'][0],'perplexity')
        result=passes.parse(raw_answer(body,'affirmed','unknown'),body,units)
        self.assertEqual(result['label'],'unknown');self.assertFalse(result['support_contract_consistent'])

    def test_wrong_model_and_malformed_distribution_rejected(self):
        bundle,_=fixture(1);body,units=passes.request(bundle['passes'][0],'perplexity')
        raw=raw_answer(body);raw['model']='unexpected'
        with self.assertRaises(ValueError):passes.parse(raw,body,units)
        raw=raw_answer(body);raw['answers']['finding']['probabilities']['unknown']=float('nan')
        with self.assertRaises(ValueError):passes.parse(raw,body,units)

    def test_no_voting_away_opposing_proposals(self):
        bundle,_=fixture();rows=[]
        for i,page in enumerate(bundle['passes']):
            rows.append({'pass_number':i+1,'label':'negated' if i==0 else 'affirmed','source_unit_id':page['evidence'][0]['id']})
        result=passes.reconcile(bundle,rows)
        self.assertEqual(result['status'],'opposing_documentary_proposals')
        self.assertEqual(result['whole_patient_clinical_state'],'unknown')
        self.assertFalse(result['automatic_acceptance'])

    def test_partial_review_cannot_be_presented_as_complete(self):
        bundle,_=fixture();row={'pass_number':1,'label':'affirmed','source_unit_id':bundle['passes'][0]['evidence'][0]['id']}
        result=passes.reconcile(bundle,[row]);self.assertEqual(result['status'],'incomplete_review')
        self.assertFalse(result['all_passes_reviewed'])

    def test_support_cannot_cross_passes(self):
        bundle,_=fixture();row={'pass_number':1,'label':'affirmed','source_unit_id':bundle['passes'][1]['evidence'][0]['id']}
        with self.assertRaises(ValueError):passes.reconcile(bundle,[row])

    def test_pass_cap_preflight_prevents_any_paid_call(self):
        bundle,_=fixture()
        with patch.object(passes,'inspect',return_value=bundle),patch('clinical_core.agere.clinical.context_student.send') as send:
            with self.assertRaisesRegex(ValueError,'pass-call cap'):
                passes.review('unused','id','perplexity',1,None,'unused',allow_live=True)
            send.assert_not_called()

    def test_overflow_preflight_prevents_any_paid_call(self):
        bundle,_=fixture(1,text='Held. '+'x'*20000)
        with patch.object(passes,'inspect',return_value=bundle),patch('clinical_core.agere.clinical.context_student.send') as send:
            with self.assertRaisesRegex(ValueError,'Unpacked source'):
                passes.review('unused','id','perplexity',64,None,'unused',allow_live=True)
            send.assert_not_called()

    def test_all_presets_compile_locally(self):
        for example in pass_examples():
            validate_graph(example['graph'],'2026-09-30')
            self.assertIsNotNone(local_plan(example['question'],'2026-09-30'))

    def test_cached_provider_replay_makes_no_second_network_call(self):
        bundle,_=fixture(2)
        class Response:
            def __init__(self,body):self.body=body
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps(raw_answer(self.body)).encode()
        with tempfile.TemporaryDirectory() as directory:
            ledger=BudgetLedger(Path(directory)/'ledger.sqlite3')
            try:
                with patch.object(passes,'inspect',return_value=bundle),patch.dict(os.environ,{'PERPLEXITY_API_KEY':'test-only'}), \
                     patch('clinical_core.agere.clinical.context_student.urllib.request.urlopen',side_effect=lambda request,**kwargs:Response(json.loads(request.data))) as send:
                    first=passes.review(directory,'bundle','perplexity',64,ledger,directory,allow_live=True)
                    second=passes.review(directory,'bundle','perplexity',64,ledger,directory,allow_live=True)
                    self.assertEqual(send.call_count,len(bundle['passes']))
                    self.assertEqual(first['new_paid_api_calls'],len(bundle['passes']))
                    self.assertEqual(second['new_paid_api_calls'],0)
                    self.assertTrue(second['all_passes_reviewed'])
            finally:ledger.close()


if __name__=='__main__':unittest.main()
