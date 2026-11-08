"""Reject an unasked technical data-partition predicate in an answer plan.

These fields describe source/sampling coverage, not clinical calendar time or
eligibility. Exploratory SQL is still available to the investigator. This is a
proposal-compiler check, not a query firewall or proof of general equivalence.
"""
import re

FIELDS=frozenset({'data_segment','dataset_partition','sampling_stratum','evaluation_split'})
SOURCE_CUE=re.compile(r'\b(?:segments?|strata|stratum|partitions?|sampling|samples?|evaluation\s+split|claims\s+only|claims\s+plus\s+ehr|deep\s+chart|duplicate\s+chart)\b',re.I)
IGNORE_SOURCE=re.compile(r'\b(?:regardless|irrespective)\s+of\s+(?:the\s+)?(?:data\s+|source\s+)?(?:segment|partition|stratum)\b',re.I)

# An explicit event-time requirement, not a disease or question handler. The
# captured clause is retained as its original request provenance. Unrecognized
# wording stays with ordinary semantic review; absence here proves nothing.
POINT_EVENT=re.compile(r'\b(?:at|as\s+of|at\s+the\s+time\s+of|on\s+the\s+date\s+of)\s+(?:each|every)\s+(?:recorded\s+)?(?:event|admission|discharge|visit|encounter|appointment|transaction|payment|invoice|delivery|service)\b',re.I)
TEMPORAL_ATTRIBUTE=re.compile(r'\b(?:eligib\w*|coverage|enroll\w*|membership|payer|attribut\w*)\b',re.I)
MONTH_DIMENSIONS=frozenset({'payer_id','product','line_of_business','plan_id','attribution_practice_id','attribution_npi'})


def financial_population_problem(question,sql,calls=(),*,selected_ids=None,inherited_ids=None,available_ids=None):
    """A care-liveness view cannot silently shrink recorded financial scope.

    This checks the known living_patient source, including its retained-view
    lineage. It does not prove general SQL/request equivalence. An exact saved
    cohort or complete source population remains valid regardless of its ages
    or vital statuses; explicit requests for living subjects remain available.
    """
    if not isinstance(sql,str):return None
    selected=set(selected_ids)if selected_ids is not None else None
    if selected is not None and any(scope is not None and selected==set(scope)
                                    for scope in (inherited_ids,available_ids)):return None
    text=question or''
    subjects=r'(?:patients?|members?|people|subjects?|beneficiaries)'
    explicit=re.search(r'\b(?:living|alive|surviving|non[- ]deceased)\s+'+subjects+r'\b|\b'+subjects+r'\s+(?:who\s+are\s+|who\s+were\s+|are\s+)?(?:still\s+)?(?:alive|living)\b',text,re.I)
    wider=re.search(r'\b(?:not\s+(?:only\s+|just\s+)?living|living\s+and\s+deceased|including\s+(?:the\s+)?deceased|regardless\s+of\s+(?:death|vital|living))\b',text,re.I)
    if explicit and not wider:return None
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope
    by={c['result']['result_view']:c for c in calls
        if isinstance(c.get('result'),dict)and c['result'].get('result_view')}
    seen=set()
    def uses_living(query):
        if not isinstance(query,str)or query in seen:return False
        seen.add(query)
        try:tree=sqlglot.parse_one(query,read='sqlite')
        except (sqlglot.errors.ParseError,sqlglot.errors.TokenError):return False
        if tree is None:return False
        for scope in traverse_scope(tree):
            for _,source in scope.selected_sources.values():
                if not isinstance(source,exp.Table):continue  # CTE shadowing is resolved by sqlglot.
                name=source.name.lower()
                if name=='living_patient':return True
                call=by.get(name)
                if call and uses_living((call.get('input')or{}).get('sql')or
                                        (call.get('input')or{}).get('patient_query_sql')):return True
        return False
    if not uses_living(sql):return None
    return ('The financial population uses living_patient without a requested living-only restriction. '
            'Recorded balances can remain outside a living care cohort. Preserve the original or exact inherited '
            'financial population; do not silently exclude those source subjects or infer hardship from amounts.')


