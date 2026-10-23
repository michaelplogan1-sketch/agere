"""Requirement contract: a ledger of every requirement the original question states, carried through planning
(GENERAL_QUESTION_ARCHITECTURE_PLAN 21-27,34; REVIEW_CORRECTIONS 2026-10-19 #30: no dropped constraints).

build(question) lists each requirement with its span in the question:
  literal       numbers, months, qualifiers, concepts, negation, grouping, rate, average, ranking, time window
                (the literal-question check, literal_check.missing, decides these)
  comparator    a threshold's direction: '3 or more' / 'at least 3' / '65 and over' (>=), 'over 65' / 'more than' (>),
                'under 18' / 'fewer than' (<), 'at most' / '2 or fewer' (<=). A plan that writes the number only with an
                operator of another direction ('> 3' for '3 or more', '< 65' for 'over 65') drops it.
  boolean       the AND/OR tree over named conditions: 'diabetes or CKD' needs a disjunction in the plan (OR / IN / UNION);
                'both diabetes and CKD' needs a conjunction and must not be answered by a disjunction alone.
check(question, text) -> the ledger with each requirement's status (covered / dropped) over a proposal's EXECUTABLE text
(literal_check.executable_text: SQL without comments, engine parameters, typed predicates; never a restatement).
gaps(question, text) -> readable reasons for every dropped requirement (literal gaps first); the checked-proposer cascade
rejects a proposal with any (proposers.Cascade.gaps), and the accepted interpretation carries the ledger
(interpretation['requirement_ledger']) so every answer names how each requirement was kept. Necessary, not sufficient:
a plan can keep every requirement and still use a different definition.
"""
import re

from . import local_normalizer as N
from .literal_check import missing

VERSION = 'requirement-ledger/1'

_NUM = r'(\d+(?:\.\d+)?)'
# 'over 12 months' / 'under 30 days' is a time span, not a threshold ('over 65 years old' is)
_NOT_SPAN = r'(?!\s*(?:days?|weeks?|months?|quarters?)\b)(?!\s*years?\b(?!\s*(?:old|of age)))'
# (pattern with the number as group 1, required direction)
COMPARATORS = (
    (rf'\b{_NUM}\s*(?:or|and)\s*(?:more|over|older|above|greater|higher|up)\b', '>='),
    (rf'\b{_NUM}\s*\+', '>='),
    (rf'\b(?:at\s+least|no\s+(?:fewer|less)\s+than|minimum\s+of)\s+{_NUM}\b', '>='),
    (rf'\b{_NUM}\s*(?:or|and)\s*(?:fewer|less|under|younger|below|lower)\b', '<='),
    (rf'\b(?:at\s+most|no\s+more\s+than|maximum\s+of|up\s+to)\s+{_NUM}\b', '<='),
    (rf'\b(?:more\s+than|over|above|greater\s+than|older\s+than|exceed(?:s|ing)?|higher\s+than)\s+{_NUM}\b{_NOT_SPAN}', '>'),
    (rf'\b(?:fewer\s+than|less\s+than|under|below|younger\s+than|lower\s+than)\s+{_NUM}\b{_NOT_SPAN}', '<'),
)
_DIRECTION = {'>=': '>=', '>': '>', '<=': '<=', '<': '<'}
# operator written in the plan -> direction class; '>= N' satisfies '>= N'; '> N-1' also satisfies '>= N' (integers)
_OPS = re.compile(r"(>=|<=|<>|!=|>|<|=)\s*'?(-?\d+(?:\.\d+)?)'?")
_OPS_REV = re.compile(r"(-?\d+(?:\.\d+)?)'?\s*(>=|<=|>|<)")  # '3 <= count'
_JSON_OP = re.compile(r'"(?:op|operator|comparator|comparison)"\s*:\s*"(>=|<=|>|<|gte|lte|gt|lt|at_least|at_most|more_than|less_than)"', re.I)
_JSON_NAMES = {'gte': '>=', 'at_least': '>=', 'lte': '<=', 'at_most': '<=', 'gt': '>', 'more_than': '>', 'lt': '<', 'less_than': '<'}
_BETWEEN = re.compile(r'\bbetween\s+(-?\d+(?:\.\d+)?)\s+and\s+(-?\d+(?:\.\d+)?)', re.I)
_FLIP = {'>=': '<=', '<=': '>=', '>': '<', '<': '>'}


