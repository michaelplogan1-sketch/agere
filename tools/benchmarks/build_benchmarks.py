"""Build config/agere/benchmarks.json: cited national/peer reference values for the metrics Agere reports.

Each metric is one Perplexity Agent API request (POST /v1/responses, preset 'fast', web search) asking for a JSON
reference value with the search-result numbers it relied on. Source titles/URLs are taken from the provider's own
search results (never from model-written URLs); an entry with no resolvable source is kept as 'uncited' and is never
shown in answers. Only the metric question text is sent: no patient data.

Incremental: existing entries whose query text is unchanged are kept; identical requests replay from the provider
ledger at $0 (context_student.send accounting). --refresh ID re-researches named entries; --only ID ... --preset NAME
re-asks named entries with another Agent API preset (used for entries the fast preset could not cite).
Values outside a wide sanity range for the metric are kept as 'implausible' (e.g. an annual figure labelled PMPM)
and, like 'uncited'/'not_found', are never shown in answers.
Usage: python tools/benchmarks/build_benchmarks.py [--refresh id ...] [--only id ... --preset medium] [--ledger PATH] [--receipts DIR]
"""
import argparse, json, os, re, sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from clinical_core.contracts import digest

OUT = ROOT / 'config/agere/benchmarks.json'
PRESET = 'fast'
CONTRACT = 'agere-benchmarks/1'
# id: (family, payer, label, unit, question)
METRICS = {
    'ed_per_1000_medicare': ('ed_per_1000', 'medicare', 'ED visits per 1,000 beneficiaries, Medicare FFS', 'visits per 1,000 member-years',
        'emergency department visits per 1,000 Medicare fee-for-service beneficiaries per year (national, CMS Geographic Variation or similar)'),
    'ed_per_1000_medicaid': ('ed_per_1000', 'medicaid', 'ED visits per 1,000 Medicaid enrollees', 'visits per 1,000 member-years',
        'emergency department visits per 1,000 Medicaid enrollees (or per 1,000 member-years) per year, national; e.g. MACPAC MACStats, CMS T-MSIS analyses, NCQA HEDIS EDU Medicaid, or peer-reviewed studies'),
    'ed_per_1000_commercial': ('ed_per_1000', 'commercial', 'ED visits per 1,000 commercially insured members', 'visits per 1,000 member-years',
        'emergency department visits per 1,000 commercially insured (employer-sponsored) members per year (national; HCCI, FAIR Health, NCQA or similar)'),
    'admits_per_1000_medicare': ('admits_per_1000', 'medicare', 'Acute inpatient admissions per 1,000, Medicare FFS', 'admissions per 1,000 member-years',
        'acute inpatient hospital stays per 1,000 Medicare fee-for-service beneficiaries per year, national (CMS Medicare Geographic Variation Public Use File "IP covered stays per 1000 beneficiaries", or MedPAC data book)'),
    'admits_per_1000_medicaid': ('admits_per_1000', 'medicaid', 'Inpatient admissions per 1,000 Medicaid enrollees', 'admissions per 1,000 member-years',
        'inpatient hospital stays per 1,000 Medicaid enrollees per year, national (AHRQ HCUP statistical briefs on Medicaid inpatient stays, MACPAC, or peer-reviewed studies)'),
    'admits_per_1000_commercial': ('admits_per_1000', 'commercial', 'Inpatient admissions per 1,000 commercially insured members', 'admissions per 1,000 member-years',
        'inpatient hospital admissions per 1,000 commercially insured (employer-sponsored) members per year, national (HCCI Health Care Cost and Utilization Report, Milliman, or peer-reviewed)'),
    'readmission_30d_medicare': ('readmission_30d', 'medicare', '30-day all-cause readmission rate, Medicare FFS', '%',
        '30-day all-cause hospital readmission rate for Medicare fee-for-service beneficiaries, national (CMS Medicare Geographic Variation Public Use File or CMS Hospital Compare / HRRP national rate)'),
    'readmission_30d_all_payer': ('readmission_30d', None, '30-day all-cause readmission rate, all payers', '%',
        '30-day all-cause hospital readmission rate for all payers combined in US hospitals (national; AHRQ HCUP Nationwide Readmissions Database or similar)'),
    'pmpm_medicare': ('pmpm', 'medicare', 'Total cost of care per member per month, Medicare FFS', 'USD PMPM',
        'Medicare fee-for-service total Parts A and B spending per beneficiary, national (CMS Geographic Variation "total actual costs per capita" or MedPAC), expressed per member per month: divide an annual per-capita figure by 12'),
    'pmpm_medicaid': ('pmpm', 'medicaid', 'Medicaid spending per enrollee per month', 'USD PMPM',
        'average Medicaid spending per full-benefit enrollee per month (PMPM), national, all eligibility groups'),
    'pmpm_commercial': ('pmpm', 'commercial', 'Commercial (employer) health spending per member per month', 'USD PMPM',
        'average employer-sponsored (commercial) health spending per member per month (PMPM), national (HCCI, Milliman, KFF or similar)'),
    'a1c_poor_control': ('a1c_poor_control', None, 'HbA1c poor control (>9%) among adults with diabetes, HEDIS', '%',
        'HEDIS Hemoglobin A1c Control for Patients With Diabetes / Glycemic Status Assessment: poor control (HbA1c >9.0%) rate, lower is better; national average from NCQA State of Healthcare Quality / Quality Compass, with the range across commercial, Medicaid and Medicare product lines'),
    'bp_control': ('bp_control', None, 'Controlling High Blood Pressure (<140/90), HEDIS CBP', '%',
        'HEDIS Controlling High Blood Pressure (CBP, adequately controlled <140/90 mm Hg) rate; national average from NCQA State of Healthcare Quality / Quality Compass, with the range across commercial, Medicaid and Medicare product lines'),
    'statin_diabetes': ('statin_diabetes', None, 'Statin therapy for patients with diabetes, HEDIS SPD', '%',
        'HEDIS Statin Therapy for Patients With Diabetes (SPD, received statin therapy) rate, national averages by product line'),
    'no_show': ('no_show', None, 'Outpatient appointment no-show rate', '%',
        'outpatient / ambulatory clinic appointment no-show rate in US practices (MGMA or peer-reviewed systematic review)'),
    'denial_rate': ('denial_rate', None, 'Initial (first-pass) claim denial rate', '%',
        'initial (first-pass) claim denial rate for US hospitals and health systems (share of claims denied on first submission; Change Healthcare, Kodiak, HFMA, AHA or similar)'),
    'days_to_payment': ('days_to_payment', None, 'Days to payment / days in accounts receivable', 'days',
        'average days in accounts receivable (or days from claim submission to payment) for US hospitals and physician practices (HFMA, MGMA, Kodiak or similar)'),
}
# Wide sanity ranges (not targets): catch unit/scale errors such as annual spend reported as PMPM or a fraction as %.
PLAUSIBLE = {'ed_per_1000': (100, 1500), 'admits_per_1000': (30, 500), 'readmission_30d': (5, 30), 'pmpm_medicare': (500, 2000),
             'pmpm_medicaid': (250, 1500), 'pmpm_commercial': (250, 1200), 'a1c_poor_control': (5, 60), 'bp_control': (30, 90),
             'statin_diabetes': (30, 95), 'no_show': (2, 40), 'denial_rate': (2, 40), 'days_to_payment': (10, 120)}


