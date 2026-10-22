"""Transitions of care (CMS TEAM / TCM style): for each discharge, did the member get a reached contact within 2 business days
(use 2 calendar days + weekend slack: 4), an ambulatory or telehealth visit within 7 or 14 days, a medication reconciliation
within 30 days, and was there a readmission within 30 days; the discharges that lack a step get a proposed action.

Evidence is the semantic warehouse only: stay (discharge_known=1), telephone_messages (an outgoing call that reached the member:
conversation_completed or patient_reached), appointment (kept) and encounter (primary care / specialist / telehealth visit),
medication_reconciliation records, adt_visit for the discharge disposition. A discharge with an unknown discharge date is never
counted as a transition (it is listed as unknown). Actions are proposals; none is marked done without a receipt.
"""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import json
import re

from . import care_actions as CA

VERSION = 'transitions-of-care/1'
OP = 'transitions_of_care'
FOCUS = ('gaps', 'all', 'summary', 'readmitted')
GROUP_BY = ('none', 'organization', 'disposition', 'type')
VISIT_CONCEPTS = ('primary_care_visit', 'specialist_visit', 'telehealth_contact')
REACHED = ('conversation_completed', 'patient_reached')
PRODUCT = {'population': 'optional', 'view': 'loops',
           'use': 'TRANSITIONS OF CARE / post-discharge follow-up (TEAM, TCM): discharges in a window, follow-up visit within 7 or 14 '
                  'days, post-discharge contact within 2 business days, medication reconciliation within 30 days, 30-day readmission, '
                  'by hospital or discharge disposition, and the next action for each discharge missing a step.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'focus': 'gaps|all|summary|readmitted', 'window_days': 365, 'follow_up_days': 7, 'stay_types': ['inpatient', 'observation'],
                           'group_by': 'none|organization|disposition|type', 'top': 200},
            'meaning': 'Per discharge (known discharge date inside the window): reached contact within 4 days, kept ambulatory/telehealth '
                       'visit within follow_up_days (7 or 14), medication reconciliation within 30 days, readmission within 30 days; rates '
                       'with Wilson intervals; each discharge missing a step gets a proposed action.'}
NATIVE = ('count', 'value')
ASSUMPTIONS = ['Synthetic warehouse data; only discharges with a known discharge date count (unknown-date stays are listed separately).',
               'A contact counts only when the call reached the member (conversation_completed / patient_reached), not a voicemail or no-answer.',
               'Actions are proposals; none is marked done without a counterparty receipt.']


def schema(obj, string, array):
    return obj({'focus': string(FOCUS, 'gaps: discharges missing a step; all; summary: rates only; readmitted: 30-day readmissions'),
                'window_days': {'type': 'integer', 'minimum': 7, 'maximum': 1830}, 'follow_up_days': {'type': 'integer', 'enum': [7, 14]},
                'stay_types': array(string(('inpatient', 'observation')), 'Stay types; empty = both'),
                'group_by': string(GROUP_BY), 'top': {'type': 'integer', 'minimum': 1, 'maximum': 5000}})


