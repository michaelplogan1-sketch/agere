"""Shared graph origins for the existing question/service/work lifecycle.

No source answer is fabricated. Population receipts reference every evaluated
member; only an explicitly opened member loads its evidence and service context.
"""
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import sqlite3
import re
import zlib

from ...contracts import digest
from . import question_decision as Q, source_state as state, pre_event_state as temporal
from . import interpretation_state as interpretation, outcomes, graph_criteria as criteria, graph_reports as reports, graph_references as references

VERSION = 'graph-question-decision/1'
MEMBER = 'graph-member-decision/1'
EVIDENCE = 'graph-member-evidence/1'
AUTHORITY = 'documentary_review_only_not_clinical_need'
STATE_OPERATIONS = ('source_state', 'pre_event_state')
OPERATIONS = STATE_OPERATIONS + criteria.OPERATIONS + reports.OPERATIONS + (references.OPERATION,)


def identity():
    return digest([VERSION, MEMBER, EVIDENCE, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), criteria.identity(), reports.identity(), references.identity()])


def member_index(path, operation, patients, parameters, snapshot, anchor=None):
    projection = {k:v for k,v in parameters.items() if k != 'filter'}
    code = temporal.code_identity() if operation == 'pre_event_state' else state.code_identity()
    interp_code = interpretation.code_identity()
    db = temporal.storage(path) if anchor else state.storage(path)
    table = 'pre_event_states' if anchor else 'source_states'
    result = []
    try:
        for pid in patients:
            iid = state.receipt_id(snapshot, pid, interpretation_code=interp_code)
            parts = [code, iid] + ([outcomes.receipt_id(anchor, pid)] if anchor else []) + [projection]
            key = digest(parts)
            row = json.loads(zlib.decompress(db.execute('SELECT payload FROM '+table+' WHERE id=?', (key,)).fetchone()[0]))
            result.append({'patient_id':pid, 'receipt_id':key, 'receipt_digest':digest(row),
                'status':row['status'], 'value':row['value'], 'membership':state.membership(row, parameters['filter']),
                'care_account_counts':row['care_account_counts']})
    finally:
        db.close()
    return result


def graph_receipt(path, key):
    if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key):
        raise ValueError('Exact saved graph result required')
    with sqlite3.connect(Path(path)/'stack.sqlite3') as db:
        found = db.execute('SELECT payload FROM result_cache WHERE id=?', (key,)).fetchone()
    if not found: raise ValueError('Unknown saved graph result')
    row = json.loads(found[0])
    if row.get('result_id') != key or not row.get('question'):
        raise ValueError('Reanswer the original question to bind this graph before creating work')
    return row


def source_fingerprint(path, graph):
    """Exact evaluated source inventory, including newly arrived/unmapped rows.

    Metadata identity alone cannot detect in-place source changes. This check
    makes the immutable graph handoff stale; it does not silently revise a saved
    interpretation or claim that an old derived cache has been recomputed.
    """
    scopes = {}
    for item in graph['results']:
        if item['node']['operation'] not in STATE_OPERATIONS: continue
        for stream in item['node']['parameters']['streams']:
            scopes.setdefault(stream, set()).update(m['patient_id'] for m in item['result']['review_members'])
    witness = hashlib.sha256(); count = 0
    with sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        for stream, patients in sorted(scopes.items()):
            for rid, pid, raw in db.execute('SELECT id,patient,payload FROM records WHERE stream=? ORDER BY id', (stream,)):
                if pid not in patients: continue
                witness.update(json.dumps([stream,rid,pid,digest(json.loads(raw))],separators=(',',':')).encode())
                witness.update(b'\n'); count += 1
    return {'sha256':witness.hexdigest(),'records':count}


