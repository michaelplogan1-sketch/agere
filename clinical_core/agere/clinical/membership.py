"""Retained membership: the exact patients a patient-level count, rate numerator or grouped table counted.

A count answer used to leave no saved cohort, so every follow-up ("of those, how many ...") had nothing to narrow.
derive() rewrites the executed scalar/grouped SELECT into the SELECT DISTINCT of the very expression it counted and
runs it read-only under the same population scope. The ids are kept only when they provably are the counted set:
  * COUNT(DISTINCT <x>.patient_id)                 -> counted patients (a count must equal the number of ids);
  * COUNT(DISTINCT CASE WHEN c THEN <x>.patient_id END) in a value -> the rate's numerator patients;
  * COUNT(*) over the patient roster or a staged step -> counted patients (count must equal the number of ids);
  * a grouped table with one patient count column    -> union of the groups (max <= ids <= sum of the column).
Event/row counts (claims, diagnoses, appointments, technicians) never qualify: their rows are not patients, so they
stay non-patient scopes. Nothing here widens an answer: ids are stored with the answer and need not be shown.
"""
import re

VERSION = 'retained-membership/1'
_STOP = r'(?:where|join|left|right|inner|outer|cross|natural|group|order|limit|having|window|union|intersect|except|on|using)'


def _code(sql):
    return re.sub(r'--[^\n]*', ' ', sql).strip().rstrip(';').strip()


def _scan(text):
    """Yield (index, depth) for every character outside string/identifier quotes."""
    depth, i, n = 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"`":
            j = i + 1
            while j < n:
                if text[j] == ch:
                    if j + 1 < n and text[j + 1] == ch: j += 2; continue
                    break
                j += 1
            i = j + 1; continue
        if ch == '(': depth += 1
        elif ch == ')': depth -= 1
        yield i, depth if ch != '(' else depth - 1
        i += 1


def _top(text, pattern):
    """Start offsets of a keyword pattern at parenthesis depth 0 (outside quotes)."""
    shallow = {i for i, d in _scan(text) if d == 0}
    return [m.start() for m in re.finditer(pattern, text, re.I) if m.start() in shallow]


def _close(text, start):
    """Offset of the parenthesis closing the one at text[start], else -1."""
    depth = 0
    for i, _ in _scan(text):
        if i < start: continue
        if text[i] == '(': depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0: return i
    return -1


def _split_with(code):
    """WITH a AS (...), b AS (...) <main> -> ([(name, body)], main offset) or None."""
    m = re.match(r'(?is)with\s+', code)
    if not m or re.match(r'(?is)with\s+recursive\b', code): return None
    pos, ctes = m.end(), []
    while True:
        h = re.compile(r'(?is)\s*("?[A-Za-z_][\w]*"?)\s*(?:\([^()]*\))?\s*as\s*(?:not\s+materialized\s*|materialized\s*)?\(').match(code, pos)
        if not h: return None
        open_at = h.end() - 1
        end = _close(code, open_at)
        if end < 0: return None
        ctes.append((h.group(1).strip('"'), code[open_at + 1:end]))
        nxt = re.compile(r'\s*,').match(code, end + 1)
        if not nxt: return ctes, end + 1
        pos = nxt.end()


def _items(select_list):
    cuts = [i for i, d in _scan(select_list) if d == 0 and select_list[i] == ',']
    bounds = [-1, *cuts, len(select_list)]
    return [select_list[a + 1:b].strip() for a, b in zip(bounds, bounds[1:])]


def _distinct_arg(text):
    """The argument of the first COUNT(DISTINCT ...) whose argument is a patient_id expression, else None."""
    for m in re.finditer(r'(?is)\bcount\s*\(\s*distinct\s+', text):
        end = _close(text, text.index('(', m.start()))
        if end < 0: continue
        arg = text[m.end():end].strip()
        if re.search(r'(?i)(?:^|[.\s"])patient_id"?\s*(?:end\s*)?$', arg): return arg
    return None


