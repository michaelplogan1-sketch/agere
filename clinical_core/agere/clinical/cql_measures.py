"""Measures and eligibility as executable CQL -> ELM contracts.

A governed measure is a CQL library (config/agere/measures_cql/<id>.cql) that states its population, exclusions and timing as
logic, compiled to ELM (Expression Logical Model, JSON) and executed over the semantic layer. Nothing is read from the JSON
measure file when the CQL runs; the JSON file is the independent reference the CQL is *checked against* (check()).

CQL subset (parse errors name the line):
    library CBP version '1.0.0'
    valueset "Hypertension": {"type": "condition", "concepts": ["hypertension"], "icd10_prefixes": ["I10", "I11"]}
    parameter "Measurement Period" Interval<Date> default Interval[@2029-06-01, @2030-05-27]
    define "Initial Population": AgeInYearsAt(end of "Measurement Period") between 18 and 85
        and EnrolledThroughout("Measurement Period", 1)
        and exists ([Condition: "Hypertension"] C where C.date during Interval[start of "Measurement Period" - 50 years, ...] or C.date is null)
    define "Denominator": "Initial Population"            define "Denominator Exclusion": ...      define "Numerator": ...
  expressions: and / or / not, parentheses, define references, < <= > >= =, between .. and .., is null / is not null,
  AgeInYearsAt(date), start of / end of interval, date +/- N days|months|years, Interval[a, b], exists(query), Count(query),
  Last(query).<property>, query = [DataType: "valueset"] Alias where predicate, "x.date during interval", alias.value / .code.
  Three-valued logic as in CQL: an unknown (null) comparison is neither true nor false; population membership needs true.
  Value sets are bound by the Agere retrieval layer (the JSON object after the colon): the same evidence rows
  (measures._rows: conditions from problem list, encounter and claim diagnoses; results; fills; services; BP) the reference engine
  reads, so a parity gap is a logic gap, not a data-access gap.

    parse(cql) -> ELM dict            compile_library(path) -> {'cql','elm','elm_sha256'}      registry() -> governed measures
    run_elm(db, elm, period, as_of, keep) -> populations, per-patient define trace
    check(db, measure_id, period, as_of) -> CQL result vs measures.evaluate_db (numerator, denominator, exclusions, patient sets)
Operation 'cql_measure' (engine_suite): rate / eligibility (why a patient is in or out) / population / elm / cql / check.
HEDIS/CMS measures here are approximations (see each measure file); nothing is a certified measure. No model calls.
"""
from collections import defaultdict
import hashlib
import json
import re
import sqlite3
from pathlib import Path

VERSION = 'agere-cql-measures/1'
ROOT = Path(__file__).resolve().parents[3]
LIB_DIR = ROOT / 'config' / 'agere' / 'measures_cql'
DATATYPE_TYPES = {'Condition': ('condition',), 'Observation': ('measurement', 'blood_pressure'), 'MedicationDispense': ('medication', 'pdc'),
                  'Procedure': ('service',), 'Encounter': ('encounter',), 'Stay': ('stay',)}
POPULATIONS = ('Initial Population', 'Denominator', 'Denominator Exclusion', 'Numerator')
KEYWORDS = {'library', 'version', 'valueset', 'parameter', 'default', 'define', 'and', 'or', 'not', 'exists', 'where', 'between', 'during',
            'is', 'null', 'start', 'end', 'of', 'Interval', 'true', 'false', 'in'}


class CQLError(ValueError):
    pass


# ---- lexer ----------------------------------------------------------------------------------------------------------
TOKEN = re.compile(r'''\s*(?:(?P<comment>//[^\n]*|/\*.*?\*/)|(?P<date>@\d{4}-\d{2}-\d{2})|(?P<num>\d+(?:\.\d+)?)|(?P<qname>"[^"\n]*")|(?P<str>'[^'\n]*')'''
                   r'''|(?P<json>\{)|(?P<id>[A-Za-z_][A-Za-z0-9_]*)|(?P<op><=|>=|<>|[<>=+\-.,:()\[\]]))''', re.S)


def lex(text):
    out, i, n = [], 0, len(text)
    while i < n:
        if text[i:].strip() == '': break
        m = TOKEN.match(text, i)
        if not m or m.end() == i: raise CQLError(f"line {text.count(chr(10), 0, i) + 1}: unreadable text near {text[i:i + 20].strip()!r}")
        line = text.count('\n', 0, m.start('comment') if m.lastgroup == 'comment' else m.end() - 1) + 1
        kind = m.lastgroup
        if kind == 'comment': i = m.end(); continue
        if kind == 'json':  # a value-set binding: read the balanced JSON object
            start, depth, j = m.start('json'), 0, m.start('json')
            while j < n:
                if text[j] == '{': depth += 1
                elif text[j] == '}':
                    depth -= 1
                    if depth == 0: break
                j += 1
            else: raise CQLError(f'line {line}: unterminated JSON object')
            try: out.append(('json', json.loads(text[start:j + 1]), line))
            except ValueError as exc: raise CQLError(f'line {line}: value-set binding is not JSON ({exc})')
            i = j + 1; continue
        val = m.group(kind)
        out.append((kind, val[1:-1] if kind in ('qname', 'str') else val, line))
        i = m.end()
    out.append(('eof', None, text.count('\n') + 1))
    return out


