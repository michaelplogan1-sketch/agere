"""Bounded discovery over the received catalog; probes are evidence, not answers.

No model SQL is accepted. A failed or empty probe never establishes clinical or
warehouse-wide absence. Search terms and source text remain untrusted data.
"""
import json
import os
import re
import sqlite3
import time

from .request_plan import PlanError, fingerprint, identifier

VERSION = 'request-discovery/1'
VALUE_VERSION = 'relation-values/1'
VALUE_LIMIT = 64
# Identifiers, contact details, clocks and free text are never indexed as categories.
_UNINDEXED_TOKENS = {'id', 'ids', 'mrn', 'ssn', 'npi', 'tin', 'email', 'phone', 'address', 'zip', 'postal',
                     'name', 'first', 'last', 'dob', 'birth', 'date', 'time', 'at', 'cents', 'payload',
                     'text', 'sentence', 'excerpt', 'comment', 'detail', 'value'}
_VALUE_CACHE = {}


def tokens(text):
    words = re.findall(r'[a-z0-9]+', str(text).lower().replace('_', ' '))
    return {w[:-1] if len(w) > 4 and w.endswith('s') and not w.endswith('ss') else w
            for w in words if len(w) > 2}


def _indexable(column, declared_type):
    if declared_type not in ('', 'TEXT') and 'CHAR' not in declared_type:
        return False
    return not set(re.findall(r'[a-z0-9]+', column.lower().replace('_', ' '))) & _UNINDEXED_TOKENS


