"""Bounded discovery over the received catalog; probes are evidence, not answers.

No model SQL is accepted. A failed or empty probe never establishes clinical or
warehouse-wide absence. Search terms and source text remain untrusted data.
"""
import json
import re
import sqlite3
import time

from .request_plan import PlanError, fingerprint, identifier

VERSION = 'request-discovery/1'


def tokens(text):
    return set(re.findall(r'[a-z0-9]+', str(text).lower().replace('_', ' ')))


def search(catalog, terms, *, limit=20):
    """Scan every catalog entry, returning bounded candidates and search scope."""
    from .semantic_layer import SCHEMA_DOC
    if not isinstance(terms, list) or not 1 <= len(terms) <= 8 or any(
            not isinstance(t, str) or not 1 <= len(t.strip()) <= 160 for t in terms):
        raise PlanError('Discovery needs 1..8 bounded literal search terms')
    wanted = tokens(' '.join(terms))
    candidates = []
    for table, columns in catalog.tables.items():
        fields = [c for c in columns if wanted & tokens(c)]
        overlap = wanted & tokens(table + ' ' + ' '.join(columns) + ' ' + SCHEMA_DOC.get(table, ''))
        if overlap:
            candidates.append({'kind': 'relation', 'name': table, 'fields': fields[:24],
                               'score': len(overlap), 'matched_terms': sorted(overlap)})
    for stream, columns in catalog.stream_fields.items():
        overlap = wanted & tokens(stream + ' ' + ' '.join(columns))
        if overlap:
            candidates.append({'kind': 'received_stream', 'name': stream,
                               'fields': [c for c in columns if wanted & tokens(c)][:24],
                               'score': len(overlap), 'matched_terms': sorted(overlap)})
    candidates.sort(key=lambda x: (-x['score'], x['kind'], x['name']))
    return {'version': VERSION, 'source_identity': catalog.identity, 'terms': terms,
            'candidates': candidates[:limit], 'candidate_count': len(candidates),
            'truncated': len(candidates) > limit, 'catalog_scan_complete': True,
            'searched': {'relations': sorted(catalog.tables), 'streams': sorted(catalog.stream_fields)},
            'record_search_performed': False, 'data_absence_established': False,
            'limitation': 'Lexical metadata candidates only. No match does not establish missing data; request schemas or bounded field probes.'}


def describe(catalog, tables, streams=()):
    """Actual key/type metadata plus existing source/extraction definitions."""
    from .semantic_layer import SCHEMA_DOC
    tables = list(dict.fromkeys(tables))
    if len(tables) > 16 or any(t not in catalog.tables for t in tables):
        raise PlanError('Choose at most 16 catalog relations')
    output = {}
    with sqlite3.connect(f'file:{catalog.target}?mode=ro', uri=True) as db:
        for table in tables:
            columns = {r[1]: {**({'sqlite_type': r[2]} if r[2] else {}),
                             **({'declared_not_null': True} if r[3] else {}),
                             **({'primary_key_position': r[5]} if r[5] else {})}
                       for r in db.execute('PRAGMA table_info(' + identifier(table) + ')')
                       if r[1] in catalog.tables[table]}
            if table == 'patient' and 'age' in catalog.tables[table]:
                columns['age'] = {'derived': 'Completed years from birth_date at catalog as_of',
                                  'unit': 'years', 'missing_birth_date': 'unknown'}
            for name, item in columns.items():
                if name.endswith('_cents'):
                    item['unit'] = 'cents'
            keys = [[r[1] for r in sorted(
                db.execute('PRAGMA table_info(' + identifier(table) + ')'), key=lambda r: r[5]) if r[5]]]
            for idx in db.execute('PRAGMA index_list(' + identifier(table) + ')'):
                # Partial/expression indexes cannot justify unconditional unique joins.
                if not idx[2] or idx[4]:
                    continue
                names = [r[2] for r in db.execute('PRAGMA index_info(' + identifier(idx[1]) + ')')]
                if names and all(n in columns for n in names):
                    keys.append(names)
            fks = [{'target': r[2], 'from': r[3], 'to': r[4], 'group': r[0], 'position': r[1]}
                   for r in db.execute('PRAGMA foreign_key_list(' + identifier(table) + ')')]
            output[table] = {
                'columns': columns, 'meaning': SCHEMA_DOC.get(table, 'Received relation; semantics need source review.'),
                'declared_unique_keys': [k for k in keys if k], 'declared_foreign_keys': fks,
                'clock_fields': [c for c in columns if c.endswith(('_date', '_time', '_at'))],
                'clock_policy': 'Field names are candidates, not interchangeable clocks; preserve the requested event/availability basis.',
                'lineage_fields': [c for c in columns if c in ('record_id', 'record_ids', 'source_stream', 'source_table', 'sources')],
                'extraction_coverage': 'Not established by table or column presence.',
            }
            if table == 'note_fact':
                output[table]['extraction_metadata'] = catalog.identity.get('note_fact_version')
                output[table]['extraction_coverage'] = 'Limited to admitted extractor concepts/version; missing extracted facts do not establish absent clinical facts.'
    profile = getattr(catalog, 'profile', {})
    current = profile.get('source_digest') == catalog.identity.get('source_digest')
    raw = {}
    for stream in streams:
        if stream not in catalog.stream_fields:
            raise PlanError('Unknown received stream')
        observed = profile.get('streams', {}).get(stream, {}) if current else {}
        raw[stream] = {
            'profile_available': bool(observed), 'records': observed.get('records'),
            'fields': {name: {k: v for k, v in observed.get('fields', {}).get(name, {}).items()
                              if k in ('populated', 'types', 'distinct', 'numeric_range')}
                       for name in catalog.stream_fields[stream]},
            'meaning': 'Received payload fields; populated counts are source observations, not evidence of completeness.',
        }
    return {'relations': output, 'received_streams': raw}


