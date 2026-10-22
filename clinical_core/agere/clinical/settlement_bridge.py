"""Opportunity-to-settlement bridge: work items -> measure-rate change -> quality score -> shared-savings settlement.

Waterfall (each step labelled realized / forecast / modeled; steps sum exactly to the projected settlement):
  realized   current settlement: vbc.settle on the program's cited terms (programs.contract_terms) at the observed
             completion-adjusted allowed PMPM, member months and the CURRENT quality score
  forecast   quality gate: open measure gaps (measure library care gaps, MSSP APP Plus computable set by default) closed by
             outreach (gap patients x their engagement probability x closure_if_engaged) -> projected measure rates ->
             projected quality score -> settlement at the observed PMPM with the projected quality score, minus realized
  modeled    utilization: avoidable-utilization value of outreach (outreach.candidates UTIL_AVOIDABLE, planning assumptions)
             for the capacity-limited worklist, x engagement probability, counted ONCE per patient -> lower actual PMPM ->
             settlement at the projected quality score, minus the forecast step
Gross utilization savings (payer-side) and the planning value of care-gap reasons are reported but never added to the
settlement: only the contract share reaches the ACO, and gap closures earn money only through the quality gate.
No double counting: a patient with several work items (care gaps + utilization) contributes utilization savings once and
measure closures once per measure; the naive per-work-item sum and the dedupe adjustment are shown.
Quality score: mean target attainment over computed quality measures (rate / target capped at 1; targets: supplied, else the defaults below,
which are planning targets, not CMS benchmarks); used as the sharing-rate multiplier (sliding scale).
Benchmark: supplied benchmark_pmpm, else the prior 12 months' completed allowed PMPM trended by `trend` (stated assumption).
"""
from collections import defaultdict
from datetime import date
from pathlib import Path
import re
import sqlite3

VERSION = 'settlement-bridge/1'
OP = 'settlement_bridge'
DEFAULT_MEASURES = ('gsd_poor_control', 'dsf_depression_screening_followup', 'cbp', 'bcs', 'col', 'pcr_readmissions')
DEFAULT_TARGETS = {'gsd_poor_control': 0.20, 'dsf_depression_screening_followup': 0.50, 'cbp': 0.70, 'bcs': 0.70, 'col': 0.65,
                   'pcr_readmissions': 0.15, 'ked': 0.45, 'spc_statin_cvd': 0.84, 'pdc_statins': 0.85, 'pdc_rasa': 0.86, 'pdc_diabetes': 0.85}
POPULATIONS = ('program_default', 'all')
PROGRAM_PRODUCTS = {'mssp': ('medicare_ffs', 'dual_ffs'), 'aco_reach': ('medicare_ffs', 'dual_ffs'), 'lead': ('medicare_ffs', 'dual_ffs'),
                    'commercial': ('commercial', 'marketplace'), 'medicaid': ('medicaid',)}

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'OPPORTUNITY-TO-SETTLEMENT bridge: what closing care gaps and working the outreach list adds to shared savings; '
                  'realized / forecast / modeled waterfall, savings deduplicated across work items.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'program': 'MSSP ENHANCED', 'benchmark_pmpm': 0, 'trend': 0.04, 'measures': list(DEFAULT_MEASURES),
                           'targets': {'cbp': 0.7}, 'closure_if_engaged': 0.6, 'capacity_patients': 200, 'population': 'program_default|all'},
            'meaning': 'Bridge from realized settlement (vbc.settle on cited program terms at observed completed PMPM and current quality '
                       'score) to projected: + forecast quality-gate dollars (open measure gaps x engagement x closure_if_engaged -> '
                       'projected rates vs targets -> quality multiplier) + modeled utilization dollars (outreach worklist avoidable '
                       'utilization x engagement, once per patient, flowing through the contract share). Gross savings and gap planning '
                       'values are shown, not added; naive per-work-item sum vs deduplicated total shown. Benchmark supplied or prior-year '
                       'PMPM x (1 + trend).'}
