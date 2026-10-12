"""Append-only documentary decisions over exact question/requirement dependencies.

Resolving source availability never resolves clinical eligibility. Machine reports
cannot satisfy a review requirement. Existing semantic reviews are reused, not
created or promoted here. Corrections, conflicts and source clocks remain visible.
"""
from pathlib import Path
import hashlib
import json
import sqlite3
from collections import Counter

from ...contracts import digest
from .. import narrative_sources
from . import care_capabilities as care, context_builder, evidence_needs, record_context, source_synthesis
from . import semantic_reader, interpretation_state, document_clause_bindings

VERSION = 'documentary-dispositions/1'


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), semantic_reader.identity(), document_clause_bindings.identity()])


def database(path):
    db = sqlite3.connect(Path(path)/'evidence-dispositions.sqlite3', timeout=30)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS decisions(target TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(target,revision));
      CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,hash TEXT,payload TEXT);
      CREATE TRIGGER IF NOT EXISTS immutable_decision_update BEFORE UPDATE ON decisions BEGIN SELECT RAISE(ABORT,'Decisions are append-only'); END;
      CREATE TRIGGER IF NOT EXISTS immutable_decision_delete BEFORE DELETE ON decisions BEGIN SELECT RAISE(ABORT,'Decisions are append-only'); END;
    ''')
    return db


def resolve_target(path, target):
    if not isinstance(target, dict): raise ValueError('Typed target required')
    if target.get('kind') == 'service_requirement':
        from .care_readiness import resolve_requirement
        return resolve_requirement(path, target)
    care.exact(target, 'kind receipt_id need_id')
    if target['kind'] != 'evidence_need': raise ValueError('Known documentary target kind required')
    r = evidence_needs.inspect(path, target['receipt_id'])
    need = next((n for n in r['needs'] if n['id'] == target['need_id']), None)
    if need is None: raise ValueError('Exact original question required')
    return {'target': target, 'id': digest(target), 'parent_hash': digest(r), 'patient_id': r['patient_id'],
            'question': need['question'], 'source_selectors': need['selectors'],
            'as_of': r['snapshot']['as_of'], 'authority': 'original_machine_question_not_clinical_requirement'}


def source_record(path, patient, key):
    records, meta = record_context.received(path, patient)
    record = next((r for r in records if r['record_id'] == key), None)
    if record is None: raise ValueError('Record not received for this patient')
    return record, meta


def field_values(record):
    # Use the same received-field and hidden-truth boundary as context construction.
    clean = context_builder.clean(record)
    allowed = set(record.get('_warehouse_fields', [])) | narrative_sources.METADATA
    values = {k: v for k, v in clean.items() if k in allowed and k not in narrative_sources.FORBIDDEN
              and (v is None or type(v) in (str, bool, int, float))}
    if record['source_table'] == 'documents':
        for k in narrative_sources.COMMON: values.pop(k, None)
    values.update({p['field']: p['text'] for p in narrative_sources.text_fields(record)})
    return values


def source_preview(path, request):
    care.exact(request, 'target record_id')
    t = resolve_target(path, request['target']); key = care.text(request['record_id'], 'record ID', 180)
    record, _ = source_record(path, t['patient_id'], key)
    fields = []
    for name, value in sorted(field_values(record).items()):
        size = len(json.dumps(value, ensure_ascii=False).encode())
        fields.append({'field': name, 'value': value if size <= 6000 else None,
                       'value_hash': digest(value), 'omitted_for_display': size > 6000})
    return {'patient_id': t['patient_id'], 'record_id': key, 'record_hash': digest(record),
            'event_date': record.get('event_date'), 'available_date': record.get('available_date'),
            'fields': fields, 'decision_date': t['as_of'], 'meaning_verified': False}


def semantic_group(path, reading):
    db = semantic_reader.db(path)
    try:
        rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM readings')]
    finally: db.close()
    siblings = []
    for candidate in rows:
        if candidate['target'] == reading['target']:
            try: siblings.append(semantic_reader.inspect(path, candidate['id']))
            except ValueError: continue  # stale interpretations cannot certify the current source
    if not any(r['id'] == reading['id'] for r in siblings): siblings.append(reading)
    return interpretation_state.review_groups(siblings)[0]


def binding(path, t, b):
    if not isinstance(b, dict): raise ValueError('Typed evidence binding required')
    kind = b.get('kind'); reference = None; review = None
    if kind == 'program_clause': return document_clause_bindings.bind(path, t, b)
    if kind in ('field', 'span'):
        care.exact(b, 'kind record_id record_hash field value_hash' if kind == 'field' else 'kind record_id record_hash field start end quote')
        record, _ = source_record(path, t['patient_id'], care.text(b['record_id'], 'record ID', 180))
        if digest(record) != b['record_hash']: raise care.work.WorkConflict('Source record changed since binding preview')
        value = field_values(record).get(b['field'], _MISSING)
        if value is _MISSING: raise ValueError('Field is not an allowed received value')
        if kind == 'field':
            if digest(value) != b['value_hash']: raise care.work.WorkConflict('Source field changed')
            if len(json.dumps(value).encode()) > 6000: raise ValueError('Field exceeds binding bound; use an exact text span')
            reference = {'field': b['field'], 'value': value, 'value_hash': digest(value)}
        else:
            if not isinstance(value, str) or type(b['start']) is not int or type(b['end']) is not int or not 0 <= b['start'] < b['end'] <= len(value):
                raise ValueError('Exact received-text offsets required')
            if not isinstance(b['quote'], str) or not 0 < len(b['quote']) <= 3000 or value[b['start']:b['end']] != b['quote']:
                raise ValueError('Quote does not match exact received text')
            reference = {k: b[k] for k in ('field', 'start', 'end', 'quote')}
    elif kind == 'source_unit':
        care.exact(b, 'kind context_id unit_id')
        context = source_synthesis.context(path, b['context_id'])
        if context['patient_id'] != t['patient_id']: raise ValueError('Source context belongs to another patient')
        unit = next((u for u in context['model_context']['evidence'] if u['id'] == b['unit_id']), None)
        if unit is None: raise ValueError('Stable source unit ID required; pass-local aliases are not accepted')
        record, _ = source_record(path, t['patient_id'], unit['source_ref'])
        reference = {'context_id': b['context_id'], 'context_hash': digest(context), 'source_unit': unit}
    elif kind == 'semantic_review':
        care.exact(b, 'kind reading_id review_revision')
        reading = semantic_reader.inspect(path, b['reading_id'])
        if reading['target']['patient_id'] != t['patient_id']: raise ValueError('Reviewed source belongs to another patient')
        if type(b['review_revision']) is not int or reading['review_revision'] != b['review_revision']:
            raise care.work.WorkConflict('Semantic review revision changed')
        group = semantic_group(path, reading)
        if group['status'] != 'reviewed_source' or reading['id'] not in group['accepted_reading_ids']:
            raise ValueError('Consistent accepted or corrected source review required; proposals and conflicts cannot qualify')
        record, _ = source_record(path, t['patient_id'], reading['target']['record_id'])
        if digest(record) != reading['source_hash']: raise care.work.WorkConflict('Semantic review source changed')
        review = group
        reference = {'reading_id': reading['id'], 'target': reading['target'], 'source_hash': reading['source_hash'],
                     'review_revision': reading['review_revision'], 'review': reading['reviews'][-1], 'review_group': group}
    else: raise ValueError('Field, span, stable source unit, existing semantic review or imported program clause required')
    available = record.get('available_date'); event = record.get('event_date')
    temporal = 'available_by_decision' if available and available <= t['as_of'] else 'future_or_unknown_arrival'
    return {'selector': b, 'record_id': record['record_id'], 'record_hash': digest(record),
            'patient_id': t['patient_id'], 'source_table': record['source_table'], 'reference': reference,
            'clocks': {'available_date': available, 'event_date': event, 'decision_date': t['as_of'],
                       'availability': temporal, 'clinical_currentness': 'not_established'},
            'semantic_review_attached': review is not None, 'clinical_truth_established': False}


_MISSING = object()


def history(path, key):
    db = database(path)
    try: return [json.loads(r[0]) for r in db.execute('SELECT payload FROM decisions WHERE target=? ORDER BY revision', (key,))]
    finally: db.close()


def inspect(path, target, *, resolved=None):
    reasons = []
    try: t = resolved or resolve_target(path, target)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        rows = history(path, digest(target))
        if not rows: raise
        t = rows[-1]['target_snapshot']
        reasons.append('Original target is no longer current: ' + str(exc))
    rows = history(path, t['id']); latest = rows[-1] if rows else None
    state = 'unreviewed'
    if latest:
        if latest['target_snapshot'] != t or latest['implementation'] != identity(): reasons.append('Target or interpretation contract changed')
        try:
            dependencies = [binding(path, t, b) for b in latest['bindings']]
            if dependencies != latest['dependencies']: reasons.append('Bound evidence or semantic review group changed')
        except (ValueError, KeyError, FileNotFoundError) as exc: reasons.append(str(exc))
        state = ('stale' if reasons else 'machine_observation' if latest['actor_kind'] != 'operator' else latest['disposition'])
    return {'schema': VERSION, 'target': t, 'revision': latest['revision'] if latest else 0, 'state': state,
            'latest': latest, 'history': rows, 'stale_reasons': reasons,
            'documentary_question_addressed': state == 'addressed', 'clinical_eligibility_established': False,
            'clinical_execution_authorized': False, 'reviewer_identity_verified': False}


def save(path, request):
    care.exact(request, 'target expected_revision request_id actor actor_kind disposition rationale bindings')
    t = resolve_target(path, request['target']); actor = care.text(request['actor'], 'actor', 120)
    rationale = care.text(request['rationale'], 'decision rationale', 2000); rid = care.text(request['request_id'], 'request ID', 128)
    if request['actor_kind'] not in ('operator', 'machine', 'synthetic_test'): raise ValueError('Explicit actor kind required')
    if request['disposition'] not in ('addressed', 'unresolved', 'invalid_binding'): raise ValueError('Known documentary disposition required')
    if request['actor_kind'] != 'operator' and request['disposition'] == 'addressed':
        raise ValueError('Machine or test observations cannot mark a documentary question addressed')
    if type(request['expected_revision']) is not int or request['expected_revision'] < 0: raise ValueError('Explicit nonnegative review revision required')
    if not isinstance(request['bindings'], list) or len(request['bindings']) > 12: raise ValueError('At most 12 bounded source bindings required')
    if len({digest(b) for b in request['bindings']}) != len(request['bindings']): raise ValueError('Duplicate evidence bindings')
    deps = [binding(path, t, b) for b in request['bindings']]
    if request['disposition'] == 'addressed' and (not deps or any(d['clocks']['availability'] != 'available_by_decision' for d in deps)):
        raise ValueError('Addressed requires received evidence available by the decision date')
    if request['disposition'] == 'addressed' and any(d.get('documentary_mapping_usable') is False for d in deps):
        raise ValueError('Unreviewed, disputed or ineffective program clauses cannot mark a question addressed')
    db = database(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT hash,payload FROM requests WHERE id=?', (rid,)).fetchone()
        if old:
            if old[0] != digest(request): raise care.work.WorkConflict('Decision request ID reused for different content')
            db.commit(); return {**json.loads(old[1]), 'idempotent_replay': True}
        revision = db.execute('SELECT COALESCE(MAX(revision),0) FROM decisions WHERE target=?', (t['id'],)).fetchone()[0]
        if revision != request['expected_revision']: raise care.work.WorkConflict('Documentary review changed; reload')
        if resolve_target(path, request['target']) != t or [binding(path, t, b) for b in request['bindings']] != deps:
            raise care.work.WorkConflict('Target or evidence changed while recording decision')
        row = {**request, 'actor': actor, 'rationale': rationale, 'revision': revision + 1, 'target_snapshot': t,
               'dependencies': deps, 'implementation': identity(), 'recorded_at': care.work.now(),
               'reviewer_identity_verified': False, 'training_eligible': False, 'clinical_execution_authorized': False,
               'authority': 'Self-reported documentary disposition; no clinical truth, benefit verification or care completion inferred'}
        db.execute('INSERT INTO decisions VALUES (?,?,?)', (t['id'], row['revision'], json.dumps(row, allow_nan=False)))
        db.execute('INSERT INTO requests VALUES (?,?,?)', (rid, digest(request), json.dumps(row, allow_nan=False)))
        db.commit(); return {**row, 'idempotent_replay': False}
    finally: db.close()


def answer_view(path, receipt_id):
    r = evidence_needs.inspect(path, receipt_id)
    targets = [{'kind': 'evidence_need', 'receipt_id': receipt_id, 'need_id': n['id']} for n in r['needs']]
    reviews = [inspect(path, t) for t in targets]
    return {'receipt_id': receipt_id, 'patient_id': r['patient_id'], 'reviews': reviews,
            'counts': dict(Counter(r['state'] for r in reviews)), 'clinical_execution_authorized': False}


def acquisition_context(path, request):
    care.exact(request, 'target record_ids')
    t = resolve_target(path, request['target'])
    ids = care.strings(request['record_ids'], 'record IDs', 12, empty=False)
    records, meta = record_context.received(path, t['patient_id'])
    for key in ids:
        record = next((r for r in records if r['record_id'] == key), None)
        if not record or not record.get('available_date') or record['available_date'] > t['as_of']:
            raise ValueError('Evidence acquisition must use this patient and decision cutoff')
    from .question_context import select_sources
    # Restrict the closure to sources available by the requested decision date.
    eligible = [r for r in records if r.get('available_date') and r['available_date'] <= t['as_of']]
    selection = select_sources(eligible, ids, t['as_of'])
    ctx = record_context.create(path, t['patient_id'], t['question'], selection['connected_record_ids'])
    return {'target': t, 'context_id': ctx['id'], 'record_scope': ctx['record_scope'], 'selection': selection,
            'decision_cutoff': t['as_of'], 'source_snapshot_cutoff': meta['snapshot']['as_of'],
            'meaning_resolved': False, 'new_paid_api_calls': 0, 'requests_sent': 0}
