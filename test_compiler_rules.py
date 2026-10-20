"""Declarative compiler rules: slot filling, the unexplained-word guard, receipt-gated loading, the local_compiler hook,
the acceptance gate (rejects stealing / wrong-engine / unreproduced-reference rules, writes receipts) and rule drafting.

Unit tests run on a minimal in-memory profile. Gate and hook tests compile read-only against the cached source profiles of
the two synthetic stacks and are skipped when a stack is absent. No model or paid calls."""
import copy, json, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tools'))
from clinical_core.agere.clinical import compiler_rules, local_compiler  # noqa: E402

AS_OF = '2029-08-15'
ARTIFACTS = Path('/Users/mikel/clinical-insights-local/artifacts')
V123 = ARTIFACTS / 'agere-healthcare-v123/stack'
V20 = ARTIFACTS / 'agere-healthcare-v20/stack'
HAVE_BOTH = (V123 / 'run.json').exists() and (V20 / 'run.json').exists()
MINI = {'streams': {s: {'records': 100, 'fields': {}} for s in ('labs', 'conditions', 'encounters', 'pharmacy_dispenses', 'medical_claims')},
        'patients': {'fields': {}}}


def rules_by_id():
    return {r['id']: r for r in compiler_rules.read_rule_files()}


def rule(**over):
    base = {'id': 'test.share_tested', 'version': 1, 'engine': 'literal_lookup',
            'requires': {'streams': ['labs']},
            'pattern': '^what share of {population} had (?:an? )?{measurement}(?: test)? {period?}$',
            'slots': {'population': {'type': 'population'}, 'measurement': {'type': 'measurement'}, 'period': {'type': 'period', 'default': 'all'}},
            'plan': {'kind': 'sql', 'returns': 'value',
                     'query': "SELECT round(100.0 * sum(patient_id IN (SELECT patient_id FROM measurement WHERE concept IN {measurement.in} "
                              "AND {period.where:event_date})) / count(*), 2) FROM patient WHERE {population.where}"},
            'positives': [{'question': 'What share of patients with diabetes had an A1c test in the last year?', 'expect': {'operation': 'sql'}}],
            'negatives': ['What share of patients with diabetes had an A1c test by practice?']}
    base.update(over)
    return base


