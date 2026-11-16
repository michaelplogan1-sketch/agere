"""Provider network: patient-sharing graph, centrality, communities, care density, leakage, steering, spread, routing, wiring."""
import json, os, re, shutil, sqlite3, subprocess, tempfile, unittest
from itertools import combinations
from pathlib import Path

import numpy as np
from scipy.stats import hypergeom

from clinical_core.agere.clinical import (analysis_graph, capability_registry, graph_contracts, graph_planner, local_compiler,
                                          network as nw, pathway, semantic_layer, source_profile)
from clinical_core.agere.clinical.analysis_graph import build_answer, validate
from test_aggregate_explain import build

ROOT = Path(__file__).resolve().parents[2]
AS_OF = '2030-05-27'
REAL_V20 = Path(os.environ.get('NETWORK_STACK') or next(
    (p / 'artifacts' / 'agere-healthcare-v20' / 'stack' for p in (ROOT, *ROOT.parents) if (p / 'artifacts' / 'agere-healthcare-v20' / 'stack').exists()),
    ROOT / 'artifacts' / 'agere-healthcare-v20' / 'stack'))


# ---- graph construction ----------------------------------------------------------------------------------
class Construction(unittest.TestCase):
    def test_projection_counts_each_patient_once(self):
        pairs = [('p1', 'A'), ('p1', 'A'), ('p1', 'B'), ('p2', 'A'), ('p2', 'B'), ('p3', 'B'), ('p3', 'C'), ('p4', 'C')]
        provs, pts, W = nw.project(pairs)
        self.assertEqual(provs, ['A', 'B', 'C'])
        self.assertEqual(len(pts), 4)
        np.testing.assert_array_equal(W, [[2, 2, 0], [2, 3, 1], [0, 1, 2]])

    def test_threshold_and_hypergeometric_filter(self):
        W = np.array([[30, 25, 3], [25, 30, 3], [3, 3, 30]], float)
        A, info = nw.edge_filter(W, 100, min_shared=3, method='hypergeometric', alpha=0.05)
        self.assertEqual(info['candidates'], 3)
        p_ab = hypergeom.sf(24, 100, 30, 30)
        self.assertAlmostEqual(info['stats'][(0, 1)]['p'], p_ab)
        self.assertEqual(A[0, 1], 25)
        self.assertEqual(A[0, 2], 0)  # 3 shared of 30 x 30 in 100 is below chance (expected 9)
        A2, _ = nw.edge_filter(W, 100, min_shared=26, method='none')
        self.assertFalse(A2.any())

    def test_disparity_filter_matches_formula(self):
        W = np.array([[0, 10, 1, 1], [10, 0, 1, 0], [1, 1, 0, 0], [1, 0, 0, 0]], float)
        A, info = nw.edge_filter(W, 50, min_shared=1, method='disparity', alpha=0.2)
        s0, k0 = 12, 3
        self.assertAlmostEqual(info['stats'][(0, 1)]['alpha_i'], (1 - 10 / s0) ** (k0 - 1))
        self.assertTrue(A[0, 1] > 0)
        self.assertTrue(A[0, 3] > 0)  # degree-1 endpoint kept
        self.assertEqual(A[0, 2], 0)  # weak edge between two hubs-with-options dropped: (1-1/12)^2 and (1-1/2)^1 both >= 0.2


