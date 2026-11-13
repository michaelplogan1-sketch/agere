"""Bridge from the app's question entry to the tiered core.

try_answer() runs a fresh question through the tiers. A validated answer is returned in the app's result shape.
Anything the core does not answer (no tier fits, the frontier tier says one query cannot answer it, validation
fails) returns None and the caller continues on its existing path, so nothing the app could do before is lost.
"""
import os

from . import VERSION, bottles as bottle_store, ledger as ledger_store, router

TIERS = ('bottles', 'recipes', 'system1', 'slm', 'llm')
TIER_NAMES = {'bottles': 'a saved recipe for this question shape', 'recipes': 'a built-in recipe matched by wording',
              'slm': 'a built-in recipe chosen by System One and filled by the small model', 'llm': 'a recipe written by the frontier model and run by code'}


def enabled():
    return os.environ.get('AGERE_TIERED_CORE', 'off') == 'on'


def _headline(columns, rows):
    if len(rows) == 1:
        return '; '.join(f"{c.replace('_', ' ')}: {v}" for c, v in zip(columns, rows[0])) + '.'
    return f"{len(rows)} rows by {columns[0].replace('_', ' ')}; see the table."


def try_answer(stack, question):
    """-> the app's result dict, or None to continue on the existing path."""
    if not enabled(): return None
    s1_ledger = getattr(getattr(stack, 'ledger', None), 'path', None) or os.environ.get('AGERE_TIERED_S1_LEDGER')
    bottles = bottle_store.Bottles()
    try:
        outcome = router.answer(question, tiers=TIERS, bottles=bottles, bottle_new=True, s1_ledger=s1_ledger)
    except Exception as exc:  # the bridge never blocks a question: any failure falls through to the existing path
        if 'limit' in str(exc).lower(): raise
        return None
    ledger_store.Ledger().write('app', question, outcome, config={'tiers': list(TIERS)})
    if outcome['status'] != 'answered': return None
    columns, rows, tier = outcome['columns'], outcome['rows'], outcome['final_tier']
    usd = sum(s.get('usd', 0.0) for s in outcome['steps'])
    calls = sum(1 for s in outcome['steps'] if s.get('model'))
    recipe = outcome['recipe'] or {}
    checks = ', '.join(c['name'].replace('_', ' ') for c in outcome['validation']['checks'])
    documents = outcome.get('documents') or []
    read_by = {}
    for d in documents: read_by[d.get('by')] = read_by.get(d.get('by'), 0) + 1
    statement = str((recipe.get('read') or {}).get('statement') or '')
    for name, value in (recipe.get('params') or {}).items():
        statement = statement.replace('{' + name + '}', str(value.get('value') if isinstance(value, dict) else value))
    reader_names = {'system1': 'System One', 'slm': 'the small model', 'llm': 'the frontier model'}
    answer = {'status': 'answered', 'answer_type': 'table', 'headline': _headline(columns, rows),
              'table': [dict(zip(columns, r)) for r in rows], 'sources': [], 'missing': [], 'patient_ids': [],
              # A recipe the frontier model has just written passed structural checks only; it is not called checked.
              # Document judgments are model readings, so an answer that rests on them is not called checked either.
              'verification': 'unverified' if tier == 'llm' or documents else 'checked',
              'limitations': [f'Answered by the tiered core using {TIER_NAMES[tier]}. ' + (
                                  f"{len(documents)} documents were selected by SQL and each was read against: \"{statement}\" "
                                  + 'Read by ' + ', '.join(f'{reader_names.get(k, k)} ({n})' for k, n in read_by.items()) + '. Code counted the readings.'
                                  if documents else 'Every number was computed by SQL on the semantic layer.'),
                              f'Deterministic checks passed: {checks}. These check structure, period and totals, not clinical meaning.',
                              *(['This recipe was written by the frontier model for this question and is not yet confirmed against a reference answer.']
                                if tier == 'llm' else [])]}
    sql = recipe.get('sql', '')
    return {'answer': answer, 'results': [], 'new_paid_api_calls': calls, 'workflow': [], 'question': question, 'route': 'tiered_core',
            'graph': {'interpretation': question, 'coverage': 'supported_observation', 'unmet_requirements': [],
                      'nodes': [{'id': 'tiered_0', 'operation': 'sql', 'inputs': [], 'parameters': {'query': sql, 'returns': 'table'},
                                 'purpose': 'Tiered core recipe (executed)'}]},
            'planner_receipt': {'route': 'tiered_core', 'version': VERSION, 'final_tier': tier, 'recipe': recipe, 'steps': outcome['steps'],
                                'escalations': outcome['escalations'], 'validation': outcome['validation'], 'new_paid_api_calls': calls,
                                'known_api_estimate_usd': usd, 'api_cost_known': True, 'frontier_called': tier == 'llm'},
            'planning_economics': {'new_api_estimate_usd': usd, 'known_api_estimate_usd': usd, 'total_cost_known': True,
                                   'scope': 'Tiered core model calls only.'},
            'tiered_core': {'final_tier': tier, 'seconds': outcome['seconds'], 'escalations': outcome['escalations'], 'bottled': outcome.get('bottled'),
                            **({'documents': documents} if documents else {})}}
