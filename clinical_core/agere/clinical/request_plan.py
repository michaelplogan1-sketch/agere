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
import sys

VERSION = 'warehouse-request/1'
STOP_WORDS = set('a an the please me our we us there are is was were be of for to from show give find get tell what which who how many number list count total sum average mean by per with where among patients patient that have has had whose in on at and or but not without excluding exclude only all any every each than then also do does it them those these their overall s plus did see saw just'.split())  # 's' is the contraction in what's


class PlanError(ValueError):
    """An interpretation cannot be executed as a complete original request."""

class AmbiguousRequest(PlanError):
    """The user must choose a scope; a fallback model must not silently choose."""

# --- the question spec's two additions (docs/agere/QUESTION_SPEC.md): a `note` predicate and a single-patient `subject`. The compiler validates
# them and keeps their place in the SQL; the System One note reader and the chart reader run through this registry (builders A and C
# register `note_frames.evaluate`, `note_frames.answer_patient`, `chart_reader.resolve_subject`, `chart_reader.answer`; an unregistered one
# is imported lazily from its module, and a missing module leaves the note predicate unresolved, never guessed).
NOTE_FRAMES = ('condition', 'symptom', 'medication_event', 'procedure_test', 'finding_value', 'adverse_event', 'care_process',
               'social_need', 'function_cognition', 'preference_refusal', 'plan_followup', 'note_contradiction')
ASSERTION_STATUSES = ('documented', 'denied', 'possible', 'planned_not_done', 'historical', 'other_person', 'not_mentioned')
NOTE_COMBINE = ('any', 'latest', 'first', 'trend', 'conflict', 'all')
NOTE_SOURCES = ('clinical_note', 'scanned_document_text', 'hie_document')
EXECUTOR_HOMES = {'note_frames.evaluate': ('note_frames', 'evaluate'), 'note_frames.answer_patient': ('note_frames', 'answer_patient'),
                  'chart_reader.resolve_subject': ('chart_reader', 'resolve_subject'), 'chart_reader.answer': ('chart_reader', 'answer')}
EXECUTORS = {}


def register_executor(name, fn):
    """Register (or with fn=None remove) the callable that runs a note predicate or a patient subject."""
    if fn is None: EXECUTORS.pop(name, None)
    else: EXECUTORS[name] = fn
    return fn


def executor(name):
    """The registered callable, else the module attribute named in EXECUTOR_HOMES (lazy import), else None."""
    if name in EXECUTORS: return EXECUTORS[name]
    home = EXECUTOR_HOMES.get(name)
    if not home: return None
    import importlib
    try:
        return getattr(importlib.import_module(f'{__package__}.{home[0]}'), home[1], None)
    except ImportError:
        return None


def note_catalog():
    """-> (frames, statuses, combine, sources, details): the note_frames module's closed lists when it is present (details: frame ->
    {field: options}), else the contract's own lists."""
    try:
        from . import note_frames as N
    except ImportError:
        return NOTE_FRAMES, ASSERTION_STATUSES, NOTE_COMBINE, NOTE_SOURCES, {}
    frames = getattr(N, 'FRAMES', None)
    names = tuple(frames) if isinstance(frames, (dict, list, tuple, set)) and frames else NOTE_FRAMES
    details = {}
    if isinstance(frames, dict):
        for name, spec in frames.items():
            fields = spec.get('detail', spec.get('details')) if isinstance(spec, dict) else None
            if isinstance(fields, dict):
                details[name] = {k: tuple(v['options'] if isinstance(v, dict) and 'options' in v else v) for k, v in fields.items()
                                 if isinstance(v, (list, tuple, set)) or (isinstance(v, dict) and isinstance(v.get('options'), (list, tuple)))}
    def pick(attr, default):
        value = getattr(N, attr, None)
        return tuple(value) if isinstance(value, (list, tuple, set, dict)) and value else default
    return names, pick('STATUSES', ASSERTION_STATUSES), pick('COMBINE', NOTE_COMBINE), pick('SOURCES', NOTE_SOURCES), details


NOTE_PRED = re.compile(r"""note_pred\(((?:[A-Za-z_]\w*\.)?"?patient_id"?), '(n\d+)'\)""")


def quote_text(value):
    """A requirement may be expressed in several discontiguous source spans."""
    return ' '.join(value) if isinstance(value, list) else value


