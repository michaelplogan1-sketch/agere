"""Durable original-question decisions using existing answer, matching and work stores.

The local baseline proposes documentary review topics from exact cited source
categories. It does not infer positive findings, clinical indications or urgency.
Negated and normal accounts still merit accurate review, never treatment by rule.
"""
from pathlib import Path
import hashlib
import json
import zlib
from ...contracts import digest
from . import source_synthesis as answers, care_capabilities as care
from . import retrieval_source_plan as source_plans, patient_context, participation_work, care_readiness

VERSION = 'original-question-decision/1'
# An explicit, inspectable baseline. Generic notes/encounters/labs are deliberately
# unclassified: their category alone cannot identify a service review discipline.
REVIEW_TOPICS = {
    'medication_review': ['medication_orders', 'medication_lists', 'medication_reconciliation',
                          'medication_use_interviews', 'pharmacy_dispenses', 'pharmacy_clarifications'],
    'functional_recovery': ['functional_assessments', 'functional_mobility_exams', 'rehabilitation_assessments'],
    'caregiver_support': ['care_support_interviews', 'caregiver_contacts'],
    'care_navigation': ['appointments', 'appointment_status_events', 'appointment_followups', 'scheduling_contacts'],
    'care_access': ['coverage', 'coverage_verifications', 'eligibility_responses', 'coverage_enrollments'],
    'symptom_support': ['respiratory_assessments', 'oxygen_assessments', 'pulmonary_function_tests'],
    'blood_pressure': ['blood_pressure_logs'],
    'diabetes': ['glycemic_assessments', 'diabetes_followups'],
}
LIMITS = [
    'Topics identify records a service could review, not a confirmed patient need, treatment indication or referral.',
    'Cited source types are the simple baseline; prose meaning, polarity, urgency and comparative service benefit are not adjudicated.',
    'Unmapped findings and all answer uncertainties remain visible; no eligible service is inferred from missing evidence.',
    'Buyer availability, payer requirements, participation, current goals and consent require verification. No external care is executed.',
]


def identity():
    # Explicit v1 behavior identity, captured by the sealed baseline release.
    # Optional planners must not invalidate unchanged baseline decisions merely
    # because this shared orchestration module acquires another implementation.
    # Bump this contract identity when baseline behavior changes; build receipts
    # independently retain the exact current implementation file hashes.
    known='4dab10a6b7d7db6e72bcc5b7b02babc14a30495f697b7173e4e5ddd03374147f'
    topics=digest(REVIEW_TOPICS)
    return known if topics=='ab64dde59aeedd5b0fcbb0eae8e47aca8297023b5e6b2e0fcd4615e36f475ac0' else digest([VERSION,known,topics])


def planner_identity(planner):
    if planner.get('method') == 'buyer_documentary_candidate':
        from . import decision_planner
        return decision_planner.identity()
    return identity()


def received_inventory(path, patient):
    """Exact locally received versions, never an interpretation of new records."""
    from . import evidence_threads
    db, meta = evidence_threads.connect(path)
    try:
        db.execute('BEGIN')
        rows = evidence_threads.checked_records(db, patient, meta['snapshot']['as_of'])
        ids = [r['record_id'] for r in rows]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate received source identities')
        result = {'schema':'decision-received-inventory/1', 'patient_id':patient,
            'snapshot':meta['snapshot'],
            'records':{r['record_id']:{'record_hash':digest(r), 'source_table':r['source_table'],
                'event_date':r.get('event_date'), 'available_date':r.get('available_date')} for r in rows}}
        result['id'] = digest(result)
        return result
    finally: db.close()


def compare_received(row, current):
    if current['patient_id'] != row['patient_id']:
        raise ValueError('Received-source comparison belongs to another patient')
    baseline = row.get('received_inventory')
    complete = baseline is not None
    if complete:
        if (baseline.get('schema') != 'decision-received-inventory/1'
                or baseline['patient_id'] != row['patient_id']
                or baseline['id'] != digest({k:v for k,v in baseline.items() if k != 'id'})):
            raise ValueError('Decision received-source inventory integrity changed')
        before = baseline['records']
    else:
        # Old decisions cannot retroactively claim that outside-selection rows
        # arrived later. Only their saved selected versions are comparable.
        before = {rid:{'record_hash':h} for rid,h in row['source_versions']['records'].items()}
    after = current['records']; selected = set(row['source_versions']['records'])
    changes = []
    for rid in sorted(set(before) | (set(after) if complete else set())):
        old, new = before.get(rid), after.get(rid)
        if old is not None and new is not None and old['record_hash'] == new['record_hash']:
            continue
        changes.append({'record_id':rid,
            'change':'newly_received' if old is None else 'not_in_current_snapshot' if new is None else 'source_version_changed',
            'previous':old, 'current':new, 'in_original_answer_selection':rid in selected,
            'clinical_relevance':'unassessed'})
    outside_baseline = len(set(after)-set(before)) if not complete else 0
    return {'schema':'decision-source-arrival/1', 'patient_id':row['patient_id'],
        'baseline_kind':'complete_received_inventory' if complete else 'original_selected_records_only',
        'baseline_inventory_id':baseline['id'] if complete else None,
        'current_inventory_id':current['id'], 'current_snapshot':current['snapshot'],
        'baseline_records':len(before), 'current_records':len(after),
        'unbaselined_current_records':outside_baseline, 'changes':changes,
        'newly_received_count':sum(c['change']=='newly_received' for c in changes) if complete else None,
        'version_changed_count':sum(c['change']=='source_version_changed' for c in changes),
        'not_in_current_snapshot_count':sum(c['change']=='not_in_current_snapshot' for c in changes),
        'status':'received_source_changed_requires_interpretation' if changes else 'no_received_source_changes' if complete else 'outside_selection_arrival_unassessed',
        'clinical_relevance_assessed':False, 'care_outcome_established':False,
        'scope':'Changes compare local received snapshots, not clinical event onset or care delivery. Existing outside-selection records are not newly received. Historical decisions without a complete inventory cannot establish arrivals outside their saved selection.'}