def validate(p):
    allowed = {'focus', 'window_days', 'follow_up_days', 'stay_types', 'group_by', 'top'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('transitions_of_care parameters: ' + ', '.join(sorted(allowed)))
    out = {'focus': p.get('focus', 'gaps'), 'window_days': p.get('window_days', 365), 'follow_up_days': p.get('follow_up_days', 7),
           'stay_types': sorted(set(p.get('stay_types') or [])), 'group_by': p.get('group_by', 'none'), 'top': p.get('top', 200)}
    if out['focus'] not in FOCUS: raise ValueError(f'focus: {FOCUS}')
    if type(out['window_days']) is not int or not 7 <= out['window_days'] <= 1830: raise ValueError('window_days 7..1830')
    if out['follow_up_days'] not in (7, 14): raise ValueError('follow_up_days 7 or 14')
    if any(s not in ('inpatient', 'observation') for s in out['stay_types']): raise ValueError('stay_types: inpatient, observation')
    if out['group_by'] not in GROUP_BY: raise ValueError(f'group_by: {GROUP_BY}')
    if type(out['top']) is not int or not 1 <= out['top'] <= 5000: raise ValueError('top 1..5000')
    return out


def _within(days, lo, hi):
    i = bisect_left(days, lo)
    return days[i] if i < len(days) and days[i] <= hi else None


def run(db, patient_ids, p, as_of):
    keep = set(patient_ids) if patient_ids is not None else None
    t = CA.tables(db)
    absent = CA.missing_tables(db, ('stay',))
    if absent:
        return {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'unavailable': absent, 'discharges': 0, 'table': [], 'patient_ids': [],
                'summary': {}, 'by_group': [], 'unknown_discharge_stays': 0, 'method': 'stay table absent: transitions cannot be computed'}
    lo = CA.add_days(as_of, -p['window_days'])
    kinds = tuple(p['stay_types'] or ('inpatient', 'observation'))
    stays, unknown = [], 0
    for pid, adm, dis, typ, org, readm, dtr, known in db.execute(
            'SELECT patient_id, admit_date, discharge_date, type, organization_id, readmitted_30d, days_to_readmission, discharge_known FROM stay'):
        if typ not in kinds or (keep is not None and pid not in keep): continue
        if not known or not dis:
            if adm and lo <= adm[:10] <= as_of: unknown += 1
            continue
        d = dis[:10]
        if lo <= d <= as_of: stays.append((pid, adm[:10], d, typ, org, readm, dtr))
    pids = {s[0] for s in stays}
    visits, calls, recs = defaultdict(list), defaultdict(list), defaultdict(list)

    def day(x): return CA.days_between(lo, x[:10])
    if 'appointment' in t:
        for pid, d in db.execute("SELECT patient_id, substr(scheduled_date,1,10) FROM appointment WHERE kept=1 AND scheduled_date >= ?", (lo,)):
            if pid in pids and d <= as_of: visits[pid].append(day(d))
    if 'encounter' in t:
        q = ','.join('?' * len(VISIT_CONCEPTS))
        for pid, d in db.execute(f"SELECT patient_id, substr(event_date,1,10) FROM encounter WHERE type_concept IN ({q}) AND event_date >= ?", (*VISIT_CONCEPTS, lo)):
            if pid in pids and d <= as_of: visits[pid].append(day(d))
    if 'record' in t:
        for pid, d, pay in db.execute("SELECT patient_id, substr(event_date,1,10), payload FROM record WHERE stream='telephone_messages' AND event_date >= ?", (lo,)):
            if pid in pids and d <= as_of:
                r = json.loads(pay or '{}')
                if r.get('direction') == 'outgoing' and r.get('communication_result') in REACHED: calls[pid].append(day(d))
        for pid, d in db.execute("SELECT patient_id, substr(event_date,1,10) FROM record WHERE stream='medication_reconciliation' AND event_date >= ?", (lo,)):
            if pid in pids and d <= as_of: recs[pid].append(day(d))
    for dct in (visits, calls, recs):
        for k in dct: dct[k].sort()
    disp = {}
    if 'adt_visit' in t:
        for pid, adm, dd in db.execute("SELECT patient_id, substr(admit_date,1,10), discharge_disposition FROM adt_visit"):
            if pid in pids: disp[(pid, adm)] = dd or 'unrecorded'
    rows = []
    for pid, adm, d, typ, org, readm, dtr in stays:
        n = day(d)
        end_known = CA.days_between(d, as_of)
        contact = _within(calls.get(pid, []), n, n + 4)
        visit = _within(visits.get(pid, []), n, n + p['follow_up_days'])
        rec = _within(recs.get(pid, []), n, n + 30)
        row = {'patient_id': pid, 'admit_date': adm, 'discharge_date': d, 'type': typ, 'organization': org or 'unknown',
               'disposition': disp.get((pid, adm), 'unrecorded'), 'contact_within_4d': contact is not None,
               f'visit_within_{p["follow_up_days"]}d': visit is not None, 'med_rec_within_30d': rec is not None,
               'readmitted_30d': bool(readm), 'days_to_readmission': dtr, 'followup_window_complete': end_known >= p['follow_up_days']}
        steps = []
        if contact is None: steps.append(CA.step('call the member to confirm discharge instructions and symptoms', 'care coordinator', 'no reached contact within 4 days'))
        if visit is None: steps.append(CA.step(f'book a follow-up visit within {p["follow_up_days"]} days of discharge' if row['followup_window_complete'] else 'book the follow-up visit before the window closes',
                                              'practice', f'no kept visit within {p["follow_up_days"]} days'))
        if rec is None: steps.append(CA.step('reconcile medications against the discharge list', 'pharmacist', 'no medication reconciliation within 30 days'))
        if row['disposition'] in ('snf', 'irf', 'home_health') and visit is None: steps.append(CA.step(f'confirm the {row["disposition"]} receiving team has the discharge summary', 'post-acute facility', 'post-acute disposition'))
        row['gap'] = bool(steps)
        row['next_actions'] = steps
        rows.append(row)
    n = len(rows)
    fv = f'visit_within_{p["follow_up_days"]}d'
    rate = lambda k: {'n': k, 'rate': round(k / n, 4) if n else None, 'ci95': CA.wilson(k, n)}
    summary = {'discharges': n, 'contact_within_4d': rate(sum(r['contact_within_4d'] for r in rows)), fv: rate(sum(r[fv] for r in rows)),
               'med_rec_within_30d': rate(sum(r['med_rec_within_30d'] for r in rows)), 'readmitted_30d': rate(sum(r['readmitted_30d'] for r in rows)),
               'all_steps_complete': rate(sum(not r['gap'] for r in rows))}
    key = lambda r: {'organization': r['organization'], 'disposition': r['disposition'], 'type': r['type']}.get(p['group_by'], 'all')
    g = defaultdict(list)
    for r in rows: g[key(r)].append(r)
    by_group = [{'group': k, 'discharges': len(v), 'visit_rate': round(sum(r[fv] for r in v) / len(v), 4), 'med_rec_rate': round(sum(r['med_rec_within_30d'] for r in v) / len(v), 4),
                 'readmit_rate': round(sum(r['readmitted_30d'] for r in v) / len(v), 4)} for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))]
    sel = {'gaps': [r for r in rows if r['gap']], 'readmitted': [r for r in rows if r['readmitted_30d']]}.get(p['focus'], rows)
    sel.sort(key=lambda r: (-len(r['next_actions']), r['discharge_date']), reverse=False)
    queue = CA.work_queue(rows, lambda r: len(r['next_actions']) + 2 * r['readmitted_30d'] + (1 if r['disposition'] in ('snf', 'irf', 'home_health') else 0), lambda r: r['organization'])
    return {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'discharges': n, 'summary': summary, 'by_group': by_group, 'work_queue': queue, 'table': sel[:p['top']],
            'patient_ids': sorted({r['patient_id'] for r in sel}), 'unknown_discharge_stays': unknown,
            'method': f"Discharges {lo} to {as_of}; visit within {p['follow_up_days']} days, reached contact within 4 days, med rec within 30 days."}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    db = CA.open_db(path)
    try:
        return run(db, patient_ids, p, str(as_of)[:10])
    finally:
        db.close()