# ---- parser -> ELM ---------------------------------------------------------------------------------------------------
class Parser:
    def __init__(self, text):
        self.t, self.i = lex(text), 0
        self.lib = {'identifier': {}, 'parameters': [], 'valueSets': [], 'statements': []}
        self.aliases = []

    def peek(self, k=0): return self.t[min(self.i + k, len(self.t) - 1)]
    def take(self): tok = self.t[self.i]; self.i += 1; return tok
    def fail(self, msg): raise CQLError(f'line {self.peek()[2]}: {msg} (at {self.peek()[1]!r})')

    def word(self, w):
        k, v, _ = self.peek()
        return k == 'id' and v == w

    def eat_word(self, w):
        if not self.word(w): self.fail(f'expected {w}')
        self.take()

    def eat_op(self, o):
        k, v, _ = self.peek()
        if k != 'op' or v != o: self.fail(f'expected {o!r}')
        self.take()

    def op(self, o):
        k, v, _ = self.peek()
        return k == 'op' and v == o

    # library -----------------------------------------------------------------
    def library(self):
        self.eat_word('library'); name = self.take()
        if name[0] != 'id': self.fail('library name')
        self.eat_word('version'); ver = self.take()
        if ver[0] != 'str': self.fail('library version as a quoted string')
        self.lib['identifier'] = {'id': name[1], 'version': ver[1]}
        while self.peek()[0] != 'eof':
            if self.word('valueset'):
                self.take(); nm = self.take()
                if nm[0] != 'qname': self.fail('valueset name in double quotes')
                self.eat_op(':'); b = self.take()
                if b[0] != 'json': self.fail('valueset binding as a JSON object')
                self.lib['valueSets'].append({'name': nm[1], 'binding': b[1]})
            elif self.word('parameter'):
                self.take(); nm = self.take()
                if nm[0] != 'qname': self.fail('parameter name in double quotes')
                self.eat_word('Interval'); self.eat_op('<'); self.take(); self.eat_op('>')
                self.eat_word('default')
                self.lib['parameters'].append({'name': nm[1], 'default': self.expr()})
            elif self.word('define'):
                self.take(); nm = self.take()
                if nm[0] != 'qname': self.fail('define name in double quotes')
                self.eat_op(':')
                self.lib['statements'].append({'name': nm[1], 'expression': self.expr()})
            else: self.fail('expected valueset, parameter or define')
        return {'library': self.lib}

    # expressions: or < and < not < comparison < additive < postfix < primary
    def expr(self): return self.or_()

    def or_(self):
        left = self.and_()
        while self.word('or'):
            self.take(); left = {'type': 'Or', 'operand': [left, self.and_()]}
        return left

    def and_(self):
        left = self.not_()
        while self.word('and'):
            self.take(); left = {'type': 'And', 'operand': [left, self.not_()]}
        return left

    def not_(self):
        if self.word('not'):
            self.take(); return {'type': 'Not', 'operand': self.not_()}
        return self.comparison()

    def comparison(self):
        left = self.additive()
        k, v, _ = self.peek()
        if k == 'op' and v in ('<', '<=', '>', '>=', '=', '<>'):
            self.take(); right = self.additive()
            name = {'<': 'Less', '<=': 'LessOrEqual', '>': 'Greater', '>=': 'GreaterOrEqual', '=': 'Equal', '<>': 'NotEqual'}[v]
            return {'type': name, 'operand': [left, right]}
        if self.word('between'):
            self.take(); lo = self.additive(); self.eat_word('and'); hi = self.additive()
            return {'type': 'And', 'operand': [{'type': 'GreaterOrEqual', 'operand': [left, lo]}, {'type': 'LessOrEqual', 'operand': [left, hi]}]}
        if self.word('during') or self.word('in'):
            self.take(); iv = self.additive()
            return {'type': 'In', 'operand': [left, iv]}
        if self.word('is'):
            self.take(); neg = False
            if self.word('not'): self.take(); neg = True
            self.eat_word('null')
            node = {'type': 'IsNull', 'operand': left}
            return {'type': 'Not', 'operand': node} if neg else node
        return left

    def additive(self):
        left = self.postfix()
        while self.op('+') or self.op('-'):
            sign = self.take()[1]; q = self.quantity()
            left = {'type': 'Add' if sign == '+' else 'Subtract', 'operand': [left, q]}
        return left

    def quantity(self):
        k, v, _ = self.peek()
        if k != 'num': self.fail('a quantity such as 6 months')
        self.take(); u = self.take()
        unit = u[1].rstrip('s') if u[0] == 'id' else None
        if unit not in ('day', 'month', 'year'): self.fail('unit day(s), month(s) or year(s)')
        return {'type': 'Quantity', 'value': int(float(v)), 'unit': unit}

    def postfix(self):
        node = self.primary()
        while self.op('.'):
            self.take(); p = self.take()
            if p[0] != 'id': self.fail('a property name')
            node = {'type': 'Property', 'path': p[1], 'source': node}
        return node

    def primary(self):
        k, v, ln = self.peek()
        if k == 'op' and v == '(':
            self.take(); e = self.expr(); self.eat_op(')'); return e
        if k == 'op' and v == '[': return self.query()
        if k == 'num':
            self.take(); return {'type': 'Literal', 'valueType': 'Decimal' if '.' in v else 'Integer', 'value': float(v) if '.' in v else int(v)}
        if k == 'date': self.take(); return {'type': 'Date', 'value': v[1:]}
        if k == 'str': self.take(); return {'type': 'Literal', 'valueType': 'String', 'value': v}
        if k == 'qname':
            self.take()
            if v in {p['name'] for p in self.lib['parameters']}: return {'type': 'ParameterRef', 'name': v}
            return {'type': 'ExpressionRef', 'name': v}
        if k == 'id':
            if v in ('true', 'false'): self.take(); return {'type': 'Literal', 'valueType': 'Boolean', 'value': v == 'true'}
            if v == 'null': self.take(); return {'type': 'Null'}
            if v == 'Interval':
                self.take(); self.eat_op('['); lo = self.expr(); self.eat_op(','); hi = self.expr(); self.eat_op(']')
                return {'type': 'Interval', 'low': lo, 'high': hi}
            if v in ('start', 'end'):
                self.take(); self.eat_word('of'); return {'type': 'Start' if v == 'start' else 'End', 'operand': self.postfix()}
            if v == 'exists':
                self.take(); self.eat_op('('); q = self.query(); self.eat_op(')'); return {'type': 'Exists', 'operand': q}
            if v in ('Count', 'Last'):
                self.take(); self.eat_op('('); q = self.query(); self.eat_op(')')
                return {'type': v, 'source': q}
            if v == 'AgeInYearsAt':
                self.take(); self.eat_op('('); e = self.expr(); self.eat_op(')'); return {'type': 'AgeInYearsAt', 'operand': e}
            if v == 'EnrolledThroughout':
                self.take(); self.eat_op('('); iv = self.expr(); self.eat_op(',')
                gap = self.take()
                if gap[0] != 'num': self.fail('allowed gap in months')
                self.eat_op(')')
                return {'type': 'FunctionRef', 'name': 'EnrolledThroughout', 'operand': [iv, {'type': 'Literal', 'valueType': 'Integer', 'value': int(gap[1])}]}
            if v in self.aliases:
                self.take(); return {'type': 'AliasRef', 'name': v}
        self.fail('unexpected token')

    def query(self):
        self.eat_op('['); dt = self.take()
        if dt[0] != 'id' or dt[1] not in DATATYPE_TYPES: self.fail(f'data type one of {sorted(DATATYPE_TYPES)}')
        self.eat_op(':'); vs = self.take()
        if vs[0] != 'qname' or vs[1] not in {x['name'] for x in self.lib['valueSets']}: self.fail('a declared valueset')
        self.eat_op(']')
        retrieve = {'type': 'Retrieve', 'dataType': dt[1], 'valueSet': vs[1]}
        alias = self.peek()
        if alias[0] == 'id' and alias[1] not in KEYWORDS:
            self.take(); self.aliases.append(alias[1]); where = None
            if self.word('where'): self.take(); where = self.expr()
            self.aliases.pop()
            return {'type': 'Query', 'source': {'alias': alias[1], 'expression': retrieve}, **({'where': where} if where else {})}
        return {'type': 'Query', 'source': {'alias': None, 'expression': retrieve}}


