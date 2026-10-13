"""Verification ladder for model-written SQL: no model output reaches a user unchecked.

1. lint     - free, static: every stream, payload field, concept id and enumerated value the query names
              must exist in the received data (json_extract on a missing field silently returns NULL,
              which turns hallucinations into confident wrong numbers).
2. sanity   - free, on the result: whole-roster answers to filtered questions, empty results, money
              in cents when dollars were asked, near-total "missing" counts.
3. agree    - an independent second model writes its own query; matching results (values compared
              without regard to column names) are accepted.
4. judge    - on disagreement a frontier model sees both queries, their results and the lint notes,
              and writes the final query, which must pass 1 and 2 again.
Every verified answer is captured for learning (see learning.py).
"""
import json, re

from . import concepts

STREAM_RX = re.compile(r"stream\s*(?:=\s*'([a-z0-9_]+)'|in\s*\(([^)]*)\))", re.I)
FIELD_RX = re.compile(r"json_extract\(\s*(?:\w+\.)?payload\s*,\s*'\$\.([A-Za-z0-9_]+)'\s*\)(?:\s*(=|<>|!=|in)\s*(\([^)]*\)|'[^']*'))?", re.I)
CONCEPT_RX = re.compile(r"\b(?:concept|type_concept)\s*(?:=\s*'([a-z0-9_]+)'|in\s*\(([^)]*)\))", re.I)


def _names(single, many):
    return [single] if single else re.findall(r"'([^']+)'", many or '')


def lint(profile, sql):
    """Static problems with a query against the received data dictionary (empty list = clean)."""
    problems, streams = [], profile['streams']
    named = [s for m in STREAM_RX.finditer(sql) for s in _names(m.group(1), m.group(2))]
    for s in named:
        if s not in streams:
            close = [x for x in streams if s.split('_')[0] in x][:6]
            problems.append(f"stream '{s}' does not exist" + (f" (similar: {', '.join(close)})" if close else ''))
    scope = [s for s in named if s in streams] or list(streams)
    for m in FIELD_RX.finditer(sql):
        field, op, literal = m.group(1), m.group(2), m.group(3)
        owners = [s for s in scope if field in streams[s]['fields']]
        if not owners:
            problems.append(f"field '{field}' is not present in stream(s) {', '.join(scope[:4])}")
            continue
        populated = sum(streams[s]['fields'][field].get('populated', 0) for s in owners)
        records = sum(streams[s]['records'] for s in owners)
        if populated == 0:
            problems.append(f"field '{field}' is never populated in {', '.join(owners)}")
        elif records and populated / records < 0.02 and re.search(r"'\$\." + field + r"'\s*\)\s*is\s+null", sql, re.I):
            problems.append(f"field '{field}' is populated in only {populated} of {records} records of {', '.join(owners)}")
        if op and literal and literal.startswith(("'", '(')):
            values = set().union(*[set((streams[s]['fields'][field].get('values') or {})) for s in owners])
            asked = re.findall(r"'([^']*)'", literal)
            if values and len(values) <= 200:
                missing = [v for v in asked if v not in values]
                if missing:
                    problems.append(f"value(s) {missing} never occur in '{field}'; observed: {sorted(values)[:15]}")
    from .drug_classes import CLASSES
    for m in re.finditer(r"drug_class\s*(?:=\s*'([a-z0-9_]+)'|in\s*\(([^)]*)\)|like\s*'([^']*)')", sql, re.I):
        if m.group(3) is not None:
            problems.append(f"drug_class must be compared exactly (= or IN); valid classes: {', '.join(sorted(CLASSES))}")
        for c in _names(m.group(1), m.group(2)):
            if c not in CLASSES: problems.append(f"drug_class '{c}' is not a known class; valid: {', '.join(sorted(CLASSES))}")
    known = set(concepts.CONDITIONS) | set(concepts.MEASUREMENTS) | set(concepts.ENCOUNTERS)
    for m in CONCEPT_RX.finditer(sql):
        for c in _names(m.group(1), m.group(2)):
            if c not in known: problems.append(f"concept '{c}' is not a library concept")
    return problems


def sanity(question, sql, probe, roster):
    """Result-level red flags. Returns (fatal problems, warnings)."""
    fatal, warn = [], []
    q = question.lower()
    rows, cols = probe.get('rows') or [], probe.get('columns') or []
    first = (list(rows[0].values()) if isinstance(rows[0], dict) else list(rows[0])) if rows else []
    scalar = first[0] if len(rows) == 1 and len(cols) == 1 and first else None
    filtered = re.search(r'\bwhere\b|\bjoin\b', sql, re.I)
    if 'patient' in ' '.join(cols).lower() or re.search(r'select\s+distinct\s+\w*\.?patient_id', sql, re.I):
        if len(rows) == roster and filtered:
            fatal.append(f'returned every patient on the roster ({roster}) although the question filters; check the join')
    if isinstance(scalar, (int, float)) and re.search(r'count\s*\(\s*distinct\s+\w*\.?patient_id', sql, re.I) and scalar == roster and filtered:
        fatal.append(f'counted the whole roster ({roster}) although the question filters; check the join')
    if not rows or scalar == 0:
        warn.append('empty or zero result; confirm the filters match how the data records it')
    wants_dollars = re.search(r'\bdollars?\b|\$|\busd\b|\bhow much\b|\brevenue\b|\bspend\b', q)
    if wants_dollars and re.search(r"_cents'\)", sql) and not re.search(r'/\s*100(?:\.0)?\b', sql):
        fatal.append('the question asks for money in dollars but the query returns cents; divide by 100.0')
    if re.search(r'\bpercent|\bshare\b|\bproportion\b|%|\brate\b', q) and isinstance(scalar, (int, float)) and 0 < scalar <= 1 \
            and not re.search(r'100(?:\.0)?\s*\*', sql):
        warn.append('looks like a fraction; percentages are reported on 0-100')
    return fatal, warn


def signature(result):
    """What an answer says, independent of column names or row order."""
    def norm(v):
        return round(float(v), 4) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    if 'value' in result: return ('v', norm(result['value']))
    if 'patient_ids' in result: return ('p', tuple(result['patient_ids']))
    rows = [list(r.values()) if isinstance(r, dict) else list(r) for r in (result.get('rows') or [])]
    return ('t', tuple(sorted((tuple(sorted((json.dumps(norm(v), default=str) for v in r), key=str)) for r in rows), key=str)))