def probe(catalog, spec, *, population=None, seconds=2.0):
    """A typed values/excerpt request, read-only and bound to the saved population."""
    allowed = {'table', 'field', 'mode', 'contains', 'stream'}
    if not isinstance(spec, dict) or set(spec) - allowed or not {'table', 'field', 'mode'} <= set(spec):
        raise PlanError('Evidence probe needs table, field and values|excerpts mode')
    table, field, mode = spec['table'], spec['field'], spec['mode']
    if table not in catalog.tables or mode not in ('values', 'excerpts'):
        raise PlanError('Unknown evidence relation or mode')
    relation, column = catalog.field(field)
    if relation != table:
        raise PlanError('Probe field must belong to its declared relation')
    contains = spec.get('contains')
    if contains is not None and (not isinstance(contains, str) or not 1 <= len(contains) <= 160):
        raise PlanError('Probe contains must be bounded literal text')
    if population is not None and ('patient_id' not in catalog.tables[table] or
            not isinstance(population, list) or any(not isinstance(p, str) for p in population)):
        raise PlanError('This relation cannot preserve the saved patient scope')
    params, where = [], []
    if column.startswith('payload.'):
        stream = spec.get('stream')
        if stream not in catalog.stream_fields or column[8:] not in catalog.stream_fields[stream]:
            raise PlanError('Raw payload probe requires the exact stream containing this field')
        expr = 'json_extract(b."payload", ?)'
        params.append('$.' + column[8:])
    else:
        if column == 'age' and table == 'patient':
            raise PlanError('Virtual age is computed by the request compiler, not a received-field probe')
        expr = 'b.' + identifier(column)
    # CTE evaluates a payload path once, without duplicating positional parameters.
    carry = [c for c in ('patient_id', 'record_id', 'event_date', 'recorded_date', 'stream')
             if c in catalog.tables[table]]
    cte = 'SELECT ' + expr + ' AS probe_value' + ''.join(', b.' + identifier(c) for c in carry)
    cte += ' FROM ' + identifier(table) + ' AS b'
    if 'stream' in spec:
        if table not in ('record', 'current_record') or spec['stream'] not in catalog.stream_fields:
            raise PlanError('Stream restriction requires a received record relation')
        where.append('"stream" = ?')
        params.append(spec['stream'])
    if population is not None:
        where.append('"patient_id" IN (SELECT value FROM json_each(?))')
        params.append(json.dumps(population))
    where.append('probe_value IS NOT NULL')
    if contains is not None:
        where.append("instr(lower(CAST(probe_value AS TEXT)), lower(?)) > 0")
        params.append(contains)
    limit = 24 if mode == 'values' else 5
    select = 'DISTINCT probe_value' if mode == 'values' else 'probe_value' + ''.join(', ' + identifier(c) for c in carry)
    query = 'WITH received_probe AS (' + cte + ') SELECT ' + select + ' FROM received_probe WHERE ' + ' AND '.join(where) + ' LIMIT ?'
    params.append(limit + 1)
    receipt = {'version': VERSION, 'source_identity': catalog.identity, 'request': spec,
               'scope': {'population': 'warehouse' if population is None else 'saved_patient_set',
                         'patient_count': None if population is None else len(set(population)),
                         'patient_digest': None if population is None else fingerprint(sorted(set(population)))},
               'query': query, 'query_parameters_digest': fingerprint(params),
               'data_absence_established': False, 'rows': [], 'truncated': False,
               'limitation': 'Received values/excerpts only. Substring matches do not establish subject, negation, timing or clinical truth. An empty probe is not global or clinical absence.'}
    start = time.monotonic()
    db = sqlite3.connect(f'file:{catalog.target}?mode=ro', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        if dict(db.execute('SELECT key,value FROM meta')) != catalog.identity:
            raise PlanError('Warehouse identity changed before evidence discovery')
        db.set_progress_handler(lambda: int(time.monotonic() - start > seconds), 1000)
        db.row_factory = sqlite3.Row
        found = db.execute(query, params).fetchall()
        receipt['truncated'] = len(found) > limit
        for row in found[:limit]:
            value = dict(row)
            for k, v in list(value.items()):
                if isinstance(v, bytes):
                    value[k] = '[binary content omitted]'
            shortened = [k for k, v in value.items() if isinstance(v, str) and len(v) > 800]
            for k in shortened:
                value[k] = value[k][:800]
            value['truncated_fields'] = shortened
            receipt['rows'].append(value)
        receipt['status'] = 'observations_found' if found else 'no_matching_received_values_in_probe_scope'
        receipt['scan_complete'] = not receipt['truncated']
    except sqlite3.Error as exc:
        receipt.update(status='search_incomplete', scan_complete=False, error=type(exc).__name__)
    finally:
        db.close()
    receipt['elapsed_seconds'] = round(time.monotonic() - start, 4)
    receipt['id'] = fingerprint({k: v for k, v in receipt.items() if k != 'elapsed_seconds'})
    return receipt


def run(catalog, request, *, population=None):
    if not isinstance(request, dict) or not request or set(request) - {'terms', 'probes'}:
        raise PlanError('need_discovery accepts terms and/or probes')
    out = {}
    if 'terms' in request:
        out['catalog_search'] = search(catalog, request['terms'])
    if 'probes' in request:
        probes = request['probes']
        if not isinstance(probes, list) or not 1 <= len(probes) <= 3:
            raise PlanError('Choose 1..3 bounded evidence probes')
        out['probes'] = [probe(catalog, p, population=population) for p in probes]
    return out