def parse(cql):
    p = Parser(cql)
    elm = p.library()
    names = [s['name'] for s in elm['library']['statements']]
    if len(set(names)) != len(names): raise CQLError('duplicate define names')
    for pop in ('Initial Population', 'Numerator'):
        if pop not in names: raise CQLError(f'measure library must define "{pop}"')
    seen = set()
    for s in elm['library']['statements']:  # a define may only refer to earlier defines (no cycles)
        for ref in _refs(s['expression']):
            if ref not in seen: raise CQLError(f'"{s["name"]}" refers to "{ref}" before it is defined')
        seen.add(s['name'])
    for v in elm['library']['valueSets']:
        t = v['binding'].get('type')
        if t not in ('condition', 'measurement', 'blood_pressure', 'medication', 'service', 'encounter', 'stay'):
            raise CQLError(f'valueset "{v["name"]}": binding type {t!r} is not a retrieval type')
    for node in _walk(elm['library']['statements']):
        if node.get('type') == 'Retrieve':
            vs = next(v for v in elm['library']['valueSets'] if v['name'] == node['valueSet'])
            if vs['binding']['type'] not in DATATYPE_TYPES[node['dataType']]:
                raise CQLError(f'[{node["dataType"]}: "{vs["name"]}"] is bound to a {vs["binding"]["type"]} retrieval')
    return elm


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values(): yield from _walk(v)
    elif isinstance(node, list):
        for v in node: yield from _walk(v)


