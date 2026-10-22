"""Medicare Advantage Star Rating projection: measure stars vs cut points, weighted overall rating, QBP, what-if and P(>= 4 stars).

Measure rates come from the measure library (measures.evaluate_db, computable HEDIS / Part D approximations) on Medicare
Advantage members (member_month product medicare_advantage or dual_ma in the measurement year; population='all' uses
everyone). Each library measure maps to its Star measure, weight (42 CFR 422.166(e): outcome / intermediate outcome 3,
process 1; config/agere/programs/ma_star_ratings.json measure_weights) and cut points. GSD poor control (>9%, lower is
better) is scored as its complement (glycemic status <= 9%); the four TRC components form the TRC composite (mean rate).

CUT POINTS ARE APPROXIMATIONS. CMS publishes measure thresholds yearly (Star Ratings Technical Notes, Attachment B;
clustering with mean resampling and guardrails, 42 CFR 422.166(a)(2)). The rulebook does not store them, so CUT_POINTS below
are rounded illustrative thresholds in the range of recent published Part C/D cut points (2025-2026 Technical Notes), not
the official table for any year; pass cut_points to use the official values. PCR is scored on the observed readmission rate
(CMS scores an observed-to-expected ratio). Overall rating = weighted mean of computable measure stars rounded to the
nearest half star (422.166(f)); the reward factor and CAHPS / HOS / complaints / improvement / administrative measures are not
computable from the warehouse and are excluded (their weight is listed). QBP: 4+ stars -> 5% (10% in double-bonus counties)
added to the benchmark (42 CFR 422.258); rebate share 50/65/70% by stars (422.266) from the rulebook.
"""
from collections import defaultdict
from pathlib import Path
import math
import re
import sqlite3

VERSION = 'star-ratings/1'
OP = 'star_rating'
# library id -> (Star measure, part, kind, weight, (2,3,4,5-star thresholds in %), direction of the scored rate)
STAR_MEASURES = {
    'bcs': ('C: Breast Cancer Screening', 'C', 'process', 1, (53, 63, 72, 80), 'higher'),
    'col': ('C: Colorectal Cancer Screening', 'C', 'process', 1, (52, 63, 72, 80), 'higher'),
    'cbp': ('C: Controlling Blood Pressure', 'C', 'intermediate_outcome', 3, (56, 66, 74, 82), 'higher'),
    'gsd_poor_control': ('C: Glycemic Status Assessment (<= 9%)', 'C', 'intermediate_outcome', 3, (58, 70, 80, 88), 'higher'),
    'ked': ('C: Kidney Health Evaluation for Patients with Diabetes', 'C', 'process', 1, (32, 44, 54, 65), 'higher'),
    'spc_statin_cvd': ('C: Statin Therapy for Patients with Cardiovascular Disease', 'C', 'process', 1, (80, 84, 87, 90), 'higher'),
    'fmc_ed_followup': ('C: Follow-up after ED Visit for People with Multiple High-Risk Chronic Conditions', 'C', 'process', 1, (38, 47, 55, 63), 'higher'),
    'trc': ('C: Transitions of Care (composite)', 'C', 'process', 1, (45, 58, 68, 78), 'higher'),
    'pcr_readmissions': ('C: Plan All-Cause Readmissions (observed rate)', 'C', 'outcome', 3, (14, 12, 10, 8), 'lower'),
    'pdc_diabetes': ('D: Medication Adherence for Diabetes Medications', 'D', 'intermediate_outcome', 3, (80, 85, 88, 91), 'higher'),
    'pdc_rasa': ('D: Medication Adherence for Hypertension (RAS antagonists)', 'D', 'intermediate_outcome', 3, (82, 86, 89, 92), 'higher'),
    'pdc_statins': ('D: Medication Adherence for Cholesterol (Statins)', 'D', 'intermediate_outcome', 3, (80, 85, 88, 91), 'higher'),
    'supd_statin_use_diabetes': ('D: Statin Use in Persons with Diabetes', 'D', 'process', 1, (79, 83, 86, 89), 'higher'),
}
TRC_PARTS = ('trc_notification_inpatient_admission', 'trc_receipt_discharge_information', 'trc_patient_engagement', 'trc_medication_reconciliation')
NOT_COMPUTABLE = [('CAHPS member experience measures', 2, 'survey not received'), ('HOS outcome measures', 3, 'survey not received'),
                  ('Complaints, appeals, call center, members leaving the plan', 2, 'plan administrative data not received'),
                  ('Part C / Part D improvement measures', 5, 'need the prior-year official measure scores'),
                  ('Other HEDIS measures without a computable library definition (e.g. osteoporosis management, care for older adults)', 1,
                   'no library definition')]
