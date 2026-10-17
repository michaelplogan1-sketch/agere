import math, random, sqlite3, unittest
from datetime import datetime, timedelta

from clinical_core.agere.clinical import queueing as q


def erlang_c_reference(c, a):
    """Textbook formula with factorials: C = (a^c/c! * c/(c-a)) / (sum_{k<c} a^k/k! + a^c/c! * c/(c-a))."""
    top = a ** c / math.factorial(c) * c / (c - a)
    return top / (sum(a ** k / math.factorial(k) for k in range(c)) + top)


def poisson(rng, lam):
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L: return k
        k += 1


def make_arrivals(rate_fn, days=70, seed=1, overdispersed=False):
    rng = random.Random(seed)
    start = datetime(2024, 1, 1)  # a Monday
    out = []
    for d in range(days):
        day_factor = rng.gammavariate(2.0, 0.5) if overdispersed else 1.0  # mean 1, CV 0.71 day-level surge
        for h in range(24):
            lam = rate_fn(h) * day_factor
            for _ in range(poisson(rng, lam)):
                out.append((start + timedelta(days=d, hours=h, minutes=rng.random() * 60)).isoformat())
    return out


class ErlangFormulas(unittest.TestCase):
    def test_erlang_c_matches_reference_formula(self):
        for lam, mu, c in ((10, 3, 4), (10, 3, 5), (50, 6, 10), (2, 1, 3), (95, 1, 100)):
            self.assertAlmostEqual(q.erlang_c(c, lam / mu), erlang_c_reference(c, lam / mu), places=10)

    def test_textbook_case_lambda10_mu3_c4(self):
        # Hand check: a = 10/3, rho = 5/6. Sum_{k<4} a^k/k! = 1 + 3.3333 + 5.5556 + 6.1728 = 16.0617;
        # top = a^4/24 * 4/(4-a) = 5.1440 * 6 = 30.864; C = 30.864 / 46.926 = 0.6577.
        m = q.mmc(10, 3, 4, t_hours=0.5)
        self.assertAlmostEqual(m['p_wait'], 0.65772, places=4)
        self.assertAlmostEqual(m['utilization'], 10 / 12)
        self.assertAlmostEqual(m['mean_wait_hours'], m['p_wait'] / (12 - 10))
        self.assertAlmostEqual(m['p_wait_gt_t'], m['p_wait'] * math.exp(-2 * 0.5))
        self.assertFalse(q.mmc(10, 3, 3)['stable'])  # a = 3.33 > 3

    def test_erlang_b_table_values(self):
        # Standard Erlang B table: 5 erlangs on 10 circuits -> 1.84%; 10 erlangs on 15 -> 3.65%; 1 erlang on 1 -> 50%.
        self.assertAlmostEqual(q.erlang_b(10, 5), 0.0184, places=4)
        self.assertAlmostEqual(q.erlang_b(15, 10), 0.0365, places=4)
        self.assertAlmostEqual(q.erlang_b(1, 1), 0.5)
        direct = (5 ** 10 / math.factorial(10)) / sum(5 ** k / math.factorial(k) for k in range(11))
        self.assertAlmostEqual(q.erlang_b(10, 5), direct, places=12)

    def test_mmck_equals_erlang_b_when_k_equals_c_and_loses_less_with_room(self):
        m = q.mmck(5, 1, 8, 8)
        self.assertAlmostEqual(m['blocking_probability'], q.erlang_b(8, 5), places=10)
        self.assertLess(q.mmck(5, 1, 8, 20)['blocking_probability'], m['blocking_probability'])

    def test_erlang_a_reduces_to_c_and_abandonment_helps(self):
        lam, mu, c = 10.0, 3.0, 4
        a0 = q.erlang_a(lam, mu, 0.0, c, 0.5)
        self.assertAlmostEqual(a0['p_wait'], erlang_c_reference(c, lam / mu), places=8)
        tiny = q.erlang_a(lam, mu, 1e-6, c, 0.5)
        self.assertAlmostEqual(tiny['p_wait_gt_t'], q.mmc(lam, mu, c, 0.5)['p_wait_gt_t'], places=3)
        ab = q.erlang_a(lam, mu, 2.0, c, 0.5)
        self.assertLess(ab['p_wait_gt_t'], a0['p_wait_gt_t'])
        self.assertGreater(ab['abandonment_probability'], 0)
        self.assertTrue(q.erlang_a(14.0, 3.0, 1.0, 4)['p_wait'] > 0.5)  # works even when a > c

    def test_erlang_a_flow_balance(self):
        m = q.erlang_a(10, 3, 1.5, 4, 0.25)
        served_rate = 10 * (1 - m['abandonment_probability'])
        self.assertAlmostEqual(served_rate, m['utilization'] * 4 * 3, places=8)


