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


NATIVE_POPULATIONS = 'native-count-populations/1'


def native_population_identity():
    from pathlib import Path
    from ...contracts import digest
    return digest({'version':NATIVE_POPULATIONS,'implementation':Path(__file__).read_text()})


def native_populations(calls, roots, execute, known_ids, source):
    """Derive named patient sets from published native COUNT(DISTINCT patient_id).

    Each set binds to an actual result cell and full executed selection. UNION
    ALL and grouped outputs retain their row identity; ambiguous rows, windows,
    source drift and event COUNT(*) never become patient scopes. No model or
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
        if not re.search(r'\bcount\s*\(\s*distinct\b',query,re.I):continue
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
            if tree.find(exp.Window)or tree.find(exp.Rand):raise ValueError('Window/ranking or nondeterministic selection needs its own membership proof.')
            if any(n.args.get('distinct')is not False for n in tree.find_all(exp.Union)):
                raise ValueError('Deduplicating set operations do not identify an original branch.')
            if tree.find(exp.Intersect)or tree.find(exp.Except):raise ValueError('Unsupported set selection.')
            columns,expected=output_table(result)
            if not columns or len(set(columns))!=len(columns)or any(len(r)!=len(columns)for r in expected):raise ValueError('Complete unique output columns required.')
            actual_columns,actual=execute(query,{})
            if actual_columns!=columns or digest(actual)!=digest(expected):raise ValueError('Current native SQL output differs from the complete published result.')
            branches=[]
            def leaves(node):
                if isinstance(node,exp.Union):leaves(node.this);leaves(node.expression)
                elif isinstance(node,exp.Select):branches.append(node)
                else:raise ValueError('Unsupported native select shape.')
            leaves(tree);matches={};plans=[]
            for branch_index,original in enumerate(branches):
                if len(original.selects)!=len(columns)or original.args.get('distinct'):continue
                counts=[]
                for index,expression in enumerate(original.selects):
                    counted=expression.unalias()
                    if (isinstance(counted,exp.Count)and isinstance(counted.this,exp.Distinct)
                        and len(counted.this.expressions)==1 and isinstance(counted.this.expressions[0],exp.Column)
                        and counted.this.expressions[0].name.casefold()=='patient_id'):
                        counts.append((index,counted.this.expressions[0]))
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
                for column_index,patient in counts:
                    value=row[column_index]
                    if type(value)is not int or value<0:continue
                    selection=branch.copy();selection.set('expressions',[exp.alias_(patient.copy(),'patient_id',quoted=True)])
                    parameters={};condition=selection.args.get('where')
                    condition=condition.this.copy()if condition else None
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
                    if len(members)!=value or not set(members)<=known_ids:raise ValueError('Derived patient membership differs from its counted cell or received source.')
                    labels=[str(v)for v in row if isinstance(v,str)and v]
                    binding={'result_view':view,'row':row_index,'column':columns[column_index]}
                    proof={'source':source,'binding':binding,'result_digest':digest(result),'source_row_digest':digest(row),
                        'executed_query_digest':digest(query),'selection_query':member_query,'parameters':parameters,
                        'patient_ids_digest':digest(members),'patient_count':value,'counted_source_rows':len(member_rows),
                        'missing_patient_rows':sum(r[0]is None for r in member_rows),'group_values':group_values,
                        'current_full_output_exact':True,'version':NATIVE_POPULATIONS,'membership_policy':native_population_identity()}
                    scopes.append({'id':'POP-'+digest(proof)[:24],'label':' · '.join(labels)or view+' / '+columns[column_index],
                        'patient_count':value,'counted_source_rows':len(member_rows),'patient_ids':members,'proof':proof,
                        'clinical_eligibility_established':False,'actionable_cohort':False})
        except (sqlite3.Error,sqlglot.errors.SqlglotError,ValueError,TypeError,KeyError,AttributeError,IndexError)as exc:
            unavailable.append({'result_view':view,'reason':str(exc)})
    return {'schema':NATIVE_POPULATIONS,'membership_policy':native_population_identity(),'source':copy.deepcopy(source),'scopes':scopes,'unavailable':unavailable,
        'scope':'Exact counted patient memberships only; native event/row counts and financial amounts retain their own units. No clinical eligibility, payer entitlement or care execution is established.'}
