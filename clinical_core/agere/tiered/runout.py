"""Claims runout (IBNR): estimated ultimate paid claims for the most recent incurred months, as of a valuation date.

The buyer problem: a CFO books medical expense, or accrues shared savings, before claims finish paying. Paid-to-date
understates the last months. The textbook correction is chain-ladder: from the paid triangle (incurred month by months
until payment, as known on the valuation date), volume-weighted completion factors are computed and applied to each
month's paid-to-date. It is deterministic and reproducible: code only, no model.

Trial (claude/zen-goldberg-4e958c, eval/tiered/trial_runout_v1.json, 6 valuation dates, six most recent incurred months
together): paid-to-date 20.5% off, chain-ladder 3.5% off. Six dates only; re-test on more.

    chain_ladder(rows, valuation, months)   rows: (service_date, first_paid_date, net paid dollars) known on the valuation date
    compute(sql, params, db)                the built-in recipe's computation (recipes.RECIPES['claims_runout'])

Below the recipe: the research trial's comparison code (estimate: paid-to-date, chain-ladder, settlement forecast and
Bornhuetter-Ferguson on the claim table; trial: python -m clinical_core.agere.tiered.runout trial), kept for re-testing.

Simplifications: a claim's final net payment is taken as paid on its first paid date; recoupments and reversals carry
their final amounts. Factors use incurred months up to HISTORY months before the valuation month, development to MAXLAG.
"""
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from . import store

HISTORY = 24
MAXLAG = 18


def _d(x):
    return date.fromisoformat(str(x)[:10]) if x else None


def _month(d):
    return d.strftime('%Y-%m')


def _add(m, k):
    y, mo = int(m[:4]), int(m[5:]) + k
    while mo > 12: y, mo = y + 1, mo - 12
    while mo < 1: y, mo = y - 1, mo + 12
    return f'{y:04d}-{mo:02d}'


def _age(m, vm):
    return (int(vm[:4]) - int(m[:4])) * 12 + int(vm[5:]) - int(m[5:])


def chain_ladder(rows, valuation, months=6):
    """-> [(incurred_month, paid_to_date, completion_factor, estimated_ultimate, ibnr)] oldest first, for the `months` incurred
    months ending in the valuation month. Only payments made on or before the valuation date are used."""
    v = _d(valuation)
    vm = _month(v)
    first = _add(vm, -HISTORY)
    tri = defaultdict(lambda: defaultdict(float))
    for s, p, paid in rows:
        s, p = _d(s), _d(p)
        if s and p and p <= v and s <= v and _month(s) >= first:
            tri[_month(s)][(p.year - s.year) * 12 + p.month - s.month] += paid or 0.0

    def cum(m, k): return sum(x for lag, x in tri[m].items() if lag <= k)
    factors = []
    for k in range(MAXLAG):
        num = den = 0.0
        for m in tri:
            if _add(m, k + 1) <= vm:   # both ages observed by the valuation date
                num += cum(m, k + 1); den += cum(m, k)
        factors.append(num / den if den else 1.0)
    out = []
    for m in [_add(vm, -k) for k in range(int(months) - 1, -1, -1)]:
        k = _age(m, vm)
        dev = 1.0
        for j in range(k, MAXLAG): dev *= factors[j]
        paid = cum(m, k)
        out.append((m, round(paid, 2), round(dev, 4), round(paid * dev, 2), round(paid * dev - paid, 2)))
    return out


def compute(sql, params, db):
    """The recipe: its SQL selects the claims paid by the valuation date; code builds the triangle. -> (columns, rows)."""
    _, rows = store.run(sql, {'valuation_date': params['valuation_date']}, db)
    got = chain_ladder([(s, p, (c or 0) / 100.0) for s, p, c in rows], params['valuation_date'], params['months'])
    total = ('total', round(sum(r[1] for r in got), 2), None, round(sum(r[3] for r in got), 2), round(sum(r[4] for r in got), 2))
    return ['incurred_month', 'paid_to_date', 'completion_factor', 'estimated_ultimate_paid', 'ibnr_estimate'], [list(r) for r in got] + [list(total)]


