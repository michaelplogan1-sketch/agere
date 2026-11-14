"""ONE acceptance contract for every route (Codex final review 2026-10-24, "Acceptance still differs by route"; ranked change 2).

Every final executed proposal -- the grammar, a saved or reused contract, the spec path, the chart reader, the governed engines, the analyst and
every other cascade tier -- passes this function before it ships:

    evaluate(question, contract=..., graph=..., answer=..., as_of=...) -> receipt

The question is read into the parts it requires and the proposal (compiled request / typed predicate tree, executable SQL, engine parameters,
executed result shape) must cover each part exactly:

  scope        conditions, qualifiers and demographics named by the question are in the executable plan (literal_check, requirement_ledger)
  operators    AND / OR / NOT bound to their OPERANDS: in a typed request the two operands' lowest common ancestor must be the connective the
               question wrote ("diabetes and CKD" is a conjunction of those two, not an OR); a negation is applied to the concept it governs
               ("without CKD": the CKD predicate is negated), not merely present somewhere
  windows      "last year" / "past 12 months" carries the 12-month rule (decision_context.year_window); a calendar year its year
  numbers      every number is carried; a comparator keeps its direction ('3 or more' is >= 3); "top five" keeps N exactly
  outputs      count / list / share / table / ranking: a share has its denominator and a stated scope ("of the patients with hypertension")
               restricts that denominator; "by payer" groups by that dimension; a ranking keeps its direction (highest -> descending) and N;
               the executed answer has the requested shape (literal_check.shape_misfit)
  evidence     a positive note answer cites the record it was read from

The receipt lists every requirement and how it was met: status met | unmet | unverified (could not be bound to the plan's representation; never a
rejection) | not_applicable. A failed contract is never a refusal by itself: the caller passes the proposal to the NEXT layer (heal, don't block);
only the last layer's unmet contract becomes a decline, and it names the unmet part (decline_message).

AGERE_ACCEPTANCE: on (default) | observe (computed and attached, nothing rejected; for measurement) | off.
"""
import contextvars
import json
import os
import re

from . import local_normalizer as N
from . import requirement_ledger
from .literal_check import ALIASES, executable_text, graph_evidence_text, shape_misfit

VERSION = 'acceptance-contract/1'
MET, UNMET, UNVERIFIED, NA = 'met', 'unmet', 'unverified', 'not_applicable'
DECLINE_STATUSES = ('unsupported', 'not_available', 'clarify', 'not_understood', 'grounding_failed')


# routes the contract rejected for THIS question (shared_planner._compose): 'cache', 'grammar', 'engine', 'reuse' and cascade tier names; the next
# plan() skips them, so the proposal goes to the next layer
REJECTED = contextvars.ContextVar('agere_acceptance_rejected', default=frozenset())


class Unmet(Exception):
    """An executed proposal left a requirement unmet; routes = the layers to skip when the question is planned again."""
    def __init__(self, routes, receipt, planner_receipt=None):
        super().__init__('acceptance contract unmet: ' + '; '.join(receipt['unmet'])[:300])
        self.routes, self.receipt = frozenset(routes), receipt
        self.planner_receipt = {k: v for k, v in (planner_receipt or {}).items() if k != 'request_contract'}


def route_tokens(interpretation, contract=None):
    """The layer(s) that produced a proposal, as the names plan() can skip."""
    tokens, interp = set(), (interpretation or {}).get('interpreter')
    if interpretation.get('exact_contract_cache_hit') or interpretation.get('carried_forward_from'): tokens.add('cache')
    if interp in ('compositional_grammar', 'compositional_grammar_program'): tokens.add('grammar')
    if interp == 'population_engine' or (contract or {}).get('kind') == 'engine_form': tokens.add('engine')
    if interpretation.get('request_shape_id') or interpretation.get('analysis_shape_id') or interpretation.get('spec_slot_reuse'): tokens.add('reuse')
    proposer = (interpretation.get('proposer') or {}).get('name')
    if proposer: tokens.add(proposer)
    elif interp in ('paraphrase_reuse', 'local_normalizer'): tokens.add(interp)
    return tokens or {interp or 'unknown'}


def skipped(route):
    return route in REJECTED.get()


def mode():
    value = os.environ.get('AGERE_ACCEPTANCE', 'on').strip().lower()
    return value if value in ('on', 'observe', 'off') else 'on'


def enforcing():
    return mode() == 'on'


def _row(rid, kind, text, status, how=None, reason=None, **extra):
    out = {'id': rid, 'kind': kind, 'text': text, 'status': status}
    if how: out['how'] = how
    if reason: out['reason'] = reason
    out.update(extra)
    return out


def _dump(value):
    return json.dumps(value, default=str, sort_keys=True)


# -- the proposal's views: executable text, typed specs ------------------------------------------------------------------------
TEXT_KEYS = ('query', 'membership_query', 'request', 'analysis', 'program', 'nodes', 'returns')


def evidence(contract=None, graph=None):
    """Executable text of the proposal: compiled SQL, predicates, outputs and engine parameters; never a restatement of the question."""
    contract = contract or {}
    parts = []
    kind = contract.get('kind')
    if kind == 'checked_graph':
        graph = graph or contract.get('graph')
        contract = {}
    if graph:
        parts.append(graph_evidence_text(graph))
    if contract:
        parts.append(executable_text({k: v for k, v in contract.items() if k in TEXT_KEYS}))
        if kind == 'engine_form': parts.append(executable_text(contract.get('params')))
    return '\n'.join(p for p in parts if p)