def database(path):
    db = care.db_open(path)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS question_decisions(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS question_decision_tasks(decision TEXT, task TEXT, payload TEXT NOT NULL, PRIMARY KEY(decision,task));
      CREATE TABLE IF NOT EXISTS question_decision_followups(id TEXT PRIMARY KEY, decision TEXT, payload TEXT NOT NULL);
    ''')
    for table in ('question_decisions', 'question_decision_tasks', 'question_decision_followups'):
        for op in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable question decision history'); END")
    return db


def load(path, key):
    key = care.text(key, 'decision ID', 64)
    db = database(path)
    try: result = db.execute('SELECT payload FROM question_decisions WHERE id=?', (key,)).fetchone()
    finally: db.close()
    if not result: raise ValueError('Unknown saved question decision')
    row = json.loads(result[0])
    if row['id'] != digest({k:v for k,v in row.items() if k != 'id'}):
        raise care.work.WorkConflict('Decision integrity changed')
    return row


def source_binding(retriever, answer_id):
    answer = answers.inspect(retriever.path, answer_id)
    if answer.get('status') != 'complete' or not answer.get('findings'):
        raise ValueError('A complete saved interpretation with findings is required; failed answers remain blocked')
    from . import plan_answer
    if answer.get('schema')==plan_answer.VERSION:
        return plan_answer.binding(retriever,answer)
    context = answers.context(retriever.path, answer['context_id'])
    if context['patient_id'] != answer['patient_id'] or context['parameters']['question'] != answer['question']:
        raise ValueError('Answer and original evidence context differ')
    from . import decision_reassessment
    revised = decision_reassessment.binding(retriever, answer, context)
    if revised is not None: return revised
    with source_plans.database(retriever.path) as db:
        rows = db.execute("SELECT DISTINCT plans.id FROM plans,json_each(plans.payload,'$.groups') g WHERE json_extract(g.value,'$.context_id')=?", (context['id'],)).fetchall()
    plans = [source_plans.inspect(retriever, {'id':r[0]})['plan'] for r in rows]
    if plans:
        scopes = {(p['preview']['original_question'], digest(p['preview']['analysis_window'])) for p in plans}
        if len(scopes) != 1: raise ValueError('Ambiguous original-question scope for this answer')
        plan = sorted(plans, key=lambda p:p['id'])[0]
        if len(plan['groups']) != 1:
            raise ValueError('This answer covers one of several source groups; complete-question decision is blocked')
        if plan['preview']['review_question'] != answer['question']:
            raise ValueError('Source plan question differs from saved interpretation')
        question = plan['preview']['original_question']; window = plan['preview']['analysis_window']
    else:
        if '\n\nEvidence scope:' in answer['question']:
            raise ValueError('Scoped question requires its saved original-question plan')
        plan = None; question = answer['question']; window = {'basis':'declared_context', 'start':'', 'end':''}
    packet = context['model_context']
    versions = {'snapshot':packet['snapshot'], 'context_digest':digest(context),
                'records':{key:row['source']['record_hash'] for key,row in packet['sources'].items()},
                'source_plan_id':plan['id'] if plan else None,
                'source_plan_digest':digest(plan) if plan else None}
    binding = {'question':question, 'patient_id':answer['patient_id'], 'context_id':context['id'],
               'answer_id':answer['id'], 'answer_digest':digest(answer), 'analysis_window':window,
               'source_versions':versions, 'record_scope':answer.get('record_scope'),
               'answer_coverage':answer.get('coverage', {})}
    return answer, context, binding


def review_plan(answer, context, binding):
    sources = context['model_context']['sources']; topics = []; mapped = set()
    for tag, tables in REVIEW_TOPICS.items():
        supporting = []
        for finding in answer['findings']:
            citations = []
            for cite in finding.get('citations', []):
                src = cite.get('source', {}); received = sources.get(src.get('record_id'), {}).get('source')
                if received != src or src.get('patient_id') != answer['patient_id']:
                    raise ValueError('Finding citation does not match the bound patient/source version')
                if src.get('source_table') in tables:
                    citations.append({'record_id':src['record_id'], 'record_hash':src['record_hash'],
                                      'source_table':src['source_table'], 'quote':cite['quote']})
            if citations:
                supporting.append({'finding_id':finding['id'], 'citations':citations}); mapped.add(finding['id'])
        if supporting: topics.append({'need_tag':tag, 'support':supporting, 'authority':'documentary_review_topic_only'})
    row = {'schema':VERSION, **binding, 'method':'cited_source_type_baseline', 'rule_version':identity(),
           'review_topics':topics, 'unmapped_finding_ids':[f['id'] for f in answer['findings'] if f['id'] not in mapped],
           'unresolved':answer.get('unanswered', []), 'clinical_indication_established':False,
           'clinical_execution_authorized':False, 'limits':LIMITS}
    row['result_id'] = digest(row)
    return row


def prepare(retriever, request, *, ledger=None, allow_live=False):
    care.exact(request, 'answer_id buyer_id coverage'+(' coverage_binding' if 'coverage_binding' in request else '')+(' planner' if 'planner' in request else ''))
    mode=request.get('planner','cited_source_type_baseline')
    if mode not in ('cited_source_type_baseline','buyer_documentary_candidate'): raise ValueError('Unknown question planner')
    answer, context, binding = source_binding(retriever, request['answer_id'])
    inventory = received_inventory(retriever.path, answer['patient_id'])
    if any(inventory['records'].get(rid, {}).get('record_hash') != h
           for rid,h in binding['source_versions']['records'].items()):
        raise care.work.WorkConflict('Decision inventory differs from bound source versions')
    coverage = care.coverage(request['coverage'])
    if coverage['as_of'] > binding['source_versions']['snapshot']['as_of']:
        raise ValueError('Decision date exceeds received snapshot')
    # Prevent later evidence from supporting an earlier decision.
    if any(not s['source'].get('available_date') or s['source']['available_date'] > coverage['as_of'] for s in context['model_context']['sources'].values()):
        raise ValueError('Decision date precedes or cannot establish availability of cited context records')
    planner = review_plan(answer, context, binding)
    if mode == 'buyer_documentary_candidate':
        from . import decision_planner
        baseline=planner
        planner=decision_planner.prepare(retriever.path,answer,context,binding,request['buyer_id'],ledger=ledger,allow_live=allow_live)
    topic_ids = sorted({s['finding_id'] for t in planner['review_topics'] for s in t['support']})
    matching = None
    if topic_ids:
        patient = patient_context.prepare(retriever.path, {'patient_id':answer['patient_id'], 'as_of':coverage['as_of']})
        match_request = {'buyer_id':request['buyer_id'], 'origin':{'kind':'answer','id':answer['id'],'finding_ids':topic_ids},
            'need_tags':[t['need_tag'] for t in planner['review_topics']],
            'rationale':('Cited-source review topics, not clinical indications. Shared question planner ' if mode=='cited_source_type_baseline' else 'Unqualified machine documentary review topics; not clinical indications. Shared question planner ')+planner['result_id'],
            'coverage':request['coverage'], 'patient_context_id':patient['id']}
        if 'coverage_binding' in request: match_request['coverage_binding'] = request['coverage_binding']
        matching = patient_context.prepare_match(retriever.path, match_request)
    row = {'schema':VERSION, **binding, 'request':request, 'buyer_id':care.text(request['buyer_id'], 'buyer ID'),
        'received_inventory':inventory,
        'finding_sources':[{'finding_id':f['id'], 'finding_digest':digest(f),
            'records':{c['source']['record_id']:c['source']['record_hash'] for c in f.get('citations',[])}}
            for f in answer['findings']],
        'decision_date':coverage['as_of'], 'planning':planner, 'planning_result_id':planner['result_id'],
        'planning_digest':digest(planner), 'matching':matching,
        'rule_versions':{'question_planner':planner_identity(planner), 'service_matching':care.identity()},
        'service_selection':{'method':'derived_from_supported_findings', 'derivation':'cited_source_type_review_topics_only' if mode=='cited_source_type_baseline' else 'buyer_documentary_candidate',
            'finding_ids':topic_ids, 'matching_id':matching['id'] if matching else None,
            'matching_digest':digest(matching) if matching else None},
        'status':'review_options_prepared' if matching and matching['options'] else 'no_supported_service_option',
        'clinical_execution_authorized':False, 'external_dispatch_performed':False, 'new_paid_api_calls':0,
        'limits':LIMITS}
    if mode=='buyer_documentary_candidate':
        row['service_selection']['candidate_capability_ids']=sorted({s['capability_id'] for t in planner['review_topics'] for s in t['support']})
        row['baseline_comparison']={'baseline':baseline,'identical_answer_digest':binding['answer_digest'],
            'baseline_mapped_findings':len(answer['findings'])-len(baseline['unmapped_finding_ids']),
            'candidate_mapped_findings':len(answer['findings'])-len(planner['unmapped_finding_ids']),
            'basis':'Same saved findings and exact excerpts; coverage comparison is not correctness or superiority.'}
        row['limits']=planner['limits']+[planner['qualification']]
        # Immutable decision history records original planning usage; replay must
        # not present that historical count as new dispatches on every reopen.
        row.pop('new_paid_api_calls')
        row['planning_paid_api_calls_recorded']=sum(not r['usage'].get('cache_hit',False) for r in planner['receipts'])
    row['id'] = digest(row)
    if source_binding(retriever, request['answer_id'])[2] != binding:
        raise care.work.WorkConflict('Answer or source changed while preparing decision')
    if received_inventory(retriever.path, answer['patient_id']) != inventory:
        raise care.work.WorkConflict('Received inventory changed while preparing decision')
    db = database(retriever.path)
    try:
        with db: db.execute('INSERT OR IGNORE INTO question_decisions VALUES (?,?)', (row['id'], json.dumps(row, allow_nan=False)))
    finally: db.close()
    return inspect(retriever, row['id'])


def inspect(retriever, key):
    row = load(retriever.path, key)
    from . import graph_decision
    if row.get('schema') == graph_decision.VERSION: return graph_decision.inspect(retriever.path, row)
    if row.get('schema') == graph_decision.MEMBER: return graph_decision.inspect_member(retriever, row)
    if row.get('schema') == POPULATION_VERSION: return inspect_population(retriever, row)
    if row['rule_versions'] != {'question_planner':planner_identity(row['planning']), 'service_matching':care.identity()}:
        raise care.work.WorkConflict('Decision rules changed; prepare a new decision version')
    if row['planning'].get('method')=='buyer_documentary_candidate' and row['planning']['buyer_digest']!=digest(care.portfolio(retriever.path,row['buyer_id'])):
        raise care.work.WorkConflict('Decision buyer definitions changed')
    _, _, binding = source_binding(retriever, row['answer_id'])
    if any(row[k] != v for k,v in binding.items()): raise care.work.WorkConflict('Decision sources changed')
    if row.get('received_inventory') is not None and received_inventory(retriever.path, row['patient_id']) != row['received_inventory']:
        raise care.work.WorkConflict('Received sources changed; refresh interpretation before further work')
    if row['matching']:
        m = row['matching']; current = care.inspect_plan(retriever.path, m['id'])
        if any(m.get(k) != v for k,v in current.items()): raise care.work.WorkConflict('Decision service plan changed')
        patient_context.association(retriever.path, current)
        for p in m['participation_contexts'].values(): participation_work.evidence.inspect(retriever.path, p['id'])
    return row


def reopen(retriever, key):
    row = load(retriever.path, key)
    from . import graph_decision
    if row.get('schema') == graph_decision.VERSION: return graph_decision.reopen(retriever, row)
    if row.get('schema') == POPULATION_VERSION: return reopen_population(retriever, row)
    try: inspect(retriever, key); current = True; reason = None
    except (ValueError, KeyError, FileNotFoundError) as exc: current = False; reason = str(exc)
    try: arrivals = compare_received(row, received_inventory(retriever.path, row['patient_id']))
    except (ValueError, KeyError, FileNotFoundError) as exc:
        arrivals = {'status':'received_inventory_unavailable', 'reason':str(exc),
            'changes':[], 'clinical_relevance_assessed':False}
    if arrivals.get('changes'):
        current = False
        reason = reason or 'Received sources changed; refresh interpretation before further work'
    db = database(retriever.path)
    try:
        links = [json.loads(r[0]) for r in db.execute('SELECT payload FROM question_decision_tasks WHERE decision=? ORDER BY task', (key,))]
        followups = [json.loads(r[0]) for r in db.execute('SELECT payload FROM question_decision_followups WHERE decision=? ORDER BY rowid', (key,))]
    finally: db.close()
    tasks = []
    for link in links:
        task = care_readiness.task(retriever.path, link['task_id'])
        matches = task['patient_id'] == row['patient_id'] and task['plan_id'] == link['plan_id'] and task['option_id'] == link['option_id']
        tasks.append({'task':task, 'decision_binding':link, 'binding_current':matches})
    return {'decision':row, 'dependencies_current':current, 'stale_reason':reason, 'tasks':tasks,
            'followups':followups, 'received_source_changes':arrivals, 'new_paid_api_calls':0}


def queue(retriever, request):
    care.exact(request, 'decision_id option_id actor')
    row = inspect(retriever, request['decision_id']); match = row['matching']
    if not match: raise ValueError('No supported service options')
    if request['option_id'] not in match['participation_contexts']: raise ValueError('Option does not belong to this decision')
    allowed=row['service_selection'].get('candidate_capability_ids')
    if allowed is not None and not any(o['id']==request['option_id'] and o['capability']['id'] in allowed for o in match['options']):
        raise ValueError('Option has no supporting candidate capability disposition')
    result = participation_work.enqueue(retriever.path, {'plan_id':match['id'], 'option_id':request['option_id'],
        'actor':request['actor'], 'participation_context_id':match['participation_contexts'][request['option_id']]['id']})
    task = result['task']
    link = {'decision_id':row['id'], 'task_id':task['id'], 'plan_id':match['id'], 'option_id':task['option_id'],
            'question':row['question'], 'source_versions':row['source_versions']}
    if 'answer_digest' in row: link['answer_digest'] = row['answer_digest']
    else: link.update(graph_result_id=row['graph_result_id'], graph_digest=row['graph_digest'],
                      parent_decision_id=row['parent_decision_id'], evidence_id=row['evidence_id'])
    inspect(retriever, row['id'])
    db = database(retriever.path)
    try:
        with db: db.execute('INSERT OR IGNORE INTO question_decision_tasks VALUES (?,?,?)', (row['id'], task['id'], json.dumps(link)))
    finally: db.close()
    return {**result, 'decision_binding':link}


def bound_task(retriever, row, key):
    view = reopen(retriever, row['id'])
    found = next((x for x in view['tasks'] if x['task']['id'] == key), None)
    if not found or not found['binding_current']: raise ValueError('Task is not bound to this exact decision version')
    return found['task']


def event(retriever, request):
    care.exact(request, 'decision_id task_id expected_revision request_id action actor note')
    row = inspect(retriever, request['decision_id']); bound_task(retriever, row, request['task_id'])
    result = patient_context.task_event(retriever.path, {k:v for k,v in request.items() if k != 'decision_id'})
    binding = ({'answer_digest':row['answer_digest']} if 'answer_digest' in row else
               {'graph_result_id':row['graph_result_id']} if 'graph_result_id' in row else {'population_proposal_id':row['proposal_id']})
    return {**result, 'decision_id':row['id'], **binding}


def followup(retriever, request):
    care.exact(request, 'decision_id task_id answer_id')
    # Historical decisions can be compared with current evidence without altering
    # their source or work history. New answer must independently validate.
    row = load(retriever.path, request['decision_id']); task = bound_task(retriever, row, request['task_id'])
    answer, context, binding = source_binding(retriever, request['answer_id'])
    if any(binding[k] != row[k] for k in ('patient_id','question','analysis_window')):
        raise ValueError('Follow-up must retain the same patient, original question and date-window definition')
    inventory = received_inventory(retriever.path, row['patient_id'])
    arrivals = compare_received(row, inventory)
    before = row['source_versions']['records']; after = binding['source_versions']['records']
    changes = [{'record_id':k, 'change':'added' if k not in before else 'removed' if k not in after else 'source_version_changed'}
               for k in sorted(set(before)|set(after)) if before.get(k) != after.get(k)]
    result = {'schema':VERSION, 'decision_id':row['id'], 'task_id':task['id'], 'task_revision':task['revision'],
        'task_digest':digest(task), 'patient_id':row['patient_id'], 'question':row['question'],
        'previous_answer_digest':row['answer_digest'], 'answer_id':answer['id'], 'answer_digest':digest(answer),
        'source_versions':binding['source_versions'], 'changes':changes,
        'current_received_inventory':inventory, 'received_source_changes':arrivals,
        'interpretation_changed':row['answer_digest'] != digest(answer),
        'review_plan':review_plan(answer, context, binding), 'unresolved':answer.get('unanswered', []),
        'status':'no_new_evidence' if not changes and not arrivals['changes'] and row['answer_digest'] == digest(answer) else 'source_or_interpretation_changed_requires_review',
        'care_outcome_established':False, 'clinical_execution_authorized':False, 'external_dispatch_performed':False,
        'scope':'Documentary comparison only. No task is closed and no patient outcome is inferred.', 'new_paid_api_calls':0}
    result['result_id'] = digest(result)
    if received_inventory(retriever.path, row['patient_id']) != inventory:
        raise care.work.WorkConflict('Received sources changed during answer follow-up')
    db = database(retriever.path)
    try:
        with db: db.execute('INSERT OR IGNORE INTO question_decision_followups VALUES (?,?,?)', (result['result_id'], row['id'], json.dumps(result)))
    finally: db.close()
    return result


def observe(retriever, request):
    """Persist received-source follow-up before a new interpretation is available."""
    care.exact(request, 'decision_id task_id')
    row = load(retriever.path, request['decision_id'])
    task = bound_task(retriever, row, request['task_id'])
    inventory = received_inventory(retriever.path, row['patient_id'])
    changes = compare_received(row, inventory)
    result = {**changes, 'decision_id':row['id'], 'task_id':task['id'],
        'task_revision':task['revision'], 'task_digest':digest(task),
        'question':row['question'], 'previous_answer_digest':row['answer_digest'],
        'current_received_inventory':inventory, 'new_answer_available':False,
        'unresolved':row['planning']['unresolved'], 'interpretation_changed':False,
        'clinical_execution_authorized':False, 'external_dispatch_performed':False,
        'task_state_changed':False, 'new_paid_api_calls':0}
    result['result_id'] = digest(result)
    if received_inventory(retriever.path, row['patient_id']) != inventory:
        raise care.work.WorkConflict('Received sources changed during follow-up observation')
    current_task = care_readiness.task(retriever.path, task['id'])
    if digest(current_task) != digest(task):
        raise care.work.WorkConflict('Work changed during follow-up observation')
    db = database(retriever.path)
    try:
        with db:
            db.execute('INSERT OR IGNORE INTO question_decision_followups VALUES (?,?,?)',
                (result['result_id'], row['id'], json.dumps(result, allow_nan=False)))
    finally: db.close()
    return result


def review_preview(retriever, request):
    """Plan a source-change review without claiming relevance or re-answering."""
    from . import record_context, clinical_events, warehouse_interpretation, question_context
    care.exact(request, 'decision_id task_id')
    row = load(retriever.path, request['decision_id'])
    task = bound_task(retriever, row, request['task_id'])
    inventory = received_inventory(retriever.path, row['patient_id'])
    arrivals = compare_received(row, inventory)
    records, meta = record_context.received(retriever.path, row['patient_id'])
    if {r['record_id']:digest(r) for r in records} != {k:v['record_hash'] for k,v in inventory['records'].items()}:
        raise care.work.WorkConflict('Received sources changed during review planning')
    window = row['analysis_window']
    date_window = {'basis':'all_observed','start':'','end':''} if window['basis']=='declared_context' else window
    clinical_events.validate_window(date_window, meta['snapshot']['as_of'])
    index = warehouse_interpretation.index(records)
    states = {r['record_id']:clinical_events.window_state(r,date_window,meta['snapshot']['as_of'],index) for r in records}
    changed = {c['record_id'] for c in arrivals['changes']}
    roots = (set(row['source_versions']['records']) | changed) & set(states)
    eligible = {rid for rid in roots if states[rid] is True}
    closure = question_context.select_sources(records, eligible, meta['snapshot']['as_of']) if eligible else {'connected_record_ids':[], 'reference_edges':[]}
    # Traverse existing typed references, but never include an out-of-window or
    # unknown-date dependency as evidence for the original dated question.
    connected = set(closure['connected_record_ids'])
    selected = sorted(rid for rid in connected if states[rid] is True)
    excluded = [{'record_id':rid,'reason':'unknown_date' if states[rid] is None else 'outside_original_window'}
                for rid in sorted(roots|connected) if states[rid] is not True]
    bound = row.get('finding_sources')
    complete_index = bound is not None
    if bound is None:
        known = {}
        for topic in row['planning']['review_topics']:
            for support in topic['support']:
                known.setdefault(support['finding_id'],{}).update({c['record_id']:c['record_hash'] for c in support['citations']})
        bound = [{'finding_id':key,'records':value} for key,value in sorted(known.items())]
    impacts = [{'finding_id':f['finding_id'], 'changed_cited_records':sorted(set(f['records']) & changed),
        'status':'cited_source_changed_requires_review' if set(f['records']) & changed else 'direct_sources_unchanged_context_effect_unassessed'} for f in bound]
    review_question = row['question']+'\n\nEvidence scope: Follow-up subset in the original date window '+json.dumps(window,sort_keys=True,separators=(',',':'))+'. Review only provided current records. Prior findings, absent records and other review groups are not supplied. Relevance, cross-group relationships and clinical completeness remain unassessed. Missing records do not establish absent care.'
    blockers = []
    if not arrivals['changes']: blockers.append('No established received-source changes to review')
    if not selected: blockers.append('No current source is eligible in the original date window')
    if len(selected)>200: blockers.append('Review exceeds 200 whole records; explicit narrower scope is required')
    if len({inventory['records'][k]['source_table'] for k in selected})>30: blockers.append('Review exceeds 30 source categories')
    if len(review_question)>1500: blockers.append('Original question and follow-up scope exceed the existing context question bound')
    result = {'schema':'decision-source-review/1', 'decision_id':row['id'], 'task_id':task['id'],
        'task_digest':digest(task), 'patient_id':row['patient_id'], 'original_question':row['question'],
        'analysis_window':window, 'review_question':review_question, 'arrivals':arrivals,
        'inventory_id':inventory['id'], 'selected_record_ids':selected,
        'selected_versions':{rid:inventory['records'][rid] for rid in selected},
        'prior_selected_records':sorted(set(selected)&set(row['source_versions']['records'])),
        'changed_records_selected':sorted(set(selected)&changed),
        'linked_records_added':sorted(set(selected)-roots), 'date_exclusions':excluded,
        'reference_edges':closure['reference_edges'],
        'absent_records':[c['record_id'] for c in arrivals['changes'] if c['current'] is None],
        'received_records_outside_review':len(records)-len(selected),
        'finding_dependency_index_complete':complete_index, 'finding_impacts':impacts,
        'unindexed_finding_ids':[] if complete_index else row['planning']['unmapped_finding_ids'],
        'can_prepare':not blockers, 'blockers':blockers, 'new_paid_api_calls':0,
        'selection_sufficiency':'unassessed', 'clinical_relevance_assessed':False,
        'scope':'Current prior-selected records plus changed received records and typed reference closure within the original date window. This is a proposed follow-up subset, not an automatically reanswered question or a complete chart.'}
    result['id'] = digest(result)
    return result


def saved_followup(path, key):
    key = care.text(key,'follow-up ID',64)
    db = database(path)
    try: found = db.execute('SELECT payload FROM question_decision_followups WHERE id=?',(key,)).fetchone()
    finally: db.close()
    if not found: raise ValueError('Unknown saved decision follow-up')
    row = json.loads(found[0])
    if row.get('result_id') != key or digest({k:v for k,v in row.items() if k!='result_id'}) != key:
        raise ValueError('Decision follow-up integrity changed')
    return row


def save_followup(path, row):
    row['result_id'] = digest(row)
    db = database(path)
    try:
        with db: db.execute('INSERT OR IGNORE INTO question_decision_followups VALUES (?,?,?)',
            (row['result_id'],row['decision_id'],json.dumps(row,allow_nan=False)))
    finally: db.close()
    return row


def prepare_review(retriever, request):
    care.exact(request,'decision_id task_id preview_id')
    origin = {k:request[k] for k in ('decision_id','task_id')}
    preview = review_preview(retriever, origin)
    if preview['id'] != request['preview_id']: raise care.work.WorkConflict('Source review preview changed')
    if not preview['can_prepare']: raise ValueError('; '.join(preview['blockers']))
    groups = source_plans.contexts(retriever, preview)
    if review_preview(retriever, origin) != preview: raise care.work.WorkConflict('Sources or work changed during follow-up preparation')
    result = {'schema':'decision-source-review/1', **origin, 'preview':preview,
        'question':preview['original_question'], 'analysis_window':preview['analysis_window'],
        'groups':[{k:v for k,v in group.items() if k!='context'}|{'context_id':group['context']['id'],
            'context_hash':digest(group['context']), 'record_ids':group['context']['record_scope']['record_ids'],
            'group_number':i} for i,group in enumerate(groups,1)],
        'changes':preview['arrivals']['changes'], 'unresolved':load(retriever.path,request['decision_id'])['planning']['unresolved'],
        'status':'followup_contexts_prepared' if all(g['request_fit']!='blocked' for g in groups) else 'followup_context_incomplete',
        'interpretation_changed':False, 'original_question_reanswered':False, 'care_outcome_established':False,
        'task_state_changed':False, 'external_dispatch_performed':False, 'new_paid_api_calls':0}
    return save_followup(retriever.path,result)


def review_answer(retriever, request):
    """Attach a validated subset answer without promoting it to a full decision."""
    care.exact(request,'review_id answer_id')
    review = saved_followup(retriever.path, request['review_id'])
    if review.get('schema')!='decision-source-review/1' or 'groups' not in review:
        raise ValueError('A prepared source-change review is required')
    origin = {k:review[k] for k in ('decision_id','task_id')}
    if review_preview(retriever,origin) != review['preview']:
        raise care.work.WorkConflict('Follow-up sources or work changed; prepare a current review')
    answer = answers.inspect(retriever.path, request['answer_id'])
    group = next((g for g in review['groups'] if g['context_id']==answer.get('context_id')),None)
    if (not group or group['request_fit']=='blocked' or answer.get('status')!='complete'
            or answer.get('patient_id')!=review['preview']['patient_id']
            or answer.get('question')!=review['preview']['review_question']):
        raise ValueError('Complete current answer for an exact prepared follow-up group required')
    context = answers.context(retriever.path, group['context_id'])
    if digest(context)!=group['context_hash'] or not context['dispatch']['source_review_allowed'] or context['omissions']:
        raise ValueError('Follow-up context changed or incomplete')
    current = received_inventory(retriever.path, answer['patient_id'])
    if (set(context['model_context']['sources']) != set(group['record_ids']) or
            any(current['records'].get(rid,{}).get('record_hash')!=src['source']['record_hash']
                for rid,src in context['model_context']['sources'].items())):
        raise ValueError('Follow-up answer source versions differ from current received records')
    result = {'schema':'decision-source-review-answer/1', **origin, 'review_id':review['result_id'],
        'question':review['question'], 'analysis_window':review['analysis_window'], 'answer_id':answer['id'],
        'answer_digest':digest(answer), 'context_id':group['context_id'], 'group_number':group['group_number'],
        'group_count':len(review['groups']), 'reviewed_record_ids':group['record_ids'],
        'findings':answer.get('findings',[]), 'unresolved':answer.get('unanswered',[]),
        'changes':review['changes'], 'finding_impacts':review['preview']['finding_impacts'],
        'status':'subset_interpretation_attached_requires_review', 'interpretation_changed':True,
        'original_question_reanswered':False, 'task_state_changed':False, 'care_outcome_established':False,
        'clinical_execution_authorized':False, 'external_dispatch_performed':False,'new_paid_api_calls':0,
        'scope':'This saved answer interprets one declared follow-up group. Findings are not automatically replacements for old findings, clinical changes, new service indications or a complete answer to the original question.'}
    if review_preview(retriever,origin) != review['preview']:
        raise care.work.WorkConflict('Sources or work changed during review attachment')
    return save_followup(retriever.path,result)


POPULATION_VERSION = 'population-question-decision/1'
# These select documentary review services, never clinical indications. Program
# qualification and individual payer/consent checks remain independent.
PROGRAM_TOPICS = {
    'respiratory_source_review':['symptom_support'],
    'behavioral_source_review':['behavioral_health'],
    'functional_support_review':['functional_recovery','caregiver_support'],
    'cardiometabolic_source_review':['blood_pressure','diabetes'],
    'appointment_followthrough_source_review':['care_navigation'],
}


def population_basis(path, request):
    from . import cohorts
    selected = cohorts.select(path, request['cohort'])
    if request['as_of'] != selected['snapshot']['as_of']:
        raise ValueError('Program assessments currently require the exact received snapshot date; historical program assessment is not implemented')
    with care.work.warehouse(path) as src:
        roster=sorted(r[0] for r in src.execute('SELECT id FROM patients'))
    selected={**selected,'roster_digest':digest(roster),'not_matched_patient_ids':sorted(set(roster)-set(selected['patient_ids'])-set(selected['unknown_patient_ids']))}
    if len(roster)!=selected['counts']['evaluated'] or len(selected['not_matched_patient_ids'])!=selected['counts']['not_matched']:
        raise ValueError('Cohort denominator does not reconcile to the complete roster')
    proposal = care.work.prepare(path, request['program'], selected['patient_ids'],
        input_membership={k:v for k,v in selected.items() if k not in ('cache_hit','examples')})
    db = care.work.database(path)
    try:
        saved = care.work.load(db,'work_proposals',proposal['proposal_id'])
        assessments = [care.work.load(db,'work_assessments',key) for key in saved['assessment_ids']]
    finally: db.close()
    if sorted(a['patient_id'] for a in assessments) != sorted(selected['patient_ids']):
        raise ValueError('Program assessment membership differs from the complete selected cohort')
    return {k:v for k,v in selected.items() if k not in ('cache_hit','examples')}, proposal, assessments


def population_topics(assessment):
    from . import population_protocol_review as protocols
    if assessment['review_candidate'] is not True or not assessment['evidence']: return []
    tables={r['source_table'] for r in assessment['evidence']}
    tags=sorted(set(PROGRAM_TOPICS.get(assessment['program']['id'],[])) |
        {tag for tag,sources in REVIEW_TOPICS.items() if tables & set(sources)})
    declared = protocols.topics(assessment)
    tags = [tag for tag in tags if tag not in {t['need_tag'] for t in declared}]
    return declared + [{'need_tag':tag,'authority':'documentary_program_review_only',
        'program_definition':assessment['program'], 'assessment_id':assessment['id'],
        'source_records':assessment['evidence']} for tag in tags]


def population_coverage(as_of):
    return {'payer_type':'unknown','plan_id':'','region':'','as_of':as_of,
        'provenance':'Population source-review planning; individual coverage and entitlement have not been verified.',
        'preferences':''}


def prepare_population(retriever, request):
    from collections import Counter
    from . import population_protocol_review as protocols
    care.exact(request,'question program cohort buyer_id as_of')
    question=care.text(request['question'],'population question',1500)
    care.text(request['buyer_id'],'buyer ID');care.day(request['as_of'])
    db=database(retriever.path)
    try:
        candidates=[r[0] for r in db.execute("SELECT id FROM question_decisions WHERE json_extract(payload,'$.schema')=? ORDER BY rowid DESC",(POPULATION_VERSION,))]
    finally:db.close()
    for key in candidates:
        old=load(retriever.path,key)
        if old.get('request')!=request:continue
        if bool(old.get('protocol_review_dependency')) != bool(protocols.rule_identity(request['program'])):continue
        try:return inspect_population(retriever,old)
        except (ValueError,KeyError,FileNotFoundError):pass
    preparation_identity=care.work.preparation_identity(retriever.path,[request['program']])
    selection, proposal, assessments = population_basis(retriever.path,request)
    protocol_dependency, protocol_contexts = protocols.prepare(retriever.path,request['program'],assessments,request['as_of'])
    port=care.portfolio(retriever.path,request['buyer_id']);knowledge=care.catalog()
    pathways={p['id']:p for p in knowledge['pathways']};cov=population_coverage(request['as_of'])
    members=[]
    for a in assessments:
        topics=population_topics(a)
        members.append({'patient_id':a['patient_id'],'assessment_id':a['id'],'assessment_digest':digest(a),
            'review_candidate':a['review_candidate'],'status':a['status'],'topics':topics,
            'unresolved':a['review']['workflow']['missing_information'] if a.get('review') else ['Need remains unknown; this program has no positive source-review nomination.'],
            'clinical_eligibility_established':False})
        if protocol_dependency:
            members[-1]['protocol_review'] = protocol_contexts[a['patient_id']]
    service_scope=[]
    for capability in port['capabilities']:
        ids=sorted(m['patient_id'] for m in members if set(capability['need_tags']) & {t['need_tag'] for t in m['topics']})
        routes=[care.route_check(capability,pathways[p],cov,knowledge) for p in capability['pathway_ids']] or [care.route_check(capability,None,cov,knowledge)]
        service_scope.append({'capability':capability,'documentary_candidate_ids':ids,'candidate_count':len(ids),
            'routes':routes,'status':'blocked' if all(r['status']=='blocked' for r in routes) else 'requires_verification',
            'capacity_is_reservation':False,'clinical_eligibility_established':False})
    mapped={pid for s in service_scope for pid in s['documentary_candidate_ids']}
    counts=Counter(review_candidates=0,need_unresolved_without_nomination=0,mapped_for_documentary_review=0,unmapped_review_candidates=0)
    for m in members:
        counts['review_candidates' if m['review_candidate'] is True else 'need_unresolved_without_nomination']+=1
        if m['review_candidate'] is True: counts['mapped_for_documentary_review' if m['patient_id'] in mapped else 'unmapped_review_candidates']+=1
    row={'schema':POPULATION_VERSION,'question':question,'request':request,'program':proposal['program'],
        'buyer_id':request['buyer_id'],'decision_date':request['as_of'],'selection':selection,
        'proposal_id':proposal['proposal_id'],'preparation_identity':preparation_identity,'members':members,'counts':dict(counts),'service_scope':service_scope,
        'rule_versions':{'population':digest([POPULATION_VERSION,PROGRAM_TOPICS,REVIEW_TOPICS]),'service_matching':care.identity()},
        'portfolio_digest':digest(port),'clinical_execution_authorized':False,'new_paid_api_calls':0,
        'scope':'Typed cohort/program specification defines this question execution. Source-review nominations are not clinical eligibility. Every selected member and unknown cohort member is retained; services overlap and declared slots are not reserved. Current-snapshot program assessment only.'}
    if protocol_dependency:
        row['protocol_review_dependency'] = protocol_dependency
        row['protocol_review_counts'] = {
            'members': len(members),
            'members_with_medication_accounts': sum(bool(m['protocol_review']['medications']['item_count']) for m in members),
            'members_without_medication_accounts': sum(not m['protocol_review']['medications']['item_count'] for m in members),
            'documentary_readiness_supported': sum(m['protocol_review']['protocol']['documentary_readiness'] is True for m in members),
            'documentary_readiness_unresolved': sum(m['protocol_review']['protocol']['documentary_readiness'] is None for m in members),
            'documentary_readiness_not_supported': sum(m['protocol_review']['protocol']['documentary_readiness'] is False for m in members),
        }
        if protocols.current_dependency(retriever.path,request['program'],selection['patient_ids']) != protocol_dependency:
            raise care.work.WorkConflict('Protocol medication sources changed during preparation')
    # Same deterministic engines recheck inputs before the immutable question is saved.
    if (care.work.preparation_identity(retriever.path,[request['program']])!=preparation_identity
            or digest(care.portfolio(retriever.path,request['buyer_id']))!=row['portfolio_digest']):
        raise care.work.WorkConflict('Population or buyer inputs changed during preparation')
    row['id']=digest(row)
    db=database(retriever.path)
    try:
        with db:db.execute('INSERT OR IGNORE INTO question_decisions VALUES (?,?)',(row['id'],json.dumps(row,allow_nan=False)))
    finally:db.close()
    return row


def inspect_population(retriever, row, *, recheck=None):
    if row.get('protocol_review_dependency'):
        from . import population_protocol_review as protocols
        if protocols.current_dependency(retriever.path,row['request']['program'],row['selection']['patient_ids']) != row['protocol_review_dependency']:
            raise care.work.WorkConflict('Population protocol review sources or mapping changed; prepare a new decision')
    if row['rule_versions']!={'population':digest([POPULATION_VERSION,PROGRAM_TOPICS,REVIEW_TOPICS]),'service_matching':care.identity()}:
        raise care.work.WorkConflict('Population decision rules changed')
    from . import cohorts
    if row.get('preparation_identity') is None:
        # Early development decisions stay historical; never rewrite their payload.
        raise care.work.WorkConflict('Population decision predates dependency binding; prepare again')
    selected=cohorts.select(retriever.path,row['request']['cohort'])
    expected={k:v for k,v in row['selection'].items() if k not in ('roster_digest','not_matched_patient_ids')}
    selected={k:v for k,v in selected.items() if k not in ('cache_hit','examples')}
    with care.work.warehouse(retriever.path) as src:roster=sorted(r[0] for r in src.execute('SELECT id FROM patients'))
    preparation = care.work.preparation_identity(retriever.path,[row['request']['program']])
    if (selected!=expected or digest(roster)!=row['selection']['roster_digest']
            or digest(care.portfolio(retriever.path,row['buyer_id']))!=row['portfolio_digest']):
        raise care.work.WorkConflict('Population, assessments or buyer services changed; prepare a new decision')
    db=care.work.database(retriever.path)
    saved_assessments={}
    try:
        for member in row['members']:
            assessment=care.work.load(db,'work_assessments',member['assessment_id'])
            if digest(assessment)!=member['assessment_digest']:
                raise care.work.WorkConflict('Saved population assessment changed')
            saved_assessments[member['patient_id']]=assessment
    finally:db.close()
    if preparation != row['preparation_identity']:
        # Shared transport/accounting dependencies can change the preparation
        # identity without changing a native assessment. Recompute the current
        # assessments, never the work, and compare every field except their
        # derived receipt ID and implementation identity. Source hashes, rules,
        # nominations, context attachments and all uncertainty must still match.
        selection, _, current = population_basis(retriever.path,row['request'])
        by_patient={a['patient_id']:a for a in current}
        substantive=lambda a:{k:v for k,v in a.items() if k not in ('id','engine_identity')}
        if (selection!=row['selection'] or len(saved_assessments)!=len(row['members'])
                or len(by_patient)!=len(current) or by_patient.keys()!=saved_assessments.keys()
                or any(substantive(a)!=substantive(by_patient[pid]) for pid,a in saved_assessments.items())):
            raise care.work.WorkConflict('Current population assessments differ; prepare a new decision')
        if (care.work.preparation_identity(retriever.path,[row['request']['program']])!=preparation
                or digest(care.portfolio(retriever.path,row['buyer_id']))!=row['portfolio_digest']):
            raise care.work.WorkConflict('Population dependencies changed during reassessment')
        if recheck is not None:
            recheck.update(method='exact_current_native_assessment_comparison',members=len(current),
                previous_preparation_identity=row['preparation_identity'],current_preparation_identity=preparation,
                excluded_metadata_fields=['id','engine_identity'],all_other_assessment_fields_equal=True,
                work_reexecuted=False,new_provider_requests=0)
    return row


def reopen_population(retriever,row):
    recheck={}
    try:inspect_population(retriever,row,recheck=recheck);current=True;reason=None
    except (ValueError,KeyError,FileNotFoundError) as exc:current=False;reason=str(exc)
    db=database(retriever.path)
    try:
        links=[json.loads(r[0]) for r in db.execute('SELECT payload FROM question_decision_tasks WHERE decision=? ORDER BY task',(row['id'],))]
        followups=[json.loads(r[0]) for r in db.execute('SELECT payload FROM question_decision_followups WHERE decision=? ORDER BY rowid',(row['id'],))]
    finally:db.close()
    tasks=[]
    for link in links:
        task=care_readiness.task(retriever.path,link['task_id'])
        matches=task['patient_id']==link['patient_id'] and task['plan_id']==link['plan_id'] and task['option_id']==link['option_id']
        tasks.append({'task':task,'decision_binding':link,'binding_current':matches})
    return {'decision':row,'dependencies_current':current,'stale_reason':reason,'tasks':tasks,'followups':followups,
            'dependency_recheck':recheck or None,'new_paid_api_calls':0}


def match_population_member(retriever,request):
    care.exact(request,'decision_id patient_id')
    row=inspect(retriever,request['decision_id'])
    if row.get('schema')!=POPULATION_VERSION:raise ValueError('Population decision required')
    member=next((m for m in row['members'] if m['patient_id']==request['patient_id']),None)
    if not member or not member['topics']:raise ValueError('A selected member with source-supported review topics is required')
    ctx=patient_context.prepare(retriever.path,{'patient_id':member['patient_id'],'as_of':row['decision_date']})
    match=patient_context.prepare_match(retriever.path,{'buyer_id':row['buyer_id'],
        'origin':{'kind':'assessment','id':member['assessment_id'],'finding_ids':[]},
        'need_tags':[t['need_tag'] for t in member['topics']],
        'rationale':'Versioned documentary program/source-category baseline for saved population question '+row['id']+'. Review nomination only; not a service indication.',
        'coverage':population_coverage(row['decision_date']),'patient_context_id':ctx['id']})
    inspect(retriever,row['id'])
    return save_followup(retriever.path,{'schema':'population-member-match/1','decision_id':row['id'],
        'question':row['question'],'patient_id':member['patient_id'],'assessment_id':member['assessment_id'],
        'matching':match,'status':'member_options_require_verification','clinical_execution_authorized':False,'new_paid_api_calls':0})


def open_population_protocol_member(retriever, request):
    from . import population_protocol_review as protocols
    care.exact(request,'decision_id patient_id')
    row=inspect(retriever,request['decision_id'])
    member=next((m for m in row.get('members',[]) if m['patient_id']==request['patient_id']),None)
    if not row.get('protocol_review_dependency') or not member:
        raise ValueError('Selected member of a protocol population required')
    context=protocols.open_member(retriever.path,row['request']['program'],member,
        row['protocol_review_dependency'],row['decision_date'],row['selection']['patient_ids'])
    return {'decision_id':row['id'],'patient_id':member['patient_id'],'protocol_review':context,
        'question':row['question'],'clinical_execution_authorized':False}


def queue_population_member(retriever,request):
    # Population batch execution uses this same reentrant lock. A new question
    # may link identical work, but must never silently refresh another plan.
    with _POPULATION_EXECUTION_LOCK:
        return _queue_population_member(retriever,request)


def _queue_population_member(retriever,request):
    care.exact(request,'decision_id match_id option_id actor')
    row=inspect(retriever,request['decision_id']);saved=saved_followup(retriever.path,request['match_id'])
    if (row.get('schema')!=POPULATION_VERSION or saved.get('schema')!='population-member-match/1'
            or saved['decision_id']!=row['id']):raise ValueError('Exact population member matching receipt required')
    match=saved['matching'];current=care.inspect_plan(retriever.path,match['id'])
    if any(match.get(k)!=v for k,v in current.items()):raise care.work.WorkConflict('Member service plan changed')
    patient_context.association(retriever.path,current)
    part=match['participation_contexts'].get(request['option_id'])
    if part is None:raise ValueError('Option does not belong to this population member')
    member = next(m for m in row['members'] if m['patient_id'] == saved['patient_id'])
    protocol_context = (open_population_protocol_member(retriever,
        {'decision_id':row['id'],'patient_id':member['patient_id']})['protocol_review']
        if member.get('protocol_review') else None)
    option=next(o for o in current['options'] if o['id']==request['option_id'])
    task_key=digest([care.VERSION,current['buyer_id'],current['patient_id'],current['request']['origin'],current['need_tags'],option['capability']['id']])
    db=database(retriever.path)
    try:
        old=db.execute('SELECT payload FROM capability_tasks WHERE id=?',(task_key,)).fetchone()
    finally:db.close()
    if old and json.loads(old[0])['plan_id']!=current['id']:
        raise care.work.WorkConflict('Existing internal work uses a different saved service plan. '
            'Review its question context before changing it; this request did not reset its handoff or ownership.')
    result=participation_work.enqueue(retriever.path,{'plan_id':match['id'],'option_id':request['option_id'],
        'actor':request['actor'],'participation_context_id':part['id']})
    task=result['task'];inspect(retriever,row['id'])
    link={'decision_id':row['id'],'question':row['question'],'proposal_id':row['proposal_id'],
        'selection_digest':digest(row['selection']),'patient_id':saved['patient_id'],'assessment_id':saved['assessment_id'],
        'task_id':task['id'],'plan_id':match['id'],'option_id':task['option_id'],'match_id':saved['result_id']}
    if protocol_context:
        link['protocol_review'] = protocol_context
        link['protocol_review_dependency_digest'] = digest(row['protocol_review_dependency'])
    db=database(retriever.path)
    try:
        with db:db.execute('INSERT OR IGNORE INTO question_decision_tasks VALUES (?,?,?)',(row['id'],task['id'],json.dumps(link)))
    finally:db.close()
    return {**result,'decision_binding':link}


import threading
POPULATION_ROUTING = 'population-review-routing/1'
_POPULATION_EXECUTION_LOCK = threading.RLock()


def route_population_member(retriever, request):
    """Retain every received payer account; never silently select current coverage."""
    from . import coverage_sources
    match = match_population_member(retriever, request)
    row = inspect(retriever, request['decision_id'])
    coverage = coverage_sources.prepare(retriever.path, {
        'patient_id': request['patient_id'], 'as_of': row['decision_date']})
    return save_followup(retriever.path, {
        'schema': POPULATION_ROUTING, 'kind': 'member', 'decision_id': row['id'],
        'question': row['question'], 'patient_id': request['patient_id'],
        'match_id': match['result_id'], 'coverage_context': coverage,
        'payer_resolution': 'No received account automatically selected as current coverage; review all dated reports and conflicts.',
        'clinical_execution_authorized': False, 'new_paid_api_calls': 0})


def population_routes(retriever, row):
    from . import coverage_sources
    db = database(retriever.path)
    try:
        saved = [json.loads(r[0]) for r in db.execute(
            'SELECT payload FROM question_decision_followups WHERE decision=? ORDER BY rowid', (row['id'],))]
    finally: db.close()
    routes = {r['patient_id']: r for r in saved if r.get('schema') == POPULATION_ROUTING and r.get('kind') == 'member'}
    result = {}
    for patient, route in routes.items():
        match = saved_followup(retriever.path, route['match_id'])
        if match['decision_id'] != row['id'] or match['patient_id'] != patient:
            raise ValueError('Population routing member binding differs')
        plan = care.inspect_plan(retriever.path, match['matching']['id'])
        if any(match['matching'].get(k) != v for k, v in plan.items()):
            raise care.work.WorkConflict('Population member plan changed')
        patient_context.association(retriever.path, plan)
        if coverage_sources.inspect(retriever.path, route['coverage_context']['id']) != route['coverage_context']:
            raise care.work.WorkConflict('Population coverage reports changed')
        for p in match['matching']['participation_contexts'].values():
            participation_work.evidence.inspect(retriever.path, p['id'])
        result[patient] = (route, match)
    return result


def allocate_population(retriever, request):
    """A bounded documentary review proposal, never a clinical service allocation."""
    from collections import Counter
    from . import work_capacity
    care.exact(request, 'decision_id review_limits assumption_source')
    row = inspect(retriever, request['decision_id'])
    if row.get('schema') != POPULATION_VERSION: raise ValueError('Population decision required')
    limits = request['review_limits']; services = {s['capability']['id'] for s in row['service_scope']}
    if not isinstance(limits, dict) or set(limits) != services:
        raise ValueError('Explicit internal review limit required for every buyer service')
    for v in limits.values(): work_capacity.integer(v, 'Internal review limit', 0, 2000)
    source = care.text(request['assumption_source'], 'Internal review limit source', 2000)
    routes = population_routes(retriever, row)
    snap = work_capacity.snapshot(retriever.path, row['buyer_id'])
    # All noncancelled work consumes this conservative review-batch envelope,
    # including stale and held tasks. Service slot declarations are not workers.
    active = [t for t in snap['tasks'] if t['state'] != 'cancelled']
    remaining = {s: max(0, limits[s] - sum(t['service_id'] == s for t in active)) for s in services}
    members = []
    for m in sorted(row['members'], key=lambda x: x['patient_id']):
        patient = m['patient_id']
        item = {'patient_id': patient, 'assessment_id': m['assessment_id'], 'clinical_eligibility_established': False}
        if not m['review_candidate']:
            item.update(disposition='need_unresolved_without_nomination', reason='No positive documentary nomination; clinical need remains unknown.')
        elif not m['topics']:
            item.update(disposition='unmapped_source_review', reason='No documentary service topic is mapped.')
        elif patient not in routes:
            item.update(disposition='member_routing_missing', reason='Patient, payer and participation contexts have not yet been prepared.')
        else:
            route, match = routes[patient]
            options = sorted(match['matching']['options'], key=lambda o: o['capability']['id'])
            item.update(route_id=route['result_id'], match_id=match['result_id'], coverage_context_id=route['coverage_context']['id'],
                payer_and_consent='unverified', options=[{'option_id': o['id'], 'service_id': o['capability']['id'],
                'status': o['status'], 'routes': o['routes'], 'owner': o['capability']['owner']} for o in options])
            existing = [t for t in active if t['patient_id'] == patient and t['service_id'] in {o['capability']['id'] for o in options}]
            available = [o for o in options if o['status'] != 'blocked']
            if existing:
                exact = all(care_readiness.task(retriever.path, t['id'])['plan_id'] == match['matching']['id']
                    and t['selectable'] for t in existing)
                item.update(disposition='existing_internal_work' if exact else 'existing_work_requires_reconciliation', task_ids=[t['id'] for t in existing],
                    reason='Existing noncancelled work retained; inspect its state and source binding, never duplicate it.')
            elif not options: item.update(disposition='no_declared_service', reason='Buyer has no matching documentary service.')
            elif not available: item.update(disposition='service_routes_blocked', reason='All declared service routes are blocked.')
            else:
                choice = next((o for o in available if remaining[o['capability']['id']] > 0), None)
                if choice is None: item.update(disposition='waiting_for_internal_review_capacity', reason='Explicit internal review envelope exhausted; this is not a clinical wait-time prediction.')
                else:
                    service = choice['capability']['id']; remaining[service] -= 1
                    item.update(disposition='proposed_internal_review', option_id=choice['id'], service_id=service,
                        owner=choice['capability']['owner'], reason='Deterministic patient/service ID order for documentary review only; no clinical priority inferred.')
        members.append(item)
    members += [{'patient_id': p, 'disposition': 'cohort_membership_unknown', 'clinical_eligibility_established': False} for p in row['selection']['unknown_patient_ids']]
    members += [{'patient_id': p, 'disposition': 'outside_typed_cohort', 'clinical_eligibility_established': False} for p in row['selection']['not_matched_patient_ids']]
    counts = dict(Counter(m['disposition'] for m in members))
    if len(members) != row['selection']['counts']['evaluated'] or len({m['patient_id'] for m in members}) != len(members):
        raise ValueError('Every original roster member must have exactly one disposition')
    inspect(retriever, row['id'])
    if work_capacity.snapshot(retriever.path, row['buyer_id']) != snap:
        raise care.work.WorkConflict('Buyer work changed while preparing the review batch')
    return save_followup(retriever.path, {'schema': POPULATION_ROUTING, 'kind': 'allocation',
        'decision_id': row['id'], 'question': row['question'], 'request': request, 'members': members, 'counts': counts,
        'work_snapshot': snap, 'review_limits': limits, 'assumption_source': source,
        'routing_complete': counts.get('member_routing_missing', 0) == 0,
        'service_capacity': row['service_scope'], 'internal_review_only': True, 'service_slots_reserved': 0,
        'clinical_execution_authorized': False, 'new_paid_api_calls': 0})


def execute_population(retriever, request):
    with _POPULATION_EXECUTION_LOCK:
        return _execute_population(retriever, request)


def _execute_population(retriever, request):
    """Explicit internal batch, resumable through durable per-member work receipts."""
    care.exact(request, 'decision_id allocation_id actor')
    actor = care.text(request['actor'], 'Internal work actor')
    row = inspect(retriever, request['decision_id']); saved = saved_followup(retriever.path, request['allocation_id'])
    if saved.get('schema') != POPULATION_ROUTING or saved.get('kind') != 'allocation' or saved['decision_id'] != row['id']:
        raise ValueError('Exact population allocation required')
    db = database(retriever.path)
    try:
        prior = [json.loads(r[0]) for r in db.execute('SELECT payload FROM question_decision_followups WHERE decision=?', (row['id'],))]
    finally: db.close()
    replay = next((r for r in prior if r.get('schema') == POPULATION_ROUTING and r.get('kind') == 'execution' and r['request'] == request), None)
    if replay: return {**replay, 'idempotent_replay': True}
    current = allocate_population(retriever, saved['request'])
    if current != saved: raise care.work.WorkConflict('Population routes or work changed; prepare a fresh internal batch')
    if not saved['routing_complete']: raise ValueError('Prepare every nominated member route before executing a population batch')
    results = []
    for m in saved['members']:
        if m['disposition'] != 'proposed_internal_review': continue
        try:
            queued = queue_population_member(retriever, {'decision_id': row['id'], 'match_id': m['match_id'],
                'option_id': m['option_id'], 'actor': actor})
            task = queued['task']
            if task['state'] == 'cancelled': raise ValueError('Cancelled work requires explicit review; never silently reopen')
            if task['state'] != 'handoff_prepared_for_review':
                task = event(retriever, {'decision_id': row['id'], 'task_id': task['id'],
                    'expected_revision': task['revision'], 'request_id': digest([saved['result_id'], task['id'], task['revision']]),
                    'action': 'prepare_handoff', 'actor': actor, 'note': 'Population internal documentary review batch; no clinical review or external dispatch.'})['task']
            results.append({'patient_id': m['patient_id'], 'task_id': task['id'], 'state': task['state'], 'revision': task['revision'], 'status': 'prepared'})
        except (ValueError, KeyError, FileNotFoundError) as exc:
            results.append({'patient_id': m['patient_id'], 'status': 'failed', 'reason': str(exc)})
            break  # Already completed members stay durable; remaining members are not claimed executed.
    return save_followup(retriever.path, {'schema': POPULATION_ROUTING, 'kind': 'execution', 'decision_id': row['id'],
        'question': row['question'], 'request': request, 'allocation_id': saved['result_id'], 'results': results,
        'status': 'partial_failure' if any(r['status'] == 'failed' for r in results) else 'internal_batch_prepared',
        'proposed_count': saved['counts'].get('proposed_internal_review', 0),
        'prepared_count': sum(r['status'] == 'prepared' for r in results), 'external_dispatch_performed': False,
        'human_review_completed': False, 'clinical_execution_authorized': False, 'new_paid_api_calls': 0})


POPULATION_ACCOUNTABILITY = 'population-question-accountability/1'


def population_work_binding(path, row):
    """Bind existing question-specific work; same patient alone never joins work."""
    db = database(path)
    try:
        links = [json.loads(r[0]) for r in db.execute(
            'SELECT payload FROM question_decision_tasks WHERE decision=? ORDER BY task', (row['id'],))]
    finally: db.close()
    tasks = []
    for link in links:
        task = care_readiness.task(path, link['task_id'])
        if (link['decision_id'] != row['id'] or link['question'] != row['question']
                or link['selection_digest'] != digest(row['selection'])
                or task['patient_id'] not in row['selection']['patient_ids']
                or any(task[k] != link[k] for k in ('patient_id', 'plan_id', 'option_id'))):
            raise care.work.WorkConflict('Population work binding changed')
        tasks.append({'task': task, 'decision_binding': link})
    return tasks


def population_accountability_inputs(retriever, request):
    from . import program_accountability as A, outcomes as O, conditional_payment as P
    care.exact(request, 'decision_id accountability_contract outcome scenario_id')
    row = inspect(retriever, request['decision_id'])
    if row.get('schema') != POPULATION_VERSION: raise ValueError('Population decision required')
    p = O.validate(request['outcome'], row['decision_date'])
    if p['available_by'] != row['decision_date'] or p['filter'] != 'all':
        raise ValueError('Accountability must retain every selected member and the original decision cutoff')
    ap = A.prepare(retriever.path, {'contract': request['accountability_contract'], 'available_by': row['decision_date']})
    op = O.prepare(retriever.path, p)
    if op['dependency']['snapshot'] != row['selection']['snapshot'] or ap['financial']['base']['snapshot'] != row['selection']['snapshot']:
        raise ValueError('Financial, outcome and population source versions differ')
    tasks = population_work_binding(retriever.path, row)
    if not isinstance(request['scenario_id'], str): raise ValueError('Saved scenario ID or an empty string required')
    scenario = None
    if request['scenario_id']:
        s = P.inspect(retriever.path, request['scenario_id'])
        if not s['current']: raise ValueError('Conditional payment scenario is stale: ' + s['stale_reason'])
        if (s['buyer_id'] != row['buyer_id'] or sorted(s['selected_task_ids']) != sorted(t['task']['id'] for t in tasks)
                or sorted(s['patient_ids']) != sorted({t['task']['patient_id'] for t in tasks})):
            raise ValueError('Payment scenario must bind this buyer and the exact question-specific work population')
        scenario = P.strip(s)
    binding = {'version': POPULATION_ACCOUNTABILITY, 'decision_id': row['id'],
        'selection_digest': digest(row['selection']), 'accountability_identity': ap['identity'],
        'outcome_identity': op['identity'], 'work_digest': digest(tasks),
        'scenario_digest': digest(scenario) if scenario else None}
    return row, ap, op, tasks, scenario, binding


def population_accountability(retriever, request):
    """Compose existing evidence operators in the durable question, with no new store."""
    from collections import Counter
    from . import program_accountability as A, financial_accounts as F, outcomes as O
    row, ap, op, tasks, scenario, binding = population_accountability_inputs(retriever, request)
    ids = sorted(row['selection']['patient_ids'])
    accountability = A.execute(retriever.path, ids, ap['base']['parameters'], prepared=ap,
        selection={'decision_id': row['id'], 'selection_digest': binding['selection_digest']})
    ar = A.load(retriever.path, accountability['id'])
    finance = F.execute(retriever.path, ids, ap['financial']['base']['parameters'], prepared=ap['financial'], preview_limit=0)
    _, _, outcomes = O.execute(retriever.path, ids, request['outcome'], prepared=op, preview_limit=0)
    fdb, odb, adb = F.storage(retriever.path), O.storage(retriever.path), A.storage(retriever.path)
    members = []; statuses = Counter(); account_count = 0
    try:
        if [r['patient_id'] for r in ar['references']] != ids: raise ValueError('Accountability population mismatch')
        for ref in ar['references']:
            pid = ref['patient_id']; a = A.read(adb, 'receipts', ref['receipt_id'])
            f = json.loads(zlib.decompress(fdb.execute('SELECT payload FROM receipts WHERE id=?', (a['financial_receipt_id'],)).fetchone()[0]))
            o = json.loads(zlib.decompress(odb.execute('SELECT payload FROM receipts WHERE id=?', (O.receipt_id(op, pid),)).fetchone()[0]))
            if (digest(a) != ref['receipt_hash'] or digest(f) != a['financial_receipt_hash']
                    or any(r['patient_id'] != pid for r in (a, f, o))):
                raise ValueError('Same-patient immutable accountability evidence required')
            owned = [t for t in tasks if t['task']['patient_id'] == pid]
            statuses.update([o['status']]); account_count += len(f['accounts'])
            members.append({'patient_id': pid, 'accountability': ref,
                'financial_receipt_id': f['id'], 'financial_receipt_hash': digest(f),
                'outcome_receipt_id': o['id'], 'outcome_receipt_hash': digest(o),
                'outcome_status': o['status'], 'recorded_label': o['recorded_label'],
                'calendar_mature': o['calendar_mature'], 'clinical_outcome_established': False,
                'account_ids': [a['account_id'] for a in f['accounts']],
                'financial_status_counts': f['status_counts'], 'unassigned_source_count': len(f['unassigned_sources']),
                'requirements': [{k: r[k] for k in ('id', 'scope', 'status', 'needed', 'semantic_validity_established')} for r in a['requirements']],
                'task_ids': [t['task']['id'] for t in owned],
                'work_outcome_link': 'not_established', 'work_payment_link': 'not_established',
                'link_reason': 'Same-patient source observations are context only. Delivered program service, contract attribution and causal or payment links have not been established.'})
    finally: fdb.close(); odb.close(); adb.close()
    if dict(statuses) != outcomes['status_counts'] or account_count != finance['accounts_evaluated']:
        raise ValueError('Population evidence denominators do not reconcile')
    # Cache counters and wall time are observations, not result identity.
    stable = lambda r: {k: v for k, v in r.items() if k not in ('processing_counts', 'financial_processing_counts', 'input_fingerprints', 'elapsed_seconds')}
    result = {'schema': POPULATION_ACCOUNTABILITY, 'decision_id': row['id'], 'question': row['question'],
        'request': request, 'binding': binding, 'program': row['program'], 'selection': row['selection'],
        'snapshot': row['selection']['snapshot'], 'members': members, 'work': tasks,
        'accountability': stable(accountability), 'financial': stable(finance), 'outcomes': stable(outcomes),
        'scenario': scenario, 'scenario_status': 'supplied_hypothetical_terms_only' if scenario else 'not_supplied',
        'payment_readiness': 'unresolved', 'attributable_program_payment_cents': None,
        'causal_savings_cents': None, 'clinical_benefit_established': False, 'observed_amounts_netted': False,
        'all_accounts_unlinked_to_program_work': account_count, 'work_items_with_unestablished_outcome_link': len(tasks),
        'limits': ['All selected members remain in the declared observation denominator; nonselected and unknown cohort members remain in the saved selection.',
            'Received snapshots, cash reports and dated observations are separate quantities. None establish a program outcome, entitlement or realized savings.',
            'Versioned development prerequisites are not an installed payer contract. Coverage, care-team labels and internal handoffs cannot supply attribution.',
            'Outcome index dates are explicit historical observation dates, not intervention dates. Missing observations are not negative clinical outcomes.',
            'Saved internal work state is not delivered care or completed human review. Conditional scenarios, if supplied, remain separate from received amounts.'],
        'new_paid_api_calls': 0, 'external_actions': 0, 'tasks_changed': 0}
    if population_accountability_inputs(retriever, request)[-1] != binding:
        raise care.work.WorkConflict('Population evidence or work changed during reconciliation')
    return save_followup(retriever.path, result)


def open_population_accountability(retriever, request):
    from . import program_accountability as A, financial_accounts as F, outcomes as O
    care.exact(request, 'decision_id result_id patient_id')
    report = saved_followup(retriever.path, request['result_id'])
    if report.get('schema') != POPULATION_ACCOUNTABILITY or report['decision_id'] != request['decision_id']:
        raise ValueError('Exact population accountability report required')
    if not isinstance(request['patient_id'], str): raise ValueError('Member ID or empty string required')
    member = next((m for m in report['members'] if m['patient_id'] == request['patient_id']), None)
    if request['patient_id'] and member is None: raise ValueError('Member outside the saved population')
    current = True; reason = None; evidence = None
    try:
        if population_accountability_inputs(retriever, report['request'])[-1] != report['binding']:
            raise care.work.WorkConflict('Source, definitions, work or scenario changed; reconcile again')
        proposal = A.load(retriever.path, report['accountability']['id'])
        if proposal['references'] != [m['accountability'] for m in report['members']]:
            raise ValueError('Accountability member references changed')
        # Check all saved dependent receipts, even when opening only a summary.
        # Keep historical report bytes inspectable if a dependency is corrupted.
        adb, fdb, odb = A.storage(retriever.path), F.storage(retriever.path), O.storage(retriever.path)
        try:
            for m in report['members']:
                a = A.read(adb, 'receipts', m['accountability']['receipt_id'])
                if digest(a) != m['accountability']['receipt_hash']:
                    raise ValueError('Accountability receipt integrity changed')
                for db, prefix in ((fdb, 'financial'), (odb, 'outcome')):
                    raw = db.execute('SELECT payload FROM receipts WHERE id=?', (m[prefix+'_receipt_id'],)).fetchone()
                    if raw is None or digest(json.loads(zlib.decompress(raw[0]))) != m[prefix+'_receipt_hash']:
                        raise ValueError('Saved patient evidence integrity changed')
        finally: adb.close(); fdb.close(); odb.close()
        if member:
            a = A.inspect(retriever.path, report['accountability']['id'], patient=member['patient_id'])
            o = O.inspect(retriever.path, member['outcome_receipt_id'])
            if (digest(a['financial_evidence']) != member['financial_receipt_hash'] or digest(o) != member['outcome_receipt_hash']):
                raise ValueError('Saved patient evidence integrity changed')
            evidence = {'accountability': a, 'outcome': o, 'work': [t for t in report['work'] if t['task']['id'] in member['task_ids']]}
    except (ValueError, KeyError, FileNotFoundError) as exc:
        current = False; reason = str(exc)
    return {'report': report, 'current': current, 'stale_reason': reason, 'member_evidence': evidence, 'new_paid_api_calls': 0}


POPULATION_PREDICTION = 'population-question-prediction/1'


def prediction_contracts(retriever, request):
    from . import outcomes as O, decision_landmarks as D
    care.exact(request, 'decision_id')
    row = inspect(retriever, request['decision_id'])
    if row.get('schema') != POPULATION_VERSION: raise ValueError('Population decision required')
    return {'contracts': [{'adapter': a, 'id': s['id'], 'title': s['title']}
        for a, definitions in (('index_day', O.definitions()), ('receipt_day', D.definitions()))
        for s in definitions.values()], 'available_by': row['decision_date']}


def prediction_inputs(retriever, request):
    from . import outcomes as O, baseline_evidence as B, decision_landmarks as D, prediction_registry as R
    care.exact(request, 'decision_id adapter contract start through')
    row = inspect(retriever, request['decision_id'])
    if row.get('schema') != POPULATION_VERSION: raise ValueError('Population decision required')
    common = {'contract': request['contract'], 'available_by': row['decision_date']}
    if request['adapter'] == 'index_day':
        p = {**common, 'index_from': request['start'], 'index_through': request['through'], 'filter': 'all'}
        op = O.prepare(retriever.path, p); prep = B.prepare(retriever.path, {'profile': 'received_prior_180d'}, op)
        spec = op['contract']; profile = prep['profile']['id']
    elif request['adapter'] == 'receipt_day':
        p = {**common, 'receipt_from': request['start'], 'receipt_through': request['through']}
        prep = D.prepare(retriever.path, p); spec = prep['contract']; profile = spec['profile']
    else: raise ValueError('Registered prediction clock adapter required')
    if prep['base']['snapshot'] != row['selection']['snapshot']:
        raise ValueError('Prediction and selected population snapshots differ')
    # The registry exposes only already-qualified aggregate reports. No private
    # split rows, new scoring, fitting, model loading or target acquisition here.
    try:
        registry = R.status(retriever.path)
        same_task = [r for r in registry['reports'] if r.get('task', {}).get('adapter') == request['adapter']
            and r.get('task', {}).get('contract') == request['contract']]
        exact = [r for r in same_task if r.get('current') and r['task']['parameters'] == p
            and r['task']['profile'] == profile and r['dataset_identity'] == prep['identity']]
        if len(exact) > 1: raise ValueError('Ambiguous prediction qualification binding')
        qualification = {'status': 'exact_task_evidence_match' if exact else 'no_current_exact_task_report',
            'report': exact[0] if exact else None,
            'other_scope_reports': [{'id': r.get('id'), 'current': r.get('current'), 'parameters': r.get('task', {}).get('parameters'),
                'reason': r.get('reason') or 'Parameters or full evidence identity differ'} for r in same_task if r not in exact],
            'scope': 'Saved development-build aggregate, not selected-cohort performance or patient risk. No new evaluation or fitting.'}
    except (ValueError, KeyError, OSError) as exc:
        qualification = {'status': 'registry_unavailable', 'report': None, 'reason': str(exc)}
    tasks = population_work_binding(retriever.path, row)
    binding = {'version': POPULATION_PREDICTION, 'decision_id': row['id'], 'selection_digest': digest(row['selection']),
        'evidence_identity': prep['identity'], 'task': {'adapter': request['adapter'], 'parameters': p, 'profile': profile},
        'qualification_digest': digest(qualification), 'work_digest': digest(tasks)}
    return row, prep, spec, qualification, tasks, binding


def prediction_receipts(path, adapter, members):
    """Read exact immutable receipts in one connection per existing store."""
    from . import baseline_evidence as B, decision_landmarks as D, outcomes as O
    db = (B if adapter == 'index_day' else D).storage(path)
    odb = O.storage(path) if adapter == 'index_day' else None
    try:
        for m in members:
            raw = db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?', (m['baseline_receipt_id'], m['patient_id'])).fetchone()
            if raw is None: raise ValueError('Missing saved prediction baseline')
            row = json.loads(zlib.decompress(raw[0]))
            target = row
            if odb is not None:
                raw = odb.execute('SELECT payload FROM receipts WHERE id=? AND patient=?', (m['outcome_receipt_id'], m['patient_id'])).fetchone()
                if raw is None: raise ValueError('Missing saved prediction outcome')
                target = json.loads(zlib.decompress(raw[0]))
            if row['patient_id'] != m['patient_id'] or target['patient_id'] != m['patient_id']:
                raise ValueError('Prediction receipt belongs to another patient')
            if 'baseline_receipt_hash' in m and (digest(row) != m['baseline_receipt_hash'] or digest(target) != m['outcome_receipt_hash']):
                raise ValueError('Prediction source receipt integrity changed')
            yield m, row, target
    finally:
        db.close()
        if odb is not None: odb.close()


def population_prediction(retriever, request):
    from collections import Counter
    from . import baseline_evidence as B, outcomes as O, decision_landmarks as D, outcome_readiness as Q
    row, prep, spec, qualification, tasks, binding = prediction_inputs(retriever, request)
    p = binding['task']['parameters']; ids = sorted(row['selection']['patient_ids']); adapter = request['adapter']
    if adapter == 'index_day':
        O.execute(retriever.path, ids, p, prepared=prep['outcome'], preview_limit=0)
        B.execute(retriever.path, ids, {'profile': binding['task']['profile']}, prepared=prep, preview_limit=0)
        refs = [{'patient_id': pid, 'baseline_receipt_id': B.receipt_id(prep, pid),
                 'outcome_receipt_id': O.receipt_id(prep['outcome'], pid)} for pid in ids]
    else:
        D.execute(retriever.path, ids, p, prepared=prep, preview_limit=0)
        refs = [{'patient_id': pid, 'baseline_receipt_id': D.receipt_id(prep, pid),
                 'outcome_receipt_id': D.receipt_id(prep, pid)} for pid in ids]
    members = []; counts = Counter(); labels = Counter(); reasons = Counter()
    for ref, raw, target in prediction_receipts(retriever.path, adapter, refs):
        baseline = raw if adapter == 'index_day' else raw['baseline']
        if adapter == 'index_day': readiness = Q.assess(baseline, target)
        else:
            # Same public timing contract as the frozen receipt-day export.
            excluded = []
            if not target['decision']: excluded.append(target['status'])
            elif target['decision']['date'] > p['available_by']: excluded.append('decision_after_available_cutoff')
            else:
                if not target['calendar_mature']: excluded.append('incomplete_calendar_followup')
                if target['recorded_label'] is None: excluded.append('unresolved_recorded_target')
            if baseline['status'] != 'baseline_observations_available': excluded.append('no_baseline_observations')
            if target['decision'] and baseline['cutoff'] != target['decision']['feature_cutoff']: excluded.append('invalid_decision_feature_cutoff')
            readiness = {'timing_eligible_observed_target': not excluded, 'exclusion_reasons': excluded}
        eligible = readiness['timing_eligible_observed_target']; reasons.update(readiness['exclusion_reasons'])
        if eligible: labels[str(target['recorded_label'])] += 1
        counts[target['status']] += 1
        members.append({**ref, 'baseline_receipt_hash': digest(raw), 'outcome_receipt_hash': digest(target),
            'feature_cutoff': baseline['cutoff'], 'baseline_status': baseline['status'],
            'outcome_status': target['status'], 'recorded_label': target['recorded_label'],
            'calendar_mature': target['calendar_mature'], 'readiness': readiness,
            'task_ids': [t['task']['id'] for t in tasks if t['task']['patient_id'] == ref['patient_id']],
            'patient_risk': None, 'clinical_negative_established': False})
    report = qualification['report']; gaps = []
    if not ids: gaps.append('No selected population members.')
    if set(labels) != {'0', '1'}: gaps.append('Selected timing-eligible received targets lack both recorded classes; a discriminative comparison is not estimable.')
    if report is None: gaps.append('No current model-build report matches this exact task, dates and evidence identity.')
    elif not report['fit_input_contract_ready']: gaps += report['readiness_gaps']
    gaps.append('No model is qualified for clinical prediction. Source capture, representative evaluation and independent clinical review remain unestablished.')
    # Counts and gates are computed from current received source receipts. Saved
    # model qualification never overwrites population membership or patient facts.
    result = {'schema': POPULATION_PREDICTION, 'decision_id': row['id'], 'question': row['question'],
        'followup_question': 'Can received evidence support prediction of '+spec['title']+' for this saved population?',
        'request': request, 'binding': binding, 'selection': row['selection'], 'task_definition': spec,
        'profile': B.definition(binding['task']['profile']), 'members': members, 'work': tasks,
        'patients_assessed': len(ids), 'status_counts': dict(counts), 'timing_eligible': sum(labels.values()),
        'eligible_recorded_label_counts': dict(labels), 'exclusion_reason_counts': dict(reasons), 'exclusion_counts_overlap': True,
        'qualification': qualification, 'readiness_gaps': gaps, 'status': 'estimate_unavailable',
        'baseline_comparison': {'method': 'constant_training_prevalence',
            'status': 'not_fitted' if report and report['status'] == 'not_fitted' else 'no_qualified_scoped_estimate',
            'selected_population_risk': None, 'saved_build_comparison': report['model_report'] if report else None,
            'meaning': 'A missing or single-class eligible target is not zero clinical risk. Any saved scores describe only their frozen development population.'},
        'routing': {'method': 'local_received_evidence_and_exact_qualification', 'new_inference_requests': 0,
            'new_model_fits': 0, 'new_evaluation_scores': 0, 'fallback': 'explicit_nonestimability_and_review_gaps',
            'reason': 'Additional inference cannot supply missing outcomes, protected class support or clinical qualification.'},
        'review_burden': {'members_with_timing_gaps': sum(not m['readiness']['timing_eligible_observed_target'] for m in members),
            'existing_question_work_items': len(tasks), 'human_review_minutes': None, 'reviews_completed': 0,
            'scope': 'Documentary review gaps, not required outreach, clinical priority or staffing estimates.'},
        'cost': {'new_api_cost_usd': 0, 'local_compute_cost_usd': None, 'human_review_cost_usd': None, 'total_cost_usd': None},
        'clinical_prediction_authorized': False, 'model_promoted': False, 'tasks_changed': 0, 'external_actions': 0,
        'limits': B.LIMITS + (D.LIMITS if adapter == 'receipt_day' else Q.summarize([])['further_training_requirements'])}
    if prediction_inputs(retriever, request)[-1] != binding:
        raise care.work.WorkConflict('Prediction evidence, qualification or work changed during assembly')
    return save_followup(retriever.path, result)


def open_population_prediction(retriever, request):
    care.exact(request, 'decision_id result_id patient_id')
    report = saved_followup(retriever.path, request['result_id'])
    if report.get('schema') != POPULATION_PREDICTION or report['decision_id'] != request['decision_id']:
        raise ValueError('Exact population prediction report required')
    if not isinstance(request['patient_id'], str): raise ValueError('Member ID or empty string required')
    if request['patient_id'] and request['patient_id'] not in report['selection']['patient_ids']:
        raise ValueError('Member outside saved population')
    evidence = None; current = True; reason = None
    try:
        if prediction_inputs(retriever, report['request'])[-1] != report['binding']:
            raise care.work.WorkConflict('Prediction source, qualification or work changed; assess again')
        for m, baseline, outcome in prediction_receipts(retriever.path, report['request']['adapter'], report['members']):
            if m['patient_id'] == request['patient_id']:
                evidence = {'member': m, 'baseline': baseline, 'outcome': outcome,
                    'work': [t for t in report['work'] if t['task']['id'] in m['task_ids']]}
    except (ValueError, KeyError, OSError) as exc: current = False; reason = str(exc); evidence = None
    return {'report': report, 'current': current, 'stale_reason': reason, 'member_evidence': evidence, 'new_paid_api_calls': 0}
