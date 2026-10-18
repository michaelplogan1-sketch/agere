"""One box for everything: route a free-text request to the right layer.

    question   -> Workspace.ask (specialists first, analysts last)
    plan / act -> Workspace.plan (routed plan, action backbone, agents)        [Workspace.ask already dispatches these]
    simulate   -> Workspace.simulate (population simulator, arms vs do-nothing)
    deliverable-> deliverables.compose (frontier model designs, stack tools supply every number)

Deterministic rules decide; nothing here calls a model. An explicit answer_type always means a question.
"""
from __future__ import annotations

import re

_SIMULATE = re.compile(r'\b(?:simulat\w*|what (?:would|will) happen if|what if we|project (?:the )?(?:impact|savings|outcomes?)|'
                       r'forecast (?:the )?(?:impact|savings)|is it worth|would it pay|roi of|return on)\b', re.I)
_DELIVERABLE = re.compile(r'\b(?:build|make|create|design|draft|write|generate|prepare|produce|put together)\b[^.?!]{0,80}?\b'
                          r'(?:deck|story|brief(?:ing)?|report|one[- ]pager|memo|map|simulator|war room|dashboard|terminal|page|'
                          r'cards?|statement|case|presentation|narrative|letter|messages?|scripts?|playbook|game|tool|app|'
                          r'visuali[sz]ation|infographic|summary|deliverable)\b', re.I)


def classify(text, answer_type=None):
    """-> 'question' | 'plan' | 'simulate' | 'deliverable'."""
    if answer_type not in (None, '', 'plan'):
        return 'question'
    q = str(text or '')
    from .plans.request import is_plan_request
    if _DELIVERABLE.search(q) and not re.search(r'\b(?:plan to (?:enroll|support|sign)|enroll (?:them|these)|sign (?:them|these) up)\b', q, re.I):
        return 'deliverable'
    if _SIMULATE.search(q):
        return 'simulate'
    if is_plan_request(q, answer_type):
        return 'plan'
    return 'question'


def handle(ws, text, *, answer_type=None, live=True, **kw):
    """Dispatch a request to its layer. -> the layer's envelope with 'intent' set."""
    kind = classify(text, answer_type)
    if kind == 'deliverable':
        from .deliverables import compose
        out = compose(ws, text, live=live)
    elif kind == 'simulate':
        from .plans.request import infer_kind
        program = infer_kind(text) or 'pharmacy_gdmt'
        out = ws.simulate(program, **kw)
    else:
        out = ws.ask(text, answer_type=answer_type, live=live)
    if isinstance(out, dict):
        out = {**out, 'intent': kind}
    return out
