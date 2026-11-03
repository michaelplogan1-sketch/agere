"""Reject an unasked technical data-partition predicate in an answer plan.

These fields describe source/sampling coverage, not clinical calendar time or
eligibility. Exploratory SQL is still available to the investigator. This is a
proposal-compiler check, not a query firewall or proof of general equivalence.
"""
import re

FIELDS=frozenset({'data_segment','dataset_partition','sampling_stratum','evaluation_split'})
SOURCE_CUE=re.compile(r'\b(?:segments?|strata|stratum|partitions?|sampling|samples?|evaluation\s+split|claims\s+only|claims\s+plus\s+ehr|deep\s+chart|duplicate\s+chart)\b',re.I)
IGNORE_SOURCE=re.compile(r'\b(?:regardless|irrespective)\s+of\s+(?:the\s+)?(?:data\s+|source\s+)?(?:segment|partition|stratum)\b',re.I)


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
    claim_gap = provider_claim_problem(question, sql)
    if claim_gap: return claim_gap
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
