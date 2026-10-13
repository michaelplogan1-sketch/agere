"""Early signal: matched filtering for faint, known-shaped precursors of an acute event (gravitational-wave style).

Answers "which patients look like they're heading toward an admission / an ED visit", "who has the pre-admission
signature", "early warning signs of decompensation". It differs from trajectory (one lab's own trend), survival
(population time-to-event curves) and outreach (a capacity-limited value ranking): it learns the multichannel SHAPE
of the months before an acute event from history and searches each current patient's recent months for it.

Data: a per-patient monthly panel of channels from the semantic layer - ED, urgent-care and hospital-admission counts,
primary-care and telehealth visit days, telephone messages, missed appointments, pharmacy dispenses, and weight,
systolic BP, potassium, creatinine and eGFR as deviations from the patient's own earlier median.

Whitening: counts are log1p-transformed; each channel-month is standardised by the mean and SD of reference patients
(no indexable event up to the training cutoff), with an SD floor (trajectory.NOISE floors for measurements), and
clipped at +/-4 (gating: one loud month cannot dominate). Missing measurements and structurally absent channel-months
(network total zero, the acute-data gap, the one-month ED spike) are masked: they carry zero weight everywhere.

Template: the masked mean of the whitened pre-event window (window_months before each training index event; first
event per patient, index month on or before the cutoff, window inside the panel), shrunk per cell by the positive-part
James-Stein factor max(0, 1 - 1/t^2), t = mean x sqrt(n).

Detection: for each lead j = 1..horizon_months the template is aligned so its end falls j months after the scoring
month and only the already-observed part is used; rho_j = sum(m h x) / sqrt(sum(m h^2)) (normalised matched filter
on whitened data); SNR = max_j rho_j. The threshold and each patient's false-alarm probability come from an empirical
background: the same statistic on reference (non-event) patients at earlier, time-shifted scoring months.
FAP = (1 + #background >= SNR) / (1 + N).

Honest evaluation: templates, whitening reference and background use only data and labels up to a training cutoff;
detection is scored on later months (label = an event in the next horizon_months), against a simple baseline
(acute visits in the last 3 months), with Wilson intervals, AUC and lead time. Reported whether or not it wins.
"""
from collections import defaultdict
from datetime import date
from pathlib import Path
import calendar, json, math, sqlite3

import numpy as np
from scipy.stats import norm, rankdata

from . import pathway, semantic_layer, trajectory

VERSION = 'early_signal/1'
# name: (kind, acute, label). Acute channels are masked in acute-data gaps and spikes.
CHANNELS = {
    'ed_visits': ('count', True, 'ED visits'),
    'urgent_care': ('count', True, 'urgent care visits'),
    'admissions': ('count', True, 'hospital admissions'),
    'pcp_visits': ('count', False, 'primary care visit days'),
    'telehealth': ('count', False, 'telehealth contacts'),
    'phone_messages': ('count', False, 'telephone messages'),
    'missed_appointments': ('count', False, 'missed appointments'),
    'dispenses': ('count', False, 'pharmacy dispenses'),
    'weight': ('measure', False, 'weight vs own baseline'),
    'systolic_bp': ('measure', False, 'systolic BP vs own baseline'),
    'potassium': ('measure', False, 'potassium vs own baseline'),
    'creatinine': ('measure', False, 'creatinine vs own baseline'),
    'egfr': ('measure', False, 'eGFR vs own baseline'),
}
OUTCOMES = {'acute_care': ('ed_visits', 'admissions'), 'admission': ('admissions',), 'ed_visit': ('ed_visits',)}
OUTCOME_LABEL = {'acute_care': 'an ED visit or hospital admission', 'admission': 'a hospital admission', 'ed_visit': 'an ED visit'}
BASELINE_CHANNELS = ('ed_visits', 'urgent_care', 'admissions')
CLIP, SD_FLOOR, MIN_EVENTS, MIN_CELL, BASELINE_MONTHS = 4.0, 0.05, 10, 5, 3
VISIT_CHANNEL = {'emergency_visit': 'ed_visits', 'urgent_care_visit': 'urgent_care', 'primary_care_visit': 'pcp_visits',
                 'telehealth_contact': 'telehealth'}


