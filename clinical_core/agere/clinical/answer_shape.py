"""Requested answer shape and the conformance / engine-fit step at the end of the answer path.

Blind runs (heldout10) showed routes computing the right number in the wrong form: a one-row table or a per-source table
when one number was asked, 120.76 when "round to one decimal" was asked, a fraction for "what percentage", a rate per 1,000
for "how many", a count for "one row per condition", a table for "which patients". They also showed canned engines
answering questions they do not fit: a lines-of-therapy table for a pharmacogenomics count, a monitor table for "longest gap
between deliveries", a measure-rate table for a PDC count.

`requested(question)` reads the shape from the wording:
  how many / number of                      -> count          ("how many days on average" -> value)
  what percentage / average / total / rate  -> value          (stated rounding and percent scale kept)
  which patients / list the members         -> patient_set
  for each / one row per / X by Y / which <things> / top N -> table

`conform(question, answer, operation=...)` is pure. It returns (answer, info) with info['status']:
  'fits'      the answer already has the requested shape (rounding applied when the question states it)
  'reshaped'  reshaped without new computation and without a choice: one-row table -> its one matching column,
              the any_source / total row of a per-source table, patient_set -> its count, one-row-per-patient -> patient_set
  'misfit'    the shape cannot be reached without guessing, or an engine's output does not hold the quantity asked. Engine
              fit reads capability_registry.answers (the shapes each engine natively answers): a count asked of an engine
              without a native count, a literal definition ("use data received as of", "round to", "i.e.") answered by an
              analysis table, a count whose noun no integer column names, or an average / median / longest / total the
              table has no column for.
  'skipped'   nothing to check (abstention, no shape stated, AGERE_ANSWER_FIT=off)
Nothing is fabricated: a value is never cast to a count, and a column is picked only when exactly one column matches.

`review(...)` runs after execution in stack_runtime.compose: a misfit declines the rung that produced it and re-plans once
through the verified hosted SQL ladder with the requested shape stated; if that also misfits (or hosted answering is off),
the answer is withheld as non-final (AGERE_GUARD_POLICY=low keeps it at low confidence instead).
"""
import os
import re

VERSION = 'answer-shape/1'

_WORD_NUM = {'zero': 0, 'no': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4}
_STOP = {'how', 'many', 'much', 'what', 'which', 'who', 'the', 'and', 'for', 'with', 'did', 'does', 'was', 'were', 'are', 'had',
         'has', 'have', 'from', 'that', 'this', 'there', 'their', 'our', 'any', 'all', 'per', 'each', 'into', 'over', 'between',
         'they', 'them', 'been', 'its', 'only', 'also', 'give', 'show', 'use', 'using', 'during', 'since', 'than', 'more', 'less',
         'least', 'most', 'when', 'where', 'whose', 'data', 'received', 'calendar', 'year', 'round', 'decimal', 'decimals', 'two',
         'one', 'get', 'you', 'not', 'number', 'count', 'total', 'value', 'what', 'was', 'were', 'is', 'in', 'of', 'to', 'a',
         'an', 'on', 'at', 'by', 'or', 'as', 'be', 'it', 'we', 'us', 'me', 'do', 'if', 'so', 'same', 'date', 'dates', 'day'}

# ---------------------------------------------------------------------------------------------------- requested shape
_PATIENT_NOUN = r'(?:patients?|members?|people|persons?|individuals?|beneficiar(?:y|ies)|enrollees?)'
_PATIENT_LIST = re.compile(r'\b(?:which|list(?: all)?|who are|identify|name|pull(?: me)?|give me|send me|i need)\b'
                           r'(?:\s+(?:the|our|all|of|of the|of our|a list of|the list of|list of))*(?:\s+[\w+-]+){0,5}?\s+'
                           + _PATIENT_NOUN + r'\b|^\s*who\b|(?:^|[:.;]\s*)who (?:has|have|had|is|are|was|were)\b|'
                           r'\b(?:pull|send|give)(?: me)? (?:the |a )?(?:[\w-]+ )?list\b|\bneed a list\b|\bwants a list\b', re.I)
