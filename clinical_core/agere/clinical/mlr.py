"""Medical loss ratio (MLR) by product line and year, with the regulatory threshold, credibility and rebate/remittance.

MLR = (incurred claims + quality-improvement expense) / (premium revenue - taxes and fees)       (45 CFR 158.221 structure)
  incurred claims  plan-paid medical + pharmacy claims (vbc._claims: payer feed at final adjudication, replacements over
                   originals, voids/duplicates and B2 pharmacy reversals excluded) for enrolled member-months, completed for
                   IBNR with vbc's chain-ladder factors on arrival lag (completion=True, default)
  member months    member_month (payer enrollment spans) by product
  premium          NOT received in the warehouse: premium_pmpm per product is a parameter. Without it the answer is the
                   claims PMPM and the break-even premium PMPM at each threshold (premium at which MLR = threshold).
Thresholds (minimum MLR):
  medicare_advantage, dual_ma   85%  42 CFR 422.2410(a)  (below: remittance to CMS, 422.2410(b))
  medicaid                      85%  42 CFR 438.8(c)     (state may require remittance, 438.8(j))
  commercial                    85%  45 CFR 158.210(a)   large group (commercial_market='small_group' -> 80%, 158.210(b))
  marketplace                   80%  45 CFR 158.210(c)   individual market
  medigap                       65% individual / 75% group  SSA 1882(r)(1)(A) (loss-ratio standard, not an ACA MLR)
  medicare_ffs, dual_ffs, cob_secondary: no plan MLR (not a risk-bearing premium line here); reported as claims PMPM only.
Credibility (commercial and marketplace only): 45 CFR 158.232 base credibility adjustment by life-years
(member months / 12): < 1,000 non-credible (no rebate owed); 1,000 8.3%; 2,500 5.2%; 5,000 3.7%; 10,000 2.6%;
25,000 1.6%; 50,000 1.2%; >= 75,000 0 (fully credible); linear interpolation between table points (158.232(c)(1));
the deductible factor (158.232(d)) is not applied (deductible level not received). MA/Medicaid credibility
adjustments (422.2440, 438.8(h)) are not applied and this is stated.
years=3 aggregates the reporting year with the two prior years (ACA three-year MLR, 158.220(b)).
"""
from collections import defaultdict
from pathlib import Path
import math
import re
import sqlite3

VERSION = 'mlr/1'
OP = 'mlr'
PRODUCT_LINES = ('medicare_advantage', 'dual_ma', 'medicaid', 'commercial', 'marketplace', 'medigap', 'medicare_ffs', 'dual_ffs', 'cob_secondary')
THRESHOLDS = {'medicare_advantage': (0.85, '42 CFR 422.2410(a)', 'remittance to CMS'), 'dual_ma': (0.85, '42 CFR 422.2410(a)', 'remittance to CMS'),
              'medicaid': (0.85, '42 CFR 438.8(c)', 'remittance to the state where the contract requires it'),
              'commercial': (0.85, '45 CFR 158.210(a) large group', 'rebate to enrollees'),
              'marketplace': (0.80, '45 CFR 158.210(c) individual market', 'rebate to enrollees'),
              'medigap': (0.65, 'SSA 1882(r)(1)(A) individual policies', 'refund or premium credit')}
SMALL_GROUP = (0.80, '45 CFR 158.210(b) small group', 'rebate to enrollees')
MEDIGAP_GROUP = (0.75, 'SSA 1882(r)(1)(A) group policies', 'refund or premium credit')
CREDIBILITY = [(1000, 0.083), (2500, 0.052), (5000, 0.037), (10000, 0.026), (25000, 0.016), (50000, 0.012), (75000, 0.0)]  # 45 CFR 158.232(c)(1)
ACA_LINES = ('commercial', 'marketplace')
GROUP_BY = ('product', 'year', 'product_year')

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'MEDICAL LOSS RATIO by product line (MA, Medicaid, commercial, marketplace) and year vs the 85%/80% minimum: '
                  'IBNR-completed claims, credibility, rebate or remittance owed; break-even premium when premium is not given.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'year': 0, 'years': 1, 'products': [], 'premium_pmpm': {'commercial': 0}, 'qi_expense_pmpm': 0,
                           'taxes_fees_pct': 0, 'commercial_market': 'large_group|small_group', 'medigap_market': 'individual|group',
                           'completion': True, 'group_by': 'product|year|product_year'},
            'meaning': 'MLR = (plan-paid incurred claims, IBNR-completed, + quality-improvement expense) / (premium - taxes/fees) per product '
                       'line and calendar year from member_month enrollment; thresholds MA 85% (42 CFR 422.2410), Medicaid 85% (438.8), '
                       'commercial large group 85% / small group 80% / individual 80% (45 CFR 158.210), Medigap 65/75% (SSA 1882(r)); '
                       'ACA credibility adjustment (158.232) for commercial lines; rebate/remittance = (threshold - adjusted MLR) x net '
                       'premium. Premium is not in the warehouse: supply premium_pmpm per product, else break-even premium PMPM is returned.'}
