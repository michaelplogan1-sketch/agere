"""Compare a simulation with what actually happened, once the horizon has passed.

    report = compare_to_actual(semantic_path, simulation, observed_through='2029-06-01')

1. Do-nothing calibration. Nobody in the workspace actually went through the simulated program, so the observed cohort is
   the do-nothing world plus whatever care happened anyway. Observed admissions, ED visits and medical spending over
   [as_of, observed_through) are compared with the simulation's do-nothing distribution (percentile of the observed value,
   inside the 90% interval or not), raw and adjusted for the member-months actually observed (deaths, disenrollment).
2. Observed exposure. Patients whose data show the intervention's active ingredient after as_of (a fill of a target GDMT
   pillar; a PT claim line after the ED low back pain visit -- the same evidence plans/completion.py uses to close a work
   item) are "treated in the data"; the rest are not.
3. Predicted vs observed effect. For each group: observed events vs the simulator's do-nothing expectation (O/E with 95%
   intervals); for the treated group also the simulator's prediction given each patient's observed start date and
   started pillars (point parameters). The risk-adjusted observed effect is (O/E treated) / (O/E untreated); the
   simulator's predicted effect for the same patients is shown beside it.
4. Engine estimate. For GDMT the program_effect engine (matched difference-in-differences, new-user design) estimates the
   observed effect of starting the most-started pillar's drug class on admissions among HF patients, independently of the
   simulator's baseline.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import json
import math
from pathlib import Path
import sqlite3
import tempfile

import numpy as np

from ..plans import catalog as catmod, completion
from . import effects as fx
from .data import History, add_months, day

PILLAR_CLASS = {'sglt2i': 'sglt2_inhibitor', 'mra': 'mra', 'beta_blocker': 'hf_evidence_based_beta_blocker', 'rasi': 'ace_inhibitor'}


def _poisson_ci(o, e):
    """O/E with an exact-style 95% interval (Byar's approximation)."""
    if e <= 0:
        return None, [None, None]
    lo = 0.0 if o == 0 else o * (1 - 1 / (9 * o) - 1.96 / (3 * math.sqrt(o))) ** 3
    o1 = o + 1
    hi = o1 * (1 - 1 / (9 * o1) + 1.96 / (3 * math.sqrt(o1))) ** 3
    return round(o / e, 4), [round(lo / e, 4), round(hi / e, 4)]


def _pct(q, x):
    """Percentile of x within stored quantiles q (101 points)."""
    q = np.asarray(q, float)
    if x <= q[0]:
        return 0.0
    if x >= q[-1]:
        return 100.0
    return round(float(np.interp(x, q, np.linspace(0, 100, len(q)))), 1)


def _share(measured):
    k, n = measured
    return (k + 0.5) / (n + 1.0) if n else 0.0


def compare_to_actual(semantic_path, sim, observed_through=None, *, catalog=None, engine=True):
    cat = catalog or catmod.load()
    inp = sim['inputs']
    A = day(sim['as_of'])
    H = int(sim['horizon_months'])
    end = day(observed_through) if observed_through else add_months(A, H)
    h = History.load(semantic_path)
    if h.data_end is not None and end > h.data_end + 1:
        raise ValueError(f'The workspace data run through {date.fromordinal(h.data_end).isoformat()}; cannot observe through '
                         f'{date.fromordinal(end).isoformat()}')
    if end <= A:
        raise ValueError('observed_through must be after the simulation as_of')
    months = (end - A) / 30.44
    cohort = inp['cohort']
    ra, re_, rc = (np.asarray(inp['rates'][k], float) for k in ('admission', 'ed', 'other_cost'))
    ca, ce = inp['cost_per_event']['admission'], inp['cost_per_event']['ed']
    kind = sim['program_kind']
    gap_type = catmod.gap_type_for(cat, kind)
    gcfg = cat['gaps'].get(gap_type) or {}
    db = sqlite3.connect(f'file:{Path(semantic_path)}?mode=ro', uri=True)
    rows = []
    try:
        a_d, e_d = date.fromordinal(A), date.fromordinal(end - 1)
        for i, pid in enumerate(cohort):
            e = h.enrolled_months(pid, A, end)
            e = min(float(e), months) if e else 0.0
            adm, ed, ecost, ocost = h.window(pid, A, end)
            started, start_day = [], None
            if gap_type == 'gdmt_pillar_missing':
                res = completion._gdmt(db, pid, inp['targets'][i], a_d, e_d, gcfg)
                for pil, v in res.items():
                    if v.get('state') == 'filled':
                        started.append(pil)
                        d0 = day(v['first_fill'])
                        start_day = d0 if start_day is None else min(start_day, d0)
            elif gap_type == 'pt_after_ed_low_back_pain':
                counting, _ = completion._pt(db, pid, a_d, e_d, gcfg.get('pt_cpt') or [])
                if counting:
                    started = ['pt']
                    start_day = day(counting[0][2])
            rows.append({'pid': pid, 'exposure': e, 'adm': adm, 'ed': ed, 'cost': ecost + ocost, 'started': started,
                         'start_month': None if start_day is None else max(0.0, (start_day - A) / 30.44)})
    finally:
        db.close()

    # Point parameters for the effect given observed exposure.
    cfg = fx.resolve(fx.load_config(), inp.get('parameters') or {})
    P = {k: v['value'] for k, v in cfg['parameters'].items()}
    eff_meas = (sim.get('effects') or {})

    def reduction(r):
        """(relative reduction of admissions, of ED visits) once started, for one treated patient."""
        if not r['started']:
            return 0.0, 0.0
        if gap_type == 'gdmt_pillar_missing':
            m = eff_meas['gdmt']['measured']
            w = fx.pillar_weights({p: P[f'gdmt.pillar_hr.{p}'] for p in fx.PILLARS})
            share = sum(float(w[p]) for p in r['started'])
            eff = P['gdmt.real_world_effect_fraction'] * (1 - float(fx.gdmt_hr(share, P['gdmt.combination_hr'])))
            return _share(m['hf_admissions']) * eff, _share(m['hf_ed_visits']) * eff
        m = eff_meas['pt']['measured']
        return 0.0, _share(m['back_pain_ed_visits_after_index']) * (1 - P['pt.back_pain_ed_rr'])

    groups = {'treated_in_data': [i for i, r in enumerate(rows) if r['started']],
              'not_treated_in_data': [i for i, r in enumerate(rows) if not r['started']]}
    table, oe = [], {}
    for g, idx in groups.items():
        row = {'group': g, 'patients': len(idx), 'member_months': round(sum(rows[i]['exposure'] for i in idx), 1)}
        for name, rate, key in (('admissions', ra, 'adm'), ('ed_visits', re_, 'ed')):
            O = float(sum(rows[i][key] for i in idx))
            E = float(sum(rate[i] * rows[i]['exposure'] for i in idx))
            ratio, ci = _poisson_ci(O, E)
            row[name] = {'observed': O, 'expected_do_nothing': round(E, 2), 'o_e': ratio, 'o_e_ci95': ci}
            oe[(g, name)] = (O, E)
            if g == 'treated_in_data':
                Ep = 0.0
                for i in idx:
                    r = rows[i]
                    red = reduction(r)[0 if name == 'admissions' else 1]
                    s = min(r['start_month'] or 0.0, r['exposure'])
                    Ep += rate[i] * (s + (r['exposure'] - s) * (1 - red))
                row[name]['simulator_predicted_given_observed_exposure'] = round(Ep, 2)
        Oc = float(sum(rows[i]['cost'] for i in idx))
        Ec = float(sum((rc[i] + ra[i] * ca + re_[i] * ce) * rows[i]['exposure'] for i in idx))
        row['medical_cost'] = {'observed': round(Oc, 2), 'expected_do_nothing': round(Ec, 2), 'o_e': round(Oc / Ec, 4) if Ec else None}
        table.append(row)

    effect = {}
    for name in ('admissions', 'ed_visits'):
        (Ot, Et), (Ou, Eu) = oe[('treated_in_data', name)], oe[('not_treated_in_data', name)]
        tr = next(r for r in table if r['group'] == 'treated_in_data')[name]
        pred = (tr['simulator_predicted_given_observed_exposure'] / Et) if Et and 'simulator_predicted_given_observed_exposure' in tr else None
        if Ot > 0 and Ou > 0 and Et > 0 and Eu > 0:
            rr = (Ot / Et) / (Ou / Eu)
            se = math.sqrt(1 / Ot + 1 / Ou)
            effect[name] = {'observed_risk_adjusted_ratio': round(rr, 3), 'ci95': [round(rr * math.exp(-1.96 * se), 3), round(rr * math.exp(1.96 * se), 3)],
                            'simulator_predicted_ratio': None if pred is None else round(pred, 3),
                            'predicted_inside_observed_ci': None if pred is None else bool(rr * math.exp(-1.96 * se) <= pred <= rr * math.exp(1.96 * se))}
        else:
            effect[name] = {'observed_risk_adjusted_ratio': None, 'simulator_predicted_ratio': None if pred is None else round(pred, 3),
                            'note': 'no events in one group'}

    # Do-nothing calibration of the whole cohort.
    cal = {}
    full = float(len(cohort) * H)
    seen = sum(r['exposure'] for r in rows)
    for name, key, rate in (('admissions', 'adm', ra), ('ed_visits', 'ed', re_), ('medical_cost', 'cost', None)):
        q = inp['do_nothing_quantiles'][name]
        O = float(sum(r[key] for r in rows))
        if rate is None:
            w = np.asarray([rc[i] + ra[i] * ca + re_[i] * ce for i in range(len(cohort))])
        else:
            w = rate
        adj = float(sum(w[i] * rows[i]['exposure'] for i in range(len(cohort))) / (w.sum() * H)) if w.sum() else 1.0
        qa = [x * adj for x in q]
        cal[name] = {'observed': round(O, 2), 'do_nothing_mean': round(float(np.mean(q)), 2), 'do_nothing_interval_90': [q[5], q[95]],
                     'percentile_of_observed': _pct(q, O), 'inside_90': bool(q[5] <= O <= q[95]),
                     'exposure_adjusted_interval_90': [round(qa[5], 2), round(qa[95], 2)], 'exposure_adjusted_percentile': _pct(qa, O),
                     'inside_90_exposure_adjusted': bool(qa[5] <= O <= qa[95])}

    eng = None
    if engine and gap_type == 'gdmt_pillar_missing':
        eng = _engine_estimates(semantic_path, rows, end, H)
    elif engine:
        eng = [{'status': 'not_available', 'reason': 'The program_effect engine has no physical-therapy exposure; the observed comparison above is the estimate.'}]
    out = {'schema': 'agere-simulator-actual/1', 'simulation_id': sim.get('id'), 'program_kind': kind, 'as_of': sim['as_of'],
           'observed_through': date.fromordinal(end).isoformat(), 'cohort_patients': len(cohort),
           'observed_member_months': round(seen, 1), 'simulated_member_months': full,
           'exposure_definition': ('a fill of a target GDMT pillar after as_of (plans/completion.py evidence)' if gap_type == 'gdmt_pillar_missing'
                                   else 'a PT claim line after as_of (plans/completion.py evidence)'),
           'do_nothing_calibration': cal, 'groups': table, 'effect': effect, 'engine_estimates': eng,
           'started_pillars': dict(Counter(p for r in rows for p in r['started'])) if gap_type == 'gdmt_pillar_missing' else None}
    out['narrative'] = _narrative(out)
    return out


def _engine_estimates(semantic_path, rows, end, H):
    from ..clinical import program_effect, semantic_layer
    counts = Counter(p for r in rows for p in r['started'])
    path = Path(semantic_path)
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
    finally:
        db.close()
    if meta.get('version') != semantic_layer.VERSION or not meta.get('source_digest'):
        return [{'status': 'not_available', 'reason': 'semantic layer version differs from the engine version'}]
    out = []
    with tempfile.TemporaryDirectory() as tmp:  # the engine reads a run directory: run.json + semantic.sqlite3 (symlink, read-only)
        (Path(tmp) / 'run.json').write_text(json.dumps({'source_digest': meta['source_digest']}))
        (Path(tmp) / 'semantic.sqlite3').symlink_to(path.resolve())
        for pil, _ in (counts.most_common(2) or [('sglt2i', 0)]):
            cls = PILLAR_CLASS[pil]
            try:
                r = program_effect.execute(tmp, None, {'program': 'medication_start', 'drug_class': cls, 'outcome': 'admissions',
                                                       'population_conditions': ['heart_failure'], 'pre_days': 180,
                                                       'post_days': int(min(365, H * 30))}, as_of=date.fromordinal(end).isoformat())
                did = r.get('did') or {}
                out.append({'pillar': pil, 'drug_class': cls, 'method': 'matched difference-in-differences (program_effect)',
                            'matched_exposed': (r.get('sample') or {}).get('matched_exposed'), 'effect_per_patient_month': did.get('effect'),
                            'ci95': did.get('ci95'), 'relative_effect': did.get('relative_effect'), 'warnings': r.get('warnings')})
            except Exception as exc:  # the engine refuses thin data; report why
                out.append({'pillar': pil, 'drug_class': cls, 'status': 'refused', 'reason': f'{type(exc).__name__}: {exc}'[:300]})
    return out


def _narrative(o):
    c = o['do_nothing_calibration']
    t = next(r for r in o['groups'] if r['group'] == 'treated_in_data')
    parts = [f"Simulation as of {o['as_of']} compared with data through {o['observed_through']} for {o['cohort_patients']} patients. "
             f"Observed admissions {c['admissions']['observed']:.0f} vs do-nothing interval {c['admissions']['exposure_adjusted_interval_90'][0]:.0f}-"
             f"{c['admissions']['exposure_adjusted_interval_90'][1]:.0f} (percentile {c['admissions']['exposure_adjusted_percentile']}); ED visits "
             f"{c['ed_visits']['observed']:.0f} vs {c['ed_visits']['exposure_adjusted_interval_90'][0]:.0f}-{c['ed_visits']['exposure_adjusted_interval_90'][1]:.0f}; "
             f"spending {c['medical_cost']['observed']:,.0f} vs {c['medical_cost']['exposure_adjusted_interval_90'][0]:,.0f}-"
             f"{c['medical_cost']['exposure_adjusted_interval_90'][1]:,.0f} (member-months adjusted)."]
    parts.append(f"{t['patients']} patients were treated in the data ({o['exposure_definition']}).")
    for name in ('admissions', 'ed_visits'):
        e = o['effect'][name]
        if e.get('observed_risk_adjusted_ratio') is not None:
            parts.append(f"{name.replace('_', ' ').capitalize()}: observed risk-adjusted ratio treated vs not {e['observed_risk_adjusted_ratio']} "
                         f"(95% CI {e['ci95'][0]}-{e['ci95'][1]}); simulator predicted {e['simulator_predicted_ratio']}.")
    return ' '.join(parts)
