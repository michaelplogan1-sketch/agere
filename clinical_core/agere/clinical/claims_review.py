"""Payment-integrity and revenue-leakage review over medical claims ('claims_review' operation).

monitor answers "is anything out of control?" with control charts and a first-pass integrity screen. This operator answers
the money questions a payment-integrity or revenue-cycle analyst asks, and ranks every finding by expected dollars:

1. Provider peer review (service organizations, compared within their setting): billing intensity for the same service mix
   (monitor.peer_outliers: empirical-Bayes shrunk log O/E, robust median/MAD z vs peers, t reference), claims per patient
   (random-effects z: own sampling error plus the peers' robust between-unit variance), first-pass denial rate (beta-binomial
   predictive tail under the leave-one-out peers, shrunk rate reported); service mix as a robust
   Mahalanobis distance of additive log-ratio share vectors (leave-one-out median center, shrunk covariance plus the unit's
   own multinomial sampling variance; chi-square reference).
2. Benford first digit (monitor.benford_test, with its volume / magnitude / fee-schedule guards) and second digit (chi-square
   + Nigrini MAD bands), overall and per provider, only where volume suffices; the report says where it does not.
3. Duplicates and churn: exact duplicates and near duplicates (same patient, provider and service within a window, amount
   within 10%) with a per-service background rate of legitimate repeats, so each pair carries P(duplicate); resubmission
   chains (corrected submissions, corrections, clearinghouse rejections) and reversal churn (take-backs, repricing).
4. Underpayment: expected payment for comparable claims (payer x service x setting) from either the allowed level or the
   allowed/billed ratio (whichever is tighter in the cell), with a 90% empirical interval; robust z, one-sided p; plus any
   payment short of the payer's own adjudication (allowed - patient responsibility - paid).
5. Denials: first-pass denial rate (shrunk) and overturn rate by payer, CARC reason categories, days from denial to payment,
   dollars recovered vs written off vs pending, a Pareto of reasons, payer x reason chi-square.

Every p-value of a family enters one Benjamini-Hochberg procedure. Each flag carries dollars at stake, a probability
(1 - q for statistical flags, P(duplicate) for pairs, 1 for observed write-offs) and expected dollars = dollars x probability.
Flags are prompts for a person to review, never findings of fraud or wrongdoing.
"""
from collections import defaultdict, Counter
from datetime import date
import json, math, re, sqlite3

import numpy as np
from scipy.stats import binom, chi2, chi2_contingency, norm, t as t_dist

from . import monitor, semantic_layer, vbc

VERSION = 'claims-review/1'
FOCUS = ('all', 'providers', 'benford', 'duplicates', 'underpayment', 'denials', 'leakage')
BY = ('payer', 'provider', 'setting', 'service')
FIELDS = {'focus', 'by', 'period', 'fdr', 'min_claims', 'window_days', 'writeoff_days', 'payer', 'provider', 'rework_cost', 'top'}
RUNS = {'all': ('providers', 'benford', 'duplicates', 'underpayment', 'denials'), 'leakage': ('duplicates', 'underpayment', 'denials')}

# X12 claim adjustment reason codes -> (plain label, root-cause category). Unlisted codes keep their group-code label.
CARC = {'1': ('deductible', 'patient_responsibility'), '2': ('coinsurance', 'patient_responsibility'), '3': ('copay', 'patient_responsibility'),
        '4': ('modifier inconsistent with procedure', 'coding'), '5': ('procedure/place of service inconsistent', 'coding'),
        '11': ('diagnosis inconsistent with procedure', 'coding'), '16': ('claim lacks information needed for adjudication', 'missing_information'),
        '18': ('exact duplicate claim/service', 'duplicate'), '22': ('may be covered by another payer (COB)', 'coordination_of_benefits'),
        '27': ('expenses after coverage terminated', 'eligibility'), '29': ('time limit for filing expired', 'timely_filing'),
        '31': ('patient not identified as our insured', 'eligibility'), '45': ('charge exceeds fee schedule', 'contractual'),
        '50': ('not medically necessary', 'medical_necessity'), '96': ('non-covered charge', 'non_covered'),
        '97': ('included in another service (bundled)', 'coding'), '109': ('not covered by this payer', 'eligibility'),
        '197': ('precertification/authorization absent', 'authorization'), '204': ('not covered under benefit plan', 'non_covered'),
        '252': ('additional documentation needed', 'missing_information')}
SECOND_DIGIT = [sum(math.log10(1 + 1 / (10 * d1 + d2)) for d1 in range(1, 10)) for d2 in range(10)]
MAD2_BANDS = [(0.008, 'close_conformity'), (0.010, 'acceptable_conformity'), (0.012, 'marginal_conformity'), (math.inf, 'nonconformity')]
LIMITATIONS = [
    'Every flag is a review prompt with a stated probability, not a finding of fraud, abuse or error: legitimate charge masters, '
    'case mix, repeat services and payer policy explain many of them.',
    'Provider = the claim\'s service organization (no rendering-clinician field on claims); peers = organizations of the same '
    'setting (name prefix) when at least three qualify; otherwise only the setting-neutral tests (intensity for the same service '
    'mix, denial rate) run, against all organizations.',
    'Expected payment uses comparable paid claims of the same payer, service code and setting; without contract fee schedules it '
    'measures consistency with what the payer usually pays, not the contracted amount.',
    'Benjamini-Hochberg controls the expected share of false review flags within each family at the stated FDR; probabilities '
    '1 - q are approximate.',
    'Written off = still denied after the write-off window; pending denials can still be overturned. Rework dollars need a '
    'cost per touch, which is reported only when stated.',
]


# ---- parameters ----------------------------------------------------------------------------
def validate(p):
    if not isinstance(p, dict) or set(p) - FIELDS:
        raise ValueError(f'claims_review parameters must be a subset of {sorted(FIELDS)}')
    focus = p.get('focus', 'all')
    if focus not in FOCUS: raise ValueError(f'claims_review focus must be one of {FOCUS}')
    by = p.get('by', 'payer')
    if by not in BY: raise ValueError(f'claims_review by must be one of {BY}')
    period = p.get('period') or {}
    if not isinstance(period, dict) or set(period) - {'start', 'end'} or \
            any(not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(v)) for v in period.values() if v):
        raise ValueError('claims_review period needs YYYY-MM-DD start/end')
    period = {k: v for k, v in period.items() if v}
    if period.get('start') and period.get('end') and period['start'] > period['end']:
        raise ValueError('claims_review period start is after its end')

    def num(name, default, lo, hi, kind=float):
        v = p.get(name, default)
        if v is None and default is None: return None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi or (kind is int and int(v) != v):
            raise ValueError(f'claims_review {name} must be a number in [{lo}, {hi}]')
        return kind(v)
    out = {'focus': focus, 'by': by, 'period': period, 'fdr': num('fdr', 0.05, 0.001, 0.25),
           'min_claims': num('min_claims', 30, 5, 5000, int), 'window_days': num('window_days', 3, 0, 30, int),
           'writeoff_days': num('writeoff_days', 120, 30, 730, int), 'rework_cost': num('rework_cost', None, 0, 10000),
           'top': num('top', 25, 1, 500, int)}
    for k in ('payer', 'provider'):
        v = p.get(k, '') or ''
        if not isinstance(v, str) or (v and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,80}', v)):
            raise ValueError(f'claims_review {k} must be an identifier or empty')
        out[k] = v
    return out


# ---- statistics ------------------------------------------------------------------------------
def bh(pvalues):
    """Benjamini-Hochberg q-values (step-up, monotone), same order as the input."""
    p = np.asarray([1.0 if v is None or not math.isfinite(v) else min(max(v, 0.0), 1.0) for v in pvalues], float)
    m = len(p)
    if not m: return []
    order = np.argsort(p)
    q = p[order] * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    out = np.empty(m); out[order] = np.minimum(q, 1.0)
    return [float(x) for x in out]


def robust_z(x, peers, floor):
    """(x - median) / (1.4826 MAD) over peers, with an absolute floor on the scale."""
    xs = np.asarray(peers, float)
    med = float(np.median(xs))
    scale = max(1.4826 * float(np.median(np.abs(xs - med))), floor)
    return (x - med) / scale, med, scale