def point_event_grain_problem(question,sql):
    """Reject a monthly attribute used as event-date membership without proof.

    This checks the known member_month source contract, not arbitrary SQL
    equivalence. Exact enrollment interval joins and ordinary monthly exposure
    remain available. Exploratory reads are checked only when they become a
    final answer dependency, as for the other source guards.
    """
    wording=question or''
    # Keep the requirement attached to its attribute clause. A temperature
    # measured at each visit does not change an unrelated monthly payer report.
    requested=next((m for m in POINT_EVENT.finditer(wording)
        if TEMPORAL_ATTRIBUTE.search(re.split(r'[.;:!?\n]',wording[:m.start()])[-1])),None)
    if not requested or not isinstance(sql,str)or 'member_month'not in sql.lower():return None
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope,traverse_scope
    try:tree=sqlglot.parse_one(sql,read='sqlite')
    except (sqlglot.errors.ParseError,sqlglot.errors.TokenError):return None
    if tree is None:return None
    scopes=list(traverse_scope(tree));by_select={id(s.expression):s for s in scopes}
    def origin(scope,column,seen=frozenset()):
        if scope is None:return set()
        key=(id(scope),column.table.lower(),column.name.lower())
        if key in seen:return set()
        seen=seen|{key};sources={a.lower():s for a,(_,s)in scope.selected_sources.items()}
        candidates=[sources[column.table.lower()]]if column.table.lower()in sources else list(sources.values())if not column.table else[]
        if not candidates and scope.parent:return origin(scope.parent,column,seen)
        found=set()
        for source in candidates:
            if isinstance(source,exp.Table):found.add((source.name.lower(),column.name.lower()));continue
            if not isinstance(source,Scope):continue
            projections=list(source.expression.selects);names=source.outer_columns or[n.alias_or_name for n in projections]
            indices=[i for i,n in enumerate(names)if n.lower()==column.name.lower()]
            for i in indices:
                for c in projections[i].find_all(exp.Column):found.update(origin(source,c,seen))
            if not indices and any(isinstance(p,exp.Star)or isinstance(p,exp.Column)and p.is_star for p in projections):
                found.update(origin(source,exp.column(column.name),seen))
        return found
    def bare(node):
        while isinstance(node,exp.Paren):node=node.this
        return node
    def atoms(node):
        node=bare(node)
        if isinstance(node,(exp.Where,exp.And)):
            result=atoms(node.this)
            if isinstance(node,exp.And):result+=atoms(node.expression)
            return result
        return [node]if node is not None else[]
    def same(a,b):return a is not None and b is not None and a.sql(dialect='sqlite').lower()==b.sql(dialect='sqlite').lower()
    for scope in scopes:
        select=scope.expression
        if not isinstance(select,exp.Select):continue
        monthly={a.lower()for a,(_,s)in scope.selected_sources.items()if isinstance(s,exp.Table)and s.name.lower()=='member_month'}
        joins=select.args.get('joins')or[]
        mandatory=atoms(select.args.get('where'))+[a for j in joins for a in atoms(j.args.get('on'))]
        for equality in select.find_all(exp.EQ):
            if equality.find_ancestor(exp.Select)is not select:continue
            for period,value in[(equality.this,equality.expression),(equality.expression,equality.this)]:
                if not isinstance(period,exp.Column)or('member_month','month')not in origin(scope,period):continue
                trunc=bare(value);event=None
                if isinstance(trunc,exp.Substring)and isinstance(trunc.args.get('start'),exp.Literal)and trunc.args['start'].this=='1'and isinstance(trunc.args.get('length'),exp.Literal)and trunc.args['length'].this=='7':
                    columns=list(trunc.this.find_all(exp.Column));event=columns[0]if len(columns)==1 else None
                elif isinstance(trunc,exp.TimeToStr)and isinstance(trunc.args.get('format'),exp.Literal)and trunc.args['format'].this=='%Y-%m':
                    columns=list(trunc.this.find_all(exp.Column));event=columns[0]if len(columns)==1 else None
                if event is None or('member_month','month')in origin(scope,event):continue
                used=set()
                for expression in select.selects:
                    for column in expression.find_all(exp.Column):
                        used.update(field for relation,field in origin(scope,column)if relation=='member_month'and field in MONTH_DIMENSIONS)
                if not used:continue  # standalone exposure/count probes do not establish an attribute claim
                # A monthly label can be corroborated by the SAME subject's
                # exact interval, with every used attribute equated to it. An
                # unrelated span query, OR branch or decoy date cannot waive it.
                proven=False
                for alias,(_,source)in scope.selected_sources.items():
                    if not isinstance(source,exp.Table)or source.name.lower()!='enrollment_span':continue
                    interval_join=next((j for j in joins if isinstance(j.this,exp.Table)and j.this.alias_or_name.lower()==alias.lower()),None)
                    if(interval_join is not None and interval_join.side.upper()in('LEFT','RIGHT','FULL')
                        or any(j.side.upper()in('RIGHT','FULL')for j in joins)):continue
                    start=exp.column('coverage_start',table=alias);end=exp.column('coverage_end',table=alias)
                    lower=any(isinstance(a,exp.LTE)and same(a.this,start)and same(a.expression,event)or isinstance(a,exp.GTE)and same(a.this,event)and same(a.expression,start)for a in mandatory)
                    def upper(a):
                        a=bare(a)
                        def end_after(n):return isinstance(n,exp.GTE)and same(n.this,end)and same(n.expression,event)or isinstance(n,exp.LTE)and same(n.this,event)and same(n.expression,end)
                        def open_end(n):return isinstance(n,exp.Is)and same(n.this,end)and isinstance(n.expression,exp.Null)
                        return isinstance(a,exp.Or)and((end_after(bare(a.this))and open_end(bare(a.expression)))or(open_end(bare(a.this))and end_after(bare(a.expression))))
                    upper_bound=any(upper(a)for a in mandatory)
                    identity=any(isinstance(a,exp.EQ)and((same(a.this,exp.column('patient_id',table=alias))and same(a.expression,exp.column('patient_id',table=event.table)))or(same(a.expression,exp.column('patient_id',table=alias))and same(a.this,exp.column('patient_id',table=event.table))))for a in mandatory)
                    matched=all(any(isinstance(a,exp.EQ)and any((same(a.this,exp.column(field,table=m))and same(a.expression,exp.column(field,table=alias)))or(same(a.expression,exp.column(field,table=m))and same(a.this,exp.column(field,table=alias)))for m in monthly)for a in mandatory)for field in used)
                    if lower and upper_bound and identity and matched:proven=True;break
                if not proven:
                    return("The original requirement '"+requested.group()+"' binds attributes at each event date. member_month means coverage on ANY day of the calendar month and its selected monthly attributes; a month-truncated join cannot establish that date's eligibility or attribution. Use same-subject enrollment_span validity (coverage_start <= event date AND (coverage_end IS NULL OR coverage_end >= event date)), retain unknown/overlapping assignments and the full event cohort, or leave this requirement unresolved. Monthly exposure remains a different valid measure.")
    return None


