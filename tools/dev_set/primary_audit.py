"""Supplemental dev-only audit of explicit primary values, never incidental table text.

The published scorer is unchanged. Unverified items are not successes; coverage
of this limited audit must be reported separately from whole-question accuracy.
"""
import math
from datetime import date

from .run import close


def audit(question, response):
    if question.get('split') not in ('dev', 'dev2', 'dev3', 'paraphrase', 'authored'):
        raise ValueError('Primary audit is development-only; closed evaluations are refused')
    kind = question['answer_type']
    if kind not in ('date', 'yes_no', 'count', 'value'):
        return {'verdict': 'unverified', 'reason': 'This answer type needs its own independent audit'}
    answer = response.get('answer') if isinstance(response.get('answer'), dict) else {}
    typed = answer.get('primary_answer_type') or answer.get('answer_type')
    value = answer.get('primary_value') if 'primary_value' in answer else answer.get('value')
    expected = question['key'].get('answer')
    if kind == 'yes_no' and typed == 'yes_no' and type(value) is bool and type(answer.get('yes_no')) is bool and value != answer['yes_no']:
        return {'verdict': 'unverified', 'reason': 'Conflicting explicit primary booleans'}
    if kind == 'yes_no' and type(expected) is not bool:
        return {'verdict': 'unverified', 'reason': 'No boolean reference for this audit'}
    if kind == 'date' and not isinstance(expected, str):
        return {'verdict': 'unverified', 'reason': 'No date reference for this audit'}
    if kind in ('count', 'value') and (type(expected) not in (float, int) or not math.isfinite(expected)):
        return {'verdict': 'unverified', 'reason': 'No numeric scalar reference for this audit'}
    if kind == 'yes_no' and type(answer.get('yes_no')) is bool:
        typed, value = 'yes_no', answer['yes_no']
    valid = False
    if kind == 'date' and typed == 'date' and isinstance(value, str):
        try:
            valid = date.fromisoformat(value).isoformat() == value
        except ValueError:
            pass
    elif kind == 'yes_no':
        valid = typed == 'yes_no' and type(value) is bool
    elif kind in ('count', 'value'):
        valid = typed in ('count', 'value') and type(value) in (float, int) and math.isfinite(value)
    if not valid:
        return {'verdict': 'unverified', 'reason': 'No primary value of the requested type; prose and supporting tables are not substitutes'}
    correct = value == expected if kind in ('date', 'yes_no') else close(value, expected, question['key'].get('tolerance'))
    return {'verdict': 'correct' if correct else 'wrong', 'actual': value, 'expected': expected,
            'reason': 'Compared the typed primary value only'}
