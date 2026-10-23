"""Answer-level poka-yoke controls (docs/agere/FMEA.md, docs/agere/fmea.json).

Each control looks at a finished answer contract (and, on the SQL route, the query that produced it) for a
failure mode that past blind runs showed reaching users unflagged. Controls are heal-not-block: they never change
a number, a status or the answer type. They add a plain-language warning to answer['warnings'] (which the headline
and the /ask card already show) and a structured record to answer['quality_checks'] naming the FMEA failure mode.
Where the fix is mechanical and certain, the control states the missing fact (e.g. the denominator read from the
query) instead of only flagging its absence.

Controls (ids are referenced from fmea.json 'controls_added'):
  QC-PERIOD   latest period of a time series is only partly observed at the snapshot date   (FM-16)
  QC-UNITS    percent columns outside 0-100, cents shown for a dollar question, days for hours (FM-14)
  QC-DENOM    a rate/percent/share answer that does not state its denominator               (FM-05)
  QC-VERSION  counting or summing raw snapshot records of a versioned stream                 (FM-08)
  QC-NUMBERS  a number or date in the question that the query never uses                     (FM-01, FM-12)
  QC-PARTS    'how many ... and how much' answered with only one of the two                   (FM-01)
  QC-CLOCK    a timing question measured to the record's arrival date, not the event         (FM-07)
  QC-PERTURB  a question slot (threshold, comparator, look-back window, year) the SQL would not track if it changed,
              or would track backwards (oracles.perturbation; text only)                       (FM-01, FM-12)
  QC-PARTITION ternary partition of a count/sum query's filter: the identity, undated rows a time window drops (info),
              rows vs distinct business keys of versioned streams / fanned-out joins (oracles.partition; needs a
              read-only executor, so it runs only when annotate() is given one)              (FM-08, FM-09)
  QC-SHAPE    a rate/percent/share question answered with a patient list or an undivided count (oracles.shape) (FM-02)
  QC-EMPTY    a count/list question answered with zero or nothing, absence not asked (oracles.empty)         (FM-09)
  QC-READINGS two candidate answers differ by a stated definitional choice: both readings are attached
              (answer['readings']) with one clarifying question (verification_v2.attach_readings) (FM-19)
  QC-METRIC   the governed metric the question resolves to (metrics.py): answer['computed_as'] states its id/version;
              warnings where the query departs from its grain, time anchor, window, value set, population or
              versioning (rules ML-*), or the value leaves its expected range (FM-05/06/07/08/12/14)
"""
import calendar
import datetime as dt
import re

VERSION = 'answer-checks/1'

# Streams whose raw records are successive snapshots of one entity (semantic_layer.VERSIONED_STREAMS) plus
# encounters, which arrive as in_progress + completed versions (SYNTHETIC_DATA_ISSUES.md, deliberate realism).
_EXTRA_VERSIONED = {'encounters': 'encounter_id'}


def _versioned():
    try:
        from .semantic_layer import VERSIONED_STREAMS
        return {**VERSIONED_STREAMS, **_EXTRA_VERSIONED}
    except Exception:  # noqa: BLE001 - a control must never break answering
        return dict(_EXTRA_VERSIONED)


def _add(answer, control, fmea, message, severity='warning', **detail):
    """Record a finding. 'warning' also reaches answer['warnings'] (headline, /ask card); 'info' states a fact quietly."""
    checks = answer.setdefault('quality_checks', [])
    if any(c['control'] == control and c['message'] == message for c in checks): return  # idempotent
    checks.append({'control': control, 'fmea': fmea, 'severity': severity, 'message': message, **detail})
    if severity != 'warning': return
    warnings = answer.get('warnings')
    if not isinstance(warnings, list): warnings = answer['warnings'] = list(warnings or [])
    if message not in warnings: warnings.append(message)


