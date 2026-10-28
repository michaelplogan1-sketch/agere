"""Compiler optimizer that fuses repeated sub-queries (query-compiler common-subexpression elimination).

Two levels, both semantics-preserving and both receipted:
  optimize_plan(plan)  graph level: a node identical to an earlier node (same operation, parameters and inputs after rewiring) is
                       executed once; consumers of the duplicate read the earlier node. The terminal node and nodes whose rewiring
                       would give a consumer the same input twice are never fused. Runs in analysis_graph.execute after validation.
  fuse_sql(query)      SQL level: a parenthesised SELECT that appears two or more times verbatim is hoisted into one
                       `WITH _fused1 AS MATERIALIZED (...)` and every occurrence reads it. Runs in sql_query.execute on the text
                       that is executed; the query recorded in the receipt stays the planned text. A fused query that fails
                       (for example a correlated sub-select that cannot stand alone) is re-run unfused: the optimizer heals, never blocks.
AGERE_PLAN_OPTIMIZER=off disables both. SQL fusion requires a live schema check,
verbatim subquery text and a supported deterministic, locally bound expression.
Unproven correlation, views, CTE dependencies and volatile functions retain the original query.
"""
import copy
import json
import os
import re

VERSION = 'plan-optimizer/3'
MIN_FUSE_CHARS = 40  # a repeated one-token sub-select is not worth a CTE


def enabled(): return os.environ.get('AGERE_PLAN_OPTIMIZER', 'on') != 'off'


# ---- graph level ----------------------------------------------------------------------------------------------------------
def _key(node, rewrite):
    inputs = [rewrite.get(x, x) for x in node.get('inputs') or []]
    return json.dumps([node['operation'], node.get('parameters'), inputs], sort_keys=True, default=str)


def _identity_sensitive(nodes):
    """Preserve opaque parameter references and their full input lineage rather than guessing how to rewrite them.

    A parameter key/value exactly matching a graph ID may be a typed reference or ordinary data. Conservatively
    retaining that branch is safe in either case. No SQL, patient identifier, question or arbitrary string is edited.
    """
    by = {n['id']: n for n in nodes}
    def matches(value):
        if isinstance(value, str): return {value} if value in by else set()
        if isinstance(value, dict):
            return set().union(*(matches(k) | matches(v) for k,v in value.items())) if value else set()
        if isinstance(value, (list, tuple)):
            return set().union(*(matches(v) for v in value)) if value else set()
        return set()
    preserve = set()
    for node in nodes:
        refs = matches(node.get('parameters'))
        if refs: preserve.update(refs | {node['id']} | set(node.get('inputs') or []))
    pending = list(preserve)
    while pending:
        ident = pending.pop()
        for parent in by[ident].get('inputs') or []:
            if parent not in preserve: preserve.add(parent); pending.append(parent)
    return preserve


def optimize_plan(plan, *, validator=None):
    """-> (plan, receipt). The plan is returned unchanged (same object) when nothing fuses."""
    nodes = (plan or {}).get('nodes') or []
    if not enabled() or len(nodes) < 2: return plan, {'version': VERSION, 'fused': []}
    terminal = nodes[-1]['id']
    preserve = _identity_sensitive(nodes)
    seen, rewrite, drop = {}, {}, []
    for n in nodes:
        k = _key(n, rewrite)
        if k in seen and n['id'] != terminal and n['id'] not in preserve:
            canon = seen[k]
            consumers = [m for m in nodes if n['id'] in (m.get('inputs') or [])]
            if all(canon not in [rewrite.get(x, x) for x in m['inputs'] if x != n['id']] for m in consumers):
                rewrite[n['id']] = canon; drop.append(n['id']); continue
        seen.setdefault(k, n['id'])
    if not drop: return plan, {'version': VERSION, 'fused': [], 'identity_preserved': sorted(preserve)}
    out = copy.deepcopy(plan)
    out['nodes'] = [dict(n, inputs=[rewrite.get(x, x) for x in n.get('inputs') or []]) for n in out['nodes'] if n['id'] not in drop]
    receipt = {'version': VERSION, 'fused': [{'node': d, 'reads': rewrite[d]} for d in drop],
               'nodes_before': len(nodes), 'nodes_after': len(out['nodes']), 'identity_preserved': sorted(preserve)}
    if validator is not None:
        try: out = validator(out)
        except (ValueError, KeyError, TypeError) as exc:
            return plan, {'version': VERSION, 'fused': [], 'rejected_fusion': receipt,
                          'fallback_reason': f'Optimized graph failed complete validation: {type(exc).__name__}: {exc}'}
    return out, receipt


