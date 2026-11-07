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


NATIVE_POPULATIONS = 'native-count-populations/2'


def native_population_identity():
    from pathlib import Path
    from ...contracts import digest
    return digest({'version':NATIVE_POPULATIONS,'implementation':Path(__file__).read_text()})


def _native_patient_grain(select, ctes, seen=frozenset()):
    """Prove one row per patient structurally, before treating row sums as people."""
    from sqlglot import exp
    relation=(select.args.get('from_')or exp.From()).this
    if not isinstance(relation,exp.Table):return None
    name=relation.name.casefold();alias=relation.alias_or_name
    if name in seen:return None
    if name!='patient':
        definition=ctes.get(name)
        if not isinstance(definition,exp.Select):return None
        groups=(definition.args.get('group')or exp.Group()).expressions
        projected=next((e.unalias()for e in definition.selects if e.alias_or_name.casefold()=='patient_id'),None)
        if not isinstance(projected,exp.Column)or projected.name.casefold()!='patient_id':return None
        grouped=(len(groups)==1 and groups[0]==projected)
        if not grouped:
            if groups or any(isinstance(e.unalias(),exp.AggFunc)for e in definition.selects):return None
            if _native_patient_grain(definition,ctes,seen|{name})is None:return None
    for join in select.args.get('joins')or[]:
        table=join.this
        if (not isinstance(table,exp.Table)or join.args.get('side')not in (None,'','LEFT')
            or join.args.get('kind')not in (None,'','INNER')):return None
        one=exp.select('patient_id').from_(table.copy())
        if _native_patient_grain(one,ctes,seen)is None:return None
        # Joining arbitrary one-per-patient tables can still form a cartesian
        # product. Require the actual patient equality to the anchor relation.
        on=join.args.get('on')
        def conjunctions(e):
            if isinstance(e,exp.Paren):return conjunctions(e.this)
            if isinstance(e,exp.And):return conjunctions(e.this)+conjunctions(e.expression)
            return [e]
        if on is None or not any(isinstance(e,exp.EQ)and isinstance(e.this,exp.Column)and isinstance(e.expression,exp.Column)
                and e.this.name.casefold()==e.expression.name.casefold()=='patient_id'
                and {e.this.table,e.expression.table}=={alias,table.alias_or_name}
                for e in conjunctions(on)):return None
    return exp.column('patient_id',table=alias)


def _native_count_selection(expression, branch, ctes):
    """Return the exact patient expression, selection and predicate for a count cell."""
    from sqlglot import exp
    counted=expression.unalias();selection=branch
    if isinstance(counted,exp.Subquery):
        selection=counted.this
        if (not isinstance(selection,exp.Select)or len(selection.selects)!=1
            or branch.args.get('from_')or branch.args.get('group')):return None
        counted=selection.selects[0].unalias()
    predicate=None;kind='distinct_patient_count';patient=None
    if isinstance(counted,exp.Count)and isinstance(counted.this,exp.Distinct):
        args=counted.this.expressions
        if len(args)==1 and isinstance(args[0],exp.Column)and args[0].name.casefold()=='patient_id':patient=args[0]
    else:
        patient=_native_patient_grain(selection,ctes)
        if patient is None:return None
        kind='patient_grain_row_count'
        if isinstance(counted,exp.Count)and isinstance(counted.this,exp.Star):pass
        elif isinstance(counted,exp.Sum):
            term=counted.this
            if isinstance(term,exp.Case):
                cases=term.args.get('ifs')or[];default=term.args.get('default')
                if (not cases or any(not isinstance(c.args.get('true'),exp.Literal)or c.args['true'].is_string
                        or c.args['true'].this!='1'for c in cases)
                    or not(isinstance(default,exp.Null)or default is None or
                        isinstance(default,exp.Literal)and not default.is_string and default.this=='0')):return None
                predicate=exp.or_(*[c.this.copy()for c in cases])
            elif isinstance(term,(exp.EQ,exp.NEQ,exp.GT,exp.GTE,exp.LT,exp.LTE,exp.And,exp.Or,exp.Not,exp.Between,exp.In,exp.Is)):
                predicate=term.copy()
            else:return None
            if predicate.find(exp.AggFunc)or predicate.find(exp.Window)or predicate.find(exp.Subquery):return None
            kind='patient_grain_conditional_count'
        elif isinstance(counted,(exp.Max,exp.Min))and isinstance(counted.this,exp.Column):
            # A repeated COUNT(*) OVER () is a patient denominator only when
            # its input is already proven one row per patient.
            table=(selection.args.get('from_')or exp.From()).this
            if counted.this.table and counted.this.table!=table.alias_or_name:return None
            definition=ctes.get(table.name.casefold())if isinstance(table,exp.Table)else None
            value=next((e.unalias()for e in definition.selects if e.alias_or_name==counted.this.name),None)if definition is not None else None
            if (not isinstance(value,exp.Window)or not isinstance(value.this,exp.Count)
                or not isinstance(value.this.this,exp.Star)or value.args.get('partition_by')or value.args.get('order')or value.args.get('spec')):return None
            kind='patient_grain_window_denominator'
        else:return None
    return (patient,selection,predicate,kind)if patient is not None else None


