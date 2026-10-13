"""A $0 local stand-in for the operator router's paid 'choose engine' step.

Features: the stack's installed sentence encoder (stack.retriever.encoder; no new model) plus a few lexical cues
taken from the deterministic compiler's own intent patterns. A multinomial logistic head, temperature-calibrated,
predicts one of the PRODUCT operators, literal_lookup or not_answerable. It decides only when its calibrated
top probability reaches the threshold at which held-out (non-benchmark) accuracy was >= 98%; otherwise the
Haiku choose step runs as before. Weights are trained by tools/learned_routing/train.py from non-benchmark
labels and bound to the encoder identity they were trained with: a different encoder disables the router.
"""
import json, os, re
from pathlib import Path

import numpy as np

from .capability_registry import PRODUCT

VERSION = 'learned-router/1'
LABELS = [*PRODUCT, 'literal_lookup', 'not_answerable']
MODEL_FILE = Path(__file__).with_name('learned_router_model.json')
LEXICAL_SCALE = 0.5  # lexical cues are binary; the embedding is unit length
# The local router may skip the paid choose step only for these outcomes. 'not_answerable' always goes to the paid
# router (its answer carries the list of missing data, and a false 'not answerable' would hide an engine).
SHORTCUT = frozenset([*PRODUCT, 'literal_lookup'])


def _cues():
    from . import local_compiler as lc
    from .analysis_graph import requested_shape
    rx = lambda pattern: (lambda low: bool(re.search(pattern, low)))
    scheduling = '|'.join(item[-1] if isinstance(item, tuple) else item for item in lc.SCHEDULING_INTENTS)
    unsupported = '|'.join(p for p, _ in lc.UNSUPPORTED)
    shape = lambda kind: (lambda low: requested_shape(low) == kind)
    return [
        ('survival', lc.survival_intent), ('demand', lc.demand_intent), ('monitor', rx(lc.MONITOR_TRIGGER)),
        ('claims', rx(lc.CLAIMS_TRIGGER)), ('market', rx(lc.MARKET_TRIGGER)), ('outreach', rx(lc.OUTREACH_INTENT)),
        ('outreach_phrase', rx(lc.OUTREACH_PHRASES)), ('profile', rx(lc.PROFILE_STRONG)), ('profile_weak', rx(lc.PROFILE_WEAK)),
        ('calibration', rx(lc.CALIBRATION_INTENT)), ('briefing', rx(lc.BRIEFING_TRIGGER)), ('pathway', rx(lc.PATHWAY_INTENT)),
        ('early_signal', rx(lc.EARLY_SIGNAL_INTENT)), ('analytic', rx(lc.ANALYTIC_INTENT)), ('scheduling', rx(scheduling)),
        ('unsupported', rx(unsupported)), ('death', rx(lc.DEATH_WORDS)),
        ('knowledge', lambda low: lc._knowledge_question(low) is not None),
        ('contract_strategy', lambda low: lc._contract_strategy_question(low, None, None) is not None),
        ('count', rx(r'\b(?:how many|number of|count|counts)\b')), ('list', rx(r'\b(?:list|which patients|who (?:are|is|has|have))\b')),
        ('aggregate', rx(r'\b(?:average|mean|median|sum|total|max(?:imum)?|min(?:imum)?)\b')),
        ('share', rx(r'%|\b(?:percent(?:age)?|share|proportion|fraction|rate)\b')),
        ('why', rx(r'\bwhy\b|\bdriv|\bassociated\b')), ('trend', rx(r'\btrend|\bdeclin|\bworsen|\bsliding|\bfalling|\brising')),
        ('forecast', rx(r'\bforecast|\bpredict|\bproject|\bnext (?:week|month|quarter|year)\b')),
        ('effect', rx(r'\b(?:did|does|has)\b.*\b(?:work|reduce|lower|improve|effect|impact|difference)')),
        ('shape_table', shape('table')), ('shape_value', shape('value')), ('shape_set', shape('patient_set')), ('shape_count', shape('count')),
    ]


_CUE_CACHE = []


def lexical(question):
    if not _CUE_CACHE: _CUE_CACHE.extend(_cues())
    low = ' '.join(question.lower().split())
    return np.array([1.0 if fn(low) else 0.0 for _, fn in _CUE_CACHE], dtype=np.float64)


def cue_names():
    if not _CUE_CACHE: _CUE_CACHE.extend(_cues())
    return [name for name, _ in _CUE_CACHE]


def features(questions, encoder):
    vectors = np.asarray(encoder.encode(list(questions)), dtype=np.float64)
    if vectors.ndim != 2 or vectors.shape[0] != len(questions) or not np.isfinite(vectors).all():
        raise ValueError('Invalid encoder vectors')
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    return np.hstack([vectors, LEXICAL_SCALE * np.vstack([lexical(q) for q in questions])])


def softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


_MODEL = {}


def load(path=None):
    path = Path(path or MODEL_FILE)
    key = (str(path), path.stat().st_mtime_ns if path.exists() else None)
    if key not in _MODEL:
        if not path.exists(): return None
        raw = json.loads(path.read_text())
        if raw.get('version') != VERSION or raw.get('labels') != LABELS or raw.get('cues') != cue_names():
            return None  # a capability or cue was added since training: the paid router decides until retrained
        _MODEL.clear()
        _MODEL[key] = {**raw, 'W': np.asarray(raw['weights'], dtype=np.float64), 'b': np.asarray(raw['bias'], dtype=np.float64)}
    return _MODEL[key]


def probabilities(model, X):
    return softmax((X @ model['W'].T + model['b']) / model['temperature'])


def predict(question, encoder, model=None):
    model = model or load()
    if model is None: raise ValueError('No learned router model for the current capability list')
    if getattr(encoder, 'identity', None) != model['encoder_identity']: raise ValueError('Encoder differs from the one the router was trained with')
    p = probabilities(model, features([question], encoder))[0]
    best = int(p.argmax())
    return {'label': LABELS[best], 'probability': float(p[best]), 'threshold': model['threshold'],
            'confident': bool(p[best] >= model['threshold'] and LABELS[best] in SHORTCUT),
            'top': sorted(((LABELS[i], round(float(p[i]), 4)) for i in range(len(LABELS))), key=lambda x: -x[1])[:3]}


def decide(question, encoder, model=None):
    """-> (label | None, receipt). label is returned only when the calibrated probability clears the threshold."""
    if encoder is None or os.environ.get('AGERE_LEARNED_ROUTER', 'on') == 'off':
        return None, {'used': False, 'reason': 'disabled' if encoder is not None else 'no local encoder'}
    try:
        result = predict(question, encoder, model)
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        return None, {'used': False, 'reason': str(exc)[:200]}
    receipt = {'router': VERSION, 'used': result['confident'], 'label': result['label'], 'probability': round(result['probability'], 4),
               'threshold': result['threshold'], 'top': result['top'], 'api_usd': 0.0}
    return (result['label'] if result['confident'] else None), receipt