def random_effects_z(own, others):
    """own/others = (estimate, standard error). z of own against the peers' median, with the peers' between-unit variance
    (robust: MAD^2 less their typical sampling variance) plus own sampling variance in the denominator."""
    xs = np.array([e for e, _ in others]); ses = np.array([s for _, s in others])
    med = float(np.median(xs))
    tau2 = max((1.4826 * float(np.median(np.abs(xs - med)))) ** 2 - float(np.median(ses ** 2)), 0.0)
    return (own[0] - med) / math.sqrt(own[1] ** 2 + tau2 + 1e-6), med


def betabinom_tail(k, n, ks, ns):
    """P(K >= k) for a unit with n trials under its peers' beta-binomial (method-of-moments mean and between-unit spread,
    leave-one-out). Returns (p, peer mean)."""
    from scipy.stats import betabinom
    ks, ns = np.asarray(ks, float), np.asarray(ns, float)
    m = float(ks.sum() / max(ns.sum(), 1))
    m = min(max(m, 1e-6), 1 - 1e-6)
    rates = ks / np.maximum(ns, 1)
    between = max(float(np.var(rates)) - m * (1 - m) / max(float(np.mean(ns)), 1), 0.0)
    if between <= 1e-12: return float(binom.sf(k - 1, n, m)), m
    conc = max(m * (1 - m) / between - 1, 1.0)
    return float(betabinom.sf(k - 1, n, m * conc, (1 - m) * conc)), m


def _p_high(z, df):
    """One-sided upper-tail p for a robust z with a t reference (heavier tails than normal for small peer groups)."""
    return float(t_dist.sf(z, max(df, 2)))


def benford_second(amounts, min_n=1000):
    """Second-digit Benford test: counts, chi-square (df 9), Nigrini MAD band. Needs values >= 10 (two significant digits)."""
    digits = []
    for a in amounts:
        try:
            v = abs(float(a))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v) or v < 10: continue
        digits.append(int(f'{v:e}'[2]))
    n = len(digits)
    out = {'n': n, 'expected_share': [round(e, 4) for e in SECOND_DIGIT]}
    if n < min_n:
        return {**out, 'status': 'insufficient', 'reason': f'fewer than {min_n} amounts for a second-digit test', 'p_value': None}
    counts = [digits.count(d) for d in range(10)]
    share = [c / n for c in counts]
    mad = float(np.mean([abs(s - e) for s, e in zip(share, SECOND_DIGIT)]))
    stat = float(sum((c - n * e) ** 2 / (n * e) for c, e in zip(counts, SECOND_DIGIT)))
    band = next(name for limit, name in MAD2_BANDS if mad <= limit)
    return {**out, 'status': 'tested', 'counts': counts, 'observed_share': [round(s, 4) for s in share], 'mad': round(mad, 5),
            'conformity': band, 'chi_square': round(stat, 2), 'p_value': float(chi2.sf(stat, 9))}


def _alr(counts, ref):
    """Additive log-ratio of a count vector against the reference category (pseudo-count 0.5)."""
    c = np.asarray(counts, float) + 0.5
    return np.log(np.delete(c, ref) / c[ref])


def robust_mahalanobis(vectors, ns, shares, i):
    """Distance of unit i's log-ratio mix from its peers (leave-one-out): median center, covariance shrunk halfway to its
    MAD-based diagonal, plus unit i's own multinomial sampling variance (delta method). Returns (d2, df)."""
    others = [v for j, v in enumerate(vectors) if j != i]
    X = np.asarray(others)
    k = X.shape[1]
    center = np.median(X, axis=0)
    diag = np.diag(np.maximum((1.4826 * np.median(np.abs(X - center), axis=0)) ** 2, 1e-4))
    S = np.cov(X, rowvar=False).reshape(k, k) if len(others) > 2 else diag
    S = 0.5 * S + 0.5 * diag
    # Sampling variance of alr_j = log(p_j / p_ref): 1/(n p_j) + 1/(n p_ref), covariance 1/(n p_ref).
    n, p = ns[i], np.maximum(np.asarray(shares[i], float), 0.5 / max(ns[i], 1))
    ref = len(p) - 1
    pj = np.delete(p, ref)
    samp = np.full((k, k), 1 / (n * p[ref])) + np.diag(1 / (n * pj))
    d = vectors[i] - center
    d2 = float(d @ np.linalg.pinv(S + samp) @ d)
    return d2, k


# ---- data ------------------------------------------------------------------------------------
def _connect(path):
    return sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)


def setting(org):
    """Peer setting from the organization id: 'WH-PRACTICE-3' -> 'practice'."""
    base = monitor._peer_type(org)
    return base.split('-')[-1].lower() if base and base != 'unknown' else 'unknown'


def load(db, keep=None, period=None):
    """Claims with their adjudication history, denial reasons and rework touches (dollars)."""
    period = period or {}
    lo, hi = period.get('start') or '0000-01-01', period.get('end') or '9999-12-31'
    cols = {r[1] for r in db.execute('PRAGMA table_info(claim)')}
    adj = 'c.first_adjudication_date' if 'first_adjudication_date' in cols else 'NULL'
    rows = db.execute(f"""SELECT c.claim_id, c.patient_id, c.payer, c.service_date, c.first_status, c.final_status, c.ever_denied,
                                 c.denied_then_paid, c.net_payer_paid_cents, c.billed_cents, c.allowed_cents, c.service_organization_id,
                                 json_extract(r.payload, '$.service_code'), {adj}
                          FROM claim c LEFT JOIN current_record r ON r.stream = 'medical_claims' AND r.business_id = c.claim_id
                          WHERE c.service_date BETWEEN ? AND ?""", (lo, hi + 'T99')).fetchall()
    claims = {}
    for (cid, pid, payer, svc, first, final, ever, dtp, net, billed, allowed, org, code, adj_date) in rows:
        if keep is not None and pid not in keep: continue
        claims[cid] = {'claim_id': cid, 'patient_id': pid, 'payer': payer or 'unknown', 'provider': org or 'unknown',
                       'setting': setting(org), 'service_code': code or 'unknown', 'service_date': (svc or '')[:10],
                       'billed': (billed or 0) / 100, 'allowed': (allowed or 0) / 100, 'paid': (net or 0) / 100,
                       'first_status': first, 'final_status': final, 'ever_denied': bool(ever), 'denied_then_paid': bool(dtp),
                       'history': [], 'denial_reasons': [], 'touches': Counter()}
    if not claims: return []
    for cid, st, ev, paid, alw, pr, rid, orig in db.execute(
            """SELECT json_extract(payload, '$.claim_id'), json_extract(payload, '$.status'), event_date,
                      json_extract(payload, '$.payer_paid_cents'), json_extract(payload, '$.allowed_cents'),
                      json_extract(payload, '$.patient_responsibility_cents'), record_id,
                      json_extract(payload, '$.original_adjudication_record_id')
               FROM record WHERE stream = 'medical_claims'
               ORDER BY available_date, json_extract(payload, '$.available_time'), record_id"""):
        c = claims.get(cid)
        if c: c['history'].append({'status': st, 'date': (ev or '')[:10], 'paid': (paid or 0) / 100, 'allowed': (alw or 0) / 100,
                                   'patient_responsibility': (pr or 0) / 100, 'record_id': rid, 'reverses': orig})
    for c in claims.values(): settle(c)
    seen = set()
    for cid, g, code, amount in db.execute("SELECT claim_id, group_code, reason_code, amount_cents FROM claim_adjustment "
                                           "WHERE adjudication_status = 'denied'"):
        c = claims.get(cid)
        if c and (cid, g, code) not in seen:
            seen.add((cid, g, code))
            c['denial_reasons'].append(f'{g}-{code}')
    streams = {s for (s,) in db.execute('SELECT DISTINCT stream FROM record')}
    touch_sql = [("medical_claim_submissions", "json_extract(payload, '$.submission_kind') = 'corrected'", "'corrected_submission'"),
                 ("claim_corrections", "1", "coalesce(json_extract(payload, '$.reason'), 'correction')"),
                 ("claim_acknowledgements", "json_extract(payload, '$.status') LIKE 'rejected%'", "json_extract(payload, '$.status')")]
    for stream, where, what in touch_sql:
        if stream not in streams: continue
        for cid, kind in db.execute(f"SELECT json_extract(payload, '$.claim_id'), {what} FROM record WHERE stream = ? AND {where}", (stream,)):
            c = claims.get(cid)
            if c: c['touches'][f'{stream}:{kind}'] += 1
    return list(claims.values())