def _refs(expr):
    return [n['name'] for n in _walk(expr) if n.get('type') == 'ExpressionRef']


def elm_sha256(elm):
    return hashlib.sha256(json.dumps(elm, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


# ---- evaluator -------------------------------------------------------------------------------------------------------
def _days(base, n, unit):
    from .measures import shift
    return shift(base, n, {'day': 'd', 'month': 'm', 'year': 'y'}[unit])


class Run:
    """One execution of a compiled library over a semantic-layer connection."""

    def __init__(self, db, elm, period, as_of, keep=None, ctx=None):
        from . import measures as M
        self.M, self.db, self.lib = M, db, elm['library']
        self.ctx = ctx or M.Context(db)
        self.period = period
        self.as_of = as_of
        self.vs = {v['name']: v['binding'] for v in self.lib['valueSets']}
        self.defs = {s['name']: s['expression'] for s in self.lib['statements']}
        self.people = M._people(db, as_of, period[0], period[1], self.ctx)
        if keep is not None: self.people = {k: v for k, v in self.people.items() if k in keep}
        self.rows = {}

    def retrieved(self, node):
        key = (node['dataType'], node['valueSet'])
        if key not in self.rows:
            c = {**self.vs[node['valueSet']], 'label': node['valueSet']}
            self.rows[key] = self.M._rows(self.ctx, c)[0]
        return self.rows[key]

    def run(self, pid):
        trace, cache = {}, {}
        for s in self.lib['statements']:
            trace[s['name']] = self.ev(s['expression'], {'pid': pid, 'cache': cache, 'alias': {}})
            cache[s['name']] = trace[s['name']]
        return trace

    def ev(self, n, env):
        t = n['type']
        if t == 'ExpressionRef': return env['cache'][n['name']]
        if t == 'ParameterRef': return {'low': self.period[0], 'high': self.period[1]}
        if t == 'Literal': return n['value']
        if t == 'Null': return None
        if t == 'Date': return n['value']
        if t == 'Interval': return {'low': self.ev(n['low'], env), 'high': self.ev(n['high'], env)}
        if t in ('Start', 'End'):
            iv = self.ev(n['operand'], env)
            return None if iv is None else iv['low' if t == 'Start' else 'high']
        if t in ('Add', 'Subtract'):
            base = self.ev(n['operand'][0], env)
            q = n['operand'][1]
            return None if base is None else _days(base, q['value'] * (1 if t == 'Add' else -1), q['unit'])
        if t == 'And':
            a, b = self.ev(n['operand'][0], env), self.ev(n['operand'][1], env)
            return False if a is False or b is False else (None if a is None or b is None else True)
        if t == 'Or':
            a, b = self.ev(n['operand'][0], env), self.ev(n['operand'][1], env)
            return True if a is True or b is True else (None if a is None or b is None else False)
        if t == 'Not':
            a = self.ev(n['operand'], env)
            return None if a is None else not a
        if t == 'IsNull': return self.ev(n['operand'], env) is None
        if t in ('Less', 'LessOrEqual', 'Greater', 'GreaterOrEqual', 'Equal', 'NotEqual'):
            a, b = self.ev(n['operand'][0], env), self.ev(n['operand'][1], env)
            if a is None or b is None: return None
            if isinstance(a, str) and isinstance(b, str) and len(a) >= 10 and a[4:5] == '-': a, b = a[:10], b[:10]
            else:
                try: a, b = float(a), float(b)
                except (TypeError, ValueError): pass
            return {'Less': a < b, 'LessOrEqual': a <= b, 'Greater': a > b, 'GreaterOrEqual': a >= b, 'Equal': a == b, 'NotEqual': a != b}[t]
        if t == 'In':
            x, iv = self.ev(n['operand'][0], env), self.ev(n['operand'][1], env)
            if x is None or iv is None or iv['low'] is None or iv['high'] is None: return None
            return iv['low'][:10] <= x[:10] <= iv['high'][:10]
        if t == 'AgeInYearsAt':
            on = self.ev(n['operand'], env)
            info = self.people.get(env['pid'])
            return None if on is None or info is None else self.M._age_at(info, on, self.as_of)
        if t == 'FunctionRef' and n['name'] == 'EnrolledThroughout':
            iv = self.ev(n['operand'][0], env)
            info = self.people.get(env['pid'])
            return None if info is None or iv is None else self.M._enrolled(info, iv['low'], iv['high'], n['operand'][1]['value'])
        if t == 'AliasRef': return env['alias'][n['name']]
        if t == 'Property':
            src = self.ev(n['source'], env)
            if src is None: return None
            return src.get(n['path']) if isinstance(src, dict) else None
        if t == 'Exists': return len(self.query(n['operand'], env)) > 0
        if t == 'Count': return len(self.query(n['source'], env))
        if t == 'Last': return self.last(n['source'], env)
        raise CQLError(f'ELM node {t} is not executable')

    def query(self, q, env):
        retrieve = q['source']['expression']
        alias = q['source']['alias']
        out = []
        for date, value, detail in self.retrieved(retrieve).get(env['pid'], []):
            row = {'date': date, 'value': value, 'code': detail}
            if 'where' in q:
                sub = {**env, 'alias': {**env['alias'], alias: row}}
                if self.ev(q['where'], sub) is not True: continue
            out.append(row)
        return out

    def last(self, q, env):
        rows = self.query(q, env)
        kind = self.vs[q['source']['expression']['valueSet']]['type']
        rows = [r for r in rows if r['date']]
        if not rows: return None
        if kind == 'blood_pressure':  # representative reading: latest day with both, lowest of each on that day
            by_day = defaultdict(dict)
            for r in rows:
                cur = by_day[r['date']].get(r['code'])
                by_day[r['date']][r['code']] = r['value'] if cur is None else min(cur, r['value'])
            days = sorted(d for d, x in by_day.items() if 'systolic_bp' in x and 'diastolic_bp' in x)
            if not days: return None
            return {'date': days[-1], 'systolic': by_day[days[-1]]['systolic_bp'], 'diastolic': by_day[days[-1]]['diastolic_bp']}
        rows.sort(key=lambda r: r['date'])
        day = rows[-1]['date']
        nums = [float(r['value']) for r in rows if r['date'] == day and self.M._num(r['value'])]
        return {'date': day, 'value': min(nums) if nums else None, 'code': rows[-1]['code']}


def run_elm(db, elm, period, as_of, keep=None, trace=False, ctx=None):
    """-> {'initial_population', 'denominator', 'excluded', 'numerator' (counts), 'patients': {...}}. The Initial Population gate is
    enrollment+age+condition as the CQL states it; Denominator defaults to it; the rate is numerator / (denominator - excluded)."""
    run = Run(db, elm, period, as_of, keep, ctx)
    names = {s['name'] for s in elm['library']['statements']}
    counts = defaultdict(int)
    patients = {}
    for pid in sorted(run.people):
        t = run.run(pid)
        ip = t.get('Initial Population') is True
        den = ip and (t.get('Denominator') is True if 'Denominator' in names else True)
        excl = den and (t.get('Denominator Exclusion') is True if 'Denominator Exclusion' in names else False)
        met = den and not excl and t.get('Numerator') is True
        counts['initial_population'] += ip; counts['eligible'] += den; counts['excluded'] += excl
        counts['denominator'] += den and not excl; counts['numerator'] += met
        if ip or trace: patients[pid] = {'initial_population': ip, 'denominator': bool(den), 'excluded': bool(excl), 'met': bool(met),
                                         **({'trace': {k: v for k, v in t.items()}} if trace else {})}
    rate = round(counts['numerator'] / counts['denominator'], 4) if counts['denominator'] else None
    return {'schema': VERSION, 'period': {'start': period[0], 'end': period[1]}, 'counts': dict(counts), 'rate': rate,
            'patients': patients, 'people_evaluated': len(run.people)}


# ---- governed registry -----------------------------------------------------------------------------------------------
def compile_library(path):
    path = Path(path)
    cql = path.read_text()
    elm = parse(cql)
    return {'cql': cql, 'cql_sha256': hashlib.sha256(cql.encode()).hexdigest(), 'elm': elm, 'elm_sha256': elm_sha256(elm)}


def registry(directory=None):
    """{measure id: {...}} from <dir>/registry.json; every entry is re-compiled and its ELM digest compared with the governed one."""
    d = Path(directory or LIB_DIR)
    f = d / 'registry.json'
    reg = json.loads(f.read_text()) if f.exists() else {'measures': {}}
    out = {}
    for mid, spec in reg['measures'].items():
        lib = compile_library(d / spec['cql'])
        out[mid] = {**spec, 'library': lib['elm']['library']['identifier'], 'cql_sha256': lib['cql_sha256'], 'elm_sha256': lib['elm_sha256'],
                    'governed': lib['elm_sha256'] == spec.get('elm_sha256'), 'elm': lib['elm'], 'cql': lib['cql']}
    return out


def govern(measure_ids=None, directory=None, steward='Agere measure governance', note='compiled from CQL'):
    """Write each CQL's current ELM digest into registry.json (the approval step) and the ELM JSON beside the CQL."""
    d = Path(directory or LIB_DIR)
    f = d / 'registry.json'
    reg = json.loads(f.read_text()) if f.exists() else {'schema': 'agere-cql-registry/1', 'measures': {}}
    for mid, spec in reg['measures'].items():
        if measure_ids and mid not in measure_ids: continue
        lib = compile_library(d / spec['cql'])
        spec.update({'elm_sha256': lib['elm_sha256'], 'cql_sha256': lib['cql_sha256'], 'governed_by': steward, 'note': note})
        (d / 'elm').mkdir(exist_ok=True)
        (d / 'elm' / f'{mid}.json').write_text(json.dumps(lib['elm'], indent=1, sort_keys=True))
    f.write_text(json.dumps(reg, indent=1, sort_keys=True))
    return reg


def _connect(path):
    from .measures import _connect as c
    return c(path)


def check(db, measure_id, period, as_of, keep=None, reg=None):
    """Run the governed CQL and the reference measure-library engine on the same period; compare counts and patient sets."""
    from . import measures as M
    reg = reg or registry()
    spec = reg[measure_id]
    ctx = M.Context(db)
    got = run_elm(db, spec['elm'], period, as_of, keep, ctx=ctx)
    m = M.library()[spec['reference_measure']]
    ref = M.evaluate_db(db, m, period[0], period[1], as_of, output='patients', keep=keep, ctx=ctx)
    ref_rows = {r['patient_id']: r for r in ref.get('patient_rows', [])}
    cql_ip = {p for p, v in got['patients'].items() if v['initial_population']}
    diff = {'eligible_only_cql': sorted(cql_ip - set(ref_rows))[:10], 'eligible_only_reference': sorted(set(ref_rows) - cql_ip)[:10]}
    both = cql_ip & set(ref_rows)
    diff['excluded_differs'] = sorted(p for p in both if got['patients'][p]['excluded'] != ref_rows[p]['excluded'])[:10]
    diff['met_differs'] = sorted(p for p in both if got['patients'][p]['met'] != ref_rows[p]['met'])[:10]
    totals = ref.get('overall') or {}
    ref_counts = {'eligible': totals.get('eligible'), 'excluded': totals.get('excluded'), 'denominator': totals.get('denominator'),
                  'numerator': totals.get('numerator')}
    cql_counts = {k: got['counts'].get(k, 0) for k in ref_counts}
    return {'measure': measure_id, 'reference_measure': spec['reference_measure'], 'period': {'start': period[0], 'end': period[1]},
            'cql': cql_counts, 'reference': ref_counts, 'rate_cql': got['rate'], 'rate_reference': totals.get('rate'),
            'identical_counts': cql_counts == ref_counts, 'identical_patient_sets': not any(diff.values()),
            'differences': {k: v for k, v in diff.items() if v}, 'elm_sha256': spec['elm_sha256'], 'governed': spec['governed']}


# ---- engine ----------------------------------------------------------------------------------------------------------
OP = 'cql_measure'
OUTPUTS = ('rate', 'eligibility', 'patients', 'check', 'elm', 'cql')
PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'EXECUTABLE MEASURE LOGIC (CQL / ELM): the governed CQL behind a quality measure (population, exclusions, timing), its '
                  'rate, why a patient is in or out of the denominator or numerator (every define evaluated), the compiled ELM, and a '
                  'check that the CQL and the measure library agree.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'measure': 'governed CQL measure id', 'period': {'start': '', 'end': ''}, 'output': 'rate|eligibility|patients|check|elm|cql',
                           'patient_id': 'patient to explain (eligibility)'},
            'meaning': 'Execute a governed CQL measure library compiled to ELM over the semantic layer. rate: numerator/denominator with the '
                       'population counts; eligibility: for one patient, the value of every define (Initial Population, Denominator, '
                       'Denominator Exclusion, Numerator and the logic they use); patients: the denominator patients with met/excluded; '
                       'check: CQL vs the measure-library engine on the same period; elm / cql: the governed source.'}
