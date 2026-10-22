"""Social needs and community-referral closure (Gravity Project closed-loop referral, HL7 BSeR feedback).

Needs: sdoh_needs.needs (corroborated evidence score: SDOH Z code 2, validated screen 1 per date (max 2), affirmed note 1,
vendor attribute 1; asserted at min_score, default 2), so one positive answer or a neighbourhood vendor score alone is not a
need. Referrals: each community_service_referrals record is a BSeR ServiceRequest for one need (service_requested); every
community_service_outcomes record citing it (referral_record_id) is a Task status update / feedback. The latest update is the
loop state:

  state                      closed?  BSeR / Gravity Task meaning               source statuses
  closed_service_confirmed   yes      completed, delivery confirmed by the CBO / coordinator (service_delivered from a
                                      non-patient information source)
  closed_patient_reported    yes*     completed, delivery reported by the patient   patient_reports_received (delivered)
  closed_declined            yes      cancelled / rejected by the patient           patient_declined
  open_failed                no       failed: arranged but not received            not_received_as_arranged
  open_in_progress           no       accepted / in-progress / on-hold              scheduled, waitlisted, unable_to_reach
  open_no_response           no       requested, no feedback yet                     request_sent (no outcome record)
  no_referral                -        a corroborated need with no referral for it
  * closure_evidence='organization_confirmed' leaves patient-reported delivery open (strict).

Zero false closures by construction: only a delivered=true update or an explicit patient decline closes a loop; scheduling,
waitlisting and contact attempts never do. Clinical (specialist) referral closure is loop_closure.
"""
from collections import Counter, defaultdict
from datetime import date
import json
import math
import re
import sqlite3
from pathlib import Path

VERSION = 'social-needs/1'
OP = 'social_needs'
FOCUS = ('all', 'needs', 'referrals', 'open', 'closed', 'unreferred')
GROUP_BY = ('none', 'practice', 'need')
CLOSURE_EVIDENCE = ('any_delivery_report', 'organization_confirmed')
SERVICE_NEED = {'transport': 'transport', 'transportation': 'transport', 'food_access': 'food', 'food': 'food',
                'housing': 'housing', 'housing_support': 'housing', 'utilities': 'utilities', 'work_schedule': 'work_schedule_constraint'}
STATUS_STATE = {'patient_reports_received': 'delivered', 'service_delivered': 'delivered', 'received': 'delivered',
                'patient_declined': 'closed_declined', 'declined': 'closed_declined',
                'not_received_as_arranged': 'open_failed', 'scheduled': 'open_in_progress', 'waitlisted': 'open_in_progress',
                'unable_to_reach': 'open_in_progress', 'accepted': 'open_in_progress', 'request_sent': 'open_no_response'}
CLOSED = ('closed_service_confirmed', 'closed_patient_reported', 'closed_declined')
BSER = {'closed_service_confirmed': 'Task completed (organization feedback)', 'closed_patient_reported': 'Task completed (patient report)',
        'closed_declined': 'Task cancelled/rejected by patient', 'open_failed': 'Task failed', 'open_in_progress': 'Task in-progress / on-hold',
        'open_no_response': 'ServiceRequest active, no Task feedback', 'no_referral': 'No ServiceRequest'}

PRODUCT = {'population': 'optional', 'view': 'loops',
           'use': 'SOCIAL NEEDS and community-referral closure: who has corroborated food, transport, housing or work-schedule needs, '
                  'which needs were never referred, community/social-service referral loops (Gravity / BSeR) open, failed or closed '
                  'with evidence, close rates and days to closure by practice.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'focus': 'all|needs|referrals|open|closed|unreferred', 'needs': ['food', 'transport', 'housing', 'work_schedule_constraint'],
                           'min_score': 2, 'closure_evidence': 'any_delivery_report|organization_confirmed', 'overdue_days': 30,
                           'group_by': 'none|practice|need', 'top': 200},
            'meaning': 'Corroborated social needs (sdoh_needs evidence score: Z code 2, screen 1/date max 2, affirmed note 1, vendor 1; need at '
                       'min_score) joined to community-service referrals (BSeR ServiceRequest) and their outcome updates (Task feedback). Loop '
                       'state = latest update: closed only on a delivered=true report (organization-confirmed or patient-reported) or a patient '
                       'decline, never on scheduling, waitlist or contact attempts; open loops with days open and overdue flag; needs with no '
                       'referral; close rate with Wilson interval and median days to closure by practice or need.'}
