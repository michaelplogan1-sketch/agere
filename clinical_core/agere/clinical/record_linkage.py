"""Duplicate patient records on the question path: Fellegi-Sunter linkage (person_linkage.py) as a governed engine.

    build(path)                       score every blocked candidate pair once per source digest -> stack/person-links.json
    enterprise_person_ids(path, threshold=None) -> {patient_id: 'AGP-...'}   linked charts only (other engines read this)
    person_of(path, patient_id)       enterprise person id, the chart's own id when it links to nothing

Operation 'record_linkage' (foundation_engines.py): which charts look like the same person, with each link's summed
Fellegi-Sunter weight, per-field agreement levels and weights, and the rule that linked it (weight >= threshold, a shared
payer member ID or a shared facility MRN with agreeing birth dates). threshold is the precision lever (default 15 bits).
Workspace onboarding builds the same cache (onboarding/workspace_stack.prepare), so a workspace's linked identities are
what its questions read. Client-visible demographics only; no generator truth. Synthetic data only; no model calls.
"""
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from . import person_linkage as pl

VERSION = 'record-linkage/1'
CACHE = 'person-links.json'
OUTPUTS = ('patients', 'pairs', 'summary')
GROUP_BY = ('none', 'practice', 'payer')

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'DUPLICATE PATIENT RECORDS / identity resolution: which charts look like the same person (probabilistic '
                  'Fellegi-Sunter record linkage over name, birth date, sex, address, postal code, phone, payer member IDs and '
                  'facility MRNs), how many patients have more than one chart, duplicates by practice or payer, enterprise '
                  'person ids, with pair weights and field agreement as evidence.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'output': 'patients|pairs|summary', 'threshold': 15.0, 'group_by': 'none|practice|payer', 'more_than': 1},
            'meaning': 'Fellegi-Sunter probabilistic record linkage of received charts: blocked candidate pairs scored with '
                       'log2(m/u) agreement weights (value-specific u for names and birth dates), linked when the summed weight '
                       '>= threshold bits or deterministically on a shared payer member ID / facility MRN with agreeing birth '
                       'dates; links closed transitively into enterprise persons (AGP-<smallest chart id>); more_than: persons with more than N charts. patients: one row '
                       'per linked chart with its person id; pairs: each link with weights and field levels; summary: counts.'}
NATIVE_SCALARS = ('count', 'value')


def parameter_schema(catalog, obj, string, array):
    return obj({'output': string(OUTPUTS, 'patients: linked charts with their enterprise person id; pairs: each link with its '
                                          'evidence; summary: counts (persons with more than one chart, extra charts)'),
                'threshold': {'type': 'number', 'minimum': 5, 'maximum': 40,
                              'description': f"Fellegi-Sunter link threshold in bits (default {pl.DEFAULTS['threshold']}); higher = fewer, surer links"},
                'group_by': string(GROUP_BY, 'Count duplicates by the chart\'s PCP practice or payer'),
                'more_than': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'description': 'Persons with more than N charts (default 1)'}})


def validate(p):
    p = dict(p or {})
    if set(p) - {'output', 'threshold', 'group_by', 'more_than'}: raise ValueError('record_linkage parameters: output, threshold, group_by, more_than')
    p.setdefault('more_than', 1)
    if type(p['more_than']) is not int or not 1 <= p['more_than'] <= 20: raise ValueError('more_than: 1..20 charts')
    p.setdefault('output', 'patients'); p.setdefault('threshold', pl.DEFAULTS['threshold']); p.setdefault('group_by', 'none')
    if p['output'] not in OUTPUTS: raise ValueError(f'output: one of {OUTPUTS}')
    if p['group_by'] not in GROUP_BY: raise ValueError(f'group_by: one of {GROUP_BY}')
    if type(p['threshold']) not in (int, float) or not 5 <= p['threshold'] <= 40: raise ValueError('threshold: 5..40 bits')
    return p


# ---- cache ------------------------------------------------------------------------------------------------------------
def _semantic(path):
    path = Path(path)
    if (path / 'run.json').exists() and not (path / 'semantic.sqlite3').exists():
        from . import semantic_layer
        return semantic_layer.ensure(path)
    return path / 'semantic.sqlite3'


def _digest(db, path):
    try:
        row = db.execute("SELECT value FROM meta WHERE key='source_digest'").fetchone()
        if row: return row[0]
    except sqlite3.Error:
        pass
    run = Path(path) / 'run.json'
    return json.loads(run.read_text()).get('source_digest') if run.exists() else None


def build(path, *, force=False):
    """Score every blocked candidate pair once per source digest (threshold-independent) and cache it beside the store.
    -> {'version', 'source_digest', 'charts', 'pairs': [...], 'u_probabilities', ...}"""
    path = Path(path)
    out = path / CACHE
    db = pl._ro(_semantic(path))
    try:
        digest = _digest(db, path)
        if not force and out.exists():
            try:
                cached = json.loads(out.read_text())
                if cached.get('version') == VERSION and cached.get('source_digest') == digest: return cached
            except (OSError, ValueError):
                pass
        people = pl.load_identities(path, db)
    finally:
        db.close()
    return _write(out, score(people, digest))