NATIVE = ('count', 'value')
NEEDS_STACK = False


def schema(obj, string, array):
    return obj({'measure': {'type': 'string', 'description': 'Governed CQL measure id (cbp, gsd_control_lt8, spd_statin_diabetes)'},
                'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                'output': string(OUTPUTS, 'rate: counts and rate; eligibility: one patient traced through every define; patients: member list; '
                                          'check: CQL vs measure library; elm / cql: governed source'),
                'patient_id': {'type': 'string'}})


def validate(p):
    p = dict(p or {})
    if set(p) - {'measure', 'period', 'output', 'patient_id'}: raise ValueError('cql_measure parameters: measure, period, output, patient_id')
    p.setdefault('output', 'rate')
    if p['output'] not in OUTPUTS: raise ValueError(f'output: one of {OUTPUTS}')
    if not isinstance(p.get('measure'), str) or not re.fullmatch(r'[a-z0-9_]{2,60}', p['measure']): raise ValueError('measure: a governed CQL measure id')
    if p['output'] == 'eligibility' and not p.get('patient_id'): raise ValueError('eligibility needs patient_id')
    per = p.get('period') or {}
    if per and not (re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(per.get('start'))) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(per.get('end')))):
        raise ValueError('period: start and end as YYYY-MM-DD')
    p['period'] = {'start': per.get('start'), 'end': per.get('end')} if per else {}
    return p


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    from . import measures as M
    reg = registry()
    if p['measure'] not in reg: raise ValueError(f"no governed CQL measure {p['measure']!r}; governed: {sorted(reg)}")
    spec = reg[p['measure']]
    as_of = str(as_of)[:10]
    period = (p['period'].get('start'), p['period'].get('end')) if p['period'] else M.default_period(as_of)
    base = {'schema': VERSION, 'parameters': p, 'measure': p['measure'], 'name': spec['name'], 'library': spec['library'], 'elm_sha256': spec['elm_sha256'],
            'governed': spec['governed'], 'period': {'start': period[0], 'end': period[1]}, 'status': spec.get('status', 'approximation'),
            'steward': spec.get('steward'), 'reference_measure': spec['reference_measure'], 'as_of': as_of}
    if p['output'] == 'cql': return {**base, 'cql': spec['cql'], 'patient_ids': []}
    if p['output'] == 'elm': return {**base, 'elm': spec['elm'], 'patient_ids': []}
    db = _connect(path)
    try:
        keep = set(patient_ids) if patient_ids is not None else None
        if p['output'] == 'check': return {**base, 'check': check(db, p['measure'], period, as_of, keep, reg), 'patient_ids': []}
        got = run_elm(db, spec['elm'], period, as_of, keep, trace=p['output'] == 'eligibility' and bool(p.get('patient_id')))
    finally:
        db.close()
    if p['output'] == 'eligibility':
        row = got['patients'].get(p['patient_id'])
        if row is None: return {**base, 'counts': got['counts'], 'patient': {'patient_id': p['patient_id'], 'found': False}, 'patient_ids': []}
        return {**base, 'counts': got['counts'], 'patient': {'patient_id': p['patient_id'], 'found': True, **row}, 'patient_ids': [p['patient_id']]}
    members = sorted(pid for pid, v in got['patients'].items() if v['denominator'] and not v['excluded'])
    table = [{'patient_id': pid, **{k: v for k, v in got['patients'][pid].items() if k != 'trace'}} for pid in sorted(got['patients'])]
    return {**base, 'counts': got['counts'], 'rate': got['rate'], 'table': table if p['output'] == 'patients' else [],
            'patient_ids': members, 'people_evaluated': got['people_evaluated']}