POPULATIONS = ('medicare_advantage', 'all')
MA_PRODUCTS = ('medicare_advantage', 'dual_ma')

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'MEDICARE STAR RATINGS projection: measure stars vs cut points, overall star, quality bonus (QBP), gap closures '
                  'needed for the next cut point or 4 stars, probability of reaching 4 stars.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'measures': [], 'population': 'medicare_advantage|all', 'period': {'start': '', 'end': ''},
                           'closure_rate': 0.25, 'target_stars': 4.0, 'benchmark_pmpm': 0, 'double_bonus_county': False,
                           'cut_points': {'cbp': [56, 66, 74, 82]}, 'draws': 2000},
            'meaning': 'Star projection on MA members: library measure rates (with numerator/denominator) -> measure stars by cut points '
                       '(approximate thresholds unless cut_points supplied) and weights (outcome/intermediate 3, process 1; 42 CFR 422.166) -> '
                       'weighted overall rounded to the half star; projected year-end rates if closure_rate of open gaps close; per-measure '
                       'patients needed for the next cut point and the cheapest path to target_stars; P(overall >= target) from Jeffreys '
                       'beta draws; QBP 5%/10% at 4+ stars (422.258) and rebate share (422.266), in dollars when benchmark_pmpm is given.'}
NATIVE = ('value',)


def schema(obj, string, array):
    return obj({'measures': array(string(sorted(STAR_MEASURES)), 'Star measures to project; empty = every computable one'),
                'population': string(POPULATIONS, 'medicare_advantage (default): MA and dual MA members; all: every member'),
                'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                'closure_rate': {'type': 'number', 'minimum': 0, 'maximum': 1, 'description': 'Share of open gaps assumed closed by year end (default 0.25)'},
                'target_stars': {'type': 'number', 'minimum': 1, 'maximum': 5},
                'benchmark_pmpm': {'type': 'number', 'minimum': 0, 'description': 'MA benchmark PMPM for QBP dollars (0 = not supplied)'},
                'double_bonus_county': {'type': 'boolean'},
                'cut_points': {'type': 'object', 'additionalProperties': {'type': 'array', 'items': {'type': 'number'}},
                               'description': 'Official 2/3/4/5-star thresholds in % per measure (overrides the approximations)'},
                'draws': {'type': 'integer', 'minimum': 100, 'maximum': 20000}})


def validate(p):
    allowed = {'measures', 'population', 'period', 'closure_rate', 'target_stars', 'benchmark_pmpm', 'double_bonus_county', 'cut_points', 'draws'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('star_rating parameters: ' + ', '.join(sorted(allowed)))
    out = {'measures': sorted(set(p.get('measures') or [])), 'population': p.get('population', 'medicare_advantage'),
           'period': p.get('period') or {}, 'closure_rate': p.get('closure_rate', 0.25), 'target_stars': p.get('target_stars', 4.0),
           'benchmark_pmpm': p.get('benchmark_pmpm') or 0, 'double_bonus_county': bool(p.get('double_bonus_county', False)),
           'cut_points': p.get('cut_points') or {}, 'draws': p.get('draws', 2000)}
    if any(m not in STAR_MEASURES for m in out['measures']): raise ValueError(f'measures: subset of {sorted(STAR_MEASURES)}')
    if out['population'] not in POPULATIONS: raise ValueError(f'population: {POPULATIONS}')
    per = out['period']
    if not isinstance(per, dict) or set(per) - {'start', 'end'} or any(v and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(v)) for v in per.values()):
        raise ValueError('period: {start, end} YYYY-MM-DD')
    for k, lo, hi in (('closure_rate', 0, 1), ('target_stars', 1, 5)):
        if isinstance(out[k], bool) or not isinstance(out[k], (int, float)) or not lo <= out[k] <= hi: raise ValueError(f'{k} {lo}..{hi}')
    if isinstance(out['benchmark_pmpm'], bool) or not isinstance(out['benchmark_pmpm'], (int, float)) or out['benchmark_pmpm'] < 0:
        raise ValueError('benchmark_pmpm >= 0')
    for m, cuts in out['cut_points'].items():
        if m not in STAR_MEASURES or not isinstance(cuts, list) or len(cuts) != 4 or \
                any(isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 100 for c in cuts):
            raise ValueError('cut_points: {measure: [2-star, 3-star, 4-star, 5-star thresholds in %]}')
        d = STAR_MEASURES[m][5]
        if (d == 'higher' and cuts != sorted(cuts)) or (d == 'lower' and cuts != sorted(cuts, reverse=True)):
            raise ValueError(f'cut_points for {m} must be monotone in the scoring direction')
    if type(out['draws']) is not int or not 100 <= out['draws'] <= 20000: raise ValueError('draws 100..20000')
    return out


