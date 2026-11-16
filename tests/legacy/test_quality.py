"""Quality engineering: FMEA file schema and RPN math, answer-level poka-yoke controls, DPMO/sigma math, defect report
and the /scorecard page (fixtures and empty data). Synthetic fixtures only; no service, model or network."""
import copy
import importlib.util
import inspect
import json
import re
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere import scorecard_view
from clinical_core.agere.clinical import analysis_graph, answer_checks

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('quality_defects', ROOT / 'tools' / 'quality' / 'defects.py')
defects = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(defects)
AS_OF = '2029-08-15'


class FmeaFile(unittest.TestCase):
    def setUp(self):
        self.fmea = defects.load_fmea()

    def test_schema_is_valid_and_covers_every_stage(self):
        self.assertEqual(defects.validate_fmea(self.fmea), [])
        stages = {m['stage'] for m in self.fmea['modes']}
        self.assertEqual(stages, {'question_parse', 'routing', 'population', 'definitions', 'sql_generation', 'verification',
                                  'execution', 'answer_rendering', 'follow_ups', 'learning_reuse'})

    def test_rpn_math_and_ranking(self):
        modes = self.fmea['modes']
        for m in modes:
            self.assertEqual(m['rpn'], m['severity'] * m['occurrence'] * m['detection'], m['id'])
            self.assertEqual(m['rpn_before'], m['severity'] * m['occurrence'] * m['detection_before'], m['id'])
            self.assertTrue(m['occurrence_basis'])
        self.assertEqual([m['rank'] for m in modes], list(range(1, len(modes) + 1)))
        self.assertEqual([m['rpn'] for m in modes], sorted((m['rpn'] for m in modes), reverse=True))

    def test_added_controls_exist(self):
        documented = set(re.findall(r'\b(QC-[A-Z]+)\b', answer_checks.__doc__))
        # controls outside the answer checks (bench audits, clinical-task checks, gates) are documented in FMEA.md's control table
        fmea_doc = (Path(__file__).resolve().parents[2] / 'docs/agere/FMEA.md').read_text()
        documented |= {c for m in self.fmea['modes'] for c in m['controls_added'] if c in fmea_doc}
        for m in self.fmea['modes']:
            self.assertLessEqual(set(m['controls_added']), documented, m['id'])
            if m['controls_added']: self.assertLess(m['detection'], m['detection_before'], m['id'])

    def test_validator_catches_bad_rpn_and_scale(self):
        bad = copy.deepcopy(self.fmea)
        bad['modes'][0]['rpn'] += 1
        bad['modes'][1]['severity'] = 11
        problems = defects.validate_fmea(bad)
        self.assertTrue(any('rpn != S x O x D' in p for p in problems))
        self.assertTrue(any('severity must be an integer' in p for p in problems))

    def test_scoring_from_evidence(self):
        evidence = {'adjudicated_counts': {'FM-08': 7, 'FM-18': 22}, 'adjudicated_examples': {'FM-08': ['N:N41']}, 'adjudicated_opportunities': 132,
                    'hosted_engine_errors': 0, 'mechanical_counts': {}, 'mechanical_opportunities': 0, 'unanswerable_opportunities': 0,
                    'replay': {}, 'acceptance': None}
        scored = {m['id']: m for m in defects.score_fmea(self.fmea, evidence)['modes']}
        self.assertEqual(scored['FM-08']['occurrence'], 9)       # 7/132 = 5.3% -> band >= 5%
        self.assertEqual(scored['FM-18']['occurrence'], 10)      # 22/132 = 16.7%
        self.assertEqual(scored['FM-11']['occurrence'], 2)       # measurable, not observed
        self.assertEqual(scored['FM-20']['occurrence'], scored['FM-20']['occurrence_judgment'])  # not measurable -> judgment
        self.assertIn('judgment', scored['FM-20']['occurrence_basis'])
        for m in scored.values(): self.assertEqual(m['rpn'], m['severity'] * m['occurrence'] * m['detection'])

    def test_occurrence_bands(self):
        bands = self.fmea['scales']['occurrence_bands']
        self.assertEqual(defects.occurrence_rating(0.0, 0, bands), 2)
        self.assertEqual(defects.occurrence_rating(0.0005, 1, bands), 3)
        self.assertEqual(defects.occurrence_rating(0.015, 2, bands), 7)
        self.assertEqual(defects.occurrence_rating(0.5, 20, bands), 10)

    def test_markdown_table_matches_json(self):
        md = (ROOT / 'docs' / 'agere' / 'FMEA.md').read_text()
        self.assertEqual(md.split('<!-- fmea-table:start -->')[1].split('<!-- fmea-table:end -->')[0].strip(), defects.fmea_table(self.fmea).strip())


