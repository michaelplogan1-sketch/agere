"""Shared runtime for the deterministic specialist families that follow the cohort_families.py pattern
(dq_families.py: delivery / identity / data-quality questions; vbc_families.py: core value-based-care metrics).

A family module provides VERSION, ROUTE, FAMILIES, parse(question, as_of) -> parsed | None, validate(spec) -> spec and
render(spec, as_of) -> SQL whose first line is '-- DEFINITION: ...'. This module turns a parsed spec into the one-node
analysis graph the runtime executes (read-only SELECT over the semantic layer), the receipt the planner records, the
learning-store reuse of a verified answer for the exact question, and the post-answer recording of that pair.
No model is called anywhere: every slot comes from the question's own definition text (or a recorded convention default,
which lowers the claim's certificate score). Synthetic data only.
"""
from __future__ import annotations

import copy, json, re

_PERSON_WORD = re.compile(r'\bpopulation\b', re.I)  # sql_query reads the bare word as an upstream-population request


def split_definition(text):
    m = re.search(r'\bdefinition\s*:', text or '', re.I)
    return (text[:m.start()], text[m.end():]) if m else (text or '', '')


def clean(text):
    t = (text or '').replace('–', '-').replace('—', '-').replace('−', '-').replace('≥', '>=').replace('≤', '<=')
    t = t.replace('’', "'").replace('‘', "'")
    return ' '.join(t.split())


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def statement(module, spec, as_of):
    p = json.dumps(spec['parameters'], sort_keys=True, separators=(',', ':'))
    text = f"{module.ROUTE.replace('_', ' ')} {spec['family']}@{module.VERSION.split('/')[-1]} as of {as_of}: {p}"
    return _PERSON_WORD.sub('people', text)[:3000]


def to_graph(module, spec, as_of):
    sql = module.render(spec, as_of)
    return {'interpretation': statement(module, spec, as_of), 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [],
                       'parameters': {'query': sql, 'returns': spec['returns']},
                       'purpose': f"{module.ROUTE} {spec['family']} ({module.VERSION})"}]}


def receipt_for(module, spec, as_of, parsed):
    return {'family': spec['family'], 'version': module.VERSION, 'parameters': spec['parameters'], 'returns': spec['returns'],
            'statement': statement(module, spec, as_of), 'defaults': list(parsed.get('defaults') or []),
            'model_filled': [], 'has_definition': bool(parsed.get('has_definition'))}


def plan(module, question, as_of):
    """-> {'graph', 'receipt', 'calls': [], 'spec'} or None. Never raises on a question the family cannot represent."""
    try:
        parsed = module.parse(question, as_of)
        if parsed is None: return None
        spec = module.validate({'family': parsed['family'], 'parameters': parsed['parameters'], 'returns': parsed['returns']})
        return {'graph': to_graph(module, spec, as_of), 'receipt': receipt_for(module, spec, as_of, parsed), 'calls': [], 'spec': spec}
    except (ValueError, KeyError, TypeError):
        return None


def reuse(module, path, question):
    """A verified family answer recorded for this exact question (learning store), when its version matches."""
    try:
        from . import learning
        known = learning.lookup(path, question)
    except Exception:  # noqa: BLE001 - the cache never blocks the answer
        return None
    if not known or known.get('plan_kind') != 'sql': return None
    fam = (known.get('plan') or {}).get(module.ROUTE)
    if not isinstance(fam, dict) or fam.get('version') != module.VERSION: return None
    return known


def record(module, path, question, planner_receipt, answer):
    """After an answered family question, the (question -> family SQL) pair becomes a verified pair the learned cache
    reuses (learning.record_verified). Deterministic code wrote the SQL; no model judgment is captured."""
    pr = planner_receipt or {}
    fam = pr.get(module.ROUTE)
    if pr.get('route') != module.ROUTE or not fam or (answer or {}).get('status') != 'answered': return False
    query = (answer or {}).get('query')
    if not query: return False
    from . import learning
    summary = {'family': fam['family']}
    for k in ('count', 'value'):
        if (answer or {}).get(k) is not None: summary[k] = answer[k]
    learning.record_verified(path, question, 'sql', {'query': query, 'returns': fam['returns'], module.ROUTE: fam},
                             summary, module.ROUTE, [], f'deterministic_{module.ROUTE}')
    return True


def record_route(path, question, planner_receipt, answer):
    """Post-answer hook (stack_runtime.compose): record a dq_family / vbc_family answer through its module's record()."""
    route = (planner_receipt or {}).get('route')
    if route == 'dq_family':
        from . import dq_families as mod
    elif route == 'vbc_family':
        from . import vbc_families as mod
    else:
        return False
    return mod.record(path, question, planner_receipt, answer)


def score(info):
    """Parse confidence of a family claim from its receipt (same form as cascade.cohort_family_score): each convention
    default the parser applied lowers it; a question without an inline definition loses 0.05."""
    info = info or {}
    s = 1.0 - 0.08 * len(info.get('defaults') or [])
    if not info.get('has_definition'): s -= 0.05
    return round(max(0.0, min(1.0, s)), 4)


def copy_spec(spec):
    return copy.deepcopy(spec)


# ---- small parsing helpers shared by the family parsers ---------------------------------------------------------------
YEAR = re.compile(r'\b(20[2-3]\d)\b')
CODE_RANGE = re.compile(r'\b([A-Z]?\d{4,5})\s*-\s*([A-Z]?\d{4,5})\b|\b([A-Z]\d{4}|\d{5})\b')


def years_in(text):
    return [int(y) for y in YEAR.findall(text or '')]


def codes_in(text):
    """'99381-99397' -> ['99381-99397']; 'G0438 or G0439' -> ['G0438', 'G0439'] (same-length ranges only)."""
    out = []
    for m in CODE_RANGE.finditer(text or ''):
        if m.group(1):
            a, b = m.group(1), m.group(2)
            if len(a) == len(b) and a[:1].isalpha() == b[:1].isalpha() and a <= b: out.append(f'{a}-{b}')
        else:
            out.append(m.group(3))
    return list(dict.fromkeys(out))


def codes_clause(col, codes):
    parts, singles = [], []
    for c in codes:
        m = re.fullmatch(r'([A-Z]?\d{4,5})-([A-Z]?\d{4,5})', c)
        if m: parts.append(f"{col} BETWEEN {q(m.group(1))} AND {q(m.group(2))}")
        else: singles.append(c)
    if singles: parts.append(f"{col} IN ({', '.join(q(c) for c in singles)})")
    return '(' + ' OR '.join(parts) + ')' if parts else '0'