class SquareRoot(unittest.TestCase):
    def test_halfin_whitt_beta_roundtrip(self):
        for alpha in (0.05, 0.2, 0.5, 0.8):
            self.assertAlmostEqual(q.hw_wait_probability(q.hw_beta(alpha)), alpha, places=6)

    def test_sqrt_rule_close_to_exact_for_large_loads(self):
        alpha = 0.2
        beta = q.hw_beta(alpha)
        for a in (100.0, 400.0, 900.0):
            exact = next(c for c in range(int(a) + 1, int(a) * 2) if q.erlang_c(c, a) <= alpha)
            approx = q.sqrt_staffing(a, beta)
            self.assertLessEqual(abs(approx - exact), 2, (a, approx, exact))
            self.assertLessEqual(abs(approx - exact) / exact, 0.02)

    def test_execute_reports_both(self):
        out = q.execute(None, {'hourly_rates': [400.0] * 24, 'mean_service_minutes': 60, 'target_wait_probability': 0.2})
        sr = out['answer']['single_point']['square_root']
        self.assertLessEqual(abs(sr['servers'] - sr['exact_min_servers_for_p_wait_target']), 2)


class SIPP(unittest.TestCase):
    def setUp(self):
        self.rates = [2, 2, 1, 1, 1, 2, 4, 8, 14, 18, 20, 20, 18, 16, 14, 12, 12, 10, 8, 6, 5, 4, 3, 2.5]  # peak 10-11h
        self.params = {'hourly_rates': self.rates, 'mean_service_minutes': 40, 'wait_threshold_minutes': 30, 'target_wait_probability': 0.10}

    def test_staffing_follows_peaked_arrivals(self):
        st = q.execute(None, {**self.params, 'lag_adjustment': False})['answer']['staffing']
        servers = [r['servers'] for r in st['by_hour_of_week'][:24]]
        peak_arrival = max(range(24), key=lambda h: self.rates[h])
        self.assertEqual(servers.index(max(servers)), peak_arrival)
        self.assertGreater(max(servers), 3 * min(servers))
        self.assertEqual(st['total_server_hours_per_week'], sum(r['servers'] for r in st['by_hour_of_week']))
        for r in st['by_hour_of_week'][:24]:
            self.assertLessEqual(r['p_wait_gt_t'], 0.10 + 1e-9)
            self.assertGreater(r['servers'] * 1.0, r['offered_load'])
        # minimality: one fewer server misses the target
        mu = 60 / 40
        r = st['by_hour_of_week'][10]
        ok, _ = q.meets(r['arrival_rate_per_hour'], mu, r['servers'] - 1, {**q.validate(self.params)})
        self.assertFalse(ok)

    def test_lag_shifts_load_after_the_peak_and_conserves_volume(self):
        mu = 60 / 120.0  # two-hour services make the lag obvious
        rates = (self.rates * 7)
        loads = q.lagged_offered_load(rates, mu)
        self.assertGreater(loads.index(max(loads[:24])), 10)
        self.assertAlmostEqual(sum(loads) * mu, sum(rates), places=6)  # one full period conserves total arrivals
        flat = q.lagged_offered_load([6.0] * 168, mu)
        self.assertTrue(all(abs(x - 6.0 / mu) < 1e-9 for x in flat))

    def test_lagged_staffing_peaks_no_earlier_and_blocking_objective_for_beds(self):
        lag = q.execute(None, {**self.params, 'mean_service_minutes': 120})['answer']['staffing']
        nolag = q.execute(None, {**self.params, 'mean_service_minutes': 120, 'lag_adjustment': False})['answer']['staffing']
        first = lambda st: [r['servers'] for r in st['by_hour_of_week'][:24]].index(st['peak_servers'])
        self.assertGreaterEqual(first(lag), first(nolag))
        beds = q.execute(None, {'source': 'manual', 'objective': 'blocking', 'hourly_rates': [0.5] * 24, 'mean_service_minutes': 4 * 24 * 60, 'target_blocking': 0.02})
        # load = 0.5 * 96 = 48 erlangs; smallest c with B <= 2%
        c = beds['answer']['staffing']['peak_servers']
        self.assertLessEqual(q.erlang_b(c, 48.0), 0.02)
        self.assertGreater(q.erlang_b(c - 1, 48.0), 0.02)
        self.assertLessEqual(beds['answer']['single_point']['erlang_b_blocking_probability'], 0.02)

    def test_sensitivity_monotone(self):
        s = q.execute(None, self.params)['answer']['sensitivity']
        hours = [x['total_server_hours_per_week'] for x in s]
        self.assertLessEqual(hours[0], hours[1]); self.assertLessEqual(hours[1], hours[2])
        self.assertEqual([x['arrival_scale_factor'] for x in s], [0.9, 1.0, 1.1])

    def test_abandonment_needs_no_more_servers(self):
        base = q.execute(None, self.params)['answer']['staffing']['total_server_hours_per_week']
        ab = q.execute(None, {**self.params, 'abandonment_rate_per_hour': 1.0})
        self.assertLessEqual(ab['answer']['staffing']['total_server_hours_per_week'], base)
        self.assertIsNotNone(ab['answer']['single_point']['erlang_a'])

    def test_forecast_scales_weekly_volume(self):
        base = q.execute(None, self.params)['answer']['inputs']
        weekly = base['weekly_arrivals_base']
        up = q.execute(None, {**self.params, 'forecast_weekly_arrivals': weekly * 1.2})['answer']['inputs']
        self.assertAlmostEqual(up['weekly_arrivals_planned'], weekly * 1.2, places=3)


