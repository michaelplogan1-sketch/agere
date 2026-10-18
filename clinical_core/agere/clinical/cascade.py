"""Certified cascades: a cheap tier answers only when its calibrated error bound is <= alpha; otherwise it defers.

Each free (or near-free) tier that can claim a question or decide a label has a score, and a certificate fitted on
labelled calibration data (tools/calibrate_cascades.py -> config/agere/cascade_thresholds.json):

  question routing (together.py specialists-first)   local_compiler, cohort_family, metric_router, operator_router,
                                                      compact_retrieval_typed_router, learned_verified_sql, semantic_cache
  therapy barrier reader (therapy_barriers.py)        tier1_rules, tier2_classifier, tier3_jev, tier3_perplexity

Certificate (Learn-then-Test, Angelopoulos, Bates, Candes, Jordan & Lei 2021; conformal.ltt_threshold): candidate
thresholds are tested from the strictest down with exact binomial tests (fixed-sequence testing); the chosen threshold
lambda is the most permissive one certified, so that with probability >= 1 - delta over the calibration draw the error
rate among cases with score >= lambda is <= alpha, assuming future cases are exchangeable with the calibration cases.
The certificate also reports `bound`: the one-sided Clopper-Pearson upper confidence bound (level 1 - delta) on the error
among accepted calibration cases at lambda; LTT's acceptance of lambda is exactly the statement bound <= alpha.

Runtime rule (certify()):
  calibrated record, score >= threshold    -> the tier answers; receipt `certified: {tier, alpha, delta, bound,
                                              n_calibration, threshold, score}`
  calibrated record, score below threshold -> the tier defers to the next tier (receipt says why)
  measured but not certifiable             -> the tier defers (status not_certified): it had enough calibration cases to be
                                              certified (>= the zero-error minimum, 45 at alpha 5% / delta 10%) but its
                                              measured errors rule out the bound at every threshold, or held-out data refute
                                              it. AGERE_CASCADE_DEFER_UNCERTIFIED=off keeps such tiers answering as today.
  no record / too little data / stale      -> today's behaviour (the tier answers as before); receipt says "uncalibrated"
A record is bound to the code it certifies (a digest of the tier's files); a different digest makes it stale, so an edit
to the tier falls back to today's behaviour until it is recalibrated. AGERE_CASCADE=off ignores every record (today's
behaviour everywhere). AGERE_CASCADE_THRESHOLDS points at another thresholds file (tests). Nothing here calls a model.
"""
from __future__ import annotations

import hashlib, json, math, os, time
from pathlib import Path

VERSION = 'agere-cascade/1'
SCHEMA = 'cascade-thresholds/1'
ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
FILE = ROOT / 'config' / 'agere' / 'cascade_thresholds.json'
DEFAULT_ALPHA = 0.05
DEFAULT_DELTA = 0.10

# tier -> files whose content the certificate is bound to (relative to clinical_core/agere/clinical)
TIER_CODE = {
    'local_compiler': ('local_compiler.py', 'compiler_rules.py', 'cohorts.py', 'concepts.py', 'analysis_graph.py'),
    'cohort_family': ('cohort_families.py',),
    'metric_router': ('metric_router.py', 'metrics.py', 'metric_families.py'),
    'operator_router': ('operator_router.py', 'learned_router.py'),
    'compact_retrieval_typed_router': ('economical_planner.py',),
    'learned_verified_sql': ('learning.py', 'verify.py'),
    'semantic_cache': ('semantic_cache.py',),
    'tier1_rules': ('therapy_barriers.py',),
    'tier2_classifier': ('therapy_barriers.py',),
    'tier3_jev': ('therapy_barriers.py',),
    'tier3_perplexity': ('therapy_barriers.py',),
}
ROUTE_TIERS = ('local_compiler', 'cohort_family', 'metric_router', 'operator_router', 'compact_retrieval_typed_router',
               'learned_verified_sql', 'semantic_cache')
THERAPY_TIERS = ('tier1_rules', 'tier2_classifier', 'tier3_jev', 'tier3_perplexity')