def dependencies(path, graph):
    """Relevant current source/review, endpoint and full roster dependencies."""
    from . import analysis_graph, evidence_threads
    db, meta = evidence_threads.connect(path)
    try: roster = sorted(r[0] for r in db.execute('SELECT id FROM patients'))
    finally: db.close()
    sources = {}; anchors = {}
    for item in graph['results']:
        node = item['node']
        if node['operation'] in STATE_OPERATIONS:
            sources[node['id']] = interpretation.snapshot(path, state.scope(node['parameters']))['identity']
        if node['operation'] == 'outcome':
            anchors[node['id']] = outcomes.prepare(path, node['parameters'])['identity']
    return {'graph_code':analysis_graph.code_identity(), 'adapter':identity(),
        'snapshot':meta['snapshot'], 'source_digest':meta['source_digest'],
        'roster_digest':digest(roster), 'roster_count':len(roster),
        'source_states':sources, 'outcomes':anchors, 'criteria':criteria.dependencies(path,graph), 'reports':reports.dependencies(path,graph), 'references':references.dependencies(path,graph),
        'evaluated_source_inventory':source_fingerprint(path,graph)}


def save(path, row):
    row['id'] = digest(row)
    db = Q.database(path)
    try:
        with db: db.execute('INSERT OR IGNORE INTO question_decisions VALUES (?,?)', (row['id'],json.dumps(row,allow_nan=False)))
    finally: db.close()
    return row


def prepare(stack, request):
    Q.care.exact(request, 'result_id buyer_id')
    graph = graph_receipt(stack.path, request['result_id'])
    # Execute the *saved* plan, never a new model plan or caller-provided scope.
    if any(n['operation'] in ('review','assertion_review') for n in graph['graph']['nodes']):
        raise ValueError('A deterministic graph is required for this review handoff; model-review graph origins remain unsupported')
    from .analysis_graph import execute
    current = execute(stack, graph['graph'], question=graph['question'])
    if current['result_id'] != graph['result_id']:
        raise Q.care.work.WorkConflict('Graph dependencies changed; reanswer the original question')
    port = Q.care.portfolio(stack.path, Q.care.text(request['buyer_id'], 'buyer ID'))
    dep = dependencies(stack.path, graph)
    if dep['evaluated_source_inventory'] != graph.get('review_source_fingerprint'):
        raise Q.care.work.WorkConflict('Graph source inventory changed; rebuild from the current versioned source snapshot before review')
    if dep.get('criteria') and dep['criteria'] != graph.get('review_criteria_dependencies'):
        raise Q.care.work.WorkConflict('Criterion sources or rules changed; reexecute the original graph against the current versioned source analysis')
    if dep.get('reports') and dep['reports'] != graph.get('review_report_dependencies'):
        raise Q.care.work.WorkConflict('Analytical sources or definitions changed; reexecute the saved question')
    if dep.get('references') and dep['references'] != graph.get('review_reference_dependencies'):
        raise Q.care.work.WorkConflict('Scientific reference changed; reexecute the saved question')
    members = {}; nodes = []
    for item in graph['results']:
        n, r = item['node'], item['result']
        if n['operation'] not in OPERATIONS: continue
        index = r.get('review_members')
        if index is None: raise ValueError('Reanswer this question to bind complete graph membership')
        ids = [x['patient_id'] for x in index]
        if len(ids) != len(set(ids)) or len(ids) != r['patients_evaluated'] or digest(sorted(ids)) != r['population_digest']:
            raise ValueError('Graph population does not reconcile to its saved denominator')
        nodes.append({'node':n, 'population_digest':r['population_digest'], 'patients_evaluated':r['patients_evaluated'],
            'input_membership':r['input_membership'], 'care_account_counts':r.get('care_account_counts',{}),
            'care_report_counts':r.get('care_report_counts',{}), 'anchor_parameters':r.get('anchor_parameters'),
            'status_counts':r.get('member_status_counts',r.get('status_counts')), 'specification':r.get('protocol',r.get('contract',r.get('package'))),
            'report_summary':reports.summary(n['operation'],r) if n['operation'] in reports.OPERATIONS else ({'title':r['reference']['title'],'reference':r['reference'],'lifecycle':r['lifecycle'],'linked_patient_counts_by_question':r['linked_patient_counts_by_question'],'unresolved_questions':r['unresolved_questions']} if n['operation']==references.OPERATION else None)})
        for m in index:
            members.setdefault(m['patient_id'], []).append({'node_id':n['id'], 'operation':n['operation'], **m})
    if not nodes: raise ValueError('A source, criterion or analytical evidence graph is required')
    service_scope = []
    knowledge = Q.care.catalog(); pathways = {p['id']:p for p in knowledge['pathways']}
    cov = Q.population_coverage(dep['snapshot']['as_of'])
    member_tags = {pid:review_tags(refs) for pid,refs in members.items()}
    for cap in port['capabilities']:
        ids = sorted(pid for pid,tags in member_tags.items() if set(tags) & set(cap['need_tags']))
        routes = [Q.care.route_check(cap,pathways[p],cov,knowledge) for p in cap['pathway_ids']] or [Q.care.route_check(cap,None,cov,knowledge)]
        service_scope.append({'capability':cap,'documentary_candidate_ids':ids,'candidate_count':len(ids),
            'routes':routes,'status':'blocked' if all(r['status']=='blocked' for r in routes) else 'requires_verification',
            'service_slots_reserved':0,'clinical_eligibility_established':False})
    row = {'schema':VERSION, 'request':request, 'question':graph['question'], 'graph_result_id':graph['result_id'],
        'graph_digest':digest(graph), 'graph':graph['graph'], 'answer_coverage':graph['answer_coverage'],
        'buyer_id':request['buyer_id'], 'portfolio_digest':digest(port), 'dependencies':dep, 'nodes':nodes,
        'service_scope':service_scope, 'members':[{'patient_id':pid,'states':refs} for pid,refs in sorted(members.items())],
        'population':{'evaluated':len(members), 'roster':dep['roster_count'], 'evaluated_digest':digest(sorted(members)),
            'meaning':'Union of evaluated source and criterion populations; node-specific predicates, unknown memberships and excluded people remain separate.'},
        'authority':AUTHORITY, 'service_mapping':'Literal care requests and existing pinned protocol review declarations nominate documentary services. Unsupported criterion mappings remain explicit; no eligibility or benefit is inferred.',
        'new_paid_api_calls':0, 'clinical_execution_authorized':False, 'external_actions_executed':0}
    if dependencies(stack.path, graph) != dep: raise Q.care.work.WorkConflict('Graph dependencies changed during preparation')
    return save(stack.path, row)


