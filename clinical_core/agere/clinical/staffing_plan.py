"""Forecast-to-capacity staffing decisions: demand forecast -> hour-of-week requirement -> minimum-hours shift roster.

Pipeline (each stage reuses the existing engine):
  1. demand.execute forecasts weekly volume for the next `weeks` complete weeks (point and 80% interval; the measure
     matches the queue: ED visits, clinic visit types, kept appointments or inpatient admissions for beds).
  2. queueing.engine_execute reads the received arrivals and service times once and gives the historical hour-of-week
     arrival profile and service rate; each forecast week rescales that profile so its weekly total equals the forecast
     (within-week shape = history).
  3. queueing.sipp gives the minimum staff on duty for each of the 168 hour blocks (Erlang C wait target, or Erlang B
     blocking for beds; SIPP with service-time lag) - the requirement.
  4. A shift-scheduling integer program (scipy.optimize.milp / HiGHS) chooses how many shifts of each allowed length start
     at each allowed hour (shifts wrap Sunday -> Monday) so every hour block is covered, minimising paid hours (ties: fewer
     shifts). Optional floor of staff on duty in every block.
Output: the posted shift plan per day for each forecast week, paid hours, FTE, coverage slack, the plan staffed to the
80% upper forecast (the "staff to the upper interval" option), and the gap against stated current staffing.
Erlang/SIPP assumptions are the queueing engine's (Poisson arrivals, exponential service, checked there); dollar cost only
from a stated hourly rate. Synthetic data only; a planning roster, not a validated clinical staffing standard.
"""
import math
import re
from datetime import date, timedelta

VERSION = 'staffing-plan/1'
OP = 'staffing_plan'
SOURCES = ('ed', 'clinic', 'appointment', 'beds')
DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
CLINIC_MEASURE = {'primary_care_visit': 'primary_care_visits', 'specialist_visit': 'specialist_visits', 'urgent_care_visit': 'urgent_care_visits'}
DEFAULTS = {'source': 'ed', 'visit_types': [], 'weeks': 4, 'shift_lengths': [8, 10, 12], 'start_step_hours': 1,
            'wait_threshold_minutes': 30.0, 'target_wait_probability': 0.10, 'target_blocking': 0.02, 'mean_service_minutes': None,
            'min_on_duty': 0, 'fte_hours_per_week': 40.0, 'cost_per_hour_usd': None, 'current_fte': None,
            'current_hours_per_week': None, 'staff_to': 'point'}
STAFF_TO = ('point', 'hi80')

PRODUCT = {'population': 'optional', 'view': 'staffing',
           'use': 'STAFFING DECISIONS from a forecast: how many shifts / FTE to post for the next weeks so forecast ED, clinic or bed demand '
                  'meets a wait or blocking target - forecast -> Erlang hour-of-week requirement -> minimum-hours shift roster (8/10/12h), '
                  'staffing to the upper forecast, gap vs current FTE.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'source': 'ed|clinic|appointment|beds', 'visit_types': [], 'weeks': 4, 'shift_lengths': [8, 10, 12],
                           'start_step_hours': 1, 'wait_threshold_minutes': 30, 'target_wait_probability': 0.1, 'target_blocking': 0.02,
                           'mean_service_minutes': None, 'min_on_duty': 0, 'fte_hours_per_week': 40, 'cost_per_hour_usd': None,
                           'current_fte': None, 'current_hours_per_week': None, 'staff_to': 'point|hi80'},
            'meaning': 'Forecast-to-capacity: the demand engine forecasts weekly volume (80% interval) for the next weeks; the queueing engine\'s '
                       'received hour-of-week arrival profile and service time are rescaled to each forecast week; SIPP Erlang C (wait) or '
                       'Erlang B (beds) gives staff needed per hour block; an integer program picks shift starts and lengths covering every '
                       'block at minimum paid hours. Reports posted shifts per day, paid hours, FTE, slack, the upper-forecast plan, cost at a '
                       'stated hourly rate and the gap vs stated current staffing.'}
NATIVE = ('count', 'value')