# ---- parameters ------------------------------------------------------------------------------
def validate(p):
    allowed = {'outcome', 'window_months', 'horizon_months', 'false_alarm_rate', 'train_cutoff', 'channels', 'top'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError(f'early_signal parameters: {sorted(allowed)}')
    out = {'outcome': p.get('outcome') or 'acute_care', 'window_months': p.get('window_months', 6), 'horizon_months': p.get('horizon_months', 3),
           'false_alarm_rate': p.get('false_alarm_rate', 0.05), 'train_cutoff': p.get('train_cutoff') or '', 'top': p.get('top', 25)}
    if out['outcome'] not in OUTCOMES: raise ValueError(f'outcome must be one of {sorted(OUTCOMES)}')
    if type(out['window_months']) is not int or not 3 <= out['window_months'] <= 12: raise ValueError('window_months 3..12')
    if type(out['horizon_months']) is not int or not 1 <= out['horizon_months'] <= 6: raise ValueError('horizon_months 1..6')
    far = out['false_alarm_rate']
    if type(far) not in (int, float) or not 0.001 <= far <= 0.5: raise ValueError('false_alarm_rate 0.001..0.5')
    out['false_alarm_rate'] = float(far)
    c = out['train_cutoff']
    if c:
        if not isinstance(c, str) or len(c) not in (7, 10): raise ValueError('train_cutoff is YYYY-MM')
        date.fromisoformat(c[:7] + '-01'); out['train_cutoff'] = c[:7]
    chans = p.get('channels') or list(CHANNELS)
    if not isinstance(chans, list) or len(set(chans)) != len(chans) or any(ch not in CHANNELS for ch in chans) or len(chans) < 2:
        raise ValueError(f'channels: 2+ distinct of {list(CHANNELS)}')
    out['channels'] = [ch for ch in CHANNELS if ch in chans]
    if type(out['top']) is not int or not 1 <= out['top'] <= 200: raise ValueError('top 1..200')
    return out


# ---- month helpers ---------------------------------------------------------------------------
def _mi(s): return int(s[:4]) * 12 + int(s[5:7]) - 1
def _ms(i): return f'{i // 12:04d}-{i % 12 + 1:02d}'


def last_complete_month(as_of):
    d = date.fromisoformat(as_of[:10])
    return _mi(as_of) if d.day == calendar.monthrange(d.year, d.month)[1] else _mi(as_of) - 1


def acute_spikes(monthly, ratio=4.0, floor=30):
    """One-month acute bursts: >= floor visits and > ratio x both the median acute month and the larger neighbour.
    monthly: {YYYY-MM: (acute, total)}. The generator's 2026-09 ED burst is the case this catches."""
    acute = {m: a for m, (a, _) in monthly.items() if a}
    if len(acute) < 3: return []
    med = float(np.median(list(acute.values())))
    out = []
    for m, a in sorted(acute.items()):
        i = _mi(m)
        neighbour = max(monthly.get(_ms(i - 1), (0, 0))[0], monthly.get(_ms(i + 1), (0, 0))[0])
        if a >= floor and a > ratio * med and a > ratio * neighbour: out.append(m)
    return out


# ---- data ------------------------------------------------------------------------------------
def load(db, as_of):
    """Raw monthly panel from the semantic layer: {months, pids, counts{ch: P x M}, measures{ch: P x M (nan = none)},
    acute_monthly {YYYY-MM: (acute, total)}}. Months span the first channel record through the snapshot month."""
    end = _mi(as_of)
    pids = [r[0] for r in db.execute('SELECT patient_id FROM patient ORDER BY patient_id')]
    row = {p: i for i, p in enumerate(pids)}
    raw = defaultdict(list)  # ch -> [(row, month_index, value)]
    monthly = defaultdict(lambda: [0, 0])
    for pid, m, concept, n in db.execute("SELECT patient_id, substr(event_date, 1, 7), type_concept, count(DISTINCT substr(event_date, 1, 10)) "
                                         "FROM visit WHERE event_date IS NOT NULL GROUP BY 1, 2, 3"):
        monthly[m][1] += n
        if concept in ('emergency_visit', 'urgent_care_visit', 'inpatient_admission', 'observation_stay'): monthly[m][0] += n
        if concept in VISIT_CHANNEL and pid in row: raw[VISIT_CHANNEL[concept]].append((row[pid], _mi(m), n))
    sources = {'admissions': "SELECT patient_id, substr(admit_date, 1, 7), count(*) FROM stay WHERE admit_date IS NOT NULL GROUP BY 1, 2",
               'phone_messages': "SELECT patient_id, substr(event_date, 1, 7), count(*) FROM record WHERE stream = 'telephone_messages' "
                                 "AND event_date IS NOT NULL GROUP BY 1, 2",
               'missed_appointments': "SELECT patient_id, substr(scheduled_date, 1, 7), sum(ever_no_show) FROM appointment "
                                      "WHERE scheduled_date IS NOT NULL GROUP BY 1, 2",
               'dispenses': "SELECT patient_id, substr(event_date, 1, 7), count(*) FROM medication WHERE evidence = 'dispensed' "
                            "AND event_date IS NOT NULL GROUP BY 1, 2"}
    for ch, sql in sources.items():
        for pid, m, n in db.execute(sql):
            if pid in row and m and n: raw[ch].append((row[pid], _mi(m), n))
    measures = [c for c, (k, _, _) in CHANNELS.items() if k == 'measure']
    for pid, concept, m, v in db.execute('SELECT patient_id, concept, substr(event_date, 1, 7), avg(value_canonical) FROM measurement '
                                         'WHERE concept IN (%s) AND value_canonical IS NOT NULL AND event_date IS NOT NULL GROUP BY 1, 2, 3'
                                         % ','.join('?' * len(measures)), measures):
        if pid in row: raw[concept].append((row[pid], _mi(m), v))
    starts = [mi for vals in raw.values() for _, mi, _ in vals if mi <= end]
    start = min(starts) if starts else end
    M = end - start + 1
    counts, meas = {}, {}
    for ch, (kind, _, _) in CHANNELS.items():
        a = np.zeros((len(pids), M)) if kind == 'count' else np.full((len(pids), M), np.nan)
        for r, mi, v in raw.get(ch, []):
            if start <= mi <= end: a[r, mi - start] = (a[r, mi - start] + v) if kind == 'count' else v
        (counts if kind == 'count' else meas)[ch] = a
    return {'start': start, 'months': [_ms(start + k) for k in range(M)], 'pids': pids, 'counts': counts, 'measures': meas,
            'acute_monthly': {m: tuple(v) for m, v in monthly.items()}}


def prepare(raw, as_of, channels):
    """Panel geometry: masked months, channel availability, grid start, acute-observed months.
    Returns dict with months (grid), per-channel present[M] booleans, acute_observed[M], gaps, spikes, dropped channels."""
    last = last_complete_month(as_of)
    monthly = {m: v for m, v in raw['acute_monthly'].items() if m and _mi(m) <= _mi(as_of)}
    gaps = pathway.acute_gaps(monthly)
    spikes = acute_spikes(monthly)
    bad = {m for g in gaps for m in (_ms(i) for i in range(_mi(g['start']), _mi(g['end']) + 1))} | set(spikes)
    months = raw['months']
    present, dropped = {}, []
    for ch in channels:
        kind, acute, _ = CHANNELS[ch]
        a = raw['counts'][ch] if kind == 'count' else raw['measures'][ch]
        net = (a > 0).sum(0) if kind == 'count' else (~np.isnan(a)).sum(0)
        ok = (net > 0) & np.array([_mi(m) <= last for m in months])
        if acute: ok &= np.array([m not in bad for m in months])
        if ok.sum() < 3: dropped.append(ch); continue
        present[ch] = ok
    if not present: raise ValueError('no channel has data')
    cover = np.mean([present[ch] for ch in present], axis=0)
    first = next((k for k, v in enumerate(cover) if v >= 0.6), int(np.argmax(cover)))
    stop = last - raw['start']  # last complete month index in raw coordinates
    sl = slice(first, stop + 1)
    acute_chans = [ch for ch in present if CHANNELS[ch][1]]
    acute_obs = np.array([m not in bad for m in months]) & (np.array([monthly.get(m, (0, 0))[0] for m in months]) > 0)
    return {'slice': sl, 'months': months[sl], 'present': {ch: v[sl] for ch, v in present.items()}, 'acute_observed': acute_obs[sl],
            'gaps': gaps, 'spikes': spikes, 'dropped_channels': dropped, 'acute_channels': acute_chans,
            'partial_month': _ms(_mi(as_of)) if last < _mi(as_of) else None}


def outcome_months(raw, geo, outcome):
    """{row: sorted grid month indices with an outcome event in an acute-observed month}."""
    sl = geo['slice']; out = defaultdict(set)
    for ch in OUTCOMES[outcome]:
        a = raw['counts'][ch][:, sl]
        for r, k in zip(*np.nonzero(a > 0)):
            if geo['acute_observed'][k]: out[int(r)].add(int(k))
    return {r: sorted(v) for r, v in out.items()}


def whiten(raw, geo, reference_rows):
    """-> Z (P x C x M) whitened/clipped, Mk (P x C x M) 0/1 masks, channel order. Statistics from reference rows only."""
    sl, chans = geo['slice'], list(geo['present'])
    P, M = len(raw['pids']), geo['acute_observed'].size
    Z = np.zeros((P, len(chans), M)); Mk = np.zeros((P, len(chans), M))
    ref = np.zeros(P, bool); ref[list(reference_rows)] = True
    for c, ch in enumerate(chans):
        kind = CHANNELS[ch][0]
        if kind == 'count':
            v = np.log1p(raw['counts'][ch][:, sl]); valid = np.ones_like(v, bool); floor = SD_FLOOR
        else:
            full = raw['measures'][ch]
            dev = np.full(full.shape, np.nan)
            for r in np.nonzero(~np.isnan(full).all(1))[0]:  # deviation from the patient's own median of EARLIER months
                seen = []
                for k in range(full.shape[1]):
                    x = full[r, k]
                    if np.isnan(x): continue
                    if seen: dev[r, k] = x - float(np.median(seen))
                    seen.append(x)
            v = dev[:, sl]; valid = ~np.isnan(v); floor = trajectory.NOISE.get(ch, (0, 1.0))[1]
        pres = geo['present'][ch]
        for k in range(M):
            if not pres[k]: continue
            col, ok = v[:, k], valid[:, k]
            rv = col[ok & ref]
            if rv.size < 5: continue
            mu, sd = float(rv.mean()), max(float(rv.std()), floor)
            z = np.clip((np.where(ok, col, mu) - mu) / sd, -CLIP, CLIP)
            Z[:, c, k] = np.where(ok, z, 0.0); Mk[:, c, k] = ok
    return Z, Mk, chans


# ---- the matched filter ------------------------------------------------------------------------
def learn_template(Z, Mk, events, W):
    """events: [(row, index_month)] with index_month >= W. -> {'template' C x W (shrunk), 'mean', 'n', 't'} (positions -W..-1)."""
    C = Z.shape[1]
    s = np.zeros((C, W)); n = np.zeros((C, W)); s2 = np.zeros((C, W))
    for r, i in events:
        z, m = Z[r, :, i - W:i], Mk[r, :, i - W:i]
        s += z * m; s2 += z * z * m; n += m
    mean = np.divide(s, n, out=np.zeros_like(s), where=n > 0)
    var = np.divide(s2, n, out=np.ones_like(s), where=n > 0) - mean ** 2
    se = np.sqrt(np.maximum(var, 1e-6) / np.maximum(n, 1))
    t = np.divide(mean, se, out=np.zeros_like(mean), where=n >= MIN_CELL)
    shrink = np.where(n >= MIN_CELL, np.maximum(0.0, 1 - 1 / np.maximum(t * t, 1e-12)), 0.0)
    return {'template': mean * shrink, 'mean': mean, 'n': n, 't': t}


def matched(Z, Mk, T, t, H, rows=None):
    """Normalised matched-filter statistic at scoring month t (grid index) for leads 1..H.
    -> snr[rows] (nan when no overlap), best lead[rows], per-channel contribution[rows x C] at the best lead."""
    rows = np.arange(Z.shape[0]) if rows is None else np.asarray(rows)
    C, W = T.shape
    best = np.full(rows.size, -np.inf); lead = np.zeros(rows.size, int); contrib = np.zeros((rows.size, C))
    for j in range(1, H + 1):
        k0 = max(0, W - j - t)                          # template positions before the panel start are unobserved
        ks = np.arange(k0, W - j + 1)
        if ks.size == 0: continue
        ms = t + j - W + ks
        h = T[:, ks]
        x, m = Z[rows][:, :, ms], Mk[rows][:, :, ms]
        den = np.sqrt((m * h * h).sum((1, 2)))
        part = (m * h * x).sum(2)
        rho = np.divide(part.sum(1), den, out=np.full(rows.size, -np.inf), where=den > 1e-9)
        better = rho > best
        best[better], lead[better] = rho[better], j
        contrib[better] = part[better] / den[better, None]
    best[~np.isfinite(best)] = np.nan
    return best, lead, contrib


def background(Z, Mk, T, rows, months, H):
    vals = [matched(Z, Mk, T, t, H, rows)[0] for t in months]
    v = np.concatenate(vals) if vals else np.array([])
    return np.sort(v[~np.isnan(v)])


def fap(null, s):
    """Empirical false-alarm probability of statistic(s) s against the sorted background."""
    s = np.atleast_1d(np.asarray(s, float))
    above = null.size - np.searchsorted(null, s, side='left')
    return np.where(np.isnan(s), np.nan, (1 + above) / (1 + null.size))


def threshold(null, alpha):
    return float(np.quantile(null, 1 - alpha, method='higher')) if null.size else math.inf


# ---- evaluation helpers ----------------------------------------------------------------------
def wilson(k, n):
    if n == 0: return None, None
    z = 1.959964; p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)


