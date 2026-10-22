"""Prior authorization and medication-access adapter: pharmacy claims rejected for prior authorization (NCPDP 75) or non-coverage
(70) become access cases with an outcome and the next action step: submit the PA packet, appeal a lapsed denial, request a
formulary exception or therapeutic alternative, or screen a Medicare member for Extra Help (LIS) when cost is the barrier.

Outcome per case, from later claims for the same member and drug (NDC or drug name) inside resolution_days: resolved_paid (a paid
claim, with days to resolution), open (still inside the window, no paid claim yet), lapsed (window over, never paid: appeal path).
A rejection is never treated as resolved by a reversal, a refill-too-soon reject or a different drug. Extra Help candidates: Medicare
members with no dual status on their enrollment span who hit a PA / non-coverage reject or have a documented cost barrier
(therapy_barrier cost_access). Synthetic warehouse data; actions are proposals, none marked done without a receipt.
"""
from collections import Counter, defaultdict
import re

from . import care_actions as CA

VERSION = 'prior-auth-access/1'
OP = 'prior_auth_access'
FOCUS = ('rejected_prior_auth', 'rejected_not_covered', 'medicare_extra_help', 'all')
STATE = ('open', 'lapsed', 'resolved_paid')
GROUP_BY = ('none', 'payer', 'drug', 'state')
REJECT = {'75': 'prior_authorization_required', '70': 'product_not_covered'}
PRODUCT = {'population': 'optional', 'view': 'loops',
           'use': 'PRIOR AUTHORIZATION and MEDICATION ACCESS: pharmacy claims rejected for prior authorization or non-coverage, which are '
                  'still unresolved, how long approvals took, denials that lapsed and need an appeal, formulary exceptions, and Medicare '
                  'members to screen for Extra Help (LIS) when cost blocks a medication.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'focus': 'rejected_prior_auth|rejected_not_covered|medicare_extra_help|all', 'state': 'open|lapsed|resolved_paid', 'resolution_days': 60,
                           'group_by': 'none|payer|drug|state', 'top': 200},
            'meaning': 'Rejected pharmacy claims (code 75 prior authorization, 70 not covered) as access cases: resolved_paid when a paid claim '
                       'for the same member and drug follows within resolution_days (with days to resolution), open inside the window, lapsed '
                       'after it. Each unresolved case gets a proposed step (PA packet, appeal, formulary exception); Medicare members with no '
                       'dual status and a PA / non-coverage reject or documented cost barrier get an Extra Help screen.'}
NATIVE = ('count', 'value')
ASSUMPTIONS = ['Synthetic warehouse data; resolution means a later paid claim for the same member and drug, not a recorded PA decision (none is stored).',
               'Extra Help candidacy is a screen from line of business and dual status, not an income determination.',
               'Actions are proposals; none is marked done without a counterparty receipt.']


def schema(obj, string, array):
    return obj({'focus': string(FOCUS, 'rejected_prior_auth: code 75; rejected_not_covered: code 70; medicare_extra_help: Medicare screen candidates; all'),
                'state': string(STATE + ('',), 'Only cases in this state; empty = any'),
                'resolution_days': {'type': 'integer', 'minimum': 7, 'maximum': 365}, 'group_by': string(GROUP_BY),
                'top': {'type': 'integer', 'minimum': 1, 'maximum': 5000}})


