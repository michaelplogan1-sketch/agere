"""Governed read-only SQL over the semantic layer.

Planners write one SELECT against the typed, concept-normalised tables (see
semantic_layer.SCHEMA_DOC). Execution is read-only by construction: the
connection is opened mode=ro and an authorizer permits only reads and
functions (no writes, ATTACH, PRAGMA or schema changes); a progress handler
enforces a time limit and results are capped. An upstream population, when
given, is exposed to the query as the CTE `population(patient_id)`.
"""
import json, math, re, sqlite3, time

from . import semantic_layer
from .age import register as register_age
from .dose import register as register_dose


def register(db):
    from ..request_lineage import trace_sql
    trace_sql(db)
    from .clinical_inputs import register as register_clinical_inputs
    semantic_layer.ensure_diagnosis_view(db)  # a semantic.sqlite3 built before diagnosis_any gets it as a temp view
    semantic_layer.ensure_concept_view(db)  # ... and the condition rows of concept_patient at the same evidence definition
    semantic_layer.ensure_sdoh_latest_view(db)  # the latest matched purchased-SDOH delivery per patient
    return register_clinical_inputs(register_dose(register_age(db)))

VERSION = 'semantic-sql/2'
RETURNS = ('count', 'value', 'patient_set', 'table')
MAX_ROWS = 200000
TIME_LIMIT_SECONDS = 600
_ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
_RECURSIVE = getattr(sqlite3, 'SQLITE_RECURSIVE', None)
if _RECURSIVE is not None: _ALLOWED.add(_RECURSIVE)


_RELATIONS = {}


def decision_context_marker():
    from .decision_context import SEGMENT_MARKER
    return SEGMENT_MARKER


def _relations(target):
    """The table names of a semantic database (cached per file and modification time)."""
    import os
    try:
        key = (str(target), os.stat(target).st_mtime_ns)
    except OSError:
        return None
    if key not in _RELATIONS:
        try:
            with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
                names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
                if 'patient' in names and any(r[1] == 'data_segment' for r in db.execute('PRAGMA table_info(patient)')):
                    names.add(decision_context_marker())  # the patient relation can tell a duplicate chart
                _RELATIONS[key] = frozenset(names)
        except sqlite3.Error:
            return None
    return _RELATIONS[key]


def statement_code(sql):
    """SQL code with literals/comments masked and quoted identifiers retained.

    Delimiters inside source strings or quoted identifiers are data. This small
    lexer establishes statement boundaries; SQLite remains the syntax authority.
    """
    out=[];i=0
    while i<len(sql):
        if sql.startswith('--',i):
            end=sql.find('\n',i+2);end=len(sql)if end<0 else end
            out.append(' '*(end-i));i=end;continue
        if sql.startswith('/*',i):
            end=sql.find('*/',i+2)
            if end<0:raise ValueError('Unterminated SQL comment')
            out.append(' '*(end+2-i));i=end+2;continue
        ch=sql[i]
        if ch in ("'",'"','`','['):
            close=']'if ch=='['else ch;single=ch=="'";i+=1;out.append(' ')
            while i<len(sql):
                c=sql[i]
                if c==close:
                    if close!=']'and i+1<len(sql)and sql[i+1]==close:
                        out.extend([' ',' ']);i+=2;continue
                    out.append(' ');i+=1;break
                out.append(' 'if single or c==';'else c);i+=1
            else:raise ValueError('Unterminated SQL quoted value or identifier')
            continue
        out.append(ch);i+=1
    return ''.join(out)


