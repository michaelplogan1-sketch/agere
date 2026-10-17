"""The analysis contract a model writes before any SQL, and its deterministic validation.

    {"answer_type": "count|value|patient_set|table|rows",
     "grain": {"entity": "person|chart|claim|claim_line|stay|visit|fill|appointment|record|measurement|member_month|other",
               "key": "what one counted unit is, e.g. person+service_date"},
     "population": "who is in scope, with its filters",
     "numerator": "what is counted (rates: the qualifying subset)",
     "denominator": null | "the base of a share or rate",
     "window": {"start": "YYYY-MM-DD"|null, "end": "YYYY-MM-DD"|null, "anchor": "table.column the window applies to"},
     "as_of": "YYYY-MM-DD",
     "perspective": "which sources count (payer claims, EHR, all sources...)",
     "exclusions": ["..."],
     "group_by": ["result column names that key a table or rows answer, in order"],
     "row_limit": null | N (a "top N" table returns at most N rows),
     "unit": "count|percent|days|usd|ratio|per_1000|...",
     "metric_id": null | "a governed metric whose definition is exactly this contract",
     "unresolved": ["meanings the question and documentation leave open"]}

A `rows` answer (a step of a clinical workflow task) is a list of rows with the step's declared output columns: the
key columns (strings, e.g. patient_id or patient_id + drug) plus typed attributes (int, number, list). group_by must be
exactly the step's key columns.

`unresolved` non-empty means the question needs clarification: the run abstains instead of guessing.
"""
from __future__ import annotations

from datetime import date

ANSWER_TYPES = ('count', 'value', 'patient_set', 'table', 'rows')
ENTITIES = ('person', 'chart', 'claim', 'claim_line', 'stay', 'visit', 'fill', 'appointment', 'record', 'measurement',
            'member_month', 'adjudication', 'prescription', 'referral', 'other')
RATE_UNITS = ('percent', 'ratio', 'per_1000', 'rate')
REQUIRED = ('answer_type', 'grain', 'population', 'numerator', 'window', 'as_of', 'unit')


def _date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def validate(contract, question):
    """Problems with a contract, as short strings (empty = valid). question: {id, answer_type, as_of?}."""
    if not isinstance(contract, dict):
        return ['contract is not an object']
    at = contract.get('answer_type')
    # a median/mean/sum value has no numerator; every other answer type counts something
    issues = [f'missing {k}' for k in REQUIRED if contract.get(k) in (None, '', {}, []) and not (k == 'numerator' and at == 'value')]
    if at not in ANSWER_TYPES:
        issues.append(f'answer_type {at!r} is not one of {ANSWER_TYPES}')
    elif question.get('answer_type') and at != question['answer_type']:
        issues.append(f"answer_type {at} but the question asks for {question['answer_type']}")
    grain = contract.get('grain') or {}
    if not isinstance(grain, dict) or grain.get('entity') not in ENTITIES:
        issues.append(f"grain.entity must be one of {ENTITIES}")
    unit = str(contract.get('unit') or '').lower()
    if unit in RATE_UNITS and not contract.get('denominator'):
        issues.append(f'unit {unit} needs a denominator')
    if at == 'table' and not contract.get('group_by'):
        issues.append('a table answer needs group_by (the key columns)')
    if at == 'rows' and question.get('keys') and list(contract.get('group_by') or []) != list(question['keys']):
        issues.append(f"group_by must be the step's key columns {question['keys']}")
    if at not in ('table', 'rows') and contract.get('group_by'):
        issues.append(f'group_by is only for table answers, not {at}')
    window = contract.get('window') or {}
    if not isinstance(window, dict):
        issues.append('window must be an object')
        window = {}
    start, end, as_of = _date(window.get('start')), _date(window.get('end')), _date(contract.get('as_of'))
    if contract.get('as_of') and as_of is None:
        issues.append('as_of is not a date')
    for name, raw, d in (('start', window.get('start'), start), ('end', window.get('end'), end)):
        if raw not in (None, '') and d is None:
            issues.append(f'window.{name} is not a date')
    if start and end and start > end:
        issues.append('window starts after it ends')
    if end and as_of and end > as_of:
        issues.append('window ends after as_of')
    q_as_of = _date(question.get('as_of'))
    if q_as_of and as_of and as_of != q_as_of:
        issues.append(f"as_of {as_of} but the question says {q_as_of}")
    if (start or end) and not window.get('anchor'):
        issues.append('window has dates but no anchor column')
    rl = contract.get('row_limit')
    if rl is not None and (isinstance(rl, bool) or not isinstance(rl, int) or rl < 1):
        issues.append('row_limit must be a positive integer or null')
    if not isinstance(contract.get('unresolved', []), list) or not isinstance(contract.get('exclusions', []), list):
        issues.append('unresolved and exclusions must be lists')
    return issues


def needs_clarification(contract):
    return bool(isinstance(contract, dict) and contract.get('unresolved'))