def _native_rank_checks(selection, execute, parameters, known_ids):
    """Check rank peers at the selection boundary, using SQL's peer equality.

    ROW_NUMBER ties are harmless when the complete tied group is included or
    excluded. A cutoff splitting peers cannot establish stable patient IDs.
    The rank-stage check precedes later joins/filters, so a valid ranked group
    can subsequently be narrowed by an independent condition.
    """
    from sqlglot import exp
    from ...contracts import digest
    with_=selection.args.get('with_');ctes={c.alias_or_name.casefold():c.this for c in with_.expressions}if with_ else{}
    reachable=set()
    def visit(node):
        for table in node.find_all(exp.Table):
            name=table.name.casefold()
            if name in ctes and name not in reachable:reachable.add(name);visit(ctes[name])
    plain=selection.copy();plain.set('with_',None);visit(plain)
    checks=[]
    for name in sorted(reachable):
        definition=ctes[name]
        windows=[e.unalias()for e in definition.selects if isinstance(e.unalias(),exp.Window)]if isinstance(definition,exp.Select)else[]
        ranks=[w for w in windows if isinstance(w.this,exp.RowNumber)]
        if not ranks:continue
        if len(ranks)!=1 or definition.args.get('group')or definition.args.get('distinct'):
            raise ValueError('Rank membership needs one row-preserving rank stage.')
        rank=ranks[0];order=rank.args.get('order')
        if not order or not order.expressions:raise ValueError('Unordered rank cannot establish stable membership.')
        if _native_patient_grain(definition,ctes)is None:raise ValueError('Rank input is not proven one row per patient.')
        # Find the first row selection consuming this rank. Only reachable
        # CTEs participate; unused branches cannot alter the scope proof.
        consumers=[]
        for consumer in [plain,*[ctes[n]for n in reachable if n!=name]]:
            relation=(consumer.args.get('from_')or exp.From()).this if isinstance(consumer,exp.Select)else None
            if isinstance(relation,exp.Table)and relation.name.casefold()==name:
                if consumer.args.get('group')or any(isinstance(e.unalias(),exp.AggFunc)for e in consumer.selects):continue
                stage=consumer.copy();stage.set('with_',None)
                stage.set('expressions',[exp.alias_(exp.column('patient_id',table=relation.alias_or_name),'patient_id')])
                consumers.append(stage)
        if not consumers:raise ValueError('Rank boundary has no provable row selection.')
        augmented=with_.copy();rank_cte=next(c for c in augmented.expressions if c.alias_or_name.casefold()==name)
        keys=[]
        for i,key in enumerate([*(rank.args.get('partition_by')or[]),*[o.this for o in order.expressions]]):
            alias='__native_peer_'+digest([name,i,selection.sql()])[:12]
            rank_cte.this.append('expressions',exp.alias_(key.copy(),alias,quoted=True));keys.append(alias)
        universe=exp.select(exp.column('patient_id',table=name)).from_(name);universe.set('with_',augmented.copy())
        columns,rows=execute(universe.sql(dialect='sqlite'),parameters)
        ids=[r[0]for r in rows]
        if columns!=['patient_id']or len(ids)!=len(set(ids))or any(not isinstance(p,str)for p in ids)or not set(ids)<=known_ids:
            raise ValueError('Rank stage must contain unique received patient identities.')
        for stage in consumers:
            selected='__native_selected_'+digest(stage.sql())[:12]
            if selected.casefold()in ctes:raise ValueError('Rank proof relation collides with an original CTE.')
            w=augmented.copy();w.append('expressions',exp.CTE(this=stage.copy(),alias=exp.TableAlias(this=exp.to_identifier(selected))))
            # GROUP BY uses native collation/NULL/numeric equality for peers.
            quoted=lambda s:'"'+s.replace('"','""')+'"'
            key_sql=', '.join('r.'+quoted(k)for k in keys)
            body=('SELECT COUNT(*) AS peer_rows, SUM(CASE WHEN EXISTS (SELECT 1 FROM '+quoted(selected)+
                ' AS s WHERE s.patient_id=r.patient_id) THEN 1 ELSE 0 END) AS selected_rows FROM '+quoted(name)+
                ' AS r GROUP BY '+key_sql)
            query=w.sql(dialect='sqlite')+' '+body
            cols,peers=execute(query,parameters)
            if cols!=['peer_rows','selected_rows']or any(0<picked<total for total,picked in peers):
                raise ValueError('Rank cutoff splits tied ordering values; the counted patients are ambiguous.')
            checks.append({'rank_stage':name,'stage_query_digest':digest(stage.sql(dialect='sqlite')),
                'peer_groups':len(peers),'peer_rows':len(ids),'split_peer_groups':0,'query_digest':digest(query)})
    return checks


