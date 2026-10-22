"""Prior-authorization rules engine ('prior_auth_rules' operation).

Evaluates prior-authorization (PA) rules against the synthetic claims and answers the utilization-management questions:
which services need PA under which product, where auth denials concentrate, which rules each payer appears to enforce,
which providers would qualify for gold-carding and which services are auto-approval candidates.

Rule table (RULES, each rule cited; `rules` parameter adds client rules in the same shape):
  regulation / CMS program (Medicare FFS):
    - Hospital OPD services requiring PA, 42 CFR 419.83(a) and the CMS OPD "List of Outpatient Services That Require Prior
      Authorization" (blepharoplasty, botulinum toxin, panniculectomy, rhinoplasty, vein ablation from 2020; cervical fusion with
      disc removal and implanted spinal neurostimulators from 2021; facet joint interventions from 2023).
    - DMEPOS Required Prior Authorization List, 42 CFR 414.234 (power mobility devices, pressure-reducing support surfaces,
      lower-limb prostheses, selected orthoses).
    - Repetitive scheduled non-emergent ambulance transport, SSA 1834(l)(16) (A0426 / A0428).
  typical payer policy (MA, Medicaid managed care, commercial, marketplace; source kind 'typical_payer_policy', not a
  specific payer's list): advanced imaging (CT / MR / PET / nuclear cardiology), sleep studies, specialty Part B drugs
  (chemotherapy J9xxx, biosimilars Q51xx). MA PA may only confirm diagnosis/medical criteria (42 CFR 422.138(c)); decision
  timeframes 72 hours expedited / 7 calendar days standard from 2026 (CMS-0057-F, 42 CFR 422.568, 422.572).
Auth denials: the FIRST adjudication's CARC is one of 15 (authorization number missing/invalid), 39 (denied at the time
authorization was requested), 62 (absent or exceeded pre-certification), 197 (precertification/authorization absent),
198 (precertification exceeded), 243 (services not authorized by network/primary care providers), 288 (referral absent).
Learned rules: payer x claim type x place-of-service cells whose auth-denial rate is significantly above the payer's own
rate (binomial upper tail, Benjamini-Hochberg q < 0.05, lift >= 2, at least min_claims claims): "this payer appears to
require authorization for this service setting".
Gold card (Texas HB 3459 / Tex. Ins. Code 4201.653 style): a provider is exempt when >= 90% of its PA requests were approved
over the evaluation period with at least min_requests requests. PA requests are not received in the warehouse: the
proxy denominator is the provider's claims in PA-relevant settings (curated-rule hits or cells where that payer has issued
auth denials) and approval = 1 - auth-denied share. Auto-approval candidates: rule-matched services whose claims were
approved at >= auto_approve_rate with enough volume (code-level only where the payer returned service lines).
"""
from collections import Counter, defaultdict
import math
import re

from scipy.stats import binom

from . import revenue_common as rc

VERSION = 'prior-auth-rules/1'
OP = 'prior_auth_rules'
AUTH_CARCS = {'15', '39', '62', '197', '198', '243', '288'}
FFS = ('medicare_ffs', 'dual_ffs')
MANAGED = ('medicare_advantage', 'dual_ma', 'medicaid', 'commercial', 'marketplace')
C_OPD = '42 CFR 419.83(a); CMS OPD Prior Authorization, List of Outpatient Services That Require Prior Authorization'
C_DME = '42 CFR 414.234; CMS DMEPOS Required Prior Authorization List'
C_AMB = 'SSA 1834(l)(16); CMS Repetitive Scheduled Non-Emergent Ambulance Transport prior authorization model'
C_TYP = ('Typical MA / managed-care / commercial utilization-management policy (radiology/specialty-drug benefit management); '
         'MA limits: 42 CFR 422.138(c); timeframes CMS-0057-F (42 CFR 422.568, 422.572)')
