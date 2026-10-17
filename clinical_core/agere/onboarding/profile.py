"""Source profiling: tables, columns, value shapes, code systems, candidate keys and joins.

Profiles are computed from a bounded sample per table (default 5,000 rows; the whole table
when smaller) so profiling a large warehouse costs one LIMIT query per table plus one
key-column scan per candidate key. Nothing is written to the source.
"""
from collections import Counter
import re

from . import codes

ABBREVIATIONS = {
    'pt': 'patient', 'pat': 'patient', 'mbr': 'member', 'enc': 'encounter', 'dx': 'diagnosis', 'px': 'procedure',
    'rx': 'rx', 'dt': 'date', 'dttm': 'datetime', 'ts': 'timestamp', 'amt': 'amount', 'qty': 'quantity', 'num': 'number',
    'nbr': 'number', 'no': 'number', 'cd': 'code', 'desc': 'description', 'dob': 'birth', 'prov': 'provider',
    'org': 'organization', 'dept': 'department', 'svc': 'service', 'chg': 'charge', 'pd': 'paid', 'src': 'source',
    'val': 'value', 'loc': 'location', 'adm': 'admit', 'disch': 'discharge', 'id': 'id', 'csn': 'csn', 'mrn': 'mrn',
    'uom': 'unit', 'ref': 'reference', 'stat': 'status', 'yr': 'year', 'addr': 'address', 'lang': 'language',
    'med': 'medication', 'meds': 'medication', 'clm': 'claim', 'payor': 'payer', 'cvg': 'coverage', 'eff': 'effective',
    'fin': 'financial', 'proc': 'procedure', 'ser': 'provider', 'ord': 'order', 'tot': 'total', 'resp': 'responsibility',
    'dep': 'department', 'dept': 'department', 'svc': 'service', 'diag': 'diagnosis', 'icd10': 'icd10',
    'sk': 'key', 'dos': 'date', 'nbr': 'number', 'cd': 'code',
}


def tokens(name):
    s = re.sub(r'([a-z])([A-Z])', r'\1 \2', str(name))
    parts = [p for p in re.split(r'[^A-Za-z0-9]+|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])', s.lower()) if p]
    return [ABBREVIATIONS.get(p, p) for p in parts]


def _storage(values, declared):
    vs = [v for v in values if v is not None]
    if not vs: return (declared or 'unknown').lower()
    if all(isinstance(v, bool) or isinstance(v, int) for v in vs): return 'integer'
    if all(isinstance(v, (int, float)) for v in vs): return 'real'
    return 'text'


def profile_column(name, declared, values, row_count):
    n = len(values)
    nonnull = [v for v in values if v is not None and str(v).strip() != '']
    counts = Counter(map(str, nonnull))
    toks = tokens(name)
    storage = _storage(values, declared)
    col = {'name': name, 'declared_type': declared, 'storage': storage, 'tokens': toks,
           'null_rate': round(1 - len(nonnull) / n, 4) if n else None,
           'distinct': len(counts), 'distinct_ratio': round(len(counts) / len(nonnull), 4) if nonnull else 0.0,
           'unique': bool(nonnull) and len(counts) == len(nonnull) and len(nonnull) == n,
           'top_values': counts.most_common(8),
           'digit_share': round(sum(1 for v in nonnull if any(ch.isdigit() for ch in str(v))) / len(nonnull), 4) if nonnull else 0.0,
'samples': [str(v)[:60] for v in list(dict.fromkeys(map(str, nonnull)))[:5]]}
    col['code'] = codes.classify_values(nonnull[:2000], toks)
    col['semantic'] = codes.classify_semantic(nonnull[:2000], storage, toks)
    if col['code']['system'] and col['semantic'] in ('identifier', 'categorical', 'integer', 'text'):
        col['semantic'] = 'code'
    if col['semantic'] in ('integer', 'decimal', 'money_cents', 'money_dollars'):
        nums = []
        for v in nonnull:  # a mostly-numeric column can carry stray text (e.g. 'TSH' in a shifted row): skip, don't fail
            try:
                nums.append(float(v))
            except (TypeError, ValueError):
                continue
        if nums:
            col['numeric'] = {'min': min(nums), 'max': max(nums), 'mean': round(sum(nums) / len(nums), 4),
                              'max_decimals': max(codes.decimals(v) for v in nonnull), 'non_numeric': len(nonnull) - len(nums)}
    return col


