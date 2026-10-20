"""Behavior checks for complete populations, unknowns and composed execution."""
import itertools
from clinical_core.agere.clinical.context_student import REQUEST_MAX_BYTES
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical.analysis_graph import (
    Membership, combine, compare_populations, execute, validate, catalog)
from clinical_core.agere.clinical.cohorts import empty, select
from clinical_core.agere.clinical.analytic_registry import compute_all
from clinical_core.agere.clinical.graph_planner import propose

ASOF = '2026-09-30'
WINDOW = {'basis': 'all_observed', 'start': '', 'end': ''}


def node(ident, operation, inputs=None, parameters=None):
    return {'id': ident, 'operation': operation, 'inputs': inputs or [],
            'parameters': parameters or {}, 'purpose': ident}


def graph(nodes):
    return {'interpretation': 'Observed-source composition', 'nodes': nodes,
            'coverage': 'supported_observation', 'unmet_requirements': []}


def record_atom(stream, field, value):
    return {'kind': 'record', 'stream': stream, 'conditions': [{'field': field, 'op': 'eq', 'value': value}], 'window': WINDOW}


class SetLogic(unittest.TestCase):
    def test_all_three_valued_combinations(self):
        for a, b in itertools.product([True, False, None], repeat=2):
            values = [Membership({'p'}, {'p'} if x is True else set(), {'p'} if x is None else set()) for x in (a, b)]
            for op in ('intersect', 'union', 'difference'):
                bb = None if b is None else not b if op == 'difference' else b
                if op == 'union': expected = True if a is True or b is True else None if a is None or b is None else False
                else: expected = False if a is False or bb is False else None if a is None or bb is None else True
                got = combine(op, values)
                actual = True if 'p' in got.yes else None if 'p' in got.unknown else False
                self.assertIs(actual, expected, (a, b, op))

    def test_comparison_unknowns_are_not_controls(self):
        u = set('abcdef')
        rows = compare_populations(Membership(u, u, set()), Membership(u, set('ad'), {'f'}),
                                   [('group', Membership(u, set('ab'), {'e'}))])
        r = rows['comparisons'][0]
        self.assertEqual(r['patients_evaluated'], 4)
        self.assertEqual(r['excluded_unknown_endpoint_or_group'], 2)
        self.assertEqual(r['cells'], dict(group_and_endpoint=1, group_without_observed_endpoint=1,
                                         complement_and_endpoint=1, complement_without_observed_endpoint=1))
        self.assertFalse(rows['inference_validity_verified'])

    def test_empty_group_has_no_probability_claim(self):
        u = {'p'}
        r = compare_populations(Membership(u, u, set()), Membership(u, u, set()), [('g', Membership(u, set(), set()))])
        self.assertEqual(r['computed_comparisons'], 0)
        self.assertIsNone(r['comparisons'][0]['nominal_bh_q'])