class SixSigmaMath(unittest.TestCase):
    def test_dpmo_and_sigma(self):
        self.assertEqual(defects.dpmo(11, 44), 250000)
        self.assertIsNone(defects.dpmo(1, 0))
        self.assertAlmostEqual(defects.sigma_level(308537), 2.0, places=3)
        self.assertAlmostEqual(defects.sigma_level(66807), 3.0, places=3)
        self.assertAlmostEqual(defects.sigma_level(3.4), 6.0, places=2)
        self.assertAlmostEqual(defects.sigma_level(30000), 3.38, places=2)
        self.assertIsNone(defects.sigma_level(0)); self.assertIsNone(defects.sigma_level(1e6))

    def test_wilson_interval(self):
        lo, hi = defects.wilson(11, 44)
        self.assertAlmostEqual(lo, 0.1458, places=3); self.assertAlmostEqual(hi, 0.3944, places=3)
        lo, hi = defects.wilson(0, 44)
        self.assertEqual(lo, 0.0); self.assertGreater(hi, 0.05)
        self.assertIsNone(defects.wilson(0, 0))

    def test_p_chart_flags_out_of_control_run(self):
        pts = [{'label': str(i), 'defects': d, 'opportunities': 44} for i, d in enumerate([10, 11, 9, 12, 10, 40])]
        chart = defects.p_chart(pts)
        self.assertEqual(chart['kind'], 'p')
        self.assertEqual(chart['points'][-1]['signal'], 'above')
        self.assertTrue(all(p['signal'] is None for p in chart['points'][:5]))
        self.assertEqual(defects.p_chart(pts[:1])['center'], None)


def row(ident, *, correct, reason='correct', expected_type='count', answer=None, route='hosted_verified_sql', cost=0.01,
        question='How many?', error=None, started='2026-10-13T10:00:00+00:00'):
    answer = answer if answer is not None else {'status': 'answered', 'answer_type': expected_type, 'count': 1, 'missing': []}
    return {'id': ident, 'system': 'agere', 'question': question, 'as_of': AS_OF, 'started_at': started, 'error': error,
            'score': {'correct': correct, 'reason': reason, 'value_match': correct, 'correct_abstention': correct and expected_type == 'unsupported',
                      'false_assertion': False, 'covered': correct},
            'expected': {'status': 'unsupported' if expected_type == 'unsupported' else 'answered', 'answer_type': expected_type},
            'answer': answer, 'response': {'planner_receipt': {'route': route}}, 'estimated_cost_usd': cost}