def settle(c):
    """Effective state from the linked adjudication history. A reversal names the payment it reverses
    (original_adjudication_record_id), so a replacement payment that arrived before its reversal still counts as the live
    payment: the received last-arrival status would call such a repriced, repaid claim 'reversed'."""
    h = c['history']
    if not h: return c
    reversed_ids = {x['reverses'] for x in h if x['status'] == 'reversed' and x.get('reverses')}
    # A reversal without a link reverses the latest payment before it.
    for i, x in enumerate(h):
        if x['status'] == 'reversed' and not x.get('reverses'):
            prior = [y for y in h[:i] if y['status'] == 'paid' and y.get('record_id') not in reversed_ids]
            if prior: reversed_ids.add(prior[-1].get('record_id'))
    live = [x for x in h if x['status'] == 'paid' and x.get('record_id') not in reversed_ids]
    c['received_final_status'] = c['final_status']
    c['reversed_payments'] = [x for x in h if x['status'] == 'paid' and x.get('record_id') in reversed_ids]
    c['live_payment'] = live[-1] if live else None
    if live:
        c['final_status'] = 'paid'
        c['allowed'] = live[-1]['allowed']
    elif c['reversed_payments']:
        c['final_status'] = 'reversed'
    return c


# ---- 1. provider peer review -------------------------------------------------------------------
MIN_PEERS = 3


def _peer_groups(units):
    """provider -> (peers, label, same_setting). Same setting when it has >= MIN_PEERS qualifying providers; otherwise all
    organizations, which only setting-neutral tests (intensity for the same service mix, denial rate) use."""
    by = defaultdict(list)
    for u in units: by[u['setting']].append(u)
    return {u['provider']: (by[u['setting']], u['setting'], True) if len(by[u['setting']]) >= MIN_PEERS else (units, 'all_organizations', False)
            for u in units}


def provider_review(claims, min_claims=30):
    """Per provider tests (p-values one-sided toward higher billing/use/denials) and dollars at stake. Returns (units, tests)."""
    per = defaultdict(list)
    for c in claims: per[c['provider']].append(c)
    units = []
    for org, cs in per.items():
        if len(cs) < min_claims: continue
        units.append({'provider': org, 'setting': setting(org), 'claims': len(cs), 'patients': len({c['patient_id'] for c in cs}),
                      'billed': round(sum(c['billed'] for c in cs), 2), 'allowed': round(sum(c['allowed'] for c in cs), 2),
                      'first_pass_denials': sum(c['first_status'] == 'denied' for c in cs)})
    skipped = sorted(org for org, cs in per.items() if len(cs) < min_claims)
    if len(units) < 3: return units, [], skipped
    groups = _peer_groups(units)
    tests = []
    # a) billing intensity for the same service mix (monitor's EB-shrunk log O/E).
    tuples = [(c['claim_id'], c['patient_id'], c['payer'], c['service_date'], c['first_status'], c['final_status'],
               round(c['billed'] * 100), round(c['allowed'] * 100), None, c['provider'], c['service_code']) for c in claims]
    po = {r['organization']: r for r in monitor.peer_outliers(tuples, min_claims=min_claims).get('rows', [])}
    for u in units:
        peers, label, _ = groups[u['provider']]
        r = po.get(u['provider'])
        if not r: continue
        z, med, _ = robust_z(math.log(r['oe_ratio_shrunk']), [math.log(po[x['provider']]['oe_ratio_shrunk']) for x in peers if x['provider'] in po], 0.05)
        effect = math.exp(math.log(r['oe_ratio_shrunk']) - med)
        u.update(intensity_vs_peers=round(effect, 3), intensity_z=round(z, 2))
        tests.append({'kind': 'billing_intensity', 'provider': u['provider'], 'p': _p_high(z, len(peers) - 1), 'z': round(z, 2),
                      'effect': round(effect, 3), 'material': bool(effect >= 1.15), 'peer_group': label, 'peers': len(peers),
                      'dollars': round(max(0.0, u['allowed'] * (1 - 1 / effect)), 2),
                      'reason': f"bills {effect:.2f}x its {label.replace('_', ' ')} peers for the same service mix"})
    # b) services per patient (random-effects z) and c) first-pass denial rate (beta-binomial predictive tail vs peers).
    rows = [{'provider': u['provider'], 'claims': u['claims'], 'patients': u['patients'], 'denials': u['first_pass_denials']} for u in units]
    # Claims per patient: log mean with its delta-method standard error from the per-patient claim counts.
    log_rate = {}
    for u in units:
        counts = np.array(list(Counter(c['patient_id'] for c in per[u['provider']]).values()), float)
        mean = float(counts.mean())
        log_rate[u['provider']] = (math.log(mean), float(counts.std(ddof=1) / math.sqrt(len(counts)) / mean) if len(counts) > 1 else 1.0)
    vbc._beta_binomial(rows, 'denials', 'claims')
    denial = {r['provider']: r['shrunk_rate'] for r in rows}
    for u in units:
        peers, label, same = groups[u['provider']]
        mean_allowed = u['allowed'] / u['claims'] if u['claims'] else 0
        others = [x for x in peers if x['provider'] != u['provider']]
        z, med = random_effects_z(log_rate[u['provider']], [log_rate[x['provider']] for x in others])
        ratio = math.exp(log_rate[u['provider']][0] - med)
        u.update(claims_per_patient=round(u['claims'] / max(u['patients'], 1), 2), volume_vs_peers=round(ratio, 3) if same else None)
        if same: tests.append({'kind': 'services_per_patient', 'provider': u['provider'], 'p': _p_high(z, len(others) - 1), 'z': round(z, 2),
                      'effect': round(ratio, 3), 'material': bool(ratio >= 1.25), 'peer_group': label, 'peers': len(peers),
                      'dollars': round(max(0.0, u['claims'] - u['patients'] * math.exp(med)) * mean_allowed, 2),
                      'reason': f"bills {ratio:.2f}x the claims per patient of its {label.replace('_', ' ')} peers"})
        others = [x for x in peers if x['provider'] != u['provider']]
        pval, peer_rate = betabinom_tail(u['first_pass_denials'], u['claims'], [x['first_pass_denials'] for x in others], [x['claims'] for x in others])
        z = float(norm.isf(max(pval, 1e-300)))
        excess = max(0.0, u['first_pass_denials'] - u['claims'] * peer_rate)
        u.update(first_pass_denial_rate=round(u['first_pass_denials'] / u['claims'], 4), denial_rate_shrunk=round(denial[u['provider']], 4))
        tests.append({'kind': 'denial_rate', 'provider': u['provider'], 'p': pval, 'z': round(z, 2),
                      'effect': round(denial[u['provider']] / max(peer_rate, 1e-9), 3),
                      'material': bool(denial[u['provider']] >= 1.5 * peer_rate and denial[u['provider']] - peer_rate >= 0.02),
                      'peer_group': label, 'peers': len(peers), 'dollars': round(excess * mean_allowed, 2),
                      'reason': f"first-pass denial rate {denial[u['provider']]:.1%} (shrunk) vs {peer_rate:.1%} for peers: rework and delayed cash"})
    # d) service mix: robust Mahalanobis on additive log-ratios, within each peer group.
    by_group = defaultdict(list)
    for u in units:
        if groups[u['provider']][2]: by_group[groups[u['provider']][1]].append(u)  # service mix is only comparable within a setting
    mix_cost = defaultdict(list)
    for c in claims: mix_cost[c['service_code']].append(c['allowed'])
    mean_cost = {k: float(np.mean(v)) for k, v in mix_cost.items()}
    for label, us in by_group.items():
        peers = us
        codes = Counter(c['service_code'] for c in claims if c['provider'] in {u['provider'] for u in peers})
        total = sum(codes.values())
        top = [k for k, v in codes.most_common(6) if v / total >= 0.01]
        if len(top) < 1: continue
        cats = top + ['other']
        counts = {}
        for u in peers:
            cnt = Counter(c['service_code'] if c['service_code'] in top else 'other' for c in per[u['provider']])
            counts[u['provider']] = [cnt.get(k, 0) for k in cats]
        # The reference category is the most common one (always last after reordering).
        order = cats[1:] + cats[:1]
        idx = [cats.index(k) for k in order]
        vecs = [_alr([counts[u['provider']][j] for j in idx], len(idx) - 1) for u in peers]
        ns = [sum(counts[u['provider']]) for u in peers]
        shares = [[counts[u['provider']][j] / max(sum(counts[u['provider']]), 1) for j in idx] for u in peers]
        if all(np.allclose(v, vecs[0]) for v in vecs): continue
        peer_share = np.median(np.asarray(shares), axis=0)
        peer_share = peer_share / peer_share.sum() if peer_share.sum() else peer_share
        for i, u in enumerate(peers):
            if u not in us: continue
            d2, df = robust_mahalanobis(vecs, ns, shares, i)
            p = float(chi2.sf(d2, df))
            own = np.asarray(shares[i])
            unit_cost = [np.mean([c['allowed'] for c in per[u['provider']] if (c['service_code'] if c['service_code'] in top else 'other') == order[j]] or
                                 [mean_cost.get(order[j], 0.0)]) for j in range(len(order))]
            dollars = max(0.0, float(ns[i] * np.dot(own - peer_share, unit_cost)))
            j = int(np.argmax(own - peer_share))
            u['mix_distance'] = round(math.sqrt(d2), 2)
            tests.append({'kind': 'service_mix', 'provider': u['provider'], 'p': p, 'z': round(math.sqrt(d2), 2), 'effect': round(float(own[j] / max(peer_share[j], 1e-9)), 3),
                          'material': bool(float(own[j] - peer_share[j]) >= 0.05), 'peer_group': label,
                          'peers': len(peers), 'dollars': round(dollars, 2),
                          'reason': f"service mix differs from peers (robust Mahalanobis d={math.sqrt(d2):.1f}, df={df}); "
                                    f"{order[j].replace('_', ' ')} is {own[j]:.0%} of claims vs {peer_share[j]:.0%} for peers"})
    return units, tests, skipped