class SlotFilling(unittest.TestCase):
    def test_periods(self):
        self.assertEqual(compiler_rules._period('last quarter', AS_OF), ('2029-04-01', '2029-06-30'))
        self.assertEqual(compiler_rules._period('in the last 6 months', AS_OF), ('2029-02-16', AS_OF))
        self.assertEqual(compiler_rules._period('2029 ytd', AS_OF), ('2029-01-01', AS_OF))
        self.assertEqual(compiler_rules._period('in 12 months', AS_OF), ('2028-08-16', AS_OF))
        self.assertIsNone(compiler_rules._period('next quarter', AS_OF))

    def test_thresholds_and_codes(self):
        t = compiler_rules._threshold('between 7.5% and 10.5%')
        self.assertEqual(t['pred_fmt'].format(col='v'), 'v BETWEEN 7.5 AND 10.5')
        self.assertEqual(compiler_rules._threshold('of 6.5% or more')['op'], 'ge')
        self.assertEqual(compiler_rules._threshold('under 30')['op'], 'lt')
        self.assertIn("BETWEEN 55 AND 65", compiler_rules._icd_range('z55-z65')['pred_fmt'])
        self.assertIsNone(compiler_rules._icd_range('z55-a65'))

    def test_population_any_order_and_cohort_spec(self):
        p = compiler_rules._population('women 65 and older on a statin with diabetes', {}, MINI)
        self.assertIn("sex_reported = 'F'", p['where']); self.assertIn("age_at(birth_date, (SELECT value FROM meta WHERE key = 'as_of')) >= 65", p['where'])
        self.assertIn("drug_class IN ('statin')", p['where']); self.assertIn("'diabetes'", p['where'])
        self.assertNotIn('spec', p)  # a drug class is not a cohort predicate: no typed specification is claimed
        p = compiler_rules._population('adults with ckd and an egfr below 30', {}, MINI)
        self.assertEqual([a['kind'] for a in p['spec']['all']], ['patient', 'concept', 'measurement'])
        self.assertIsNone(compiler_rules._population('patients with gout', {}, MINI))  # unknown words never resolve

    def test_rule_fills_template(self):
        g, d = compiler_rules.match(rule(), 'What share of patients with diabetes or hypertension had an A1c test in the last year?', MINI, AS_OF)
        sql = g['nodes'][-1]['parameters']['query']
        self.assertIn("concept IN ('hba1c')", sql)
        self.assertIn("event_date BETWEEN '2028-08-16' AND '2029-08-15'", sql)
        self.assertIn("concept IN ('diabetes', 'hypertension')", sql)
        g, _ = compiler_rules.match(rule(), 'What share of patients with diabetes had an A1c test?', MINI, AS_OF)
        self.assertIn("(event_date IS NULL OR event_date <= '2029-08-15')", g['nodes'][-1]['parameters']['query'])  # all time = up to the snapshot

    def test_variants_and_repeated_slots(self):
        r = rules_by_id()['population.missing_measurement']
        g, _ = compiler_rules.match(r, 'How many patients with diabetes have not had an A1c in the last 6 months?', MINI, AS_OF)
        self.assertEqual(g['nodes'][-1]['parameters']['returns'], 'count')
        g, _ = compiler_rules.match(r, 'Which patients with diabetes have not had an A1c in the last 6 months?', MINI, AS_OF)
        self.assertEqual(g['nodes'][-1]['parameters']['returns'], 'patient_set')
        r = rules_by_id()['population.lab_without_diagnosis']
        for q in ('How many patients have an A1c of 6.5% or more on two occasions without a diabetes diagnosis?',
                  'How many patients have two eGFR values below 60 at least 90 days apart but no CKD diagnosis?'):
            g, d = compiler_rules.match(r, q, MINI, AS_OF)
            self.assertIsNotNone(g, (q, d))
            self.assertIn('count(DISTINCT event_date) >= 2', g['nodes'][-1]['parameters']['query'])

    def test_operator_plan_is_typed_and_validated(self):
        r = rules_by_id()['trajectory.decline_to_stage']
        g, _ = compiler_rules.match(r, 'Which patients with CKD have eGFR declining fast enough to reach stage 4 within two years?', MINI, AS_OF)
        self.assertEqual([n['operation'] for n in g['nodes']], ['cohort', 'trajectory'])
        self.assertEqual((g['nodes'][-1]['parameters']['threshold'], g['nodes'][-1]['parameters']['horizon_days']), (30.0, 730))
        bad = copy.deepcopy(r); bad['plan']['parameters']['concept'] = 'not_a_lab'
        g, d = compiler_rules.match(bad, 'Which patients with CKD have eGFR declining fast enough to reach stage 4 within two years?', MINI, AS_OF)
        self.assertIsNone(g); self.assertIn('invalid plan', d['reason'])


class UnexplainedWordGuard(unittest.TestCase):
    def test_extra_meaning_blocks_the_rule(self):
        for q in ('What share of patients with diabetes had an A1c test in the last year, by practice?',
                  'What share of patients with diabetes had an A1c test in the last year and why?',
                  'What share of patients with diabetes had an A1c test in the last year above 9?'):
            g, d = compiler_rules.match(rule(pattern='what share of {population} had (?:an? )?{measurement}(?: test)? {period?}'), q, MINI, AS_OF)
            self.assertIsNone(g, q); self.assertTrue(d['reason'].startswith('unexplained') or d['reason'] == 'pattern', d)
        g, d = compiler_rules.match(rule(pattern='what share of {population} had (?:an? )?{measurement}(?: test)? {period?}'),
                                    'What share of patients with diabetes had an A1c test in the last year, by practice?', MINI, AS_OF)
        self.assertIn('practice', d.get('unexplained', []))

    def test_slots_must_resolve_and_data_must_exist(self):
        g, d = compiler_rules.match(rule(), 'What share of patients with gout had an A1c test?', MINI, AS_OF)
        self.assertIsNone(g)
        no_labs = {'streams': {}, 'patients': {'fields': {}}}
        g, d = compiler_rules.match(rule(), 'What share of patients with diabetes had an A1c test?', no_labs, AS_OF)
        self.assertEqual(d['reason'], 'requires not met')

    def test_reject_if_and_marks(self):
        g, d = compiler_rules.match(rule(reject_if=[r'\bdiabetes\b']), 'What share of patients with diabetes had an A1c test?', MINI, AS_OF)
        self.assertIsNone(g)
        r = rule(marks={'cpt_lookup': True, 'route': 'evil'})
        g, info = compiler_rules.apply('What share of patients with diabetes had an A1c test?', MINI, AS_OF, {}, rules=[r])
        self.assertTrue(info['cpt_lookup']); self.assertEqual(info['route'], 'local_compiler')  # only listed marks pass