def validate(p):
    allowed = {'focus', 'state', 'resolution_days', 'group_by', 'top'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('prior_auth_access parameters: ' + ', '.join(sorted(allowed)))
    out = {'focus': p.get('focus', 'rejected_prior_auth'), 'state': p.get('state') or '', 'resolution_days': p.get('resolution_days', 60),
           'group_by': p.get('group_by', 'none'), 'top': p.get('top', 200)}
    if out['focus'] not in FOCUS: raise ValueError(f'focus: {FOCUS}')
    if out['state'] and out['state'] not in STATE: raise ValueError(f'state: {STATE}')
    if type(out['resolution_days']) is not int or not 7 <= out['resolution_days'] <= 365: raise ValueError('resolution_days 7..365')
    if out['group_by'] not in GROUP_BY: raise ValueError(f'group_by: {GROUP_BY}')
    if type(out['top']) is not int or not 1 <= out['top'] <= 5000: raise ValueError('top 1..5000')
    return out


def cases(db, keep, as_of, resolution_days):
    out = []
    rejected = db.execute("SELECT record_id, patient_id, fill_date, drug_name, ndc, reject_code, payer_id, prescriber_npi FROM payer_pharmacy_claim "
                          "WHERE claim_status='rejected' AND reject_code IN ('75','70') AND fill_date <= ?", (as_of,)).fetchall()
    pids = {r[1] for r in rejected if keep is None or r[1] in keep}
    paid = defaultdict(list)
    for pid, day, drug, ndc in db.execute("SELECT patient_id, fill_date, drug_name, ndc FROM payer_pharmacy_claim WHERE claim_status='paid' AND coalesce(counts_as_fill,1)=1 AND fill_date <= ?", (as_of,)):
        if pid in pids: paid[pid].append((day, drug, ndc))
    for rec, pid, day, drug, ndc, code, payer, npi in rejected:
        if keep is not None and pid not in keep: continue
        later = sorted(d for d, dr, nd in paid.get(pid, ()) if d >= day and (dr == drug or (ndc and nd == ndc)) and CA.days_between(day, d) <= resolution_days)
        age = CA.days_between(day, as_of)
        state = 'resolved_paid' if later else 'open' if age <= resolution_days else 'lapsed'
        out.append({'patient_id': pid, 'case': REJECT[code], 'reject_code': code, 'reject_date': day, 'drug': (drug or '')[:90], 'payer': payer or 'unknown',
                    'prescriber_npi': npi, 'state': state, 'days_to_resolution': CA.days_between(day, later[0]) if later else None,
                    'days_open': None if later else age, 'record_id': rec})
    return out


def next_step(c):
    if c['state'] == 'resolved_paid': return None
    if c['reject_code'] == '75':
        if c['state'] == 'open': return CA.step('submit the prior authorization packet (diagnosis, step-therapy history, labs) to the plan', f"prescriber {c['prescriber_npi']} / {c['payer']}", f"PA required, {c['days_open']} days with no paid claim")
        return CA.step('file an appeal / redetermination with the plan and offer a covered alternative while it is pending', f"{c['payer']} appeals", f"PA never resolved after {c['days_open']} days")
    if c['state'] == 'open': return CA.step('ask the prescriber for a covered alternative or request a formulary exception', f"prescriber {c['prescriber_npi']}", 'product not covered')
    return CA.step('request a formulary exception with a medical-necessity letter', f"{c['payer']} pharmacy benefit", f"not covered, {c['days_open']} days unresolved")


def extra_help(db, keep, as_of, hit_patients):
    t = CA.tables(db)
    out = []
    if 'enrollment_span' not in t: return out
    spans = {}
    for pid, lob, dual in db.execute("SELECT patient_id, line_of_business, dual_status FROM enrollment_span WHERE coverage_start <= ? AND (coverage_end IS NULL OR coverage_end >= ?)", (as_of, as_of)):
        spans[pid] = (lob, dual)
    cost = {}
    if 'therapy_barrier' in t:
        for pid, drug in db.execute("SELECT patient_id, drug_class FROM therapy_barrier WHERE barrier_type='cost_access' AND coalesce(negated,0)=0 AND coalesce(hypothetical,0)=0"):
            cost.setdefault(pid, drug)
    for pid in sorted(set(hit_patients) | set(cost)):
        if keep is not None and pid not in keep: continue
        lob, dual = spans.get(pid, (None, None))
        if lob != 'medicare' or dual: continue
        why = ['PA / non-coverage reject'] * (pid in hit_patients) + ['documented cost barrier' + (f' ({cost[pid]})' if cost.get(pid) else '')] * (pid in cost)
        out.append({'patient_id': pid, 'case': 'extra_help_screen', 'state': 'candidate', 'payer': 'medicare', 'drug': cost.get(pid, ''), 'basis': ' + '.join(why),
                    'next_action': CA.step('screen for Medicare Extra Help (LIS) and Medicare Savings Programs; help file the SSA application', 'benefits navigator', ' + '.join(why))})
    return out


def run(db, patient_ids, p, as_of):
    keep = set(patient_ids) if patient_ids is not None else None
    absent = CA.missing_tables(db, ('payer_pharmacy_claim',))
    if absent:
        return {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'unavailable': absent, 'cases': 0, 'table': [], 'patient_ids': [], 'states': {},
                'by_group': [], 'extra_help_candidates': 0, 'method': 'payer_pharmacy_claim absent: access cases cannot be computed'}
    allc = cases(db, keep, as_of, p['resolution_days'])
    for c in allc:
        s = next_step(c)
        if s: c['next_action'] = s
    pa = [c for c in allc if c['reject_code'] == '75']
    eh = extra_help(db, keep, as_of, {c['patient_id'] for c in allc if c['state'] != 'resolved_paid'}) if p['focus'] in ('medicare_extra_help', 'all') else []
    sel = {'rejected_prior_auth': pa, 'rejected_not_covered': [c for c in allc if c['reject_code'] == '70'], 'medicare_extra_help': eh, 'all': allc + eh}[p['focus']]
    if p['state']: sel = [c for c in sel if c['state'] == p['state']]
    key = lambda c: {'payer': c['payer'], 'drug': (c['drug'] or '')[:40], 'state': c['state']}.get(p['group_by'], 'all')
    g = defaultdict(Counter)
    for c in sel: g[key(c)][c['state']] += 1
    by_group = [{'group': k, 'cases': sum(v.values()), **{s: v[s] for s in (*STATE, 'candidate') if v[s]}} for k, v in sorted(g.items(), key=lambda kv: -sum(kv[1].values()))]
    days = sorted(c['days_to_resolution'] for c in sel if c.get('days_to_resolution') is not None)
    sel = sorted(sel, key=lambda c: (c['state'] == 'resolved_paid', -(c.get('days_open') or 0), c['patient_id']))
    queue = CA.work_queue(sel, lambda c: (10 if c['state'] == 'lapsed' else 5 if c['state'] == 'open' else 6) + min((c.get('days_open') or 0) / 30, 4), lambda c: c['payer'])
    return {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'work_queue': queue, 'cases': len(sel), 'states': dict(Counter(c['state'] for c in sel)), 'by_group': by_group,
            'median_days_to_resolution': days[len(days) // 2] if days else None, 'extra_help_candidates': len(eh), 'table': sel[:p['top']],
            'patient_ids': sorted({c['patient_id'] for c in sel}),
            'method': f"Rejected pharmacy claims (75 PA, 70 not covered) resolved by a later paid claim for the same member and drug within {p['resolution_days']} days."}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    db = CA.open_db(path)
    try:
        return run(db, patient_ids, p, str(as_of)[:10])
    finally:
        db.close()


def headline(r):
    if r.get('unavailable'): return 'Medication-access cases cannot be computed: ' + ', '.join(r['unavailable']) + ' absent from this warehouse'
    s = r['states']
    if r['parameters']['focus'] == 'medicare_extra_help': return f"{r['extra_help_candidates']} Medicare members with no dual status and a PA / non-coverage reject or documented cost barrier to screen for Extra Help"
    med = f", median {r['median_days_to_resolution']} days to a paid claim" if r['median_days_to_resolution'] is not None else ''
    eh = f"; {r['extra_help_candidates']} Medicare members to screen for Extra Help" if r['extra_help_candidates'] else ''
    return (f"{r['cases']} {r['parameters']['focus'].replace('_', ' ')} cases: {s.get('resolved_paid', 0)} resolved with a paid claim{med}, "
            f"{s.get('open', 0)} open, {s.get('lapsed', 0)} lapsed (appeal path){eh}")


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements') or bool(report.get('unavailable'))
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['table'], 'count': len(report['patient_ids']),
            'patient_ids': report['patient_ids'], 'value': headline(report) + '.', 'method': report['method'],
            'definitions': [{'term': 'resolved', 'basis': 'a paid pharmacy claim for the same member and drug after the reject, within the resolution window'}],
            'limitations': ASSUMPTIONS, 'membership_role': 'selected', 'overall': {'prior_auth_access': {'states': report['states'], 'by_group': report['by_group'], 'work_queue': report.get('work_queue')}}}


PA = (r'\bprior auth\w*\b|\bprior-auth\w*\b|\bpa (?:denials?|requests?|packets?|rejects?|required)\b|\bformulary exceptions?\b|\bextra help\b|\blis\b|\blow[- ]income subsid\w*\b|'
      r'\bappeals?\b.*\b(?:denials?|claims?|drugs?|medications?|rx)\b|\bmedication access\b|\bnot covered\b.*\b(?:drug|medication|rx|claims?)\b|\b(?:rx|pharmacy) rejects?\b|\brejected pharmacy claims?\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(PA, low): return None
    if re.match(r'(?:what (?:is|are)|explain|define|how (?:do|does))\b', low) and not re.search(r'\b(?:our|we|my|patients|members|which|how many)\b', low): return None
    if re.search(r'\b(?:cost|spend|pmpm|why|drive[sn]?|predict\w*|readmission\w*)\b', low) and not re.search(r'\bextra help\b', low): return None
    params = {}
    if re.search(r'\bextra help\b|\blis\b|\blow[- ]income subsid\w*\b', low): params['focus'] = 'medicare_extra_help'
    elif re.search(r'\bnot covered\b|\bformulary exceptions?\b|\bnon-?coverage\b', low) and not re.search(r'\bprior auth', low): params['focus'] = 'rejected_not_covered'
    elif re.search(r'\bmedication access\b|\ball (?:access|rejects?)\b', low): params['focus'] = 'all'
    else: params['focus'] = 'rejected_prior_auth'
    if re.search(r'\bstill (?:open|pending|unresolved)\b|\bpending\b|\bunresolved\b|\bwaiting\b|\bopen\b', low): params['state'] = 'open'
    elif re.search(r'\blapsed\b|\bneed(?:s)? (?:an? )?appeal\b|\bappeal\b|\bnever (?:resolved|approved)\b|\bstuck\b', low): params['state'] = 'lapsed'
    elif re.search(r'\bapproved\b|\bresolved\b|\bgot (?:the|their) (?:drug|medication)\b', low): params['state'] = 'resolved_paid'
    if re.search(r'\bby (?:payer|plan)\b|\bwhich (?:payers?|plans?)\b', low): params['group_by'] = 'payer'
    elif re.search(r'\bby (?:drug|medication)\b|\bwhich (?:drugs?|medications?)\b', low): params['group_by'] = 'drug'
    params.setdefault('group_by', 'none')
    return plan(OP, params, 'Prior authorization and medication-access cases', question, [f"focus {params['focus']}"])


TEMPLATES = ['Which prior authorization requests are still unresolved?', 'how many PA denials never got approved and need an appeal',
             'prior auth rejects by payer', 'which Medicare members should we screen for Extra Help', 'list rejected pharmacy claims not covered by the plan',
             'how long do prior authorizations take to resolve', 'formulary exception candidates', 'medication access summary by drug',
             'pharmacy PA rejects by plan', 'which drugs get rejected at the pharmacy for missing prior auth', 'reject code 75 counts by payer']
