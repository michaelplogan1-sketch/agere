"""Negotiated-rate (price transparency) benchmark across payers and providers.

Source A, always: a claims-derived allowed-amount file, the warehouse analogue of the Transparency-in-Coverage Allowed Amount
file (45 CFR 147.212(b)(1)(ii)): payer_claim_line allowed amounts (counts_in_metrics=1, allowed > 0) joined to payer_claim for
payer, product and billing NPI -> organization (TIN, name). Cell = payer x provider organization x billing code (CPT/HCPCS):
claim lines n, median / p25 / p75 allowed per unit. A cell needs min_lines lines (default 5; TiC publishes allowed amounts only
with >= 20 claims, 147.212(b)(1)(ii)(B) -- pass min_lines=20 for that rule).
Source B, optional: a CMS Transparency-in-Coverage in-network rate file (mrf_path, JSON schema of the CMS price-transparency-guide
"in-network-rates": in_network[].billing_code / billing_code_type / negotiated_rates[].negotiated_prices[].negotiated_rate,
provider_groups[].npi / tin, or provider_references resolved against the top-level provider_references). Each published rate
is matched to warehouse organizations by NPI or TIN and shown beside the observed allowed median for the same code.
Benchmarks per code
  market median            median of provider-cell medians within the payer (and across payers)
  % of Medicare            cell median / observed Medicare FFS median allowed for the code (product medicare_ffs; observed
                           allowed, not the published fee schedule)
  provider percentile      rank of a provider's median among the payer's providers for the code (0 = cheapest)
  payer index              payer's median / cross-payer median for the code
  dispersion               p75/p25 of provider medians (and coefficient of variation)
'Ours' = provider organizations named by `provider` (organization id, NPI, TIN or name text); without it, the organizations
billing for attributed patients' primary care practices (patient.pcp_practice_id) are marked own.
"""
from collections import defaultdict
from pathlib import Path
import json
import re
import statistics

import numpy as np

from . import revenue_common as rc

VERSION = 'price-transparency/1'
OP = 'price_benchmark'
FOCUS = ('payers', 'providers', 'codes')
MAX_MRF_BYTES = 200 * 1024 * 1024

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'PRICE TRANSPARENCY BENCHMARK: negotiated / allowed rates by payer x provider x CPT/HCPCS from claims (TiC allowed-amount '
                  'analogue) and optional CMS TiC in-network MRF files: median, p25/p75, % of Medicare, our percentile vs competitors, payer '
                  'index, dispersion.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'codes': [], 'payers': [], 'products': [], 'provider': '', 'period_months': 12, 'min_lines': 5, 'focus': 'payers|providers|codes',
                           'top_codes': 10, 'mrf_path': ''},
            'meaning': 'Per billing code: allowed-per-unit medians by payer and provider organization from payer claim lines (period, min_lines cells), '
                       'percent of observed Medicare FFS median, provider percentile within payer, payer index vs cross-payer median, p75/p25 dispersion; '
                       'optional CMS TiC in-network JSON merged by NPI/TIN. Default codes = top_codes by volume.'}
NATIVE = ('value',)


def schema(obj, string, array):
    return obj({'codes': array({'type': 'string', 'pattern': '^[A-Za-z0-9]{5}$'}, 'CPT/HCPCS codes; empty = top codes by volume'),
                'payers': array({'type': 'string'}, 'Payer ids; empty = all'), 'products': array({'type': 'string'}, 'Product lines; empty = all'),
                'provider': {'type': 'string', 'description': 'Our provider: organization id, NPI, TIN or name text (empty = attributed practices)'},
                'period_months': {'type': 'integer', 'minimum': 1, 'maximum': 60}, 'min_lines': {'type': 'integer', 'minimum': 1, 'maximum': 1000},
                'focus': string(FOCUS), 'top_codes': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                'mrf_path': {'type': 'string', 'description': 'Optional local CMS TiC in-network rate JSON file'}})