_CACHE = {}
_DIGESTS = {}


def enabled():
    return os.environ.get('AGERE_CASCADE', 'on').strip().lower() not in ('off', '0', 'false', 'no')


def code_digest(tier):
    """Digest of the files a tier's certificate is bound to (None for an unknown tier)."""
    names = TIER_CODE.get(tier)
    if not names: return None
    key = tuple((n, (HERE / n).stat().st_mtime_ns if (HERE / n).exists() else None) for n in names)
    if _DIGESTS.get(tier, (None,))[0] != key:
        h = hashlib.sha256()
        for n in names:
            p = HERE / n
            h.update(n.encode()); h.update(p.read_bytes() if p.exists() else b'<absent>')
        _DIGESTS[tier] = (key, h.hexdigest()[:16])
    return _DIGESTS[tier][1]


def load(path=None):
    """The thresholds document ({} when absent, unreadable or another schema)."""
    path = Path(path or os.environ.get('AGERE_CASCADE_THRESHOLDS') or FILE)
    try: key = (str(path), path.stat().st_mtime_ns)
    except OSError: return {}
    if key not in _CACHE:
        try: raw = json.loads(path.read_text())
        except (OSError, ValueError): raw = {}
        _CACHE.clear()
        _CACHE[key] = raw if isinstance(raw, dict) and raw.get('schema') == SCHEMA else {}
    return _CACHE[key]


def record(tier, path=None):
    return ((load(path).get('tiers') or {}).get(tier)) or None


# ---- statistics -----------------------------------------------------------------------------------------------------
def upper_bound(k, n, delta):
    """One-sided Clopper-Pearson upper bound on an error rate (k errors in n), confidence 1 - delta."""
    from .conformal import binomial_cdf
    if n <= 0: return 1.0
    if k >= n: return 1.0
    lo, hi = k / n, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if binomial_cdf(k, n, mid) > delta: lo = mid
        else: hi = mid
    return hi


def min_accepted(alpha, delta):
    """Accepted calibration cases needed to certify alpha at delta with zero errors."""
    return math.ceil(math.log(delta) / math.log1p(-alpha))


def fit(tier, scores, errors, *, alpha=DEFAULT_ALPHA, delta=DEFAULT_DELTA, grid=None, data=None, bind=None, score_definition=None):
    """Learn-then-Test certificate for one tier from calibration (score, error) pairs. -> record (calibrated or not)."""
    from .conformal import ltt_threshold
    scores = [float(s) for s in scores]
    errors = [bool(e) for e in errors]
    res = ltt_threshold(scores, errors, alpha, delta, grid)
    rec = {'tier': tier, 'alpha': alpha, 'delta': delta, 'n_calibration': len(scores), 'threshold': res['threshold'],
           'calibrated': res['threshold'] is not None, 'method': 'Learn-then-Test (fixed-sequence exact binomial tests)',
           'bind': bind if bind is not None else code_digest(tier), 'data': data or {}, 'score': score_definition,
           'fitted_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    if res['threshold'] is not None:
        n, k = res['n_accepted'], res['errors_accepted']
        rec.update(n_accepted=n, errors_accepted=k, coverage=res['coverage'], p_value=res['p_value'],
                   bound=round(upper_bound(k, n, delta), 4), decision='certified')
    else:
        acc_all = len(scores)
        k_all = sum(errors)
        rec['bound_if_accept_all'] = round(upper_bound(k_all, acc_all, delta), 4) if acc_all else None
        # enough data = had every case with a usable score (>= 0; -1 marks an answer the tier never accepts, e.g. 'unknown')
        # been accepted with zero errors, the tier would have been certified
        eligible = sum(1 for x in scores if x >= 0)
        rec['eligible'] = eligible
        enough = eligible >= min_accepted(alpha, delta)
        rec['decision'] = 'defer' if enough else 'uncalibrated'
        rec['reason'] = (f'not certifiable at alpha={alpha:.0%}, delta={delta:.0%}: {len(scores)} calibration cases ({eligible} with a usable '
                         f'score), {k_all} wrong; '
                         + (f'enough cases to certify with zero errors, so the measured errors rule the bound out: the tier defers'
                            if enough else f'needs >= {min_accepted(alpha, delta)} accepted cases with zero errors (more with errors); '
                                           f'too little data: today\'s behaviour kept'))
    return rec


def evaluate(scores, errors, threshold, delta=DEFAULT_DELTA):
    """Held-out check of a threshold: accepted, errors, empirical error, upper bound, coverage."""
    acc = [e for s, e in zip(scores, errors) if threshold is not None and s is not None and s >= threshold]
    n, k = len(acc), sum(acc)
    return {'n': len(scores), 'n_accepted': n, 'errors_accepted': k, 'coverage': round(n / len(scores), 4) if scores else None,
            'error_rate': round(k / n, 4) if n else None, 'error_upper_bound': round(upper_bound(k, n, delta), 4) if n else None}


def write(records, path=None, *, meta=None):
    path = Path(path or FILE)
    doc = {'schema': SCHEMA, 'version': VERSION, 'written_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
           'target': {'alpha': DEFAULT_ALPHA, 'delta': DEFAULT_DELTA},
           'method': 'Learn-then-Test certificates per tier (clinical_core/agere/clinical/cascade.py); held-out evaluation in each record',
           **(meta or {}), 'tiers': {r['tier']: r for r in records}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, default=float))
    _CACHE.clear()
    return doc