def answer(report, plan, question):
    out = report['parameters']['output']
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    method = (f"Governed CQL library {report['library'].get('id')} {report['library'].get('version')} compiled to ELM (sha256 {report['elm_sha256'][:12]}), "
              f"executed over the semantic layer for {report['period']['start']}..{report['period']['end']}; {report['status']} of the "
              f"{report['reference_measure']} measure (steward {report.get('steward')}), not a certified measure.")
    base = {'status': 'partial' if partial else 'answered', 'method': method, 'membership_role': 'selected',
            'definitions': [{'term': report['name'], 'basis': f"CQL library {report['library'].get('id')} v{report['library'].get('version')}; governed digest "
                                                             f"{'matches' if report['governed'] else 'DIFFERS FROM'} the registry"}],
            'limitations': [method]}
    if out in ('cql', 'elm'):
        return {**base, 'answer_type': 'text', 'value': report['cql'] if out == 'cql' else json.dumps(report['elm'], indent=1)[:12000], 'count': 0, 'patient_ids': []}
    if out == 'check':
        c = report['check']
        verdict = ('identical counts and identical patient sets' if c['identical_counts'] and c['identical_patient_sets'] else
                   f"counts {'match' if c['identical_counts'] else 'differ'}; patient sets {'match' if c['identical_patient_sets'] else 'differ'}")
        return {**base, 'answer_type': 'table', 'table': [{'source': 'CQL', **c['cql'], 'rate': c['rate_cql']}, {'source': 'measure library', **c['reference'], 'rate': c['rate_reference']}],
                'count': c['cql'].get('denominator') or 0, 'patient_ids': [],
                'value': f"{report['name']}: CQL {c['cql']} vs measure library {c['reference']}: {verdict}." + (f" Differences: {c['differences']}" if c['differences'] else '')}
    if out == 'eligibility':
        pt = report['patient']
        if not pt.get('found'):
            return {**base, 'answer_type': 'text', 'count': 0, 'patient_ids': [], 'value': f"{pt['patient_id']} is not in the evaluated population (not enrolled or not received)."}
        tr = pt['trace']
        steps = [{'define': k, 'value': v} for k, v in tr.items()]
        state = ('excluded from the denominator' if pt['excluded'] else 'in the denominator and meets the numerator' if pt['met'] else
                 'in the denominator and does not meet the numerator' if pt['denominator'] else 'not in the initial population')
        return {**base, 'answer_type': 'table', 'table': steps, 'count': 1 if pt['denominator'] else 0, 'patient_ids': [pt['patient_id']],
                'value': f"{pt['patient_id']} is {state} for {report['name']}: " + '; '.join(f"{s['define']} = {s['value']}" for s in steps) + '.'}
    c = report['counts']
    value = (f"{report['name']}: {c.get('numerator', 0)} of {c.get('denominator', 0)} meet the measure"
             + (f" ({report['rate']:.1%})" if report.get('rate') is not None else '')
             + f"; {c.get('excluded', 0)} excluded of {c.get('eligible', 0)} eligible, {c.get('initial_population', 0)} in the initial population.")
    return {**base, 'answer_type': 'table' if out == 'patients' else 'scalar', 'table': report.get('table') or [], 'count': c.get('denominator', 0),
            'patient_ids': report['patient_ids'], 'value': value,
            'overall': {'cql_measure': {'counts': c, 'rate': report.get('rate'), 'elm_sha256': report['elm_sha256']}}}