def schema(obj, string, array):
    num = lambda lo, hi, d: {'type': 'number', 'minimum': lo, 'maximum': hi, 'description': d}  # noqa: E731
    return obj({'source': string(SOURCES, 'ed (default), clinic visits, kept appointments, beds (inpatient stays)'),
                'visit_types': array(string(list(CLINIC_MEASURE)), 'clinic only; one type for a forecastable series'),
                'weeks': {'type': 'integer', 'minimum': 1, 'maximum': 13, 'description': 'forecast weeks to plan (default 4)'},
                'shift_lengths': array({'type': 'integer', 'minimum': 4, 'maximum': 16}, 'allowed shift lengths in hours (default 8, 10, 12)'),
                'start_step_hours': {'type': 'integer', 'minimum': 1, 'maximum': 12, 'description': 'shifts may start every N hours'},
                'wait_threshold_minutes': num(1, 1440, 'wait target threshold'), 'target_wait_probability': num(0.001, 1, 'P(wait > threshold) target'),
                'target_blocking': num(0.0001, 1, 'beds: blocking target'), 'mean_service_minutes': num(0.1, 100000, 'override observed service time'),
                'min_on_duty': {'type': 'integer', 'minimum': 0, 'maximum': 100, 'description': 'floor of staff on duty every hour'},
                'fte_hours_per_week': num(1, 80, 'paid hours per FTE (default 40)'), 'cost_per_hour_usd': num(0, 10000, 'stated loaded hourly rate'),
                'current_fte': num(0, 10000, 'current staffing in FTE'), 'current_hours_per_week': num(0, 100000, 'current paid hours per week'),
                'staff_to': string(STAFF_TO, 'point forecast (default) or the 80% upper forecast')})


def validate(p):
    if p is None: p = {}
    if not isinstance(p, dict) or set(p) - set(DEFAULTS): raise ValueError('staffing_plan parameters: ' + ', '.join(sorted(DEFAULTS)))
    out = {**DEFAULTS, **{k: v for k, v in p.items() if v is not None}}
    if out['source'] not in SOURCES: raise ValueError(f'source: {SOURCES}')
    vt = out['visit_types'] or []
    if not isinstance(vt, list) or any(v not in CLINIC_MEASURE for v in vt): raise ValueError(f'visit_types: subset of {sorted(CLINIC_MEASURE)}')
    out['visit_types'] = list(vt)
    if type(out['weeks']) is not int or not 1 <= out['weeks'] <= 13: raise ValueError('weeks 1..13')
    sl = out['shift_lengths']
    if not isinstance(sl, list) or not sl or any(type(x) is not int or not 4 <= x <= 16 for x in sl): raise ValueError('shift_lengths: integers 4..16')
    out['shift_lengths'] = sorted(set(sl))
    if type(out['start_step_hours']) is not int or not 1 <= out['start_step_hours'] <= 12: raise ValueError('start_step_hours 1..12')
    if type(out['min_on_duty']) is not int or not 0 <= out['min_on_duty'] <= 100: raise ValueError('min_on_duty 0..100')
    for k, lo, hi in (('wait_threshold_minutes', 1, 1440), ('target_wait_probability', 0.001, 1), ('target_blocking', 0.0001, 1),
                      ('fte_hours_per_week', 1, 80)):
        if type(out[k]) not in (int, float) or not lo <= out[k] <= hi: raise ValueError(f'{k} {lo}..{hi}')
        out[k] = float(out[k])
    for k, lo, hi in (('mean_service_minutes', 0.1, 1e5), ('cost_per_hour_usd', 0, 1e4), ('current_fte', 0, 1e4), ('current_hours_per_week', 0, 1e5)):
        if out[k] is not None and (type(out[k]) not in (int, float) or not lo <= out[k] <= hi): raise ValueError(f'{k} {lo}..{hi}')
    if out['staff_to'] not in STAFF_TO: raise ValueError(f'staff_to: {STAFF_TO}')
    return out