# ---- 2. Benford ---------------------------------------------------------------------------------
def benford_review(claims, min_first=300, min_second=1000):
    """First and second digit tests on billed amounts, overall and per provider; guards stated per group."""
    groups = [('all', claims)] + sorted(((org, cs) for org, cs in _by(claims, 'provider').items()), key=lambda x: x[0])
    rows, tests = [], []
    for g, cs in groups:
        amounts = [c['billed'] for c in cs if c['billed'] > 0]
        first = monitor.benford_test(amounts, chi_min_n=min_first)
        second = benford_second(amounts, min_second)
        row = {'group': g, 'n': first['n'], 'first_digit_status': first['status'], 'first_digit_mad': first.get('mad'),
               'first_digit_conformity': first.get('conformity'), 'first_digit_p': first.get('p_value'),
               'second_digit_status': second['status'], 'second_digit_mad': second.get('mad'),
               'second_digit_conformity': second.get('conformity'), 'second_digit_p': second.get('p_value'),
               'reason': first.get('reason') or second.get('reason'), 'billed': round(sum(amounts), 2)}
        rows.append(row)
        if first['status'] in ('review_flag', 'conforms', 'nonconformity_not_significant'):
            tests.append({'kind': 'benford_first_digit', 'provider': g, 'p': first['p_value'], 'material': bool(first['conformity'] == 'nonconformity'),
                          'z': None, 'effect': first['mad'], 'dollars': None, 'n': first['n'],
                          'reason': f"billed amounts depart from Benford's first-digit law (MAD {first['mad']}, {first['conformity'].replace('_', ' ')}, n={first['n']})"})
        if second['status'] == 'tested' and first['status'] not in ('not_applicable',):
            tests.append({'kind': 'benford_second_digit', 'provider': g, 'p': second['p_value'], 'material': second['conformity'] == 'nonconformity',
                          'z': None, 'effect': second['mad'], 'dollars': None, 'n': second['n'],
                          'reason': f"billed amounts depart from Benford's second-digit law (MAD {second['mad']}, {second['conformity'].replace('_', ' ')}, n={second['n']})"})
    return rows, tests


def _by(claims, key):
    out = defaultdict(list)
    for c in claims: out[c[key]].append(c)
    return out


# ---- 3. duplicates, resubmission chains, reversal churn -------------------------------------------
def duplicate_review(claims, window_days=3, horizon=60):
    """Exact and near-duplicate pairs with P(duplicate) from the background rate of legitimate repeats of the same service."""
    seq = defaultdict(list)
    for c in claims:
        if c['service_date']: seq[(c['patient_id'], c['provider'], c['service_code'])].append(c)
    gaps = defaultdict(list)          # service code -> (gap days, amounts within 10%, amounts identical) of consecutive repeats
    pairs = []
    for (pid, org, code), cs in seq.items():
        cs.sort(key=lambda c: (c['service_date'], c['claim_id']))
        for a, b in zip(cs, cs[1:]):
            gap = (date.fromisoformat(b['service_date']) - date.fromisoformat(a['service_date'])).days
            close = a['billed'] > 0 and abs(a['billed'] - b['billed']) <= 0.1 * max(a['billed'], b['billed'])
            same = round(a['billed'], 2) == round(b['billed'], 2)
            gaps[code].append((gap, close, same))
            if gap > window_days or not close: continue
            exact = gap == 0 and same
            pairs.append({'kind': 'exact' if exact else 'near', 'claim_ids': [a['claim_id'], b['claim_id']], 'patient_id': pid,
                          'provider': org, 'payer': b['payer'], 'service_code': code, 'service_dates': [a['service_date'], b['service_date']],
                          'gap_days': gap, 'billed': b['billed'], 'dollars': round(b['allowed'] if b['final_status'] == 'paid' else 0.0, 2)})
    # Background: legitimate repeats of the service arrive at a roughly constant rate per day of gap over (window, horizon];
    # extrapolate into the window, times the share of those repeats whose amounts agree as closely as the pair's do.
    prob = {}
    for code, gs in gaps.items():
        later = [g for g in gs if window_days < g[0] <= horizon]
        per_day = len(later) / max(horizon - window_days, 1)
        for kind, width, agree in (('exact', 1, lambda g: g[2]), ('near', window_days + 1, lambda g: g[1])):
            share = (sum(1 for g in later if agree(g)) + 0.5) / (len(later) + 1)  # Jeffreys-style: never exactly 0
            observed = sum(1 for p in pairs if p['service_code'] == code and p['kind'] == kind)
            expected = per_day * width * share
            prob[(code, kind)] = max(0.0, 1 - expected / observed) if observed else 0.0
    for p in pairs:
        p['probability'] = round(prob[(p['service_code'], p['kind'])], 3)
        p['expected_dollars'] = round(p['dollars'] * p['probability'], 2)
    pairs.sort(key=lambda p: -p['expected_dollars'])
    return pairs