_FOR_EACH = re.compile(r'\bfor (?:each|every)\b|\bone row (?:per|for each)\b|\b(?:break|broken|split|grouped)(?:s|ing)? (?:it |this |them )?'
                       r'(?:down |out )?by\b|\bin which\b|\bthrough which\b|\bvia which\b|\btop (?:\d+|three|five|ten)\b|'
                       r'\b(?:three|five|ten|\d+) most (?:common(?:ly)?|frequent(?:ly)?)\b|\beach month\b|\bmonth by month\b|'
                       r'\b(?:monthly|quarterly|weekly) (?:trend|counts?|totals?|rates?|breakdown|[\w-]+ (?:volume|counts?|visits?))\b|'
                       r'\b(?:return|as|give me|in) (?:a |the )?table\b|\btable with\b|\b(?:went to|on|in|at) each (?!patient|member|person)|'
                       r'\b(?:did|has|have) each\b|(?<!member )(?<!patient )\bper (?:year|month|quarter|week|payer|practice|plan|clinic|'
                       r'provider|pcp|facility|site|specialty|channel|visit type|service type|drug class|condition|stream|feed)\b|'
                       r'\b(?:vs\.?|versus)\b[^?]*\b(?:vs\.?|versus)\b', re.I)
_WHICH_THING = re.compile(r'\bwhich (?!' + _PATIENT_NOUN + r'\b|of (?:the |our |these )?' + _PATIENT_NOUN + r'\b)([a-z]+)', re.I)
# "X by Y" is a breakdown unless "by" belongs to a passive verb ("replaced by", "billed by"), states an amount ("by at
# least 1.0", "by 10%") or a deadline ("by March 2030").
_BY = re.compile(r'(?:\b([\w]+),?\s+|^|[:;,.]\s*)by (?:each |the |their |our )?([\w]+)', re.I)
_BY_METHOD = {'using', 'matching', 'counting', 'comparing', 'taking', 'looking', 'checking', 'joining', 'linking', 'adding',
              'subtracting', 'dividing', 'excluding', 'including', 'treating', 'combining', 'reading', 'applying', 'measuring'}
_PASSIVE = {'sent', 'paid', 'made', 'seen', 'done', 'written', 'given', 'taken', 'held', 'run', 'set', 'read', 'owned', 'caused',
            'known', 'shown', 'drawn', 'kept', 'met', 'led', 'driven', 'chosen', 'brought', 'bought', 'sold', 'told', 'found', 'won',
            'filled', 'billed', 'ordered', 'improve', 'increase', 'decrease', 'drop', 'rise', 'fall', 'change', 'grow', 'reduce',
            'go', 'went', 'rose', 'fell', 'grew', 'up', 'down', 'down', 'out', 'step', 'side', 'one', 'day', 'case'}
_BY_NOT = {'at', 'a', 'an', 'more', 'less', 'over', 'under', 'about', 'around', 'up', 'then', 'now', 'end', 'same', 'name',
           'mistake', 'default', 'law', 'hand', 'itself', 'themselves', 'far', 'much', 'how', 'definition', 'design',
           'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december',
           'number', 'which'}

_PCT = r'percent(?:age)?s?|%'
_VALUE_WORDS = (_PCT + r'|share|proportion|fraction|rates?|averages?|mean|median|total|sum|ratio|pmpm|amount|longest|shortest|'
                r'maximum|minimum|dollars?|cost|spend(?:ing)?|length of stay|per member per month|per 1,?000')
_VALUE_INTERROGATIVE = re.compile(r'\bhow much\b|\bhow often\b|\bwhat(?:\'s|\s+is|\s+was|\s+are|\s+were|\s+share|\s+percentage|\s+percent|'
                                  r'\s+proportion|\s+fraction)?\s*(?:the\s+|our\s+|their\s+)?(?:[\w-]+\s+){0,5}?(?:(?:' + _VALUE_WORDS + r')\b|%)', re.I)
_COUNT_INTERROGATIVE = re.compile(r'\bhow many\b|\bnumber of\b|\b(?:what (?:is|was|are|were) )?the (?:total )?number of\b|\btotal number of\b|'
                                  r'\bcount (?:of|how many|the number)\b|^\s*count\b|\bcount please\b|\bheadcount\b|\bgive me the number\b|'
                                  r'\bregistry count\b|\bsize of\b', re.I)