NATIVE = ('value',)


def schema(obj, string, array):
    from . import measures
    return obj({'program': string(None, 'Named program with cited settlement terms (e.g. MSSP ENHANCED, MSSP BASIC LEVEL E, ACO REACH)'),
                'benchmark_pmpm': {'type': 'number', 'minimum': 0, 'description': '0 = not supplied (prior-year PMPM x (1 + trend))'},
                'trend': {'type': 'number', 'minimum': -0.2, 'maximum': 0.3},
                'measures': array(string(sorted(measures.library())), 'Quality measures in the gate; empty = MSSP APP Plus computable set'),
                'targets': {'type': 'object', 'additionalProperties': {'type': 'number'}, 'description': 'Target rate per measure (0..1)'},
                'closure_if_engaged': {'type': 'number', 'minimum': 0, 'maximum': 1},
                'capacity_patients': {'type': 'integer', 'minimum': 1, 'maximum': 5000},
                'population': string(POPULATIONS, 'program_default: the program\'s lines of business (MSSP/REACH: Medicare FFS)')})


def validate(p):
    allowed = {'program', 'benchmark_pmpm', 'trend', 'measures', 'targets', 'closure_if_engaged', 'capacity_patients', 'population'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('settlement_bridge parameters: ' + ', '.join(sorted(allowed)))
    out = {'program': p.get('program') or 'MSSP ENHANCED', 'benchmark_pmpm': p.get('benchmark_pmpm') or 0, 'trend': p.get('trend', 0.04),
           'measures': list(dict.fromkeys(p.get('measures') or DEFAULT_MEASURES)), 'targets': dict(p.get('targets') or {}),
           'closure_if_engaged': p.get('closure_if_engaged', 0.6), 'capacity_patients': p.get('capacity_patients', 200),
           'population': p.get('population', 'program_default')}
    from . import measures, programs
    lib = measures.library()
    if any(m not in lib for m in out['measures']): raise ValueError('measures: measure-library ids')
    terms_for(out['program'])  # unknown program -> ValueError naming the known ones
    for k, lo, hi in (('benchmark_pmpm', 0, 1e6), ('trend', -0.2, 0.3), ('closure_if_engaged', 0, 1)):
        if isinstance(out[k], bool) or not isinstance(out[k], (int, float)) or not lo <= out[k] <= hi: raise ValueError(f'{k} {lo}..{hi}')
    if any(m not in lib or isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1 for m, v in out['targets'].items()):
        raise ValueError('targets: {measure id: rate 0..1}')
    if type(out['capacity_patients']) is not int or not 1 <= out['capacity_patients'] <= 5000: raise ValueError('capacity_patients 1..5000')
    if out['population'] not in POPULATIONS: raise ValueError(f'population: {POPULATIONS}')
    return out


def terms_for(name):
    """(cited contract terms, assumption or None). A program that needs an option (ACO REACH Professional/Global) gets its
    first listed option, stated as an assumption, instead of a refusal."""
    from . import programs
    try:
        return programs.contract_terms(name), None
    except ValueError:
        ref = programs.detect(name) or {'id': name}
        doc = programs.load().get(ref.get('id'))
        if not doc or not doc.get('variants') or ref.get('variant'): raise
        v = next(iter(doc['variants']))
        return programs.contract_terms({**ref, 'variant': v}), f"{doc['label']} option not stated: {v} assumed (options: {', '.join(doc['variants'])})."


def meets(rate, target, direction):
    if rate is None or target is None: return None
    return rate <= target if direction == 'lower_is_better' else rate >= target


def attainment(rate, target, direction):
    """Share of the target achieved, capped at 1 (lower-is-better: target / rate)."""
    if rate is None or target is None: return None
    if direction == 'lower_is_better': return 1.0 if rate <= target else round(target / rate, 4)
    return 1.0 if rate >= target else round(rate / target, 4) if target else 1.0


def quality_score(rows, key='rate'):
    """Mean target attainment over measures with a target (sliding-scale sharing multiplier); 1.0 when none is judged."""
    vals = [attainment(r[key], r['target'], r['direction']) for r in rows]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else 1.0


def combine_once(values, discount):
    from .outreach import combine
    return combine(values, discount)


def work_items(cands, capacity, discount=0.5):
    """Capacity-limited worklist from outreach.candidates rows -> per-patient utilization savings once, plus the naive sum."""
    chosen = sorted(cands, key=lambda r: (-r['expected_value'], r['patient_id']))[:capacity]
    per_patient, naive_util, naive_all, dedup_all = {}, 0.0, 0.0, 0.0
    for r in chosen:
        reach = r['engagement_probability']
        util = sum(c['value_if_engaged'] for c in r['reasons'] if c['code'] == 'UTIL_AVOIDABLE')
        per_patient[r['patient_id']] = {'reach': reach, 'utilization_value': util, 'reasons': [c['code'] for c in r['reasons']]}
        naive_util += reach * util * max(1, len(r['reasons']))  # utilization credited to every work item of the patient
        naive_all += reach * sum(c['value_if_engaged'] for c in r['reasons'])
        dedup_all += reach * combine_once([c['value_if_engaged'] for c in r['reasons']], discount)
    return chosen, per_patient, {'naive_utilization_per_work_item': round(naive_util, 2),
                                 'utilization_once_per_patient': round(sum(v['reach'] * v['utilization_value'] for v in per_patient.values()), 2),
                                 'naive_planning_value_all_reasons': round(naive_all, 2), 'overlap_discounted_planning_value': round(dedup_all, 2)}


def run(db, patient_ids, p, as_of):
    from . import vbc, measures, outreach, programs
    end = str(as_of)[:10]
    start = f'{int(end[:4]) - 1}{end[4:7]}-01'
    terms, option_note = terms_for(p['program'])
    pid_ = terms['program']['id']
    family = 'mssp' if pid_.startswith('mssp') else 'aco_reach' if 'reach' in pid_ else 'lead' if pid_ == 'lead' else \
        'medicaid' if 'medicaid' in pid_ else 'commercial' if 'commercial' in pid_ else None
    people = vbc.eligibility(db, end, start, end)
    if p['population'] == 'program_default' and family in PROGRAM_PRODUCTS:
        prods = PROGRAM_PRODUCTS[family]
        inline = {pid for (pid,) in db.execute(f"SELECT DISTINCT patient_id FROM member_month WHERE month BETWEEN ? AND ? AND product IN "
                                               f"({','.join('?' * len(prods))})", (start[:7], end[:7], *prods))}
        people = {k: v for k, v in people.items() if k in inline}
    if patient_ids is not None:
        keep = set(patient_ids); people = {k: v for k, v in people.items() if k in keep}
    attr = vbc.attribution(db, people, start, end)
    cost, _ = vbc._cost(db, people, attr, {'group_by': 'none'}, start, end)
    c = cost[0] if cost else {'member_months': 0, 'allowed_pmpm_completed': 0.0}
    mm, actual = c['member_months'], c['allowed_pmpm_completed']
    assumptions = ['Synthetic warehouse; settlement is scenario arithmetic on the program\'s cited terms, not a CMS reconciliation.']
    if option_note: assumptions.append(option_note)
    if p['benchmark_pmpm']: bench = float(p['benchmark_pmpm'])
    else:
        pstart, pend = f'{int(end[:4]) - 2}{end[4:7]}-01', f'{int(end[:4]) - 1}{end[4:10]}'
        prior_people = vbc.eligibility(db, pend, pstart, pend)
        prior_people = {k: v for k, v in prior_people.items() if k in people}
        prior, _ = vbc._cost(db, prior_people, attr, {'group_by': 'none'}, pstart, pend)
        base = prior[0]['allowed_pmpm_completed'] if prior and prior[0]['member_months'] else actual
        bench = round(base * (1 + p['trend']), 2)
        assumptions.append(f"Benchmark not supplied: prior 12 months' completed allowed PMPM ${base:,.2f} trended {p['trend']:.0%} = ${bench:,.2f}.")
    if not bench: bench = 1.0
    if terms.get('msr_schedule') and 'msr' not in terms:
        rate = programs.variable_msr(max(1, sum(1 for i in people.values() if i['months'])), terms['msr_schedule'])
        terms = {**terms, 'msr': rate, **({'mlr': rate} if 'mlr' not in terms else {})}
    keep = {k for k, v in people.items() if v['months']}
    # quality gate: current rates and gaps
    lib = measures.library()
    cands, _ = outreach.candidates(db, end, keep)
    reach = {r['patient_id']: r['engagement_probability'] for r in cands}
    base_reach = outreach.ASSUMPTIONS['base_reach_probability']
    qrows, declined = [], []
    ctx = measures.Context(db)
    for mid in p['measures']:
        m = lib[mid]
        rep = measures.evaluate_db(db, m, *measures.default_period(end), end, 'none', keep, output='rates', top=10 ** 6, ctx=ctx)
        if rep['declined'] or not (rep.get('overall') or {}).get('denominator'):
            declined.append({'measure': mid, 'missing_inputs': rep.get('missing_inputs') or ['no eligible patients']}); continue
        o = rep['overall']; num, den = o['numerator'], o['denominator']
        gaps = rep['care_gaps']
        closures = sum(reach.get(g['patient_id'], base_reach) for g in gaps) * p['closure_if_engaged']
        lower = m['direction'] == 'lower_is_better'
        proj_num = num - closures if lower else num + closures
        target = p['targets'].get(mid, DEFAULT_TARGETS.get(mid))
        qrows.append({'measure': mid, 'name': m['name'], 'direction': m['direction'], 'numerator': num, 'denominator': den,
                      'rate': round(num / den, 4), 'open_gaps': len(gaps), 'expected_closures': round(closures, 1),
                      'projected_rate': round(proj_num / den, 4), 'target': target,
                      'target_source': 'supplied' if mid in p['targets'] else 'default planning target' if target is not None else 'none',
                      'meets_target': meets(num / den, target, m['direction']), 'projected_meets_target': meets(proj_num / den, target, m['direction']),
                      'gap_patient_ids': sorted({g['patient_id'] for g in gaps}), 'label': 'forecast'})
    q_now, q_proj = quality_score(qrows), quality_score(qrows, 'projected_rate')
    # utilization: capacity-limited worklist, once per patient
    chosen, per_patient, dedupe = work_items(cands, p['capacity_patients'])
    gross_util = dedupe['utilization_once_per_patient']
    actual_new = max(0.0, actual - gross_util / mm) if mm else actual
    t_now = {**terms, 'quality_score': q_now}; t_proj = {**terms, 'quality_score': q_proj}
    realized = vbc.settle(actual, bench, mm, t_now) if mm else 0.0
    after_quality = vbc.settle(actual, bench, mm, t_proj) if mm else 0.0
    projected = vbc.settle(actual_new, bench, mm, t_proj) if mm else 0.0
    waterfall = [{'step': 'Current settlement at observed spend and current quality score', 'label': 'realized', 'dollars': round(realized, 2)},
                 {'step': f'Quality gate: quality score {q_now:.2f} -> {q_proj:.2f} from expected gap closures', 'label': 'forecast',
                  'dollars': round(after_quality - realized, 2)},
                 {'step': f'Avoidable utilization from {len(chosen)} worked patients (contract share of ${gross_util:,.0f} gross)', 'label': 'modeled',
                  'dollars': round(projected - after_quality, 2)},
                 {'step': 'Projected settlement', 'label': 'projected', 'dollars': round(projected, 2)}]
    gap_patients = {pid for r in qrows for pid in r['gap_patient_ids']}
    work_ids = sorted(gap_patients | set(per_patient))
    if declined: assumptions.append('Measures not computable from the data and left out of the quality gate: ' + ', '.join(d['measure'] for d in declined) + '.')
    assumptions += [f"Gap closure = engagement probability (outreach reachability) x closure_if_engaged {p['closure_if_engaged']:.0%}.",
                    'Quality score = mean target attainment (rate / target, capped at 1; lower-is-better: target / rate) over computed measures, '
                    'used as the sharing-rate multiplier (sliding scale); default targets are '
                    'planning targets, not CMS quality benchmarks.',
                    'Utilization savings = outreach avoidable-utilization planning assumptions (relative reduction x unit cost) x engagement, '
                    'once per patient; not a measured effect.',
                    'Care-gap planning values are not settlement dollars and are not added (they earn money only through the quality gate).']
    return {'schema': VERSION, 'as_of': end, 'parameters': p, 'period': {'start': start, 'end': end},
            'program': terms['program']['label'], 'savings_cap_pct': terms.get('savings_cap_pct'), 'member_months': mm, 'actual_pmpm': actual, 'benchmark_pmpm': bench,
            'projected_actual_pmpm': round(actual_new, 2), 'quality_score': {'current': q_now, 'projected': q_proj},
            'waterfall': waterfall, 'measures': [{k: v for k, v in r.items() if k != 'gap_patient_ids'} for r in qrows], 'declined': declined,
            'gross_utilization_savings': {'dollars': round(gross_util, 2), 'label': 'modeled', 'counted_in_settlement': 'contract share only'},
            'dedupe': {**dedupe, 'adjustment_utilization': round(dedupe['naive_utilization_per_work_item'] - gross_util, 2),
                       'adjustment_planning_value': round(dedupe['naive_planning_value_all_reasons'] - dedupe['overlap_discounted_planning_value'], 2)},
            'work_patients': len(work_ids), 'patient_ids': work_ids, 'assumptions': assumptions + list(terms['program'].get('assumptions') or []),
            'method': ('Waterfall: realized = vbc.settle(observed completed allowed PMPM, benchmark, member months, cited terms, current quality '
                       'score); forecast quality gate = settlement with the projected quality score minus realized; modeled utilization = '
                       'settlement at PMPM lowered by once-per-patient expected avoidable-utilization savings minus the forecast step. '
                       'Steps sum exactly to the projected settlement.')}


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
    w = {x['label']: x['dollars'] for x in r['waterfall']}
    d = r['dedupe']
    return (f"{r['program']}: realized settlement ${w['realized']:,.0f} -> projected ${w['projected']:,.0f} "
            f"(forecast quality gate {w['forecast']:+,.0f}, quality score {r['quality_score']['current']:.2f} -> {r['quality_score']['projected']:.2f}; "
            f"modeled utilization {w['modeled']:+,.0f} from ${r['gross_utilization_savings']['dollars']:,.0f} gross savings). "
            f"Actual ${r['actual_pmpm']:,.2f} vs benchmark ${r['benchmark_pmpm']:,.2f} PMPM over {r['member_months']:,} member months; "
            f"dedupe removed ${d['adjustment_utilization']:,.0f} of utilization double-counted across work items"
            + (f"; realized savings already sit at the program's {float(r['savings_cap_pct']):.0%} savings cap, so further quality or "
               f"utilization gains add no settlement dollars under these terms" if r.get('savings_cap_pct') is not None and w['realized'] > 0
               and abs(w['realized'] - r['benchmark_pmpm'] * r['member_months'] * float(r['savings_cap_pct'])) < 1 else ''))


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['waterfall'] + report['measures'],
            'patient_ids': report['patient_ids'], 'count': report['work_patients'], 'value': headline(report) + '.', 'method': report['method'],
            'limitations': report['assumptions'], 'number': report['waterfall'][-1]['dollars'], 'membership_role': 'contributing',
            'definitions': [{'term': 'realized / forecast / modeled', 'basis': 'realized = current settlement arithmetic; forecast = quality-gate '
                                                                               'change from projected gap closures; modeled = contract share of '
                                                                               'assumption-based utilization savings'}],
            'overall': {'settlement_bridge': {k: report[k] for k in ('waterfall', 'quality_score', 'dedupe', 'gross_utilization_savings')}}}


