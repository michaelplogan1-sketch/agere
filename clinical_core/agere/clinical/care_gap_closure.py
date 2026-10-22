"""Payer care-gap closure (HL7 DEQM Gaps-in-Care style): per quality measure, which members have an open gap, which are
closed and on what evidence, which are not applicable, and the next action that would close each open gap.

DEQM groups a member's status per measure as open-gap / closed-gap / not-applicable (a GapsInCareReport per member). The
engine does the same over the synthetic semantic warehouse and attaches an action family: an order + schedule step when no
visit is booked, a keep-the-booked-visit step when one is, a recontact step after a no-show or cancellation. Closure by
claim alone (billed, no result) is reported as its own evidence class and counted as closed only under
closure_evidence=result_or_claim (the HEDIS administrative method); result_only holds those members open.

Measures (lookback ends at as_of): bcs women 50-74, mammography in the 27 months before as_of; col adults 45-75, FIT in the
12 months; flu adults 18+, influenza vaccine in the current season (1 Aug - 31 Jul); awv members 65+, annual wellness visit in
12 months. Members need active coverage on the patient row to be applicable.
"""
from collections import Counter, defaultdict
import re

from . import care_actions as CA

VERSION = 'care-gap-closure/1'
OP = 'care_gap_closure'
# Parameter values carry the real names of what is measured: the literal-question check reads the plan's parameters, so a plan for
# "breast cancer screening" must say so there (a bare 'bcs' would look like a plan that dropped the question's concept).
MEASURES = ('breast_cancer_screening_mammogram', 'colorectal_cancer_screening_fit', 'influenza_flu_vaccine', 'annual_wellness_visit')
KEY = dict(zip(MEASURES, ('bcs', 'col', 'flu', 'awv')))
NAME = {v: k for k, v in KEY.items()}
LABEL = {'bcs': 'Breast cancer screening (mammography, 27 mo)', 'col': 'Colorectal cancer screening (FIT, 12 mo)',
         'flu': 'Adult influenza immunization (current season)', 'awv': 'Annual wellness visit (12 mo)'}
FOCUS = ('no_closing_evidence', 'closed', 'all', 'summary')
DENOMINATOR = ('applicable_members', 'all_active_members')
CLOSURE = ('result_or_claim', 'result_only')
GROUP_BY = ('none', 'measure', 'practice', 'lob')
PRODUCT = {'population': 'optional', 'view': 'loops',
           'use': 'PAYER CARE GAPS (HEDIS / DEQM gaps in care): members with an open gap in breast cancer screening, colorectal '
                  'screening, flu vaccine or annual wellness visit, gap closure rates by measure / practice / line of business, '
                  'closed-by-claim versus closed-by-result, and the next action to close each open gap.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'measures': list(MEASURES), 'focus': 'no_closing_evidence (open gaps)|closed|all|summary', 'as_of': 'optional ISO date',
                           'closure_evidence': 'result_or_claim|result_only', 'denominator': 'applicable_members|all_active_members',
                           'exclude_not_applicable': True, 'group_by': 'none|measure|practice|lob', 'top': 200},
            'meaning': 'Per member and measure: open-gap, closed-gap (with the evidence record and date) or not-applicable '
                       '(DEQM GapsInCareReport statuses), the closure rate among applicable members with a Wilson interval, and a '
                       'proposed next action for each open gap (order and schedule, keep the booked visit, or recontact after a '
                       'no-show). Claim-only closure is reported separately.'}
NATIVE = ('count', 'value')
ASSUMPTIONS = ['Synthetic warehouse data; coverage must be active on the patient row for a member to be applicable.',
               'Colorectal screening counts FIT only: no colonoscopy or Cologuard rows exist in this warehouse, so col is a lower bound on closure.',
               'Actions are proposals; none is marked done without a counterparty receipt.']


