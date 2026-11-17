"""Triage trials, 2026-11-17, research rows (code only, no model). Committed before its first run; seed 17 throughout.

1 stop_loss     Specific stop-loss at attachment A per member per year: predict next year's expected excess claims from this
                year. Candidate: empirical member-cost distribution, expected excess + CVaR(95%) of the aggregate excess by
                bootstrap. Simple alternative: normal approximation of member cost (mean, sd). Truth: next year's actual excess.
2 comparison    Comparison-group guard, placebo: no program exists, so the true effect is 0. "Enrol" a random half of the
                top-5%-cost members of year t; plain pre-post (t -> t+1) on the enrolled vs difference against the eligible
                but not enrolled half. 200 random enrolments per year pair.
3 staggered     Staggered adoption: practices assigned to three adoption waves (or never) at random; outcome = practice
                PMPM by year with an injected effect that grows with time since adoption (-$20, -$40, -$60). Two-way fixed
                effects vs Callaway-Sant'Anna (cohort-time ATT against not-yet/never treated, averaged over post cells). 200
                random assignments; truth = the injected average effect over the same post cells.
4 settlement    MSSP-style settlement for the synthetic contract (benchmark, minimum savings rate, sharing rate, quality
                gate, cap 10% of benchmark): point estimate vs member-bootstrap Monte Carlo (probability the minimum savings
                rate is met, 90% interval of shared savings). Three contract years only: descriptive, not a test.

    PYTHONPATH=. python eval/tiered/trial_finance_methods.py
"""
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

from clinical_core.agere.tiered import store

R = random.Random(17)
OUT = Path('artifacts/agere-tiered-core/trials/finance-methods.jsonl')


def save(rec):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, 'a') as f: f.write(json.dumps(rec) + '\n')
    print(json.dumps(rec))


def member_costs(db):
    cost = defaultdict(float)
    for pid, sd, n in db.execute('SELECT patient_id, service_date, net_payer_paid_cents FROM claim WHERE service_date IS NOT NULL'):
        cost[(pid, sd[:4])] += (n or 0) / 100
    members = defaultdict(set)
    for pid, m in db.execute('SELECT patient_id, month FROM member_month'): members[m[:4]].add(pid)
    return {y: {p: cost.get((p, y), 0.0) for p in ps} for y, ps in members.items()}


def stop_loss(costs):
    import math
    for attach in (50000, 100000):
        for y in ('2027', '2028'):
            nxt = str(int(y) + 1)
            xs = list(costs[y].values()); n_next = len(costs[nxt])
            truth = sum(max(0.0, c - attach) for c in costs[nxt].values())
            emp = sum(max(0.0, c - attach) for c in xs) / len(xs) * n_next
            mu, sd = statistics.mean(xs), statistics.pstdev(xs)
            z = (attach - mu) / sd   # E[(X - A)+] for X ~ Normal(mu, sd)
            phi = math.exp(-z * z / 2) / math.sqrt(2 * math.pi); Phi = 0.5 * (1 + math.erf(z / math.sqrt(2)))
            norm = ((mu - attach) * (1 - Phi) + sd * phi) * n_next
            draws = sorted(sum(max(0.0, R.choice(xs) - attach) for _ in range(n_next)) for _ in range(500))
            cvar = statistics.mean(draws[int(0.95 * len(draws)):])
            save({'trial': 'stop_loss', 'attachment': attach, 'fit_year': y, 'test_year': nxt, 'truth_excess': round(truth),
                  'empirical_expected': round(emp), 'normal_expected': round(norm), 'empirical_cvar95': round(cvar),
                  'empirical_error_pct': round(100 * (emp - truth) / truth, 1) if truth else None,
                  'normal_error_pct': round(100 * (norm - truth) / truth, 1) if truth else None,
                  'truth_within_cvar95': truth <= cvar})


