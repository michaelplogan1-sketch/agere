"""One source-bound request contract, compiled to the existing read-only SQL tool.

The interpreter proposes data, never executable SQL. Every executable fragment is
bound to literal request text. Boolean structure, clocks, grain and grouping live
in this contract; rendering, cache reuse and follow-ups cannot reinterpret them.
This proves preservation of the declared interpretation, not language or clinical
correctness. Model interpretations therefore require a separate review receipt.
"""
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
import hashlib
import json
import copy
import math
import re
import sqlite3

VERSION = 'warehouse-request/1'
STOP_WORDS = set('a an the please me our we us there are is was were be of for to from show give find get tell what which who how many number list count total sum average mean by per with where among patients patient that have has had whose in on at and or but not without excluding exclude only all any every each than then also do does it them those these their'.split())


class PlanError(ValueError):
    """An interpretation cannot be executed as a complete original request."""

class AmbiguousRequest(PlanError):
    """The user must choose a scope; a fallback model must not silently choose."""

def quote_text(value):
    """A requirement may be expressed in several discontiguous source spans."""
    return ' '.join(value) if isinstance(value, list) else value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', value):
        raise PlanError('Invalid warehouse identifier')
    return '"' + value + '"'


def literal(value):
    if value is None: return 'NULL'
    if isinstance(value, bool): return str(int(value))
    if isinstance(value, (int, float)) and math.isfinite(value): return str(value)
    if isinstance(value, str) and len(value) <= 500: return "'" + value.replace("'", "''") + "'"
    raise PlanError('A bounded scalar literal is required')


