"""Bridge /ask answers to the existing deliverable runtime and action backbone.

Immutable answer snapshots keep cohort membership and evidence together. Review
work uses the backbone's revisioned work store. Explicit demo steps use the same
store with separate simulation receipts. No real outreach is performed.
Frontier composition receives the frozen bundle, not a new cohort.
"""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

from .receipts import Bundle, fingerprint
from ..plans import backbone

PROGRAMS = {
    'pharmd': {'label': 'PharmD program review', 'requirements': ['Reconcile current therapy, doses and documented exceptions', 'Confirm clinical suitability and prescribing oversight', 'Confirm patient preference, consent, service availability and coverage']},
    'rpm': {'label': 'Remote monitoring review', 'requirements': ['Confirm monitoring need and appropriate care-team response', 'Confirm patient preference, consent and ability to use equipment', 'Confirm service availability, coverage and responsible team']},
    'custom': {'label': 'Custom follow-up review', 'requirements': ['Define the intended action and evidence needed', 'Confirm suitability, patient preferences and responsible team']},
}
STATES = ('needs_review', 'in_review', 'blocked', 'review_complete', 'cancelled')
TRANSITIONS = {
    'needs_review': {'needs_review', 'in_review', 'blocked', 'cancelled'},
    'in_review': {'in_review', 'needs_review', 'blocked', 'review_complete', 'cancelled'},
    'blocked': {'blocked', 'needs_review', 'in_review', 'cancelled'},
    'review_complete': {'review_complete', 'in_review'},
    'cancelled': {'cancelled', 'needs_review'},
}


def now():
    return datetime.now(timezone.utc).isoformat()


def root(path):
    return Path(path) / 'answer-workspaces'


def source_identity(path):
    semantic = Path(path) / 'semantic.sqlite3'
    if not semantic.is_file():
        raise ValueError('The active semantic layer is unavailable')
    db = sqlite3.connect(f'file:{semantic}?mode=ro', uri=True)
    try:
        meta = dict(db.execute('SELECT key,value FROM meta'))
    finally:
        db.close()
    st = semantic.stat()
    return {'source_digest': meta.get('source_digest'), 'as_of': meta.get('as_of'),
            'semantic_version': meta.get('version'), 'size': st.st_size, 'mtime_ns': st.st_mtime_ns}


def patient_rows(answer):
    rows = {}
    for row in answer.get('table') or []:
        if not isinstance(row, dict):
            continue
        pid = row.get('patient_id') or row.get('patient')
        if isinstance(pid, str) and pid:
            rows.setdefault(pid, {'patient_id': pid, 'evidence_rows': []})['evidence_rows'].append(row)
    for pid in answer.get('patient_ids') or []:
        if isinstance(pid, str) and pid:
            rows.setdefault(pid, {'patient_id': pid, 'evidence_rows': []})
    return list(rows.values())


def capture(path, question, result, *, original_question=None):
    """Persist a server-produced result; no client-provided IDs enter this boundary."""
    answer = result.get('answer') or {}
    if not answer or answer.get('status') not in ('answered', 'partial', 'low_confidence'):
        return None
    source = source_identity(path)
    produced = result.get('execution_snapshot')
    contract = (result.get('planner_receipt') or {}).get('request_contract')
    if produced and produced != source:
        raise ValueError('Warehouse changed between execution and capture; ask again against the new snapshot')
    if contract:
        identity = contract['source_identity']
        if (identity.get('source_digest') != source['source_digest'] or identity.get('as_of') != source['as_of']
                or identity.get('version') != source['semantic_version']):
            raise ValueError('Executed source identity differs from the current warehouse; no context saved')
    source = produced or source
    rows = patient_rows(answer)
    inherited = result.get('cohort_patient_rows')
    if inherited is not None:
        if {r['patient_id'] for r in inherited} != set(answer.get('patient_ids') or []):
            raise ValueError('Transformed patient evidence does not match the saved cohort')
        rows = inherited
    data = {'schema': 'agere-answer-context/1', 'question': question,
            'original_question': original_question or question, 'source': source,
            'answer': answer, 'graph': result.get('graph'), 'results': result.get('results'),
            'planner_receipt': result.get('planner_receipt'), 'planning_economics': result.get('planning_economics'),
            'cohort_transform': result.get('cohort_transform'),
            'workflow': result.get('workflow', []),
            'patient_rows': rows, 'patient_ids': [r['patient_id'] for r in rows],
            'synthetic': True, 'clinical_eligibility_established': False}
    # Identity describes the executed answer, not punctuation, elapsed time,
    # cache state or cost. Keep the first complete execution receipt immutable.
    graph = data.get('graph') or {}
    plan = [{k: n.get(k) for k in ('id', 'operation', 'inputs', 'parameters')} for n in graph.get('nodes') or []]
    key = 'ANS-' + fingerprint({'version': 3, 'source': source, 'answer': answer, 'plan': plan,
                               'workflow': data['workflow'],
                               'answer_policy_version': (result.get('planner_receipt') or {}).get('version'),
                               'answer_policy_identity': (result.get('planner_receipt') or {}).get('policy_identity'),
                               'cohort_transform': data.get('cohort_transform')})[:24]
    d = root(path) / key
    d.mkdir(parents=True, exist_ok=True)
    target = d / 'answer.json'
    if not target.exists():
        # Exclusive creation keeps a shared question context immutable.
        try:
            with target.open('x') as f:
                json.dump({**data, 'id': key, 'created_at': now()}, f, default=str)
        except FileExistsError:
            pass
    return {'id': key, 'patients': len(rows), 'url': '/action-center?context=' + key}