def inspect(path, row):
    graph = graph_receipt(path, row['graph_result_id'])
    if digest(graph) != row['graph_digest'] or dependencies(path, graph) != row['dependencies']:
        raise Q.care.work.WorkConflict('Graph sources, scope, interpretation or rules changed; reanswer the original question')
    if digest(Q.care.portfolio(path, row['buyer_id'])) != row['portfolio_digest']:
        raise Q.care.work.WorkConflict('Buyer services changed; prepare a new decision')
    return row


def evidence(path, row, patient):
    member = next((m for m in row['members'] if m['patient_id'] == patient), None)
    if member is None: raise ValueError('Patient is outside this exact graph population')
    findings = []; records = {}; gaps = []
    for ref in member['states']:
        if ref['operation']==references.OPERATION:
            node=next(n['node'] for n in row['nodes'] if n['node']['id']==ref['node_id'])
            finding,missing=references.evidence(path,ref,node,member['states'])
            findings.append(finding);gaps.extend(missing)
            continue
        if ref['operation'] in reports.OPERATIONS:
            node=next(n['node'] for n in row['nodes'] if n['node']['id']==ref['node_id'])
            finding,refs,missing=reports.evidence(path,ref,node)
            findings.append(finding);records.update(refs);gaps.extend(missing)
            continue
        if ref['operation'] in criteria.OPERATIONS:
            node=next(n['node'] for n in row['nodes'] if n['node']['id']==ref['node_id'])
            finding,refs,missing=criteria.evidence(path,ref,node)
            findings.append(finding);records.update(refs);gaps.extend(missing)
            continue
        r = (temporal if ref['operation']=='pre_event_state' else state).inspect(path, ref['receipt_id'])
        bare = {k:v for k,v in r.items() if k not in ('interpretation','outcome')}
        if digest(bare) != ref['receipt_digest'] or r['patient_id'] != patient:
            raise Q.care.work.WorkConflict('Graph member evidence integrity changed')
        # Keep all scoped received sources (including unmapped units) distinct
        # from selected/pending/excluded interpretations and timing dependencies.
        for rid, source in r['interpretation']['sources'].items():
            if source['patient_id'] != patient: raise ValueError('Cross-patient graph source')
            records[rid] = source['record_hash']
        finding = {'node_id':ref['node_id'], 'operation':ref['operation'], 'receipt_id':ref['receipt_id'],
            'receipt_digest':ref['receipt_digest'], 'parameters':r['parameters'], 'membership':ref['membership'],
            **{k:r[k] for k in ('status','value','selected_date','date_basis','care_accounts','care_account_counts',
                'selected_assertions','pending_assertions','excluded_assertions','native_mapping_gaps','source_review_problems','source_coverage','next_evidence_steps')},
            'anchor':r.get('anchor'), 'temporal_scope':r.get('temporal_scope'), 'authority':AUTHORITY}
        findings.append(finding)
        for account in r['care_accounts']:
            if account['status'] == 'performed_service_documented': continue
            gaps.append({'node_id':ref['node_id'], 'receipt_id':ref['receipt_id'], 'request_id':account['request_id'],
                'status':account['status'], 'service_concept':account['service_concept'],
                'request_record_hash':records.get(account['request_id']),
                'requested_action':'review_request_meaning' if account['status']=='request_meaning_unresolved' else 'review_request_completion_evidence',
                'target_record_id':None, 'meaning':'No absent record ID or clinical nonattendance is inferred.',
                'temporal_scope':r.get('temporal_scope'), 'parameters':r['parameters']})
        if r.get('anchor', {}).get('problem') or r['pending_assertions'] or r['native_mapping_gaps'] or r['source_review_problems']:
            gaps.append({'node_id':ref['node_id'], 'receipt_id':ref['receipt_id'], 'request_id':None,
                'status':'source_meaning_or_timing_unresolved', 'target_record_id':None,
                'requested_action':'review_bound_interpretation', 'temporal_scope':r.get('temporal_scope'),
                'parameters':r['parameters']})
    return {'schema':EVIDENCE, 'decision_id':row['id'], 'question':row['question'], 'patient_id':patient,
        'graph_result_id':row['graph_result_id'], 'graph_digest':row['graph_digest'],
        'population':row['population'], 'nodes':row['nodes'], 'findings':findings, 'evidence_gaps':gaps,
        'source_versions':{'snapshot':row['dependencies']['snapshot'], 'records':records}, 'authority':AUTHORITY,
        'new_paid_api_calls':0, 'clinical_execution_authorized':False}


