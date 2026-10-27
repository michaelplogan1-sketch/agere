"""Time-series foundation-model CHALLENGERS for demand forecasting: Chronos-2 (120M) and TimesFM-2.5 (200M), zero-shot, local.

They enter demand.candidates() beside the classic models and Timestral. They are CHALLENGERS: a series uses one only when its
rolling-origin backtest MASE beats the incumbent (the default Timestral when available, else the best classic model) by
CHALLENGER_MARGIN -- they never win by reputation. Weights live in models/ (<1 GB each); AGERE_DEMAND_CHALLENGERS=off disables,
a comma list (e.g. 'chronos2') picks. A missing package/weights raises ValueError and the classic candidates take over.
"""
import os
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
MODELS = {'chronos2': ROOT / 'models' / 'chronos-2', 'timesfm25': ROOT / 'models' / 'timesfm-2.5-200m'}
PREFIXES = ('timestral', 'chronos2', 'timesfm25')
CHALLENGERS = ('chronos2', 'timesfm25')
CHALLENGER_MARGIN = 0.02   # a challenger must beat the incumbent's backtest MASE by 2%
_LOADED = {}


def enabled():
    raw = os.environ.get('AGERE_DEMAND_CHALLENGERS', 'on').strip().lower()
    if raw in ('', 'off', '0', 'false'): return []
    names = list(CHALLENGERS) if raw in ('on', '1', 'true', 'all') else [s.strip() for s in raw.split(',')]
    return [n for n in names if n in CHALLENGERS and MODELS[n].exists()]


def is_foundation(name):
    return str(name).startswith(PREFIXES)


def _chronos():
    if 'chronos2' not in _LOADED:
        from chronos import Chronos2Pipeline
        _LOADED['chronos2'] = Chronos2Pipeline.from_pretrained(str(MODELS['chronos2']), device_map='cpu')
    return _LOADED['chronos2']


def _timesfm():
    if 'timesfm25' not in _LOADED:
        import timesfm
        m = timesfm.TimesFM_2p5_200M_torch.from_pretrained(str(MODELS['timesfm25']), local_files_only=True)
        m.compile(timesfm.ForecastConfig(max_context=512, max_horizon=64, normalize_inputs=True, use_continuous_quantile_head=True,
                                         force_flip_invariance=True, infer_is_positive=True, fix_quantile_crossing=True))
        _LOADED['timesfm25'] = m
    return _LOADED['timesfm25']


LEVELS = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9])


def quantile_samples(q, levels, n, rng):
    """Preserve the supplied probability axis; use explicit linear tail assumptions outside its support.

    Missing tails are not identified by central quantiles. Linear extrapolation is a model assumption, not a
    coverage guarantee; callers retain it in their forecast receipt and evaluate interval coverage separately.
    """
    q, levels = np.asarray(q, float), np.asarray(levels, float)
    if (q.ndim != 2 or levels.ndim != 1 or len(levels) < 2 or q.shape[1] != len(levels)
            or not np.all(np.isfinite(q)) or not np.all(np.isfinite(levels))
            or np.any(np.diff(levels) <= 0) or levels[0] < 0 or levels[-1] > 1):
        raise ValueError('Finite increasing probability levels and aligned forecast quantiles required')
    q = np.maximum.accumulate(q, axis=1)
    h = q.shape[0]
    u = rng.uniform(0.0, 1.0, (n, h))
    draws = np.empty((n, h))
    for k in range(h):
        draws[:, k] = np.interp(u[:, k], levels, q[k])
        lower, upper = u[:, k] < levels[0], u[:, k] > levels[-1]
        draws[lower, k] = q[k, 0] + (u[lower, k] - levels[0]) * (q[k, 1] - q[k, 0]) / (levels[1] - levels[0])
        draws[upper, k] = q[k, -1] + (u[upper, k] - levels[-1]) * (q[k, -1] - q[k, -2]) / (levels[-1] - levels[-2])
    assumptions = {'provided_probability_range': [float(levels[0]), float(levels[-1])],
                   'tails': 'linear_extrapolation' if levels[0] > 0 or levels[-1] < 1 else 'supplied_full_range',
                   'tail_coverage_qualified': False, 'independent_horizon_draws': True,
                   'meaning': 'Central quantiles keep their original probabilities. Unprovided tails use a linear assumption; model intervals require empirical qualification.'}
    return draws, assumptions


def quantiles(name, y, h):
    """-> (h, 9) forecast quantiles at LEVELS."""
    y = np.asarray(y, float)[-512:]
    if name == 'chronos2':
        import torch
        q, _ = _chronos().predict_quantiles([torch.tensor(y, dtype=torch.float32)], prediction_length=h, quantile_levels=LEVELS.tolist())
        return np.asarray(q[0], float).reshape(h, -1)
    if name == 'timesfm25':
        _, q = _timesfm().forecast(horizon=h, inputs=[y])
        return np.asarray(q[0], float)[:, 1:10]   # column 0 is the mean; 1..9 are the 0.1..0.9 quantiles
    raise ValueError(f'unknown challenger {name}')


class Challenger:
    """Same contract as demand.Model: point(h) = median; simulate(h, n, rng) = inverse-CDF draws from the quantiles."""
    def __init__(self, y, name):
        self.name, self.y = name, np.asarray(y, float)
        self._h, self._q = 0, None

    def _quantiles(self, h):
        if self._h < h:
            try:
                self._q = quantiles(self.name, self.y, h); self._h = h
            except Exception as e:
                raise ValueError(f'{self.name} unavailable: {e}') from e
        return self._q[:h]

    def point(self, h): return self._quantiles(h)[:, 4]

    def simulate(self, h, n, rng):
        draws, self.simulation_assumptions = quantile_samples(self._quantiles(h), LEVELS, n, rng)
        return draws
