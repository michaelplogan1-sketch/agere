"""Conservative, checked request templates with typed literal slots.

Only a successfully executed, checked hosted interpretation can seed a shape.
Reuse changes explicit numeric/string literals and calendar periods, never words,
operators, concepts or predicate structure. Every reuse recompiles the full
question. A model's check is recorded as such, never independent adjudication.
"""
import copy
import json
from pathlib import Path
import re
import sqlite3
import time

from .request_plan import compile_request, fingerprint, PlanError

VERSION = 'checked-request-shape/1'
TOKEN = re.compile(r'"[^"\n]*"|\'[^\'\n]*\'|(?<![\w.-])\d{4}-\d{2}-\d{2}(?![\w.-])|(?<![\w.])\d+(?:\.\d+)?(?![\w.])')

def family(question):
    from .learning import normalize_question
    # Sentence punctuation must not turn a final year/threshold into a fixed
    # concept. Normalize before tokenization; embedded decimals/codes stay intact.
    return TOKEN.sub('<literal>', normalize_question(question))

def _db(path):
    db = sqlite3.connect(Path(path)/'question-plans.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS request_shapes(id TEXT PRIMARY KEY, policy TEXT, payload TEXT, status TEXT, reason TEXT)')
    return db


def _corrections(path):
    # Keep revocation in the shared planner's store; learning.record_correction
    # also calls invalidate so this does not depend on legacy answer routing.
    with _db(path) as db:
        return {r[0] for r in db.execute("SELECT id FROM request_shapes WHERE status='revoked'")}


def valid(path, ident):
    with _db(path) as db:
        row = db.execute('SELECT status FROM request_shapes WHERE id=?', (ident,)).fetchone()
    return bool(row and row[0] == 'active')


def make(question, request):
    tokens = list(TOKEN.finditer(question))
    if not tokens: return None
    slots, fragments, cursor, labels = {}, [], 0, {}
    for token in tokens:
        raw = token.group()
        fragments.append(re.escape(question[cursor:token.start()]))
        if raw in labels:
            fragments.append('(?P='+labels[raw]+')')
        else:
            name = 's'+str(len(slots)); labels[raw] = name
            kind = 'string' if raw[0] in "\"'" else 'date' if re.fullmatch(r'\d{4}-\d\d-\d\d', raw) else 'number'
            pattern = re.escape(raw[0])+r'[^'+re.escape(raw[0])+r'\n]{1,150}'+re.escape(raw[0]) if kind == 'string' else (
                r'\d{4}-\d{2}-\d{2}' if kind == 'date' else r'\d+(?:\.\d+)?')
            fragments.append(f'(?P<{name}>{pattern})')
            slots[name] = {'kind': kind, 'original': raw}
        cursor = token.end()
    fragments.append(re.escape(question[cursor:]))
    used = set()
    def slot_value(value, quote):
        for raw, name in labels.items():
            literal = raw[1:-1] if slots[name]['kind'] == 'string' else raw
            if str(value) == literal and raw in quote:
                used.add(name)
                return {'$slot': name, 'transform': 'identity'}
            if slots[name]['kind'] == 'number' and re.fullmatch(r'20\d\d', raw) and raw in quote:
                for suffix, offset, transform in [('-01-01', 0, 'year_start'), ('-12-31', 0, 'year_end'),
                                                  ('-01-01', 1, 'next_year_start')]:
                    if value == str(int(raw)+offset)+suffix:
                        used.add(name)
                        return {'$slot': name, 'transform': transform}
        return value
    def visit(obj, inherited_quote=''):
        if isinstance(obj, list): return [visit(x, inherited_quote) for x in obj]
        if not isinstance(obj, dict): return obj
        quote = obj.get('quote', inherited_quote)
        quote = ' '.join(quote) if isinstance(quote, list) else quote
        out = {}
        for key, value in obj.items():
            if key == 'quote':
                def quoted(text):
                    for raw, name in sorted(labels.items(), key=lambda kv: -len(kv[0])):
                        text = re.sub(r'(?<![\w.])'+re.escape(raw)+r'(?![\w.])', '{{'+name+'}}', text)
                    return text
                out[key] = [quoted(x) for x in value] if isinstance(value, list) else quoted(value)
            elif key == 'value' and ('op' in obj or set(obj) == {'value', 'quote'}):
                out[key] = [slot_value(x, quote) for x in value] if isinstance(value, list) else slot_value(value, quote)
            elif key in ('start', 'end') and isinstance(value, str):
                out[key] = slot_value(value, quote)
            else:
                out[key] = visit(value, quote)
        return out
    template = visit(request)
    # Every variable in the question must have an executable binding. We do not
    # generalize a number in a title, model enum, field name or unsupported clause.
    if used != set(slots): return None
    return {'pattern': ''.join(fragments), 'slots': slots, 'request': template}


