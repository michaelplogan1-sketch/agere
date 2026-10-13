"""Lab and vital trajectories: who is materially getting worse (or better), and when did it change?

Per patient, a measurement series (canonical units, one value per day) is segmented
with the uncertainty-weighted change-point program in signal_math (Bayesian-blocks
family). Each value carries a concept-specific analytic + biological variation,
so ordinary noise does not register as change. A patient is flagged only when
the first and last segments differ by more than two combined standard errors
AND by a clinically material amount for that measurement. A robust Theil-Sen
slope (per year) is reported alongside.
"""
from collections import defaultdict
from datetime import date
import json, math, sqlite3, statistics

from . import concepts, semantic_layer
from ...signal_math import gaussian_change_points

VERSION = 'trajectory/1'
# (relative variation, absolute floor) per concept: combined analytic + within-person biological variation.
NOISE = {'egfr': (0.08, 3.0), 'creatinine': (0.06, 0.05), 'hba1c': (0.02, 0.15), 'glucose': (0.12, 8.0), 'potassium': (0.04, 0.15),
         'sodium': (0.01, 1.0), 'hemoglobin': (0.03, 0.3), 'ldl': (0.08, 6.0), 'hdl': (0.07, 3.0), 'total_cholesterol': (0.06, 8.0),
         'triglycerides': (0.2, 15.0), 'uacr': (0.3, 5.0), 'nt_probnp': (0.3, 50.0), 'systolic_bp': (0.06, 6.0),
         'diastolic_bp': (0.07, 5.0), 'weight': (0.01, 0.8), 'heart_rate': (0.1, 6.0), 'tsh': (0.2, 0.3), 'platelets': (0.08, 15.0)}
# Clinically material change: (absolute, relative) - either suffices.
MATERIAL = {'egfr': (10.0, 0.25), 'creatinine': (0.3, 0.3), 'hba1c': (1.0, None), 'glucose': (40.0, None), 'potassium': (0.7, None),
            'sodium': (5.0, None), 'hemoglobin': (2.0, None), 'ldl': (40.0, None), 'systolic_bp': (20.0, None), 'diastolic_bp': (12.0, None),
            'weight': (None, 0.05), 'uacr': (None, 1.0), 'nt_probnp': (None, 0.5), 'tsh': (None, 0.5), 'platelets': (None, 0.3)}
# Direction that means worsening for each concept.
WORSE = {'egfr': 'down', 'hemoglobin': 'down', 'hdl': 'down', 'platelets': 'down', 'weight': 'down'}


def validate(p):
    if not isinstance(p, dict) or set(p) - {'concept', 'direction', 'min_points', 'top', 'threshold', 'horizon_days'}:
        raise ValueError('trajectory parameters: concept, direction, min_points, top, threshold, horizon_days')
    if 'threshold' in p and (type(p['threshold']) not in (int, float) or not math.isfinite(p['threshold'])): raise ValueError('threshold must be a number')
    if 'horizon_days' in p and (type(p['horizon_days']) is not int or not 7 <= p['horizon_days'] <= 1095): raise ValueError('horizon_days 7..1095')
    if p.get('concept') not in NOISE: raise ValueError(f'trajectory concept must be one of {sorted(NOISE)}')
    direction = p.get('direction', 'worse')
    if direction not in ('worse', 'better', 'up', 'down', 'any'): raise ValueError('direction: worse, better, up, down or any')
    mp = p.get('min_points', 3)
    if type(mp) is not int or not 3 <= mp <= 50: raise ValueError('min_points 3..50')
    out = {'concept': p['concept'], 'direction': direction, 'min_points': mp, 'top': p.get('top', 50)}
    if 'threshold' in p: out.update(threshold=float(p['threshold']), horizon_days=p.get('horizon_days', 365))
    return out


def kalman(points, rel, floor, drift_per_year=None):
    """Local linear trend Kalman filter (level + slope per day) with per-value measurement error
    max(rel x value, floor). Returns final state mean [level, slope/day] and covariance."""
    import numpy as np
    t0, v0 = points[0]
    q_level = (floor * 0.5) ** 2 / 365.0          # true level wanders slowly (per day)
    q_slope = (drift_per_year or floor) ** 2 / 365.0 ** 3
    x = np.array([v0, 0.0]); P = np.diag([max(rel * abs(v0), floor) ** 2, (floor / 365.0) ** 2 * 4])
    last = t0
    for t, v in points:
        dt = max(t - last, 0); last = t
        F = np.array([[1.0, dt], [0.0, 1.0]])
        Q = np.array([[q_level * dt + q_slope * dt ** 3 / 3, q_slope * dt ** 2 / 2], [q_slope * dt ** 2 / 2, q_slope * dt]])
        x = F @ x; P = F @ P @ F.T + Q
        R = max(rel * abs(v), floor) ** 2
        S = P[0, 0] + R; K = P[:, 0] / S
        x = x + K * (v - x[0]); P = P - np.outer(K, P[0, :])
    return x, P, last


def forecast(points, rel, floor, horizon_days, threshold, below=True):
    """Predicted value at the horizon (mean, sd) and the probability it is past the threshold."""
    import numpy as np
    x, P, last = kalman(points, rel, floor)
    F = np.array([1.0, float(horizon_days)])
    mean = float(F @ x); sd = float(np.sqrt(max(F @ P @ F, 1e-12)))
    z = (threshold - mean) / sd
    p_below = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return {'level_now': round(float(x[0]), 2), 'slope_per_year': round(float(x[1]) * 365.25, 2),
            'forecast': round(mean, 2), 'forecast_sd': round(sd, 2), 'p_cross': round(p_below if below else 1 - p_below, 3)}


