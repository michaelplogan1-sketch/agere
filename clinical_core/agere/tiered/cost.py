"""Cost per 1,000 questions: what is measured, and what is still an assumption.

Measured, from scorecard result files (artifacts/agere-tiered-core/proof/proof-*.json): each arm's model spend per answer,
and for Agere the spend by rung. A first-time question that Agere answers with a frontier-written recipe costs about what a
one-shot frontier answer costs; a repeat of it is answered by the saved recipe at no model cost. The scorecards ask every
question for the first time, so they show Agere's dearest case.

Not measured yet, so given as scenario inputs and labelled as assumptions: the share of a health system's questions that
repeat an earlier shape (a pilot's question log measures it), the engineer weeks to map a warehouse, and the monthly hours
of recipe and definition upkeep. The model never presents these as measurements.

    python -m clinical_core.agere.tiered.cost <result.json> [...]   -> the table
"""
import json
import sys
from collections import defaultdict

FREE_RUNGS = ('bottles', 'recipes', 'near')   # saved or built-in recipes: no model call to answer


def measured(paths):
    """-> {arm: {'answers', 'usd_per_answer', 'first_time_usd' (Agere: mean over answers that reached a model), 'by_rung'}}"""
    by = defaultdict(list)
    for p in paths:
        for r in json.load(open(p))['results']:
            by[r['arm']].append(r)
    out = {}
    for arm, rs in by.items():
        usd = [float(r['usd']) for r in rs]
        rung = defaultdict(list)
        for r in rs: rung[str(r['tier'])].append(float(r['usd']))
        paid = [float(r['usd']) for r in rs if str(r['tier']) not in FREE_RUNGS and float(r['usd']) > 0]
        out[arm] = {'answers': len(rs), 'usd_per_answer': sum(usd) / len(usd), 'first_time_usd': (sum(paid) / len(paid)) if paid else 0.0,
                    'by_rung': {k: {'answers': len(v), 'usd_each': round(sum(v) / len(v), 4)} for k, v in sorted(rung.items())}}
    return out


def per_thousand(m, repeat_share):
    """Model spend per 1,000 questions when `repeat_share` of them repeat an earlier question's shape. Agere answers a repeat
    with its saved recipe (no model call); every other arm pays for every question."""
    out = {}
    for arm, x in m.items():
        if arm.startswith('stack'):
            out[arm] = 1000 * ((1 - repeat_share) * x['usd_per_answer'] + repeat_share * 0.0)
        else:
            out[arm] = 1000 * x['usd_per_answer']
    return out


def table(paths, repeat_shares=(0.0, 0.5, 0.8)):
    m = measured(paths)
    lines = ['Model spend per 1,000 questions (USD), measured per answer; repeat shares are scenarios, not measurements', '',
             'arm | answers measured | $ per answer | ' + ' | '.join(f'{int(s * 100)}% repeats' for s in repeat_shares)]
    for arm in sorted(m):
        row = [arm, str(m[arm]['answers']), f"{m[arm]['usd_per_answer']:.4f}"] + [f'{per_thousand(m, s)[arm]:.2f}' for s in repeat_shares]
        lines.append(' | '.join(row))
    lines += ['', 'Agere by rung: ' + json.dumps(m.get('stack', {}).get('by_rung', {}))]
    return '\n'.join(lines), m


if __name__ == '__main__':
    print(table(sys.argv[1:])[0])