def provider_claim_problem(question, sql):
    """Keep an explicit provider-claim reading out of the merged claim feed.

    This checks the received source restriction, not general SQL meaning. A
    source predicate must constrain every read, before aggregation. Literals,
    comments, an unrelated CTE or one side of an OR are not source authority.
    """
    from .claim_count_policy import PROVIDER, PAYER, ALL
    if not PROVIDER.search(question) or PAYER.search(question) or ALL.search(question):
        return None
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope, traverse_scope
    try:
        tree = sqlglot.parse_one(sql, read='sqlite')
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
        return None  # The SQL compiler reports malformed statements.
    if tree is None: return None
    scopes = list(traverse_scope(tree))
    consumers = {}
    for scope in scopes:
        for alias, (_, source) in scope.selected_sources.items():
            if isinstance(source, Scope):
                consumers.setdefault(id(source), []).append((scope, alias))

    def column_matches(node, scope, alias, name):
        return (isinstance(node, exp.Column) and node.name.lower() == name.lower()
                and (node.table.lower() == alias.lower()
                     or not node.table and len(scope.selected_sources) == 1))

    def constrains(node, scope, alias, name):
        if isinstance(node, (exp.Where, exp.Paren)):
            return constrains(node.this, scope, alias, name)
        if isinstance(node, exp.And):
            return constrains(node.this, scope, alias, name) or constrains(node.expression, scope, alias, name)
        def provider(value):
            return isinstance(value, exp.Literal) and value.is_string and value.this == 'medical_claims'
        if isinstance(node, exp.EQ):
            return ((column_matches(node.this, scope, alias, name) and provider(node.expression))
                    or (provider(node.this) and column_matches(node.expression, scope, alias, name)))
        return (isinstance(node, exp.In) and not node.args.get('query') and len(node.expressions) == 1
                and column_matches(node.this, scope, alias, name) and provider(node.expressions[0]))

    def confined(scope, alias, name, visited):
        key = (id(scope), alias, name)
        if key in visited: return False
        visited = visited | {key}
        query = scope.expression
        if not isinstance(query, exp.Select): return False
        if constrains(query.args.get('where'), scope, alias, name): return True
        # A simple projection may carry its source field to an outer WHERE.
        # A filter after an aggregate cannot repair the aggregate's input.
        if any(query.args.get(k) for k in ('group', 'having', 'distinct', 'limit', 'offset')):
            return False
        if any(node.find(exp.AggFunc, exp.Window) for node in query.selects): return False
        projected = []
        for i, node in enumerate(query.selects):
            value = node.this if isinstance(node, exp.Alias) else node
            if column_matches(value, scope, alias, name):
                projected.append(scope.outer_columns[i] if scope.outer_columns else node.alias_or_name)
            elif isinstance(value, exp.Star) and len(scope.selected_sources) == 1:
                projected.append(name)
            elif isinstance(value, exp.Column) and value.is_star and value.table.lower() == alias.lower():
                projected.append(name)
        readers = consumers.get(id(scope), [])
        return bool(readers) and any(all(confined(parent, binding, output, visited)
                                        for parent, binding in readers) for output in projected)

    for scope in scopes:
        for alias, (_, source) in scope.selected_sources.items():
            if not isinstance(source, exp.Table): continue
            if source.name.lower() == 'payer_claim' or (source.name.lower() == 'claim'
                    and not confined(scope, alias, 'claim_source', set())):
                return ("The question explicitly requests provider claims. The merged claim relation also contains payer-feed claims. "
                        "Restrict each claim read to claim_source = 'medical_claims' before aggregation; a payer or merged total "
                        "cannot substitute for this source. If that source cannot be established, leave the requirement unresolved.")
    return None


