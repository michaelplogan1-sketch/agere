import copy,unittest
from unittest.mock import patch
import test_default_stable_synthesis as fixture
from clinical_core.contracts import digest
from clinical_core.agere.clinical import synthesis_work_availability as w, source_synthesis

class WorkAvailability(unittest.TestCase):
    setUp=fixture.DefaultStable.setUp
    raw=fixture.DefaultStable.raw
    answer=fixture.DefaultStable.answer
    sender=fixture.DefaultStable.sender
    run_default=fixture.DefaultStable.run_default

    def setup_task(self):
        result=self.run_default();answer=source_synthesis.inspect(self.path,result['id'])
        self.task={'id':'task','plan_id':'plan','patient_id':'p','revision':1}
        self.plan={'id':'plan','patient_id':'p','origin':{'kind':'answer','id':answer['id'],'hash':digest(answer)}}
        for target,name,value in [(w.care_readiness,'task',self.task),(w.care,'inspect_plan',self.plan)]:
            patcher=patch.object(target,name,return_value=value);patcher.start();self.addCleanup(patcher.stop)
        return answer

    def test_current_coverage_is_bound_to_task_without_changing_it(self):
        answer=self.setup_task();before=copy.deepcopy(self.task);out=w.prepare(self.path,'task')
        self.assertEqual(self.task,before);self.assertEqual(out['answers'][0]['availability'],answer['source_availability'])
        self.assertEqual(out['answers'][0]['availability_status'],'recorded_at_inference')
        self.assertEqual(w.prepare(self.path,'task')['id'],out['id']);self.assertFalse(out['clinical_execution_authorized'])
        self.task['revision']=2;self.assertNotEqual(w.prepare(self.path,'task')['id'],out['id'])

    def test_changed_source_hash_or_patient_blocks_report(self):
        self.setup_task();self.plan['origin']['hash']='changed'
        with self.assertRaisesRegex(ValueError,'identity'):w.prepare(self.path,'task')
        self.task['patient_id']='foreign'
        with self.assertRaisesRegex(ValueError,'patient'):w.prepare(self.path,'task')

    def test_historical_absence_is_not_reconstructed_as_inference_coverage(self):
        a=self.setup_task();a.pop('source_availability');self.plan['origin']['hash']=digest(a)
        with patch.object(source_synthesis,'inspect',return_value=a):
            out=w.prepare(self.path,'task');self.assertIsNone(out['answers'][0]['availability'])
            self.assertEqual(out['answers'][0]['availability_status'],'not_recorded_in_historical_answer')

    def test_assessment_origin_carries_exact_answer_bindings(self):
        answer=self.setup_task();self.plan['origin']={'kind':'assessment','assessment':{'synthesis_context':{
            'answers':[{'answer_id':answer['id'],'answer_hash':digest(answer)}]}}}
        out=w.prepare(self.path,'task');self.assertEqual(out['answers'][0]['answer_id'],answer['id'])
        self.plan['origin']['assessment']['synthesis_context']=None
        self.assertEqual(w.prepare(self.path,'task')['answers'],[])

    def test_concurrent_task_revision_invalidates_read(self):
        self.setup_task()
        with patch.object(w.care_readiness,'task',side_effect=[self.task,{**self.task,'revision':2}]):
            with self.assertRaisesRegex(ValueError,'Task changed'):w.prepare(self.path,'task')

if __name__=='__main__':unittest.main()