_COUNT_IS_VALUE = re.compile(r'\bon average\b[^?.]*\bhow many\b|\bhow many\b[^?.]*\b(?:on average|each (?:one )?averages?|does each \w+ average)\b|\bhow many\s+(?:[\w-]+\s+){0,3}?(?:on average|per (?:patient|member|month|day|visit|admission|claim)|'
                             r'per 1,?000|in an average)\b|\bhow many (?:days|hours|minutes|weeks|months|years)\b(?=[^?.]*\b(?:average|mean|median)\b)', re.I)
_VALUE_ANYWHERE = re.compile(r'\b(?:percent(?:age)?|average|mean|median|pmpm|how often|in dollars|how long|rate|per 1,?000|total|dollars)\b|%', re.I)
_MULTI = re.compile(r'\bhow many\b[^?]*\band (?:how many|how much|what|the average|average)\b|\bhow (?:many|much)\b[^?]*,\s*and why\b', re.I)

_DECIMALS = re.compile(r'\b(?:round(?:ed)?|to|with|at)\s+(?:the\s+nearest\s+)?(\d+|zero|one|two|three|four)\s+decimal(?:s| places?| points?)?\b', re.I)
_CENTS = re.compile(r'\b(?:round(?:ed)? to (?:the nearest )?cents?|to the cent|nearest cent)\b', re.I)
_WHOLE = re.compile(r'\bnearest (?:whole (?:number|percent(?:age point)?)|integer)\b|\bround(?:ed)? to (?:a )?whole\b', re.I)
_PERCENT_ASKED = re.compile(r'\bwhat (?:is |was |are |were )?(?:the )?(?:[\w-]+\s+){0,3}?percent(?:age)?\b|\b(?:give|express|report|state|return)'
                            r' (?:it |the answer |the rate |the share |this )?(?:as )?(?:a |the )?percent(?:age)?\b|\bas a percent(?:age)?\b|'
                            r'\bin percent\b|\bpercent(?:age)? (?:of|had|were|was|is|are)\b', re.I)


_OPEN = re.compile(r'^\s*(?:[\w\s-]{0,40}:\s*)?(?:is|are|was|were|did|does|do|has|have|had|should|shall|can|could|will|would|may|might|'
                   r'compare|projected|forecast|effect of|what happens|what terms|what should|what if|if we)\b|'
                   r'\bnext (?:quarter|month|year|week)\b|\b(?:expected|predicted|projected) (?:number|count|cost|volume)\b|'
                   r'\bhow (?:many|much)\b[^?]*\b(?:will|would|should|could|can)\b|\bhow (?:many|much)\b[^?]*\bdo we need\b', re.I)


def _first(rx, text):
    m = rx.search(text)
    return m.start() if m else None


def _breakdown_by(q):
    for m in _BY.finditer(q):
        before, after = (m.group(1) or '').lower(), m.group(2).lower()
        if before in _PASSIVE or before.endswith('ed') or after in _BY_NOT or after.isdigit() or re.match(r'\d', after):
            continue
        # "by" introducing the method ("by matching", "by using") is not a grouping
        if after in _BY_METHOD: continue
        return True
    return False


