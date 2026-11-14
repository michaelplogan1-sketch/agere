"""Opt-in lossless reference tables for new, explicitly scoped contexts.

Historical source contexts and their request bodies are never rewritten. Full
source units remain verbatim; this codec changes only relationship metadata.
"""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import os
import re
import tempfile
from ...contracts import digest
from . import record_context as C, synthesis_availability as A, context_builder as B

VERSION = 'reference-table-context/1'
DECLARATION = ('Reference tables encode ordered objects as columns and rows. Per-column dictionaries map integer cells to original values; other cells are literal. '
               'Each present array gives column positions present in that row; all means every column. Omitted keys differ from null. '
               'A same_as value is an exact JSON pointer to the equivalent reference table in this request. Expand these tables when interpreting typed links; no relationships or source content were removed.')


def identity(): return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), C.identity(), A.identity()])


def matrix(value):
    result = deepcopy(value); dictionaries = {}
    for i in range(len(value['columns'])):
        values = [r[i] for r in value['rows']]
        if not values or any(not isinstance(v, str) for v in values): continue
        unique = list(dict.fromkeys(values)); indices = [unique.index(v) for v in values]
        if len(B.encode(values)) <= len(B.encode([unique, indices])) + 30: continue
        dictionaries[str(i)] = unique
        for row, ix in zip(result['rows'], indices): row[i] = ix
    if dictionaries: result['dictionaries'] = dictionaries
    return result


def unmatrix(value):
    result = deepcopy(value)
    for key, values in result.pop('dictionaries', {}).items():
        if not isinstance(key, str) or not key.isdigit() or not 0 <= int(key) < len(result['columns']): raise ValueError('Invalid reference dictionary column')
        for row in result['rows']:
            cell = row[int(key)]
            if type(cell) is not int or not 0 <= cell < len(values): raise ValueError('Invalid reference dictionary cell')
            row[int(key)] = values[cell]
    return result


def table(rows):
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows): raise ValueError('Ordered reference objects required')
    keys = sorted({k for r in rows for k in r})
    result = {'columns': keys, 'rows': [[r.get(k) for k in keys] for r in rows],
              'present': [[i for i, k in enumerate(keys) if k in r] for r in rows]}
    if all(len(r) == len(keys) for r in rows): result['present'] = 'all'
    result = matrix(result)
    if untable(result) != rows: raise ValueError('Reference table did not reconstruct exactly')
    return result


def untable(value):
    value = unmatrix(value)
    keys, rows, present = value['columns'], value['rows'], value['present']
    if present == 'all': present = [list(range(len(keys))) for _ in rows]
    if len(keys) != len(set(keys)) or len(rows) != len(present): raise ValueError('Ambiguous reference table')
    result = []
    for values, indices in zip(rows, present):
        if (len(values) != len(keys) or len(set(indices)) != len(indices)
                or any(type(i) is not int or not 0 <= i < len(keys) for i in indices)):
            raise ValueError('Invalid reference-table row')
        result.append({keys[i]: values[i] for i in indices})
    return result


def restore_wire(value):
    wire = deepcopy(value); marker = wire.pop('reference_table_transport')
    if marker != {'schema': VERSION, 'instructions': DECLARATION}: raise ValueError('Unknown reference table contract')
    for name in ('dependencies', 'unresolved_references'):
        if name in wire: wire[name] = untable(wire[name])
    scope = wire['received_source_availability']
    if 'records' in scope: scope['records'] = unmatrix(scope['records'])
    for name, original in (('typed_references', 'dependencies'), ('unresolved_typed_references', 'unresolved_references')):
        value = scope[name]
        if isinstance(value, dict) and set(value) == {'same_as'}:
            if value['same_as'] != '/'+original or original not in wire: raise ValueError('Unbound reference table alias')
            scope[name] = deepcopy(wire[original])
        else: scope[name] = untable(value)
    return wire