def profile_table(conn, table, sample_rows=5000):
    cols = conn.columns(table)
    rows = conn.sample(table, sample_rows)
    count = conn.row_count(table)
    out = {'name': table, 'row_count': count, 'sampled_rows': len(rows), 'complete_sample': len(rows) >= count, 'tokens': tokens(table),
           'columns': {}}
    for name, declared in cols:
        out['columns'][name] = profile_column(name, declared, [r.get(name) for r in rows], count)
    keys = [c for c, p in out['columns'].items() if p['unique'] and p['null_rate'] == 0]
    out['candidate_keys'] = keys
    # Repeating business keys: identifier-like column whose values repeat a little (versions) and a timestamp to order by.
    out['version_candidates'] = [c for c, p in out['columns'].items()
                                 if p['semantic'] in ('identifier', 'integer', 'code') and 0.3 <= p['distinct_ratio'] < 1.0
                                 and p['null_rate'] < 0.5 and c not in keys]
    return out


def discover_joins(conn, tables, min_share=0.95, max_key_values=500000):
    """Inclusion dependencies: column A.x whose sampled values lie (>= min_share) inside candidate key B.k."""
    key_values = {}
    for t, p in tables.items():
        for k in p['candidate_keys']:
            if p['row_count'] > max_key_values: continue
            key_values[(t, k)] = {str(r[0]) for r in conn.rows(t, [k]) if r[0] is not None}
    joins = []
    for t, p in tables.items():
        sample = conn.sample(t, 2000)
        for c, col in p['columns'].items():
            if col['semantic'] not in ('identifier', 'integer', 'code', 'categorical') or col['distinct'] == 0: continue
            vals = {str(r[c]) for r in sample if r.get(c) is not None}
            for (kt, k), kv in key_values.items():
                if kt == t and k == c: continue
                if not kv: continue
                share = len(vals & kv) / len(vals)
                if share >= min_share and len(vals) >= 2:
                    ft = set(tokens(c)) - {'id', 'key', 'source', 'value'}
                    tt = (set(tokens(k)) | set(tokens(kt))) - {'id', 'key', 'source', 'value'}
                    joins.append({'from': f'{t}.{c}', 'to': f'{kt}.{k}', 'inclusion_share': round(share, 4), 'distinct_from': len(vals),
                                  'name_match': bool(ft & tt) or c == k})
    return joins


def profile_source(conn, sample_rows=5000, tables=None):
    names = tables or conn.tables()
    prof = {name: profile_table(conn, name, sample_rows) for name in names}
    joins = discover_joins(conn, prof)
    return {'connector': conn.describe(), 'tables': prof, 'joins': joins}


def summary(profile):
    """Compact human view: per table the detected code systems, money columns, keys and joins."""
    out = {}
    for t, p in profile['tables'].items():
        out[t] = {'rows': p['row_count'], 'keys': p['candidate_keys'], 'version_candidates': p['version_candidates'],
                  'codes': {c: col['code']['system'] for c, col in p['columns'].items() if col['code']['system']},
                  'money': {c: col['semantic'] for c, col in p['columns'].items() if col['semantic'].startswith('money')},
                  'dates': [c for c, col in p['columns'].items() if col['semantic'] in ('date', 'datetime')]}
    out['_joins'] = [f"{j['from']} -> {j['to']} ({j['inclusion_share']})" for j in profile['joins']]
    return out
