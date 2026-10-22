"""Does a proposed executable plan still carry what the ORIGINAL question literally asked for?

Every proposer outside the fully-consumed grammar (rungs of the former cost ladder, local models, routers, hosted SQL,
analysts) must pass this before its plan is accepted. The check reads only the executable parts of a proposal (SQL with
comments removed, engine parameters, typed request predicates and outputs), never its restatement of the question, and
requires each literal element of the question to appear there: numbers and years, months, negation, named qualifiers and
concepts, grouping, denominators, averages, ordering and relative time windows. After execution, the answer's shape must
fit the requested output (shape_misfit).

This is a necessary check, not a proof of equivalence: a plan can keep every literal element and still use a different
definition. It exists so that no proposer silently answers a different, reduced question (2026-10-19 protection).
"""
import json
import re

from . import local_normalizer as N

VERSION = 'literal-question-check/1'

# Executable-side spellings of each qualifier/concept, in addition to the question-side pattern (local_normalizer).
ALIASES = {
    'female': r"'f'|\bfemale|\bwom[ae]n\b", 'male': r"'m'|(?<!fe)\bmale\b|\bmen\b",
    'living': r'death|deceased|alive|living|died', 'deceased': r'death|deceased|died|dead',
    'by_group': r'group by|"group_by"\s*:\s*(?:\[\s*)?"|"groups?"\s*:|"by"\s*:\s*"|partition by',
    'started': r'start|initiat|new|first|min\(|index', 'positive': r'positive|\bpos\b|>=|>|= ?1\b|true',
    'heart_failure': r'heart.?failure|\bhf\b|\bchf\b|i50', 'ckd': r'\bckd|kidney|n18', 'afib': r'fibrillation|afib|\baf\b|i48',
    'cad': r'coronary|\bcad\b|i25', 'mi': r'infarction|\bmi\b|i21', 'hypertension': r'hypertens|\bhtn\b|i10',
    'a1c': r'a1c|4548-4|17856-6', 'beta_blocker': r'beta.?block', 'sglt2': r'sglt', 'flu': r'flu|influenza',
    'uacr': r'uacr|albumin|acr', 'food': r'food', 'transport': r'transport', 'housing': r'housing|homeless',
    'diabetes': r'diabet|\bdm\b|e1[01]', 'depression': r'depress|phq|f3[23]', 'copd': r'copd|j44',
}
MONTHS = {m: i + 1 for i, m in enumerate('january february march april may june july august september october november december'.split())}
NEGATED = re.compile(r"\bnot\b|!=|<>|\bis null\b|\bexcept\b|=\s*0\b|\bexclu|\bwithout\b|\bno_|\bmissing|\bgap|\babsen|\bnever\b|"
                     r"\bnone\b|\bfalse\b|\black|\bunfilled|\bnot_|\bnon_|\bnegat|\bcancel|\bno.?show", re.I)
RATE_ASKED = re.compile(r'%|\b(?:percent(?:age)?|share|proportion|(?<!ejection )fraction|ratio|rate|per\s+1,?000|pmpm|per member)\b', re.I)
RATE_DONE = re.compile(r'/|\bratio\b|\brate\b|percent|\bshare\b|proportion|pmpm|per_1000|denominator|avg\(|\bmean\b|\bpct\b', re.I)
AVERAGE_ASKED = re.compile(r'\b(?:average|mean|median|typical)\b', re.I)
AVERAGE_DONE = re.compile(r'avg\(|\bmean\b|median|percentile|/|"statistic"\s*:\s*"(?:mean|median|avg)', re.I)
SUPERLATIVE = re.compile(r'\b(?:most|highest|lowest|least|top|largest|smallest|biggest|fewest|best|worst|leading)\b', re.I)
ORDERED = re.compile(r'order by|max\(|min\(|\blimit\b|\brank|"order_by"\s*:\s*\[\s*\{|\btop\b|"sort', re.I)
RELATIVE_TIME = re.compile(r'\b(?:last|past|previous|prior|since|within|recent(?:ly)?|this)\s+(?:\d+\s+|one\s+|two\s+|three\s+|six\s+|twelve\s+)?'
                           r'(?:day|week|month|quarter|year)s?\b', re.I)
DATED = re.compile(r"date\(|julianday|\d{4}-\d{2}-\d{2}|\bwindow\b|\bperiod\b|\bmonths?\b|\byears?\b|\bdays?\b|as_of|strftime", re.I)