# ---- QC-PERIOD: partial latest period ----------------------------------------------------------------------
_PERIOD_PATTERNS = (
    ('day', re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')),
    ('month', re.compile(r'^(\d{4})-(\d{2})$')),
    ('quarter', re.compile(r'^(\d{4})[- ]?Q([1-4])$', re.I)),
    ('year', re.compile(r'^(\d{4})$')),
)
_PERIOD_KEYS = re.compile(r'^(month|period|quarter|year|week|bucket|date|time|benefit_year|service_month|month_start)$', re.I)


def _period_bounds(kind, m):
    y = int(m.group(1))
    if kind == 'month':
        mo = int(m.group(2)); return dt.date(y, mo, 1), dt.date(y, mo, calendar.monthrange(y, mo)[1])
    if kind == 'quarter':
        q = int(m.group(2)); start = dt.date(y, 3 * q - 2, 1); end_m = 3 * q
        return start, dt.date(y, end_m, calendar.monthrange(y, end_m)[1])
    if kind == 'year': return dt.date(y, 1, 1), dt.date(y, 12, 31)
    return None


def partial_period(rows, as_of):
    """(column, period, days_observed, days_in_period) when a time series ends in a period the snapshot cuts short."""
    if not isinstance(rows, list) or len(rows) < 2 or not all(isinstance(r, dict) for r in rows) or not as_of: return None
    try: cutoff = dt.date.fromisoformat(str(as_of)[:10])
    except ValueError: return None
    for col in rows[0]:
        values = [r.get(col) for r in rows if r.get(col) is not None]
        if not values or not all(isinstance(v, (str, int)) for v in values): continue
        values = [str(v) for v in values]
        if isinstance(rows[0][col], int) and not _PERIOD_KEYS.match(col): continue  # a bare integer column is a year only when named so
        for kind, rx in _PERIOD_PATTERNS:
            matches = [rx.match(v) for v in values]
            if not all(matches): continue
            if kind == 'day':
                # Daily rows are complete days; weekly buckets keyed by their start date are partial if the week runs past the cutoff.
                if not re.search(r'week', col, re.I): break
                last = max(dt.date.fromisoformat(v) for v in values); start, end = last, last + dt.timedelta(days=6)
            else:
                last_value = max(values, key=lambda v: _period_bounds(kind, rx.match(v))[0])
                start, end = _period_bounds(kind, rx.match(last_value))
                last = last_value
            if start <= cutoff < end:
                return {'column': col, 'period': str(last), 'granularity': 'week' if kind == 'day' else kind,
                        'days_observed': (cutoff - start).days + 1, 'days_in_period': (end - start).days + 1}
            break
    return None


# ---- QC-UNITS -----------------------------------------------------------------------------------------------
_PCT_COL = re.compile(r'(?:^|_)(pct|percent|percentage|share)(?:_|$)', re.I)
_DOLLAR_Q = re.compile(r'\bdollars?\b|\$|\busd\b|\bhow much\b|\brevenue\b|\bspend(?:ing)?\b|\bpaid\b|\bcost\b', re.I)


def _numeric(v): return isinstance(v, (int, float)) and not isinstance(v, bool)


def unit_findings(answer, question):
    out, q = [], (question or '')
    rows = answer.get('table') if isinstance(answer.get('table'), list) else []
    rows = [r for r in rows if isinstance(r, dict)]
    cols = list(dict.fromkeys(k for r in rows for k in r))
    for c in cols:
        if not _PCT_COL.search(c): continue
        bad = [r[c] for r in rows if _numeric(r.get(c)) and not (0 <= r[c] <= 100.0001)]
        if bad: out.append(f"Column '{c}' is labelled a percentage but has values outside 0-100 (e.g. {bad[0]}); check the units.")
    if answer.get('unit') == '%' and _numeric(answer.get('value')) and not (0 <= answer['value'] <= 100.0001):
        out.append(f"The answer is labelled a percentage but its value ({answer['value']}) is outside 0-100; check the units.")
    query = answer.get('query') or ''
    if _DOLLAR_Q.search(q) and not re.search(r'\bcents?\b', q, re.I):
        cent_cols = [c for c in cols if c.lower().endswith('_cents')]
        dollar_cols = [c for c in cols if re.search(r'usd|dollar', c, re.I)]
        if cent_cols and not dollar_cols:
            out.append(f"The question asks for dollars but {', '.join(cent_cols[:3])} {'is' if len(cent_cols) == 1 else 'are'} in cents (divide by 100).")
        elif answer.get('answer_type') == 'value' and re.search(r"_cents\b", query) and not re.search(r'/\s*100(?:\.0*)?\b', query) \
                and not re.search(r'\bcount\s*\(', query, re.I):
            out.append('The question asks for dollars but the query returns a cents field without dividing by 100.')
    if re.search(r'\bhours?\b', q, re.I) and not re.search(r'\bdays?\b', q, re.I):
        uses_days = re.search(r'\blos_days\b|julianday\s*\(', query, re.I) and not re.search(r'\*\s*24(?:\.0*)?\b|strftime\s*\(\s*\'%s\'', query, re.I)
        day_cols = [c for c in cols if re.search(r'(?:^|_)days?(?:_|$)', c, re.I)] and not [c for c in cols if re.search(r'hour', c, re.I)]
        if re.search(r'\blos_days\b\s*\*\s*24', query, re.I):
            out.append('Hours were computed by multiplying a whole-day length of stay by 24; use start and end timestamps for hours.')
        elif uses_days or day_cols:
            out.append('The question asks for hours but the answer is computed in days; check the time unit.')
    return out


# ---- QC-DENOM -----------------------------------------------------------------------------------------------
_RATE_Q = re.compile(r'%|\bpercent(?:age)?\b|\bshare\b|\bproportion\b|\brate\b|\bper 1,?000\b|\bfraction\b', re.I)
_DENOM_STATED = re.compile(r'\bamong\b|\bdenominator\b|\bout of\b|\bdivided by\b|\bper\b|\beligible\b|\bof (?:all|the|those|patients|members)\b|\bwho\b|/', re.I)
_DENOM_COLS = re.compile(r'denominator|eligible|^n$|^total|_total$|^members|^patients$|population|screened|in_scope|^base', re.I)


def _query_denominator(query):
    """The expression a SQL query divides by, when it is written as a direct division."""
    m = re.search(r'/\s*(?:nullif\s*\(\s*)?(?:cast\s*\(\s*|1\.0\s*\*\s*)?((?:count|sum)\s*\((?:[^()]|\([^()]*\))*\)|\(\s*select\b(?:[^()]|\([^()]*\))*\)|[a-z_][\w.]*)',
                  query or '', re.I)
    if not m: return None
    expr = re.sub(r'\s+', ' ', m.group(1)).strip()
    return None if re.fullmatch(r'\d+(?:\.\d+)?', expr) else expr[:200]


def denominator_finding(answer, question):
    if not _RATE_Q.search(question or '') or answer.get('answer_type') not in ('value', 'table'): return None
    if answer.get('status') not in ('answered', 'partial'): return None
    stated = ' '.join(str(answer.get(k) or '') for k in ('computed', 'method', 'value' if isinstance(answer.get('value'), str) else '_'))
    if _DENOM_STATED.search(stated): return None
    rows = answer.get('table') if isinstance(answer.get('table'), list) else []
    if any(isinstance(r, dict) and any(_DENOM_COLS.search(k) for k in r) for r in rows[:1]): return None
    expr = _query_denominator(answer.get('query'))
    if expr:
        return ({'denominator_from_query': expr}, f'Denominator (read from the query): {expr}.', 'info')
    if answer.get('answer_type') != 'value' or answer.get('overall') or answer.get('concepts') or answer.get('query'):
        return None  # tables carry their own columns; engine routes carry their population in the plan and receipts
    return ({'denominator_from_query': None}, 'This rate does not state its denominator; check which population it is measured over.', 'warning')


# ---- QC-VERSION ---------------------------------------------------------------------------------------------
_RAW_Q = re.compile(r'\brecords?\b|\brows?\b|\braw\b|\bversions?\b|\btransactions?\b|\bsnapshots?\b', re.I)


def version_finding(answer, question=None):
    query = answer.get('query') or ''
    if not query: return None
    outside_exists = re.sub(r'\bexists\s*\(\s*select\s+1\s+from\s+record\b', ' ', query, flags=re.I)
    if not re.search(r'\bfrom\s+record\b|\bjoin\s+record\b', outside_exists, re.I): return None  # record only used in an EXISTS test
    if _RAW_Q.search(question or ''): return None  # the question asks about records/versions themselves
    if re.search(r'_delta_\w*', query): return None  # delta fields are designed to be summed across versions
    if re.search(r'\bcurrent_record\b|\brow_number\s*\(|\bmax\s*\(\s*\w*\.?available_date|\bcount\s*\(\s*distinct\b', query, re.I): return None
    if not re.search(r'\bcount\s*\(|\bsum\s*\(|\bavg\s*\(|\btotal\s*\(', query, re.I): return None
    streams = set(re.findall(r"stream\s*=\s*'([a-z0-9_]+)'", query, re.I))
    streams |= {s for m in re.finditer(r'stream\s+in\s*\(([^)]*)\)', query, re.I) for s in re.findall(r"'([^']+)'", m.group(1))}
    keys = _versioned()
    def grouped(key):  # versions collapsed per entity: GROUP BY the key (directly or through its alias)
        return ("'$." + key + "'") in query and re.search(r'group\s+by\s+[^;]*\b' + key + r'\b', query, re.I)
    hit = sorted(s for s in streams if s in keys and not grouped(keys[s]))
    if not hit: return None
    ids = ', '.join(sorted({keys[s] for s in hit}))
    return (hit, f"Counts or sums raw records of {', '.join(hit)}, which hold several snapshot versions per {ids}; "
                 f"if the question means entities rather than records, the current version of each is in current_record.")


# ---- QC-NUMBERS ---------------------------------------------------------------------------------------------
_DATE = re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b')
_NUM = re.compile(r'(?<![\w.$-])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\w])')


_WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve']


def unused_numbers(question, query, as_of=None):
    """Numbers and dates the question states that the SQL never mentions (a silently dropped constraint)."""
    if not question or not query: return []
    q, sql = question, re.sub(r'(?<=\d),(?=\d{3})', '', query)
    missing = []
    for m in _DATE.finditer(q):
        date = m.group(0)
        if as_of and date == str(as_of)[:10]: continue  # the snapshot cutoff itself: the warehouse is already cut there
        if date not in sql: missing.append(date)
    rest = _DATE.sub(' ', q)
    # 'type 2 diabetes', 'stage 3 CKD', 'Q3', 'H1' name things rather than constrain them
    rest = re.sub(r'\b(?:type|stage|tier|class|grade|level|phase|step|schedule)\s+\d+\b', ' ', rest, flags=re.I)
    for m in _NUM.finditer(rest):
        whole = m.group(1).replace(',', ''); frac = m.group(2)
        text = whole + ('.' + frac if frac else '')
        n = float(text)
        if n == 0: continue
        forms = {text, whole if not frac else text}
        if frac is None:
            forms |= {f'{whole}.0', f'{int(whole) / 100:g}'}  # 8% may be written 0.08
            if len(whole) == 4 and whole.startswith(('19', '20')): forms |= {whole}
        follows = rest[m.end():m.end() + 2]
        if re.match(r'\s*(?:-\s*)?(?:months?|weeks?|days?|years?)\b', rest[m.end():m.end() + 12], re.I) and \
                re.search(r"'\d{4}-\d{2}-\d{2}'|date\s*\(|julianday\s*\(", sql, re.I):
            continue  # a look-back window the query wrote as calendar dates
        if follows.startswith('+') and re.search(r'\bconcept\s*=', sql, re.I):
            continue  # '30+' thresholds encoded in a library concept
        if '%' in follows: forms |= {f'{n / 100:g}', f'{n / 100}'}
        if frac is None and 0 < int(whole) < len(_WORDS) and re.search(r'(?<![a-z])' + _WORDS[int(whole)] + r'(?![a-z])', sql, re.I):
            continue  # spelled out in a coded value ('recalls_tetanus_over_ten_years')
        if not any(re.search(r'(?<![\d.])' + re.escape(f) + r'(?![\d])', sql) for f in forms):
            missing.append(text)
    return list(dict.fromkeys(missing))


# ---- QC-PARTS -----------------------------------------------------------------------------------------------
_COUNT_PART = re.compile(r'\bhow many\b|\bnumber of\b|\bcount\b', re.I)
_MONEY_PART = re.compile(r'\bhow much\b|\btotal (?:dollars|amount|paid|cost|spend)\b|\bdollars?\b|\bamount\b|\$', re.I)


def parts_finding(answer, question):
    q = question or ''
    if not (_COUNT_PART.search(q) and _MONEY_PART.search(q) and re.search(r'\band\b', q, re.I)): return None
    if answer.get('status') not in ('answered', 'partial'): return None
    if answer.get('answer_type') in ('count', 'value', 'patient_set'):
        return 'The question asks for both a count and an amount, but the answer gives one number.'
    rows = [r for r in (answer.get('table') or []) if isinstance(r, dict)]
    cols = list(dict.fromkeys(k for r in rows[:5] for k in r))
    if not cols: return None
    money = [c for c in cols if re.search(r'usd|dollar|cents|amount|paid|cost|allowed|billed|spend|charge|balance', c, re.I)]
    count = [c for c in cols if re.search(r'count|^n$|number|^num|patients|members|claims|payments|visits|encounters|fills|appointments', c, re.I)
             and c not in money]
    if money and not count: return 'The question asks how many and how much; the answer gives the amount but not the count.'
    if count and not money: return 'The question asks how many and how much; the answer gives the count but not the amount.'
    return None


# ---- QC-CLOCK -----------------------------------------------------------------------------------------------
_TIMING_Q = re.compile(r'\bhow (?:long|many days|many hours)\b|\bdays? (?:from|to|between|until)\b|\blag\b|\bturnaround\b|\btime to\b|\bdelay\b', re.I)
_ARRIVAL_Q = re.compile(r'\barriv|\breceived\b|\bavailable\b|\bshow(?:ed)? up\b|\blanded? in the (?:warehouse|data)\b|\bfeed\b', re.I)


def clock_finding(answer, question):
    """A timing question measured to the date a record arrived in the warehouse instead of the business event (FM-07)."""
    q, query = question or '', answer.get('query') or ''
    if not query or not _TIMING_Q.search(q) or _ARRIVAL_Q.search(q): return None
    clocks = sorted(set(re.findall(r'\b(first_available_date|last_available_date|available_date)\b', query, re.I)))
    if not clocks or not re.search(r'julianday|date\s*\(|-\s*\w*\.?(?:first_)?available_date', query, re.I): return None
    return clocks, (f"Timing is measured with {', '.join(clocks)}, the date the record arrived in the data, not when the event happened; "
                    f"check that arrival time is what the question means.")


# ---- QC-METRIC: the governed metric the answer was computed as (metrics.py) -----------------------------------
_RATE_NUMERATORS = {'ed': 'ed_visits', 'admits': 'admissions', 'admissions': 'admissions', 'pcp': 'pcp_visits', 'visits': 'visits'}


def rate_arithmetic(answer):
    """The arithmetic actually behind a per-1,000 answer, read from the engine's own row: {'statement', 'denominator'}
    ('member_years' | 'headcount') or None. A label must never claim a headcount rate over a member-year computation."""
    if answer.get('answer_type') != 'value' or not isinstance(answer.get('value'), (int, float)): return None
    rows = answer.get('table') if isinstance(answer.get('table'), list) else []
    row = answer.get('overall') if isinstance(answer.get('overall'), dict) else (rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else None)
    if not row: return None
    for key, v in row.items():
        if not key.endswith('_per_1000') or not isinstance(v, (int, float)) or abs(v - answer['value']) > 1e-6: continue
        num_key = _RATE_NUMERATORS.get(key[:-len('_per_1000')], key[:-len('_per_1000')])
        num = row.get(num_key)
        for den_key, kind, unit in (('member_years', 'member_years', 'member-years'), ('members', 'headcount', 'roster patients'),
                                    ('patients', 'headcount', 'roster patients')):
            den = row.get(den_key)
            if isinstance(num, (int, float)) and isinstance(den, (int, float)) and den and abs(1000.0 * num / den - v) <= max(0.06, abs(v) * 1e-3):
                return {'denominator': kind, 'numerator': num_key,
                        'statement': f"1,000 x {num:,} {num_key.replace('_', ' ')} / {den:,.1f} {unit} = {v}"}
    return None


def metric_findings(answer, question, as_of=None, execute=None):
    """State the metric id/version the question resolves to (answer['computed_as']) and, on the SQL route, warn where the
    query departs from it (grain, time anchor, window, value set, population, versioning) or the value leaves the
    metric's expected range. Heal-not-block: nothing is changed but the annotations."""
    import os
    # A sealed shared request already identifies the executed engine and method.
    # A wording-based catalog match is not evidence that a different metric ran.
    if (answer.get('computed_as') or {}).get('source_request_id'):
        return None
    if os.environ.get('AGERE_METRIC_LINT', 'repair') == 'off': return None
    from . import metrics
    r = metrics.resolve({'question': question, 'as_of': as_of})
    if not r: return None
    query = answer.get('query') if isinstance(answer.get('query'), str) else None
    found = metrics.lint(question, query, as_of, source=execute, resolution=r) if query else []
    answer['computed_as'] = {'metric': r['id'], 'version': r['version'], 'variant': r.get('variant'), 'layer': metrics.VERSION,
                             'definition': r['metric']['summary'], 'conforms': not found if query else None,
                             'citations': [c['url'] for c in r.get('citations') or []][:3],
                             **({'evidence': answer['computed_as']['evidence']} if isinstance(answer.get('computed_as'), dict)
                                and answer['computed_as'].get('evidence') else {})}
    arith = rate_arithmetic(answer)
    if arith:  # state the arithmetic that produced the number; a definition naming another denominator does not conform
        answer['computed_as']['arithmetic'] = arith['statement']
        said = r['metric']['summary'].lower()
        claims_headcount = bool(re.search(r'roster patients|headcount|not annuali[sz]ed', said)) and 'member-year' not in said
        claims_years = 'member-year' in said or 'member years' in said
        if (arith['denominator'] == 'member_years' and claims_headcount) or (arith['denominator'] == 'headcount' and claims_years):
            unit = 'member-years' if arith['denominator'] == 'member_years' else 'roster patients'
            answer['computed_as']['definition'] = f"Computed per 1,000 {unit}: {arith['statement']}"
            answer['computed_as']['conforms'] = False
            _add(answer, 'QC-METRIC', 'FM-05', f"The rate was computed as {arith['statement']} (per 1,000 {unit}), not as metric "
                 f"{r['id']}" + (f" ({r['variant']})" if r.get('variant') else '') + f": {r['metric']['summary'][:160]}",
                 metric=r['id'], metric_version=r['version'])
            answer['computed'] = f"{arith['statement']} (per 1,000 {unit})"
            return r
    _add(answer, 'QC-METRIC', 'FM-05', f"Computed as {metrics.computed_as(r)}", 'info', metric=r['id'], metric_version=r['version'])
    tag = f"metric {r['id']} v{r['version']}" + (f" ({r['variant']})" if r.get('variant') else '')
    if tag not in str(answer.get('computed') or ''):  # the headline's "Computed as:" names the governed metric
        answer['computed'] = (f"{str(answer['computed']).rstrip('.')} [{tag}]" if answer.get('computed')
                              else metrics.computed_as(r))
    for f in found:
        _add(answer, 'QC-METRIC', {'ML-VALUESET': 'FM-06', 'ML-POP': 'FM-05', 'ML-ANCHOR': 'FM-07', 'ML-WINDOW': 'FM-12',
                                   'ML-VERSION': 'FM-08', 'ML-GRAIN': 'FM-08'}.get(f['rule'], 'FM-05'),
             f['message'], rule=f['rule'], metric=r['id'], metric_version=r['version'])
    value = answer.get('value') if answer.get('answer_type') == 'value' else answer.get('count') if answer.get('answer_type') == 'count' else None
    out_of_range = metrics.check_result(r, value)
    if out_of_range: _add(answer, 'QC-METRIC', 'FM-14', out_of_range, rule='range', metric=r['id'], metric_version=r['version'])
    return r


def annotate(answer, question, as_of=None, execute=None):
    """Run every control on a finished answer; adds warnings and quality_checks in place and returns the answer.
    execute: optional read-only scalar executor (oracles.readonly_executor) for the partition oracle."""
    if not isinstance(answer, dict) or answer.get('answer_type') == 'unsupported': return answer
    try:
        found = partial_period(answer.get('table'), as_of)
        if found:
            _add(answer, 'QC-PERIOD', 'FM-16',
                 f"The latest {found['granularity']} ({found['period']}) is partial: data through {str(as_of)[:10]} covers "
                 f"{found['days_observed']} of {found['days_in_period']} days, so compare it with full periods with caution.", partial_period=found)
        for message in unit_findings(answer, question):
            _add(answer, 'QC-UNITS', 'FM-14', message)
        denom = denominator_finding(answer, question)
        if denom:
            detail, message, severity = denom
            if detail.get('denominator_from_query'): answer['denominator'] = detail['denominator_from_query']
            _add(answer, 'QC-DENOM', 'FM-05', message, severity, **detail)
        versioned = version_finding(answer, question)
        if versioned:
            _add(answer, 'QC-VERSION', 'FM-08', versioned[1], streams=versioned[0])
        unused = unused_numbers(question, answer.get('query'), as_of)
        if unused:
            fmea = 'FM-12' if all(_DATE.fullmatch(u) or (len(u) == 4 and u.startswith(('19', '20'))) for u in unused) else 'FM-01'
            _add(answer, 'QC-NUMBERS', fmea, f"The question states {', '.join(unused[:4])} but the query never uses "
                 f"{'it' if len(unused) == 1 else 'them'}; check that the constraint was applied.", unused=unused)
        part = parts_finding(answer, question)
        if part:
            _add(answer, 'QC-PARTS', 'FM-01', part)
        clock = clock_finding(answer, question)
        if clock:
            _add(answer, 'QC-CLOCK', 'FM-07', clock[1], clocks=clock[0])
        if answer.get('query') and isinstance(answer.get('query'), str):
            from . import oracles
            for f in oracles.check(question, answer['query'], as_of, execute, answer=answer):
                if f.get('control') == 'error': continue
                detail = {k: v for k, v in f.items() if k not in ('control', 'fmea', 'severity', 'message')}
                _add(answer, f['control'], f['fmea'], f['message'], f.get('severity', 'warning'), **detail)
        metric_findings(answer, question, as_of, execute)
    except Exception as exc:  # noqa: BLE001 - controls annotate; they must never take an answer down
        answer.setdefault('quality_checks', []).append({'control': 'error', 'fmea': None, 'message': f'{type(exc).__name__}: {exc}'[:200]})
    return answer


# ==================================================================================================== shared post-execution hook
# check(): every answer-checking component on the shared question path, run once on a proposal the checked-proposer cascade
# (proposers.Cascade) has already executed. Order: zero-result probe -> answer guard (suspicion, catalog probe incl. dense
# retrieval, zero contradictions; paid cross-check only live + uncertified) -> unchecked-zero guard -> answer shape review ->
# cascade certificate + gate policy. Heal, never block: accept=False rejects THIS proposal only (the cascade's next proposer
# tries) and is used only for concrete evidence (data found elsewhere, a contradicting value, a wrong shape that cannot be
# reshaped, a calibrated certificate below its threshold); everything else is an annotation. A sub-check that raises is
# recorded in annotations and never stops the others.
import json as _json
import os as _os
import sqlite3 as _sqlite3
import time as _time
from pathlib import Path as _Path
from . import system_one_gate as _s1_gate

CHECK_VERSION = 'shared-answer-checks/1'
PROBE_VERSION = 'zero-result-probe/1'
PROBE_BUDGET_S = 6.0
PROBE_MAX_LITERALS = 8

# family, semantic table, group column (source within the table), date column(s), {column: match kind}
#   eq: case-insensitive equality; like: substring of a received label/name; code: ICD-style prefix; code_json: prefix inside a JSON list
PROBES = (
    ('condition', 'condition', 'source_table', 'event_date', {'concept': 'eq', 'normalized_label': 'like', 'label': 'like', 'icd10': 'code'}),
    ('condition', 'problem_list_entry', None, 'first_recorded', {'problem_key': 'eq', 'label': 'like', 'icd10': 'code'}),
    ('condition', 'encounter_diagnosis', 'source', 'event_date', {'icd10': 'code'}),
    ('condition', 'claim', None, 'service_date', {'principal_diagnosis': 'code', 'diagnosis_codes': 'code_json'}),
    ('condition', 'payer_claim', None, 'service_from', {'principal_diagnosis': 'code', 'diagnosis_codes': 'code_json'}),
    ('condition', 'note_fact', 'stream', 'event_date', {'fact_type': 'eq', 'value': 'eq'}),
    ('measurement', 'measurement', 'source_table', 'event_date', {'concept': 'eq', 'name': 'like', 'component': 'like', 'loinc': 'eq'}),
    ('any', 'concept_patient', 'kind', ('first_date', 'last_date'), {'concept': 'eq'}),
    ('medication', 'medication', 'source_stream', 'event_date', {'drug_class': 'eq', 'drug_name': 'like', 'ingredients': 'like',
                                                                 'therapeutic_category': 'eq', 'rxnorm': 'eq', 'ndc': 'eq'}),
    ('encounter', 'visit', None, 'event_date', {'type_concept': 'eq', 'type': 'like', 'setting': 'eq', 'wellness_visit_type': 'eq'}),
    ('encounter', 'encounter', 'source_table', 'event_date', {'type_concept': 'eq', 'type': 'like'}),
    ('encounter', 'utilization_event', None, 'event_date', {'kind': 'eq'}),
    ('encounter', 'stay', 'stay_source', 'admit_date', {'type': 'like'}),
    ('procedure', 'procedure_line', 'source', 'service_date', {'cpt': 'eq', 'hcpcs': 'eq', 'code': 'eq'}),
    ('procedure', 'claim_line', None, 'service_date', {'cpt': 'eq', 'hcpcs': 'eq', 'local_charge_code': 'eq'}),
    ('procedure', 'payer_claim_line', None, 'service_date', {'cpt': 'eq', 'hcpcs': 'eq'}),
    ('procedure', 'wellness_visit', 'source', 'event_date', {'hcpcs_code': 'eq', 'visit_type': 'eq', 'awv_kind': 'eq'}),
    ('procedure', 'immunization', 'source', 'event_date', {'vaccine': 'like', 'cvx': 'eq', 'code': 'eq', 'product': 'like'}),
)
PROBE_TABLES = {p[1] for p in PROBES}
_ICD = re.compile(r'^[A-TV-Z]\d{2}(?:\.[0-9A-Z]{0,4})?$', re.I)
_IDENT = re.compile(r'^[a-z][a-z0-9_ ]{2,48}$', re.I)
_SQL_LITERAL = re.compile(r"'((?:[^']|'')*)'")
# literal values that name a status, polarity or bookkeeping state, never the subject of a predicate
_NOT_SUBJECT = {'answered', 'partial', 'completed', 'complete', 'final', 'active', 'inactive', 'resolved', 'dispensed', 'paid', 'denied',
                'reversed', 'cancelled', 'canceled', 'booked', 'arrived', 'fulfilled', 'yes', 'no', 'true', 'false', 'null', 'none',
                'unknown', 'male', 'female', 'other', 'principal', 'primary', 'secondary', 'ehr', 'payer_claim', 'claim', 'record',
                'current', 'amended', 'preliminary', 'entered_in_error', 'positive', 'negative', 'count', 'table', 'value',
                'patient_set', 'sql', 'stream', 'payload', 'event_date', 'patient_id'}


def _zero_kind(answer):
    """'zero' | 'empty' | 'all_unknown' | None for an asserted answer."""
    if not isinstance(answer, dict) or answer.get('answer_type') in (None, 'unsupported'): return None
    if answer.get('status') not in ('answered', 'partial'): return None
    t = answer.get('answer_type')
    if t in ('count', 'value'):
        v = answer.get(t)
        if v == 0 and not isinstance(v, bool):
            return 'all_unknown' if (answer.get('unknown_count') or 0) > 0 else 'zero'
        if v is None and (answer.get('unknown_count') or 0) > 0: return 'all_unknown'
        return None
    if t == 'patient_set':
        return None if answer.get('patient_ids') or answer.get('count') else ('all_unknown' if answer.get('unknown_count') else 'empty')
    if t == 'table':
        rows = answer.get('table')
        if isinstance(rows, list) and not rows: return 'empty'
        if isinstance(rows, list) and all(isinstance(r, dict) for r in rows):
            vals = [v for r in rows for v in list(r.values())[1:]]
            if vals and all(v is None or str(v).strip().lower() in ('', 'unknown', 'none') for v in vals): return 'all_unknown'
    return None


def _queries(graph, answer):
    out = [str(answer.get('query'))] if isinstance(answer, dict) and answer.get('query') else []
    for n in (graph or {}).get('nodes') or []:
        p = n.get('parameters') if isinstance(n, dict) else None
        if isinstance(p, dict):
            for k in ('query', 'membership_query', 'unknown_membership_query'):
                if isinstance(p.get(k), str) and p[k] and p[k] not in out: out.append(p[k])
    return out


def _plan_strings(graph):
    """Identifier-like strings of non-SQL plan parameters (cohort concepts, drug classes, encounter concepts)."""
    out = []

    def walk(v, key=None):
        if isinstance(v, dict):
            for k, x in v.items():
                if k not in ('query', 'membership_query', 'unknown_membership_query', 'purpose', 'returns', 'analysis_window'): walk(x, k)
        elif isinstance(v, list):
            for x in v: walk(x, key)
        elif isinstance(v, str) and (_IDENT.match(v) or _ICD.match(v)) and ' ' not in v.strip():
            out.append(v)
    for n in (graph or {}).get('nodes') or []:
        if isinstance(n, dict) and n.get('operation') != 'sql': walk(n.get('parameters'))
    return out


def _literals(sqls, extra=()):
    lits = []
    for sql in sqls:
        code = re.sub(r'--[^\n]*', '', sql)
        for m in _SQL_LITERAL.findall(code):
            lits.append(m.replace("''", "'"))
    lits += list(extra)
    out = []
    for raw in lits:
        v = raw.strip().strip('%').strip()
        if not v or len(v) < 3 or len(v) > 60: continue
        if re.fullmatch(r'[\d.,:\- ]+', v) or re.fullmatch(r'\$\.\w+', v): continue  # numbers, dates, json paths
        if v.lower() in _NOT_SUBJECT or v.lower().startswith('$.'): continue
        if not (_ICD.match(v) or _IDENT.match(v) or re.fullmatch(r'[A-Z0-9]{4,6}', v)): continue
        if v not in out: out.append(v)
    return out[:PROBE_MAX_LITERALS]


def _tables_read(sqls):
    read = set()
    for sql in sqls:
        for t in re.findall(r'\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)', sql, re.I):
            read.add(t.lower())
    return read


def _group_filter(sql, group):
    """Source values the SQL restricts a group column to ({'problem_lists'}), or None when it does not filter on it."""
    if not group: return None
    vals, hit = set(), False
    for m in re.finditer(r'\b' + re.escape(group) + r"\s*=\s*'((?:[^']|'')*)'", sql, re.I):
        hit = True; vals.add(m.group(1))
    for m in re.finditer(r'\b' + re.escape(group) + r'\s+in\s*\(([^)]*)\)', sql, re.I):
        hit = True; vals |= set(_SQL_LITERAL.findall(m.group(1)))
    return vals if hit else None


def _code_prefixes(lit):
    """ICD prefixes a literal stands for: itself when it is a code, or the library concept's code prefixes ('diabetes' -> E08..E13)."""
    if _ICD.match(lit): return [lit.upper()]
    try:
        from .concepts import CONDITIONS
        return list(CONDITIONS.get(lit.lower(), {}).get('icd10') or [])
    except Exception:  # noqa: BLE001 - expansion is an aid only
        return []


def _semantic_target(stack, catalog):
    if catalog is not None and getattr(catalog, 'target', None): return _Path(catalog.target)
    path = getattr(stack, 'path', None)
    return _Path(path) / 'semantic.sqlite3' if path else None


def _probe_db(target):
    db = _sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    deadline = _time.perf_counter() + PROBE_BUDGET_S
    db.set_progress_handler(lambda: 1 if _time.perf_counter() > deadline else 0, 20000)
    return db


def _count_source(db, cols, table, group, date, matchers, lit, period):
    """-> [(group value or table, distinct patients)] with matching rows, or None when no column of the table can match."""
    have = cols.get(table)
    if not have: return None
    clauses, args = [], []
    prefixes = _code_prefixes(lit)
    for col, kind in matchers.items():
        if col not in have: continue
        if kind == 'eq':
            clauses.append(f'lower({col}) = lower(?)'); args.append(lit)
        elif kind == 'like' and len(lit) >= 4 and not _ICD.match(lit) and re.search(r'[a-z]', lit, re.I):
            clauses.append(f'lower({col}) LIKE ?'); args.append('%' + lit.lower() + '%')
        elif kind == 'code' and prefixes:
            for p in prefixes[:12]: clauses.append(f'upper({col}) LIKE ?'); args.append(p.upper() + '%')
        elif kind == 'code_json' and prefixes:
            for p in prefixes[:12]: clauses.append(f'upper({col}) LIKE ?'); args.append('%"' + p.upper() + '%')
    if not clauses: return None
    where = '(' + ' OR '.join(clauses) + ')'
    if period:
        lo, hi = period
        if isinstance(date, tuple) and all(d in have for d in date):
            where += f' AND {date[0]} <= ? AND {date[1]} >= ?'; args += [hi, lo]
        elif isinstance(date, str) and date in have:
            where += f' AND substr({date}, 1, 10) BETWEEN ? AND ?'; args += [lo, hi]
    who = 'count(DISTINCT patient_id)' if 'patient_id' in have else 'count(*)'
    if group and group in have:
        rows = db.execute(f'SELECT {group}, {who} FROM {table} WHERE {where} GROUP BY {group}', args).fetchall()
        return [(str(g), n) for g, n in rows if n]
    n = db.execute(f'SELECT {who} FROM {table} WHERE {where}', args).fetchone()[0]
    return [(table, n)] if n else []


def zero_result_probe(stack, question, graph, answer, *, catalog=None, target=None):
    """Zero-result discovery probe (ledger zero_result_probe_shared). For a 0 / empty / all-unknown answer, each predicate value
    the executed plan filtered on is counted in every semantic source that can hold it (conditions vs problem lists vs encounter
    and claim diagnoses vs labs; medication lists vs orders vs fills vs pharmacy claims; EHR visits vs ADT vs claims; claim lines
    vs wellness / immunization records), in the question's period. A predicate whose sources the answer read hold no matching
    rows while a source it did not read does is where the data lives. Free, read-only, bounded (PROBE_BUDGET_S).
    -> None (not a zero answer) or {'version', 'trigger', 'period', 'tables_read', 'predicates': [{'literal', 'own', 'alternatives',
       'flagged'}], 'flagged': [...], 'complete', 'reason'?}"""
    trigger = _zero_kind(answer)
    if trigger is None: return None
    from .answer_guard import _ABSENCE_Q, period_of
    out = {'version': PROBE_VERSION, 'trigger': trigger, 'period': None, 'tables_read': [], 'predicates': [], 'flagged': [],
           'complete': True}
    if _ABSENCE_Q.search(re.split(r'\bDefinition:', question or '', maxsplit=1, flags=re.I)[0]):  # the question, not its definition text
        out['reason'] = 'the question asks about absence; a zero is a plausible answer'; return out
    sqls = _queries(graph, answer)
    read = _tables_read(sqls)
    lits = _literals(sqls, _plan_strings(graph))
    period = period_of(question)
    out.update(period=list(period) if period else None, tables_read=sorted(read))
    if not lits:
        out['reason'] = 'the plan filtered on no predicate value to look up'; return out
    target = _Path(target) if target else _semantic_target(stack, catalog)
    if target is None or not target.exists():
        out.update(complete=False, reason='no semantic layer to probe'); return out
    db = _probe_db(target)
    try:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        cols = {t: {r[1] for r in db.execute(f'PRAGMA table_info({t})')} for t in PROBE_TABLES if t in names}
        sql_text = ' '.join(sqls)
        try:  # stream / source names select a source; they are not predicates to look up
            streams = {r[0] for r in db.execute('SELECT DISTINCT stream FROM record')} if 'record' in names else set()
        except _sqlite3.Error:
            streams = set()
        lits = [l for l in lits if l not in streams]
        for lit in lits:
            own, alt, own_known = [], [], False
            for family, table, group, date, matchers in PROBES:
                try:
                    found = _count_source(db, cols, table, group, date, matchers, lit, period)
                except _sqlite3.OperationalError as exc:
                    if 'interrupt' in str(exc).lower(): out['complete'] = False; break
                    continue
                if found is None: continue
                if table in read:
                    own_known = True
                    # the answer read only the sources its SQL restricts this table to (source_table = 'problem_lists'), else all
                    named = _group_filter(sql_text, group)
                    for g, n in found:
                        row = {'table': table, 'source': g, 'family': family, 'patients': n}
                        (own if (named is None or g in named) else alt).append(row)
                else:
                    alt += [{'table': table, 'source': g, 'family': family, 'patients': n} for g, n in found]
            alt.sort(key=lambda r: -r['patients'])
            flagged = own_known and not sum(r['patients'] for r in own) and bool(alt)
            out['predicates'].append({'literal': lit, 'own': own, 'alternatives': alt[:8], 'own_known': own_known, 'flagged': flagged})
            if flagged: out['flagged'].append(lit)
            if not out['complete']: break
    finally:
        db.close()
    return out


_FILTER = re.compile(r"\b(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)\s*(?:(=)\s*'((?:[^']|'')*)'|IN\s*\(\s*('(?:[^']|'')*'(?:\s*,\s*'(?:[^']|'')*')*)\s*\))", re.I)
_ALIAS = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_]\w*)(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?", re.I)
_LABEL_COL = re.compile(r'concept|label|category|condition_name|class', re.I)
_CODE_COLS = r'icd10|icd_10|icd10_code|code|dx_code|snomed|loinc|rxnorm|ndc|cpt|hcpcs'
_NOT_ALIAS = {'where', 'join', 'left', 'right', 'inner', 'outer', 'cross', 'on', 'group', 'order', 'limit', 'union', 'using', 'natural'}


def value_existence(stack, graph, answer, *, catalog=None, target=None):
    """Text filters (col = 'x', col IN ('a', 'b')) whose values the column never holds (case-insensitive) AND whose removal makes
    the same plan return rows: the made-up label alone emptied or narrowed the answer (BH-07: codes F20/F25/F31 find patients,
    concept IN ('schizophrenia', 'bipolar') matched none). A label absent everywhere with nothing else filtering is a genuine
    zero and is left to the zero-result probe. -> ['table.column never holds ... (without it the plan returns rows)']."""
    sqls = _queries(graph, answer)
    if not sqls: return []
    target = _Path(target) if target else _semantic_target(stack, catalog)
    if target is None or not target.exists(): return []
    db = _probe_db(target)
    out = []
    try:
        names = {r[0].lower(): r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        cols = {}
        for sql in sqls:
            aliases, used = {}, []
            for t, a in _ALIAS.findall(sql):
                table = names.get(t.lower())
                if table is None: continue
                used.append(table); aliases[t.lower()] = table
                if a and a.lower() not in _NOT_ALIAS: aliases[a.lower()] = table
            for m in _FILTER.finditer(sql):
                alias, col, eq, val, listed = m.groups()
                table = aliases.get(alias.lower()) if alias else (used[0] if len(set(used)) == 1 else None)
                if table is None: continue
                if table not in cols: cols[table] = {r[1].lower(): (r[1], (r[2] or '').upper()) for r in db.execute(f'PRAGMA table_info("{table}")')}
                info = cols[table].get(col.lower())
                if info is None or any(k in info[1] for k in ('INT', 'REAL', 'NUM', 'DATE', 'BOOL')): continue
                values = [val] if eq else re.findall(r"'((?:[^']|'')*)'", listed)
                values = [v.replace("''", "'") for v in values if v and not re.fullmatch(r'[-+]?\d+(?:\.\d+)?|\d{4}-\d{2}-\d{2}.*', v)]
                if not values: continue
                try:
                    hit = db.execute(f'SELECT 1 FROM "{table}" WHERE lower("{info[0]}") IN ({",".join("?" * len(values))}) LIMIT 1',
                                     [v.lower() for v in values]).fetchone()
                except _sqlite3.Error:
                    continue  # time budget or an unreadable column: no verdict
                relaxed = sql[:m.start()] + '1=1' + sql[m.end():]
                if hit is not None:  # the label exists; beside a code filter on the same table it must not drop coded rows
                    if not (_LABEL_COL.fullmatch(info[0]) and re.search(r'\b(?:' + re.escape(alias or table) + r'\.)?(?:' + _CODE_COLS + r')\s+(?:LIKE|=|IN)\b', relaxed, re.I)):
                        continue
                    try:
                        before, after = db.execute(sql).fetchone(), db.execute(relaxed).fetchone()
                    except _sqlite3.Error:
                        continue
                    if before and after and isinstance(before[0], (int, float)) and isinstance(after[0], (int, float)) and after[0] > before[0]:
                        out.append(f"{table}.{info[0]} = {', '.join(repr(v) for v in values[:4])} drops coded rows the plan's own code filter matches "
                                   f"({before[0]} with the label, {after[0]} without)")
                    continue
                where = re.split(r'\bWHERE\b', relaxed, maxsplit=1, flags=re.I)
                if len(where) < 2 or not re.search(r"\bLIKE\b|\bIN\s*\(|\bBETWEEN\b|[<>]|(?<![<>!])=(?!\s*1\b)", where[1].replace('1=1', ''), re.I):
                    continue  # nothing else filters: a label absent everywhere is a genuine zero (zero-result probe's call)
                try:  # the same plan without this filter: rows there mean the label alone produced the answer
                    rows = db.execute(relaxed).fetchmany(2)
                except _sqlite3.Error:
                    continue
                if any(r and r[0] not in (None, 0, '0') for r in rows):
                    out.append(f"{table}.{info[0]} never holds {', '.join(repr(v) for v in values[:4])} (without that filter the plan returns rows)")
    finally:
        db.close()
    return list(dict.fromkeys(out))


# ---- diagnosis source coverage ----------------------------------------------------------------------------------------------
COVERAGE_VERSION = 'diagnosis-source-coverage/1'
DX_SOURCES = ('condition', 'payer_claim_diagnosis')  # the two sources of the one condition-evidence rule (decision_context; diagnosis_any v2)
COVERAGE_MIN_GAIN = 2      # diagnosis_any must hold more than the answer + 2 patients ...
COVERAGE_MIN_RATIO = 1.1   # ... and more than 1.1x the answer: a gap of a patient or two is noise, not a missing source
# a question that names one source asks for that source alone ("on a paid claim", "problem list entries", "ED principal diagnosis")
_ONE_SOURCE_Q = re.compile(r'\b(?:problem[- ]list|payer[- ]reported|(?:on|in|from) (?:a |the )?(?:paid |payer |medical )?claims?|claims?[- ](?:only|based)|'
                           r'claim diagnos\w+|encounter diagnos\w+|visit diagnos\w+|(?:principal|primary|admitting|discharge|working|ed|er) diagnos\w+|'
                           r'adt diagnos\w+|ehr (?:condition|conditions) (?:only|records)|condition records)\b', re.I)
# icd10 / icd10_category compared to code literals (through substr/upper/replace wrappers); NOT LIKE / NOT IN are exclusions
_ICD_FILTER = re.compile(r"\bicd10(?:_category)?\b(?:\s*,[^()=<>]*?)?\s*\)*\s*(NOT\s+)?(?:(LIKE|=)\s*'([^']*)'|IN\s*\(([^)]*)\))", re.I)
_PATIENT_COUNT = re.compile(r'\bcount\s*\(\s*distinct\s+(?:\w+\.)?patient_id\s*\)', re.I)


def icd_patterns(sql):
    """ICD-10 code prefixes (dots dropped, upper case) the SQL requires via icd10 / icd10_category LIKE, =, IN; exclusions left out."""
    code = re.sub(r'--[^\n]*', '', sql or '')
    out = []
    for m in _ICD_FILTER.finditer(code):
        if m.group(1): continue
        for lit in ([m.group(3)] if m.group(3) is not None else _SQL_LITERAL.findall(m.group(4) or '')):
            v = lit.replace("''", "'").replace('.', '').replace('%', '').strip().upper()
            if re.fullmatch(r'[A-TV-Z][0-9A-Z]{1,6}', v) and v not in out: out.append(v)
    return out


def _to_diagnosis_any(sql):
    """The plan's SQL with every diagnosis-source table it reads replaced by diagnosis_any (alias kept), or None when it reads none."""
    n = 0

    def sub(m):
        nonlocal n
        n += 1
        word = m.group(3)
        if word and word.lower() not in _NOT_ALIAS: return f'{m.group(1)} diagnosis_any {word}'  # the plan's alias stays
        return f'{m.group(1)} diagnosis_any AS {m.group(2)}' + (f' {word}' if word else '')  # qualified refs (condition.icd10) still resolve
    out = re.sub(r'\b(FROM|JOIN)\s+(' + '|'.join(DX_SOURCES) + r')\b(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?', sub, sql, flags=re.I)
    return out if n else None


def diagnosis_source_coverage(stack, question, graph, answer, *, catalog=None, target=None):
    """Source-coverage check for diagnosis counts. A documented diagnosis is an ICD-10 code on a paid payer claim, an EHR problem-list
    entry, an EHR condition record or an encounter diagnosis; the free SQL tiers often count `condition` alone (EHR condition
    records) and undercount (opioid use disorder: 1 patient in condition, 27 in diagnosis_any). When an executed plan counts or lists
    patients from diagnosis sources, filtered by ICD-10 codes, without reading diagnosis_any / concept_patient or all four sources,
    the same plan is re-run on diagnosis_any (each diagnosis table read swapped for it, every other filter kept; else, for a plan that
    reads no other table, the distinct patients with the same code patterns in the question's period). Materially larger
    (> answer + COVERAGE_MIN_GAIN and > COVERAGE_MIN_RATIO x answer) -> flagged. Read-only, bounded (PROBE_BUDGET_S).
    -> None (not applicable) or {'version', 'reads', 'patterns', 'answer', 'diagnosis_any', 'method', 'flagged'}"""
    if not isinstance(answer, dict) or answer.get('status') not in ('answered', 'partial'): return None
    t = answer.get('answer_type')
    if t not in ('count', 'patient_set', 'value'): return None
    if _ONE_SOURCE_Q.search(question or ''): return None
    sqls = _queries(graph, answer)
    if not sqls: return None
    plan = None
    for sql in sqls:
        read = _tables_read([sql])
        if read & {'diagnosis_any', 'concept_patient'} or set(DX_SOURCES) <= read: continue
        mine = [d for d in DX_SOURCES if d in read]
        patterns = icd_patterns(sql)
        if not mine or not patterns: continue
        if not (_PATIENT_COUNT.search(sql) or t == 'patient_set' or re.search(r'select\s+distinct\s+(?:\w+\.)?patient_id', sql, re.I)): continue
        plan = (sql, mine, patterns, read); break
    if plan is None: return None
    sql, mine, patterns, read = plan
    n = (len(answer.get('patient_ids') or []) or answer.get('count')) if t == 'patient_set' else answer.get(t)
    if not _numeric(n): return None
    out = {'version': COVERAGE_VERSION, 'reads': mine, 'patterns': patterns, 'answer': n, 'diagnosis_any': None, 'method': None, 'flagged': False}
    target = _Path(target) if target else _semantic_target(stack, catalog)
    if target is None or not target.exists(): return out
    from . import semantic_layer
    db = _probe_db(target)
    try:
        if semantic_layer.ensure_diagnosis_view(db) is None: return out  # no diagnosis source to compare with
        rewritten = _to_diagnosis_any(sql)
        total = None
        try:
            rows = db.execute(rewritten).fetchmany(200001) if rewritten else []
            if rows and t == 'patient_set': total, out['method'] = len({r[0] for r in rows}), 'plan_on_diagnosis_any'
            elif rows and len(rows) == 1 and _numeric(rows[0][0]): total, out['method'] = rows[0][0], 'plan_on_diagnosis_any'
        except _sqlite3.Error as exc:
            if 'interrupt' in str(exc).lower(): return out
        if total is None and read <= set(DX_SOURCES):  # the plan filters nothing outside the diagnosis table: count the codes themselves
            from .answer_guard import period_of
            period = period_of(question)
            where = '(' + ' OR '.join("replace(icd10, '.', '') LIKE ?" for _ in patterns) + ')'
            args = [p + '%' for p in patterns]
            if period: where += ' AND substr(event_date, 1, 10) BETWEEN ? AND ?'; args += list(period)
            try:
                total, out['method'] = db.execute(f'SELECT count(DISTINCT patient_id) FROM diagnosis_any WHERE {where}', args).fetchone()[0], \
                    'codes_in_diagnosis_any'
            except _sqlite3.Error:
                total = None
        if total is None: return out
        out['diagnosis_any'] = total
        out['flagged'] = total > n + COVERAGE_MIN_GAIN and total > COVERAGE_MIN_RATIO * n
    finally:
        db.close()
    return out


def _coverage_reason(c):
    return (f"diagnosis source coverage: this plan reads {', '.join(c['reads'])} (one of the two condition-evidence sources: EHR condition records, "
            f"counted payer claim diagnoses) and counts {c['answer']} patients with {', '.join(c['patterns'][:6])}; "
            f"diagnosis_any holds {c['diagnosis_any']} patients with these codes; count by ICD-10 code from diagnosis_any "
            f"(all sources), or by library concept from concept_patient")


def _where(rows, k=3):
    return ', '.join(f"{r['patients']} patients in {r['table']}" + (f" ({r['source']})" if r['source'] != r['table'] else '') for r in rows[:k])


def _probe_reasons(probe):
    out = []
    for p in probe.get('predicates') or []:
        if not p['flagged']: continue
        when = f" in {probe['period'][0][:4]}" if probe.get('period') and probe['period'][0][:4] == probe['period'][1][:4] else ''
        out.append(f"zero-result probe: {p['literal']!r} matches no rows{when} in the sources this answer read "
                   f"({', '.join(sorted({r['table'] for r in p['own']}) or [t for t in probe['tables_read'] if t in PROBE_TABLES]) or 'its tables'}), "
                   f"but {_where(p['alternatives'])}; read those sources")
    return out


# ---- the hook ---------------------------------------------------------------------------------------------------------------
def _route(receipt):
    return (receipt or {}).get('route') if isinstance(receipt, dict) else None


def _profile(stack, profile):
    if profile is not None: return profile
    try:
        from .source_profile import load
        return load(stack.path)
    except Exception:  # noqa: BLE001 - no profile means the catalog probe is skipped (annotated)
        return None


def _guard(stack, question, graph, receipt, answer, profile, state, verdict):
    """answer_guard_suspicion + answer_guard_dense_catalog (free parts): suspicion -> full-catalog probe (lexical + dense RRF) ->
    zero contradictions. Rejects only for an unread likely source or a concrete contradiction; a source the answer read that
    returned nothing, or no source anywhere, is annotated."""
    from . import answer_guard as AG
    if not AG.enabled():
        verdict['annotations'].append('answer guard off (AGERE_ANSWER_GUARD=off)'); return
    found = AG.suspicion(question, answer)
    claim = AG.decline_claim(answer, receipt) if not found else None
    state['suspicion'] = [f['kind'] for f in found]
    if not found and not claim: return
    if profile is None:
        verdict['annotations'].append('answer guard: ' + '; '.join(f['message'] for f in found) + ' (no source profile to probe)'); return
    p = AG.probe(question, profile, path=getattr(stack, 'path', None), encoder=getattr(getattr(stack, 'retriever', None), 'encoder', None))
    used = AG.used_sources(graph, answer, profile.get('streams') or {})
    for c in p['candidates']: c['used'] = c['stream'] in used
    kinds = {f['kind'] for f in found}
    zero_only = bool(found) and kinds <= AG.ZERO_KINDS
    contra = AG.zero_contradictions(answer, profile, used, p['period'], getattr(stack, 'path', None)) if zero_only else []
    likely = [c for c in p['candidates'] if c['likely']]
    state['guard_probe'] = {'streams_searched': p['streams_searched'], 'dense': bool(p.get('dense')),
                            'likely': [c['stream'] for c in likely], 'used': sorted(used)[:40],
                            'contradictions': [{k: c.get(k) for k in ('stream', 'contradiction')} for c in contra]}
    if claim:
        unread = [c for c in likely if not c['used']]
        if unread:
            verdict['accept'] = False
            verdict['reasons'].append(f'answer guard: the answer declined ({claim[:120]}) but ' + '; '.join(AG.describe_source(c) for c in unread[:2]))
        else:
            verdict['annotations'].append(f"Catalog check: searched all {p['streams_searched']} received streams; no unread source holds it, so the decline stands.")
        return
    if contra:
        verdict['accept'] = False
        verdict['reasons'].append('answer guard: ' + '; '.join(AG.describe_source(c) for c in contra[:2]))
        return
    unread = [c for c in likely if not c['used']]
    if unread:
        verdict['accept'] = False
        verdict['reasons'].append('answer guard: ' + found[0]['message'] + ', yet ' + '; '.join(AG.describe_source(c) for c in unread[:2])
                                  + " that this answer didn't read")
        return
    if zero_only and not likely:
        state['zero_confirmed'] = 'catalog'
        what = ', '.join(p['terms'][:6]) or 'the question terms'
        verdict['annotations'].append(f"Catalog check: searched all {p['streams_searched']} received streams (names, fields, values and codes) "
                                      f"for {what}" + (f" in {p['period'][0][:4]}" if p['period'] else '') + '; no unread source has matching records, so the zero stands.')
    elif zero_only:
        verdict['annotations'].append('answer guard: ' + '; '.join(AG.describe_source(c) for c in likely[:2]) + ', and this answer read it and found none')
    elif kinds - {'money_small'} and not likely:
        # a shape/units suspicion with no catalog evidence: a concrete misfit (dollars answered with a count, % outside 0-100)
        hard = [f for f in found if f['kind'] in ('money_shape', 'rate_range', 'rate_shape')]
        if hard:
            verdict['accept'] = False
            verdict['reasons'].append('answer guard: ' + '; '.join(f['message'] for f in hard))
        else:
            verdict['annotations'].append('answer guard: ' + '; '.join(f['message'] for f in found))
    else:
        verdict['annotations'].append('answer guard: ' + '; '.join(f['message'] for f in found))


def spec_confirmed(receipt):
    """True when the answer's spec was confirmed part by part by System One (spec_path); such an answer needs no result-summary cross-check."""
    spec = (receipt or {}).get('spec_verification') if isinstance(receipt, dict) else None
    return bool(isinstance(spec, dict) and spec.get('confirmed') and spec.get('verdict') == 'confirmed')


def _cross_check(stack, question, graph, receipt, answer, profile, ledger, hosted, state, verdict, result=None, claude_ok=True):
    """answer_guard_cross_check for an uncertified answer, live stacks only. System One first (system_one_cross_check: Jev and
    Perplexity each judge it): both confirm -> cross-checked, no Claude call; both refute -> concrete contradiction; the families
    split -> the independent Claude path below, only for a comparable answer and only when Claude is available. Agreement and
    unavailability annotate; a disagreement from a non-relaxed independent path is a concrete contradiction."""
    from . import answer_guard as AG
    if _os.environ.get('AGERE_CROSS_CHECK', 'on') == 'off': return 'off'
    if spec_confirmed(receipt):  # the spec was checked part by part against the question (spec_verify): the result-summary cross-check is replaced
        verdict['annotations'].append('Verified part by part against the question by System One (spec verification); the result-summary cross-check is not repeated.')
        return 'spec_verified'
    if not getattr(stack, 'allow_live', False) or ledger is None: return 'not live'
    if state.get('certified'): return 'certified'
    if AG._asserted(answer) and _s1_gate.ready(stack, 'AGERE_S1_CROSS_CHECK'):
        from . import system_one_cross_check as X
        s1 = X.cross_check(stack, question, graph, result or {'answer': answer}, ledger=ledger)
        verdict['cost']['new_paid_api_calls'] += s1['calls']; verdict['cost']['usd'] += s1['cost_usd']
        verdict['system_one_cross_check'] = s1
        partial = sorted(f for f, v in (s1.get('votes') or {}).items() if isinstance(v, dict) and v.get('label') == 'answers_partially')
        if s1['verdict'] == 'confirmed' and partial:  # an uncertified plan System One says covers only part of the question is not shipped
            verdict['accept'] = False
            verdict['reasons'].append(f'cross-check: {", ".join(partial)} judged this uncertified plan to answer only part of the question '
                                      f'({X.describe(s1)}); the next proposer tries'); return 'partial'
        if s1['verdict'] == 'confirmed':
            state['zero_confirmed'] = state.get('zero_confirmed') or 'system_one_cross_check'
            verdict['annotations'].append(f'Cross-checked by System One ({X.describe(s1)}); no Claude call needed.'); return 'agree'
        if s1['verdict'] == 'refuted':
            verdict['accept'] = False
            verdict['reasons'].append(f'cross-check: both System One families judged it a different question ({X.describe(s1)})'); return 'disagree'
        no = [f for f, v in (s1.get('votes') or {}).items() if isinstance(v, dict) and v.get('label') == X.NO]
        if len(no) == len(X.FAMILIES):  # both families lean 'different question', each below its own reject threshold
            verdict['accept'] = False
            verdict['reasons'].append(f'cross-check: both System One families lean toward a different question ({X.describe(s1)}); the next proposer tries')
            return 'disagree'
        if s1['verdict'] != 'split':
            verdict['annotations'].append(f'Cross-check: System One could not decide ({X.describe(s1)}).'); return 'unavailable'
        if not claude_ok:
            verdict['annotations'].append(f'Cross-check: System One families disagree ({X.describe(s1)}); the Claude judge was skipped '
                                          '(Claude unavailable earlier in this question).'); return 'split'
    if not AG.comparable(answer): return 'not comparable'
    pending = AG.PendingCheck(stack, question, receipt, ledger=ledger, profile=profile, hosted=hosted,
                              deadline=_time.perf_counter() + AG.budget_s())
    pending.provide(answer)
    out = pending.result()
    if out is None:
        verdict['annotations'].append(f'Cross-check: not finished within {AG.budget_s()} s; the answer is not cross-checked.'); return 'timed_out'
    if out[0] is None:
        _, why, usd, n = out
        verdict['cost']['new_paid_api_calls'] += n or 0; verdict['cost']['usd'] += usd or 0.0
        verdict['annotations'].append(f'Cross-check unavailable: {why}'); return 'unavailable'
    label, g, r, res, usd, n = out
    verdict['cost']['new_paid_api_calls'] += n or 0; verdict['cost']['usd'] += usd or 0.0
    if res is None:
        verdict['annotations'].append('Cross-checked: verification v2 already found independent agreement.'); return 'agree'
    other = (res or {}).get('answer') or {}
    if AG._asserted(other) and AG._same(answer, other):
        state['zero_confirmed'] = state.get('zero_confirmed') or 'cross_check'
        verdict['annotations'].append(f'Cross-checked: an independent path ({label}) gives the same answer.'); return 'agree'
    if not AG._asserted(other) or label.endswith('(relaxed)'):
        verdict['annotations'].append(f'Cross-check: the independent path ({label}) gave no comparable answer.'); return 'unavailable'
    verdict['accept'] = False
    verdict['reasons'].append(f"cross-check: an independent path ({label}) answered {_json.dumps(AG._summary(other), default=str)[:160]}, "
                              f"this proposal answered {_json.dumps(AG._summary(answer), default=str)[:160]}")
    return 'disagree'


def _unchecked_zero(receipt, answer, state, verdict):
    """unchecked_zero_guard (together.decide): a zero/empty nothing independent confirmed ships at low confidence, never as a
    confident 0. Confirmations: analyst-executed answer, catalog check, zero-result probe with every predicate looked up,
    cross-check agreement, certified tier."""
    from .together import _empty_answer
    if _os.environ.get('AGERE_UNCHECKED_ZERO', 'abstain') != 'abstain' or not _empty_answer(answer): return None
    probe = verdict.get('probe') or {}
    if _route(receipt) == 'hybrid_analysts': state['zero_confirmed'] = state.get('zero_confirmed') or 'independent_analysts'
    if probe.get('complete') and probe.get('predicates') and not probe.get('flagged'):
        state['zero_confirmed'] = state.get('zero_confirmed') or 'zero_result_probe'
    if state.get('certified'): state['zero_confirmed'] = state.get('zero_confirmed') or 'certified_tier'
    if state.get('zero_confirmed'): return state['zero_confirmed']
    verdict['annotations'].append('Unchecked zero: no independent check confirmed this empty result (low confidence).')
    verdict['confidence'] = 'low'
    return 'unchecked'


def _shape(question, graph, result, verdict):
    """answer_shape_review: free conformance; unambiguous reshape replaces the answer, a misfit rejects the proposal."""
    from . import answer_shape as S
    answer = result['answer']
    if answer.get('withheld') is not None and answer.get('answer_type') == 'unsupported':
        verdict['accept'] = False
        verdict['reasons'].append('answer shape: ' + str((answer.get('missing') or ['wrong-shaped answer withheld'])[-1])[:300])
        return 'withheld'
    fixed, info = S.conform(question, answer, operation=S._final_operation(graph))
    if info['status'] == 'misfit':
        verdict['accept'] = False
        verdict['reasons'].append(f"answer shape: the question asks for a {info.get('requested')}; the proposal returned a "
                                  f"{info.get('answered')} ({info.get('reason')})")
    elif info['status'] == 'reshaped':
        fixed = {k: v for k, v in fixed.items() if k != 'headline'}  # proposers.execute restates the headline
        verdict['result'] = {**result, 'answer': fixed}
        verdict['annotations'].append(f"Answer shape: reshaped ({info.get('how')}).")
    elif info.get('rounded') is not None:
        verdict['result'] = {**result, 'answer': fixed}
    return info['status']


def _certificates(receipt, state, verdict):
    """cascade_certificates + gate_policy.assess: annotate certified / uncalibrated; only a calibrated tier scoring below its
    certified threshold rejects (the certificate itself defers to the next tier). 'not_certified' tiers propose by design."""
    from . import cascade, gate_policy
    cert = cascade.certify_route(receipt) if isinstance(receipt, dict) else None
    gate = gate_policy.assess(receipt) if isinstance(receipt, dict) else None
    status = (cert or {}).get('status') or ('certified' if (gate or {}).get('level') == 'verified' else 'uncalibrated')
    state['certified'] = status == 'certified' or (gate or {}).get('level') == 'verified'
    verdict['certificate'] = {'status': status, **({k: cert.get(k) for k in ('tier', 'score', 'threshold', 'alpha', 'delta', 'bound',
                                                                             'n_calibration', 'reason') if cert.get(k) is not None} if cert else {}),
                              'gate_level': (gate or {}).get('level'), 'gate_reason': (gate or {}).get('reason')}
    if cert and cert.get('status') == 'below_threshold':
        verdict['accept'] = False
        verdict['reasons'].append('certificate: ' + cert['statement'])
    elif cert and cert.get('status') == 'certified':
        verdict['annotations'].append('Certified: ' + cert['statement'] + '.')
    else:
        verdict['annotations'].append(f"Certificate: {status}" + (f" ({(cert or gate or {}).get('reason')})" if (cert or gate) else '') + '.')
    return status


# Claude tiers that only choose a governed metric or engine (its template computes): their answers need no confirmation.
GOVERNED_CLAUDE_ROUTERS = frozenset({'metric_router', 'operator_router'})
# Checks whose failure to run leaves the answer unverified (the rest annotate).
MANDATORY_CHECKS = frozenset({'zero_result_probe_shared', 'filter_value_existence', 'diagnosis_source_coverage',
                              'local_sql_confirmation', 'answer_shape_review', 'decision_context'})


def check(stack, question, graph, receipt, result, *, ledger=None, catalog=None, profile=None, hosted=None, claude_ok=True, tier=None):
    """Every answer check on one executed proposal. result = analysis_graph execute result (result['answer']).
    ledger: BudgetLedger for the paid cross-check (live only); catalog: request_plan.Catalog (its semantic target);
    profile: source profile (default source_profile.load(stack.path)); hosted: answer_guard.hosted_sql replacement (tests).
    -> {'accept', 'reasons', 'annotations', 'probe', 'checks_run', 'certificate', 'confidence'?, 'result' (possibly reshaped),
        'cost': {'new_paid_api_calls', 'usd'}, 'version'}"""
    verdict = {'version': CHECK_VERSION, 'accept': True, 'reasons': [], 'annotations': [], 'probe': None, 'checks_run': [],
               'certificate': None, 'result': result, 'cost': {'new_paid_api_calls': 0, 'usd': 0.0}}
    answer = (result or {}).get('answer') if isinstance(result, dict) else None
    if not isinstance(answer, dict):
        verdict['annotations'].append('answer checks: no answer object to check'); return verdict
    state = {}

    def run(component, fn):
        verdict['checks_run'].append(component)
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - one check failing never stops the others (heal, don't block)
            verdict['annotations'].append(f'{component} failed: {type(exc).__name__}: {exc}'[:300])
            if component in MANDATORY_CHECKS:
                # A check that guards correctness and could not run cannot pass the answer: this proposal is declined and
                # the next proposer tries (Codex review 2026-10-23: mandatory checks failed open).
                verdict['accept'] = False
                verdict['reasons'].append(f'{component} could not run ({type(exc).__name__}); the answer was not verified')
            return None

    def probe():
        p = zero_result_probe(stack, question, graph, answer, catalog=catalog)
        verdict['probe'] = p
        if p and p['flagged']:
            verdict['accept'] = False
            verdict['reasons'] += _probe_reasons(p)
        elif p and p.get('predicates'):
            verdict['annotations'].append('Zero-result probe: ' + ('; '.join(
                f"{q['literal']!r} has no matching rows in any other source" if not q['alternatives'] else
                f"{q['literal']!r} also in {_where(q['alternatives'], 2)}" for q in p['predicates'][:4]))
                + ('' if p['complete'] else ' (probe stopped at its time budget)') + '.')
        elif p and p.get('reason'):
            verdict['annotations'].append('Zero-result probe: ' + p['reason'] + '.')
        return p
    run('zero_result_probe_shared', probe)
    def values():
        missing = value_existence(stack, graph, answer, catalog=catalog)
        if missing:  # a filter on labels the data never uses: refuse, whatever the result (BH-07 counted 0 schizophrenia patients)
            verdict['accept'] = False
            verdict['reasons'] += [f'value check: the plan filters on values the warehouse never holds ({m})' for m in missing]
        return missing
    run('filter_value_existence', values)
    def coverage():
        c = diagnosis_source_coverage(stack, question, graph, answer, catalog=catalog)
        verdict['diagnosis_coverage'] = c
        if c and c['flagged']:  # heal, don't block: the next proposer reads every diagnosis source
            verdict['accept'] = False
            verdict['reasons'].append(_coverage_reason(c))
        return c
    run('diagnosis_source_coverage', coverage)
    def decision():
        """Mandatory (Mike, 2026-10-23): the answer follows the decision context (living patients, latest labs, all-source events)."""
        from . import decision_context as DC
        state = DC.current()
        if state is None: return None  # not under a question scope (a tool or a test calling check directly)
        target = _semantic_target(stack, catalog)
        db = _probe_db(target) if target is not None and target.exists() else None
        try:
            found = DC.verify(question, answer, state, db, _queries(graph, answer))
        finally:
            if db is not None: db.close()
        DC.stamp(answer, question, state)
        state.reset_run()
        verdict['decision_context'] = {'version': DC.VERSION, **found}
        if not found['ok']:  # heal, don't block: the next proposer tries
            verdict['accept'] = False
            verdict['reasons'] += [f'decision context: {r}' for r in found['reasons']]
        return found
    run('decision_context', decision)
    prof = _profile(stack, profile)
    run('answer_guard_suspicion', lambda: _guard(stack, question, graph, receipt, answer, prof, state, verdict))
    verdict['checks_run'].append('answer_guard_dense_catalog')  # inside the guard probe (AG.probe dense='auto')
    # certificates are read before the paid cross-check and the unchecked-zero guard (both depend on certification);
    # they are reported in the documented order below
    run('cascade_certificates', lambda: _certificates(receipt, state, verdict))
    verdict['checks_run'].remove('cascade_certificates')
    if verdict['accept']:
        run('answer_guard_cross_check', lambda: _cross_check(stack, question, graph, receipt, answer, prof, ledger, hosted, state, verdict,
                                                             result, claude_ok))
    def local_sql_confirmed():  # uncalibrated planners ship only when System One confirms: free planners (stage C), local-model
        # SQL, and Claude planners that write their own plan (stage E, except the governed metric/engine routers). 2026-10-23: a
        # Haiku/Sonnet verified-SQL answer with a failed certificate and a weak System One vote shipped a wrong count.
        from .proposers import STAGES, stage_of
        stage = STAGES[stage_of(tier)][0] if tier is not None else None
        planner = (_route(receipt) == 'local_model_sql' or stage == 'free_planners'
                   or (stage == 'claude' and tier not in GOVERNED_CLAUDE_ROUTERS))
        if not planner or state.get('certified') or not verdict['accept'] or spec_confirmed(receipt): return None
        s1 = verdict.get('system_one_cross_check') or {}
        if s1.get('verdict') != 'confirmed':
            verdict['accept'] = False
            verdict['reasons'].append('an uncalibrated free planner\'s answer is shipped only when System One confirms it answers the question; '
                                      f"cross-check {s1.get('verdict') or 'did not run'}; the next proposer tries")
        return s1.get('verdict')
    run('local_sql_confirmation', local_sql_confirmed)
    run('unchecked_zero_guard', lambda: _unchecked_zero(receipt, answer, state, verdict))
    from . import release_drift
    run('release_drift_guard', lambda: release_drift.annotate(stack, question, graph, answer))  # QC-DRIFT: definitions that drifted this release
    run('answer_shape_review', lambda: _shape(question, graph, result, verdict))
    run('source_defect_annotation', lambda: verdict['annotations'].extend(__import__(__package__ + '.dq_detectors', fromlist=['annotate']).annotate(stack, question, graph, answer)))
    if verdict['accept'] and not spec_confirmed(receipt): _s1_gate.apply_check(stack, question, graph, verdict, ledger=ledger)  # System One: answers the question as asked?
    from . import normalization_check as _norm  # Jev second check on the normalized codes the plan reads (annotation only)
    run('jev_normalization_check', lambda: _norm.annotate_verdict(stack, graph, answer, verdict, ledger=ledger))
    from . import answer_verifier as _av, semantic_entropy as _se, label_choice as _lc  # learned verifier, sampled-agreement entropy, score label
    run('learned_answer_verifier', lambda: _av.annotate(question, receipt, answer, verdict))
    run('semantic_entropy_probe', lambda: _se.check_answer(receipt, answer, verdict))
    run('label_choice_audit', lambda: _lc.annotate(graph, answer, verdict))
    verdict['checks_run'].append('cascade_certificates')
    verdict['reasons'] = list(dict.fromkeys(verdict['reasons']))
    verdict['annotations'] = list(dict.fromkeys(verdict['annotations']))
    return verdict


def gate(cascade, name, graph, receipt, result):
    """proposers.Cascade hook: run check() on an executed proposal. Rejected -> cascade.reject(name, reasons) and None (the next
    proposer tries); accepted -> the (possibly reshaped) result with annotations in answer['limitations'], the probe and the
    verdict in result['answer_checks'] and receipt['answer_checks']. Paid cross-check spend goes to cascade.spend."""
    live = getattr(cascade.stack, 'allow_live', False)
    verdict = check(cascade.stack, cascade.question, graph, receipt, result, ledger=cascade.ledger if live else None,
                    catalog=cascade.catalog if getattr(cascade.catalog, 'target', None) else None,
                    claude_ok=not getattr(cascade, 'claude_down', None), tier=name)
    if verdict['cost']['new_paid_api_calls'] or verdict['cost']['usd']:
        cascade.spend(verdict['cost']['new_paid_api_calls'], verdict['cost']['usd'])
    summary = {k: verdict[k] for k in ('version', 'accept', 'reasons', 'annotations', 'probe', 'checks_run', 'certificate', 'confidence', 'system_one',
                                       'system_one_cross_check', 'normalization_check', 'answer_verifier', 'semantic_entropy', 'label_choice', 'diagnosis_coverage',
                                       'decision_context') if k in verdict}
    if isinstance(receipt, dict): receipt['answer_checks'] = summary
    if not verdict['accept']:
        cascade.reject(name, verdict['reasons'] or ['answer checks rejected the proposal']); return None
    out = dict(verdict['result'])
    answer = dict(out['answer'])
    answer['limitations'] = list(dict.fromkeys([*(answer.get('limitations') or []), *verdict['annotations']]))
    if verdict.get('confidence') == 'low': answer.setdefault('confidence', 'low')
    out['answer'] = answer
    out['answer_checks'] = summary
    return out