# ---- SQL level ------------------------------------------------------------------------------------------------------------
def _subselects(sql):
    """[(start, end, inner)] for every parenthesised (SELECT ...), honouring quoted strings."""
    out, stack, i, n = [], [], 0, len(sql)
    quote = None
    while i < n:
        c = sql[i]
        if quote:
            if c == quote:
                if sql[i + 1:i + 2] == quote: i += 1
                else: quote = None
        elif sql[i:i+2]=='--':
            newline=sql.find('\n',i+2);i=n if newline<0 else newline;continue
        elif sql[i:i+2]=='/*':
            end=sql.find('*/',i+2);i=n if end<0 else end+2;continue
        elif c in ("'", '"', '`','['): quote = ']' if c=='[' else c
        elif c == '(':
            m = re.match(r'\(\s*select\b', sql[i:i + 20], re.I)
            stack.append((i, bool(m)))
        elif c == ')' and stack:
            start, is_select = stack.pop()
            if is_select: out.append((start, i + 1, sql[start + 1:i]))
        i += 1
    return out


def _norm(text): return text.strip()  # whitespace within a literal or quoted identifier is data


def _safe_subquery(inner, db):
    """Prove the supported local/deterministic subset; otherwise execute the planned query unchanged."""
    if db is None:return False
    try:
        from sqlglot import parse_one,exp
        from sqlglot.optimizer.qualify import qualify
        from sqlglot.optimizer.scope import traverse_scope
        tree=parse_one(inner,read='sqlite')
        if tree.find(exp.CTE):return False
        pure={'COUNT','SUM','MIN','MAX','AVG','COALESCE','NULLIF','IF','LOWER','UPPER','LENGTH','TRIM',
              'SUBSTRING','SUBSTR','ABS','ROUND','JSON_EXTRACT','CAST','TRY_CAST'}
        for fn in tree.find_all(exp.Func):
            if isinstance(fn,exp.Binary):continue
            name=fn.name.upper() if isinstance(fn,exp.Anonymous) else fn.sql_name().upper()
            if name not in pure:return False
        schema={}
        for table in tree.find_all(exp.Table):
            if table.db or table.catalog or not isinstance(table.this,exp.Identifier):return False
            name=table.name
            kind=db.execute("SELECT type FROM sqlite_temp_master WHERE name=? UNION ALL SELECT type FROM sqlite_master WHERE name=? LIMIT 1",(name,name)).fetchone()
            if not kind or kind[0]!='table':return False  # view bodies can hide volatile expressions or external scope
            quoted='"'+name.replace('"','""')+'"'
            cols=db.execute('SELECT * FROM '+quoted+' LIMIT 0').description
            schema[name]={c[0]:'UNKNOWN' for c in cols}
        qualified=qualify(tree.copy(),dialect='sqlite',schema=schema,
                          validate_qualify_columns=True,quote_identifiers=False)
        return all(not s.external_columns for s in traverse_scope(qualified))
    except Exception:
        return False


def fuse_sql(query, db=None):
    """-> (fused_query, receipt). Unchanged text and fused=[] when no repeated sub-select exists."""
    none = (query, {'version': VERSION, 'fused': []})
    if not enabled() or not isinstance(query, str) or query.lower().count('select') < 3: return none
    if '--' in query or '/*' in query:return none  # comment-bearing text is not in the qualified rewrite subset
    subs = _subselects(query)
    groups = {}
    for s, e, inner in subs:
        if len(inner.strip()) >= MIN_FUSE_CHARS: groups.setdefault(_norm(inner), []).append((s, e, inner))
    repeated = sorted((k for k, v in groups.items() if len(v) >= 2), key=len, reverse=True)
    if not repeated: return none
    taken, picks = [], []  # outermost repeated sub-selects first; occurrences inside an already-chosen span are covered by it
    for k in repeated:
        if not _safe_subquery(groups[k][0][2],db):continue
        spans = [(s, e) for s, e, _ in groups[k] if not any(a <= s and e <= b for a, b in taken)]
        if len(spans) >= 2:
            picks.append((k, groups[k][0][2].strip(), spans)); taken += spans
    if not picks: return none
    names=[];index=1
    for _ in picks:
        while re.search(r'\b_fused'+str(index)+r'\b',query,re.I):index+=1
        names.append(f'_fused{index}');index+=1
    edits = sorted(((s, e, names[i]) for i, (_, _, spans) in enumerate(picks) for s, e in spans), reverse=True)
    body = query
    for s, e, name in edits: body = body[:s] + f'(SELECT * FROM {name})' + body[e:]
    ctes = ', '.join(f'{names[i]} AS MATERIALIZED ({inner})' for i, (_, inner, _) in enumerate(picks))
    m = re.match(r'(?is)^\s*with\s+(recursive\s+)?', body)
    fused = f'{body[:m.end()]}{ctes}, {body[m.end():]}' if m else f'WITH {ctes} {body}'
    return fused, {'version': VERSION, 'fused': [{'name': names[i], 'occurrences': len(sp), 'chars': len(inner)}
                                                 for i, (_, inner, sp) in enumerate(picks)],
                   'chars_before': len(query), 'chars_after': len(fused)}