def _strip_sql_comments(text):
    return re.sub(r'--[^\n]*', ' ', re.sub(r'/\*.*?\*/', ' ', text, flags=re.S))


QUESTION_KEYS = {'interpretation', 'purpose', 'quote', 'original_question', 'question', 'title', 'statement', 'unresolved',
                 'headline', 'grounding', 'normalized_question', 'reason', 'description', 'meaning'}


def executable_text(*parts):
    """Executable content of graphs, SQL params, typed requests or contracts, without any restated question text."""
    out = []

    def walk(value, key=None):
        if key in QUESTION_KEYS: return
        if isinstance(value, dict):
            for k, v in value.items(): walk(v, k)
        elif isinstance(value, (list, tuple)):
            for v in value: walk(v, key)
        elif isinstance(value, str):
            out.append(f'"{key}": "{_strip_sql_comments(value)}"' if key else _strip_sql_comments(value))
        elif value is not None:
            out.append(f'"{key}": {json.dumps(value)}' if key else json.dumps(value))
    for p in parts: walk(p)
    return '\n'.join(out)


def _numbers(text):
    found = set()
    for n in re.findall(r'(?<![a-z_])\d+(?:\.\d+)?', text.lower().replace(',', '')):
        v = float(n)
        found |= {_fmt(v), _fmt(v * 100), _fmt(v / 100)}
    return found


def _fmt(v):
    return str(round(v, 6)).rstrip('0').rstrip('.') if '.' in str(round(v, 6)) else str(round(v, 6))


def missing(question, evidence):
    """Literal elements of the question absent from the executable evidence text (a list of readable reasons)."""
    q, text = question, evidence.lower()
    gaps = []
    have = _numbers(text)
    absent = sorted(n for n in N.numbers(q) if _fmt(float(n)) not in have)
    if absent: gaps.append('number(s) ' + ', '.join(absent) + ' from the question are not in the plan')
    for name, number in MONTHS.items():
        if re.search(rf'\b{name}\b', q, re.I) and not re.search(rf'-{number:02d}-|\b{name}\b|"month"\s*:\s*{number}\b', text):
            gaps.append(f'month {name} is not in the plan')
    if N.NEGATION.search(q) and not NEGATED.search(text):
        gaps.append('the question negates or excludes something; the plan has no negation or exclusion')
    for table in (N.QUALIFIERS, N.CONCEPTS):
        for key, rx in table.items():
            if re.search(rx, q, re.I) and not (re.search(rx, text, re.I) or re.search(key.replace('_', '[ _]?'), text)
                                              or (key in ALIASES and re.search(ALIASES[key], text, re.I))):
                gaps.append(f'"{key.replace("_", " ")}" from the question is not in the plan')
    from .analysis_graph import requested_shape
    if requested_shape(q) == 'table' and not re.search(ALIASES['by_group'] + r'|"returns"\s*:\s*"table"', text, re.I):
        gaps.append('the question asks for a breakdown; the plan has no grouping')
    if RATE_ASKED.search(q) and not RATE_DONE.search(text):
        gaps.append('the question asks for a rate or share; the plan has no denominator')
    if AVERAGE_ASKED.search(q) and not AVERAGE_DONE.search(text):
        gaps.append('the question asks for an average; the plan computes none')
    if SUPERLATIVE.search(q) and not ORDERED.search(text):
        gaps.append('the question asks for a ranking; the plan has no ordering')
    if RELATIVE_TIME.search(q) and not DATED.search(text):
        gaps.append('the question names a time window; the plan has no date restriction')
    return gaps


SHAPES = {'count': {'count', 'value', 'patient_set'}, 'value': {'value', 'count'},
          'patient_set': {'patient_set', 'table'}, 'table': {'table'}}


def shape_misfit(question, answer):
    """None when the executed answer has the requested output shape (one-row tables count as scalars), else why not."""
    from .analysis_graph import requested_shape
    wanted = requested_shape(question)
    status = (answer or {}).get('status')
    if status in ('unsupported', 'not_available', 'clarify'): return None  # an honest decline is not a reshaped answer
    if status not in ('answered', 'partial'): return f'the plan did not produce an answer (status {status})'
    got = answer.get('answer_type')
    if got == 'composite': return None
    if wanted is None or got in SHAPES.get(wanted, {got}): return None
    rows = answer.get('table')
    if wanted in ('count', 'value') and isinstance(rows, list) and len(rows) == 1: return None
    return f'the question asks for a {wanted.replace("_", " ")}; the plan returned a {str(got).replace("_", " ")}'