def write_fixture(root):
    results = Path(root)
    (results / 'agere').mkdir(parents=True); (results / 'frontier_sql').mkdir(); (results / 'adjudications').mkdir(); (results / 'acceptance').mkdir()
    def dump(path, rows): path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    # Blind set X: 8 answerable + 2 unanswerable, first attempt 4 failures; re-run 2 failures (adjudicated: 1 engine error).
    first = [row(f'X0{i}', correct=i > 4, reason='correct' if i > 4 else 'answer_mismatch', started='2026-10-13T10:00:00+00:00') for i in range(1, 9)]
    first[0] = row('X01', correct=False, reason='answer_type_mismatch', answer={'status': 'unsupported', 'answer_type': 'unsupported', 'missing': ['x']},
                   route='hosted_missing_data', started='2026-10-13T10:00:00+00:00')
    first += [row('X09', correct=True, reason='correct_abstention', expected_type='unsupported', route='local_unsupported', cost=0.0,
                  answer={'status': 'unsupported', 'answer_type': 'unsupported', 'missing': ['race']}, started='2026-10-13T10:00:00+00:00'),
              row('X10', correct=True, reason='correct_abstention', expected_type='unsupported', route='local_unsupported', cost=None,
                  answer={'status': 'unsupported', 'answer_type': 'unsupported', 'missing': ['death']}, started='2026-10-13T10:00:00+00:00')]
    rerun = [dict(r, started_at='2026-10-13T11:00:00+00:00') for r in first]
    for r in rerun[2:4]: r['score'] = {**r['score'], 'correct': True, 'reason': 'correct'}
    rerun[1]['answer'] = {'status': 'answered', 'answer_type': 'value', 'value': 50.0, 'missing': [],
                          'query': "SELECT 100.0*sum(x)/count(*) FROM record WHERE stream = 'appointments'"}
    dump(results / 'agere' / '20261013T100000.000000Z.jsonl', first)
    dump(results / 'agere' / '20261013T110000.000000Z.jsonl', rerun)
    # Development regression run (C/D).
    dump(results / 'agere' / '20261013T090000.000000Z.jsonl',
         [row(f'C{i:02d}', correct=True, route='local_compiler', cost=0.0, started='2026-10-13T09:00:00+00:00') for i in range(1, 6)] +
         [row(f'D{i:02d}', correct=i != 5, reason='correct' if i != 5 else 'answer_mismatch', route='local_compiler', cost=0.0,
              started='2026-10-13T09:00:00+00:00') for i in range(1, 6)])
    dump(results / 'frontier_sql' / '20261013T100500.000000Z.jsonl',
         [dict(row(f'X0{i}', correct=i > 2, started='2026-10-13T10:05:00+00:00'), system='frontier_sql') for i in range(1, 9)] +
         [dict(row(f'X{i}', correct=True, reason='correct_abstention', expected_type='unsupported', started='2026-10-13T10:05:00+00:00',
                   answer={'status': 'unsupported', 'answer_type': 'unsupported', 'missing': ['x']}), system='frontier_sql') for i in ('09', '10')])
    (results / 'adjudications' / 'agere__20261013T110000.000000Z.json').write_text(json.dumps({
        'schema': 'bench-adjudication/1', 'system': 'agere', 'results_file': 'agere/20261013T110000.000000Z.jsonl', 'adjudicated_at': '2026-10-13T12:00:00Z',
        'provenance': 'fixture', 'counts': {'engine_error': 1, 'format_only': 1}, 'items_complete': True,
        'items': [{'id': 'X01', 'category': 'engine_error', 'reason': 'The engine wrongly claimed the data was missing.'},
                  {'id': 'X02', 'category': 'format_only', 'reason': 'Same numbers, <b>different</b> columns.'}]}))
    (results / 'acceptance' / '20261013T120000.json').write_text(json.dumps([
        {'question': 'q1', 'expect_operation': 'survival', 'route': 'local_compiler', 'pass': True, 'usd': 0.0},
        {'question': 'q2', 'expect_operation': 'monitor', 'route': 'operator_router', 'pass': False, 'usd': 0.001}]))
    return results


class DefectReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.results = write_fixture(self.tmp.name)
        self.report = defects.report(self.results)

    def tearDown(self):
        self.tmp.cleanup()

    def test_runs_sets_and_metrics(self):
        runs = {r['file']: r for r in self.report['runs']}
        first = runs['agere/20261013T100000.000000Z.jsonl']
        self.assertEqual((first['set'], first['split'], first['full']), ('X', 'blind', True))
        self.assertEqual((first['answerable'], first['unanswerable'], first['strict_defects']), (8, 2, 4))
        self.assertEqual(first['mechanical'], {'false_refusal': 1, 'wrong_value_unadjudicated': 3})
        self.assertEqual(first['strict_dpmo'], 500000)
        self.assertEqual(first['cost_unknown_rows'], 1)
        rerun = runs['agere/20261013T110000.000000Z.jsonl']
        self.assertTrue(rerun['adjudicated'])
        self.assertEqual((rerun['strict_defects'], rerun['engine_errors'], rerun['engine_dpmo']), (2, 1, 125000))
        self.assertEqual(runs['agere/20261013T090000.000000Z.jsonl']['set'], 'C+D')

    def test_trend_totals_pareto_and_status(self):
        s = self.report['blind_sets'][0]
        self.assertEqual((s['set'], s['runs'], s['first']['strict_defects'], s['latest']['strict_defects']), ('X', 2, 4, 2))
        self.assertEqual(s['frontier']['correct_answerable'], 6)
        t = self.report['totals']
        self.assertEqual((t['strict_first_attempt']['defects'], t['strict_first_attempt']['opportunities']), (4, 8))
        self.assertEqual((t['adjudicated']['defects'], t['adjudicated']['opportunities']), (1, 8))
        adj = {i['mode']: i['count'] for i in self.report['pareto_adjudicated']}
        self.assertEqual(adj, {'FM-04': 1, 'FM-18': 1})
        self.assertEqual(self.report['smoke']['passed'], True)
        self.assertEqual((self.report['acceptance']['passed'], self.report['acceptance']['total']), (1, 2))
        self.assertIn('QC-VERSION', self.report['evidence']['replay'])
        self.assertIn('hosted_verified_sql', self.report['routes'])

    def test_engine_error_classification(self):
        fmea = defects.load_fmea()
        cases = {'The engine counts every row in the visit table instead of de-duplicating by encounter_id': 'FM-08',
                 'The engine wrongly claimed medication data was missing and returned no answer': 'FM-04',
                 'The engine left out the roster age 18-85 denominator restriction': 'FM-05',
                 'The engine measured to first_available_date, the date the record arrived': 'FM-07',
                 'The question asks specifically for first-generation antihistamines': 'FM-01',
                 'The engine multiplied a day-granularity los_days field by 24': 'FM-14',
                 'The engine looked for a nonexistent service_received field': 'FM-11',
                 'Something nobody anticipated': 'unclassified'}
        for reason, mode in cases.items():
            self.assertEqual(defects.classify_engine_error(reason, fmea), mode, reason)

    def test_cli_runs_on_fixture(self):
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(defects.main(['--results', str(self.results), '--json']), 0)
        self.assertEqual(json.loads(out.getvalue())['schema'], 'agere-defects/1')