def cuts_for(mid, p): return tuple(p['cut_points'].get(mid) or STAR_MEASURES[mid][4])


def stars(rate_pct, cuts, direction):
    """1..5 measure stars: the highest threshold the rate reaches."""
    if rate_pct is None: return None
    return 1 + sum(1 for c in cuts if (rate_pct >= c if direction == 'higher' else rate_pct <= c))


def round_half(x): return math.floor(x * 2 + 0.5) / 2


def overall(measure_stars):
    """[(stars, weight)] -> (weighted mean, rounded half star)."""
    w = sum(wt for s, wt in measure_stars if s is not None)
    if not w: return None, None
    mean = sum(s * wt for s, wt in measure_stars if s is not None) / w
    return round(mean, 3), round_half(mean)


def needed(num, den, cut, direction):
    """Additional numerator patients (higher) or fewer events (lower) to reach a threshold (in %)."""
    if not den: return None
    if direction == 'higher': return max(0, math.ceil(cut / 100 * den - num - 1e-9))
    return max(0, num - math.floor(cut / 100 * den + 1e-9))


def score_rows(rates, p):
    """rates: {mid: {'numerator','denominator','gaps'}} -> per-measure rows with current and projected stars and the next cut."""
    rows = []
    for mid, r in sorted(rates.items()):
        name, part, kind, weight, _, direction = STAR_MEASURES[mid]
        cuts, num, den = cuts_for(mid, p), r['numerator'], r['denominator']
        if not den: continue
        rate = 100 * num / den
        scored = rate
        s = stars(scored, cuts, direction)
        gaps = r.get('gaps', den - num if direction == 'higher' else num)
        moved = gaps * p['closure_rate']
        proj_num = num + moved if direction == 'higher' else num - moved
        proj = 100 * proj_num / den
        ps = stars(proj, cuts, direction)
        nxt = cuts[s - 1] if s < 5 else None
        rows.append({'measure': mid, 'star_measure': name, 'part': part, 'kind': kind, 'weight': weight, 'numerator': num, 'denominator': den,
                     'rate_pct': round(scored, 2), 'stars': s, 'cut_points_pct': list(cuts), 'open_gaps': gaps,
                     'projected_rate_pct': round(proj, 2), 'projected_stars': ps,
                     'next_cut_pct': nxt, 'patients_to_next_star': needed(num, den, nxt, direction) if nxt is not None else 0,
                     'direction': direction, 'cut_point_source': 'supplied' if mid in p['cut_points'] else 'approximation'})
    return rows


def path_to_target(rows, target):
    """Cheapest measure upgrades (weight gained per patient needed) until the rounded overall reaches target."""
    state = {r['measure']: r['stars'] for r in rows}
    weights = {r['measure']: r['weight'] for r in rows}
    steps = []
    for _ in range(4 * len(rows)):
        cur = overall([(state[m], weights[m]) for m in state])[1]
        if cur is None or cur >= target: break
        best = None
        for r in rows:
            s = state[r['measure']]
            if s >= 5: continue
            cut = cuts_for_row(r)[s - 1]
            n = needed(r['numerator'], r['denominator'], cut, r['direction'])
            if n is None: continue
            score = r['weight'] / max(n, 1)
            if best is None or score > best[0]: best = (score, r, s + 1, n, cut)
        if best is None: break
        _, r, s2, n, cut = best
        state[r['measure']] = s2
        steps = [x for x in steps if x['measure'] != r['measure']] + [{'measure': r['measure'], 'to_stars': s2, 'cut_pct': cut, 'patients': n}]
    final = overall([(state[m], weights[m]) for m in state])
    return {'steps': steps, 'patients_total': sum(x['patients'] for x in steps), 'reaches_overall': final[1],
            'feasible': final[1] is not None and final[1] >= target}