# ---- centrality vs hand computation ---------------------------------------------------------------------
class Centrality(unittest.TestCase):
    def test_path_and_star_betweenness(self):
        P = np.zeros((4, 4)); P[0, 1] = P[1, 0] = P[1, 2] = P[2, 1] = P[2, 3] = P[3, 2] = 1
        raw, normed = nw.betweenness(P)
        np.testing.assert_allclose(raw, [0, 2, 2, 0])  # b lies on a-c and a-d
        np.testing.assert_allclose(normed, [0, 2 / 3, 2 / 3, 0])
        S = np.zeros((5, 5)); S[0, 1:] = S[1:, 0] = 1
        raw, normed = nw.betweenness(S)
        self.assertEqual(raw[0], 6)  # C(4, 2) leaf pairs
        self.assertEqual(normed[0], 1)

    def test_weighted_betweenness_uses_inverse_weight(self):
        T = np.zeros((3, 3)); T[0, 1] = T[1, 0] = 1; T[0, 2] = T[2, 0] = 4; T[1, 2] = T[2, 1] = 4
        raw, _ = nw.betweenness(T)  # a-b direct costs 1, via c costs 0.25 + 0.25
        np.testing.assert_allclose(raw, [0, 0, 1])
        raw, _ = nw.betweenness(T, weighted=False)
        np.testing.assert_allclose(raw, [0, 0, 0])

    def test_pagerank_and_eigenvector(self):
        C = np.zeros((5, 5))
        for i in range(5): C[i, (i + 1) % 5] = C[(i + 1) % 5, i] = 1
        np.testing.assert_allclose(nw.pagerank(C), [0.2] * 5)
        A = np.array([[0, 2, 1, 0], [2, 0, 0, 0], [1, 0, 0, 3], [0, 0, 3, 0]], float)
        M = A / A.sum(1, keepdims=True)
        exact = np.linalg.solve(np.eye(4) - 0.85 * M.T, np.full(4, 0.15 / 4))
        np.testing.assert_allclose(nw.pagerank(A), exact / exact.sum(), atol=1e-9)
        S = np.zeros((5, 5)); S[0, 1:] = S[1:, 0] = 1
        np.testing.assert_allclose(nw.eigenvector(S), [1, *[1 / 2] * 4])  # leaves = 1/sqrt(n-1) of the centre


# ---- communities --------------------------------------------------------------------------------------------
class Communities(unittest.TestCase):
    def test_planted_partition_recovered(self):
        rng = np.random.default_rng(3)
        truth = np.repeat(np.arange(4), 10)
        A = np.zeros((40, 40))
        for i, j in combinations(range(40), 2):
            if rng.random() < (0.8 if truth[i] == truth[j] else 0.03):
                A[i, j] = A[j, i] = rng.integers(1, 5)
        labels, Q = nw.louvain(A, seed=0)
        self.assertEqual({frozenset(np.nonzero(labels == c)[0]) for c in set(labels)},
                         {frozenset(np.nonzero(truth == c)[0]) for c in range(4)})
        self.assertAlmostEqual(Q, nw.modularity(A, truth))
        self.assertGreater(Q, 0.5)
        again, _ = nw.louvain(A, seed=0)
        np.testing.assert_array_equal(labels, again)

    def test_two_cliques_and_a_bridge(self):
        A = np.zeros((10, 10))
        for g in (range(5), range(5, 10)):
            for i, j in combinations(g, 2): A[i, j] = A[j, i] = 3
        A[4, 5] = A[5, 4] = 1
        labels, _ = nw.louvain(A, seed=1)
        self.assertEqual(len(set(labels[:5])), 1); self.assertEqual(len(set(labels[5:])), 1)
        self.assertNotEqual(labels[0], labels[9])
        _, normed = nw.betweenness(A)
        self.assertEqual(set(np.argsort(-normed)[:2]), {4, 5})  # the bridge ends are the brokers


