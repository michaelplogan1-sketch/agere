"""domain_pack engine: answers a non-healthcare pack's measures and dimensions from its own tables.

The engine is the only place the pack layer touches the planner. It answers only when a non-healthcare pack is active
(AGERE_PACK) - under the default healthcare pack its route returns None and every healthcare phrasing goes where it always went.
Measures are the pack's defined aggregates; the answer states the definition and unit, and carries the pack's action families.
Group-by dimensions are the pack's declared columns only (closed enum, identifier-checked)."""
from collections import Counter
import re

from .. import packs

VERSION = 'domain-pack-engine/1'
OP = 'domain_pack'
NEEDS_STACK = False
PRODUCT = {'population': 'none', 'view': 'table',
           'use': 'INDUSTRY PACK questions when a non-healthcare domain pack is active (for example logistics: on-time delivery rate, '
                  'late shipments, damage rate, freight cost, shipments in transit, by carrier / lane / mode): the pack defines the measures.'}
CONTRACT = {'inputs': [], 'parameters': {'measure': 'a measure id of the active pack', 'statistic': 'count|sum|mean|share_of_denominator (the measure\'s own)', 'group_by': 'a dimension id of the active pack or none', 'top': 50},
            'meaning': 'Evaluates one defined measure of the active domain pack over the pack warehouse, optionally grouped by one declared dimension; '
                       'the answer states the measure definition and unit.'}
NATIVE = ('value', 'table')


def schema(obj, string, array):
    return obj({'measure': string(tuple(packs.all_measure_ids()), 'Measure id from the active pack'),
                'statistic': string(packs.STATISTICS, "The measure's own statistic; a mismatch is refused"),
                'group_by': string(('none', *sorted({d['id'] for p in packs.available() for d in packs.load(p)['dimensions']})), 'Dimension id; none = overall'),
                'top': {'type': 'integer', 'minimum': 1, 'maximum': 500}})


def validate(p):
    if not isinstance(p, dict) or set(p) - {'measure', 'statistic', 'group_by', 'top'}: raise ValueError('domain_pack parameters: measure, statistic, group_by, top')
    out = {'measure': p.get('measure'), 'statistic': p.get('statistic'), 'group_by': p.get('group_by', 'none'), 'top': p.get('top', 50)}
    pk = packs.active()
    if out['measure'] not in {m['id'] for m in pk['measures']}: raise ValueError(f"measure must be one of the active pack's: {[m['id'] for m in pk['measures']]}")
    stat = next(m['statistic'] for m in pk['measures'] if m['id'] == out['measure'])
    if out['statistic'] in (None, ''): out['statistic'] = stat
    if out['statistic'] != stat: raise ValueError(f"statistic for {out['measure']} is {stat}")
    if out['group_by'] != 'none' and out['group_by'] not in {d['id'] for d in pk['dimensions']}: raise ValueError('group_by: a declared dimension or none')
    if type(out['top']) is not int or not 1 <= out['top'] <= 500: raise ValueError('top 1..500')
    return out


def run(db, p, pack):
    m = next(x for x in pack['measures'] if x['id'] == p['measure'])
    where = f" WHERE {m['where']}" if m.get('where') else ''
    if p['group_by'] == 'none':
        value = db.execute(f"SELECT {m['expression']} FROM {m['table']}{where}").fetchone()[0]
        table = [{'group': 'all', 'value': value}]
    else:
        d = next(x for x in pack['dimensions'] if x['id'] == p['group_by'])
        table = [{'group': g, 'value': v} for g, v in db.execute(
            f"SELECT {d['column']}, {m['expression']} FROM {m['table']}{where} GROUP BY {d['column']} ORDER BY 2 DESC LIMIT {p['top']}")]
    rows = db.execute(f"SELECT count(*) FROM {m['table']}{where}").fetchone()[0]
    return {'schema': VERSION, 'pack': pack['id'], 'measure': m, 'parameters': p, 'table': table, 'rows_scanned': rows, 'patient_ids': [],
            'actions': pack['actions']}


def execute(path, patient_ids, params, as_of):
    import sqlite3
    from pathlib import Path
    p = validate(params)
    db = sqlite3.connect(f'file:{Path(path)/"semantic.sqlite3"}?mode=ro', uri=True)
    try:
        return run(db, p, packs.active())
    finally:
        db.close()


def _fmt(v, unit):
    if v is None: return 'n/a'
    return f'{v:.1%}' if 'share' in unit else f'{v:,.2f}' if isinstance(v, float) else f'{v:,}'


def answer(report, plan, question):
    m = report['measure']
    top = report['table'][0]
    head = (f"{m['label']}: {_fmt(top['value'], m['unit'])} ({m['unit']})" if len(report['table']) == 1 and top['group'] == 'all'
            else f"{m['label']} by {report['parameters']['group_by']}: " + '; '.join(f"{r['group']} {_fmt(r['value'], m['unit'])}" for r in report['table'][:8]))
    return {'status': 'answered' if plan.get('coverage') == 'supported_observation' and not plan.get('unmet_requirements') else 'partial',
            'answer_type': 'table', 'table': report['table'], 'count': report['rows_scanned'], 'patient_ids': [], 'value': head + '.',
            'method': f"Pack {report['pack']}: {m['expression']} over {m['table']}" + (f" where {m['where']}" if m.get('where') else ''),
            'definitions': [{'term': m['label'], 'basis': m['definition']}], 'limitations': [f"Synthetic {report['pack']} pack data; actions proposed by the pack: {', '.join(report['actions'])}."],
            'membership_role': 'none', 'overall': {'domain_pack': {'pack': report['pack'], 'measure': m['id'], 'unit': m['unit']}}}


def route(question, profile, as_of):
    from .engine_suite import plan
    if packs.active_id() == packs.DEFAULT: return None  # healthcare unchanged
    pk = packs.active()
    low = ' '.join(str(question).lower().split())
    hits = [m for m in pk['measures'] if any(re.search(rf'\b(?:{s})\b', low) for s in m['synonyms'])]
    if not hits: return None
    # a specific measure beats the generic count; among specific ones the longest matching synonym wins
    hits = [m for m in hits if not m.get('generic')] or hits
    best = max(hits, key=lambda m: max(len(mm.group(0)) for s in m['synonyms'] for mm in [re.search(rf'\b(?:{s})\b', low)] if mm))
    params = {'measure': best['id'], 'statistic': best['statistic']}
    for d in pk['dimensions']:
        if re.search(rf'\b(?:by|per|for each|each|which)\s+(?:{"|".join(d["synonyms"])})\b', low): params['group_by'] = d['id']; break
    return plan(OP, params, f"{pk['label']}: {best['label']}", question, [f"pack {pk['id']}", f"measure {best['id']}"])


TEMPLATES = ['What is our on-time delivery rate by carrier?', 'how many shipments are still in transit', 'late shipments by lane',
             'damage rate by mode', 'average freight cost by carrier']
