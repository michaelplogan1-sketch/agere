"""Claims runout (IBNR): estimated ultimate paid claims for the most recent incurred months, as of a valuation date.

The buyer problem: a CFO books medical expense, or accrues shared savings, before claims finish paying. Paid-to-date
understates the last months. The textbook correction is chain-ladder: from the paid triangle (incurred month by months
until payment, as known on the valuation date), volume-weighted completion factors are computed and applied to each
month's paid-to-date. It is deterministic and reproducible: code only, no model.

Trial (claude/zen-goldberg-4e958c, eval/tiered/trial_runout_v1.json, 6 valuation dates, six most recent incurred months
together): paid-to-date 20.5% off, chain-ladder 3.5% off. Six dates only; re-test on more.

    chain_ladder(rows, valuation, months)   rows: (service_date, first_paid_date, net paid dollars) known on the valuation date
    compute(sql, params, db)                the built-in recipe's computation (recipes.RECIPES['claims_runout'])

Simplifications: a claim's final net payment is taken as paid on its first paid date; recoupments and reversals carry
their final amounts. Factors use incurred months up to HISTORY months before the valuation month, development to MAXLAG.
"""
from collections import defaultdict
from datetime import date

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