def churn_review(claims):
    """Per claim: rework touches (corrected submissions, corrections, rejections) and reversal churn with dollars taken back."""
    rows = []
    for c in claims:
        h = c['history']
        if 'live_payment' not in c: settle(c)
        reversals = [x for x in h if x['status'] == 'reversed']
        originals = c.get('reversed_payments') or []
        # Dollars in allowed terms (payer + patient share): what the provider is owed for the service.
        taken = sum(x['allowed'] for x in originals) if originals else sum(x['allowed'] for x in reversals)
        first = h.index(originals[0]) if originals else (h.index(reversals[0]) if reversals else len(h))
        replacements = [x for x in h[first + 1:] if x['status'] == 'paid' and x not in originals]
        repaid = sum(x['allowed'] for x in replacements)
        kind = None
        if reversals:
            kind = 'repriced' if replacements and repaid < taken - 0.005 else 'reprocessed_same' if replacements else 'taken_back'
        short = 0.0
        last_paid = c.get('live_payment')
        if last_paid and c['final_status'] == 'paid':
            short = round(max(0.0, last_paid['allowed'] - last_paid['patient_responsibility'] - last_paid['paid']), 2)
        rows.append({'claim_id': c['claim_id'], 'provider': c['provider'], 'payer': c['payer'], 'touches': sum(c['touches'].values()),
                     'touch_kinds': dict(c['touches']), 'reversals': len(reversals), 'reversal_kind': kind,
                     'taken_back': round(max(0.0, taken - repaid) if kind in ('repriced', 'taken_back') else 0.0, 2),
                     'short_paid_vs_adjudication': short})
    return rows


def rate_tests(claims, flag_of, key, kind, dollars_of, reason):
    """Per group binomial test of a claim-level flag rate against the pooled rate (one-sided high)."""
    total, hits, dollars = Counter(), Counter(), defaultdict(float)
    for c in claims:
        g = c[key]; total[g] += 1
        if flag_of(c): hits[g] += 1; dollars[g] += dollars_of(c)
    n, k = sum(total.values()), sum(hits.values())
    if not n or not k or len(total) < 2: return []
    pooled = k / n
    out = []
    for g in total:
        p = float(binom.sf(hits[g] - 1, total[g], pooled)) if hits[g] else 1.0
        rate = hits[g] / total[g]
        excess = max(0.0, hits[g] - total[g] * pooled)
        out.append({'kind': kind, 'provider' if key == 'provider' else 'payer': g, 'group_by': key, 'p': p, 'z': None,
                    'effect': round(rate / pooled, 3), 'material': rate >= 1.25 * pooled and hits[g] >= 5,
                    'dollars': round(dollars[g] * excess / hits[g], 2) if hits[g] else 0.0, 'n': total[g],
                    'reason': reason(g, rate, pooled, hits[g], total[g])})
    return out


# ---- 4. underpayment -------------------------------------------------------------------------------
def expected_payment(claims, min_cell=20, level=0.90):
    """Comparable-claims model per (payer, service, setting): level (allowed) or ratio (allowed / billed), whichever has the
    smaller robust coefficient of variation. Returns cell -> model."""
    cells = defaultdict(list)
    for c in claims:
        if c['final_status'] == 'paid' and c['allowed'] > 0 and c['billed'] > 0:
            cells[(c['payer'], c['service_code'], c['setting'])].append(c)
    models = {}
    for cell, cs in cells.items():
        if len(cs) < min_cell: continue
        lv = np.array([c['allowed'] for c in cs]); rt = np.array([c['allowed'] / c['billed'] for c in cs])
        cv = lambda x: 1.4826 * float(np.median(np.abs(x - np.median(x)))) / max(float(np.median(x)), 1e-9)
        use_ratio = cv(rt) < cv(lv)
        x = rt if use_ratio else lv
        med = float(np.median(x))
        scale = max(1.4826 * float(np.median(np.abs(x - med))), 0.01 * med)
        lo, hi = np.quantile(x, [(1 - level) / 2, 1 - (1 - level) / 2])
        models[cell] = {'model': 'ratio_of_billed' if use_ratio else 'allowed_level', 'center': med, 'scale': scale,
                        'interval': [float(lo), float(hi)], 'n': len(cs), 'robust_cv': round(min(cv(rt), cv(lv)), 4)}
    return models


def _expected(c, models):
    m = models.get((c['payer'], c['service_code'], c['setting']))
    if not m: return None, None
    k = c['billed'] if m['model'] == 'ratio_of_billed' else 1.0
    return m, (m['center'] * k, [m['interval'][0] * k, m['interval'][1] * k])


def underpayment_review(claims, models):
    rows = []
    for c in claims:
        if c['final_status'] != 'paid' or c['allowed'] <= 0: continue
        m, est = _expected(c, models)
        if not m: continue
        x = c['allowed'] / c['billed'] if m['model'] == 'ratio_of_billed' else c['allowed']
        z = (x - m['center']) / m['scale']
        shortfall = est[0] - c['allowed']
        rows.append({'claim_id': c['claim_id'], 'payer': c['payer'], 'provider': c['provider'], 'service_code': c['service_code'],
                     'setting': c['setting'], 'service_date': c['service_date'], 'billed': c['billed'], 'allowed': c['allowed'],
                     'expected': round(est[0], 2), 'expected_interval': [round(est[1][0], 2), round(est[1][1], 2)], 'model': m['model'],
                     'z': round(z, 2), 'p': float(t_dist.cdf(z, max(m['n'] - 1, 2))), 'shortfall': round(shortfall, 2),
                     'material': shortfall >= max(5.0, 0.10 * est[0])})
    return rows


# ---- 5. denials -------------------------------------------------------------------------------------
def reason_of(code):
    g, _, rc = code.partition('-')
    label, cat = CARC.get(rc, (f'{g}-{rc}', 'other'))
    return label, cat


def denial_review(claims, models, as_of, writeoff_days=120):
    """Per denied claim: reason, outcome (overturned / written_off / pending), days to resolution, dollars."""
    cutoff = date.fromisoformat(as_of)
    out = []
    global_ratio = np.median([c['allowed'] / c['billed'] for c in claims if c['final_status'] == 'paid' and c['allowed'] > 0 and c['billed'] > 0] or [0.65])
    for c in claims:
        if not c['ever_denied']: continue
        h = c['history']
        first = next((x for x in h if x['status'] == 'denied'), None)
        if not first: continue
        after = h[h.index(first) + 1:]
        paid = next((x for x in after if x['status'] == 'paid'), None)
        _, est = _expected(c, models)
        value = est[0] if est else c['billed'] * float(global_ratio)
        age = (cutoff - date.fromisoformat(first['date'])).days if first['date'] else None
        if paid:
            outcome, days = 'overturned', (date.fromisoformat(paid['date']) - date.fromisoformat(first['date'])).days if paid['date'] and first['date'] else None
            recovered = paid['allowed']
        else:
            outcome, days, recovered = ('written_off' if age is not None and age >= writeoff_days else 'pending'), None, 0.0
        code = (c['denial_reasons'] or ['unspecified'])[0]
        label, cat = reason_of(code) if code != 'unspecified' else ('no reason code received', 'unspecified')
        out.append({'claim_id': c['claim_id'], 'payer': c['payer'], 'provider': c['provider'], 'setting': c['setting'],
                    'service_code': c['service_code'], 'first_pass': c['first_status'] == 'denied', 'reason_code': code,
                    'reason': label, 'category': cat, 'outcome': outcome, 'days_to_resolution': days, 'denial_age_days': age,
                    'recovered': round(recovered, 2), 'written_off': round(value, 2) if outcome == 'written_off' else 0.0,
                    'pending': round(value, 2) if outcome == 'pending' else 0.0})
    return out


