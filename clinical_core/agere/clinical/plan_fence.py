"""PlanFence: validate derived work against its CURRENT premises before it is reused or acted on (DAIR review 2026-10-20
39,59-67; research report 2026-10-18 26,166).

A saved plan (a verified question -> SQL pair replayed by learned_verified_sql / semantic_cache, a bottled query distilled
from verified pairs, a paraphrase-reused contract) was right for the premises it was derived under. premises(graph) reads
what its executable text depends on; validate() re-checks each premise against the stack as it is now:
  schema        every table it reads still exists in the semantic layer, and every table.column it names still exists
  definitions   no clinical definition decided since supersedes it (clinical_definitions.superseded)
  corrections   no user correction of the same question family was recorded after the pair was saved
  context       the pair's saved data-release context equals the current one (learning.context)
  drift         no concept / field / metric it reads drifted in this release (release_drift.touching) when the pair was
                saved before the drift index was installed or carried forward from an earlier release
A failed premise rejects THAT reuse only (proposers.Cascade.graph -> reject; the next proposer re-derives the answer:
heal, never block) and the receipt names the premise. answer_dependencies.validate calls premise_issues() so saved
answers in workspaces are re-validated the same way at action time. Free, read-only.
"""
import json
import re
import sqlite3
import time
from functools import lru_cache
from pathlib import Path

VERSION = 'plan-fence/1'
FENCED = ('learned_verified_sql', 'semantic_cache', 'template_decoding', 'bottled_sql', 'paraphrase_reuse')
_TABLE = re.compile(r'\b(?:from|join)\s+"?([A-Za-z_][A-Za-z0-9_]*)"?', re.I)
_QUALIFIED = re.compile(r'\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b')
_CTE = re.compile(r'(?:\bwith\b|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(', re.I)

# Lex quoted identifiers/comments before looking inside their contents. Only
# a leading literal in the first argument of SQLite's JSON table functions is replaceable for
# dependency parsing; quoted table names and all executable SQL stay intact.
_DEPENDENCY_TOKENS = re.compile(
    r"(?P<json>\bjson_(?:each|tree)\s*\(\s*)"
    r"|(?P<string>'[^']*+(?:''[^']*+)*')"
    r'|(?P<identifier>"[^"]*+(?:""[^"]*+)*"|`[^`]*+(?:``[^`]*+)*`|\[[^\]]*\])'
    r"|(?P<comment>--[^\n]*|/\*.*?\*/)", re.I | re.S)


def _dependency_sql(sql):
    """Skip evidence JSON contents in a syntax-only dependency probe.

    Literal JSON cannot name a SQL table or column. Its original bytes remain
    in the executable query, receipt, cache key and definition checks. This
    avoids tokenizing millions of data characters as Python SQL string tokens.
    """
    pieces=[];copied=0;previous=0;first_argument=False
    for token in _DEPENDENCY_TOKENS.finditer(sql):
        if first_argument and sql[previous:token.start()].strip():first_argument=False
        kind=token.lastgroup
        if kind=='json':first_argument=True
        elif kind=='string':
            if first_argument:
                pieces.extend((sql[copied:token.start()],"''"));copied=token.end()
            first_argument=False
        elif kind!='comment':first_argument=False
        previous=token.end()
    return ''.join((*pieces,sql[copied:])) if pieces else sql


def _sqls(graph):
    out = []
    for n in (graph or {}).get('nodes') or []:
        p = n.get('parameters') if isinstance(n, dict) else None
        for k in ('query', 'membership_query', 'unknown_membership_query'):
            if isinstance(p, dict) and isinstance(p.get(k), str): out.append(p[k])
    return out