class AssumptionChecks(unittest.TestCase):
    rate = staticmethod(lambda h: 6.0 if 8 <= h < 20 else 1.5)

    def test_poisson_arrivals_pass_without_warning(self):
        out = q.execute(None, {'arrivals': make_arrivals(self.rate), 'mean_service_minutes': 30})
        chk = out['answer']['assumption_checks']['poisson_arrivals']
        self.assertEqual(chk['status'], 'consistent', chk)
        self.assertLess(abs(chk['index_of_dispersion'] - 1.0), 0.15)
        self.assertFalse([w for w in out['warnings'] if 'overdispersed' in w])

    def test_overdispersed_arrivals_warn(self):
        out = q.execute(None, {'arrivals': make_arrivals(self.rate, overdispersed=True), 'mean_service_minutes': 30})
        chk = out['answer']['assumption_checks']['poisson_arrivals']
        self.assertEqual(chk['status'], 'violated', chk)
        self.assertGreater(chk['index_of_dispersion'], 1.5)
        self.assertTrue([w for w in out['warnings'] if 'overdispersed' in w])

    def test_service_cv_warning(self):
        rng = random.Random(3)
        arr = make_arrivals(self.rate, days=14)
        expo = [rng.expovariate(1 / 30) for _ in range(400)]
        const = [30.0 + rng.random() for _ in range(400)]
        ok = q.execute(None, {'arrivals': arr, 'service_minutes': expo})['answer']['assumption_checks']['exponential_service']
        bad = q.execute(None, {'arrivals': arr, 'service_minutes': const})
        self.assertEqual(ok['status'], 'consistent', ok)
        self.assertEqual(bad['answer']['assumption_checks']['exponential_service']['status'], 'violated')
        self.assertTrue([w for w in bad['warnings'] if 'not exponential' in w])
        self.assertIsNotNone(bad['answer']['single_point']['allen_cunneen_mean_wait_minutes'])

    def test_conventions_and_user_mean_assumption(self):
        out = q.execute(None, {'hourly_rates': [3.0] * 24, 'mean_service_minutes': 20})
        for k in ('answer', 'warnings', 'assumptions', 'inference_status'): self.assertIn(k, out)
        self.assertFalse(out['optimality_claimed'])
        self.assertTrue([a for a in out['assumptions'] if 'user-provided' in a])
        self.assertTrue([a for a in out['assumptions'] if 'Poisson' in a])

    def test_validation_and_insufficient(self):
        with self.assertRaises(ValueError): q.execute(None, {'bogus': 1})
        with self.assertRaises(ValueError): q.execute(None, {'hourly_rates': [1.0] * 5, 'mean_service_minutes': 5})
        out = q.execute(None, {'arrivals': make_arrivals(self.rate, days=14)})
        self.assertEqual(out['inference_status'], 'insufficient_data')


class SemanticLayer(unittest.TestCase):
    def test_ed_visits_from_connection(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE visit (encounter_id, type_concept, event_date, end_date, start_time, end_time, organization_id)')
        rng = random.Random(5)
        rows = []
        for i, a in enumerate(make_arrivals(lambda h: 3.0 if 10 <= h < 22 else 0.5, days=28)):
            st = datetime.fromisoformat(a)
            en = st + timedelta(minutes=rng.expovariate(1 / 90))
            rows.append((f'e{i}', 'emergency_visit', st.date().isoformat(), en.date().isoformat(), st.isoformat(), en.isoformat(), 'org1'))
        rows.append(('x', 'primary_care_visit', '2024-01-02', '2024-01-02', '2024-01-02T09:00:00', '2024-01-02T09:20:00', 'org1'))
        db.executemany('INSERT INTO visit VALUES (?,?,?,?,?,?,?)', rows)
        out = q.execute(db, {'source': 'ed'})
        inp = out['answer']['inputs']
        self.assertEqual(inp['arrivals_used'], len(rows) - 1)
        self.assertAlmostEqual(inp['mean_service_minutes'], 90, delta=8)
        self.assertEqual(out['answer']['assumption_checks']['exponential_service']['status'], 'consistent')
        self.assertGreater(out['answer']['staffing']['peak_servers'], 0)
        e = q.execute(db, {'source': 'ed', 'organization_id': 'nope'})
        self.assertEqual(e['inference_status'], 'insufficient_data')


if __name__ == '__main__':
    unittest.main()