def auc(scores, labels):
    """Mann-Whitney AUC with ties, Hanley-McNeil 95% interval."""
    s, y = np.asarray(scores, float), np.asarray(labels, bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if not n1 or not n0: return None
    r = rankdata(s)
    a = (r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
    q1, q2 = a / (2 - a), 2 * a * a / (1 + a)
    se = math.sqrt(max(a * (1 - a) + (n1 - 1) * (q1 - a * a) + (n0 - 1) * (q2 - a * a), 0) / (n1 * n0))
    a = float(a)
    return {'auc': round(a, 4), 'ci95_low': round(max(0.0, a - 1.96 * se), 4), 'ci95_high': round(min(1.0, a + 1.96 * se), 4)}


def _rates(flag, y):
    tp = int((flag & y).sum()); fp = int((flag & ~y).sum()); fn = int((~flag & y).sum())
    pr, rc = wilson(tp, tp + fp), wilson(tp, tp + fn)
    return {'alerts': tp + fp, 'true_positives': tp, 'precision': round(tp / (tp + fp), 4) if tp + fp else None,
            'precision_ci95': pr, 'recall': round(tp / (tp + fn), 4) if tp + fn else None, 'recall_ci95': rc}


def _matched_volume(score, y, k):
    """Expected precision/recall of flagging the top-k by score with ties broken at random."""
    if k <= 0 or not y.size: return {'alerts': 0, 'precision': None, 'recall': None}
    order = np.sort(score)[::-1]; cut = order[min(k, score.size) - 1]
    above = score > cut; tied = score == cut
    take = k - int(above.sum()); frac = take / max(int(tied.sum()), 1)
    tp = float((above & y).sum() + frac * (tied & y).sum())
    return {'alerts': k, 'precision': round(tp / k, 4), 'recall': round(tp / max(int(y.sum()), 1), 4) if y.sum() else None}


def _baseline(raw, geo, rows, t):
    sl = geo['slice']; lo = max(0, t - BASELINE_MONTHS + 1)
    tot = np.zeros(len(rows))
    for ch in BASELINE_CHANNELS:
        if ch not in geo['present']: continue
        a = raw['counts'][ch][:, sl][rows][:, lo:t + 1]
        tot += (a * geo['present'][ch][lo:t + 1]).sum(1)
    return tot


def _train(raw, geo, events_by_row, cutoff, W, H, train_rows):
    """Template, whitening, background using only months and labels <= cutoff."""
    P = len(raw['pids'])
    seen = {r for r, ks in events_by_row.items() if any(k <= cutoff for k in ks)}
    reference = [r for r in range(P) if r not in seen]
    Z, Mk, chans = whiten(raw, geo, reference)
    index = [(r, next(k for k in ks if W <= k <= cutoff)) for r, ks in events_by_row.items() if any(W <= k <= cutoff for k in ks)]
    pop_index = [(r, i) for r, i in index if r in train_rows]
    source = 'population' if len(pop_index) >= MIN_EVENTS else 'all patients'
    use = pop_index if source == 'population' else index
    tpl = learn_template(Z, Mk, use, W) if use else None
    null = background(Z, Mk, tpl['template'], reference, range(W - 1, cutoff - H + 1), H) if tpl else np.array([])
    return {'Z': Z, 'Mk': Mk, 'channels': chans, 'template': tpl, 'events': len(use), 'events_in_population': len(pop_index),
            'template_source': source, 'reference': len(reference), 'null': null}


def evaluate(raw, geo, events_by_row, p, rows, cutoff):
    """Time split: everything learned from months <= cutoff; detection scored at months t >= cutoff on labels in t+1..t+H."""
    W, H, alpha = p['window_months'], p['horizon_months'], p['false_alarm_rate']
    M = geo['acute_observed'].size
    tr = _train(raw, geo, events_by_row, cutoff, W, H, set(rows))
    if not tr['template'] or tr['events'] < 3 or tr['null'].size < 50:
        return {'status': 'not_evaluable', 'reason': f"{tr['events']} training events and {tr['null'].size} background samples before {_ms_geo(geo, cutoff)}"}
    thr = threshold(tr['null'], alpha)
    months = [t for t in range(cutoff, M - H) if geo['acute_observed'][t + 1:t + H + 1].all()]
    if not months: return {'status': 'not_evaluable', 'reason': 'no scoring month after the cutoff has a fully observed label window'}
    S, B, Y = [], [], []
    for t in months:
        s, _, _ = matched(tr['Z'], tr['Mk'], tr['template']['template'], t, H, rows)
        S.append(s); B.append(_baseline(raw, geo, rows, t))
        Y.append(np.array([any(t < k <= t + H for k in events_by_row.get(r, ())) for r in rows]))
    S, B, Y = np.concatenate(S), np.concatenate(B), np.concatenate(Y)
    ok = ~np.isnan(S); S, B, Y = S[ok], B[ok], Y[ok]
    flag, base = S >= thr, B >= 1
    mf, bl = _rates(flag, Y), _rates(base, Y)
    # event level: first event per patient after the cutoff whose look-back months were all scored
    det = {'matched_filter': [], 'baseline': []}
    scored = set(months)
    for r in rows:
        e = next((k for k in events_by_row.get(r, ()) if k > cutoff and any(k - j in scored for j in range(1, H + 1))), None)
        if e is None: continue
        looks = [t for t in range(max(cutoff, e - H), e) if t in scored]
        sc = {t: matched(tr['Z'], tr['Mk'], tr['template']['template'], t, H, [r])[0][0] for t in looks}
        bs = {t: _baseline(raw, geo, [r], t)[0] for t in looks}
        hit = next((t for t in looks if sc[t] >= thr), None); det['matched_filter'].append(e - hit if hit is not None else None)
        hb = next((t for t in looks if bs[t] >= 1), None); det['baseline'].append(e - hb if hb is not None else None)

    def lead(xs):
        got = [x for x in xs if x is not None]
        return {'events': len(xs), 'detected': len(got), 'detected_share': round(len(got) / len(xs), 4) if xs else None,
                'detected_ci95': wilson(len(got), len(xs)), 'median_lead_months': float(np.median(got)) if got else None}
    return {'status': 'evaluated', 'cutoff': _ms_geo(geo, cutoff), 'scoring_months': [_ms_geo(geo, t) for t in months],
            'label_months': f'{_ms_geo(geo, months[0] + 1)}..{_ms_geo(geo, months[-1] + H)}',
            'training_events': tr['events'], 'template_source': tr['template_source'], 'background_samples': int(tr['null'].size),
            'threshold_snr': round(thr, 3), 'patient_months': int(S.size), 'positives': int(Y.sum()), 'base_rate': round(float(Y.mean()), 4),
            'matched_filter': {**mf, **(auc(S, Y) or {}), 'alert_rate': round(float(flag.mean()), 4), **lead(det['matched_filter'])},
            'baseline': {'rule': f'>= 1 ED/urgent/admission in the last {BASELINE_MONTHS} months', **bl, **(auc(B, Y) or {}),
                         'alert_rate': round(float(base.mean()), 4), **lead(det['baseline'])},
            'baseline_at_matched_volume': _matched_volume(B, Y, int(flag.sum())),
            'null_false_alarm_rate_test': round(float((S[~Y] >= thr).mean()), 4) if (~Y).any() else None}


def _ms_geo(geo, k): return geo['months'][k] if 0 <= k < len(geo['months']) else None


def auto_cutoff(geo, events_by_row, W, H):
    """Median month of indexable first events, leaving at least one fully observed label window after it."""
    firsts = sorted(next(k for k in ks if k >= W) for ks in events_by_row.values() if any(k >= W for k in ks))
    M = geo['acute_observed'].size
    if not firsts: return None
    c = firsts[len(firsts) // 2]
    later = [t for t in range(c, M - H) if geo['acute_observed'][t + 1:t + H + 1].all()]
    if later: return c
    ok = [t for t in range(W, M - H) if geo['acute_observed'][t + 1:t + H + 1].all() and any(f <= t for f in firsts)]
    return ok[len(ok) // 2] if ok else None


# ---- operator ----------------------------------------------------------------------------------
def analyse(raw, as_of, p, patient_ids=None):
    W, H, alpha = p['window_months'], p['horizon_months'], p['false_alarm_rate']
    geo = prepare(raw, as_of, p['channels'])
    events = outcome_months(raw, geo, p['outcome'])
    M = len(geo['months'])
    keep = None if patient_ids is None else set(patient_ids)
    rows = [i for i, pid in enumerate(raw['pids']) if keep is None or pid in keep]
    notes = []
    if p['train_cutoff']:
        cut = _mi(p['train_cutoff']) - _mi(geo['months'][0])
        if not 0 <= cut < M: raise ValueError(f"train_cutoff outside the panel {geo['months'][0]}..{geo['months'][-1]}")
    else:
        cut = auto_cutoff(geo, events, W, H)
    evaluation = evaluate(raw, geo, events, p, rows, cut) if cut is not None else {'status': 'not_evaluable', 'reason': 'no indexable events'}
    # current detection: everything up to the last complete month
    now = M - 1
    tr = _train(raw, geo, events, now, W, H, set(rows))
    ranked, tpl_out = [], []
    thr = None
    if tr['template'] and tr['null'].size:
        T = tr['template']['template']
        thr = threshold(tr['null'], alpha)
        snr, lead, contrib = matched(tr['Z'], tr['Mk'], T, now, H, rows)
        f = fap(tr['null'], snr)
        recent = _baseline(raw, geo, rows, now)
        for i, r in enumerate(rows):
            if np.isnan(snr[i]): continue
            top = [tr['channels'][c] for c in np.argsort(-contrib[i])[:3] if contrib[i, c] > 0.25]
            ranked.append({'patient_id': raw['pids'][r], 'snr': round(float(snr[i]), 3), 'false_alarm_probability': round(float(f[i]), 4),
                           'alert': bool(snr[i] >= thr), 'aligned_lead_months': int(lead[i]),
                           'driving_channels': [CHANNELS[c][2] for c in top], 'acute_visits_last_3_months': int(recent[i]),
                           'event_in_window': any(now - W < k <= now for k in events.get(r, ()))})
        ranked.sort(key=lambda x: (-x['snr'], x['patient_id']))
        for c, ch in enumerate(tr['channels']):
            vals = T[c]
            tpl_out.append({'channel': ch, 'label': CHANNELS[ch][2], 'months_before_event': list(range(-W, 0)),
                            'template': [round(float(v), 3) for v in vals], 'raw_mean': [round(float(v), 3) for v in tr['template']['mean'][c]],
                            'events_per_month': [int(v) for v in tr['template']['n'][c]], 'energy': round(float((vals ** 2).sum()), 4)})
        tpl_out.sort(key=lambda x: -x['energy'])
        norm_ = float(np.sqrt((T ** 2).sum()))
    else:
        norm_ = 0.0
        notes.append('No template: too few indexable events or no background.')
    if tr['template_source'] != 'population' and patient_ids is not None:
        notes.append(f"Only {tr['events_in_population']} indexable events in the population (< {MIN_EVENTS}); templates learned from all patients' events.")
    gap_text = ', '.join(f"{g['start']}..{g['end']}" for g in geo['gaps'])
    if geo['gaps'] or geo['spikes']:
        notes.append('Acute-data defects excluded: ' + '; '.join(
            ([f'no ED/inpatient/observation/urgent-care records {gap_text} while other visits continue (feed gap, not absent care)'] if geo['gaps'] else []) +
            ([f"one-month acute spike {', '.join(geo['spikes'])}"] if geo['spikes'] else [])) +
            '. Acute channels are masked (zero weight) in those months; no event there is used as an index or a label.')
    if geo['partial_month']: notes.append(f"{geo['partial_month']} is a partial month (snapshot {as_of}); scoring uses months through {geo['months'][-1]}.")
    if geo['dropped_channels']: notes.append('Channels without data: ' + ', '.join(geo['dropped_channels']) + '.')
    if evaluation.get('status') == 'evaluated' and evaluation['positives'] < 30:
        notes.append(f"Small evaluation: {evaluation['positives']} positive patient-months; intervals are wide.")
    flagged = sum(r['alert'] for r in ranked)
    method = (f"Monthly panel {geo['months'][0]}..{geo['months'][-1]} of {len(tr['channels'])} channels (log1p counts; measurements as deviation from the "
              f"patient's earlier median), whitened per channel-month by reference (non-event) patients, clipped at +/-{CLIP:g}, missing/absent cells masked. "
              f"Template = shrunk mean whitened {W}-month window before each patient's first {OUTCOME_LABEL[p['outcome']]} (index months with observed acute data). "
              f"Detection = normalised matched filter maximised over alignments placing the event 1..{H} months ahead; threshold and false-alarm probability "
              f"from an empirical background of non-event patients at time-shifted months (nominal false-alarm rate {alpha:g}). "
              f"Evaluation is a time split (templates/whitening/background from months <= cutoff, scored on later months) against "
              f">= 1 acute visit in the last {BASELINE_MONTHS} months. Channels are whitened independently (cross-channel correlation not removed); "
              f"patient-months overlap, so intervals are optimistic. Associational early warning, not a causal or clinical risk model.")
    return {'schema': VERSION, 'parameters': p, 'as_of': as_of, 'outcome_label': OUTCOME_LABEL[p['outcome']],
            'panel': {'start': geo['months'][0], 'end': geo['months'][-1], 'months': M, 'channels': tr['channels'],
                      'dropped_channels': geo['dropped_channels']},
            'acute_data_gaps': geo['gaps'], 'acute_spikes': geo['spikes'], 'partial_month': geo['partial_month'],
            'patients_scored': len(ranked), 'flagged': flagged, 'threshold_snr': round(thr, 3) if thr is not None else None,
            'training_events': tr['events'], 'template_source': tr['template_source'], 'background_samples': int(tr['null'].size),
            'template_norm': round(norm_, 3), 'templates': tpl_out, 'patients': ranked[:p['top']], 'evaluation': evaluation,
            'notes': notes, 'method': method}


def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    path = Path(path)
    as_of = as_of or json.loads((path / 'run.json').read_text())['snapshot']['as_of']
    db = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
    try:
        raw = load(db, as_of)
    finally:
        db.close()
    return analyse(raw, as_of, p, patient_ids)


def summary(report):
    p, ev = report['parameters'], report['evaluation']
    if not report['patients_scored']:
        return f"No early-signal ranking: {'; '.join(report['notes']) or 'no data'}"
    text = (f"{report['flagged']} of {report['patients_scored']} patients carry the {p['window_months']}-month pre-event signature of "
            f"{report['outcome_label']} at false-alarm rate {p['false_alarm_rate']:g} (matched-filter SNR >= {report['threshold_snr']:g}; "
            f"template from {report['training_events']} past events).")
    if ev.get('status') == 'evaluated':
        mf, bl, mv = ev['matched_filter'], ev['baseline'], ev['baseline_at_matched_volume']
        f = lambda v: '-' if v is None else f'{v:.1%}'
        text += (f" Time-split check (cutoff {ev['cutoff']}, labels {ev['label_months']}, {ev['positives']} events in {ev['patient_months']} patient-months): "
                 f"precision {f(mf['precision'])}, recall {f(mf['recall'])}, AUC {mf.get('auc', '-')}, median lead {mf['median_lead_months'] or '-'} months; "
                 f"baseline ({bl['rule']}) precision {f(bl['precision'])}, recall {f(bl['recall'])}, AUC {bl.get('auc', '-')}; "
                 f"baseline at the same alert volume precision {f(mv['precision'])}.")
    else:
        text += f" Not evaluated: {ev.get('reason')}."
    for n in report['notes']:
        if n.startswith('Acute-data defects'): text += ' ' + n
    return text