def specs(contract=None, graph=None):
    """Typed request specs (source / select / where / group_by / order_by / limit) the proposal carries, one per program node."""
    contract = contract or {}
    out = []

    def add(c):
        if isinstance(c, dict) and isinstance(c.get('select'), list): out.append(c)
    kind = contract.get('kind')
    if kind == 'request_program':
        for node in contract.get('nodes') or []: add((node.get('contract') or {}).get('request'))
    elif kind not in ('general_analysis', 'engine_form', 'checked_graph'):
        add(contract.get('request'))
    return out


def charts(contract=None, graph=None):
    contract = contract or {}
    graph = graph or (contract.get('graph') if contract.get('kind') == 'checked_graph' else None)
    return [p['chart'] for n in (graph or {}).get('nodes', []) for p in [n.get('parameters') or {}] if isinstance(p.get('chart'), dict)]


def queries(contract=None, graph=None):
    """Every executable SQL string of the proposal."""
    contract = contract or {}
    graph = graph or (contract.get('graph') if contract.get('kind') == 'checked_graph' else None)
    out = [n['parameters']['query'] for n in (graph or {}).get('nodes', []) if isinstance(n.get('parameters'), dict) and isinstance(n['parameters'].get('query'), str)]
    for key in ('query', 'membership_query'):
        if isinstance(contract.get(key), str): out.append(contract[key])
    for node in contract.get('nodes') or []:
        c = node.get('contract') or {} if isinstance(node, dict) else {}
        if isinstance(c.get('query'), str): out.append(c['query'])
    return out


def sql_digest(graph_or_queries):
    """Digest of the exact executable SQL of a proposal (graph nodes' queries): what an audit binds to."""
    import hashlib
    qs = graph_or_queries if isinstance(graph_or_queries, (list, tuple)) else queries(graph=graph_or_queries)
    return hashlib.sha256('\n'.join(re.sub(r'\s+', ' ', q).strip() for q in qs).encode()).hexdigest()[:16]


def audit_bound(receipt, graph):
    """True when the analyst's definitions audit covered the SQL that finally executes (not only the analyst's own run before reconciliation)."""
    audit = (receipt or {}).get('definitions_audit') if isinstance(receipt, dict) else None
    return bool(isinstance(audit, dict) and audit.get('final_sql_digest') and graph and audit['final_sql_digest'] == sql_digest(graph)
                and not audit.get('final_sql_findings'))


def _sql(text):
    return re.sub(r'\s+', ' ', text or '').lower()


# -- typed predicate trees -------------------------------------------------------------------------------------------------------
def _strip(value):
    if isinstance(value, dict): return {k: _strip(v) for k, v in value.items() if k != 'quote'}
    if isinstance(value, list): return [_strip(v) for v in value]
    return value