def problem(question,sql):
    temporal_gap=point_event_grain_problem(question,sql)
    if temporal_gap:return temporal_gap
    claim_gap = provider_claim_problem(question, sql)
    if claim_gap: return claim_gap
    reversal_gap = pharmacy_reversal_problem(question, sql)
    if reversal_gap:return reversal_gap
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope,traverse_scope
    if not any(name in sql.lower()for name in FIELDS):return None
    wording=re.sub(r'[_-]+',' ',question)
    if SOURCE_CUE.search(wording)and not IGNORE_SOURCE.search(wording):return None
    try:tree=sqlglot.parse_one(sql,read='sqlite')
    except (sqlglot.errors.ParseError,sqlglot.errors.TokenError):return None
    if tree is None:return None
    scopes=list(traverse_scope(tree));by_select={id(s.expression):s for s in scopes};visited=set()
    def scope_of(column,fallback):return by_select.get(id(column.find_ancestor(exp.Select)),fallback)
    def from_source(source,name):
        if isinstance(source,exp.Table):return name.lower()in FIELDS
        if not isinstance(source,Scope):return False
        selected=list(source.expression.selects);names=source.outer_columns or [x.alias_or_name for x in selected]
        index=next((i for i,n in enumerate(names)if n.lower()==name.lower()),None)
        if index is not None:
            return any(resolve(scope_of(c,source),c.name,c.table)for c in selected[index].find_all(exp.Column))
        if any(isinstance(x,exp.Star)or isinstance(x,exp.Column)and x.is_star for x in selected):
            return any(from_source(s,name)for _,s in source.selected_sources.values())
        return False
    def resolve(scope,name,qualifier):
        if scope is None:return name.lower()in FIELDS
        key=(id(scope),name.lower(),qualifier.lower())
        if key in visited:return False
        visited.add(key)
        sources={alias.lower():src for alias,(_,src)in scope.selected_sources.items()}
        if qualifier:
            if qualifier.lower()in sources:return from_source(sources[qualifier.lower()],name)
            return resolve(scope.parent,name,qualifier)if scope.parent else False
        return any(from_source(s,name)for s in sources.values())
    def owner_exclusion(predicate,column):
        from .decision_context import DUPLICATE_SEGMENT,exclude_duplicate_charts
        if column.name.lower()!='data_segment'or not exclude_duplicate_charts():return False
        def exact_duplicate(v):return isinstance(v,exp.Literal)and v.is_string and v.this==DUPLICATE_SEGMENT
        if isinstance(predicate,(exp.EQ,exp.NEQ)):
            if not ((isinstance(predicate.this,exp.Column)and exact_duplicate(predicate.expression))or(isinstance(predicate.expression,exp.Column)and exact_duplicate(predicate.this))):return False
        elif isinstance(predicate,exp.In):
            if not isinstance(predicate.this,exp.Column)or predicate.args.get('query')or len(predicate.expressions)!=1 or not exact_duplicate(predicate.expressions[0]):return False
        else:return False
        negative=isinstance(predicate,exp.NEQ)
        node=predicate.parent
        while node is not None:
            if isinstance(node,exp.Not):negative=not negative
            node=node.parent
        return negative
    comparisons=(exp.EQ,exp.NEQ,exp.GT,exp.GTE,exp.LT,exp.LTE,exp.In,exp.Between,exp.Like,exp.ILike,exp.Is)
    for predicate in tree.walk():
        if not isinstance(predicate,comparisons):continue
        for col in predicate.find_all(exp.Column):
            if col.find_ancestor(*comparisons)is not predicate:continue
            visited.clear()
            if resolve(scope_of(col,None),col.name,col.table) and not owner_exclusion(predicate,col):
                return ('The plan restricts a technical source/sampling partition that the question did not request. '
                    'A source data segment is not a calendar year or clinical cohort. Remove the unasked partition predicate; '
                    'apply dates and eligibility to their documented fields. Requested source-partition analyses remain supported.')
    return None


