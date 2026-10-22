"""Real patient change vs feed outage: a joint patient-state / source-observation tensor model (research program prototype 1).

Monthly record counts form a tensor Y[g, s, t] (patient group x source stream x month; groups = each patient's home practice,
payer line of business, or the sending source system). The joint model keeps the two latent states of
research/agere/RESEARCH_PROGRAM.md separate:

    log E[Y[g, s, t]] = c[g, s] + a[g, t] + b[s, t]
      c[g, s]  stationary group x stream capture level (full-rank interaction)
      a[g, t]  PATIENT-ACTIVITY state of group g in month t, shared by every stream that group's patients appear in
      b[s, t]  SOURCE-CAPTURE state of stream s in month t, shared by every group the stream delivers for

fitted as a Poisson log-linear tensor model by exact block-coordinate (iterative proportional fitting) updates on the
baseline months, with a quasi-Poisson dispersion phi (Pearson chi2 / df). Each recent cell whose standardized residual
against the baseline projection is large is attributed by comparing, on that month's whole slice, the explanations
    none              no change (baseline a, b)
    source_outage     b[s, t] shifts: the stream changed for every group (feed outage / capture change)
    local_feed        only cell (g, s) shifts: one group's interface for one stream (local feed problem)
    patient_change    a[g, t] shifts: every stream of group g moved together (real change in that group's patients)
with one free shift each, quasi-likelihood / phi and BIC weights normalized to probabilities; a top probability under
0.6 is reported as unresolved. Source-level patterns can support an outage hypothesis; they never prove an individual
patient's status (no per-patient claims are made). Counts are received records by event month, which is not care
volume confirmed independently.
"""
from collections import defaultdict
import math
import re
import sqlite3
from pathlib import Path

VERSION = 'change-attribution/1'
OP = 'change_attribution'
GROUP_BY = ('home_practice', 'payer', 'source_system')
CORE_STREAMS = ('encounters', 'labs', 'payer_medical_claims', 'payer_pharmacy_claims', 'pharmacy_dispenses', 'notes', 'appointments',
                'vitals', 'medical_claims', 'adt_messages', 'telephone_messages', 'procedures', 'imaging_reports')
EXPLANATIONS = ('none', 'source_outage', 'local_feed', 'patient_change')
LABEL = {'source_outage': 'feed outage / source capture change (stream-wide)', 'local_feed': 'local feed problem (one group, one stream)',
         'patient_change': 'real change in patient activity (all streams of the group)', 'none': 'within normal variation',
         'unresolved': 'unresolved (explanations not separable from these counts)'}
STREAM_ALIASES = [('labs', r'\blab(?:oratory)?s?\b|\blab (?:volume|results?)\b'), ('encounters', r'\bencounters?\b|\bvisits?\b'),
                  ('payer_medical_claims', r'\b(?:medical )?claims?\b'), ('pharmacy_dispenses', r'\bpharmacy\b|\bdispens\w*\b|\bfills?\b'),
                  ('notes', r'\bnotes?\b|\bdocumentation\b'), ('appointments', r'\bappointments?\b'), ('vitals', r'\bvitals?\b'),
                  ('adt_messages', r'\badt\b'), ('payer_pharmacy_claims', r'\bpharmacy claims?\b')]

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'REAL CHANGE OR FEED PROBLEM: is a drop or spike in lab, claims, visit or pharmacy volume a true change in patients or a '
                  'feed outage / capture change; joint patient-state x source-observation tensor model per practice, payer or source x '
                  'stream x month.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'streams': [], 'focus_streams': ['labs'], 'group_by': 'home_practice|payer|source_system', 'group': '',
                           'window_months': 3, 'baseline_months': 18, 'z_threshold': 3.0, 'min_ratio': 1.5},
            'meaning': 'Poisson log-linear tensor model log E[Y[group, stream, month]] = c[g,s] + a[g,t] (patient-activity state shared '
                       'across streams) + b[s,t] (source-capture state shared across groups), fitted on baseline months with quasi-Poisson '
                       'dispersion; each recent cell deviating from its projection is attributed to source_outage (stream-wide shift), '
                       'local_feed (one group x stream), patient_change (all streams of a group) or none, by BIC weights over one-parameter '
                       'shifts of that month slice; top probability < 0.6 = unresolved. Use for real-change-vs-feed-outage and data-quality '
                       'questions about volume drops or spikes; control charts stay with monitor.'}
