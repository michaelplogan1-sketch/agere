"""Contract reimbursement modelling: expected allowed amount per claim line under observed or supplied contract terms.

Terms, in order of precedence per payer x code:
  supplied   params.terms = {payer_id or '*': {'rates': {code: dollars_per_unit}, 'pct_of_medicare': 1.15}} (explicit contract)
  observed   the payer's modal-ish contract rate for the code = median allowed per unit across that payer's other lines for the code
             in the period (needs >= min_lines lines; the line itself is excluded, so a single short-pay stands out)
Medicare reference = observed Medicare FFS median allowed per unit for the code in this warehouse (not the published fee schedule).
Views
  variance   lines paid below (short-paid) or above expected by more than `tolerance`; dollars by payer and code
  expected   expected vs actual allowed totals by payer
  reprice    revenue change if a payer were repriced to `reprice` = {payer: pct_of_medicare} (or '*' for all): same volume, new rate
"""
from collections import defaultdict
import re
import statistics

from . import revenue_common as rc

VERSION = 'contract-reimbursement/1'
OP = 'contract_reimbursement'
VIEWS = ('variance', 'expected', 'reprice')

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'CONTRACT REIMBURSEMENT MODEL: expected allowed per claim line under observed or supplied contract terms (rate per code or percent '
                  'of Medicare), underpayment / overpayment variance by payer and code, and revenue effect of repricing a contract.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'payers': [], 'products': [], 'codes': [], 'period_months': 12, 'min_lines': 5, 'tolerance': 0.05, 'view': 'variance|expected|reprice',
                           'terms': {}, 'reprice': {}, 'top': 25},
            'meaning': 'Per payer x code expected allowed per unit (supplied terms else observed payer median), variance of each final claim line vs expected '
                       '(short-paid below -tolerance), dollar totals; reprice scenario at a percent of Medicare.'}
NATIVE = ('value',)


def schema(obj, string, array):
    return obj({'payers': array({'type': 'string'}, 'Payer ids; empty = all'), 'products': array({'type': 'string'}, 'Product lines; empty = all'),
                'codes': array({'type': 'string'}, 'CPT/HCPCS; empty = all'),
                'period_months': {'type': 'integer', 'minimum': 1, 'maximum': 60}, 'min_lines': {'type': 'integer', 'minimum': 2, 'maximum': 1000},
                'tolerance': {'type': 'number', 'minimum': 0, 'maximum': 0.5}, 'view': string(VIEWS),
                'terms': {'type': 'object', 'description': "{payer or '*': {'rates': {code: $/unit}, 'pct_of_medicare': number}}"},
                'reprice': {'type': 'object', 'description': "{payer or '*': pct_of_medicare}"},
                'top': {'type': 'integer', 'minimum': 1, 'maximum': 500}})


def _terms(t):
    out = {}
    for k, v in (t or {}).items():
        if not isinstance(v, dict) or set(v) - {'rates', 'pct_of_medicare'}: raise ValueError("terms: {payer: {'rates': {code: $}, 'pct_of_medicare': x}}")
        rates = {str(c).upper(): float(r) for c, r in (v.get('rates') or {}).items()}
        pm = v.get('pct_of_medicare')
        if pm is not None and not 0 < float(pm) <= 20: raise ValueError('pct_of_medicare: 0..20')
        out[str(k).upper() if k != '*' else '*'] = {'rates': rates, 'pct_of_medicare': float(pm) if pm is not None else None}
    return out