def _trees(obj, out=None):
    """Every boolean predicate tree of a spec (request where, measure where, ratio numerator / denominator where)."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == 'where' and isinstance(value, dict): out.append(value)
            _trees(value, out)
    elif isinstance(obj, list):
        for value in obj: _trees(value, out)
    return out


def _paths(expr, path=(), out=None):
    """[(leaf, path)] of a predicate tree; path = ((op, index), ...) from the root, 'not' as its own step."""
    out = [] if out is None else out
    if not isinstance(expr, dict): return out
    op = expr.get('op')
    if op in ('and', 'or'):
        for i, arg in enumerate(expr.get('args') or []): _paths(arg, path + ((op, i),), out)
    elif op == 'not':
        _paths(expr.get('arg'), path + (('not', 0),), out)
    else:
        out.append((expr, path))
    return out


def _has_key(key, blob):
    low = blob.lower()
    rx = N.CONCEPTS.get(key)
    return bool((rx and re.search(rx, low.replace('_', ' '), re.I)) or re.search(key.replace('_', '[ _]?'), low)
                or (key in ALIASES and re.search(ALIASES[key], low, re.I)))


def _leaf_blob(leaf):
    return _dump(_strip(leaf))


def _lca_op(p, q):
    for i, (a, b) in enumerate(zip(p, q)):
        if a != b: return a[0] if a[0] == b[0] else None  # diverge under one node: that node's connective
    return None


def _negated(path):
    return sum(1 for op, _ in path if op == 'not') % 2 == 1


NEG_LEAF_OPS = ('!=', 'not_in', 'is_null', '<>')
_SEP = re.compile(r'\s*(?:,\s*)?(?P<op>and/or|and|or|&)\s+(?:(?:also|with|have|has|a|an|the|any|either)\s+)*', re.I)
_NEG_CUE = re.compile(r"\bw/o\b|\b(?:no|not|never|without|neither|nor|missing|missed|didn't|doesn't|don't|haven't|hasn't|excluding|exclude|except|lacking|lack)\b", re.I)
_NEG_FILLER = r'(?:(?:a|an|any|the|on|taking|had|have|has|having|received|documented|recorded|with|their|ever|been|prescribed|filled|recent|current|active|an?\s+active)\s+){0,3}'


def _connective(between):
    m = _SEP.fullmatch(between or '')
    if not m or len(between) > 32: return None
    op = m.group('op').lower()
    return 'or' if op in ('or', 'and/or') else 'and'


def bound_operators(question, spec_list):
    """AND / OR / NOT bound to their operands, over the typed predicate trees. -> [rows]."""
    rows = []
    trees = [t for s in spec_list for t in _trees(s)]
    if not trees: return rows
    leaves = [(leaf, path) for t in trees for leaf, path in _paths(t)]
    spans = requirement_ledger._concept_spans(question)
    for (s1, e1, k1), (s2, e2, k2) in zip(spans, spans[1:]):
        op = _connective(question[e1:s2])
        if not op or k1 == k2: continue
        if _NEG_CUE.search(question[max(0, s1 - 28):s1]) and op == 'and':
            continue  # "without diabetes and CKD": the scope of the negation is not stated by the words; not bound
        text = question[s1:e2]
        rid = f'operator:{op}:{k1}|{k2}'
        a = [(leaf, path) for leaf, path in leaves if _has_key(k1, _leaf_blob(leaf))]
        b = [(leaf, path) for leaf, path in leaves if _has_key(k2, _leaf_blob(leaf))]
        if not a or not b:
            rows.append(_row(rid, 'operator', text, UNVERIFIED, how='an operand has no predicate in the typed request')); continue
        verdicts = []
        for la, pa in a:
            for lb, pb in b:
                if la is lb:
                    names = la.get('names') if la.get('op') == 'concept' else None
                    verdicts.append(op == 'or' and bool(names) and len(names) > 1)  # one concept leaf over several names is a disjunction
                    continue
                if not pa or not pb: continue
                got = _lca_op(pa, pb)
                if got is None: continue
                verdicts.append(got == op)
        if not verdicts:
            rows.append(_row(rid, 'operator', text, UNVERIFIED, how='the operands are not combined in one predicate tree'))
        elif any(verdicts):
            rows.append(_row(rid, 'operator', text, MET, how=f'the typed request combines {k1.replace("_", " ")} and {k2.replace("_", " ")} with {op.upper()}'))
        else:
            other = 'OR' if op == 'and' else 'AND'
            rows.append(_row(rid, 'operator', text, UNMET,
                             reason=f'"{text}" is a {"conjunction (both)" if op == "and" else "disjunction (either)"}; the plan combines '
                                    f'{k1.replace("_", " ")} and {k2.replace("_", " ")} with {other}'))
    for m in _NEG_CUE.finditer(question):
        after = question[m.end():m.end() + 60]
        mm = re.match(r'\s+' + _NEG_FILLER + r'(?=\S)', after, re.I)
        tail = after[mm.end():] if mm else after.lstrip()
        concept = next(((k, c.start()) for k, rx in N.CONCEPTS.items() for c in [re.match(rx, tail, re.I)] if c), None)
        if concept is None:
            concept = next(((k, c.start()) for k, rx in N.CONCEPTS.items() for c in [re.match(r'(?:' + rx + r')', tail, re.I)] if c), None)
        if concept is None: continue
        key = concept[0]
        mine = [(leaf, path) for leaf, path in leaves if _has_key(key, _leaf_blob(leaf))]
        rid, text = f'operator:not:{key}', question[m.start():m.end() + (mm.end() if mm else 0) + 24].strip()
        if not mine:
            rows.append(_row(rid, 'operator', text, UNVERIFIED, how='the negated concept has no predicate in the typed request')); continue
        if any(_negated(path) or leaf.get('op') in NEG_LEAF_OPS for leaf, path in mine):
            rows.append(_row(rid, 'operator', text, MET, how=f'{key.replace("_", " ")} is negated in the typed request'))
        else:
            rows.append(_row(rid, 'operator', text, UNMET, reason=f'"{text}" excludes {key.replace("_", " ")}; the plan requires it (the negation was dropped)'))
    return rows


# -- windows -----------------------------------------------------------------------------------------------------------------------
def windows(question, text, as_of, spec_list=()):
    rows = []
    if not as_of: return rows
    from . import decision_context as DC
    low = text.lower()
    for i, w in enumerate(DC.year_window(question, as_of)):
        rid = f'window:{w["kind"]}:{i}'
        if w['kind'] == 'rolling_12_months':
            start = DC.rolling_year(as_of)[0]
            ok = (w['start'] in low or start in low or re.search(r"-\s*12\s*months?|-\s*1\s*years?|-\s*365\s*days?", low)
                  or re.search(r'within_last_months"?\s*[,:]?[^\d]{0,12}12\b', low) or re.search(r'"value"\s*:\s*12\b', low) or 'last_12' in low or 'rolling' in low)
            rows.append(_row(rid, 'window', w['phrase'], MET if ok else UNMET,
                             how=f'the 12 months ending {as_of[:10]} are in the plan' if ok else None,
                             reason=None if ok else f'"{w["phrase"]}" is the 12 months ending {as_of[:10]}; the plan carries no such bound'))
        else:
            year = w['start'][:4]
            ok = year in text
            # Consecutive year comparisons can compile to a single half-open
            # interval: 2028..2030 includes all of 2029 without spelling 2029.
            def atoms(expr):
                if not isinstance(expr,dict): return []
                if expr.get('op') == 'and': return [a for c in expr.get('args',[]) for a in atoms(c)]
                return [expr] if 'field' in expr else []
            for spec in spec_list:
                grouped = {}
                for a in atoms(spec.get('where')):
                    grouped.setdefault(a['field'],[]).append(a)
                for bounds in grouped.values():
                    starts=[str(a.get('value')) for a in bounds if a.get('op') == '>=']
                    ends=[str(a.get('value')) for a in bounds if a.get('op') == '<']
                    if any(v <= year+'-01-01' and re.fullmatch(r'\d{4}-\d{2}-\d{2}',v) for v in starts) and any(v >= str(int(year)+1)+'-01-01' and re.fullmatch(r'\d{4}-\d{2}-\d{2}',v) for v in ends):
                        ok = True
            rows.append(_row(rid, 'window', w['phrase'], MET if ok else UNMET, how=f'calendar year {year} is in the plan' if ok else None,
                             reason=None if ok else f'"{w["phrase"]}" is calendar year {year}; the plan does not carry it'))
    return rows


# -- outputs: ranking (direction and N), group-by dimension, share denominator and scope ------------------------------------------
_DESC = re.compile(r'\b(?:most(?!\s+recent)|highest|largest|biggest|top|greatest|leading|longest)\b', re.I)
_ASC = re.compile(r'\b(?:(?<!at )least|lowest|fewest|smallest|bottom|shortest)\b', re.I)
_WORDS = '|'.join(sorted(N.NUMBER_WORDS, key=len, reverse=True))
_RANK_NOUN = r'(?:highest|lowest|largest|smallest|biggest|top|bottom|best|worst|leading|most|fewest|least)'
_TOP_N = (re.compile(rf'\b(?:top|bottom|first)\s+(\d+|{_WORDS})\b', re.I),
          re.compile(rf'\b(\d+|{_WORDS})\s+(?:highest|lowest|largest|smallest|biggest|most|fewest|best|worst|leading|top|bottom)\b', re.I))


def _num(token):
    t = token.lower()
    return str(float(t)).rstrip('0').rstrip('.') if re.fullmatch(r'\d+(?:\.\d+)?', t) else N.NUMBER_WORDS.get(t)


def wanted_ranking(question):
    """-> (direction 'desc'|'asc'|None, N str|None, phrase)."""
    desc, asc = _DESC.search(question), _ASC.search(question)
    if desc and asc: return None, None, None
    hit = desc or asc
    if not hit: return None, None, None
    n = None
    for rx in _TOP_N:
        m = rx.search(question)
        if m and _num(m.group(1)): n = _num(m.group(1)); break
    return ('desc' if desc else 'asc'), n, hit.group(0)


def _order_directions(spec_list, chart_list, queries_list, text):
    found = []
    for s in spec_list:
        for o in s.get('order_by') or []:
            if isinstance(o, dict) and o.get('direction') in ('asc', 'desc'): found.append(o['direction']); break
    for c in chart_list:
        for o in c.get('order_by') or []:
            if isinstance(o, dict) and o.get('direction') in ('asc', 'desc'): found.append(o['direction']); break
    for q in queries_list:
        for m in re.finditer(r'order\s+by\s+(.+?)(?:\blimit\b|\bhaving\b|\bgroup\s+by\b|\)|;|$)', _sql(q)):
            first = re.split(r',(?![^(]*\))', m.group(1))[0]
            found.append('desc' if re.search(r'\bdesc\b', first) else 'asc')
    low = _sql(text)
    if not found:
        if re.search(r'\bmax\s*\(', low) and not re.search(r'\bmin\s*\(', low): found.append('desc')
        if re.search(r'\bmin\s*\(', low) and not re.search(r'\bmax\s*\(', low): found.append('asc')
    return found


def _limits(spec_list, chart_list, queries_list, text):
    found = []
    for s in spec_list:
        lim = s.get('limit')
        if isinstance(lim, dict) and lim.get('value') is not None: found.append(lim['value'])
    for c in chart_list:
        if c.get('limit') is not None: found.append(c['limit'])
    for q in queries_list:
        found += re.findall(r'\blimit\s+(\d+)', _sql(q))
    found += re.findall(r'"(?:limit|top_n|top|n|rows)"\s*:\s*(\d+)', text)
    out = set()
    for v in found:
        try: out.add(str(float(v)).rstrip('0').rstrip('.'))
        except (TypeError, ValueError): pass
    return out


def ranking(question, spec_list, chart_list, queries_list, text, answer):
    direction, n, phrase = wanted_ranking(question)
    rows = []
    if direction is None: return rows
    if re.search(r'\b(?:most|least)\s+recent|\bhighest\s+(?:risk\s+)?(?:a1c|ldl|egfr|bp|creatinine|value)\b', question, re.I) and not _TOP_N[0].search(question):
        pass  # a lab extreme or recency, not a ranking of groups: the direction is still checked below through MAX / MIN
    got = _order_directions(spec_list, chart_list, queries_list, text)
    rid = f'ranking:direction:{direction}'
    if not got:
        rows.append(_row(rid, 'output.ranking', phrase, UNVERIFIED, how='the proposal states no ordering to compare (engine or fixed form)'))
    elif direction in got:
        rows.append(_row(rid, 'output.ranking', phrase, MET, how=f'ordered {"descending" if direction == "desc" else "ascending"}'))
    else:
        rows.append(_row(rid, 'output.ranking', phrase, UNMET,
                         reason=f'"{phrase}" asks for {"descending (highest first)" if direction == "desc" else "ascending (lowest first)"} order; '
                                f'the plan orders {"ascending" if direction == "desc" else "descending"}'))
    if n is not None:
        limits = _limits(spec_list, chart_list, queries_list, text)
        rid = f'ranking:n:{n}'
        shown = answer.get('table') if isinstance(answer, dict) and isinstance(answer.get('table'), list) else None
        if limits:
            ok = n in limits
            rows.append(_row(rid, 'output.ranking', f'top {n}', MET if ok else UNMET, how=f'keeps the first {n}' if ok else None,
                             reason=None if ok else f'the question asks for {n}; the plan keeps {", ".join(sorted(limits))}'))
        elif shown is not None:
            ok = len(shown) <= int(float(n))
            rows.append(_row(rid, 'output.ranking', f'top {n}', MET if ok else UNMET, how=f'the answer has {len(shown)} rows',
                             reason=None if ok else f'the question asks for {n}; the answer has {len(shown)} rows'))
        else:
            rows.append(_row(rid, 'output.ranking', f'top {n}', UNVERIFIED, how='no limit stated by the proposal'))
    return rows


DIMENSIONS = {
    'payer': r'payer|line_of_business|\blob\b|coverage|insurance|plan_type|product', 'practice': r'practice|clinic|site|\bpcp\b|provider|location',
    'sex': r'\bsex(?:_reported)?\b|gender', 'month': r'month|strftime|date_trunc', 'year': r'year|strftime', 'quarter': r'quarter|strftime',
    'age': r'\bage\b|age_band|age_group|birth', 'race': r'race|ethnic', 'language': r'language', 'specialty': r'specialty',
    'condition': r'condition|diagnos|concept|disease', 'drug': r'drug|medication|class', 'code': r'\bcode|carc|icd|cpt|drg',
}
_DIM_Q = {'payer': r'payer|coverage|line of business|insurance|plan|lob', 'practice': r'practice|clinic|site|pcp|provider|location', 'sex': r'sex|gender',
          'month': r'month', 'year': r'year', 'quarter': r'quarter', 'age': r'age|age band|age group', 'race': r'race|ethnicity',
          'language': r'language', 'specialty': r'specialty', 'condition': r'condition|diagnosis|disease', 'drug': r'drug|medication|drug class|class',
          'code': r'code|reason code|diagnosis code'}
_BY = re.compile(r'\b(?:by|per|for each|each|broken\s+down\s+by|split\s+(?:by|out\s+by)|across)\s+((?:[a-z][\w-]*\s*){1,3})', re.I)


def _group_evidence(spec_list, queries_list, text):
    names = []
    for s in spec_list:
        names += [str(g) for g in s.get('group_by') or []]
        names += [str(m.get('field') or '') for m in s.get('select') or [] if isinstance(m, dict) and m.get('alias') in (s.get('group_by') or [])]
    for q in queries_list:
        for m in re.finditer(r'group\s+by\s+(.+?)(?:\border\b|\bhaving\b|\blimit\b|\)|;|$)', _sql(q)): names.append(m.group(1))
    for m in re.finditer(r'"(?:group_by|groups?|by|dimension|grain|breakdown)"\s*:\s*(\[[^\]]*\]|"[^"]*"|\{[^}]*\})', text): names.append(m.group(1))
    return ' '.join(names)


def grouping(question, spec_list, queries_list, text):
    """'by payer' groups by that dimension, not merely by something."""
    from .analysis_graph import requested_shape
    rows = []
    if requested_shape(question) != 'table': return rows
    wanted = []
    for m in _BY.finditer(question):
        phrase = m.group(1).lower()
        if re.match(r'(?:at least|at most|more than|less than|over|under|about|around|up to|\d)', phrase): continue
        for dim, rx in _DIM_Q.items():
            if re.search(rf'\b(?:{rx})\b', phrase) and dim not in wanted: wanted.append(dim)
    if not wanted: return rows
    ev = _group_evidence(spec_list, queries_list, text)
    low = ev.lower()
    rid = 'output.group_by:' + '|'.join(wanted)
    if not low.strip():
        rows.append(_row(rid, 'output.group_by', ', '.join(wanted), UNVERIFIED, how='no grouping stated by the proposal (engine or fixed form)')); return rows
    have = [d for d, rx in DIMENSIONS.items() if re.search(rx, low)]
    if any(d in have for d in wanted):
        rows.append(_row(rid, 'output.group_by', ', '.join(wanted), MET, how='groups by ' + ', '.join(d for d in wanted if d in have)))
    elif have:
        rows.append(_row(rid, 'output.group_by', ', '.join(wanted), UNMET,
                         reason=f'the question breaks the result down by {", ".join(wanted)}; the plan groups by {", ".join(have)}'))
    else:
        rows.append(_row(rid, 'output.group_by', ', '.join(wanted), UNVERIFIED, how='the grouping column is not a known dimension'))
    return rows


_RATE_WORD = r'(?:share|percent(?:age)?|proportion|fraction|rate|ratio)'
_SCOPE_LEAD = r'(?:the\s+|our\s+|all\s+|these\s+|those\s+)?(?:(?:patients|members|people|adults|individuals)\s+(?:with|who\s+have|having|on|taking|diagnosed\s+with|who\s+are\s+on|who\s+take)\s+(?:a\s+|an\s+|the\s+)?)?'


def scope_concepts(question):
    """Concepts that name the POPULATION a share is of: 'of the patients with hypertension, what share ...', 'what share of diabetics ...',
    'among our CKD patients ...'. -> [(key, phrase)]."""
    found = []
    spans = requirement_ledger._concept_spans(question)
    for s, e, key in spans:
        head = question[:s]
        phrase = question[max(0, s - 60):e]
        if re.search(rf'\b{_RATE_WORD}\s+of\s+{_SCOPE_LEAD}$', head, re.I) or re.search(rf'\bamong\s+{_SCOPE_LEAD}$', head, re.I) \
                or re.match(rf'\s*(?:of|among)\s+{_SCOPE_LEAD}$', head, re.I):
            found.append((key, phrase.strip()))
    return list(dict.fromkeys(found))


def denominators(question, spec_list, queries_list):
    """A share has its denominator, and a stated scope restricts it. Typed: the ratio's denominator (or the request's where) carries the scope
    concept. -> [rows]."""
    rows = []
    from .literal_check import RATE_ASKED
    if not RATE_ASKED.search(question): return rows
    scope = scope_concepts(question)
    ratios = [(m, s) for s in spec_list for m in s.get('select') or [] if isinstance(m, dict) and m.get('kind') == 'ratio']
    if not ratios:
        if spec_list:
            rows.append(_row('output.share', 'output.share', 'share', UNVERIFIED, how='the typed request has no ratio measure (rate computed another way)'))
        return rows
    for m, s in ratios:
        denom = _leaf_blob(m.get('denominator') or {})
        where = _leaf_blob(s.get('where') or {})
        rows.append(_row('output.share:denominator', 'output.share', 'share', MET if m.get('denominator') else UNMET,
                         how='the ratio has a denominator measure', reason=None if m.get('denominator') else 'the share has no denominator'))
        for key, phrase in scope:
            rid = f'output.share:scope:{key}'
            if _has_key(key, denom) or _has_key(key, where):
                rows.append(_row(rid, 'output.share', phrase, MET, how=f'the denominator is restricted to {key.replace("_", " ")}'))
            else:
                rows.append(_row(rid, 'output.share', phrase, UNMET,
                                 reason=f'the share is "of {phrase.strip()}"; the denominator is not restricted to {key.replace("_", " ")}'))
    return rows


_COUNT_ASK = re.compile(r'\b(?:how\s+many|number\s+of|count(?:\s+of)?|tally)\b', re.I)
_LIST_ASK = re.compile(r'\b(?:list|show(?:\s+me)?|which|identify|name)\b(?:\s+(?:the|our|all|of|of the|of our))*(?:\s+[\w-]+){0,2}\s+(?:patients?|members?|people|individuals|pts)\b|'
                       r'\bwho\s+(?:are|is|have|has|had|were|was)\b|^\s*who\b', re.I)
_VALUE_ASK = re.compile(r'%|\b(?:percent(?:age)?|share|proportion|fraction|rate|average|mean|median|sum|total|how\s+much|pmpm|per\s+member\s+per\s+month|ratio)\b', re.I)
_BY_DIM = re.compile(r'\b(?:by|per|for\s+each|each|across|broken\s+down\s+by|split\s+(?:by|out\s+by))\s+(?:the\s+)?(?:payer|coverage|line\s+of\s+business|insurance|plan|practice|'
                     r'clinic|site|pcp|provider|location|sex|gender|month|year|quarter|week|age|race|ethnicity|language|specialty|condition|diagnosis|drug|medication|class|code|reason)\b', re.I)


def requested_output(question):
    """count | patient_set | table | value | None, read carefully: "how many ... who ..." is a count; "per claim" is not a breakdown."""
    q = re.sub(r'\(\s*(?:including|incl\.?|meaning|i\.e\.)\b[^)]*\)', ' ', question or '', flags=re.I)
    # Per-row measures remain scalar; explicit group comparisons and per-group
    # measures request a table even when the measure itself is an average/count.
    if re.search(r'\b(?:for\s+each|compare|versus|vs|compared\s+(?:with|to))\b',q,re.I): return 'table'
    if re.search(r'\b(?:by|split\s+by|grouped\s+by)\s+(?:[\w-]+\s+){0,2}(?:practice|payer|pharmac(?:y|ies)|sex|gender|need|tier|type|status|year|month|age)\b',q,re.I): return 'table'
    if re.search(r'\b(?:women|men|female|male|commercial|medicare|medicaid|dual|medigap)\b[^?]{0,35}\band\s+(?:of\s+)?(?:women|men|female|male|commercial|medicare|medicaid|dual|medigap)\b',q,re.I): return 'table'
    if re.search(r'\b(?:in|during)\s+(?:19|20)\d\d\s+and\s+(?:in|during)\s+(?:19|20)\d\d',q,re.I): return 'table'
    if re.search(r'\bwhich\s+(?:\d+|one|two|three|four|five)\b',q,re.I) and wanted_ranking(q)[0]: return 'table'
    if re.search(r'\bwhich\b',q,re.I) and not _COUNT_ASK.search(q):
        # "Which inputs ... behind each total" requests named rows; an
        # incidental measure word must not collapse that output to one value.
        # The shared parser keeps direct scalar asks and patient sets distinct.
        from .answer_shape import requested
        shared=requested(q)['shape']
        if shared in ('patient_set','table'):return shared
    if _COUNT_ASK.search(q) and not _VALUE_ASK.search(q): return 'table' if _BY_DIM.search(q) else 'count'
    if _BY_DIM.search(q) or re.search(r'\b(?:break(?:s)?\s*(?:it\s+)?(?:down|out)|breakdown|split|tabulate|monthly|quarterly|weekly|trend)\b', q, re.I): return 'table'
    if _LIST_ASK.search(q):
        # A measure may be a filter or a requested column of a patient list.
        # Use the shared shape parser before treating 'total' as one scalar.
        from .answer_shape import requested
        patient_shape = requested(q)['shape']
        if patient_shape in ('patient_set', 'table'): return patient_shape
    if _VALUE_ASK.search(q): return 'value'
    if _LIST_ASK.search(q): return 'patient_set'
    if _COUNT_ASK.search(q): return 'count'
    return None


def output_misfit(question, answer, contract=None):
    """None when the executed answer has the requested output, else why not. A one-row table is a scalar; a composite answer carries several outputs."""
    status = (answer or {}).get('status')
    if status in DECLINE_STATUSES: return None
    if status not in ('answered', 'partial'): return f'the plan did not produce an answer (status {status})'
    got, rows = answer.get('answer_type'), answer.get('table')
    if got == 'composite': return None
    wanted = requested_output(question)
    if wanted is None: return None
    if wanted == 'count' and got == 'table' and isinstance(contract, dict):
        from .claim_count_policy import validated_dual_counts
        if validated_dual_counts(contract, answer): return None
    one_row = isinstance(rows, list) and len(rows) == 1
    ok = {'count': {'count', 'value', 'patient_set'}, 'value': {'value', 'count'}, 'patient_set': {'patient_set', 'table'}, 'table': {'table'}}[wanted]
    if got in ok or (wanted in ('count', 'value') and got == 'table' and one_row): return None
    from .literal_check import RANKED_GROUPS
    if got == 'table' and RANKED_GROUPS.search(question or ''): return None
    return f'the question asks for a {wanted.replace("_", " ")}; the plan returned a {str(got).replace("_", " ")}'


# -- evidence needs --------------------------------------------------------------------------------------------------------------
_EVIDENCE_ASK = re.compile(r'\b(?:why|reason|notes?|documented|chart|impression|mention(?:ed|s)?|say|says|said|quote|written|record(?:ed)?)\b', re.I)
_NO_FINDING = re.compile(r'not (?:documented|mentioned|recorded|found)|no (?:mention|documentation|note)|undecided|unknown|not available|none', re.I)
_RECORD_ID = re.compile(r'record_id|\bWH[A-Z]-[A-Z]-[0-9a-f]{6,}|\bN-\d{3,}', re.I)


_NOTES_NAMED = re.compile(r'\b(?:notes?|chart notes?|documentation|documents?)\b', re.I)
_READS_NOTES = re.compile(r'note|document|hie|scanned|ocr|passage|retriev|text|"frame"', re.I)


def evidence_need(question, answer, text):
    if not isinstance(answer, dict) or answer.get('status') not in ('answered', 'partial'): return []
    rows = []
    chart = bool(re.search(r'"op": "note"|note_predicates|chart_reader|"frame"', text) or answer.get('chart_reader') is not None)
    head0 = str(answer.get('chart_headline') or answer.get('headline') or answer.get('value') or '').strip()
    if chart and 'unknown' in ((answer.get('facts') or {}).get('statuses') or []):
        rows.append(_row('evidence:undecided', 'evidence', 'settled note reading', UNMET,
                         reason='the note reader is undecided; this is not a completed answer and another layer must attempt the reading'))
    if re.search(r'\b(?:why|reason|what|how)\b', question, re.I) and 'not as asked' in head0 and not re.match(r'(?:No|Not documented)\b', head0):
        rows.append(_row('evidence:matches', 'evidence', 'what was asked', UNMET,
                         reason='a note finding was read, but not the one asked for (the reader reports it as documented but not as asked); '
                                'the detail the question asks for is not in it'))
    if (answer.get('chart_reader') is not None or 'chart' in text) and re.search(r'\bnotes?\b', question, re.I) and not _READS_NOTES.search(text):
        rows.append(_row('evidence:source', 'evidence', 'the notes', UNMET,
                         reason='the question asks what the notes say; the plan does not read any note, document or passage'))
    blob = _dump({k: answer.get(k) for k in ('table', 'grounding', 'facts', 'headline', 'chart_headline', 'notes', 'sources') if answer.get(k) is not None})
    absent = bool(_NO_FINDING.match(head0.lstrip('Yes:').strip()) or _NO_FINDING.search(head0[:60]))
    if re.search(r'\b(?:why|reason)\b', question, re.I) and requirement_ledger.PATIENT_ID.search(question) and answer.get('answer_type') != 'composite' \
            and not _RECORD_ID.search(blob) and not answer.get('narrative') and not absent:
        rows.append(_row('evidence:reason', 'evidence', 'the reason asked for', UNMET,
                         reason='a reason was asked for about a named patient; the answer carries no passage or record that states one'))
    if not chart: return rows
    if not _EVIDENCE_ASK.search(question): return rows
    head = str(answer.get('headline') or answer.get('chart_headline') or '')
    if _NO_FINDING.match(head.strip().lstrip('Yes:').strip()) or _NO_FINDING.search(head[:60]): return rows
    if _RECORD_ID.search(blob):
        return rows + [_row('evidence:citation', 'evidence', 'cited record', MET, how='the answer cites the record(s) it was read from')]
    return rows + [_row('evidence:citation', 'evidence', 'cited record', UNMET, reason='a positive note answer must cite the record it was read from; none is cited')]


# -- the contract ----------------------------------------------------------------------------------------------------------------
_QUOTED = re.compile(r'"[^"]*"|\'[^\']*\'')
_LAB_RATIO = re.compile(r'\b(?:albumin|protein)[-/ ]*(?:to[-/ ]*)?creatinine\s+ratio\b|\bcreatinine\s+ratio\b|\b(?:inr|a/g)\s+ratio\b', re.I)
_PLACE = (('breakdown', 'output.group_by'), ('rate or share', 'output.share'), ('average', 'output.average'), ('ranking', 'output.ranking'),
          ('time window', 'window'), ('month', 'window'))


def _ledger_rows(question, text):
    q = requirement_ledger._covered_ids(question, text)
    ledger = requirement_ledger.check(q, text)
    rows = []
    for r in ledger['requirements']:
        if r['kind'] == 'comparator': kind = 'threshold'
        elif r['kind'] == 'boolean': kind = 'operator'
        else: kind = {'number': 'number', 'negation': 'operator'}.get(r.get('literal'), 'scope')
        status = {'covered': MET, 'dropped': UNMET}.get(r['status'], UNVERIFIED if r['status'] == 'unverified' else MET)
        rows.append(_row(r['id'], kind, r['text'], status, how='carried by the executable plan' if status == MET else (
            'a number without an explicit operator; the literal check decides' if status == UNVERIFIED else None), reason=r.get('reason')))
    from .literal_check import RATE_ASKED
    for gap in ledger.get('other_gaps', []):
        # a '%' inside a quoted LIKE pattern, or the "ratio" of a lab name (urine albumin-creatinine ratio), is not a request for a rate
        if 'rate or share' in gap and not RATE_ASKED.search(_LAB_RATIO.sub(' ', _QUOTED.sub(' ', q))): continue
        kind = next((k for needle, k in _PLACE if needle in gap), 'scope')
        rows.append(_row(f'gap:{kind}', kind, gap[:80], UNMET, reason=gap))
    return rows


def evaluate(question, *, contract=None, graph=None, answer=None, as_of=None, route=None, extra_text='', phase=None, shape=True, lexical=True):
    """The acceptance receipt of one proposal: {'version', 'ok', 'phase', 'route', 'requirements', 'unmet', 'unverified'}. `answer` is the executed
    answer when it exists (shape and evidence checks run only then); a decline is never rejected here."""
    if isinstance(answer, dict) and answer.get('status') in DECLINE_STATUSES:
        return {'version': VERSION, 'ok': True, 'phase': phase or 'executed', 'route': route, 'requirements': [], 'unmet': [], 'unverified': [],
                'note': 'a decline states what it did not answer; no reduced question was executed'}
    text = '\n'.join(t for t in (evidence(contract, graph), extra_text) if t)
    spec_list, chart_list, query_list = specs(contract, graph), charts(contract, graph), queries(contract, graph)
    rows = _ledger_rows(question, text)
    if not lexical:
        # A fully consumed grammar reading or a governed engine form accounts for every word of the question by construction, and an engine's
        # parameters are not text a lexical presence check can read: only the structural parts (comparator directions, bound operators, windows,
        # denominators, ranking, grouping, output shape, evidence) are checked on it.
        rows = [r for r in rows if r['kind'] == 'threshold']
        rows.append(_row('scope:words', 'scope', 'every word of the question', NA, how='accounted for by the fully consumed grammar / governed engine form'))
    rows += bound_operators(question, spec_list)
    rows += windows(question, text, as_of, spec_list)
    rows += ranking(question, spec_list, chart_list, query_list, text, answer)
    rows += grouping(question, spec_list, query_list, text)
    rows += denominators(question, spec_list, query_list)
    if isinstance(answer, dict):
        if shape:  # answer_checks' own shape review (which may reshape) decides inside the cascade; every other route checks it here
            misfit = output_misfit(question, answer, contract=contract)
            rows.append(_row('output.shape', 'output.shape', 'requested output', UNMET if misfit else MET,
                             how=None if misfit else 'the executed answer has the requested shape', reason=misfit))
        rows += evidence_need(question, answer, text)
    unmet = list(dict.fromkeys(r['reason'] for r in rows if r['status'] == UNMET and r.get('reason')))
    return {'version': VERSION, 'ok': not unmet, 'phase': phase or ('executed' if isinstance(answer, dict) else 'plan'), 'route': route,
            'requirements': rows, 'unmet': unmet, 'unverified': [r['id'] for r in rows if r['status'] == UNVERIFIED]}


def merge(plan_summary, receipt):
    """The plan-time half (a summary carried on the proposal's receipt) and the executed half as one receipt: every requirement of both, the
    executed row winning where both name one."""
    if not isinstance(plan_summary, dict) or plan_summary.get('phase') != 'plan': return receipt
    ids = {r['id'] for r in receipt['requirements']}
    rows = [dict(r) for r in plan_summary.get('requirements') or [] if r['id'] not in ids] + list(receipt['requirements'])
    unmet = list(dict.fromkeys([*(plan_summary.get('unmet') or []), *receipt['unmet']]))
    return {**receipt, 'requirements': rows, 'unmet': unmet, 'ok': not unmet, 'phase': 'plan+executed',
            'unverified': list(dict.fromkeys([*(plan_summary.get('unverified') or []), *receipt['unverified']]))}


def analyst_text(receipt, graph=None):
    """Extra evidence text of a semantic-analyst proposal (every query it rests on, the rules its definition views apply, concept vocabulary): see
    analyst_acceptance. '' for every other tier."""
    from . import analyst_acceptance
    return analyst_acceptance.text(receipt, graph)


def gaps(question, *, contract=None, graph=None, as_of=None):
    """The unmet parts of a proposal before it executes (the cascade's plan-time check): readable reasons, [] when it passes."""
    return evaluate(question, contract=contract, graph=graph, as_of=as_of, phase='plan')['unmet']


def summary(receipt):
    """Compact form for answer receipts."""
    return {'version': receipt['version'], 'ok': receipt['ok'], 'phase': receipt['phase'], 'route': receipt.get('route'),
            'mode': mode(), 'unmet': receipt['unmet'], 'unverified': receipt['unverified'],
            'requirements': [{k: r.get(k) for k in ('id', 'kind', 'text', 'status', 'how', 'reason') if r.get(k) is not None} for r in receipt['requirements']]}


def decline_message(receipt):
    return ('The original question was preserved, but the proposed reading did not satisfy the acceptance contract: ' + '; '.join(receipt['unmet'])[:600]
            + '. No reduced question was executed.')