def plausible(mid, entry):
    family = METRICS[mid][0]
    lo, hi = PLAUSIBLE.get(mid) or PLAUSIBLE.get(family) or (float('-inf'), float('inf'))
    vals = [v for v in (entry.get('value'), entry.get('low'), entry.get('high')) if v is not None]
    return all(lo <= v <= hi for v in vals)


PROMPT = ('Find the most recent authoritative US national reference value for: {q}. Use data from 2019 or later when it exists. Prefer government (CMS, AHRQ HCUP, '
          'MACPAC, CDC), NCQA, KFF, HCCI, MGMA, HFMA or peer-reviewed sources. Reply with ONLY a JSON object, no prose: '
          '{{"value": number or null (single best national figure, in {unit}), "low": number or null, "high": number or null '
          '(range across recent sources or product lines if relevant), "unit": "{unit}", "population": "who the figure '
          'describes (payer, product line, setting)", "year": data year as integer, "summary": "one sentence naming the source and '
          'what is measured", "source_ids": [numbers of the search results the value comes from]}}. Use percent numbers '
          '(15.5, not 0.155). If no reliable figure exists, use null values.')


def parse_response(raw):
    """(json value, {search id: {title,url,date}}) from an Agent API response."""
    results = {}
    for item in raw.get('output') or []:
        if item.get('type') == 'search_results':
            for r in item.get('results') or []:
                if r.get('url'): results[r.get('id')] = {'title': r.get('title'), 'url': r['url'], 'date': r.get('date')}
        if item.get('type') == 'fetch_url_results':
            for r in item.get('contents') or []:
                if r.get('url'): results.setdefault(r['url'], {'title': r.get('title'), 'url': r['url']})
    text = ''.join(c.get('text', '') for item in raw.get('output') or [] if item.get('type') == 'message'
                   for c in item.get('content') or [] if c.get('type') == 'output_text')
    m = re.search(r'\{.*\}', text, re.S)
    if not m: raise ValueError('No JSON object in benchmark answer')
    value = json.loads(m.group(0))
    cited = [int(x) for x in re.findall(r'\[(?:web:)?(\d+)\]', text)]
    return value, results, cited


def _num(v):
    if isinstance(v, bool): return None
    if isinstance(v, (int, float)): return v
    if isinstance(v, str):
        m = re.search(r'-?\d[\d,]*\.?\d*', v)
        return float(m.group(0).replace(',', '')) if m else None
    return None