def requested(question):
    """-> {'shape': 'count'|'value'|'patient_set'|'table'|None, 'percent': bool, 'decimals': int|None, 'cue': str|None,
    'terms': [measure words]}."""
    q = ' '.join(str(question or '').split())
    full = q.lower()
    out = {'shape': None, 'percent': False, 'decimals': None, 'cue': None, 'terms': []}
    d = _DECIMALS.search(full)
    if d: out['decimals'] = int(d.group(1)) if d.group(1).isdigit() else _WORD_NUM[d.group(1)]
    elif _CENTS.search(full): out['decimals'] = 2
    elif _WHOLE.search(full): out['decimals'] = 0
    low = re.sub(r'\s+', ' ', re.sub(r'\([^)]*\)', ' ', full))  # parenthetical definitions and value lists state no shape
    if _OPEN.search(re.sub(r'(?:,\s*|\s+)(?:so (?:that )?|in order to |so as to ).*$', '', low)):
        pass  # a yes/no, forecast, recommendation or comparison question states no answer shape
    elif _PATIENT_LIST.search(low) and not _COUNT_INTERROGATIVE.search(low) and not _VALUE_INTERROGATIVE.search(low):
        out['shape'], out['cue'] = 'patient_set', 'which patients'
    elif _FOR_EACH.search(low) or _MULTI.search(low) or re.match(r'\s*which\b', low) or (_WHICH_THING.search(low) and not _COUNT_INTERROGATIVE.search(low)
                                   and not _VALUE_INTERROGATIVE.search(low)) or _breakdown_by(low):
        out['shape'], out['cue'] = 'table', 'breakdown'
    else:
        c, v = _first(_COUNT_INTERROGATIVE, low), _first(_VALUE_INTERROGATIVE, low)
        if c is not None and _COUNT_IS_VALUE.search(low) and (v is None or _COUNT_IS_VALUE.search(low).start() <= v):
            v, c = _COUNT_IS_VALUE.search(low).start(), None
        if c is not None and (v is None or c <= v):
            out['shape'], out['cue'] = 'count', 'how many'
        elif v is not None:
            out['shape'] = 'value'
        elif _VALUE_ANYWHERE.search(low):
            out['shape'] = 'value'
    if out['shape'] == 'value':
        m = _VALUE_INTERROGATIVE.search(low) or _VALUE_ANYWHERE.search(low) or _COUNT_IS_VALUE.search(low)
        word = m.group(0) if m else ''
        out['cue'] = _family(word)
        out['percent'] = bool(re.search(_PCT, word) or _PERCENT_ASKED.search(low))
    if not out['percent'] and out['shape'] == 'value': out['percent'] = bool(_PERCENT_ASKED.search(full))
    out['terms'] = measure_terms(low)
    return out


def _family(word):
    w = word.lower()
    for fam, rx in (('percentage', _PCT), ('per_1000', r'per 1,?000'), ('pmpm', r'pmpm|per member per month'),
                    ('average', r'average|mean|on average'), ('median', r'median'), ('rate', r'\brates?\b'),
                    ('share', r'share|proportion|fraction'), ('ratio', r'ratio'), ('longest', r'longest|maximum'),
                    ('shortest', r'shortest|minimum'), ('total', r'total|sum|how much|amount|dollars?|cost|spend')):
        if re.search(rx, w): return fam
    return 'value'


def _stem(t):
    t = t.lower()
    if len(t) > 4 and t.endswith('ies'): return t[:-3] + 'y'
    if len(t) > 4 and t.endswith('es') and t[-3] in 'sxz': return t[:-2]
    if len(t) > 3 and t.endswith('s') and not t.endswith('ss'): return t[:-1]
    return t


def measure_terms(low):
    """Content words of the clause that states what is measured (the interrogative sentence)."""
    sentences = re.split(r'(?<=[?.])\s+', low)
    clause = next((s for s in sentences if re.search(r'\b(?:how|what|which|who|list|show|give)\b', s)), low)
    clause = re.split(r'\buse data received\b', clause)[0]
    return sorted({_stem(t) for t in re.findall(r'[a-z][a-z0-9-]+', clause) if len(t) >= 3 and t not in _STOP})


_SYNONYMS = {'avg': 'average', 'mean': 'average', 'pct': 'percent', 'percentage': 'percent', 'ed': 'emergency', 'er': 'emergency',
             'n': 'number', 'cnt': 'count', 'num': 'number', 'los': 'length', 'max': 'longest', 'min': 'shortest',
             'usd': 'dollar', 'amt': 'amount', 'qty': 'quantity', 'dx': 'diagnosis', 'rx': 'prescription', 'pt': 'patient',
             'pts': 'patient', 'member': 'patient', 'adm': 'admission', 'admit': 'admission'}


def _column_terms(name):
    parts = [p for p in re.split(r'[^a-z0-9]+', str(name).lower()) if p]
    out = set()
    for p in parts:
        out.add(_stem(p)); out.add(_SYNONYMS.get(p, _stem(p)))
    return out