def load(path, key, *, current=False):
    if not isinstance(key, str) or not re.fullmatch(r'ANS-[0-9a-f]{24}', key):
        raise ValueError('A saved answer context is required')
    f = root(path) / key / 'answer.json'
    if not f.is_file():
        raise ValueError('Saved answer not found in this warehouse')
    data = json.loads(f.read_text())
    stale = data['source'] != source_identity(path)
    invalidated = ((data.get('planner_receipt') or {}).get('version') == 'patient-therapy-review/1')
    from ..clinical.cohort_families import scope_rejection
    receipt = data.get('planner_receipt') or {}
    if receipt.get('route') in ('cohort_family', 'cohort_family_bottled') and scope_rejection(data['question']):
        invalidated = True
    if current and invalidated:
        raise ValueError('This saved answer was invalidated after incorrect cohort or constraint handling was found. It remains available for inspection; new work is blocked.')
    if current and stale:
        raise ValueError('The data changed. Ask the question again before creating or changing work.')
    return {**data, 'stale': stale, 'invalidated': invalidated}


def listing(path):
    out = []
    for f in sorted(root(path).glob('ANS-*/answer.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        d = json.loads(f.read_text())
        out.append({'id': d['id'], 'question': d['question'], 'patients': len(d['patient_ids']), 'created_at': d['created_at']})
    return out[:100]


def workspace(path, context):
    return SimpleNamespace(dir=root(path) / context['id'], slug=context['id'],
                           semantic_path=Path(path) / 'semantic.sqlite3', state={'as_of': context['source']['as_of']})


def _db(path, context):
    return backbone.database(workspace(path, context).dir)


def work(path, context):
    db = _db(path, context)
    try:
        return [json.loads(r[0]) for r in db.execute('SELECT payload FROM work_items ORDER BY program,patient')]
    finally:
        db.close()


def inspect(path, key):
    context = load(path, key)
    items = work(path, context)
    from .workflow import read
    return {'context': context, 'workflow': read(path, key), 'items': items, 'counts': dict(Counter(i['state'] for i in items)),
            'programs': PROGRAMS, 'states': STATES, 'external_dispatch_performed': False}


def create_work(path, request):
    if set(request) != {'context_id', 'program', 'patient_ids', 'owner', 'note'}:
        raise ValueError('Exact answer, program, selected patients, owner and note are required')
    c = load(path, request['context_id'], current=True)
    if c['answer'].get('status') != 'answered' or c['answer'].get('missing'):
        raise ValueError('Resolve the incomplete answer before preparing program work')
    if (c['answer'].get('actionable_cohort') is False or
            any(n.get('operation') == 'governed_metric' for n in (c.get('graph') or {}).get('nodes', []))):
        raise ValueError('This analytical summary does not select an action population; ask for the intended patient cohort.')
    program = request['program']
    if program not in PROGRAMS:
        raise ValueError('Unknown review program')
    ids = request['patient_ids']
    if not isinstance(ids, list) or not ids or any(not isinstance(p, str) for p in ids) or len(set(ids)) != len(ids):
        raise ValueError('Select distinct patients from this answer')
    if not set(ids) <= set(c['patient_ids']):
        raise ValueError('Selected patients are outside the saved answer')
    # Membership in a model/table response must still resolve to the current roster.
    db = sqlite3.connect(f'file:{Path(path) / "semantic.sqlite3"}?mode=ro', uri=True)
    try:
        known = {r[0] for r in db.execute('SELECT patient_id FROM patient')}
    finally:
        db.close()
    if not set(ids) <= known:
        raise ValueError('The answer contains a patient absent from the source roster')
    owner = str(request['owner']).strip()
    note = str(request['note']).strip()
    if not owner or len(owner) > 120 or len(note) > 2000:
        raise ValueError('Provide an owner (up to 120 characters) and a note up to 2000 characters')
    lane = 'answer_review:' + program
    db = _db(path, c)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            decision_id = fingerprint([c['id'], program])
            decision = {'id': decision_id, 'question': c['question'], 'context_id': c['id'], 'program': program,
                        'patients': c['patient_ids'], 'clinical_eligibility_established': False}
            db.execute('INSERT OR IGNORE INTO question_decisions VALUES (?,?)', (decision_id, json.dumps(decision)))
            for pid in ids:
                key = 'WI-' + fingerprint([c['id'], program, pid])[:16]
                assessment = {'patient_id': pid, 'context_id': c['id'], 'program': program, 'source': c['source'],
                              'requirements': PROGRAMS[program]['requirements'], 'clinical_eligibility_established': False}
                aid = fingerprint(assessment)
                db.execute('INSERT OR IGNORE INTO work_assessments VALUES (?,?,?,?)', (aid, pid, lane, json.dumps(assessment)))
                existing = db.execute('SELECT payload FROM work_items WHERE id=?', (key,)).fetchone()
                if existing and json.loads(existing[0])['owner'] != owner:
                    raise ValueError('Review work already has a different owner. Open the existing item to change ownership explicitly; no selected work was changed.')
                if existing:
                    continue  # Idempotent creation never resets progress or changes the assigned owner.
                item = {'id': key, 'patient_id': pid, 'context_id': c['id'], 'program': program, 'lane': lane,
                        'decision_id': decision_id, 'assessment_id': aid, 'revision': 1, 'state': 'needs_review',
                        'owner': owner, 'note': note, 'requirements': assessment['requirements'], 'updated_at': now(),
                        'synthetic': True, 'external_dispatch_performed': False}
                db.execute('INSERT INTO work_items VALUES (?,?,?,?,?)', (key, pid, lane, 1, json.dumps(item)))
                db.execute('INSERT INTO question_decision_tasks VALUES (?,?,?)', (decision_id, key, json.dumps({'patient_id': pid})))
                db.execute('INSERT INTO work_events VALUES (?,?,?)', (key, 1, json.dumps({'action': 'review_created', 'item': item})))
    finally:
        db.close()
    return inspect(path, c['id'])


def update_work(path, request):
    if set(request) != {'context_id', 'item_id', 'revision', 'state', 'owner', 'note'}:
        raise ValueError('Exact work item, revision, state, owner and note are required')
    c = load(path, request['context_id'], current=True)
    if request['state'] not in STATES:
        raise ValueError('Only internal review states are supported; no enrollment or care execution')
    owner, note = str(request['owner']).strip(), str(request['note']).strip()
    if not owner or len(owner) > 120 or len(note) > 2000 or type(request['revision']) is not int:
        raise ValueError('Valid owner, note and revision are required')
    db = _db(path, c)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,payload FROM work_items WHERE id=?', (request['item_id'],)).fetchone()
            if not row:
                raise ValueError('Work item is absent from this answer context')
            if row[0] != request['revision']:
                raise ValueError('Work changed in another window. Reload before saving.')
            item = json.loads(row[1])
            if request['state'] not in TRANSITIONS[item['state']]:
                raise ValueError(f"Review cannot move from {item['state']} to {request['state']}; start or reopen the review first.")
            item.update(state=request['state'], owner=owner, note=note, revision=row[0] + 1, updated_at=now())
            db.execute('UPDATE work_items SET revision=?,payload=? WHERE id=?', (item['revision'], json.dumps(item), item['id']))
            db.execute('INSERT INTO work_events VALUES (?,?,?)', (item['id'], item['revision'], json.dumps({'action': 'review_updated', 'item': item})))
    finally:
        db.close()
    return inspect(path, c['id'])


def followup(path, key, question):
    """Deterministic transformations of any saved patient set, never a new search.

    Unsupported edits return None. The caller must not rewrite an anaphoric
    request into an unbound whole-warehouse question.
    """
    text = ' '.join(question.lower().strip(' ?.!').split())
    listing = re.fullmatch(r'(?:list|show|count)(?: me)? (?:them|those patients|these patients)', text)
    grouping = re.fullmatch(r'(?:group|break down|split)(?: them| those patients| these patients)? by (pcp|practice|payer)', text)
    exclusion = re.fullmatch(r'(?:exclude|excluding|remove)(?: the)? (?:patients|ones|those|them)(?: who have| who| that have)? (?:died|are deceased|deceased)', text)
    if not (listing or grouping or exclusion):
        return None
    c = load(path, key, current=True)
    ids = set(c['patient_ids'])
    rows = c['patient_rows']
    operations, evidence = [], []
    db = sqlite3.connect(f'file:{Path(path) / "semantic.sqlite3"}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        table = [r for p in rows for r in p['evidence_rows']] or [{'patient_id': p} for p in sorted(ids)]
        if exclusion:
            query = 'SELECT patient_id,death_date,record_ids FROM death WHERE death_date<=?'
            deaths = [dict(r) for r in db.execute(query, (c['source']['as_of'],)) if r['patient_id'] in ids]
            ids -= {r['patient_id'] for r in deaths}
            table = [r for r in table if (r.get('patient_id') or r.get('patient')) in ids]
            operations.append({'operation': 'exclude_recorded_deaths', 'as_of': c['source']['as_of']})
            evidence.append({'query': query, 'parameters': [c['source']['as_of']], 'rows': deaths,
                             'limitation': 'No death record does not confirm that a patient is alive.'})
        if grouping:
            field = {'pcp': 'pcp_clinician_id', 'practice': 'pcp_practice_id', 'payer': 'payer_id'}[grouping.group(1)]
            query = f'SELECT patient_id,{field} FROM patient'
            people = {r['patient_id']: r[field] for r in db.execute(query) if r['patient_id'] in ids}
            counts = Counter(people.get(pid) or 'Unknown' for pid in ids)
            table = [{grouping.group(1): k, 'patients': v} for k, v in sorted(counts.items())]
            operations.append({'operation': 'group_saved_cohort', 'field': field})
            evidence.append({'query': query, 'restricted_to_context': key,
                             'definition': f'Observed patient.{field}; no inference of ongoing care or alternate attribution.'})
        if listing:
            operations.append({'operation': 'count_saved_cohort' if text.startswith('count') else 'list_saved_cohort'})
    finally:
        db.close()
    parent = c['answer']
    status = parent['status']
    count_only = bool(listing and text.startswith('count'))
    answer = {'status': status, 'answer_type': 'count' if count_only else 'table', 'count': len(ids),
              'headline': f'{len(ids)} patients from the saved cohort' + (f', grouped by {grouping.group(1)}.' if grouping else '.'),
              'table': [] if count_only else table, 'patient_ids': sorted(ids),
              'missing': parent.get('missing') or [], 'limitations': [*(parent.get('limitations') or []),
                  'This transformation preserves the original answer limitations and does not re-establish cohort correctness.'],
              'source_checks': evidence}
    return {'question': question, 'answer': answer, 'route': 'saved_cohort',
            'cohort_patient_rows': [r for r in rows if r['patient_id'] in ids],
            'planner_receipt': {'route': 'saved_cohort', 'new_paid_api_calls': 0},
            'cohort_transform': {'parent_context_id': key, 'input_patient_ids': sorted(c['patient_ids']), 'operations': operations},
            'new_paid_api_calls': 0, 'planning_economics': {'known_api_estimate_usd': 0, 'new_api_estimate_usd': 0}}


def evidence_bundle(path, key):
    c = load(path, key, current=True)
    b = Bundle({'workspace': key, 'as_of': c['source']['as_of'], 'synthetic': True,
                'scope': 'Exact saved answer and internal review work only. No new population, eligibility or clinical outcome may be inferred.'})
    b.add('saved_answer', {'context_id': key}, c, definition=c['question'],
          as_of=c['source']['as_of'], data_fp=fingerprint(c['source']), source='server-saved /ask execution')
    items = work(path, c)
    b.add('review_work', {'context_id': key}, {'items': items, 'counts': dict(Counter(i['state'] for i in items))},
          definition='Internal review states, not outreach, enrollment or clinical completion', data_fp=fingerprint(items))
    return c, b


def compose_frontier(path, request, *, allow_live=False, send=None):
    if set(request) != {'context_id', 'request'} or not isinstance(request['request'], str) or not 1 <= len(request['request'].strip()) <= 4000:
        raise ValueError('A saved answer and a deliverable request up to 4000 characters are required')
    if not allow_live:
        raise ValueError('Frontier composition is disabled on this server; the local command center remains available')
    from .compose import compose
    c, b = evidence_bundle(path, request['context_id'])
    # The original question, source, returned evidence and revisioned review
    # work are already in this bundle. A design prompt cannot fetch new facts.
    return compose(workspace(path, c), request['request'].strip(), evidence_bundle=b, tool_calls=[], send=send)
