"""Received-source process investigation in the existing population decision store.

No clinical inference, hypothesis significance, new work or model promotion.
"""
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
import hashlib
import json
import math
from ...contracts import digest
from .. import narrative_sources
from . import question_decision as q, evidence_threads, source_blocks

VERSION = 'population-source-discovery/1'
MAX_RECORDS = 100000


def identity():
    return digest([VERSION, narrative_sources.VERSION,
        hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        hashlib.sha256(Path(source_blocks.__file__).read_bytes()).hexdigest()])


def day(value):
    if not isinstance(value, str): return None
    try:
        parsed = date.fromisoformat(value)
        return value if parsed.isoformat() == value else None
    except ValueError: return None


def decision(retriever, key):
    row = q.inspect(retriever, key)
    if row.get('schema') != q.POPULATION_VERSION: raise ValueError('Population decision required')
    return row


def options(retriever, request):
    q.care.exact(request, 'decision_id')
    row = decision(retriever, request['decision_id'])
    db, _ = evidence_threads.connect(retriever.path)
    try: streams = [r[0] for r in db.execute('SELECT DISTINCT stream FROM records ORDER BY stream')]
    finally: db.close()
    return {'streams': streams, 'available_by': row['decision_date'], 'max_months': 24,
        'scope': 'Choose one stream and window before inspecting its pattern; exploratory, no significance claim.'}


def inputs(retriever, request):
    q.care.exact(request, 'decision_id stream months')
    if not isinstance(request['stream'], str) or not 1 <= len(request['stream']) <= 128:
        raise ValueError('One received source stream required')
    if type(request['months']) is not int or not 2 <= request['months'] <= 24:
        raise ValueError('Choose 2 to 24 calendar months')
    row = decision(retriever, request['decision_id'])
    selected = set(row['selection']['patient_ids'])
    db, meta = evidence_threads.connect(retriever.path)
    records = []
    try:
        db.execute('BEGIN')
        if meta['snapshot'] != row['selection']['snapshot']: raise ValueError('Population/source snapshot differs')
        if not db.execute('SELECT 1 FROM records WHERE stream=? LIMIT 1', (request['stream'],)).fetchone():
            raise ValueError('Unknown received stream')
        for rid, pid, payload in db.execute('SELECT id,patient,payload FROM records WHERE stream=? ORDER BY id', (request['stream'],)):
            if pid not in selected: continue
            r = json.loads(payload)
            if (r.get('record_id') != rid or r.get('patient_id') != pid or r.get('source_table') != request['stream']
                    or r.get('synthetic') is not True or r.get('identity_status') != 'matched'):
                raise ValueError('Matched same-patient synthetic received source required')
            records.append(r)
            if len(records) > MAX_RECORDS:
                raise ValueError('Selected stream exceeds 100000-record local investigation limit; nothing was sampled or saved')
    finally: db.close()
    tasks = q.population_work_binding(retriever.path, row)
    binding = {'version': identity(), 'decision_id': row['id'], 'selection_digest': digest(row['selection']),
        'request': request, 'records_digest': digest([(r['record_id'], digest(r)) for r in records]),
        'work_digest': digest(tasks)}
    return row, records, tasks, binding


def compare(counts, exposure):
    candidate = source_blocks.poisson_blocks(counts, exposure, penalty=6.0)
    n = sum(counts); t = sum(exposure)
    baseline = {'method': 'constant_received_record_rate', 'count': n, 'calendar_days': t,
        'records_per_day': n/t, 'objective': (n*math.log(n/t) if n else 0.0)-6.0}
    return {'counts': counts, 'candidate': candidate, 'baseline': baseline,
        'penalized_objective_gain': candidate['objective']-baseline['objective'],
        'meaning': 'In-sample fit only. Optimized partitions can improve fit; this is not significance or predictive superiority.'}


