"""340B eligibility: which prescriptions / dispensed fills qualify as 340B-eligible for a covered entity, and why not.

HRSA's patient definition (61 FR 55156, 1996 guidelines) has three tests plus the Medicaid duplicate-discount rule; each is a gate here:
  1 entity   the prescription was written in an organization of the covered entity (params.entity: organization ids, NPIs or name text;
             default = hospital organizations, the DSH/children's/CAH covered-entity shape)
  2 relation the patient has an encounter with that organization within `lookback_days` before the order (entity keeps the patient's record)
  3 dispensed  the order has a paid, non-reversed pharmacy fill (eligibility is decided at dispensing; unfilled orders are shown separately)
  4 outpatient not every fill is dispensed during an inpatient / observation stay (inpatient-administered drugs are bundled; discharge
               prescriptions filled after discharge remain eligible)
  5 medicaid  fills paid by a Medicaid payer are held out unless params.medicaid_carve_in is true (duplicate-discount risk: 42 U.S.C. 256b(a)(5)(A))
Gates run in order; each order reports the first one it fails. Savings are an estimate: eligible plan-paid + patient-pay spend x discount (params.discount, default 0.25; actual ceiling price = AMP - URA is unknown).
"""
from collections import defaultdict
from datetime import date, timedelta
import re

from . import revenue_common as rc

VERSION = 'eligibility-340b/1'
OP = 'eligibility_340b'
VIEWS = ('summary', 'prescriptions', 'patients', 'exceptions')

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': '340B ELIGIBILITY: per prescription/fill HRSA three-test patient eligibility (entity, relationship, outpatient), dispensing and Medicaid '
                  'duplicate-discount exclusion, with the failed test named, eligible spend and estimated savings.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'entity': [], 'period_months': 12, 'lookback_days': 365, 'medicaid_carve_in': False, 'discount': 0.25, 'view': 'summary|prescriptions|patients|exceptions', 'top': 25},
            'meaning': 'Orders in the period gated by covered-entity site, prior-encounter relationship, outpatient setting, paid fill and Medicaid exclusion; '
                       'eligible fills, spend and estimated 340B savings; the first failed gate for each ineligible order.'}
NATIVE = ('value',)


def schema(obj, string, array):
    return obj({'entity': array({'type': 'string'}, 'Covered-entity organization ids, NPIs or name text; empty = hospital organizations; * = every organization'),
                'period_months': {'type': 'integer', 'minimum': 1, 'maximum': 60}, 'lookback_days': {'type': 'integer', 'minimum': 0, 'maximum': 1825},
                'medicaid_carve_in': {'type': 'boolean'}, 'discount': {'type': 'number', 'minimum': 0, 'maximum': 0.9}, 'view': string(VIEWS),
                'top': {'type': 'integer', 'minimum': 1, 'maximum': 500}})