class AnswerChecks(unittest.TestCase):
    def checks(self, answer, question, as_of=AS_OF):
        before = copy.deepcopy(answer)
        out = answer_checks.annotate(answer, question, as_of)
        for k in ('status', 'answer_type', 'count', 'value', 'table', 'patient_ids'):
            self.assertEqual(out.get(k), before.get(k), f'{k} changed')  # heal-not-block: numbers and status untouched
        return {c['control']: c for c in out.get('quality_checks') or []}

    def test_partial_period(self):
        months = [{'month': f'2029-0{m}', 'visits': 100} for m in range(1, 9)]
        c = self.checks({'status': 'answered', 'answer_type': 'table', 'table': months}, 'ED visits by month')
        self.assertEqual(c['QC-PERIOD']['partial_period']['period'], '2029-08')
        self.assertEqual((c['QC-PERIOD']['partial_period']['days_observed'], c['QC-PERIOD']['partial_period']['days_in_period']), (15, 31))
        quarters = [{'quarter': '2029-Q2', 'n': 3}, {'quarter': '2029-Q3', 'n': 1}]
        self.assertEqual(answer_checks.partial_period(quarters, AS_OF)['granularity'], 'quarter')
        self.assertEqual(answer_checks.partial_period([{'year': 2028, 'n': 1}, {'year': 2029, 'n': 2}], AS_OF)['days_in_period'], 365)
        self.assertIsNone(answer_checks.partial_period(months[:7], AS_OF))               # ends in a complete month
        self.assertIsNone(answer_checks.partial_period(months, '2029-08-31'))           # last day of the month: complete
        self.assertIsNone(answer_checks.partial_period([{'payer': 'a', 'n': 1}, {'payer': 'b', 'n': 2}], AS_OF))
        weeks = [{'week': '2029-08-06', 'n': 1}, {'week': '2029-08-13', 'n': 1}]
        self.assertEqual(answer_checks.partial_period(weeks, AS_OF)['granularity'], 'week')

    def test_units(self):
        c = self.checks({'status': 'answered', 'answer_type': 'table', 'table': [{'payer': 'a', 'denial_pct': 140.0}]}, 'Denial percent by payer')
        self.assertIn('outside 0-100', c['QC-UNITS']['message'])
        c = self.checks({'status': 'answered', 'answer_type': 'table', 'table': [{'payer': 'a', 'paid_cents': 12345}]}, 'How much did each payer pay us in dollars?')
        self.assertIn('cents', c['QC-UNITS']['message'])
        c = self.checks({'status': 'answered', 'answer_type': 'value', 'value': 48.0, 'query': 'SELECT avg(los_days * 24) FROM stay'},
                        'Average hours in observation?')
        self.assertIn('multiplying a whole-day length of stay by 24', c['QC-UNITS']['message'])
        ok = self.checks({'status': 'answered', 'answer_type': 'table', 'table': [{'payer': 'a', 'paid_usd': 123.45, 'share_pct': 40.0}]}, 'How much did each payer pay us?')
        self.assertNotIn('QC-UNITS', ok)

    def test_denominator(self):
        answer = {'status': 'answered', 'answer_type': 'value', 'value': 12.5,
                  'query': 'SELECT 100.0 * count(DISTINCT CASE WHEN x THEN p.patient_id END) / count(DISTINCT p.patient_id) FROM patient p'}
        c = self.checks(answer, 'What percent of diabetics have a statin?')
        self.assertEqual(c['QC-DENOM']['severity'], 'info')
        self.assertEqual(answer['denominator'], 'count(DISTINCT p.patient_id)')
        self.assertNotIn(c['QC-DENOM']['message'], answer.get('warnings') or [])   # info is not a warning
        bare = {'status': 'answered', 'answer_type': 'value', 'value': 12.5}
        c = self.checks(bare, 'What is our readmission rate?')
        self.assertEqual(c['QC-DENOM']['severity'], 'warning'); self.assertTrue(bare['warnings'])
        stated = {'status': 'answered', 'answer_type': 'value', 'value': 12.5, 'computed': 'readmissions among discharges in 2028'}
        self.assertNotIn('QC-DENOM', self.checks(stated, 'What is our readmission rate?'))
        self.assertNotIn('QC-DENOM', self.checks({'status': 'answered', 'answer_type': 'count', 'count': 3}, 'How many patients?'))

    def test_versioned_records(self):
        raw = lambda: {'status': 'answered', 'answer_type': 'count', 'count': 7750, 'query': "SELECT count(*) FROM record WHERE stream = 'appointments'"}
        c = self.checks(raw(), 'How many appointments were scheduled?')
        self.assertIn('current_record', c['QC-VERSION']['message']); self.assertEqual(c['QC-VERSION']['streams'], ['appointments'])
        for query in ("SELECT count(DISTINCT json_extract(payload,'$.appointment_id')) FROM record WHERE stream = 'appointments'",
                      "SELECT count(*) FROM current_record WHERE stream = 'appointments'",
                      "SELECT count(*) FROM (SELECT json_extract(payload,'$.claim_id') AS claim_id FROM record WHERE stream = 'medical_claims' GROUP BY claim_id)",
                      "SELECT sum(json_extract(payload,'$.payer_paid_delta_cents')) FROM record WHERE stream = 'pharmacy_claim_transactions'",
                      "SELECT count(*) FROM visit v WHERE NOT EXISTS (SELECT 1 FROM record r WHERE r.stream = 'encounters')",
                      "SELECT count(*) FROM record WHERE stream = 'labs'"):
            self.assertNotIn('QC-VERSION', self.checks({'status': 'answered', 'answer_type': 'count', 'count': 1, 'query': query}, 'How many appointments?'), query)
        self.assertNotIn('QC-VERSION', self.checks(raw(), 'How many appointment records have status no_show?'))

    def test_unused_numbers(self):
        q = 'How many patients aged 18-85 have an eGFR below 60 in 2028?'
        sql = "SELECT count(*) FROM patient WHERE age_latest BETWEEN 18 AND 85 AND event_date LIKE '2028%'"
        self.assertEqual(answer_checks.unused_numbers(q, sql, AS_OF), ['60'])
        c = self.checks({'status': 'answered', 'answer_type': 'count', 'count': 3, 'query': sql}, q)
        self.assertEqual((c['QC-NUMBERS']['fmea'], c['QC-NUMBERS']['unused']), ('FM-01', ['60']))
        self.assertEqual(answer_checks.unused_numbers('Repeat ED users in the past 12 months?', "SELECT 1 WHERE event_date >= '2028-08-15'", AS_OF), [])
        self.assertEqual(answer_checks.unused_numbers('Data through 2029-08-15: how many visits?', 'SELECT count(*) FROM visit', AS_OF), [])
        self.assertEqual(answer_checks.unused_numbers('Tetanus over 10 years ago?', "SELECT 1 WHERE recall = 'over_ten_years'", AS_OF), [])
        self.assertEqual(answer_checks.unused_numbers('Type 2 diabetes patients with A1c over 9%?', 'SELECT 1 WHERE value > 9', AS_OF), [])
        self.assertEqual(answer_checks.unused_numbers('Per 1,000 members?', 'SELECT 1000.0 * x', AS_OF), [])
        self.assertEqual(answer_checks.unused_numbers('Visits in 2027?', "SELECT 1 WHERE substr(d,1,4) = '2028'", AS_OF), ['2027'])

    def test_parts_and_clock(self):
        c = self.checks({'status': 'answered', 'answer_type': 'table', 'table': [{'payer': 'a', 'paid_usd': 10.0}]},
                        'How many payments did we get and how much in dollars, by payer?')
        self.assertIn('not the count', c['QC-PARTS']['message'])
        self.assertNotIn('QC-PARTS', self.checks({'status': 'answered', 'answer_type': 'table', 'table': [{'payer': 'a', 'payments': 3, 'paid_usd': 10.0}]},
                                                 'How many payments did we get and how much in dollars, by payer?'))
        clock = lambda: {'status': 'answered', 'answer_type': 'value', 'value': 17.9,
                         'query': 'SELECT avg(julianday(first_available_date) - julianday(service_date)) FROM claim'}
        c = self.checks(clock(), 'Average days from service to first adjudication for claims?')
        self.assertEqual(c['QC-CLOCK']['clocks'], ['first_available_date'])
        self.assertNotIn('QC-CLOCK', self.checks(clock(), 'How many days after service did claims arrive in the warehouse?'))

    def test_annotate_is_idempotent_safe_and_skips_unsupported(self):
        answer = {'status': 'answered', 'answer_type': 'table', 'table': [{'month': '2029-07', 'n': 1}, {'month': '2029-08', 'n': 1}], 'warnings': ['existing']}
        answer_checks.annotate(answer, 'by month', AS_OF); answer_checks.annotate(answer, 'by month', AS_OF)
        self.assertEqual(len(answer['quality_checks']), 1); self.assertEqual(len(answer['warnings']), 2); self.assertEqual(answer['warnings'][0], 'existing')
        unsupported = {'status': 'unsupported', 'answer_type': 'unsupported', 'missing': ['race']}
        self.assertNotIn('quality_checks', answer_checks.annotate(unsupported, 'race breakdown?', AS_OF))
        broken = answer_checks.annotate({'status': 'answered', 'answer_type': 'table', 'table': 'not a list', 'query': 5}, 'rate by month', AS_OF)
        self.assertIsInstance(broken, dict)  # a malformed answer is returned, never raised on

    def test_headline_shows_warnings_and_hook_is_wired(self):
        answer = {'status': 'answered', 'answer_type': 'table', 'table': [{'month': '2029-07', 'visits': 10}, {'month': '2029-08', 'visits': 4}]}
        answer_checks.annotate(answer, 'ED visits by month', AS_OF)
        self.assertIn('partial', analysis_graph.headline(answer))
        source = inspect.getsource(analysis_graph.execute)
        self.assertLess(source.index('quality_annotate(result'), source.index("result['answer']['headline']=headline"))