# ---- runtime ----------------------------------------------------------------------------------------------------------
def statement(cert):
    if cert.get('status') == 'certified':
        return (f"this tier ({cert['tier']}) answered because its calibrated error bound at confidence {1 - cert['delta']:.0%} is "
                f"{cert['bound']:.1%} <= alpha {cert['alpha']:.0%} (Learn-then-Test on {cert['n_calibration']} labelled cases; "
                f"score {cert['score']:.3g} >= threshold {cert['threshold']:.3g}; exchangeability assumed)")
    if cert.get('status') == 'not_certified':
        return (f"{cert['tier']} deferred: measured on {cert.get('n_calibration')} labelled cases, it cannot be certified at alpha "
                f"{cert['alpha']:.0%} (confidence {1 - cert['delta']:.0%})")
    if cert.get('status') == 'below_threshold':
        return (f"{cert['tier']} deferred: score {cert['score'] if cert['score'] is None else round(cert['score'], 4)} is below its "
                f"certified threshold {cert['threshold']:.3g} (alpha {cert['alpha']:.0%}, confidence {1 - cert['delta']:.0%})")
    return f"{cert.get('tier')} is uncalibrated ({cert.get('reason')}); it answered as before"


def certify(tier, score, path=None):
    """-> certificate dict with 'accept' (bool: may this tier answer?) and 'status' certified | below_threshold | uncalibrated.
    Missing, uncertifiable, disabled or stale records accept (today's behaviour) and say 'uncalibrated'."""
    rec = record(tier, path) if enabled() else None
    base = {'tier': tier, 'score': None if score is None else float(score), 'version': VERSION}
    if not enabled():
        out = {**base, 'status': 'uncalibrated', 'accept': True, 'certified': False, 'reason': 'cascade certificates disabled (AGERE_CASCADE=off)'}
    elif rec is None:
        out = {**base, 'status': 'uncalibrated', 'accept': True, 'certified': False, 'reason': 'no calibration record'}
    elif rec.get('bind') is not None and rec.get('bind') != code_digest(tier):
        out = {**base, 'status': 'uncalibrated', 'accept': True, 'certified': False,
               'reason': 'stale: the tier\'s code changed since calibration (recalibrate with tools/calibrate_cascades.py)',
               'n_calibration': rec.get('n_calibration')}
    elif (not rec.get('calibrated') or rec.get('threshold') is None) and rec.get('decision') == 'defer' and \
            os.environ.get('AGERE_CASCADE_DEFER_UNCERTIFIED', 'on').strip().lower() not in ('off', '0', 'false', 'no'):
        out = {**base, 'status': 'not_certified', 'accept': False, 'certified': False, 'alpha': rec.get('alpha'),
               'delta': rec.get('delta'), 'reason': rec.get('reason') or 'measured and not certifiable',
               'n_calibration': rec.get('n_calibration')}
    elif not rec.get('calibrated') or rec.get('threshold') is None:
        out = {**base, 'status': 'uncalibrated', 'accept': True, 'certified': False,
               'reason': rec.get('reason') or 'not certifiable', 'n_calibration': rec.get('n_calibration')}
    else:
        cert = {**base, 'alpha': rec['alpha'], 'delta': rec['delta'], 'bound': rec['bound'], 'n_calibration': rec['n_calibration'],
                'threshold': rec['threshold']}
        ok = score is not None and float(score) >= float(rec['threshold'])
        out = {**cert, 'status': 'certified' if ok else 'below_threshold', 'accept': ok, 'certified': ok}
    out['statement'] = statement(out)
    return out


