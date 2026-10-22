"""Capacity-aware outreach allocation: a mixed-integer program assigning patients to (channel, week) outreach slots.

Candidates and their value of one contact come from outreach.candidates (value_if_engaged: explained reason components with
the outreach.py planning-assumption dollar values and overlap discount); the top max_candidates by expected value enter the
program. Response probability of patient i on channel c:
    p(i, c) = rate(c, segment(i)) x engagement_i / mean engagement (clipped to [0.02, 0.98])
where rate is the posterior-mean engagement rate learned by the outreach bandit (outreach_learning.response_rates over the
outreach_attempts log) and engagement_i is outreach.py's patient reachability prior; an arm with no log falls back to the
patient's outreach.py engagement prior alone. A contact in week w (0-based) is worth week_decay^w of a contact now.

    maximise   sum_{i,c,w} value_i x p(i,c) x decay^w x X_icw
    subject to sum_{c,w} X_icw <= 1                          (one contact per patient over the horizon)
               sum_i minutes_c X_icw <= 60 hours_c           (staff hours per channel per week)
               sum_i X_icw <= slots_c                        (slot count per channel per week, when given)
               sum_{i in practice k} X_icw >= floor          (optional per-practice floor, capped at the candidates there)
               X binary
solved with scipy.optimize.milp (HiGHS branch and bound; Google OR-Tools CP-SAT is not installed in this environment, and the
problem is a linear MILP HiGHS solves to optimality). Reported: the assignment, objective, solver status and MIP gap, the
greedy-by-value baseline on the same constraints, and the shadow price of one more staff hour per week on each channel (re-solve).
All dollar values are outreach.py planning assumptions, not measured effects; outcome rates are learned from a synthetic log.
"""
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

VERSION = 'outreach-allocation/1'
OP = 'outreach_allocation'
SEGMENTS = ('none', 'risk_tier')
DEFAULT_CHANNELS = [{'channel': 'phone', 'hours_per_week': 20.0, 'minutes_per_contact': 20, 'slots_per_week': 0},
                    {'channel': 'text', 'hours_per_week': 4.0, 'minutes_per_contact': 4, 'slots_per_week': 0},
                    {'channel': 'mail', 'hours_per_week': 2.0, 'minutes_per_contact': 3, 'slots_per_week': 40}]
P_BOUNDS = (0.02, 0.98)

PRODUCT = {'population': 'optional', 'view': 'worklist',
           'use': 'OPTIMISE OUTREACH UNDER CAPACITY: allocate or assign patients to phone / text / mail slots week by week within staff '
                  'hours and slot limits (mixed-integer program), using learned channel response rates; value vs greedy, value of one '
                  'more staff hour per channel.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'channels': [{'channel': 'phone', 'hours_per_week': 20, 'minutes_per_contact': 20, 'slots_per_week': 0}],
                           'weeks': 4, 'week_decay': 0.95, 'max_candidates': 800, 'min_per_practice': 0, 'segment_by': 'none|risk_tier'},
            'meaning': 'MILP (scipy HiGHS) assignment of the top outreach candidates (outreach.py value of one contact) to at most one '
                       '(channel, week) slot under per-channel weekly staff hours (minutes per contact) and slot counts, optional practice '
                       'floors; objective value x channel response probability (outreach bandit posterior from the outreach_attempts log, '
                       'scaled by the patient reachability prior) x week decay. Returns the assignment, optimality status and gap, greedy '
                       'baseline, shadow price of +1 staff hour per channel per week; dollar values are planning assumptions.'}
NATIVE = ('value',)


def schema(obj, string, array):
    chan = obj({'channel': string(None, 'Channel name as logged (phone, text, mail) or another channel'),
                'hours_per_week': {'type': 'number', 'minimum': 0, 'maximum': 2000},
                'minutes_per_contact': {'type': 'number', 'minimum': 0.5, 'maximum': 480},
                'slots_per_week': {'type': 'integer', 'minimum': 0, 'maximum': 100000, 'description': '0 = no slot-count limit'}})
    return obj({'channels': array(chan, 'Channels with weekly capacity; empty = defaults (phone 20 h, text 4 h, mail 40 slots)'),
                'weeks': {'type': 'integer', 'minimum': 1, 'maximum': 26}, 'week_decay': {'type': 'number', 'minimum': 0.5, 'maximum': 1},
                'max_candidates': {'type': 'integer', 'minimum': 5, 'maximum': 2000},
                'min_per_practice': {'type': 'integer', 'minimum': 0, 'maximum': 1000}, 'segment_by': string(SEGMENTS)})