# ---- cohesion, concentration, steering, spread math ------------------------------------------------------------
class Formulas(unittest.TestCase):
    def test_care_density_pollack(self):
        W = np.array([[9, 4, 2], [4, 7, 6], [2, 6, 8]], float)
        idx = {'a': 0, 'b': 1, 'c': 2}
        d = nw.care_density({'x': {'a', 'b', 'c'}, 'y': {'a', 'b'}, 'z': {'c'}}, W, idx)
        self.assertAlmostEqual(d['x'], (4 + 2 + 6) / 3)
        self.assertAlmostEqual(d['y'], 4.0)
        self.assertIsNone(d['z'])

    def test_hhi(self):
        self.assertEqual(nw.hhi({'a': 10}), 10000)
        self.assertAlmostEqual(nw.hhi({'a': 5, 'b': 5}), 5000)
        self.assertAlmostEqual(nw.hhi([6, 3, 1]), 10000 * (0.36 + 0.09 + 0.01))
        self.assertIsNone(nw.hhi({}))

    def test_steering_arithmetic(self):
        s = nw.steering(40, 25, 8000.0, 0.5, 100, 0.25, target_price=150.0)
        self.assertEqual((s['recovered_visits'], s['recovered_patients'], s['recovered_dollars']), (20, 12.5, 4000))
        self.assertEqual(s['recovered_dollars_at_target_price'], 3000)
        self.assertEqual(s['target_volume_increase'], 0.2)
        self.assertTrue(s['within_capacity'])
        self.assertFalse(nw.steering(40, 25, 0, 1.0, 100, 0.25)['within_capacity'])
        self.assertTrue(nw.steering(0, 0, 0, 0.5, 0, 0.25)['within_capacity'])

    def test_greedy_seeds_pick_the_two_star_centres(self):
        W = np.zeros((12, 12))
        for c, leaves in ((0, range(1, 7)), (7, range(8, 12))):
            for l in leaves: W[c, l] = W[l, c] = 5
        samples = nw.live_edge_samples(W, 0.3, 200, np.random.default_rng(1))
        seeds, gains = nw.greedy_seeds(2, 12, lambda s: float(nw.cascade_curve(samples, s, 4)[:, -1].mean()))
        self.assertEqual(seeds, [0, 7])
        self.assertGreater(gains[0], gains[1])

    def test_cascade_reproducible_and_monotone(self):
        rng = np.random.default_rng(0)
        W = (rng.random((15, 15)) < 0.3) * rng.integers(1, 4, (15, 15)); W = np.triu(W, 1); W = W + W.T
        a = nw.cascade_curve(nw.live_edge_samples(W, 0.2, 50, np.random.default_rng(9)), [0], 5)
        b = nw.cascade_curve(nw.live_edge_samples(W, 0.2, 50, np.random.default_rng(9)), [0], 5)
        np.testing.assert_array_equal(a, b)
        self.assertTrue((np.diff(a, axis=1) >= 0).all())
        lt = nw.threshold_curve(W, [0, 1], 5, np.random.default_rng(2).random((30, 15)))
        self.assertTrue((lt[:, 0] == 2).all() and (np.diff(lt, axis=1) >= 0).all())

    def test_parameters(self):
        p = nw.validate({})
        self.assertEqual((p['focus'], p['level'], p['leakage'], p['min_shared']), ('all', 'clinician', 'organization', 2))
        self.assertEqual(set(pathway.LEAKAGE) - set(nw.LEAKAGE), set())  # the pathway definitions are reused, not redefined
        for bad in ({'focus': 'x'}, {'nope': 1}, {'leakage': 'tins'}, {'focus': 'steering'}, {'steer_share': 2}, {'seeds': 0},
                    {'period': {'start': '2030-02-01', 'end': '2030-01-01'}}, {'include_ancillary': 'yes'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                nw.validate(bad)


# ---- fixture warehouse (semantic-layer/13 shape, the tables the engine reads) -----------------------------------
DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE patient(patient_id TEXT PRIMARY KEY, pcp_clinician_id TEXT, pcp_practice_id TEXT, coverage_type TEXT);
CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, status TEXT, event_date TEXT, organization_id TEXT, attending_clinician_id TEXT);
CREATE TABLE claim(claim_id TEXT, patient_id TEXT, service_date TEXT, final_status TEXT, allowed_cents INTEGER, rendering_npi TEXT,
  service_organization_id TEXT);
CREATE TABLE clinician(clinician_id TEXT, organization_id TEXT, specialty TEXT, npi TEXT);
CREATE TABLE organization(organization_id TEXT, organization_type TEXT, display_name TEXT, npi TEXT, tin TEXT);
CREATE TABLE record(record_id TEXT, patient_id TEXT, stream TEXT, event_date TEXT, payload TEXT);
"""
ORGS = [('PR1', 'primary_care', 'Care practice 1', '11-0000001'), ('PR2', 'primary_care', 'Care practice 2', '11-0000002'),
        ('SPEC-A', 'independent_specialty_practice', 'District A specialty care', '11-0000003'),
        ('SPEC-B', 'independent_specialty_practice', 'District B specialty care', '11-0000004'),
        ('WH-OUTSIDE-CARD', 'independent_outpatient_practice', 'Outside cardiology group', '99-0000001'),
        ('LAB', 'independent_outpatient_laboratory', 'Reference lab', '11-0000005')]
CLINS = [('pcp1', 'PR1', 'Family Medicine'), ('pcp2', 'PR1', 'Family Medicine'), ('pcp3', 'PR2', 'Family Medicine'),
         ('cardA', 'SPEC-A', 'Cardiology'), ('cardB', 'SPEC-B', 'Cardiology'), ('endoA', 'SPEC-A', 'Endocrinology'),
         ('cardOut', 'WH-OUTSIDE-CARD', 'Cardiology'), ('labtech', 'LAB', 'Diagnostic Technician')]


def fixture(legacy=False):
    root = Path(tempfile.mkdtemp())
    db = sqlite3.connect(root / 'semantic.sqlite3')
    db.executescript(DDL)
    db.execute("INSERT INTO meta VALUES ('version','semantic-layer/13'), ('as_of', ?)", (AS_OF,))
    for oid, typ, name, tin in ORGS:
        db.execute('INSERT INTO organization VALUES (?,?,?,?,?)', (oid, typ, name, None if legacy else '1' + tin[:2], None if legacy else tin))
    for k, (cid, oid, spec) in enumerate(CLINS):
        db.execute('INSERT INTO clinician VALUES (?,?,?,?)', (cid, oid, spec, None if legacy else f'10000000{k:02d}'))
    pts = []
    for i in range(12):  # p0-p5 at PR1 (pcp1/pcp2), p6-p11 at PR2 (pcp3); p11 unattributed
        pcp = 'pcp1' if i < 3 else 'pcp2' if i < 6 else 'pcp3'
        pts.append((f'p{i}', pcp if i != 11 else None, ('PR1' if i < 6 else 'PR2') if i != 11 else None))
    db.executemany('INSERT INTO patient VALUES (?,?,?,?)', [(*p, 'commercial') for p in pts])
    visits = []
    for pid, pcp, prac in pts:
        if pcp: visits += [(pid, '2029-03-01', prac, pcp), (pid, '2029-09-01', prac, pcp)]
    # PR1 patients see cardA; PR2 patients see cardB; p0, p1, p6 also leak to the outside cardiologist; p2/p3 see endoA
    for i in range(6): visits.append((f'p{i}', '2029-05-01', 'SPEC-A', 'cardA'))
    for i in range(6, 11): visits.append((f'p{i}', '2029-05-02', 'SPEC-B', 'cardB'))
    for i in (0, 1, 6): visits.append((f'p{i}', '2029-06-01', 'WH-OUTSIDE-CARD', 'cardOut'))
    for i in (2, 3): visits.append((f'p{i}', '2029-07-01', 'SPEC-A', 'endoA'))
    for i in range(11): visits.append((f'p{i}', '2029-04-01', 'LAB', 'labtech'))  # ancillary: kept out of the sharing graph
    visits.append(('p0', '2020-01-01', 'SPEC-B', 'cardB'))  # outside the default 730-day window
    for k, (pid, d, org, clin) in enumerate(visits):
        db.execute('INSERT INTO visit VALUES (?,?,?,?,?,?)', (f'e{k}', pid, 'completed', d, org, clin))
    npi = {c[0]: (None if legacy else f'10000000{k:02d}') for k, c in enumerate(CLINS)}
    claims = [('c1', 'p0', '2029-06-01', 'paid', 30000, 'cardOut', 'WH-OUTSIDE-CARD'), ('c2', 'p1', '2029-06-01', 'paid', 20000, 'cardOut', 'WH-OUTSIDE-CARD'),
              ('c3', 'p6', '2029-06-01', 'denied', 50000, 'cardOut', 'WH-OUTSIDE-CARD'), ('c4', 'p0', '2029-05-01', 'paid', 10000, 'cardA', 'SPEC-A'),
              ('c5', 'p7', '2029-05-02', 'paid', 15000, 'cardB', 'SPEC-B')]
    for cid, pid, d, st, cents, clin, org in claims:
        db.execute('INSERT INTO claim VALUES (?,?,?,?,?,?,?)', (cid, pid, d, st, cents, npi[clin], org))
    db.execute("INSERT INTO record VALUES ('r1','p0','referrals','2029-04-15',?)",
               (json.dumps({'requesting_clinician_id': 'pcp1', 'receiving_organization_id': 'SPEC-A', 'specialty': 'Cardiology'}),))
    db.execute("INSERT INTO record VALUES ('r2','p6','referrals','2029-04-15',?)",
               (json.dumps({'requesting_encounter_id': 'e12', 'receiving_clinician_id': 'cardB', 'receiving_organization_id': 'SPEC-B', 'specialty': 'Cardiology'}),))
    db.commit(); db.close()
    return root


class Engine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = fixture()
        cls.report = nw.execute(cls.path, None, {'min_shared': 1, 'filter': 'none'}, AS_OF)
        cls.nodes = {n['id']: n for n in cls.report['nodes']}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.path, ignore_errors=True)

    def test_graph_construction_on_fixture(self):
        r = self.report
        self.assertEqual(r['status'], 'answered')
        self.assertEqual(set(self.nodes), {'pcp1', 'pcp2', 'pcp3', 'cardA', 'cardB', 'endoA', 'cardOut'})  # lab left out, old visit outside window
        self.assertEqual(self.nodes['cardA']['patients'], 6)
        shared = {frozenset((e['a'], e['b'])): e['shared'] for e in r['edges']}
        self.assertEqual(shared[frozenset(('pcp1', 'cardA'))], 3)
        self.assertEqual(shared[frozenset(('cardA', 'cardOut'))], 2)
        self.assertEqual(shared[frozenset(('cardA', 'endoA'))], 2)
        self.assertNotIn(frozenset(('cardA', 'cardB')), shared)
        self.assertFalse(self.nodes['cardOut']['in_network'])
        self.assertEqual(self.report['graph']['providers'], 7)
        # cardOut is the only bridge between the districts: all 4 x 2 cross pairs pass through it (8 of C(7,2) = 21 pairs);
        # cardA carries 8 within-district shortest paths (pcp1-pcp2, endoA-cardOut, pcp2-cardOut, ...): equal betweenness, higher PageRank
        self.assertAlmostEqual(self.nodes['cardOut']['betweenness'], 8 / 15, places=4)
        self.assertEqual({h['id'] for h in r['hubs'][:2]}, {'cardA', 'cardOut'})
        self.assertEqual(len(r['communities']), 2)

    def test_leakage_math(self):
        lk = self.report['leakage']
        self.assertEqual(lk['attributed_patients'], 11)
        o = lk['overall']
        card =next(r for r in lk['by'] if r['specialty'] == 'cardiology')
        self.assertEqual((card['visits'], card['outside_visits']), (14, 3))
        self.assertAlmostEqual(card['leakage_share'], 3 / 14, places=4)
        self.assertEqual(card['outside_dollars'], 500.0)  # denied claim counts zero dollars
        self.assertEqual(card['claim_dollars'], 750.0)
        self.assertEqual(o['outside_visits'], 3)
        self.assertIn('WH-OUTSIDE-CARD 3', card['outside_destinations'])
        by_practice = nw.execute(self.path, None, {'focus': 'leakage', 'group_by': 'practice'}, AS_OF)['leakage']['by']
        self.assertEqual({r['practice']: r['outside_visits'] for r in by_practice}, {'PR1': 2, 'PR2': 1})
        tins = nw.execute(self.path, None, {'focus': 'leakage', 'leakage': 'tins', 'network_tins': ['11-0000001', '11-0000002', '11-0000003']}, AS_OF)
        card = next(r for r in tins['leakage']['by'] if r['specialty'] == 'cardiology')
        self.assertEqual(card['outside_visits'], 3 + 5)  # SPEC-B is outside a TIN list that omits it

    def test_steering_on_fixture(self):
        r = nw.execute(self.path, None, {'focus': 'steering', 'steer_specialty': 'cardiology', 'steer_to': 'SPEC-A', 'steer_share': 0.5}, AS_OF)
        s = r['steering']
        self.assertEqual((s['pool_visits'], s['pool_patients'], s['pool_dollars']), (3, 3, 500.0))
        self.assertEqual((s['recovered_visits'], s['recovered_dollars']), (1.5, 250.0))
        self.assertEqual(s['target_current_visits'], 6)
        self.assertEqual(s['target_volume_increase'], 0.25)
        self.assertEqual(s['recovered_dollars_at_target_price'], 150.0)  # SPEC-A cardiology $100 per claim x 1.5
        value, table = nw.summarize(r)
        self.assertIn('recovers 1.5 visits', value)
        other = nw.execute(self.path, None, {'focus': 'steering', 'steer_specialty': 'cardiology', 'steer_to': 'care practice 9', 'steer_from': 'all_other'}, AS_OF)['steering']
        self.assertFalse(other['target_matched'])
        self.assertEqual(other['target'], 'SPEC-A')  # busiest in-network cardiology organisation
        self.assertEqual(other['pool_visits'], 5 + 3)

    def test_cohesion_concentration_sharing(self):
        r = self.report
        self.assertGreater(r['cohesion']['patients_with_2plus_providers'], 0)
        c = r['concentration']
        pcp1 = next(x for x in c['by_pcp'] if x['pcp'] == 'pcp1')
        self.assertEqual(pcp1['specialist_visits'], 3 + 2 + 1)  # p0-p2: 3 cardA + 2 outside cardiology + 1 endoA
        self.assertAlmostEqual(pcp1['hhi_organizations'], 10000 * ((4 / 6) ** 2 + (2 / 6) ** 2), places=1)
        self.assertAlmostEqual(pcp1['hhi_specialists'], 10000 * ((3 / 6) ** 2 + (2 / 6) ** 2 + (1 / 6) ** 2), places=1)
        self.assertEqual(c['referral_record_count'], 2)
        self.assertIn('pcp3', {x['requesting_clinician'] for x in c['referral_records']})  # resolved through the requesting encounter
        self.assertEqual(r['pcp_sharing']['pairs_sharing'], 0)

    def test_spread_reproducible(self):
        a = nw.execute(self.path, None, {'focus': 'spread', 'min_shared': 1, 'filter': 'none', 'random_seed': 5, 'runs': 50}, AS_OF)['spread']
        b = nw.execute(self.path, None, {'focus': 'spread', 'min_shared': 1, 'filter': 'none', 'random_seed': 5, 'runs': 50}, AS_OF)['spread']
        self.assertEqual(a, b)
        self.assertEqual(a['status'], 'model')
        self.assertEqual(a['pcps'], 3)
        self.assertEqual(a['over_time'][0]['expected_pcps'], a['seeds'].__len__())
        self.assertEqual(a['graph'], 'pcp_via_specialists')  # PCPs share no patient directly

    def test_answer_and_population(self):
        plan = validate({'interpretation': 'Who are the key referral hubs?', 'coverage': 'supported_observation', 'unmet_requirements': [],
                         'nodes': [{'id': 'answer', 'operation': 'network', 'inputs': [], 'purpose': 'hubs',
                                    'parameters': {'focus': 'hubs', 'min_shared': 1, 'filter': 'none'}}]}, AS_OF)
        report = nw.execute(self.path, None, plan['nodes'][0]['parameters'], AS_OF)
        answer = build_answer(plan, [{'node': plan['nodes'][0], 'result': report}], {}, plan['interpretation'])
        self.assertEqual((answer['status'], answer['answer_type']), ('answered', 'table'))
        self.assertEqual(answer['table'][0]['id'], 'cardA')
        self.assertIn('provider_network', answer['overall'])
        self.assertTrue(answer['overall']['provider_network']['visual']['nodes'])
        self.assertEqual(analysis_graph.benchmark_references(answer, operation='network'), [])
        json.dumps(answer)
        sub = nw.execute(self.path, ['p6', 'p7', 'p8'], {'min_shared': 1, 'filter': 'none'}, AS_OF)
        self.assertEqual(sub['graph']['patients'], 3)
        self.assertNotIn('cardA', {n['id'] for n in sub['nodes']})
        for focus in nw.FOCUS:
            with self.subTest(focus=focus):
                params = {'focus': focus, 'min_shared': 1, 'filter': 'none', 'runs': 30}
                if focus == 'steering': params['steer_specialty'] = 'cardiology'
                value, table = nw.summarize(nw.execute(self.path, None, params, AS_OF))
                self.assertTrue(value)

    def test_graph_validation(self):
        plan = {'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'answer', 'operation': 'network', 'inputs': [], 'purpose': 'n', 'parameters': {'period': {'start': '2031-01-01', 'end': ''}}}]}
        with self.assertRaises(ValueError): validate(plan, AS_OF)
        plan['nodes'][0]['parameters'] = {'focus': 'nope'}
        with self.assertRaises(ValueError): validate(plan, AS_OF)

    def test_v123_shape_degrades_and_says_so(self):
        path = fixture(legacy=True)
        try:
            r = nw.execute(path, None, {'min_shared': 1, 'filter': 'none', 'leakage': 'tins', 'network_tins': ['11-0000001']}, AS_OF)
            self.assertEqual(r['status'], 'answered')
            gaps = ' '.join(r['data_gaps'])
            self.assertIn('No clinician NPIs', gaps); self.assertIn('No organisation TINs', gaps); self.assertIn('rendering NPI', gaps)
            self.assertIn('leakage by TIN requested', gaps)
            self.assertEqual(r['parameters']['leakage'], 'organization')
            self.assertIn('cardOut', {n['id'] for n in r['nodes']})  # visits still carry the clinicians
            card = next(x for x in r['leakage']['by'] if x['specialty'] == 'cardiology')
            self.assertEqual(card['outside_visits'], 3)
        finally:
            shutil.rmtree(path, ignore_errors=True)


# ---- routing -------------------------------------------------------------------------------------------------
PHRASINGS = {'Which specialists and hospitals do our patients actually use?': 'usage',
             'Who are the key referral hubs?': 'hubs',
             'Where does care leak out of our preferred network?': 'leakage',
             'Which PCPs share the most patients?': 'pcp_sharing',
             'If we steer cardiology to practice 3, how much leakage do we recover?': 'steering',
             'How would a practice-pattern change spread through the network?': 'spread',
             'Show provider communities in our network': 'communities',
             'How cohesive are our care teams by practice?': 'cohesion',
             'What is the referral concentration (HHI) for each PCP?': 'concentration',
             'Which specialties have the highest leakage rate, and to which outside groups?': 'leakage',
             'Who are the most central providers in our referral network?': 'hubs',
             'Which seed PCPs would spread a new protocol fastest across the network?': 'spread'}
PATHWAY_QUESTIONS = [c['question'] for c in json.loads((ROOT / 'bench' / 'acceptance' / 'pathway.json').read_text())]


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patients = {f'p{i}': {'age_reported': 60, 'sex_reported': 'F'} for i in range(3)}
        recs = [(f'p{i}', 'coverage', {'coverage_type': 'medicare', 'event_date': '2026-01-01'}) for i in range(3)]
        recs += [('p0', 'labs', {'test': 'HbA1c', 'value': 7, 'unit': '%', 'event_date': '2028-01-01'}),
                 ('p0', 'adt', {'event': 'emergency_registration', 'event_date': '2028-01-01'}),
                 ('p0', 'encounters', {'type': 'office', 'event_date': '2028-01-01', 'organization_id': 'PR1'}),
                 ('p1', 'referrals', {'status': 'requested', 'event_date': '2028-02-01', 'organization_id': 'PR1'}),
                 ('p0', 'medical_claims', {'claim_id': 'C1', 'service_date': '2028-01-01', 'event_date': '2028-01-01', 'status': 'paid',
                                           'source_system': 'PAY1', 'billed_cents': 10000, 'allowed_cents': 8000, 'payer_paid_cents': 8000})]
        cls.path = build(recs, patients)
        semantic_layer.build(cls.path)
        cls.profile = source_profile.load(cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.path, ignore_errors=True)

    def compile(self, q):
        return local_compiler.compile(q, self.profile, '2029-08-15')

    def test_phrasings(self):
        for q, focus in PHRASINGS.items():
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertIsNotNone(g, info)
                node = g['nodes'][-1]
                self.assertEqual(node['operation'], 'network', info)
                self.assertEqual(node['parameters']['focus'], focus)
        g, _ = self.compile('If we steer 30% of cardiology to WH-SPECIALTY-2, how much leakage do we recover?')
        p = g['nodes'][-1]['parameters']
        self.assertEqual((p['steer_specialty'], p['steer_to'], p['steer_share']), ('cardiology', 'WH-SPECIALTY-2', 0.3))
        g, _ = self.compile('Leakage by practice for cardiology in 2029')
        p = g['nodes'][-1]['parameters']
        self.assertEqual((p['focus'], p['group_by'], p['specialty'], p['period']['start']), ('leakage', 'practice', 'cardiology', '2029-01-01'))
        g, _ = self.compile('Which organizations are the hubs of our referral network?')
        self.assertEqual(g['nodes'][-1]['parameters']['level'], 'organization')
        g, _ = self.compile('Which specialists do our heart failure patients actually use?')
        self.assertEqual([n['operation'] for n in g['nodes']], ['cohort', 'network'])

    def test_does_not_steal_pathway_or_claims_questions(self):
        for q in PATHWAY_QUESTIONS + ['Where are we leaking money in claims (denials, underpayments, rework)?',
                                      'What share of patients get primary care within 7 days after discharge?',
                                      'How many patients have diabetes?', 'Show the RAF score distribution spread',
                                      'Where is heart failure unusually common in our network?']:
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertNotEqual(g['nodes'][-1]['operation'] if g and g.get('nodes') else None, 'network', info)

    def test_acceptance_file(self):
        rows = json.loads((ROOT / 'bench' / 'acceptance' / 'network.json').read_text())
        self.assertGreaterEqual(len(rows), 8)
        for row in rows:
            with self.subTest(q=row['question']):
                self.assertEqual(row['expect_operation'], 'network')
                g, info = self.compile(row['question'])
                self.assertIsNotNone(g, info)
                self.assertEqual(g['nodes'][-1]['operation'], 'network', info)

    def test_follow_ups_compile_back(self):
        for q in nw.follow_up():
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertEqual(g['nodes'][-1]['operation'], 'network', info)


# ---- integration --------------------------------------------------------------------------------------------
class Integration(unittest.TestCase):
    def test_registered_everywhere(self):
        self.assertIn('network', analysis_graph.OPERATIONS)
        self.assertIn('network', analysis_graph.NO_BENCHMARK_OPERATIONS)
        self.assertIn('network', graph_contracts.TYPED)
        self.assertEqual(graph_contracts.input_contract('network')['count'], [0, 1])
        self.assertIn('network', graph_planner.CONTRACTS)
        self.assertEqual(capability_registry.PRODUCT['network']['population'], 'optional')
        self.assertIn('pathway', capability_registry.PRODUCT['network']['use'])
        schema = graph_contracts.parameter_schema('network', {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}})
        self.assertEqual(set(schema['properties']['focus']['enum']), set(nw.FOCUS))
        self.assertLess(len(json.dumps(schema)), 3500)
        self.assertTrue(set(schema['properties']) <= nw.FIELDS)
        nw.validate({k: v for k, v in graph_planner.CONTRACTS['network']['parameters'].items() if not isinstance(v, str) or '|' not in v})
        source = Path(analysis_graph.__file__).read_text()
        block = source[source.index("if last['operation'] == 'network':"):]
        block = block[:block.index("\n    if last['operation']", 10)]
        self.assertTrue(block.rstrip().endswith('return answer'))
        self.assertIn('network.py', source[source.index('def code_identity'):source.index('def code_identity') + 2500])

    def test_router_tool_comes_from_the_registry(self):
        from clinical_core.agere.clinical import operator_router
        self.assertIn('network', {t['name'] for t in operator_router.menu()})
        t = operator_router.tool('network')
        self.assertIn('referral hubs', t['description'])
        self.assertIn('population', t['input_schema']['properties'])

    def test_console_renders_network(self):
        from clinical_core.agere import ask_view
        html = ask_view.page()
        self.assertIn('function vizNetwork', html)
        self.assertIn('if(o.provider_network)return vizNetwork(a)', html)
        self.assertLess(html.index('if(o.provider_network)'), html.index('if(o.network&&o.data_gaps)'))  # before the markets view
        node = shutil.which('node') or str(Path.home() / '.local' / 'bin' / 'node')
        if not Path(node).exists(): self.skipTest('node not available for a syntax check')
        script = re.findall(r'<script>(.*?)</script>', html, re.S)[-1]
        tmp = Path(os.environ.get('TMPDIR', '/tmp')) / 'agere_network_check.js'
        tmp.write_text(script)
        out = subprocess.run([node, '--check', str(tmp)], capture_output=True, text=True)
        tmp.unlink()
        self.assertEqual(out.returncode, 0, out.stderr[:500])


def _v20_ready():
    try:
        db = sqlite3.connect(f"file:{REAL_V20 / 'semantic.sqlite3'}?mode=ro", uri=True)
        meta = dict(db.execute('SELECT key, value FROM meta')); db.close()
        return meta.get('version') == semantic_layer.VERSION
    except Exception:
        return False


@unittest.skipUnless(_v20_ready(), 'warehouse v2.0 semantic layer not present (never rebuilt here)')
class RealV20(unittest.TestCase):
    def test_smoke(self):
        r = nw.execute(REAL_V20, None, {}, AS_OF)
        self.assertEqual(r['status'], 'answered')
        self.assertFalse(r['data_gaps'])  # v2.0 carries NPIs, TINs and rendering NPIs
        self.assertGreater(r['graph']['providers'], 50)
        self.assertGreater(r['graph']['edges'], 50)
        self.assertGreaterEqual(len(r['communities']), 2)
        self.assertTrue(0 <= r['leakage']['overall']['leakage_share'] < 1)
        self.assertTrue(r['usage'])
        s = nw.execute(REAL_V20, None, {'focus': 'spread', 'runs': 60}, AS_OF)['spread']
        self.assertEqual(s['status'], 'model')
        self.assertGreaterEqual(s['final_expected_pcps'], len(s['seeds']))


if __name__ == '__main__':
    unittest.main()