def _theil_sen(points):
    slopes = [(b[1] - a[1]) / ((b[0] - a[0]) / 365.25) for i, a in enumerate(points) for b in points[i + 1:] if b[0] > a[0]]
    return statistics.median(slopes) if slopes else None


def _material(concept, first, last):
    absolute, relative = MATERIAL.get(concept, (None, 0.2))
    delta = last - first
    return (absolute is not None and abs(delta) >= absolute) or (relative is not None and first and abs(delta) / abs(first) >= relative)


def execute(path, patient_ids, params):
    p = validate(params)
    c = p['concept']
    rel, floor = NOISE[c]
    db = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
    try:
        series = defaultdict(lambda: defaultdict(list))
        for pid, d, v in db.execute("SELECT patient_id, event_date, value_canonical FROM measurement WHERE concept = ? "
                                    "AND value_canonical IS NOT NULL AND event_date IS NOT NULL", (c,)):
            series[pid][d[:10]].append(v)
    finally:
        db.close()
    keep = set(patient_ids) if patient_ids is not None else None
    worse = WORSE.get(c, 'up')
    wanted = {'worse': worse, 'better': 'down' if worse == 'up' else 'up'}.get(p['direction'], p['direction'])
    flagged, evaluated = [], 0
    for pid, days in series.items():
        if keep is not None and pid not in keep: continue
        pts = sorted((date.fromisoformat(d).toordinal(), statistics.mean(v)) for d, v in days.items())
        if len(pts) < p['min_points']: continue
        evaluated += 1
        times = [t for t, _ in pts]; values = [v for _, v in pts]
        errors = [max(rel * abs(v), floor) for v in values]
        seg = gaussian_change_points(times, values, errors, penalty=6.0)
        blocks = seg['blocks']
        first, last = blocks[0], blocks[-1]
        delta = last['weighted_mean'] - first['weighted_mean']
        se = math.hypot(first['standard_error'], last['standard_error'])
        direction = 'up' if delta > 0 else 'down'
        if len(blocks) < 2 or last['observations'] < 2 or abs(delta) < 2 * se or not _material(c, first['weighted_mean'], last['weighted_mean']): continue
        if wanted != 'any' and direction != wanted: continue
        flagged.append({'patient_id': pid, 'direction': direction, 'from': round(first['weighted_mean'], 2), 'to': round(last['weighted_mean'], 2),
                        'change': round(delta, 2), 'z': round(abs(delta) / se, 1),
                        'changed_on': date.fromordinal(int(last['start_time'])).isoformat(),
                        'slope_per_year': round(_theil_sen(pts), 2) if _theil_sen(pts) is not None else None,
                        'points': len(pts), 'last_value': round(values[-1], 2), 'last_date': date.fromordinal(times[-1]).isoformat()})
    flagged.sort(key=lambda f: (-abs(f['change']) / max(abs(f['from']), 1e-9)))
    if 'threshold' in p:  # forecast mode: who is projected to cross the threshold within the horizon
        below = wanted in ('down',) or (wanted in ('worse', 'any') and worse == 'down')
        import json as _json
        from pathlib import Path as _Path
        snapshot = date.fromisoformat(_json.loads((_Path(path) / 'run.json').read_text())['snapshot']['as_of']).toordinal()
        at_risk = []
        for pid, days in series.items():
            if keep is not None and pid not in keep: continue
            pts = sorted((date.fromisoformat(d).toordinal(), statistics.mean(v)) for d, v in days.items())
            if len(pts) < p['min_points']: continue
            if (pts[-1][1] < p['threshold']) if below else (pts[-1][1] > p['threshold']): continue  # already past it
            gap = max(0, snapshot - pts[-1][0])  # the horizon runs from the snapshot, not the last result
            f = forecast(pts, rel, floor, p['horizon_days'] + gap, p['threshold'], below)
            f['days_since_last_result'] = gap
            if f['p_cross'] >= 0.5:
                at_risk.append({'patient_id': pid, 'last_value': round(pts[-1][1], 2), 'last_date': date.fromordinal(pts[-1][0]).isoformat(),
                                'points': len(pts), **f})
        at_risk.sort(key=lambda r: -r['p_cross'])
        unit = concepts.MEASUREMENTS[c]['unit']
        return {'schema': VERSION, 'concept': c, 'unit': unit, 'mode': 'forecast', 'threshold': p['threshold'],
                'horizon_days': p['horizon_days'], 'direction': 'below' if below else 'above', 'patients_with_series': evaluated,
                'flagged': len(at_risk), 'patients': at_risk[:p['top']],
                'method': (f'Local linear trend Kalman filter per patient (per-value error max({rel:.0%} x value, {floor} {unit})); '
                           f'flagged when P(value {"below" if below else "above"} {p["threshold"]} {unit} in {p["horizon_days"]} days) >= 0.5 '
                           f'and not already past it; series need >= {p["min_points"]} dated values.')}
    unit = concepts.MEASUREMENTS[c]['unit']
    return {'schema': VERSION, 'concept': c, 'unit': unit, 'direction': wanted, 'patients_with_series': evaluated,
            'flagged': len(flagged), 'patients': flagged[:p['top']],
            'method': (f'Per-patient change points (uncertainty-weighted blocks; per-value error = max({rel:.0%} x value, {floor} {unit})) '
                       f'and Theil-Sen slope; flagged when first vs last segment differ by > 2 SE and by a material amount; '
                       f'series need >= {p["min_points"]} dated values.')}