def origin(path, request):
    Q.care.exact(request, 'kind id finding_ids')
    saved = Q.saved_followup(path, request['id'])
    if saved.get('schema') != EVIDENCE or request['finding_ids']:
        raise ValueError('Exact graph member evidence with no invented answer finding IDs required')
    parent = inspect(path, Q.load(path, saved['decision_id']))
    current = evidence(path, parent, saved['patient_id'])
    if {k:v for k,v in saved.items() if k!='result_id'} != current:
        raise Q.care.work.WorkConflict('Graph member evidence changed')
    return {'kind':'graph_member','id':saved['result_id'],'hash':digest(saved),'patient_id':saved['patient_id'],
        'question':saved['question'],'graph_result_id':saved['graph_result_id'], 'evidence':saved,'authority':AUTHORITY}


def install():
    """Same origin extension point used by the existing retirement guard.

    Only the explicit graph_member kind is handled; frozen answer/assessment
    matching, task transactions, readiness and participation contracts are reused.
    """
    if getattr(Q.care.origin, 'graph_origin_guard', False): return
    original = Q.care.origin
    def guarded(path, request):
        return origin(path, request) if isinstance(request,dict) and request.get('kind')=='graph_member' else original(path,request)
    guarded.graph_origin_guard = True
    Q.care.origin = guarded