# ---------------------------------------------------------------------------------------------------- conformance
_FAMILY_COLUMN = {'percentage': r'pct|percent|rate|share|proportion|fraction', 'share': r'pct|percent|share|proportion|fraction|rate',
                  'rate': r'rate|pct|percent|per_?1000|share', 'per_1000': r'per_?1000|per_?k\b|rate',
                  'average': r'avg|average|mean', 'median': r'median|p50', 'ratio': r'ratio', 'pmpm': r'pmpm',
                  'longest': r'max|longest|largest', 'shortest': r'min|shortest|smallest',
                  'total': r'total|sum|amount|paid|dollars?|usd|cost|spend|collections?|net'}
_FRACTION_COLUMN = re.compile(r'rate|share|proportion|fraction|persist|adheren|_day_\d+$', re.I)
_PERCENT_COLUMN = re.compile(r'pct|percent', re.I)
_ALL_ROW = {'any_source', 'any', 'all', 'all_sources', 'total', 'overall', 'combined', 'any source', 'all sources', 'grand_total',
            'grand total', 'union', 'deduplicated', 'unique'}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float('inf'), float('-inf'))


def _integral(v):
    return isinstance(v, int) and not isinstance(v, bool) or (isinstance(v, float) and v.is_integer())


def _single_row(table):
    """The one row a table answer reduces to: a one-row table, or the single any_source / total row of a per-source table."""
    rows = [r for r in table if isinstance(r, dict)]
    if len(rows) != len(table) or not rows: return None, None
    if len(rows) == 1: return rows[0], None
    marked = [r for r in rows if any(isinstance(v, str) and v.strip().lower() in _ALL_ROW for v in r.values())]
    if len(marked) == 1:
        label = next(v for v in marked[0].values() if isinstance(v, str) and v.strip().lower() in _ALL_ROW)
        return marked[0], label
    return None, None


def _pick(row, want, cue, terms, counted):
    """The single column of `row` that answers the question, or (None, reason)."""
    numeric = {k: v for k, v in row.items() if _num(v)}
    if want == 'count': numeric = {k: v for k, v in numeric.items() if _integral(v)}
    if not numeric: return None, 'no numeric column of the requested kind'
    family = _FAMILY_COLUMN.get(cue or '')
    scored = {}
    for k in numeric:
        cols = _column_terms(k)
        s = 0
        if want == 'value' and family and re.search(family, k, re.I): s += 4
        if want == 'value' and family and not re.search(family, k, re.I) and len(numeric) > 1: s -= 1
        if want == 'count' and counted & cols: s += 3
        if want == 'count' and re.search(r'^(?:n|count|total|patients?|members?)$', k, re.I): s += 1
        s += len(cols & set(terms))
        scored[k] = s
    best = max(scored.values())
    top = [k for k, s in scored.items() if s == best]
    if len(numeric) == 1: return next(iter(numeric)), None
    if best <= 0: return None, f'no column of {sorted(numeric)} matches what the question asks'
    if len(top) > 1: return None, f'columns {sorted(top)} match equally; Agere will not choose'
    return top[0], None


def _counted_noun(question):
    m = re.search(r'\bhow many\s+((?:[\w-]+\s+){0,3}?[\w-]+?)(?:\s+(?:had|have|has|were|was|are|is|did|do|does|with|in|from|that|who|which|'
                  r'received|got|began|started|happened|moved|picked|occurred)\b|[?,.])', question.lower())
    if not m:
        m = re.search(r'\bnumber of\s+((?:[\w-]+\s+){0,2}[\w-]+)', question.lower())
    return {_SYNONYMS.get(_stem(t), _stem(t)) for t in re.findall(r'[a-z]+', m.group(1)) if t not in _STOP} if m else set()


def _round(answer, req):
    if answer.get('answer_type') == 'value' and _num(answer.get('value')) and req.get('decimals') is not None:
        answer['value'] = round(float(answer['value']), req['decimals'])
        if req['decimals'] == 0: answer['value'] = float(int(answer['value']))
    return answer


def enabled():
    return os.environ.get('AGERE_ANSWER_FIT', 'on') != 'off'


def asserted(answer):
    return isinstance(answer, dict) and answer.get('answer_type') not in (None, 'unsupported') and \
        answer.get('status') in ('answered', 'partial')


