"""Gate thresholds chosen on calibration data with a stated guarantee (Learn-then-Test), with hand-set fallbacks.

Each gate that accepts a model output when a score clears a cutoff reads its cutoff here:

  economical_planner.decision   typed Perplexity route choice probability      (hand-set fallback 0.95)
  nl2sql.decider                typed A/B decider probability between two SQLs  (hand-set fallback 0.80)
  learned_router                local router calibrated top probability         (fallback: the value saved with the model)
  semantic_cache.similarity     encoder cosine for slot-identical paraphrases   (hand-set fallback 0.85)

The fitted values live in calibrated_thresholds.json, written by tools/certify_gates.py (gate_policy.GATES) from labelled
outcomes (the data each record names). A record is used only when Learn-then-Test (conformal.ltt_threshold) certified
a threshold: with probability >= 1 - delta over the calibration draw, the error rate among accepted cases is <= alpha,
assuming future cases are exchangeable with the calibration cases. When the data are too few or too noisy to certify
the target (with zero errors, alpha = 2% at delta = 10% needs >= 114 accepted cases), the hand-set value stays in force
and the receipt says the gate is uncalibrated. Nothing here calls a model.
"""
import json, os, time
from pathlib import Path

VERSION = 'calibrated-thresholds/1'
FILE = Path(__file__).with_name('calibrated_thresholds.json')
TARGET = {'alpha': 0.02, 'delta': 0.10}

_CACHE = {}


def _load(path=None):
    path = Path(path or os.environ.get('AGERE_THRESHOLDS_FILE') or FILE)
    try: key = (str(path), path.stat().st_mtime_ns)
    except OSError: return {}
    if key not in _CACHE:
        try: raw = json.loads(path.read_text())
        except (OSError, ValueError): raw = {}
        _CACHE.clear(); _CACHE[key] = raw if isinstance(raw, dict) and raw.get('version') == VERSION else {}
    return _CACHE[key]


def get(name, fallback, path=None, bind=None):
    """-> {'threshold', 'calibrated', 'guarantee', ...}: the certified threshold, or the fallback with the reason.
    bind: identity of the scorer the record was fitted for (e.g. a model digest); a different scorer gets the fallback."""
    rec = (_load(path).get('gates') or {}).get(name)
    if rec and rec.get('bind') is not None and rec.get('bind') != bind:
        return {'name': name, 'threshold': fallback, 'calibrated': False, 'reason': 'calibration was fitted for a different scorer'}
    if os.environ.get('AGERE_CALIBRATED_THRESHOLDS', 'on') == 'off':
        return {'name': name, 'threshold': fallback, 'calibrated': False, 'reason': 'calibrated thresholds disabled'}
    if not rec or rec.get('threshold') is None or not rec.get('calibrated'):
        return {'name': name, 'threshold': fallback, 'calibrated': False,
                'reason': (rec or {}).get('reason') or 'no calibration record', 'n_calibration': (rec or {}).get('n_calibration', 0)}
    return {'name': name, 'threshold': float(rec['threshold']), 'calibrated': True, 'alpha': rec['alpha'], 'delta': rec['delta'],
            'n_calibration': rec['n_calibration'], 'guarantee': guarantee_text(rec), 'data': rec.get('data')}


def threshold(name, fallback, path=None, bind=None):
    return get(name, fallback, path, bind)['threshold']


def guarantee_text(rec):
    return (f"error among accepted <= {rec['alpha']:.0%} with probability >= {1 - rec['delta']:.0%} "
            f"(Learn-then-Test on {rec['n_calibration']} labelled cases; {rec.get('n_accepted')} accepted, "
            f"{rec.get('errors_accepted')} wrong; exchangeability assumed)")


def fit(name, scores, errors, fallback, data, alpha=None, delta=None, grid=None, floor=None, bind=None):
    """Learn-then-Test record for one gate. floor: never certify below this score (outside the searched range)."""
    from .conformal import ltt_threshold
    alpha = TARGET['alpha'] if alpha is None else alpha
    delta = TARGET['delta'] if delta is None else delta
    scores, errors = list(scores), list(errors)
    if floor is not None:
        grid = [g for g in (grid if grid is not None else sorted(set(scores))) if g >= floor] or [floor]
    res = ltt_threshold(scores, errors, alpha, delta, grid)
    rec = {**res, 'name': name, 'fallback': fallback, 'data': data, 'fitted_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
           'search_floor': floor, 'bind': bind, 'calibrated': res['threshold'] is not None}
    if res['threshold'] is None:
        rec['reason'] = (f"not certifiable at alpha={alpha:.0%}, delta={delta:.0%} from {len(scores)} labelled cases "
                         f"(needs >= {res.get('min_accepted_for_zero_errors')} accepted with zero errors); hand-set {fallback} kept")
    else:
        rec['guarantee'] = guarantee_text(rec)
    return rec


def write(records, path=None):
    path = Path(path or FILE)
    doc = {'version': VERSION, 'target': TARGET, 'written_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
           'method': 'Learn-then-Test, fixed-sequence binomial tests (conformal.ltt_threshold)', 'gates': {r['name']: r for r in records}}
    path.write_text(json.dumps(doc, indent=1, default=float))
    _CACHE.clear()
    return doc
