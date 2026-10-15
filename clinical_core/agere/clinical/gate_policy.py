"""What 'confidence: verified' means: every gate on the answer's path is certified (Learn-then-Test) and passed.

A gate is any step that accepts an output and so can be wrong: a scored model choice (typed route decision, A/B decider,
learned router, semantic-cache similarity) or a whole answering route (the deterministic compiler, the metric router,
free-form SQL acceptance, ...). Each gate's certificate lives in calibrated_thresholds.json, written by
tools/certify_gates.py from saved labelled outcomes and bound to the identity of what it certifies (model weights, encoder,
model id + contract, or the route's code). A certificate states: with probability >= 1 - delta over the calibration draw,
the error rate among accepted cases is <= alpha (2% at delta 10%), assuming future questions are exchangeable with the
calibration questions.

Every answering path ends in an END-TO-END gate ('<route>.answer' or 'freeform_sql.verified') whose error is "the delivered
answer was wrong"; scored step gates on the path add their own certificate (their step error). The rule:

  verified    the path is known, every gate on it has a certificate bound to the current scorer/code, and every gate passed
              (score >= certified threshold; for route gates: the route was taken; for free-form SQL: acceptance verified).
              Then the end-to-end gate certifies the answer's error rate.
  low         some check on the path FAILED (free-form SQL not independently verified, a step score under its threshold,
              a contradicted abstention).
  unverified  nothing failed, but at least one gate on the path has no valid certificate (too little labelled data, a
              stale binding, or an unknown path). The number may well be right; its error rate is not certified.

Nothing here calls a model. AGERE_CONFIDENCE_RULE=legacy restores the pre-certificate labels (governed / independent =
verified).
"""
import hashlib, inspect, json, os
from pathlib import Path

from . import calibrated_thresholds as CT

VERSION = 'gate-policy/1'
HERE = Path(__file__).parent
LEVELS = ('verified', 'unverified', 'low')

# gate -> (kind, fallback threshold, what an error is). kind 'score': accept when score >= threshold; 'route': the gate
# is the route itself (score 1.0 when taken); 'flag': passed when the route's own acceptance check said verified.
GATES = {
    'economical_planner.decision': ('score', 0.95, 'the typed route choice it accepted led to a wrong answer'),
    'nl2sql.decider': ('score', 0.80, 'the SQL candidate the decider picked was the wrong answer'),
    'learned_router': ('score', None, 'the engine the local router picked was not the expected engine'),
    'semantic_cache.similarity': ('score', 0.85, 'a verified query was reused for a question that is not equivalent'),
    'operator_router.choice': ('route', 1.0, 'the analytic engine the operator router chose was not the expected operation'),
    'local_compiler.operation': ('route', 1.0, 'the deterministic compiler planned a different operation than expected'),
    'local_compiler.answer': ('route', 1.0, 'the delivered answer was wrong'),
    'local_rewrite.answer': ('route', 1.0, 'the delivered answer was wrong'),
    'learned_reuse.answer': ('route', 1.0, 'the delivered answer was wrong'),
    'bottled.answer': ('route', 1.0, 'the delivered answer was wrong'),
    'metric_router.answer': ('route', 1.0, 'the delivered answer was wrong'),
    'operator_router.answer': ('route', 1.0, 'the delivered answer was wrong'),
    'freeform_sql.verified': ('flag', 1.0, 'an answer the free-form acceptance check marked verified was wrong'),
    'typed_route.answer': ('route', 1.0, 'the delivered answer was wrong'),
    'frontier_planner.answer': ('route', 1.0, 'the delivered answer was wrong'),
}