def headline(r):
    if r.get('unavailable'): return 'Transitions of care cannot be computed: ' + ', '.join(r['unavailable']) + ' absent from this warehouse'
    s, fv = r['summary'], f"visit_within_{r['parameters']['follow_up_days']}d"
    pc = lambda k: f"{s[k]['rate']:.0%}" if s[k]['rate'] is not None else 'n/a'
    return (f"{s['discharges']} discharges in the last {r['parameters']['window_days']} days: {pc(fv)} had a follow-up visit within {r['parameters']['follow_up_days']} days, "
            f"{pc('contact_within_4d')} a reached contact within 4 days, {pc('med_rec_within_30d')} a medication reconciliation within 30 days; "
            f"{pc('readmitted_30d')} were readmitted within 30 days; {s['all_steps_complete']['n']} completed every step. {r['unknown_discharge_stays']} stays with no known discharge date are excluded")


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements') or bool(report.get('unavailable'))
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['table'], 'count': len(report['patient_ids']),
            'patient_ids': report['patient_ids'], 'value': headline(report) + '.', 'method': report['method'],
            'definitions': [{'term': 'transition of care', 'basis': 'CMS TEAM / TCM: discharge, reached contact, follow-up visit (7/14 days), medication reconciliation (30 days)'}],
            'limitations': ASSUMPTIONS, 'membership_role': 'selected', 'overall': {'transitions_of_care': {'summary': report['summary'], 'by_group': report['by_group'], 'work_queue': report.get('work_queue')}}}


TOC = (r'\btransitions? of care\b|\bpost[- ]?discharge\b|\bafter (?:a )?(?:hospital )?discharge\b|\bdischarge follow[- ]?up\b|\bfollowed up (?:after|within)\b|'
       r'\b(?:7|seven|14|fourteen)[- ]day follow[- ]?up\b|\bteam model\b|\btcm\b|\bdischarged\b.*\b(?:follow|visit|seen|reconcil\w*)\b|\bdischarges?\b.*\b(?:follow|visit|seen|reconcil\w*|contact)\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(TOC, low): return None
    if re.match(r'(?:what (?:is|are)|explain|define)\b', low) and not re.search(r'\b(?:our|we|my|patients|members|which|how many)\b', low): return None
    if re.search(r'\b(?:cost|spend|pmpm|why|drive[sn]?|predict\w*|risk of)\b', low): return None
    params = {}
    if re.search(r'\b(?:14|fourteen)[- ]day\b', low): params['follow_up_days'] = 14
    if re.search(r'\breadmi\w*\b', low) and not re.search(r'\b(?:follow|visit|reconcil\w*|contact)\b', low): params['focus'] = 'readmitted'
    elif re.search(r'\bhow many\b|\brate\b|\bpercent\w*\b|\bsummary\b|\bwhat share\b|\bproportion\b', low): params['focus'] = 'summary'
    else: params['focus'] = 'gaps'
    if re.search(r'\bby (?:hospital|facility|organi[sz]ation)\b|\bwhich hospitals?\b', low): params['group_by'] = 'organization'
    elif re.search(r'\bby disposition\b|\bsnf\b|\bhome health\b', low): params['group_by'] = 'disposition'
    m = re.search(r'\b(?:last|past) (\d{1,3}) days\b', low)
    if m and 7 <= int(m.group(1)) <= 1830: params['window_days'] = int(m.group(1))
    if re.search(r'\bobservation\b', low): params['stay_types'] = ['observation']
    return plan(OP, params, 'Transitions of care (TEAM / TCM) follow-up completion', question, [f"focus {params['focus']}"])


TEMPLATES = ['Which discharges did not get a follow-up visit within 7 days?', '14-day follow-up rate after discharge by hospital',
             'how many discharged members had a medication reconciliation', 'transitions of care gaps in the last 90 days',
             'post-discharge contact rate by disposition',
             'list discharged members with no follow-up after discharge', 'TEAM discharge follow-up summary']