def denial_tables(claims, denials, by='payer'):
    keyf = {'payer': lambda c: c['payer'], 'provider': lambda c: c['provider'], 'setting': lambda c: c['setting'],
            'service': lambda c: c['service_code']}[by]
    total = Counter(keyf(c) for c in claims)
    rows = {g: {'group': g, 'claims': n, 'first_pass_denials': 0, 'denied_claims': 0, 'overturned': 0, 'written_off_claims': 0, 'pending_claims': 0,
                'recovered': 0.0, 'written_off': 0.0, 'pending': 0.0, '_days': []} for g, n in total.items()}
    for d in denials:
        r = rows[{'payer': d['payer'], 'provider': d['provider'], 'setting': d['setting'], 'service': d['service_code']}[by]]
        r['denied_claims'] += 1; r['first_pass_denials'] += d['first_pass']
        r['overturned'] += d['outcome'] == 'overturned'; r['written_off_claims'] += d['outcome'] == 'written_off'
        r['pending_claims'] += d['outcome'] == 'pending'
        r['recovered'] += d['recovered']; r['written_off'] += d['written_off']; r['pending'] += d['pending']
        if d['days_to_resolution'] is not None: r['_days'].append(d['days_to_resolution'])
    table = list(rows.values())
    vbc._beta_binomial(table, 'first_pass_denials', 'claims')
    for r in table:
        days = r.pop('_days')
        resolved = r['overturned'] + r['written_off_claims']
        r.update(first_pass_denial_rate=round(r['first_pass_denials'] / r['claims'], 4) if r['claims'] else None,
                 denial_rate_shrunk=round(r.pop('shrunk_rate'), 4), denial_rate_ci95=[round(x, 4) for x in r.pop('rate_ci95')],
                 overturn_rate=round(r['overturned'] / resolved, 4) if resolved else None,
                 median_days_to_resolution=float(np.median(days)) if days else None,
                 days_to_resolution_iqr=[float(x) for x in np.quantile(days, [0.25, 0.75])] if days else None,
                 recovered=round(r['recovered'], 2), written_off=round(r['written_off'], 2), pending=round(r['pending'], 2))
    table.sort(key=lambda r: -(r['written_off'] + r['pending']))
    reasons = defaultdict(lambda: {'denials': 0, 'overturned': 0, 'written_off_claims': 0, 'written_off': 0.0, 'recovered': 0.0, 'pending': 0.0})
    for d in denials:
        x = reasons[(d['reason_code'], d['reason'], d['category'])]
        x['denials'] += 1; x['overturned'] += d['outcome'] == 'overturned'; x['written_off_claims'] += d['outcome'] == 'written_off'
        x['written_off'] += d['written_off']; x['recovered'] += d['recovered']; x['pending'] += d['pending']
    n = sum(v['denials'] for v in reasons.values())
    pareto, cum = [], 0
    for (code, label, cat), v in sorted(reasons.items(), key=lambda kv: -kv[1]['denials']):
        cum += v['denials']
        pareto.append({'reason_code': code, 'reason': label, 'category': cat, **{k: round(x, 2) if isinstance(x, float) else x for k, x in v.items()},
                       'share': round(v['denials'] / n, 4), 'cumulative_share': round(cum / n, 4)})
    payers = sorted({d['payer'] for d in denials}); codes = sorted({d['reason_code'] for d in denials})
    independence = None
    if len(payers) >= 2 and len(codes) >= 2:
        m = np.array([[sum(1 for d in denials if d['payer'] == p and d['reason_code'] == c) for c in codes] for p in payers])
        stat, pv, dof, _ = chi2_contingency(m)
        independence = {'chi_square': round(float(stat), 2), 'df': int(dof), 'p_value': float(pv), 'payers': payers, 'reason_codes': codes}
    else:
        independence = {'status': 'not_testable', 'reason': f'{len(codes)} reason code(s) across {len(payers)} payer(s): reason mix cannot differ by payer'}
    return table, pareto, independence


# ---- orchestration ---------------------------------------------------------------------------------
def _flag(category, subject, reason, dollars, probability, q=None, direction='review', claims=None, evidence=None, follow_up=None):
    exp = round(dollars * probability, 2) if dollars is not None and probability is not None else None
    return {'category': category, 'subject': subject, 'reason': reason, 'direction': direction,
            'dollars_at_stake': round(dollars, 2) if dollars is not None else None, 'probability': round(probability, 3) if probability is not None else None,
            'expected_dollars': exp, 'q_value': round(q, 5) if q is not None else None, 'claims': claims,
            'evidence': evidence or {}, 'follow_up': follow_up}


def _bh_flags(tests, fdr, category_of, subject_key='provider', direction='overpayment_risk', follow_up=None):
    qs = bh([t['p'] for t in tests])
    out = []
    for t, q in zip(tests, qs):
        t['q'] = q
        if q <= fdr and t['material']:
            out.append(_flag(category_of(t), t.get(subject_key) or t.get('payer'), t['reason'], t['dollars'], 1 - q, q, direction,
                             t.get('n'), {k: v for k, v in t.items() if k not in ('reason',)}, follow_up))
    return out


def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    db = _connect(path)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        claims = load(db, set(patient_ids) if patient_ids is not None else None, p['period'])
    finally:
        db.close()
    return review(claims, p, as_of)


