"""Outcome-labelled tier router: which cascade tier's CHECKED answer was right for questions like this one.

Trained by tools/training/train_all.py (learner 'tier_router') from tools/training/router_outcomes.py rows: verified
live answers (the accepted tier passed every check and nobody corrected it) and replayed factory questions whose
accepted answer matched the exact label. A MiniLM-feature logistic head (learned_router.features, the stack's own
encoder) predicts the tier; when its calibrated probability clears the model's threshold, Cascade.order moves that tier
to the front (after the exact authored plan). It only reorders: every tier still runs in order if the first one declines
or fails a check. No promoted model (or a different encoder) = the default order, unchanged.
"""
import json
import os
from pathlib import Path

import numpy as np

VERSION = 'tier-router/1'
ROOT = Path(__file__).resolve().parents[3]
PINNED = ('exact_local_plan',)
_CACHE = {}


def model_path():
    if os.environ.get('AGERE_COMPONENT_RELEASE_ROOT'):
        from .component_learning import runtime_model
        return runtime_model('tier_router')
    if os.environ.get('AGERE_TIER_ROUTER'): return Path(os.environ['AGERE_TIER_ROUTER'])
    base = next((p for p in (ROOT, *ROOT.parents) if (p / 'artifacts' / 'training' / 'runs').exists()), ROOT)
    pointers = sorted((base / 'artifacts' / 'training' / 'runs').glob('*/promoted/tier_router.json'), key=lambda p: p.stat().st_mtime)
    for pointer in reversed(pointers):
        try:
            model = Path(json.loads(pointer.read_text())['model'])
        except (OSError, ValueError, KeyError):
            continue
        if model.exists(): return model
    return None


def load(path=None):
    path = path or model_path()
    if path is None or not Path(path).exists(): return None
    key = (str(path), Path(path).stat().st_mtime_ns)
    if key not in _CACHE:
        raw = json.loads(Path(path).read_text())
        if raw.get('version') != VERSION: return None
        _CACHE[key] = {**raw, 'W': np.asarray(raw['weights']), 'b': np.asarray(raw['bias'])}
    return _CACHE[key]


def predict(question, encoder, model):
    from .learned_router import features, softmax,lexical,LEXICAL_SCALE
    X = np.asarray([LEXICAL_SCALE*lexical(question)]) if model.get('feature_kind')=='lexical' else features([question], encoder)
    p = softmax((X @ model['W'].T + model['b']) / model['temperature'])[0]
    i = int(p.argmax())
    return model['labels'][i], float(p[i])


def reorder(stack, question, order, *, model=None, encoder=None):
    """-> (order, receipt | None). The predicted tier goes first (after PINNED) when p >= threshold; a permutation, never fewer."""
    order = tuple(order)
    if os.environ.get('AGERE_TIER_ROUTER_ON', 'on') == 'off': return order, None
    model = model or load()
    if not model: return order, None
    encoder = encoder or getattr(getattr(stack, 'retriever', None), 'encoder', None)
    if model.get('feature_kind')!='lexical':
        from .component_learning import model_encoder
        encoder=model_encoder(model,encoder)
    if model.get('feature_kind')!='lexical' and (encoder is None or getattr(encoder, 'identity', None) != model.get('encoder_identity')): return order, None
    tier, p = predict(question, encoder, model)
    from .decision_evaluation import observe,snapshot_identity
    observe(decision_type='route',model='tier_router',confidence=p,proposal=tier,incumbent=order,
        identity={k:model.get(k)for k in ('version','weights','bias','labels','threshold','temperature','encoder_identity')},
        inputs={'question':question,'order':order,
                'snapshot':snapshot_identity(stack)})
    receipt = {'version': VERSION, 'tier': tier, 'p': round(p, 4), 'threshold': model.get('threshold'), 'applied': False}
    if tier not in order or p < float(model.get('threshold', 1.01)): return order, receipt
    front = [t for t in PINNED if t in order and t != tier] + [tier]
    return tuple(front) + tuple(t for t in order if t not in front), {**receipt, 'applied': True}