# A stated definition marks a literal lookup ("Use data received as of", "Round to", "i.e.", "count only ...", "meaning ..."):
# an engine's analytic view is not the defined quantity even when the engine natively reports something similar.
_LITERAL = re.compile(r'\buse data (?:received )?as of\b|\bround(?:ed)? to\b|\bto (?:one|two|three|\d) decimals?\b|\bi\.e\.|'
                      r'\bmeaning\b|\bdefined as\b|\bdefine [\w ]+ as\b|\bcount (?:only|each|a |one )|\bcounting (?:one|only|each)\b', re.I)
# Concrete aggregations whose result must appear as a column of an engine table that claims to answer the question.
_CONCRETE = (('average', r'\b(?:average|mean|on average)\b'), ('median', r'\bmedian\b'), ('longest', r'\b(?:longest|maximum)\b'),
             ('shortest', r'\b(?:shortest|minimum)\b'), ('total', r'\b(?:in total|total (?:amount|dollars|paid|cost|spend))\b'))


def _engine_misfit(question, req, want, answer, operation, served):
    """Why an engine's answer does not hold the quantity asked, in the requested shape (None when it fits)."""
    q = str(question or '')
    if want not in served:
        return f"the {operation} engine answers {' / '.join(served)}, not the {want} this question asks for"
    rows = [r for r in (answer.get('table') or []) if isinstance(r, dict)] if isinstance(answer.get('table'), list) else []
    cols = [k for r in rows[:5] for k in r]
    if want in ('count', 'value') and answer.get('answer_type') == 'table':
        if _LITERAL.search(q):
            return f'the question states a literal definition; the {operation} engine table is an analysis, not that quantity'
        if want == 'count':
            noun = _counted_noun(q)
            ints = {k for r in rows[:5] for k, v in r.items() if _num(v) and _integral(v)}
            if noun and rows and not any(noun & _column_terms(k) for k in ints):
                return f"the {operation} table has no count of {' '.join(sorted(noun))}"
    for fam, rx in _CONCRETE:
        if re.search(rx, q, re.I) and rows and not any(re.search(_FAMILY_COLUMN[fam], k, re.I) for k in cols):
            return f'the question asks for the {fam}; the {operation} table has no {fam} column'
    return None


def conform(question, answer, *, operation=None):
    """-> (answer, info). Pure; the input answer is not mutated."""
    req = requested(question)
    info = {'version': VERSION, 'requested': req['shape'], 'status': 'skipped'}
    if req['percent']: info['percent'] = True
    if req['decimals'] is not None: info['decimals'] = req['decimals']
    if not enabled() or not asserted(answer) or req['shape'] is None:
        return answer, info
    want, have = req['shape'], answer.get('answer_type')
    out = dict(answer)
    engine = None
    try:
        from .capability_registry import answers as engine_answers
        engine = engine_answers(operation) if operation else None
    except ImportError:
        engine = None
    info['answered'] = have
    if operation: info['operation'] = operation

    def misfit(reason):
        info.update(status='misfit', reason=reason)
        return answer, info

    if engine is not None:
        reason = _engine_misfit(question, req, want, answer, operation, engine)
        if reason: return misfit(reason)
        if want != have and answer.get('answer_type') == 'table' and want in ('count', 'value'):
            # A native engine quantity: the engine's analysis is the answer and its table the evidence (no scalar is read out).
            info.update(status='fits', how=f'{operation} engine view')
            return out, info
    if want == have:
        info['status'] = 'fits'
        if want == 'value':
            before = out.get('value'); _round(out, req)
            if out.get('value') != before: info['rounded'] = req['decimals']
        return out, info
    table = answer.get('table') if isinstance(answer.get('table'), list) else None
    truncated = any('truncated' in str(m).lower() for m in answer.get('missing') or [])
    if want == 'count' and have == 'patient_set' and isinstance(answer.get('patient_ids'), list):
        out.update(answer_type='count', count=len(answer['patient_ids']))
        info.update(status='reshaped', how='patient_set -> its count')
        return out, info
    if want == 'value' and have == 'count' and req['cue'] == 'total' and _num(answer.get('count')) and \
            not re.search(r'\bdollars?\b|\$|\bpaid\b|\bcost\b|\bspend', str(question), re.I):
        out.update(answer_type='value', value=answer['count'])
        info.update(status='reshaped', how='a total stated as its count')
        return _round(out, req), info
    if want == 'patient_set' and have == 'table' and table and not truncated:
        rows = [r for r in table if isinstance(r, dict)]
        ids = [r.get('patient_id') for r in rows]
        if len(rows) == len(table) and all(isinstance(i, str) and i for i in ids) and len(set(ids)) == len(ids):
            out.update(answer_type='patient_set', patient_ids=sorted(ids), count=len(ids))
            info.update(status='reshaped', how='one row per patient -> patient set')
            return out, info
        return misfit('the table is not one row per patient')
    if want in ('count', 'value') and have == 'table' and table:
        if truncated: return misfit('the table is truncated')
        row, label = _single_row(table)
        if row is None: return misfit(f'a {len(table)}-row table, and no single total row answers the one {want} asked')
        col, why = _pick(row, want, req['cue'], req['terms'], _counted_noun(str(question)) if want == 'count' else set())
        if col is None: return misfit(why)
        v = row[col]
        if want == 'count':
            out.update(answer_type='count', count=int(v))
        else:
            if req['percent'] and not _PERCENT_COLUMN.search(col) and _FRACTION_COLUMN.search(col) and 0 <= v <= 1:
                v = v * 100; info['scaled'] = 'fraction -> percent'
            out.update(answer_type='value', value=float(v) if isinstance(v, float) else v)
            _round(out, req)
        info.update(status='reshaped', how=f"column {col}" + (f" of the '{label}' row" if label else ' of the one-row table'),
                    column=col)
        return out, info
    return misfit(f'the answer is a {have}; the question asks for a {want}')


