"""`calibration` operation: how well calibrated are Agere's forecasts and risk scores?

One row per prediction Agere makes (Kalman lab/vital forecasts and last-observed-window exceedance probabilities,
per-member next-year cost from the internal risk score, outreach avoidable-utilization value), each with
its time-split conformal backtest: nominal level, empirical coverage of held-out values, the model's own
interval coverage where it has one, Brier score and expected calibration error (raw vs recalibrated) for
probabilities, and adaptive-conformal coverage for sequential forecasts. Reliability tables are attached.
Nothing here uses data after the moment a backtested prediction would have been issued.
"""
from collections import defaultdict
from datetime import date
import math, sqlite3

from . import conformal, semantic_layer, trajectory

VERSION = 'calibration/2'
TARGETS = ('all', 'forecasts', 'risk_scores', 'outreach_utilization')
# default forecast checks: the outreach rising-risk definitions
FORECASTS = (('egfr', 30.0, 'below'), ('hba1c', 9.0, 'above'), ('systolic_bp', 160.0, 'above'))


def validate(p):
    allowed = {'target', 'concept', 'threshold', 'direction', 'horizon_days', 'level'}
    if not isinstance(p, dict) or set(p) - allowed:
        raise ValueError('calibration parameters: ' + ', '.join(sorted(allowed)))
    out = {'target': p.get('target', 'all'), 'horizon_days': p.get('horizon_days', 365), 'level': p.get('level', 0.9)}
    if out['target'] not in TARGETS: raise ValueError(f'calibration target: {TARGETS}')
    if type(out['horizon_days']) is not int or not 30 <= out['horizon_days'] <= 730: raise ValueError('horizon_days 30..730')
    if type(out['level']) not in (int, float) or not 0.5 <= out['level'] <= 0.99: raise ValueError('level 0.5..0.99')
    if p.get('concept'):
        if p['concept'] not in trajectory.NOISE: raise ValueError(f'calibration concept must be one of {sorted(trajectory.NOISE)}')
        if type(p.get('threshold')) not in (int, float) or not math.isfinite(p['threshold']): raise ValueError('concept needs a numeric threshold')
        if p.get('direction', 'below') not in ('below', 'above'): raise ValueError('direction: below or above')
        out.update(concept=p['concept'], threshold=float(p['threshold']), direction=p.get('direction', 'below'))
        if 'target' not in p: out['target'] = 'forecasts'
    return out


def _forecast_row(series, concept, threshold, direction, snapshot, horizon, alpha, keep):
    cal = trajectory.calibrate(series, concept, snapshot, horizon, threshold, direction == 'below', alpha=alpha, keep=keep)
    pb = cal.get('probability_backtest') or {}
    row = {'prediction': f'{concept} forecast ({horizon} days); P({direction} {threshold:g})', 'method': 'split conformal (normalized) + Venn-Abers + ACI',
           'status': 'backtested' if cal.get('backtest_coverage') is not None else ('calibrated_only' if cal.get('available') else 'unavailable'),
           'level': cal['level'], 'backtest_coverage': cal.get('backtest_coverage'), 'model_interval_coverage': cal.get('kalman_nominal_coverage'),
           'calibration_cases': cal.get('backtest_calibration_pairs'), 'evaluation_cases': cal.get('backtest_evaluation_pairs'),
           'brier_model': pb.get('brier_kalman'), 'brier_calibrated': pb.get('brier_calibrated'),
           'ece_model': pb.get('ece_kalman'), 'ece_calibrated': pb.get('ece_calibrated'),
           'aci_coverage': (cal.get('aci') or {}).get('coverage'), 'statement': cal['statement']}
    reliability = pb.get('reliability_calibrated')
    return row, trajectory.public(cal), reliability


