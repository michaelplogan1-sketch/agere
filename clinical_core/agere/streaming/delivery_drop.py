"""A delivery drop folder for streaming demos and tests: symlinks into a native delivery, in manifest delivery order.

    entries = manifest_entries(raw_dir)                 # [{path, delivered, ...}] sorted by (delivered, path)
    make_drop(raw_dir, drop_dir, [e for e in entries if e['delivered'] < '2030-04-01'])
    add_files(raw_dir, drop_dir, batch)                 # -> relpaths to hand to Workspace.ingest_delta

The drop folder carries its own manifest.json listing only the files delivered so far (what the customer's manifest
said at that time). Paths under raw/ are kept, so landing lineage (_file) is identical to ingesting the delivery itself.
Folders named private are never linked.
"""
from __future__ import annotations

from collections import OrderedDict
import json
import os
from pathlib import Path


def _rel(entry):
    p = str(entry.get('path') or '')
    return p[4:] if p.startswith('raw/') else p


def manifest_entries(raw_dir):
    data = json.loads((Path(raw_dir) / 'manifest.json').read_text())
    files = data.get('files') if isinstance(data, dict) else data
    out = [f for f in files if _rel(f) and 'private' not in Path(_rel(f)).parts]
    return sorted(out, key=lambda f: (f.get('delivered') or '', _rel(f)))


def _write_manifest(drop_dir, entries):
    path = Path(drop_dir) / 'manifest.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps({'files': entries, 'summary': {'files': len(entries), 'note': 'streaming drop: files delivered so far'}}))
    tmp.replace(path)


def _link(raw_dir, drop_dir, entry):
    rel = _rel(entry)
    src = Path(raw_dir) / rel
    dst = Path(drop_dir) / rel
    if not src.is_file():
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        os.symlink(src, dst)
    return rel


def delivered(drop_dir):
    p = Path(drop_dir) / 'manifest.json'
    if not p.is_file():
        return []
    return json.loads(p.read_text()).get('files') or []


def make_drop(raw_dir, drop_dir, entries):
    Path(drop_dir).mkdir(parents=True, exist_ok=True)
    rels = [r for r in (_link(raw_dir, drop_dir, e) for e in entries) if r]
    _write_manifest(drop_dir, list(entries))
    return rels


def add_files(raw_dir, drop_dir, entries):
    """Deliver more files into the drop. -> their relpaths (plus manifest.json, which changed)."""
    have = delivered(drop_dir)
    seen = {_rel(e) for e in have}
    rels = []
    for e in entries:
        r = _link(raw_dir, drop_dir, e)
        if r:
            rels.append(r)
        if _rel(e) not in seen:
            have.append(e)
            seen.add(_rel(e))
    _write_manifest(drop_dir, have)
    return rels


def batches(entries, cadence='week'):
    """Group manifest entries into delivery batches by delivered day or ISO week. -> OrderedDict(label -> entries)."""
    from datetime import date
    out = OrderedDict()
    for e in entries:
        d = e.get('delivered') or ''
        if cadence == 'day':
            key = d
        else:
            y, w, _ = date.fromisoformat(d).isocalendar()
            key = f'{y}-W{w:02d}'
        out.setdefault(key, []).append(e)
    return out