def _fmt(v):
    v = float(v)
    return str(int(v)) if v.is_integer() else str(v)


def _satisfies(op, value, want, n):
    """Does `<expr> op value` express `<expr> want n`?"""
    v, n = float(value), float(n)
    if want == '>=': return (op == '>=' and v == n) or (op == '>' and v == n - 1 and n.is_integer())
    if want == '>': return (op == '>' and v == n) or (op == '>=' and v == n + 1 and n.is_integer())
    if want == '<=': return (op == '<=' and v == n) or (op == '<' and v == n + 1 and n.is_integer())
    if want == '<': return (op == '<' and v == n) or (op == '<=' and v == n - 1 and n.is_integer())
    return False


def _operators(text):
    """[(op, value)] the executable text applies (both operand orders, BETWEEN bounds, JSON op fields next to a value)."""
    out = [(op, v) for op, v in _OPS.findall(text) if op in _FLIP]
    out += [(_FLIP[op], v) for v, op in _OPS_REV.findall(text)]
    for lo, hi in _BETWEEN.findall(text): out += [('>=', lo), ('<=', hi)]
    for m in _JSON_OP.finditer(text):
        op = _JSON_NAMES.get(m.group(1).lower(), m.group(1))
        v = re.search(r':\s*(-?\d+(?:\.\d+)?)\b', text[m.end():m.end() + 40])  # the value field right after the operator
        if v: out.append((op, v.group(1)))
    return out


def _concept_spans(question):
    spans = []
    for key, rx in N.CONCEPTS.items():
        for m in re.finditer(rx, question, re.I): spans.append((m.start(), m.end(), key))
    return sorted(spans)


def build(question):
    """-> [{'id', 'kind', 'text', 'span', ...}] every requirement the question states."""
    q, reqs = question or '', []
    for i, (rx, want) in enumerate(COMPARATORS):
        for m in re.finditer(rx, q, re.I):
            if any(r['kind'] == 'comparator' and r['span'][0] <= m.start(1) < r['span'][1] for r in reqs): continue
            reqs.append({'id': f'cmp:{m.group(1)}:{want}', 'kind': 'comparator', 'text': m.group(0), 'span': [m.start(), m.end()],
                         'number': _fmt(m.group(1)), 'direction': want})
    concepts = _concept_spans(q)
    for (s1, e1, k1), (s2, e2, k2) in zip(concepts, concepts[1:]):
        between = q[e1:s2]
        if k1 == k2 or len(between) > 24: continue
        if re.fullmatch(r'\s*(?:,\s*)?or\s+(?:with\s+)?', between, re.I):
            reqs.append({'id': f'bool:or:{k1}|{k2}', 'kind': 'boolean', 'operator': 'or', 'operands': [k1, k2],
                         'text': q[s1:e2], 'span': [s1, e2]})
        elif re.fullmatch(r'\s*(?:,\s*)?and\s+(?:also\s+)?', between, re.I) and re.search(r'\bboth\b|\bas well as\b|\bplus\b|\band also\b',
                                                                                           q[max(0, s1 - 12):e2 + 12], re.I):
            reqs.append({'id': f'bool:and:{k1}|{k2}', 'kind': 'boolean', 'operator': 'and', 'operands': [k1, k2],
                         'text': q[s1:e2], 'span': [s1, e2]})
    for n in sorted(N.numbers(q), key=lambda x: float(x)):
        reqs.append({'id': f'lit:number:{n}', 'kind': 'literal', 'text': n, 'span': _span(q, n), 'literal': 'number'})
    for table, what in ((N.QUALIFIERS, 'qualifier'), (N.CONCEPTS, 'concept')):
        for key, rx in table.items():
            m = re.search(rx, q, re.I)
            if m: reqs.append({'id': f'lit:{what}:{key}', 'kind': 'literal', 'text': m.group(0), 'span': [m.start(), m.end()],
                               'literal': what, 'key': key})
    m = N.NEGATION.search(q)
    if m: reqs.append({'id': 'lit:negation', 'kind': 'literal', 'text': m.group(0), 'span': [m.start(), m.end()], 'literal': 'negation'})
    return reqs


def _span(q, token):
    m = re.search(r'(?<![\d.])' + re.escape(str(token)) + r'(?![\d])', q.replace(',', ''))
    return [m.start(), m.end()] if m else None