def native_query_bindings(query, available):
    """Resolve real table bindings, including CTE shadowing, with the SQL parser.

    SQLite authorization remains the execution authority. Its source callback
    alone cannot distinguish a view from a same-named CTE reading a main table.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope
    try:
        tree = sqlglot.parse_one(query, read='sqlite')
        bindings = {}
        for scope in traverse_scope(tree):
            for source in scope.sources.values():
                if (isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier)
                        and source.db.casefold() in ('', 'temp')
                        and source.name.casefold() in available):
                    bindings[id(source)] = source
        return tree, list(bindings.values())
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError, sqlglot.errors.OptimizeError):
        return None, None


def native_result_source(columns, rows):
    """Exact SQL representation shared by native views and portable receipts."""
    fields = ', '.join(f'json_extract(value,\'$[{i}]\') AS "{str(c).replace(chr(34), chr(34)*2)}"' for i, c in enumerate(columns))
    data = json.dumps(rows, default=str, allow_nan=False).replace("'", "''")
    return f"SELECT {fields} FROM json_each('{data}')"


def native_query_model_projection(query, native):
    """Snapshot-equivalent display of literal native rows by their SQL handles.

    Only the exact generated JSON row-source expression may be replaced. All
    joins, predicates, dates, cohort filters and calculations remain untouched.
    This proves literal-copy equivalence, never provenance or clinical meaning.
    The complete original SQL and rows remain native execution evidence.
    """
    import sqlglot
    from sqlglot import exp
    from ...contracts import digest
    from .result_assembly import incomplete_reason
    from .backstop_agent import output_table
    try:tree=sqlglot.parse_one(query,read='sqlite')
    except (sqlglot.errors.SqlglotError,ValueError,TypeError):return None
    candidates=[]
    for view,result in native.items():
        if not isinstance(view,str)or not isinstance(result,dict)or not re.fullmatch(r'agere_result_\d+',view)or incomplete_reason(result):continue
        columns,rows=output_table(result)
        if(not isinstance(columns,list)or not columns or len(set(columns))!=len(columns)
           or not all(isinstance(c,str)for c in columns)or not isinstance(rows,list)):continue
        matrix=rows
        if any(r is None or len(r)!=len(columns)for r in matrix):continue
        shape=sqlglot.parse_one(native_result_source(columns,[]),read='sqlite').sql(dialect='sqlite')
        candidates.append((view,result,columns,digest(matrix),shape,len(rows)))
    bindings=[]
    # Parsing treats each complete JSON payload as one literal, not SQL. Never
    # search/replace arbitrary text or choose a source by a matching scalar.
    for select in list(tree.find_all(exp.Select)):
        source=(select.args.get('from_')or exp.From()).this
        if not isinstance(source,exp.Table)or not isinstance(source.this,exp.Anonymous)or source.this.name.lower()!='json_each':continue
        arguments=source.this.expressions
        if len(arguments)!=1 or not isinstance(arguments[0],exp.Literal)or not arguments[0].is_string:continue
        try:rows=json.loads(arguments[0].this)
        except (ValueError,TypeError):continue
        if not isinstance(rows,list):continue
        small=select.copy();small_source=small.args['from_'].this.this
        small_source.set('expressions',[exp.Literal.string('[]')]);shape=small.sql(dialect='sqlite')
        matches=[c for c in candidates if c[4]==shape and c[3]==digest(rows)]
        if len(matches)!=1:continue  # equal-looking sources do not establish a unique handle
        view,result,columns,rows_digest,_,count=matches[0]
        ordinal=next((name for name in ('_rowid_','rowid','oid')if name not in {c.casefold()for c in columns}),None)
        if ordinal is None:continue
        # Native tables are inserted in the exact literal-array order. Their
        # lookup indexes must not reorder duplicate/keyed rows in a display
        # program; the original receipts and row-address bindings stay intact.
        replacement=sqlglot.parse_one('SELECT * FROM temp."'+view+'" ORDER BY '+ordinal,read='sqlite')
        select.replace(replacement)
        if select is tree:tree=replacement
        bindings.append({'result_view':view,'native_receipt_digest':digest(result),'columns':columns,
            'rows':count,'rows_digest':rows_digest,'native_order_column':ordinal,'literal_source_digest':digest(select.sql(dialect='sqlite'))})
    if not bindings:return None
    return {'schema':'native-literal-program-view/1','query':tree.sql(dialect='sqlite'),
        'original_query_digest':digest(query),'original_query_characters':len(query),'source_bindings':bindings,
        'scope':'Snapshot-equivalent model view of exact literal native row sources only. All non-source query logic is retained. '
            'Complete original SQL and every row remain in native receipts and result_views. This is not the original executed SQL '
            'and does not establish clinical meaning or independent source provenance.'}


def portable_native_query(query, sources, read_views):
    """Bind actual native reads in a portable receipt; never execute this rewrite."""
    available = {name.casefold(): name for name in read_views}
    tree, tables = native_query_bindings(query, available)
    if tree is None:
        raise ValueError('Native SQL dependencies could not be bound for a portable receipt')
    from sqlglot import exp
    declared = {cte.alias_or_name.casefold() for cte in tree.find_all(exp.CTE)}
    occupied = set(available) | {node.name.casefold() for node in tree.find_all(exp.Identifier)}
    portable = {}
    for index, name in enumerate(read_views, 1):
        alias = name
        if name.casefold() in declared:
            alias = f'_agere_native_source_{index}'
            while alias.casefold() in occupied:
                alias += '_'
        occupied.add(alias.casefold())
        portable[name.casefold()] = alias
    for table in tables:
        name = table.name.casefold()
        if portable[name] != available[name] and not table.args.get('alias'):
            # Preserve original column qualifiers when only the portable source
            # name changes. The real executed statement remains untouched.
            table.set('alias', exp.TableAlias(this=exp.to_identifier(table.name)))
        table.set('db', None)
        table.set('this', exp.to_identifier(portable[name]))
    for column in tree.find_all(exp.Column):
        name = column.table.casefold()
        if column.db.casefold() == 'temp' and name in portable and portable[name] != available[name]:
            column.set('db', None)
    # Parse only the declared query. Complete native JSON literals can be large;
    # retain their exact stored definitions without reparsing or copying an AST.
    body = tree.sql(dialect='sqlite', comments=False)
    ctes = ', '.join(portable[name.casefold()] + ' AS (' + sources[name] + ')' for name in read_views)
    if re.match(r'^WITH\b', body, re.I):
        return re.sub(r'^WITH\s+(RECURSIVE\s+)?', lambda match:
                      'WITH ' + (match.group(1) or '') + ctes + ', ', body, count=1, flags=re.I)
    return 'WITH ' + ctes + ' ' + body


def bound_native_query_receipt(query, read_views, read_columns, available):
    """Reference exact preceding native receipts instead of copying their rows.

    Actual SQLite read receipts supply the hashes. Unsupported dependency
    shapes retain the legacy portable representation; no evidence is omitted.
    """
    from ...contracts import digest
    if (not isinstance(read_views,list)or not read_views or len(set(read_views))!=len(read_views)
        or not isinstance(read_columns,dict)or read_columns.get('schema')!='sqlite-native-column-reads/1'
        or read_columns.get('query_digest')!=digest(query)):return None
    tree,tables=native_query_bindings(query,{name.casefold():name for name in available})
    if tree is None or {t.name.casefold()for t in tables}!={v.casefold()for v in read_views}:return None
    sources=read_columns.get('sources')or{}
    if set(sources)!=set(read_views):return None
    bindings=[]
    for name in sorted(read_views):
        value=sources[name].get('receipt_digest')
        if name not in available or not isinstance(value,str)or not re.fullmatch('[0-9a-f]{64}',value):return None
        bindings.append({'result_view':name,'receipt_digest':value})
    return {'schema':'native-bound-query/1','query_digest':digest(query),'read_columns_digest':digest(read_columns),'source_bindings':bindings,
        'scope':'Exact executed statement plus hash-bound preceding native receipts. Complete source rows remain in those receipts; standalone export must resolve them. No clinical/source acceptance is inherited.'}


def _validate_bound_native_query(result, native, *, digests=None):
    """Fail closed on a changed statement, missing source or changed receipt."""
    from ...contracts import digest
    receipt=result.get('native_query_receipt')if isinstance(result,dict)else None
    if not isinstance(receipt,dict)or receipt.get('schema')!='native-bound-query/1':return False
    query=result.get('executed_query');views=result.get('read_views')
    if (not isinstance(query,str)or result.get('declared_executed_query')!=query
        or receipt.get('query_digest')!=digest(query)or not isinstance(views,list)
        or len(set(views))!=len(views)or not views
        or receipt.get('read_columns_digest')!=digest(result.get('native_read_columns'))):return False
    bindings=receipt.get('source_bindings')
    if (not isinstance(bindings,list)or any(not isinstance(b,dict)or set(b)!={'result_view','receipt_digest'}for b in bindings)
        or len(bindings)!=len(views)or {b['result_view']for b in bindings}!=set(views)):return False
    reads=result.get('native_read_columns')or{}
    if(reads.get('schema')!='sqlite-native-column-reads/1'or reads.get('query_digest')!=digest(query)
        or set(reads.get('sources')or{})!=set(views)
        or any(reads['sources'][b['result_view']].get('receipt_digest')!=b['receipt_digest']for b in bindings)):return False
    tree,tables=native_query_bindings(query,{v.casefold():v for v in native})
    if tree is None or {t.name.casefold()for t in tables}!={v.casefold()for v in views}:return False
    checked={}if digests is None else digests
    for binding in bindings:
        name=binding['result_view'];source=native.get(name)
        if not isinstance(source,dict):return False
        if name not in checked:checked[name]=digest({k:v for k,v in source.items()if k!='result_view'})
        if checked[name]!=binding['receipt_digest']:return False
    return True


def validate_bound_native_query(result, native, *, digests=None):
    try:return _validate_bound_native_query(result,native,digests=digests)
    except (KeyError,TypeError,ValueError,AttributeError):return False


def native_query_binding_errors(calls):
    """Validate each new-format query against preceding, unique call results."""
    native={};digests={};ambiguous=set();errors=[]
    for index,call in enumerate(calls):
        result=call.get('result')or{}
        if not isinstance(result,dict):continue
        if 'native_query_receipt'in result and (call.get('name')!='query_sql'or
                not validate_bound_native_query(result,native,digests=digests)or
                bool(set(result.get('read_views')or[])&ambiguous)):
            errors.append({'tool_index':index,'reason':'Native query source/statement binding changed or is unavailable'})
        name=result.get('result_view')
        if isinstance(name,str):
            if name in native:ambiguous.add(name)
            native[name]=result;digests.pop(name,None)
    return errors


def resolve_bound_native_query(result, native):
    """Explicit standalone export: reconstruct exact legacy SQL from origins."""
    digests={};checked=set()
    def check(value,active):
        if not validate_bound_native_query(value,native,digests=digests):raise ValueError('Native query origins are missing or changed')
        for binding in value['native_query_receipt']['source_bindings']:
            name=binding['result_view']
            if name in active:raise ValueError('Cyclic native query origin')
            parent=native[name]
            if name not in checked and parent.get('native_query_receipt'):
                check(parent,active|{name});checked.add(name)
    check(result,set())
    from .backstop_agent import output_table
    sources={name:native_result_source(*output_table(native[name]))for name in result['read_views']}
    return portable_native_query(result['executed_query'],sources,result['read_views'])


def validate(p):
    if not isinstance(p, dict) or set(p) - {'query', 'returns'} or not isinstance(p.get('query'), str):
        raise ValueError('sql parameters: query (one SELECT), returns')
    q = p['query'].strip().rstrip(';').strip()
    code = statement_code(q).strip().rstrip(';').strip()
    if not code: raise ValueError('A SQL query is required')  # no length cap (Mike, 2026-10-20): governed macros expand inline
    if not re.match(r'(?is)^(select|with)\b', code) or ';' in code:
        raise ValueError('Exactly one SELECT/WITH statement is allowed')
    if p.get('returns') not in RETURNS: raise ValueError(f'returns must be one of {RETURNS}')
    if re.search(r'\bage_latest\b', code, re.I):
        raise ValueError('Stale feed age is not an analytical field; use age_at(birth_date, as_of).')
    return {'query': q, 'returns': p['returns']}


def date_arithmetic_issues(db, query):
    """Detect SQLite's silent numeric coercion of received ISO date text.

    Resolve ordinary columns and CTE aliases through the installed SQL parser.
    A data column is temporal only when every non-NULL received value is ISO
    date text; numeric epochs and explicitly numeric conversions remain valid.
    This checks types, not clinical meaning, and never rewrites the calculation.
    """
    if not any(op in query for op in ('-', '+', '*', '/', '%')):
        return []
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope, traverse_scope
    try:
        tree = sqlglot.parse_one(query, read='sqlite')
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
        # SQLite remains the syntax authority for extensions this parser cannot
        # represent. No type finding is inferred from an unsuccessful parse.
        return []
    profiles, schemas = {}, {}
    def quoted(name):
        return '"' + name.replace('"', '""') + '"'
    def columns(table):
        if table not in schemas:
            try:
                cur = db.execute('SELECT * FROM ' + quoted(table) + ' LIMIT 0')
                schemas[table] = {d[0].casefold(): d[0] for d in cur.description or []}
            except sqlite3.DatabaseError as exc:
                if 'interrupt' in str(exc).lower(): raise
                schemas[table] = {}
        return schemas[table]
    def temporal_column(table, name):
        actual = columns(table).get(name.casefold())
        if actual is None: return False
        key = (table, actual)
        if key not in profiles:
            col = quoted(actual)
            # Exact counterexample search, not sampled type inference: one
            # numeric/mixed value disproves the date-text type immediately.
            invalid = db.execute(
                f"SELECT 1 FROM {quoted(table)} WHERE {col} IS NOT NULL AND NOT (typeof({col})='text' AND "
                f"{col} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' "
                f"AND julianday({col}) IS NOT NULL) LIMIT 1").fetchone()
            profiles[key] = invalid is None and db.execute(
                f'SELECT 1 FROM {quoted(table)} WHERE {col} IS NOT NULL LIMIT 1').fetchone() is not None
        return profiles[key]
    def from_source(source, name, seen):
        if isinstance(source, exp.Table):
            return temporal_column(source.name, name)
        if isinstance(source, Scope):
            for index, item in enumerate(source.expression.selects):
                alias = source.outer_columns[index] if index < len(source.outer_columns) else item.alias_or_name
                if alias.casefold() == name.casefold():
                    return temporal(item.this if isinstance(item, exp.Alias) else item, source, seen)
                if item.is_star:
                    return temporal(exp.column(name, table=item.table if isinstance(item, exp.Column) else None), source, seen)
        return False
    def temporal(node, scope, seen=None):
        seen = set() if seen is None else seen
        if isinstance(node, exp.Paren): return temporal(node.this, scope, seen)
        if isinstance(node, exp.Literal):
            return node.is_string and bool(re.match(r'^\d{4}-\d{2}-\d{2}(?:$|[ T])', node.this))
        if isinstance(node, exp.Date) or (isinstance(node, exp.Anonymous) and node.name.casefold() in ('date', 'datetime')):
            return True
        if not isinstance(node, exp.Column): return False
        key = (id(scope), node.table.casefold(), node.name.casefold())
        if key in seen: return False
        seen = seen | {key}
        sources = [source for alias, source in scope.sources.items()
                   if not node.table or alias.casefold() == node.table.casefold()]
        # Unqualified columns need one received source; ambiguity is left to
        # SQLite rather than guessed from a similarly named field.
        if len(sources) != 1 and not node.table:
            sources = [s for s in sources if isinstance(s, exp.Table) and node.name.casefold() in columns(s.name)]
        return len(sources) == 1 and from_source(sources[0], node.name, seen)
    issues = []
    for scope in traverse_scope(tree):
        for node in scope.expression.find_all(exp.Sub, exp.Add, exp.Mul, exp.Div, exp.Mod):
            if node.find_ancestor(exp.Select) is not scope.expression: continue
            if temporal(node.this, scope) or temporal(node.expression, scope):
                issues.append('Raw arithmetic on ISO date text would use SQLite numeric coercion: ' + node.sql(dialect='sqlite') +
                              '. Use julianday for elapsed days or unixepoch for elapsed seconds, then convert units explicitly.')
    return list(dict.fromkeys(issues))


def source_tuple_issues(query):
    """Independent extrema cannot select an intact dated source reference.

    Follow direct native date/record_id projections through SQL scopes. This is
    a structural provenance contract, not a guess about clinical meaning or a
    claim that arbitrary SQL has been proven correct. Different source bindings
    and groups fixed to one of the tuple's members remain valid. A planner must
    choose a row (with explicit tie handling), or retain all matching rows.
    """
    if not re.search(r'\b(?:max|min)\s*\(', query, re.I): return []
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope, traverse_scope
    try:
        tree = sqlglot.parse_one(query, read='sqlite')
        scopes = list(traverse_scope(tree))
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError, sqlglot.errors.OptimizeError):
        return []  # SQLite remains the syntax authority.

    clocks = {'date', 'source_date', 'event_date', 'available_date', 'recorded_date', 'clinical_date', 'reference_date'}

    def binding(node, scope, seen=frozenset()):
        if isinstance(node, (exp.Alias, exp.Paren)): return binding(node.this, scope, seen)
        if not isinstance(node, exp.Column): return None
        key = (id(scope), node.table.casefold(), node.name.casefold())
        if key in seen: return None
        sources = [(alias, source) for alias, (_, source) in scope.selected_sources.items()
                   if not node.table or alias.casefold() == node.table.casefold()]
        if len(sources) != 1: return None  # Do not invent ambiguous lineage.
        alias, source = sources[0]; origin = (id(scope), alias.casefold())
        if isinstance(source, exp.Table):
            return ((origin,), node.name.casefold()) if node.name.casefold() in clocks | {'record_id'} else None
        if isinstance(source, Scope):
            for index, item in enumerate(source.expression.selects):
                name = source.outer_columns[index] if index < len(source.outer_columns) else item.alias_or_name
                if name.casefold() == node.name.casefold():
                    found = binding(item, source, seen | {key})
                    return ((origin,) + found[0], found[1]) if found else None
                if item.is_star:
                    found = binding(exp.column(node.name, table=item.table if isinstance(item, exp.Column) else None),
                                    source, seen | {key})
                    if found: return ((origin,) + found[0], found[1])
        return None

    issues = []
    for scope in scopes:
        selected = scope.expression
        if not isinstance(selected, exp.Select): continue
        extrema = []
        for item in selected.find_all(exp.Max, exp.Min):
            if item.find_ancestor(exp.Select) is not selected: continue
            bound = binding(item.this, scope)
            if bound is None: continue
            window = item.find_ancestor(exp.Window)
            if window is None:
                group = selected.args.get('group')
                fixed = [binding(x, scope) for x in group.expressions] if group else []
                window_key = None
            else:
                fixed = [binding(x, scope) for x in window.args.get('partition_by') or []]
                window_key = tuple((name, str(window.args.get(name)))
                                   for name in ('partition_by', 'order', 'spec', 'alias'))
            extrema.append((item, bound, fixed, window_key))
        for date_expr, date_binding, fixed, window_key in extrema:
            if date_binding[1] not in clocks: continue
            for record_expr, record_binding, _, other_window in extrema:
                if (record_binding[1] != 'record_id' or record_binding[0] != date_binding[0]
                        or window_key != other_window or date_binding in fixed or record_binding in fixed):
                    continue
                issues.append('Independent source aggregates ' + date_expr.sql(dialect='sqlite') + ' and ' +
                              record_expr.sql(dialect='sqlite') + ' do not select the same source row. '
                              'Keep date, record_id and quote together: use ROW_NUMBER() OVER '
                              '(PARTITION BY the required keys ORDER BY date DESC, record_id DESC) '
                              'and select that intact row, or retain every tied source row. '
                              'Declare tie handling; a NULL source date remains unknown, not missing evidence.')
    return list(dict.fromkeys(issues))


_DEFINITION_VIEW = re.compile(r'(?i)\b(?:living_patient|condition_member|latest_lab|last_12_months|medicare_member)\b')


def uses_definition_views(query):
    """True when the query reads one of the analyst's decision-context definition views (analyst_definitions.VIEW_NAMES)."""
    return bool(_DEFINITION_VIEW.search(re.sub(r"'[^']*'", "''", re.sub(r'--[^\n]*', ' ', query or ''))))