def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    alpha = round(1 - p['level'], 6)
    keep = set(patient_ids) if patient_ids is not None else None
    db = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
    rows, receipts, reliability = [], {}, {}
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        snapshot = date.fromisoformat(as_of[:10]).toordinal()
        if p['target'] in ('all', 'forecasts'):
            checks = [(p['concept'], p['threshold'], p['direction'])] if p.get('concept') else list(FORECASTS)
            series = trajectory.load_series(db, [c for c, _, _ in checks], as_of, keep)
            for c, t, direction in checks:
                row, receipt, rel = _forecast_row(series[c], c, t, direction, snapshot, p['horizon_days'], alpha, keep)
                rows.append(row); receipts[row['prediction']] = receipt
                if rel: reliability[row['prediction']] = rel
        if p['target'] in ('all', 'risk_scores'):
            from . import vbc
            cal = vbc.risk_calibration(db, as_of, 'none', alpha)
            rows.append({'prediction': 'risk score: per-member next-year allowed cost', 'method': 'Mondrian (age band) split conformal',
                         'status': 'backtested' if cal.get('backtest_coverage') is not None else ('calibrated_only' if cal.get('available') else 'unavailable'),
                         'level': cal['level'], 'backtest_coverage': cal.get('backtest_coverage'), 'model_interval_coverage': None,
                         'calibration_cases': cal.get('backtest_calibration_pairs'), 'evaluation_cases': cal.get('backtest_evaluation_pairs'),
                         'brier_model': None, 'brier_calibrated': None, 'ece_model': None, 'ece_calibrated': None, 'aci_coverage': None,
                         'statement': cal['statement']})
            receipts[rows[-1]['prediction']] = {k: v for k, v in cal.items() if not k.startswith('_')}
        if p['target'] in ('all', 'outreach_utilization'):
            from . import outreach
            cal = outreach.calibrate_utilization(db, as_of, alpha)
            rows.append({'prediction': 'outreach: avoidable-utilization value next year', 'method': 'Mondrian (age band) split conformal',
                         'status': 'backtested' if cal.get('backtest_coverage') is not None else ('calibrated_only' if cal.get('available') else 'unavailable'),
                         'level': cal['level'], 'backtest_coverage': cal.get('backtest_coverage'), 'model_interval_coverage': None,
                         'calibration_cases': cal.get('backtest_calibration_pairs'), 'evaluation_cases': cal.get('backtest_evaluation_pairs'),
                         'brier_model': None, 'brier_calibrated': None, 'ece_model': None, 'ece_calibrated': None, 'aci_coverage': None,
                         'statement': cal['statement']})
            receipts[rows[-1]['prediction']] = {k: v for k, v in cal.items() if not k.startswith('_')}
    finally:
        db.close()
    tested = [r for r in rows if r['backtest_coverage'] is not None]
    level = p['level']
    if tested:
        worst = min(tested, key=lambda r: r['backtest_coverage'])
        reading = (f"{len(tested)} of {len(rows)} prediction types backtested: {level:.0%} conformal intervals covered "
                   + ', '.join(f"{r['backtest_coverage']:.1%} ({r['prediction'].split(';')[0]})" for r in tested)
                   + f" of held-out values. Least calibrated: {worst['prediction'].split(';')[0]}"
                   + (' (below nominal: the next window behaved differently from the calibration window).' if worst['backtest_coverage'] < level - 0.03 else '.'))
    else:
        reading = 'No prediction type has enough time-split history to backtest coverage: ' + '; '.join(r['statement'] for r in rows)
    return {'schema': VERSION, 'parameters': p, 'as_of': as_of, 'rows': rows, 'receipts': receipts, 'reliability': reliability, 'reading': reading,
            'method': ('Time-split conformal calibration (conformal/1): predictions issued at a past origin from data on or before it, scored on the '
                       'following window; live intervals calibrate on the most recent complete window, the reported coverage calibrates one window '
                       'earlier and evaluates the next. Probabilities: Brier score and expected calibration error (10 equal-width bins) for the model '
                       'and after inductive Venn-Abers recalibration. Sequential: adaptive conformal inference (Gibbs & Candes 2021).'),
            'limitations': ['Coverage guarantees assume calibration and evaluation cases are exchangeable; a shift between windows shows up as coverage below nominal.',
                            'Windows with a run of months without recorded utilization are feed gaps and are not used for calibration.',
                            'Calibrated on synthetic data; no clinical validation.']}