@dataclass
class Catalog:
    target: Path
    tables: dict
    identity: dict
    stream_fields: dict = dataclass_field(default_factory=dict)
    profile: dict = dataclass_field(default_factory=dict)

    @classmethod
    def load(cls, path):
        from .semantic_layer import ensure
        path = Path(path)
        target = ensure(path) if (path / 'run.json').exists() else path / 'semantic.sqlite3'
        with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
            names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name != 'meta' AND name NOT LIKE 'sqlite_%'")]
            tables = {t: [r[1] for r in db.execute('PRAGMA table_info(' + identifier(t) + ')')
                          if r[1] != 'age_latest'] for t in names}
            identity = dict(db.execute('SELECT key,value FROM meta'))
        if 'birth_date' in tables.get('patient', []):
            tables['patient'].append('age')
        profile_path = path/'source-profile.json'
        profile = json.loads(profile_path.read_text()) if profile_path.exists() else {}
        streams = ({s: list(v.get('fields', {})) for s, v in profile.get('streams', {}).items()}
                   if profile.get('source_digest') == identity.get('source_digest') else {})
        return cls(target, tables, identity, streams,
                   profile if profile.get('source_digest') == identity.get('source_digest') else {})

    def field(self, name):
        if not isinstance(name, str): raise PlanError('Explicit table.column required')
        bits = name.split('.')
        if len(bits) >= 3 and bits[0] in ('record', 'current_record') and bits[1] == 'payload':
            key = '.'.join(bits[2:])
            if all(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', p) for p in bits[2:]) and any(key in f for f in self.stream_fields.values()):
                return bits[0], 'payload.'+key
        if len(bits) != 2 or bits[0] not in self.tables or bits[1] not in self.tables[bits[0]]:
            raise PlanError(f'Field is not in the received warehouse catalog: {name}')
        return bits

    def prompt(self):
        from .semantic_layer import SCHEMA_DOC
        return '\n'.join(f'{t}({", ".join(cols)}) -- {SCHEMA_DOC.get(t, "received table")}' for t, cols in sorted(self.tables.items()))


class Compiler:
    """Relational algebra with semijoins: one-to-many filters never inflate totals."""
    def __init__(self, catalog, question, *, population=None):
        self.catalog, self.question, self.population = catalog, question, population
        self.bindings, self.joins, self.serial = [], {}, 0
        self.base = None
        self.outputs = {}
        self.json_streams = {}

    def bind(self, obj, path, kind):
        quotes = obj.get('quote')
        if not isinstance(quotes, list): quotes = [quotes]
        if not 1 <= len(quotes) <= 12:
            raise PlanError(f'{path}: 1..12 literal source fragments required')
        for quote in quotes:
            if not isinstance(quote, str) or not quote.strip() or quote not in self.question:
                raise PlanError(f'{path}: a literal fragment of the original question is required')
            if kind in ('population', 'measure') and re.search(
                    r'\b(with|without|where|whose|who|excluding|exclude|unless)\b', quote, re.I):
                raise PlanError(f'{path}: bind filter wording to its own predicate, not a broad source or measure quote')
            self.bindings.append({'path': path, 'kind': kind, 'quote': quote,
                                  'covers': not (kind == 'predicate' and obj.get('op') in ('and', 'or', 'not', 'exists') and
                                                 (obj.get('where') is not None or obj.get('args') or obj.get('arg'))),
                                  'disposition': 'unresolved' if kind == 'unresolved' else 'applied'})

    def field(self, name, scope):
        if not isinstance(name, str): raise PlanError('Explicit field name required')
        if name == 'patient.age':
            cutoff = self.catalog.identity.get('as_of')
            if not cutoff: raise PlanError('Age requires the warehouse as-of date')
            return f'age_at({self.field("patient.birth_date", scope)}, {literal(cutoff)})'
        if name.startswith('result.') and 'result' in scope:
            alias = name.partition('.')[2]
            if alias not in self.outputs: raise PlanError('Unknown aggregate output: '+alias)
            return self.outputs[alias]
        table, column = self.catalog.field(name)
        if column.startswith('payload.'):
            alias = scope.get(table)
            stream = self.json_streams.get(alias)
            key = column[len('payload.'):]
            if not stream or key not in self.catalog.stream_fields.get(stream, []):
                raise PlanError('Payload fields require an explicitly selected received source stream')
            return f'json_extract({alias}.payload, {literal("$."+key)})'
        if table in scope: return scope[table] + '.' + identifier(column)
        # Only the patient dimension can be implicitly joined. Other relationships
        # must be explicit EXISTS predicates; no guessed provider/claim joins.
        if table == 'patient' and 'patient_id' in self.catalog.tables[self.base]:
            if 'patient' not in self.joins:
                with sqlite3.connect(f'file:{self.catalog.target}?mode=ro', uri=True) as db:
                    duplicate = db.execute('SELECT patient_id FROM patient GROUP BY patient_id HAVING count(*)>1 LIMIT 1').fetchone()
                if duplicate: raise PlanError('Patient dimension is not unique; refusing a duplicating join')
                self.joins['patient'] = 'LEFT JOIN patient AS patient_dimension ON patient_dimension.patient_id = b.patient_id'
            return 'patient_dimension.' + identifier(column)
        raise PlanError(f'{name} is outside this relation; use an explicit related-record predicate')

    def predicate(self, expr, scope, path='where', depth=0):
        if depth > 12 or not isinstance(expr, dict): raise PlanError('Bounded predicate tree required')
        op = expr.get('op')
        self.bind(expr, path, 'predicate')
        if op == 'concept':
            from .request_vocabulary import concept_sql
            return concept_sql(expr, self, scope)
        if op in ('and', 'or'):
            if set(expr) != {'op', 'args', 'quote'} or not isinstance(expr['args'], list) or not 2 <= len(expr['args']) <= 24:
                raise PlanError('Boolean operations need 2..24 explicit operands')
            if op == 'or' and not re.search(r'\bor\b|/', quote_text(expr['quote']), re.I):
                raise PlanError('An OR requires its original disjunction')
            return '(' + (' AND ' if op == 'and' else ' OR ').join(self.predicate(x, scope, f'{path}.args.{i}', depth+1) for i, x in enumerate(expr['args'])) + ')'
        if op == 'not':
            if set(expr) != {'op', 'arg', 'quote'} or not re.search(r'\b(not|no|without|exclude|excluding|except|neither)\b', quote_text(expr['quote']), re.I):
                raise PlanError('Negation must be explicit in the original request')
            return '(NOT ' + self.predicate(expr['arg'], scope, path+'.arg', depth+1) + ')'
        if op == 'exists':
            if set(expr) - {'op', 'table', 'where', 'quote', 'link', 'stream'}: raise PlanError('Unknown related-record fields')
            table = expr.get('table')
            if table not in self.catalog.tables: raise PlanError(f'Unknown relation: {table}')
            self.serial += 1
            alias = 'r' + str(self.serial)
            # Same-patient EXISTS is safe across received domains. A named entity
            # key can instead bind a claim/encounter/record relationship.
            link = expr.get('link') or {'outer': self.base+'.patient_id', 'inner': table+'.patient_id'}
            if not isinstance(link, dict) or set(link) != {'outer', 'inner'}: raise PlanError('Explicit relation keys required')
            it, ic = self.catalog.field(link['inner'])
            ot, oc = self.catalog.field(link['outer'])
            if it != table or ic != oc or ic not in ('patient_id', 'claim_id', 'encounter_id', 'record_id', 'appointment_id'):
                raise PlanError('Unregistered relation: keys must name the same received entity')
            if ot not in scope: raise PlanError('Relation must bind an enclosing entity')
            outer = self.field(link['outer'], scope)
            nested = {**scope, table: alias}
            stream_condition = ''
            if expr.get('stream'):
                if table not in ('record', 'current_record') or expr['stream'] not in self.catalog.stream_fields:
                    raise PlanError('Unknown received source stream')
                self.json_streams[alias] = expr['stream']
                stream_condition = f' AND {alias}.stream = {literal(expr["stream"])}'
            condition = self.predicate(expr['where'], nested, path+'.where', depth+1) if expr.get('where') else '1'
            return f'EXISTS (SELECT 1 FROM {identifier(table)} AS {alias} WHERE {alias}.{identifier(ic)} = {outer}{stream_condition} AND {condition})'
        if set(expr) != {'op', 'field', 'value', 'quote'}: raise PlanError('Comparison needs field, operator, value and source text')
        field = self.field(expr['field'], scope)
        value = expr['value']
        if op in ('is_null', 'not_null'):
            if value is not None: raise PlanError('Null tests do not take a value')
            return field + (' IS NULL' if op == 'is_null' else ' IS NOT NULL')
        if op in ('in', 'not_in'):
            if not isinstance(value, list) or not 1 <= len(value) <= 100: raise PlanError('IN requires 1..100 literals')
            return field + (' NOT IN (' if op == 'not_in' else ' IN (') + ','.join(map(literal, value)) + ')'
        if op not in ('=', '!=', '>', '>=', '<', '<=', 'like'): raise PlanError(f'Unknown predicate operator: {op}')
        if value is None: raise PlanError('Use explicit null predicates, not equality to null')
        # Independently check literal comparisons rather than trusting a model's
        # statement that a threshold survived. Dates expressed as a whole year
        # legitimately compile into two endpoints and have no comparison cue.
        cue = re.search(r'(>=|<=|!=|(?<![<>!])=|>|<|\bat least\b|\bat most\b|\bover\b|\bunder\b|\bolder than\b|\byounger than\b|\bgreater than\b|\bless than\b)\s*["\']?([+-]?\d+(?:\.\d+)?)', quote_text(expr['quote']), re.I)
        if cue and isinstance(value, (int, float)):
            expected = {'at least': '>=', 'at most': '<=', 'over': '>', 'under': '<',
                        'older than': '>', 'younger than': '<', 'greater than': '>', 'less than': '<'}.get(cue[1].lower(), cue[1])
            if op != expected or float(cue[2]) != value:
                raise PlanError('Literal comparator/threshold differs from the original request')
        return f'({field} {op.upper()} {literal(value)})'

    def measure(self, expr, scope, path):
        if not isinstance(expr, dict): raise PlanError('Typed measure required')
        self.bind(expr, path, 'measure')
        kind = expr.get('kind')
        allowed = ({'kind', 'field', 'alias', 'quote', 'grain'} if kind == 'field' else
                   {'kind', 'alias', 'quote', 'numerator', 'denominator', 'scale'} if kind == 'ratio' else
                   {'kind', 'field', 'alias', 'quote', 'where', 'scale'})
        if set(expr) - allowed: raise PlanError('Unknown measure fields')
        if kind == 'ratio':
            if expr.get('scale', 1) not in (1, 100, 1000): raise PlanError('Ratio scale must be 1, 100 or 1000')
            n = self.measure(expr['numerator'], scope, path+'.numerator')
            d = self.measure(expr['denominator'], scope, path+'.denominator')
            return f'({expr.get("scale", 1)}.0 * ({n}) / NULLIF(({d}), 0))'
        field = self.field(expr['field'], scope) if expr.get('field') else '*'
        if kind == 'field':
            if field == '*': raise PlanError('Projection needs a field')
            grain = expr.get('grain')
            if grain not in (None, 'month', 'year'): raise PlanError('Date grain must be month or year')
            return f'substr({field},1,{7 if grain == "month" else 4})' if grain else field
        if kind not in ('count', 'count_distinct', 'sum', 'avg', 'min', 'max'): raise PlanError('Unregistered aggregation')
        if field == '*' and kind != 'count': raise PlanError('This aggregation needs a field')
        if expr.get('where'):
            condition = self.predicate(expr['where'], scope, path+'.where')
            field = f'CASE WHEN {condition} THEN {"1" if field == "*" else field} END'
        fn = 'count' if kind == 'count_distinct' else kind
        rendered = f'{fn.upper()}({"DISTINCT " if kind == "count_distinct" else ""}{field})'
        if 'scale' in expr:
            scale = expr['scale']
            if type(scale) not in (int, float) or not math.isfinite(scale) or not 0 < scale <= 1000:
                raise PlanError('Measure scale must be a finite positive number up to 1000')
            rendered = f'({literal(scale)} * {rendered})'
        return rendered

    def compile(self, plan):
        required = {'source', 'select', 'where', 'group_by', 'order_by', 'limit', 'unresolved'}
        if not isinstance(plan, dict) or set(plan) - {'analyses', 'joins', 'having', 'workflow'} != required: raise PlanError('Complete typed request fields required')
        if not isinstance(plan['source'], dict) or set(plan['source']) - {'stream'} != {'table', 'quote'}: raise PlanError('Explicit source relation required')
        self.base = plan['source']['table']
        if self.base not in self.catalog.tables: raise PlanError(f'Unknown source: {self.base}')
        self.bind(plan['source'], 'source', 'population')
        if plan['where'] is not None and not isinstance(plan['where'], dict): raise PlanError('where must be a predicate or null')
        if not isinstance(plan['unresolved'], list): raise PlanError('unresolved must be a list')
        stream = plan['source'].get('stream')
        if stream:
            if self.base not in ('record', 'current_record') or stream not in self.catalog.stream_fields:
                raise PlanError('Unknown received source stream')
            self.json_streams['b'] = stream
        scope = {self.base: 'b'}
        joins = plan.get('joins', [])
        if not isinstance(joins, list) or len(joins) > 6: raise PlanError('At most six explicit dimension joins')
        for i, join in enumerate(joins):
            if set(join) != {'table', 'left', 'right', 'kind', 'quote'} or join['kind'] not in ('left', 'inner'):
                raise PlanError('Explicit dimension join fields required')
            table = join['table']
            if table in scope: raise PlanError('Repeated table roles require an explicit supported relationship definition')
            rt, rc = self.catalog.field(join['right'])
            lt, lc = self.catalog.field(join['left'])
            if rt != table or lt not in scope or not rc.endswith('_id') or not lc.endswith('_id'):
                raise PlanError('Dimension joins require received entity identity keys')
            self.bind(join, f'joins.{i}', 'relationship')
            with sqlite3.connect(f'file:{self.catalog.target}?mode=ro', uri=True) as db:
                duplicate = db.execute(f'SELECT {identifier(rc)} FROM {identifier(table)} WHERE {identifier(rc)} IS NOT NULL GROUP BY {identifier(rc)} HAVING count(*)>1 LIMIT 1').fetchone()
            if duplicate: raise PlanError('A dimension join would duplicate source rows; use EXISTS or preaggregation')
            alias = 'd'+str(i)
            rendered = f'{join["kind"].upper()} JOIN {identifier(table)} AS {alias} ON {scope[lt]}.{identifier(lc)} = {alias}.{identifier(rc)}'
            scope[table] = alias
            self.joins[table] = rendered
        if not isinstance(plan['select'], list) or not 1 <= len(plan['select']) <= 24: raise PlanError('1..24 projections required')
        aliases, selections, projections = [], [], {}
        for i, expr in enumerate(plan['select']):
            alias = expr.get('alias')
            identifier(alias)
            if alias in aliases: raise PlanError('Duplicate output name')
            aliases.append(alias)
            rendered = self.measure(expr, scope, f'select.{i}')
            self.outputs[alias] = rendered
            selections.append(rendered + ' AS ' + identifier(alias))
            if expr['kind'] == 'field': projections[alias] = rendered
        groups = plan['group_by']
        if not isinstance(groups, list) or any(x not in projections for x in groups) or len(set(groups)) != len(groups):
            raise PlanError(f'group_by must contain only these projected field aliases: {list(projections)}; got {groups}')
        aggregate = len(projections) != len(aliases)
        if aggregate and set(groups) != set(projections): raise PlanError('Every projected dimension must be grouped')
        if not aggregate and groups: raise PlanError('Row lists do not take groupings')
        where = self.predicate(plan['where'], scope) if plan['where'] else '1'
        if stream: where = f'b.stream = {literal(stream)} AND ({where})'
        if self.population is not None:
            if 'patient_id' not in self.catalog.tables[self.base]: raise PlanError('This source cannot be scoped to the saved patient cohort')
            where = f'({where}) AND b.patient_id IN (SELECT patient_id FROM population)'
        from_clause = f' FROM {identifier(self.base)} AS b ' + ' '.join(self.joins.values())
        relation = from_clause + ' WHERE ' + where
        query = 'SELECT ' + ('DISTINCT ' if not aggregate else '') + ', '.join(selections) + relation
        if groups: query += ' GROUP BY ' + ','.join(projections[g] for g in groups)
        if plan.get('having'):
            if not aggregate: raise PlanError('Aggregate filters require an aggregate result')
            query += ' HAVING ' + self.predicate(plan['having'], {'result': 'result'}, 'having')
        if not isinstance(plan['order_by'], list) or len(plan['order_by']) > 8: raise PlanError('Bounded ordering required')
        orders = []
        for i, order in enumerate(plan['order_by']):
            if set(order) != {'alias', 'direction', 'quote'} or order['alias'] not in aliases or order['direction'] not in ('asc', 'desc'):
                raise PlanError('Ordering must use an output alias and asc/desc')
            self.bind(order, f'order_by.{i}', 'ordering')
            orders.append(identifier(order['alias']) + ' ' + order['direction'].upper())
        if orders: query += ' ORDER BY ' + ','.join(orders)
        limit = plan['limit']
        if limit is not None:
            if set(limit) != {'value', 'quote'} or type(limit['value']) is not int or not 1 <= limit['value'] <= 5000:
                raise PlanError('Limit must be 1..5000')
            self.bind(limit, 'limit', 'limit')
            query += ' LIMIT ' + str(limit['value'])
        for i, item in enumerate(plan['unresolved']):
            if set(item) != {'quote', 'reason'} or not isinstance(item['reason'], str) or not item['reason']:
                raise PlanError('Unresolved requirements need their literal text and reason')
            self.bind(item, f'unresolved.{i}', 'unresolved')
            self.bindings[-1]['disposition'] = 'unresolved'
        analyses = plan.get('analyses', [])
        if not isinstance(analyses, list) or len(analyses) > 4: raise PlanError('At most four analytical operations per request')
        if analyses:
            from .analysis_graph import OPERATIONS, validate
            from .graph_contracts import input_contract
            for i, item in enumerate(analyses):
                if set(item) != {'operation', 'parameters', 'quote'} or item['operation'] not in OPERATIONS:
                    raise PlanError('Use a registered analytical operation with exact parameters')
                self.bind(item, f'analyses.{i}', 'analysis')
                op = item['operation']
                lo, hi = input_contract(op)['count']
                if not lo <= 1 <= hi or op in ('sql', 'review', 'query_population'):
                    raise PlanError('This operator cannot consume the shared population or requires separately authorized model execution')
                validate({'interpretation': self.question[:2000], 'coverage': 'supported_observation', 'unmet_requirements': [],
                          'nodes': [{'id': 'p', 'operation': 'cohort', 'inputs': [], 'purpose': 'Input contract validation',
                                     'parameters': {'specification': {'all': [], 'any': [], 'exclude': []}}},
                                    {'id': 'a', 'operation': op, 'inputs': ['p'], 'parameters': item['parameters'], 'purpose': quote_text(item['quote'])}]},
                         self.catalog.identity.get('as_of', '2030-05-27'))
        from .workflow_contract import validate as validate_workflow
        output_names = [*aliases, *[f'analysis_{i}' for i in range(len(analyses))]]
        output_quotes = [b['quote'] for b in self.bindings if b['kind'] in ('measure', 'analysis')]
        for i, step in enumerate(validate_workflow(plan.get('workflow', []), self.question, outputs=output_names, output_quotes=output_quotes)):
            self.bind(step, f'workflow.{i}', 'workflow')
        if len(self.bindings) > 160: raise PlanError('Request is too large for one bounded plan')
        # Literal coverage is an omission check, not a semantic-equivalence proof.
        covered = [False] * len(self.question)
        for b in self.bindings:
            if not b['covers']:
                # A relation's name is structural; its filter words still need
                # their own leaf predicates. Never cover an entire parent span.
                obj = plan
                for part in b['path'].split('.'):
                    obj = obj[int(part)] if isinstance(obj, list) else obj.get(part, {})
                table_words = set(str(obj.get('table', '')).split('_')) if isinstance(obj, dict) else set()
                for match in re.finditer(re.escape(b['quote']), self.question):
                    for word in re.finditer(r'\w+', b['quote']):
                        if word.group().lower().rstrip('s') in table_words:
                            covered[match.start()+word.start():match.start()+word.end()] = [True] * len(word.group())
                continue
            for match in re.finditer(re.escape(b['quote']), self.question):
                covered[match.start():match.end()] = [True] * (match.end()-match.start())
        uncovered = [m.group() for m in re.finditer(r'[\w%]+', self.question)
                     if not all(covered[m.start():m.end()]) and m.group().lower() not in STOP_WORDS]
        if uncovered: raise PlanError('Unaccounted original request text: ' + ', '.join(uncovered))
        # Never accept a numeric/time literal that vanished during interpretation.
        numeric = set(re.findall(r'(?<!\w)\d+(?:\.\d+)?', self.question))
        rendered_numbers = set(re.findall(r'(?<!\w)\d+(?:\.\d+)?', query + json.dumps([a['parameters'] for a in analyses])))
        # Analytical contracts commonly express quoted percentages as fractions
        # (2% -> 0.02). Only numeric parameter values in that same bound analysis
        # may account for this conversion; prose/aliases cannot satisfy it.
        from decimal import Decimal
        def parameter_numbers(value):
            if isinstance(value, dict):
                return {n for child in value.values() for n in parameter_numbers(child)}
            if isinstance(value, list):
                return {n for child in value for n in parameter_numbers(child)}
            return {Decimal(str(value))} if type(value) in (int, float) else set()
        for analysis in analyses:
            values = parameter_numbers(analysis['parameters'])
            for match in re.finditer(r'(?<![\w.])(\d+(?:\.\d+)?)\s*%', quote_text(analysis['quote'])):
                if Decimal(match.group(1)) / 100 in values:
                    rendered_numbers.add(match.group(1))
        # Received semantic fields can encode a fixed window (readmitted_30d).
        # Count only referenced, real fields, never model-invented output aliases.
        for columns in self.catalog.tables.values():
            for column in columns:
                if identifier(column) in query:
                    rendered_numbers.update(re.findall(r'(?<!\w)\d+(?:\.\d+)?', column.replace('_', ' ')))
        unresolved_text = ' '.join(quote_text(x['quote']) for x in [*plan['unresolved'], *[s for s in plan.get('workflow', []) if s['unresolved']]])
        missing_numbers = numeric - rendered_numbers - set(re.findall(r'(?<!\w)\d+(?:\.\d+)?', unresolved_text))
        if missing_numbers: raise PlanError('Numeric constraints were not compiled: ' + ', '.join(sorted(missing_numbers)))
        membership = ('SELECT DISTINCT b.patient_id AS patient_id' + relation) if 'patient_id' in self.catalog.tables[self.base] else None
        returns = 'table' if groups or plan.get('having') or len(aliases) > 1 or not aggregate else 'count' if plan['select'][0]['kind'] in ('count', 'count_distinct') and not plan['select'][0].get('scale') else 'value'
        if not aggregate and aliases == ['patient_id']: returns = 'patient_set'
        # A limited patient list must not silently create work for the unlimited set.
        if returns == 'patient_set': membership = query
        elif membership and (plan.get('having') or limit) and groups:
            matches = ' AND '.join(f'sg.{identifier(g)} IS {projections[g]}' for g in groups)
            membership = f'WITH selected_groups AS ({query}) SELECT DISTINCT b.patient_id AS patient_id{relation} AND EXISTS (SELECT 1 FROM selected_groups AS sg WHERE {matches})'
        elif membership and plan.get('having'):
            membership = f'SELECT DISTINCT b.patient_id AS patient_id{relation} AND EXISTS ({query})'
        elif membership and limit and not aggregate:
            # A multi-column row list is actionable only when its own selected
            # rows explicitly contain patient IDs; never substitute all matches.
            if 'patient_id' not in aliases: membership = None
            else: membership = f'SELECT DISTINCT patient_id FROM ({query})'
        payload = {'version': VERSION, 'original_question': self.question, 'request': plan,
                   'source_identity': self.catalog.identity, 'bindings': self.bindings,
                   'query': query, 'membership_query': membership, 'returns': returns,
                   'unknown_membership_query': (f'SELECT DISTINCT b.patient_id AS patient_id{from_clause} WHERE ({where}) IS NULL'
                                                if 'patient_id' in self.catalog.tables[self.base] else None),
                   'population': sorted(self.population) if self.population is not None else None}
        if self.base == 'utilization_event':
            payload['coverage_query'] = ('SELECT b.sources, b.in_ehr, count(*) AS events' + relation +
                                         ' GROUP BY b.sources,b.in_ehr')
        return {**payload, 'id': fingerprint(payload)}


def compile_request(catalog, question, plan, *, population=None):
    return Compiler(catalog, question, population=population).compile(plan)


def normalize_wire(catalog, question, proposal):
    """Resolve wire omissions and unqualified identifiers, never rewrite meaning.

    No predicate, projection, threshold, boolean operator or unmet requirement is
    removed or invented. Literal quote text may change only in case.
    """
    plan = copy.deepcopy(proposal)
    for key, value in {'where': None, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}.items():
        plan.setdefault(key, value)
    base = (plan.get('source') or {}).get('table')
    def visit(value, table):
        if isinstance(value, list):
            for item in value: visit(item, table)
        elif isinstance(value, dict):
            if value.get('op') == 'exists': table = value.get('table')
            field = value.get('field')
            if isinstance(field, str) and '.' not in field:
                if field in catalog.tables.get(table, []): value['field'] = table+'.'+field
                elif field in catalog.tables.get('patient', []): value['field'] = 'patient.'+field
            if value.get('op') in ('is_null', 'not_null'): value.setdefault('value', None)
            quote = value.get('quote')
            if isinstance(quote, str) and quote not in question:
                matches = list(re.finditer(re.escape(quote), question, re.I))
                if len(matches) == 1: value['quote'] = matches[0].group()
            for child in value.values():
                if isinstance(child, (list, dict)): visit(child, table)
    visit(plan, base)
    return plan


def assert_sealed(contract):
    if fingerprint({k: v for k, v in contract.items() if k != 'id'}) != contract.get('id'):
        raise PlanError('Request contract changed after validation')