class Loading(unittest.TestCase):
    def test_only_rules_with_a_matching_receipt_load(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); (d / 'receipts').mkdir()
            r = rule()
            (d / 'family.json').write_text(json.dumps({'rules': [r]}))
            self.assertEqual(compiler_rules.load(d), [])
            receipt = {'status': 'accepted', 'rule_digest': compiler_rules.digest(r), 'sequence': 1}
            (d / 'receipts' / 'test.share_tested@1.json').write_text(json.dumps(receipt))
            self.assertEqual([x['id'] for x in compiler_rules.load(d)], ['test.share_tested'])
            changed = rule(pattern=r['pattern'] + '?')  # edited after acceptance: the receipt no longer matches
            (d / 'family.json').write_text(json.dumps({'rules': [changed]}))
            self.assertEqual(compiler_rules.load(d), [])
            (d / 'receipts' / 'test.share_tested@1.json').write_text(json.dumps({**receipt, 'status': 'rejected'}))
            (d / 'family.json').write_text(json.dumps({'rules': [r]}))
            self.assertEqual(compiler_rules.load(d), [])

    def test_shipped_rules_are_all_accepted(self):
        shipped = compiler_rules.read_rule_files()
        self.assertGreater(len(shipped), 20)
        for r in shipped:
            self.assertTrue(compiler_rules.accepted(r), f"{r['id']}@{r['version']} has no matching accepted receipt")


class Propose(unittest.TestCase):
    PROPOSAL = {'id': 'Ptest', 'cluster': 'c', 'reason_family': 'unexplained_words',
                'target': {'kind': 'operator', 'operator': 'monitor', 'shared_parameters': {'check': 'control_chart', 'series': ['ed_rate'], 'group_by': 'practice'}},
                'examples': ['Did the ED rate at any practice blow past its control limits in 2028?',
                             'Did the ED rate at any practice blow past its control limits in 2029?']}

    def run_propose(self, status):
        from compiler_growth import propose
        seen = []
        def fake_gate(drafts, write=False, log=None):
            seen.extend(drafts)
            return [{'status': status, 'rule_id': d['id']} for d in drafts]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'grammar-proposals.json').write_text(json.dumps({'proposals': [self.PROPOSAL]}))
            propose.run(d, write=True, gate=fake_gate, log=lambda *_: None, learned_file=d / 'learned.json')
            written = json.loads((d / 'learned.json').read_text())['rules'] if (d / 'learned.json').exists() else []
        return seen, written

    def test_only_rules_the_gate_accepts_are_written(self):
        seen, written = self.run_propose('accepted')
        self.assertEqual(len(seen), 1)
        self.assertEqual([r['id'] for r in written], [seen[0]['id']])
        self.assertEqual(written[0]['origin']['drafter'], 'template')
        seen, written = self.run_propose('rejected')
        self.assertEqual((len(seen), written), (1, []))

    def test_hosted_drafter_is_off_unless_enabled(self):
        import os
        from compiler_growth import propose
        calls = []
        propose.register_drafter(lambda p: calls.append(p) or [])
        try:
            self.assertEqual(propose.hosted_drafts(self.PROPOSAL), [])
            self.assertEqual(calls, [])
            os.environ['AGERE_COMPILER_RULE_DRAFTER'] = 'hosted'
            propose.hosted_drafts(self.PROPOSAL)
            self.assertEqual(len(calls), 1)
        finally:
            os.environ.pop('AGERE_COMPILER_RULE_DRAFTER', None)
            propose.register_drafter(None)