def cuts_for_row(r): return tuple(r['cut_points_pct'])


def probability_at_least(rows, target, draws, seed=11, projected=False, closure_rate=0.0):
    """P(rounded overall >= target): measure rates drawn from Jeffreys Beta(num + .5, den - num + .5)."""
    import numpy as np
    if not rows: return None
    rng = np.random.default_rng(seed)
    total_w = sum(r['weight'] for r in rows)
    score = np.zeros(draws)
    for r in rows:
        num = r['numerator']
        if projected: num = num + r['open_gaps'] * closure_rate if r['direction'] == 'higher' else num - r['open_gaps'] * closure_rate
        rate = 100 * rng.beta(num + 0.5, r['denominator'] - num + 0.5, draws)
        cuts = np.array(r['cut_points_pct'])
        s = 1 + ((rate[:, None] >= cuts[None, :]) if r['direction'] == 'higher' else (rate[:, None] <= cuts[None, :])).sum(1)
        score += s * r['weight']
    rounded = np.floor(score / total_w * 2 + 0.5) / 2
    return round(float((rounded >= target).mean()), 4)


def qbp(star, p, member_months):
    from . import programs
    pct, rebate = programs.qbp_bonus_pct(star, p['double_bonus_county']), programs.rebate_pct(star)
    out = {'stars': star, 'qbp_bonus_pct': pct, 'rebate_share': rebate}
    if p['benchmark_pmpm']: out['qbp_dollars'] = round(pct * p['benchmark_pmpm'] * member_months, 2)
    return out


def ma_members(db, start, end):
    mm = defaultdict(int)
    for pid, n in db.execute(f"SELECT patient_id, count(*) FROM member_month WHERE month BETWEEN ? AND ? AND product IN "
                             f"({','.join('?' * len(MA_PRODUCTS))}) GROUP BY 1", (start[:7], end[:7], *MA_PRODUCTS)):
        mm[pid] = n
    return dict(mm)


def rates(db, wanted, start, end, as_of, keep):
    """{mid: {'numerator','denominator','gaps','reading'}} and declined measures."""
    from . import measures
    lib, ctx = measures.library(), measures.Context(db)
    out, declined = {}, []
    for mid in wanted:
        parts = TRC_PARTS if mid == 'trc' else (mid,)
        reps = [measures.evaluate_db(db, lib[x], start, end, as_of, 'none', keep, output='rates', top=0, ctx=ctx) for x in parts if x in lib]
        if not reps or any(r['declined'] for r in reps):
            declined.append({'measure': mid, 'missing_inputs': [m for r in reps for m in r.get('missing_inputs', [])] or ['no library definition']})
            continue
        if mid == 'trc':  # composite: mean of component rates, expressed on the pooled denominator
            dens = [r['overall']['denominator'] for r in reps]
            if not all(dens): declined.append({'measure': mid, 'missing_inputs': ['a TRC component has no denominator']}); continue
            den = round(sum(dens) / len(dens))
            rate = sum(r['overall']['numerator'] / r['overall']['denominator'] for r in reps) / len(reps)
            out[mid] = {'numerator': round(rate * den), 'denominator': den, 'reading': '; '.join(r['reading'] for r in reps)}
            continue
        o = reps[0]['overall']
        num, den = o.get('numerator'), o.get('denominator')
        if not den: declined.append({'measure': mid, 'missing_inputs': ['no eligible members in the period']}); continue
        if mid == 'gsd_poor_control': num = den - num  # scored as glycemic status <= 9% (complement of poor control)
        out[mid] = {'numerator': num, 'denominator': den, 'reading': reps[0]['reading']}
    return out, declined


