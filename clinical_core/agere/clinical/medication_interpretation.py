"""Explicit medication question/source selection and immutable machine-answer return.

Answers supplement the original documentary review and its existing work. They
never revise a requirement, handoff, current regimen, consent or clinical authority.
"""
from pathlib import Path
import fcntl, hashlib, json, sqlite3
from ...contracts import digest
from . import medication_source_review as M, record_context as RC, source_synthesis as S
from . import evidence_needs as E, care_capabilities as C, care_readiness as R
from . import medication_work_view as old_view

VERSION = 'medication-interpretation/1'
LIMITS = {'original_questions_changed': False, 'current_regimen_established': False,
          'clinical_execution_authorized': False, 'human_review_established': False,
          'external_actions': 0, 'new_paid_api_calls': 0}


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), M.identity(), RC.identity()])


def database(path):
    db = sqlite3.connect(Path(path)/'medication-interpretation.sqlite3', timeout=30)
    for table in ('selections', 'answers'):
        db.execute(f'CREATE TABLE IF NOT EXISTS {table}(id TEXT PRIMARY KEY, review TEXT NOT NULL, payload TEXT NOT NULL)')
        for op in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'Medication interpretation history is immutable'); END")
    return db


def verified(row):
    if row.get('id') != digest({k: v for k, v in row.items() if k != 'id'}):
        raise ValueError('Medication interpretation receipt integrity changed')
    return row


def save(path, table, row):
    row = {**row, 'id': digest(row)}
    db = database(path)
    try:
        with db:
            db.execute(f'INSERT OR IGNORE INTO {table} VALUES (?,?,?)', (row['id'], row['review_id'], json.dumps(row, allow_nan=False)))
        prior = verified(json.loads(db.execute(f'SELECT payload FROM {table} WHERE id=?', (row['id'],)).fetchone()[0]))
        if prior != row:
            raise ValueError('Saved medication interpretation differs')
    finally:
        db.close()
    return row


def load(path, table, key):
    C.text(key, 'medication interpretation ID', 64)
    db = database(path)
    try:
        raw = db.execute(f'SELECT payload FROM {table} WHERE id=?', (key,)).fetchone()
    finally:
        db.close()
    if not raw:
        raise ValueError('Unknown medication interpretation receipt')
    return verified(json.loads(raw[0]))