NATIVE = ('value',)


def schema(obj, string, array):
    return obj({'streams': array(string(None, 'received record stream'), 'Streams forming the tensor; empty = core streams present'),
                'focus_streams': array(string(None, 'received record stream'), 'Streams whose anomalies to report; empty = all'),
                'group_by': string(GROUP_BY, 'home_practice: patients by the practice with most of their encounters; payer: line of business; '
                                             'source_system: the sending organization'),
                'group': string(None, 'One group id to report on, or empty'),
                'window_months': {'type': 'integer', 'minimum': 1, 'maximum': 6},
                'baseline_months': {'type': 'integer', 'minimum': 6, 'maximum': 36},
                'z_threshold': {'type': 'number', 'minimum': 1.5, 'maximum': 10}, 'min_ratio': {'type': 'number', 'minimum': 1.05, 'maximum': 10}})


def validate(p):
    allowed = {'streams', 'focus_streams', 'group_by', 'group', 'window_months', 'baseline_months', 'z_threshold', 'min_ratio'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('change_attribution parameters: ' + ', '.join(sorted(allowed)))
    out = {'streams': list(dict.fromkeys(p.get('streams') or [])), 'focus_streams': list(dict.fromkeys(p.get('focus_streams') or [])),
           'group_by': p.get('group_by', 'home_practice'), 'group': p.get('group') or '', 'window_months': p.get('window_months', 3),
           'baseline_months': p.get('baseline_months', 18), 'z_threshold': p.get('z_threshold', 3.0), 'min_ratio': p.get('min_ratio', 1.5)}
    if any(not isinstance(s, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,60}', s) for s in out['streams'] + out['focus_streams']):
        raise ValueError('streams: stream names')
    if out['group_by'] not in GROUP_BY: raise ValueError(f'group_by: {GROUP_BY}')
    if not isinstance(out['group'], str): raise ValueError('group: string')
    if type(out['window_months']) is not int or not 1 <= out['window_months'] <= 6: raise ValueError('window_months 1..6')
    if type(out['baseline_months']) is not int or not 6 <= out['baseline_months'] <= 36: raise ValueError('baseline_months 6..36')
    for k, lo, hi in (('z_threshold', 1.5, 10), ('min_ratio', 1.05, 10)):
        if type(out[k]) not in (int, float) or not lo <= out[k] <= hi: raise ValueError(f'{k} {lo}..{hi}')
        out[k] = float(out[k])
    return out


# ---- the tensor model -------------------------------------------------------------------------------------------------
def fit(Y, mask=None, iters=200, tol=1e-8):
    """Poisson log-linear fit of log mu = c[g,s] + a[g,t] + b[s,t] on cells where mask is true (exact block updates).
    Returns c, a, b, mu. Cells with zero baseline volume for a (g, s) pair stay at mu = 0 (structural zero)."""
    import numpy as np
    Y = np.asarray(Y, float)
    G, S, T = Y.shape
    M = np.ones_like(Y, bool) if mask is None else np.asarray(mask, bool)
    Ym = np.where(M, Y, 0.0)
    eps = 1e-12
    c = np.log((Ym.sum(2) + eps) / np.maximum(M.sum(2), 1))
    a, b = np.zeros((G, T)), np.zeros((S, T))
    prev = None
    for _ in range(iters):
        mu = np.exp(c[:, :, None] + a[:, None, :] + b[None, :, :]) * M
        c = c + np.log((Ym.sum(2) + eps) / (mu.sum(2) + eps))
        mu = np.exp(c[:, :, None] + a[:, None, :] + b[None, :, :]) * M
        a = a + np.log((Ym.sum(1) + eps) / (mu.sum(1) + eps)) * (M.sum(1) > 0)
        mu = np.exp(c[:, :, None] + a[:, None, :] + b[None, :, :]) * M
        b = b + np.log((Ym.sum(0) + eps) / (mu.sum(0) + eps)) * (M.sum(0) > 0)
        a -= a.mean(1, keepdims=True)  # identifiability: time effects carried by b's level, a centred per group
        ll = float(np.sum(Ym * np.log(mu + eps) - mu))
        if prev is not None and abs(ll - prev) < tol * (1 + abs(ll)): break
        prev = ll
    mu = np.exp(c[:, :, None] + a[:, None, :] + b[None, :, :])
    mu[(Ym.sum(2) == 0)] = 0.0
    return c, a, b, mu


def dispersion(Y, mu, mask):
    import numpy as np
    ok = mask & (mu > 0.5)
    G, S, T = Y.shape
    df = max(int(ok.sum()) - (G * S + G * T + S * T), 1)
    return max(1.0, float(np.sum((Y[ok] - mu[ok]) ** 2 / mu[ok]) / df))


def _ll(y, mu):
    import numpy as np
    mu = np.maximum(mu, 1e-9)
    return float(np.sum(y * np.log(mu) - mu))


def attribute_slice(y, mu0, g, s, phi):
    """Explanations of one month slice y[G, S] given the no-change projection mu0[G, S], for the flagged cell (g, s).
    Returns {explanation: {'probability', 'shift_ratio', 'bic'}}."""
    import numpy as np
    live = mu0 > 0
    base = _ll(y[live], mu0[live]) / phi

    def shifted(sel):
        r = (y[sel].sum() + 0.5) / (mu0[sel].sum() + 0.5)
        mu = mu0.copy(); mu[sel] *= r
        return _ll(y[live], mu[live]) / phi, r
    stream, group, cell = np.zeros_like(live), np.zeros_like(live), np.zeros_like(live)
    stream[:, s] = True; group[g, :] = True; cell[g, s] = True
    stream &= live; group &= live; cell &= live
    k = math.log(max(int(live.sum()), 2))
    fits = {'none': (base, 1.0, 0)}
    fits['source_outage'] = (*shifted(stream), 1)
    fits['local_feed'] = (*shifted(cell), 1)
    fits['patient_change'] = (*shifted(group), 1)
    if stream.sum() <= 1: fits.pop('source_outage')  # a stream seen in one group cannot be told apart from a local feed
    if group.sum() <= 1: fits.pop('patient_change')  # a group with one stream: activity vs capture are not separable
    bic = {h: -2 * ll + n * k for h, (ll, _, n) in fits.items()}
    lo = min(bic.values())
    w = {h: math.exp(-(v - lo) / 2) for h, v in bic.items()}
    tot = sum(w.values())
    return {h: {'probability': w[h] / tot, 'shift_ratio': round(float(fits[h][1]), 3), 'bic': round(bic[h], 2)} for h in fits}


def attribute_tensor(Y, groups, streams, months, *, window=3, z_threshold=3.0, min_ratio=1.5, focus_streams=(), group=None,
                     min_expected=5.0):
    """Fit on months[:-window], project the window, attribute every deviating cell. -> report dict (no warehouse access)."""
    import numpy as np
    Y = np.asarray(Y, float)
    G, S, T = Y.shape
    if T < window + 6: raise ValueError('need at least six baseline months before the window')
    base_mask = np.zeros_like(Y, bool); base_mask[:, :, :T - window] = True
    c, a, b, mu = fit(Y, base_mask)
    phi = dispersion(Y, mu, base_mask)
    recent = slice(max(0, T - window - 6), T - window)  # projection: mean of the last six baseline months' states
    a0, b0 = a[:, recent].mean(1), b[:, recent].mean(1)
    proj = np.exp(c + a0[:, None] + b0[None, :])
    proj[Y[:, :, :T - window].sum(2) == 0] = 0.0
    anomalies = []
    for t in range(T - window, T):
        y = Y[:, :, t]
        z = (y - proj) / np.sqrt(phi * np.maximum(proj, 1e-9))
        for gi in range(G):
            for si in range(S):
                if proj[gi, si] < min_expected: continue
                if focus_streams and streams[si] not in focus_streams: continue
                if group and groups[gi] != group: continue
                ratio = (y[gi, si] + 0.5) / (proj[gi, si] + 0.5)
                if abs(z[gi, si]) < z_threshold or max(ratio, 1 / ratio) < min_ratio: continue
                post = attribute_slice(y, proj, gi, si, phi)
                best = max(post, key=lambda h: post[h]['probability'])
                verdict = best if post[best]['probability'] >= 0.6 else 'unresolved'
                others_g = [round(float((y[gi, j] + .5) / (proj[gi, j] + .5)), 2) for j in range(S) if j != si and proj[gi, j] >= min_expected]
                others_s = [round(float((y[i, si] + .5) / (proj[i, si] + .5)), 2) for i in range(G) if i != gi and proj[i, si] >= min_expected]
                anomalies.append({'month': months[t], 'group': groups[gi], 'stream': streams[si], 'observed': int(y[gi, si]),
                                  'expected': round(float(proj[gi, si]), 1), 'ratio': round(float(ratio), 3), 'z': round(float(z[gi, si]), 2),
                                  'direction': 'drop' if ratio < 1 else 'spike', 'attribution': verdict, 'label': LABEL[verdict],
                                  'probabilities': {h: round(v['probability'], 4) for h, v in post.items()},
                                  'shift_ratios': {h: v['shift_ratio'] for h, v in post.items() if h != 'none'},
                                  'evidence': {'same_stream_other_groups_median_ratio': float(np.median(others_s)) if others_s else None,
                                               'same_group_other_streams_median_ratio': float(np.median(others_g)) if others_g else None,
                                               'other_groups_evaluated': len(others_s), 'other_streams_evaluated': len(others_g)}})
    anomalies.sort(key=lambda r: (-abs(r['z']), r['month'], r['group'], r['stream']))
    counts = defaultdict(int)
    for r in anomalies: counts[r['attribution']] += 1
    return {'groups': list(groups), 'streams': list(streams), 'months': list(months), 'window_months': months[T - window:],
            'baseline_months': [months[0], months[T - window - 1]], 'dispersion_phi': round(phi, 3), 'anomalies': anomalies,
            'attribution_counts': dict(counts),
            'patient_activity_state': {g: [round(float(v), 4) for v in a[i, :T - window]] for i, g in enumerate(groups)},
            'source_capture_state': {s: [round(float(v), 4) for v in b[j, :T - window]] for j, s in enumerate(streams)}}


# ---- warehouse ----------------------------------------------------------------------------------------------------------
def _shift(month, k):
    y, m = int(month[:4]), int(month[5:7]) + k
    y += (m - 1) // 12; m = (m - 1) % 12 + 1
    return f'{y:04d}-{m:02d}'


def months_for(as_of, window, baseline):
    import calendar
    last = as_of[:7]
    if as_of[:10] < f'{last}-{calendar.monthrange(int(last[:4]), int(last[5:7]))[1]:02d}': last = _shift(last, -1)  # complete months only
    return [_shift(last, -k) for k in range(window + baseline - 1, -1, -1)]


def _group_map(db, group_by, keep):
    if group_by == 'home_practice':
        best = {}
        for pid, org, n in db.execute("SELECT patient_id, organization_id, count(*) FROM record WHERE stream IN ('encounters', 'appointments') "
                                      "AND organization_id LIKE '%PRACTICE%' GROUP BY 1, 2"):
            if pid not in best or n > best[pid][1]: best[pid] = (org, n)
        return {pid: v[0] for pid, v in best.items()}
    if group_by == 'payer':
        out = {}
        for pid, lob in db.execute('SELECT patient_id, line_of_business FROM member_month ORDER BY month'):
            out[pid] = lob or 'unknown'
        return out
    return None  # source_system: the record's own organization_id


def attribute(db, as_of, p, keep=None):
    """Warehouse entry point (reused by monitor): tensor from record counts, then attribute_tensor."""
    import numpy as np
    months = months_for(str(as_of)[:10], p['window_months'], p['baseline_months'])
    have = {r[0] for r in db.execute("SELECT DISTINCT stream FROM record WHERE event_date >= ?", (f'{months[0]}-01',))}
    streams = [s for s in (p['streams'] or CORE_STREAMS) if s in have] or sorted(have)[:12]
    for s in p['focus_streams']:
        if s in have and s not in streams: streams.append(s)
    gmap = _group_map(db, p['group_by'], keep)
    counts = defaultdict(float)
    q = ("SELECT patient_id, organization_id, stream, substr(event_date, 1, 7), count(*) FROM record WHERE event_date >= ? AND event_date < ? "
         f"AND stream IN ({','.join('?' * len(streams))}) GROUP BY 1, 2, 3, 4")
    for pid, org, st, m, n in db.execute(q, (f'{months[0]}-01', f'{_shift(months[-1], 1)}-01', *streams)):
        if keep is not None and pid not in keep: continue
        g = (org or 'unknown') if gmap is None else gmap.get(pid, 'no_home_practice' if p['group_by'] == 'home_practice' else 'unknown')
        counts[(g, st, m)] += n
    vol = defaultdict(float)
    for (g, st, m), n in counts.items(): vol[g] += n
    groups = sorted(g for g, v in vol.items() if v >= 50 * len(months) / 12)  # tiny groups carry no separable signal
    mi = {m: i for i, m in enumerate(months)}; gi = {g: i for i, g in enumerate(groups)}; si = {s: i for i, s in enumerate(streams)}
    Y = np.zeros((len(groups), len(streams), len(months)))
    for (g, st, m), n in counts.items():
        if g in gi and m in mi: Y[gi[g], si[st], mi[m]] = n
    if not groups: raise ValueError('No group has enough received records for the tensor model')
    rep = attribute_tensor(Y, groups, streams, months, window=p['window_months'], z_threshold=p['z_threshold'], min_ratio=p['min_ratio'],
                           focus_streams=p['focus_streams'], group=p['group'] or None)
    rep['group_by'] = p['group_by']
    rep['records_in_tensor'] = int(Y.sum())
    return rep


def execute(path, patient_ids, params, as_of):
    from . import semantic_layer
    p = validate(params)
    target = semantic_layer.ensure(path) if (Path(path)/'run.json').exists() else Path(path)/'semantic.sqlite3'
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        rep = attribute(db, str(as_of)[:10], p, set(patient_ids) if patient_ids is not None else None)
    finally:
        db.close()
    return {'schema': VERSION, 'as_of': str(as_of)[:10], 'parameters': p, **rep, 'method': METHOD, 'assumptions': ASSUMPTIONS}


METHOD = ('Joint patient-state / source-observation tensor model: log E[records(group, stream, month)] = c[g,s] + a[g,t] + b[s,t], '
          'a = patient-activity state shared across a group\'s streams, b = source-capture state shared across groups; Poisson '
          'log-linear fit (iterative proportional fitting) on baseline months, quasi-Poisson dispersion; window cells projected from '
          'the last six baseline months; cells with |z| >= threshold and ratio beyond min_ratio attributed by BIC weights over '
          'no change / stream-wide shift / single-cell shift / group-wide shift of that month slice.')
ASSUMPTIONS = ['Synthetic warehouse; counts are received records by event month, not independently confirmed care volume.',
               'A stream-wide shift is evidence of a capture change but does not prove an individual patient\'s status.',
               'Late-arriving records in the last months look like drops; the window uses complete months only.',
               'Groups with too few records are left out; a top attribution probability below 0.6 is reported as unresolved.']


def headline(r):
    an = r['anomalies']
    if not an:
        return (f"No volume change beyond normal variation in {', '.join(r['window_months'])} across {len(r['groups'])} groups x "
                f"{len(r['streams'])} streams (dispersion {r['dispersion_phi']})")
    c = r['attribution_counts']
    parts = ', '.join(f'{v} {LABEL[k]}' for k, v in sorted(c.items(), key=lambda kv: -kv[1]))
    top = an[0]
    return (f"{len(an)} volume changes in {r['window_months'][0]} to {r['window_months'][-1]}: {parts}. Largest: {top['stream']} for "
            f"{top['group']} in {top['month']} {top['direction']} to {top['ratio']:.0%} of expected ({top['observed']} vs {top['expected']:.0f}) -> "
            f"{top['label']} (p={max(top['probabilities'].values()):.2f})")


def answer(report, plan, question):
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['anomalies'],
            'count': len(report['anomalies']), 'patient_ids': [], 'value': headline(report) + '.', 'method': report['method'],
            'definitions': [{'term': 'feed outage vs real change', 'basis': 'Stream-wide shift (source-capture state) vs group-wide shift '
                                                                              'across streams (patient-activity state) in a joint tensor model'}],
            'limitations': report['assumptions'], 'membership_role': 'evaluated',
            'overall': {'change_attribution': {'counts': report['attribution_counts'], 'window': report['window_months'],
                                               'dispersion_phi': report['dispersion_phi']}}}


def annotate_monitor(report, path, as_of, keep=None):
    """Consumer hook for monitor: adds 'change_attribution' (real change vs outage) for streams with feed findings. Never raises."""
    try:
        feeds = report.get('feeds') or []
        if not feeds: return report
        from . import semantic_layer
        target = semantic_layer.ensure(path) if (Path(path)/'run.json').exists() else Path(path)/'semantic.sqlite3'
        flagged = sorted({f['stream'] for f in feeds})
        p = validate({'streams': list(dict.fromkeys([*flagged[:10], 'encounters', 'labs', 'payer_medical_claims'])), 'focus_streams': flagged[:10], 'window_months': 3})
        db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
        try:
            rep = attribute(db, str(as_of)[:10], p, keep)
        finally:
            db.close()
        report['change_attribution'] = {'schema': VERSION, 'window_months': rep['window_months'], 'counts': rep['attribution_counts'],
                                        'anomalies': rep['anomalies'][:25], 'headline': headline(rep), 'method': METHOD}
    except Exception as exc:  # noqa: BLE001 - an annotation never breaks the monitor answer
        report['change_attribution'] = {'schema': VERSION, 'status': 'not_computed', 'reason': str(exc)[:200]}
    return report


# ---- free local-compiler route ----------------------------------------------------------------------------------------
REAL = (r'\breal (?:change|drop|decline|increase|spike|shift)\b|\b(?:is|was) (?:the|this|that) (?:drop|decline|dip|fall|spike|jump|increase|change)\b[^.?]{0,60}\breal\b|'
        r'\breal or (?:a |an )?(?:data|feed|reporting|interface|capture)\b|\b(?:genuine|true|actual|real) (?:patient )?(?:change|drop|decline|spike)s?\b')
ARTIFACT = (r'\b(?:feed|interface|outage|data (?:issue|problem|artifact|glitch|quality)|capture|reporting (?:issue|problem|change|lag)|'
            r'missing data|data gap)s?\b')
CHANGE = r'\b(?:drop\w*|declin\w*|dip\w*|fell|fall\w*|decreas\w*|spik\w*|jump\w*|surg\w*|increas\w*|chang\w*|shift\w*|down|up)\b'


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if re.match(r'(?:what (?:is|are)|explain|define)\b', low) and not re.search(r'\breal\b', low): return None
    if re.match(r'how (?:do|should|can) (?:we|i|you) (?:interpret|think about|tell)\b', low): return None  # general guidance stays knowledge / monitor
    real = re.search(REAL, low)
    artifact = re.search(ARTIFACT, low)
    because = re.search(r'\bbecause of (?:a |an |the )?(?:feed|outage|interface|data)|\bdue to (?:a |an |the )?(?:feed|outage|interface|data)|'
                        r'\b(?:outage|feed problem|data issue) or (?:a )?real\b|\breal (?:patient )?change or\b', low)
    if not ((real and artifact) or because or (real and re.search(CHANGE, low))): return None
    if not re.search(CHANGE, low) and not because: return None
    params = {}
    focus = [s for s, rx in STREAM_ALIASES if re.search(rx, low)]
    if 'payer_pharmacy_claims' in focus: focus = [s for s in focus if s not in ('payer_medical_claims', 'pharmacy_dispenses')]
    if focus: params['focus_streams'] = focus
    if re.search(r'\bby payer\b|\bline of business\b|\bpayers?\b', low): params['group_by'] = 'payer'
    elif re.search(r'\bsource systems?\b|\bsending (?:system|organi[sz]ation)s?\b|\binterfaces?\b', low): params['group_by'] = 'source_system'
    org = re.search(r'\b(wh[n]?-[a-z]+-[a-z0-9-]+)\b', low)
    if org: params['group'] = org.group(1).upper()
    m = re.search(r'\blast (\d) months?\b|\bpast (\d) months?\b', low)
    if m: params['window_months'] = min(6, max(1, int(m.group(1) or m.group(2))))
    return plan(OP, params, 'Joint patient-state / source-observation tensor model: real change vs feed outage', question,
                [f"streams {','.join(focus) or 'all'}"])


TEMPLATES = ['Is the drop in {feed} volume a real change or a data problem?', 'did our {feed} numbers fall because of an outage',
             'real change or feed issue: claims volume this quarter', 'Was the spike in encounters real or a reporting change?',
             'is the decline in pharmacy fills genuine or an interface problem', 'tell me whether the lab dip is real or missing data',
             'real patient change or capture change in visit volume by practice', 'Is the change in notes a true decline or a feed gap?']
