"""Deterministic test oracles for model-written SQL (no model calls; research report items 3-4, FMEA FM-08/FM-09/FM-01).

QC-PARTITION  Ternary logic partitioning (SQLancer TLP, Rigger & Su 2020) applied as an answer check. For a count/sum/share
              query with a top-level WHERE predicate p over a FROM clause F:
                count(F WHERE p) + count(F WHERE NOT p) + count(F WHERE p IS NULL) == count(F)
              must hold. The identity itself guards against a non-deterministic or malformed predicate; the partition sizes
              are the useful part:
                * the NULL partition restricted to rows meeting every other conjunct, for a date/time-window conjunct:
                  undated rows a time window silently drops (FM-09);
                * on the TRUE partition, rows vs distinct business keys when the query counts or sums rows of a
                  versioned/snapshot stream or a fanned-out join: snapshot versions counted as entities (FM-08).
QC-PERTURB    Question-side metamorphic check (SQLHD-style, done deterministically on the SQL text). Each numeric slot in
              the question (threshold, comparator, look-back window, year) is perturbed in turn; the SQL is consistent only
              if a literal bound to that slot would change with it, in the same direction. Concretely: the slot's number
              appears as a SQL literal next to an operator of the same direction (over 180 -> > 180, never < 180 or >= 180),
              a 'past N months' window appears as a date N months before the snapshot (or as date arithmetic with N),
              and the SQL names no calendar year that neither the question nor its window implies.
Both are heal-not-block: they return findings; answer_checks turns them into warnings, the verified ladder uses them to
decide whether same-family model agreement may be accepted without the judge.
"""
import calendar
import datetime as dt
import re

VERSION = 'sql-oracles/1'
# Typed semantic tables whose rows should be one per business key (a duplicate row means a version or a join fan-out).
TABLE_KEYS = {'visit': 'encounter_id', 'claim': 'claim_id', 'appointment': 'appointment_id'}
_DATEISH = re.compile(r'\b\w*(?:date|_time|time)\b|\bsubstr\s*\(\s*\w*\.?\w*date', re.I)
_RAW_Q = re.compile(r'\brecords?\b|\brows?\b|\braw\b|\bversions?\b|\btransactions?\b|\bsnapshots?\b|\blines?\b', re.I)
_UNDATED_Q = re.compile(r'\bundated\b|\bno (?:event )?date\b|\bmissing (?:event )?dates?\b|\bwithout (?:a |an )?(?:event )?date\b', re.I)


# ---- SQL splitting at paren depth 0 -------------------------------------------------------------------------------
def _strip_comments(sql):
    return re.sub(r'--[^\n]*', ' ', sql or '').strip().rstrip(';').strip()


def _top_level(sql):
    """Positions of top-level clause keywords (outside parentheses and string literals)."""
    marks, depth, i, quote = [], 0, 0, None
    low = sql.lower()
    while i < len(sql):
        ch = sql[i]
        if quote:
            if ch == quote: quote = None
        elif ch in "'\"": quote = ch
        elif ch == '(': depth += 1
        elif ch == ')': depth -= 1
        elif depth == 0:
            for kw in ('select', 'from', 'where', 'group by', 'having', 'order by', 'limit', 'union', 'intersect', 'except', 'with'):
                if low.startswith(kw, i) and (i == 0 or not (low[i - 1].isalnum() or low[i - 1] == '_')) and \
                        (i + len(kw) >= len(low) or not (low[i + len(kw)].isalnum() or low[i + len(kw)] == '_')):
                    marks.append((kw, i)); i += len(kw) - 1; break
        i += 1
    return marks


def split_simple(sql):
    """{'select','from','where','group','rest'} for a single top-level SELECT ... FROM ... WHERE ..., else None."""
    body = _strip_comments(sql)
    marks = _top_level(body)
    kws = [k for k, _ in marks]
    if not kws or kws[0] != 'select' or kws.count('select') != 1 or 'from' not in kws or 'where' not in kws: return None
    if any(k in kws for k in ('union', 'intersect', 'except', 'with')): return None
    pos = dict((k, p) for k, p in reversed(marks))
    order = sorted((p, k) for k, p in pos.items())
    def part(k):
        start = pos[k] + len(k)
        nxt = [p for p, kk in order if p > pos[k]]
        return body[start:nxt[0] if nxt else len(body)].strip()
    out = {'select': part('select'), 'from': part('from'), 'where': part('where'),
           'group': part('group by') if 'group by' in pos else None, 'having': 'having' in pos}
    if pos['from'] > pos['where']: return None
    return out