# ---- free local-compiler route -----------------------------------------------------------------------------------------
def _alias_map():
    out = {}
    try:
        for mid, spec in registry().items():
            for a in spec.get('aliases', []): out[a.lower()] = mid
    except (OSError, ValueError, CQLError):
        pass
    return out


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(r'\bcql\b|\belm\b|executable (?:measure )?(?:logic|measure)|measure logic|clinical quality language|why (?:is|isn.t|is not|was|wasn.t) (?:patient )?\S+ (?:in|out of|excluded|included|not in)|is (?:patient )?\S+ (?:in|eligible for) the (?:denominator|numerator)|denominator logic', low):
        return None
    amap = _alias_map()
    mid = next((m for a, m in sorted(amap.items(), key=lambda kv: -len(kv[0])) if re.search(r'(?<![a-z0-9])' + re.escape(a) + r'(?![a-z0-9])', low)), None)
    if mid is None: return None
    params = {'measure': mid}
    pid = re.search(r'\b(p-\d+|[a-z]{1,4}-\d{3,})\b', question, re.I)
    if pid and re.search(r'\bwhy\b|\bin the denominator\b|\bin the numerator\b|\beligible\b|\bexcluded\b|\bincluded\b', low): params.update(output='eligibility', patient_id=pid.group(1).upper())
    elif re.search(r'\b(?:agree|match|differ|compare|check|parity|reconcile)\w*\b', low): params['output'] = 'check'
    elif re.search(r'\belm\b', low) and re.search(r'\bshow|compiled|json|print|give\b', low): params['output'] = 'elm'
    elif re.search(r'\b(?:show|print|give|what is|display)\b.*\bcql\b|\bcql (?:source|library|text|logic)\b|\bexecutable (?:measure )?logic\b|\bmeasure logic\b', low) and not re.search(r'\brate\b|\bhow many\b', low): params['output'] = 'cql'
    elif re.search(r'\blist\b|\bwhich patients\b|\bwho\b', low): params['output'] = 'patients'
    else: params['output'] = 'rate'
    return plan(OP, params, 'Governed CQL/ELM measure execution', question, [f"measure {mid}", f"output {params['output']}"])


TEMPLATES = ['Show the executable CQL logic for the blood pressure control measure',
             'Run the CQL for controlling high blood pressure and tell me the rate',
             'Do the CQL and the measure library agree for the diabetes A1c control measure?',
             'Why is patient P-1002 in the denominator of the glycemic control CQL measure?',
             'Show the compiled ELM for the statin therapy for diabetes measure']