# ---- shift scheduling integer program -------------------------------------------------------------------------
def shift_milp(requirement, lengths, step=1, floor=0):
    """Minimum paid hours covering requirement[h] (168 hour-of-week blocks, cyclic). -> {'shifts': [(start, length, count)],
    'paid_hours', 'coverage', 'slack_hours', 'status'}."""
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    req = [max(int(r or 0), floor) for r in requirement]
    H = len(req)
    if not any(req): return {'shifts': [], 'paid_hours': 0, 'coverage': [0] * H, 'slack_hours': 0, 'status': 'optimal', 'shift_count': 0}
    cols = [(s, L) for L in lengths for s in range(0, H, step)]
    A = np.zeros((H, len(cols)))
    for j, (s, L) in enumerate(cols):
        for k in range(L): A[(s + k) % H, j] = 1
    cost = np.array([L + 1e-3 for _, L in cols])  # paid hours; tie-break: fewer shifts
    res = milp(cost, integrality=np.ones(len(cols)), bounds=Bounds(0, max(req) + 1),
               constraints=LinearConstraint(A, np.array(req, float), np.inf), options={'time_limit': 20})
    if res.x is None: raise ValueError(f'shift program found no solution ({res.message})')
    x = np.round(res.x).astype(int)
    cover = (A @ x).astype(int).tolist()
    shifts = [(s, L, int(n)) for (s, L), n in zip(cols, x) if n > 0]
    return {'shifts': shifts, 'paid_hours': int(sum(L * n for _, L, n in shifts)), 'coverage': cover,
            'slack_hours': int(sum(c - r for c, r in zip(cover, req))), 'status': 'optimal' if res.success else 'time_limited_feasible',
            'shift_count': int(x.sum()), 'requirement_hours': int(sum(req))}


