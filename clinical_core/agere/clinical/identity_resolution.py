"""Reversible identity resolution on top of record_linkage: contradiction checks, recorded split / confirm decisions, recompute.

Probabilistic linkage (record_linkage.py, Fellegi-Sunter) will sometimes merge two people. This module is the recovery path
(AGERE research report 2026-10-18: identity contradiction checks and reversible splits):

  contradictions(path, assign)   for every linked cluster, charts that cannot be one person on the received data:
        sex_conflict           the two charts record different sex
        birth_date_conflict    full birth dates that differ by more than a keying error (one digit / month-day swap is not a conflict)
        lifetime_conflict      one chart has a death date and another chart has care dated more than 30 days after it
        overlapping_stays      two stays at different organisations that share at least one night
      hard conflicts (all of the above) mean the cluster should be reviewed; nothing is split automatically.
  propose_splits(...)            for a cluster with hard conflicts, the weakest links to cut (ascending pair weight) until the
                                 conflicting charts fall in different groups, with the evidence that drove each cut
  append(path, kind, ...)        a decision in the append-only ledger <stack>/identity-decisions.jsonl:
        split   {person_id, groups}      charts in different groups can never be linked, directly or through a third chart
        reject  {a, b}                   these two charts are not the same person
        confirm {a, b}                   these two charts are the same person (a must-link, processed before any score)
        undo    {target}                 withdraws an earlier decision; the ledger is never edited, only extended
  overrides(path)                state of the ledger after undos: {'confirm': [(a, b)], 'reject': [(a, b)]}
  decide(scored, threshold, overrides)  the record_linkage decision with constraints: confirms first, then links by descending
                                 weight, and a union is refused when it would join two components that hold a rejected pair

Reversible by construction: a person id is a pure function of (scored pairs, threshold, ledger after undos); undoing a split
recomputes the original cluster. Every ledger line carries the sha256 of the line before it, so an edited history is visible
(verify_ledger). Client-visible demographics and received dates only; synthetic data only; no model calls.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import time

VERSION = 'identity-resolution/1'
LEDGER = 'identity-decisions.jsonl'
KINDS = ('split', 'reject', 'confirm', 'undo')
LIFETIME_GRACE_DAYS = 30
OUTPUTS = ('review', 'persons', 'ledger')


# ---- ledger ------------------------------------------------------------------------------------------------------------
def _path(stack):
    return Path(stack) / LEDGER


def events(stack):
    f = _path(stack)
    if not f.exists(): return []
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]


def _line_hash(e):
    return hashlib.sha256(json.dumps({k: v for k, v in e.items() if k != 'sha'}, sort_keys=True).encode()).hexdigest()


def verify_ledger(stack):
    """-> (ok, first_bad_index). Each line stores the hash of the previous line."""
    prev = None
    for i, e in enumerate(events(stack)):
        if e.get('prev') != prev or e.get('sha') != _line_hash(e): return False, i
        prev = e['sha']
    return True, None


def append(stack, kind, *, actor, reason, a=None, b=None, person_id=None, groups=None, target=None):
    if kind not in KINDS: raise ValueError(f'kind: one of {KINDS}')
    if not actor or not reason: raise ValueError('a decision names who made it and why')
    ev = events(stack)
    ids = {e['id'] for e in ev}
    body = {'kind': kind, 'actor': actor, 'reason': reason}
    if kind in ('reject', 'confirm'):
        if not a or not b or a == b: raise ValueError('two different chart ids (a, b)')
        body.update(a=a, b=b)
    elif kind == 'split':
        if not person_id or not groups or len(groups) < 2 or any(not g for g in groups): raise ValueError('person_id and at least two non-empty groups')
        if len({c for g in groups for c in g}) != sum(len(g) for g in groups): raise ValueError('a chart can be in one group only')
        body.update(person_id=person_id, groups=[sorted(g) for g in groups])
    else:
        if target not in ids: raise ValueError('target must be an earlier decision id')
        if any(e['kind'] == 'undo' and e['target'] == target for e in ev): raise ValueError('that decision is already undone')
        body['target'] = target
    prev = ev[-1]['sha'] if ev else None
    e = {'id': f'ID-{len(ev) + 1:04d}', 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **body, 'prev': prev}
    e['sha'] = _line_hash(e)
    with open(_path(stack), 'a') as fh: fh.write(json.dumps(e, sort_keys=True) + '\n')
    return e


def overrides(stack):
    """Ledger state after undos -> {'confirm': [(a, b)], 'reject': [(a, b)], 'active': [event ids]}; None when there is no ledger."""
    ev = events(stack)
    if not ev: return None
    undone = {e['target'] for e in ev if e['kind'] == 'undo'}
    out = {'confirm': [], 'reject': [], 'active': []}
    for e in ev:
        if e['kind'] == 'undo' or e['id'] in undone: continue
        out['active'].append(e['id'])
        if e['kind'] == 'confirm': out['confirm'].append((e['a'], e['b']))
        elif e['kind'] == 'reject': out['reject'].append((e['a'], e['b']))
        elif e['kind'] == 'split':
            gs = e['groups']
            for i, g in enumerate(gs):
                for h in gs[i + 1:]:
                    out['reject'] += [(x, y) for x in g for y in h]
    return out if out['active'] else None


# ---- constrained decision ------------------------------------------------------------------------------------------------
def decide(scored, threshold=None, over=None):
    """record_linkage.decide with constraints. -> (assignments {chart: person_id}, links [pair + rule]). Without overrides this is
    exactly the record_linkage result."""
    from . import person_linkage as pl
    threshold = pl.DEFAULTS['threshold'] if threshold is None else threshold
    cannot = {frozenset(p) for p in (over or {}).get('reject', [])}
    must = [tuple(sorted(p)) for p in (over or {}).get('confirm', [])]
    members, owner = {}, {}

    def comp(x):
        if x not in owner: owner[x] = x; members[x] = {x}
        return owner[x]

    def blocked(ra, rb):
        small, large = (members[ra], members[rb]) if len(members[ra]) <= len(members[rb]) else (members[rb], members[ra])
        return any(frozenset((x, y)) in cannot for x in small for y in large)

    def union(a, b):
        ra, rb = comp(a), comp(b)
        if ra == rb: return True
        if blocked(ra, rb): return False
        if len(members[ra]) < len(members[rb]): ra, rb = rb, ra
        members[ra] |= members[rb]
        for x in members[rb]: owner[x] = ra
        del members[rb]
        return True
    links = []
    for a, b in must:
        if union(a, b): links.append({'a': a, 'b': b, 'weight': 0.0, 'levels': {}, 'weights': {}, 'rule': 'confirmed'})
    cands = []
    for p in scored['pairs']:
        dob_ok = p['levels'].get('birth_date') in ('agree', 'partial')
        rule = ('fellegi_sunter' if p['weight'] >= threshold else 'member_id' if p['shared_member_id'] and dob_ok
                else 'mrn' if p['shared_mrn'] and dob_ok else None)
        if rule: cands.append((p, rule))
    for p, rule in sorted(cands, key=lambda x: (-x[0]['weight'], x[0]['a'], x[0]['b'])):
        if frozenset((p['a'], p['b'])) in cannot: continue
        if union(p['a'], p['b']): links.append({**p, 'rule': rule})
    out = {}
    for ms in members.values():
        if len(ms) > 1:
            for m in ms: out[m] = 'AGP-' + min(ms)
    return out, links


# ---- contradictions -------------------------------------------------------------------------------------------------------
def _days(a, b):
    from datetime import date
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


def _facts(db, charts):
    marks = ','.join('?' * len(charts))
    facts = {c: {'sex': None, 'birth_date': None, 'death': None, 'last_care': None, 'stays': []} for c in charts}
    for pid, sex, born in db.execute(f'SELECT patient_id, sex_reported, birth_date FROM patient WHERE patient_id IN ({marks})', charts):
        facts[pid].update(sex=(sex or '')[:1].upper() or None, birth_date=born)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    if 'death' in tables:
        for pid, d in db.execute(f'SELECT patient_id, death_date FROM death WHERE patient_id IN ({marks})', charts):
            facts[pid]['death'] = d
    for table, col in (('visit', 'event_date'), ('measurement', 'event_date'), ('medication', 'event_date')):
        if table in tables:
            for pid, d in db.execute(f'SELECT patient_id, max({col}) FROM {table} WHERE patient_id IN ({marks}) GROUP BY 1', charts):
                if d and (facts[pid]['last_care'] is None or d > facts[pid]['last_care']): facts[pid]['last_care'] = d[:10]
    if 'stay' in tables:
        npi = {}
        if 'organization' in tables:  # a payer claim names the facility by NPI, the EHR by organisation id: one hospital, one key
            npi = {n: o for o, n in db.execute('SELECT organization_id, npi FROM organization WHERE npi IS NOT NULL')}
        for pid, a, d, org in db.execute(f'SELECT patient_id, admit_date, discharge_date, organization_id FROM stay WHERE patient_id IN ({marks})', charts):
            if a and d: facts[pid]['stays'].append((a[:10], d[:10], npi.get(org, org)))
    return facts


def _same_key_kind(a, b):
    return str(a).isdigit() == str(b).isdigit()


def pair_conflicts(fa, fb):
    """Hard conflicts between two charts' facts -> [{'type', 'detail'}]."""
    from . import person_linkage as pl
    out = []
    if fa['sex'] and fb['sex'] and fa['sex'] != fb['sex'] and fa['sex'] in 'MF' and fb['sex'] in 'MF':
        out.append({'type': 'sex_conflict', 'detail': f"{fa['sex']} vs {fb['sex']}"})
    if fa['birth_date'] and fb['birth_date'] and len(fa['birth_date']) == 10 and len(fb['birth_date']) == 10:
        if pl._dob_level(fa['birth_date'], fb['birth_date']) == 'disagree':
            out.append({'type': 'birth_date_conflict', 'detail': f"{fa['birth_date']} vs {fb['birth_date']}"})
    for x, y in ((fa, fb), (fb, fa)):
        if x['death'] and y['last_care'] and _days(x['death'], y['last_care']) > LIFETIME_GRACE_DAYS:
            out.append({'type': 'lifetime_conflict', 'detail': f"death {x['death'][:10]} but care dated {y['last_care']} on the other chart"})
            break
    for a1, d1, o1 in fa['stays']:
        # an organisation key that cannot be resolved (an NPI with no organization row) is never compared with a facility id
        hit = next(((a2, d2, o2) for a2, d2, o2 in fb['stays'] if o1 != o2 and a1 < d2 and a2 < d1 and _same_key_kind(o1, o2)), None)
        if hit:
            out.append({'type': 'overlapping_stays', 'detail': f"{o1} {a1}..{d1} overlaps {hit[2]} {hit[0]}..{hit[1]}"}); break
    return out


