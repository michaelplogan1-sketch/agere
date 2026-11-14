"""Fast, reproducible opening brief over the received warehouse.

These are defined review checks, not a claim to discover every important issue.
Each card keeps its executable result so all pathways start from the same answer.
"""
import json
import sqlite3
import time
import threading
from pathlib import Path
from types import SimpleNamespace
from . import shared_planner as S
from ..deliverables import answer_context as A
from ..deliverables.receipts import fingerprint

VERSION = 'chief-of-staff/1'
_lock = threading.RLock()
CHECKS = [
    ('claims', 'Claims that need a closer look', 'Count claims where final_status = "denied" or final_status = "denied_upheld" by payer',
     'Final recorded denials may warrant a revenue-cycle review. This does not establish recoverable revenue.', 'Review denials'),
    ('access', 'A chance to reconnect', 'Count appointments where final_status = "no_show" by clinician_id',
     'Recorded no-shows identify a starting point for access review. Patient preference and current need still require review.', 'Explore access'),
    ('utilization', 'The return journey', 'Count admissions where readmitted_30d = 1 by organization_id',
     'Examine recorded readmissions and discharge follow-through. This is an observed count, without risk adjustment or a causal conclusion.', 'Review transitions'),
    ('attribution', 'Who owns the next step?', 'List patients where pcp_clinician_id is null',
     'Missing PCP attribution can interrupt follow-through. A missing warehouse value does not prove the patient has no PCP.', 'Resolve attribution'),
    ('spend', 'Where spending is concentrated', 'Total allowed_cents for claims by payer',
     'Start a payer-level spending review. Allowed amounts are in cents, across all received service dates; they are not PMPM or savings.', 'Explore spending'),
]


def cached(path):
    with _lock:
        return _cached(path)


def _cached(path):
    started = time.perf_counter()
    source = A.source_identity(path)
    target = A.root(path).parent / 'chief-staff-briefing.json'
    dependencies = [Path(__file__).with_name(n).read_text() for n in
                    ('shared_planner.py', 'request_plan.py', 'request_language.py', 'concepts.py', 'drug_classes.py',
                     'workflow_contract.py', 'sql_query.py')]
    identity = fingerprint([VERSION, S.VERSION, source, CHECKS, dependencies])
    if target.exists():
        try:
            saved = json.loads(target.read_text())
            if saved.get('identity') == identity:
                # A relocated/partial installation can retain the cached cards
                # while their saved answers are missing, stale or corrected.
                for card in saved.get('cards') or []:
                    context = A.load(path, card['context_id'], current=True)
                    if context['question'] != card['follow_up'] or context['source'] != source:
                        raise ValueError('Briefing card differs from its saved answer')
                return {**saved, 'cache_hit': True}
        except (OSError, ValueError, KeyError, sqlite3.Error):
            pass
    cards, checks = [], []
    stack = SimpleNamespace(path=path, allow_live=False)
    for domain, title, question, why, action in CHECKS:
        try:
            # The fixed review checks must compile locally; never hide a model
            # call inside a page load or change a check to fit a new schema.
            result = S.compose(stack, question, model=lambda *a, **k: (_ for _ in ()).throw(ValueError('Review check needs a schema update')))
            answer = result['answer']
            if answer.get('status') != 'answered' or answer.get('missing'):
                raise ValueError('Check did not produce a complete answer')
            rows = answer.get('table') or []
            if domain == 'attribution':
                value = len(answer.get('patient_ids') or [])
            else:
                field = 'sum_allowed_cents' if domain == 'spend' else 'count'
                value = sum(r.get(field) or 0 for r in rows)
            # A zero is still an observed check, but not a call to action.
            checks.append({'domain': domain, 'status': 'computed', 'value': value})
            if not value:
                continue
            context = A.capture(path, question, result)
            cards.append({'card_id': fingerprint([identity, domain])[:20], 'topic': domain, 'kind': 'observed review opportunity',
                          'title': title, 'headline': why, 'value': value, 'unit': 'allowed cents' if domain == 'spend' else 'patients' if domain == 'attribution' else 'records',
                          'follow_up': question, 'why': why, 'action': action, 'context_id': context['id'],
                          'patients': len(answer.get('patient_ids') or []), 'source': source,
                          'evidence': {'definition': question, 'rows': rows[:10], 'status': answer['status']}})
        except (ValueError, OSError, sqlite3.Error) as exc:
            checks.append({'domain': domain, 'status': 'unavailable', 'reason': str(exc)})
    if A.source_identity(path) != source:
        raise ValueError('Warehouse changed while preparing the briefing; retry')
    result = {'schema': VERSION, 'identity': identity, 'cards': cards, 'checks': checks, 'source': source,
              'as_of': source.get('as_of'), 'api_calls': 0, 'cache_hit': False,
              'elapsed_seconds': round(time.perf_counter()-started, 2),
              'method': 'Five explicit review checks across claims, access, utilization, attribution and spending; all received dates. No causal, clinical eligibility or completeness claim.'}
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(result, default=str))
    temp.replace(target)
    return result


def open_pathway(path, body):
    if set(body) != {'card_id', 'pathway'} or body['pathway'] not in ('deliverable', 'simulation', 'monitor'):
        raise ValueError('Choose a briefing card and a supported pathway')
    card = next((c for c in cached(path)['cards'] if c['card_id'] == body['card_id']), None)
    if not card:
        raise ValueError('Briefing changed. Refresh it before preparing work.')
    c = A.load(path, card['context_id'], current=True)
    kind = body['pathway']
    step = {'index': 0, 'kind': kind, 'title': card['title'], 'unresolved': [], 'status': 'planned',
            'origin': {'kind': 'briefing_selection', 'card_id': card['card_id']}, 'quote': None}
    step.update({'template': 'command'} if kind == 'deliverable' else {'program': 'custom'} if kind == 'simulation' else
                {'mode': 'answer', 'scope': 'query', 'outputs': [s['alias'] for s in c['planner_receipt']['request_contract']['request']['select']]})
    from .workflow_contract import validate_selection
    validate_selection(step)
    result = {k: c[k] for k in ('answer', 'graph', 'results', 'planner_receipt', 'planning_economics')}
    result['workflow'] = [step]
    result['execution_snapshot'] = c['source']
    context = A.capture(path, c['question'], result)
    from ..deliverables.workflow import materialize
    return {'action_context': context, 'workflow': materialize(path, context['id'])}
