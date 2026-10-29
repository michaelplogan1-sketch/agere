"""Reject an unasked technical data-partition predicate in an answer plan.

These fields describe source/sampling coverage, not clinical calendar time or
eligibility. Exploratory SQL is still available to the investigator. This is a
proposal-compiler check, not a query firewall or proof of general equivalence.
"""
import re

FIELDS=frozenset({'data_segment','dataset_partition','sampling_stratum','evaluation_split'})
SOURCE_CUE=re.compile(r'\b(?:segments?|strata|stratum|partitions?|sampling|samples?|evaluation\s+split|claims\s+only|claims\s+plus\s+ehr|deep\s+chart|duplicate\s+chart)\b',re.I)
IGNORE_SOURCE=re.compile(r'\b(?:regardless|irrespective)\s+of\s+(?:the\s+)?(?:data\s+|source\s+)?(?:segment|partition|stratum)\b',re.I)


def problem(question,sql):
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