def select(path, request):
    C.exact(request, 'review_id question_receipt_ids record_ids')
    review = M.inspect(path, request['review_id'])
    questions = C.strings(request['question_receipt_ids'], 'question receipt IDs', 3, empty=False)
    records = C.strings(request['record_ids'], 'record IDs', 24, empty=False)
    index = {q['receipt_id']: q for q in review['questions']}
    if set(questions)-set(index):
        raise ValueError('Question is not in this exact medication review')
    accounts = {a['record_id']: a for a in review['medication_accounts']+review['narrative_accounts'] if not a['field_path']}
    if set(records)-set(accounts):
        raise ValueError('Record is not in this exact medication review')
    selected = [index[k] for k in sorted(questions)]
    # Preserve full original contracts in the selection; compact exact task text
    # travels to the existing bounded interpreter. Overlong tasks fail, never clip.
    question = 'Interpret selected dated medication accounts for these original review questions: ' + ' '.join(
        q['title']+': '+q['purpose']+' Original documentary result: '+q['status']+'.' for q in selected)
    question += (' Distinguish lists, patient reports, orders, dispenses, copied history and individual administrations. '
                 'Do not infer current use, ingestion or longitudinal nonuse from dispensing or a single declined dose. '
                 'Selection excludes other received records; sufficiency and narrative omissions remain unassessed. '
                 'Identify discrepancies and evidence needed next. Do not resolve the original contracts or authorize treatment.')
    if len(question) > 1500:
        raise ValueError('Selected question text exceeds the bounded interpreter; select fewer questions')
    with (Path(path)/'medication-interpretation-context.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if M.inspect(path, review['id']) != review:
            raise ValueError('Medication review changed during selection')
        context = RC.create(path, review['patient_id'], question, sorted(records))
    unselected = sorted(set(accounts)-set(records))
    links = [x for x in review['source_links'] if x['source']['record_id'] in records and x['target_record_id'] not in records]
    row = {'schema': VERSION, 'request': {'review_id': review['id'], 'question_receipt_ids': sorted(questions), 'record_ids': sorted(records)},
           'review_id': review['id'], 'review_hash': digest(review), 'patient_id': review['patient_id'], 'implementation': identity(),
           'questions': selected, 'question': question, 'context_id': context['id'], 'context_hash': digest(context),
           'selected_records': [{k: accounts[r][k] for k in ('record_id', 'record_hash', 'source_table', 'source_date', 'available_date')} for r in sorted(records)],
           'unselected_review_record_ids': unselected,
           'unselected_narrative_record_ids': sorted({a['record_id'] for a in review['narrative_accounts']}-set(records)),
           'selected_source_links_outside_scope': links, 'source_link_issues': review['source_link_issues'],
           'record_scope': context['record_scope'], 'omissions': context['omissions'], 'dispatch': context['dispatch'],
           'status': 'source_context_prepared' if context['dispatch']['source_review_allowed'] and not context['omissions'] else 'selected_scope_incomplete', **LIMITS}
    if M.inspect(path, review['id']) != review or digest(RC.inspect(path, context['id'])) != row['context_hash']:
        raise ValueError('Medication source dependencies changed during selection')
    return save(path, 'selections', row)


def inspect_selection(path, key):
    row = load(path, 'selections', key)
    if row['implementation'] != identity() or digest(M.inspect(path, row['review_id'])) != row['review_hash']:
        raise ValueError('Medication selection review or implementation changed')
    if digest(RC.inspect(path, row['context_id'])) != row['context_hash']:
        raise ValueError('Medication selection context changed')
    return row


def answer_binding(path, request):
    C.exact(request, 'selection_id answer_id')
    selection = inspect_selection(path, request['selection_id'])
    RC.verified(path, selection['context_id'])
    answer = S.inspect(path, request['answer_id'])
    if answer.get('status') != 'complete' or (len(answer.get('passes', [])) > 1 and not answer.get('reconciliation', {}).get('groups')):
        raise ValueError('Complete saved interpretation and required reconciliation needed')
    if answer['patient_id'] != selection['patient_id'] or answer['context_id'] != selection['context_id']:
        raise ValueError('Interpretation must use this exact medication selection and patient')
    return selection, answer, {'selection_hash': digest(selection), 'answer_hash': digest(answer)}


def attach(path, request):
    selection, answer, binding = answer_binding(path, request)
    needs = E.prepare(path, answer['id'], [])
    row = {'schema': VERSION, 'request': request, 'review_id': selection['review_id'], 'patient_id': selection['patient_id'],
           'question_receipt_ids': selection['request']['question_receipt_ids'], 'binding': binding, 'implementation': identity(),
           'context_id': selection['context_id'], 'followup_needs_id': needs['id'], 'followup_needs_hash': digest(needs),
           'summary': {'findings': len(answer['findings']), 'exact_citations': sum(len(f['citations']) for f in answer['findings']),
                       'review_questions': len(answer['review_steps']), **needs['summary']},
           'status': 'machine_interpretation_attached_for_review', **LIMITS}
    if answer_binding(path, request)[2] != binding or digest(E.inspect(path, needs['id'])) != row['followup_needs_hash']:
        raise ValueError('Medication answer dependencies changed during attachment')
    return save(path, 'answers', row)


def inspect_answer(path, key):
    row = load(path, 'answers', key)
    if row['implementation'] != identity() or answer_binding(path, row['request'])[2] != row['binding']:
        raise ValueError('Medication answer dependencies changed')
    if digest(E.inspect(path, row['followup_needs_id'])) != row['followup_needs_hash']:
        raise ValueError('Medication follow-up needs changed')
    return row


def listing(path, request):
    C.exact(request, 'review_id')
    db = database(path)
    try:
        rows = {table: [verified(json.loads(r[0])) for r in db.execute(f'SELECT payload FROM {table} WHERE review=? ORDER BY rowid', (request['review_id'],))] for table in ('selections', 'answers')}
    finally:
        db.close()
    if not any(rows.values()):
        M.inspect(path, request['review_id'])
    for table, values in rows.items():
        for row in values:
            reasons = []
            try:
                (inspect_selection if table == 'selections' else inspect_answer)(path, row['id'])
            except (ValueError, KeyError, FileNotFoundError) as exc:
                reasons.append(str(exc))
            row.update(current=not reasons, stale_reasons=reasons)
    return {'schema': VERSION, **request, **rows, **LIMITS}


def task_review(path, task_id):
    task = R.task(path, task_id)
    plan = C.inspect_plan(path, task['plan_id'])
    origin = plan['origin']
    if origin['kind'] != M.KIND:
        raise ValueError('Task does not originate from a medication source review')
    review = M.inspect(path, origin['id'])
    if task['patient_id'] != review['patient_id'] or plan['patient_id'] != review['patient_id'] or digest(review) != origin['hash']:
        raise ValueError('Medication task and review identities differ')
    return task, review


def task_state(path, request):
    C.exact(request, 'task_id')
    task, review = task_review(path, request['task_id'])
    result = {**listing(path, {'review_id': review['id']}), 'task_id': task['id'], 'task_revision': task['revision'],
              'relationship': 'additive_review_interpretations_original_handoff_unchanged'}
    if R.task(path, task['id']) != task:
        raise ValueError('Medication task changed while loading interpretations')
    return result


def task_availability(path, task_id):
    result = old_view.prepare(path, task_id)
    if result.get('schema') != 'medication-work-availability/1':
        return result
    state = task_state(path, {'task_id': task_id})
    if state['task_revision'] != result['task_revision']:
        raise ValueError('Medication task changed while reading coverage')
    answers = []
    for row in state['answers']:
        if not row['current']:
            continue
        a = S.inspect(path, row['request']['answer_id'])
        answers.append({'answer_id': a['id'], 'answer_hash': digest(a), 'question': a['question'],
            'question_receipt_ids': row['question_receipt_ids'], 'review_attachment_id': row['id'],
            'availability_status': 'recorded_at_inference' if a.get('source_availability') else 'not_recorded_in_historical_answer',
            'availability': a.get('source_availability'), 'source_scope': a['source_scope'], 'record_scope': a.get('record_scope'),
            'coverage': a['coverage'], 'source_passes': [{'pass': p['pass_number'], 'availability': p.get('source_availability')} for p in a.get('passes', [])],
            'reconciliation_groups': [{'source_records': g['source_records'], 'availability': g.get('source_availability')} for g in a.get('reconciliation', {}).get('groups', [])],
            'clinical_meaning_validated': False})
    if task_state(path, {'task_id': task_id}) != state:
        raise ValueError('Medication answers or task changed while reading coverage')
    result = {**result, 'schema': VERSION, 'answers': answers, 'interpretation_history': state['answers'],
              'implementation': identity(), 'scope': 'Additive machine interpretations of this exact source review. Original handoff and documentary results remain unchanged.'}
    result.pop('id', None)
    result['id'] = digest(result)
    return result