def entry_from(mid, raw, preset=PRESET):
    family, payer, label, unit, q = METRICS[mid]
    value, results, cited = parse_response(raw)
    ids = []
    for x in list(value.get('source_ids') or []) + cited:
        try: ids.append(int(x))
        except (TypeError, ValueError): pass
    sources = []
    for i in dict.fromkeys(ids):
        if i in results:
            r = results[i]
            host = re.sub(r'^www\.', '', re.sub(r'^https?://([^/]+).*$', r'\1', r['url']))
            sources.append({'title': r['title'], 'url': r['url'], 'publisher': host, 'search_result_date': r.get('date')})
    year = value.get('year')
    entry = {'id': mid, 'family': family, 'payer': payer, 'label': label, 'unit': value.get('unit') or unit,
             'value': _num(value.get('value')), 'low': _num(value.get('low')), 'high': _num(value.get('high')),
             'population': value.get('population'), 'year': int(year) if isinstance(year, (int, float)) and not isinstance(year, bool) else None,
             'summary': value.get('summary'), 'sources': sources,
             'served_model': raw.get('served_model') or raw.get('model'), 'preset': preset}
    has_value = entry['value'] is not None or (entry['low'] is not None and entry['high'] is not None)
    entry['status'] = ('not_found' if not has_value else 'implausible' if not plausible(mid, entry) else 'cited' if sources else 'uncited')
    return entry


def run(*, ledger, receipts, refresh=(), transport=None, out=OUT, only=None, preset=PRESET):
    from clinical_core.agere.clinical.context_student import send
    receipts.mkdir(parents=True, exist_ok=True)
    doc = json.loads(out.read_text()) if out.exists() else {}
    entries = doc.get('benchmarks', {})
    spend, calls, hits = 0.0, 0, 0
    for mid, (family, payer, label, unit, q) in METRICS.items():
        if only and mid not in only: continue
        query = PROMPT.format(q=q, unit=unit)
        signature = digest({'contract': CONTRACT, 'query': query})
        if entries.get(mid, {}).get('query_digest') == signature and mid not in refresh and not only: continue
        body = {'preset': preset, 'input': query}
        if mid in refresh: body['metadata'] = {'agere_refresh': date.today().isoformat()}  # new request identity, explicit paid retry
        try:
            entry, receipt = send(body, 'perplexity_agent', ledger, receipts, parse=lambda raw, mid=mid: entry_from(mid, raw, preset),
                                  transport=transport, timeout=120)
        except Exception as e:
            print(f'{mid}: failed {str(e)[:200]}'); continue
        calls += 1; hits += bool(receipt.get('cache_hit'))
        spend += (receipt.get('cost_estimate') or {}).get('estimated_usd') or 0
        entries[mid] = {**entry, 'query': q, 'query_digest': signature, 'researched_at': date.today().isoformat(),
                        'request_hash': receipt.get('request_hash')}
        print(f"{mid}: {entry['status']} {entry['value']} [{entry['low']}, {entry['high']}] {entry['unit']} {entry['year']} "
              f"{[s['publisher'] for s in entry['sources']][:3]}")
    doc = {'schema': CONTRACT, 'status': 'machine-researched reference values with cited sources; not reviewed by a person',
           'researched_via': f'Perplexity Agent API (POST /v1/responses, web search); preset {PRESET} unless the entry says otherwise',
           'use': 'Shown next to answers as national/peer reference only; never changes a computed answer. Populations, '
                  'definitions and years differ from the received data.',
           'benchmarks': dict(sorted(entries.items()))}
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + '\n')
    return doc, {'requests': calls, 'ledger_cache_hits': hits, 'new_estimated_usd': spend}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', nargs='*', default=[])
    ap.add_argument('--only', nargs='*', default=None)
    ap.add_argument('--preset', default=PRESET)
    ap.add_argument('--ledger', default=os.environ.get('AGERE_LEDGER', str(ROOT / 'artifacts/agere-full/provider-budget.sqlite3')))
    ap.add_argument('--receipts', default=os.environ.get('AGERE_BENCHMARK_RECEIPTS', str(ROOT / 'artifacts/jev-benchmarks/benchmarks')))
    a = ap.parse_args(argv)
    env = Path(os.environ.get('AGERE_ENV_FILE', '/Users/mikel/insight-engine/.env'))
    for line in env.read_text().splitlines() if env.exists() else []:
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip().strip('"'))
    from clinical_core.agere.gateway import BudgetLedger
    doc, cost = run(ledger=BudgetLedger(a.ledger), receipts=Path(a.receipts), refresh=set(a.refresh), only=a.only, preset=a.preset)
    status = {}
    for e in doc['benchmarks'].values(): status[e['status']] = status.get(e['status'], 0) + 1
    print(status, cost)


if __name__ == '__main__':
    main()
