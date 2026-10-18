"""Population simulator: test an action plan against a simulated copy of the population before acting.

    out = simulate(semantic_path, program_kind='pharmacy_gdmt', as_of='2030-05-27', horizon_months=12)
    out = simulate(semantic_path, plan=routed_plan, alternatives=['pharmd_barrier_review'])

Question answered: if we enroll these routed patients in this program (or an alternative), what happens over the horizon
to admissions, ED visits, total cost of care, quality measures and shared savings, with uncertainty, compared with doing
nothing?

Pipeline
  1. Plan: the deterministic router (plans/router.py) over the workspace's semantic layer; the cohort is every patient the
     plan routes to enrollment.
  2. Baseline: per-patient monthly admission / ED rates and expected spending from the workspace's own history, calibrated
     on a held-out window (simulator/baseline.py).
  3. Funnel: who actually receives the intervention and when -- the action backbone's simulated counterparties walked once
     per Monte Carlo scenario (simulator/funnel.py).
  4. Effect: cited or explicitly assumed effects with ranges (simulator/config.json, simulator/effects.py), applied to the
     related events from the first visit / fill onward. The GDMT pillar combination is anchored to the network
     meta-analysis combined effect and never multiplies the four pillar hazard ratios.
  5. Monte Carlo (seeded, reproducible, common random numbers across arms): coefficient draws (Laplace), an origin-drift
     term from the held-out O/E, parameter draws, Poisson event counts, avoided events by binomial thinning of the same
     counts, event costs sampled from the workspace's own event costs, spending residuals from the held-out window,
     program costs from milestones, pharmacy cost of started pillars, and the contract settlement (clinical/vbc.settle on
     the attributed population) for every arm in every draw.
Every number comes with a 90% interval (5th-95th percentile of the draws).
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import math
import time

import numpy as np

from ..clinical import programs as programs_mod, vbc
from ..plans import catalog as catmod, router
from . import baseline as bl, effects as fx, funnel
from .data import History, add_months, day

VERSION = 'agere-population-simulator/1'
DAYS_PER_MONTH = 30.44


def _q(a, lo=0.05, hi=0.95):
    a = np.asarray(a, float)
    return {'mean': round(float(a.mean()), 2), 'p05': round(float(np.quantile(a, lo)), 2), 'p50': round(float(np.quantile(a, 0.5)), 2),
            'p95': round(float(np.quantile(a, hi)), 2)}


def _hist(a, bins=20):
    a = np.asarray(a, float)
    if a.size == 0:
        return None
    lo, hi = float(a.min()), float(a.max())
    if hi <= lo:
        return {'edges': [lo, hi], 'counts': [int(a.size)]}
    counts, edges = np.histogram(a, bins=bins, range=(lo, hi))
    return {'edges': [round(float(e), 2) for e in edges], 'counts': counts.astype(int).tolist()}


def _sample_sum(rng, counts, pool):
    """Per-cell sums of `counts` draws from pool (counts: int array of any shape)."""
    counts = np.asarray(counts, np.int64)
    flat = counts.ravel()
    tot = int(flat.sum())
    out = np.zeros(flat.shape, float)
    if tot == 0 or len(pool) == 0:
        return out.reshape(counts.shape)
    vals = rng.choice(np.asarray(pool, float), size=tot)
    idx = np.repeat(np.arange(flat.size), flat)
    out = np.bincount(idx, weights=vals, minlength=flat.size)
    return out.reshape(counts.shape)


def resolve_plan(semantic_path, plan=None, *, program_kind=None, question=None, patient_ids=None, as_of=None, catalog=None):
    cat = catalog or catmod.load()
    if plan is not None:
        return plan, cat
    kind = router.resolve_kind(cat, program_kind, question)
    ids = patient_ids if patient_ids is not None else router.candidates(semantic_path, kind, as_of, catalog=cat)
    return router.route(semantic_path, ids, kind, as_of, catalog=cat), cat


def simulate(semantic_path, plan=None, *, program_kind=None, question=None, patient_ids=None, as_of=None, horizon_months=None,
             alternatives=None, iterations=None, funnel_scenarios=None, seed=None, parameters=None, parameter_uncertainty=True,
             contract=None, workspace='adhoc', catalog=None, config=None, per_patient=True):
    t0 = time.time()
    cfg = fx.resolve(fx.load_config(config), parameters)
    fx.check_config(cfg)
    d = cfg['defaults']
    H = int(horizon_months or d['horizon_months'])
    if not 1 <= H <= 36:
        raise ValueError('horizon_months must be 1..36')
    N = int(iterations or d['iterations'])
    if not 50 <= N <= 20000:
        raise ValueError('iterations must be 50..20000')
    K = int(min(funnel_scenarios or d['funnel_scenarios'], N))
    seed = int(d['seed'] if seed is None else seed)
    unc = bool(parameter_uncertainty)
    plan, cat = resolve_plan(semantic_path, plan, program_kind=program_kind, question=question, patient_ids=patient_ids,
                             as_of=as_of, catalog=catalog)
    kind = plan['program_kind']
    as_of_s = plan['as_of']
    A = day(as_of_s)
    end = add_months(A, H)
    horizon_days = end - A
    rng = np.random.default_rng(seed)
    timings = {}

    # --- 2. baseline ----------------------------------------------------------------------------------------------------
    t = time.time()
    h = History.load(semantic_path)
    timings['load_history_s'] = round(time.time() - t, 2)
    t = time.time()
    L, W = bl.windows(h, A, H, d['lookback_months'])
    cohort_rows = [r for r in plan['patients'] if r.get('route') == 'enroll']
    cohort = [r['patient_id'] for r in cohort_rows]
    n = len(cohort)
    pool_from = add_months(A, -24)
    pop_events = bl.event_costs(h, list(h.events), pool_from, A)
    anchors = {}
    for r in cohort_rows:  # index events that define cohort membership (excluded from the cohort's held-out outcomes)
        ed = ((r.get('gap') or {}).get('ed_visit_date') or {}).get('value')
        if ed:
            anchors[r['patient_id']] = {day(ed)}
    calib, drift, ratios, recal = bl.calibrate(h, A, L, W, cohort=cohort, rng=np.random.default_rng(seed + 1), cost_tables=pop_events,
                                               anchors=anchors)
    ratios = ratios / ratios.mean() if len(ratios) and ratios.mean() > 0 else np.ones(1)  # dispersion only; level is drift / recal
    live = bl.Model(h, A, L, W)
    timings['baseline_fit_s'] = round(time.time() - t, 2)
    coh_events = bl.event_costs(h, cohort, pool_from, A)
    pools = {k: (coh_events[k] if len(coh_events[k]) >= 30 else pop_events[k]) or [0.0] for k in ('admission', 'ed')}
    pool_source = {k: ('cohort' if len(coh_events[k]) >= 30 else 'population') for k in ('admission', 'ed')}
    Xc = np.array([bl.features(h, pid, A, L) for pid in cohort], float).reshape(-1, len(bl.FEATURES))
    la0, le0, lc0 = live.rates(Xc) if n else (np.zeros(0),) * 3
    fa, fe, fc = _point_recal(recal)
    la0, le0, lc0 = la0 * fa, le0 * fe, lc0 * fc   # point predictions after the cohort recalibration
    ca_mean, ce_mean = float(np.mean(pools['admission'])), float(np.mean(pools['ed']))

    # Contract population: members attributed at as_of (all enrolled members when the layer has no attribution).
    ccfg = {**cfg['contract'], **(contract or {})}
    lobs = set(ccfg.get('lines_of_business') or [])
    enrolled = [p for p in h.months if bl.eligible(h, p, A) and (not lobs or h.lob_at(p, A) in lobs)]
    attributed = [p for p in enrolled if h.practice_at(p, A)]
    pop = attributed or enrolled
    pop_set = set(pop)
    cohort_set = set(cohort)
    noncohort = [p for p in pop if p not in cohort_set]
    Xn = np.array([bl.features(h, p, A, L) for p in noncohort], float).reshape(-1, len(bl.FEATURES))
    if len(noncohort):
        na_, ne_, nc_ = live.rates(Xn)
        nc_expected = (nc_ + na_ * ca_mean + ne_ * ce_mean) * H
    else:
        nc_expected = np.zeros(0)
    in_pop = np.array([p in pop_set for p in cohort], bool)

    # --- 3/4. draws shared by every arm ---------------------------------------------------------------------------------
    t = time.time()
    # Cohort rates carry the cohort's held-out recalibration (Gamma / log-normal draws); population spending carries the
    # population origin drift.
    if unc and n:
        ra, re_, rc = live.draws(Xc, N, rng)
        sd = drift
        dc = np.exp(rng.normal(-sd['cost'] ** 2 / 2, sd['cost'], N))
        if recal:
            da = rng.gamma(recal['admissions']['shape'], 1 / recal['admissions']['rate'], N)
            de = rng.gamma(recal['ed_visits']['shape'], 1 / recal['ed_visits']['rate'], N)
            dco = np.exp(rng.normal(recal['other_cost']['log_mean'], recal['other_cost']['log_sd'], N))
        else:
            da = np.exp(rng.normal(-sd['admissions'] ** 2 / 2, sd['admissions'], N))
            de = np.exp(rng.normal(-sd['ed_visits'] ** 2 / 2, sd['ed_visits'], N))
            dco = dc
    else:
        ra, re_, rc = (np.tile(x, (N, 1)) for x in (la0, le0, lc0)) if n else (np.zeros((N, 0)),) * 3
        da = de = dc = dco = np.ones(N)   # la0 / le0 / lc0 already carry the point recalibration
    ra, re_, rc = ra * da[:, None], re_ * de[:, None], rc * dco[:, None]
    NA = rng.poisson(ra * H)
    NE = rng.poisson(re_ * H)
    OC = rc * H * rng.choice(ratios, size=rc.shape) if n else np.zeros((N, 0))
    EA = _sample_sum(rng, NA, pools['admission'])
    EE = _sample_sum(rng, NE, pools['ed'])
    dn_cost = OC + EA + EE                                    # (N, n) do-nothing medical spending per patient
    if len(noncohort):
        nc_draw = np.empty(N)
        for k in range(N):  # member-level residuals (high-cost claimants), one draw per member per iteration
            nc_draw[k] = float(np.sum(nc_expected * rng.choice(ratios, size=len(noncohort)))) * dc[k]
    else:
        nc_draw = np.zeros(N)

    # Effect parameters (shared draws across arms so arms differ only by who receives what).
    P = cfg['parameters']
    par = {pid: fx.draw(P[pid], rng, N, unc) for pid in P}
    used = set()
    lookback_a = add_months(A, -24)
    effect_ctx = {}
    progs_in_play = set()
    arms = funnel.normalize_arms(alternatives, kind, plan, cat)
    arm_plans = []
    for arm in arms:
        variant, ids, used_progs = funnel.arm_plan(plan, arm, cat)
        progs_in_play.update(used_progs)
        arm_plans.append((arm, variant, ids, used_progs))
    effect_types = {(cfg['programs'].get(p) or {}).get('effect') for p in progs_in_play}
    effect_types.discard(None)
    if 'gdmt' in effect_types:
        pre = cfg['effects']['gdmt']['related_icd10_prefixes']
        ka, na_ = bl.related_share(h, cohort, lookback_a, A, 'admission', pre)
        ke, ne_ = bl.related_share(h, cohort, lookback_a, A, 'ed', pre)
        if na_ < 10 or ne_ < 10:  # thin cohort history: every HF patient's events
            hf = [p for p, dd in h.hf_dx.items() if dd < A]
            if na_ < 10:
                ka, na_ = bl.related_share(h, hf, lookback_a, A, 'admission', pre)
            if ne_ < 10:
                ke, ne_ = bl.related_share(h, hf, lookback_a, A, 'ed', pre)
        effect_ctx['gdmt'] = {'hf_share_admissions': fx.beta_share(ka, na_, rng, N, unc), 'hf_share_ed': fx.beta_share(ke, ne_, rng, N, unc),
                              'measured': {'hf_admissions': [ka, na_], 'hf_ed_visits': [ke, ne_]}}
        hrs = {p: par[f'gdmt.pillar_hr.{p}'] for p in fx.PILLARS}
        effect_ctx['gdmt']['weights'] = fx.pillar_weights(hrs)  # {pillar: (N,)}
        used |= {f'gdmt.pillar_hr.{p}' for p in fx.PILLARS} | {'gdmt.combination_hr', 'gdmt.real_world_effect_fraction', 'gdmt.fill_lag_days'}
        used |= {f'drug_cost_month.{p}' for p in fx.PILLARS}
    if 'pt' in effect_types:
        pre = cfg['effects']['pt']['related_icd10_prefixes']
        excl = {}
        for r in cohort_rows:
            ed = ((r.get('gap') or {}).get('ed_visit_date') or {}).get('value')
            if ed:
                excl[r['patient_id']] = {day(ed)}
        ke, ne_ = bl.related_share(h, cohort, lookback_a, A, 'ed', pre, exclude=excl)
        if ne_ < 10:
            ke2, ne2 = bl.related_share(h, list(h.events), lookback_a, A, 'ed', pre)
            ke, ne_ = ke + ke2, ne_ + ne2
        rel, tot = bl.related_other_share(h, cohort, lookback_a, A, pre)
        effect_ctx['pt'] = {'bp_share_ed': fx.beta_share(ke, ne_, rng, N, unc),
                            'bp_share_other': np.full(N, rel / tot if tot else 0.0),
                            'measured': {'back_pain_ed_visits_after_index': [ke, ne_], 'back_pain_other_spend_share': round(rel / tot, 4) if tot else None}}
        used |= {'pt.back_pain_ed_rr', 'pt.back_pain_other_spend_rr', 'pt.partial_effect_fraction', 'pt.start_lag_days'}

    targets = []
    for r in cohort_rows:
        g = r.get('gap') or {}
        targets.append(set(((g.get('missing_pillars') or {}).get('value')) or []))
    present = [4 - len(tg) for tg in targets]
    index = {pid: i for i, pid in enumerate(cohort)}

    # --- funnel and arms ------------------------------------------------------------------------------------------------
    terms = programs_mod.contract_terms(ccfg.get('program') or 'MSSP ENHANCED')
    if 'msr' not in terms and terms.get('msr_schedule'):
        rate = programs_mod.variable_msr(len(pop), terms['msr_schedule'])
        terms = {**terms, 'msr': rate, 'mlr': rate}
    mm = float(len(pop) * H)
    pop_dn = nc_draw + (dn_cost[:, in_pop].sum(1) if n else 0.0)
    exp_pmpm = float(pop_dn.mean() / mm) if mm else 0.0
    bench_given = ccfg.get('benchmark_pmpm')
    factors = list(ccfg.get('benchmark_factors_sensitivity') or [])
    bench = float(bench_given) if bench_given else exp_pmpm * float(ccfg.get('benchmark_factor', 1.0))

    def settle_all(total, b):
        return np.array([vbc.settle(x / mm, b, mm, terms) for x in total]) if mm and b else np.zeros(len(total))

    dn_settle = settle_all(pop_dn, bench)
    results = {'do_nothing': {'arm': {'id': 'do_nothing', 'label': 'Do nothing'}, 'admissions': NA.sum(1), 'ed_visits': NE.sum(1),
                              'medical_cost': dn_cost.sum(1), 'program_cost': np.zeros(N), 'pharmacy_cost': np.zeros(N),
                              'settlement': dn_settle, 'funnel': None}}
    monthly = {'do_nothing': _monthly(ra, re_, None, None, H)}
    per_patient_rows = {}
    t_f = time.time()
    for arm, variant, ids, used_progs in arm_plans:
        status, start, flags, program = funnel.scenarios(variant, ids, K, workspace=workspace, seed=seed, cat=cat)
        sel = np.array([index[p] for p in ids], int)
        sk = np.arange(N) % K
        st = np.zeros((N, n), np.int8); sd_ = np.full((N, n), -1, np.int32)
        if len(sel):
            st[:, sel] = status[sk]
            sd_[:, sel] = start[sk]
        prog_of = [None] * n
        for j, i in enumerate(sel):
            prog_of[i] = program[j]
        avoid_a = np.zeros((N, n)); avoid_e = np.zeros((N, n)); other_cut = np.zeros((N, n)); rel_cut = np.zeros((N, n))
        rel_cut_e = np.zeros((N, n))
        pharm = np.zeros(N)
        q_rows = {}
        started_any = np.zeros((N, n), bool); quad = np.zeros((N, n), bool); rasa_new = np.zeros((N, n), bool)
        pillar_starts = np.zeros(N)
        for prog in sorted({p for p in prog_of if p}):
            pc = cfg['programs'].get(prog) or {}
            cols = np.array([i for i in range(n) if prog_of[i] == prog], int)
            stc, sdc = st[:, cols], sd_[:, cols]
            if pc.get('effect') == 'gdmt':
                g = effect_ctx['gdmt']
                lag = par['gdmt.fill_lag_days'][:, None]
                active = np.where(stc > 0, np.clip((horizon_days - (sdc + lag)) / horizon_days, 0, 1), 0.0)
                p_full = par[pc['p_started_completed']][:, None]
                p_part = par[pc['p_started_attended']][:, None]
                used.update({pc['p_started_completed'], pc['p_started_attended']})
                p_start = np.where(stc == funnel.COMPLETED, p_full, np.where(stc == funnel.ATTENDED, p_part, 0.0))
                share = np.zeros(stc.shape)
                n_started = np.zeros(stc.shape)
                months_on = np.clip((horizon_days - (sdc + lag)) / DAYS_PER_MONTH, 0, H)
                for pil in fx.PILLARS:
                    tmask = np.array([pil in targets[i] for i in cols], bool)[None, :]
                    s = (rng.random(stc.shape) < p_start) & tmask
                    share += s * g['weights'][pil][:, None]
                    n_started += s
                    pharm += np.sum(s * months_on, axis=1) * par[f'drug_cost_month.{pil}']
                    if pil == 'rasi':
                        rasa_new[:, cols] |= s
                hr = fx.gdmt_hr(share, par['gdmt.combination_hr'][:, None])
                full = share >= 1 - 1e-9
                if full.any():
                    fx.assert_not_forbidden(hr[full], cfg)
                eff = par['gdmt.real_world_effect_fraction'][:, None] * (1 - hr)
                avoid_a[:, cols] = active * g['hf_share_admissions'][:, None] * eff
                avoid_e[:, cols] = active * g['hf_share_ed'][:, None] * eff
                rel_cut[:, cols] = g['hf_share_admissions'][:, None] * eff * (stc > 0)
                rel_cut_e[:, cols] = g['hf_share_ed'][:, None] * eff * (stc > 0)
                started_any[:, cols] = n_started > 0
                quad[:, cols] = (n_started + np.array([present[i] for i in cols])[None, :]) >= 4
                pillar_starts += n_started.sum(1)
            elif pc.get('effect') == 'pt':
                g = effect_ctx['pt']
                lag = par['pt.start_lag_days'][:, None]
                active = np.where(stc > 0, np.clip((horizon_days - (sdc + lag)) / horizon_days, 0, 1), 0.0)
                frac = np.where(stc == funnel.COMPLETED, 1.0, np.where(stc == funnel.ATTENDED, par['pt.partial_effect_fraction'][:, None], 0.0))
                eff_e = (1 - par['pt.back_pain_ed_rr'][:, None]) * frac
                avoid_e[:, cols] = active * g['bp_share_ed'][:, None] * eff_e
                rel_cut_e[:, cols] = g['bp_share_ed'][:, None] * eff_e * (stc > 0)
                other_cut[:, cols] = active * g['bp_share_other'][:, None] * (1 - par['pt.back_pain_other_spend_rr'][:, None]) * frac
                started_any[:, cols] = stc > 0
        AA = rng.binomial(NA, np.clip(avoid_a, 0, 1))
        AE = rng.binomial(NE, np.clip(avoid_e, 0, 1))
        saved = _sample_sum(rng, AA, pools['admission']) + _sample_sum(rng, AE, pools['ed']) + OC * other_cut
        arm_cost = dn_cost - saved
        prog_cost = np.zeros(N)
        for prog in sorted({p for p in prog_of if p}):
            cols_local = np.array([j for j, i in enumerate(sel) if prog_of[i] == prog], int)
            costs = (cfg['programs'].get(prog) or {}).get('costs') or {}
            for item, flag in (('per_outreach', 'outreach'), ('per_enrolled', 'enrolled'), ('per_attended', 'attended'),
                               ('per_completed', 'completed')):
                spec = costs.get(item)
                if not spec or not (spec.get('value') or spec.get('high')):
                    continue
                cnt = flags[flag][:, cols_local].sum(1)[sk]
                prog_cost += cnt * fx.draw(spec, rng, N, unc)
        pop_arm = pop_dn - (saved[:, in_pop].sum(1) if n else 0.0)  # claims spending only; program fees are paid outside the claims stream
        res = {'arm': arm, 'programs': used_progs, 'admissions': NA.sum(1) - AA.sum(1), 'ed_visits': NE.sum(1) - AE.sum(1),
               'medical_cost': arm_cost.sum(1), 'program_cost': prog_cost, 'pharmacy_cost': pharm,
               'admissions_avoided': AA.sum(1), 'ed_visits_avoided': AE.sum(1),
               'settlement': settle_all(pop_arm, bench), 'pop_total': pop_arm,
               'funnel': funnel.funnel_rates(status, flags),
               'quality': {'patients_with_new_therapy': started_any.sum(1).astype(float)}}
        if 'gdmt' in effect_types and any((cfg['programs'].get(p) or {}).get('effect') == 'gdmt' for p in used_progs):
            res['quality'].update({'pillars_started': pillar_starts, 'patients_on_quadruple_therapy_added': quad.sum(1).astype(float)
                                   - np.sum(np.array(present) >= 4), 'pdc_rasa_new_denominator_members': rasa_new.sum(1).astype(float)})
        if any((cfg['programs'].get(p) or {}).get('effect') == 'pt' for p in used_progs):
            res['quality'].update({'pt_after_ed_lbp_gap_closed': (st[:, sel] > 0).sum(1).astype(float) if len(sel) else np.zeros(N)})
        results[arm['id']] = res
        monthly[arm['id']] = _monthly(ra, re_, (rel_cut, rel_cut_e), np.where(st > 0, sd_, 10 ** 6), H)
        if per_patient and n:
            per_patient_rows[arm['id']] = {'p_completed': (st == funnel.COMPLETED).mean(0), 'p_attended': (st > 0).mean(0),
                                           'admissions_avoided': AA.mean(0), 'ed_avoided': AE.mean(0),
                                           'savings': saved.mean(0)}
    timings['funnel_and_arms_s'] = round(time.time() - t_f, 2)
    timings['monte_carlo_s'] = round(time.time() - t, 2)

    # --- summaries ------------------------------------------------------------------------------------------------------
    dn = results['do_nothing']
    table, dists = [], {}
    for aid, r in results.items():
        row = {'arm': aid, 'label': r['arm'].get('label'), 'programs': r.get('programs', []),
               'admissions': _q(r['admissions']), 'ed_visits': _q(r['ed_visits']), 'medical_cost': _q(r['medical_cost']),
               'settlement': _q(r['settlement']), 'p_earn_shared_savings': round(float((r['settlement'] > 0).mean()), 3)}
        if aid != 'do_nothing':
            gross = dn['medical_cost'] - r['medical_cost']
            net = gross - r['program_cost']
            net_rx = net - r['pharmacy_cost']
            dss = r['settlement'] - dn['settlement']
            row.update({'admissions_avoided': _q(r['admissions_avoided']), 'ed_visits_avoided': _q(r['ed_visits_avoided']),
                        'gross_medical_savings': _q(gross), 'program_cost': _q(r['program_cost']), 'net_savings': _q(net),
                        'p_net_savings_positive': round(float((net > 0).mean()), 3), 'pharmacy_cost_added': _q(r['pharmacy_cost']),
                        'net_savings_incl_pharmacy': _q(net_rx), 'shared_savings_change': _q(dss),
                        'roi_gross_over_program': round(float(gross.mean() / r['program_cost'].mean()), 2) if r['program_cost'].mean() else None,
                        'funnel': r['funnel'], 'quality': {k: _q(v) for k, v in r['quality'].items()}})
            sens = []
            for f in factors:
                b = exp_pmpm * f
                s_dn, s_arm = settle_all(pop_dn, b), settle_all(r['pop_total'], b)
                sens.append({'benchmark_factor': f, 'benchmark_pmpm': round(b, 2), 'expected_settlement_do_nothing': round(float(s_dn.mean()), 2),
                             'expected_settlement_arm': round(float(s_arm.mean()), 2), 'expected_change': round(float((s_arm - s_dn).mean()), 2),
                             'p_earn_do_nothing': round(float((s_dn > 0).mean()), 3), 'p_earn_arm': round(float((s_arm > 0).mean()), 3)})
            row['shared_savings_sensitivity'] = sens
            dists[aid] = {'net_savings': _hist(net), 'admissions_avoided': _hist(r['admissions_avoided']),
                          'ed_visits_avoided': _hist(r['ed_visits_avoided']), 'shared_savings_change': _hist(dss)}
        else:
            dists[aid] = {'admissions': _hist(r['admissions']), 'ed_visits': _hist(r['ed_visits']), 'medical_cost': _hist(r['medical_cost'])}
        table.append(row)

    base = {'patients': n, 'expected_admissions': round(float(la0.sum() * H), 2), 'expected_ed_visits': round(float(le0.sum() * H), 2),
            'expected_medical_cost': round(float(((lc0 + la0 * ca_mean + le0 * ce_mean) * H).sum()), 2),
            'monthly_admission_rate_mean': round(float(la0.mean()), 5) if n else None,
            'monthly_ed_rate_mean': round(float(le0.mean()), 5) if n else None,
            'cost_per_admission_mean': round(ca_mean, 2), 'cost_per_ed_visit_mean': round(ce_mean, 2), 'event_cost_pool': pool_source,
            'events_in_cost_pool': {k: len(v) for k, v in pools.items()}}
    pp = None
    if per_patient and n:
        pp = []
        for i, pid in enumerate(cohort):
            row = {'patient_id': pid, 'program_id': cohort_rows[i].get('program_id'),
                   'monthly_admission_risk': round(float(1 - math.exp(-la0[i])), 5), 'monthly_ed_risk': round(float(1 - math.exp(-le0[i])), 5),
                   'monthly_admission_rate': round(float(la0[i]), 6), 'monthly_ed_rate': round(float(le0[i]), 6),
                   'expected_monthly_cost': round(float(lc0[i] + la0[i] * ca_mean + le0[i] * ce_mean), 2),
                   'missing_pillars': sorted(targets[i]) or None}
            for aid, v in per_patient_rows.items():
                row[aid] = {'p_completed': round(float(v['p_completed'][i]), 3), 'p_attended': round(float(v['p_attended'][i]), 3),
                            'admissions_avoided': round(float(v['admissions_avoided'][i]), 4), 'ed_avoided': round(float(v['ed_avoided'][i]), 4),
                            'savings': round(float(v['savings'][i]), 2)}
            pp.append(row)
    prog_ids = sorted(progs_in_play)
    out = {
        'schema': VERSION, 'workspace': workspace, 'program_kind': kind, 'as_of': as_of_s, 'horizon_months': H,
        'horizon_end': date.fromordinal(end).isoformat(), 'iterations': N, 'funnel_scenarios': K, 'seed': seed,
        'parameter_uncertainty': unc, 'interval': '90% (5th-95th percentile of the Monte Carlo draws)',
        'plan': {'patients': len(plan['patients']), 'by_route': plan['summary']['by_route'], 'by_program': plan['summary']['by_program'],
                 'routing_digest': router.digest(plan), 'catalog_version': plan.get('catalog_version')},
        'cohort': {'patients': n, 'definition': 'patients the plan routes to enrollment', 'in_contract_population': int(in_pop.sum())},
        'arms': [{'id': 'do_nothing', 'label': 'Do nothing'}] + [{**a, 'patients_routed': len(ids)} for a, _, ids, _ in arm_plans],
        'summary_table': table,
        'baseline': base,
        'calibration': calib,
        'baseline_model': {'fit_origins': [date.fromordinal(o).isoformat() for o in live.origins], 'lookback_months': L,
                           'window_months': W, 'fit_patients': live.n, 'fit_person_windows': live.rows, 'features': list(bl.FEATURES), 'coefficients': live.coefficients()},
        'contract': {'program': (terms.get('program') or {}).get('label'), 'population': len(pop),
                     'population_basis': 'attributed members at as_of' if attributed else 'enrolled members at as_of',
                     'member_months': mm, 'benchmark_pmpm': round(bench, 2),
                     'benchmark_basis': 'supplied' if bench_given else f"projected do-nothing PMPM x {ccfg.get('benchmark_factor', 1.0)}",
                     'projected_do_nothing_pmpm': round(exp_pmpm, 2), 'msr': terms.get('msr'), 'sharing_rate': terms.get('sharing_rate'),
                     'two_sided': terms.get('two_sided'), 'program_cost_in_settlement_spend': False,
                     'sources': [s['id'] for s in ((terms.get('program') or {}).get('sources') or [])]},
        'effects': {k: {'measured': v['measured'], 'label': cfg['effects'][k]['label']} for k, v in effect_ctx.items()},
        'parameters': fx.parameter_table(cfg, used) + fx.cost_table(cfg, prog_ids),
        'distributions': dists,
        'monthly_expected': monthly,
        'per_patient': pp,
        'assumptions': _assumptions(effect_types),
        'timings': {**timings, 'total_s': round(time.time() - t0, 2)},
        'synthetic_counterparties': True,
        'inputs': {'parameters': parameters or {}, 'alternatives': alternatives, 'contract': contract or {}, 'config_version': cfg.get('version'),
                   'cohort': cohort, 'present_pillars': present, 'targets': [sorted(x) for x in targets],
                   'gap_ed_dates': [((r.get('gap') or {}).get('ed_visit_date') or {}).get('value') for r in cohort_rows],
                   'rates': {'admission': [round(float(x), 7) for x in la0], 'ed': [round(float(x), 7) for x in le0],
                             'other_cost': [round(float(x), 3) for x in lc0]},
                   'do_nothing_quantiles': {m: [round(float(x), 3) for x in np.quantile(results['do_nothing'][m], np.linspace(0, 1, 101))]
                                            for m in ('admissions', 'ed_visits', 'medical_cost')},
                   'cost_per_event': {'admission': ca_mean, 'ed': ce_mean}},
    }
    out['id'] = 'SIM-' + hashlib.sha256(json.dumps([workspace, kind, as_of_s, H, N, seed, unc, router.digest(plan), parameters or {},
                                                     alternatives, contract or {}], sort_keys=True, default=str).encode()).hexdigest()[:12].upper()
    out['narrative'] = narrative(out)
    return out


def _point_recal(recal):
    if not recal:
        return 1.0, 1.0, 1.0
    return (recal['admissions']['shape'] / recal['admissions']['rate'], recal['ed_visits']['shape'] / recal['ed_visits']['rate'],
            math.exp(recal['other_cost']['log_mean'] + recal['other_cost']['log_sd'] ** 2 / 2))


def _monthly(ra, re_, cuts, start, H):
    """Expected admissions and ED visits per month (mean over draws)."""
    out = {'admissions': [], 'ed_visits': []}
    for m in range(H):
        lo = m * DAYS_PER_MONTH
        if cuts is None:
            out['admissions'].append(round(float(ra.sum(1).mean()), 3))
            out['ed_visits'].append(round(float(re_.sum(1).mean()), 3))
            continue
        on = start <= lo
        out['admissions'].append(round(float((ra * (1 - cuts[0] * on)).sum(1).mean()), 3))
        out['ed_visits'].append(round(float((re_ * (1 - cuts[1] * on)).sum(1).mean()), 3))
    return out


def _assumptions(effect_types):
    a = ['Baseline rates are constant over the horizon at the level fit on the most recent complete window; month-to-month seasonality '
         'is not modelled.',
         'Counterparties are the simulated adapters of the action backbone (programs.json rates): enrollment, attendance and completion '
         'are planning assumptions until a real program reports them.',
         'Avoided events save the workspace\'s own average spending for that kind of event; program and pharmacy costs are the stated '
         'parameters.']
    if 'gdmt' in effect_types:
        a.append('GDMT effects apply only to admissions and ED visits with a principal HF diagnosis, from the first new fill onward; the '
                 'combined pillar effect is anchored to the network meta-analysis value and is never a product of single-trial hazard ratios.')
    if 'pt' in effect_types:
        a.append('The physical therapy effect on back-pain ED visits and spending is an explicit assumption with a range (no cited effect loaded).')
    return a


def _money(x):
    sign = '-' if x < 0 else ''
    x = abs(x)
    return f'{sign}${x / 1e6:.2f}M' if x >= 1e6 else (f'{sign}${x / 1e3:.0f}K' if x >= 1e4 else f'{sign}${x:,.0f}')


def narrative(out):
    rows = {r['arm']: r for r in out['summary_table']}
    dn = rows['do_nothing']
    lines = [f"{out['cohort']['patients']} patients routed to enrollment ({out['program_kind']}) as of {out['as_of']}, simulated over "
             f"{out['horizon_months']} months ({out['iterations']} draws). Doing nothing: about {dn['admissions']['mean']:.0f} admissions "
             f"(90% interval {dn['admissions']['p05']:.0f}-{dn['admissions']['p95']:.0f}), {dn['ed_visits']['mean']:.0f} ED visits and "
             f"{_money(dn['medical_cost']['mean'])} in medical spending."]
    for aid, r in rows.items():
        if aid == 'do_nothing':
            continue
        f = r['funnel']
        lines.append(f"{r['label']}: about {f['completed']:.0f} of {f['patients']} complete the program; "
                     f"{r['admissions_avoided']['mean']:.1f} admissions ({r['admissions_avoided']['p05']:.0f}-{r['admissions_avoided']['p95']:.0f}) "
                     f"and {r['ed_visits_avoided']['mean']:.1f} ED visits avoided; gross medical savings {_money(r['gross_medical_savings']['mean'])} "
                     f"against program cost {_money(r['program_cost']['mean'])}, net {_money(r['net_savings']['mean'])} "
                     f"({_money(r['net_savings']['p05'])} to {_money(r['net_savings']['p95'])}; P(net > 0) = {r['p_net_savings_positive']:.0%})"
                     + (f"; added pharmacy cost {_money(r['pharmacy_cost_added']['mean'])}" if r['pharmacy_cost_added']['mean'] else '')
                     + f"; shared-savings change {_money(r['shared_savings_change']['mean'])}.")
    cal = out['calibration']['outcomes']
    lines.append(f"Baseline calibration on the held-out window {out['calibration']['heldout_origin']} to {out['calibration']['heldout_end']}: "
                 f"admissions O/E {cal['admissions']['o_e']}, ED O/E {cal['ed_visits']['o_e']}, spending O/E {cal['total_cost']['o_e']}.")
    return ' '.join(lines)