def run(db, patient_ids, p, as_of):
    from . import measures
    s0, e0 = measures.default_period(as_of)
    start, end = (p['period'].get('start') or s0)[:10], (p['period'].get('end') or e0)[:10]
    members = ma_members(db, start, end)
    keep = set(members) if p['population'] == 'medicare_advantage' else None
    if patient_ids is not None: keep = (keep & set(patient_ids)) if keep is not None else set(patient_ids)
    wanted = p['measures'] or sorted(STAR_MEASURES)
    rs, declined = rates(db, wanted, start, end, as_of, keep)
    rows = score_rows(rs, p)
    cur = overall([(r['stars'], r['weight']) for r in rows])
    proj = overall([(r['projected_stars'], r['weight']) for r in rows])
    mm = sum(v for k, v in members.items() if keep is None or k in keep) if members else 0
    excluded_weight = sum(w for _, w, _ in NOT_COMPUTABLE) + sum(STAR_MEASURES[d['measure']][3] for d in declined)
    report = {'schema': VERSION, 'as_of': str(as_of)[:10], 'parameters': p, 'period': {'start': start, 'end': end},
              'population': {'basis': p['population'], 'patients': len(keep) if keep is not None else None, 'ma_member_months': mm},
              'rows': rows, 'declined': declined, 'not_computable': [{'measures': n, 'weight': w, 'reason': r} for n, w, r in NOT_COMPUTABLE],
              'overall': {'weighted_mean': cur[0], 'stars': cur[1], 'projected_weighted_mean': proj[0], 'projected_stars': proj[1]},
              'qbp_current': qbp(cur[1], p, mm) if cur[1] is not None else None,
              'qbp_projected': qbp(proj[1], p, mm) if proj[1] is not None else None,
              'path_to_target': path_to_target(rows, p['target_stars']) if rows else None,
              'p_reach_target_current': probability_at_least(rows, p['target_stars'], p['draws']),
              'p_reach_target_projected': probability_at_least(rows, p['target_stars'], p['draws'], projected=True, closure_rate=p['closure_rate']),
              'excluded_weight': excluded_weight}
    report['assumptions'] = [
        'Synthetic warehouse; measure rates are computable approximations from the measure library, not certified HEDIS / PDE results.',
        'Cut points are approximate illustrative thresholds (not the official Technical Notes table for any year) unless supplied as cut_points.',
        f"Projected rates assume {p['closure_rate']:.0%} of each measure's open gaps close by year end.",
        'PCR scored on the observed readmission rate (CMS uses observed-to-expected); GSD scored as the complement of poor control (>9%).',
        f"Not computable and excluded from the weighted mean (weight {excluded_weight}): CAHPS, HOS, administrative, improvement measures"
        + (', and declined: ' + ', '.join(d['measure'] for d in declined) if declined else '') + '; the reward factor is not applied.',
        'Overall rating rounds the weighted mean of computable measure stars to the nearest half star; real contract ratings also need the excluded measures.']
    report['method'] = ('Measure stars from cut points; overall = weighted mean (outcome/intermediate outcome 3, process 1; 42 CFR 422.166(e)) '
                        'rounded to the half star; QBP 5% (10% double-bonus counties) at 4+ stars (42 CFR 422.258), rebate share 50/65/70% '
                        '(422.266); P(>= target) from 2,000 Jeffreys beta draws of every measure rate; what-if = patients needed per next cut point '
                        'and the cheapest set of upgrades (weight per patient) to reach the target.')
    return report


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
    o = r['overall']
    if o['stars'] is None: return 'No Star measure was computable for this population'
    path = r['path_to_target'] or {}
    q = r['qbp_current'] or {}
    tgt = r['parameters']['target_stars']
    steps = ', '.join(f"{s['measure']} +{s['patients']} to {s['to_stars']} stars" for s in path.get('steps', [])[:4])
    money = f" (${q['qbp_dollars']:,.0f})" if q.get('qbp_dollars') is not None else ''
    return (f"Projected overall {o['stars']:.1f} stars now (weighted {o['weighted_mean']}) and {o['projected_stars']:.1f} at year end "
            f"if {r['parameters']['closure_rate']:.0%} of open gaps close, across {len(r['rows'])} computable measures; QBP "
            f"{q.get('qbp_bonus_pct', 0):.0%}{money}, rebate share {q.get('rebate_share', 0):.0%}. P(>= {tgt:g} stars): "
            f"{r['p_reach_target_current']:.0%} now, {r['p_reach_target_projected']:.0%} projected"
            + (f"; cheapest path to {tgt:g}: {steps} ({path['patients_total']} patients)" if steps else ''))


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    out = {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['rows'], 'patient_ids': [],
           'value': headline(report) + '.', 'method': report['method'], 'limitations': report['assumptions'],
           'definitions': [{'term': 'overall Star Rating', 'basis': 'weighted mean of measure stars rounded to the half star (42 CFR 422.166(f)); '
                                                                     'QBP at 4+ stars (42 CFR 422.258)'}],
           'membership_role': 'evaluated', 'overall': {'star_rating': {k: report[k] for k in ('overall', 'qbp_current', 'qbp_projected',
                                                                                              'path_to_target', 'p_reach_target_current',
                                                                                              'p_reach_target_projected', 'declined')}}}
    if report['overall']['stars'] is not None: out['number'] = report['overall']['stars']
    return out