# code each route gate certifies (a change to any of these files voids the certificate)
ROUTE_CODE = {
    'local_compiler.operation': ('local_compiler.py', 'compiler_rules.py', 'economical_planner.py'),
    'local_compiler.answer': ('local_compiler.py', 'compiler_rules.py', 'cohorts.py', 'concepts.py', 'grounding.py', 'analysis_graph.py',
                              'answer_checks.py', 'metrics.py'),
    'local_rewrite.answer': ('local_compiler.py', 'nl2sql.py', 'cohorts.py', 'concepts.py', 'analysis_graph.py'),
    'learned_reuse.answer': ('learning.py', 'verify.py', 'semantic_cache.py'),
    'bottled.answer': ('bottled.py', 'verify.py'),
    'metric_router.answer': ('metric_router.py', 'metrics.py', 'answer_checks.py'),
    'operator_router.choice': ('operator_router.py', 'learned_router.py'),
    'operator_router.answer': ('operator_router.py', 'analysis_graph.py', 'cohorts.py', 'concepts.py'),
    'freeform_sql.verified': ('nl2sql.py', 'verification_v2.py', 'metric_router.py', 'metrics.py', 'oracles.py'),
    'typed_route.answer': ('economical_planner.py', 'analysis_graph.py'),
    'frontier_planner.answer': ('economical_planner.py', 'analysis_graph.py'),
}

_IDENTITY = {}


def _files_digest(names):
    h = hashlib.sha256()
    for n in names:
        p = HERE / n
        h.update(n.encode()); h.update(p.read_bytes() if p.exists() else b'<absent>')
    return h.hexdigest()[:16]


def identity(gate, encoder=None):
    """The identity a gate's certificate must be bound to (calibrated_thresholds.get(bind=...))."""
    if gate == 'semantic_cache.similarity':
        return getattr(encoder, 'identity', None)
    if gate == 'learned_router':
        from . import learned_router as LR
        model = LR.load()
        if model is None or 'weights' not in model: return None
        model.setdefault('_identity', LR.model_identity(model))
        return model['_identity']
    if gate in _IDENTITY: return _IDENTITY[gate]
    if gate in ('economical_planner.decision', 'nl2sql.decider'):
        from ..gateway import PersistentGateway
        from . import economical_planner, nl2sql
        contract = inspect.getsource(nl2sql.Decider) if gate == 'nl2sql.decider' else \
            inspect.getsource(economical_planner.decision_body) + economical_planner.VERSION
        value = hashlib.sha256(json.dumps([gate, PersistentGateway.MODELS.get('perplexity'), contract]).encode()).hexdigest()[:16]
    elif gate in ROUTE_CODE:
        value = _files_digest(ROUTE_CODE[gate])
    else:
        value = None
    _IDENTITY[gate] = value
    return value


def certificate(gate, encoder=None, path=None):
    kind, fallback, _ = GATES[gate]
    if gate == 'learned_router' and fallback is None:
        from . import learned_router as LR
        fallback = (LR.load() or {}).get('threshold')
    return CT.get(gate, fallback, path=path, bind=identity(gate, encoder))