def define_definition_views(db):
    """Create the definition views as TEMP views on this (read-only, not yet authorized) connection, exactly as the analyst's own connection has them
    (semantic_analyst.SemanticWarehouse.define): a graph built from the analyst's SQL then executes where the analyst ran it. Never written to the file."""
    from . import analyst_definitions
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")]
    columns = {n: [r[1] for r in db.execute(f'PRAGMA table_info("{n}")')] for n in names}
    as_of = next((r[0] for r in db.execute("SELECT value FROM meta WHERE key = 'as_of'")), None) if 'meta' in columns else None
    views = analyst_definitions.build_views(columns, str(as_of or '2000-01-01')[:10])
    db.execute('PRAGMA query_only=OFF')
    try:
        for name in analyst_definitions.VIEW_NAMES:  # dependency order: living_patient before medicare_member
            if name in views: db.execute(f'CREATE TEMP VIEW {name} AS {views[name]}')
    finally:
        db.execute('PRAGMA query_only=ON')


def _authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in _ALLOWED else sqlite3.SQLITE_DENY


def execute(path, params, patient_ids=None, target=None, time_limit=None):
    from ..request_lineage import component
    from ...contracts import digest
    with component('governed_sql', query_digest=digest(params),
                   population_digest=digest(patient_ids), target=str(target or path)) as metrics:
        result = _execute(path, params, patient_ids=patient_ids, target=target, time_limit=time_limit)
        metrics.update(rows=result.get('row_count'), truncated=result.get('truncated'))
        return result