RULES = [
    {'id': 'opd_blepharoplasty', 'label': 'Blepharoplasty, eyelid surgery, brow lift (OPD)', 'codes': ['15820-15823', '67900-67908', '67911', '67950'],
     'products': FFS, 'kind': 'regulation', 'citation': C_OPD, 'effective': '2020-07-01'},
    {'id': 'opd_botulinum', 'label': 'Botulinum toxin injections (OPD)', 'codes': ['64612', '64615-64617', '64642-64647', 'J0585-J0588'],
     'products': FFS, 'kind': 'regulation', 'citation': C_OPD, 'effective': '2020-07-01'},
    {'id': 'opd_panniculectomy', 'label': 'Panniculectomy, excision of excess skin (OPD)', 'codes': ['15830', '15847', '15877'],
     'products': FFS, 'kind': 'regulation', 'citation': C_OPD, 'effective': '2020-07-01'},
    {'id': 'opd_rhinoplasty', 'label': 'Rhinoplasty (OPD)', 'codes': ['30400-30420', '30430-30435', '30450', '30460-30465', '30520'],
     'products': FFS, 'kind': 'regulation', 'citation': C_OPD, 'effective': '2020-07-01'},
    {'id': 'opd_vein_ablation', 'label': 'Vein ablation (OPD)', 'codes': ['36473-36479', '36482-36483'],
     'products': FFS, 'kind': 'regulation', 'citation': C_OPD, 'effective': '2020-07-01'},
    {'id': 'opd_cervical_fusion', 'label': 'Cervical fusion with disc removal (OPD)', 'codes': ['22551-22552'],
     'products': FFS, 'kind': 'regulation', 'citation': C_OPD, 'effective': '2021-07-01'},
    {'id': 'opd_neurostimulator', 'label': 'Implanted spinal neurostimulators (OPD)', 'codes': ['63650', '63685', '63688'],
     'products': FFS, 'kind': 'regulation', 'citation': C_OPD, 'effective': '2021-07-01'},
    {'id': 'opd_facet', 'label': 'Facet joint interventions (OPD)', 'codes': ['64490-64495', '64633-64636'],
     'products': FFS, 'kind': 'regulation', 'citation': C_OPD, 'effective': '2023-07-01'},
    {'id': 'dme_power_mobility', 'label': 'Power mobility devices (DMEPOS)', 'codes': ['K0813-K0829', 'K0835-K0843', 'K0848-K0864'],
     'products': FFS, 'kind': 'regulation', 'citation': C_DME, 'effective': '2019-07-22'},
    {'id': 'dme_support_surfaces', 'label': 'Pressure-reducing support surfaces (DMEPOS)', 'codes': ['E0193', 'E0277', 'E0371-E0373'],
     'products': FFS, 'kind': 'regulation', 'citation': C_DME, 'effective': '2019-07-22'},
    {'id': 'dme_prostheses_orthoses', 'label': 'Lower-limb prostheses and selected orthoses (DMEPOS)',
     'codes': ['L5856-L5858', 'L5973', 'L5980', 'L5987', 'L0648', 'L0650', 'L1832-L1833', 'L1851'],
     'products': FFS, 'kind': 'regulation', 'citation': C_DME, 'effective': '2022-04-13'},
    {'id': 'rsnat_ambulance', 'label': 'Repetitive scheduled non-emergent ambulance transport', 'codes': ['A0426', 'A0428'],
     'products': FFS, 'kind': 'cms_program', 'citation': C_AMB, 'effective': '2022-08-01'},
    {'id': 'typ_ct', 'label': 'Advanced imaging: CT', 'codes': ['70450-70498', '71250-71275', '72125-72133', '72191-72194', '73200-73206',
                                                               '73700-73706', '74150-74178', '74261-74263'],
     'products': MANAGED, 'kind': 'typical_payer_policy', 'citation': C_TYP, 'effective': None},
    {'id': 'typ_mri', 'label': 'Advanced imaging: MRI / MRA', 'codes': ['70336', '70540-70559', '71550-71555', '72141-72159', '72195-72198',
                                                                       '73218-73225', '73718-73725', '74181-74185', '75557-75565', '77046-77049'],
     'products': MANAGED, 'kind': 'typical_payer_policy', 'citation': C_TYP, 'effective': None},
    {'id': 'typ_pet_nuclear', 'label': 'Advanced imaging: PET / nuclear cardiology', 'codes': ['78451-78454', '78459', '78491-78492', '78608',
                                                                                               '78811-78816'],
     'products': MANAGED, 'kind': 'typical_payer_policy', 'citation': C_TYP, 'effective': None},
    {'id': 'typ_sleep', 'label': 'Attended sleep studies', 'codes': ['95810-95811'],
     'products': MANAGED, 'kind': 'typical_payer_policy', 'citation': C_TYP, 'effective': None},
    {'id': 'typ_specialty_drugs', 'label': 'Specialty Part B drugs (chemotherapy, biosimilars)', 'codes': ['J9000-J9999', 'Q5101-Q5130'],
     'products': MANAGED, 'kind': 'typical_payer_policy', 'citation': C_TYP, 'effective': None},
]
PRODUCTS = FFS + MANAGED + ('medigap', 'cob_secondary')
FOCUS = ('all', 'rules', 'denials', 'learned', 'gold_card', 'auto_approval', 'lookup')
FIELDS = {'focus', 'codes', 'product', 'payer', 'rules', 'start', 'end', 'min_claims', 'min_requests', 'gold_card_rate',
          'auto_approve_rate', 'top'}

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'PRIOR AUTHORIZATION rules: which services need prior auth under which product (cited CMS OPD/DMEPOS/ambulance lists '
                  'and typical payer policy), auth-related denials (CARC 197/15/62/...) by payer, service and provider, rules each payer '
                  'appears to enforce (learned), gold-card candidates (>=90% approval) and auto-approval candidates.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'focus': '|'.join(FOCUS), 'codes': [], 'product': '', 'payer': '', 'rules': [{'label': '', 'codes': [], 'products': []}],
                           'start': 'YYYY-MM-DD', 'end': 'YYYY-MM-DD', 'min_claims': 30, 'min_requests': 5, 'gold_card_rate': 0.9,
                           'auto_approve_rate': 0.95, 'top': 25},
            'meaning': 'Cited PA rule table (42 CFR 419.83 OPD list, 42 CFR 414.234 DMEPOS list, SSA 1834(l)(16) ambulance, typical MA/'
                       'commercial imaging & specialty-drug policy) plus supplied rules, evaluated against claim service codes; auth '
                       'denials = first-adjudication CARC 15/39/62/197/198/243/288; learned payer rules = payer x claim type x POS cells '
                       'with auth-denial rate above the payer rate (binomial, BH q<0.05, lift>=2); gold card = provider approval >= 90% '
                       'with >= min_requests PA-relevant claims (Texas HB 3459 style); auto-approval = rule services approved >= 95%.'}