def review_tags(refs):
    tags=set()
    for ref in refs:
        if ref.get('membership') is not True:continue
        if ref.get('care_accounts') or sum(ref.get('care_account_counts',{}).values()):tags.add('care_navigation')
        tags.update(t['need_tag'] for t in ref.get('review_topics',[]))
    return sorted(tags)


def refresh(stack, request):
    """Reexecute the saved question/plan against current versioned dependencies."""
    Q.care.exact(request,'decision_id')
    old=Q.load(stack.path,request['decision_id'])
    if old['schema']!=VERSION:raise ValueError('Graph population decision required')
    from .analysis_graph import execute
    graph=execute(stack,old['graph'],question=old['question'])
    current=prepare(stack,{'result_id':graph['result_id'],'buyer_id':old['buyer_id']})
    before={(m['patient_id'],r['node_id']):r for m in old['members'] for r in m['states']}
    after={(m['patient_id'],r['node_id']):r for m in current['members'] for r in m['states']}
    changes=[{'patient_id':pid,'node_id':node,'before':before.get((pid,node)), 'after':after.get((pid,node))}
             for pid,node in sorted(set(before)|set(after)) if before.get((pid,node))!=after.get((pid,node))]
    return Q.save_followup(stack.path,{'schema':'graph-decision-recomputation/1','decision_id':old['id'],
        'question':old['question'],'previous_result_id':old['graph_result_id'],'current_result_id':graph['result_id'],
        'current_decision_id':current['id'],'changes':changes,'changed_member_count':len({x['patient_id'] for x in changes}),
        'source_or_rule_change_is_not_clinical_change':True,'task_state_changed':False,'external_actions_executed':0,
        'new_paid_api_calls':graph['new_paid_api_calls']})


def open_member(retriever, request):
    Q.care.exact(request, 'decision_id patient_id')
    row = inspect(retriever.path, Q.load(retriever.path, request['decision_id']))
    if row['schema'] != VERSION: raise ValueError('Graph population decision required')
    bound = evidence(retriever.path, row, request['patient_id'])
    saved = Q.save_followup(retriever.path, bound)
    inventory = Q.received_inventory(retriever.path, request['patient_id'])
    if any(inventory['records'].get(k,{}).get('record_hash')!=v for k,v in bound['source_versions']['records'].items()):
        raise Q.care.work.WorkConflict('Member sources changed during review preparation')
    # Source-type baseline: review is distinct from a clinical intervention.
    tags = review_tags(bound['findings'])
    matching = None
    if tags:
        install()
        ctx = Q.patient_context.prepare(retriever.path, {'patient_id':request['patient_id'],'as_of':inventory['snapshot']['as_of']})
        matching = Q.patient_context.prepare_match(retriever.path, {'buyer_id':row['buyer_id'],
            'origin':{'kind':'graph_member','id':saved['result_id'],'finding_ids':[]},
            'need_tags':tags, 'rationale':'Existing pinned protocol review declarations and literal request evidence nominate documentary review for the original question. Criterion, clinical need, program eligibility and service prerequisites remain separate.',
            'coverage':Q.population_coverage(inventory['snapshot']['as_of']), 'patient_context_id':ctx['id']})
    result = {'schema':MEMBER, 'parent_decision_id':row['id'], 'evidence_id':saved['result_id'],
        **{k:bound[k] for k in ('question','patient_id','graph_result_id','graph_digest','population','nodes','findings','evidence_gaps','source_versions','authority')},
        'buyer_id':row['buyer_id'], 'received_inventory':inventory,
        'analysis_window':{'basis':'declared_context','start':'','end':''},
        'analysis_window_meaning':'Use each bound node parameter and temporal_scope; this marker does not widen historical graph evidence.',
        'decision_date':inventory['snapshot']['as_of'], 'matching':matching,
        'service_selection':{'method':'pinned_protocol_or_literal_request_documentary_review','need_tags':tags},
        'status':'review_options_prepared' if matching and matching['options'] else 'no_supported_service_option',
        'rule_versions':{'graph_adapter':identity(),'service_matching':Q.care.identity()},
        'clinical_execution_authorized':False, 'new_paid_api_calls':0}
    inspect(retriever.path,row)
    if Q.received_inventory(retriever.path,request['patient_id']) != inventory:
        raise Q.care.work.WorkConflict('Received inventory changed during graph handoff')
    return save(retriever.path,result)


