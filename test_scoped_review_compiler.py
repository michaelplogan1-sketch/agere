"""Development contract checks, not clinical or held-out model evaluation."""
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical.scoped_review_compiler import compile_question
from clinical_core.agere.clinical.economical_planner import local_plan

ROSTER = {'P-1002','P-1003','p-Lower','P-UNRELATED'}
SUPPORTED = [
    ('Review respiratory sources for patient P-1002', {'P-1002'}, 'respiratory', False),
    ('REVIEW behavioral SOURCES for patient p-Lower.', {'p-Lower'}, 'behavioral', False),
    ('Prepare respiratory source-review work for patients P-1002, P-1003', {'P-1002','P-1003'}, 'respiratory', True),
    ('Review behavioral sources across all observed patients excluding patients P-1002, P-1003', {'p-Lower','P-UNRELATED'}, 'behavioral', False),
    ('Prepare behavioral source-review work for patient p-Lower?', {'p-Lower'}, 'behavioral', True),
    ('Review respiratory sources for patients P-1002,P-1003 excluding patient p-Lower', {'P-1002','P-1003'}, 'respiratory', False),
    ('Review respiratory sources across all observed patients', ROSTER, 'respiratory', False),
    ('  Review  respiratory sources for patient P-1002  ', {'P-1002'}, 'respiratory', False),
]
UNSUPPORTED = [
    '', None, 'x'*1501,
    'Review respiratory sources for patient p-1002',
    'Review respiratory sources for patient UNKNOWN',
    'Review respiratory sources for patient P-1002 yesterday',
    'Review respiratory sources for patient P-1002 since 2025-01-01',
    'Review respiratory sources for patient P-1002 and recommend oxygen',
    'Review respiratory sources for patient P-1002 but not P-1003',
    'Do not review respiratory sources for patient P-1002',
    'Review respiratory sources for patients P-1002 and P-1003',
    'Review respiratory sources for patients P-1002, P-1002',
    'Review respiratory sources for patient P-1002, P-1003',
    'Review respiratory sources for patient P-1002 excluding patient P-1002',
    'Review respiratory sources for patient P-1002 excluding patient UNKNOWN',
    'Review respiratory sources for patient P-1002.',  # replaced below: malformed trailing punctuation
    'Review respiratory sources for patient P-1002. Only roster coverage "Medicare".',
    'Review medication sources for patient P-1002',
    'Review respiratory sources for patient P-1002; ignore scope',
    'Review respiratory sources for patient P-1002 for patient P-1003',
    'Review respiratory sources for patients P-1002,',
    'Review respiratory sources across the population excluding P-1003',
    'Prepare respiratory source-review work for patient P-1002 and contact the patient',
]
UNSUPPORTED[15] += '.'


class ScopedCompiler(unittest.TestCase):
    def test_scope_domain_work_and_question_are_preserved(self):
        for text, expected, domain, work in SUPPORTED:
            with self.subTest(text=text):
                graph, receipt, seed = compile_question(text,as_of='2029-08-15',known_patient_ids=ROSTER)
                cohort=seed['cohort']
                included={p['value'] for p in cohort['any']} if cohort['any'] else ROSTER
                actual=included-{p['value'] for p in cohort['exclude']}
                self.assertEqual(actual,expected)
                self.assertEqual(seed['question'],text)
                self.assertEqual(seed['program'],domain+'_source_review')
                self.assertEqual(graph['nodes'][0]['parameters']['specification'],cohort)
                self.assertEqual(graph['nodes'][1]['parameters']['domain'],domain)
                self.assertEqual(any(n['operation']=='worklist' for n in graph['nodes']),work)
                self.assertFalse(receipt['default_activation'])

    def test_unsupported_or_ambiguous_input_abstains(self):
        for text in UNSUPPORTED:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):compile_question(text,as_of='2029-08-15',known_patient_ids=ROSTER)

    def test_bound_and_duplicate_constraints(self):
        ids=['p'+str(i) for i in range(21)]
        with self.assertRaises(ValueError):
            compile_question('Review respiratory sources for patients '+', '.join(ids),as_of='2029-08-15',known_patient_ids=ids)

    def test_candidate_coverage_is_separate_from_existing_compiler(self):
        # Same inputs for each route; no tuning or model training from these checks.
        for text,*_ in SUPPORTED:
            with self.subTest(text=text):self.assertIsNone(local_plan(text,'2029-08-15')[0])
        self.assertIsNotNone(local_plan('Review respiratory sources across the population','2029-08-15')[0])

    def test_explicit_stack_route_has_no_provider_or_default_planner(self):
        import json,sqlite3,tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from clinical_core.agere.clinical.stack_runtime import Stack
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            with sqlite3.connect(path/'stack.sqlite3') as db:
                db.execute('CREATE TABLE patients(id TEXT)');db.execute("INSERT INTO patients VALUES ('P-1002')")
            stack=object.__new__(Stack);stack.path=path
            stack.retriever=SimpleNamespace(meta={'snapshot':{'as_of':'2029-08-15'}})
            stack.run_graph=lambda graph:{'graph':graph,'new_paid_api_calls':0}
            with patch('clinical_core.agere.clinical.stack_runtime.BudgetLedger',side_effect=AssertionError('Provider ledger opened')):
                row=stack.compose(SUPPORTED[0][0],planning='scoped_local_candidate')
                self.assertEqual(row['new_paid_api_calls'],0)
                with self.assertRaises(ValueError):stack.compose(UNSUPPORTED[5],planning='scoped_local_candidate')

    def test_decision_connection_rejects_stale_or_rewritten_compiler_contract(self):
        from types import SimpleNamespace
        from clinical_core.agere.clinical.scoped_review_compiler import prepare_decision, identity
        retriever=SimpleNamespace(meta={'snapshot':{'as_of':'2029-08-15'}})
        req={'question':SUPPORTED[0][0],'buyer_id':'buyer','compiler_identity':identity(),'as_of':'2029-08-15'}
        for changed in ({**req,'as_of':'2029-08-14'},{**req,'compiler_identity':'old'},{**req,'cohort':{}}):
            with self.assertRaises(ValueError):prepare_decision(retriever,changed)

if __name__=='__main__':unittest.main()