def review(claims, params, as_of):
    """Run the requested families on loaded claims (dicts as built by load())."""
    p = validate(params)
    if p['payer']: claims = [c for c in claims if c['payer'].lower() == p['payer'].lower()]
    runs = RUNS.get(p['focus'], (p['focus'],))
    report = {'schema': VERSION, 'parameters': p, 'as_of': as_of, 'claims': len(claims),
              'patients': len({c['patient_id'] for c in claims}), 'providers': len({c['provider'] for c in claims}),
              'payers': sorted({c['payer'] for c in claims}), 'billed': round(sum(c['billed'] for c in claims), 2),
              'allowed': round(sum(c['allowed'] for c in claims), 2), 'runs': list(runs), 'flags': [], 'notes': [],
              'method': METHOD, 'limitations': LIMITATIONS}
    if claims:
        dates = sorted(c['service_date'] for c in claims if c['service_date'])
        report['period'] = [dates[0], dates[-1]] if dates else None
    if not claims:
        report['notes'].append('No medical claims in scope.')
        return report
    flags = []
    focus_provider = p['provider'].lower()
    if 'providers' in runs:
        units, tests, skipped = provider_review(claims, p['min_claims'])
        f = _bh_flags(tests, p['fdr'], lambda t: f"provider_{t['kind']}", follow_up='Which providers bill unusually compared to their peers?')
        report['provider_review'] = {'units': sorted(units, key=lambda u: -u['claims']), 'tests': len(tests), 'flags': len(f),
                                     'below_min_claims': skipped, 'min_claims': p['min_claims']}
        if skipped: report['notes'].append(f"{len(skipped)} provider(s) with fewer than {p['min_claims']} claims were not peer-tested.")
        if len(units) < 3: report['notes'].append('Fewer than three providers with enough claims: no peer comparison possible.')
        flags += f
    if 'benford' in runs:
        rows, tests = benford_review(claims)
        f = _bh_flags(tests, p['fdr'], lambda t: t['kind'], follow_up='Any suspicious billing patterns?')
        report['benford'] = rows
        tested = sum(1 for r in rows if r['first_digit_status'] in ('review_flag', 'conforms', 'nonconformity_not_significant'))
        report['notes'].append(f"Benford: {tested} of {len(rows)} groups had enough volume and spread to test; the rest are listed with the reason "
                               f"(fewer than 300 amounts, under two orders of magnitude, or fee-schedule repeated amounts).")
        flags += f
    if 'duplicates' in runs:
        pairs = duplicate_review(claims, p['window_days'])
        churn = churn_review(claims)
        report['duplicates'] = {'pairs': pairs[:200], 'exact_pairs': sum(x['kind'] == 'exact' for x in pairs),
                                'near_pairs': sum(x['kind'] == 'near' for x in pairs), 'window_days': p['window_days'],
                                'expected_duplicate_dollars': round(sum(x['expected_dollars'] for x in pairs), 2)}
        for x in pairs:
            if x['probability'] >= 0.5 and x['dollars'] > 0:
                flags.append(_flag('duplicate_' + x['kind'], x['provider'],
                                   f"{x['kind']} duplicate: patient {x['patient_id']} billed {x['service_code'].replace('_', ' ')} twice "
                                   f"{x['gap_days']} day(s) apart (${x['billed']:,.0f}); P(duplicate) {x['probability']:.0%} from the background rate of repeat services",
                                   x['dollars'], x['probability'], None, 'overpayment_risk', 2, x, 'Any duplicate or resubmitted claims?'))
        by_claim = {c['claim_id']: c for c in claims}
        chains = Counter(min(r['touches'], 4) for r in churn)
        kinds = Counter(k for r in churn for k, n in r['touch_kinds'].items() for _ in range(n))
        rev = Counter(r['reversal_kind'] for r in churn if r['reversal_kind'])
        cost = p['rework_cost']
        report['churn'] = {'claims_with_rework': sum(r['touches'] > 0 for r in churn), 'rework_touches': sum(r['touches'] for r in churn),
                           'chain_length_distribution': {('4+' if k == 4 else str(k)): v for k, v in sorted(chains.items())},
                           'touch_kinds': dict(kinds.most_common()), 'reversals': dict(rev),
                           'taken_back': round(sum(r['taken_back'] for r in churn), 2),
                           'short_paid_vs_adjudication': round(sum(r['short_paid_vs_adjudication'] for r in churn), 2),
                           'rework_cost_per_touch': cost,
                           'rework_dollars': round(cost * sum(r['touches'] for r in churn), 2) if cost is not None else None}
        churn_by = {r['claim_id']: r for r in churn}
        for c in claims: c['_churn'] = churn_by[c['claim_id']]
        tests = rate_tests(claims, lambda c: c['_churn']['touches'] > 0, 'provider', 'rework_rate',
                           lambda c: (cost or 0.0) * c['_churn']['touches'],
                           lambda g, r, pool, k, n: f"{r:.1%} of its claims needed rework (corrected submission, correction or rejection) vs {pool:.1%} pooled ({k} of {n})")
        tests += rate_tests(claims, lambda c: c['_churn']['taken_back'] > 0, 'payer', 'take_back_rate', lambda c: c['_churn']['taken_back'],
                            lambda g, r, pool, k, n: f"took back or repriced {r:.1%} of paid claims vs {pool:.1%} pooled ({k} of {n})")
        for t in tests:
            if t['kind'] == 'rework_rate' and cost is None: t['dollars'] = None
        flags += _bh_flags(tests, p['fdr'], lambda t: t['kind'], subject_key='provider', direction='revenue_leakage',
                           follow_up='Where are we leaking money in claims?')
        for r in churn:
            if r['short_paid_vs_adjudication'] >= 1:
                flags.append(_flag('paid_below_adjudication', by_claim[r['claim_id']]['payer'],
                                   f"claim {r['claim_id']} paid ${r['short_paid_vs_adjudication']:,.2f} less than the payer's own adjudicated amount "
                                   '(allowed - patient responsibility)', r['short_paid_vs_adjudication'], 1.0, None, 'revenue_leakage', 1, r,
                                   'Which claims were underpaid?'))
        repriced = defaultdict(lambda: [0, 0.0])
        for r in churn:
            if r['taken_back'] > 0: repriced[(r['payer'], r['reversal_kind'])][0] += 1; repriced[(r['payer'], r['reversal_kind'])][1] += r['taken_back']
        for (payer, kind), (n, dollars) in repriced.items():
            flags.append(_flag('take_back', payer, f"{n} paid claims {'repriced lower' if kind == 'repriced' else 'reversed without repayment'} "
                                                   f"after payment (${dollars:,.0f} taken back): check the reprocessing against contract terms",
                               dollars, 1.0, None, 'revenue_leakage', n, {'kind': kind}, 'Where are we leaking money in claims?'))
        for c in claims: c.pop('_churn', None)
    models = expected_payment(claims) if {'underpayment', 'denials'} & set(runs) else {}
    if 'underpayment' in runs:
        rows = underpayment_review(claims, models)
        qs = bh([r['p'] for r in rows])
        flagged = []
        for r, q in zip(rows, qs):
            r['q'] = round(q, 5)
            if q <= p['fdr'] and r['material']: flagged.append(r)
        cells = defaultdict(lambda: {'claims': 0, 'flagged': 0, 'shortfall': 0.0})
        for r in rows:
            x = cells[(r['payer'], r['service_code'], r['setting'])]; x['claims'] += 1
        for r in flagged:
            x = cells[(r['payer'], r['service_code'], r['setting'])]; x['flagged'] += 1; x['shortfall'] += r['shortfall']
        cell_rows = []
        for (payer, code, st), x in cells.items():
            m = models[(payer, code, st)]
            cell_rows.append({'payer': payer, 'service_code': code, 'setting': st, 'claims': x['claims'], 'flagged': x['flagged'],
                              'shortfall': round(x['shortfall'], 2), 'model': m['model'], 'expected_center': round(m['center'], 4),
                              'expected_interval': [round(v, 4) for v in m['interval']], 'robust_cv': m['robust_cv']})
        cell_rows.sort(key=lambda r: (-r['shortfall'], -r['claims']))
        modelled = sum(1 for c in claims if c['final_status'] == 'paid' and models.get((c['payer'], c['service_code'], c['setting'])))
        paid = sum(1 for c in claims if c['final_status'] == 'paid')
        report['underpayment'] = {'cells': cell_rows, 'claims_compared': len(rows), 'paid_claims': paid, 'flagged': len(flagged),
                                  'shortfall': round(sum(r['shortfall'] for r in flagged), 2),
                                  'claims': sorted(flagged, key=lambda r: -r['shortfall'])[:200]}
        if paid > modelled:
            report['notes'].append(f'{paid - modelled} paid claims sit in payer x service x setting cells with fewer than 20 comparable claims and were not scored for underpayment.')
        for r in flagged:
            flags.append(_flag('underpayment', r['payer'], f"claim {r['claim_id']} ({r['service_code'].replace('_', ' ')}, {r['provider']}) paid "
                                                           f"${r['allowed']:,.2f} vs ${r['expected']:,.2f} expected for comparable {r['payer']} claims "
                                                           f"(90% range ${r['expected_interval'][0]:,.2f}-${r['expected_interval'][1]:,.2f})",
                               r['shortfall'], 1 - r['q'], r['q'], 'revenue_leakage', 1, r, 'Which claims were underpaid versus similar claims?'))
    if 'denials' in runs:
        denials = denial_review(claims, models, as_of, p['writeoff_days'])
        table, pareto, independence = denial_tables(claims, denials, p['by'])
        resolved = [d for d in denials if d['outcome'] != 'pending']
        writeoff_share = sum(d['outcome'] == 'written_off' for d in resolved) / len(resolved) if resolved else None
        days = [d['days_to_resolution'] for d in denials if d['days_to_resolution'] is not None]
        report['denials'] = {'by': p['by'], 'table': table, 'pareto': pareto, 'payer_reason_independence': independence,
                             'claims': len(claims), 'first_pass_denials': sum(c['first_status'] == 'denied' for c in claims),
                             'first_pass_denial_rate': round(sum(c['first_status'] == 'denied' for c in claims) / len(claims), 4),
                             'denied_claims': len(denials), 'overturned': sum(d['outcome'] == 'overturned' for d in denials),
                             'written_off_claims': sum(d['outcome'] == 'written_off' for d in denials),
                             'pending_claims': sum(d['outcome'] == 'pending' for d in denials),
                             'overturn_rate': round(1 - writeoff_share, 4) if writeoff_share is not None else None,
                             'median_days_to_resolution': float(np.median(days)) if days else None,
                             'recovered': round(sum(d['recovered'] for d in denials), 2), 'written_off': round(sum(d['written_off'] for d in denials), 2),
                             'pending': round(sum(d['pending'] for d in denials), 2), 'writeoff_days': p['writeoff_days']}
        if independence.get('status') == 'not_testable': report['notes'].append('Denial reasons: ' + independence['reason'] + '.')
        for x in pareto:
            if x['written_off'] > 0:
                flags.append(_flag('denial_write_off', x['reason_code'], f"{x['written_off_claims']} denials for '{x['reason']}' ({x['category'].replace('_', ' ')}) "
                                                                         f"were never paid (${x['written_off']:,.0f} expected value written off)",
                                   x['written_off'], 1.0, None, 'revenue_leakage', x['written_off_claims'], x, 'Explain the denial patterns by payer and reason'))
            if x['pending'] > 0 and writeoff_share is not None:
                flags.append(_flag('denial_pending', x['reason_code'], f"{x['denials'] - x['overturned'] - x['written_off_claims']} denials for '{x['reason']}' still open "
                                                                       f"(${x['pending']:,.0f}); {writeoff_share:.0%} of resolved denials end written off",
                                   x['pending'], writeoff_share, None, 'revenue_leakage', None, x, 'Explain the denial patterns by payer and reason'))
        per_denial = (sum(d['written_off'] for d in denials) / max(sum(d['outcome'] == 'written_off' for d in denials), 1)) * (writeoff_share or 0)
        tests = rate_tests(claims, lambda c: c['first_status'] == 'denied', 'payer', 'payer_denial_rate', lambda c: per_denial,
                           lambda g, r, pool, k, n: f"denies {r:.1%} of claims on first pass vs {pool:.1%} across payers ({k} of {n})")
        flags += _bh_flags(tests, p['fdr'], lambda t: t['kind'], subject_key='payer', direction='revenue_leakage',
                           follow_up='Explain the denial patterns by payer and reason')
    if focus_provider:
        flags = [f for f in flags if str(f['subject']).lower() == focus_provider or str((f['evidence'] or {}).get('provider', '')).lower() == focus_provider]
    flags.sort(key=lambda f: (f['expected_dollars'] is None, -(f['expected_dollars'] or 0), f['q_value'] if f['q_value'] is not None else 1))
    for i, f in enumerate(flags, 1): f['rank'] = i
    report['flags'] = flags
    report['leakage_pareto'] = leakage_pareto(report)
    return report


