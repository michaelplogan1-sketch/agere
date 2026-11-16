"""A failed source review stops new patients and retains completed evidence."""
import os,threading,time,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import document_review as D
from clinical_core.agere.gateway import ProviderUnavailable
import test_document_review as fixture


class DocumentDispatch(unittest.TestCase):
    setUp=fixture.Review.setUp

    def prepare(self):
        ids=list('ABCDEF')
        self.db.executemany('INSERT INTO patient VALUES (?)',[(p,)for p in ids if p not in 'AB'])
        self.tools._texts=lambda p:[('source-'+p,'clinical_note','notes','office','2030-01-01','Complete source context.','')]
        return ids

    def invoke(self,ids,reader):
        with patch.dict(os.environ,{'AGERE_DOCUMENT_REVIEW':'typed_spans'}),patch(
            'clinical_core.agere.clinical.typed_document_review.read',side_effect=reader),patch(
            'clinical_core.agere.clinical.harness_improvement.recover_document_fields',return_value=None),patch(
            'clinical_core.agere.clinical.harness_improvement.recover_document_observations',return_value=None):
            return D.review(self.tools,ids,{'q':'What is documented?'})

    @staticmethod
    def result():
        return {'version':'authored','coverage':[],'ranking':{},'all_blocks_processed':True,
                'unresolved':[],'checks':[],'findings':{'q':[]}}

    def test_limit_stops_queued_patients_and_preserves_completed_siblings(self):
        ids=self.prepare();barrier=threading.Barrier(3);failed=threading.Event();started=[]
        error=ProviderUnavailable('Authored subscription limit',kind='rate_limit')
        def reader(tools,pid,*args,**kwargs):
            started.append(pid);barrier.wait(timeout=5)
            if pid=='A':failed.set();raise error
            self.assertTrue(failed.wait(timeout=5));time.sleep(.02)
            return self.result()
        with self.assertRaises(ProviderUnavailable)as caught:self.invoke(ids,reader)
        self.assertIs(caught.exception,error)
        self.assertEqual(set(started),set('ABC'))
        partial=error.partial_document_review
        self.assertFalse(partial['complete'])
        self.assertEqual(set(partial['completed']),set('BC'))
        self.assertEqual(partial['uncompleted_patient_ids'],list('ADEF'))
        self.assertEqual(partial['requested_patient_ids'],ids)
        self.assertEqual(len(partial['manifest']),6)

    def test_success_dispatches_the_complete_population_in_original_row_order(self):
        ids=self.prepare();started=[]
        def reader(tools,pid,*args,**kwargs):
            started.append(pid);return self.result()
        result=self.invoke(ids,reader)
        self.assertEqual(set(started),set(ids))
        self.assertEqual([r['patient_id']for r in result['rows']],ids)
        self.assertEqual(len(result['coverage']),6)


if __name__=='__main__':unittest.main()