def _execute(path, params, patient_ids=None, target=None, time_limit=None):
    """target: an existing semantic.sqlite3 to read as-is (never rebuilt; used to replay against a read-only build)."""
    p = validate(params)
    from .answer_budget import tier_remaining  # a query inside a cascade tier never outlives the tier's time share (a runaway candidate SQL held a question for 19 minutes)
    limit = tier_remaining(time_limit or TIME_LIMIT_SECONDS)
    from . import decision_context  # the question's decision context: living patients and latest labs in the SQL that runs
    target = target or semantic_layer.ensure(path)
    state = decision_context.current()
    token = None
    # A compiled event-per-patient rate sums correlated event counts. Its denominator names patients, but the rate is
    # event grain (Mike 2026-10-25). Keep lab enforcement and an outer composite answer's patient scope intact.
    masked, _ = decision_context._mask(p['query'])
    if (state is not None and p['returns'] != 'patient_set' and not decision_context.LIVING_ASK.search(state.question)
            and not decision_context.PATIENT_COUNT.search(state.question)
            and re.search(r'(?i)\bsum\s*\(\s*\(\s*select\s+count\s*\(', masked)
            and re.search(r'(?i)\bfrom\s+"?(?:utilization_event|stay|fill|appointment)"?\b', masked)):
        import copy
        events = copy.copy(state)
        events.population = {'living_only': False, 'reason': 'event rate: every patient and their events count (Mike 2026-10-25)'}
        token = decision_context._SCOPE.set(events)
    try:
        run_sql, dc_receipt = decision_context.enforce_sql(p['query'], p['returns'], _relations(target) if state is not None else None)
    finally:
        if token is not None: decision_context._SCOPE.reset(token)
    shown = p
    p = {**p, 'query': run_sql}
    issues = source_tuple_issues(run_sql)
    if issues: raise ValueError('; '.join(issues))
    from . import duckdb_engine  # DuckDB serving (AGERE_SQL_ENGINE=duckdb) with SQLite fallback; shadow comparison
    engine_receipt = None
    uses_views = uses_definition_views(run_sql)  # the analyst's definition views exist only as temp views on a SQLite connection
    if duckdb_engine.engine() == 'duckdb' and not uses_views:
        started = time.perf_counter()
        columns, rows, engine_receipt = duckdb_engine.serve(target, p['query'], patient_ids, time_limit=limit,
                                                            max_rows=MAX_ROWS)
        if columns is not None: return _result(p, columns, rows, started, engine_receipt, shown, dc_receipt)
    from . import plan_optimizer  # repeated sub-selects run once (fused CTE); an unfusable form is re-run as planned
    if re.search(r'\bpopulation\b',p['query'],re.I) and patient_ids is None:
        raise ValueError('Query references population but no population input was given')
    db = register(sqlite3.connect(f'file:{target}?mode=ro', uri=True))
    if uses_views: define_definition_views(db)
    query, fusion = plan_optimizer.fuse_sql(p['query'], db)
    unfused, args = p['query'], {}
    if re.search(r'\bpopulation\b', query, re.I):
        if patient_ids is None: raise ValueError('Query references population but no population input was given')
        cte = 'population(patient_id) AS (SELECT value FROM json_each(:population))'
        add = lambda q: (f'WITH {cte}, ' + q[4:].lstrip()) if re.match(r'(?is)^with\b', q) else f'WITH {cte} {q}'
        query, unfused = add(query), add(unfused)
        args['population'] = json.dumps(sorted(patient_ids))
    deadline = time.perf_counter() + limit
    db.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10000)
    db.set_authorizer(_authorizer)
    started = time.perf_counter()
    try:
        issues = date_arithmetic_issues(db, unfused)
        if issues: raise ValueError('; '.join(issues))
        try:
            cursor = db.execute(query, args)
            columns = [c[0] for c in cursor.description or []]
            rows = cursor.fetchmany(MAX_ROWS + 1)
        except sqlite3.DatabaseError as exc:
            if not fusion['fused'] or 'interrupt' in str(exc).lower(): raise
            fusion = {**fusion, 'fused': [], 'fallback': str(exc)[:120]}  # the rewrite did not stand alone: run the planned text
            cursor = db.execute(unfused, args)
            columns = [c[0] for c in cursor.description or []]
            rows = cursor.fetchmany(MAX_ROWS + 1)
    except sqlite3.DatabaseError as exc:  # includes authorizer denials and the time limit
        raise ValueError(f'SQL failed: {exc}') from exc
    finally:
        db.close()
    if duckdb_engine.shadow_on():
        engine_receipt = {**(engine_receipt or {'engine': 'sqlite'}), 'shadow': duckdb_engine.shadow(
            target, p['query'], patient_ids, columns, rows[:MAX_ROWS], round(time.perf_counter() - started, 4), max_rows=MAX_ROWS)}
    if fusion['fused'] or fusion.get('fallback'): engine_receipt = {**(engine_receipt or {'engine': 'sqlite'}), 'plan_optimizer': fusion}
    return _result(p, columns, rows, started, engine_receipt, shown, dc_receipt)