def _rewrite_select(code, returns, ctes):
    """(membership SQL, role, counted column index) for one plain SELECT, or None."""
    if not re.match(r'(?is)select\b', code) or _top(code, r'\b(?:union|intersect|except)\b'): return None
    froms = _top(code, r'\bfrom\b')
    if not froms: return None
    head = re.sub(r'(?is)^select\s+(?:all\s+)?', '', code[:froms[0]])
    if re.match(r'(?is)distinct\b', head): return None
    tail_at = min([*_top(code, r'\b(?:group\s+by|order\s+by|limit|window)\b'), len(code)])
    if _top(code, r'\b(?:having|limit)\b'): return None  # filtered groups/rows: the union would include dropped ones
    if returns != 'table' and _top(code, r'\bgroup\s+by\b'): return None
    relation = code[froms[0]:tail_at]
    items = _items(head)
    picks = []
    for i, item in enumerate(items):
        arg = _distinct_arg(item)
        bare = re.fullmatch(r'(?is)count\s*\(\s*distinct\s+.+?\)\s*(?:as\s+\S+|"[^"]+"|\w+)?', item)
        if arg and (returns == 'value' or bare):
            numerator = bool(re.match(r'(?is)case\b', arg))
            picks.append((i, arg, 'numerator' if numerator and returns == 'value' else 'counted' if returns != 'value' else 'denominator'))
        elif returns in ('count', 'table') and re.fullmatch(r'(?is)count\s*\(\s*(?:\*|1)\s*\)\s*(?:as\s+\S+|"[^"]+"|\w+)?', item):
            source = re.match(r'(?is)from\s+("?[A-Za-z_]\w*"?)(?:\s+(?:as\s+)?(?!' + _STOP + r'\b)("?[A-Za-z_]\w*"?))?', relation)
            if not source: continue
            table = source.group(1).strip('"')
            if table != 'patient' and table not in ctes: continue  # a row of any other relation is not a patient
            picks.append((i, f'{(source.group(2) or source.group(1)).strip(chr(34))}.patient_id', 'counted'))
    if returns == 'value':
        picks = [p for p in picks if p[2] == 'numerator'] or picks
    if not picks or (returns != 'value' and len(picks) != 1): return None
    index, arg, role = picks[0]
    return f'SELECT DISTINCT {arg} AS patient_id {relation}', role, index


def rewrite(sql, returns, outer=frozenset()):
    """Membership SELECT for a scalar or grouped patient count: (sql, role, column index) or None."""
    code = _code(sql)
    if re.match(r'(?is)with\b', code):
        split = _split_with(code)
        if not split: return None
        ctes, at = split
        # Staged steps qualify for COUNT(*); a population-scoping copy of a catalog table (SELECT * FROM main.t) does not.
        names = {n for n, b in ctes if n == 'patient' or not re.match(r'(?is)\s*select\s+\*\s+from\s+main\.', b)} | set(outer)
        main = code[at:].strip()
        star = re.fullmatch(r'(?is)select\s+\*\s+from\s+"?([A-Za-z_]\w*)"?', main)
        if star and star.group(1) in names:
            inner = rewrite(dict(ctes)[star.group(1)], returns, names)
            if not inner: return None
            return f'{code[:at].rstrip()}, agere_members AS ({inner[0]}) SELECT patient_id FROM agere_members', inner[1], inner[2]
        inner = _rewrite_select(main, returns, names)
        return None if not inner else (f'{code[:at].rstrip()} {inner[0]}', inner[1], inner[2])
    return _rewrite_select(code, returns, set(outer))


def derive(path, params, report, population=None, target=None):
    """{'patient_ids', 'role', 'query'} for the patients an executed count/value/grouped table counted, else None."""
    from . import sql_query
    returns = report.get('returns')
    if returns not in ('count', 'value', 'table') or report.get('truncated'): return None
    if returns == 'table' and any(isinstance(r, dict) and r.get('patient_id') for r in report.get('rows') or []): return None
    found = rewrite(params['query'], returns)
    if not found: return None
    query, role, index = found
    try:
        got = sql_query.execute(path, {'query': query, 'returns': 'patient_set'}, patient_ids=population, target=target)
    except (ValueError, KeyError, TypeError):
        return None
    ids = got['patient_ids']
    if got['truncated'] or (population is not None and not set(ids) <= set(population)): return None
    if returns == 'count' and (role != 'counted' or report.get('value') != len(ids)): return None
    if returns == 'table':
        columns = report.get('columns') or []
        if index >= len(columns): return None
        values = [r.get(columns[index]) for r in report.get('rows') or []]
        if not values or any(type(v) is not int for v in values) or not max(values) <= len(ids) <= sum(values): return None
    return {'patient_ids': ids, 'role': role, 'query': query, 'version': VERSION}


def disclose(found):
    """One limitation line naming what the retained ids are."""
    what = {'counted': 'the patients counted', 'numerator': "the rate's numerator patients",
            'denominator': 'the patients in the calculation'}[found['role']]
    return f'{len(found["patient_ids"])} patient IDs retained as {what}, so follow-up questions can narrow this exact set.'


def from_graph(path, graph, answer, population=None):
    """Retained membership for an executed answer whose graph ran stand-alone SQL (e.g. voted analyst queries) but whose
    answer kept no ids: re-run the SQL read-only, require the same scalar, then derive. None when nothing qualifies."""
    from pathlib import Path
    from . import sql_query
    target = Path(path)/'semantic.sqlite3'
    target = target if target.is_file() else None
    kind = answer.get('answer_type')
    if kind not in ('count', 'value'): return None
    for node in reversed((graph or {}).get('nodes') or []):
        params = node.get('parameters') or {}
        if node.get('operation') != 'sql' or node.get('inputs') or not isinstance(params.get('query'), str): continue
        try:
            report = sql_query.execute(path, {'query': params['query'], 'returns': kind}, target=target,patient_ids=population)
        except (ValueError, KeyError, TypeError):
            continue
        if report.get('value') != answer.get(kind): continue
        found = derive(path, {'query': params['query']}, report, target=target,population=population)
        if found: return found
    return None