def score(people, digest=None):
    """Threshold-independent comparison of every blocked candidate pair."""
    model = pl.Model(people)
    pairs, skipped = pl.candidate_pairs(people, model.p['max_block'])
    rows = []
    for i, j in sorted(pairs):
        c = model.compare(people[i], people[j])
        rows.append({'a': people[i]['patient_id'], 'b': people[j]['patient_id'], 'weight': c['total'], 'levels': c['levels'],
                     'weights': c['weights'], 'shared_member_id': c['shared_member_id'], 'shared_mrn': c['shared_mrn']})
    return {'version': VERSION, 'linkage': pl.VERSION, 'source_digest': digest, 'charts': len(people),
            'chart_ids': sorted(p['patient_id'] for p in people), 'candidate_pairs': len(rows), 'oversized_blocks_skipped': skipped,
            'u_probabilities': {k: round(v, 6) for k, v in model.u.items()}, 'm_probabilities': pl.M, 'pairs': rows}


def _write(out, data):
    try:
        tmp = out.with_suffix('.tmp'); tmp.write_text(json.dumps(data)); tmp.replace(out)
    except OSError:  # a read-only store still answers from the in-memory scores
        pass
    return data


def decide(scored, threshold=None):
    """-> (assignments {patient_id: person_id} for linked charts, links [pair + rule])."""
    threshold = pl.DEFAULTS['threshold'] if threshold is None else threshold
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    links = []
    for p in scored['pairs']:
        dob_ok = p['levels'].get('birth_date') in ('agree', 'partial')
        rule = ('fellegi_sunter' if p['weight'] >= threshold else 'member_id' if p['shared_member_id'] and dob_ok
                else 'mrn' if p['shared_mrn'] and dob_ok else None)
        if rule:
            links.append({**p, 'rule': rule}); parent[find(p['a'])] = find(p['b'])
    clusters = defaultdict(list)
    for x in list(parent): clusters[find(x)].append(x)
    out = {}
    for members in clusters.values():
        if len(members) > 1:
            for m in members: out[m] = 'AGP-' + min(members)
    return out, links


def _decide(path, scored, threshold=None):
    """decide(), with the recorded identity decisions (splits, rejects, confirms: identity_resolution ledger) applied when there are any."""
    from . import identity_resolution as IR
    over = IR.overrides(path)
    return IR.decide(scored, threshold, over) if over else decide(scored, threshold)


def enterprise_person_ids(path, threshold=None):
    """{patient_id: enterprise person id} for every chart linked to another chart (read by other engines)."""
    return _decide(path, build(path), threshold)[0]


def person_of(path, patient_id, threshold=None):
    return enterprise_person_ids(path, threshold).get(patient_id, patient_id)


# ---- engine -----------------------------------------------------------------------------------------------------------
def _groups(path, ids, by):
    if by == 'none' or not ids: return {}
    col = {'practice': 'pcp_practice_id', 'payer': 'payer_id'}[by]
    db = pl._ro(_semantic(path))
    try:
        marks = ','.join('?' * len(ids))
        return {pid: g for pid, g in db.execute(f'SELECT patient_id, {col} FROM patient WHERE patient_id IN ({marks})', sorted(ids))}
    finally:
        db.close()


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    scored = build(path)
    assign, links = _decide(path, scored, p['threshold'])
    if patient_ids is not None:  # scope: persons with at least one chart in the population
        scope = set(patient_ids)
        keep = {v for k, v in assign.items() if k in scope}
        assign = {k: v for k, v in assign.items() if v in keep}
        links = [l for l in links if l['a'] in assign]
    persons = defaultdict(list)
    for chart, person in assign.items(): persons[person].append(chart)
    if p['more_than'] > 1:  # persons with more than N charts
        persons = {k: v for k, v in persons.items() if len(v) > p['more_than']}
        assign = {c: k for k, v in persons.items() for c in v}
        links = [l for l in links if l['a'] in assign]
    groups = _groups(path, assign, p['group_by'])
    rows = []
    if p['output'] == 'pairs':
        rows = [{'chart_a': l['a'], 'chart_b': l['b'], 'person_id': assign[l['a']], 'weight_bits': l['weight'], 'rule': l['rule'],
                 **{f'{f}_agreement': lv for f, lv in l['levels'].items()}} for l in sorted(links, key=lambda l: -l['weight'])]
    elif p['output'] == 'patients' and p['group_by'] == 'none':
        best = defaultdict(float)
        for l in links:
            for c in (l['a'], l['b']): best[c] = max(best[c], l['weight'])
        rows = [{'patient_id': c, 'person_id': person, 'charts_for_person': len(persons[person]), 'strongest_link_bits': best[c]}
                for c, person in sorted(assign.items(), key=lambda kv: (kv[1], kv[0]))]
    else:
        by = defaultdict(lambda: {'persons': set(), 'charts': 0})
        for person, charts in persons.items():
            for c in charts:
                g = groups.get(c) if p['group_by'] != 'none' else 'all'
                by[g]['persons'].add(person); by[g]['charts'] += 1
        rows = [{p['group_by'] if p['group_by'] != 'none' else 'scope': g, 'persons_with_duplicates': len(v['persons']),
                 'charts': v['charts']} for g, v in sorted(by.items(), key=lambda kv: (-len(kv[1]['persons']), str(kv[0])))]
    return {'version': VERSION, 'linkage': pl.VERSION, 'parameters': p, 'source_digest': scored['source_digest'],
            'charts_compared': scored['charts'] if patient_ids is None else len(patient_ids), 'candidate_pairs': scored['candidate_pairs'],
            'persons_with_duplicates': len(persons), 'linked_charts': len(assign), 'extra_charts': len(assign) - len(persons),
            'links': len(links), 'rules': {r: sum(l['rule'] == r for l in links) for r in ('fellegi_sunter', 'member_id', 'mrn', 'confirmed')},
            'patient_ids': sorted(assign), 'rows': rows, 'u_probabilities': scored['u_probabilities'],
            'evidence': [{'chart_a': l['a'], 'chart_b': l['b'], 'weight_bits': l['weight'], 'rule': l['rule'], 'field_weights': l['weights']}
                         for l in sorted(links, key=lambda l: -l['weight'])[:50]]}