def schema(obj, string, array):
    return obj({'measures': array(string(MEASURES), 'Measures to include; empty = all'),
                'focus': string(FOCUS, 'no_closing_evidence: open gaps with next action; closed; all; summary: counts only'),
                'as_of': {'type': 'string', 'description': 'ISO date the lookbacks end on; empty = the snapshot date'},
                'denominator': string(DENOMINATOR, 'what closure rates divide by'),
                'exclude_not_applicable': {'type': 'boolean', 'description': 'true: members the measure does not apply to are left out of the table'},
                'closure_evidence': string(CLOSURE, 'result_only: a billed claim without a result does not close the gap'),
                'group_by': string(GROUP_BY), 'top': {'type': 'integer', 'minimum': 1, 'maximum': 5000}})


def validate(p):
    allowed = {'measures', 'focus', 'closure_evidence', 'group_by', 'top', 'as_of', 'denominator', 'exclude_not_applicable'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('care_gap_closure parameters: ' + ', '.join(sorted(allowed)))
    out = {'measures': sorted(set(p.get('measures') or [])), 'focus': p.get('focus', 'no_closing_evidence'),
           'as_of': p.get('as_of') or '', 'denominator': p.get('denominator', 'applicable_members'), 'exclude_not_applicable': p.get('exclude_not_applicable', True),
           'closure_evidence': p.get('closure_evidence', 'result_or_claim'), 'group_by': p.get('group_by', 'none'), 'top': p.get('top', 200)}
    if any(m not in MEASURES for m in out['measures']): raise ValueError(f'measures: subset of {MEASURES}')
    if out['focus'] not in FOCUS: raise ValueError(f'focus: {FOCUS}')
    if out['as_of'] and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', out['as_of']): raise ValueError('as_of: ISO date')
    if out['denominator'] not in DENOMINATOR: raise ValueError(f'denominator: {DENOMINATOR}')
    if type(out['exclude_not_applicable']) is not bool: raise ValueError('exclude_not_applicable: boolean')
    if out['closure_evidence'] not in CLOSURE: raise ValueError(f'closure_evidence: {CLOSURE}')
    if out['group_by'] not in GROUP_BY: raise ValueError(f'group_by: {GROUP_BY}')
    if type(out['top']) is not int or not 1 <= out['top'] <= 5000: raise ValueError('top 1..5000')
    return out


def season_start(as_of):
    y = int(as_of[:4])
    return f'{y if as_of[5:7] >= "08" else y - 1}-08-01'


def _members(db, as_of):
    out = {}
    for pid, sex, birth, age, status, lob, prac in db.execute(
            'SELECT patient_id, sex_reported, birth_date, age_latest, coverage_status, line_of_business, pcp_practice_id FROM patient'):
        if birth:
            y, m, d = int(birth[:4]), int(birth[5:7]), int(birth[8:10])
            age = int(as_of[:4]) - y - ((int(as_of[5:7]), int(as_of[8:10])) < (m, d))
        out[pid] = {'sex': sex, 'age': age, 'active': status == 'active', 'lob': lob or 'unknown', 'practice': prac or 'unknown'}
    return out


def _evidence(db, as_of, t):
    """{measure: {patient_id: (kind, date, record)}} latest closing evidence at or before as_of."""
    out = {m: {} for m in KEY.values()}

    def put(m, pid, kind, day, rec):
        if not day or day > as_of: return
        cur = out[m].get(pid)
        rank = lambda k: 0 if k == 'result' else 1  # a result beats a claim on the same day
        if cur is None or (day, -rank(kind)) > (cur[1], -rank(cur[0])): out[m][pid] = (kind, day, rec)
    if 'cancer_screening' in t:
        lo = {'bcs': CA.add_days(as_of, -27 * 30 - 7), 'col': CA.add_days(as_of, -365)}
        for rec, pid, test, status, day in db.execute('SELECT record_id, patient_id, test, result_status, substr(coalesce(result_date, event_date),1,10) FROM cancer_screening'):
            m = 'bcs' if 'mammo' in str(test).lower() else 'col' if str(test).upper() in ('FIT', 'FOBT', 'COLONOSCOPY', 'FIT-DNA') else None
            if m and day and day >= lo[m]: put(m, pid, 'claim' if status == 'claim_billed' else 'result', day, rec)
    if 'immunization' in t:
        start = season_start(as_of)
        for rec, pid, day in db.execute("SELECT record_id, patient_id, substr(event_date,1,10) FROM immunization WHERE vaccine LIKE 'influenza%' AND status='completed'"):
            if day and day >= start: put('flu', pid, 'result', day, rec)
    if 'wellness_visit' in t:
        lo = CA.add_days(as_of, -365)
        for rec, pid, day in db.execute("SELECT record_id, patient_id, substr(event_date,1,10) FROM wellness_visit WHERE awv=1 AND status='completed'"):
            if day and day >= lo: put('awv', pid, 'result', day, rec)
    return out


def _applicable(m, mem):
    a, s = mem['age'], mem['sex']
    if not mem['active'] or a is None: return False
    return {'bcs': s == 'F' and 50 <= a <= 74, 'col': 45 <= a <= 75, 'flu': a >= 18, 'awv': a >= 65}[m]


def _bookings(db, as_of, t):
    """{patient_id: {'next': date | None, 'last_missed': (status, date) | None}} from the appointment table."""
    out = defaultdict(dict)
    if 'appointment' not in t: return out
    lo = CA.add_days(as_of, -180)
    for pid, day, status in db.execute("SELECT patient_id, substr(scheduled_date,1,10), final_status FROM appointment WHERE scheduled_date >= ?", (lo,)):
        if day > as_of and status in ('scheduled', 'booked', None, ''):
            if not out[pid].get('next') or day < out[pid]['next']: out[pid]['next'] = day
        elif status in ('no_show', 'cancelled') and day <= as_of:
            out[pid]['missed'] = (status, day)
    return out


def next_action(m, mem, book):
    b = book or {}
    if b.get('next'):
        return CA.step(f'confirm the {LABEL[m].split(" (")[0].lower()} is performed at the visit booked {b["next"]}', 'practice', 'a visit is already scheduled')
    if b.get('missed'):
        return CA.step('recontact the member to rebook (missed ' + b['missed'][0].replace('_', ' ') + f' {b["missed"][1]}) and place the order',
                       'care coordinator', 'recent missed visit is the barrier')
    return CA.step(f'place the {LABEL[m].split(" (")[0].lower()} order and schedule it', 'practice', 'no visit booked and no closing evidence')


def run(db, patient_ids, p, as_of):
    keep = set(patient_ids) if patient_ids is not None else None
    t = CA.tables(db)
    need = {'bcs': ('cancer_screening',), 'col': ('cancer_screening',), 'flu': ('immunization',), 'awv': ('wellness_visit',)}
    as_of = p['as_of'] or as_of
    measures = [KEY[m] for m in p['measures']] or list(KEY.values())
    unavailable = [m for m in measures if CA.missing_tables(db, need[m])]
    measures = [m for m in measures if m not in unavailable]
    members = _members(db, as_of) if 'patient' in t else {}
    ev, book = _evidence(db, as_of, t), _bookings(db, as_of, t)
    rows, counts = [], defaultdict(Counter)
    for m in measures:
        for pid, mem in members.items():
            if keep is not None and pid not in keep: continue
            if not _applicable(m, mem):
                counts[m]['not_applicable'] += 1
                if not p['exclude_not_applicable'] and mem['active']:
                    rows.append({'patient_id': pid, 'measure': m, 'measure_label': LABEL[m], 'gap_status': 'not-applicable', 'practice': mem['practice'], 'lob': mem['lob'],
                                 'evidence': None, 'evidence_date': None, 'evidence_record': None})
                continue
            hit = ev[m].get(pid)
            closed = bool(hit) and (hit[0] == 'result' or p['closure_evidence'] == 'result_or_claim')
            status = 'closed-gap' if closed else 'open-gap'
            counts[m][status] += 1
            if hit and hit[0] == 'claim': counts[m]['closed_by_claim_only' if closed else 'claim_only_held_open'] += 1
            row = {'patient_id': pid, 'measure': m, 'measure_label': LABEL[m], 'gap_status': status, 'practice': mem['practice'], 'lob': mem['lob'],
                   'evidence': hit[0] if hit else None, 'evidence_date': hit[1] if hit else None, 'evidence_record': hit[2] if hit else None}
            if not closed: row['next_action'] = next_action(m, mem, book.get(pid))
            rows.append(row)
    key = lambda r: {'measure': r['measure'], 'practice': r['practice'], 'lob': r['lob']}.get(p['group_by'], 'all')
    groups = defaultdict(Counter)
    for r in rows:
        if r['gap_status'] != 'not-applicable': groups[key(r)][r['gap_status']] += 1
    def den(g, c):
        if p['denominator'] == 'applicable_members': return c['open-gap'] + c['closed-gap']
        return max(len(active_members), c['open-gap'] + c['closed-gap'])
    active_members = [pid for pid, mem in members.items() if mem['active'] and (keep is None or pid in keep)]
    by_group = [{'group': g, 'applicable': c['open-gap'] + c['closed-gap'], 'open': c['open-gap'], 'closed': c['closed-gap'],
                 'closure_rate': round(c['closed-gap'] / den(g, c), 4), 'closure_rate_ci95': CA.wilson(c['closed-gap'], den(g, c))} for g, c in sorted(groups.items())]
    summary = [{'measure': m, 'label': LABEL[m], **{k: counts[m][k] for k in ('open-gap', 'closed-gap', 'not_applicable', 'closed_by_claim_only', 'claim_only_held_open')}} for m in measures]
    focus = p['focus']
    table = [r for r in rows if focus in ('all', 'summary') or r['gap_status'] == ('open-gap' if focus == 'no_closing_evidence' else 'closed-gap')]
    table.sort(key=lambda r: (r['gap_status'] != 'open-gap', r['measure'], r['patient_id']))
    ids = sorted({r['patient_id'] for r in (rows if focus in ('all', 'summary') else table)})
    weight = {'bcs': 4, 'col': 3, 'awv': 2, 'flu': 1}
    queue = CA.work_queue(rows, lambda r: weight[r['measure']] + (3 if 'recontact' in r['next_action']['step'] else 0) + (-1 if 'confirm the' in r['next_action']['step'] else 0),
                          lambda r: r['practice'])
    return {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'measures': measures, 'unavailable_measures': unavailable, 'summary': summary, 'work_queue': queue, 'as_of_used': as_of,
            'by_group': by_group, 'table': table[:p['top']], 'patient_ids': ids,
            'open': sum(c['open-gap'] for c in counts.values()), 'closed': sum(c['closed-gap'] for c in counts.values()),
            'method': f"DEQM gap status per member and measure; lookbacks end {as_of}; closure evidence {p['closure_evidence']}."}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    db = CA.open_db(path)
    try:
        return run(db, patient_ids, p, str(as_of)[:10])
    finally:
        db.close()


def headline(r):
    parts = [f"{s['label']}: {s['open-gap']} open / {s['closed-gap']} closed" + (f" ({s['closed_by_claim_only']} on claim alone)" if s['closed_by_claim_only'] else '')
             for s in r['summary']]
    miss = f" Not computable here: {', '.join(r['unavailable_measures'])} (source table absent)." if r['unavailable_measures'] else ''
    return f"{r['open']} open care gaps and {r['closed']} closed among applicable members. " + '; '.join(parts) + '.' + miss


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements') or bool(report['unavailable_measures'])
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['table'], 'count': len(report['patient_ids']),
            'patient_ids': report['patient_ids'], 'value': headline(report), 'method': report['method'],
            'definitions': [{'term': 'open-gap / closed-gap', 'basis': 'HL7 DEQM Gaps in Care report statuses per member and measure'}],
            'limitations': ASSUMPTIONS, 'membership_role': 'selected',
            'overall': {'care_gap_closure': {'summary': report['summary'], 'by_group': report['by_group'], 'work_queue': report['work_queue']}}}


