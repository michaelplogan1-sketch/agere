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
    if os.environ.get('AGERE_METRIC_LINT', 'repair') == 'off': return None
    from . import metrics
    r = metrics.resolve({'question': question, 'as_of': as_of})
    if not r: return None
    query = answer.get('query') if isinstance(answer.get('query'), str) else None
    found = metrics.lint(question, query, as_of, source=execute, resolution=r) if query else []
    answer['computed_as'] = {'metric': r['id'], 'version': r['version'], 'variant': r.get('variant'), 'layer': metrics.VERSION,
                             'definition': r['metric']['summary'], 'conforms': not found if query else None,
                             'citations': [c['url'] for c in r.get('citations') or []][:3]}
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