def relation_values(catalog, *, seconds=1.0, total_seconds=30.0):
    """Bounded distinct categorical values of catalog relations, bound to the source identity.

    Derived relations (extracted facts, normalized concepts, specialties, statuses) carry their
    meaning in values, not column names. Only low-cardinality text categories are kept; a slow,
    high-cardinality or skipped column is reported, never treated as absent.
    """
    key = fingerprint({'target': str(catalog.target), 'identity': catalog.identity, 'tables': catalog.tables,
                       'version': VALUE_VERSION, 'limit': VALUE_LIMIT})
    out = {'version': VALUE_VERSION, 'source_identity_digest': fingerprint(catalog.identity),
           'fields': {}, 'high_cardinality': [], 'incomplete': [], 'limit': VALUE_LIMIT}
    db = sqlite3.connect(f'file:{catalog.target}?mode=ro', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        # Checked on every call: a cached scan is never served for a stale catalog.
        from .request_plan import warehouse_metadata
        if warehouse_metadata(catalog.target, db) != catalog.identity:
            raise PlanError('Warehouse identity changed before value discovery')
        if key in _VALUE_CACHE:
            return _VALUE_CACHE[key]
        started = time.monotonic()
        for table in sorted(catalog.tables):
            declared = {r[1]: (r[2] or '').upper() for r in db.execute('PRAGMA table_info(' + identifier(table) + ')')}
            for column in catalog.tables[table]:
                if column not in declared or not _indexable(column, declared[column]):
                    continue  # virtual (computed) or non-categorical field
                name = table + '.' + column
                if time.monotonic() - started > total_seconds:
                    out['incomplete'].append(name)
                    continue
                begin = time.monotonic()
                db.set_progress_handler(lambda: int(time.monotonic() - begin > seconds), 1000)
                try:
                    found = [r[0] for r in db.execute(
                        'SELECT DISTINCT ' + identifier(column) + ' FROM ' + identifier(table) +
                        ' WHERE ' + identifier(column) + ' IS NOT NULL LIMIT ?', (VALUE_LIMIT + 1,))]
                except sqlite3.Error:
                    out['incomplete'].append(name)
                    continue
                finally:
                    db.set_progress_handler(None, 0)
                if len(found) > VALUE_LIMIT:
                    out['high_cardinality'].append(name)
                    continue
                kept = sorted(v for v in found if isinstance(v, str) and 0 < len(v.strip()) <= 160)
                if kept:
                    out['fields'][name] = kept
    finally:
        db.close()
    if not out['incomplete']:  # a transient slow scan is retried, never frozen as the identity's scope
        if len(_VALUE_CACHE) >= 4:
            _VALUE_CACHE.pop(next(iter(_VALUE_CACHE)))
        _VALUE_CACHE[key] = out
    return out


def search(catalog, terms, *, limit=20):
    """Scan every catalog entry, returning bounded candidates and search scope."""
    from .semantic_layer import SCHEMA_DOC
    if not isinstance(terms, list) or not terms or any(
            not isinstance(t, str) or not 1 <= len(t.strip()) <= 160 for t in terms):
        raise PlanError('Discovery needs literal search terms')
    wanted = tokens(' '.join(terms))
    candidates = []
    def matches(text):
        available = tokens(text)
        exact = wanted & available
        # Candidate expansion only, never a semantic equivalence or SQL filter.
        related = {w for w in wanted if len(w) >= 6 and any(
            len(a) >= 6 and a[:4] == w[:4] for a in available)}
        return exact, related - exact
    def value_match(value):
        # Observed values are stricter than names: exact words or a shared stem of 7+ letters.
        available = tokens(value)
        return bool(wanted & available) or any(
            len(os.path.commonprefix([w, a])) >= 7 for w in wanted for a in available)
    relation_scan = relation_values(catalog)
    for table, columns in catalog.tables.items():
        fields = [c for c in columns if any(matches(c))]
        value_hits = {}
        for column in columns:
            hits = [v for v in relation_scan['fields'].get(table + '.' + column, []) if value_match(v)]
            if hits:
                value_hits[column] = hits[:5]
        overlap, related = matches(table + ' ' + ' '.join(columns) + ' ' + SCHEMA_DOC.get(table, '') + ' ' +
                                   ' '.join(v for hits in value_hits.values() for v in hits))
        if overlap or related:
            entry = {'kind': 'relation', 'name': table,
                     'fields': list(dict.fromkeys([*fields, *value_hits]))[:24],
                     'score': 2 * len(overlap) + len(related), 'matched_terms': sorted(overlap),
                     'approximate_terms': sorted(related)}
            if value_hits:
                # Literal observed category values; usable as exact predicate values after schema review.
                entry['matched_values'] = dict(list(value_hits.items())[:8])
            candidates.append(entry)
    profile = getattr(catalog, 'profile', {})
    observed_streams = profile.get('streams', {}) if profile.get('source_digest') == catalog.identity.get('source_digest') else {}
    value_fields = []
    for stream, columns in catalog.stream_fields.items():
        profile_fields = observed_streams.get(stream, {}).get('fields', {})
        values = {}
        for column in columns:
            observed = profile_fields.get(column, {}).get('values', {})
            if isinstance(observed, dict) and 0 < len(observed) <= 64:
                values[column] = ' '.join(str(v) for v in observed if len(str(v)) <= 160)
                value_fields.append(stream + '.' + column)
        overlap, related = matches(stream + ' ' + ' '.join(columns) + ' ' + ' '.join(values.values()))
        if overlap or related:
            candidates.append({'kind': 'received_stream', 'name': stream,
                               'fields': [c for c in columns if any(matches(c+' '+values.get(c, '')))][:24],
                               'profile_value_fields': [c for c, text in values.items() if any(matches(text))][:24],
                               'score': 2 * len(overlap) + len(related), 'matched_terms': sorted(overlap),
                               'approximate_terms': sorted(related)})
    # Equal lexical scores prefer observed literal values, then directly queryable relations.
    candidates.sort(key=lambda x: (-x['score'], 'matched_values' not in x, x['kind'] != 'relation', x['name']))
    return {'version': VERSION, 'source_identity': catalog.identity, 'terms': terms,
            'candidates': candidates[:limit], 'candidate_count': len(candidates),
            'truncated': len(candidates) > limit, 'catalog_scan_complete': True,
            'searched': {'relations': sorted(catalog.tables), 'streams': sorted(catalog.stream_fields),
                         'bounded_profile_value_fields': sorted(value_fields)},
            # Top level so compact model views keep the scan's completeness, not only a count.
            'relation_value_scan': {'version': relation_scan['version'],
                                    'source_identity_digest': relation_scan['source_identity_digest'],
                                    'categorical_fields': len(relation_scan['fields']),
                                    'value_limit': relation_scan['limit'],
                                    'high_cardinality_fields': len(relation_scan['high_cardinality']),
                                    'complete': not relation_scan['incomplete'],
                                    'incomplete_fields': relation_scan['incomplete'][:24]},
            'record_search_performed': False, 'data_absence_established': False,
            'limitation': 'Lexical/approximate metadata, bounded current-profile values and bounded categorical relation values only, not semantic equivalence. High-cardinality, free-text and identifier fields are not value-indexed. No match does not establish missing data; request schemas or bounded field probes.'}


def describe(catalog, tables, streams=()):
    """Actual key/type metadata plus existing source/extraction definitions."""
    from .semantic_layer import SCHEMA_DOC
    tables = list(dict.fromkeys(tables))
    if any(t not in catalog.tables for t in tables):
        raise PlanError('Choose catalog relations')
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
        from .request_plan import warehouse_metadata
        if warehouse_metadata(catalog.target, db) != catalog.identity:
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
        if not isinstance(probes, list) or not probes:
            raise PlanError('Provide evidence probes as a list')
        out['probes'] = [probe(catalog, p, population=population) for p in probes]
    return out


def prompt_view(receipts, *, max_bytes=None):
    """Compact model view; complete source identities and queries stay in receipts.

    Older detail may be omitted to bound the tool loop. That omission is explicit,
    and cannot establish completeness or absence.
    """
    projected = []
    for receipt in receipts:
        item = {}
        for key, entries in receipt.items():
            many = entries if isinstance(entries, list) else [entries]
            compact = []
            for entry in many:
                view = {k: v for k, v in entry.items()
                        if k not in ('source_identity', 'query', 'searched', 'elapsed_seconds')}
                identity = entry['source_identity']
                view['source_identity'] = {k: identity.get(k) for k in ('source_digest', 'version', 'as_of')}
                view['source_identity']['fingerprint'] = fingerprint(identity)
                if 'searched' in entry:
                    view['searched'] = {k + '_count': len(v) for k, v in entry['searched'].items()}
                    view['searched']['catalog_fingerprint'] = fingerprint(entry['searched'])
                view['receipt_id'] = entry.get('id') or fingerprint(entry)
                compact.append(view)
            item[key] = compact if isinstance(entries, list) else compact[0]
        projected.append(item)
    # Retain recent detail first; older requests/statuses remain inspectable.
    for item in projected:
        if max_bytes is None or len(json.dumps(projected, separators=(',', ':')).encode()) <= max_bytes:
            break
        for key, entries in item.items():
            for entry in entries if isinstance(entries, list) else [entries]:
                entry.pop('rows', None)
                entry.pop('candidates', None)
                entry['prompt_detail_omitted'] = 'Full observations retained in the saved receipt; omission proves no absence.'
    if max_bytes is not None and len(json.dumps(projected, separators=(',', ':')).encode()) > max_bytes:
        return [{'receipt_fingerprint': fingerprint(r),
                 'prompt_detail_omitted': 'Discovery detail exceeds prompt allowance; full evidence saved. Request narrower probes or leave interpretation unresolved.',
                 'data_absence_established': False} for r in receipts]
    return projected
