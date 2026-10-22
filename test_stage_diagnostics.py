"""Stage diagnostics: each cascade failure attributed to retrieval / rerank / reading / planning / execution / check."""
import json
import unittest

from clinical_core.agere.clinical import stage_diagnostics as SD
from clinical_core.agere.clinical import proposers


def e(proposer, outcome, detail=None):
    return {'proposer': proposer, 'outcome': outcome, 'detail': detail, 'seconds': 0.1}


class StageOf(unittest.TestCase):
    def test_each_stage(self):
        cases = [
            (e('semantic_cache', 'no proposal'), 'retrieval'),
            (e('evidence_retrieval', 'error', 'RuntimeError: BGE reranker failed to load'), 'rerank'),
            (e('evidence_review', 'declined', 'no reader could read the cited passage'), 'reading'),
            (e('local_compiler', 'rejected', 'dropped requirement: age >= 65'), 'planning'),
            (e('hosted_typed_interpreter', 'declined', 'not understood'), 'planning'),
            (e('local_compiler', 'rejected', 'PlanFence: definition changed since the plan was saved'), 'planning'),
            (e('local_compiler', 'rejected', 'execution failed: OperationalError: no such column: x'), 'execution'),
            (e('cohort_family_late', 'rejected', 'answer guard: notes say the patient is on insulin'), 'check'),
            (e('local_compiler', 'rejected', 'the question asks for a count; the plan returned a table'), 'check'),
            (e('typed_decider', 'skipped', 'Claude unavailable'), None),
            (e('local_compiler', 'accepted'), None),
        ]
        for entry, stage in cases:
            self.assertEqual(SD.stage_of(entry), stage, entry)

    def test_failed_stage_is_the_furthest_reached(self):
        log = [e('semantic_cache', 'no proposal'), e('local_compiler', 'rejected', 'dropped requirement: female'),
               e('cohort_family_late', 'rejected', 'answer shape: wrong-shaped answer withheld')]
        d = SD.attribute(log)
        self.assertEqual(d['failed_stage'], 'check')
        self.assertEqual(d['by_stage'], {'retrieval': 1, 'planning': 1, 'check': 1})
        ok = SD.attribute(log + [e('bottled_sql', 'accepted')], answered_by='bottled_sql')
        self.assertIsNone(ok['failed_stage']); self.assertTrue(ok['recovered'])
        self.assertEqual(SD.attribute([], error=RuntimeError('database is locked'))['failed_stage'], 'execution')

    def test_summarize_and_from_result(self):
        exc = RuntimeError('x'); exc.planner_receipt = {'proposers': [e('local_compiler', 'rejected', 'execution failed: boom')]}
        rows = [{'stage_diagnostics': SD.from_result(exc=exc)},
                {'stage_diagnostics': SD.from_result({'answer': {'status': 'answered'}, 'planner_receipt': {'interpretation': {
                    'interpreter': 'semantic_cache', 'proposers': [e('semantic_cache', 'accepted')]}}})}]
        s = SD.summarize(rows)
        self.assertEqual(s['failed_stage'], {'execution': 1, 'answered': 1})


class CascadeWiring(unittest.TestCase):
    def test_finish_and_decline_carry_stage_diagnostics(self):
        c = proposers.Cascade.__new__(proposers.Cascade)
        c.log = [e('semantic_cache', 'no proposal')]; c.calls = 0; c.usd = 0.0; c.cost_known = True
        c.routing = c.learned_routing = None
        out = c.finish({}, 'local_compiler')
        self.assertIsNone(out['stage_diagnostics']['failed_stage'])
        self.assertEqual(out['stage_diagnostics']['by_stage'], {'retrieval': 1})

    def test_usage_tracer_rows_carry_failed_stage(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools'))
        import component_usage as CU
        class Conv:
            @staticmethod
            def handle_compose(stack, body):
                exc = ValueError('not understood')
                exc.planner_receipt = {'proposers': [e('local_compiler', 'rejected', 'execution failed: no such table')]}
                raise exc
        row = CU.ask(None, Conv, {'id': 'q1', 'question': 'how many?'}, {})
        self.assertEqual(row['failed_stage'], 'execution')
        json.dumps(row, default=str)


if __name__ == '__main__':
    unittest.main()