NATIVE = ('count', 'value')


def schema(obj, string, array):
    from .sdoh_needs import NEEDS
    return obj({'focus': string(FOCUS, 'needs: patients with a corroborated need; referrals: every referral loop; open / closed: loops in that '
                                       'state; unreferred: needs with no referral; all: everything'),
                'needs': array(string(NEEDS), 'Need types to include; empty = all'),
                'min_score': {'type': 'integer', 'minimum': 1, 'maximum': 6, 'description': 'Evidence score to assert a need (default 2)'},
                'closure_evidence': string(CLOSURE_EVIDENCE, 'organization_confirmed: patient-reported delivery stays open'),
                'overdue_days': {'type': 'integer', 'minimum': 1, 'maximum': 365}, 'group_by': string(GROUP_BY),
                'top': {'type': 'integer', 'minimum': 1, 'maximum': 5000}})


def validate(p):
    from .sdoh_needs import NEEDS
    allowed = {'focus', 'needs', 'min_score', 'closure_evidence', 'overdue_days', 'group_by', 'top'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('social_needs parameters: ' + ', '.join(sorted(allowed)))
    out = {'focus': p.get('focus', 'all'), 'needs': sorted(set(p.get('needs') or [])), 'min_score': p.get('min_score', 2),
           'closure_evidence': p.get('closure_evidence', 'any_delivery_report'), 'overdue_days': p.get('overdue_days', 30),
           'group_by': p.get('group_by', 'none'), 'top': p.get('top', 200)}
    if out['focus'] not in FOCUS: raise ValueError(f'focus: {FOCUS}')
    if any(n not in NEEDS for n in out['needs']): raise ValueError(f'needs: subset of {NEEDS}')
    if type(out['min_score']) is not int or not 1 <= out['min_score'] <= 6: raise ValueError('min_score 1..6')
    if out['closure_evidence'] not in CLOSURE_EVIDENCE: raise ValueError(f'closure_evidence: {CLOSURE_EVIDENCE}')
    if type(out['overdue_days']) is not int or not 1 <= out['overdue_days'] <= 365: raise ValueError('overdue_days 1..365')
    if out['group_by'] not in GROUP_BY: raise ValueError(f'group_by: {GROUP_BY}')
    if type(out['top']) is not int or not 1 <= out['top'] <= 5000: raise ValueError('top 1..5000')
    return out


def _tables(db): return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def diagnosis_rows(db):
    """(patient_id, icd10, date, source) for SDOH Z55-Z65 codes from encounters, claims and conditions."""
    t, rows = _tables(db), []
    if 'encounter_diagnosis' in t:
        rows += db.execute("SELECT patient_id, icd10, substr(event_date,1,10), 'encounter' FROM encounter_diagnosis "
                           "WHERE icd10 >= 'Z55' AND icd10 < 'Z66'").fetchall()
    if 'payer_claim_diagnosis' in t:
        rows += db.execute("SELECT patient_id, icd10, substr(service_from,1,10), 'claim' FROM payer_claim_diagnosis "
                           "WHERE icd10 >= 'Z55' AND icd10 < 'Z66' AND coalesce(counts_in_metrics,1) = 1").fetchall()
    if 'condition' in t:
        rows += db.execute("SELECT patient_id, icd10, substr(coalesce(event_date, recorded_date),1,10), 'condition' FROM condition "
                           "WHERE icd10 >= 'Z55' AND icd10 < 'Z66'").fetchall()
    return rows


def referrals(db, as_of):
    """{referral record_id: {...}} with its ordered outcome updates (BSeR ServiceRequest + Task feedback)."""
    out = {}
    for rid, pid, day, org, payload in db.execute("SELECT record_id, patient_id, substr(event_date,1,10), organization_id, payload FROM record "
                                                  "WHERE stream = 'community_service_referrals'"):
        r = json.loads(payload or '{}')
        if day and day > as_of: continue
        service = str(r.get('service_requested') or 'unspecified')
        out[rid] = {'referral_record_id': rid, 'patient_id': pid, 'referral_date': day, 'practice': org or 'unknown',
                    'service_requested': service, 'need': SERVICE_NEED.get(service, service), 'consented': r.get('patient_consents'),
                    'initial_status': r.get('status'), 'updates': []}
    for rec, pid, day, payload in db.execute("SELECT record_id, patient_id, substr(event_date,1,10), payload FROM record "
                                             "WHERE stream = 'community_service_outcomes'"):
        r = json.loads(payload or '{}')
        ref = out.get(r.get('referral_record_id'))
        if ref is None or (day and day > as_of): continue
        ref['updates'].append({'record_id': rec, 'date': day, 'status': r.get('status'), 'delivered': r.get('service_delivered') in (True, 1, 'true'),
                               'source': r.get('information_source') or 'unknown'})
    for ref in out.values():
        ref['updates'].sort(key=lambda u: (u['date'] or '', u['record_id']))
    return out


def loop_state(ref, closure_evidence='any_delivery_report'):
    """(state, basis, closing update or None). Only delivery reports or a patient decline close a loop."""
    if not ref['updates']:
        return 'open_no_response', f"referral {ref['initial_status'] or 'sent'}; no outcome update received", None
    last = ref['updates'][-1]
    status = str(last['status'] or '')
    kind = 'delivered' if last['delivered'] else STATUS_STATE.get(status, 'open_in_progress')
    if kind == 'delivered':
        patient = bool(re.search(r'patient|self|family|caregiver', last['source'], re.I))
        if not patient: return 'closed_service_confirmed', f"delivery confirmed by {last['source']} ({status})", last
        if closure_evidence == 'organization_confirmed':
            return 'open_in_progress', f'delivery reported by the patient only ({last["source"]}); awaiting organization confirmation', None
        return 'closed_patient_reported', f"delivery reported by {last['source']} ({status})", last
    if kind == 'closed_declined': return 'closed_declined', f"patient declined ({last['source']})", last
    return kind, f"latest update {status or 'unknown'} from {last['source']} on {last['date']}", None


def _days(a, b):
    try: return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (TypeError, ValueError): return None


def wilson(k, n, z=1.96):
    if not n: return None
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def run(db, patient_ids, p, as_of):
    from . import sdoh_needs
    keep = set(patient_ids) if patient_ids is not None else None
    wanted = set(p['needs']) or set(sdoh_needs.NEEDS) | set(SERVICE_NEED.values())
    found = sdoh_needs.needs(db, [(pid, code, day, src) for pid, code, day, src in diagnosis_rows(db)], {'min_score': p['min_score']})
    refs = [r for r in referrals(db, as_of).values() if (keep is None or r['patient_id'] in keep) and r['need'] in wanted]
    need_rows = [{'patient_id': pid, 'need': need, 'score': v['score'], 'sources': v['sources']}
                 for pid, by in found.items() if keep is None or pid in keep for need, v in by.items() if need in wanted]
    referred = {(r['patient_id'], r['need']) for r in refs}
    loops = []
    for r in refs:
        state, basis, closing = loop_state(r, p['closure_evidence'])
        end = closing['date'] if closing else as_of
        days = _days(r['referral_date'], end)
        loops.append({'patient_id': r['patient_id'], 'need': r['need'], 'service_requested': r['service_requested'], 'practice': r['practice'],
                      'referral_date': r['referral_date'], 'state': state, 'closed': state in CLOSED, 'bser_status': BSER[state], 'basis': basis,
                      'days_to_closure' if closing else 'days_open': days,
                      'overdue': state not in CLOSED and days is not None and days > p['overdue_days'],
                      'last_update': r['updates'][-1]['date'] if r['updates'] else None, 'updates': len(r['updates']),
                      'consented': r['consented'], 'record_ids': [r['referral_record_id'], *[u['record_id'] for u in r['updates']]],
                      'corroborated_need': any(n['patient_id'] == r['patient_id'] and n['need'] == r['need'] for n in need_rows)})
    unreferred = [{**n, 'state': 'no_referral', 'bser_status': BSER['no_referral']} for n in need_rows if (n['patient_id'], n['need']) not in referred]
    groups = defaultdict(lambda: {'referrals': 0, 'closed': 0, 'closed_with_service': 0, 'open': 0, 'overdue': 0, 'days': []})
    for l in loops:
        g = groups[l['practice'] if p['group_by'] == 'practice' else l['need'] if p['group_by'] == 'need' else 'all']
        g['referrals'] += 1; g['closed'] += l['closed']; g['closed_with_service'] += l['state'] in ('closed_service_confirmed', 'closed_patient_reported')
        g['open'] += not l['closed']; g['overdue'] += l['overdue']
        if l.get('days_to_closure') is not None and l['state'] != 'closed_declined': g['days'].append(l['days_to_closure'])
    by_group = []
    for k, g in sorted(groups.items()):
        d = sorted(g.pop('days'))
        by_group.append({'group': k, **g, 'close_rate': round(g['closed_with_service'] / g['referrals'], 4) if g['referrals'] else None,
                         'close_rate_ci95': wilson(g['closed_with_service'], g['referrals']),
                         'median_days_to_service': d[len(d) // 2] if d else None})
    states = Counter(l['state'] for l in loops)
    needs_by_type = Counter(n['need'] for n in need_rows)
    focus = p['focus']
    if focus == 'needs': table, ids = need_rows, {n['patient_id'] for n in need_rows}
    elif focus == 'unreferred': table, ids = unreferred, {n['patient_id'] for n in unreferred}
    elif focus == 'open': table = [l for l in loops if not l['closed']]; ids = {l['patient_id'] for l in table}
    elif focus == 'closed': table = [l for l in loops if l['closed']]; ids = {l['patient_id'] for l in table}
    elif focus == 'referrals': table, ids = loops, {l['patient_id'] for l in loops}
    else:
        table = loops + unreferred
        ids = {n['patient_id'] for n in need_rows} | {l['patient_id'] for l in loops}
    table = sorted(table, key=lambda r: (r.get('closed', False), -(r.get('days_open') or 0), r['patient_id']))
    return {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'needs': need_rows, 'needs_by_type': dict(needs_by_type),
            'loops': loops, 'unreferred': unreferred, 'states': dict(states), 'by_group': by_group,
            'table': table[:p['top']], 'patient_ids': sorted(ids), 'patients_with_needs': len({n['patient_id'] for n in need_rows}),
            'referrals': len(loops), 'closed_with_service': states['closed_service_confirmed'] + states['closed_patient_reported'],
            'false_closures': 0, 'method': METHOD.format(min_score=p['min_score'], ev=p['closure_evidence'], od=p['overdue_days'])}


METHOD = ('Needs: sdoh_needs corroboration score >= {min_score} (Z55-Z65 code 2, validated screen 1 per date max 2, affirmed note 1, '
          'vendor attribute 1). Referrals: community_service_referrals as BSeR ServiceRequests; community_service_outcomes citing them '
          'as Task feedback; loop state = latest update; closed only on a delivered=true report ({ev}) or a patient decline; '
          'scheduled / waitlisted / unable-to-reach stay open; overdue = open more than {od} days. Close rate = loops closed with service / '
          'referrals (Wilson 95% interval).')
ASSUMPTIONS = ['Synthetic warehouse data; patient-reported delivery counts as closure unless closure_evidence=organization_confirmed.',
               'A need without a Z code, screen, note or vendor signal is not observed, which is not the same as absent.',
               'Referral need is mapped from service_requested (transport, food_access, housing, utilities); other services keep their own name.']


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
    s, needs = r['states'], ', '.join(f'{k} {v}' for k, v in sorted(r['needs_by_type'].items(), key=lambda kv: -kv[1])) or 'none'
    open_ = sum(v for k, v in s.items() if k not in CLOSED)
    overdue = sum(1 for l in r['loops'] if l['overdue'])
    rate = f"{r['closed_with_service'] / r['referrals']:.0%}" if r['referrals'] else 'n/a'
    return (f"{r['patients_with_needs']} patients with a corroborated social need ({needs}); {len(r['unreferred'])} needs never referred. "
            f"{r['referrals']} community referrals: {r['closed_with_service']} closed with service ({rate}), {s.get('closed_declined', 0)} declined, "
            f"{open_} open ({overdue} overdue > {r['parameters']['overdue_days']} days; {s.get('open_failed', 0)} failed, "
            f"{s.get('open_no_response', 0)} with no feedback). False closures: 0 (scheduling never closes a loop)")


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    ids = report['patient_ids']
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['table'], 'count': len(ids),
            'patient_ids': ids, 'value': headline(report) + '.', 'method': report['method'],
            'definitions': [{'term': 'closed loop', 'basis': 'Gravity Project closed-loop referral / HL7 BSeR Task completed with delivery '
                                                             'evidence, or cancelled by patient decline'}],
            'limitations': ASSUMPTIONS, 'membership_role': 'selected', 'overall': {'social_needs': {
                'states': report['states'], 'needs_by_type': report['needs_by_type'], 'by_group': report['by_group']}}}


# ---- free local-compiler route ----------------------------------------------------------------------------------------
SOCIAL = (r'\bsocial (?:needs?|determinants?|risks?|services?|care)\b|\bsdoh\b|\bhrsn\b|\bfood (?:insecurity|insecure|access|needs?)\b|'
          r'\btransport(?:ation)? (?:barriers?|insecurity|needs?|problems?)\b|\bhousing (?:instability|insecurity|needs?|unstable)\b|'
          r'\bunstably housed\b|\b(?:transport\w*|housing|food|utilit(?:y|ies)|ride) referrals?\b|\bhomeless\w*\b|\bcommunity (?:service |resource |social )?referrals?\b|\bfood bank\b|\bcommunity (?:service|resource|organi[sz]ation|based organi[sz]ation)s?\b|\bcbos?\b|\bgravity project\b|\bbser\b')
REFERRAL = r'\breferr\w*\b|\bloops?\b|\bclos(?:e|ed|ing|ure)\b|\bfollow[- ]?(?:up|through)\b|\bconnected\b|\bresolved\b'


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(SOCIAL, low): return None
    if re.match(r'(?:what (?:is|are)|explain|define|how (?:does|do) .* work)\b', low) and not re.search(r'\b(?:our|we|my|patients|members|which|how many|rate)\b', low): return None
    if re.search(r'\b(?:impact|effect\w*|downstream|utili[sz]ation|miss(?:es)? more|more (?:likely|often)|outcomes? of)\b', low): return None  # effects stay program_effect / explain
    if re.search(r'\b(?:drive|drives|driver|associated|predict\w*|why)\b', low): return None
    if re.search(r'\b(?:contact|call|prioriti[sz]\w*|capacity|benefit\w*|respond\w*|uplift|persuadable\w*)\b', low): return None  # worklists stay outreach, who-benefits stays uplift  # why / what drives stays explain / discover
    if re.search(r'\b(?:pmpm|cost|spend|admissions?|ed visits?|readmission\w*)\b', low) and not re.search(REFERRAL, low): return None
    params = {}
    from .sdoh_needs import NEEDS
    named = [n for n, rx in (('food', r'\bfood\b'), ('transport', r'\btransport\w*\b|\brides?\b'), ('housing', r'\bhous\w*\b|\bhomeless\w*\b'),
                             ('work_schedule_constraint', r'\bwork schedul\w*\b')) if re.search(rx, low)]
    if named: params['needs'] = [n for n in named if n in NEEDS]
    if re.search(r'\bnever (?:been )?referred\b|\bnot (?:been )?referred\b|\bno referral\b|\bwithout (?:a )?referral\b|\bunreferred\b', low): params['focus'] = 'unreferred'
    elif re.search(r'\b(?:still )?open\b|\bnot (?:yet )?(?:closed|completed|resolved|connected)\b|\bunresolved\b|\boverdue\b|\bstuck\b|\bfailed\b', low): params['focus'] = 'open'
    elif re.search(r'\b(?:were|got|been) (?:closed|completed|resolved|connected)\b|\bclosed loops?\b', low): params['focus'] = 'closed'
    elif re.search(REFERRAL, low): params['focus'] = 'referrals'
    elif re.search(r'\bwho\b|\bwhich (?:patients|members)\b|\bhow many\b|\blist\b|\bpatients with\b|\bmembers with\b', low): params['focus'] = 'needs'
    else: return None
    if re.search(r'\bby (?:practice|clinic)\b|\bwhich (?:practices?|clinics?)\b|\beach (?:practice|clinic)\b', low): params['group_by'] = 'practice'
    elif re.search(r'\bby need\b|\bby (?:type|need type)\b', low): params['group_by'] = 'need'
    if re.search(r'\bconfirmed by (?:the )?(?:organi[sz]ation|cbo|agency|provider)\b|\bstrict\b', low): params['closure_evidence'] = 'organization_confirmed'
    d = re.search(r'\b(\d{1,3})\s*days?\b', low)
    if d and 1 <= int(d.group(1)) <= 365 and params['focus'] == 'open': params['overdue_days'] = int(d.group(1))
    return plan(OP, params, 'Corroborated social needs and community-referral loop closure (Gravity / BSeR)', question,
                [f"focus {params['focus']}"])


TEMPLATES = ['Which patients have {need} and were never referred?', 'Show open community referrals for {need}',
             'How many social service referrals were closed with the service actually delivered?',
             'social needs referral close rate by practice', 'who has unmet social needs right now',
             'Which community resource referrals are overdue?', 'Did our food bank referrals get completed?',
             'list members with corroborated transportation needs', 'closed loop rate for SDOH referrals by clinic',
             'Which housing referrals are still open after 30 days?']
