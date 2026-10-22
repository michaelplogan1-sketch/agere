"""Inputs a question depends on that this workspace's data does not deliver: such a question declines as unsupported with
the missing input named, instead of being answered from a column that is empty or from a link Agere inferred itself.

Three general sources, all read from the workspace's own semantic layer (read-only):

  null everywhere   a column the question names (a snake_case identifier in its text or stated definition) that is NULL /
                    empty on every row of every modelled semantic table carrying it: no feed in this delivery sends it
                    (e.g. payer_pharmacy_claim.formulary_tier on native NCPDP / flat pharmacy / CCLF7 feeds). A
                    deterministic family that states a derivation for such a field (vbc_families pharmacy_tier_share)
                    represents the question in full, so the field checks are skipped for it.
  inferred natively a field native raw feeds never carry but Agere's native adapters rebuild by inference (record-version
                    lineage: supersedes_record_id / record_correction are reconstructed by matching record keys across
                    deliveries). Applies only to a native raw-feed build (a delivery_file manifest in the semantic layer,
                    or a run.json / workspace.json beside it naming the native ingest); a warehouse build that delivers
                    the field is unaffected.
  stated            the question's own definition says it is not answerable from this delivery ("Not answerable from this
                    delivery: <input>").

undelivered(path, question) -> [{'input', 'reason', 'source'}] (empty when nothing is missing). AGERE_UNDELIVERED_GATE=off
disables it. Synthetic data only; no model calls.
"""
from __future__ import annotations

import os, re, sqlite3, threading
from pathlib import Path

VERSION = 'agere-delivered-inputs/1'
ROUTE = 'undelivered_input'

# Fields native raw feeds never deliver although the native build fills them by inference.
NATIVE_INFERRED = (
    {'input': 'record-version lineage (supersedes_record_id)',
     'patterns': (r'\bsupersedes_record_id\b', r'\brecord_correction\b', r'\brestated by a (?:newer|later) (?:record|version)\b',
                  r'\blater restated\b'),
     'reason': 'native feeds deliver no link from a correction or restatement to the record it replaces; the version chains '
               "in this workspace are Agere's own inference from matching record keys across deliveries, not delivered data"},
)
_STATED = re.compile(r'\bnot answerable from this delivery\s*:\s*(?P<what>.+?)\s*(?:\.\s*)?$', re.I | re.S)
_IDENT = re.compile(r'\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b')
_CACHE, _LOCK = {}, threading.Lock()


def enabled():
    return os.environ.get('AGERE_UNDELIVERED_GATE', 'on').strip().lower() != 'off'


def _target(path):
    p = Path(path)
    return p / 'semantic.sqlite3' if p.is_dir() else p


def _native_build(target):
    """The semantic layer was built from native raw feeds: its stack's run.json names the native ingest adapter (a stack
    semantic layer carries no delivery_file manifest), or its workspace.json names a native:// source."""
    import json
    for name, test in (('run.json', lambda d: str(d.get('schema', '')).startswith('agere-native-stack') or str(
                           ((d.get('snapshot') or {}).get('source_dataset') or {}).get('adapter', '')).startswith('agere-native-ingest')),
                       ('workspace.json', lambda d: str(d.get('source', '')).startswith('native:'))):
        for folder in (target.parent, target.parent.parent):
            f = folder / name
            try:
                if f.is_file() and test(json.loads(f.read_text())): return True
            except (OSError, ValueError, AttributeError):
                continue
    return False


def _profile(target):
    """{'native': bool, 'columns': {column: [tables]}} for the modelled (non-raw) semantic tables, cached per file version."""
    st = target.stat()
    key = (str(target), st.st_mtime_ns, st.st_size)
    with _LOCK:
        if key in _CACHE: return _CACHE[key]
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        cols = {}
        for t in tables:
            if t.startswith(('raw_', 'sqlite_')): continue
            for r in db.execute(f'PRAGMA table_info("{t}")'):
                cols.setdefault(r[1], []).append(t)
        prof = {'native': 'delivery_file' in tables or _native_build(target), 'columns': cols, 'empty': {}}
    finally:
        db.close()
    with _LOCK:
        _CACHE[key] = prof
    return prof


def _null_everywhere(target, prof, column):
    if column in prof['empty']: return prof['empty'][column]
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        empty = True
        for t in prof['columns'][column]:
            if db.execute(f'SELECT 1 FROM "{t}" WHERE "{column}" IS NOT NULL AND CAST("{column}" AS TEXT) <> \'\' LIMIT 1').fetchone():
                empty = False
                break
    finally:
        db.close()
    prof['empty'][column] = empty
    return empty


def _family_derivation(text):
    """A deterministic VBC family represents this stated definition in full (it states its own derivation for any field the
    delivery lacks, e.g. the generic tier from RxNorm product names), so the field-level checks do not apply."""
    try:
        from . import vbc_families
        return vbc_families.enabled() and vbc_families.parse(text, '') is not None
    except Exception:  # noqa: BLE001 - the check never blocks the gate
        return False


def undelivered(path, question):
    """-> [{'input', 'reason', 'source'}]: what the question needs that this workspace does not deliver. Never raises."""
    if not enabled() or not path or not question: return []
    try:
        target = _target(path)
        if not target.is_file(): return []
        text = ' '.join(str(question).split())
        out = []
        m = _STATED.search(text)
        if m:
            out.append({'input': m.group('what').strip().rstrip('.'), 'source': 'stated',
                        'reason': "the question's definition says this delivery does not contain it"})
        if _family_derivation(text): return out  # a deterministic family states how it reads this definition
        prof = _profile(target)
        low = text.lower()
        if prof['native']:
            for item in NATIVE_INFERRED:
                if any(re.search(p, low) for p in item['patterns']):
                    out.append({'input': item['input'], 'reason': item['reason'], 'source': 'inferred_natively'})
        ed = _ed_undelivered(target, low)
        if ed: out.append(ed)
        for ident in dict.fromkeys(_IDENT.findall(low)):
            if ident in prof['columns'] and _null_everywhere(target, prof, ident):
                if any(ident in o['input'] for o in out): continue
                tables = ', '.join(sorted(prof['columns'][ident]))
                out.append({'input': f'{ident} ({tables})', 'source': 'null_everywhere',
                            'reason': f'{ident} is empty on every row: no feed in this delivery sends it'})
        return out
    except (OSError, sqlite3.Error, ValueError):
        return []


def _ed_undelivered(target, low):
    """An ED question on a workspace with no ED-capable source at all (EHR encounters, ADT, claims; ed_definition.py): the
    zero would be 'source not delivered', never a true zero, so the question declines with the input named."""
    from .ed_definition import ED_QUESTION, availability, columns
    if not ED_QUESTION.search(low): return None
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        avail = availability(db, '0000-01-01', '9999-12-31', tables=columns(db))
    finally:
        db.close()
    if avail['delivered_sources']: return None
    return {'input': 'an ED-capable source (EHR encounters, ADT or claims)', 'source': 'ed_definition',
            'reason': 'no feed in this delivery can show an ED visit, so no ED count (including zero) is observable'}


def requirements(missing):
    """The graph's unmet_requirements lines for a decline."""
    return [f"Missing input: {m['input']} - {m['reason']}"[:400] for m in missing]


def missing_names(missing):
    return [m['input'] for m in missing]
