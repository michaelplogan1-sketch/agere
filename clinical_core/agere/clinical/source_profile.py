"""Observed source profile: which fields each received stream actually populates.

Plans must be grounded in the received data, not in a declared schema. A field
that a schema lists but no record populates is a binding failure; evaluating it
per patient turns one technical error into thousands of false "unknown" states.
The profile is computed once per source digest and cached beside the store.
"""
from pathlib import Path
import json, sqlite3, time

VERSION = 'source-profile/1'
LOW_CARDINALITY = 400      # string fields with at most this many values are enumerated
SKIP_VALUE_SUFFIXES = ('_id', '_ids', '_time', '_date', 'record_id')
MEASUREMENT_STREAMS = {'labs': ('test', 'unit', 'value'), 'vitals': ('measurement', 'unit', 'value')}


def _connect(db_path):
    return sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)


def build(db_path):
    db = _connect(db_path)
    start = time.perf_counter()
    try:
        streams = {s: {'records': n, 'fields': {}} for s, n in
                   db.execute('SELECT stream, count(*) FROM records GROUP BY stream')}
        for stream, key, kind, n, distinct in db.execute(
                "SELECT r.stream, j.key, j.type, count(*), count(DISTINCT j.value) "
                "FROM records r, json_each(r.payload) j WHERE substr(j.key,1,1) <> '_' GROUP BY 1,2,3"):
            f = streams[stream]['fields'].setdefault(key, {'populated': 0, 'types': {}, 'distinct': 0})
            f['types'][kind] = n
            if kind != 'null':
                f['populated'] += n
                f['distinct'] = max(f['distinct'], distinct)
        for stream, key, lo, hi in db.execute(
                "SELECT r.stream, j.key, min(j.value), max(j.value) FROM records r, json_each(r.payload) j "
                "WHERE j.type IN ('integer','real') AND substr(j.key,1,1) <> '_' GROUP BY 1,2"):
            streams[stream]['fields'][key]['numeric_range'] = [lo, hi]
        wanted = {(s, k) for s, v in streams.items() for k, f in v['fields'].items()
                  if f['types'].get('text') and f['distinct'] <= LOW_CARDINALITY
                  and not k.endswith(SKIP_VALUE_SUFFIXES)}
        for stream, key, value, n in db.execute(
                "SELECT r.stream, j.key, j.value, count(*) FROM records r, json_each(r.payload) j "
                "WHERE j.type='text' AND substr(j.key,1,1) <> '_' GROUP BY 1,2,3"):
            if (stream, key) in wanted:
                streams[stream]['fields'][key].setdefault('values', {})[value] = n
        # List-valued code fields (e.g. claim diagnoses) are enumerated by element.
        for stream, key, value, n in db.execute(
                "SELECT r.stream, j.key, e.value, count(*) FROM records r, json_each(r.payload) j, json_each(j.value) e "
                "WHERE j.type='array' AND e.type='text' AND substr(j.key,1,1) <> '_' GROUP BY 1,2,3"):
            f = streams[stream]['fields'][key]
            elements = f.setdefault('element_values', {})
            if len(elements) < 5000: elements[value] = n
        measurements = {}
        for stream, (name_field, unit_field, value_field) in MEASUREMENT_STREAMS.items():
            if stream not in streams or name_field not in streams[stream]['fields']: continue
            for name, unit, n, numeric, lo, hi in db.execute(
                    f"SELECT json_extract(payload,'$.{name_field}'), json_extract(payload,'$.{unit_field}'), count(*), "
                    f"sum(json_type(payload,'$.{value_field}') IN ('integer','real')), "
                    f"min(json_extract(payload,'$.{value_field}')), max(json_extract(payload,'$.{value_field}')) "
                    f"FROM records WHERE stream=? GROUP BY 1,2", (stream,)):
                measurements.setdefault(stream, []).append(
                    {'name': name, 'unit': unit, 'records': n, 'numeric': numeric or 0, 'range': [lo, hi]})
        patients = {'records': db.execute('SELECT count(*) FROM patients').fetchone()[0], 'fields': {}}
        for key, kind, n in db.execute(
                "SELECT j.key, j.type, count(*) FROM patients p, json_each(p.payload) j GROUP BY 1,2"):
            f = patients['fields'].setdefault(key, {'populated': 0, 'types': {}})
            f['types'][kind] = n
            if kind != 'null': f['populated'] += n
    finally:
        db.close()
    return {'schema': VERSION, 'streams': streams, 'patients': patients, 'measurements': measurements,
            'elapsed_seconds': round(time.perf_counter() - start, 2)}


_CACHE = {}


def load(path):
    """Profile for a product build directory (the one holding run.json and stack.sqlite3)."""
    path = Path(path)
    run = path / 'run.json'
    if run.exists():
        digest = json.loads(run.read_text())['source_digest']
    else:  # bare stores (tests, ad-hoc builds): identify by the database file itself
        stat = (path / 'stack.sqlite3').stat()
        digest = f'unversioned:{stat.st_size}:{stat.st_mtime_ns}'
    meta = {'source_digest': digest}
    key = (str(path), meta['source_digest'])
    if key in _CACHE: return _CACHE[key]
    target = path / 'source-profile.json'
    if target.exists():
        saved = json.loads(target.read_text())
        if saved.get('schema') == VERSION and saved.get('source_digest') == meta['source_digest']:
            _CACHE[key] = saved
            return saved
    profile = {**build(path / 'stack.sqlite3'), 'source_digest': meta['source_digest']}
    tmp = target.with_suffix('.tmp')
    tmp.write_text(json.dumps(profile))
    tmp.replace(target)
    _CACHE[key] = profile
    return profile


def field(profile, stream, name):
    if stream == 'patients': return profile['patients']['fields'].get(name)
    return profile['streams'].get(stream, {}).get('fields', {}).get(name)


def populated(profile, stream, name):
    f = field(profile, stream, name)
    return bool(f and f['populated'])


PLANNER_SKIP = {'synthetic', 'source_table', 'identity_link_basis', 'identity_status', 'event_date_precision',
                'source_patient_id', 'patient_id'}


def summary(profile, max_values=15, max_value_length=40):
    """Compact view for planners: populated fields per stream and short coded vocabularies.

    Free text is summarised as 'text' so prompts stay small; ranges show numeric fields.
    """
    out = {}
    for stream, s in sorted(profile['streams'].items()):
        fields = {}
        for name, f in sorted(s['fields'].items()):
            if not f['populated'] or name.endswith(SKIP_VALUE_SUFFIXES) or name in PLANNER_SKIP:
                continue
            values = f.get('values') or f.get('element_values')
            if f['types'].get('true') or f['types'].get('false'):
                fields[name] = 'boolean (true/false; null = not reported)'
            elif 'numeric_range' in f:
                fields[name] = f['numeric_range']
            elif values and sorted(map(len, values))[len(values) // 2] <= max_value_length:
                ranked = [v for v in sorted(values, key=lambda v: -values[v]) if len(v) <= max_value_length]
                fields[name] = ranked[:max_values] + ([f'+{len(values) - max_values} rarer values'] if len(values) > max_values else [])
            else:
                fields[name] = 'text'
        out[stream] = {'records': s['records'], 'fields': fields}
    return out