@unittest.skipUnless(HAVE_BOTH, 'synthetic v1.23 and v2.0 stacks not present')
class GateAndHook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from compiler_growth import accept
        cls.accept = accept
        cls.profiles = accept.load_profiles()
        items, cls.elements = accept.corpus()
        cls.items = items
        cls.by_id = {(i['kind'], i['id']): i for i in items}
        p = cls.profiles['v1.23'][0]
        with compiler_rules.using([]):  # fixtures: a declined unsupported library question and a declined bench question
            cls.unsupported_id = next(i['id'] for i in items if i['kind'] == 'library' and i['item']['expected_engine'] == 'unsupported'
                                      and i['item'].get('missing_elements') and local_compiler.compile(i['question'], p, i['as_of'])[0] is None)
            cls.bench_id = next(i['id'] for i in items if i['kind'] == 'bench' and i['as_of'] == AS_OF and i['item']['answer_type'] != 'unsupported'
                                and local_compiler.compile(i['question'], p, i['as_of'])[0] is None)

    def gate(self, candidates, keys, **kw):
        items = [self.by_id[k] for k in keys]
        with tempfile.TemporaryDirectory() as d:
            receipts = self.accept.gate(candidates, items=items, elements=self.elements, profiles=self.profiles, base_rules=[],
                                        receipts_dir=d, log=lambda *_: None, **kw)
            written = sorted(p.name for p in Path(d).glob('*.json'))
        return receipts, written

    def test_hook_runs_only_after_handwritten_routes_decline(self):
        p = self.profiles['v1.23'][0]
        greedy = rule(id='test.greedy', pattern='^how many {population}$', slots={'population': {'type': 'population'}},
                      plan={'kind': 'sql', 'returns': 'count', 'query': 'SELECT 42'})
        q = 'How many patients with diabetes?'
        before, _ = local_compiler.compile(q, p, AS_OF)
        with compiler_rules.using([greedy]):
            after, info = local_compiler.compile(q, p, AS_OF)
        self.assertEqual(after, before)  # the hand-written route answers: the rule never sees it
        self.assertNotIn('compiler_rule', info)
        q = 'Which patients on metformin have an eGFR below 30?'
        with compiler_rules.using([]):
            g, _ = local_compiler.compile(q, p, AS_OF)
        self.assertIsNone(g)
        with compiler_rules.using([rules_by_id()['population.list']]):
            g, info = local_compiler.compile(q, p, AS_OF)
        self.assertEqual((g['nodes'][-1]['operation'], info['compiler_rule']['id']), ('sql', 'population.list'))

    def test_gate_rejects_wrong_engine_and_acceptance_steals(self):
        steal = rule(id='test.steal', pattern='^which members are predicted to have an admission in the next 90 days$', slots={},
                     plan={'kind': 'sql', 'returns': 'count', 'query': 'SELECT count(*) FROM patient'},
                     positives=[{'question': 'Which members are predicted to have an admission in the next 90 days?', 'expect': {'operation': 'sql'}}],
                     negatives=['Which members are predicted to have an admission in the next 90 days and why?'])
        receipts, written = self.gate([steal], [('library', 'VQ-241')])
        self.assertEqual(receipts[0]['status'], 'rejected')
        self.assertTrue(any('captures library VQ-241' in r and 'early_signal' in r for r in receipts[0]['reasons']), receipts[0]['reasons'])
        self.assertEqual(written, [])  # nothing written for a rejected rule

    def test_gate_rejects_unsupported_and_unreproduced_bench(self):
        # a question labelled unsupported on this profile (a required data element is missing): never give it a data graph
        item = self.by_id[('library', self.unsupported_id)]
        grab = rule(id='test.grab', pattern='^' + _literal(compiler_rules.normalize(item['question'], AS_OF)) + '$', slots={},
                    plan={'kind': 'sql', 'returns': 'count', 'query': 'SELECT count(*) FROM patient'},
                    positives=[{'question': item['question'], 'expect': {'operation': 'sql'}}], negatives=[item['question'] + ' by practice?'])
        receipts, _ = self.gate([grab], [('library', self.unsupported_id)])
        self.assertEqual(receipts[0]['status'], 'rejected', receipts[0]['captured_counts'])
        self.assertTrue(any('unsupported' in r for r in receipts[0]['reasons']), receipts[0]['reasons'])
        item = self.by_id[('bench', self.bench_id)]
        wrong = rule(id='test.bench', pattern='^' + _literal(compiler_rules.normalize(item['question'], item['as_of'])) + '$', slots={},
                     plan={'kind': 'sql', 'returns': item['item']['answer_type'],
                           'query': {'count': 'SELECT -1', 'value': 'SELECT -1.5', 'patient_set': "SELECT 'NOBODY'", 'table': 'SELECT 1 AS x'}[item['item']['answer_type']]},
                     positives=[{'question': item['question'], 'expect': {'operation': 'sql'}}], negatives=[item['question'] + ' by practice?'])
        receipts, _ = self.gate([wrong], [('bench', self.bench_id)])
        self.assertEqual(receipts[0]['status'], 'rejected')
        self.assertTrue(any('bench' in r for r in receipts[0]['reasons']), receipts[0]['reasons'])

    def test_gate_accepts_a_correct_rule_and_writes_a_receipt(self):
        r = rules_by_id()['population.missing_measurement']
        receipts, written = self.gate([r], [('library', 'HQ-CDS-037'), ('library', 'HQ-CDS-036'), ('library', 'VQ-241')], write=True)
        self.assertEqual(receipts[0]['status'], 'accepted', receipts[0]['reasons'])
        self.assertEqual(written, ['population.missing_measurement@1.json'])
        self.assertEqual(receipts[0]['rule_digest'], compiler_rules.digest(r))
        self.assertEqual(receipts[0]['captured_counts']['v1.23'], {'library': 2})

    def test_gate_rejects_failing_own_examples_and_bad_schema(self):
        r = rule(id='test.own', negatives=['What share of patients with diabetes had an A1c test?'])
        receipts, _ = self.gate([r], [('library', 'VQ-241')])
        self.assertTrue(any('negative matched' in x for x in receipts[0]['reasons']))
        r = rule(id='test.schema', engine='sql_magic')
        receipts, _ = self.gate([r], [('library', 'VQ-241')])
        self.assertTrue(any('engine must be' in x for x in receipts[0]['reasons']))

    def test_propose_drafts_and_gates(self):
        from compiler_growth import propose
        proposal = {'id': 'Ptest', 'cluster': 'c', 'reason_family': 'unexplained_words',
                    'target': {'kind': 'operator', 'operator': 'monitor', 'shared_parameters': {'check': 'control_chart', 'series': ['ed_rate'], 'group_by': 'practice'}},
                    'examples': ['Did the ED rate at any practice blow past its control limits in 2028?',
                                 'Did the ED rate at any practice blow past its control limits in 2029?',
                                 'Did the ED rate at any practice blow past its control limits?']}
        drafts = propose.draft(proposal)
        self.assertEqual(len(drafts), 1)
        d = drafts[0]
        self.assertTrue(d['pattern'].endswith('{period?}$'))
        self.assertEqual(d['plan']['parameters']['period'], {'start': '{period.start}', 'end': '{period.end}'})
        receipts, _ = self.gate(drafts, [('library', 'VQ-241')])
        self.assertEqual(receipts[0]['status'], 'accepted', receipts[0]['reasons'])
        # examples that differ in a word the plan cannot use do not generalise into one rule
        mixed = dict(proposal, examples=['How many patients with diabetes had an ED visit in 2028?', 'How many patients with asthma had an ED visit in 2028?'],
                     target={'kind': 'operator', 'operator': 'monitor', 'shared_parameters': {}})
        self.assertEqual(propose.draft(mixed), [])
        self.assertEqual(propose.hosted_drafts(proposal), [])  # hosted drafting is off by default


def _literal(text):
    import re
    return re.escape(text).replace('\\ ', ' ')


if __name__ == '__main__':
    unittest.main()