def posted(shifts):
    """Shift plan per day: [{day, start, length, count}] sorted by day and start."""
    return [{'day': DAYS[s // 24], 'start': f'{s % 24:02d}:00', 'end': f'{(s + L) % 24:02d}:00', 'length_hours': L, 'staff': n}
            for s, L, n in sorted(shifts)]


# ---- forecast and requirement -------------------------------------------------------------------------------
def _measure(p):
    if p['source'] == 'ed': return 'ed_visits'
    if p['source'] == 'appointment': return 'appointments'
    if p['source'] == 'beds': return 'inpatient_admissions'
    return CLINIC_MEASURE[p['visit_types'][0]] if len(p['visit_types']) == 1 else 'primary_care_visits'


def _forecast(path, patient_ids, p, as_of):
    from . import demand
    rep = demand.execute(path, patient_ids, {'measure': _measure(p), 'frequency': 'week', 'horizon': p['weeks'] + 1}, as_of)
    s = rep['series'][0]
    if s.get('status') != 'forecast': return None, f"demand engine returned no forecast ({s.get('reason') or s.get('status')})"
    weeks = [r for r in s['forecast'] if not r.get('in_progress')][:p['weeks']]
    return {'measure': _measure(p), 'weeks': [{'week_of': r['period'], 'point': r['point'], 'lo80': r.get('lo80'), 'hi80': r.get('hi80')}
                                              for r in weeks], 'model': s.get('model') or s.get('selected_model')}, None


def _queue_params(p):
    from . import queueing
    q = {'source': p['source'], 'wait_threshold_minutes': p['wait_threshold_minutes'], 'target_wait_probability': p['target_wait_probability'],
         'target_blocking': p['target_blocking'], 'mean_service_minutes': p['mean_service_minutes']}
    if p['source'] == 'clinic' and p['visit_types']: q['visit_types'] = p['visit_types']
    return queueing.validate(q)


def run(path, patient_ids, p, as_of):
    from . import queueing
    qp = _queue_params(p)
    base = queueing.engine_execute(path, patient_ids, qp, as_of)
    a = base['answer']
    if a.get('status') == 'insufficient_data':
        return {'schema': VERSION, 'status': 'insufficient_data', 'reason': a['reason'], 'parameters': p, 'as_of': as_of,
                'warnings': base['warnings'], 'assumptions': base['assumptions']}
    inp = a['inputs']
    rates = [r / (inp['arrival_scale_applied'] or 1.0) for r in inp['arrival_rates_per_hour']]
    mu, hist = inp['service_rate_per_hour'], sum(rates)
    fc, err = _forecast(path, patient_ids, p, as_of)
    warnings = list(base['warnings'])
    if fc is None:  # heal: plan on the historical weekly volume, say so
        warnings.append(f'{err}; the plan uses the historical weekly volume ({hist:.1f}/week) for every week.')
        start = date.fromisoformat(as_of) + timedelta(days=7 - date.fromisoformat(as_of).weekday())
        fc = {'measure': _measure(p), 'model': None, 'weeks': [{'week_of': (start + timedelta(weeks=i)).isoformat(), 'point': hist, 'lo80': None, 'hi80': None}
                                                               for i in range(p['weeks'])]}
    plans = []
    for w in fc['weeks']:
        row = {'week_of': w['week_of'], 'forecast_volume': w['point'], 'forecast_lo80': w['lo80'], 'forecast_hi80': w['hi80']}
        for label, vol in (('point', w['point']), ('hi80', w['hi80'])):
            if vol is None: continue
            scale = (vol / hist) if hist > 0 else 0.0
            req_rows, _ = queueing.sipp(rates, mu, qp, lag=qp['lag_adjustment'], scale=scale)
            req = [r['servers'] or 0 for r in req_rows]
            sol = shift_milp(req, p['shift_lengths'], p['start_step_hours'], p['min_on_duty'])
            row[label] = {'scale_vs_history': round(scale, 4), 'requirement_staff_hours': sol['requirement_hours'],
                          'peak_staff': max(req) if req else 0, 'paid_hours': sol['paid_hours'], 'shifts': sol['shift_count'],
                          'fte': round(sol['paid_hours'] / p['fte_hours_per_week'], 2), 'slack_hours': sol['slack_hours'],
                          'solver_status': sol['status'], 'posted_shifts': posted(sol['shifts']),
                          'cost_usd': None if p['cost_per_hour_usd'] is None else round(sol['paid_hours'] * p['cost_per_hour_usd'], 2),
                          'unmet_blocks': sum(1 for r in req_rows if r['servers'] is None)}
        plans.append(row)
    chosen = [w.get(p['staff_to']) or w['point'] for w in plans]
    current = p['current_hours_per_week'] if p['current_hours_per_week'] is not None else (
        p['current_fte'] * p['fte_hours_per_week'] if p['current_fte'] is not None else None)
    gaps = None if current is None else [{'week_of': w['week_of'], 'needed_hours': c['paid_hours'], 'current_hours': current,
                                          'gap_hours': round(c['paid_hours'] - current, 1),
                                          'gap_fte': round((c['paid_hours'] - current) / p['fte_hours_per_week'], 2)} for w, c in zip(plans, chosen)]
    table = [{'week_of': w['week_of'], 'plan': p['staff_to'], 'forecast_volume': w['forecast_volume'], **{k: c[k] for k in (
        'peak_staff', 'requirement_staff_hours', 'paid_hours', 'fte', 'shifts', 'slack_hours', 'cost_usd')},
              'hi80_paid_hours': (w.get('hi80') or {}).get('paid_hours'), 'hi80_fte': (w.get('hi80') or {}).get('fte'),
              'posted_shifts': '; '.join(f"{s['day']} {s['start']}-{s['end']} x{s['staff']}" for s in c['posted_shifts'])}
             for w, c in zip(plans, chosen)]
    assumptions = [*base['assumptions'],
                   'Each forecast week uses the historical hour-of-week arrival shape rescaled to that week\'s forecast volume.',
                   f"Shifts of {', '.join(map(str, p['shift_lengths']))} hours may start every {p['start_step_hours']} hour(s), any day, wrapping Sunday to Monday; "
                   'breaks, handoff overlap, skill mix and individual availability are not modelled.',
                   f"FTE = paid hours / {p['fte_hours_per_week']:g} hours." + (f" Cost at the stated ${p['cost_per_hour_usd']:g}/hour." if p['cost_per_hour_usd'] is not None else ' No hourly rate stated, so no dollar cost.')]
    return {'schema': VERSION, 'status': 'planned', 'as_of': as_of, 'parameters': p, 'forecast': fc, 'weeks': plans, 'table': table,
            'gap_vs_current': gaps, 'queueing': {'objective': a['objective'], 'mean_service_minutes': inp['mean_service_minutes'],
                                                  'weekly_arrivals_history': round(hist, 3), 'service_source': inp['service_source'],
                                                  'arrival_rate_source': inp['arrival_rate_source'], 'assumption_checks': a['assumption_checks']},
            'warnings': warnings, 'assumptions': assumptions,
            'method': ('demand.execute weekly forecast (80% interval) -> queueing hour-of-week profile and service rate rescaled per week -> '
                       'SIPP Erlang requirement per hour block -> shift integer program (scipy milp / HiGHS) minimising paid hours.')}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    return run(path, patient_ids, p, str(as_of)[:10])


def headline(r):
    if r.get('status') == 'insufficient_data': return f"No staffing plan: {r['reason']}"
    p, t = r['parameters'], r['table']
    target = (f"blocking <= {p['target_blocking']:.1%}" if r['queueing']['objective'] == 'blocking' else
              f"P(wait > {p['wait_threshold_minutes']:g} min) <= {p['target_wait_probability']:.0%}")
    first = t[0]
    text = (f"Staffing plan for {len(t)} forecast week(s) from {first['week_of']} ({r['forecast']['measure'].replace('_', ' ')}, {target}): "
            f"week 1 forecast {first['forecast_volume']:.1f} arrivals -> peak {first['peak_staff']} on duty, {first['shifts']} shifts, "
            f"{first['paid_hours']} paid hours ({first['fte']} FTE, {first['slack_hours']} slack hours)")
    if first.get('hi80_paid_hours') is not None and p['staff_to'] == 'point':
        text += f"; staffing to the 80% upper forecast needs {first['hi80_paid_hours']} hours ({first['hi80_fte']} FTE)"
    if first.get('cost_usd') is not None: text += f"; cost ${first['cost_usd']:,.0f}/week"
    hours = [w['paid_hours'] for w in t]
    if len(t) > 1: text += f"; paid hours range {min(hours)}-{max(hours)} across the weeks"
    if r.get('gap_vs_current'):
        g = r['gap_vs_current'][0]
        text += f"; vs current {g['current_hours']:g} hours the gap is {g['gap_hours']:+g} hours ({g['gap_fte']:+g} FTE)"
    return text


def answer(report, plan, question):
    if report.get('status') == 'insufficient_data':
        return {'status': 'unsupported', 'answer_type': 'unsupported', 'value': headline(report), 'missing': [report['reason']],
                'patient_ids': [], 'limitations': report.get('assumptions', [])}
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    first = report['table'][0]
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['table'], 'count': first['shifts'],
            'patient_ids': [], 'value': headline(report) + '.', 'method': report['method'],
            'limitations': report['assumptions'] + report['warnings'][:3] + ['Planning roster from synthetic data; not an optimal individual schedule or a clinical staffing standard.'],
            'overall': {'staffing_plan': {'weeks': report['weeks'], 'gap_vs_current': report['gap_vs_current'], 'forecast': report['forecast']}}}


# ---- free local-compiler route ----------------------------------------------------------------------------
STAFF = r'(?:nurses?|providers?|physicians?|doctors?|clinicians?|staff|rns?|techs?|apps?)'
ROSTER = (r'\bshifts?\b|\bstaffing (?:needed|required|needs?)\b|\brosters?\b|\bfte\b|\bfull[- ]time equivalents?\b|\bstaffing (?:plan|schedule|decisions?|levels?|model)\b|'
          r'\bschedul\w* (?:\w+ ){0,2}' + STAFF + r'\b|\bhow many ' + STAFF + r' (?:should|do|will|must) we (?:schedule|post|hire|staff|roster)\b|\bpost(?:ed)? shifts?\b')
FORECAST = (r'\bforecast\w*\b|\bprojected\b|\bexpected (?:demand|volume|visits)\b|\bnext (?:\d{1,2} |two |three |four |six |eight )?(?:weeks?|month|quarter)\b|'
            r'\bcoming (?:weeks?|month)\b|\bupcoming\b|\bdemand\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not (re.search(ROSTER, low) and re.search(FORECAST, low)): return None
    if re.match(r'(?:what (?:is|are)|explain|define|why)\b', low): return None
    if re.search(r'\bno[- ]?shows?\b|\bover[- ]?book', low): return None  # scheduling engine
    if re.search(r'\bratios?\b|\b\d+\s*:\s*\d+\b|\bper (?:nurse|rn)\b', low): return None  # staffing from stated ratios stays demand
    params = {}
    if re.search(r'\bbeds?\b|\binpatient\b|\bwards?\b', low): params['source'] = 'beds'
    elif re.search(r'\burgent care\b', low): params.update(source='clinic', visit_types=['urgent_care_visit'])
    elif re.search(r'\bprimary care\b', low): params.update(source='clinic', visit_types=['primary_care_visit'])
    elif re.search(r'\bspecialt(?:y|ist)\b', low): params.update(source='clinic', visit_types=['specialist_visit'])
    elif re.search(r'\bclinics?\b|\boutpatient\b|\bpractices?\b', low) and not re.search(r'\bed\b|\bemergency\b', low): params['source'] = 'clinic'
    wk = re.search(r'\bnext (\d{1,2}|two|three|four|six|eight) weeks?\b', low)
    if wk:
        n = {'two': 2, 'three': 3, 'four': 4, 'six': 6, 'eight': 8}.get(wk.group(1)) or int(wk.group(1))
        if 1 <= n <= 13: params['weeks'] = n
    elif re.search(r'\bnext month\b|\bcoming month\b', low): params['weeks'] = 4
    elif re.search(r'\bnext quarter\b', low): params['weeks'] = 13
    elif re.search(r'\bnext week\b', low): params['weeks'] = 1
    lens = sorted({int(x) for x in re.findall(r'\b(8|10|12)[- ]?(?:hour|hr)\b', low)})
    if lens: params['shift_lengths'] = lens
    m = re.search(r'\bwaits?\b[^.?\d]{0,40}?(\d+(?:\.\d+)?)\s*(?:min|minutes)\b', low)
    if m and 1 <= float(m.group(1)) <= 1440: params['wait_threshold_minutes'] = float(m.group(1))
    pct = re.search(r'(\d{1,2}(?:\.\d+)?)\s?(?:%|percent)', low)
    if pct and 0 < float(pct.group(1)) < 100:
        params['target_blocking' if params.get('source') == 'beds' else 'target_wait_probability'] = float(pct.group(1)) / 100
    cur = re.search(r'\b(?:we have|currently|current(?:ly)? staffed with|today we staff)\s+(\d{1,4}(?:\.\d+)?)\s*(fte|hours)\b', low)
    if cur: params['current_fte' if cur.group(2) == 'fte' else 'current_hours_per_week'] = float(cur.group(1))
    rate = re.search(r'\$\s?(\d[\d,]*(?:\.\d+)?)\s*(?:/|per|an|a)\s*(?:hour|hr)\b', low)
    if rate: params['cost_per_hour_usd'] = float(rate.group(1).replace(',', ''))
    if re.search(r'\bupper (?:forecast|bound|interval|end)\b|\b(?:80th|90th) percentile\b|\bworst case\b|\bbusy (?:scenario|case)\b', low): params['staff_to'] = 'hi80'
    return plan(OP, params, 'Forecast-to-capacity staffing: demand forecast -> Erlang hour-of-week requirement -> minimum-hours shift roster',
                question, ['source ' + params.get('source', 'ed')])


TEMPLATES = ['Build a shift roster for the emergency department that covers the forecast for the {horizon}',
             'how many FTE should we budget for urgent care given projected volume next month',
             'Turn the demand forecast into a staffing plan with 12 hour shifts',
             'what shifts do we need to post for primary care over the next 4 weeks',
             'staffing decisions for the ED based on forecasted visits, waits under 20 minutes',
             'Plan nurse shifts against the upcoming demand forecast, we have 6 FTE today',
             'roster for inpatient beds for the next quarter based on forecast admissions',
             'translate projected ED arrivals into 8 and 10 hour shifts at the lowest paid hours',
             'staffing model for the coming weeks if demand hits the upper forecast']