def contradictions(db, assign, links=None):
    """-> [{'person_id', 'charts', 'conflicts': [{'a','b','type','detail'}]}] for linked clusters with a hard conflict."""
    clusters = defaultdict(list)
    for c, p in assign.items(): clusters[p].append(c)
    out = []
    for person, charts in sorted(clusters.items()):
        facts = _facts(db, sorted(charts))
        found = []
        cs = sorted(charts)
        for i, a in enumerate(cs):
            for b in cs[i + 1:]:
                found += [{'a': a, 'b': b, **c} for c in pair_conflicts(facts[a], facts[b])]
        if found: out.append({'person_id': person, 'charts': cs, 'conflicts': found})
    return out


def propose_splits(cluster, links):
    """Cut the weakest links (ascending weight) of one conflicted cluster until every conflicting pair is in different groups.
    -> {'person_id', 'cut': [link], 'groups': [[chart]], 'unresolved': [conflict]}."""
    charts = set(cluster['charts'])
    inside = [l for l in links if l['a'] in charts and l['b'] in charts]
    keep = sorted(inside, key=lambda l: (l['rule'] == 'confirmed', l['weight'], l['a'], l['b']))
    cut = []

    def groups(active):
        parent = {c: c for c in charts}

        def find(x):
            while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
            return x
        for l in active: parent[find(l['a'])] = find(l['b'])
        g = defaultdict(list)
        for c in charts: g[find(c)].append(c)
        return [sorted(v) for v in g.values()]
    active = list(keep)
    while True:
        gs = groups(active)
        where = {c: i for i, g in enumerate(gs) for c in g}
        bad = [c for c in cluster['conflicts'] if where[c['a']] == where[c['b']]]
        if not bad or not active: break
        weakest = active.pop(0)  # lowest weight first; a confirmed link (no weight) is cut last
        cut.append({'a': weakest['a'], 'b': weakest['b'], 'weight_bits': weakest['weight'], 'rule': weakest['rule']})
    gs = groups(active)
    where = {c: i for i, g in enumerate(gs) for c in g}
    return {'person_id': cluster['person_id'], 'cut': cut, 'groups': sorted(gs), 'unresolved': [c for c in cluster['conflicts'] if where[c['a']] == where[c['b']]]}