# ---- the research trial's comparison methods (claude/zen-goldberg-4e958c, eval/tiered/trial_runout_v1.json) ----
AGES = (30, 60, 120, 10 ** 6)   # days since service, the pending-claim age buckets




def load(db=None):
    db = db or store.connect()
    rows = db.execute('SELECT service_date, first_submitted_date, first_paid_date, net_payer_paid_cents, billed_cents FROM claim WHERE service_date IS NOT NULL').fetchall()
    return [(_d(s), _d(sub), _d(p), (n or 0) / 100.0, (b or 0) / 100.0) for s, sub, p, n, b in rows]




def _lag(service, paid):
    return (paid.year - service.year) * 12 + paid.month - service.month


def _add_months(m, k):
    y, mo = int(m[:4]), int(m[5:]) + k
    while mo > 12: y, mo = y + 1, mo - 12
    while mo < 1: y, mo = y - 1, mo + 12
    return f'{y:04d}-{mo:02d}'


def truth(claims, months):
    out = defaultdict(float)
    for s, _, p, n, _ in claims:
        if p and _month(s) in months: out[_month(s)] += n
    return {m: round(out[m], 2) for m in months}


def estimate(claims, valuation, months, history=24):
    v, vm = valuation, _month(valuation)
    first = _add_months(vm, -history)
    # Paid triangle: incurred month x development lag, cumulative, as known on the valuation date.
    tri = defaultdict(lambda: defaultdict(float))
    tri_b = defaultdict(lambda: defaultdict(float))   # claims with no submission record: visible only once paid
    for s, sub, p, n, _ in claims:
        if p and p <= v and s <= v and _month(s) >= first:
            tri[_month(s)][_lag(s, p)] += n
            if not sub: tri_b[_month(s)][_lag(s, p)] += n
    def cum(m, k, t=None): return sum(x for lag, x in (t if t is not None else tri)[m].items() if lag <= k)
    def chain(t):
        f = {}
        for k in range(maxlag):
            num = den = 0.0
            for m in t:
                if _add_months(m, k + 1) <= vm: num += cum(m, k + 1, t); den += cum(m, k, t)
            f[k] = num / den if den else 1.0
        return f
    maxlag = 18
    factors_b = chain(tri_b)
    factors = {}
    for k in range(maxlag):
        num = den = 0.0
        for m in tri:
            if _add_months(m, k + 1) <= vm:   # both ages observed by the valuation date
                num += cum(m, k + 1); den += cum(m, k)
        factors[k] = num / den if den else 1.0
    out = {'paid_to_date': {}, 'chain_ladder': {}, 'settlement': {}, 'bornhuetter_ferguson': {}}
    # Bornhuetter-Ferguson (research row, 2026-11-17): ultimate = paid + (1 - 1/CDF) x prior expected ultimate; the prior is
    # the mean chain-ladder ultimate of incurred months 7-18 before the valuation month (mature by then).
    def cdf(k):
        d = 1.0
        for j in range(k, maxlag): d *= factors[j]
        return d
    # Settlement: payment ratio and payment chance of claims pending at each age, from incurred months 13-24 back (settled by now).
    old_lo, old_hi = _add_months(vm, -history), _add_months(vm, -13)
    ratio = {a: [0.0, 0.0] for a in AGES}
    sub_lag = defaultdict(float); billed_old = 0.0
    for s, sub, p, n, b in claims:
        if not (old_lo <= _month(s) <= old_hi) or not sub: continue
        billed_old += b
        sub_lag[(sub - s).days] += b
        for i, a in enumerate(AGES):   # pending at the start of age bucket a: submitted by then and not yet paid
            at = s + timedelta(days=(0 if i == 0 else AGES[i - 1] + 1))
            if sub <= at and (p is None or p > at):
                ratio[a][0] += n if p else 0.0; ratio[a][1] += b
    r = {a: (x / y if y else 0.0) for a, (x, y) in ratio.items()}
    r_all = sum(n for s, sub, p, n, b in claims if old_lo <= _month(s) <= old_hi and p and sub) / max(1.0, billed_old)
    def submitted_share(age):   # share of billed dollars submitted within `age` days of service, historically
        return sum(x for d, x in sub_lag.items() if d <= age) / max(1.0, sum(sub_lag.values()))
    pending = defaultdict(float); billed_sub = defaultdict(float); ibnr = defaultdict(float)
    for s, sub, p, n, b in claims:
        if s > v or _month(s) not in months: continue
        if sub and sub <= v:
            billed_sub[_month(s)] += b
            if p is None or p > v:
                age = (v - s).days
                pending[_month(s)] += b * r[next(a for a in AGES if age <= a)]
    for m in months:
        last = (_add_months(m, 1) + '-01')
        end = min(v, date.fromisoformat(last) - timedelta(days=1))
        age = (v - end).days   # the youngest claims of the month
        share = submitted_share(age) or 1.0
        ibnr[m] = billed_sub[m] * (1 / share - 1) * r_all
        k = (int(vm[:4]) - int(m[:4])) * 12 + int(vm[5:]) - int(m[5:])
        paid = cum(m, k)
        dev = 1.0
        for j in range(k, maxlag): dev *= factors[j]
        mature = [_add_months(vm, -j) for j in range(7, 19)]
        prior = sum(cum(x, (int(vm[:4]) - int(x[:4])) * 12 + int(vm[5:]) - int(x[5:])) * cdf((int(vm[:4]) - int(x[:4])) * 12 + int(vm[5:]) - int(x[5:])) for x in mature) / len(mature)
        out['bornhuetter_ferguson'][m] = round(paid + (1 - 1 / dev) * prior, 2) if dev else round(paid, 2)
        out['paid_to_date'][m] = round(paid, 2)
        out['chain_ladder'][m] = round(paid * dev, 2)
        paid_b = cum(m, k, tri_b); dev_b = 1.0
        for j in range(k, maxlag): dev_b *= factors_b[j]
        # submitted claims: paid + expected payment of the pending inventory + not-yet-submitted; the rest: chain-ladder
        out['settlement'][m] = round((paid - paid_b) + pending[m] + ibnr[m] + paid_b * dev_b, 2)
    return out