def note(req_shape, info):
    """The instruction a re-plan receives."""
    what = {'count': 'one integer count', 'value': 'one number', 'patient_set': 'the list of patient IDs',
            'table': 'a table with one row per group'}.get(req_shape, req_shape)
    extra = ''
    if info.get('percent'): extra += ' Express it as a percentage (0-100), not a fraction.'
    if info.get('decimals') is not None: extra += f" Round to {info['decimals']} decimal places."
    return (f"\n\nNOTE: an earlier route answered with a {info.get('answered')} ({info.get('reason')}). "
            f"This question asks for {what}.{extra} Compute exactly that quantity.")


def withhold(answer, info):
    """Non-final answer: the wrong-shaped result is withheld (AGERE_GUARD_POLICY=low keeps it at low confidence)."""
    from .answer_checks import _add
    out = dict(answer)
    reason = f"Answer shape: the question asks for a {info.get('requested')}; the route returned a {info.get('answered')} ({info.get('reason')})"
    if os.environ.get('AGERE_GUARD_POLICY', 'abstain') != 'low':
        out['withheld'] = {k: answer.get(k) for k in ('answer_type', 'count', 'value') if answer.get(k) is not None} | \
            ({'rows': len(answer['table'])} if isinstance(answer.get('table'), list) else {})
        out.update(count=None, value=None, table=None, patient_ids=None, answer_type='unsupported', status='partial')
        out['missing'] = list(out.get('missing') or []) + [reason]
    _add(out, 'QC-SHAPE', 'FM-02', ('Not shipped: ' if out.get('answer_type') == 'unsupported' else 'Low confidence: ') + reason)
    return out


def _final_operation(graph):
    nodes = (graph or {}).get('nodes') or []
    return nodes[-1].get('operation') if nodes and isinstance(nodes[-1], dict) else None


