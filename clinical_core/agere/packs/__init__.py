"""Domain packs: an industry-neutral core plus configuration packs for each industry.

The core (cascade, planner menu, checks, receipts, learning, locks, work queues) knows nothing about any industry. A pack
carries everything industry-specific as data:

  vocabulary    entity names, synonyms for measures and dimensions (what the compiler route recognises)
  entities      table / column descriptions the semantic layer and schema reranker read
  measures      named, defined metrics (one SQL aggregate over a pack table, with its definition and unit)
  dimensions    group-by columns with their synonyms
  actions       the action families the pack proposes (the pack_engine answers carry them)
  warehouse     where the pack's semantic.sqlite3 lives (a sample builder for demos)

`healthcare` is the default pack and is a thin descriptor: all healthcare engines, measures, definitions and checks keep running
exactly as before and the pack engine never claims a question while healthcare is active. A non-healthcare pack is selected with
AGERE_PACK=<id> (packs/<id>.json); its questions route to the `domain_pack` engine over the pack's own tables, through the same
planner menu, checks and receipts. `logistics` ships as the worked non-healthcare example (shipments, carriers, lanes).
"""
from pathlib import Path
import json
import os
import re

VERSION = 'domain-packs/1'
HERE = Path(__file__).parent
DEFAULT = 'healthcare'
IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
# The statistic a measure reports, echoed in the plan's parameters so a question for a rate / average is visibly answered by one.
STATISTICS = ('count', 'sum', 'mean', 'share_of_denominator')
REQUIRED = ('id', 'label', 'industry', 'entities', 'measures', 'dimensions', 'actions')


class PackError(ValueError):
    pass


def validate(pack):
    """Raise PackError unless the pack is well formed; returns the pack. SQL fragments may reference only the pack's own tables."""
    missing = [k for k in REQUIRED if k not in pack]
    if missing: raise PackError(f"pack {pack.get('id')!r} lacks {missing}")
    tables = {e['table'] for e in pack['entities']}
    for e in pack['entities']:
        if not IDENT.match(e['table']) or not all(IDENT.match(c['name']) for c in e.get('columns', [])): raise PackError(f"bad identifier in entity {e.get('table')!r}")
    seen = set()
    for m in pack['measures']:
        for k in ('id', 'label', 'table', 'expression', 'unit', 'definition', 'synonyms', 'statistic'):
            if k not in m: raise PackError(f"measure {m.get('id')!r} lacks {k}")
        if m['statistic'] not in STATISTICS: raise PackError(f"measure {m['id']} statistic must be one of {STATISTICS}")
        if m['id'] in seen: raise PackError(f"duplicate measure {m['id']}")
        seen.add(m['id'])
        if m['table'] not in tables: raise PackError(f"measure {m['id']} reads unknown table {m['table']}")
        if re.search(r'[;]|--|/\*|\b(?:insert|update|delete|drop|alter|attach|pragma|create)\b', m['expression'], re.I): raise PackError(f"measure {m['id']} expression is not a read-only aggregate")
    for d in pack['dimensions']:
        if d['table'] not in tables or not IDENT.match(d['column']): raise PackError(f"dimension {d.get('id')!r} is not on a pack table")
    return pack


def load(pack_id):
    path = HERE / f'{pack_id}.json'
    if not re.match(r'^[a-z0-9_]+$', pack_id) or not path.exists(): raise PackError(f'no such pack: {pack_id!r}')
    return validate(json.loads(path.read_text()))


def available():
    return sorted(p.stem for p in HERE.glob('*.json'))


def active_id():
    return os.environ.get('AGERE_PACK', DEFAULT).strip() or DEFAULT


def active():
    return load(active_id())


def all_measure_ids():
    return sorted({m['id'] for p in available() for m in load(p)['measures']})


def status():
    pk = active()
    return {'schema': VERSION, 'active': pk['id'], 'industry': pk['industry'], 'available': available(), 'measures': [m['id'] for m in pk['measures']],
            'core_is_industry_neutral': True}