# ---- free local-compiler route ----------------------------------------------------------------------------------------
WORK = (r'\bclos\w* (?:the |our |all |those |these )?(?:care |quality |measure )?gaps\b|\bgap closures?\b|\boutreach\b|\bworklist\b|\bwork list\b|'
        r'\bcare management\b|\bwork(?:ing)? the list\b|\bquality (?:improvement|gate|score)\b')
MONEY = r'\bsettlement\b|\bshared savings\b|\bworth\b|\bdollars?\b|\bfinancial (?:impact|bridge)\b|\bbridge\b|\bwaterfall\b|\bpayout\b|\bearn\b|\$'


def route(question, profile, as_of):
    from .engine_suite import plan
    from . import programs
    low = ' '.join(str(question).lower().split())
    bridge = re.search(r'\bopportunity[- ]to[- ]settlement\b|\bsettlement bridge\b|\bsettlement waterfall\b', low)
    if not bridge and not (re.search(WORK, low) and re.search(MONEY, low)): return None
    if re.match(r'(?:what (?:is|are)|explain|define|how (?:does|do)\b.*\bwork)', low) and not re.search(r'\b(?:our|we|my|us)\b', low): return None
    params = {}
    text = _program_text(low) if programs.detect(question) else ''
    if text:
        try:
            terms_for(text); params['program'] = text
        except ValueError:
            pass  # a track that needs a level (MSSP BASIC) keeps the default; the planner can supply it
    b = re.search(r'\bbenchmark (?:of |is |at )?\$\s?(\d[\d,]*(?:\.\d+)?)|\$\s?(\d[\d,]*(?:\.\d+)?)\s*(?:pmpm )?benchmark\b', low)
    if b: params['benchmark_pmpm'] = float((b.group(1) or b.group(2)).replace(',', ''))
    cap = re.search(r'\b(\d{1,4})\s+(?:patients|members|people)\b', low)
    if cap and 1 <= int(cap.group(1)) <= 5000: params['capacity_patients'] = int(cap.group(1))
    return plan(OP, params, 'Opportunity-to-settlement waterfall: realized, forecast quality gate, modeled utilization', question,
                [f"program {params.get('program', 'MSSP ENHANCED (default)')}"])


def _program_text(low):
    """The program phrase as the rulebook detects it (track and level words kept)."""
    m = re.search(r'\b(?:mssp|medicare shared savings program|shared savings program)(?: (?:basic|enhanced))?(?: (?:track|level [a-e]))?(?: level [a-e])?\b|\baco reach\b|\breach\b|\blead\b|\benhanced track\b|\bbasic track(?: level [a-e])?\b', low)
    return m.group(0).upper() if m else ''


TEMPLATES = ['What is closing our open care gaps worth in shared savings dollars?', 'Bridge from our current settlement to projected if we work the outreach list',
             'how much of the settlement comes from quality gate vs utilization if we close gaps', 'settlement waterfall for the MSSP enhanced track',
             'Translate our care-gap work into shared savings under a $1,050 benchmark', 'opportunity to settlement for 150 patients of outreach',
             'What would gap closure add to our payout this year?', 'show forecast vs modeled vs realized savings from our worklist']