def conjuncts(pred):
    """Top-level AND conjuncts of a predicate (BETWEEN x AND y kept whole)."""
    parts, depth, quote, start, low, i = [], 0, None, 0, pred.lower(), 0
    between = 0
    while i < len(pred):
        ch = pred[i]
        if quote:
            if ch == quote: quote = None
        elif ch in "'\"": quote = ch
        elif ch == '(': depth += 1
        elif ch == ')': depth -= 1
        elif depth == 0 and re.match(r'\bbetween\b', low[i:]) and (i == 0 or not low[i - 1].isalnum()): between += 1
        elif depth == 0 and re.match(r'and\b', low[i:]) and (i == 0 or not (low[i - 1].isalnum() or low[i - 1] == '_')):
            if between: between -= 1
            else:
                parts.append(pred[start:i].strip()); start = i + 3
        i += 1
    parts.append(pred[start:].strip())
    return [p for p in parts if p]


def _aliases(from_clause):
    """table -> alias for the typed tables named in a FROM clause."""
    out = {}
    for m in re.finditer(r'\b(record|visit|claim|appointment|condition|measurement|medication|stay|patient)\b(?:\s+(?:as\s+)?(?!on\b|join\b|left\b|inner\b|where\b|cross\b)([a-z_]\w*))?',
                         from_clause, re.I):
        out.setdefault(m.group(1).lower(), m.group(2) or m.group(1))
    return out


def _counts_rows(select):
    """The aggregate counts or sums rows (not distinct entities)."""
    s = select.lower()
    return bool(re.search(r'\bcount\s*\((?!\s*distinct\b)|\bsum\s*\(|\btotal\s*\(', s))


def partition(question, sql, execute, as_of=None):
    """QC-PARTITION findings for a query. execute(sql) -> first column of the first row (read-only executor).
    Returns a list of {'control','fmea','severity','message',...}; [] when the query is out of scope or clean."""
    parts = split_simple(sql or '')
    if not parts or parts['having']: return []
    if not re.search(r'\bcount\s*\(|\bsum\s*\(|\bavg\s*\(|100(?:\.0)?\s*\*', parts['select'], re.I): return []
    F, p = parts['from'], parts['where']
    one = lambda q: execute(q)
    findings = []
    try:
        total = one(f'SELECT count(*) FROM {F}')
        t = one(f'SELECT count(*) FROM {F} WHERE ({p})')
        f = one(f'SELECT count(*) FROM {F} WHERE NOT ({p})')
        n = one(f'SELECT count(*) FROM {F} WHERE ({p}) IS NULL')
    except Exception:  # noqa: BLE001 - an oracle never takes an answer down (no store, time limit, unsupported shape)
        return []
    detail = {'partition': {'total': total, 'true': t, 'false': f, 'null': n}}
    if None not in (total, t, f, n) and t + f + n != total:
        findings.append({'control': 'QC-PARTITION', 'fmea': 'FM-13', 'severity': 'warning', **detail,
                         'message': f'The filter does not partition the rows (true {t} + false {f} + unknown {n} != {total}); '
                                    f'the predicate is not deterministic, check the query.'})
    # FM-09: undated rows inside a time window.
    cs = conjuncts(p)
    if n and not _UNDATED_Q.search(question or ''):
        for i, c in enumerate(cs):
            if not _DATEISH.search(c) or re.search(r'\bis\s+(?:not\s+)?null\b', c, re.I): continue
            others = [o for j, o in enumerate(cs) if j != i]
            where = ' AND '.join([f'({c}) IS NULL'] + [f'({o})' for o in others])
            try: dropped = one(f'SELECT count(*) FROM {F} WHERE {where}')
            except Exception: continue  # noqa: BLE001
            if dropped:
                findings.append({'control': 'QC-PARTITION', 'fmea': 'FM-09', 'severity': 'info', 'undated_rows_dropped': dropped,
                                 'conjunct': c[:160], **detail,
                                 'message': f'{dropped:,} rows match every other condition but have no value for the time filter '
                                            f'({c[:80]}), so the window leaves them out; they are undated, not outside the window.'})
                break
    # FM-08: rows vs distinct business keys on the TRUE partition.
    if _counts_rows(parts['select']) and not _RAW_Q.search(question or ''):
        aliases = _aliases(F)
        key_expr = None
        if 'record' in aliases:
            from .answer_checks import _versioned
            streams = set(re.findall(r"stream\s*=\s*'([a-z0-9_]+)'", p, re.I))
            keys = _versioned()
            hit = [s for s in streams if s in keys]
            if len(hit) == 1:
                key_expr, label = f"json_extract({aliases['record']}.payload,'$.{keys[hit[0]]}')", f"{keys[hit[0]]} of {hit[0]}"
        if key_expr is None:
            for table, key in TABLE_KEYS.items():
                if table in aliases:
                    key_expr, label = f'{aliases[table]}.{key}', f'{key} of {table}'; break
        if key_expr:
            try: rows, distinct = (one(f'SELECT count({key_expr}) FROM {F} WHERE ({p})'),
                                   one(f'SELECT count(DISTINCT {key_expr}) FROM {F} WHERE ({p})'))
            except Exception: rows = distinct = None  # noqa: BLE001
            if distinct is not None and rows and distinct < rows:
                # info, not warning: on the M/N/O replay it fired on 0 wrong and 2 right answers (net-of-reversal sums
                # legitimately add every version); it states the numbers without blocking agreement or capture.
                findings.append({'control': 'QC-PARTITION', 'fmea': 'FM-08', 'severity': 'info', 'rows': rows, 'distinct_keys': distinct, **detail,
                                 'message': f'The query counts or sums {rows:,} rows that hold only {distinct:,} distinct {label}: '
                                            f'snapshot versions or a join repeat entities; count or sum once per entity.'})
    return findings