def validate(p):
    allowed = {'channels', 'weeks', 'week_decay', 'max_candidates', 'min_per_practice', 'segment_by'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('outreach_allocation parameters: ' + ', '.join(sorted(allowed)))
    chans = []
    for c in p.get('channels') or DEFAULT_CHANNELS:
        if not isinstance(c, dict) or not c.get('channel') or set(c) - {'channel', 'hours_per_week', 'minutes_per_contact', 'slots_per_week'}:
            raise ValueError('channels: [{channel, hours_per_week, minutes_per_contact, slots_per_week}]')
        d = next((x for x in DEFAULT_CHANNELS if x['channel'] == c['channel']), {'hours_per_week': 0.0, 'minutes_per_contact': 15, 'slots_per_week': 0})
        row = {'channel': str(c['channel']).lower(), 'hours_per_week': float(c.get('hours_per_week', d['hours_per_week'])),
               'minutes_per_contact': float(c.get('minutes_per_contact', d['minutes_per_contact'])), 'slots_per_week': int(c.get('slots_per_week', d['slots_per_week']))}
        if not 0 <= row['hours_per_week'] <= 2000 or not 0.5 <= row['minutes_per_contact'] <= 480 or not 0 <= row['slots_per_week'] <= 100000:
            raise ValueError('channel capacity out of range')
        if row['hours_per_week'] == 0 and row['slots_per_week'] == 0: raise ValueError(f"channel {row['channel']} has no capacity")
        chans.append(row)
    if len({c['channel'] for c in chans}) != len(chans): raise ValueError('duplicate channels')
    out = {'channels': chans, 'weeks': p.get('weeks', 4), 'week_decay': p.get('week_decay', 0.95), 'max_candidates': p.get('max_candidates', 800),
           'min_per_practice': p.get('min_per_practice', 0), 'segment_by': p.get('segment_by', 'none')}
    if type(out['weeks']) is not int or not 1 <= out['weeks'] <= 26: raise ValueError('weeks 1..26')
    if type(out['week_decay']) not in (int, float) or not 0.5 <= out['week_decay'] <= 1: raise ValueError('week_decay 0.5..1')
    if type(out['max_candidates']) is not int or not 5 <= out['max_candidates'] <= 2000: raise ValueError('max_candidates 5..2000')
    if type(out['min_per_practice']) is not int or not 0 <= out['min_per_practice'] <= 1000: raise ValueError('min_per_practice 0..1000')
    if out['segment_by'] not in SEGMENTS: raise ValueError(f'segment_by: {SEGMENTS}')
    return out


def _segment(row, segment_by):
    if segment_by == 'risk_tier':
        from .outreach_learning import risk_tier
        return risk_tier(row.get('risk_score'))
    return 'all'


def probabilities(rows, channels, rates, segment_by):
    """p[i][c] and its basis per channel."""
    mean_e = sum(r['engagement_probability'] for r in rows) / len(rows) if rows else 1.0
    lo, hi = P_BOUNDS
    out, basis = [], {}
    for r in rows:
        seg = _segment(r, segment_by)
        ps = []
        for c in channels:
            rate = rates.get((c['channel'], seg), rates.get((c['channel'], 'all')))
            if rate is None:
                ps.append(min(hi, max(lo, r['engagement_probability']))); basis.setdefault(c['channel'], 'outreach.py engagement prior (no logged outcomes for this channel)')
            else:
                ps.append(min(hi, max(lo, rate * r['engagement_probability'] / mean_e))); basis.setdefault(c['channel'], 'bandit posterior engagement rate x patient reachability')
        out.append(ps)
    return out, basis


def solve(rows, probs, channels, weeks, decay, floor=0, extra_hours=None):
    """MILP via scipy HiGHS -> {'status', 'objective', 'assignment': [(i, c, w)], 'mip_gap'}."""
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    n, C, W = len(rows), len(channels), weeks
    if not n: return {'status': 'optimal', 'objective': 0.0, 'assignment': [], 'mip_gap': 0.0}
    idx = lambda i, c, w: (i * C + c) * W + w  # noqa: E731
    nv = n * C * W
    gain = np.zeros(nv)
    for i, r in enumerate(rows):
        for c in range(C):
            for w in range(W): gain[idx(i, c, w)] = r['value_if_engaged'] * probs[i][c] * decay ** w
    A, lo, hi = [], [], []
    def row(cols, coef, l, h):
        a = np.zeros(nv); a[cols] = coef; A.append(a); lo.append(l); hi.append(h)
    for i in range(n): row([idx(i, c, w) for c in range(C) for w in range(W)], 1.0, -np.inf, 1.0)
    for c, ch in enumerate(channels):
        hours = ch['hours_per_week'] + ((extra_hours or {}).get(ch['channel'], 0.0))
        for w in range(W):
            cols = [idx(i, c, w) for i in range(n)]
            if hours > 0: row(cols, ch['minutes_per_contact'], -np.inf, 60.0 * hours)
            if ch['slots_per_week']: row(cols, 1.0, -np.inf, float(ch['slots_per_week']))
    if floor:
        by = defaultdict(list)
        for i, r in enumerate(rows): by[r['practice']].append(i)
        for members in by.values():
            row([idx(i, c, w) for i in members for c in range(C) for w in range(W)], 1.0, float(min(floor, len(members))), np.inf)
    res = milp(-gain, integrality=np.ones(nv), bounds=Bounds(0, 1), constraints=LinearConstraint(np.array(A), np.array(lo), np.array(hi)),
               options={'time_limit': 20, 'mip_rel_gap': 1e-6})
    if res.x is None: return {'status': 'infeasible' if res.status == 2 else 'no_solution', 'objective': None, 'assignment': [], 'mip_gap': None}
    chosen = [(int(k // (C * W)), int((k // W) % C), int(k % W)) for k in np.flatnonzero(res.x > 0.5)]
    return {'status': 'optimal' if res.status == 0 else 'time_limit_feasible', 'objective': float(gain[np.flatnonzero(res.x > 0.5)].sum()),
            'assignment': chosen, 'mip_gap': float(getattr(res, 'mip_gap', 0.0) or 0.0)}


def greedy(rows, probs, channels, weeks, decay):
    """Baseline: take (patient, channel, week) options in descending value while the patient and capacity are free."""
    C, W = len(channels), weeks
    opts = sorted(((rows[i]['value_if_engaged'] * probs[i][c] * decay ** w, i, c, w) for i in range(len(rows)) for c in range(C) for w in range(W)),
                  key=lambda t: (-t[0], t[1], t[2], t[3]))
    used_min = defaultdict(float); used_n = defaultdict(int); taken, total, out = set(), 0.0, []
    for v, i, c, w in opts:
        ch = channels[c]
        if i in taken: continue
        if ch['hours_per_week'] > 0 and used_min[(c, w)] + ch['minutes_per_contact'] > 60 * ch['hours_per_week'] + 1e-9: continue
        if ch['slots_per_week'] and used_n[(c, w)] + 1 > ch['slots_per_week']: continue
        taken.add(i); used_min[(c, w)] += ch['minutes_per_contact']; used_n[(c, w)] += 1; total += v; out.append((i, c, w))
    return {'objective': total, 'assignment': out}


def run(db, patient_ids, p, as_of, candidates=None):
    from . import outreach
    from .outreach_learning import response_rates
    keep = set(patient_ids) if patient_ids is not None else None
    rows, context = (candidates or outreach.candidates)(db, as_of, keep, 0.5)
    pool = len(rows)
    rows = sorted(rows, key=lambda r: (-r['expected_value'], r['patient_id']))[:p['max_candidates']]
    rates = response_rates(db, 'risk_tier' if p['segment_by'] == 'risk_tier' else 'none', None, as_of)
    probs, basis = probabilities(rows, p['channels'], rates, p['segment_by'])
    chans, W, decay = p['channels'], p['weeks'], p['week_decay']
    opt = solve(rows, probs, chans, W, decay, p['min_per_practice'])
    if opt['objective'] is None and p['min_per_practice']:  # floors infeasible under capacity: heal by dropping them, say so
        opt = solve(rows, probs, chans, W, decay, 0); opt['floor_dropped'] = True
    base = greedy(rows, probs, chans, W, decay)
    shadow = []
    for ch in chans:
        if ch['hours_per_week'] <= 0: continue
        more = solve(rows, probs, chans, W, decay, p['min_per_practice'] if not opt.get('floor_dropped') else 0, {ch['channel']: 1.0})
        shadow.append({'channel': ch['channel'], 'usd_per_extra_hour_per_week': round((more['objective'] or 0) - (opt['objective'] or 0), 2),
                       'extra_contacts': len(more['assignment']) - len(opt['assignment'])})
    plan = []
    for i, c, w in sorted(opt['assignment'], key=lambda t: (t[2], t[1], -rows[t[0]]['value_if_engaged'])):
        r = rows[i]
        plan.append({'patient_id': r['patient_id'], 'channel': chans[c]['channel'], 'week': w + 1, 'value_if_engaged': r['value_if_engaged'],
                     'response_probability': round(float(probs[i][c]), 4), 'expected_value': round(float(r['value_if_engaged'] * probs[i][c] * decay ** w), 2),
                     'practice': r['practice'], 'top_reasons': [x['code'] for x in r['reasons'][:3]], 'suggested_action': r.get('suggested_action'),
                     'minutes': chans[c]['minutes_per_contact']})
    load = defaultdict(lambda: {'contacts': 0, 'minutes': 0.0})
    for x in plan:
        k = (x['channel'], x['week']); load[k]['contacts'] += 1; load[k]['minutes'] += x['minutes']
    utilization = [{'channel': ch, 'week': wk, **v, 'hours_capacity': next(c['hours_per_week'] for c in chans if c['channel'] == ch)}
                   for (ch, wk), v in sorted(load.items())]
    lift = (opt['objective'] or 0) - base['objective']
    return {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'candidates_in_pool': pool, 'candidates_in_program': len(rows),
            'assigned': len(plan), 'objective_usd': round(opt['objective'] or 0, 2), 'solver': {'name': 'scipy.optimize.milp (HiGHS)',
            'status': opt['status'], 'mip_gap': opt['mip_gap'], 'floor_dropped': bool(opt.get('floor_dropped')),
            'note': 'OR-Tools CP-SAT not installed; linear MILP solved by HiGHS'},
            'greedy_baseline': {'objective_usd': round(base['objective'], 2), 'assigned': len(base['assignment']), 'milp_lift_usd': round(lift, 2)},
            'shadow_prices': shadow, 'channel_rates': {f'{k[0]}|{k[1]}': round(v, 4) for k, v in sorted(rates.items())},
            'probability_basis': basis, 'assignment': plan, 'utilization': utilization,
            'patient_ids': sorted(x['patient_id'] for x in plan), 'context': {k: v for k, v in context.items() if k != 'risk_model_calibration_by_decile'},
            'method': METHOD.format(n=len(rows), W=W, d=decay)}


METHOD = ('Top {n} outreach candidates (outreach.py value of one contact) assigned to at most one (channel, week) slot over {W} weeks by a '
          'binary MILP (scipy HiGHS) maximising value x channel response probability x {d}^week under per-channel weekly staff minutes '
          'and slot limits; response probability = outreach bandit posterior engagement rate (outreach_attempts log) x patient '
          'reachability / mean reachability. Greedy baseline takes options in value order; shadow price = re-solved objective with one '
          'more staff hour per week on that channel.')
ASSUMPTIONS = ['Dollar values of a contact are outreach.py planning assumptions (avoidable utilization and care-gap values), not measured effects.',
               'Channel engagement rates are learned from a synthetic outreach_attempts log; channels without logged outcomes use the outreach.py reachability prior.',
               'Capacity defaults (phone 20 h/week at 20 min, text 4 h/week at 4 min, mail 40 slots/week) apply unless the question states capacity.',
               'One contact per patient over the horizon; later weeks are discounted by week_decay.']


def execute(path, patient_ids, params, as_of):
    from . import semantic_layer
    p = validate(params)
    target = semantic_layer.ensure(path) if (Path(path)/'run.json').exists() else Path(path)/'semantic.sqlite3'
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        return run(db, patient_ids, p, str(as_of)[:10])
    finally:
        db.close()


def headline(r):
    if not r['assigned']: return f"No outreach candidates could be assigned ({r['candidates_in_pool']} candidates in scope)"
    by = defaultdict(int)
    for x in r['assignment']: by[x['channel']] += 1
    sp = '; '.join(f"+1 {s['channel']} hour/week adds ${s['usd_per_extra_hour_per_week']:,.0f}" for s in r['shadow_prices'])
    g = r['greedy_baseline']
    return (f"{r['assigned']} of {r['candidates_in_program']} top candidates assigned over {r['parameters']['weeks']} weeks ("
            + ', '.join(f'{k} {v}' for k, v in sorted(by.items(), key=lambda kv: -kv[1]))
            + f"): expected value ${r['objective_usd']:,.0f} (MILP {r['solver']['status']}, gap {r['solver']['mip_gap']:.2%}) vs greedy "
              f"${g['objective_usd']:,.0f} (+${g['milp_lift_usd']:,.0f})" + (f". {sp}" if sp else ''))


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    ids = report['patient_ids']
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['assignment'], 'count': len(ids),
            'patient_ids': ids, 'value': headline(report) + '.', 'method': report['method'],
            'definitions': [{'term': 'expected value', 'basis': 'outreach.py value if engaged x channel response probability x week decay'}],
            'limitations': ASSUMPTIONS + (['Practice floors were infeasible under the stated capacity and were dropped.'] if report['solver']['floor_dropped'] else []),
            'membership_role': 'selected', 'overall': {'outreach_allocation': {k: report[k] for k in ('solver', 'greedy_baseline', 'shadow_prices', 'utilization', 'channel_rates')}}}


# ---- free local-compiler route ----------------------------------------------------------------------------------------
OPTIMISE = r'\ballocat\w*\b|\bassign\w*\b|\boptimi[sz]\w*\b|\bmilp\b|\binteger program\w*\b|\bcp[- ]sat\b|\bschedul\w* (?:our |the )?outreach\b|\bsplit\b|\bdistribut\w*\b'
SPREAD = (r'\bchannels?\b|\bweeks?\b|\bweekly\b|\bphone\b|\btext\w*\b|\bmail\w*\b|\bstaff (?:hours|capacity|time)\b|\bnurse hours\b|'
          r'\bcapacity\b|\bslots?\b|\bcare managers?\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(r'\boutreach\b|\bcontacts?\b|\bcalls?\b|\bpatients?\b|\bmembers?\b', low): return None
    if not re.search(OPTIMISE, low) or not re.search(SPREAD, low): return None
    if re.match(r'(?:what (?:is|are)|explain|define)\b', low): return None
    if re.search(r'\bno[- ]?shows?\b|\boverbook\w*\b|\bappointments?\b|\bwho benefits?\b|\bpersuadables?\b|\bnurses? (?:do we need|per hour|each hour)\b', low): return None
    params, chans = {}, []
    for name, rx in (('phone', r'phone|call'), ('text', r'text|sms'), ('mail', r'mail|letter')):
        m = (re.search(rf'(\d+(?:\.\d+)?)\s*(?:staff |nurse )?hours?(?: (?:a|per) week)?(?: (?:of|for) (?:{rx})\w*)', low)
             or re.search(rf'(?:{rx})\w*[^.,;]{{0,20}}?(\d+(?:\.\d+)?)\s*hours?', low))
        s = re.search(rf'(\d{{1,5}})\s*(?:{rx})\w* slots?', low) or re.search(rf'(?:{rx})\w*[^.,;]{{0,15}}?(\d{{1,5}})\s*slots?', low)
        if m or s:
            c = {'channel': name}
            if m: c['hours_per_week'] = float(m.group(1))
            if s: c['slots_per_week'] = int(s.group(1))
            if m and not s: c['slots_per_week'] = 0
            chans.append(c)
    if chans: params['channels'] = chans
    w = re.search(r'\b(?:next|over|across|for)\s+(\d{1,2})\s+weeks?\b', low)
    if w and 1 <= int(w.group(1)) <= 26: params['weeks'] = int(w.group(1))
    elif re.search(r'\bnext month\b', low): params['weeks'] = 4
    elif re.search(r'\bthis week\b|\bnext week\b', low): params['weeks'] = 1
    f = re.search(r'\bat least (\d{1,3}) (?:patients? )?(?:per|from each|in each) (?:practice|clinic)\b', low)
    if f: params['min_per_practice'] = int(f.group(1))
    if re.search(r'\brisk tiers?\b|\bby risk\b', low): params['segment_by'] = 'risk_tier'
    return plan(OP, params, 'Capacity-aware outreach allocation across channels and weeks (MILP, learned response rates)', question,
                [f"channels {','.join(c['channel'] for c in chans) or 'default'}"])


TEMPLATES = ['Assign our outreach candidates to phone, text or mail given {n} staff hours a week',
             'optimise outreach across channels over the next {days} days with our care manager capacity',
             'Build the best outreach schedule across channels for the next 6 weeks', 'allocate this month\'s outreach slots by channel for {conds}',
             'How should we distribute calls and texts across weeks to get the most value with limited nurse hours?',
             'Solve an integer program for which members get a phone call versus a letter each week',
             'What is one extra hour of phone outreach capacity worth per week?', 'optimize our weekly outreach plan with phone and mail slots']