class Scorecard(unittest.TestCase):
    def test_renders_with_fixture_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = write_fixture(tmp)
            learning = {'routes': {'local_compiler': 3}, 'statuses': {'answered': 3}, 'verified_training_pairs': 2, 'corrections': 0,
                        'top_gaps': [{'reason': '<script>x</script>', 'count': 1}]}
            html = scorecard_view.page(scorecard_view.build(results), learning=learning, now='2026-10-13 12:00 UTC')
        self.assertTrue(html.startswith('<!doctype html>'))
        self.assertIn('<title>Agere Error Scorecard</title>', html)
        self.assertNotRegex(html, r'<script|<link[^>]+stylesheet|@import')   # self-contained, and fixture text is escaped
        self.assertIn('&lt;script&gt;', html)
        for text in ('Latest blind set: X', 'Trend across blind sets', 'Defects per million opportunities', 'Defect Pareto by failure mode',
                     'Cost per question by route', 'Smoke and acceptance', 'What counts as an error', 'Learning store',
                     '1/8 = 12%', 'Frontier model with SQL tools on the same set: 6/8', 'FM-04', '1/2', 'prefers-color-scheme:dark'):
            self.assertIn(text, html)

    def test_renders_with_empty_or_missing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            for results in (Path(tmp), Path(tmp) / 'missing'):
                html = scorecard_view.page(scorecard_view.build(results), learning=None)
                self.assertIn('No scored benchmark runs were found', html)
                self.assertIn('No stack connected', html)
                self.assertIn('What counts as an error', html)
        self.assertIn('could not be computed', scorecard_view.page({'empty': 'The defect report could not be computed: boom'}))

    def test_service_serves_scorecard(self):
        source = (ROOT / 'clinical_core' / 'agere' / 'service.py').read_text()
        self.assertIn("parsed.path=='/scorecard'", source)


if __name__ == '__main__':
    unittest.main()