def without_request_scaffolding(text):
    """Ignore first-person request phrasing, preserving patient preferences."""
    return re.sub(r'\bI\s+(?:want|would\s+like)\s+you\s+to\s+', '', text, flags=re.I)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', value):
        raise PlanError('Invalid warehouse identifier')
    return '"' + value + '"'


def warehouse_metadata(target, db):
    """Read adapter metadata when present; fingerprint an ordinary SQLite snapshot.

    A received warehouse need not contain Agere's clinical tables or metadata.
    Without adapter metadata there is no inferred business as-of date. Main-file
    and WAL identity invalidate plans when the received snapshot changes.
    """
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='meta' AND type IN ('table','view')").fetchone():
        columns = {r[1] for r in db.execute('PRAGMA table_info(meta)')}
        if {'key', 'value'} <= columns:
            return dict(db.execute('SELECT key,value FROM meta'))
    target = Path(target)
    files = {}
    for file in (target, Path(str(target) + '-wal')):
        try:
            st = file.stat()
            files[file.name] = {'size': st.st_size, 'mtime_ns': st.st_mtime_ns}
        except FileNotFoundError:
            pass
    return {'source_digest': 'sqlite-snapshot:' + fingerprint({'path': str(target.resolve()), 'files': files}),
            'version': 'received-sqlite/1', 'as_of': None}


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
            identity = warehouse_metadata(target, db)
        if 'birth_date' in tables.get('patient', []):
            tables['patient'].append('age')
        if 'sdoh_vendor' in tables and 'sdoh_latest' not in tables:
            tables['sdoh_latest'] = list(tables['sdoh_vendor'])  # the TEMP VIEW sql_query registers (semantic_layer.ensure_sdoh_latest_view)
        if {'pcp_practice_id'} <= set(tables.get('patient', [])) and {'patient_id', 'attribution_practice_id', 'coverage_start'} <= set(tables.get('enrollment_span', [])):
            tables['patient'].append('practice')  # virtual: the PCP practice, else the practice attributed on the latest enrollment span
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
        self.notes, self.rescaled = [], set()
        self.ratio_parts = {}  # select path -> (numerator SQL, denominator SQL) of each ratio measure
        self.conditions = {}  # measure path -> its conditional (numerator) predicate
        self.note_predicates = {}  # id -> the validated note predicate; the SQL holds note_pred(patient_id, id) until a reader fills it
        self.subject = None  # the named patient of a single-patient question

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
            reference = literal(cutoff)
            if {'patient_id', 'death_date'} <= set(self.catalog.tables.get('death', [])):
                # Mike, 2026-10-21: a deceased patient's age is their age at death; living patients age to the snapshot.
                pid = self.field('patient.patient_id', scope)
                reference = (f'COALESCE((SELECT MIN(age_death.death_date) FROM death AS age_death WHERE age_death.patient_id = {pid} '
                             f'AND age_death.death_date <= {literal(cutoff)}), {literal(cutoff)})')
            return f'age_at({self.field("patient.birth_date", scope)}, {reference})'
        if name == 'patient.practice':
            pid = self.field('patient.patient_id', scope)
            return (f'COALESCE({self.field("patient.pcp_practice_id", scope)}, (SELECT e_att.attribution_practice_id FROM enrollment_span AS e_att '
                    f'WHERE e_att.patient_id = {pid} AND e_att.attribution_practice_id IS NOT NULL ORDER BY e_att.coverage_start DESC LIMIT 1))')
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
        if op == 'note':
            return self.note(expr, scope, path)
        if op in ('and', 'or'):
            if set(expr) != {'op', 'args', 'quote'} or not isinstance(expr['args'], list) or not 2 <= len(expr['args']) <= 24:
                raise PlanError('Boolean operations need 2..24 explicit operands')
            if op == 'or' and not re.search(r'\bor\b|/', quote_text(expr['quote']), re.I):
                raise PlanError('An OR requires its original disjunction')
            return '(' + (' AND ' if op == 'and' else ' OR ').join(self.predicate(x, scope, f'{path}.args.{i}', depth+1) for i, x in enumerate(expr['args'])) + ')'
        if op == 'not':
            if set(expr) != {'op', 'arg', 'quote'} or not re.search(r"\b(?:not|no|without|exclude|excluding|except|neither|nor|never|missing|missed|living|alive|didn)\b|n't\b", quote_text(expr['quote']), re.I):
                raise PlanError('Negation must be explicit in the original request')
            return '(NOT ' + self.predicate(expr['arg'], scope, path+'.arg', depth+1) + ')'
        if op == 'sequence':
            return self.sequence(expr, scope, path, depth)
        if op == 'count_at_least':
            # At least N related events for the same patient (episodes for relations whose rows are observations).
            if set(expr) != {'op', 'table', 'where', 'n', 'quote'} or type(expr['n']) is not int or not 1 <= expr['n'] <= 10000:
                raise PlanError('An event-count threshold needs a relation, a condition and a whole-number N')
            table = expr['table']
            if table not in self.catalog.tables or 'patient_id' not in self.catalog.tables[table]: raise PlanError(f'Unknown relation: {table}')
            self.serial += 1
            alias = 'k' + str(self.serial)
            nested = {**scope, table: alias}
            condition = self.predicate(expr['where'], nested, path + '.where', depth + 1) if expr.get('where') else '1'
            unit = ("DISTINCT " + " || '|' || ".join(f'{alias}.{identifier(c)}' for c in EPISODE_KEYS[table])) if table in EPISODE_KEYS else '*'
            outer = self.field(self.base + '.patient_id', scope)
            return f'((SELECT COUNT({unit}) FROM {identifier(table)} AS {alias} WHERE {alias}.patient_id = {outer} AND {condition}) >= {expr["n"]})'
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
        if op == 'within_last_months':
            # A trailing window ending at the snapshot date: (as_of - N months, as_of].
            if type(value) is not int or not 1 <= value <= 240: raise PlanError('A trailing window needs 1..240 months')
            as_of = self.catalog.identity['as_of']
            return f"({field} > date({literal(as_of)}, {literal('-' + str(value) + ' months')}) AND {field} <= {literal(as_of)})"
        if op not in ('=', '!=', '>', '>=', '<', '<=', 'like'): raise PlanError(f'Unknown predicate operator: {op}')
        if value is None: raise PlanError('Use explicit null predicates, not equality to null')
        # Independently check literal comparisons rather than trusting a model's
        # statement that a threshold survived. Dates expressed as a whole year
        # legitimately compile into two endpoints and have no comparison cue.
        cue = re.search(r'(>=|<=|!=|(?<![<>!])=|>|<|\bat least\b|\bat most\b|\bno more than\b|\bno less than\b|\bmore than\b|\bfewer than\b|\bover\b|\bunder\b|\bolder than\b|\byounger than\b|\bgreater than\b|\bless than\b)\s*["\']?([+-]?\d+(?:\.\d+)?)', quote_text(expr['quote']), re.I)
        if cue and isinstance(value, (int, float)):
            expected = {'at least': '>=', 'at most': '<=', 'over': '>', 'under': '<',
                        'older than': '>', 'younger than': '<', 'greater than': '>', 'less than': '<',
                        'no more than':'<=','no less than':'>=','more than':'>','fewer than':'<'}.get(cue[1].lower(), cue[1])
            if op != expected or float(cue[2]) != value:
                raise PlanError('Literal comparator/threshold differs from the original request')
        if op in ('>', '>=', '<', '<=') and type(value) in (int, float):
            value = self.percent_scale(expr['field'], value)
        return f'({field} {op.upper()} {literal(value)})'

    def note(self, expr, scope, path):
        """A System One note predicate (docs/agere/QUESTION_SPEC.md, Addition 1). Validated here; the SQL holds a three-valued
        note_pred(patient_id, id) that the note reader fills at execution (spec_exec): true, false, or NULL for an undecided patient."""
        from datetime import date
        frames, statuses, combines, sources, details = note_catalog()
        required = {'op', 'frame', 'concept', 'statuses', 'quote'}
        if not required <= set(expr) or set(expr) - required - {'detail', 'window', 'combine', 'sources'}:
            raise PlanError('A note predicate needs op, frame, concept, statuses and quote (optional detail, window, combine, sources)')
        if expr['frame'] not in frames: raise PlanError(f'Unknown note frame: {expr["frame"]}')
        concept = expr['concept']
        if (not isinstance(concept, dict) or set(concept) - {'term', 'codes', 'quote'} or not isinstance(concept.get('term'), str) or not concept['term'].strip()
                or not isinstance(concept.get('codes', []), list) or any(not isinstance(c, str) for c in concept.get('codes', []))):
            raise PlanError('A note concept needs a term, optional codes and the question wording')
        if concept.get('quote') is not None: self.bind({'quote': concept['quote']}, path + '.concept', 'predicate')
        wanted = expr['statuses']
        if not isinstance(wanted, list) or not 1 <= len(wanted) <= len(statuses) or len(set(wanted)) != len(wanted) or any(x not in statuses for x in wanted):
            raise PlanError('Note statuses must be distinct values of: ' + ', '.join(statuses))
        detail = expr.get('detail')
        if detail is not None:
            if not isinstance(detail, dict): raise PlanError('Note detail must be an object')
            allowed = details.get(expr['frame'])
            for key, value in detail.items():
                values = value if isinstance(value, list) else [value]
                if allowed is not None and (key not in allowed or any(v not in allowed[key] for v in values)):
                    raise PlanError(f'Note detail {key} is not a declared option of the {expr["frame"]} frame')
        if expr.get('combine', 'any') not in combines: raise PlanError('Unknown note combine: ' + str(expr.get('combine')))
        given = expr.get('sources')
        if given is not None and (not isinstance(given, list) or not given or any(x not in sources for x in given)):
            raise PlanError('Note sources must be among: ' + ', '.join(sources))
        window = expr.get('window')
        if window is not None:
            if not isinstance(window, dict) or set(window) - {'start', 'end', 'quote'} or not {'start', 'end'} <= set(window):
                raise PlanError('A note window needs start, end and the question wording')
            try:
                start, end = (date.fromisoformat(window[k]).isoformat() for k in ('start', 'end'))
            except (ValueError, TypeError) as exc:
                raise PlanError('Invalid note window') from exc
            as_of = self.catalog.identity.get('as_of')
            if start > end or (as_of and end > str(as_of)[:10]): raise PlanError('A note window must end by the snapshot date')
            if window.get('quote') is not None: self.bind({'quote': window['quote']}, path + '.window', 'predicate')
        if self.base is None or 'patient_id' not in self.catalog.tables.get(self.base, []) or self.base not in scope:
            raise PlanError('A note predicate needs a patient-keyed source')
        validate = getattr(sys.modules.get(__package__ + '.note_frames'), 'validate_predicate', None)
        if validate is not None:  # the note reader's own contract check (builder A)
            try: validate(expr)
            except ValueError as exc: raise PlanError(f'Note predicate rejected by the note reader: {exc}') from exc
        self.serial += 1
        ident = 'n' + str(self.serial)
        self.note_predicates[ident] = copy.deepcopy(dict(expr))
        return f'(note_pred({self.field(self.base + ".patient_id", scope)}, {literal(ident)}) = 1)'

    def sequence(self, expr, scope, path, depth):
        """A later (or earlier) event for the same patient within a day window of an anchor event.

        anchor is another received event {table, date, where} or {'outer': field} for the counted row itself
        (e.g. each discharge). days = [low, high] is then.date minus anchor date: after = [1, N], before = [-N, 0].
        """
        if set(expr) - {'window'} != {'op', 'anchor', 'then', 'days', 'quote'}: raise PlanError('Sequence needs anchor, then, days and source text')
        if 'window' in expr:  # a window stated in weeks/months/years, compiled to whole days and disclosed
            w = expr['window']
            units = {'week': 7, 'month': 30, 'year': 365}
            if (not isinstance(w, dict) or set(w) != {'n', 'unit'} or type(w['n']) is not int or w['unit'] not in units
                    or w['n'] * units[w['unit']] not in [abs(d) for d in expr['days']]):
                raise PlanError('A sequence window in weeks/months/years must match its day bounds')
            if str(w['n']) not in self.rescaled:
                self.rescaled.add(str(w['n']))
                self.notes.append(f"{w['n']} {w['unit']}(s) compiled as {w['n'] * units[w['unit']]} days ({units[w['unit']]} days per {w['unit']}).")
            expr = {k: v for k, v in expr.items() if k != 'window'}
        if isinstance(expr['then'], dict) and set(expr['then']) == {'any'}:
            # One event received from several sources (an EHR visit or a claim line): any of them satisfies it.
            options = expr['then']['any']
            if not isinstance(options, list) or not 1 <= len(options) <= 4: raise PlanError('A sequence event needs 1..4 sources')
            return '(' + ' OR '.join(self.sequence({**expr, 'then': o}, scope, f'{path}.then.{i}', depth)
                                     for i, o in enumerate(options)) + ')'
        days = expr['days']
        if (not isinstance(days, list) or len(days) != 2 or any(type(d) is not int for d in days)
                or not -3650 <= days[0] <= days[1] <= 3650):
            raise PlanError('Sequence window needs two whole-day bounds')
        then, anchor = expr['then'], expr['anchor']
        def event(e, role):
            if not isinstance(e, dict) or set(e) - {'table', 'date', 'where', *(('rank',) if role == 'anchor' else ())} or e.get('table') not in self.catalog.tables:
                raise PlanError(f'Sequence {role} needs a received table')
            if e['date'] not in self.catalog.tables[e['table']] or 'patient_id' not in self.catalog.tables[e['table']]:
                raise PlanError(f'Sequence {role} needs a dated patient event')
        event(then, 'event')
        self.serial += 1
        t_alias = 's' + str(self.serial)
        outer = self.field(self.base + '.patient_id', scope)
        nested = {**scope}
        joins = ''
        if set(anchor) == {'outer'}:
            anchor_date = self.field(anchor['outer'], scope)
            if then['table'] in scope: raise PlanError('A sequence over the same relation as its anchor needs an episode definition')
            conditions = []
        else:
            event(anchor, 'anchor')
            if anchor['table'] == then['table'] or anchor['table'] in scope or then['table'] in scope:
                raise PlanError('A sequence over the same relation needs an episode definition')
            self.serial += 1
            a_alias = 'a' + str(self.serial)
            nested[anchor['table']] = a_alias
            joins = f' JOIN {identifier(anchor["table"])} AS {a_alias} ON {a_alias}.patient_id = {t_alias}.patient_id'
            anchor_date = f'{a_alias}.{identifier(anchor["date"])}'
            conditions = [self.predicate(anchor['where'], {**nested, then['table']: t_alias}, path + '.anchor', depth + 1)] if anchor.get('where') else []
            if anchor.get('rank') is not None:  # "their first / most recent <event>": the patient's earliest / latest qualifying one
                if anchor['rank'] not in ('first', 'last'): raise PlanError('Anchor rank is first or last')
                self.serial += 1
                f_alias = 'f' + str(self.serial)
                inner = self.predicate(anchor['where'], {**scope, anchor['table']: f_alias}, path + '.anchor_rank', depth + 1) if anchor.get('where') else '1'
                pick = 'MIN' if anchor['rank'] == 'first' else 'MAX'
                joins += (f' JOIN (SELECT {f_alias}.patient_id AS pid, {pick}({f_alias}.{identifier(anchor["date"])}) AS d FROM '
                          f'{identifier(anchor["table"])} AS {f_alias} WHERE {inner} GROUP BY {f_alias}.patient_id) AS {f_alias}r '
                          f'ON {f_alias}r.pid = {a_alias}.patient_id AND {anchor_date} = {f_alias}r.d')
        nested[then['table']] = t_alias
        if then.get('where'): conditions.append(self.predicate(then['where'], nested, path + '.then', depth + 1))
        gap = f'(julianday({t_alias}.{identifier(then["date"])}) - julianday({anchor_date}))'
        conditions.append(f'{gap} BETWEEN {days[0]} AND {days[1]}')
        return (f'EXISTS (SELECT 1 FROM {identifier(then["table"])} AS {t_alias}{joins} WHERE {t_alias}.patient_id = {outer} AND '
                + ' AND '.join(conditions) + ')')

    def bind_subject(self, subject):
        """A single-patient question (docs/agere/QUESTION_SPEC.md, Addition 2): every patient-keyed relation binds this patient_id."""
        if (not isinstance(subject, dict) or set(subject) != {'kind', 'patient_id', 'quote'} or subject['kind'] != 'patient'
                or not isinstance(subject['patient_id'], str) or not subject['patient_id'].strip()):
            raise PlanError('A subject names one patient: kind patient, patient_id and the question wording')
        if 'patient_id' not in self.catalog.tables.get(self.base, []): raise PlanError('This source cannot be bound to one patient')
        table = 'patient' if 'patient' in self.catalog.tables else self.base
        try:
            with sqlite3.connect(f'file:{self.catalog.target}?mode=ro', uri=True) as db:
                found = db.execute(f'SELECT 1 FROM {identifier(table)} WHERE patient_id = ? LIMIT 1', (subject['patient_id'],)).fetchone()
        except sqlite3.Error as exc:
            raise PlanError('The named patient could not be looked up') from exc
        if not found: raise PlanError('The named patient id is not in the received warehouse')
        self.bind(subject, 'subject', 'predicate')
        self.subject = dict(subject)

    def percent_scale(self, name, value):
        """A fractional threshold (0.75) on a percentile column stored on a 0-100 scale means 75."""
        if not 0 < value <= 1: return value
        try:
            table, column = self.catalog.field(name)
        except (PlanError, ValueError, KeyError, TypeError):
            return value  # an output alias or derived measure, not a stored column
        if not 0 < value <= 1 or not re.search(r'svi|percentile|pct|percent', column, re.I): return value
        try:
            with sqlite3.connect(f'file:{self.catalog.target}?mode=ro', uri=True) as db:
                low, high = db.execute(f'SELECT MIN({identifier(column)}), MAX({identifier(column)}) FROM {identifier(table)}').fetchone()
        except sqlite3.Error:
            return value
        if low is None or not (0 <= low and 1 < high <= 100): return value
        scaled = round(value * 100, 6)
        self.rescaled.add(format(value, 'g')); self.rescaled.add(str(value))
        self.notes.append(f'{table}.{column} is stored on a 0-100 scale (observed {low}-{high}); the threshold {value} was read as {scaled:g}.')
        return scaled

    def measure(self, expr, scope, path):
        if not isinstance(expr, dict): raise PlanError('Typed measure required')
        self.bind(expr, path, 'measure')
        kind = expr.get('kind')
        if kind == 'note_answer':
            if set(expr) - {'kind', 'alias', 'quote'} or self.subject is None:
                raise PlanError('A note answer is read for one named patient (subject) and takes only kind, alias and quote')
            return '1'
        allowed = ({'kind', 'field', 'alias', 'quote', 'grain'} if kind == 'field' else
                   {'kind', 'alias', 'quote', 'numerator', 'denominator', 'scale', 'round'} if kind == 'ratio' else
                   {'kind', 'field', 'alias', 'quote', 'where', 'scale', 'round'})
        if set(expr) - allowed: raise PlanError('Unknown measure fields')
        if kind == 'ratio':
            if expr.get('scale', 1) not in (1, 100, 1000): raise PlanError('Ratio scale must be 1, 100 or 1000')
            n = self.measure(expr['numerator'], scope, path+'.numerator')
            d = self.measure(expr['denominator'], scope, path+'.denominator')
            self.ratio_parts[path] = (n, d)
            ratio = f'({expr.get("scale", 1)}.0 * ({n}) / NULLIF(({d}), 0))'
            return self.rounded(ratio, expr)
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
            self.conditions[path] = condition
        if kind == 'count' and expr.get('field') in (None, '*') and self.base in EPISODE_KEYS and self.base in scope:
            # One admission arrives as several source rows (ADT, encounter, claim); count the episode once.
            key = " || '|' || ".join(f'{scope[self.base]}.{identifier(c)}' for c in EPISODE_KEYS[self.base])
            field = f'CASE WHEN {condition} THEN {key} END' if expr.get('where') else key
            kind = 'count_distinct'
        fn = 'count' if kind == 'count_distinct' else kind
        rendered = f'{fn.upper()}({"DISTINCT " if kind == "count_distinct" else ""}{field})'
        if 'scale' in expr:
            scale = expr['scale']
            if type(scale) not in (int, float) or not math.isfinite(scale) or not 0 < scale <= 1000:
                raise PlanError('Measure scale must be a finite positive number up to 1000')
            rendered = f'({literal(scale)} * {rendered})'
        return self.rounded(rendered, expr)

    def rounded(self, rendered, expr):
        """ROUND(x, n) when the measure states a rounding (money to cents, shares to a decimal): display only, never a threshold."""
        if 'round' not in expr: return rendered
        if type(expr['round']) is not int or not 0 <= expr['round'] <= 6: raise PlanError('Rounding is 0..6 decimal places')
        return f'ROUND({rendered}, {expr["round"]})'

    def living_filter(self, plan, where):
        """The decision context's living-patient rule in the compiled SQL (decision_context.py): a request that counts or lists patients
        reads living patients only, unless the question asks for the deceased or the request already reads `death`. None otherwise."""
        from . import decision_context
        if self.subject is not None: return None  # a named patient is answered whether or not they died (their answer says so)
        if self.population is not None or 'death' not in self.catalog.tables: return None
        if 'patient_id' not in self.catalog.tables.get(self.base, []) or self.base == 'death': return None
        if not decision_context.population(self.question)['living_only']: return None
        # a request that already reads `death` (the care-gap clause, "living") states the living half itself; each person still counts once
        # (b3-04: the care-gap clause alone left second charts in, 211 vs 206)
        own_death = decision_context.references_death(where)
        ends = lambda e: str(e.get('field') or '').endswith('.patient_id') or str(e.get('field') or '') == 'patient_id'
        kinds = [e for e in plan['select'] if isinstance(e, dict)]
        # an event relation counted for a population (appointments, visits, stays) is a patient-level reading too: the answer's own
        # defaults say "living patients", so the rows of a deceased patient are not counted (b3-29: no-shows by practice)
        patients = self.base == 'patient' or self.base in decision_context.EVENT_GRAIN_TABLES or any(
            (e.get('kind') in ('field', 'count_distinct') and ends(e)) or (e.get('kind') == 'ratio' and any(
                isinstance(x, dict) and x.get('kind') == 'count_distinct' and ends(x) for x in (e.get('numerator'), e.get('denominator'))))
            for e in kinds)
        if not patients: return None
        has_segment = 'data_segment' in self.catalog.tables.get('patient', [])
        if own_death:
            return (f"b.patient_id NOT IN (SELECT patient_id FROM patient WHERE data_segment = '{decision_context.DUPLICATE_SEGMENT}')"
                    if has_segment and decision_context.exclude_duplicate_charts() else None)
        return f'b.patient_id {decision_context.counted_predicate(has_segment)}'

    def compile(self, plan):
        required = {'source', 'select', 'where', 'group_by', 'order_by', 'limit', 'unresolved'}
        if not isinstance(plan, dict) or set(plan) - {'analyses', 'joins', 'having', 'workflow', 'notes', 'subject'} != required: raise PlanError('Complete typed request fields required')
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
        if plan.get('subject') is not None: self.bind_subject(plan['subject'])
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
        if plan.get('notes') is not None:  # definition notes the grammar attaches (shown with the answer's limitations)
            if not isinstance(plan['notes'], list) or any(not isinstance(n, str) or len(n) > 400 for n in plan['notes']): raise PlanError('Notes are short text')
            self.notes.extend(plan['notes'])
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
        living = self.living_filter(plan, where)
        if living: where = f'({where}) AND {living}'
        if self.subject: where = f'({where}) AND b.patient_id = {literal(self.subject["patient_id"])}'
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
        if orders and aggregate:  # ties break by the grouping dimensions, so a top-N cut at a tie is the same rows every run
            ordered = {order['alias'] for order in plan['order_by']}
            orders += [identifier(g) + ' ASC' for g in groups if g not in ordered]
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
        # A number joined to letters by a hyphen is part of a name or code (PHQ-9, COVID-19), not a constraint.
        grouped = re.findall(r'(?<![\w,.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])', self.question)  # 50,000 is one number, written with thousands separators
        numeric = set(re.findall(r'(?<!\w)(?<![A-Za-z]-)\d+(?:\.\d+)?', re.sub(r'(?<![\w,.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])', ' ', self.question)))
        numeric |= {g.replace(',', '') for g in grouped}
        rendered_numbers = set(re.findall(r'(?<!\w)\d+(?:\.\d+)?', query + json.dumps([a['parameters'] for a in analyses]) + json.dumps(self.note_predicates)))
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
            # A number in a governed measure's own identity (readmission_30d) is that measure's fixed window.
            if analysis.get('operation') == 'governed_metric':
                rendered_numbers.update(re.findall(r'(?<!\w)\d+(?:\.\d+)?', str(analysis['parameters'].get('id', '')).replace('_', ' ')))
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
        rendered_numbers.update(self.rescaled)  # compiled as their 0-100 equivalent, disclosed in notes
        unresolved_text = ' '.join(quote_text(x['quote']) for x in [*plan['unresolved'], *[s for s in plan.get('workflow', []) if s['unresolved']]])
        missing_numbers = numeric - rendered_numbers - set(re.findall(r'(?<!\w)\d+(?:\.\d+)?', unresolved_text))
        if missing_numbers: raise PlanError('Numeric constraints were not compiled: ' + ', '.join(sorted(missing_numbers)))
        membership = ('SELECT DISTINCT b.patient_id AS patient_id' + relation) if 'patient_id' in self.catalog.tables[self.base] else None
        returns = 'table' if groups or plan.get('having') or len(aliases) > 1 or not aggregate else 'count' if plan['select'][0]['kind'] in ('count', 'count_distinct') and not plan['select'][0].get('scale') else 'value'
        if not aggregate and aliases == ['patient_id']: returns = 'patient_set'
        if any(e['kind'] == 'note_answer' for e in plan['select']):
            if len(plan['select']) != 1 or groups or plan.get('having'): raise PlanError('A note answer is the only output of a single-patient question')
            returns = 'note_answer'
        # A limited patient list must not silently create work for the unlimited set.
        if returns == 'patient_set': membership = query
        elif membership and (plan.get('having') or limit) and groups:
            matches = ' AND '.join(f'sg.{identifier(g)} IS {projections[g]}' for g in groups)
            membership = f'WITH selected_groups AS MATERIALIZED ({query}) SELECT DISTINCT b.patient_id AS patient_id{relation} AND EXISTS (SELECT 1 FROM selected_groups AS sg WHERE {matches})'
        elif membership and plan.get('having'):
            membership = f'SELECT DISTINCT b.patient_id AS patient_id{relation} AND EXISTS ({query})'
        elif membership and limit and not aggregate:
            # A multi-column row list is actionable only when its own selected
            # rows explicitly contain patient IDs; never substitute all matches.
            if 'patient_id' not in aliases: membership = None
            else: membership = f'SELECT DISTINCT patient_id FROM ({query})'
        measured = [(i, e) for i, e in enumerate(plan['select']) if e['kind'] != 'field']
        if membership and returns != 'patient_set' and len(measured) == 1 and not plan.get('having') and not limit:
            # A conditional count or a ratio's conditional numerator: retain the patients it counted, not its whole base.
            i, e = measured[0]
            key = f'select.{i}' + ('.numerator' if e['kind'] == 'ratio' else '')
            if key in self.conditions and (e['kind'] != 'ratio' or e['numerator'].get('kind') in ('count', 'count_distinct')):
                membership = f'SELECT DISTINCT b.patient_id AS patient_id{relation} AND ({self.conditions[key]})'
        parts_query = None
        ratios = [(i, e) for i, e in enumerate(plan['select']) if e['kind'] == 'ratio' and f'select.{i}' in self.ratio_parts]
        if len(ratios) == 1:
            n_sql, d_sql = self.ratio_parts[f'select.{ratios[0][0]}']
            cols = ''.join(projections[g] + ' AS ' + identifier(g) + ', ' for g in groups)
            parts_query = ('SELECT ' + cols + f'{n_sql} AS numerator, {d_sql} AS denominator' + relation
                           + (' GROUP BY ' + ','.join(projections[g] for g in groups) if groups else ''))
        payload = {'version': VERSION, 'original_question': self.question, 'request': plan,
                   'source_identity': self.catalog.identity, 'bindings': self.bindings,
                   'query': query, 'membership_query': membership, 'returns': returns,
                   'unknown_membership_query': (f'SELECT DISTINCT b.patient_id AS patient_id{from_clause} WHERE ({where}) IS NULL'
                                                if 'patient_id' in self.catalog.tables[self.base] else None),
                   'population': sorted(self.population) if self.population is not None else None}
        if parts_query: payload['parts_query'] = parts_query
        if self.notes: payload['notes'] = list(dict.fromkeys(self.notes))
        if self.note_predicates: payload['note_predicates'] = self.note_predicates
        if self.subject: payload['subject'] = self.subject
        if self.base == 'utilization_event':
            payload['coverage_query'] = ('SELECT b.sources, b.in_ehr, count(*) AS events' + relation +
                                         ' GROUP BY b.sources,b.in_ehr')
        return {**payload, 'id': fingerprint(payload)}


def reads_picked_up_fills_only(request):
    """True when every medication read in the request is a governed drug_fill concept (picked-up fills with a stated or defaulted window)
    and none is a bare medication-table mention (ordered / listed / reported), so the answer is current fill evidence, not mentions."""
    found = {'concept': False, 'mention': False}

    def walk(node):
        if isinstance(node, dict):
            if node.get('op') == 'concept' and node.get('kind') == 'drug_fill':
                found['concept'] = True
            if node.get('op') == 'exists' and node.get('table') == 'medication':
                found['mention'] = True
            for v in node.values(): walk(v)
        elif isinstance(node, list):
            for v in node: walk(v)
    walk(request)
    return found['concept'] and not found['mention']


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


# Relations whose rows are source observations of one event: counts count the event.
EPISODE_KEYS = {'stay': ('patient_id', 'admit_date')}
EPISODE_DISCLOSURE = {'stay': 'Stays are counted as episodes: rows from ADT, encounters and claims that share patient and admit date count once.'}


def assert_sealed(contract):
    if fingerprint({k: v for k, v in contract.items() if k != 'id'}) != contract.get('id'):
        raise PlanError('Request contract changed after validation')