def answer(report, plan, question):
    p = report['parameters']
    method = (f"Fellegi-Sunter record linkage ({report['linkage']}): {report['candidate_pairs']} blocked candidate pairs among "
              f"{report['charts_compared']} charts; linked at >= {p['threshold']} bits or a shared member ID / MRN with agreeing birth dates "
              f"({report['rules']}).")
    n = report['persons_with_duplicates']
    value = (f"{n} people have more than {p['more_than'] if p['more_than'] > 1 else 'one'} chart ({report['linked_charts']} charts, {report['extra_charts']} extra), "
             f"from {report['links']} links.")
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    count_q = bool(re.search(r'\bhow many\b|\bnumber of\b|\bcount\b', question or '', re.I))
    count = (report['extra_charts'] if re.search(r'\b(records|charts)\b', question or '', re.I) and not
             re.search(r'\b(patients|people|persons|members)\b\s+(?:have|with)', question or '', re.I) else n) if count_q else n
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['rows'], 'count': count,
            'patient_ids': report['patient_ids'], 'value': value, 'method': method,
            'definitions': [{'term': 'duplicate patient record', 'basis': 'two or more received charts linked to one enterprise person '
                             'by probabilistic record linkage (Fellegi & Sunter 1969)'}],
            'evidence': report['evidence'], 'membership_role': 'linked_charts',
            'limitations': [method, 'Probabilistic links from client-visible demographics; a link is a likely duplicate for review, '
                            'not a confirmed merge.']}


# ---- exact routing ----------------------------------------------------------------------------------------------------
_DUP = re.compile(r'\b(duplicate|duplicated)\s+(patient\s+|member\s+)?(records?|charts?|patients?|members?|mrns?)\b'
                  r'|\b(more than (?:one|two|three|\d+)|multiple|two or more|several)\s+(charts?|mrns?|medical record numbers?|patient records?)\b'
                  r'|\b(same (?:person|patient)|record linkage|enterprise (?:person|patient) ids?|identity resolution)\b', re.I)
_REJECT = re.compile(r'\b(claims?|lab|labs|results?|orders?|prescriptions?|fills?|encounters?|visits?|notes?|files?|deliver|'
                     r'messages?|hl7|x12|since|before|after|between|in \d{4}|last (?:year|month)|hba1c|a1c|diabet|heart|'
                     r'admission|conversion|go-live|renumber)\b', re.I)


def local_question(question, as_of=None):
    """Plain duplicate-patient questions (no other qualifier) -> the record_linkage graph."""
    from .foundation_engines import graph
    q = ' '.join((question or '').split())
    if not _DUP.search(q) or _REJECT.search(q) or re.search(r'\bdefinition\s*:', q, re.I): return None
    group = 'practice' if re.search(r'\b(by|per|each|for every) (pcp )?practice\b', q, re.I) else \
        'payer' if re.search(r'\b(by|per|each) payer\b', q, re.I) else 'none'
    if re.search(r'\bby\b|\bper\b', q, re.I) and group == 'none': return None  # an unhandled breakdown: leave it to others
    if re.search(r'\b(pairs?|evidence|why|weights?)\b', q, re.I): output = 'pairs'
    elif group != 'none' or re.search(r'\bhow many\b|\bnumber of\b|\bcount\b', q, re.I): output = 'summary'
    else: output = 'patients'
    m = re.search(r'\bmore than (\d+|one|two|three|four|five)\b', q, re.I)
    more = int({'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5}.get(m.group(1).lower(), m.group(1))) if m else 1
    if m and re.search(r'\bmore than \S+\s+(?:payers?|addresses|phones?|practices?|providers?)\b', q, re.I): return None
    return graph(question, 'record_linkage', {'output': output, 'threshold': pl.DEFAULTS['threshold'], 'group_by': group, 'more_than': more},
                 'Duplicate patient records by Fellegi-Sunter record linkage')
