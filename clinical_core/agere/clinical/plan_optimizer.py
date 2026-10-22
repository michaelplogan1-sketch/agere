"""Compiler optimizer that fuses repeated sub-queries (query-compiler common-subexpression elimination).

Two levels, both semantics-preserving and both receipted:
  optimize_plan(plan)  graph level: a node identical to an earlier node (same operation, parameters and inputs after rewiring) is
                       executed once; consumers of the duplicate read the earlier node. The terminal node and nodes whose rewiring
                       would give a consumer the same input twice are never fused. Runs in analysis_graph.execute after validation.
  fuse_sql(query)      SQL level: a parenthesised SELECT that appears two or more times verbatim is hoisted into one
                       `WITH _fused1 AS MATERIALIZED (...)` and every occurrence reads it. Runs in sql_query.execute on the text
                       that is executed; the query recorded in the receipt stays the planned text. A fused query that fails
                       (for example a correlated sub-select that cannot stand alone) is re-run unfused: the optimizer heals, never blocks.
AGERE_PLAN_OPTIMIZER=off disables both. Correlation is not parsed: a correlated sub-select either binds to its own table in both forms
(same result) or fails standalone (fallback), so a wrong answer cannot be produced by the rewrite.
"""
import copy
import json
import os
import re

VERSION = 'plan-optimizer/1'
MIN_FUSE_CHARS = 40  # a repeated one-token sub-select is not worth a CTE


def enabled(): return os.environ.get('AGERE_PLAN_OPTIMIZER', 'on') != 'off'


# ---- graph level ----------------------------------------------------------------------------------------------------------
def _key(node, rewrite):
    inputs = [rewrite.get(x, x) for x in node.get('inputs') or []]
    return json.dumps([node['operation'], node.get('parameters'), inputs], sort_keys=True, default=str)


def optimize_plan(plan):
    """-> (plan, receipt). The plan is returned unchanged (same object) when nothing fuses."""
    nodes = (plan or {}).get('nodes') or []
    if not enabled() or len(nodes) < 2: return plan, {'version': VERSION, 'fused': []}
    terminal = nodes[-1]['id']
    seen, rewrite, drop = {}, {}, []
    for n in nodes:
        k = _key(n, rewrite)
        if k in seen and n['id'] != terminal:
            canon = seen[k]
            consumers = [m for m in nodes if n['id'] in (m.get('inputs') or [])]
            if all(canon not in [rewrite.get(x, x) for x in m['inputs'] if x != n['id']] for m in consumers):
                rewrite[n['id']] = canon; drop.append(n['id']); continue
        seen.setdefault(k, n['id'])
    if not drop: return plan, {'version': VERSION, 'fused': []}
    out = copy.deepcopy(plan)
    out['nodes'] = [dict(n, inputs=[rewrite.get(x, x) for x in n.get('inputs') or []]) for n in out['nodes'] if n['id'] not in drop]
    return out, {'version': VERSION, 'fused': [{'node': d, 'reads': rewrite[d]} for d in drop],
                 'nodes_before': len(nodes), 'nodes_after': len(out['nodes'])}


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
        elif c in ("'", '"'): quote = c
        elif c == '(':
            m = re.match(r'\(\s*select\b', sql[i:i + 20], re.I)
            stack.append((i, bool(m)))
        elif c == ')' and stack:
            start, is_select = stack.pop()
            if is_select: out.append((start, i + 1, sql[start + 1:i]))
        i += 1
    return out


def _norm(text): return ' '.join(text.split())


def fuse_sql(query):
    """-> (fused_query, receipt). Unchanged text and fused=[] when no repeated sub-select exists."""
    none = (query, {'version': VERSION, 'fused': []})
    if not enabled() or not isinstance(query, str) or query.lower().count('select') < 3: return none
    subs = _subselects(query)
    groups = {}
    for s, e, inner in subs:
        if len(inner.strip()) >= MIN_FUSE_CHARS: groups.setdefault(_norm(inner), []).append((s, e, inner))
    repeated = sorted((k for k, v in groups.items() if len(v) >= 2), key=len, reverse=True)
    if not repeated: return none
    taken, picks = [], []  # outermost repeated sub-selects first; occurrences inside an already-chosen span are covered by it
    for k in repeated:
        spans = [(s, e) for s, e, _ in groups[k] if not any(a <= s and e <= b for a, b in taken)]
        if len(spans) >= 2:
            picks.append((k, groups[k][0][2].strip(), spans)); taken += spans
    if not picks: return none
    edits = sorted(((s, e, f'_fused{i + 1}') for i, (_, _, spans) in enumerate(picks) for s, e in spans), reverse=True)
    body = query
    for s, e, name in edits: body = body[:s] + f'(SELECT * FROM {name})' + body[e:]
    ctes = ', '.join(f'_fused{i + 1} AS MATERIALIZED ({inner})' for i, (_, inner, _) in enumerate(picks))
    m = re.match(r'(?is)^\s*with\s+(recursive\s+)?', body)
    fused = f'{body[:m.end()]}{ctes}, {body[m.end():]}' if m else f'WITH {ctes} {body}'
    return fused, {'version': VERSION, 'fused': [{'name': f'_fused{i + 1}', 'occurrences': len(sp), 'chars': len(inner)}
                                                 for i, (_, inner, sp) in enumerate(picks)],
                   'chars_before': len(query), 'chars_after': len(fused)}
