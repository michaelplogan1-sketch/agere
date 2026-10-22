"""Object-centric event log (OCEL 2.0) built from the semantic layer, for pathway conformance.

A classic event log forces one case notion (one row per patient-event). OCEL 2.0 (van der Aalst et al., arXiv 2403.01975)
keeps every object an event touches: a discharge event relates the patient AND the hospital stay; a follow-up visit relates
the patient, its encounter and every stay it follows up; a fill relates the patient, the fill and the stays whose GDMT
window it falls in; a lab result relates the patient and its order/result. Flattening that to one case per patient
duplicates shared events (convergence) or drops their other objects (divergence), so conformance numbers computed on the
flattened log miscount. Here:
  objects   patient, stay (one object per hospital stay, merged across ADT / encounter / payer-claim sources that report the
            same admission), encounter (outpatient visit), fill (pharmacy fill), lab (result)
  events    admit, discharge (one per source report, all related to the same stay object), visit, fill, lab_result
build() returns the in-memory log; to_ocel_json() serializes it in the OCEL 2.0 JSON layout (objectTypes, eventTypes,
objects with relationships, events with qualified object relationships); metrics() reports the object-centric counts
that flattening would hide. Read-only over the synthetic semantic layer.
"""
from collections import Counter, defaultdict
from datetime import date

VERSION = 'ocel/1'
OUTPATIENT = ('primary_care', 'specialty', 'telehealth')
MERGE_DAYS = 1  # discharges of one patient within a day of each other (any source) are one stay object


def _d(s): return date.fromisoformat(str(s)[:10])


class Log:
    def __init__(self):
        self.objects = {}  # id -> {'id', 'type', 'attributes': {}, 'relationships': [(object_id, qualifier)]}
        self.events = []   # {'id', 'type', 'time', 'attributes': {}, 'relationships': [(object_id, qualifier)]}

    def obj(self, oid, otype, **attrs):
        o = self.objects.setdefault(oid, {'id': oid, 'type': otype, 'attributes': {}, 'relationships': []})
        o['attributes'].update({k: v for k, v in attrs.items() if v is not None})
        return o

    def event(self, etype, time, rels, **attrs):
        e = {'id': f'e{len(self.events) + 1}', 'type': etype, 'time': str(time)[:10], 'attributes': attrs, 'relationships': list(rels)}
        self.events.append(e)
        return e

    def relate(self, event, oid, qualifier):
        if (oid, qualifier) not in event['relationships']: event['relationships'].append((oid, qualifier))

    def of_type(self, otype):
        return [o for o in self.objects.values() if o['type'] == otype]


def merge_stays(rows):
    """rows: [(patient_id, admit, discharge, source, organization)] -> [{'id', 'patient_id', 'admit', 'discharge', 'sources': [..],
    'reports': n}] one per stay object (same patient, discharge within MERGE_DAYS of the object's first discharge)."""
    out, by_patient = [], defaultdict(list)
    for r in rows:
        if r[2]: by_patient[r[0]].append(r)
    for pid, rs in by_patient.items():
        rs.sort(key=lambda r: (str(r[2])[:10], str(r[1] or '')[:10]))
        cur = None
        for p, admit, dis, src, org in rs:
            if cur is not None and abs((_d(dis) - _d(cur['discharge'])).days) <= MERGE_DAYS:
                cur['sources'].append(src); cur['reports'] += 1
                if admit and (not cur['admit'] or str(admit) < cur['admit']): cur['admit'] = str(admit)[:10]
                continue
            cur = {'id': f'stay:{pid}:{str(dis)[:10]}', 'patient_id': pid, 'admit': str(admit)[:10] if admit else None,
                   'discharge': str(dis)[:10], 'sources': [src], 'reports': 1, 'organization_id': org}
            out.append(cur)
    return out


def build(stays=(), visits=(), fills=(), labs=(), patients=()):
    """stays: merge_stays() output; visits: [(encounter_id, patient_id, date, setting)]; fills: [(fill_id, patient_id, date,
    drug_class)]; labs: [(record_id, patient_id, date, concept)]; patients: [(patient_id, {attrs})]. -> Log"""
    log = Log()
    for pid, attrs in patients: log.obj(f'patient:{pid}', 'patient', **(attrs or {}))
    for s in stays:
        p = f"patient:{s['patient_id']}"
        log.obj(p, 'patient')
        st = log.obj(s['id'], 'stay', admit=s['admit'], discharge=s['discharge'], sources=sorted(set(s['sources'])), reports=s['reports'])
        st['relationships'].append((p, 'of_patient'))
        if s['admit']: log.event('admit', s['admit'], [(p, 'patient'), (s['id'], 'stay')])
        for src in s['sources']:  # one discharge event per source report, all on the same stay object
            log.event('discharge', s['discharge'], [(p, 'patient'), (s['id'], 'stay')], source=src)
    for eid, pid, d, setting in visits:
        p = f'patient:{pid}'; o = f'encounter:{eid}'
        log.obj(p, 'patient'); log.obj(o, 'encounter', setting=setting)['relationships'].append((p, 'of_patient'))
        log.event('visit', d, [(p, 'patient'), (o, 'encounter')], setting=setting)
    for fid, pid, d, cls in fills:
        p = f'patient:{pid}'; o = f'fill:{fid}'
        log.obj(p, 'patient'); log.obj(o, 'fill', drug_class=cls)['relationships'].append((p, 'of_patient'))
        log.event('fill', d, [(p, 'patient'), (o, 'fill')], drug_class=cls)
    for rid, pid, d, concept in labs:
        p = f'patient:{pid}'; o = f'lab:{rid}'
        log.obj(p, 'patient'); log.obj(o, 'lab', concept=concept)['relationships'].append((p, 'of_patient'))
        log.event('lab_result', d, [(p, 'patient'), (o, 'lab')], concept=concept)
    return log