# ---- engine ---------------------------------------------------------------------------------------------------------------
OP = 'identity_resolution'
PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'FALSE MERGES / WRONGLY MERGED PATIENTS / identity contradictions: linked charts that cannot be one person (different sex, '
                  'birth dates that differ by more than a keying error, care after a death date, overlapping inpatient stays at two '
                  'hospitals), the weakest links to split, the recorded split / confirm / undo decisions, and the effective enterprise '
                  'persons after those decisions.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'output': 'review|persons|ledger', 'threshold': 15.0},
            'meaning': 'Reversible identity resolution over record_linkage: review lists linked clusters with a hard identity contradiction '
                       'and the weakest links to cut; persons lists the effective enterprise persons after the recorded decisions (splits and '
                       'confirmed links, undone ones ignored); ledger lists the append-only decisions with their hash chain check.'}
NATIVE = ('count', 'value')


def schema(obj, string, array):
    return obj({'output': string(OUTPUTS, 'review: contradictions and proposed splits; persons: effective persons; ledger: recorded decisions'),
                'threshold': {'type': 'number', 'minimum': 5, 'maximum': 40, 'description': 'Fellegi-Sunter link threshold in bits (default 15)'}})


def validate(p):
    p = dict(p or {})
    if set(p) - {'output', 'threshold'}: raise ValueError('identity_resolution parameters: output, threshold')
    p.setdefault('output', 'review'); p.setdefault('threshold', 15.0)
    if p['output'] not in OUTPUTS: raise ValueError(f'output: one of {OUTPUTS}')
    if type(p['threshold']) not in (int, float) or not 5 <= p['threshold'] <= 40: raise ValueError('threshold: 5..40 bits')
    return p