GAPS = r'\bcare gaps?\b|\bgaps? in care\b|\bgap closure\b|\bclos(?:e|ing) (?:the |our )?(?:care )?gaps?\b|\bdeqm\b|\bhedis gaps?\b|\bopen gaps?\b|\b(?:vaccin\w*|immuni[sz]ation|flu|wellness) gaps?\b'  # named screening-measure gaps stay with the measure library
MEASURE_WORDS = ((MEASURES[0], r'\bbreast\b|\bmammogra\w*\b|\bbcs\b'), (MEASURES[1], r'\bcolorectal\b|\bcolon\w*\b|\bfit\b|\bcol\b'),
                 (MEASURES[2], r'\bflu\b|\binfluenza\b'), (MEASURES[3], r'\bannual wellness\b|\bawv\b|\bwellness visits?\b'))


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    named = [m for m, rx in MEASURE_WORDS if re.search(rx, low)]
    gaps = re.search(GAPS, low)
    screening = re.search(r'\b(?:overdue|due|missing|needing|need|without|not (?:had|received|up to date)|never had|behind on)\b', low) and named
    if not (gaps or screening): return None
    if re.search(r'\b(?:positive|abnormal)\b', low): return None  # follow-up of a positive / abnormal result is loop_closure
    if re.match(r'(?:what (?:is|are)|explain|define)\b', low) and not re.search(r'\b(?:our|we|my|patients|members|which|how many)\b', low): return None
    if re.search(r'\b(?:cost|spend|pmpm|readmission\w*|admissions?|why|drive[sn]?|predict\w*)\b', low): return None
    if re.search(r'\b\d+\b|\b(?:last|past|previous|prior|since|within)\b', low): return None  # explicit ages / windows belong to the cohort-family and compiler routes
    params = {}
    if named: params['measures'] = named
    if re.search(r'\bclosed\b|\bmet\b|\bcompliant\b|\bcompleted\b', low) and not re.search(r'\bnot (?:yet )?closed\b|\bclosure\b', low): params['focus'] = 'closed'
    elif re.search(r'\bclosure rate\b|\bsummary\b|\brate\b|\bclosure\b|\bbreakdown\b|\breport\b', low): params['focus'] = 'summary'
    else: params['focus'] = 'no_closing_evidence'
    if re.search(r'\bby practice\b|\bby clinic\b|\bwhich practices?\b|\beach practice\b', low): params['group_by'] = 'practice'
    elif re.search(r'\bby (?:line of business|lob|plan)\b', low): params['group_by'] = 'lob'
    elif re.search(r'\bby measure\b', low): params['group_by'] = 'measure'
    if re.search(r'\b(?:result|results) only\b|\bnot (?:just|only) (?:a )?claims?\b|\bwithout (?:counting )?claims?\b', low): params['closure_evidence'] = 'result_only'
    params.setdefault('group_by', 'none')
    params.update({'as_of': str(as_of)[:10], 'denominator': 'applicable_members', 'exclude_not_applicable': True})
    return plan(OP, params, 'Payer care-gap closure (DEQM gaps in care)', question, [f"focus {params['focus']}"])


TEMPLATES = ['Which members have an open breast cancer screening gap?', 'care gap closure rate by practice', 'how many open care gaps do we have by measure',
             'list members overdue for a flu shot', 'show open gaps in care for colorectal screening', 'annual wellness visit gap closure by line of business',
             'which members have a mammography gap counting only results, not claims', 'DEQM gap report for all measures']
