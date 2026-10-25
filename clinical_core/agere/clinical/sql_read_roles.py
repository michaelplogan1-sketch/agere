"""Resolve numeric lab-value uses to their physical reads, without executing SQL.

An EXISTS projection, a null-validity test, or COUNT(non-null values) asks about
available evidence, not the latest numeric result. Derived columns and aliases
must retain that distinction in composed queries.
"""
from __future__ import annotations


def numeric_measurement_reads(sql, table_positions, *, exempt_aliases=()):
    from sqlglot.errors import SqlglotError
    try:
        return _numeric_measurement_reads(sql, table_positions, exempt_aliases=exempt_aliases)
    except (SqlglotError, RecursionError) as exc:
        raise ValueError('Measurement value roles could not be resolved') from exc


def _numeric_measurement_reads(sql, table_positions, *, exempt_aliases=()):
    """Return FROM/JOIN offsets for measurement reads whose values are consumed.

    table_positions maps identifier offsets to the caller's FROM/JOIN offsets.
    The input has comments removed and literal placeholders replaced, preserving
    those offsets. SQLGlot parses SQLite syntax; no query is run here.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope, traverse_scope

    tree = sqlglot.parse_one(sql, read='sqlite')
    if tree is None: return set()
    if any(w.args.get('recursive') for w in tree.find_all(exp.With)):
        raise ValueError('Recursive measurement scopes need an explicit reduction')
    scopes = list(traverse_scope(tree))
    by_select = {id(s.expression): s for s in scopes}
    required, visited = set(), set()
    exempt_aliases = {x.lower() for x in exempt_aliases}
    value_names = {'value', 'value_canonical', 'value_num'}

    def scope_of(column, fallback):
        select = column.find_ancestor(exp.Select)
        return by_select.get(id(select), fallback)

    def ignored(column):
        parent = column.parent
        return ((isinstance(parent, exp.Is) and parent.this is column and isinstance(parent.expression, exp.Null))
                or (isinstance(parent, exp.Count) and parent.this is column))

    def projection_is_intermediate(column):
        select = column.find_ancestor(exp.Select)
        if select is None: return False
        top = column
        while top.parent is not None and top.parent is not select: top = top.parent
        if top not in select.expressions: return False
        owner = select.parent
        if isinstance(owner, exp.Exists): return True  # EXISTS ignores every projected value
        while isinstance(owner, exp.SetOperation): owner = owner.parent
        if isinstance(owner, exp.CTE): return True
        return isinstance(owner, exp.Subquery) and isinstance(owner.parent, (exp.From, exp.Join, exp.CTE))

    def active(scope):
        return {name.lower(): source for name, (_, source) in scope.selected_sources.items()}

    def trace_expression(expression, scope, reduced=False):
        for column in expression.find_all(exp.Column):
            if not ignored(column): resolve(scope_of(column, scope), column.name, column.table, reduced)

    def trace_source(source, name, reduced=False):
        if isinstance(source, exp.Table):
            if reduced: return
            if source.name.lower() != 'measurement' or (name != '*' and name.lower() not in value_names): return
            if source.alias_or_name.lower() in exempt_aliases: return
            owner = source.find_ancestor(exp.Select)
            if owner is not None and (owner.args.get('having') or any(w.find_ancestor(exp.Select) is owner for w in owner.find_all(exp.Window))):
                return  # explicit grouped/window reductions already choose their own result grain
            identifier = source.this
            position = identifier.meta.get('start')
            if position is not None and identifier.args.get('quoted'): position += 1
            if position not in table_positions:
                raise ValueError('Measurement read could not be bound to its SQL source')
            required.add(table_positions[position])
            return
        if not isinstance(source, Scope): return
        expressions = list(source.expression.selects)
        owner = source.expression
        reduced = reduced or bool(owner.args.get('having') or any(w.find_ancestor(exp.Select) is owner for w in owner.find_all(exp.Window)))
        if name == '*':
            for expression in expressions:
                if isinstance(expression, exp.Star):
                    for child in active(source).values(): trace_source(child, '*', reduced)
                else: trace_expression(expression, source, reduced)
            return
        names = source.outer_columns or [x.alias_or_name for x in expressions]
        index = next((i for i, n in enumerate(names) if n.lower() == name.lower()), None)
        branches = source.set_operation_scopes or [source]
        if index is not None:
            for branch in branches:
                selected = list(branch.expression.selects)
                if index < len(selected): trace_expression(selected[index], branch, reduced)
        elif any(isinstance(x, exp.Star) or (isinstance(x, exp.Column) and x.is_star) for x in expressions):
            for branch in branches: resolve(branch, name, '', reduced)

    def resolve(scope, name, qualifier, reduced=False):
        if scope is None: return
        key = (id(scope), name.lower(), qualifier.lower(), reduced)
        if key in visited: return
        visited.add(key)
        sources = active(scope)
        if qualifier:
            source = sources.get(qualifier.lower())
            if source is not None: trace_source(source, name, reduced)
            elif scope.parent is not None: resolve(scope.parent, name, qualifier, reduced)
            return
        if len(sources) == 1:
            trace_source(next(iter(sources.values())), name, reduced)
            return
        # Unqualified canonical value columns are unambiguous only with a single
        # value-bearing source. Multiple physical candidates cannot be guessed.
        candidates = []
        for source in sources.values():
            if isinstance(source, exp.Table):
                if source.name.lower() in ('measurement', 'measurement_latest', 'latest_lab') and name.lower() in value_names:
                    candidates.append(source)
            elif isinstance(source, Scope):
                selected = list(source.expression.selects)
                names = source.outer_columns or [x.alias_or_name for x in selected]
                if name.lower() in {n.lower() for n in names} or any(isinstance(x, exp.Star) for x in selected):
                    candidates.append(source)
        if len(candidates) == 1: trace_source(candidates[0], name, reduced)
        elif not sources and scope.parent is not None: resolve(scope.parent, name, '', reduced)
        elif len(candidates) > 1: raise ValueError('Ambiguous measurement value column')

    for column in tree.find_all(exp.Column):
        if not ignored(column) and not projection_is_intermediate(column):
            resolve(scope_of(column, None), column.name, column.table)
    for scope in scopes:
        select = scope.expression
        if not isinstance(select, exp.Select) or select.parent is not None: continue
        for projection in select.expressions:
            if isinstance(projection, exp.Star):
                for source in active(scope).values(): trace_source(source, '*')
    return required


def counts_distinct_patient_ids(sql):
    from sqlglot.errors import SqlglotError
    try:
        return _counts_distinct_patient_ids(sql)
    except (SqlglotError, RecursionError) as exc:
        raise ValueError('Counted identifier lineage could not be resolved') from exc


def _counts_distinct_patient_ids(sql):
    """Recognize a patient-count unit through direct column aliases and CTEs.

    It does not infer units from names like `pid`, arithmetic, concatenation,
    arbitrary constants, or multiple unqualified sources.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope, traverse_scope
    tree = sqlglot.parse_one(sql, read='sqlite')
    scopes = {id(s.expression): s for s in traverse_scope(tree)}
    seen = set()

    def bound(scope, name, qualifier):
        if scope is None: return False
        key = (id(scope), name.lower(), qualifier.lower())
        if key in seen: return False
        seen.add(key)
        sources = {k.lower(): source for k, (_, source) in scope.selected_sources.items()}
        if qualifier:
            source = sources.get(qualifier.lower())
            if source is None: return bound(scope.parent, name, qualifier)
        elif len(sources) == 1:
            source = next(iter(sources.values()))
        else:
            return False
        if isinstance(source, exp.Table): return name.lower() == 'patient_id'
        if not isinstance(source, Scope): return False
        selected = list(source.expression.selects)
        names = source.outer_columns or [x.alias_or_name for x in selected]
        index = next((i for i, n in enumerate(names) if n.lower() == name.lower()), None)
        if index is None:
            return any(isinstance(x, exp.Star) for x in selected) and bound(source, name, '')
        expressions = [list(s.expression.selects)[index] for s in source.set_operation_scopes] if source.set_operation_scopes else [selected[index]]
        results = []
        for expression in expressions:
            expression = expression.this if isinstance(expression, exp.Alias) else expression
            if not isinstance(expression, exp.Column): return False
            results.append(bound(scopes.get(id(expression.find_ancestor(exp.Select)), source), expression.name, expression.table))
        return bool(results) and all(results)

    for count in tree.find_all(exp.Count):
        if not isinstance(count.this, exp.Distinct) or len(count.this.expressions) != 1: continue
        column = count.this.expressions[0]
        if isinstance(column, exp.Column) and bound(scopes.get(id(column.find_ancestor(exp.Select))), column.name, column.table):
            return True
    return False