def execute(path, patient_ids, params, as_of):
    from . import record_linkage as RL, person_linkage as pl
    p = validate(params)
    path = Path(path)
    over = overrides(path)
    ok, bad = verify_ledger(path)
    base = {'schema': VERSION, 'parameters': p, 'ledger_events': len(events(path)), 'ledger_intact': ok, 'active_decisions': (over or {}).get('active', []), 'patient_ids': []}
    if p['output'] == 'ledger':
        return {**base, 'table': events(path), 'ledger_first_bad_line': bad}
    scored = RL.build(path)
    plain, plain_links = decide(scored, p['threshold'], None)
    assign, links = decide(scored, p['threshold'], over)
    scope = set(patient_ids) if patient_ids is not None else None
    if scope is not None:
        keep = {v for k, v in assign.items() if k in scope}
        assign = {k: v for k, v in assign.items() if v in keep}
    if p['output'] == 'persons':
        persons = defaultdict(list)
        for c, person in assign.items(): persons[person].append(c)
        rows = [{'person_id': person, 'charts': sorted(ch), 'chart_count': len(ch),
                 'changed_by_decisions': sorted(ch) != sorted([c for c, x in plain.items() if x == plain.get(ch[0])] or ch)} for person, ch in sorted(persons.items())]
        split_off = sorted(c for c in plain if c not in assign)
        return {**base, 'table': rows, 'persons': len(persons), 'charts_split_off': split_off, 'patient_ids': sorted(assign)}
    db = pl._ro(RL._semantic(path))
    try:
        found = contradictions(db, assign)
    finally:
        db.close()
    proposals = [propose_splits(c, links) for c in found]
    rows = [{'person_id': c['person_id'], 'chart_a': x['a'], 'chart_b': x['b'], 'conflict': x['type'], 'detail': x['detail']} for c in found for x in c['conflicts']]
    return {**base, 'table': rows, 'clusters': len({c['person_id'] for c in found}), 'conflicts': len(rows), 'proposals': proposals,
            'linked_clusters': len(set(assign.values())), 'patient_ids': sorted({ch for c in found for ch in c['charts']})}


