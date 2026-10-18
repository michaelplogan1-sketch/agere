"""A plan request end to end: cohort (caller ids, else the existing answer path, else the deterministic candidates for the
program's gap) -> deterministic router -> optional model narrative -> the answer envelope with a `plan` block.

    out = build(semantic_path, question, cohort=lambda q: ws.ask(q, answer_type='patient_set'))
    env = envelope(out)          # {'answer': {'patient_ids': [...]}, 'answer_type': 'plan', 'plan': {...}, ...}
"""
from __future__ import annotations

from . import catalog as catmod, narrative as narr, request as req, router


def _cohort_ids(env):
    a = (env or {}).get('answer')
    if isinstance(a, dict) and isinstance(a.get('patient_ids'), list):
        return [str(x) for x in a['patient_ids']]
    return None


def build(semantic_path, question=None, *, patient_ids=None, program_kind=None, as_of=None, answer_type=None,
          cohort=None, gap_details=None, narrative=False, send_factory=None, catalog_path=None):
    """-> {'plan', 'cohort', 'request'}. cohort: callable(question) -> an answer envelope with answer.patient_ids (the
    product's existing answer path); used only when the request names a cohort and no patient_ids are given."""
    cat = catmod.load(catalog_path)
    parsed = req.parse(question or '', answer_type or 'plan', program_kind=program_kind, cat=cat) if question else None
    kind = program_kind or (parsed or {}).get('program_kind')
    if not kind:
        raise ValueError(f'Which program? The plan request names none of the catalog kinds {catmod.kinds(cat)} '
                         '(pass program_kind)')
    kind = router.resolve_kind(cat, kind)
    info = {'source': None, 'question': (parsed or {}).get('cohort_question') or None, 'patients': 0}
    ids = None
    if patient_ids is not None:
        ids, info['source'] = [str(p) for p in patient_ids], 'caller'
    elif info['question'] and cohort is not None:
        try:
            env = cohort(info['question'])
        except Exception as exc:  # noqa: BLE001 - the deterministic candidates still give a plan
            env, info['cohort_error'] = None, f'{type(exc).__name__}: {exc}'[:300]
        got = _cohort_ids(env)
        info['answer_path'] = {k: (env or {}).get(k) for k in ('pipeline', 'decision', 'interpretation', 'cost_usd', 'status')}
        if got:
            ids, info['source'] = got, 'answer_path'
        else:
            info['fallback_reason'] = info.get('cohort_error') or 'the answer path returned no patient list'
    if ids is None:
        ids = router.candidates(semantic_path, kind, as_of, catalog=cat)
        info['source'] = 'deterministic_candidates'
        info.setdefault('fallback_reason', 'the request names no cohort' if not info['question'] else
                        'cohort path unavailable')
    info['patients'] = len(ids)
    plan = router.route(semantic_path, ids, kind, as_of, gap_details, catalog=cat)
    plan['cohort'] = info
    plan['narrative'] = narr.draft(plan, send_factory=send_factory, enabled=bool(narrative)) if narrative else \
        {'status': 'disabled', 'reason': 'not requested'}
    return {'plan': plan, 'cohort': info, 'request': parsed}


def envelope(out, *, pipeline=None, base=None):
    """The workspace answer envelope for a plan: answer.patient_ids = the patients the plan acts on (not excluded),
    plan = the routed plan (cohort ids, per-patient routing with lineage, tasks, summary)."""
    plan = out['plan']
    acted = [r['patient_id'] for r in plan['patients'] if r['route'] != 'exclude']
    s = plan['summary']
    routes = ', '.join(f'{k} {v}' for k, v in s['by_route'].items()) or 'no patients'
    cost = (plan['cohort'].get('answer_path') or {}).get('cost_usd')
    ncost = (plan.get('narrative') or {}).get('cost_usd')
    total = None if cost is None and ncost is None else round((cost or 0) + (ncost or 0), 6)
    env = dict(base or {})
    env.update({'pipeline': pipeline or env.get('pipeline') or 'plan', 'answer': {'patient_ids': acted}, 'answer_type': 'plan',
                'status': 'answered', 'headline': f"Routed plan for {s['patients']} patients ({plan['program_kind']}): {routes}",
                'decision': 'plan', 'route': 'deterministic_router',
                'interpretation': f"routed plan ({plan['program_kind']}; cohort from {plan['cohort']['source']}; {routes})",
                'cost_usd': total, 'missing': [], 'decline': None,
                'plan': plan, 'cohort_patient_ids': [r['patient_id'] for r in plan['patients']]})
    return env