def receipt_block(cert):
    """The `certified` block for answer receipts: {tier, alpha, delta, bound, n_calibration} (+ threshold, score, statement),
    or None when the tier is uncalibrated."""
    if not cert or cert.get('status') != 'certified':
        return None
    return {k: cert.get(k) for k in ('tier', 'alpha', 'delta', 'bound', 'n_calibration', 'threshold', 'score', 'statement')}


# ---- route scores -----------------------------------------------------------------------------------------------------
def cohort_family_score(info):
    """Parse confidence of a cohort-family claim, from its receipt: each convention default the parser had to apply and each
    slot a model filled lowers it (a missed phrase falls back to a default, so defaults mark where misreadings hide)."""
    info = info or {}
    s = 1.0 - 0.08 * len(info.get('defaults') or []) - 0.25 * len(info.get('model_filled') or [])
    if not info.get('has_definition'): s -= 0.05
    return round(max(0.0, min(1.0, s)), 4)


def route_score(route, receipt):
    """Score in [0, 1] of a specialist route's claim (higher = more confident). Routes without their own score get 1.0, so
    their certificate is route-level (all claims or none)."""
    receipt = receipt or {}
    if route == 'cohort_family':
        return cohort_family_score(receipt.get('cohort_family'))
    if route == 'semantic_cache':
        sim = (receipt.get('semantic_cache') or {}).get('similarity')
        return None if sim is None else float(sim)
    if route == 'compact_retrieval_typed_router':
        step = next((s for s in receipt.get('steps') or [] if s.get('stage') == 'typed_decision_router'), None)
        dec = (step or {}).get('decision') or {}
        p = (dec.get('probabilities') or {}).get(dec.get('choice'))
        return None if p is None else float(p)
    if route == 'operator_router':
        info = (receipt.get('local_compiler_declined') or {}).get('operator_router') or {}
        learned = info.get('learned_router') or {}
        if info.get('chosen_by') == 'learned_router' and learned.get('used') and learned.get('probability') is not None:
            return float(learned['probability'])
        return 1.0
    return 1.0


def route_tier(receipt):
    """Calibration tier for a planner receipt's route (exact-question cache replays take their original route's tier)."""
    receipt = receipt or {}
    route = receipt.get('route')
    if route == 'exact_question_cache':
        route = receipt.get('original_route')
    if route == 'local_model_rewrite_compiled':
        return None  # the local rewrite rung is not a calibrated specialist here (off on live stacks)
    return route if route in ROUTE_TIERS else None


def certify_route(receipt, path=None):
    """-> certificate for the specialist route that produced this planner receipt (None when it is not a specialist tier)."""
    tier = route_tier(receipt)
    if tier is None: return None
    scored = receipt
    if (receipt or {}).get('route') == 'exact_question_cache' and isinstance(receipt.get('prior_planning_receipt'), dict):
        scored = receipt['prior_planning_receipt']
    return certify(tier, route_score(tier, scored), path)