def answer(report, plan, question):
    out = report['parameters']['output']
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    method = ('Identity contradiction checks over record_linkage clusters (sex, birth date beyond a keying error, care after a death date, '
              'overlapping stays at two organisations); decisions are an append-only hash-chained ledger and every person id is recomputed '
              'from the scored pairs plus the ledger after undos, so any split can be undone.')
    base = {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report.get('table') or [], 'patient_ids': report['patient_ids'],
            'method': method, 'membership_role': 'selected', 'limitations': [method, 'A contradiction is a reason to review a merge, not proof it is wrong; no merge is split automatically.'],
            'definitions': [{'term': 'hard identity contradiction', 'basis': 'received facts that cannot hold for one person'}]}
    if out == 'ledger':
        n = report['ledger_events']
        return {**base, 'count': n, 'value': f"{n} recorded identity decisions, {len(report['active_decisions'])} active; ledger hash chain {'intact' if report['ledger_intact'] else 'BROKEN at line ' + str(report['ledger_first_bad_line'])}."}
    if out == 'persons':
        return {**base, 'count': report['persons'], 'value': f"{report['persons']} effective enterprise persons with more than one chart after {len(report['active_decisions'])} active decisions; "
                                                             f"{len(report['charts_split_off'])} charts are no longer linked because of recorded splits."}
    if not report['conflicts']:
        return {**base, 'count': 0, 'value': f"No linked cluster has a hard identity contradiction ({report['linked_clusters']} linked clusters checked)."}
    cuts = '; '.join(f"{p['person_id']}: cut {', '.join(c['a'] + '-' + c['b'] for c in p['cut'])}" for p in report['proposals'] if p['cut'])
    return {**base, 'count': report['clusters'], 'value': f"{report['clusters']} of {report['linked_clusters']} linked clusters hold {report['conflicts']} hard contradiction(s). Proposed splits (weakest links first): {cuts or 'none'}."}


_ASK = (r'\bfalse(?:ly)? merg\w*|\bwrongly merged\b|\bmerged (?:the )?wrong\b|\bover[- ]?merg\w*|\bidentity (?:contradiction|conflict)s?\b|\bsplit (?:a |the )?(?:merged|linked|duplicate)\b|'
        r'\bundo (?:a |the )?(?:merge|split|link)\b|\beffective (?:enterprise )?persons?\b|\bafter (?:the )?(?:identity )?splits?\b|\bidentity ledger\b|\bsplit decisions?\b|\bidentity decisions?\b|\blinked charts? that (?:can.?t|cannot) be (?:the )?same\b|\bnot the same person\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    low = ' '.join(str(question).lower().split())
    if not re.search(_ASK, low): return None
    out = 'ledger' if re.search(r'\b(?:ledger|decisions?|audit|history|undo)\b', low) else \
        'persons' if re.search(r'\b(?:effective|current|after (?:the )?splits?|how many persons?)\b', low) else 'review'
    return plan(OP, {'output': out}, 'Reversible identity resolution: contradictions, splits and recorded decisions', question, [f'output {out}'])


TEMPLATES = ['Which linked patient charts are probably false merges?', 'Show identity contradictions in the linked charts',
             'List the recorded identity split decisions and check the ledger', 'How many effective enterprise persons are there after the splits?']
