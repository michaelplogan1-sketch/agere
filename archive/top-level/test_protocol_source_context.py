"""Supporting source chains remain inspectable without becoming care criteria."""
import copy
import unittest
from unittest.mock import patch
import test_care_protocols as fixture
from clinical_core.contracts import digest
from clinical_core.agere.clinical import care_protocols as cp, evidence_requirements as req
from clinical_core.agere.clinical import selection_context as sc, program_work as work


class SupportingContextTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ProtocolCache();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.root=self.f.root;self.p=self.f.P
        self.original=cp.definition(self.p['protocol'])
        spec={**self.original,'supporting_documentary_contracts':['affordability_report']}
        spec['hash']=digest(spec)
        self.spec=spec

    def test_supporting_context_does_not_change_clinical_trigger_or_readiness(self):
        _,_,before=cp.execute(self.root,['p','q'],self.p)
        originals={r['patient_id']:cp.inspect(self.root,r['id']) for r in before['patients']}
        with patch.object(cp,'definition',return_value=self.spec):
            _,_,result=cp.execute(self.root,['p','q'],self.p)
            for r in result['patients']:
                row=cp.inspect(self.root,r['id']);old=originals[row['patient_id']]
                self.assertEqual(row['trigger'],old['trigger']);self.assertEqual(row['documentary_readiness'],old['documentary_readiness'])
                supporting=row['supporting_documentary_evidence'][0]
                self.assertFalse(supporting['clinical_trigger_or_readiness_input']);self.assertFalse(supporting['clinical_eligibility_established'])
                self.assertEqual(req.inspect(self.root,supporting['receipt_id'])['patient_id'],row['patient_id'])
            self.assertEqual(cp.execute(self.root,['p','q'],self.p)[2]['processing_counts']['patient_cache_hits'],2)

    def test_supporting_only_change_invalidates_exact_patient_dependency(self):
        prepared={'base':{},'interpretation':None,'patient_inputs':{'p':'same','q':'same'},'supporting_documentary':{'context':{'p':'old','q':'old'}}}
        with patch.object(req,'receipt_id',side_effect=lambda snap,pid,**kw:snap[pid]):
            before={p:cp.receipt_id(prepared,p) for p in ('p','q')}
            prepared['supporting_documentary']['context']['p']='changed'
            self.assertNotEqual(before['p'],cp.receipt_id(prepared,'p'));self.assertEqual(before['q'],cp.receipt_id(prepared,'q'))

    def test_missing_supporting_receipt_fails_instead_of_dropping_context(self):
        with patch.object(cp,'definition',return_value=self.spec):
            prepared=cp.prepare(self.root,self.p);db=req.storage(self.root)
            try:
                with self.assertRaisesRegex(ValueError,'receipt missing'):cp.supporting_evidence(db,prepared,'p',cp.interpretation.code_identity())
            finally:db.close()

    def test_program_selection_keeps_supporting_receipt_and_blocks_changed_review(self):
        self.f.review(self.f.a)
        with patch.object(cp,'definition',return_value=self.spec):
            cp.execute(self.root,['p'],self.p);prepared=cp.prepare(self.root,self.p)
            context=sc.contexts(self.root,['p'],[{'id':'review','parameters':self.p}],{'review':prepared},evaluated_patients={'review':{'p'}})
            proposal=work.prepare(self.root,'affordability',['p'],decision_contexts=context)
            db=work.database(self.root)
            try:
                saved=work.load(db,'work_proposals',proposal['proposal_id'])
                assessed=work.load(db,'work_assessments',saved['assessment_ids'][0])
            finally:db.close()
            protocol=cp.inspect(self.root,assessed['decision_context']['protocols'][0]['receipt_id'])
            self.assertEqual(len(protocol['supporting_documentary_evidence']),1)
            self.f.review(self.f.a,status='unresolved',revision=1)
            with self.assertRaisesRegex(ValueError,'changed'):sc.verify(self.root,list(context.values()))

    def test_unregistered_duplicate_and_unbounded_supporting_contracts_rejected(self):
        spec=copy.deepcopy(self.original);spec.pop('authority');spec.pop('hash')
        for value in (['unknown'],['affordability_report']*2,['affordability_report']*5,'affordability_report'):
            with self.assertRaises(ValueError):cp.validate_definition({**spec,'supporting_documentary_contracts':value})


if __name__=='__main__':unittest.main()