def link_followups(log, stay_ids, event_type, within_days, match=lambda e: True, qualifier='follows_up'):
    """Relate each event of event_type to every stay object it falls within `within_days` after (same patient): the
    object-centric link that lets one visit or fill serve several discharges. -> {stay_id: [event, ...]}"""
    by_patient = defaultdict(list)
    for e in log.events:
        if e['type'] == event_type and match(e):
            pid = next(o for o, q in e['relationships'] if q == 'patient')
            by_patient[pid].append(e)
    out = {}
    for sid in stay_ids:
        st = log.objects[sid]
        pid = next(o for o, q in st['relationships'] if q == 'of_patient')
        d0 = _d(st['attributes']['discharge'])
        hits = [e for e in by_patient.get(pid, []) if 0 < (_d(e['time']) - d0).days <= within_days]
        for e in hits: log.relate(e, sid, qualifier)
        out[sid] = hits
    return out


def metrics(log):
    """Object-centric counts that a one-case-per-patient flattening hides."""
    disch = [e for e in log.events if e['type'] == 'discharge']
    stays = log.of_type('stay')
    multi_source = [s for s in stays if len(set(s['attributes'].get('sources') or [])) > 1]
    dup_reports = [s for s in stays if s['attributes'].get('reports', 1) > 1]
    shared = Counter()
    for e in log.events:
        n = sum(1 for _, q in e['relationships'] if q in ('follows_up', 'gdmt_window'))
        if n > 1: shared[e['type']] += 1
    return {'objects_by_type': dict(Counter(o['type'] for o in log.objects.values())),
            'events_by_type': dict(Counter(e['type'] for e in log.events)),
            'discharge_reports': len(disch), 'stay_objects': len(stays),
            'stays_reported_by_several_sources': len(multi_source), 'stays_with_duplicate_reports': len(dup_reports),
            'flattened_discharge_overcount': len(disch) - len(stays),
            'events_shared_by_several_stays': dict(shared),
            'relationships': sum(len(e['relationships']) for e in log.events)}


def to_ocel_json(log, max_objects=None, max_events=None):
    """OCEL 2.0 JSON layout (objectTypes / eventTypes with attribute declarations, objects, events). Truncation is stated."""
    def attr_types(items):
        types = defaultdict(dict)
        for it in items:
            for k, v in it['attributes'].items():
                types[it['type']][k] = 'float' if isinstance(v, (int, float)) and not isinstance(v, bool) else 'string'
        return [{'name': t, 'attributes': [{'name': k, 'type': ty} for k, ty in sorted(a.items())]} for t, a in sorted(types.items())] + \
            [{'name': t, 'attributes': []} for t in sorted({i['type'] for i in items} - set(types))]
    objs = list(log.objects.values())[:max_objects] if max_objects else list(log.objects.values())
    evs = log.events[:max_events] if max_events else log.events
    return {'objectTypes': attr_types(objs), 'eventTypes': attr_types(evs),
            'objects': [{'id': o['id'], 'type': o['type'],
                         'attributes': [{'name': k, 'time': '1970-01-01T00:00:00Z', 'value': v if isinstance(v, (int, float, str)) else ','.join(map(str, v))}
                                        for k, v in o['attributes'].items()],
                         'relationships': [{'objectId': t, 'qualifier': q} for t, q in o['relationships']]} for o in objs],
            'events': [{'id': e['id'], 'type': e['type'], 'time': f"{e['time']}T00:00:00Z",
                        'attributes': [{'name': k, 'value': v} for k, v in e['attributes'].items()],
                        'relationships': [{'objectId': t, 'qualifier': q} for t, q in e['relationships']]} for e in evs],
            'truncated': {'objects': len(log.objects) - len(objs), 'events': len(log.events) - len(evs)},
            'standard': 'OCEL 2.0 JSON (ocel-standard.org)'}