class GraphExecution(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.evidence = self.root/'evidence'; self.evidence.mkdir()
        self.meta = {'synthetic': True, 'patients': 6, 'source_digest': 'visible-source', 'source_run': 'run',
                     'snapshot': {'as_of': ASOF}}
        (self.root/'run.json').write_text(json.dumps(self.meta))
        db = sqlite3.connect(self.root/'stack.sqlite3')
        db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,stream TEXT,payload TEXT); CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT,PRIMARY KEY(patient,operator)); CREATE TABLE result_cache(id TEXT PRIMARY KEY,payload TEXT);')
        with db:
            for i in range(6):
                pid = 'p'+str(i)
                patient = {'patient_id': pid, 'region': 'west' if i < 3 else 'east' if i < 5 else None}
                db.execute('INSERT INTO patients VALUES (?,?)', (pid, json.dumps(patient)))
                if i in (0, 3):
                    r = {'record_id': 'r'+pid, 'patient_id': pid, 'source_table': 'adt', 'event': 'A01',
                         'event_date': '2026-05-01', 'available_date': '2026-05-01'}
                    db.execute('INSERT INTO records VALUES (?,?,?,?)', (r['record_id'], pid, 'adt', json.dumps(r)))
        db.close()
        compute_all(self.root)
        em = {'schema': 'evidence-query/1', 'synthetic': True, 'source_run': 'run', 'model_identity': 'reader',
              'snapshot': self.meta['snapshot'], 'counts': {'patients': 6}}
        (self.evidence/'run.json').write_text(json.dumps(em))
        (self.root/'annotation-link.json').write_text(json.dumps({'path': str(self.evidence), 'model_identity': 'reader', 'source_run': 'run'}))
        db = sqlite3.connect(self.evidence/'evidence.sqlite3')
        db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY); CREATE TABLE evidence(id TEXT PRIMARY KEY,patient TEXT,kind TEXT,concept TEXT,assertion TEXT,subject TEXT,time TEXT,relation TEXT,value_json TEXT,payload TEXT);')
        with db:
            for i in range(6): db.execute('INSERT INTO patients VALUES (?)', ('p'+str(i),))
            for i in (0, 2, 5):
                db.execute('INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?)',
                           ('e'+str(i), 'p'+str(i), 'text', 'financial hardship', 'affirmed', 'patient', 'current', 'clinical_observation', 'null', json.dumps({'source': {'event_date': '2026-04-01'}, 'quote': 'synthetic hardship'})))
        db.close()
        self.stack = SimpleNamespace(path=self.root, retriever=SimpleNamespace(meta=self.meta))
        self.base = node('base', 'cohort', parameters={'specification': empty()})
        self.admission = node('admission', 'cohort', parameters={'specification': {**empty(), 'all': [record_atom('adt', 'event', 'A01')]}})
        self.text = node('hardship', 'evidence', parameters={'plan': {'match': 'all', 'clauses': [
            {'source': 'text', 'concept': 'financial hardship', 'assertion': 'affirmed', 'subject': 'patient'}]}})

    def tearDown(self): self.temp.cleanup()

    def test_actual_join_comparison_and_cached_replay(self):
        plan = graph([self.base, self.admission, self.text, node('comparison', 'compare', ['base', 'admission', 'hardship'])])
        r = execute(self.stack, plan)
        self.assertEqual(r['results'][2]['result']['counts']['matched'], 3)
        c = r['results'][3]['result']['comparisons'][0]['cells']
        self.assertEqual(list(c.values()), [1, 2, 1, 2])
        self.assertTrue(execute(self.stack, plan)['cache_hit'])
        self.assertEqual(r['new_paid_api_calls'], 0)

    def test_worklist_complete_membership_cache_tracks_review_changes(self):
        from clinical_core.agere.clinical.program_work import list_items, materialize
        plan=graph([self.base,node('work','worklist',['base'],{'program':'hf_review'})])
        first=execute(self.stack,plan)
        self.assertEqual(first['results'][-1]['result']['counts']['missing_assessment'],6)
        self.assertTrue(execute(self.stack,plan)['cache_hit'])
        self.assertEqual(list_items(self.root)['matched'],0)
        db=sqlite3.connect(self.root/'stack.sqlite3')
        analysis={'synthetic':True,'patient_id':'p0','source_run':'run',
                  'workflow':{'as_of':ASOF,'review_candidate':True,'status':'review_required','missing_information':['Actual use']},
                  'source_bound_facts':[{'record_id':'rp0'}]}
        db.execute('INSERT INTO analyses VALUES (?,?,?)',('p0','heart_failure_review',json.dumps(analysis)));db.commit();db.close()
        second=execute(self.stack,plan)
        self.assertFalse(second['cache_hit'])
        proposal=second['results'][-1]['result']
        self.assertEqual(proposal['counts'],dict(selected=6,assessed=1,candidates=1,missing_assessment=5))
        self.assertNotEqual(first['result_id'],second['result_id'])
        self.assertEqual(materialize(self.root,proposal['proposal_id'],'test')['changes']['created'],1)

    def test_worklist_empty_set_never_expands(self):
        plan=graph([self.base,self.text,node('none','difference',['hardship','base']),
                    node('work','worklist',['none'],{'program':'affordability'})])
        self.assertEqual(execute(self.stack,plan)['results'][-1]['result']['counts']['selected'],0)

    def test_state_node_passes_complete_selected_membership_and_is_cached(self):
        from unittest.mock import patch
        plan=graph([self.text,node('state','state_review',['hardship'],{'contract':'affordability'})])
        with patch('clinical_core.agere.clinical.clinical_state.attached',return_value=(self.root,{'identity':'state-v1'})), \
             patch('clinical_core.agere.clinical.clinical_state.review',return_value={'patients_evaluated':3}) as review:
            result=execute(self.stack,plan)
            self.assertEqual(review.call_args.args,(self.root,['p0','p2','p5'],'affordability'))
            self.assertEqual(result['results'][-1]['result']['input_membership']['counts']['matched'],3)
            self.assertTrue(execute(self.stack,plan)['cache_hit'])
            self.assertEqual(review.call_count,1)

    def test_state_contract_validation_rejects_arbitrary_policy(self):
        with self.assertRaisesRegex(ValueError,'Unknown clinical evidence contract'):
            execute(self.stack,graph([self.base,node('state','state_review',['base'],{'contract':'automatic_eligibility'})]))

    def test_interpretation_membership_composes_and_review_change_invalidates_graph(self):
        params={'streams':['notes'],'concepts':['financial hardship'],'filter':'review_conflict'}
        plan=graph([self.base,node('review_conflicts','interpretation',['base'],params),
                    node('coverage','analyze',['review_conflicts'],{'operator':'source_completeness','view':'all','drug_class':'','analysis_window':WINDOW})])
        snapshot={'identity':'review-revision-one'}
        def interpretation(path,ids,p,**kwargs):
            self.assertEqual(ids,['p0','p1','p2','p3','p4','p5']);self.assertEqual(p,params)
            return {'p2','p5'},{'patients_evaluated':6,'counts':{'patient_cache_hits':6}}
        with patch('clinical_core.agere.clinical.interpretation_state.snapshot',side_effect=lambda *args:snapshot.copy()), \
             patch('clinical_core.agere.clinical.interpretation_state.execute',side_effect=interpretation) as run:
            first=execute(self.stack,plan)
            self.assertEqual(first['results'][1]['result']['counts']['matched'],2)
            self.assertEqual(first['results'][-1]['result']['summary']['patients'],2)
            self.assertTrue(execute(self.stack,plan)['cache_hit']);self.assertEqual(run.call_count,1)
            snapshot['identity']='review-revision-two'
            second=execute(self.stack,plan)
            self.assertFalse(second['cache_hit']);self.assertNotEqual(second['result_id'],first['result_id'])

    def test_interpretation_cannot_drop_unknown_input_membership(self):
        west=node('west','cohort',parameters={'specification':{**empty(),'all':[{'kind':'patient','field':'region','op':'eq','value':'west'}]}})
        plan=graph([west,node('reviews','interpretation',['west'],{'streams':['notes'],'concepts':['financial hardship'],'filter':'all'})])
        with patch('clinical_core.agere.clinical.interpretation_state.snapshot',return_value={'identity':'fixture'}), \
             patch('clinical_core.agere.clinical.interpretation_state.execute',return_value=({'p0','p1','p2'},{'counts':{}})):
            result=execute(self.stack,plan)['results'][-1]['result']
            self.assertEqual(result['counts'],{'evaluated':6,'matched':3,'not_matched':2,'unknown':1})

    def test_interpretation_rejects_clinical_eligibility_filter(self):
        with self.assertRaisesRegex(ValueError,'Unknown interpretation status'):
            validate(graph([self.base,node('invented','interpretation',['base'],{'streams':['notes'],'concepts':['financial hardship'],'filter':'clinically_eligible'})]),ASOF)

    def test_original_question_is_persisted_and_part_of_cache_identity(self):
        plan=graph([self.base])
        first=execute(self.stack,plan,question='Original question')
        second=execute(self.stack,plan,question='Different original question')
        self.assertNotEqual(first['result_id'],second['result_id'])
        self.assertEqual(first['question'],'Original question')
        self.assertTrue(execute(self.stack,plan,question='Original question')['cache_hit'])

    def test_source_state_unknowns_and_complete_downstream_membership(self):
        params={'streams':['notes','financial'],'concept':'financial hardship','relation':'reported_barrier','clock':'source_statement_date','filter':'affirmed'}
        plan=graph([self.base,node('documented','source_state',['base'],params),
            node('coverage','analyze',['documented'],{'operator':'source_completeness','view':'all','drug_class':'','analysis_window':WINDOW})])
        revision={'identity':'one'}
        with patch('clinical_core.agere.clinical.graph_decision.member_index',return_value=[]), \
             patch('clinical_core.agere.clinical.graph_decision.source_fingerprint',return_value={}), \
             patch('clinical_core.agere.clinical.interpretation_state.snapshot',side_effect=lambda *a:revision.copy()), \
             patch('clinical_core.agere.clinical.source_state.execute',return_value=({'p0','p1'},{'p2','p3'},{'patients_evaluated':6,'status_counts':{'documented_affirmed':2,'documented_negated':2,'unobserved':2},'evidence_coverage':{}})) as project:
            first=execute(self.stack,plan)
            self.assertEqual(first['results'][1]['result']['counts'],{'evaluated':6,'matched':2,'not_matched':2,'unknown':2})
            self.assertEqual(first['results'][-1]['result']['summary']['patients'],2)
            self.assertEqual(project.call_args.args[1],['p0','p1','p2','p3','p4','p5'])
            self.assertTrue(execute(self.stack,plan)['cache_hit'])
            revision['identity']='two'
            self.assertFalse(execute(self.stack,plan)['cache_hit'])

    def test_source_state_preserves_upstream_unknown_and_rejects_eligibility(self):
        params={'streams':['notes'],'concept':'medication bb','relation':'reported_use','clock':'source_statement_date','filter':'affirmed'}
        west=node('west','cohort',parameters={'specification':{**empty(),'all':[{'kind':'patient','field':'region','op':'eq','value':'west'}]}})
        with patch('clinical_core.agere.clinical.graph_decision.member_index',return_value=[]), \
             patch('clinical_core.agere.clinical.graph_decision.source_fingerprint',return_value={}), \
             patch('clinical_core.agere.clinical.interpretation_state.snapshot',return_value={'identity':'fixture'}), \
             patch('clinical_core.agere.clinical.source_state.execute',return_value=({'p0'},{'p1','p2'},{'status_counts':{'documented_affirmed':1,'unobserved':2},'evidence_coverage':{}})):
            result=execute(self.stack,graph([west,node('state','source_state',['west'],params)]))['results'][-1]['result']
            self.assertEqual(result['counts'],{'evaluated':6,'matched':1,'not_matched':2,'unknown':3})
        with self.assertRaises(ValueError):
            validate(graph([self.base,node('state','source_state',['base'],{**params,'filter':'eligible'})]),ASOF)

    def test_unknown_cohort_membership_propagates_to_difference(self):
        west = node('west', 'cohort', parameters={'specification': {**empty(), 'all': [{'kind': 'patient', 'field': 'region', 'op': 'eq', 'value': 'west'}]}})
        r = execute(self.stack, graph([self.base, west, node('other', 'difference', ['base', 'west'])]))
        self.assertEqual(r['results'][-1]['result']['counts'], dict(evaluated=6, matched=2, not_matched=3, unknown=1))

    def test_narrative_set_drives_existing_analytic_full_membership(self):
        r = execute(self.stack, graph([self.text, node('coverage', 'analyze', ['hardship'], {
            'operator': 'source_completeness', 'view': 'all', 'drug_class': '', 'analysis_window': WINDOW})]))
        result = r['results'][-1]['result']
        self.assertEqual(result['summary']['patients'], 3)
        self.assertEqual({p['patient_id'] for p in result['patients']}, {'p0', 'p2', 'p5'})

    def test_empty_selected_set_never_expands_to_all(self):
        plan = graph([self.base, self.text, node('none', 'difference', ['hardship', 'base']),
                      node('coverage', 'analyze', ['none'], {'operator': 'source_completeness', 'view': 'all', 'drug_class': '', 'analysis_window': WINDOW})])
        self.assertEqual(execute(self.stack, plan)['results'][-1]['result']['summary']['patients'], 0)
        self.assertEqual(select(self.root, patient_ids=[])['counts']['evaluated'], 0)

    def test_unknown_input_id_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown patient'): select(self.root, patient_ids=['not-in-warehouse'])

    def test_unmapped_concept_not_interpreted_as_negative(self):
        self.text['parameters']['plan']['clauses'][0].update(concept='not-extracted', observation='not_observed')
        with self.assertRaisesRegex(ValueError, 'Unmapped evidence concept'): execute(self.stack, graph([self.text]))

    def test_boolean_screen_presence_requires_explicit_value(self):
        db=sqlite3.connect(self.evidence/'evidence.sqlite3')
        db.execute('INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?)',
                   ('screen','p0','structured','financial_hardship',None,None,None,None,'false','{}'))
        db.commit();db.close()
        screen=node('screen','evidence',parameters={'plan':{'match':'all','clauses':[
            {'source':'structured','concept':'financial_hardship'}]}})
        with self.assertRaisesRegex(ValueError,'Boolean-valued'):execute(self.stack,graph([screen]))
        screen['parameters']['plan']['clauses'][0]['value_equals']=True
        self.assertEqual(execute(self.stack,graph([screen]))['results'][0]['result']['counts']['matched'],0)

    def test_unavailable_forecast_and_disabled_live_review_fail_before_execution(self):
        self.stack.allow_live=False
        plan=graph([self.base,node('predict','forecast',['base'],{'model':'all'})])
        with self.assertRaisesRegex(ValueError,'No fitted'):execute(self.stack,plan)
        plan=graph([self.base,node('read','review',['base'],{'providers':['jev'],'question':'Evidence?', 'maximum_patients':1})])
        with self.assertRaisesRegex(ValueError,'disabled'):execute(self.stack,plan)

    def test_source_snapshot_mismatch_rejected(self):
        meta = json.loads((self.evidence/'run.json').read_text()); meta['snapshot'] = {'as_of': '2026-01-01'}
        (self.evidence/'run.json').write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, 'snapshot/model'): execute(self.stack, graph([self.base]))

    def test_future_date_and_cycles_rejected_before_execution(self):
        for plan in [graph([node('cycle', 'intersect', ['cycle', 'other'])]),
                     graph([self.base, node('comparison', 'compare', ['base', 'base', 'base'])])]:
            with self.assertRaises(ValueError): validate(plan, ASOF)
        self.text['parameters']['plan']['clauses'][0]['event_date_from'] = '2027-01-01'
        with self.assertRaisesRegex(ValueError, 'cutoff'): validate(graph([self.text]), ASOF)

    @patch('clinical_core.agere.clinical.graph_reports.attach', new=lambda path,op,patients,parameters,prep,result,selection: result)
    @patch('clinical_core.agere.clinical.graph_reports.dependencies', new=lambda path,graph: {})
    def test_outcome_membership_unknowns_and_dependency_refresh(self):
        params={'contract':'discharge_admission_source_30d','index_from':'2026-01-01',
                'index_through':ASOF,'available_by':ASOF,'filter':'observed'}
        west=node('west','cohort',parameters={'specification':{**empty(),'all':[{'kind':'patient','field':'region','op':'eq','value':'west'}]}})
        plan=graph([west,node('events','outcome',['west'],params),
            node('coverage','analyze',['events'],{'operator':'source_completeness','view':'all','drug_class':'','analysis_window':WINDOW})])
        identity={'identity':'outcome-v1'}
        with patch('clinical_core.agere.clinical.outcomes.prepare',side_effect=lambda *args:identity.copy()), \
             patch('clinical_core.agere.clinical.outcomes.execute',return_value=({'p0'},{'p1','p2'},{'patients_evaluated':3})) as run:
            first=execute(self.stack,plan)
            self.assertEqual(run.call_args.args[1],['p0','p1','p2'])
            self.assertEqual(first['results'][1]['result']['counts'],{'evaluated':6,'matched':1,'not_matched':2,'unknown':3})
            self.assertEqual(first['results'][-1]['result']['summary']['patients'],1)
            self.assertTrue(execute(self.stack,plan)['cache_hit']);self.assertEqual(run.call_count,1)
            identity['identity']='outcome-v2'
            self.assertFalse(execute(self.stack,plan)['cache_hit'])

    def test_provider_request_fits_existing_bound(self):
        def capture(body, *args, **kwargs):
            if REQUEST_MAX_BYTES is not None:
                self.assertLess(len(json.dumps(body).encode()), REQUEST_MAX_BYTES)
            else:
                self.assertGreater(len(json.dumps(body, allow_nan=False).encode()), 0)
            schema = body['output_config']['format']['schema']
            if 'segments' in schema['properties']:
                result = {'segments': [{'text': 'Describe observed populations.',
                    'operations': ['cohort'], 'source_streams': [], 'unmet': ''}]}
                raw = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(result)}]}
                return kwargs['parse'](raw), {'cache_hit': True}
            result = {
                'interpretation': 'Full population', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'base', 'operation': 'cohort', 'inputs': [], 'purpose': 'population',
                           'parameters_json': json.dumps({'specification': empty()})}]}
            if 'requirement_coverage' in schema['properties']:
                result['requirement_coverage'] = [{'segment': 0, 'node_ids': ['base'], 'unmet': ''}]
                if 'coverage' not in schema['properties']:
                    result.pop('coverage');result.pop('unmet_requirements')
            raw = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(result)}]}
            self.assertEqual(kwargs['parse'](raw)['nodes'][0]['id'], 'base')
            return kwargs['parse'](raw), {'cache_hit': True}
        with patch('clinical_core.agere.clinical.graph_planner.send', side_effect=capture):
            propose('Describe observed populations.', self.root, None, snapshot=self.meta['snapshot'], graph_catalog=catalog(self.root))


if __name__ == '__main__': unittest.main()