# ---- QC-PERTURB ---------------------------------------------------------------------------------------------------
_DIRECTION = {'gt': '>', 'ge': '>=', 'lt': '<', 'le': '<=', 'eq': '='}
_FLIP = {'>': '<', '<': '>', '>=': '<=', '<=': '>=', '=': '='}


def _sql_literal_ops(sql, number):
    """Operators the SQL applies to a numeric literal, normalised to 'column OP literal' form."""
    num = re.escape(number)
    text = re.sub(r"'[^']*'", "''", sql)  # string literals never bind numeric slots
    ops = []
    for m in re.finditer(r'(>=|<=|<>|!=|=|>|<)\s*' + num + r'(?![\d.])', text):
        ops.append(m.group(1))
    for m in re.finditer(r'(?<![\d.\w])' + num + r'\s*(>=|<=|<>|!=|=|>|<)(?!=)', text):
        ops.append(_FLIP.get(m.group(1), m.group(1)))
    for m in re.finditer(r'\bbetween\s+' + num + r'\s+and\b', text, re.I): ops.append('>=')
    for m in re.finditer(r'\bbetween\s+[\w.]+\s+and\s+' + num + r'(?![\d.])', text, re.I): ops.append('<=')
    return ops


def _months_back(as_of, n, unit):
    d = dt.date.fromisoformat(str(as_of)[:10])
    if unit.startswith('day'): return d - dt.timedelta(days=n)
    if unit.startswith('week'): return d - dt.timedelta(weeks=n)
    months = n * (12 if unit.startswith('year') else 1)
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    return dt.date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


_WINDOW = re.compile(r'\b(?:past|last|previous|prior|trailing|preceding|within the (?:past|last))\s+(\d+|one|two|three|six|twelve|eighteen|twenty-four)\s+'
                     r'(days?|weeks?|months?|years?)\b', re.I)
_WORDNUM = {'one': 1, 'two': 2, 'three': 3, 'six': 6, 'twelve': 12, 'eighteen': 18, 'twenty-four': 24}