# ---- free local-compiler route ----------------------------------------------------------------------------------------
STAR_RX = r'\bstars? ratings?\b|\bstar (?:score|bonus|measures?|cut ?points?|projection)\b|\b(?:4|5|four|five|3\.5|4\.5)[- ]stars?\b|\bqbp\b|\bquality bonus payments?\b|\bcut ?points?\b|\b(?:up|raise|lift) (?:by )?(?:a|one|1) stars?\b|\bland on stars\b|\bstars? (?:this|next) year\b'


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(STAR_RX, low): return None
    if re.match(r'(?:what (?:is|are)|explain|define|how (?:does|do|is|are)\b.*\b(?:work|calculated|computed|determined))', low) and \
            not re.search(r'\b(?:our|my|we|us)\b', low): return None  # explanation of the program stays knowledge
    params = {}
    t = re.search(r'\b(3\.5|4\.5|4|5|four|five)[- ]stars?\b', low)
    if t: params['target_stars'] = {'four': 4.0, 'five': 5.0}.get(t.group(1), None) or float(t.group(1))
    named = [m for m, rx in (('bcs', r'\bbreast cancer screening\b|\bmammogra\w*\b'), ('col', r'\bcolorectal\b|\bcolon cancer screening\b'),
                             ('cbp', r'\bblood pressure\b|\bcbp\b'), ('gsd_poor_control', r'\ba1c\b|\bglyc\w*\b|\bdiabetes control\b'),
                             ('pdc_statins', r'\bstatin adherence\b'), ('pdc_rasa', r'\bras(?:a)? adherence\b|\bhypertension adherence\b'),
                             ('pdc_diabetes', r'\bdiabetes (?:medication )?adherence\b'), ('pcr_readmissions', r'\breadmission\w*\b'),
                             ('trc', r'\btransitions? of care\b|\btrc\b'), ('ked', r'\bkidney health\b|\bked\b')) if re.search(rx, low)]
    if named and re.search(r'\bonly\b|\bjust\b', low): params['measures'] = named
    if named and not re.search(r'\bstar ratings?\b|\boverall\b|\bqbp\b|\bquality bonus\b|\bget (?:us )?to\b|\breach\b|\bproject\w*\b|\bforecast\b|\bup (?:a|one|1) stars?\b', low):
        return None  # one measure's distance to its cut point stays with the vbc measure answer
    c = re.search(r'\b(\d{1,3})\s?%\s+of (?:(?:the|our) )?(?:open )?gaps\b|\bclos\w* (\d{1,3})\s?%', low)
    if c and 0 <= int(c.group(1) or c.group(2)) <= 100: params['closure_rate'] = int(c.group(1) or c.group(2)) / 100
    b = re.search(r'\bbenchmark (?:of )?\$\s?(\d[\d,]*(?:\.\d+)?)|\$\s?(\d[\d,]*(?:\.\d+)?)\s*(?:pmpm )?benchmark\b', low)
    if b: params['benchmark_pmpm'] = float((b.group(1) or b.group(2)).replace(',', ''))
    if re.search(r'\bdouble[- ]bonus\b', low): params['double_bonus_county'] = True
    if re.search(r'\ball (?:members|patients|lines)\b|\bwhole population\b', low): params['population'] = 'all'
    return plan(OP, params, 'Medicare Star Rating projection vs cut points with QBP, what-if and P(target stars)', question,
                [f"target {params.get('target_stars', 4.0)}"])


TEMPLATES = ['Where will our MA plan land on stars this year?', 'Which measures keep us from a 4-star rating?',
             'How many more mammograms do we need to move breast cancer screening up a star?', 'odds we earn the quality bonus payment',
             'Star rating forecast if we close 40% of open gaps', 'what is our projected star score for medicare advantage',
             'Are we at risk of dropping below four stars?', 'Estimate QBP dollars at a $1,100 benchmark']
