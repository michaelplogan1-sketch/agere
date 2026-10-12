"""Versioned index/endpoint definitions over the shared longitudinal ledger.

One index per patient, selected without looking at the endpoint. A zero denotes
no matching *received observation* in a calendar-mature window, never no care.
Persist complete denominator accounting and exact evidence, not top-k samples.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import time
import zlib

from ...contracts import digest
from . import longitudinal as ledger
from .clinical_events import iso

VERSION = 'observed-outcomes/1'
FILTERS = {'all', 'indexed', 'mature_labeled', 'observed', 'not_observed_mature', 'unresolved'}
LIMITS = [
    'All current data and definitions are synthetic development inputs; these are not validated clinical or payer measures.',
    'A zero label means no matching received observation, never no clinical event. Calendar maturity does not establish source coverage or claims runout.',
    'The index is the first unambiguous observed event inside the requested index window, not the first event in a lifetime.',
    'Events on the same calendar day have no verified within-day order; the supplied definitions start at day one.',
    'Explicit encounter links are source-scoped. Missing identity permits only provisional observation links, not verified hospital stays or readmissions.',
    'An earlier availability cutoff cannot reconstruct superseded or quarantined historical records from this current snapshot.',
    'These receipts do not identify causes, estimate intervention benefit, adjust provider case mix or authorize treatment.',
    'Model fitting still requires source-family partitioning and features actually available at the index. No model is trained or promoted by this operation.',
]


def definitions():
    raw = json.loads(Path(__file__).with_name('outcome_contracts.json').read_text())
    if raw['schema'] != 'observed-outcome-contracts/1' or raw['authority'] != 'internal_synthetic_observation_definitions':
        raise ValueError('Unrecognized outcome definition authority')
    result = {}
    for spec in raw['contracts']:
        validate_definition(spec)
        if spec['id'] in result: raise ValueError('Duplicate outcome definition')
        result[spec['id']] = {**spec, 'hash': digest(spec), 'authority': raw['authority']}
    return result


def validate_definition(s):
    fields = {'id','version','title','purpose','index','endpoint','basis','index_selection',
              'min_days','max_days','source_scope','encounter_rule','meaning'}
    if not isinstance(s, dict) or set(s) != fields: raise ValueError('Explicit outcome definition required')
    if any(not isinstance(s[k], str) or not 1 <= len(s[k]) <= 1200 for k in fields - {'index','endpoint','min_days','max_days'}):
        raise ValueError('Bounded definition text required')
    if not re.fullmatch('[a-z][a-z0-9_]{0,63}', s['id']): raise ValueError('Bounded outcome ID required')
    if s['basis'] not in ('source_event','clinical') or s['index_selection'] != 'first_in_window':
        raise ValueError('Explicit event clock and first index selection required')
    if (any(type(s[k]) is not int for k in ('min_days','max_days'))
            or not 0 <= s['min_days'] <= s['max_days'] <= 3650): raise ValueError('Bounded ordered follow-up window required')
    if s['source_scope'] not in ('any_contributing_source','same_source_organization') or s['encounter_rule'] not in ('same_when_present','different_when_present','unrestricted'):
        raise ValueError('Explicit source and encounter relationship required')
    for key in ('index','endpoint'):
        ledger.validate_selector(s[key])
        # Narrative dates/relations need reviewed event bindings before becoming
        # outcome labels. The existing annotation ledger alone is insufficient.
        if s[key]['kind'] != 'record': raise ValueError('Outcome labels require mapped record events; narrative event binding is not yet implemented')
    return s


def definition(key):
    if not isinstance(key, str) or key not in definitions(): raise ValueError('Unknown registered outcome contract')
    return definitions()[key]


def validate(p, as_of):
    if not isinstance(p, dict) or set(p) != {'contract','index_from','index_through','available_by','filter'}:
        raise ValueError('Explicit outcome contract, index dates, availability cutoff and filter required')
    definition(p['contract'])
    if any(not iso(p[k]) for k in ('index_from','index_through','available_by')):
        raise ValueError('Canonical outcome dates required')
    if not p['index_from'] <= p['index_through'] <= p['available_by'] <= as_of:
        raise ValueError('Ordered index window and observed availability cutoff required')
    if not isinstance(p['filter'], str) or p['filter'] not in FILTERS: raise ValueError('Unknown outcome membership filter')
    return p


def code_identity():
    return digest([ledger.code_identity(), *[hashlib.sha256(Path(p).read_bytes()).hexdigest()
        for p in (__file__, Path(__file__).with_name('outcome_contracts.json'))]])


def scope(p):
    return {k: v for k, v in p.items() if k != 'filter'}


def prepare(path, parameters):
    """Read/hash exactly the relevant ledger rows once per population evaluation."""
    db, meta = ledger.connect(path)
    try:
        p = validate(parameters, meta['snapshot']['as_of']); spec = definition(p['contract'])
        patients = {r[0] for r in db.execute('SELECT id FROM patients')}
        sides = {}
        for side in ('index','endpoint'):
            selector = spec[side]; ledger.validate_selector(selector, meta['vocabulary'])
            query = 'SELECT patient,payload FROM events WHERE ' + ' AND '.join(k+'=?' for k in sorted(selector))
            grouped = defaultdict(list)
            for pid, raw in db.execute(query, [selector[k] for k in sorted(selector)]):
                e = json.loads(raw)
                if pid != e['patient_id'] or pid not in patients: raise ValueError('Outcome event/patient binding failed')
                if not iso(e.get('arrival_date')) or e['arrival_date'] <= p['available_by']:
                    grouped[pid].append(e)
            sides[side] = dict(grouped)
        dependency = {'schema': VERSION, 'code_identity': code_identity(), 'contract_hash': spec['hash'],
            'parameters': scope(p), 'snapshot': meta['snapshot'], 'source_digest': meta['source_digest'],
            'longitudinal_code': meta['code_identity'],
            'events_digest': digest({s: {pid: sorted(v, key=lambda e:e['id']) for pid,v in rows.items()} for s,rows in sides.items()}),
            'population_digest': digest(sorted(patients))}
        return {'identity': digest(dependency), 'dependency': dependency, 'contract': spec,
                'parameters': p, 'patients': patients, **sides}
    finally: db.close()


def encounter_key(e):
    source = e['source']
    parts = (source.get('source_system'), source.get('organization_id'), e.get('encounter_id'))
    return parts if all(parts) else None


def deduplicate(events, clock):
    """Collapse repeated identified events, retaining every supporting record.

    Different records without an encounter key are not declared duplicates simply
    because their dates look alike. Conflicting dates remain separate candidates.
    """
    groups = defaultdict(list)
    for e in events:
        encounter = encounter_key(e)
        key = (encounter, e['event_type'], e[clock]) if encounter else ('record', e['id'])
        groups[key].append(e)
    return [{'event': sorted(rows,key=lambda e:e['id'])[0], 'evidence': sorted(rows,key=lambda e:e['id'])}
            for rows in groups.values()]


def evaluate(pid, index_events, endpoint_events, spec, p):
    """Pure single-patient calculation; no hidden truth, models or source fallbacks."""
    clock = ledger.CLOCKS[spec['basis']]
    if any(e['patient_id'] != pid or e['source']['patient_id'] != pid for e in index_events+endpoint_events):
        raise ValueError('Cross-patient outcome evidence')
    evidence = {e['id']: e for e in index_events+endpoint_events}
    row = {'schema': VERSION, 'patient_id': pid, 'contract': spec, 'parameters': scope(p),
        'status': 'no_observed_index', 'indexed': False, 'index': None, 'window': None,
        'recorded_label': None, 'mature_recorded_label_usable': False, 'matches': [],
        'unresolved_endpoint_evidence': [], 'excluded_endpoint_evidence': [],
        'index_evidence_ids': [], 'calendar_mature': False,
        'coverage': {'status':'unknown', 'no_event_established':False,
                     'reason':'Contributing-source completeness, outside care and reporting lag are not established.'},
        'clinical_outcome_established': False, 'clinical_negative_established': False,
        'model_training_authorized_by_receipt': False, 'evidence': evidence}
    received = lambda e: bool(iso(e.get('arrival_date')) and e['arrival_date'] <= p['available_by'])
    candidate = [e for e in index_events if received(e) and e.get(clock)
                 and p['index_from'] <= e[clock] <= p['index_through']]
    # An undated index could precede the apparent first index inside this window.
    uncertain = [e for e in index_events if (not iso(e.get('arrival_date')) or (received(e) and not e.get(clock)))]
    row['index_evidence_ids'] = sorted(e['id'] for e in candidate+uncertain)
    if uncertain:
        row.update(status='unresolved_index_timing', indexed=None)
        return row
    groups = deduplicate(candidate, clock)
    if not groups: return row
    earliest = min(g['event'][clock] for g in groups)
    tied = [g for g in groups if g['event'][clock] == earliest]
    anchor = tied[0]['event']; ek = encounter_key(anchor)
    conflicting_identity = ek and any(encounter_key(g['event']) == ek and g['event'][clock] != earliest for g in groups)
    if len(tied) != 1 or conflicting_identity:
        row.update(status='ambiguous_index_identity', indexed=None)
        return row
    day = date.fromisoformat(earliest); end = day+timedelta(days=spec['max_days'])
    row.update(indexed=True, index={'event_id':anchor['id'], 'date':earliest,
        'evidence_ids':[e['id'] for e in tied[0]['evidence']],
        'encounter_identity':'explicit_source_scoped' if ek else 'unavailable'},
        window={'start':str(day+timedelta(days=spec['min_days'])), 'end':str(end),
                'bounds':'inclusive', 'basis':spec['basis'], 'feature_cutoff':earliest,
                'available_by':p['available_by']}, calendar_mature=str(end) <= p['available_by'])
    for group in deduplicate(endpoint_events, clock):
        e = group['event']; ids = [v['id'] for v in group['evidence']]; reason = None; unresolved = None
        delta = (date.fromisoformat(e[clock])-day).days if e.get(clock) else None
        source_a = tuple(anchor['source'].get(k) for k in ('source_system','organization_id'))
        source_b = tuple(e['source'].get(k) for k in ('source_system','organization_id'))
        other = encounter_key(e)
        if e['id'] == anchor['id'] or (e['source']['record_id'] == anchor['source']['record_id']
                and not (spec['basis']=='clinical' and e['event_type']!=anchor['event_type'])):
            reason = 'same_source_record'
        elif iso(e.get('arrival_date')) and not received(e): reason = 'not_received_by_cutoff'
        elif e.get(clock) and e[clock] > p['available_by']: reason = 'event_after_availability_cutoff'
        elif delta is not None and not spec['min_days'] <= delta <= spec['max_days']: reason = 'outside_declared_window'
        elif spec['source_scope'] == 'same_source_organization' and all(source_a+source_b) and source_a != source_b:
            reason = 'different_source_organization'
        elif ek and other and spec['encounter_rule'] == 'same_when_present' and ek != other: reason = 'different_encounter'
        elif ek and other and spec['encounter_rule'] == 'different_when_present' and ek == other: reason = 'same_encounter'
        elif not iso(e.get('arrival_date')): unresolved = 'unknown_arrival_date'
        elif delta is None: unresolved = 'missing_selected_clock'
        elif delta == 0: unresolved = 'within_day_order_unknown'
        elif spec['source_scope'] == 'same_source_organization' and not all(source_a+source_b): unresolved = 'source_organization_unknown'
        if reason:
            row['excluded_endpoint_evidence'].append({'evidence_ids':ids,'reason':reason})
        elif unresolved:
            row['unresolved_endpoint_evidence'].append({'evidence_ids':ids,'reason':unresolved})
        else:
            row['matches'].append({'endpoint_id':e['id'],'date':e[clock],'days_after_index':delta,
                'evidence_ids':ids, 'link':'explicit_source_scoped_encounters' if ek and other else 'provisional_observation_link',
                'verified_new_stay':False, 'verified_episode':False})
    row['matches'].sort(key=lambda x:(x['date'],x['endpoint_id']))
    if row['matches']: row.update(status='observed', recorded_label=1)
    elif row['unresolved_endpoint_evidence']: row['status']='unresolved_endpoint_timing_or_link'
    elif not row['calendar_mature']: row['status']='incomplete_followup'
    else: row.update(status='not_observed_mature', recorded_label=0)
    row['mature_recorded_label_usable'] = row['calendar_mature'] and row['recorded_label'] is not None
    return row


def membership(row, filter_name):
    if filter_name == 'all': return True
    if filter_name == 'indexed': return row['indexed']
    if filter_name == 'mature_labeled':
        if row['indexed'] is None: return None
        if not row['indexed'] or not row['calendar_mature']: return False
        return True if row['recorded_label'] is not None else None
    if filter_name == 'unresolved': return row['recorded_label'] is None
    if row['recorded_label'] is None: return None
    return row['status'] == filter_name


def storage(path):
    db = sqlite3.connect(Path(path)/'outcomes.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,patient TEXT,payload BLOB)')
    return db


def receipt_id(prepared, pid):
    return digest([prepared['identity'],pid])


def execute(path, patient_ids, parameters, *, prepared=None, preview_limit=8):
    start = time.perf_counter(); prepared = prepared or prepare(path, parameters)
    p = validate(parameters, prepared['dependency']['snapshot']['as_of'])
    if scope(prepared['parameters']) != scope(p): raise ValueError('Outcome preparation mismatch')
    ids = sorted(set(patient_ids)); spec = prepared['contract']
    if set(ids)-prepared['patients']: raise ValueError('Unknown outcome patient')
    if type(preview_limit) is not int or not 0 <= preview_limit <= 20: raise ValueError('Bounded outcome preview required')
    counts=Counter(); statuses=Counter(); processing=Counter(); preview=[]; yes=set(); unknown=set(); db=storage(path)
    try:
        with db:
            for pid in ids:
                key = receipt_id(prepared,pid); old=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
                if old: row=json.loads(zlib.decompress(old[0])); processing['patient_cache_hits']+=1
                else:
                    row=evaluate(pid,prepared['index'].get(pid,[]),prepared['endpoint'].get(pid,[]),spec,p)
                    row.update(id=key,dependency=prepared['dependency'],identity=prepared['identity'])
                    db.execute('INSERT INTO receipts VALUES (?,?,?)',(key,pid,zlib.compress(json.dumps(row,allow_nan=False).encode())))
                    processing['patient_outcomes_computed']+=1
                statuses[row['status']]+=1
                for name,value in [('indexed',row['indexed'] is True),('calendar_mature',row['calendar_mature']),
                    ('observed',row['recorded_label']==1),('no_matching_received_event_mature',row['recorded_label']==0),
                    ('unresolved',row['recorded_label'] is None),('mature_recorded_label_usable',row['mature_recorded_label_usable'])]: counts[name]+=bool(value)
                counts['provisional_observation_links']+=sum(x['link']=='provisional_observation_link' for x in row['matches'])
                selected=membership(row,p['filter'])
                if selected is True: yes.add(pid)
                elif selected is None: unknown.add(pid)
                sample={k:row[k] for k in ('id','patient_id','status','index','window','calendar_mature','recorded_label')}
                sample['matching_endpoints']=len(row['matches'])
                sample['membership']='matched' if selected is True else 'unknown' if selected is None else 'not_matched'
                # Show real indexed evidence before the many no-index rows.
                preview=sorted(preview+[sample],key=lambda r:(r['index'] is None,r['status']!='observed',r['patient_id']))[:preview_limit]
        return yes,unknown,{'schema':VERSION,'synthetic':True,'contract':spec,'parameters':p,
            'identity':prepared['identity'],'patients_evaluated':len(ids),'population_digest':digest(ids),
            'status_counts':dict(statuses),'outcome_counts':dict(counts),'patients':preview,
            'processing_counts':dict(processing),'all_patient_receipts_persisted':True,
            'new_paid_api_calls':0,'elapsed_seconds':time.perf_counter()-start,'limits':LIMITS}
    finally: db.close()


def inspect(path, key):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key): raise ValueError('Exact outcome receipt ID required')
    db=storage(path)
    try: raw=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
    finally: db.close()
    if raw is None: raise ValueError('Unknown outcome receipt')
    row=json.loads(zlib.decompress(raw[0]))
    fresh=prepare(path,{**row['parameters'],'filter':'all'})
    if row['identity'] != fresh['identity'] or key != receipt_id(fresh,row['patient_id']):
        raise ValueError('Outcome source or definition changed; evaluate again')
    return row


def export_labels(path, patient_ids, parameters, output):
    """Export task targets for later family-separated training, without fitting.

    Nulls are retained and immature positives are excluded from fixed-horizon
    training eligibility; otherwise a truncated window selects on the label.
    """
    output=Path(output)
    if output.exists(): raise ValueError('Fresh outcome-label file required')
    prep=prepare(path,parameters); _,_,summary=execute(path,patient_ids,parameters,prepared=prep,preview_limit=0)
    db=storage(path); counts=Counter()
    try:
        with output.open('x') as f:
            for pid in sorted(set(patient_ids)):
                raw=db.execute('SELECT payload FROM receipts WHERE id=?',(receipt_id(prep,pid),)).fetchone()
                row=json.loads(zlib.decompress(raw[0])); counts[row['status']]+=1
                f.write(json.dumps({'patient_id':pid,'outcome_receipt_id':row['id'],'contract_hash':prep['contract']['hash'],
                    'status':row['status'],'index_date':row['index']['date'] if row['index'] else None,
                    'recorded_label':row['recorded_label'],'mature_recorded_label_usable':row['mature_recorded_label_usable'],
                    'followup':row['window'],'capture_status':'unknown','clinical_label':None,
                    'training_status':'candidate_only_requires_family_partition_and_index_available_features'},allow_nan=False)+'\n')
    finally: db.close()
    return {'schema':VERSION,'synthetic':True,'identity':prep['identity'],'rows':sum(counts.values()),
        'statuses':dict(counts),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
        'model_fitted':False,'summary':summary,'limits':LIMITS}