def validate(p):
    allowed = {'codes', 'payers', 'products', 'provider', 'period_months', 'min_lines', 'focus', 'top_codes', 'mrf_path'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('price_benchmark parameters: ' + ', '.join(sorted(allowed)))
    out = {'codes': sorted({str(c).upper() for c in p.get('codes') or []}), 'payers': sorted(set(p.get('payers') or [])),
           'products': sorted(set(p.get('products') or [])), 'provider': str(p.get('provider') or '').strip(),
           'period_months': p.get('period_months', 12), 'min_lines': p.get('min_lines', 5), 'focus': p.get('focus', 'payers'),
           'top_codes': p.get('top_codes', 10), 'mrf_path': str(p.get('mrf_path') or '').strip()}
    if any(not re.fullmatch(r'[A-Z0-9]{5}', c) for c in out['codes']): raise ValueError('codes: five-character CPT/HCPCS')
    for k, lo, hi in (('period_months', 1, 60), ('min_lines', 1, 1000), ('top_codes', 1, 100)):
        if type(out[k]) is not int or not lo <= out[k] <= hi: raise ValueError(f'{k}: {lo}..{hi}')
    if out['focus'] not in FOCUS: raise ValueError(f'focus: {FOCUS}')
    if out['mrf_path'] and not out['mrf_path'].lower().endswith(('.json',)): raise ValueError('mrf_path: a .json in-network rate file')
    return out


# ---- CMS TiC in-network MRF ---------------------------------------------------------------------------------------------
def parse_mrf(path, codes=None):
    """Rows {payer, code, code_type, negotiated_type, rate, billing_class, npis, tins, arrangement} from a CMS TiC in-network file."""
    f = Path(path)
    if not f.is_file(): raise ValueError(f'mrf_path not found: {path}')
    if f.stat().st_size > MAX_MRF_BYTES: raise ValueError('mrf_path larger than 200 MB: split the file or pass the codes subset')
    doc = json.loads(f.read_text())
    refs = {r.get('provider_group_id'): r.get('provider_groups') or [] for r in doc.get('provider_references') or []}
    payer = doc.get('reporting_entity_name') or f.stem
    out = []
    for item in doc.get('in_network') or []:
        code = str(item.get('billing_code') or '').upper()
        if codes and code not in codes: continue
        for nr in item.get('negotiated_rates') or []:
            groups = list(nr.get('provider_groups') or [])
            for ref in nr.get('provider_references') or []: groups += refs.get(ref, [])
            npis = sorted({str(n) for g in groups for n in (g.get('npi') or [])})
            tins = sorted({str((g.get('tin') or {}).get('value')) for g in groups if (g.get('tin') or {}).get('value')})
            for price in nr.get('negotiated_prices') or []:
                try: rate = float(price.get('negotiated_rate'))
                except (TypeError, ValueError): continue
                out.append({'payer': payer, 'code': code, 'code_type': item.get('billing_code_type'), 'arrangement': item.get('negotiation_arrangement'),
                            'negotiated_type': price.get('negotiated_type'), 'billing_class': price.get('billing_class'), 'rate': rate,
                            'npis': npis, 'tins': tins, 'expiration_date': price.get('expiration_date')})
    return out


# ---- claims-derived allowed-amount file ---------------------------------------------------------------------------------
def _pct(xs, q):
    return float(np.percentile(xs, q)) if xs else None


def lines(db, start, end, keep, p):
    orgs = {}
    for oid, npi, tin, name, otype in db.execute('SELECT organization_id, npi, tin, display_name, organization_type FROM organization'):
        if npi: orgs[str(npi)] = {'organization_id': oid, 'tin': tin, 'name': name, 'type': otype, 'npi': str(npi)}
    claims = {}
    q = ('SELECT payer_claim_id, patient_id, payer_id, product, billing_npi, service_from FROM payer_claim WHERE service_from BETWEEN ? AND ?'
         + (' AND counts_in_metrics = 1' if 'counts_in_metrics' in rc.columns(db, 'payer_claim') else ''))
    for cid, pid, payer, product, npi, sf in db.execute(q, (start, end)):
        if keep is not None and pid not in keep: continue
        if p['payers'] and payer not in p['payers'] and product != 'medicare_ffs': continue
        if p['products'] and product not in p['products'] and product != 'medicare_ffs': continue
        claims[cid] = (payer, product, str(npi) if npi else None)
    cells = defaultdict(list)
    medicare = defaultdict(list)
    lq = 'SELECT payer_claim_id, COALESCE(cpt, hcpcs), units, allowed_cents FROM payer_claim_line WHERE allowed_cents > 0' + \
         (' AND counts_in_metrics = 1' if 'counts_in_metrics' in rc.columns(db, 'payer_claim_line') else '')
    for cid, code, units, allowed in db.execute(lq):
        c = claims.get(cid)
        if c is None or not code: continue
        code = str(code).upper()
        per_unit = allowed / 100 / (units if units and units > 0 else 1)
        payer, product, npi = c
        if product == 'medicare_ffs':
            medicare[code].append(per_unit)
            if 'medicare_ffs' not in p['products']: continue  # Medicare FFS is the reference, not a negotiated-rate cell
        if (p['payers'] and payer not in p['payers']) or (p['products'] and product not in p['products']): continue
        cells[(payer, npi, code)].append(per_unit)
    return cells, medicare, orgs


def own_npis(db, p, orgs):
    if p['provider']:
        t = p['provider'].lower()
        return {n for n, o in orgs.items() if t in (str(o['organization_id']).lower(), n, str(o['tin'] or '').lower()) or t in str(o['name'] or '').lower()}
    practices = {r[0] for r in db.execute('SELECT DISTINCT pcp_practice_id FROM patient WHERE pcp_practice_id IS NOT NULL')}
    return {n for n, o in orgs.items() if o['organization_id'] in practices}


def benchmark(cells, medicare, orgs, own, p):
    volume = defaultdict(int)
    for (payer, npi, code), xs in cells.items(): volume[code] += len(xs)
    codes = p['codes'] or [c for c, _ in sorted(volume.items(), key=lambda kv: -kv[1])[:p['top_codes']]]
    rows, codes_out = [], []
    for code in codes:
        mcr = statistics.median(medicare[code]) if medicare.get(code) else None
        by_payer = defaultdict(list)
        for (payer, npi, c), xs in cells.items():
            if c == code and len(xs) >= p['min_lines']: by_payer[payer].append((npi, xs))
        all_meds = [statistics.median(xs) for v in by_payer.values() for _, xs in v]
        cross = statistics.median(all_meds) if all_meds else None
        summary = {'code': code, 'lines': volume.get(code, 0), 'medicare_ffs_median': round(mcr, 2) if mcr else None,
                   'market_median': round(cross, 2) if cross else None, 'payers': {}, 'own': []}
        for payer, provs in sorted(by_payer.items()):
            meds = sorted(statistics.median(xs) for _, xs in provs)
            pm = statistics.median(meds)
            disp = {'p75_p25': round(_pct(meds, 75) / _pct(meds, 25), 3) if _pct(meds, 25) else None,
                    'cv': round(float(np.std(meds) / np.mean(meds)), 3) if len(meds) > 1 and np.mean(meds) else None}
            summary['payers'][payer] = {'providers': len(provs), 'median': round(pm, 2), 'payer_index': round(pm / cross, 3) if cross else None,
                                        'pct_of_medicare': round(pm / mcr, 3) if mcr else None, **disp}
            for npi, xs in provs:
                m = statistics.median(xs)
                org = orgs.get(npi or '', {})
                below = sum(1 for x in meds if x < m); pct = below / (len(meds) - 1) if len(meds) > 1 else 0.5
                row = {'code': code, 'payer': payer, 'provider': org.get('name') or npi, 'organization_id': org.get('organization_id'),
                       'tin': org.get('tin'), 'npi': npi, 'own': npi in own, 'lines': len(xs), 'median_allowed': round(m, 2),
                       'p25': round(_pct(xs, 25), 2), 'p75': round(_pct(xs, 75), 2), 'payer_market_median': round(pm, 2),
                       'percentile_in_payer': round(pct, 3), 'pct_of_medicare': round(m / mcr, 3) if mcr else None,
                       'vs_market': round(m / pm - 1, 4) if pm else None}
                rows.append(row)
                if row['own']: summary['own'].append({k: row[k] for k in ('payer', 'provider', 'median_allowed', 'percentile_in_payer', 'pct_of_medicare', 'vs_market')})
        codes_out.append(summary)
    return codes, rows, codes_out


def merge_mrf(mrf, rows, orgs):
    by_tin = defaultdict(set)
    for n, o in orgs.items():
        if o['tin']: by_tin[str(o['tin'])].add(n)
    obs = defaultdict(list)
    for r in rows: obs[(r['code'], r['npi'])].append(r['median_allowed'])
    out = []
    for m in mrf:
        npis = set(m['npis']) | {n for t in m['tins'] for n in by_tin.get(t, ())}
        matched = [n for n in npis if n in orgs]
        observed = [x for n in matched for x in obs.get((m['code'], n), [])]
        out.append({**{k: m[k] for k in ('payer', 'code', 'negotiated_type', 'billing_class', 'rate', 'expiration_date')},
                    'matched_organizations': sorted({orgs[n]['organization_id'] for n in matched}),
                    'observed_allowed_median': round(statistics.median(observed), 2) if observed else None,
                    'published_vs_observed': round(m['rate'] / statistics.median(observed) - 1, 4) if observed else None})
    return out


def run(db, patient_ids, p, as_of):
    start, end = rc.window(as_of, p['period_months'])
    keep = set(patient_ids) if patient_ids is not None else None
    cells, medicare, orgs = lines(db, start, end, keep, p)
    own = own_npis(db, p, orgs)
    codes, rows, summary = benchmark(cells, medicare, orgs, own, p)
    mrf_rows = merge_mrf(parse_mrf(p['mrf_path'], set(codes) if p['codes'] else None), rows, orgs) if p['mrf_path'] else []
    rows.sort(key=lambda r: (r['code'], r['payer'], r['median_allowed']))
    assumptions = [f'Synthetic warehouse; allowed amounts per unit from payer claim lines with service dates {start} to {end} (final versions).',
                   'Claims-derived allowed amounts stand in for negotiated rates: no TiC machine-readable file is received'
                   + (f"; supplied MRF {Path(p['mrf_path']).name} merged ({len(mrf_rows)} published rates)." if p['mrf_path'] else ' and none was supplied.'),
                   f"Cells with fewer than {p['min_lines']} lines are suppressed (TiC allowed-amount files suppress < 20 claims).",
                   '% of Medicare uses the observed Medicare FFS median allowed per code in this warehouse, not the published fee schedule '
                   '(no geographic adjustment or site-of-service differential applied); professional/technical component modifiers (26/TC) are not '
                   'received, so imaging and other split-billed codes mix global and component lines.',
                   ("Own provider = organizations matching '" + p['provider'] + "'.") if p['provider'] else
                   'Own provider = organizations billing as attributed primary care practices (no provider named).']
    if not rows: assumptions.append('No payer x provider x code cell met the minimum line count in the period.')
    return {'schema': VERSION, 'as_of': str(as_of)[:10], 'parameters': p, 'period': {'start': start, 'end': end}, 'codes': summary,
            'rows': rows, 'mrf': mrf_rows, 'own_organizations': sorted({orgs[n]['organization_id'] for n in own if n in orgs})[:50],
            'assumptions': assumptions,
            'method': ('Allowed-per-unit medians by payer x billing organization x CPT/HCPCS from claim lines (TiC allowed-amount analogue), market '
                       'median of provider medians, % of observed Medicare FFS median, provider percentile within payer, payer index vs cross-payer '
                       'median, p75/p25 dispersion; optional CMS TiC in-network MRF rates matched by NPI/TIN.')}


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    with rc.connect(path) as db:
        return run(db, patient_ids, p, str(as_of)[:10])


def headline(r):
    parts = []
    for c in r['codes'][:5]:
        if not c['payers']: continue
        lo = min(c['payers'].items(), key=lambda kv: kv[1]['median']); hi = max(c['payers'].items(), key=lambda kv: kv[1]['median'])
        s = f"{c['code']} market ${c['market_median']:,.2f}" + (f" ({c['market_median'] / c['medicare_ffs_median']:.0%} of Medicare)" if c['medicare_ffs_median'] else '')
        if lo[0] != hi[0]: s += f", {lo[0]} ${lo[1]['median']:,.2f} to {hi[0]} ${hi[1]['median']:,.2f}"
        if c['own']:
            o = c['own'][0]; s += f"; ours {o['provider']} at the {o['percentile_in_payer']:.0%} percentile with {o['payer']}"
        parts.append(s)
    return 'Negotiated-rate benchmark: ' + ('; '.join(parts) if parts else 'no code met the minimum volume')


def answer(report, plan, question):
    focus = report['parameters']['focus']
    if focus == 'codes' or (focus == 'payers' and not report['parameters']['provider']):
        table = [{'code': c['code'], 'payer': payer, **v, 'medicare_ffs_median': c['medicare_ffs_median'], 'market_median': c['market_median']}
                 for c in report['codes'] for payer, v in c['payers'].items()]
    else:
        table = [r for r in report['rows'] if focus != 'payers' or r['own']] or report['rows']
    if report['mrf']: table = table + [{'source': 'tic_mrf', **m} for m in report['mrf']]
    return rc.answer(report, plan, value=headline(report) + '.', table=table,
                     definitions=[{'term': 'allowed amount file', 'basis': '45 CFR 147.212(b)(1)(ii) Transparency in Coverage; claims-derived analogue'},
                                  {'term': 'in-network rate file', 'basis': 'CMS price-transparency-guide in-network-rates JSON schema'}])


# ---- free local-compiler route --------------------------------------------------------------------------------------------
PT_RX = (r'\bprice transparency\b|\btransparency (?:in coverage|files?|data)\b|\bmachine[- ]readable (?:files?|mrfs?)\b|\bmrfs?\b|\btic (?:files?|data)\b'
         r'|\bnegotiated (?:rates?|prices?)\b|\bcontracted rates?\b.{0,40}\b(?:compare|benchmark|vs|versus|competitor|market|other payers)\b'
         r'|\brate benchmark\w*\b|\b(?:benchmark|compare)\w* (?:our )?(?:rates|prices|reimbursement rates)\b|(?:\bpercent|%) of medicare\b'
         r'|\bwhat (?:do|does) (?:each|other|the) payers? pay\b.{0,40}\b(?:for|cpt|code)\b|\bcompetitors?\b.{0,30}\b(?:rates?|prices?|paid)\b|\brates? sit\b|\b(?:vs|versus|against) the market\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    q = rc.low(question)
    if not re.search(PT_RX, q): return None
    if rc.knowledge_only(q): return None
    if re.search(r'\bunderpa(?:id|yment)s?\b|\bvariance\b|\bexpected reimbursement\b|\bshort[- ]?paid\b', q): return None  # contract variance engine
    params = {}
    codes = [c.upper() for c in re.findall(r'\b(\d{5}|[a-z]\d{4})\b', q) if not re.fullmatch(r'20\d{3}', c)]
    if codes: params['codes'] = sorted(set(codes))
    if re.search(r'\bcompetitors?\b|\bvs (?:other|competing) (?:providers|hospitals|groups)\b|\bour (?:rank|percentile|position)\b', q): params['focus'] = 'providers'
    m = re.search(r'\bfor ([a-z0-9 &\-]+?) (?:vs|versus|compared)\b', q)
    if m and not re.search(r'\b(?:cpt|code|codes|each|all|our)\b', m.group(1)): params['provider'] = m.group(1).strip()
    if re.search(r'\bmedicare advantage\b', q): params['products'] = ['medicare_advantage']
    elif re.search(r'\bcommercial\b', q): params['products'] = ['commercial']
    elif re.search(r'\bmedicaid\b', q): params['products'] = ['medicaid']
    f = re.search(r'([\w./\-]+\.json)\b', str(question))
    if f: params['mrf_path'] = f.group(1)
    return plan(OP, params, 'Negotiated-rate benchmark by payer and provider vs market and Medicare', question, [f"codes {codes or 'top by volume'}"])


TEMPLATES = ['How do our negotiated rates for 99213 compare across payers?', 'Benchmark our rates against competitors for the top CPT codes',
             'What percent of Medicare does each commercial payer pay for office visits?', 'price transparency benchmark for 99214 and 99215',
             'Which payer pays the lowest negotiated rate for 71046?', 'Where do our commercial rates sit versus the market?',
             'Compare negotiated prices from the transparency in coverage file', 'What do other payers pay for CPT 80053?']
