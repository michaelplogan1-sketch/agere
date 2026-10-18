"""Equivalence of two builds, table by table (a delta-built workspace vs a full rebuild of the same files).

    report = compare_workspaces(ws_dir_a, ws_dir_b)

Tables are compared as multisets of rows (row order is not part of a table's content). Known by-design differences are
excluded and listed in the report rather than hidden:
- meta.source_digest (every stage mints a new digest), meta keys holding build timings or summary counts that streaming
  does not re-derive (EXCLUDED_META), meta.streaming (streaming's own note), meta.workspace;
- fill.fill_id is compared too (fill_events re-runs on the whole build, so it is exact);
- warehouse.records.release (derived from the landing path, which differs between two workspaces).
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3

EXCLUDED_META = ('source_digest', 'streaming', 'workspace', 'claim_settlement', 'note_fact_version', 'population_feeds')
TIMING_KEYS = ('elapsed_seconds', 'seconds')
SUMMARY_META = ('population_feeds',)  # compared part by part: only the parts streaming re-derives must match


def _ro(p):
    return sqlite3.connect(f'file:{p}?mode=ro', uri=True)


def _rows(db, table, cols):
    h = Counter()
    for r in db.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{table}"'):
        h[hashlib.sha1(repr(r).encode()).hexdigest()] += 1
    return h


def _samples(db, table, cols, digests, n=3):
    out = []
    if not digests:
        return out
    want = set(digests)
    for r in db.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{table}"'):
        if hashlib.sha1(repr(r).encode()).hexdigest() in want:
            out.append([str(v)[:120] for v in r])
            if len(out) >= n:
                break
    return out


def _strip(v):
    if isinstance(v, dict):
        return {k: _strip(x) for k, x in v.items() if k not in TIMING_KEYS}
    if isinstance(v, list):
        return [_strip(x) for x in v]
    return v


def _norm(value):
    """meta values compared without build timings (elapsed_seconds) inside JSON summaries."""
    try:
        return json.dumps(_strip(json.loads(value)), sort_keys=True)
    except (TypeError, ValueError):
        return value


def compare_db(a_path, b_path, *, skip_prefixes=(), exclude_cols=None, exclude_meta=EXCLUDED_META):
    a, b = _ro(a_path), _ro(b_path)
    exclude_cols = exclude_cols or {}
    try:
        ta = {r[0] for r in a.execute("SELECT name FROM sqlite_master WHERE type='table'") if not r[0].startswith(('sqlite_', *skip_prefixes))}
        tb = {r[0] for r in b.execute("SELECT name FROM sqlite_master WHERE type='table'") if not r[0].startswith(('sqlite_', *skip_prefixes))}
        report = {'only_in_a': sorted(ta - tb), 'only_in_b': sorted(tb - ta), 'tables': {}}
        for t in sorted(ta & tb):
            ca = [r[1] for r in a.execute(f'PRAGMA table_info("{t}")')]
            cb = [r[1] for r in b.execute(f'PRAGMA table_info("{t}")')]
            if sorted(ca) != sorted(cb):
                report['tables'][t] = {'equal': False, 'columns_a': ca, 'columns_b': cb}
                continue
            cols = [c for c in ca if c not in exclude_cols.get(t, ())]
            if t == 'meta':
                ma, mb = dict(a.execute('SELECT key, value FROM meta')), dict(b.execute('SELECT key, value FROM meta'))
                diff = sorted(k for k in set(ma) | set(mb) if k not in exclude_meta and _norm(ma.get(k)) != _norm(mb.get(k)))
                parts = {}
                for k in SUMMARY_META:
                    try:
                        ja, jb = json.loads(ma.get(k) or '{}'), json.loads(mb.get(k) or '{}')
                    except ValueError:
                        continue
                    parts[k] = sorted(p for p in set(ja) | set(jb) if ja.get(p) != jb.get(p))
                report['tables'][t] = {'equal': not diff, 'keys_differ': diff, 'excluded_keys': list(exclude_meta),
                                       'summary_parts_differ': parts}
                continue
            ha, hb = _rows(a, t, cols), _rows(b, t, cols)
            only_a, only_b = ha - hb, hb - ha
            entry = {'equal': not only_a and not only_b, 'rows_a': sum(ha.values()), 'rows_b': sum(hb.values()),
                     'only_a': sum(only_a.values()), 'only_b': sum(only_b.values())}
            if exclude_cols.get(t):
                entry['excluded_columns'] = list(exclude_cols[t])
            if not entry['equal']:
                entry['sample_only_a'] = _samples(a, t, cols, list(only_a)[:3])
                entry['sample_only_b'] = _samples(b, t, cols, list(only_b)[:3])
            report['tables'][t] = entry
        report['differing_tables'] = sorted(t for t, e in report['tables'].items() if not e['equal']) + report['only_in_a'] + report['only_in_b']
        report['equal'] = not report['differing_tables']
        return report
    finally:
        a.close(); b.close()


def compare_profiles(a_path, b_path):
    pa, pb = json.loads(Path(a_path).read_text()), json.loads(Path(b_path).read_text())
    for p in (pa, pb):
        p.pop('elapsed_seconds', None); p.pop('source_digest', None)
    diff = [k for k in set(pa) | set(pb) if json.dumps(pa.get(k), sort_keys=True) != json.dumps(pb.get(k), sort_keys=True)]
    streams = []
    if 'streams' in diff:
        sa, sb = pa.get('streams', {}), pb.get('streams', {})
        streams = sorted(s for s in set(sa) | set(sb) if json.dumps(sa.get(s), sort_keys=True) != json.dumps(sb.get(s), sort_keys=True))
    return {'equal': not diff, 'keys_differ': diff, 'streams_differ': streams}


def compare_workspaces(a_dir, b_dir, *, raw_tables=True):
    """Full rebuild workspace (a) vs streamed workspace (b)."""
    a_dir, b_dir = Path(a_dir), Path(b_dir)
    out = {}
    out['stack_semantic'] = compare_db(a_dir / 'stack' / 'semantic.sqlite3', b_dir / 'stack' / 'semantic.sqlite3')
    out['workspace_semantic'] = compare_db(a_dir / 'semantic.sqlite3', b_dir / 'semantic.sqlite3',
                                           skip_prefixes=() if raw_tables else ('raw_',),
                                           exclude_cols={'delivery_file': ('local_copy_time',)})
    out['stack_store'] = compare_db(a_dir / 'stack' / 'stack.sqlite3', b_dir / 'stack' / 'stack.sqlite3',
                                    skip_prefixes=('agere_record_version',))
    out['warehouse'] = compare_db(a_dir / 'warehouse.sqlite3', b_dir / 'warehouse.sqlite3',
                                  exclude_cols={'records': ('release',), 'patients': ('release',), 'releases': ('id',),
                                                'source_entities': ('release',)})
    out['source_profile'] = compare_profiles(a_dir / 'stack' / 'source-profile.json', b_dir / 'stack' / 'source-profile.json')
    out['equal'] = all(v['equal'] for v in out.values() if isinstance(v, dict) and 'equal' in v)
    return out


def summarize(report):
    lines = []
    for k, v in report.items():
        if not isinstance(v, dict):
            continue
        if 'tables' in v:
            n = len(v['tables'])
            lines.append(f"{k}: {n - len([t for t in v['differing_tables'] if t in v['tables']])}/{n} tables identical; "
                         f"differ: {v['differing_tables'] or 'none'}")
        else:
            lines.append(f"{k}: {'identical' if v.get('equal') else 'differs ' + json.dumps(v)}")
    return '\n'.join(lines)


# ------------------------------------------------------------------------------------------------- fingerprints
# A full rebuild of a large delivery and its streamed twin do not both fit on a small disk; a fingerprint keeps, per
# table, the row count and an order-independent multiset hash (sum of row hashes mod 2**128), so the reference build can
# be fingerprinted and deleted before the streamed one is built.
_MOD = 1 << 128


def _fp_db(path, *, skip_prefixes=(), exclude_cols=None, exclude_meta=EXCLUDED_META):
    db = _ro(path)
    exclude_cols = exclude_cols or {}
    out = {}
    try:
        for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            if t.startswith(('sqlite_', *skip_prefixes)):
                continue
            cols = sorted(r[1] for r in db.execute(f'PRAGMA table_info("{t}")') if r[1] not in exclude_cols.get(t, ()))
            if t == 'meta':
                out[t] = {k: _norm(v) for k, v in db.execute('SELECT key, value FROM meta') if k not in exclude_meta}
                continue
            total, n = 0, 0
            for r in db.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{t}"'):
                total = (total + int.from_bytes(hashlib.sha1(repr(r).encode()).digest()[:16], 'big')) % _MOD
                n += 1
            out[t] = {'rows': n, 'hash': f'{total:032x}', 'columns': cols}
        return out
    finally:
        db.close()


def fingerprint_workspace(ws_dir):
    ws_dir = Path(ws_dir)
    prof = json.loads((ws_dir / 'stack' / 'source-profile.json').read_text())
    prof.pop('elapsed_seconds', None); prof.pop('source_digest', None)
    return {'stack_semantic': _fp_db(ws_dir / 'stack' / 'semantic.sqlite3'),
            'workspace_semantic': _fp_db(ws_dir / 'semantic.sqlite3', exclude_cols={'delivery_file': ('local_copy_time',)}),
            'stack_store': _fp_db(ws_dir / 'stack' / 'stack.sqlite3', skip_prefixes=('agere_record_version',)),
            'warehouse': _fp_db(ws_dir / 'warehouse.sqlite3', exclude_cols={'records': ('release',), 'patients': ('release',),
                                                                              'releases': ('id',), 'source_entities': ('release',)}),
            'source_profile': hashlib.sha256(json.dumps(prof, sort_keys=True).encode()).hexdigest()}


def compare_fingerprints(a, b):
    out = {}
    for part in ('stack_semantic', 'workspace_semantic', 'stack_store', 'warehouse'):
        if part not in a or part not in b:
            continue
        ta, tb = a[part], b[part]
        tables = {}
        for t in sorted(set(ta) | set(tb)):
            if t not in ta or t not in tb:
                tables[t] = {'equal': False, 'missing_in': 'a' if t not in ta else 'b'}
            elif t == 'meta':
                diff = sorted(k for k in set(ta[t]) | set(tb[t]) if ta[t].get(k) != tb[t].get(k))
                tables[t] = {'equal': not diff, 'keys_differ': diff}
            else:
                tables[t] = {'equal': ta[t] == tb[t], 'rows_a': ta[t]['rows'], 'rows_b': tb[t]['rows']}
        out[part] = {'tables': tables, 'differing_tables': sorted(t for t, e in tables.items() if not e['equal'])}
        out[part]['equal'] = not out[part]['differing_tables']
    out['source_profile'] = {'equal': a['source_profile'] == b['source_profile']}
    out['equal'] = all(v['equal'] for v in out.values() if isinstance(v, dict) and 'equal' in v)
    return out