NATIVE = ('value', 'count')


def schema(obj, string, array):
    rule = {'type': 'object', 'properties': {'label': {'type': 'string'}, 'codes': {'type': 'array', 'items': {'type': 'string'}},
                                             'products': {'type': 'array', 'items': {'type': 'string', 'enum': list(PRODUCTS)}}},
            'required': ['codes'], 'additionalProperties': False}
    return obj({'focus': string(FOCUS), 'codes': array({'type': 'string'}, 'CPT/HCPCS codes or ranges to look up'),
                'product': {'type': 'string', 'description': 'Product line (empty = all)'}, 'payer': {'type': 'string', 'description': 'Payer id (empty = all)'},
                'rules': {'type': 'array', 'items': rule, 'description': 'Client PA rules added to the cited table'},
                'start': {'type': 'string', 'description': 'Period start YYYY-MM-DD (default 12 months before as-of)'},
                'end': {'type': 'string', 'description': 'Period end YYYY-MM-DD (default as-of)'},
                'min_claims': {'type': 'integer', 'minimum': 5, 'maximum': 100000}, 'min_requests': {'type': 'integer', 'minimum': 1, 'maximum': 1000},
                'gold_card_rate': {'type': 'number', 'minimum': 0.5, 'maximum': 1}, 'auto_approve_rate': {'type': 'number', 'minimum': 0.5, 'maximum': 1},
                'top': {'type': 'integer', 'minimum': 1, 'maximum': 500}})


