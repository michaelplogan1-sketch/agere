"""/ask workspace page: renders as one self-contained page with the demo script, and every answer type it draws carries the
fields the visuals read (survival curves, monitor chart points, outreach portfolio + reason labels, program-effect interval,
VBC settlement risk + fair allocation, trajectory rows). Fixtures are the operator suites' own synthetic stacks."""
import json
import re
import unittest

from clinical_core.agere import ask_view
from clinical_core.agere.clinical import analysis_graph, monitor, outreach, program_effect, survival, trajectory, vbc

import test_monitor
import test_outreach
import test_program_effect
import test_survival
import test_vbc

AS_OF = '2029-08-15'


def plan_for(operation, parameters, cohort=False):
    nodes = [{'id': 'population', 'operation': 'cohort', 'inputs': [], 'purpose': 'all',
              'parameters': {'specification': {'all': [], 'any': [], 'exclude': []}}}] if cohort else []
    nodes.append({'id': 'answer', 'operation': operation, 'inputs': ['population'] if cohort else [], 'parameters': parameters, 'purpose': operation})
    return analysis_graph.validate({'interpretation': operation, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': nodes}, AS_OF)


def answer_of(plan, report, question='q'):
    results = [{'node': n, 'result': {}} for n in plan['nodes'][:-1]] + [{'node': plan['nodes'][-1], 'result': report}]
    answer = analysis_graph.build_answer(plan, results, {}, question)
    answer['headline'] = analysis_graph.headline(answer)
    return json.loads(json.dumps(answer, allow_nan=False, default=str))  # what /api/stack/compose actually sends


class Page(unittest.TestCase):
    def setUp(self):
        self.html = ask_view.page()

    def test_single_self_contained_page(self):
        self.assertTrue(self.html.startswith('<!doctype html>'))
        self.assertIn('<title>Wollstonecraft · Ask your data</title>', self.html)
        self.assertNotRegex(self.html, r'<script[^>]+\bsrc=|<link[^>]+stylesheet|@import')  # no external assets
        self.assertIn('color-scheme:light', self.html)
        self.assertIn('max-width:800px', self.html)  # single-column layout for phones
        for route in ('/api/stack/compose', '/api/stack/conversation', '/api/stack/briefing', '/api/stack/economics', '/api/stack/correct'):
            self.assertIn(route, self.html)
        for hook in ('conversation_id', 'Answered as', 'Flag as wrong', 'Download CSV', 'Computed as', 'Benchmark reference', 'Standards'):
            self.assertIn(hook, self.html)

    def test_demo_script_is_embedded_and_well_formed(self):
        raw = re.search(r'<script id="demo-script" type="application/json">(.*?)</script>', self.html, re.S).group(1)
        steps = json.loads(raw)
        self.assertEqual(steps, ask_view.DEMO_STEPS)
        self.assertEqual(steps[0]['kind'], 'input')
        self.assertEqual(steps[-1]['kind'], 'work')
        self.assertEqual(len({s['id'] for s in steps}), len(steps))
        for s in steps:
            self.assertIn(s['kind'], ('input', 'view', 'work'))
            self.assertTrue(s['label'] and s['note'])
            if s['kind'] == 'view':
                self.assertIn('data-contract-section="' + s['section'] + '"', self.html)
        self.assertFalse(any('question' in s for s in steps))  # no unrelated rerun per view

    def test_script_has_no_closing_tag_injection(self):
        self.assertEqual(self.html.count('</script>'), 2)


class AnswerFields(unittest.TestCase):
    """Fields the console's visuals read, per answer type, on the operator suites' fixtures."""

    def test_monitor_exposes_chart_points(self):
        test_monitor.EndToEnd.setUpClass()
        plan = plan_for('monitor', {'check': 'control_chart', 'series': ['admit_rate'], 'group_by': 'practice'})
        report = monitor.execute(test_monitor.EndToEnd.path, None, plan['nodes'][0]['parameters'], AS_OF)
        a = answer_of(plan, report)
        charts = a['overall']['charts']
        self.assertEqual(len(charts), len(report['chart_status']))
        self.assertTrue(charts)
        c = charts[0]
        self.assertTrue({'series', 'label', 'group_by', 'group', 'status', 'center', 'points', 'rate_signal_months', 'data_signal_months'} <= set(c))
        self.assertTrue(all(set(p) == {'month', 'value', 'lcl', 'ucl'} for p in c['points']))
        self.assertEqual([p['month'] for p in c['points']], [p['month'] for p in report['chart_status'][0]['chart']])
        self.assertTrue(all('chart' not in row for row in a['table']))  # the table itself is unchanged
        self.assertTrue(any(c['status'] == 'rate_signal' and c['rate_signal_months'] for c in charts))

    def test_outreach_exposes_portfolio_and_reason_labels(self):
        test_outreach.Worklist.setUpClass()
        plan = plan_for('outreach', {'capacity_patients': 5})
        report = outreach.execute(test_outreach.Worklist.path, None, plan['nodes'][0]['parameters'], AS_OF)
        a = answer_of(plan, report, 'who should we call? 5 slots')
        o = a['overall']
        self.assertTrue({'portfolio', 'by_group', 'capacity', 'candidates', 'optimization', 'limitations', 'reason_labels'} <= set(o))
        codes = {c for row in a['table'] for c in row['top_reasons']}
        self.assertTrue(codes and codes <= set(o['reason_labels']))
        for code, info in o['reason_labels'].items():
            self.assertEqual((info['category'], info['label']), outreach.REASONS[code][:2])
        self.assertNotIn('reasons', a['table'][0])

    def test_survival_exposes_curves(self):
        test_survival.Fixture.setUpClass()
        plan = plan_for('survival', {'index': {'type': 'discharge'}, 'event': {'type': 'readmission'}, 'group_by': 'coverage_type'}, cohort=True)
        report = survival.execute(test_survival.Fixture.path, test_survival.Fixture.ids, plan['nodes'][-1]['parameters'])
        a = answer_of(plan, report)
        curves = a['overall']['curves']
        self.assertTrue(curves)
        for points in curves.values():
            self.assertTrue(all({'day', 'survival'} <= set(p) for p in points))
        self.assertTrue({'event_label', 'time_zero', 'groups', 'logrank'} <= set(a['overall']))

    def test_program_effect_exposes_interval_checks_and_warnings(self):
        path, _ = test_program_effect.fixture()
        plan = plan_for('program_effect', dict(test_program_effect.PARAMS))
        report = program_effect.execute(path, None, plan['nodes'][0]['parameters'], AS_OF)
        a = answer_of(plan, report, 'Did care management reduce ED visits?')
        did = a['overall']['did']
        self.assertTrue(isinstance(did['effect'], float) and len(did['ci95']) == 2)
        self.assertLessEqual(did['ci95'][0], did['ci95'][1])
        self.assertIn('inference', a['overall'])
        for k in ('placebo', 'pre_trend', 'synthetic_control', 'sample', 'balance'):
            self.assertIn(k, a['overall'])
        self.assertIsInstance(a['warnings'], list)
        self.assertIsInstance(a['assumptions'], list)

    def test_vbc_exposes_settlement_risk_and_fair_allocation(self):
        test_vbc.VBC.setUpClass()
        case = test_vbc.VBC('test_shared_savings_arithmetic')
        params = {'period': case.period, 'metric': 'shared_savings', 'group_by': 'practice', 'terms': {'benchmark_pmpm': 20, 'sharing_rate': 0.5}}
        plan = plan_for('vbc', params)
        report = vbc.execute(test_vbc.VBC.path, None, plan['nodes'][0]['parameters'], AS_OF)
        a = answer_of(plan, report, 'What would our shared savings be with a benchmark of $20 PMPM and a sharing rate of 50%, by practice?')
        row = a['table'][0]
        risk = row['settlement_risk']
        for k in ('p_earn_savings', 'p_owe_loss', 'p_within_corridor', 'net_payment_p5', 'net_payment_p50', 'net_payment_p95',
                  'expected_net_payment', 'value_at_risk_95'):
            self.assertIn(k, risk)
        self.assertAlmostEqual(risk['p_earn_savings'] + risk['p_owe_loss'] + risk['p_within_corridor'], 1.0, places=2)
        alloc = row['fair_allocation']
        self.assertTrue(alloc['allocation'])
        self.assertTrue(all({'group', 'shapley_share', 'pro_rata_share'} <= set(g) for g in alloc['allocation']))

    def test_trajectory_rows_carry_patient_ids(self):
        test_survival.Fixture.setUpClass()
        plan = plan_for('trajectory', {'concept': 'egfr', 'direction': 'down'}, cohort=True)
        report = trajectory.execute(test_survival.Fixture.path, test_survival.Fixture.ids, plan['nodes'][-1]['parameters'])
        a = answer_of(plan, report, 'Which CKD patients have a declining eGFR?')
        self.assertEqual(a['answer_type'], 'table')
        self.assertEqual(a['patient_ids'], [r['patient_id'] for r in a['table']])


if __name__ == '__main__':
    unittest.main()


class WholePageScript(unittest.TestCase):
    def test_queueing_overview_exposes_scope_and_preserves_small_positive_rates(self):
        import shutil, subprocess
        node = shutil.which('node')
        if not node: self.skipTest('node not installed')
        html = ask_view.page()
        helpers = html[html.index('const isNum='):html.index('const usdK=')]
        code = html[html.index('function vizQueueing('):html.index('function vizUplift(')]
        setup = "const esc=s=>String(s==null?'':s).replace(/</g,'&lt;'); const pct=String;\n"
        checks = r'''
const assert=require('node:assert/strict');
const a={overall:{queueing:{source:'ed',inputs:{weekly_arrivals_planned:.172,arrivals_used:9}},input_scope:{population:'selected',patient_count:3,window:{start:'2029-01-01',end:'2029-12-31'},as_of:'2030-05-27'}}};
let rendered=vizQueueing(a);
assert.match(rendered,/3 selected patients/);
assert.match(rendered,/2029-01-01 to 2029-12-31 \(inclusive\)/);
assert.match(rendered,/snapshot 2030-05-27/);
assert.match(rendered,/Arrivals \/ week<\/div><div class="v">0\.172</);
assert.match(rendered,/Observed arrivals<\/div><div class="v">9</);
a.overall.queueing.inputs.weekly_arrivals_planned=.00001;
assert.match(vizQueueing(a),/Arrivals \/ week<\/div><div class="v">&lt;0\.001</);
a.overall.queueing.inputs.weekly_arrivals_planned=0;
assert.match(vizQueueing(a),/Arrivals \/ week<\/div><div class="v">0</);
'''
        run = subprocess.run([node, '-e', setup+helpers+code+checks], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr[:1600])

    def test_summary_without_selected_patients_cannot_offer_empty_cohort_followup(self):
        import shutil, subprocess
        from pathlib import Path
        node = shutil.which('node')
        if not node: self.skipTest('node not installed')
        script = (Path(ask_view.__file__).parent/'assets/explore.js').read_text()
        code = script[script.index('function syncScope('):script.index('function updateScopeNote(')]
        setup = """
let activeContext; const option={},input={},picker={querySelector:s=>s.includes('input')?input:option};
const $=()=>picker, fmt=String, history={replaceState:()=>{}},updateScopeNote=()=>{};
"""
        checks = r'''
const assert=require('node:assert/strict');
const t={data:{action_context:{id:'ANS-000000000000000000000001',patients:0},answer:{patient_ids:[],analysis_population_count:3,answer_type:'table'}}};
syncScope(t);assert.equal(option.disabled,true);assert.equal(picker.value,'warehouse');
assert.equal(option.textContent,'Analytical summary · no selected patients');
assert.equal(input.disabled,true);
t.data.action_context.input_patients=3;
syncScope(t);assert.equal(input.disabled,false);assert.equal(input.textContent,'Original input · 3 patients');
assert.equal(picker.value,'warehouse'); // Never silently change summary output into its input.
t.data.action_context.input_patients=0;
syncScope(t);assert.equal(input.disabled,false);assert.equal(input.textContent,'Original input · 0 patients');
t.archiveBlocked=true;syncScope(t);assert.equal(input.disabled,true);
t.archiveBlocked=false;
t.data.action_context.result_scope_available=false;
t.data.answer={patient_ids:[],answer_type:'count'};
syncScope(t);assert.equal(option.disabled,true);assert.equal(picker.value,'warehouse');
delete t.data.action_context.result_scope_available;
const failed={error:'unsupported wording',scopeBefore:{context:{id:t.data.action_context.id,patients:0,input_patients:3,result_scope_available:false},value:'input'}};
syncScope(failed);assert.equal(picker.value,'input');assert.equal(activeContext.id,t.data.action_context.id);
assert.equal(option.disabled,true);assert.equal(input.disabled,false);
failed.pending=true;delete failed.error;syncScope(failed);assert.equal(picker.value,'input');
t.data.answer={patient_ids:[],answer_type:'patient_set'};
syncScope(t);assert.equal(option.disabled,false);assert.equal(picker.value,'saved');
t.data.answer={patient_ids:['A'],answer_type:'count'};t.data.action_context.patients=1;
syncScope(t);assert.equal(option.disabled,false);assert.match(option.textContent,/1 saved patients/);
'''
        run = subprocess.run([node, '-e', setup+code+checks], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr[:1600])

    def test_followup_shortcut_preserves_explicit_input_selection(self):
        import shutil, subprocess
        node = shutil.which('node')
        if not node: self.skipTest('node not installed')
        html = ask_view.page()
        handler = next(line.strip() for line in html.splitlines() if line.strip().startswith('if(el.dataset.ask!=null)'))
        script = "const assert=require('node:assert/strict');let activeContext={id:'saved'},asked;const picker={value:'input'},$=()=>picker,ask=q=>{asked=q};function click(el){"+handler+"}\n"
        script += "click({dataset:{ask:'list them'}});assert.equal(asked,'list them');assert.equal(picker.value,'input');picker.value='warehouse';click({dataset:{ask:'list them'}});assert.equal(picker.value,'saved');"
        result = subprocess.run([node, '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[:1600])

    def test_grouped_chart_keeps_missing_first_category_visible(self):
        import shutil, subprocess
        node = shutil.which('node')
        if not node: self.skipTest('node not installed')
        html = ask_view.page()
        code = html[html.index('function vizTable('):html.index('function vizScalar(')]
        setup = """
const GROUP_KEYS=['practice']; const esc=String, label=String;
const pickMetric=()=> 'count', isTime=()=>false, drill=String;
const tableHTML=()=>'<table>original rows</table>';
let bars=null; const hbars=rows=>{bars=rows;return '<svg>chart</svg>'};
"""
        assertions = """
const assert=require('node:assert/strict');
const rows=[{pcp_practice_id:null,count:869},{pcp_practice_id:'WH-PRACTICE-0',count:400}];
const html=vizTable({table:rows});
assert.match(html,/<svg>chart<\\/svg>/);
assert.deepEqual(bars.map(r=>[r.label,r.value]),[['Not recorded',869],['WH-PRACTICE-0',400]]);
assert.equal(bars[0].ask,null);
assert.match(html,/<table>original rows<\\/table>/);
assert.equal(rows[0].pcp_practice_id,null);
"""
        subprocess.run([node, '-e', setup + code + assertions], check=True, capture_output=True, text=True)

    def test_execution_view_preserves_cost_unknowns_and_actual_model_dispatch(self):
        import shutil, subprocess
        node = shutil.which('node')
        if not node: self.skipTest('node not installed')
        html = ask_view.page()
        code = html[html.index('function executionInfo('):html.index('function resolvedLine(')]
        setup = "const ROUTES={local_compiler:'Agere compiler'}; const isNum=v=>typeof v==='number'&&isFinite(v); const fmt=String; const label=s=>String(s).replace(/_/g,' '); const esc=s=>String(s).replace(/</g,'&lt;');\n"
        assertions = r'''
const assert=require('node:assert/strict');
let x=executionInfo({});assert.equal(x.cost,'Unknown');assert.equal(x.calls,null);
assert.match(badges({data:{}}),/API estimate: Unknown/);
const partial={new_paid_api_calls:3,planning_economics:{new_api_estimate_usd:null,known_api_estimate_usd:.12,failed_or_unpriced_api_cost:true}};
assert.equal(executionInfo(partial).cost,'Unknown (known portion $0.1200)');
let local={new_paid_api_calls:0,planner_receipt:{route:'local_compiler'},planning_economics:{new_api_estimate_usd:0,known_api_estimate_usd:0},graph:{nodes:[{operation:'cohort'},{operation:'aggregate'}]},together:{analyst:{state:'skipped_specialist',model:'claude-haiku-5-5'}}};
x=executionInfo(local);assert.equal(x.cost,'$0.0000');assert.equal(x.calls,0);assert.deepEqual(x.models,[]);assert.deepEqual(x.operations,['cohort','aggregate']);
let model={new_paid_api_calls:4,planning_economics:{new_api_estimate_usd:.15},planner_receipt:{route:'deferred_to_analyst',steps:[{receipt:{stages:[{model:'claude-haiku-5-5'}]}}]},together:{winner:'analyst',analyst:{state:'done',model:'claude-opus-5-5'},hybrid:{decision:'escalated',models:{analyst:'claude-opus-5-5',escalation:'claude-opus-5-5'}}}};
x=executionInfo(model);assert.equal(x.route,'Executed analyst SQL');assert.deepEqual(x.models,['claude-haiku-5-5','claude-opus-5-5']);assert.equal(x.decision,'escalated');
assert.match(executionDetails(model),/How Agere answered/);assert.match(executionDetails(model),/\$0.1500/);
assert.doesNotMatch(executionDetails({route:'<script>bad</script>'}),/<script>/);
'''
        result = subprocess.run([node, '-e', setup+code+assertions], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[:1600])

    def test_console_script_is_valid_javascript(self):
        """Parallel-builder merges have broken the page script by dropping a shared closing line; node --check catches it."""
        import re, shutil, subprocess, tempfile
        node = shutil.which('node')
        if not node: self.skipTest('node not installed')
        from clinical_core.agere import ask_view
        scripts = re.findall(r'<script>(.*?)</script>', ask_view.page(), re.S)
        self.assertTrue(scripts)
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as f:
            f.write(scripts[-1])
        out = subprocess.run([node, '--check', f.name], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr[:400])