def inspect_member(retriever, row):
    install()
    if row['rule_versions'] != {'graph_adapter':identity(),'service_matching':Q.care.identity()}:
        raise Q.care.work.WorkConflict('Graph decision rules changed')
    origin(retriever.path, {'kind':'graph_member','id':row['evidence_id'],'finding_ids':[]})
    if Q.received_inventory(retriever.path,row['patient_id']) != row['received_inventory']:
        raise Q.care.work.WorkConflict('Received evidence changed; reanswer the original graph question')
    if row['matching']:
        current = Q.care.inspect_plan(retriever.path,row['matching']['id'])
        if any(row['matching'].get(k)!=v for k,v in current.items()):
            raise Q.care.work.WorkConflict('Graph service matching changed')
    return row


def reopen(retriever, row):
    try: inspect(retriever.path,row); current=True; reason=None
    except (ValueError,KeyError,FileNotFoundError) as exc: current=False; reason=str(exc)
    db = Q.database(retriever.path)
    try:
        children = [json.loads(r[0]) for r in db.execute("SELECT payload FROM question_decisions WHERE json_extract(payload,'$.parent_decision_id')=? ORDER BY id",(row['id'],))]
        links = [json.loads(r[0]) for r in db.execute('SELECT payload FROM question_decision_tasks WHERE decision IN (SELECT id FROM question_decisions WHERE json_extract(payload,\'$.parent_decision_id\')=?)',(row['id'],))]
        followups = [json.loads(r[0]) for r in db.execute('SELECT payload FROM question_decision_followups WHERE decision=? ORDER BY rowid',(row['id'],))]
    finally: db.close()
    return {'decision':row,'dependencies_current':current,'stale_reason':reason,
        'member_decisions':[{'id':r['id'],'patient_id':r['patient_id'],'status':r['status']} for r in children],
        'task_links':links,'followups':followups,'new_paid_api_calls':0,'clinical_execution_authorized':False}


def bound_gap_dependency(row, slot, *, current):
    if row.get('schema') != MEMBER: raise ValueError('Graph member decision required')
    index = slot.get('gap_index')
    if type(index) is not int or not 0 <= index < len(row['evidence_gaps']): raise ValueError('Exact graph gap required')
    gap = row['evidence_gaps'][index]
    status=gap['status'] if current else 'graph_evidence_changed_requires_reanswer'
    identity_row = {'decision_id':row['id'],'patient_id':row['patient_id'],'analysis_window':row['analysis_window'],**slot}
    out = {'id':digest(identity_row),'identity':identity_row,
        'observation':{'status':status,'original_gap':gap,'graph_dependencies_current':current},
        'requested_action':gap['requested_action'] if current else 'reanswer_original_question',
        'direct_finding_ids':[gap['node_id']], 'clinical_relevance':'unassessed', 'finding_dependency_index_complete':True}
    out['version']=digest(out)
    return out


def gap_dependency(retriever, row, slot):
    try:inspect_member(retriever,row);current=True
    except (ValueError,KeyError,FileNotFoundError):current=False
    return bound_gap_dependency(row,slot,current=current)