def compact_body(body):
    result = deepcopy(body); original = json.loads(body['messages'][0]['content']); wire = deepcopy(original)
    scope = wire['received_source_availability']
    if 'records' in scope: scope['records'] = matrix(scope['records'])
    for name, other in (('typed_references', 'dependencies'), ('unresolved_typed_references', 'unresolved_references')):
        scope[name] = {'same_as': '/'+other} if other in original and scope[name] == original[other] else table(scope[name])
    for name in ('dependencies', 'unresolved_references'):
        if name in wire: wire[name] = table(wire[name])
    wire['reference_table_transport'] = {'schema': VERSION, 'instructions': DECLARATION}
    if B.encode(restore_wire(wire)) != B.encode(original): raise ValueError('Reference metadata reconstruction changed')
    result['messages'][0]['content'] = B.encode(wire).decode()
    return result


def transform(parent, *, reference_identity=None):
    if parent.get('schema') == VERSION: raise ValueError('Context already uses reference tables')
    row = deepcopy(parent); row.pop('id')
    row.update(schema=VERSION, reference_table_identity=reference_identity or identity(), reference_parent_id=parent['id'], reference_parent_hash=digest(parent))
    row['missing_evidence']['record_scope']['reference_table_transport'] = VERSION
    row['model_context']['missing_evidence'] = row['missing_evidence']
    row['budget']['used'] = len(B.encode(row['model_context']))
    if row['budget']['used'] > row['parameters']['max_bytes']: raise ValueError('Reference contract exceeds declared context bound')
    row['id'] = digest(row)
    return row


def location(path, key):
    if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key): raise ValueError('Exact reference context ID required')
    return Path(path)/'reference-table-contexts'/(key+'.json')


def exists(path, key): return location(path, key).exists()


def create(path, key):
    parent = C.verified(path, key); row = transform(parent)
    if digest(C.inspect(path, key)) != row['reference_parent_hash']: raise ValueError('Original context changed')
    target = location(path, row['id']); target.parent.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, prefix='.references-', delete=False) as stream:
        temporary = Path(stream.name); json.dump(row, stream, indent=2, allow_nan=False)
    try: os.replace(temporary, target)
    finally: temporary.unlink(missing_ok=True)
    return row


def inspect(path, key):
    row = json.loads(location(path, key).read_text())
    if row['id'] != key or digest({k: v for k, v in row.items() if k != 'id'}) != key: raise ValueError('Reference context integrity changed')
    historical=row.get('reference_table_identity')
    if not isinstance(historical,str) or not re.fullmatch('[a-f0-9]{64}',historical):raise ValueError('Invalid reference transport provenance')
    parent = C.inspect(path, row['reference_parent_id'])
    # Reconstruct every current context field from the freshly verified parent.
    # Holding only its historical stamp preserves immutable IDs, never a source
    # or model judgment. Actual requests/results retain their own validation.
    rebuilt=transform(parent,reference_identity=historical)
    if parent.get('schema') == VERSION or digest(parent) != row['reference_parent_hash'] or rebuilt != row:
        raise ValueError('Reference transport differs from original context')
    current=identity()
    if historical!=current:
        from .case_passes import atomic_write
        proof={'schema':'reference-context-current-revalidation/1','status':'passed','context_id':key,
               'parent_id':parent['id'],'parent_digest':digest(parent),'historical_identity':historical,
               'current_identity':current,'all_current_context_fields_equal':True,
               'provider_calls':0,'clinical_qualification_transferred':False}
        target=Path(path)/'reference-context-current-revalidation'/(key+'.json');target.parent.mkdir(exist_ok=True)
        if not target.exists() or json.loads(target.read_text())!=proof:atomic_write(target,proof)
    return row


def install():
    if getattr(C.inspect, 'reference_tables', False): return
    previous_inspect, previous_exists, previous_envelope = C.inspect, C.exists, A.envelope
    def inspect_any(path, key): return inspect(path, key) if exists(path, key) else previous_inspect(path, key)
    def exists_any(path, key): return exists(path, key) or previous_exists(path, key)
    def envelope(body, units, links, received, *, excerpts=None):
        original = previous_envelope(body, units, links, received, excerpts=excerpts)
        marker = received['packet'].get('missing_evidence', {}).get('record_scope', {}).get('reference_table_transport')
        return compact_body(original) if marker == VERSION else original
    inspect_any.reference_tables = True
    C.inspect, C.exists, A.envelope = inspect_any, exists_any, envelope
