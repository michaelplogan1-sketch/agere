"""The program catalog: programs (id, name, kind, eligibility as data, channels, required fields, vendor slots), gap
definitions, exclusions and coverage rules. Loaded from programs.json next to this module unless a path is given."""
from __future__ import annotations

import copy
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE / 'programs.json'
SCHEMA = 'agere-enrollment-programs/1'
PRIORITIES = ('high', 'medium', 'low')
_CACHE = {}


class CatalogError(ValueError):
    pass


def validate(cat):
    """-> list of problems (empty when the catalog is usable)."""
    problems = []
    if cat.get('schema') != SCHEMA:
        problems.append(f"schema must be {SCHEMA!r}")
    gaps = cat.get('gaps') or {}
    ids = set()
    for p in cat.get('programs') or []:
        pid = p.get('id')
        if not pid or pid in ids:
            problems.append(f'program id missing or duplicated: {pid!r}')
        ids.add(pid)
        for key in ('name', 'kind', 'eligibility', 'enrollment_channel', 'outreach_channel', 'script_template_id',
                    'required_fields', 'vendor_slots'):
            if key not in p:
                problems.append(f'{pid}: missing {key}')
        gap = (p.get('eligibility') or {}).get('gap_type')
        if gap and gap not in gaps:
            problems.append(f'{pid}: unknown gap_type {gap!r}')
        if not p.get('vendor_slots'):
            problems.append(f'{pid}: needs at least one vendor slot')
        for s in p.get('vendor_slots') or []:
            if not s.get('slot_id'):
                problems.append(f'{pid}: vendor slot without slot_id')
            if s.get('assignment', 'priority') not in ('priority', 'hash'):
                problems.append(f"{pid}: slot {s.get('slot_id')} assignment must be priority or hash")
        for r in p.get('priority_rules') or []:
            if r.get('priority') not in PRIORITIES:
                problems.append(f'{pid}: priority rule with unknown priority {r.get("priority")!r}')
        ctx = p.get('context')
        if ctx and ctx not in (cat.get('contexts') or {}):
            problems.append(f'{pid}: unknown context {ctx!r}')
    if not any(p.get('kind') == 'coverage_check' for p in cat.get('programs') or []):
        problems.append('catalog needs a coverage_check program (the queue for patients without active coverage)')
    return problems


def load(path=None):
    """The catalog (a deep copy; callers may not mutate the cached one). Raises CatalogError when invalid."""
    p = Path(path or DEFAULT_PATH).resolve()
    key = (str(p), p.stat().st_mtime_ns)
    cat = _CACHE.get(key)
    if cat is None:
        cat = json.loads(p.read_text())
        problems = validate(cat)
        if problems:
            raise CatalogError('Invalid program catalog: ' + '; '.join(problems))
        _CACHE[key] = cat
    return copy.deepcopy(cat)


def kinds(cat):
    return sorted({p['kind'] for p in cat['programs']})


def programs_of(cat, kind):
    return [p for p in cat['programs'] if p['kind'] == kind]


def coverage_program(cat):
    return next(p for p in cat['programs'] if p['kind'] == 'coverage_check')


def gap_type_for(cat, kind):
    for p in programs_of(cat, kind):
        g = (p.get('eligibility') or {}).get('gap_type')
        if g:
            return g
    return None