def leakage_pareto(report):
    """Dollars by leak, largest first, with cumulative share (observed write-offs and take-backs; expected for pending and flags)."""
    items = []
    d = report.get('denials')
    if d:
        for x in d['pareto']:
            if x['written_off']: items.append({'leak': f"Denials written off: {x['reason']}", 'dollars': x['written_off'], 'kind': 'denial_write_off'})
        pend = sum(f['expected_dollars'] or 0 for f in report['flags'] if f['category'] == 'denial_pending')
        if pend: items.append({'leak': 'Open denials (expected write-off)', 'dollars': round(pend, 2), 'kind': 'denial_pending'})
    u = report.get('underpayment')
    if u and u['shortfall']: items.append({'leak': 'Underpaid vs comparable claims', 'dollars': u['shortfall'], 'kind': 'underpayment'})
    c = report.get('churn')
    if c:
        for kind, label in (('repriced', 'Repriced lower after payment'), ('taken_back', 'Reversed without repayment')):
            v = sum(f['dollars_at_stake'] or 0 for f in report['flags'] if f['category'] == 'take_back' and f['evidence'].get('kind') == kind)
            if v: items.append({'leak': label, 'dollars': round(v, 2), 'kind': 'take_back'})
        if c['short_paid_vs_adjudication']: items.append({'leak': 'Paid below own adjudication', 'dollars': c['short_paid_vs_adjudication'], 'kind': 'short_pay'})
        if c['rework_dollars']: items.append({'leak': 'Rework (stated cost per touch)', 'dollars': c['rework_dollars'], 'kind': 'rework'})
    dup = report.get('duplicates')
    if dup and dup['expected_duplicate_dollars']:
        items.append({'leak': 'Duplicate payments (overpayment risk)', 'dollars': dup['expected_duplicate_dollars'], 'kind': 'duplicate'})
    items.sort(key=lambda x: -x['dollars'])
    total, cum = sum(x['dollars'] for x in items), 0.0
    for x in items:
        cum += x['dollars']; x['share'] = round(x['dollars'] / total, 4) if total else None; x['cumulative_share'] = round(cum / total, 4) if total else None
    return items


METHOD = ('Provider peers: billing intensity O/E for the same service mix (EB-shrunk, monitor.peer_outliers), robust median/MAD z, t(peers-1) one-sided p), '
          'claims per patient (random-effects z with own SE and peers\' robust between variance), first-pass denial rate (beta-binomial tail vs '
          'leave-one-out peers); service '
          'mix = robust Mahalanobis on additive log-ratio shares (leave-one-out median, covariance shrunk to its MAD diagonal plus own multinomial '
          'variance, chi-square p). Benford first digit (chi-square + Nigrini MAD; >= 300 amounts, >= 2 orders of magnitude, not fee-schedule '
          'dominated) and second digit (>= 1,000 amounts). Duplicates: same patient/provider/service within the window and amount within 10%; '
          'P(duplicate) = 1 - expected legitimate repeats (per-day repeat rate beyond the window x window x share of repeats whose amounts agree) / observed. Churn: corrected submissions, corrections, '
          'clearinghouse rejections, reversals and repricing. Underpayment: per payer x service x setting, allowed level or allowed/billed ratio '
          '(smaller robust CV), robust z, one-sided t(n-1) p, flagged when also >= 10% and >= $5 short, 90% empirical interval. Denials: CARC categories, first-pass rate (beta-binomial), overturn '
          'rate, days to payment, recovered vs written off (expected payment of the claim) vs pending. Benjamini-Hochberg within each family; '
          'flags ranked by expected dollars = dollars at stake x probability (1 - q, P(duplicate), or 1 for observed write-offs).')


def _usd(v):
    return '$' + f'{v:,.0f}' if v is not None else 'no dollar estimate'


def summarize(report, limit=5):
    """Direct answer for the shared answer contract: (one-paragraph value, ranked flag table)."""
    flags = report['flags']
    table = [{'rank': f['rank'], 'category': f['category'], 'subject': f['subject'], 'reason': f['reason'], 'direction': f['direction'],
              'dollars_at_stake': f['dollars_at_stake'], 'probability': f['probability'], 'expected_dollars': f['expected_dollars'],
              'q_value': f['q_value'], 'claims': f['claims'], 'follow_up': f['follow_up']} for f in flags[:report['parameters']['top']]]
    if not report['claims']:
        return 'No medical claims in scope for a claims review.', table
    parts = [f"{len(flags)} review flag(s) across {report['claims']:,} claims from {report['providers']} providers and {len(report['payers'])} payers"
             + (f" ({report['period'][0]} to {report['period'][1]})" if report.get('period') else '')
             + f"; expected dollars at stake {_usd(sum(f['expected_dollars'] or 0 for f in flags))}"]
    for f in flags[:limit]:
        parts.append(f"#{f['rank']} {f['subject']}: {f['reason']} — {_usd(f['dollars_at_stake'])} at stake"
                     + (f", probability {f['probability']:.0%}" if f['probability'] is not None and f['probability'] < 1 else ''))
    d = report.get('denials')
    if d:
        top = d['pareto'][0] if d['pareto'] else None
        parts.append(f"Denials: first-pass rate {d['first_pass_denial_rate']:.1%}, {d['denied_claims']} denied claims, "
                     f"{d['overturned']} overturned (${d['recovered']:,.0f} recovered), {d['written_off_claims']} written off (${d['written_off']:,.0f}), "
                     f"{d['pending_claims']} pending (${d['pending']:,.0f})"
                     + (f"; median {d['median_days_to_resolution']:.0f} days from denial to payment" if d['median_days_to_resolution'] is not None else '')
                     + (f"; top reason '{top['reason']}' ({top['share']:.0%} of denials)" if top else ''))
    u = report.get('underpayment')
    if u:
        parts.append(f"Underpayment: {u['flagged']} of {u['claims_compared']:,} paid claims below the range of comparable claims"
                     + (f" ({_usd(u['shortfall'])} short)" if u['flagged'] else ''))
    pr = report.get('provider_review')
    if pr is not None:
        parts.append(f"Provider peers: {pr['flags']} flag(s) from {pr['tests']} tests on {len(pr['units'])} providers (BH FDR {report['parameters']['fdr']})")
    dup = report.get('duplicates')
    if dup:
        parts.append(f"Duplicates: {dup['exact_pairs']} exact and {dup['near_pairs']} near pairs (expected {_usd(dup['expected_duplicate_dollars'])})")
    c = report.get('churn')
    if c:
        parts.append(f"Rework: {c['claims_with_rework']:,} claims needed {c['rework_touches']:,} touches; reversals {c['reversals'] or 'none'}, "
                     f"{_usd(c['taken_back'])} taken back")
    parts += report['notes'][:3]
    return '. '.join(x.rstrip('.') for x in parts) + '. Review flags are prompts to look, never findings of fraud.', table


def follow_up(report):
    """Questions that reproduce each family through the normal answer path (they compile to claims_review)."""
    return sorted({f['follow_up'] for f in report['flags'] if f.get('follow_up')})