def perturbation(question, sql, as_of=None):
    """QC-PERTURB findings: question slots whose perturbation the SQL would not track (or would track backwards)."""
    if not question or not sql: return []
    from .semantic_cache import canonical
    findings, body = [], _strip_comments(sql)
    try: tokens = canonical(question)
    except Exception: return []  # noqa: BLE001
    # 1. comparator + number slots
    for i, tok in enumerate(tokens):
        m = re.fullmatch(r'<cmp:(gt|ge|lt|le|eq)>', tok)
        if not m or i + 1 >= len(tokens): continue
        n = re.fullmatch(r'<num:([\d.]+)>', tokens[i + 1])
        if not n: continue
        want, number = _DIRECTION[m.group(1)], n.group(1)
        if len(number) == 4 and number.startswith(('19', '20')): continue  # 'after 2027' is a year slot, handled below
        ops = _sql_literal_ops(body, number)
        if not ops and '.' not in number:
            ops = _sql_literal_ops(body, number + '.0')
        if not ops: continue  # an unbound number is QC-NUMBERS' finding
        direction = lambda o: '+' if o in ('>', '>=') else '-' if o in ('<', '<=') else '='
        opposite = {'+': '-', '-': '+'}
        # '= 1' on a flag answers 'at least one'; only a comparison pointing the other way is a reversal.
        if want in ('>', '>=', '<', '<=') and all(direction(o) == opposite[direction(want)] for o in ops):
            findings.append({'control': 'QC-PERTURB', 'fmea': 'FM-01', 'severity': 'warning', 'slot': f'{m.group(1)} {number}', 'sql_ops': ops,
                             'message': f"The question says {'more' if direction(want) == '+' else 'less'} than/at {number}, but the query compares "
                                        f"{number} the other way ({', '.join(sorted(set(ops)))}); raising the number would move the answer backwards."})
        elif want in ('>', '<') and all(o == want + '=' for o in ops):
            findings.append({'control': 'QC-PERTURB', 'fmea': 'FM-01', 'severity': 'warning', 'slot': f'{m.group(1)} {number}', 'sql_ops': ops,
                             'message': f"The question excludes {number} itself ('{ {'>': 'over', '<': 'under'}[want]} {number}') but the query "
                                        f"uses {want}= {number}, so values equal to {number} are counted."})
        elif want in ('>=', '<=') and all(o == want[0] for o in ops):
            findings.append({'control': 'QC-PERTURB', 'fmea': 'FM-01', 'severity': 'warning', 'slot': f'{m.group(1)} {number}', 'sql_ops': ops,
                             'message': f"The question includes {number} itself ('at least/at most {number}') but the query uses {want[0]} {number}."})
    # 2. look-back windows
    if as_of:
        dates = [dt.date.fromisoformat(d) for d in re.findall(r"'(\d{4}-\d{2}-\d{2})'", body)]
        for w in _WINDOW.finditer(question):
            raw, unit = w.group(1).lower(), w.group(2).lower()
            n = int(raw) if raw.isdigit() else _WORDNUM.get(raw)
            if not n: continue
            start = _months_back(as_of, n, unit)
            near = any(abs((d - start).days) <= 3 for d in dates)
            arithmetic = re.search(r"[-+]\s*" + str(n) + r"\s*(?:" + unit.rstrip('s') + r")s?'", body, re.I) or \
                re.search(r"julianday\s*\([^)]*\)\s*-\s*julianday\s*\([^)]*\)\s*(?:<=|<|>|>=|between)\s*" + str(n * (30 if unit.startswith('month') else 365 if unit.startswith('year') else 7 if unit.startswith('week') else 1)), body, re.I) or \
                re.search(r"\b(?:<=|<|>|>=)\s*" + str(n) + r"\b", body)
            if not near and not arithmetic:
                findings.append({'control': 'QC-PERTURB', 'fmea': 'FM-12', 'severity': 'warning', 'slot': w.group(0), 'expected_start': start.isoformat(),
                                 'message': f"The question asks about the {w.group(0).lower()} (from about {start.isoformat()} to {str(as_of)[:10]}), "
                                            f"but no date or interval in the query tracks that window."})
    # 3. years the SQL uses that nothing in the question implies
    q_years = {int(y) for y in re.findall(r'\b(19\d\d|20\d\d)\b', question)}
    if q_years and as_of and not _WINDOW.search(question) and not re.search(r'\b(?:since|through|until|to date|ytd|year[- ]to[- ]date|baseline|prior year|previous year|year over year|vs\.?|versus|compared?)\b', question, re.I):
        sql_years = {int(y) for y in re.findall(r"'(\d{4})(?:-\d{2}-\d{2})?'", body)}
        extra = sorted(y for y in sql_years - q_years if y != int(str(as_of)[:4]))
        if extra:
            findings.append({'control': 'QC-PERTURB', 'fmea': 'FM-12', 'severity': 'warning', 'slot': 'year', 'sql_years': extra,
                             'message': f"The query uses {', '.join(map(str, extra))}, a year the question does not mention "
                                        f"(it asks about {', '.join(map(str, sorted(q_years)))}); changing the question's year would not move that bound."})
    return findings