def validate(p):
    allowed = {'payers', 'products', 'codes', 'period_months', 'min_lines', 'tolerance', 'view', 'terms', 'reprice', 'top'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('contract_reimbursement parameters: ' + ', '.join(sorted(allowed)))
    out = {'payers': sorted({str(x).upper() for x in p.get('payers') or []}), 'products': sorted(set(p.get('products') or [])),
           'codes': sorted({str(c).upper() for c in p.get('codes') or []}), 'period_months': p.get('period_months', 12), 'min_lines': p.get('min_lines', 5),
           'tolerance': float(p.get('tolerance', 0.05)), 'view': p.get('view', 'variance'), 'terms': _terms(p.get('terms')),
           'reprice': {str(k).upper() if k != '*' else '*': float(v) for k, v in (p.get('reprice') or {}).items()}, 'top': p.get('top', 25)}
    for k, lo, hi in (('period_months', 1, 60), ('min_lines', 2, 1000), ('top', 1, 500)):
        if type(out[k]) is not int or not lo <= out[k] <= hi: raise ValueError(f'{k}: {lo}..{hi}')
    if not 0 <= out['tolerance'] <= 0.5: raise ValueError('tolerance: 0..0.5')
    if out['view'] not in VIEWS: raise ValueError(f'view: {VIEWS}')
    if any(not 0 < v <= 20 for v in out['reprice'].values()): raise ValueError('reprice: percent of Medicare as a ratio, e.g. 1.2')
    return out


def load(db, start, end, keep, p):
    claims = {}
    cm = ' AND counts_in_metrics = 1' if 'counts_in_metrics' in rc.columns(db, 'payer_claim') else ''
    for cid, pid, payer, product in db.execute('SELECT payer_claim_id, patient_id, payer_id, product FROM payer_claim WHERE service_from BETWEEN ? AND ?' + cm, (start, end)):
        if keep is not None and pid not in keep: continue
        claims[cid] = (payer, product)
    lm = ' AND counts_in_metrics = 1' if 'counts_in_metrics' in rc.columns(db, 'payer_claim_line') else ''
    lines, medicare = [], defaultdict(list)
    for cid, code, units, allowed in db.execute('SELECT payer_claim_id, COALESCE(cpt, hcpcs), units, allowed_cents FROM payer_claim_line WHERE allowed_cents > 0' + lm):
        c = claims.get(cid)
        if c is None or not code: continue
        u = units if units and units > 0 else 1
        code = str(code).upper()
        rate = allowed / 100 / u
        if c[1] == 'medicare_ffs': medicare[code].append(rate)
        if (p['payers'] and c[0] not in p['payers']) or (p['products'] and c[1] not in p['products']) or (p['codes'] and code not in p['codes']): continue
        lines.append((cid, c[0], c[1], code, u, allowed / 100, rate))
    return lines, {k: statistics.median(v) for k, v in medicare.items()}


def expected_rate(payer, code, terms, medicare, observed):
    t = terms.get(payer) or {}
    star = terms.get('*') or {}
    for src in (t, star):
        if code in src.get('rates', {}): return src['rates'][code], 'supplied rate'
    for src in (t, star):
        if src.get('pct_of_medicare') and medicare.get(code): return src['pct_of_medicare'] * medicare[code], 'supplied percent of Medicare'
    return observed, 'observed payer median'


def run(db, patient_ids, p, as_of):
    start, end = rc.window(as_of, p['period_months'])
    lines, medicare = load(db, start, end, set(patient_ids) if patient_ids is not None else None, p)
    by = defaultdict(list)
    for ln in lines: by[(ln[1], ln[3])].append(ln[6])
    rows, agg, summary = [], defaultdict(lambda: {'lines': 0, 'actual': 0.0, 'expected': 0.0, 'short': 0.0, 'over': 0.0, 'short_lines': 0, 'over_lines': 0}), defaultdict(lambda: defaultdict(int))
    reprice = defaultdict(lambda: {'lines': 0, 'actual': 0.0, 'repriced': 0.0})
    for cid, payer, product, code, u, allowed, rate in lines:
        rates = by[(payer, code)]
        if payer in p['terms'] and (code in p['terms'][payer]['rates'] or p['terms'][payer]['pct_of_medicare']) or '*' in p['terms']:
            exp, basis = expected_rate(payer, code, p['terms'], medicare, None)
        else: exp, basis = None, None
        if exp is None:
            others = list(rates); others.remove(rate)  # leave-one-out observed contract rate
            if len(others) < p['min_lines'] - 1: summary['no_basis'][payer] += 1; continue
            exp, basis = statistics.median(others), 'observed payer median'
        summary[basis][payer] += 1
        e = exp * u; d = allowed - e
        a = agg[(payer, code)]
        a['lines'] += 1; a['actual'] += allowed; a['expected'] += e
        if d < -p['tolerance'] * e:
            a['short'] += -d; a['short_lines'] += 1
            rows.append({'claim': cid, 'payer': payer, 'product': product, 'code': code, 'units': u, 'allowed': round(allowed, 2), 'expected': round(e, 2),
                         'variance': round(d, 2), 'variance_pct': round(d / e, 4) if e else None, 'basis': basis, 'direction': 'short_paid'})
        elif d > p['tolerance'] * e:
            a['over'] += d; a['over_lines'] += 1
            rows.append({'claim': cid, 'payer': payer, 'product': product, 'code': code, 'units': u, 'allowed': round(allowed, 2), 'expected': round(e, 2),
                         'variance': round(d, 2), 'variance_pct': round(d / e, 4) if e else None, 'basis': basis, 'direction': 'over_paid'})
        pct = p['reprice'].get(payer) or p['reprice'].get('*')
        if pct and medicare.get(code):
            r = reprice[payer]; r['lines'] += 1; r['actual'] += allowed; r['repriced'] += pct * medicare[code] * u
    payers = defaultdict(lambda: {'lines': 0, 'actual': 0.0, 'expected': 0.0, 'short': 0.0, 'over': 0.0, 'short_lines': 0, 'over_lines': 0})
    for (payer, code), a in agg.items():
        for k, v in a.items(): payers[payer][k] += v
    pay_rows = [{'payer': k, **{x: (round(y, 2) if isinstance(y, float) else y) for x, y in v.items()},
                 'net_variance': round(v['actual'] - v['expected'], 2), 'net_variance_pct': round(v['actual'] / v['expected'] - 1, 4) if v['expected'] else None}
                for k, v in sorted(payers.items(), key=lambda kv: kv[1]['short'] - kv[1]['over'], reverse=True)]
    code_rows = sorted(({'payer': pc[0], 'code': pc[1], **{x: (round(y, 2) if isinstance(y, float) else y) for x, y in v.items()}} for pc, v in agg.items() if v['short'] or v['over']),
                       key=lambda r: -r['short'])
    rows.sort(key=lambda r: r['variance'])
    rep = [{'payer': k, **{x: (round(y, 2) if isinstance(y, float) else y) for x, y in v.items()}, 'delta': round(v['repriced'] - v['actual'], 2),
            'delta_pct': round(v['repriced'] / v['actual'] - 1, 4) if v['actual'] else None} for k, v in sorted(reprice.items())]
    modelled = sum(v['lines'] for v in payers.values())
    assumptions = [f'Synthetic warehouse; final payer claim lines with service dates {start} to {end}; allowed per unit = allowed / units.',
                   'Expected rate: supplied terms where given, otherwise the payer\'s observed median allowed per unit for the code from its other lines '
                   f"(leave-one-out, at least {p['min_lines']} lines); lines with no basis are not modelled ({sum(sum(v.values()) for k, v in summary.items() if k == 'no_basis')}).",
                   'No contract documents are received: observed rates define the contract, so a payer that underpays systematically looks compliant; supply terms to test that.',
                   f"Variance beyond +/- {p['tolerance']:.0%} of expected is flagged; carve-outs, multiple-procedure and site-of-service reductions and bundled "
                   'case rates are not modelled, so some short-pays are contractual.',
                   'Medicare reference is the observed Medicare FFS median allowed per unit, not the published fee schedule.']
    return {'schema': VERSION, 'as_of': str(as_of)[:10], 'parameters': p, 'period': {'start': start, 'end': end}, 'modelled_lines': modelled,
            'totals': {'actual': round(sum(v['actual'] for v in payers.values()), 2), 'expected': round(sum(v['expected'] for v in payers.values()), 2),
                       'short_paid': round(sum(v['short'] for v in payers.values()), 2), 'over_paid': round(sum(v['over'] for v in payers.values()), 2)},
            'payers': pay_rows, 'codes': code_rows[:200], 'rows': rows[:2000], 'reprice': rep,
            'basis_lines': {k: sum(v.values()) for k, v in summary.items()}, 'assumptions': assumptions,
            'method': 'Leave-one-out observed (or supplied) contract rate per payer x code, line-level variance vs tolerance, payer and code roll-ups, percent-of-Medicare repricing.'}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    with rc.connect(path) as db:
        return run(db, patient_ids, p, str(as_of)[:10])


def headline(r):
    t = r['totals']
    if r['parameters']['view'] == 'reprice' and r['reprice']:
        return '; '.join(f"repricing {x['payer']} to {r['parameters']['reprice'].get(x['payer'], r['parameters']['reprice'].get('*', 0)):.0%} of Medicare changes allowed by {rc.money(x['delta'])} ({x['delta_pct']:+.1%}) on {x['lines']} lines" for x in r['reprice'][:4])
    if not r['modelled_lines']: return 'No claim lines had a contract rate basis'
    worst = r['payers'][0] if r['payers'] else None
    s = f"{r['modelled_lines']} lines modelled: expected {rc.money(t['expected'])} vs allowed {rc.money(t['actual'])}; short-paid {rc.money(t['short_paid'])}, over-paid {rc.money(t['over_paid'])}"
    return s + (f"; largest shortfall {worst['payer']} ({rc.money(worst['short'])} on {worst['short_lines']} lines)" if worst and worst['short'] else '')


def answer(report, plan, question):
    v = report['parameters']['view']
    table = report['reprice'] if v == 'reprice' and report['reprice'] else report['payers'] if v == 'expected' else (report['rows'][:report['parameters']['top']] or report['payers'])
    return rc.answer(report, plan, value=headline(report) + '.', table=table,
                     definitions=[{'term': 'short-paid', 'basis': 'allowed per unit below the expected contract rate by more than the tolerance'}])


CR_RX = (r'\bcontract(?:ed|ual)? (?:reimbursement|rates?|terms|compliance|payments?|performance)\b|\bunder-?paid?\b|\bunderpa(?:id|yments?)\b|\bshort[- ]?paid?\b|'
         r'\bshort[- ]?pay\w*\b|\bexpected reimbursement\b|\breimbursement (?:variance|model\w*)\b|\bpaid (?:less|below) than (?:the )?contract\b|\bpayer contracts?\b|'
         r'\brepric\w+\b|\brenegotiat\w+\b|\bcontract variance\b|\bvariance by payer\b|\bpercent(?:age)? of medicare\b.{0,40}\b(?:contract|renegotiat)\w*|\bmodel\w* (?:the )?(?:contract|reimbursement)')


def route(question, profile, as_of):
    from .engine_suite import plan
    q = rc.low(question)
    if not re.search(CR_RX, q): return None
    if rc.knowledge_only(q): return None
    if re.search(r'\bprice transparency\b|\bmrfs?\b|\bcompetitors?\b', q): return None
    # peer-claim underpayment and revenue-leakage reviews stay claims_review unless contract terms are named
    if re.search(r'\bsimilar claims\b|\bleak\w*\b|\brework\b', q) and not re.search(r'\bcontract\w*\b|\brepric\w+\b|\brenegotiat\w+\b', q): return None
    params = {}
    codes = [c.upper() for c in re.findall(r'\b(\d{5}|[a-z]\d{4})\b', q) if not re.fullmatch(r'20\d{3}', c)]
    if codes: params['codes'] = sorted(set(codes))
    m = re.search(r'\b(wh-payer-[a-z0-9]+)\b', q)
    if m: params['payers'] = [m.group(1).upper()]
    for k, rx in (('commercial', r'\bcommercial\b'), ('medicaid', r'\bmedicaid\b'), ('medicare_advantage', r'\bmedicare advantage\b|\bma plans?\b')):
        if re.search(rx, q): params['products'] = [k]; break
    rp = re.search(r'\b(?:repric|renegotiat)\w+\b.{0,60}?\b(\d{2,3})\s?%\s?(?:of )?medicare|\b(\d{2,3})\s?%\s?(?:of )?medicare\b.{0,60}\b(?:repric|renegotiat)\w+|\b(?:repric|renegotiat)\w+\b.{0,60}?\bto (\d{2,3})\s?%', q)
    if re.search(r'\brepric\w+\b|\brenegotiat\w+\b', q):
        params['view'] = 'reprice'
        pct = next((g for g in (rp.groups() if rp else ()) if g), None)
        params['reprice'] = {(params['payers'][0] if params.get('payers') else '*'): int(pct) / 100 if pct else 1.2}
    elif re.search(r'\bexpected reimbursement\b|\bmodel\w* (?:the )?(?:contract|reimbursement)\b', q): params['view'] = 'expected'
    m = re.search(r'\bby (?:more than |over )?(\d{1,2})\s?%', q)
    if m and 0 < int(m.group(1)) <= 50: params['tolerance'] = int(m.group(1)) / 100
    return plan(OP, params, 'Expected reimbursement under contract terms with underpayment variance and repricing', question,
                [f"codes {codes or 'all'}"])


TEMPLATES = ['Which payers are underpaying us versus our contracted rates?', 'What is our expected reimbursement versus what we were actually allowed?',
             'Show short-paid claims for 99214 by more than 10%', 'How much revenue would we gain if we repriced commercial contracts to 130% of Medicare?',
             'Model the contract reimbursement for WH-PAYER-C1', 'Which CPT codes are the biggest contract variance by payer?',
             'Payer contract compliance: where do we get paid less than the contract rate?', 'What if we renegotiate Medicare Advantage to 105% of Medicare?']