def pharmacy_reversal_problem(question,sql):
    """A broad event collision is not a received claim-reversal relationship.

    For an unreversed-fill request, exclusion against reversal rows must use
    the normalized record links. The source adapter already resolves explicit
    links/prescription chains into counts_as_fill. Inspecting potential matches
    remains available; this checks final analytical dependencies, not all SQL.
    """
    if not re.search(r'\bunreversed\b|\bnot\s+(?:been\s+)?reversed\b|\breversal\s+handling\b|'
                     r'\b(?:exclud\w*|omit\w*|remov\w*)\s+(?:(?:all|any|the|pharmacy|claim)\s+)*reversals?\b',question,re.I):return None
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope,traverse_scope
    try:
        tree=sqlglot.parse_one(sql,read='sqlite')
        scopes=list(traverse_scope(tree))
    except (sqlglot.errors.SqlglotError,ValueError,TypeError):return None
    if not scopes:return None
    by_select={id(s.expression):s for s in scopes}
    reachable={};pending=[scopes[-1]]
    while pending:
        scope=pending.pop()
        if id(scope)in reachable:continue
        reachable[id(scope)]=scope
        pending.extend(source for _,source in scope.selected_sources.values()if isinstance(source,Scope))
        pending.extend(scope.subquery_scopes);pending.extend(scope.set_operation_scopes)

    def origin(scope,column,seen=frozenset()):
        if not isinstance(column,exp.Column)or column.is_star:return None
        key=(id(scope),column.table.casefold(),column.name.casefold())
        if key in seen:return None
        sources={a.casefold():s for a,(_,s)in scope.selected_sources.items()}
        alias=column.table.casefold()
        if alias and alias not in sources:
            return origin(scope.parent,column,seen|{key})if scope.parent else None
        if not alias:
            if len(sources)!=1:return None
            alias=next(iter(sources))
        source=sources[alias]
        if isinstance(source,exp.Table):return (id(scope),alias,source.name.casefold(),column.name.casefold())
        if not isinstance(source,Scope):return None
        selected=list(source.expression.selects)
        names=source.outer_columns or [p.alias_or_name for p in selected]
        for name,value in zip(names,selected):
            if name.casefold()==column.name.casefold():
                value=value.this if isinstance(value,exp.Alias)else value
                return origin(source,value,seen|{key})
        stars=[p for p in selected if isinstance(p,exp.Star)or isinstance(p,exp.Column)and p.is_star]
        if len(stars)==1:
            return origin(source,exp.column(column.name,table=stars[0].table if isinstance(stars[0],exp.Column)else None),seen|{key})
        return None

    def markers(scope,seen=frozenset()):
        if id(scope)in seen:return set()
        found=set()
        for predicate in scope.expression.find_all(exp.In):
            if predicate.find_ancestor(exp.Select)is not scope.expression:continue
            src=origin(scope,predicate.this)
            if src and src[2]=='payer_pharmacy_claim'and any(
                isinstance(value,exp.Literal)and value.is_string and (
                    src[3]=='claim_status'and value.this in ('reversed','recouped')or
                    src[3]=='transaction_code'and value.this=='B2')for value in predicate.expressions):
                found.add(src[:3])
        for predicate in scope.expression.find_all(exp.EQ):
            if predicate.find_ancestor(exp.Select)is not scope.expression:continue
            for col,value in ((predicate.this,predicate.expression),(predicate.expression,predicate.this)):
                src=origin(scope,col)
                if src and src[2]=='payer_pharmacy_claim'and isinstance(value,exp.Literal)and value.is_string and (
                    src[3]=='claim_status'and value.this in ('reversed','recouped')or src[3]=='transaction_code'and value.this=='B2'):
                    found.add(src[:3])
        for _,source in scope.selected_sources.values():
            if isinstance(source,Scope):found.update(markers(source,seen|{id(scope)}))
        return found

    def record_link(left,right,reverse):
        if not left or not right or left[:3]==right[:3]or left[2]!='payer_pharmacy_claim'or right[2]!='payer_pharmacy_claim':return False
        return ((left[:3]==reverse and left[3]=='reversal_of_record_id'and right[3]=='record_id')or
                (right[:3]==reverse and right[3]=='reversal_of_record_id'and left[3]=='record_id')or
                (left[:3]==reverse and left[3]=='record_id'and right[3]=='reversed_by_record_id')or
                (right[:3]==reverse and right[3]=='record_id'and left[3]=='reversed_by_record_id'))

    def linked(condition,scope,reverse):
        if isinstance(condition,(exp.Where,exp.Paren)):return linked(condition.this,scope,reverse)
        if isinstance(condition,exp.And):return linked(condition.this,scope,reverse)or linked(condition.expression,scope,reverse)
        if isinstance(condition,exp.Or):return linked(condition.this,scope,reverse)and linked(condition.expression,scope,reverse)
        return isinstance(condition,exp.EQ)and record_link(origin(scope,condition.this),origin(scope,condition.expression),reverse)

    issue=('The unreversed pharmacy-fill calculation excludes records using an unproven reversal relationship. '
           'Same patient, drug/NDC or fill date does not identify the claim being reversed. Use the received '
           'counts_as_fill = 1 contract, which already applies the registered reversal handling, or bind '
           'reversal.reversal_of_record_id = paid.record_id / paid.reversed_by_record_id = reversal.record_id. '
           'Do not add a broad-match reversal exclusion; recompute the affected totals with claim identity preserved.')
    for scope in reachable.values():
        for negative in scope.expression.find_all(exp.Not):
            if negative.find_ancestor(exp.Select)is not scope.expression:continue
            value=negative.this
            if isinstance(value,exp.Exists):
                inner=by_select.get(id(value.this))
                if inner and any(not linked(inner.expression.args.get('where'),inner,r)for r in markers(inner)):return issue
            if isinstance(value,exp.In)and value.args.get('query'):
                query=value.args['query'];query=query.this if isinstance(query,exp.Subquery)else query
                inner=by_select.get(id(query))
                if inner and len(query.selects)==1:
                    selected=query.selects[0];selected=selected.this if isinstance(selected,exp.Alias)else selected
                    if any(not record_link(origin(scope,value.this),origin(inner,selected),r)for r in markers(inner)):return issue
        for join in scope.expression.args.get('joins')or[]:
            if join.side!='LEFT':continue
            alias=join.this.alias_or_name.casefold()
            reverse=markers(scope)
            where=scope.expression.args.get('where')
            if where is None:continue
            for missing in where.find_all(exp.Is):
                col=missing.this
                if not isinstance(col,exp.Column)or col.table.casefold()!=alias or not isinstance(missing.expression,exp.Null):continue
                src=origin(scope,col)
                if src and src[:3]in reverse and not linked(join.args.get('on'),scope,src[:3]):return issue
    return None
