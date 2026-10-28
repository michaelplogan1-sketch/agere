"""Authored operational delivery/cost fixtures; no clinical accuracy or provider claim."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from clinical_core.agere.clinical import acquisition_policy as A, learning as L

ROUTES=('local_compiler','metric_router','frontier_escalation')


def rows(kind='same'):
    result=[]
    for i in range(100):
        a=i<80
        b=a if kind=='same' else i>=20 if kind=='disjoint' else i<64 or 80<=i<96
        for route,ok in zip(ROUTES,(a,b,not a)):
            result.append(dict(family='count',route=route,ok=ok,usd=1. if route==ROUTES[2] else 0.,calls=int(route==ROUTES[2]),
                               question=f'How many authored items for case {i}?',source_context='authored-release',episode_id=f'case-{i}',
                               attempt_id=f'{i}-{route}',prior_routes=[]))
    return result


def policy(data,**params):
    return A.estimate(data,A.validate({'family':'count',**params}))[1][0]


class AcquisitionDependence(unittest.TestCase):
    def test_same_failure_pair_is80_and_complement_is_retained(self):
        data=rows();pair=A.paired_replay(data,ROUTES[:2])
        self.assertEqual(pair['coverage'],.8)
        self.assertEqual(pair['conditional']['additional_deliveries'],0)
        p=policy(data)
        self.assertEqual(p['try_order'],[ROUTES[0],ROUTES[2]])
        self.assertEqual(p['cumulative_success'],1.)
        self.assertAlmostEqual(p['expected_usd_per_original_question'],.2)
        self.assertAlmostEqual(p['usd_per_delivered_workflow'],.2)

    def test_identical_marginals_preserve_different_joint_structure(self):
        same,apart,ind=rows(),rows('disjoint'),rows('independent')
        def marginals(data):return [(r,sum(x['ok'] for x in data if x['route']==r)) for r in ROUTES]
        self.assertEqual(marginals(same),marginals(apart));self.assertEqual(marginals(same),marginals(ind))
        self.assertEqual(A.paired_replay(same,ROUTES[:2])['coverage'],.8)
        self.assertEqual(A.paired_replay(apart,ROUTES[:2])['coverage'],1.)
        self.assertEqual(A.paired_replay(ind,ROUTES[:2])['coverage'],.96)
        self.assertEqual(policy(apart)['try_order'],list(ROUTES[:2]))

    def test95_percent_is_not_an_automatic_budget_or_stopping_rule(self):
        p=policy(rows('independent'),min_attempts=2)
        self.assertEqual(p['steps'][1]['coverage'],.96)
        self.assertEqual(p['try_order'],list(ROUTES))
        self.assertEqual(p['cumulative_success'],1.)
        self.assertAlmostEqual(p['expected_usd_per_original_question'],.04)

    def test_unpaired_marginals_do_not_identify_coverage_or_stopping_cost(self):
        data=[(r['family'],r['route'],r['ok'],r['usd'],r['calls']) for r in rows()]
        p=policy(data)
        self.assertIsNone(p['cumulative_success']);self.assertIsNone(p['coverage_bounds'])
        self.assertIsNone(p['expected_usd_per_original_question'])
        self.assertIn(ROUTES[2],p['try_order'])
        self.assertIn('no automatic stopping',p['stop_reason'])
        self.assertIn('independent',p['independence_scenario']['assumption'])

    def test_missing_attempt_is_unknown_not_a_failure(self):
        data=[r for r in rows() if not (r['episode_id']=='case-80' and r['route']==ROUTES[2])]
        replay=A.paired_replay(data,[ROUTES[0],ROUTES[2]])
        self.assertIsNone(replay['coverage'])
        self.assertEqual(replay['coverage_bounds'],[.99,1.])
        self.assertIsNone(replay['expected_usd_per_question'])
        self.assertEqual(replay['conditional']['observed'],19)

    def test_same_question_or_episode_cannot_pair_across_source_contexts(self):
        data=rows()
        for r in data:
            if r['route']==ROUTES[1]:r['source_context']='different-release'
        replay=A.paired_replay(data,ROUTES[:2])
        self.assertIsNone(replay['coverage'])
        self.assertEqual(replay['episodes'],200)

    def test_observed_route_history_and_prior_attempt_must_match(self):
        for wrong in ['route','attempt','malformed']:
            data=rows()
            for r in data:
                if r['route']==ROUTES[2]:
                    previous=ROUTES[1] if wrong=='route' else ROUTES[0]
                    r['prior_routes']='malformed' if wrong=='malformed' else [{'route':previous,
                        'attempt_id':'other' if wrong=='attempt' else f"{r['episode_id'][5:]}-{previous}"}]
            replay=A.paired_replay(data,[ROUTES[0],ROUTES[2]])
            self.assertIsNone(replay['coverage'],wrong)
            self.assertEqual(replay['coverage_bounds'],[.8,1.])

    def test_matching_observed_history_retains_residual_delivery(self):
        data=rows()
        for r in data:
            if r['route']==ROUTES[2]:r['prior_routes']=[{'route':ROUTES[0],'attempt_id':f"{r['episode_id'][5:]}-{ROUTES[0]}"}]
        self.assertEqual(A.paired_replay(data,[ROUTES[0],ROUTES[2]])['coverage'],1.)

    def test_duplicate_event_does_not_double_count_delivery_or_cost(self):
        data=rows();data.append(copy.deepcopy(data[-1]))
        clean=A._attempts(data)
        self.assertEqual(len(clean),300)
        table,_=A.estimate(data,A.validate({}))
        paid=next(x for x in table if x['operation']==ROUTES[2])
        self.assertEqual(paid['attempts'],100);self.assertEqual(paid['answered'],20)
        self.assertEqual(A.paired_replay(clean,[ROUTES[0],ROUTES[2]])['coverage'],1.)

    def test_conflicting_event_identity_does_not_manufacture_a_success_or_zero_cost(self):
        data=rows();duplicate=copy.deepcopy(data[-1]);duplicate['ok']=False;data.append(duplicate)
        clean=A._attempts(data)
        conflict=next(r for r in clean if r.get('identity_conflict'))
        self.assertIsNone(conflict['ok']);self.assertIsNone(conflict['usd'])
        table,p=A.estimate(data,A.validate({}))
        paid=next(x for x in table if x['operation']==ROUTES[2])
        self.assertEqual(paid['unknown_labels'],1)
        self.assertIsNone(p[0]['cumulative_success'])

    def test_distinct_retries_retain_cost_but_do_not_imply_single_route_potential(self):
        data=rows();retry=copy.deepcopy(data[-1]);retry['attempt_id']='retry';data.append(retry)
        table,_=A.estimate(data,A.validate({}))
        paid=next(x for x in table if x['operation']==ROUTES[2])
        self.assertEqual(paid['attempts'],101)
        self.assertIsNone(A.paired_replay(data,[ROUTES[0],ROUTES[2]])['coverage'])

    def test_unknown_cost_is_not_free_or_known_best_value(self):
        data=rows()
        for r in data:
            if r['route']==ROUTES[2]:r['usd']=None
        table,p=A.estimate(data,A.validate({}))
        paid=next(x for x in table if x['operation']==ROUTES[2])
        self.assertIsNone(paid['mean_usd']);self.assertFalse(paid['free']);self.assertIsNone(paid['p_best_value'])
        self.assertIsNone(p[0]['expected_usd_per_original_question'])
        self.assertIsNone(p[0]['usd_per_delivered_workflow'])

    def test_missing_attempt_identity_does_not_silently_pair(self):
        data=rows();data[0]['attempt_id']=None
        self.assertIsNone(A.paired_replay(data,ROUTES[:2])['coverage'])

    def test_writer_binds_source_and_preserves_episode_attempt_and_history(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'run.json').write_text('{"source_digest":"authored-source"}')
            L.record_outcome(root,'How many authored items?','local_compiler','declined',usd=.5,
                             episode_id='episode',attempt_id='a',prior_routes=[])
            L.record_outcome(root,'How many authored items?','frontier_escalation','answered',usd=1.,paid_calls=1,
                             episode_id='episode',attempt_id='b',prior_routes=[{'route':'local_compiler','attempt_id':'a'}])
            raw=A.outcome_rows(root)
            self.assertEqual(raw[0]['episode_id'],raw[1]['episode_id'])
            self.assertEqual(raw[1]['attempt_id'],'b');self.assertTrue(raw[0]['source_context'].startswith('authored-source|'))
            self.assertEqual(json.loads(raw[1]['prior_routes'])[0]['attempt_id'],'a')
            report=A.execute(root,None,{'min_attempts':2},'2026-10-28')
            self.assertEqual(report['recorded_total_usd'],1.5) # failed attempt cost retained
            self.assertEqual(report['recorded_paid_calls'],1)

    def test_ordinary_repeated_questions_get_distinct_episodes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'run.json').write_text('{"source_digest":"authored-source"}')
            for route in ROUTES[:2]:L.record_outcome(root,'How many authored items?',route,'answered')
            raw=A.outcome_rows(root)
            self.assertNotEqual(raw[0]['episode_id'],raw[1]['episode_id'])
            self.assertNotEqual(raw[0]['attempt_id'],raw[1]['attempt_id'])
            self.assertIsNone(A.paired_replay(raw,list(ROUTES[:2]))['expected_usd_per_question'])

    def test_metadata_migration_preserves_legacy_rows_without_invented_pairing(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'run.json').write_text('{"source_digest":"authored-source"}')
            db=sqlite3.connect(root/'learning.sqlite3')
            db.execute('CREATE TABLE outcomes(question,route,status,reason,paid_calls,usd,created)')
            db.execute('INSERT INTO outcomes VALUES (?,?,?,?,?,?,?)',('How many authored items?',ROUTES[0],'declined','old',1,.5,1.))
            db.commit();db.close()
            L.record_outcome(root,'How many authored items?',ROUTES[2],'answered',usd=1.)
            raw=A.outcome_rows(root)
            self.assertEqual(len(raw),2);self.assertEqual(raw[0]['usd'],.5)
            self.assertIsNone(raw[0]['source_context']);self.assertIsNone(raw[0]['episode_id'])
            self.assertIsNone(A.paired_replay(raw,[ROUTES[0],ROUTES[2]])['coverage'])

    def test_evaluation_keeps_outcome_schema_and_identity_writes_off(self):
        from clinical_core.agere.clinical import evaluation
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'run.json').write_text('{"source_digest":"authored-source"}')
            L.record_outcome(root,'seed','local_compiler','answered')
            before=(root/'learning.sqlite3').read_bytes()
            with evaluation.scope('authored-no-learning',root):
                L.record_outcome(root,'How many authored items?','frontier_escalation','answered',episode_id='x',attempt_id='y')
            self.assertEqual((root/'learning.sqlite3').read_bytes(),before)

    def test_legacy_reader_does_not_invent_metadata_and_headline_exposes_gap(self):
        with tempfile.TemporaryDirectory() as d:
            db=sqlite3.connect(Path(d)/'learning.sqlite3')
            db.execute('CREATE TABLE outcomes(question,route,status,usd,paid_calls)')
            db.executemany('INSERT INTO outcomes VALUES (?,?,?,?,?)',[(f'How many authored items for case {i}?',ROUTES[0],'answered',0,0) for i in range(15)])
            db.commit();db.close()
            report=A.execute(d,None,{},'2026-10-28')
            self.assertIsNone(A.outcome_rows(d)[0]['episode_id'])
            self.assertIn('joint coverage unknown',A.headline(report))
            self.assertNotIn('cumulative success',A.headline(report))

    def test_minimum_evidence_remains_explicit_and_not_an_untried_route_failure(self):
        p=policy(rows('independent'))
        self.assertEqual(p['cumulative_success'],.96)
        self.assertIn(ROUTES[2],p['remaining_candidates'])
        self.assertIn(ROUTES[2],p['not_demonstrated'])
        self.assertIn('unresolved',p['stop_reason'])


if __name__=='__main__':unittest.main()