def native_populations(calls, roots, execute, known_ids, source):
    """Derive named patient sets from exact published native patient counts.

    Each set binds to an actual result cell and full executed selection. UNION
    ALL and grouped outputs retain their row identity. Conditional row counts
    require patient grain; ranked selections require an unsplit peer boundary.
    Source drift and event COUNT(*) never become patient scopes. No model or
    original analytical engine is run. ``execute`` is a guarded read-only SQL
    callback over the unchanged source and retained native inputs.
    """
    import copy,sqlite3
    import sqlglot
    from sqlglot import exp
    from ...contracts import digest
    from .backstop_agent import output_table,native_result_dependencies,NATIVE_SQL_CONTRACT
    from .result_assembly import incomplete_reason
    required=native_result_dependencies(calls,roots);scopes=[];unavailable=[]
    if required is None:return {'schema':NATIVE_POPULATIONS,'source':source,'scopes':[],
        'unavailable':[{'reason':'Original bound calculation dependencies are unresolved.'}]}
    for call in calls:
        result=call.get('result')or{};view=result.get('result_view');query=result.get('declared_executed_query')or result.get('executed_query')
        if view not in required or call.get('name')!='query_sql'or not isinstance(query,str):continue
        if not re.search(r'\b(?:count|sum|min|max)\s*\(',query,re.I):continue
        try:
            tree=sqlglot.parse_one(query,read='sqlite')
            # An event/provider identifier cannot become patient identity by
            # acquiring the patient_id alias, even when values happen to match
            # received patient IDs. Check the declared native input lineage too.
            for upstream in calls:
                dependency=(upstream.get('result')or{}).get('result_view')
                if dependency not in (native_result_dependencies(calls,[view])or set())or upstream.get('name')!='query_sql':continue
                declared=(upstream.get('input')or{}).get('sql')
                identity_tree=sqlglot.parse_one(declared,read='sqlite')if isinstance(declared,str)else tree
                for alias in identity_tree.find_all(exp.Alias):
                    if alias.alias.casefold()=='patient_id'and not(isinstance(alias.this,exp.Column)and alias.this.name.casefold()=='patient_id'):
                        raise ValueError('A renamed or computed non-patient identifier cannot establish patient membership.')
                for cte in identity_tree.find_all(exp.CTE):
                    if any(c.name.casefold()=='patient_id'for c in (cte.args.get('alias')or exp.TableAlias()).args.get('columns')or[]):
                        raise ValueError('Explicit CTE patient identity renaming requires a separate lineage proof.')
            contracts=result.get('source_contracts')or{}
            if (result.get('native_sql_contract')!=NATIVE_SQL_CONTRACT or result.get('error')or incomplete_reason(result)
                or contracts.get('source_digest')!=source['source_digest']or contracts.get('as_of')!=source['as_of']):
                raise ValueError('Complete native SQL and matching source/date contracts required.')
            for function in tree.find_all(exp.Func):
                name=(function.name if isinstance(function,exp.Anonymous)else function.sql_name()).casefold()
                clock=name in ('date','datetime','time','julianday','strftime','unixepoch')
                if (name in ('rand','random','randomblob','uuid','current_date','current_time','current_timestamp')
                    or clock and (not list(function.iter_expressions())or any(
                        isinstance(v,exp.Literal)and v.is_string and v.this.casefold()in ('now','localtime','utc','subsec','subsecond')
                        for v in function.find_all(exp.Literal)))):
                    raise ValueError('Nondeterministic selection cannot establish patient membership.')
            for window in tree.find_all(exp.Window):
                if not isinstance(window.this,(exp.RowNumber,exp.Count,exp.Sum,exp.Min,exp.Max))or not window.find_ancestor(exp.CTE):
                    raise ValueError('Window/ranking shape needs its own membership proof.')
            if any(n.args.get('distinct')is not False for n in tree.find_all(exp.Union)):
                raise ValueError('Deduplicating set operations do not identify an original branch.')
            if tree.find(exp.Intersect)or tree.find(exp.Except):raise ValueError('Unsupported set selection.')
            columns,expected=output_table(result)
            if not columns or len(set(columns))!=len(columns)or any(len(r)!=len(columns)for r in expected):raise ValueError('Complete unique output columns required.')
            branches=[]
            ctes={c.alias_or_name.casefold():c.this for c in (tree.args.get('with_')or exp.With()).expressions}
            def leaves(node):
                if isinstance(node,exp.Union):leaves(node.this);leaves(node.expression)
                elif isinstance(node,exp.Select):branches.append(node)
                else:raise ValueError('Unsupported native select shape.')
            leaves(tree);matches={};plans=[]
            if not any(_native_count_selection(expression,branch,ctes)
                    for branch in branches for expression in branch.selects):
                # Money and event counts cannot define a patient population.
                # Do not repeat their source calculations just to discover
                # that this existing patient-count proof does not apply.
                unavailable.append({'result_view':view,'reason':'No qualified patient-count expression; financial/event quantities retain their original domain.'})
                continue
            actual_columns,actual=execute(query,{})
            if actual_columns!=columns or digest(actual)!=digest(expected):raise ValueError('Current native SQL output differs from the complete published result.')
            for branch_index,original in enumerate(branches):
                if len(original.selects)!=len(columns)or original.args.get('distinct'):continue
                counts=[]
                for index,expression in enumerate(original.selects):
                    found=_native_count_selection(expression,original,ctes)
                    if found:counts.append((index,*found))
                branch=original.copy()
                branch.set('expressions',[exp.alias_(e.unalias().copy(),c,quoted=True)for e,c in zip(branch.selects,columns)])
                if tree.args.get('with_')and not branch.args.get('with_'):branch.set('with_',tree.args['with_'].copy())
                groups=[]
                for group in (branch.args.get('group')or exp.Group()).expressions:
                    if isinstance(group,exp.Literal)and not group.is_string and group.this.isdigit():
                        index=int(group.this)-1
                        if not 0<=index<len(columns):raise ValueError('Invalid group ordinal.')
                        group=branch.selects[index].unalias()
                    elif isinstance(group,exp.Column)and not group.table and group.name in columns:
                        group=branch.selects[columns.index(group.name)].unalias()
                    groups.append(group.copy())
                tagged=branch.copy();names=[]
                for index,group in enumerate(groups):
                    name='__native_group_'+str(index)
                    while name in columns:name+='x'
                    names.append(name);tagged.append('expressions',exp.alias_(group.copy(),name,quoted=True))
                tagged_columns,tagged_rows=execute(tagged.sql(dialect='sqlite'),{})
                if tagged_columns!=columns+names:raise ValueError('Selected group keys could not be bound exactly.')
                for row in tagged_rows:
                    key=digest(row[:len(columns)]);matches.setdefault(key,[]).append((branch_index,row[len(columns):]))
                plans.append((branch_index,branch,counts,groups))
            for row_index,row in enumerate(expected):
                candidates=matches.get(digest(row),[])
                if len(candidates)!=1:continue
                branch_index,group_values=candidates[0]
                _,branch,counts,groups=next(p for p in plans if p[0]==branch_index)
                for column_index,patient,selector,predicate,count_kind in counts:
                    value=row[column_index]
                    if type(value)is not int or value<0:continue
                    selection=selector.copy();selection.set('expressions',[exp.alias_(patient.copy(),'patient_id',quoted=True)])
                    if tree.args.get('with_')and not selection.args.get('with_'):selection.set('with_',tree.args['with_'].copy())
                    parameters={};condition=selection.args.get('where')
                    condition=condition.this.copy()if condition else None
                    if predicate is not None:condition=exp.and_(condition,predicate.copy())if condition is not None else predicate.copy()
                    for index,(group,group_value)in enumerate(zip(groups,group_values)):
                        name='__native_group_'+str(index);parameters[name]=group_value
                        predicate=exp.NullSafeEQ(this=group.copy(),expression=exp.Placeholder(this=name))
                        condition=exp.and_(condition,predicate)if condition is not None else predicate
                    if condition is not None:selection.set('where',exp.Where(this=condition))
                    # Published GROUP/HAVING/ORDER/LIMIT qualification was
                    # checked above. The exact selected group is now a WHERE
                    # predicate; dropping aggregation does not widen it.
                    for key in ('group','having','order','limit','offset','distinct'):selection.set(key,None)
                    member_query=selection.sql(dialect='sqlite');member_columns,member_rows=execute(member_query,parameters)
                    if member_columns!=['patient_id']:raise ValueError('Exact patient identity projection required.')
                    ids=[r[0]for r in member_rows if r[0]is not None]
                    if any(not isinstance(pid,str)or not pid for pid in ids):raise ValueError('Non-patient counted identities cannot select a patient scope.')
                    members=sorted(set(ids))
                    if count_kind!='distinct_patient_count'and (len(ids)!=len(member_rows)or len(ids)!=len(members)):
                        raise ValueError('A patient-grain count cannot count missing or duplicate patient rows.')
                    if len(members)!=value or not set(members)<=known_ids:raise ValueError('Derived patient membership differs from its counted cell or received source.')
                    rank_checks=_native_rank_checks(selection,execute,parameters,known_ids)
                    labels=[str(v)for v in row if isinstance(v,str)and v]
                    binding={'result_view':view,'row':row_index,'column':columns[column_index]}
                    proof={'source':source,'binding':binding,'result_digest':digest(result),'source_row_digest':digest(row),
                        'executed_query_digest':digest(query),'selection_query':member_query,'parameters':parameters,
                        'patient_ids_digest':digest(members),'patient_count':value,'counted_source_rows':len(member_rows),
                        'missing_patient_rows':sum(r[0]is None for r in member_rows),'group_values':group_values,
                        'current_full_output_exact':True,'count_kind':count_kind,'rank_checks':rank_checks,
                        'version':NATIVE_POPULATIONS,'membership_policy':native_population_identity()}
                    scopes.append({'id':'POP-'+digest(proof)[:24],'label':' · '.join(labels)or columns[column_index].replace('_',' '),
                        'patient_count':value,'counted_source_rows':len(member_rows),'patient_ids':members,'proof':proof,
                        'clinical_eligibility_established':False,'actionable_cohort':False})
        except (sqlite3.Error,sqlglot.errors.SqlglotError,ValueError,TypeError,KeyError,AttributeError,IndexError)as exc:
            unavailable.append({'result_view':view,'reason':str(exc)})
    return {'schema':NATIVE_POPULATIONS,'membership_policy':native_population_identity(),'source':copy.deepcopy(source),'scopes':scopes,'unavailable':unavailable,
        'scope':'Exact counted patient memberships only; native event/row counts and financial amounts retain their own units. No clinical eligibility, payer entitlement or care execution is established.'}