NATIVE = ('value',)


def schema(obj, string, array):
    return obj({'year': {'type': 'integer', 'description': 'Reporting calendar year (0 = latest complete year before the as-of date)'},
                'years': {'type': 'integer', 'minimum': 1, 'maximum': 3, 'description': '1 = single year; 3 = ACA three-year aggregate'},
                'products': array(string(PRODUCT_LINES), 'Product lines; empty = every line with members'),
                'premium_pmpm': {'type': 'object', 'additionalProperties': {'type': 'number'},
                                 'description': 'Premium revenue PMPM by product line (or {"all": x}); empty = not supplied'},
                'qi_expense_pmpm': {'type': 'number', 'minimum': 0, 'description': 'Quality-improvement expense PMPM (default 0)'},
                'taxes_fees_pct': {'type': 'number', 'minimum': 0, 'maximum': 0.5, 'description': 'Taxes and fees as a share of premium (default 0)'},
                'commercial_market': string(('large_group', 'small_group')), 'medigap_market': string(('individual', 'group')),
                'completion': {'type': 'boolean', 'description': 'Complete claims for IBNR (default true)'}, 'group_by': string(GROUP_BY)})


def validate(p):
    allowed = {'year', 'years', 'products', 'premium_pmpm', 'qi_expense_pmpm', 'taxes_fees_pct', 'commercial_market', 'medigap_market',
               'completion', 'group_by'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('mlr parameters: ' + ', '.join(sorted(allowed)))
    prem = p.get('premium_pmpm') or {}
    if isinstance(prem, (int, float)) and not isinstance(prem, bool): prem = {'all': prem}
    if not isinstance(prem, dict) or any(k not in (*PRODUCT_LINES, 'all') or isinstance(v, bool) or not isinstance(v, (int, float))
                                         or not math.isfinite(v) or v < 0 for k, v in prem.items()):
        raise ValueError('premium_pmpm: {product line or "all": nonnegative PMPM}')
    out = {'year': p.get('year') or 0, 'years': p.get('years', 1), 'products': sorted(set(p.get('products') or [])),
           'premium_pmpm': {k: float(v) for k, v in prem.items() if v}, 'qi_expense_pmpm': float(p.get('qi_expense_pmpm') or 0),
           'taxes_fees_pct': float(p.get('taxes_fees_pct') or 0), 'commercial_market': p.get('commercial_market', 'large_group'),
           'medigap_market': p.get('medigap_market', 'individual'), 'completion': p.get('completion', True), 'group_by': p.get('group_by', 'product')}
    if type(out['year']) is not int or not (out['year'] == 0 or 1990 <= out['year'] <= 2100): raise ValueError('year: 0 or a calendar year')
    if out['years'] not in (1, 3): raise ValueError('years: 1 or 3')
    if any(x not in PRODUCT_LINES for x in out['products']): raise ValueError(f'products: subset of {PRODUCT_LINES}')
    if out['qi_expense_pmpm'] < 0 or not 0 <= out['taxes_fees_pct'] <= 0.5: raise ValueError('qi_expense_pmpm >= 0; taxes_fees_pct 0..0.5')
    if out['commercial_market'] not in ('large_group', 'small_group'): raise ValueError('commercial_market: large_group|small_group')
    if out['medigap_market'] not in ('individual', 'group'): raise ValueError('medigap_market: individual|group')
    if type(out['completion']) is not bool: raise ValueError('completion: true|false')
    if out['group_by'] not in GROUP_BY: raise ValueError(f'group_by: {GROUP_BY}')
    return out


def threshold(product, p):
    if product == 'commercial' and p['commercial_market'] == 'small_group': return SMALL_GROUP
    if product == 'medigap' and p['medigap_market'] == 'group': return MEDIGAP_GROUP
    return THRESHOLDS.get(product)


def credibility_adjustment(life_years):
    """45 CFR 158.232(c)(1) base credibility adjustment; None = non-credible (< 1,000 life-years, no rebate owed)."""
    if life_years < CREDIBILITY[0][0]: return None
    for (a, x), (b, y) in zip(CREDIBILITY, CREDIBILITY[1:]):
        if a <= life_years < b: return round(x + (y - x) * (life_years - a) / (b - a), 5)
    return 0.0


def default_year(as_of):
    """Latest complete calendar year before the as-of date."""
    return int(str(as_of)[:4]) - 1


def cells(db, start, end, keep=None, completion=True, as_of=None):
    """{(product, year): {'member_months', 'paid', 'paid_completed', 'members'}} (dollars)."""
    from . import vbc
    mm = defaultdict(set)
    for pid, month, product in db.execute('SELECT patient_id, month, product FROM member_month WHERE month BETWEEN ? AND ?', (start[:7], end[:7])):
        if keep is not None and pid not in keep: continue
        mm[pid].add((month, product or 'unknown'))
    by_month = {pid: dict(v) for pid, v in mm.items()}
    out = defaultdict(lambda: {'member_months': 0, 'paid': 0.0, 'paid_completed': 0.0, 'members': set()})
    for pid, v in by_month.items():
        for month, product in v.items():
            c = out[(product, month[:4])]; c['member_months'] += 1; c['members'].add(pid)
    claims = vbc._claims(db, start, end)
    factors, _ = vbc._completion(claims, as_of or end) if completion else ({}, {})  # runout observed through the as-of date
    for pid, month, _allowed, paid, _kind, _arr in claims:
        product = by_month.get(pid, {}).get(month)
        if product is None: continue  # claim outside an enrolled member month: not this plan's incurred claim
        c = out[(product, month[:4])]
        c['paid'] += paid / 100; c['paid_completed'] += paid / 100 * factors.get(month, 1.0)
    return {k: {**v, 'members': len(v['members'])} for k, v in out.items()}


def compute(cell_map, p, report_year):
    years = [str(report_year - i) for i in range(p['years'])][::-1]
    groups = defaultdict(lambda: {'member_months': 0, 'paid': 0.0, 'paid_completed': 0.0, 'members': 0})
    for (product, y), c in cell_map.items():
        if y not in years or (p['products'] and product not in p['products']): continue
        key = product if p['group_by'] == 'product' else y if p['group_by'] == 'year' else f'{product} {y}'
        g = groups[(key, product if p['group_by'] != 'year' else 'all')]
        for k in ('member_months', 'paid', 'paid_completed', 'members'): g[k] += c[k]
    rows = []
    for (key, product), g in sorted(groups.items()):
        mm = g['member_months']
        if not mm: continue
        claims = g['paid_completed'] if p['completion'] else g['paid']
        claims_pmpm = claims / mm
        num_pmpm = claims_pmpm + p['qi_expense_pmpm']
        th = threshold(product, p) if product != 'all' else None
        row = {'group': key, 'product': product, 'years': years, 'member_months': mm, 'life_years': round(mm / 12, 1),
               'incurred_claims': round(claims, 2), 'paid_claims': round(g['paid'], 2), 'ibnr': round(g['paid_completed'] - g['paid'], 2),
               'claims_pmpm': round(claims_pmpm, 2), 'qi_expense_pmpm': p['qi_expense_pmpm'],
               'threshold': th[0] if th else None, 'threshold_basis': th[1] if th else 'no plan MLR for this line (not a premium product here)'}
        if th:  # premium at which MLR equals the threshold (taxes/fees grossed up)
            row['break_even_premium_pmpm'] = round(num_pmpm / th[0] / (1 - p['taxes_fees_pct']), 2)
        prem = p['premium_pmpm'].get(product, p['premium_pmpm'].get('all'))
        if prem and th:
            net = prem * (1 - p['taxes_fees_pct'])
            mlr = num_pmpm / net
            adj = credibility_adjustment(mm / 12) if product in ACA_LINES else 0.0
            row.update(premium_pmpm=prem, net_premium=round(net * mm, 2), mlr=round(mlr, 4),
                       credibility_adjustment=adj, credible=adj is not None,
                       adjusted_mlr=round(mlr + adj, 4) if adj is not None else None)
            short = th[0] - (mlr + (adj or 0))
            owed = short * net * mm if adj is not None and short > 0 else 0.0
            row.update(meets_minimum=short <= 0, rebate_or_remittance=round(owed, 2), payee=th[2] if owed else None)
            if adj is None: row['note'] = 'Non-credible (< 1,000 life-years): deemed to meet the standard, no rebate owed (45 CFR 158.230(d)).'
        elif th:
            row['mlr'] = None
            row['note'] = 'Premium not supplied: claims PMPM and break-even premium reported.'
        rows.append(row)
    return rows


def run(db, patient_ids, p, as_of):
    year = p['year'] or default_year(as_of)
    start, end = f'{year - p["years"] + 1}-01-01', min(f'{year}-12-31', str(as_of)[:10])
    keep = set(patient_ids) if patient_ids is not None else None
    rows = compute(cells(db, start, end, keep, p['completion'], str(as_of)[:10]), p, year)
    assumptions = ['Synthetic warehouse; incurred claims = plan-paid claims (member cost sharing excluded) for enrolled member-months.',
                   f"Quality-improvement expense {p['qi_expense_pmpm']:g} PMPM and taxes/fees {p['taxes_fees_pct']:.0%} of premium (supplied or default 0).",
                   'IBNR completion: vbc chain-ladder factors on claim arrival lag.' if p['completion'] else 'Claims as paid (no IBNR completion).',
                   'Credibility adjustment (45 CFR 158.232) applied to commercial/marketplace only; deductible factor not applied; '
                   'MA (422.2440) and Medicaid (438.8(h)) credibility not applied.',
                   f"Commercial treated as {p['commercial_market'].replace('_', ' ')}."]
    if not p['premium_pmpm']:
        assumptions.insert(0, 'Premium revenue is not received in the warehouse and none was supplied: MLR itself is not computed; '
                              'the break-even premium PMPM at each threshold is reported instead (supply premium_pmpm to get MLR and rebate).')
    if year >= int(str(as_of)[:4]): assumptions.append(f'{year} is not complete at the as-of date {str(as_of)[:10]}: a partial-year (run-rate) MLR.')
    return {'schema': VERSION, 'as_of': str(as_of)[:10], 'parameters': p, 'report_year': year, 'period': {'start': start, 'end': end},
            'rows': rows, 'assumptions': assumptions,
            'method': ('MLR = (IBNR-completed plan-paid incurred claims + QI expense) / (premium - taxes and fees) per product line, '
                       'member months from member_month; thresholds by line (MA 85% 42 CFR 422.2410; Medicaid 85% 42 CFR 438.8; '
                       'commercial 85% large group / 80% small group and individual 45 CFR 158.210; Medigap SSA 1882(r)); '
                       'rebate/remittance = (threshold - credibility-adjusted MLR) x net premium when below the minimum.')}


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
    parts = []
    for x in r['rows']:
        if x.get('mlr') is not None:
            owed = f", {x['payee']} ${x['rebate_or_remittance']:,.0f}" if x.get('rebate_or_remittance') else ''
            parts.append(f"{x['group']} MLR {x['mlr']:.1%} vs {x['threshold']:.0%} minimum{owed}")
        elif x.get('break_even_premium_pmpm'):
            parts.append(f"{x['group']} claims ${x['claims_pmpm']:,.0f} PMPM (MLR = {x['threshold']:.0%} at premium ${x['break_even_premium_pmpm']:,.0f} PMPM)")
        else:
            parts.append(f"{x['group']} claims ${x['claims_pmpm']:,.0f} PMPM (no plan MLR)")
    span = f"{r['report_year'] - r['parameters']['years'] + 1}-{r['report_year']}" if r['parameters']['years'] > 1 else str(r['report_year'])
    return f'Medical loss ratio {span}: ' + ('; '.join(parts) if parts else 'no enrolled member-months in the period')


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    mlrs = [x['mlr'] for x in report['rows'] if x.get('mlr') is not None]
    out = {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['rows'], 'patient_ids': [],
           'value': headline(report) + '.', 'method': report['method'], 'limitations': report['assumptions'],
           'definitions': [{'term': 'medical loss ratio', 'basis': '(incurred claims + quality-improvement expense) / (premium - taxes and fees); '
                                                                  '45 CFR 158.221, 42 CFR 422.2420, 42 CFR 438.8(d)'}],
           'membership_role': 'evaluated', 'overall': {'mlr': {'report_year': report['report_year'], 'rows': report['rows']}}}
    if len(mlrs) == 1: out['number'] = mlrs[0]
    return out


# ---- free local-compiler route ----------------------------------------------------------------------------------------
MLR_RX = r'\bmedical loss ratios?\b|\bmlr\b|\bloss ratios?\b|\bmlr rebates?\b|\bmlr remittance\b'
LINES = [('medicare_advantage', r'\bmedicare advantage\b|\bma\b(?! ?(?:degree|student))|\bpart c\b'), ('dual_ma', r'\bd-?snp\b|\bdual(?:ly)?[- ]eligible ma\b'),
         ('medicaid', r'\bmedicaid\b|\bmanaged medicaid\b'), ('marketplace', r'\bmarketplace\b|\bexchange\b|\bindividual market\b|\baca plans?\b'),
         ('commercial', r'\bcommercial\b|\bemployer\b|\blarge group\b|\bsmall group\b'), ('medigap', r'\bmedigap\b|\bmedicare supplement\b')]


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(MLR_RX, low): return None
    if re.search(r'\bminimum loss rate\b|\bmsr\b', low) and not re.search(r'\bmedical loss\b', low): return None  # MSSP MSR/MLR corridor = contract terms
    if re.match(r'(?:what (?:is|are|does)|explain|define|how (?:is|are|does|do)\b.*\b(?:calculated|computed|work|defined))', low) and \
            not re.search(r'\b(?:our|my|we|this year|last year|\d{4})\b', low): return None  # "what is MLR" stays knowledge
    params = {}
    lines = [k for k, rx in LINES if re.search(rx, low)]
    if lines: params['products'] = lines
    if re.search(r'\bsmall group\b', low): params['commercial_market'] = 'small_group'
    y = re.search(r'\b(20\d{2})\b', low)
    if y: params['year'] = int(y.group(1))
    if re.search(r'\bthree[- ]year\b|\b3[- ]year\b', low): params['years'] = 3
    prem = re.search(r'\bpremiums?\b[^$\d]{0,30}\$\s?(\d[\d,]*(?:\.\d+)?)|\$\s?(\d[\d,]*(?:\.\d+)?)\s*(?:pmpm|per member per month)?\s*(?:in )?premiums?\b', low)
    if prem: params['premium_pmpm'] = {'all': float((prem.group(1) or prem.group(2)).replace(',', ''))}
    qi = re.search(r'\$\s?(\d[\d,]*(?:\.\d+)?)\s*(?:pmpm )?(?:of |in )?(?:quality[- ]improvement|qi)\b', low)
    if qi: params['qi_expense_pmpm'] = float(qi.group(1).replace(',', ''))
    if re.search(r'\bby year\b|\beach year\b|\bover time\b|\btrend\b', low): params['group_by'] = 'product_year'
    return plan(OP, params, 'Medical loss ratio by product line vs regulatory minimum, with rebate/remittance', question,
                [f'products {lines or "all"}'])


TEMPLATES = ['what was our medical loss ratio for medicaid members', 'Compute the MLR for the commercial book with premium of $520 PMPM',
             'Are we going to owe an MLR rebate on the marketplace plans?', 'loss ratio by product line last year',
             'Do our Medicare Advantage plans clear the 85% medical loss ratio floor?', 'three-year MLR for small group',
             'What premium PMPM would put Medicaid at an 85% loss ratio?', 'MLR trend by year for each product']