def _result(p, columns, rows, started, engine_receipt=None, shown=None, dc_receipt=None):
    truncated = len(rows) > MAX_ROWS
    rows = [list(r) for r in rows[:MAX_ROWS]]
    if any(isinstance(v,float) and not math.isfinite(v) for row in rows for v in row):
        raise ValueError('Nonfinite numeric result is not a verified answer; define compatible numeric and undefined-result handling')
    result = {'schema': VERSION, 'query': (shown or p)['query'], 'returns': p['returns'], 'columns': columns,
              'row_count': len(rows), 'truncated': truncated, 'elapsed_seconds': round(time.perf_counter() - started, 3)}
    if dc_receipt and dc_receipt.get('rewritten'): result['decision_context'] = {**dc_receipt, 'executed_query': p['query']}
    if engine_receipt: result['engine'] = engine_receipt
    if p['returns'] in ('count', 'value'):
        if not rows or not columns: raise ValueError('Scalar query returned no rows')
        if len(rows) != 1 or len(columns) != 1:
            raise ValueError('Scalar query must return exactly one row and one column; use table for multiple outputs')
        value = rows[0][0]
        if p['returns'] == 'count' and not isinstance(value, int): raise ValueError('Count query must return an integer')
        result['value'] = value
    elif p['returns'] == 'patient_set':
        result['patient_ids'] = sorted({r[0] for r in rows if r and r[0] is not None})
    else:
        result['rows'] = [dict(zip(columns, r)) for r in rows]
    return result
