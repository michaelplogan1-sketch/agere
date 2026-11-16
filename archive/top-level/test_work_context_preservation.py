"""Attachment refresh behavior in disposable synthetic work databases."""
import copy,json,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import program_work as work,program_work_v1 as legacy
from clinical_core.agere.clinical import work_context_preservation as p,synthesis_work
from test_program_work import ProgramWork

class WorkContextPreservation(unittest.TestCase):
    def setUp(self):
        ProgramWork.setUp(self);self.addCleanup(self.temp.cleanup)
        # Saved-answer verification is a separate, tested contract. These
        # dummy in-memory contexts exercise work persistence, not inference.
        self.verify=patch.object(synthesis_work,'verify').start();self.addCleanup(patch.stopall)
        self.contexts=patch.object(synthesis_work,'contexts',side_effect=lambda path,ids,pids:{pids[0]:self.context(pids[0],*ids)}).start()
    def context(self,pid,*ids):
        return {'schema':synthesis_work.VERSION,'patient_id':pid,'code_identity':synthesis_work.identity(),
            'authority':synthesis_work.AUTHORITY,'answers':[{'answer_id':i} for i in sorted(ids)]}
    def prepare(self,patients=('p00',),**kwargs):return work.prepare(self.root,'hf_review',list(patients),**kwargs)
    def save(self,proposal):return work.materialize(self.root,proposal['proposal_id'],'synthetic fixture')
    def attach(self,pid,*ids):
        proposal=self.prepare([pid],synthesis_contexts={pid:self.context(pid,*ids)});self.save(proposal);return proposal
    def item(self,pid='p00'):
        return next(r for r in work.list_items(self.root,program='hf_review')['items'] if r['patient_id']==pid)
    def assessment(self,proposal,pid='p00'):
        db=work.database(self.root)
        try:
            raw=work.load(db,'work_proposals',proposal['proposal_id'])
            return next(work.load(db,'work_assessments',a) for a in raw['assessment_ids'] if work.load(db,'work_assessments',a)['patient_id']==pid)
        finally:db.close()
    def test_bulk_refresh_preserves_answer_and_work_revision(self):
        self.attach('p00','a');before=self.item();proposal=self.prepare(['p00','p01'])
        self.assertEqual(proposal['synthesis_assessments'],1)
        self.assertEqual(self.assessment(proposal)['synthesis_context'],self.context('p00','a'))
        result=self.save(proposal);self.assertEqual(result['changes']['unchanged'],1);self.assertEqual(self.item(),before)
    def test_explicit_new_answer_unions_existing_and_deduplicates(self):
        self.attach('p00','a');supplied={'p00':self.context('p00','b')};original=copy.deepcopy(supplied)
        proposal=self.prepare(synthesis_contexts=supplied)
        self.assertEqual(self.assessment(proposal)['synthesis_context'],self.context('p00','a','b'))
        self.assertEqual(supplied,original);self.verify.assert_any_call(self.root,[self.context('p00','a'),self.context('p00','b')])
        self.save(proposal);again=self.prepare(synthesis_contexts={'p00':self.context('p00','a','b')})
        self.assertEqual(again['proposal_id'],proposal['proposal_id'])
    def test_old_proposal_cannot_remove_later_attachment(self):
        old=self.prepare();self.attach('p00','a');before=self.item()
        with self.assertRaisesRegex(work.WorkConflict,'would be lost'):self.save(old)
        self.assertEqual(self.item(),before)
    def test_drop_rejection_rolls_back_entire_multi_patient_save(self):
        self.attach('p00','a')
        proposal=self.prepare(['p00','p01'],synthesis_contexts={'p00':self.context('p00','b')})
        self.attach('p01','c');before=[self.item('p00'),self.item('p01')]
        with self.assertRaises(work.WorkConflict):self.save(proposal)
        self.assertEqual([self.item('p00'),self.item('p01')],before)
        db=work.database(self.root)
        try:self.assertEqual(db.execute('SELECT COUNT(*) FROM work_events').fetchone()[0],2)
        finally:db.close()
    def test_interleaved_attachment_rejects_older_union(self):
        self.attach('p00','a');older=self.prepare(synthesis_contexts={'p00':self.context('p00','b')})
        self.attach('p00','c');before=self.item()
        with self.assertRaises(work.WorkConflict):self.save(older)
        self.assertEqual(self.item(),before)
        refreshed=self.prepare(synthesis_contexts={'p00':self.context('p00','b')})
        self.assertEqual(self.assessment(refreshed)['synthesis_context'],self.context('p00','a','b','c'))
    def test_guard_also_protects_engine_direct_writes(self):
        old=self.prepare();self.attach('p00','a')
        with self.assertRaisesRegex(sqlite3.IntegrityError,'would be lost'):
            legacy.materialize(self.root,old['proposal_id'],'synthetic fixture')
    def test_empty_population_does_not_inherit_or_expand(self):
        self.attach('p00','a');proposal=self.prepare([])
        self.assertEqual(proposal['synthesis_assessments'],0);self.assertEqual(proposal['counts']['selected'],0)
    def test_patient_and_program_attachment_isolation(self):
        self.attach('p00','a');proposal=self.prepare(['p01'])
        self.assertNotIn('synthesis_context',self.assessment(proposal,'p01'))
        self.assertEqual(p.attached(self.root,'affordability'),{})
    def test_cross_patient_context_rejected_before_union(self):
        self.attach('p00','a')
        with self.assertRaisesRegex(ValueError,'match selected patient'):
            self.prepare(synthesis_contexts={'p00':self.context('p01','b')})
        self.contexts.assert_not_called()
    def test_stale_attached_context_is_not_silently_dropped(self):
        self.attach('p00','a');before=self.item();self.verify.side_effect=ValueError('Source changed')
        with self.assertRaisesRegex(ValueError,'Source changed'):self.prepare()
        self.assertEqual(self.item(),before)
    def test_invalid_provided_context_is_not_sanitized_by_union(self):
        self.attach('p00','a');self.verify.side_effect=ValueError('Malformed answer binding')
        with self.assertRaisesRegex(ValueError,'Malformed'):self.prepare(synthesis_contexts={'p00':self.context('p00','b')})
        self.contexts.assert_not_called()
    def test_graph_dependency_changes_only_with_attachments(self):
        before=work.preparation_identity(self.root,['hf_review']);self.save(self.prepare())
        self.assertEqual(before,work.preparation_identity(self.root,['hf_review']))
        self.attach('p00','a');after=work.preparation_identity(self.root,['hf_review'])
        self.assertNotEqual(before,after);self.save(self.prepare())
        self.assertEqual(after,work.preparation_identity(self.root,['hf_review']))
    def test_no_attachments_preserves_historical_assessment_identity(self):
        self.assertEqual(self.prepare(),legacy.prepare(self.root,'hf_review',['p00']))
        self.assertEqual(work.code_identity(),legacy.code_identity())

if __name__=='__main__':unittest.main()