def instantiate(shape, question):
    match = re.fullmatch(shape['pattern'], question)
    if not match: return None
    raw = match.groupdict()
    def decode(name):
        text = raw[name]
        kind = shape['slots'][name]['kind']
        if kind == 'string': return text[1:-1]
        if kind == 'number': return float(text) if '.' in text else int(text)
        from datetime import date
        return date.fromisoformat(text).isoformat()
    def expand(obj):
        if isinstance(obj, list): return [expand(x) for x in obj]
        if isinstance(obj, dict):
            if set(obj) == {'$slot', 'transform'}:
                value = decode(obj['$slot'])
                transform = obj['transform']
                if transform == 'identity': return value
                if type(value) is not int or not 1900 <= value <= 2100: raise PlanError('Calendar-year slot is invalid')
                return str(value+(transform == 'next_year_start'))+('-12-31' if transform == 'year_end' else '-01-01')
            return {k: expand(v) for k, v in obj.items()}
        if isinstance(obj, str):
            return re.sub(r'\{\{(s\d+)\}\}', lambda m: raw[m[1]], obj)
        return obj
    return expand(shape['request'])


def save(path, catalog, contract, interpretation, policy):
    if interpretation.get('procedural_memory'): return None  # exact cache retains lesson dependencies
    from .learning import correction_context
    if correction_context(path, contract['original_question']): return None
    audit = interpretation.get('semantic_review') or {}
    if (interpretation.get('review') != 'checked_hosted_interpretation' or not interpretation.get('execution_accepted')
            or audit != {'equivalent': True, 'issues': []} or contract['request']['unresolved']):
        return None
    if interpretation.get('request_shape_id'): return interpretation['request_shape_id']
    shape = make(contract['original_question'], contract['request'])
    if not shape: return None
    if instantiate(shape, contract['original_question']) != contract['request']: return None
    shape.update(schema=VERSION, original_question=contract['original_question'], policy=policy,
                 tables=catalog.tables, stream_fields=catalog.stream_fields,
                 source_identity=catalog.identity, population=contract['population'],
                 parent_contract=contract['id'], created=time.time(),
                 qualification='Executed and model-checked; not independently adjudicated',
                 acceptance='Only typed-literal substitutions; full compiler validation required')
    ident = fingerprint({k: v for k, v in shape.items() if k != 'created'})
    with _db(path) as db:
        for (payload,) in db.execute("SELECT payload FROM request_shapes WHERE status='revoked'"):
            if family(json.loads(payload)['original_question']) == family(contract['original_question']):
                return None
        # A correction is durable: a repeat of the teacher cannot revive it.
        db.execute('INSERT OR IGNORE INTO request_shapes VALUES (?,?,?,?,?)',
                   (ident, policy, json.dumps(shape), 'active', ''))
    return ident if valid(path, ident) else None


def lookup(path, catalog, question, policy, population=None):
    from .learning import correction_context
    if correction_context(path, question): return None
    with _db(path) as db:
        rows = db.execute("SELECT id,payload,policy FROM request_shapes WHERE status='active'").fetchall()
    # Shapes saved under the current code first; otherwise a checked shape from an earlier release, recompiled now.
    current = [(i, p) for i, p, pol in rows if pol == policy]
    earlier = [(i, p) for i, p, pol in rows if pol != policy]
    return _match(catalog, question, population, current) or _match(catalog, question, population, earlier, carried=True)


def _match(catalog, question, population, rows, carried=False):
    matches = []
    for ident, payload in rows:
        shape = json.loads(payload)
        if shape['tables'] != catalog.tables or shape['stream_fields'] != catalog.stream_fields: continue
        if shape['source_identity'] != catalog.identity or shape['population'] != (sorted(population) if population is not None else None): continue
        try:
            request = instantiate(shape, question)
            if request is None: continue
            contract = compile_request(catalog, question, request, population=population)
        except (ValueError, TypeError, KeyError): continue
        matches.append((ident, contract))
    if not matches or len({fingerprint(c['request']) for _, c in matches}) != 1: return None
    ident, contract = matches[0]
    return contract, {'interpreter': 'checked_request_shape', 'review': 'typed_slot_recompile',
                      'request_shape_id': ident, 'execution_accepted': True, 'attempts': [],
                      'qualified_interpretation': False,
                      'acceptance_scope': 'Reuse of model-checked structure; not independent adjudication',
                      **({'carry_forward': 'Shape saved under an earlier code release; recompiled under the current code.'}
                         if carried else {})}


def invalidate(path, question, reason):
    from .learning import normalize_question
    with _db(path) as db:
        for ident, payload in db.execute("SELECT id,payload FROM request_shapes WHERE status='active'").fetchall():
            shape = json.loads(payload)
            if family(question) == family(shape['original_question']):
                db.execute("UPDATE request_shapes SET status='revoked',reason=? WHERE id=?", (reason, ident))
        # Exact interpretations of a corrected question are not an escape hatch.
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='shared_requests'").fetchone()
        if exists:
            for ident, payload in db.execute('SELECT id,payload FROM shared_requests').fetchall():
                item = json.loads(payload)
                shape_id = item['interpretation'].get('request_shape_id')
                if normalize_question(item.get('question', '')) == normalize_question(question) or (
                    shape_id and db.execute("SELECT status FROM request_shapes WHERE id=?", (shape_id,)).fetchone() == ('revoked',)):
                    db.execute('DELETE FROM shared_requests WHERE id=?', (ident,))