def trial(path='eval/tiered/trial_runout_v1.json', out='artifacts/agere-tiered-core/trials/runout-journal.jsonl'):
    spec = json.loads(Path(path).read_text())
    claims = load()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    lines, totals = [], defaultdict(list)
    for val in spec['valuations']:
        v = date.fromisoformat(val)
        months = [_add_months(_month(v), -k) for k in range(spec['months'] - 1, -1, -1)]
        est, real = estimate(claims, v, months), truth(claims, months)
        whole = sum(real.values())
        rec = {'valuation': val, 'months': months, 'truth': real, **est,
               'error_pct': {k: round(100 * (sum(e.values()) - whole) / whole, 2) for k, e in est.items()}}
        with open(out, 'a') as f: f.write(json.dumps(rec) + '\n')
        for k, e in rec['error_pct'].items(): totals[k].append(abs(e))
        lines.append(f"{val}: truth ${whole:,.0f} for {months[0]}..{months[-1]}; error " + ', '.join(f'{k} {e:+.2f}%' for k, e in rec['error_pct'].items()))
    lines.append('mean absolute error of the six-month total: ' + ', '.join(f'{k} {sum(v) / len(v):.2f}%' for k, v in totals.items()))
    return '\n'.join(lines)


if __name__ == '__main__':
    print(trial() if sys.argv[1:2] == ['trial'] else __doc__)