@lru_cache(maxsize=8)
def _parsed_dependencies(sqls):
    """Reuse pure SQL parsing, never current schema, source or acceptance checks.

    Keys are the complete, ordered executable SQL strings. Immutable results
    prevent a caller from changing another request's dependency set.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope
    tables, cols = set(), set()
    for sql in sqls:
        # A schema-qualified table is not a table named "main"; quoted CTEs,
        # nested aliases and the bound population relation are not missing feeds.
        tree=sqlglot.parse_one(_dependency_sql(sql),read='sqlite')
        for scope in traverse_scope(tree) or []:
            for _,(_,source) in scope.selected_sources.items():
                if isinstance(source,exp.Table) and isinstance(source.this,exp.Identifier) and source.name.lower()!='population':
                    tables.add(source.name.lower())
            for column in scope.columns:
                source=scope.sources.get(column.table)
                if isinstance(source,exp.Table) and source.name.lower() in tables:
                    cols.add((source.name.lower(),column.name.lower()))
    return tuple(sorted(tables)),tuple(sorted(cols))


def premises(graph):
    """-> {'tables': [...], 'columns': [(table, column)], 'text'} the executable plan depends on."""
    from .literal_check import executable_text
    tables,cols=_parsed_dependencies(tuple(_sqls(graph)))
    return {'tables':list(tables),'columns':list(cols),
            'text':executable_text([n.get('parameters')for n in (graph or{}).get('nodes')or[]])}


def _schema(path):
    target = Path(path) / 'semantic.sqlite3'
    if not target.exists(): return None
    db = sqlite3.connect(target.resolve().as_uri()+'?mode=ro', uri=True)
    try:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")]
        schema = {n.lower(): [r[1].lower() for r in db.execute('PRAGMA table_info("'+n.replace('"','""')+'")')] for n in names}
        # The investigator creates this registered documentation view per
        # connection. Its absence from persistent storage is not source drift;
        # exact columns remain a versioned application contract. Arbitrary
        # temporary/result tables are still rejected below.
        from .analyst_context import VIEW,COLUMNS
        schema[VIEW]=list(COLUMNS)
        if 'meta' not in schema: return schema
        day = db.execute("SELECT value FROM meta WHERE key='as_of'").fetchone()
    finally:
        db.close()
    if day:
        # Recreate exactly the registered owner views, not arbitrary missing
        # aliases. Their base relations/columns still have to compile.
        from .semantic_analyst import SemanticWarehouse
        wh = SemanticWarehouse(target)
        try:
            for name in wh.define(day[0]):
                try: cursor = wh.db.execute('SELECT * FROM "'+name.replace('"','""')+'" LIMIT 0')
                except sqlite3.Error: continue
                schema[name.lower()] = [c[0].lower() for c in cursor.description]
        finally:
            wh.close()
    return schema


def _saved_row(path, graph):
    """The verified-store row the replayed SQL came from ({'created', 'context', 'provenance', 'question'}), or None."""
    target = Path(path) / 'learning.sqlite3'
    sqls = _sqls(graph)
    if not target.exists() or not sqls: return None
    with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='verified'").fetchone(): return None
        cols = [r[1] for r in db.execute('PRAGMA table_info(verified)')]
        for row in db.execute('SELECT * FROM verified ORDER BY created DESC'):
            rec = dict(zip(cols, row))
            try: plan = json.loads(rec.get('plan') or '{}')
            except ValueError: continue
            if isinstance(plan, dict) and plan.get('query') and plan['query'].strip() == sqls[0].strip():
                return rec
    return None


def native_premise_graph(graph,calls):
    """Resolve only hash-bound, actually executed native query dependencies.

    This is syntax-only schema checking. Empty column projections stand in for
    immutable native rows; they never execute or replace captured observations.
    Every physical producer query is also checked against the current schema.
    Unknown names, changed statements and changed source receipts fail closed.
    """
    from .sql_query import native_query_binding_errors,portable_native_query
    errors=native_query_binding_errors(calls)
    if errors:return graph,['native: '+e['reason']for e in errors]
    from .sql_query import validate_bound_native_query
    from .backstop_agent import output_table
    native={};replacements={};producers=[]
    def columns(result):
        # Governed scalar metrics use value/unit rather than a columns list.
        # Match the exact projection used when retain_result materialized the
        # hash-bound native source, including its unchanged scalar contract.
        fields,_=output_table(result)
        if not fields or any(not isinstance(c,str)or not c for c in fields)or len(fields)!=len(set(fields)):
            raise ValueError('Native source has no complete unique column contract')
        return 'SELECT '+', '.join('NULL AS "'+c.replace('"','""')+'"'for c in fields)+' WHERE 0'
    for call in calls:
        result=call.get('result')or{}
        if not isinstance(result,dict)or result.get('error'):continue
        name=result.get('result_view')
        if not isinstance(name,str):continue
        if name in native:return graph,['native: Duplicate native result identity']
        if call.get('name')=='query_sql':
            query=result.get('executed_query')
            if not isinstance(query,str):return graph,['native: Executed query is unavailable']
            resolved=query
            if 'native_query_receipt'in result:
                if not validate_bound_native_query(result,native):return graph,['native: Native query binding changed']
                try:
                    sources={v:columns(native[v])for v in result['read_views']}
                    resolved=portable_native_query(query,sources,result['read_views'])
                except (ValueError,KeyError,TypeError):return graph,['native: Native source columns or bindings unavailable']
            replacements[query.strip()]=resolved
            producers.append(resolved)
        native[name]=result
    nodes=[]
    for node in(graph or{}).get('nodes')or[]:
        params=dict(node.get('parameters')or{})
        for key in('query','membership_query','unknown_membership_query'):
            if isinstance(params.get(key),str):params[key]=replacements.get(params[key].strip(),params[key])
        nodes.append({**node,'parameters':params})
    present=set(_sqls({'nodes':nodes}))
    for index,query in enumerate(producers):
        if query not in present:
            nodes.append({'id':'native_premise_'+str(index),'operation':'sql','parameters':{'query':query}});present.add(query)
    return {**(graph or{}),'nodes':nodes},[]


def native_claim_lookup_only(calls, answer, schema):
    """Classify the complete bound answer's provider-claim source role.

    This is a syntax/source-role check, not cash arithmetic, subject-link or
    clinical acceptance. The complete journal still receives schema/provenance
    checks. Unused exploratory reads cannot become the answer's definition.
    """
    from . import backstop_agent as B
    from .sql_query import portable_native_query,validate_bound_native_query,native_query_binding_errors
    if not isinstance(answer,dict)or not calls or native_query_binding_errors(calls):return False
    root=(answer.get('table_binding')or{}).get('result_view')or answer.get('table_from_tool')
    roots=list(dict.fromkeys([*([root]if root else[]),*B.computed_prose_views(answer.get('computed_prose')or[])]))
    selected={root:None}
    def address(binding):
        if not isinstance(binding,dict)or not isinstance(binding.get('result_view'),str):return
        view=binding['result_view'];field=binding.get('column')
        if not isinstance(field,str):selected[view]=None
        elif view not in selected:selected[view]={field}
        elif selected[view]is not None:selected[view].add(field)
    for proof in answer.get('computed_prose')or[]:
        for basis in proof.get('basis',[]):address(basis)
        for source in proof.get('sources',{}).values():
            binding=source.get('binding')or{};address(binding)
            measure=binding.get('measure')or{};address(measure);address(measure.get('population'))
    required=B.native_result_dependencies(calls,roots)
    if not root or not required:return False
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer import optimize
    from sqlglot.optimizer.scope import traverse_scope
    from .financial_accounts import mapping
    cash_streams={r['stream']for r in mapping()['transactions']if r['snapshot_field']=='payer_received_cents'}
    resolved={};native={}
    try:
        for call in calls:
            result=call.get('result')or{};name=result.get('result_view')
            if name not in required:continue
            if result.get('error'):return False
            if call.get('name')=='compose_table':
                from .result_assembly import source_query
                query=source_query({**native,name:result},name)
                if not query:return False
                assembly=result['assembly'];dependencies=[assembly['base_result_view'],*[j['result_view']for j in assembly.get('joins',[])]]
                query=portable_native_query(query,resolved,dependencies)
            else:
                if call.get('name')!='query_sql'or result.get('native_sql_contract')!=B.NATIVE_SQL_CONTRACT:return False
                query=result['executed_query']
                if 'native_query_receipt'in result:
                    if not validate_bound_native_query(result,native):return False
                    query=portable_native_query(query,resolved,result['read_views'])
            resolved[name]=query;native[name]=result
        if any(r not in resolved for r in roots):return False
        fields={'claim_id','patient_id','payer','service_date'}
        def bare(n):
            while isinstance(n,exp.Paren):n=n.this
            return n
        def atoms(n):
            n=bare(n)
            if isinstance(n,exp.Where):return atoms(n.this)
            if isinstance(n,exp.And):return atoms(n.this)+atoms(n.expression)
            return [n]if n is not None else[]
        def column(n,alias,name):return isinstance(n,exp.Column)and n.table.casefold()==alias.casefold()and n.name.casefold()==name
        def origin(scope,column,seen=frozenset()):
            key=(id(scope),column.sql())
            if key in seen:return None
            source=scope.sources.get(column.table)
            if isinstance(source,exp.Table):return scope,column
            select=getattr(source,'expression',None)
            if not isinstance(select,exp.Select):return None
            value=next((e.unalias()for e in select.selects if e.alias_or_name.casefold()==column.name.casefold()),None)
            if value is None:return None
            return origin(source,value,seen|{key})if isinstance(value,exp.Column)else(source,value)
        def payload_claim(scope,n,seen=frozenset()):
            n=bare(n)
            if isinstance(n,exp.Column):
                found=origin(scope,n)
                if found is None or(found[0]is scope and found[1]==n):return False
                key=(id(found[0]),found[1].sql())
                return False if key in seen else payload_claim(*found,seen|{key})
            if not isinstance(n,(exp.JSONExtract,exp.JSONExtractScalar))or not isinstance(n.this,exp.Column):return False
            path=n.args.get('expression')
            if not isinstance(path,exp.JSONPath)or [p.this for p in path.expressions if isinstance(p,exp.JSONPathKey)]!=['claim_id']:return False
            found=origin(scope,n.this)
            if found is None:return False
            base,payload=found
            if not isinstance(payload,exp.Column)or payload.name.casefold()!='payload':return False
            relation=base.sources.get(payload.table)
            if not isinstance(relation,exp.Table)or relation.name.casefold()not in('record','current_record'):return False
            mandatory=atoms(base.expression.args.get('where'))+[a for j in base.expression.args.get('joins')or[]for a in atoms(j.args.get('on'))]
            return any(isinstance(n,exp.EQ)and any(column(a,payload.table,'stream')and isinstance(b,exp.Literal)
                and b.is_string and b.this in cash_streams for a,b in[(n.this,n.expression),(n.expression,n.this)])for n in mandatory)
        def classification_count(column):
            aggregate=column.find_ancestor(exp.AggFunc)
            if aggregate is None:return True
            if not isinstance(aggregate,exp.Count):return False
            value=aggregate.this
            if isinstance(value,exp.Distinct):
                if len(value.expressions)!=1:return False
                value=value.expressions[0]
            if not isinstance(value,exp.Case):return False
            labels=[c.args.get('true')for c in value.args.get('ifs')or[]]+[value.args.get('default')]
            return bool(labels)and all(isinstance(v,exp.Literal)and v.is_string for v in labels)
        for root in roots:
            query=sqlglot.parse_one(resolved[root],read='sqlite')
            if selected.get(root):
                # This is only a definition probe over actual compiled scalar
                # addresses. All native rows/columns and physical producers
                # remain intact in the saved receipt and schema checks.
                query=exp.select(*[exp.column(c,quoted=True)for c in sorted(selected[root])]).from_(query.subquery('__definition_values'))
            tree=optimize(query,schema={t:{c:'UNKNOWN'for c in cols}for t,cols in schema.items()},dialect='sqlite')
            for scope in traverse_scope(tree)or[]:
                sources={a:s for a,(_,s)in scope.selected_sources.items()if isinstance(s,exp.Table)}
                if any(s.name.casefold()=='claim_adjustment'for s in sources.values()):return False
                claim_aliases=[a for a,s in sources.items()if s.name.casefold()=='claim']
                if not claim_aliases:continue
                select=scope.expression
                if not isinstance(select,exp.Select):return False
                mandatory=atoms(select.args.get('where'))+[a for j in select.args.get('joins')or[]for a in atoms(j.args.get('on'))]
                for alias in claim_aliases:
                    used=[c for c in scope.columns if c.table.casefold()==alias.casefold()]
                    if not used or any(c.name.casefold()not in fields for c in used):return False
                    # A provider claim count is a financial denominator, not
                    # an identity lookup, even beside an unrelated cash read.
                    if not all(classification_count(c)for c in used):return False
                    linked=any(isinstance(n,exp.EQ)and any(column(a,alias,'claim_id')and payload_claim(scope,b)
                        for a,b in[(n.this,n.expression),(n.expression,n.this)])for n in mandatory)
                    # A payer-key dimension may enumerate received labels. It
                    # cannot carry provider amounts, counts or other facts.
                    attribute_only=(len(scope.selected_sources)==1 and all(
                        isinstance(e.unalias(),exp.Column)and e.unalias().table.casefold()==alias.casefold()
                        for e in select.selects))
                    if not linked and not attribute_only:return False
        return True
    except (ValueError,KeyError,TypeError,AttributeError,RecursionError,sqlglot.errors.SqlglotError):return False


def definition_premise_issues(question, graph, schema, *, native_calls=None, answer=None):
    from .clinical_definitions import superseded
    plan={'query':'\n'.join(_sqls(graph))}
    retired=superseded(question,plan,schema)
    # The legacy JSON-text probe can miss a trailing FROM claim. Use actual
    # executable dependencies here, never source text inside data literals.
    provider_read=False
    if 'counts_in_metrics'in schema.get('payer_claim',[])and not re.search(r'\bprovider\b',str(question),re.I):
        try:provider_read=bool({'claim','claim_adjustment'}&set(premises(graph)['tables']))
        except Exception:pass  # no parser failure grants a role exception below
    if not retired and not provider_read:return []
    try:lookup_only=native_claim_lookup_only(native_calls,answer,schema)if native_calls is not None else False
    except Exception:lookup_only=False  # a failed role proof cannot waive the existing definition
    # Query plans invoke the owner provider-claim precedence rule. Only the
    # fully bound answer's classified lookup role refines that source-presence
    # check; unrelated retirements and ordinary saved plans remain strict.
    return []if provider_read and lookup_only else ['definitions: a clinical definition decided since supersedes this saved plan']


def premise_issues(path, question, graph, *, saved=None, schema=None,native_calls=None,answer=None):
    """Readable reasons the plan's premises no longer hold (empty list = current)."""
    issues = []
    effective=graph
    if native_calls is not None and isinstance(answer,dict):
        from .decision_context import population_scope_issues
        issues.extend('population: '+issue for issue in population_scope_issues(question,calls=native_calls,
            result_view=(answer.get('table_binding')or{}).get('result_view')or answer.get('table_from_tool')))
    if native_calls is not None:
        try:effective,native_issues=native_premise_graph(graph,native_calls)
        except (ValueError,KeyError,TypeError,AttributeError,RecursionError):
            native_issues=['native: Native result provenance could not be validated']
        issues.extend(native_issues)
    try:prem = premises(effective)
    except Exception:
        if native_calls is not None:return [*issues,'native: Executed dependency schema could not be resolved']
        raise
    schema = _schema(path) if schema is None else schema
    if schema is not None:
        gone = [t for t in prem['tables'] if t not in schema]
        if gone: issues.append('schema: table(s) ' + ', '.join(gone) + ' no longer exist in the semantic layer')
        lost = [f'{t}.{c}' for t, c in prem['columns'] if t in schema and c not in schema[t]]
        if lost: issues.append('schema: column(s) ' + ', '.join(lost[:6]) + ' no longer exist')
        try:
            issues.extend(definition_premise_issues(question,graph,schema,native_calls=native_calls,answer=answer))
        except Exception:  # noqa: BLE001 - no definitions module: nothing superseded
            pass
    if saved:
        try:
            from .learning import context
            if saved.get('context') and saved['context'] != context(path):
                issues.append('context: saved under data release / semantic layer ' + str(saved['context'])[:80] + ', not the current one')
        except Exception:  # noqa: BLE001 - no run.json (fixtures)
            pass
        created = float(saved.get('created') or 0)
        try:
            from .request_shapes import family
            target = Path(path) / 'learning.sqlite3'
            with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
                later = [q for q, c in db.execute('SELECT question, created FROM corrections') if (c or 0) > created and family(q) == family(question)]
            if later: issues.append(f'corrections: {len(later)} correction(s) of this question family were recorded after the pair was saved')
        except Exception:  # noqa: BLE001 - no corrections table
            pass
        from . import release_drift
        doc = release_drift.load(path)
        if doc:
            hits = release_drift.touching(path, prem['text'], None)
            installed = doc.get('generated_at') or ''
            saved_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(created)) if created else ''
            carried = bool(saved.get('provenance'))
            if hits and (carried or (saved_at and saved_at < installed)):
                issues.append('drift: ' + '; '.join(h['message'][:140] for h in hits[:2]) + ' (the pair predates this release)')
    return issues


def validate(stack, question, graph, receipt=None, *, tier=None):
    """-> {'version', 'tier', 'current', 'issues', 'premises': {'tables', 'columns'}, 'saved_at'}"""
    path = getattr(stack, 'path', None)
    saved = _saved_row(path, graph) if path and tier in ('learned_verified_sql', 'semantic_cache') else None
    issues = premise_issues(path, question, graph, saved=saved) if path else []
    prem = premises(graph)
    return {'version': VERSION, 'tier': tier, 'current': not issues, 'issues': issues,
            'premises': {'tables': prem['tables'], 'columns': [f'{t}.{c}' for t, c in prem['columns']][:40]},
            'saved_at': (saved or {}).get('created')}