def _boolean_kept(req, text):
    low = text.lower()
    if req['operator'] == 'or':
        return bool(re.search(r"\bor\b|\bin\s*\(|\bunion\b|\"any\"|\"any_of\"|\"or\"|\[\s*\"[^\"]+\"\s*,\s*\"", low))
    # 'both X and Y': a plan whose only combination is OR answers a wider question
    has_or = bool(re.search(r'\bor\b|\bunion\b|"any_of"|"or"', low))
    has_and = bool(re.search(r'\band\b|\bintersect\b|"all_of"|"and"|having\s+count\s*\(\s*distinct', low))
    return has_and or not has_or


def check(question, text):
    """The ledger over one proposal's executable text: requirements with status covered/dropped and a reason when dropped."""
    text = text or ''
    reqs = build(question)
    gaps = missing(question, text)
    ops = _operators(text)
    for r in reqs:
        if r['kind'] == 'comparator':
            same = [(op, v) for op, v in ops if float(v) in (float(r['number']), float(r['number']) - 1, float(r['number']) + 1)]
            ok = any(_satisfies(op, v, r['direction'], r['number']) for op, v in same)
            wrong = [f'{op} {_fmt(v)}' for op, v in same if not _satisfies(op, v, r['direction'], r['number']) and _fmt(v) == r['number']]
            if ok or not wrong:
                r['status'] = 'covered' if ok else 'unverified'  # number without an explicit operator: the literal check decides
            else:
                r['status'] = 'dropped'
                r['reason'] = (f"'{r['text']}' asks for {r['direction']} {r['number']}; the plan applies {', '.join(dict.fromkeys(wrong))}")
        elif r['kind'] == 'boolean':
            ok = _boolean_kept(r, text)
            r['status'] = 'covered' if ok else 'dropped'
            if not ok:
                r['reason'] = (f"'{r['text']}' is a disjunction (either condition); the plan has no OR / IN / UNION" if r['operator'] == 'or'
                               else f"'{r['text']}' requires both conditions; the plan only combines them with OR")
        else:
            hit = next((g for g in gaps if _names(g, r)), None)
            r['status'] = 'dropped' if hit else 'covered'
            if hit: r['reason'] = hit
    unplaced = [g for g in gaps if not any(r.get('reason') == g for r in reqs)]  # grouping, rate, average, ranking, window, months
    return {'version': VERSION, 'requirements': reqs, 'other_gaps': unplaced,
            'complete': not unplaced and all(r['status'] != 'dropped' for r in reqs)}


def _names(gap, r):
    if r['literal'] == 'number': return gap.startswith('number(s)') and re.search(r'(?<![\d.])' + re.escape(r['text']) + r'(?![\d])', gap)
    if r['literal'] == 'negation': return 'negates' in gap
    return f'"{r["key"].replace("_", " ")}"' in gap


# A warehouse patient id is an entity reference, not numbers: when the plan carries the same id, the id is covered as a whole
# (its digits are not thresholds the plan must contain).
PATIENT_ID = re.compile(r'\b(?:SYN-[0-9A-F]{8}-[0-9A-F]{3}|WHP-[0-9A-F]{12}|P-[A-Z0-9]{2,8})\b', re.I)


def _covered_ids(question, text):
    low = (text or '').lower()
    return PATIENT_ID.sub(lambda m: 'the patient' if m.group().lower() in low else m.group(), question)


def gaps(question, text):
    """Readable reasons for every dropped requirement (literal-check gaps first, then comparators and Boolean structure)."""
    question = _covered_ids(question, text)
    ledger = check(question, text)
    literal = missing(question, text or '')
    extra = [r['reason'] for r in ledger['requirements'] if r['status'] == 'dropped' and r['kind'] != 'literal']
    return list(dict.fromkeys(literal + extra))


def summary(ledger):
    """Compact form for receipts: counts and the dropped/unverified requirement ids."""
    reqs = ledger.get('requirements') or []
    return {'version': VERSION, 'n': len(reqs), 'covered': sum(r['status'] == 'covered' for r in reqs),
            'dropped': [r['id'] for r in reqs if r['status'] == 'dropped'] + ledger.get('other_gaps', []),
            'unverified': [r['id'] for r in reqs if r['status'] == 'unverified'],
            'requirements': [{k: r.get(k) for k in ('id', 'kind', 'text', 'span', 'status')} for r in reqs]}