# ---- the path an answer took -------------------------------------------------------------------------------------
def path_gates(receipt):
    """-> [{'gate', 'score', 'passed'}] for a planner receipt, or None when the path is unknown. 'passed' is None for score
    gates (decided against the certified threshold in assess)."""
    if not isinstance(receipt, dict): return None
    route = receipt.get('route')
    if route == 'exact_question_cache':
        return path_gates(receipt.get('prior_planning_receipt') or {'route': receipt.get('original_route')})
    if route == 'local_compiler':
        return [{'gate': 'local_compiler.operation', 'score': 1.0, 'passed': True},
                {'gate': 'local_compiler.answer', 'score': 1.0, 'passed': True}]
    if route == 'local_model_rewrite_compiled':
        return [{'gate': 'local_rewrite.answer', 'score': 1.0, 'passed': True}]
    if route == 'learned_verified_sql':
        return [{'gate': 'learned_reuse.answer', 'score': 1.0, 'passed': True}]
    if route == 'semantic_cache':
        sim = (receipt.get('semantic_cache') or {}).get('similarity')
        return [{'gate': 'semantic_cache.similarity', 'score': sim, 'passed': None},
                {'gate': 'learned_reuse.answer', 'score': 1.0, 'passed': True}]
    if route == 'bottled_sql':
        return [{'gate': 'bottled.answer', 'score': 1.0, 'passed': True}]
    if route == 'metric_router':
        return [{'gate': 'metric_router.answer', 'score': 1.0, 'passed': True}]
    if route == 'operator_router':
        info = ((receipt.get('local_compiler_declined') or {}).get('operator_router') or {})
        out = []
        learned = info.get('learned_router') or {}
        if info.get('chosen_by') == 'learned_router' and learned.get('used'):
            out.append({'gate': 'learned_router', 'score': learned.get('probability'), 'passed': None})
        return out + [{'gate': 'operator_router.choice', 'score': 1.0, 'passed': True},
                      {'gate': 'operator_router.answer', 'score': 1.0, 'passed': True}]
    if route == 'hosted_verified_sql':
        hs = receipt.get('hosted_sql') or {}
        out = []
        pick = hs.get('decider') or {}
        if pick.get('choice') in ('A', 'B') and str(hs.get('agreement', '')).startswith('decider'):
            out.append({'gate': 'nl2sql.decider', 'score': pick.get('probability'), 'passed': None})
        acc = (receipt.get('acceptance') or {}).get('status')
        return out + [{'gate': 'freeform_sql.verified', 'score': 1.0 if acc == 'verified' else 0.0,
                       'passed': acc == 'verified' if acc else None}]
    if route == 'compact_retrieval_typed_router':
        step = next((s for s in receipt.get('steps') or [] if s.get('stage') == 'typed_decision_router'), None)
        dec = (step or {}).get('decision') or {}
        return [{'gate': 'economical_planner.decision', 'score': (dec.get('probabilities') or {}).get(dec.get('choice')), 'passed': None},
                {'gate': 'typed_route.answer', 'score': 1.0, 'passed': True}]
    if route == 'frontier_escalation':
        return [{'gate': 'frontier_planner.answer', 'score': 1.0, 'passed': True}]
    return None


def assess(receipt, encoder=None, path=None):
    """-> {'level': verified|unverified|low, 'reason', 'gates': [...]} under the rule in the module docstring."""
    gates = path_gates(receipt)
    if gates is None:
        return {'level': 'unverified', 'reason': f"no gate path is defined for route {receipt.get('route') if isinstance(receipt, dict) else None}",
                'gates': [], 'rule': VERSION}
    out, failed, uncertified = [], [], []
    for g in gates:
        cert = certificate(g['gate'], encoder, path)
        passed = g['passed']
        if passed is None:
            passed = g['score'] is not None and cert['threshold'] is not None and float(g['score']) >= float(cert['threshold'])
        row = {'gate': g['gate'], 'score': g['score'], 'threshold': cert['threshold'], 'certified': bool(cert['calibrated']),
               'passed': bool(passed)}
        if cert.get('guarantee'): row['guarantee'] = cert['guarantee']
        if not cert['calibrated']: row['why_uncertified'] = cert.get('reason')
        out.append(row)
        if not passed: failed.append(g['gate'])
        elif not cert['calibrated']: uncertified.append(g['gate'])
    if failed:
        return {'level': 'low', 'reason': 'failed on the answer path: ' + ', '.join(failed), 'gates': out, 'rule': VERSION}
    if uncertified or not out:
        return {'level': 'unverified', 'reason': 'no Learn-then-Test certificate for: ' + ', '.join(uncertified or ['(empty path)']),
                'gates': out, 'rule': VERSION}
    return {'level': 'verified', 'reason': 'every gate on the path is certified and passed: ' + '; '.join(
        f"{r['gate']} ({r['guarantee']})" for r in out if r.get('guarantee')), 'gates': out, 'rule': VERSION}


def legacy():
    return os.environ.get('AGERE_CONFIDENCE_RULE', 'certified') == 'legacy'
