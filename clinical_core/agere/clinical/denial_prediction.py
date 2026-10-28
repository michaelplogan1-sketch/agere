"""First-pass denial review with explicit supervision and feature-availability clocks.

A received claim is not a received target. Only a unique first adjudication with
matching claim/patient identity, a supported final status and both event/receipt
clocks at or before the relevant origin supplies a label or CARC. Later labels
may evaluate earlier submissions, but may not train a model claimed at that origin.

Current payer headers, coverage and duplicate flags are not an archived 837 feature
snapshot. The existing empirical-Bayes/logistic model therefore provides descriptive
associations; even a supported retrospective time split is not qualification for
pre-submission prediction. No prospective performance is inferred from refit scores.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import math
import re

import numpy as np

from . import revenue_common as rc

VERSION = 'denial-prediction/2'
OP = 'denial_prediction'
CATEGORICAL = ('payer', 'product', 'claim_type', 'pos', 'network', 'billing_npi', 'dx3', 'cell', 'covered')
NUMERIC = ('lag30', 'late90', 'npi_missing', 'duplicate', 'replacement')
FIELDS = {'validation_months', 'score_days', 'prior_strength', 'l2', 'top', 'payer', 'product', 'min_level_claims'}
CARC_TEXT = {
    'CO-16': 'claim lacks information or has submission/billing errors', 'CO-18': 'exact duplicate claim/service',
    'CO-197': 'precertification/authorization absent', 'CO-50': 'not medically necessary (non-covered)',
    'CO-97': 'bundled into another service already adjudicated', 'PR-27': 'expenses incurred after coverage terminated',
    'CO-4': 'procedure code inconsistent with modifier / modifier missing', 'CO-22': 'may be covered by another payer (COB)',
    'CO-11': 'diagnosis inconsistent with procedure', 'CO-45': 'charge exceeds fee schedule / contracted amount',
    'CO-29': 'time limit for filing has expired', 'CO-15': 'authorization number missing or invalid',
    'CO-62': 'absent or exceeded pre-certification', 'CO-129': 'prior processing information incorrect'}
FIX = {'CO-16': 'complete required fields (rendering NPI, member ID, dx pointers) before release',
       'CO-18': 'check for a prior submission of the same service', 'CO-197': 'obtain or attach the prior authorization',
       'CO-50': 'attach medical-necessity documentation / confirm coverage policy', 'CO-97': 'review NCCI bundling edits',
       'PR-27': 'run a 270/271 eligibility check for the service date', 'CO-4': 'review modifiers',
       'CO-22': 'confirm coordination of benefits / primary payer', 'CO-11': 'review diagnosis-procedure linkage',
       'CO-29': 'submit within the payer timely-filing limit', 'CO-45': 'contractual: no action'}

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'DENIAL PREDICTION: which claims are likely to be denied on first pass and why - claim-level denial risk from header '
                  'fields (payer, product, POS, provider, diagnosis, coverage, filing lag), availability-audited retrospective comparison where supported, '
                  'with per-claim reason attribution (likely CARC and the fix).'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'validation_months': 6, 'score_days': 30, 'prior_strength': 50, 'l2': 1.0, 'top': 25, 'payer': '', 'product': '',
                           'min_level_claims': 1},
            'meaning': 'First-pass denial risk per payer claim: empirical-Bayes target-encoded header features + L2 logistic regression, '
                       'trained only on labels and current header versions available at the fit origin, compared after (AUROC, Brier, decile '
                       'calibration, payer-only baseline); retrospective refit associations, not qualified pre-submission predictions, with top '
                       'log-odds contributions and the most likely CARC per claim. Service codes excluded (payer feed returns lines only on '
                       'paid claims).'}
NATIVE = ('value', 'count')


def schema(obj, string, array):
    return obj({'validation_months': {'type': 'integer', 'minimum': 1, 'maximum': 24, 'description': 'Hold-out window length (months before as-of)'},
                'score_days': {'type': 'integer', 'minimum': 1, 'maximum': 365, 'description': 'Score claims received in the last N days'},
                'prior_strength': {'type': 'number', 'minimum': 1, 'maximum': 10000, 'description': 'Empirical-Bayes prior strength (claims)'},
                'l2': {'type': 'number', 'minimum': 0, 'maximum': 1000, 'description': 'L2 penalty'},
                'top': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'description': 'Highest-risk claims listed'},
                'payer': {'type': 'string', 'description': 'Restrict to one payer id (empty = all)'},
                'product': {'type': 'string', 'description': 'Restrict to one product line (empty = all)'},
                'min_level_claims': {'type': 'integer', 'minimum': 1, 'maximum': 1000, 'description': 'Levels with fewer claims pool to "other"'}})


def validate(p):
    if not isinstance(p, dict) or set(p) - FIELDS: raise ValueError('denial_prediction parameters: ' + ', '.join(sorted(FIELDS)))
    out = {'validation_months': p.get('validation_months', 6), 'score_days': p.get('score_days', 30), 'prior_strength': p.get('prior_strength', 50),
           'l2': p.get('l2', 1.0), 'top': p.get('top', 25), 'payer': str(p.get('payer') or ''), 'product': str(p.get('product') or ''),
           'min_level_claims': p.get('min_level_claims', 1)}
    for k, lo, hi in (('validation_months', 1, 24), ('score_days', 1, 365), ('top', 1, 500), ('min_level_claims', 1, 1000)):
        if type(out[k]) is not int or not lo <= out[k] <= hi: raise ValueError(f'{k}: integer {lo}..{hi}')
    for k, lo, hi in (('prior_strength', 1, 10000), ('l2', 0, 1000)):
        v = out[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi: raise ValueError(f'{k}: {lo}..{hi}')
        out[k] = float(v)
    return out


def _days(a, b):
    try:
        return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    except (TypeError, ValueError):
        return 0


def _clock(value):
    """Validate source dates without treating a missing/invalid clock as timely."""
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[T ].*)?', value): return None
    try:
        if len(value) > 10: datetime.fromisoformat(value.replace('Z', '+00:00'))
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError: return None


def _target(witnesses, pid, received, header_status, origin):
    if len(witnesses) != 1: return None, None, 'missing_first_adjudication' if not witnesses else 'ambiguous_first_adjudication'
    w = witnesses[0]
    if not w['record_id'] or w['patient_id'] != pid: return None, None, 'first_adjudication_identity_mismatch'
    event, arrival = _clock(w['adjudication_date']), _clock(w['available_date'])
    if not event or not arrival: return None, None, 'missing_or_invalid_label_clock'
    if event < received or arrival < event: return None, None, 'inconsistent_label_clock'
    if event > origin or arrival > origin: return None, None, 'label_not_available_at_origin'
    status = w['claim_status']
    if status not in ('paid', 'denied', 'denied_upheld'): return None, None, 'unresolved_first_status'
    if header_status and header_status != status: return None, None, 'first_status_conflict'
    return int(status in ('denied', 'denied_upheld')), w['carc'], 'known'


def load(db, patient_ids, p, as_of):
    if _clock(as_of) != as_of: raise ValueError('denial review requires an ISO as-of date')
    keep = set(patient_ids) if patient_ids is not None else None
    covered = set(db.execute('SELECT DISTINCT patient_id, month, payer_id FROM member_month'))
    first = defaultdict(list)
    for row in db.execute('SELECT payer_claim_id, record_id, patient_id, claim_status, carc, adjudication_date, available_date '
                          'FROM payer_adjudication WHERE is_first=1'):
        first[row[0]].append(dict(zip(('record_id', 'patient_id', 'claim_status', 'carc', 'adjudication_date', 'available_date'), row[1:])))
    cols = rc.columns(db, 'payer_claim')
    clocks = ', '.join(c if c in cols else 'NULL' for c in ('last_available_date', 'record_id'))
    sql = ('SELECT payer_claim_id, patient_id, payer_id, product, claim_type, place_of_service, network_status, billing_npi, principal_diagnosis, '
           'service_from, claim_received_date, first_status, rendering_npi_missing, duplicate_submission, claim_frequency_code, charge_cents, ' + clocks +
           " FROM payer_claim WHERE COALESCE(version_status,'') <> 'voided' AND claim_received_date IS NOT NULL")
    rows = []
    for (cid, pid, payer, product, ctype, pos, net, npi, dx, svc, recv, header_status, npi_missing, dup, freq, charge, header_arrival, header_record) in db.execute(sql):
        if keep is not None and pid not in keep: continue
        if p['payer'] and payer != p['payer']: continue
        if p['product'] and product != p['product']: continue
        received = _clock(recv)
        if not received or received > as_of: continue
        denied, carc, target_status = _target(first[cid], pid, received, header_status, as_of)
        lag = max(_days(svc or received, received), 0)
        rows.append({'claim_id': cid, 'patient_id': pid, 'received': received, 'received_source': recv,
                     'service_from': (svc or '')[:10], 'denied': denied, 'carc': carc, 'label_status': target_status,
                     'label_origin': as_of, 'label_witnesses': first[cid], 'header_first_status': header_status,
                     'header_available_source': header_arrival, 'header_available': _clock(header_arrival), 'header_record_id': header_record,
                     'payer': payer or 'unknown', 'product': product or 'unknown', 'claim_type': ctype or 'unknown', 'pos': pos or 'none',
                     'network': net or 'unknown', 'billing_npi': npi or 'none', 'dx3': (dx or 'none').replace('.', '')[:3],
                     'cell': f'{payer}|{ctype}|{pos or "-"}', 'covered': 'yes' if (pid, (svc or '')[:7], payer) in covered else 'no',
                     'lag30': min(lag / 30, 12.0), 'late90': float(lag > 90), 'npi_missing': float(bool(npi_missing)),
                     'duplicate': float(bool(dup)), 'replacement': float(str(freq or '1') in ('7', '8')), 'lag_days': lag,
                     'charge': (charge or 0) / 100})
    return sorted(rows, key=lambda r: (r['received'], str(r['claim_id'])))


def split_at_origin(rows, cutoff, as_of):
    """Keep target observation time distinct from the earlier model-fitting origin."""
    train, test = [], []
    excluded = Counter()
    candidates = sum(r['received'] < cutoff for r in rows)
    for r in rows:
        origin = cutoff if r['received'] < cutoff else as_of
        target, carc, reason = _target(r['label_witnesses'], r['patient_id'], r['received'], r['header_first_status'], origin)
        prefix = 'train' if r['received'] < cutoff else 'validation'
        if reason != 'known': excluded[prefix + ':' + reason] += 1; continue
        if not r['header_available'] or r['header_available'] > origin or r['header_available'] < r['received']:
            excluded[prefix + ':current_header_unavailable_at_origin'] += 1; continue
        (train if prefix == 'train' else test).append({**r, 'denied': target, 'carc': carc, 'label_origin': origin})
    return train, test, {'fit_origin': cutoff, 'evaluation_as_of': as_of, 'train_candidates': candidates,
                         'validation_candidates': len(rows) - candidates, 'exclusions': dict(sorted(excluded.items()))}


class Encoder:
    """Empirical-Bayes shrunk log-odds encoding per categorical feature (fit on rows with labels)."""

    def __init__(self, rows, m, min_n=1):
        self.p0 = (sum(r['denied'] for r in rows) + 0.5) / (len(rows) + 1)
        self.m, self.min_n = m, min_n
        self.stats = {f: defaultdict(lambda: [0, 0]) for f in CATEGORICAL}
        for r in rows:
            for f in CATEGORICAL:
                s = self.stats[f][r[f]]; s[0] += r['denied']; s[1] += 1

    def value(self, f, level):
        d, n = self.stats[f].get(level, (0, 0))
        if n < self.min_n: d, n = 0, 0
        return rc.logit((d + self.m * self.p0) / (n + self.m)) - rc.logit(self.p0)


def design(rows, enc):
    X = np.ones((len(rows), 1 + len(CATEGORICAL) + len(NUMERIC)))
    for i, r in enumerate(rows):
        for j, f in enumerate(CATEGORICAL):
            X[i, 1 + j] = enc.value(f, r[f])
        for j, f in enumerate(NUMERIC):
            X[i, 1 + len(CATEGORICAL) + j] = r[f]
    return X


def fit(rows, p, folds=5):
    """Out-of-fold encodings for training rows, then L2 logistic. Returns (beta, full encoder)."""
    enc = Encoder(rows, p['prior_strength'], p['min_level_claims'])
    X = np.ones((len(rows), 1 + len(CATEGORICAL) + len(NUMERIC)))
    parts = [rows[k::folds] for k in range(folds)]
    encs = [Encoder([r for j, part in enumerate(parts) if j != k for r in part], p['prior_strength'], p['min_level_claims']) for k in range(folds)]
    for i, r in enumerate(rows):
        e = encs[i % folds]
        for j, f in enumerate(CATEGORICAL): X[i, 1 + j] = e.value(f, r[f])
        for j, f in enumerate(NUMERIC): X[i, 1 + len(CATEGORICAL) + j] = r[f]
    y = np.array([r['denied'] for r in rows])
    return rc.logistic_fit(X, y, l2=p['l2']), enc


def deciles(y, s):
    order = np.argsort(s)
    out = []
    for k, idx in enumerate(np.array_split(order, 10)):
        if not len(idx): continue
        out.append({'decile': k + 1, 'claims': int(len(idx)), 'mean_predicted': round(float(np.mean(s[idx])), 4),
                    'observed_rate': round(float(np.mean(y[idx])), 4)})
    return out


def carc_model(rows):
    """CARC counts by (feature, level) and by payer among denied claims with a first-adjudication CARC."""
    by = defaultdict(Counter); payer = defaultdict(Counter); overall = Counter()
    for r in rows:
        if not r['denied'] or not r['carc']: continue
        overall[r['carc']] += 1; payer[r['payer']][r['carc']] += 1
        for f in CATEGORICAL: by[(f, r[f])][r['carc']] += 1
        for f in FLAGS:
            if r[f]: by[(f, 1)][r['carc']] += 1
    return by, payer, overall


FLAGS = ('late90', 'npi_missing', 'duplicate', 'replacement')


def level(r, feature):
    if feature == 'lag30': feature = 'late90'
    return feature, (r[feature] if feature in CATEGORICAL else 1)


def likely_carc(r, feature, cm, strength=20):
    by, payer, overall = cm
    base = payer.get(r['payer']) or overall
    tot_b = sum(base.values()) or 1
    cell = by.get(level(r, feature), Counter()); n = sum(cell.values())
    scores = {c: (cell.get(c, 0) + strength * base.get(c, 0) / tot_b) / (n + strength) for c in set(base) | set(cell)}
    if not scores: return None, None
    c = max(scores, key=scores.get)
    return c, round(scores[c], 3)


LABEL = {'payer': 'payer', 'product': 'product line', 'claim_type': 'claim type', 'pos': 'place of service', 'network': 'network status',
         'billing_npi': 'billing provider', 'dx3': 'principal diagnosis', 'cell': 'payer x claim type x POS', 'covered': 'coverage on file',
         'lag30': 'filing lag', 'late90': 'filed > 90 days after service', 'npi_missing': 'rendering NPI missing', 'duplicate': 'duplicate submission',
         'replacement': 'replacement claim'}


def run(db, patient_ids, p, as_of):
    rows = load(db, patient_ids, p, as_of)
    names = list(CATEGORICAL) + list(NUMERIC)
    cutoff = (date.fromisoformat(as_of) - timedelta(days=round(p['validation_months'] * 30.4375))).isoformat()
    train, test, support = split_at_origin(rows, cutoff, as_of)
    validation = {'cutoff': cutoff, 'train_claims': len(train), 'validation_claims': len(test), 'status': 'insufficient',
                  'availability': support, 'prospective_status': 'unqualified',
                  'feature_timing': 'Current payer headers, coverage and outcome-derived duplicate flags lack archived pre-submission provenance.'}
    if len(train) >= 50 and len(test) >= 20 and 0 < sum(r['denied'] for r in train) < len(train):
        beta, enc = fit(train, p)
        Xt = design(test, enc); st = rc.sigmoid(Xt @ beta); yt = np.array([r['denied'] for r in test])
        # payer-only baseline: shrunk payer rate from the training window
        base = np.array([rc.sigmoid(rc.logit(enc.p0) + enc.value('payer', r['payer'])) for r in test])
        validation.update(status='retrospective_only', auroc=rc.auroc(yt, st), auroc_payer_only=rc.auroc(yt, base),
                          brier=round(float(np.mean((st - yt) ** 2)), 5), observed_rate=round(float(yt.mean()), 4) if len(yt) else None,
                          mean_predicted=round(float(st.mean()), 4) if len(st) else None, calibration=deciles(yt, st))
        if validation['auroc'] is not None: validation['auroc'] = round(validation['auroc'], 4)
        if validation['auroc_payer_only'] is not None: validation['auroc_payer_only'] = round(validation['auroc_payer_only'], 4)
    labelled = [r for r in rows if r['label_status'] == 'known']
    fit_rows = [r for r in labelled if r['header_available'] and r['received'] <= r['header_available'] <= as_of]
    limitations = ['Current payer headers, member-month coverage and duplicate flags are not archived pre-submission features. Prospective denial prediction remains unqualified.',
                   'Later first-adjudication labels may evaluate earlier claims but cannot train a model at an earlier fit origin.',
                   'Refit scores are descriptive associations, not out-of-sample prediction performance.']
    report = {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'claims': len(rows),
              'labelled_claims': len(labelled), 'unknown_first_pass': len(rows) - len(labelled),
              'label_exclusions': dict(Counter(r['label_status'] for r in rows if r['label_status'] != 'known')),
              'denied_first_pass': sum(r['denied'] for r in labelled), 'refit_claims': len(fit_rows),
              'validation': validation, 'rows': [], 'coefficients': [], 'drivers': []}
    if len(fit_rows) < 20 or not 0 < sum(r['denied'] for r in fit_rows) < len(fit_rows):
        report.update(status='partial', assumptions=limitations + ['Too few known, available labels/header versions (or no class variation); observed rates only.'],
                      method=VERSION)
        return report
    beta, enc = fit(fit_rows, p)
    report['coefficients'] = [{'feature': LABEL[n], 'coefficient': round(float(b), 4)} for n, b in zip(names, beta[1:])]
    cm = carc_model(fit_rows)
    start = (date.fromisoformat(as_of) - timedelta(days=p['score_days'])).isoformat()
    available = [r for r in rows if r['header_available'] and r['received'] <= r['header_available'] <= as_of]
    recent = [r for r in available if r['received'] >= start]
    report['score_population'] = {'window_start': start, 'available_claims': len(available),
                                  'unavailable_header_claims': len(rows) - len(available)}
    X = design(recent, enc); s = rc.sigmoid(X @ beta)
    scored = []
    for r, x, prob in zip(recent, X, s):
        contrib = sorted(((float(beta[1 + j] * x[1 + j]), names[j]) for j in range(len(names))), reverse=True)
        top = [(c, n) for c, n in contrib if c > 0][:3]
        strongest = top[0][1] if top else 'payer'
        carc, carc_p = likely_carc(r, strongest, cm)
        scored.append({'claim_id': r['claim_id'], 'patient_id': r['patient_id'], 'received': r['received'], 'payer': r['payer'],
                       'product': r['product'], 'billing_npi': r['billing_npi'], 'denial_probability': round(float(prob), 4),
                       'drivers': [f"{LABEL[n]}={r[n] if n in CATEGORICAL else round(r[n], 2)} (+{c:.2f} log-odds)" for c, n in top],
                       'likely_reason': carc, 'reason_text': CARC_TEXT.get(carc, ''), 'reason_probability': carc_p,
                       'suggested_fix': FIX.get(carc, 'review before release'),
                       'actual_first_pass': 'unknown' if r['denied'] is None else ('denied' if r['denied'] else 'paid'),
                       'actual_carc': r['carc'], 'label_status': r['label_status'], 'charge': r['charge'],
                       'score_use': 'retrospective_association_only'})
    scored.sort(key=lambda x: -x['denial_probability'])
    exp = sum(x['denial_probability'] for x in scored)
    report['rows'] = scored[:p['top']]
    report['scored'] = {'window_start': start, 'claims': len(scored), 'expected_denials': round(exp, 1),
                        'expected_denied_charges': round(sum(x['denial_probability'] * x['charge'] for x in scored), 2),
                        'observed_denials': sum(1 for x in scored if x['actual_first_pass'] == 'denied'),
                        'unknown_outcomes': sum(x['actual_first_pass'] == 'unknown' for x in scored)}
    # population drivers: levels with the highest shrunk log-odds (>= 30 claims)
    drv = []
    for f in ('payer', 'product', 'pos', 'billing_npi', 'dx3', 'cell'):
        for level, (d, n) in enc.stats[f].items():
            if n >= 30: drv.append({'feature': LABEL[f], 'level': level, 'claims': n, 'denial_rate': round(d / n, 4),
                                    'shrunk_log_odds_vs_base': round(enc.value(f, level), 3)})
    report['drivers'] = sorted(drv, key=lambda x: -x['shrunk_log_odds_vs_base'])[:15]
    report['status'] = 'partial'
    report['assumptions'] = limitations + [
        'Received payer feed; label = first adjudication denied (first-pass), regardless of later appeal or resubmission.',
        'Service codes (CPT/HCPCS) excluded: the payer feed returns service lines only on paid claims, so they would leak the label; '
        'add the provider-side 837 lines to use them.',
        f"Retrospective split at {cutoff}: fit labels and current header versions must be available by that origin; later evaluation labels may arrive through {as_of}.",
        f"Empirical-Bayes prior strength {p['prior_strength']:g} claims, out-of-fold (5-fold) encoding, L2 {p['l2']:g}.",
        'Coverage feature = a member_month row for that payer in the service month (eligibility responses themselves not used).',
        'Reason = most likely first-pass CARC for the claim\'s strongest feature level, shrunk toward the payer\'s CARC mix; a prompt for review, not a payer decision.']
    report['method'] = (f'{VERSION}: empirical-Bayes target-encoded header features + L2 logistic regression; availability-audited retrospective comparison '
                        '(AUROC, Brier, decile calibration, payer-only baseline); per-claim log-odds attribution and CARC reason.')
    return report


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    with rc.connect(path) as db:
        return run(db, patient_ids, p, str(as_of)[:10])


def headline(r):
    v = r['validation']; s = r.get('scored') or {}
    known = r['labelled_claims']
    rate = f"{r['denied_first_pass'] / known:.1%}" if known else 'rate unknown'
    parts = [f"{r['denied_first_pass']:,} of {known:,} known first outcomes denied ({rate}); {r['unknown_first_pass']:,} outcomes unknown"]
    if v.get('status') == 'retrospective_only':
        parts.append(f"retrospective AUROC {v['auroc']} vs payer-only {v['auroc_payer_only']} (fit origin {v['cutoff']}, {v['validation_claims']:,} claims)")
    elif v.get('status') == 'insufficient':
        parts.append(f"insufficient time-split support: {v['train_claims']} training / {v['validation_claims']} evaluation claims")
    if s:
        parts.append(f"last {r['parameters']['score_days']} days: {s['claims']:,} claims with descriptive refit scores")
    parts.append('pre-submission prediction remains unqualified')
    return 'Denial prediction: ' + '; '.join(parts)


def answer(report, plan, question):
    table = report['rows'] or report.get('drivers') or []
    return rc.answer(report, plan, value=headline(report) + '.', table=table,
                     definitions=[{'term': 'first-pass denial', 'basis': 'first adjudication of the claim denied (835 claim status 4 / CARC on first remit)'},
                                  {'term': 'CARC', 'basis': 'X12 Claim Adjustment Reason Code (group code CO/PR + reason)'}],
                     number=None, overall_key='denial_prediction',
                     patient_ids=sorted({x['patient_id'] for x in report['rows']}), membership_role='evaluated')


# ---- free local-compiler route ----------------------------------------------------------------------------------------
PREDICT = (r'\bpredict\w*\b|\bforecast\w*\b|\blikely (?:to be|to get) denied\b|\bat risk of (?:being )?denied\b|\bat risk of denial\b|'
           r'\bdenial (?:risk|probability|likelihood|score|propensity)\b|\brisk of denial\b|\bprobability (?:of|that) (?:a |the |each )?(?:claim|denial)|'
           r'\bwill (?:be|get) denied\b|\bgoing to (?:be|get) denied\b|\bwhich claims (?:will|are likely|might|may)\b|\bscrub\w*\b.*\bbefore\b|'
           r'\bprobability\b.{0,40}\bdenied\b|\bpre-?submission\b|\bbefore (?:we )?(?:submit|send|release)\w*\b')
DENIAL = r'\bdenial\w*\b|\bdenied\b|\bdeny\b|\brejection\w*\b|\brejected\b'


def route(question, profile, as_of):
    from .engine_suite import plan
    q = rc.low(question)
    if not re.search(DENIAL, q) or not re.search(PREDICT, q): return None
    if rc.knowledge_only(q): return None
    if re.search(r'\bprior[- ]?auth\w*\b|\bauthorization\b|\bgold[- ]?card', q): return None  # auth questions -> prior_auth_rules
    params = {}
    m = re.search(r'\blast (\d{1,3}) days\b', q)
    if m: params['score_days'] = max(1, min(365, int(m.group(1))))
    elif re.search(r'\blast week\b|\bthis week\b', q): params['score_days'] = 7
    from .mlr import LINES
    for k, rx in LINES:
        if re.search(rx, q) and k != 'medigap': params['product'] = k; break
    m = re.search(r'\b(wh-payer-[a-z0-9]+)\b', q)
    if m: params['payer'] = m.group(1).upper()
    m = re.search(r'\btop (\d{1,3})\b', q)
    if m: params['top'] = max(1, min(500, int(m.group(1))))
    return plan(OP, params, 'First-pass denial review with availability-audited retrospective comparison', question)


TEMPLATES = ['Which claims are likely to be denied?', 'Predict which of our recent claims will get denied and why',
             'Denial risk score for claims submitted in the last 14 days', 'Give me the probability that each of our claims is denied on first pass',
             'Which Medicaid claims are at risk of denial before we submit them?', 'Score pending claims for denial likelihood with the reason',
             'Forecast denials for this week\'s claims', 'Top 20 claims most likely to be denied and what to fix']