CODE_RX = re.compile(r'^[A-Z]?\d{4,5}$')


def _norm_code(c):
    return str(c).strip().upper().replace(' ', '')


def _check_code(c):
    parts = c.split('-')
    if len(parts) > 2 or not all(CODE_RX.match(x) for x in parts): raise ValueError(f'code or range: {c}')
    if len(parts) == 2 and (parts[0][0].isalpha() != parts[1][0].isalpha() or (parts[0][0].isalpha() and parts[0][0] != parts[1][0])):
        raise ValueError(f'range endpoints must share their letter prefix: {c}')


def validate(p):
    if not isinstance(p, dict) or set(p) - FIELDS: raise ValueError('prior_auth_rules parameters: ' + ', '.join(sorted(FIELDS)))
    out = {'focus': p.get('focus', 'all'), 'codes': [_norm_code(c) for c in p.get('codes') or []], 'product': str(p.get('product') or ''),
           'payer': str(p.get('payer') or ''), 'rules': [], 'start': p.get('start') or '', 'end': p.get('end') or '',
           'min_claims': p.get('min_claims', 30), 'min_requests': p.get('min_requests', 5), 'gold_card_rate': p.get('gold_card_rate', 0.9),
           'auto_approve_rate': p.get('auto_approve_rate', 0.95), 'top': p.get('top', 25)}
    if out['focus'] not in FOCUS: raise ValueError(f'focus: {FOCUS}')
    if out['focus'] == 'lookup' and not out['codes']: raise ValueError('focus=lookup needs codes')
    for c in out['codes']: _check_code(c)
    if out['product'] and out['product'] not in PRODUCTS: raise ValueError(f'product: {PRODUCTS}')
    for i, r in enumerate(p.get('rules') or []):
        if not isinstance(r, dict) or set(r) - {'label', 'codes', 'products'} or not r.get('codes'): raise ValueError('rules: [{label, codes, products}]')
        codes = [_norm_code(c) for c in r['codes']]
        for c in codes: _check_code(c)
        prods = tuple(r.get('products') or PRODUCTS)
        if any(x not in PRODUCTS for x in prods): raise ValueError(f'rule products: {PRODUCTS}')
        out['rules'].append({'id': f'client_{i + 1}', 'label': str(r.get('label') or f'Client rule {i + 1}'), 'codes': codes, 'products': prods,
                             'kind': 'client_supplied', 'citation': 'Supplied in the question', 'effective': None})
    for k in ('start', 'end'):
        if out[k]: out[k] = rc.iso(out[k])
    for k, lo, hi in (('min_claims', 5, 100000), ('min_requests', 1, 1000), ('top', 1, 500)):
        if type(out[k]) is not int or not lo <= out[k] <= hi: raise ValueError(f'{k}: integer {lo}..{hi}')
    for k in ('gold_card_rate', 'auto_approve_rate'):
        v = out[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.5 <= v <= 1: raise ValueError(f'{k}: 0.5..1')
        out[k] = float(v)
    return out


def _key(code):
    code = _norm_code(code)
    return (code[0], int(code[1:])) if code[:1].isalpha() else ('', int(code)) if code.isdigit() else None


def matches(code, spec):
    k = _key(code)
    if k is None: return False
    a, _, b = spec.partition('-')
    ka, kb = _key(a), _key(b or a)
    return ka is not None and kb is not None and k[0] == ka[0] and ka[1] <= k[1] <= kb[1]


def rules_for(code, rules, product=None):
    return [r for r in rules if any(matches(code, s) for s in r['codes']) and (not product or product in r['products'])]


def carc_number(carc):
    m = re.search(r'(\d+)', str(carc or ''))
    return m.group(1) if m else None


def load(db, patient_ids, p, start, end):
    keep = set(patient_ids) if patient_ids is not None else None
    first = dict(db.execute('SELECT payer_claim_id, carc FROM payer_adjudication WHERE is_first=1'))
    codes = defaultdict(set)
    for cid, cpt, hcpcs in db.execute('SELECT payer_claim_id, cpt, hcpcs FROM payer_claim_line'):
        for c in (cpt, hcpcs):
            if c: codes[cid].add(_norm_code(c))
    if 'procedure_line' in rc.tables(db):
        for cid, cpt, hcpcs, code in db.execute("SELECT claim_id, cpt, hcpcs, code FROM procedure_line WHERE source='payer_claim_line'"):
            for c in (cpt, hcpcs):
                if c: codes[cid].add(_norm_code(c))
    rows = []
    sql = ('SELECT payer_claim_id, patient_id, payer_id, product, claim_type, place_of_service, billing_npi, service_from, first_status, '
           "final_status, charge_cents, paid_cents FROM payer_claim WHERE version_status='current' AND service_from BETWEEN ? AND ?")
    for cid, pid, payer, product, ctype, pos, npi, svc, fs, final, charge, paid in db.execute(sql, (start, end)):
        if keep is not None and pid not in keep: continue
        if p['payer'] and payer != p['payer']: continue
        if p['product'] and product != p['product']: continue
        carc = first.get(cid)
        auth = bool(str(fs or '').startswith('denied') and carc_number(carc) in AUTH_CARCS)
        rows.append({'claim_id': cid, 'patient_id': pid, 'payer': payer or 'unknown', 'product': product or 'unknown', 'claim_type': ctype or 'unknown',
                     'pos': pos or '-', 'billing_npi': npi or 'none', 'service_from': svc, 'auth_denied': auth, 'carc': carc,
                     'upheld': auth and final == 'denied_upheld', 'charge': (charge or 0) / 100, 'paid': (paid or 0) / 100,
                     'codes': sorted(codes.get(cid, ())), 'cell': f"{ctype or 'unknown'} POS {pos or '-'}"})
    return rows


def run(db, patient_ids, p, as_of):
    end = p['end'] or as_of
    start = p['start'] or rc.window(as_of, 12, end)[0]
    rules = RULES + p['rules']
    rows = load(db, patient_ids, p, start, end)
    n = len(rows); auth = [r for r in rows if r['auth_denied']]
    out = {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'period': {'start': start, 'end': end}, 'claims': n,
           'auth_denials': len(auth), 'auth_denial_rate': round(len(auth) / n, 5) if n else None,
           'auth_denied_charges': round(sum(r['charge'] for r in auth), 2), 'upheld': sum(r['upheld'] for r in auth)}
    # rule hits (code-level; denied claims mostly carry no service lines in the payer feed)
    hits = defaultdict(list)
    for r in rows:
        for rule in {x['id']: x for c in r['codes'] for x in rules_for(c, rules, r['product'])}.values():
            hits[rule['id']].append(r)
    by_id = {x['id']: x for x in rules}
    out['rule_hits'] = sorted([{'rule': by_id[k]['label'], 'rule_id': k, 'kind': by_id[k]['kind'], 'citation': by_id[k]['citation'],
                                'products': list(by_id[k]['products']), 'claims': len(v), 'patients': len({r['patient_id'] for r in v}),
                                'auth_denied': sum(r['auth_denied'] for r in v), 'paid': round(sum(r['paid'] for r in v), 2),
                                'approval_rate': round(1 - sum(r['auth_denied'] for r in v) / len(v), 4),
                                'example_claims': [r['claim_id'] for r in v[:3]]} for k, v in hits.items()], key=lambda x: -x['claims'])
    # auth denials by payer / setting / provider
    def table(key, min_n=1):
        g = defaultdict(lambda: [0, 0, 0.0])
        for r in rows:
            s = g[r[key]]; s[0] += 1; s[1] += r['auth_denied']; s[2] += r['charge'] if r['auth_denied'] else 0
        return sorted([{key: k, 'claims': a, 'auth_denials': b, 'auth_denial_rate': round(b / a, 4), 'auth_denied_charges': round(c, 2)}
                       for k, (a, b, c) in g.items() if a >= min_n and b], key=lambda x: (-x['auth_denials'], x[key]))
    out['by_payer'] = table('payer'); out['by_setting'] = table('cell'); out['by_provider'] = table('billing_npi')[:p['top']]
    out['carc_mix'] = dict(Counter(r['carc'] for r in auth).most_common())
    # learned rules: payer x setting cells above the payer's own auth-denial rate
    payer_rate = {x['payer']: x['auth_denial_rate'] for x in out['by_payer']}
    cells = defaultdict(lambda: [0, 0])
    for r in rows:
        c = cells[(r['payer'], r['cell'])]; c[0] += 1; c[1] += r['auth_denied']
    cand = []
    for (payer, cell), (a, b) in cells.items():
        base = payer_rate.get(payer) or 0
        if a < p['min_claims'] or not b or not base: continue
        cand.append([payer, cell, a, b, base, float(binom.sf(b - 1, a, base))])
    qs = rc.bh([c[5] for c in cand])
    out['learned_rules'] = sorted([{'payer': c[0], 'setting': c[1], 'claims': c[2], 'auth_denials': c[3], 'rate': round(c[3] / c[2], 4),
                                    'payer_rate': round(c[4], 4), 'lift': round(c[3] / c[2] / c[4], 2), 'q_value': round(q, 5),
                                    'rule': f'{c[0]} appears to require authorization for {c[1]}'}
                                   for c, q in zip(cand, qs) if q < 0.05 and c[3] / c[2] >= 2 * c[4]], key=lambda x: x['q_value'])
    # gold card: PA-relevant claims per provider x payer
    relevant_cells = {(r['payer'], r['cell']) for r in auth} | {(c['payer'], c['setting']) for c in out['learned_rules']}
    hit_ids = {r['claim_id'] for v in hits.values() for r in v}
    prov = defaultdict(lambda: [0, 0])
    for r in rows:
        if r['claim_id'] in hit_ids or (r['payer'], r['cell']) in relevant_cells:
            s = prov[(r['billing_npi'], r['payer'])]; s[0] += 1; s[1] += r['auth_denied']
    gold = [{'billing_npi': k[0], 'payer': k[1], 'pa_relevant_claims': a, 'auth_denials': b, 'approval_rate': round(1 - b / a, 4),
             'gold_card': a >= p['min_requests'] and 1 - b / a >= p['gold_card_rate']} for k, (a, b) in prov.items() if a >= p['min_requests']]
    out['gold_card'] = {'evaluated': len(gold), 'qualifying': sum(g['gold_card'] for g in gold),
                        'not_qualifying': sorted([g for g in gold if not g['gold_card']], key=lambda g: g['approval_rate'])[:p['top']],
                        'qualifying_top': sorted([g for g in gold if g['gold_card']], key=lambda g: -g['pa_relevant_claims'])[:p['top']]}
    out['auto_approval'] = [h for h in out['rule_hits'] if h['claims'] >= p['min_requests'] and h['approval_rate'] >= p['auto_approve_rate']]
    # lookup of named codes
    lk = []
    for spec in p['codes']:
        applicable = [x for x in rules if any(matches(spec.split('-')[0], s) for s in x['codes'])]
        obs = [r for r in rows if any(matches(c, spec) for c in r['codes'])]
        lk.append({'code': spec, 'pa_required_for': sorted({prod for x in applicable for prod in x['products']}),
                   'rules': [{'rule': x['label'], 'kind': x['kind'], 'citation': x['citation'], 'products': list(x['products'])} for x in applicable],
                   'requires_pa_for_product': (bool(rules_for(spec.split('-')[0], rules, p['product'])) if p['product'] else None),
                   'claims': len(obs), 'auth_denied': sum(r['auth_denied'] for r in obs), 'paid': round(sum(r['paid'] for r in obs), 2)})
    out['lookup'] = lk
    f = p['focus']
    out['rows'] = (lk if f == 'lookup' else out['learned_rules'] if f == 'learned' else
                   (out['gold_card']['not_qualifying'] + out['gold_card']['qualifying_top'])[:p['top']] if f == 'gold_card' else
                   out['auto_approval'] if f == 'auto_approval' else out['rule_hits'] if f == 'rules' else
                   out['by_payer'] + out['by_setting'][:10] if f == 'denials' else (lk or out['by_payer'] + out['learned_rules'][:10] + out['rule_hits'][:10]))
    out['assumptions'] = [
        f'Synthetic payer feed, claims with service dates {start} to {end}; auth denial = first adjudication denied with CARC '
        + '/'.join(sorted(AUTH_CARCS, key=int)) + '.',
        'Prior-authorization requests and decisions are not received in the warehouse: approval is proxied by claims NOT auth-denied in '
        'PA-relevant settings (curated-rule hits or payer settings with auth denials).',
        'The payer feed returns service lines mostly on paid claims, so code-level rule hits under-count denied services; payer/setting '
        'analytics use claim headers and cover every claim.',
        'Medicare FFS rules are cited regulation/CMS program lists; managed-care rules are typical payer policy, not a specific payer\'s list '
        '(supply `rules` for a payer\'s actual list). OPD-list rules are applied to every setting where the code appears.',
        f"Gold card: >= {p['gold_card_rate']:.0%} approval with >= {p['min_requests']} PA-relevant claims (Texas HB 3459 style threshold).",
        f"Learned rules need >= {p['min_claims']} claims in the payer x setting cell, BH q < 0.05 and lift >= 2."]
    out['method'] = (f'{VERSION}: cited rule table + supplied rules matched to claim codes by product; auth-denial CARC analytics by payer, '
                     'setting and provider; learned payer rules (binomial upper tail vs payer rate, Benjamini-Hochberg); gold-card and '
                     'auto-approval thresholds.')
    out['status'] = 'answered'
    return out


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    with rc.connect(path) as db:
        return run(db, patient_ids, p, str(as_of)[:10])


def headline(r):
    if r['lookup']:
        parts = []
        for x in r['lookup']:
            need = ('PA required for ' + ', '.join(x['pa_required_for'])) if x['pa_required_for'] else 'no PA rule in the cited table'
            if x['requires_pa_for_product'] is not None:
                need = f"{'PA required' if x['requires_pa_for_product'] else 'no PA rule'} for {r['parameters']['product']} ({need})"
            parts.append(f"{x['code']}: {need}" + (f" ({x['rules'][0]['citation'].split(';')[0]})" if x['rules'] else '')
                         + f"; {x['claims']} claims, {x['auth_denied']} auth-denied")
        return 'Prior-auth lookup: ' + '; '.join(parts)
    g = r['gold_card']
    rate = f"{r['auth_denial_rate']:.2%}" if r['auth_denial_rate'] is not None else 'n/a'
    top = r['by_payer'][0] if r['by_payer'] else None
    s = (f"Prior auth {r['period']['start']} to {r['period']['end']}: {r['auth_denials']:,} auth denials of {r['claims']:,} claims ({rate}, "
         f"${r['auth_denied_charges']:,.0f} charges, {r['upheld']} upheld)")
    if top: s += f"; highest {top['payer']} {top['auth_denial_rate']:.1%}"
    s += f"; {len(r['learned_rules'])} learned payer rules; gold card {g['qualifying']} of {g['evaluated']} provider-payer pairs qualify"
    s += f"; {len(r['rule_hits'])} cited rules matched claims, {len(r['auto_approval'])} auto-approval candidates"
    return s


def answer(report, plan, question):
    return rc.answer(report, plan, value=headline(report) + '.', table=report['rows'],
                     definitions=[{'term': 'auth denial', 'basis': 'first-adjudication CARC 15/39/62/197/198/243/288 (X12 CARC list)'},
                                  {'term': 'gold card', 'basis': 'PA exemption at >= 90% approval (Texas HB 3459, Tex. Ins. Code ch. 4201 subch. N)'}],
                     number=None if report['lookup'] else report['auth_denials'], overall_key='prior_auth')


# ---- free local-compiler route ----------------------------------------------------------------------------------------
PA = r'\bprior[- ]?auth\w*\b|\bpre-?auth\w*\b|\bpre-?cert\w*\b|\bauthorization\b|\bgold[- ]?card\w*\b|\bauth denials?\b|\bpa (?:rules?|requirements?|denials?)\b|\bco-?197\b|\bcarc 197\b'


def route(question, profile, as_of):
    from .engine_suite import plan
    q = rc.low(question)
    if not re.search(PA, q): return None
    if re.search(r'\boauth\b|\bauthori[sz]ation (?:token|header|code flow)\b', q): return None
    if rc.knowledge_only(q) and not re.search(r'\b(?:cpt|hcpcs|code)\s*[a-z]?\d{4,5}\b|\b[a-z]\d{4}\b|\b\d{5}\b', q): return None
    params = {}
    codes = re.findall(r'\b([a-z]\d{4}|\d{5})(?:\s*-\s*([a-z]?\d{4,5}))?\b', q)
    codes = [f'{a.upper()}-{b.upper()}' if b else a.upper() for a, b in codes if not re.fullmatch(r'20\d{2}', a)]
    if codes: params['codes'] = codes[:20]; params['focus'] = 'lookup'
    elif re.search(r'\bgold[- ]?card', q): params['focus'] = 'gold_card'
    elif re.search(r'\bauto[- ]?approv\w*\b|\bwaive\b|\bremove (?:the )?(?:prior )?auth', q): params['focus'] = 'auto_approval'
    elif re.search(r'\bappears? to require\b|\b(?:which|what) (?:services|settings) (?:does|do) (?:each |the )?payers? require\b|\blearn\w*\b|\benforc\w*\b', q):
        params['focus'] = 'learned'
    elif re.search(r'\bdenial\w*\b|\bdenied\b', q): params['focus'] = 'denials'
    from .mlr import LINES
    if re.search(r'\bmedicare (?:ffs|fee[- ]for[- ]service)\b|\btraditional medicare\b|\boriginal medicare\b', q): params['product'] = 'medicare_ffs'
    else:
        for k, rx in LINES:
            if re.search(rx, q) and k != 'medigap': params['product'] = k; break
    m = re.search(r'\b(wh-payer-[a-z0-9]+)\b', q)
    if m: params['payer'] = m.group(1).upper()
    return plan(OP, params, 'Prior-authorization rule evaluation, auth-denial analytics, gold-card and auto-approval candidates', question)


TEMPLATES = ['Does CPT 72148 need prior authorization for Medicare Advantage?', 'Which providers qualify for gold carding on prior auth?',
             'Where are our authorization denials concentrated by payer?', 'Which services does each payer appear to require prior auth for?',
             'Which prior auth requirements could be auto-approved?', 'How many claims were denied for missing precertification last year?',
             'Is prior authorization required for E0277 under traditional Medicare?', 'Prior auth denial rate by provider']
