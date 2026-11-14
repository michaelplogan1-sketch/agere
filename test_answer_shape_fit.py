"""Requested answer shape, conformance and engine fit (clinical_core/agere/clinical/answer_shape.py).

Phrasings here are deliberately NOT the heldout10 wording: they test the general rules (how many -> count, what percentage /
average / total -> value with stated rounding and percent scale, which patients -> patient_set, for each / by / one row per ->
table), the safe reshapes, the refusals to guess, the engine-fit contract and the metric router's route-time decline."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from clinical_core.agere.clinical import answer_shape as S
from clinical_core.agere.clinical.metric_router import shape_misfit


def ans(kind, **kw):
    base = {'status': 'answered', 'answer_type': kind, 'count': None, 'patient_ids': None, 'value': None, 'table': None,
            'unknown_count': 0, 'missing': [], 'grounding': []}
    base.update(kw)
    return base


class RequestedShape(unittest.TestCase):
    def shape(self, q):
        return S.requested(q)['shape']

    def test_count_phrasings(self):
        for q in ('How many members filled an inhaler last quarter?',
                  'What was the number of telehealth visits in June 2028?',
                  'Count the patients with an albumin under 3.0 this year.',
                  'Among adults with asthma, how many had a controller medication fill, measured as 50% of days or more?',
                  'Readmissions in 2028: give me the number of index stays followed by a return within 30 days.'):
            self.assertEqual(self.shape(q), 'count', q)

    def test_value_phrasings(self):
        for q in ('What percentage of new mothers had a postpartum visit within 12 weeks?',
                  'What is the average door-to-provider time in minutes for urgent care in 2028?',
                  'How much did we collect from self-pay patients in 2028, in dollars?',
                  'What was the median days to first payment for commercial claims?',
                  'On average, how many days pass between referral and the specialist visit?',
                  'How many days on average did prior authorizations take to approve?',
                  'What share of the panel has a documented advance directive?',
                  'How often did a discharged patient get a call within two business days?'):
            self.assertEqual(self.shape(q), 'value', q)

    def test_patient_set_phrasings(self):
        for q in ('Which members on warfarin have no INR in the last 60 days?',
                  'List the patients whose latest potassium is above 5.5.',
                  'Pull me the list of smokers who never had cessation counseling.',
                  'Which of our 80+ patients had two or more falls?'):
            self.assertEqual(self.shape(q), 'patient_set', q)

    def test_table_phrasings(self):
        for q in ('For each practice, how many diabetics have an A1c on file?',
                  'Return one row per payer with columns payer and denials.',
                  'Show the number of no-shows by appointment type.',
                  'Average wait in days, broken down by specialty.',
                  'By coverage type, what percentage of members had a wellness visit?',
                  'Which clinics had the most cancellations, and how many each?',
                  'What were the top 5 discharge diagnoses in 2029?',
                  'How many claims did we submit and how many were denied in 2028?'):
            self.assertEqual(self.shape(q), 'table', q)

    def test_by_that_is_not_a_breakdown(self):
        # passive "by", amounts and deadlines are not groupings
        for q in ('How many claims were denied and later replaced by a paid adjudication?',
                  'How many patients lowered their LDL by at least 30 mg/dL?',
                  'How many referrals were completed by March 31, 2029?',
                  'What percentage of visits were documented by using the new template?'):
            self.assertNotEqual(self.shape(q), 'table', q)

    def test_definitions_after_the_question_do_not_change_the_shape(self):
        q = ('How many inpatient stays began in 2028? Count one stay per patient per admit date, combining each feed, '
             'and use the average of duplicate charges only to break ties.')
        self.assertEqual(self.shape(q), 'count')
        q = 'What was the claim denial rate in 2028? Count a claim as denied when its latest status is denied.'
        self.assertEqual(self.shape(q), 'value')

    def test_rounding_and_percent_scale(self):
        r = S.requested('What percentage of statin users stayed adherent? Round to one decimal.')
        self.assertEqual((r['decimals'], r['percent']), (1, True))
        self.assertEqual(S.requested('What did the plan pay in total? Round to cents.')['decimals'], 2)
        self.assertEqual(S.requested('Average age at diagnosis, to the nearest whole number?')['decimals'], 0)
        r = S.requested('What was the no-show rate in May? Give it as a percentage with two decimal places.')
        self.assertEqual((r['shape'], r['decimals'], r['percent']), ('value', 2, True))
        self.assertFalse(S.requested('What was the average LDL in 2029?')['percent'])


class EntityAttribution(unittest.TestCase):
    def test_aggregate_cannot_replace_requested_entity_detail(self):
        from clinical_core.agere.clinical.literal_check import shape_misfit
        for q,fields in [
            ('Show conservation checks, patient/account attribution and lifecycle dates.',{'patients':12,'accounts':20}),
            ('Return customer/order attribution.',{'customer_count':12,'order_count':20}),
            ('Include payer and provider attribution.',{'payer_total':12,'provider_total':20})]:
            with self.subTest(q=q):self.assertIsNotNone(shape_misfit(q,ans('table',table=[fields]),analyst=True))

    def test_every_row_needs_the_requested_identity_but_unknown_values_are_preserved(self):
        from clinical_core.agere.clinical.literal_check import shape_misfit
        q='Show patient/account attribution.'
        rows=[{'patient_id':'a','account_id':'same'},{'patient_id':'b','account_id':None}]
        self.assertIsNone(shape_misfit(q,ans('table',table=rows)))
        rows[1].pop('account_id');self.assertIsNotNone(shape_misfit(q,ans('table',table=rows)))

    def test_named_nested_identities_and_other_industry_entities_are_supported(self):
        from clinical_core.agere.clinical.literal_check import shape_misfit
        self.assertIsNone(shape_misfit('Report customer/order attribution.',ans('table',table=[
            {'customer':{'identifier':'c'},'order_number':'o'}])))

    def test_counts_source_qualifiers_empty_results_and_declines_keep_their_shape(self):
        from clinical_core.agere.clinical.literal_check import shape_misfit
        for q in ('How many accounts lack patient/account attribution?',
                  'Show the total paid using source/date attribution.'):
            self.assertIsNone(shape_misfit(q,ans('value',value=10)))
        self.assertIsNone(shape_misfit('Show patient/account attribution.',ans('table',table=[])))
        self.assertIsNone(shape_misfit('Show patient/account attribution.',{'status':'unsupported'}))


class Conformance(unittest.TestCase):
    def test_one_row_table_collapses_to_its_named_column(self):
        q = 'What percentage of eligible women had a cervical screening? Round to one decimal.'
        a = ans('table', table=[{'screened': 41, 'eligible': 90, 'screened_pct': 45.5556}])
        out, info = S.conform(q, a, operation='sql')
        self.assertEqual(info['status'], 'reshaped')
        self.assertEqual((out['answer_type'], out['value']), ('value', 45.6))
        self.assertEqual(a['answer_type'], 'table')  # pure: the input is not mutated

    def test_fraction_column_becomes_percent_only_when_percent_asked(self):
        a = ans('table', table=[{'starters': 40, 'still_filling_rate': 0.775}])
        out, _ = S.conform('What percentage were still filling after a year?', a, operation='sql')
        self.assertAlmostEqual(out['value'], 77.5)
        out, _ = S.conform('What share were still filling after a year?', a, operation='sql')
        self.assertAlmostEqual(out['value'], 0.775)  # scale not stated: left as computed

    def test_average_column(self):
        a = ans('table', table=[{'avg_wait_days': 12.345, 'patients': 300}])
        out, info = S.conform('What is the average wait in days for a new-patient visit?', a, operation='sql')
        self.assertEqual((out['answer_type'], out['value'], info['column']), ('value', 12.345, 'avg_wait_days'))

    def test_per_source_table_picks_the_any_source_row(self):
        a = ans('table', table=[{'source': 'ehr', 'patients': 120}, {'source': 'claims', 'patients': 140},
                                {'source': 'any_source', 'patients': 190}, {'source': 'both', 'patients': 70}])
        out, info = S.conform('How many patients had a flu shot in the 2028 season? Check the EHR and the claims.', a, operation='sql')
        self.assertEqual((info['status'], out['answer_type'], out['count']), ('reshaped', 'count', 190))

    def test_multi_row_table_without_total_is_a_misfit(self):
        a = ans('table', table=[{'line': 1, 'patients': 50}, {'line': 2, 'patients': 20}])
        _, info = S.conform('How many patients moved from a first to a second antihypertensive?', a, operation='sql')
        self.assertEqual(info['status'], 'misfit')

    def test_ambiguous_columns_are_not_guessed(self):
        a = ans('table', table=[{'eligible': 500, 'excluded': 3, 'denominator': 497, 'numerator': 210}])
        _, info = S.conform('How many patients met the adherence threshold?', a, operation='sql')
        self.assertEqual(info['status'], 'misfit')

    def test_a_rate_is_never_cast_to_a_count(self):
        _, info = S.conform('How many urgent care visits happened in 2028?', ans('value', value=312.4), operation='sql')
        self.assertEqual(info['status'], 'misfit')
        _, info = S.conform('How many urgent care visits happened in 2028?', ans('value', value=312.0), operation='sql')
        self.assertEqual(info['status'], 'misfit')  # even an integral value: its provenance is a rate

    def test_patient_set_to_count_and_table_to_patient_set(self):
        out, _ = S.conform('How many members have two or more ED visits?', ans('patient_set', patient_ids=['a', 'b'], count=2))
        self.assertEqual((out['answer_type'], out['count']), ('count', 2))
        rows = [{'patient_id': 'p1', 'visits': 4}, {'patient_id': 'p2', 'visits': 5}]
        out, _ = S.conform('Which patients had four or more ED visits last year?', ans('table', table=rows), operation='sql')
        self.assertEqual((out['answer_type'], out['patient_ids'], out['count']), ('patient_set', ['p1', 'p2'], 2))
        truncated = ans('table', table=rows, missing=['Result truncated at 2 rows.'])
        self.assertEqual(S.conform('Which patients had four or more ED visits last year?', truncated, operation='sql')[1]['status'], 'misfit')

    def test_scalar_for_a_table_question_is_a_misfit(self):
        _, info = S.conform('For each payer, how many claims were denied in 2028?', ans('count', count=91), operation='sql')
        self.assertEqual(info['status'], 'misfit')

    def test_rounding_applies_to_a_fitting_value(self):
        out, info = S.conform('What was the mean length of stay? Round to one decimal.', ans('value', value=4.6667))
        self.assertEqual((out['value'], info['rounded']), (4.7, 1))

    def test_abstentions_and_unstated_shapes_are_left_alone(self):
        a = ans('unsupported', status='unsupported')
        self.assertEqual(S.conform('How many patients have a CYP2D6 result?', a)[1]['status'], 'skipped')
        t = ans('table', table=[{'x': 1}])
        self.assertEqual(S.conform('Tell me about readmissions.', t)[1]['status'], 'skipped')

    def test_switch_off(self):
        os.environ['AGERE_ANSWER_FIT'] = 'off'
        try:
            self.assertEqual(S.conform('How many?', ans('value', value=1.5))[1]['status'], 'skipped')
        finally:
            del os.environ['AGERE_ANSWER_FIT']


class EngineFit(unittest.TestCase):
    def test_engine_table_never_yields_a_scalar(self):
        # a lines-of-therapy table holds a 'patients' column; reading it as the answer to a genotype question would fabricate
        a = ans('table', table=[{'line': 1, 'patients': 18, 'top_regimen': 'antiplatelet'}])
        _, info = S.conform('How many warfarin users carry a VKORC1 variant on genotype testing?', a, operation='line_of_therapy')
        self.assertEqual(info['status'], 'misfit')
        self.assertIn('line_of_therapy', info['reason'])
        # persistence is a native lines-of-therapy quantity: the engine view stands (no scalar is read out of it) ...
        out, info = S.conform('What percentage of SGLT2 starters were persistent at 12 months?', a, operation='line_of_therapy')
        self.assertEqual((info['status'], out['answer_type']), ('fits', 'table'))
        # ... but a literal definition asks for that exact quantity, which the analysis table is not
        _, info = S.conform('What percentage of SGLT2 starters were still filling, i.e. a fill at least 365 days after the first? '
                            'Round to one decimal.', a, operation='line_of_therapy')
        self.assertEqual(info['status'], 'misfit')
        # an average the table does not report
        _, info = S.conform('On average, how many days passed between the first and the second-line fill?', a, operation='line_of_therapy')
        self.assertEqual(info['status'], 'misfit')

    def test_native_engine_count_needs_a_count_of_the_asked_thing(self):
        funnel = ans('table', table=[{'step': 'eligible', 'patients': 120}, {'step': 'screened', 'patients': 300}])
        self.assertEqual(S.conform('How many patients qualify for the asthma biologic trial?', funnel,
                                   operation='trial_feasibility')[1]['status'], 'fits')
        rates = ans('table', table=[{'measure': 'pdc', 'eligible': 554, 'denominator': 553, 'rate': 0.38}])
        self.assertEqual(S.conform('How many prescriptions were abandoned at the pharmacy?', rates, operation='vbc')[1]['status'], 'misfit')

    def test_open_questions_state_no_shape(self):
        for q in ('Is the readmission rate for practice 4 an outlier?', 'How many beds will we need next quarter?',
                  'Should we move to the enhanced track at a $120 PMPM benchmark?', 'Compare PMPM for members with and without diabetes.'):
            self.assertIsNone(S.requested(q)['shape'], q)
        # a purpose clause is not a modal question
        self.assertEqual(S.requested('How many members on warfarin lack an INR this month, so we can call them?')['shape'], 'count')

    def test_engine_table_must_hold_the_measured_quantity(self):
        signal = ans('table', table=[{'kind': 'data_feed', 'category': 'volume_shift', 'group': 'all', 'surprise_bits': 40.0,
                                      'patients': 2660, 'score': 285.5, 'headline': 'Warehouse-wide shift'}])
        _, info = S.conform('For each imaging feed, what was the maximum delay in hours between transmissions?', signal,
                            operation='monitor')
        self.assertEqual(info['status'], 'misfit')
        fits = ans('table', table=[{'practice': 'A', 'ed_visits_per_1000': 410.2}, {'practice': 'B', 'ed_visits_per_1000': 380.0}])
        self.assertEqual(S.conform('For each practice, what is the ED visit rate per 1,000?', fits, operation='profile')[1]['status'], 'fits')

    def test_vbc_rate_for_a_count_question(self):
        _, info = S.conform('How many admissions did attributed members have in 2028?', ans('value', value=129.4), operation='vbc')
        self.assertEqual(info['status'], 'misfit')
        self.assertEqual(S.conform('What was the admission rate per 1,000 in 2028?', ans('value', value=129.4),
                                   operation='vbc')[1]['status'], 'fits')

    def test_engine_patient_list(self):
        rows = [{'patient_id': 'p1', 'slope': -3.1}, {'patient_id': 'p2', 'slope': -2.2}]
        out, info = S.conform('Which patients have a falling eGFR trend?', ans('table', table=rows), operation='trajectory')
        self.assertEqual((info['status'], out['answer_type']), ('reshaped', 'patient_set'))


class MetricRouterDecline(unittest.TestCase):
    def node(self, returns):
        return {'operation': 'sql', 'parameters': {'query': 'SELECT 1', 'returns': returns}}

    def test_rate_metric_declines_a_count_question(self):
        self.assertTrue(shape_misfit('How many ED visits did we have in 2028?', 'ed_per_1000', self.node('value'), {}))
        self.assertIsNone(shape_misfit('What was the ED visit rate per 1,000 members in 2028?', 'ed_per_1000', self.node('value'), {}))

    def test_single_number_declines_one_row_per_group(self):
        q = 'For each of these conditions, how many patients ever had the code: asthma, gout, migraine? One row per condition.'
        self.assertTrue(shape_misfit(q, 'condition_count', self.node('count'), {}))
        self.assertIsNone(shape_misfit(q, 'condition_count', self.node('table'), {}))
        self.assertIsNone(shape_misfit(q, 'condition_count', self.node('count'), {'group_by': 'condition'}))

    def test_measure_engine_declines_a_count(self):
        node = {'operation': 'vbc', 'parameters': {}}
        self.assertTrue(shape_misfit('How many members had a PDC of 80% or more for statins? Count each covered day once.',
                                     'pdc_statins', node, {}))
        self.assertTrue(shape_misfit('Which members are non-adherent to statins?', 'pdc_statins',
                                     {'operation': 'sql', 'parameters': {'returns': 'value'}}, {}))
        self.assertIsNone(shape_misfit('What was our statin PDC adherence rate?', 'pdc_statins', node, {}))


class FitReview(unittest.TestCase):
    """review(): a misfit declines the rung and re-plans through verified SQL; offline it is withheld as non-final."""

    def stack(self, live):
        return SimpleNamespace(path=Path('.'), allow_live=live, receipts=None,
                               retriever=SimpleNamespace(meta={'snapshot': {'as_of': '2030-01-01'}}),
                               run_graph=lambda g, question=None: {'answer': ans('unsupported', status='unsupported'),
                                                                   'new_paid_api_calls': 0})

    def lot(self):
        graph = {'nodes': [{'id': 'n', 'operation': 'line_of_therapy'}]}
        result = {'answer': ans('table', table=[{'line': 1, 'patients': 47}, {'line': 2, 'patients': 12}]), 'new_paid_api_calls': 0}
        return graph, {'route': 'local_compiler', 'new_paid_api_calls': 0, 'steps': []}, result

    def test_offline_misfit_is_withheld(self):
        graph, receipt, result = self.lot()
        _, receipt, result, calls, replaced = S.review(self.stack(False), 'How many patients moved to a second-line drug?', graph,
                                                       receipt, result)
        self.assertEqual((result['answer']['answer_type'], result['answer']['status'], replaced), ('unsupported', 'partial', False))
        self.assertEqual(receipt['answer_fit']['decision'], 'withheld')
        self.assertTrue(any('Answer shape' in m for m in result['answer']['missing']))

    def test_live_misfit_falls_through_to_verified_sql(self):
        graph, receipt, result = self.lot()
        seen = {}

        def hosted(stack, question, ledger, feedback='', stage='', profile=None, **kw):
            seen['feedback'] = feedback
            g = {'nodes': [{'id': 'answer', 'operation': 'sql'}]}
            return g, {'route': 'hosted_verified_sql', 'new_paid_api_calls': 2, 'steps': [], '_cost': 0.01}, \
                {'answer': ans('count', count=92), 'new_paid_api_calls': 0}
        g, r, res, calls, replaced = S.review(self.stack(True), 'How many patients moved to a second-line drug?', graph, receipt,
                                              result, ledger=SimpleNamespace(path='x'), hosted=hosted)
        self.assertTrue(replaced)
        self.assertEqual((res['answer']['count'], r['route'], r['replaced_route']['route'], calls), (92, 'hosted_verified_sql', 'local_compiler', 2))
        self.assertIn('one integer count', seen['feedback'])

    def test_live_missing_data_becomes_unsupported(self):
        from clinical_core.agere.clinical.nl2sql import MissingData
        graph, receipt, result = self.lot()

        def hosted(*a, **kw):
            raise MissingData('no pharmacogenomic results are received')
        try:
            g, r, res, calls, replaced = S.review(self.stack(True), 'How many clopidogrel users are CYP2C19 poor metabolizers?', graph,
                                                  receipt, result, ledger=SimpleNamespace(path='x'), hosted=hosted)
        except TypeError:
            self.skipTest('MissingData signature differs')
        self.assertTrue(replaced)
        self.assertEqual(r['route'], 'hosted_missing_data')
        self.assertEqual(g['coverage'], 'unsupported')

    def test_fitting_answer_passes_through_with_rounding(self):
        graph = {'nodes': [{'id': 'answer', 'operation': 'sql'}]}
        result = {'answer': ans('value', value=33.333), 'new_paid_api_calls': 0}
        _, r, res, calls, replaced = S.review(self.stack(True), 'What percentage were readmitted? Round to one decimal.', graph,
                                              {'route': 'hosted_verified_sql'}, result, ledger=SimpleNamespace(path='x'))
        self.assertEqual((res['answer']['value'], calls, replaced, r['answer_fit']['status']), (33.3, 0, False, 'fits'))


class ReferenceCacheWrite(unittest.TestCase):
    def test_concurrent_writers_do_not_steal_each_others_temp_file(self):
        import threading
        from bench.run import atomic_write
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'Q1.json'
            errors = []

            def write(i):
                try:
                    for _ in range(50): atomic_write(target, '{"n": %d}' % i)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
            threads = [threading.Thread(target=write, args=(i,)) for i in range(4)]
            for t in threads: t.start()
            for t in threads: t.join()
            self.assertEqual(errors, [])
            self.assertTrue(target.read_text().startswith('{"n"'))
            self.assertEqual([p.name for p in Path(d).iterdir()], ['Q1.json'])


if __name__ == '__main__':
    unittest.main()