def comparison(costs):
    for y in ('2027', '2028'):
        nxt = str(int(y) + 1)
        both = [p for p in costs[y] if p in costs[nxt]]
        top = sorted(both, key=lambda p: -costs[y][p])[:max(20, len(both) // 20)]
        prepost, did = [], []
        for _ in range(200):
            R.shuffle(top); a, b = top[:len(top) // 2], top[len(top) // 2:]
            ch = lambda g: statistics.mean(costs[nxt][p] - costs[y][p] for p in g)
            prepost.append(ch(a)); did.append(ch(a) - ch(b))
        save({'trial': 'comparison_group', 'years': f'{y}->{nxt}', 'enrolled_per_draw': len(top) // 2, 'true_effect': 0,
              'prepost_mean_change': round(statistics.mean(prepost)), 'prepost_share_claiming_savings': sum(x < 0 for x in prepost) / 200,
              'comparison_mean_effect': round(statistics.mean(did)), 'comparison_sd': round(statistics.pstdev(did)),
              'comparison_share_beyond_2sd': sum(abs(x) > 2 * statistics.pstdev(did) for x in did) / 200})


def staggered(db):
    import numpy as np
    mm = defaultdict(int); paid = defaultdict(float); pm = {}
    for pid, m, prac in db.execute('SELECT patient_id, month, attribution_practice_id FROM member_month WHERE attribution_practice_id IS NOT NULL'):
        mm[(prac, m[:4])] += 1; pm[(pid, m)] = prac
    for pid, sd, n in db.execute('SELECT patient_id, service_date, net_payer_paid_cents FROM claim WHERE service_date IS NOT NULL'):
        prac = pm.get((pid, sd[:7]))
        if prac: paid[(prac, sd[:4])] += (n or 0) / 100
    years = ['2026', '2027', '2028', '2029']
    pracs = sorted({p for p, y in mm if all(mm[(p, yy)] for yy in years)})
    base = {(p, y): paid[(p, y)] / mm[(p, y)] for p in pracs for y in years}
    effect = {0: -20.0, 1: -40.0, 2: -60.0}
    tw_err, cs_err = [], []
    for _ in range(200):
        wave = {p: R.choice(['2027', '2028', '2029', None]) for p in pracs}
        y_obs, cells = {}, []
        for p in pracs:
            for y in years:
                e = int(y) - int(wave[p]) if wave[p] and y >= wave[p] else None
                y_obs[(p, y)] = base[(p, y)] + (effect[e] if e is not None else 0.0)
                if e is not None: cells.append(effect[e])
        truth = statistics.mean(cells)
        # two-way fixed effects: y = a_p + b_t + d * treated
        rows = [(p, y) for p in pracs for y in years]
        X = np.zeros((len(rows), len(pracs) + len(years) - 1 + 1)); Y = np.array([y_obs[r] for r in rows])
        for i, (p, y) in enumerate(rows):
            X[i, pracs.index(p)] = 1
            if years.index(y) > 0: X[i, len(pracs) + years.index(y) - 1] = 1
            X[i, -1] = 1.0 if wave[p] and y >= wave[p] else 0.0
        d = np.linalg.lstsq(X, Y, rcond=None)[0][-1]
        # Callaway-Sant'Anna: ATT(g, t) = [mean change g from g-1 to t] - [same for not-yet-treated at t (incl. never)]
        atts = []
        for g in ('2027', '2028', '2029'):
            grp = [p for p in pracs if wave[p] == g]; pre = str(int(g) - 1)
            for t in years:
                if t < g: continue
                ctl = [p for p in pracs if wave[p] is None or wave[p] > t]
                if not grp or not ctl: continue
                ch = lambda ps: statistics.mean(y_obs[(p, t)] - y_obs[(p, pre)] for p in ps)
                atts += [ch(grp) - ch(ctl)] * len(grp)
        cs = statistics.mean(atts)
        tw_err.append(d - truth); cs_err.append(cs - truth)
    save({'trial': 'staggered_did', 'practices': len(pracs), 'draws': 200, 'injected_effect_by_years_since': effect,
          'twfe_bias': round(statistics.mean(tw_err), 2), 'twfe_rmse': round((sum(e * e for e in tw_err) / 200) ** 0.5, 2),
          'cs_bias': round(statistics.mean(cs_err), 2), 'cs_rmse': round((sum(e * e for e in cs_err) / 200) ** 0.5, 2)})


def settlement(db, costs):
    from clinical_core.agere.tiered import aco
    for cid, payer, y, bench, msr, share, quality, _ in aco.contracts():
        mem = defaultdict(int)
        for pid, in db.execute('SELECT patient_id FROM member_month WHERE payer_id = ? AND substr(month, 1, 4) = ?', (payer, y)): mem[pid] += 1
        if not mem: continue
        ps = list(mem)
        def settle(sample):
            months = sum(mem[p] for p in sample); spend = sum(costs[y].get(p, 0.0) for p in sample)
            savings = bench * months - spend
            earned = savings >= msr * bench * months and quality
            return min(share * savings, 0.10 * bench * months) if earned else 0.0, spend / months
        point, pmpm = settle(ps)
        draws = sorted(settle([R.choice(ps) for _ in ps])[0] for _ in range(1000))
        save({'trial': 'settlement', 'contract': cid, 'year': y, 'members': len(ps), 'benchmark_pmpm': bench, 'actual_pmpm': round(pmpm, 2),
              'point_shared_savings': round(point), 'p_msr_met': sum(d > 0 for d in draws) / 1000,
              'shared_savings_90pct': [round(draws[50]), round(draws[949])]})


if __name__ == '__main__':
    db = store.connect()
    costs = member_costs(db)
    stop_loss(costs); comparison(costs); staggered(db); settlement(db, costs)
