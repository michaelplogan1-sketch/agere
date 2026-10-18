"""Optional model-drafted prose for a routed plan: a plan narrative and one outreach script per script template.

The model only writes prose. It sees the plan's summary, the programs and contexts, and per-template gap statistics (no
patient identifiers), and its output is attached as plan['narrative']; routing, patient lists and tasks are never read
back from it. A digest of the routing decisions is taken before and after and the draft is dropped if they differ.

Off unless asked for (enabled=True or AGERE_PLAN_NARRATIVE=on) and a sender exists: a send_factory from the caller (the
product's ledgered sender, as live.answer uses) or, when none is given, nothing -- without one the draft is skipped with
the reason named. Model: AGERE_PLAN_NARRATIVE_MODEL, default Haiku-class (the hybrid core's default analyst model).
"""
from __future__ import annotations

from collections import Counter
import json
import os
import re

from . import router

MAX_TOKENS = 2000
SYSTEM = ('You write concise, plain-language operational prose for a care-management team working from a deterministic, '
          'already-routed enrollment plan built on synthetic data. You never change who is routed where, never add or remove '
          'patients, programs, vendors or numbers, and never invent vendor names: vendor slots are placeholders the customer '
          'configures. Outreach scripts are short, warm, use [FIRST_NAME] / [VENDOR_NAME] / [PRACTICE_NAME] placeholders, '
          'state the reason for the call from the gap, and offer the program without pressure. Reply with ONLY one JSON '
          'object: {"narrative": "...", "scripts": {"<script_template_id>": "..."}}.')


def model_name():
    from ..hybrid.live import DEFAULT_MODEL
    return os.environ.get('AGERE_PLAN_NARRATIVE_MODEL') or DEFAULT_MODEL


def enabled_by_env():
    return os.environ.get('AGERE_PLAN_NARRATIVE', 'off').strip().lower() in ('on', '1', 'true', 'yes')


def brief(plan):
    """What the model sees: aggregate, identifier-free."""
    by_template = {}
    for r in plan['patients']:
        t = r.get('task')
        if not t:
            continue
        b = by_template.setdefault(t['script_template_id'], {'tasks': 0, 'channel': t['channel'], 'program_id': t['program_id'],
                                                             'priorities': Counter(), 'gaps': Counter()})
        b['tasks'] += 1
        b['priorities'][t['priority']] += 1
        g = r['gap']
        for m in (g.get('missing_pillars') or {}).get('value') or []:
            b['gaps'][f'missing {m}'] += 1
        if (g.get('window_open') or {}).get('value') is not None:
            b['gaps']['PT window still open' if g['window_open']['value'] else 'PT window passed'] += 1
    s = plan['summary']
    return {'as_of': plan['as_of'], 'program_kind': plan['program_kind'], 'gap_type': plan['gap_type'],
            'programs': plan['programs'], 'contexts': plan.get('contexts'),
            'summary': {k: s.get(k) for k in ('patients', 'by_route', 'by_program', 'by_vendor_slot', 'by_exclusion_reason',
                                              'by_line_of_business', 'tasks', 'tasks_by_priority', 'missing_pillar_counts')},
            'templates': {k: {**v, 'priorities': dict(v['priorities']), 'gaps': dict(v['gaps'])} for k, v in by_template.items()}}


def _text(response):
    if isinstance(response, str):
        return response
    parts = (response or {}).get('content') or []
    return ''.join(p.get('text', '') for p in parts if isinstance(p, dict) and p.get('type') == 'text')


def _parse(text):
    m = re.search(r'\{.*\}', text or '', re.S)
    if not m:
        raise ValueError('no JSON object in the reply')
    obj = json.loads(m.group(0))
    if not isinstance(obj.get('narrative'), str) or not isinstance(obj.get('scripts', {}), dict):
        raise ValueError('reply is not {narrative, scripts}')
    return obj


def draft(plan, *, send_factory=None, enabled=None, model=None):
    """-> {'status': 'drafted'|'disabled'|'failed', ...}. Never mutates the plan's routing."""
    on = enabled_by_env() if enabled is None else bool(enabled)
    if not on:
        return {'status': 'disabled', 'reason': 'plan narrative is off (enabled=False / AGERE_PLAN_NARRATIVE unset)'}
    if send_factory is None:
        return {'status': 'disabled', 'reason': 'no model sender (no API key / send_factory): the deterministic plan stands alone'}
    if not any(r.get('task') for r in plan['patients']):
        return {'status': 'disabled', 'reason': 'no outreach tasks to script'}
    before = router.digest(plan)
    model = model or model_name()
    body = {'model': model, 'max_tokens': MAX_TOKENS, 'system': SYSTEM,
            'messages': [{'role': 'user', 'content': 'Routed plan (aggregate):\n' + json.dumps(brief(plan), default=str)}]}
    try:
        with send_factory() as send:
            response, cost, cache_hit = send(body)
        obj = _parse(_text(response))
    except Exception as exc:  # noqa: BLE001 - the deterministic plan never depends on the draft
        return {'status': 'failed', 'model': model, 'error': f'{type(exc).__name__}: {exc}'[:300]}
    if router.digest(plan) != before:
        return {'status': 'failed', 'model': model, 'error': 'routing changed during drafting; draft dropped'}
    templates = {r['task']['script_template_id'] for r in plan['patients'] if r.get('task')}
    return {'status': 'drafted', 'model': model, 'cost_usd': cost, 'cache_hit': bool(cache_hit),
            'narrative': obj['narrative'], 'scripts': {k: v for k, v in (obj.get('scripts') or {}).items() if k in templates},
            'routing_digest': before, 'authority': 'prose only; routing and patient lists come from the deterministic router'}
