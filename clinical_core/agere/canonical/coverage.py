"""Source-coverage gate for the canonical model.

Every raw stream in a release must appear in config/agere/canonical/stream_map.json, either mapped to known canonical
tables or declared `{"unused": "<reason>"}`. A stream that is missing, mapped to an unknown table, or declared unused
without a reason fails the gate, so a new feed can never silently fall out of the canonical model.

    python -m clinical_core.agere.canonical.coverage --source <raw records db> [--stream-map path]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from clinical_core.agere.canonical import CONFIG

STREAM_MAP = CONFIG / 'stream_map.json'


class CoverageError(ValueError):
    pass


def load_stream_map(path=STREAM_MAP):
    return json.loads(Path(path).read_text())


def check(streams, stream_map):
    """Return a report dict; report['ok'] is False on any unmapped, unknown-table or reasonless-unused stream."""
    tables = stream_map.get('tables', {})
    entries = stream_map.get('streams', {})
    unmapped, unknown, reasonless, bad = [], {}, [], []
    feeds = {}
    for s in sorted(set(streams)):
        entry = entries.get(s)
        if entry is None:
            unmapped.append(s)
        elif 'unused' in entry:
            if not str(entry['unused']).strip():
                reasonless.append(s)
        elif isinstance(entry.get('tables'), list) and entry['tables']:
            missing = [t for t in entry['tables'] if t not in tables]
            if missing:
                unknown[s] = missing
            for t in entry['tables']:
                feeds.setdefault(t, []).append(s)
        else:
            bad.append(s)
    stale = sorted(set(entries) - set(streams))
    ok = not (unmapped or unknown or reasonless or bad)
    return {'ok': ok, 'streams': len(set(streams)),
            'mapped': len({s for s in streams if 'tables' in entries.get(s, {})}),
            'unused': sorted(s for s in streams if 'unused' in entries.get(s, {})),
            'unmapped': unmapped, 'unknown_tables': unknown, 'unused_without_reason': reasonless, 'malformed': bad,
            'not_in_release': stale, 'table_feeds': {t: sorted(v) for t, v in sorted(feeds.items())}}


def require(streams, stream_map=None):
    """Raise CoverageError unless every stream is accounted for; returns the report otherwise."""
    report = check(streams, stream_map if stream_map is not None else load_stream_map())
    if not report['ok']:
        problems = {k: report[k] for k in ('unmapped', 'unknown_tables', 'unused_without_reason', 'malformed') if report[k]}
        raise CoverageError('Canonical source-coverage gate failed: ' + json.dumps(problems))
    return report


def release_streams(db, release=None):
    """Distinct streams of one release in a raw records DB (uses the (stream, release, ...) index)."""
    if release is None:
        releases = [r[0] for r in db.execute('SELECT id FROM releases')]
        if len(releases) != 1:
            raise ValueError(f'{len(releases)} releases; name one')
        release = releases[0]
    return [r[0] for r in db.execute('SELECT DISTINCT stream FROM records WHERE release = ? ORDER BY 1', (release,))]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--release')
    parser.add_argument('--stream-map', type=Path, default=STREAM_MAP)
    args = parser.parse_args(argv)
    db = sqlite3.connect(args.source.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        report = check(release_streams(db, args.release), load_stream_map(args.stream_map))
    finally:
        db.close()
    print(json.dumps({k: v for k, v in report.items() if k != 'table_feeds'}, indent=1))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