def analyze(records, selected, asof, months):
    labels, exposure = source_blocks.month_bins(asof, months)
    start = labels[0]+'-01'
    counts = Counter(); exclusions = Counter(); refs = defaultdict(list); included = []
    source_counts = Counter(); lag = Counter(); lags = []; daily = Counter()
    for r in records:
        arrival = day(r.get('available_date')); event = day(r.get('event_date'))
        status = ('unknown_arrival_date' if arrival is None else 'after_cutoff' if arrival > asof else
                  'before_window' if arrival < start else 'included')
        ref = {'record_id': r['record_id'], 'record_hash': digest(r), 'status': status,
            'available_date': r.get('available_date'), 'event_date': r.get('event_date'),
            'repeat_of': None}
        refs[r['patient_id']].append(ref)
        if status != 'included': exclusions[status] += 1; continue
        parts = list(narrative_sources.text_fields(r))
        # Exact allowed received fields, no generator lineage or inferred text equivalence.
        text_hash = digest(parts) if parts else None
        ref['received_text_digest'] = text_hash
        included.append((arrival, r['record_id'], r['patient_id'], text_hash, ref))
        counts[arrival[:7]] += 1; daily[arrival] += 1
        if event is None: lag['unknown_source_date'] += 1
        else:
            delta = (date.fromisoformat(arrival)-date.fromisoformat(event)).days
            lags.append(delta)
            lag['source_after_arrival' if delta < 0 else 'same_day' if delta == 0 else 'arrival_after_source_date'] += 1
            if event < start: lag['source_before_window'] += 1
            elif event > asof: lag['source_after_cutoff'] += 1
            else: source_counts[event[:7]] += 1
    unique = Counter(); repeats = Counter(); no_text = 0; seen = {}
    for arrival, rid, pid, thash, ref in sorted(included):
        key = (pid, thash)
        if thash and key in seen:
            ref['repeat_of'] = seen[key]; repeats[arrival[:7]] += 1
        else:
            unique[arrival[:7]] += 1
            if thash: seen[key] = rid
        no_text += thash is None
    members = [{'patient_id': p, 'records': refs[p], 'included_records': sum(r['status']=='included' for r in refs[p]),
        'clinical_state': 'unassessed'} for p in sorted(selected)]
    lags.sort()
    median = (lags[len(lags)//2] if len(lags)%2 else (lags[len(lags)//2-1]+lags[len(lags)//2])/2) if lags else None
    return {'months': labels, 'calendar_days': exposure, 'window_start': start, 'window_end': asof,
        'denominators': {'selected_members': len(selected), 'stream_records': len(records),
            'included_records': len(included), 'members_with_included_records': sum(m['included_records']>0 for m in members),
            'members_without_included_records': sum(m['included_records']==0 for m in members),
            'excluded_records_by_reason': dict(exclusions), 'calendar_days': sum(exposure),
            'verified_person_time': None, 'clinical_capture_complete': None},
        'arrival_comparison': compare([counts[m] for m in labels], exposure),
        'repeat_sensitivity': {**compare([unique[m] for m in labels], exposure),
            'repeat_counts': [repeats[m] for m in labels], 'records_without_received_text': no_text,
            'definition': 'Within this window, retain earliest arrival/id per patient with identical allowed received text fields; textless records stay. Identical text may describe distinct care. No record is removed from evidence.'},
        'source_date_sensitivity': {'counts': [source_counts[m] for m in labels], 'clock_counts': dict(lag),
            'lag_days_median': median, 'lag_days_min': min(lags) if lags else None,
            'lag_days_max': max(lags) if lags else None,
            'definition': 'Source dates of the same included arrivals only; not a complete clinical event series.'},
        'arrival_day_counts': dict(sorted(daily.items())), 'members': members}


def run(retriever, request):
    row, records, tasks, binding = inputs(retriever, request)
    result = analyze(records, row['selection']['patient_ids'], row['decision_date'], request['months'])
    for member in result['members']:
        member['task_ids'] = [t['task']['id'] for t in tasks if t['task']['patient_id'] == member['patient_id']]
    findings = [{'kind': 'received_process_partition', 'status': 'descriptive_hypothesis',
        'start_month': result['months'][b['start_index']],
        'end_month': result['months'][b['end_index_exclusive']-1], **b}
        for b in result['arrival_comparison']['candidate']['blocks']]
    report = {'schema': VERSION, 'decision_id': row['id'], 'request': request, 'binding': binding,
        'question': row['question'], 'selection': row['selection'], 'buyer_id': row['buyer_id'], 'work': tasks,
        'followup_question': f"How does received {request['stream']} volume vary across {request['months']} months ending {row['decision_date']}, and could copying or arrival timing explain it?",
        **result, 'findings': findings, 'clinical_change_established': False, 'causal_effect': None,
        'search_scope': {'streams_this_result': 1, 'windows_this_result': 1, 'partition_candidates': 2**(request['months']-1),
            'penalty': 6.0, 'p_value': None, 'false_discovery_rate': None, 'qualification': 'exploratory',
            'warning': 'Partition selection is data dependent. Additional queries increase the search family. No multiplicity-adjusted inference, replication or clinical alert threshold is established.'},
        'alternatives': [
            {'hypothesis': 'Batching or delayed source transmission', 'observable_check': 'Compare arrival-day counts, source-date counts and dated lag distribution.', 'needed_to_distinguish': 'Source feed receipt and outage logs; not supplied.'},
            {'hypothesis': 'Repeated documentation', 'observable_check': 'Inspect repeat_of pairs and compare exact-text sensitivity partitions.', 'needed_to_distinguish': 'Received amendment/copy provenance and document review; identical text alone is not duplicate care.'},
            {'hypothesis': 'Changed patient activity or changed capture', 'observable_check': 'Inspect exact member records, absent-source members and unknown dates.', 'needed_to_distinguish': 'Verified source coverage and independent clinical assessment; no-record months do not establish no care.'}],
        'review_burden': {'source_records': result['denominators']['included_records'], 'human_review_minutes': None},
        'cost': {'new_paid_api_calls': 0, 'api_cost_usd': 0, 'local_compute_cost_usd': None, 'total_cost_usd': None},
        'route': 'local received-source arithmetic and existing Poisson segmentation; no model needed',
        'clinical_execution_authorized': False}
    if inputs(retriever, request)[-1] != binding:
        raise q.care.work.WorkConflict('Sources or work changed during discovery; no result saved')
    return q.save_followup(retriever.path, report)


def reopen(retriever, request):
    q.care.exact(request, 'decision_id result_id patient_id')
    report = q.saved_followup(retriever.path, request['result_id'])
    if report.get('schema') != VERSION or report['decision_id'] != request['decision_id']:
        raise ValueError('Discovery result belongs to another decision or method')
    pid = request['patient_id']
    if pid and pid not in report['selection']['patient_ids']: raise ValueError('Member not selected in this population')
    evidence = None
    try:
        _, records, _, binding = inputs(retriever, report['request'])
        if binding != report['binding']: raise ValueError('Received sources, method or owned work changed')
        if pid:
            member = next(m for m in report['members'] if m['patient_id']==pid)
            evidence = {'member': member, 'records': [
                {'record_id': r['record_id'], 'record_hash': digest(r), 'event_date': r.get('event_date'),
                 'available_date': r.get('available_date'), 'received_fields': list(narrative_sources.text_fields(r))}
                for r in records if r['patient_id']==pid],
                'work': [t for t in report['work'] if t['task']['id'] in member['task_ids']]}
        current = True; reason = None
    except (ValueError, KeyError, FileNotFoundError) as exc:
        current = False; reason = str(exc)
    return {'report': report, 'current': current, 'stale_reason': reason, 'member_evidence': evidence, 'new_paid_api_calls': 0}