def validate(p):
    allowed = {'entity', 'period_months', 'lookback_days', 'medicaid_carve_in', 'discount', 'view', 'top'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('eligibility_340b parameters: ' + ', '.join(sorted(allowed)))
    out = {'entity': sorted({str(x).strip() for x in p.get('entity') or [] if str(x).strip()}), 'period_months': p.get('period_months', 12),
           'lookback_days': p.get('lookback_days', 365), 'medicaid_carve_in': bool(p.get('medicaid_carve_in', False)),
           'discount': float(p.get('discount', 0.25)), 'view': p.get('view', 'summary'), 'top': p.get('top', 25)}
    for k, lo, hi in (('period_months', 1, 60), ('lookback_days', 0, 1825), ('top', 1, 500)):
        if type(out[k]) is not int or not lo <= out[k] <= hi: raise ValueError(f'{k}: {lo}..{hi}')
    if not 0 <= out['discount'] <= 0.9: raise ValueError('discount: 0..0.9')
    if out['view'] not in VIEWS: raise ValueError(f'view: {VIEWS}')
    return out


def entity_orgs(db, p):
    orgs = {r[0]: (r[1], r[2], r[3]) for r in db.execute('SELECT organization_id, npi, display_name, organization_type FROM organization')}
    if not p['entity']:
        return {o for o, v in orgs.items() if v[2] == 'hospital'}, 'hospital organizations (no covered entity named)'
    if p['entity'] == ['*']: return set(orgs), 'every organization in the warehouse (system-wide upper bound)'
    keys = [e.lower() for e in p['entity']]
    sel = {o for o, v in orgs.items() if any(k in (o.lower(), str(v[0] or '')) or k in str(v[1] or '').lower() for k in keys)}
    return sel, 'named covered entity: ' + ', '.join(p['entity'])


def run(db, patient_ids, p, as_of):
    start, end = rc.window(as_of, p['period_months'])
    keep = set(patient_ids) if patient_ids is not None else None
    sites, site_basis = entity_orgs(db, p)
    inpatient = defaultdict(list)  # patient -> [(admit, discharge)]
    for pid, a, d in db.execute("SELECT patient_id, admit_date, COALESCE(discharge_date, admit_date) FROM stay WHERE lower(type) LIKE 'inpatient%' OR lower(type) = 'observation'"):
        inpatient[pid].append((str(a)[:10], str(d)[:10]))
    visit = {}  # encounter -> (setting, date, org)
    rel = defaultdict(list)  # (patient, org) -> visit dates
    for eid, pid, setting, vt, d, org in db.execute('SELECT encounter_id, patient_id, setting, type, event_date, organization_id FROM visit'):
        visit[eid] = (str(setting or '').lower(), str(vt or '').lower(), str(d)[:10], org)
        if d and org in sites: rel[(pid, org)].append(str(d)[:10])
    fills = defaultdict(list)  # order record -> paid fill rows
    pc_cols = rc.columns(db, 'payer_pharmacy_claim')
    for order, rec, fd, status, pay, ppay, payer, pharm in db.execute(
            "SELECT f.order_record_id, c.record_id, c.fill_date, c.claim_status, COALESCE(c.plan_paid_cents,0), COALESCE(c.patient_pay_cents,0), c.payer_id, c.pharmacy_name "
            "FROM prescription_fill f JOIN payer_pharmacy_claim c ON c.record_id = f.fill_record_id" + (' WHERE c.counts_as_fill = 1' if 'counts_as_fill' in pc_cols else '')):
        if status == 'paid': fills[order].append((rec, str(fd)[:10], (pay + ppay) / 100, payer, pharm))
    medicaid_payers = {r[0] for r in db.execute("SELECT DISTINCT payer_id FROM payer_claim WHERE line_of_business IN ('medicaid') OR product = 'medicaid'")}
    pt_mcd = {r[0] for r in db.execute("SELECT patient_id FROM patient WHERE lower(COALESCE(line_of_business,'')) = 'medicaid' OR lower(COALESCE(coverage_type,'')) LIKE '%medicaid%'")}
    rows, gates = [], defaultdict(int)
    for rec, pid, org, eid, od, drug, rxcui in db.execute('SELECT record_id, patient_id, organization_id, encounter_id, ordered_date, drug_name, rxcui FROM prescription WHERE ordered_date BETWEEN ? AND ?', (start, end)):
        if keep is not None and pid not in keep: continue
        od = str(od)[:10]
        gate = None
        if org not in sites: gate = '1_entity: ordered outside the covered entity'
        else:
            lo = (date.fromisoformat(od) - timedelta(days=p['lookback_days'])).isoformat()
            prior = [d for d in rel.get((pid, org), []) if lo <= d <= od]
            v = visit.get(eid)
            if not prior and not (v and v[3] in sites): gate = '2_relationship: no encounter with the entity before the order'
        fl = fills.get(rec, [])
        if gate is None and not fl: gate = '3_dispensed: no paid fill'
        if gate is None and all(any(a <= f[1] <= d for a, d in inpatient.get(pid, [])) for f in fl):
            gate = '4_outpatient: every fill dispensed during an inpatient/observation stay'
        mcd = bool(fl) and (pid in pt_mcd or any(f[3] in medicaid_payers for f in fl))
        if gate is None and mcd and not p['medicaid_carve_in']: gate = '5_medicaid: duplicate-discount exclusion'
        spend = sum(f[2] for f in fl)
        gates[gate or 'eligible'] += 1
        rows.append({'record_id': rec, 'patient_id': pid, 'organization_id': org, 'ordered': od, 'drug': drug, 'rxcui': rxcui, 'fills': len(fl),
                     'spend': round(spend, 2), 'status': 'eligible' if gate is None else 'ineligible', 'failed_gate': gate, 'medicaid': mcd})
    elig = [r for r in rows if r['status'] == 'eligible']
    spend = sum(r['spend'] for r in elig)
    by_pt = defaultdict(lambda: {'orders': 0, 'spend': 0.0})
    for r in elig: by_pt[r['patient_id']]['orders'] += 1; by_pt[r['patient_id']]['spend'] += r['spend']
    patients = [{'patient_id': k, 'eligible_orders': v['orders'], 'spend': round(v['spend'], 2), 'est_savings': round(v['spend'] * p['discount'], 2)}
                for k, v in sorted(by_pt.items(), key=lambda kv: -kv[1]['spend'])]
    drugs = defaultdict(lambda: [0, 0.0])
    for r in elig: drugs[r['drug']][0] += 1; drugs[r['drug']][1] += r['spend']
    assumptions = [f'Synthetic warehouse; orders written {start} to {end}; covered entity = {site_basis}.',
                   'No 340B registry (OPAIS) or child-site list is received: entity membership is by organization, not by registered outpatient department; '
                   'supply the entity parameter to test a specific site list.',
                   'Eligibility is evaluated on orders linked to paid payer pharmacy fills; fills with no linked order and dispenses with no claim are not counted.',
                   'Contract-pharmacy and in-house dispensing are not distinguished (the fill is accepted wherever dispensed); '
                   + ('Medicaid fills included (carve-in).' if p['medicaid_carve_in'] else 'Medicaid fills excluded because the Medicaid Exclusion File is not received.'),
                   f"Estimated savings use a flat {p['discount']:.0%} discount on plan-paid plus patient-pay spend; the true ceiling price (AMP less URA) is not received.",
                   'Modifier compliance (JG/TB on Medicaid FFS and Medicare Part B claims) cannot be checked: line modifiers are not received.']
    return {'schema': VERSION, 'as_of': str(as_of)[:10], 'parameters': p, 'period': {'start': start, 'end': end}, 'entity_sites': len(sites),
            'orders': len(rows), 'eligible_orders': len(elig), 'eligible_spend': round(spend, 2), 'est_savings': round(spend * p['discount'], 2),
            'gates': dict(sorted(gates.items())), 'patients': patients[:500], 'rows': rows[:5000],
            'top_drugs': [{'drug': d, 'orders': v[0], 'spend': round(v[1], 2)} for d, v in sorted(drugs.items(), key=lambda kv: -kv[1][1])[:20]],
            'assumptions': assumptions,
            'method': 'HRSA three-test 340B patient definition as ordered gates (entity, relationship, outpatient) plus dispensed and Medicaid duplicate-discount screens on order-linked paid fills.'}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    with rc.connect(path) as db:
        return run(db, patient_ids, p, str(as_of)[:10])


def headline(r):
    if not r['orders']: return 'No prescriptions in the period'
    g = {k: v for k, v in r['gates'].items() if k != 'eligible'}
    top = max(g.items(), key=lambda kv: kv[1]) if g else None
    return (f"{r['eligible_orders']} of {r['orders']} orders are 340B-eligible ({r['eligible_orders'] / r['orders']:.1%}), {rc.money(r['eligible_spend'])} spend, "
            f"est. savings {rc.money(r['est_savings'])} at {r['parameters']['discount']:.0%}" + (f"; most common failed gate: {top[0]} ({top[1]})" if top else ''))


def answer(report, plan, question):
    v = report['parameters']['view']; n = report['parameters']['top']
    if v == 'prescriptions': table = [r for r in report['rows'] if r['status'] == 'eligible'][:n]
    elif v == 'patients': table = report['patients'][:n]
    elif v == 'exceptions': table = [r for r in report['rows'] if r['status'] == 'ineligible' and r['fills']][:n]
    else: table = [{'result': k, 'orders': c} for k, c in report['gates'].items()]
    ids = [x['patient_id'] for x in report['patients']] if v == 'patients' else ()
    return rc.answer(report, plan, value=headline(report) + '.', table=table, patient_ids=ids, number=report['eligible_orders'],
                     definitions=[{'term': '340B eligible patient', 'basis': 'HRSA 1996 patient definition (61 FR 55156): relationship, entity-provided service, entity-responsible care'},
                                  {'term': 'duplicate discount', 'basis': '42 U.S.C. 256b(a)(5)(A)'}])


B_RX = r'\b340 ?b\b'


def route(question, profile, as_of):
    from .engine_suite import plan
    q = rc.low(question)
    if not re.search(B_RX, q) or rc.knowledge_only(q): return None
    params = {}
    if re.search(r'\bmedicaid\b.{0,30}\b(?:carve[- ]in|included?)\b|\bwith medicaid\b', q): params['medicaid_carve_in'] = True
    if re.search(r'\b(?:which|list|show)\b.{0,30}\b(?:patients|members)\b', q): params['view'] = 'patients'
    elif re.search(r'\bduplicate[- ]discount\b|\bexclu\w+\b|\bnot eligible\b|\bineligible\b|\bexceptions?\b|\bcompliance\b', q): params['view'] = 'exceptions'
    elif re.search(r'\b(?:prescriptions?|scripts?|fills?|orders?)\b', q) and re.search(r'\b(?:which|list|show)\b', q): params['view'] = 'prescriptions'
    m = re.search(r'\b(wh-hospital-\d+)\b', q)
    if m: params['entity'] = [m.group(1).upper()]
    elif re.search(r'\bsystem[- ]?wide\b|\ball (?:sites|clinics|practices|organizations)\b|\bevery (?:site|clinic|practice)\b', q): params['entity'] = ['*']
    m = re.search(r'\b(\d{1,2})\s?%\s?discount\b', q)
    if m: params['discount'] = int(m.group(1)) / 100
    return plan(OP, params, '340B patient/prescription eligibility with the failed HRSA gate and estimated savings', question)


TEMPLATES = ['Which prescriptions are 340B eligible?', 'System-wide, what share of our prescriptions qualify for 340B?', 'What percent of our outpatient prescriptions qualify for 340B?', 'How much 340B savings are we missing?',
             'Which patients qualify for 340B at WH-HOSPITAL-0?', 'Show 340B duplicate discount exceptions for Medicaid patients',
             '340B eligibility with Medicaid carve-in at a 30% discount', 'Why are some of our fills not 340B eligible?', 'List 340B eligible patients by spend']