# ---- QC-SHAPE / QC-EMPTY: the answer cannot be what was asked ----------------------------------------------------------
_RATE = re.compile(r'%|\bpercent(?:age)?\b|\bshare\b|\bproportion\b|\brate\b|\bfraction\b|\bper 1,?000\b', re.I)
_POPULATED = re.compile(r'\b(?:how many|number of|count|which|who|list)\b', re.I)
_ABSENCE_Q = re.compile(r'\b(?:no|not|never|without|none|zero|missing|lacks?|lacking|any)\b', re.I)


def shape(question, answer):
    """QC-SHAPE: a rate/percent/share question answered with a patient list or a bare count, i.e. no division (FM-02)."""
    if not question or not _RATE.search(question) or re.search(r'\bhow many\b|\bnumber of\b|\bcount\b', question, re.I): return []
    if re.search(r'\b(?:by|per|for each|each|break ?down)\b(?! 1,?000)', question, re.I): return []  # tables carry their own columns
    query = answer.get('query') or ''
    if answer.get('answer_type') == 'patient_set' or (answer.get('answer_type') in ('count', 'value') and query and '/' not in query
                                                      and not re.search(r'\bavg\s*\(', query, re.I)):
        return [{'control': 'QC-SHAPE', 'fmea': 'FM-02', 'severity': 'warning',
                 'message': f"The question asks for a rate, but the answer is {'a list of patients' if answer.get('answer_type') == 'patient_set' else 'a count with no division'}; "
                            f"state the numerator and the denominator."}]
    return []


def empty(question, answer):
    """QC-EMPTY: a count/list question whose answer is zero or empty, when the question does not ask about absence (FM-09/FM-11)."""
    if not question or not _POPULATED.search(question) or _ABSENCE_Q.search(question): return []
    t = answer.get('answer_type')
    v = answer.get('value') if answer.get('value') is not None else answer.get('count')
    zero = (t in ('count', 'value') and isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0) or \
        (t == 'patient_set' and not (answer.get('patient_ids') or answer.get('count'))) or \
        (t == 'table' and isinstance(answer.get('table'), list) and not answer['table'])
    if not zero: return []
    return [{'control': 'QC-EMPTY', 'fmea': 'FM-09', 'severity': 'warning',
             'message': 'The answer is zero or empty for a question that expects some rows; a join or filter may have removed '
                        'everything (undated rows, a value spelled differently, a field that is never populated).'}]


def check(question, sql, as_of=None, execute=None, answer=None):
    """All oracle findings for one query (partition needs a read-only executor; perturbation is text-only; shape and
    empty need the answer contract)."""
    out = []
    if answer is not None:
        try: out += shape(question, answer) + empty(question, answer)
        except Exception as exc:  # noqa: BLE001
            out.append({'control': 'error', 'fmea': None, 'severity': 'info', 'message': f'shape: {exc}'[:200]})
    try: out += perturbation(question, sql, as_of)
    except Exception as exc:  # noqa: BLE001
        out.append({'control': 'error', 'fmea': None, 'severity': 'info', 'message': f'perturbation: {exc}'[:200]})
    if execute is not None:
        try: out += partition(question, sql, execute, as_of)
        except Exception as exc:  # noqa: BLE001
            out.append({'control': 'error', 'fmea': None, 'severity': 'info', 'message': f'partition: {exc}'[:200]})
    return out


def readonly_executor(path):
    """execute(sql) -> scalar, over the semantic layer opened read-only with the governor's authorizer and time limit."""
    import sqlite3, time
    from . import sql_query

    def run(query):
        target = sql_query.semantic_layer.ensure(path)
        db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
        deadline = time.perf_counter() + min(getattr(sql_query, 'TIME_LIMIT_SECONDS', 10), 10)
        db.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10000)
        db.set_authorizer(sql_query._authorizer)
        try:
            row = db.execute(query).fetchone()
        finally:
            db.close()
        return row[0] if row else None
    return run