def review(stack, question, graph, receipt, result, *, ledger=None, profile=None, hosted=None):
    """Engine fit + conformance on one executed answer. -> (graph, receipt, result, new_paid_calls, replaced).
    A misfit declines the rung that produced it: one re-plan through the verified hosted SQL ladder (answer_guard.hosted_sql,
    or `hosted` in tests) with the requested shape stated. A hosted MissingData claim becomes an unsupported answer."""
    if not enabled() or not isinstance(receipt, dict) or not isinstance(result, dict) or not isinstance(result.get('answer'), dict):
        return graph, receipt, result, 0, False
    answer, info = conform(question, result['answer'], operation=_final_operation(graph))
    info['route'] = receipt.get('route')
    receipt['answer_fit'] = info
    if info['status'] in ('fits', 'reshaped'):
        return graph, receipt, {**result, 'answer': answer}, 0, False
    if info['status'] != 'misfit':
        return graph, receipt, result, 0, False
    live = bool(getattr(stack, 'allow_live', False)) and ledger is not None and os.environ.get('AGERE_FIT_REPLAN', 'on') != 'off'
    if not live:
        info['decision'] = 'withheld'
        return graph, receipt, {**result, 'answer': withhold(result['answer'], info)}, 0, False
    from . import answer_guard
    hosted = hosted or answer_guard.hosted_sql
    calls0 = receipt.get('new_paid_api_calls') or 0
    try:
        g2, r2, res2 = hosted(stack, question, ledger, feedback=note(info['requested'], info), stage='answer_fit_replan', profile=profile)
    except (ValueError, RuntimeError, OSError) as exc:
        usd, n = getattr(exc, 'guard_cost', 0.0) or 0.0, getattr(exc, 'guard_calls', 0) or 0
        answer_guard._add_step(receipt, 'answer_fit_replan', usd, n, error=str(exc)[:200])
        from .nl2sql import MissingData
        if isinstance(exc, MissingData):
            from .analysis_graph import validate
            as_of = stack.retriever.meta['snapshot']['as_of']
            g2 = validate({'interpretation': f'Unsupported by received data: {question}', 'coverage': 'unsupported', 'nodes': [],
                           'unmet_requirements': ['Missing input: ' + str(exc)[:400]]}, as_of)
            res2 = stack.run_graph(g2, question=question)
            info['decision'] = 'replanned_unsupported'
            receipt['replaced_route'] = {'route': receipt.get('route'), 'reason': info.get('reason')}
            receipt['route'] = 'hosted_missing_data'
            receipt['missing_check'] = {'claim': str(exc)[:300], 'contradicted': list(getattr(exc, 'contradicted', None) or [])}
            return g2, receipt, res2, (receipt.get('new_paid_api_calls') or 0) - calls0, True
        info['decision'] = 'withheld'; info['replan_error'] = str(exc)[:300]
        return graph, receipt, {**result, 'answer': withhold(result['answer'], info)}, (receipt.get('new_paid_api_calls') or 0) - calls0, False
    usd = r2.pop('_cost', None) or 0.0
    a2, info2 = conform(question, res2.get('answer'), operation=_final_operation(g2))
    info['replan'] = {'route': r2.get('route'), 'status': info2['status'], 'cost_usd': round(usd, 6),
                      'paid_calls': r2.get('new_paid_api_calls', 0)}
    if info2['status'] in ('fits', 'reshaped', 'skipped'):
        r2['steps'] = list(receipt.get('steps') or []) + list(r2.get('steps') or [])
        r2['new_paid_api_calls'] = calls0 + (r2.get('new_paid_api_calls') or 0)
        r2['replaced_route'] = {'route': receipt.get('route'), 'reason': info.get('reason'),
                                'local_compiler_declined': receipt.get('local_compiler_declined')}
        r2['local_compiler_declined'] = receipt.get('local_compiler_declined')
        info['decision'] = 'replanned'
        r2['answer_fit'] = info
        return g2, r2, {**res2, 'answer': a2}, r2['new_paid_api_calls'] - calls0, True
    answer_guard._add_step(receipt, 'answer_fit_replan', usd, r2.get('new_paid_api_calls', 0), still=info2.get('reason'))
    info['decision'] = 'withheld'
    return graph, receipt, {**result, 'answer': withhold(result['answer'], info)}, (receipt.get('new_paid_api_calls') or 0) - calls0, False


def final(question, graph, receipt, result):
    """Last conformance on the shipped answer (no re-plan): a later step (guard re-plan, cross-check replacement) may have
    replaced the answer after review(). Reshapes / rounds when unambiguous; a misfit is withheld as non-final."""
    if not enabled() or not isinstance(result, dict) or not isinstance(result.get('answer'), dict): return result
    answer, info = conform(question, result['answer'], operation=_final_operation(graph))
    if isinstance(receipt, dict) and info['status'] != 'skipped':
        receipt.setdefault('answer_fit', {})['final'] = {k: info.get(k) for k in ('status', 'how', 'reason', 'rounded') if info.get(k) is not None}
    if info['status'] == 'misfit':
        info['decision'] = 'withheld'
        return {**result, 'answer': withhold(result['answer'], info)}
    return {**result, 'answer': answer}
