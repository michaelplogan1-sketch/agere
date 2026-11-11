"""Bridge /ask answers to the existing deliverable runtime and action backbone.

Immutable answer snapshots keep cohort membership and evidence together. Review
work uses the backbone's revisioned work store. Explicit demo steps use the same
store with separate simulation receipts. No real outreach is performed.
Frontier composition receives the frozen bundle, not a new cohort.
"""
from collections import Counter
from datetime import datetime, timezone
import json
import fcntl
import gzip
import io
import os
from pathlib import Path
import re
import sqlite3
import shutil
import tempfile
from types import SimpleNamespace

from .receipts import Bundle, fingerprint
from ..plans import backbone

PROGRAMS = {
    'evidence_review': {'label': 'Review unresolved evidence', 'requirements': ['Reconcile unresolved source support against the saved evidence and original question', 'Retain missing evidence, conflicting accounts and machine uncertainty explicitly', 'Record review findings without establishing clinical eligibility or authorizing care']},
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
    from ..clinical import evaluation  # evaluation mode: saved cohorts live in the evaluation scratch directory, outside the stack
    return (evaluation.scratch_dir(path) if evaluation.active() else Path(path)) / 'answer-workspaces'


def snapshot_path(target):
    """Resolve the immutable native snapshot, including lossless packed storage."""
    target=Path(target)
    if target.is_file():return target
    return target.with_name(target.name+'.gz')


def read_snapshot(target):
    target=snapshot_path(target)
    opener=gzip.open if target.suffix=='.gz'else open
    with opener(target,'rt',encoding='utf-8')as stream:return json.load(stream)


def source_identity(path):
    semantic = Path(path) / 'semantic.sqlite3'
    if not semantic.is_file():
        raise ValueError('The active semantic layer is unavailable')
    db = sqlite3.connect(f'file:{semantic}?mode=ro', uri=True)
    try:
        from ..clinical.request_plan import warehouse_metadata
        meta = warehouse_metadata(semantic, db)
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


def load_unfinished_for_review(path,key):
    """Read a sealed proposal for current re-investigation, never accepted reuse.

    A compiler repair changes the implementation policy, and the last returned
    answer may have been rejected before the retained draft corrected its basis.
    Keep that answer invalidated. The analyst must re-execute/check every native
    receipt and perform full current review before capturing a new result.
    """
    from ...contracts import digest
    from ..clinical import answer_dependencies as AD,plan_fence as F,learning
    from ..clinical.request_plan import fingerprint as derivation_fingerprint
    context=load(path,key,source_review=True)
    checkpoint=(context.get('investigation')or{}).get('recovery_checkpoint')or{}
    draft=checkpoint.get('draft');source=source_identity(path)
    if (not isinstance(draft,dict)or draft.get('status')!='partial'
            or checkpoint.get('draft_digest')!=digest(draft)
            or checkpoint.get('source')!=source
            or (checkpoint.get('context')or{}).get('question')!=context.get('question')
            or context.get('workflow')):
        raise ValueError('This investigation has no current unfinished draft available to resume')
    # Validate original integrity independently of compatibility with today's
    # implementation. Old policy is provenance for this proposal, not authority.
    saved=context.get('dependencies')or{}
    if (saved.get('schema')!=AD.VERSION or not context.get('context_policy_identity')
            or saved.get('derivation')!=derivation_fingerprint({k:context.get(k)for k in AD.FIELDS})
            or saved.get('observation')!={'source':source,'wal':AD.wal_identity(path)}
            or context.get('stale')or not context.get('graph')
            or (context.get('answer',{}).get('evidence_completeness')or{}).get('status')=='incomplete'):
        raise ValueError('Saved investigation integrity or observations changed; no draft resumed')
    if (context.get('cohort_transform')or{}).get('parent_context_id'):
        # Inherited scopes still require their normal current parent checks.
        load(path,(context['cohort_transform']['parent_context_id']),current=True,source_review=True)
    if (learning.correction_context(path,context['question'])
            and (context.get('planner_receipt')or{}).get('feedback_identity')!=learning.feedback_identity(path,context['question'])):
        raise ValueError('The original question has new corrections; re-investigate with that feedback')
    if context['source_disposition']['status']=='unavailable':
        raise ValueError('Source-reading status is unavailable; no draft resumed')
    issues=F.premise_issues(path,context['question'],context['graph'],
        native_calls=(context.get('investigation')or{}).get('tool_calls'),answer=draft)
    if issues:raise ValueError('Unfinished draft premises remain invalidated: '+str(issues))
    return {**context,'draft_review_basis':{'draft_digest':checkpoint['draft_digest'],
        'premises_current':True,'returned_answer_invalidated':context['invalidated'],
        'authority':'unfinished_investigation_only',
        'scope':'Recheck native receipts and full current answer; no accepted reuse, work authority or historical status change.'}}


def investigation_evidence(investigation):
    """Stable evidence identity; retain business amounts inside returned rows."""
    source=investigation or {}
    metadata={'accounting','elapsed_s','seconds','new_paid_api_calls','estimated_cost_usd','cost_usd','api_cost_known'}
    return {'tool_calls':[{'name':c.get('name'),'input':c.get('input'),
                           'result':{k:v for k,v in c['result'].items() if k not in metadata} if isinstance(c.get('result'),dict) else c.get('result')}
                          for c in source.get('tool_calls') or []],
            'document_reviews':[{k:v for k,v in r.items() if k not in metadata} for r in source.get('document_reviews') or []]}


def capture(path, question, result, *, original_question=None, _population_scopes=None):
    """Persist a server-produced result; no client-provided IDs enter this boundary."""
    answer = result.get('answer') or {}
    if not answer or answer.get('status') not in ('answered', 'partial', 'low_confidence'):
        return None
    source = source_identity(path)
    produced = result.get('execution_snapshot')
    contract = (result.get('planner_receipt') or {}).get('request_contract') or result.get('request_contract')
    if produced and produced != source:
        raise ValueError('Warehouse changed between execution and capture; ask again against the new snapshot')
    if contract and contract.get('source_identity'):
        identity = contract['source_identity']
        if (identity.get('source_digest') != source['source_digest'] or identity.get('as_of') != source['as_of']
                or identity.get('version') != source['semantic_version']):
            raise ValueError('Executed source identity differs from the current warehouse; no context saved')
    source = produced or source
    if not answer.get('patient_ids') and (contract or result.get('request_contract') or {}).get('kind') == 'checked_graph':
        # Every patient-level count keeps who it counted (stored, not shown) so follow-ups can narrow it.
        from ..clinical.membership import from_graph
        kept = from_graph(path, result.get('graph'), answer,population=(contract or {}).get('population'))
        if kept: answer = {**answer, 'patient_ids': kept['patient_ids'], 'membership_role': kept['role']}
    rows = patient_rows(answer)
    inherited = result.get('cohort_patient_rows')
    if inherited is not None:
        if {r['patient_id'] for r in inherited} != set(answer.get('patient_ids') or []):
            raise ValueError('Transformed patient evidence does not match the saved cohort')
        rows = inherited
    from ..clinical.shared_planner import policy_identity
    context_policy = policy_identity()
    # Preserve an explicit question input independently of output membership.
    # None means unscoped, [] means an intentionally empty saved population.
    input_ids = (contract or {}).get('population')
    transformed_ids = (result.get('cohort_transform') or {}).get('input_patient_ids')
    if input_ids is not None and transformed_ids is not None and set(input_ids) != set(transformed_ids):
        raise ValueError('Executed contract and cohort transform disagree on the saved input population')
    if input_ids is None:
        input_ids = transformed_ids
    input_ids = None if input_ids is None else sorted(set(input_ids))
    data = {'schema': 'agere-answer-context/1', 'context_policy_identity': context_policy, 'question': question,
            'original_question': original_question or question, 'source': source,
            'answer': answer, 'graph': result.get('graph'), 'results': result.get('results'),
            'investigation': result.get('investigation'),
            'planner_receipt': result.get('planner_receipt'), 'planning_economics': result.get('planning_economics'),
            'cohort_transform': result.get('cohort_transform'),
            'workflow': result.get('workflow', []),
            'patient_rows': rows, 'patient_ids': [r['patient_id'] for r in rows],
            'input_patient_ids': input_ids,
            'synthetic': True, 'clinical_eligibility_established': False}
    if(_population_scopes is None and not data['patient_ids']
       and answer.get('answer_type')in ('count','value','table','composite')
       and (data.get('investigation')or{}).get('tool_calls')):
        _population_scopes,checks=_prepare_native_population_snapshot(path,data)
        data['native_population_preparation']={'new_paid_api_calls':0,
            'original_analytical_engine_replays':0,'native_sql_checks':checks}
    if _population_scopes is not None:
        from ..clinical import membership
        if (_population_scopes.get('schema')!=membership.NATIVE_POPULATIONS
            or _population_scopes.get('source')!=source
            or _population_scopes.get('membership_policy')!=membership.native_population_identity()
            or _population_scopes.get('native_calls_digest')!=fingerprint((result.get('investigation')or{}).get('tool_calls')or[])):
            raise ValueError('Population scopes must come from current server-owned native calculations')
        data['native_population_scopes']=_population_scopes
    from .patient_evidence import capture_profiles
    data['patient_profiles']=capture_profiles(path,data['patient_ids'],answer,graph=data.get('graph'))
    from ..clinical import answer_dependencies
    data['dependencies'] = answer_dependencies.seal(path, data)
    # Identity describes the executed answer, not punctuation, elapsed time,
    # cache state or cost. Keep the first complete execution receipt immutable.
    graph = data.get('graph') or {}
    recovery=(data.get('investigation') or {}).get('recovery_checkpoint')
    plan = [{k: n.get(k) for k in ('id', 'operation', 'inputs', 'parameters')} for n in graph.get('nodes') or []]
    key = 'ANS-' + fingerprint({'version': 8, 'context_policy_identity': context_policy,
                               'patient_profiles':data['patient_profiles'],
                               'input_patient_ids': input_ids,
                               'feedback_identity': (data.get('planner_receipt') or {}).get('feedback_identity'),
                               'procedural_memory_identity': ((data.get('planner_receipt') or {}).get('interpretation', {}).get('procedural_memory') or {}).get('identity'),
                               'source': source, 'answer': answer, 'plan': plan,
                               'workflow': data['workflow'],
                               'answer_policy_version': (result.get('planner_receipt') or {}).get('version'),
                               'answer_policy_identity': (result.get('planner_receipt') or {}).get('policy_identity'),
                               'cohort_transform': data.get('cohort_transform'),
                               'investigation_evidence': investigation_evidence(data.get('investigation')),
                               **({'native_population_scopes':data['native_population_scopes']}if 'native_population_scopes'in data else{}),
                               **({'unfinished_draft_digest': recovery['draft_digest']} if recovery else {}),
                               'observation_dependencies': data['dependencies']['observation']})[:24]
    d = root(path) / key
    d.mkdir(parents=True, exist_ok=True)
    # Stream native histories and large snapshots as lossless gzip. Compression
    # is a storage format, never a limit on evidence. A shared
    # publication lock preserves first-writer identity across both encodings.
    with (d/'.capture.lock').open('a+b')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        target=snapshot_path(d/'answer.json')
        if not target.exists():
            created=now();payload={**data,'id':key,'created_at':created}
            investigation=data.get('investigation')
            packed=isinstance(investigation,dict)and bool(investigation.get('tool_calls'))
            if not packed:
                required=sum(len(chunk.encode('utf-8'))for chunk in json.JSONEncoder(default=str).iterencode(payload))
                packed=required>1_048_576 or required>shutil.disk_usage(d).free
            target=d/('answer.json.gz'if packed else'answer.json')
            # Publish only complete snapshots, without replacing a concurrent writer.
            # Keep an interrupted write under its partial name for recovery/accounting.
            options={}if packed else{'encoding':'utf-8'}
            with tempfile.NamedTemporaryFile(mode='wb'if packed else'w',dir=d,
                    prefix='.answer-',suffix='.partial',delete=False,**options)as f:
                partial=Path(f.name)
                if packed:
                    with gzip.GzipFile(filename='',fileobj=f,mode='wb',mtime=0)as encoded:
                        with io.TextIOWrapper(encoded,encoding='utf-8')as stream:json.dump(payload,stream,default=str)
                else:json.dump(payload,f,default=str)
                f.flush();os.fsync(f.fileno())
            published=False
            try:
                os.link(partial,target);published=True
            except FileExistsError:pass
            partial.unlink()
            if published:publish_listing(target,payload)
    return {'id': key, 'patients': len(rows), 'url': '/action-center?context=' + key, **scope_options(data)}


def load(path, key, *, current=False, follow_up=False, source_review=False, local_review=False):
    """follow_up=True: the caller only narrows, lists or groups the exact saved membership (no new work is created). A system
    quarantine ('quarantine: QC-...' written by learning.quarantine when an answer tripped a quality control) records that the
    plan must not be reused or learned from; it is not a user correction of the cohort the user saw, so it does not invalidate the
    saved membership for a follow-up. A user correction still does. local_review
    is used only by existing source-bound review items: their input receipts are
    rechecked independently while the parent answer remains visibly stale."""
    if not isinstance(key, str) or not re.fullmatch(r'ANS-[0-9a-f]{24}', key):
        raise ValueError('A saved answer context is required')
    f = snapshot_path(root(path) / key / 'answer.json')
    if not f.is_file():
        raise ValueError('Saved answer not found in this warehouse')
    data = read_snapshot(f)
    stale = data['source'] != source_identity(path)
    if data.get('patient_profiles') is not None:
        from .patient_evidence import capture_profiles
        stale = stale or data['patient_profiles']!=capture_profiles(path,data['patient_ids'],data['answer'],graph=data.get('graph'))
    invalidated = ((data.get('planner_receipt') or {}).get('version') == 'patient-therapy-review/1')
    population_policy=(data.get('cohort_transform')or{}).get('population_policy')
    if population_policy is not None or str((data.get('cohort_transform')or{}).get('scope','')).startswith('population:'):
        from ..clinical import membership
        invalidated=invalidated or population_policy!=membership.native_population_identity()
    from ..clinical.cohort_families import scope_rejection
    receipt = data.get('planner_receipt') or {}
    if receipt.get('route') in ('cohort_family', 'cohort_family_bottled') and scope_rejection(data['question']):
        invalidated = True
    # A source snapshot can stay unchanged while corrected planning definitions
    # change membership. Preserve old answers for inspection, not new work.
    from ..clinical.shared_planner import policy_identity
    from ..clinical.learning import correction_context, feedback_identity
    active_policy, ancestor, visited = policy_identity(), data, set()
    from ..clinical import answer_dependencies
    dependency_checks = []
    current_source = source_identity(path)
    for _ in range(32):
        check = answer_dependencies.validate(path, ancestor, source=current_source, policy=active_policy, check_probes=current)
        dependency_checks.append({'context_id': ancestor.get('id'), **check})
        invalidated = invalidated or not check['derivation_current'] or not check['evidence_complete'] or not check['premises_current']
        stale = stale or not check['observation_current']
        invalidated = invalidated or ancestor.get('context_policy_identity') != active_policy
        r = ancestor.get('planner_receipt') or {}
        question = ancestor.get('question', '')
        def feedback(q):
            matches = correction_context(path, q)
            return [m for m in matches if not (follow_up and str(m.get('note') or '').startswith('quarantine:'))]
        if feedback(question):
            invalidated = invalidated or r.get('feedback_identity') != feedback_identity(path, question)
        origin = r.get('program_origin') or {}
        if origin.get('original_question') and feedback(origin['original_question']):
            invalidated = invalidated or origin.get('feedback_identity') != feedback_identity(path, origin['original_question'])
        if r.get('version', '').startswith('shared-warehouse-planner/'):
            invalidated = invalidated or r.get('policy_identity') != active_policy
        parent_key = (ancestor.get('cohort_transform') or {}).get('parent_context_id')
        if not parent_key:
            break
        if parent_key in visited or not re.fullmatch(r'ANS-[0-9a-f]{24}', parent_key):
            invalidated = True
            break
        visited.add(parent_key)
        parent_file = snapshot_path(root(path)/parent_key/'answer.json')
        if not parent_file.is_file():
            invalidated = True
            break
        ancestor = read_snapshot(parent_file)
    else:
        invalidated = True
    dispositions = [check['source_disposition'] for check in dependency_checks]
    suspended = {}
    for disposition in dispositions:
        for item in disposition['suspended_assertions']:
            saved = suspended.setdefault(item['assertion_id'], {'assertion_id': item['assertion_id'], 'observations': []})
            for observation in item['observations']:
                if observation not in saved['observations']:
                    saved['observations'].append(observation)
    unavailable = any(item['status'] == 'unavailable' for item in dispositions)
    checked = {ident for item in dispositions for ident in item['checked_assertion_ids']}
    unchecked = {ident for item in dispositions for ident in item['unchecked_assertion_ids']} - checked
    disposition = {'status': 'unavailable' if unavailable else 'suspended' if suspended else 'current',
                   'source_readings_current': not unavailable and not suspended,
                   'suspended_assertions': [suspended[ident] for ident in sorted(suspended)],
                   'checked_assertion_ids': sorted(checked), 'unchecked_assertion_ids': sorted(unchecked),
                   'feedback_is_clinical_truth': False}
    if current and invalidated:
        raise ValueError('This saved answer is invalidated under the current planning definitions. It remains available for inspection; ask again before creating new work.')
    if current and stale and not (source_review and local_review):
        raise ValueError('The data changed. Ask the question again before creating or changing work.')
    if current and unavailable:
        raise ValueError('Source-reading status is unavailable. Inspect the saved evidence; restore source verification before changing work.')
    if current and suspended and not source_review:
        raise ValueError('This answer relies on a suspended source reading. Ask again or open source-evidence review before preparing program work.')
    return {**data, 'stale': stale, 'invalidated': invalidated,
            'source_disposition': disposition, 'source_review_required': bool(suspended),
            'dependency_validation': dependency_checks,
            **scope_options(data),
            'supports_patient_work': supports_patient_work(data)}


def listing_row(data):
    return {'id':data['id'],'question':data['question'],'patients':len(data['patient_ids']),
            'created_at':data['created_at'],**scope_options(data)}


def listing_stamp(target):
    s=target.stat()
    return [s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_ino]


def publish_listing(target,data):
    """Small derived catalog; never confers scope or answer acceptance."""
    value={'schema':'answer-listing/1','file':listing_stamp(target),'row':listing_row(data)}
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=target.parent,prefix='.listing-',suffix='.partial',delete=False)as f:
            partial=Path(f.name);json.dump(value,f);f.flush();os.fsync(f.fileno())
        try:os.link(partial,target.with_name('listing.json'))
        except FileExistsError:pass
        partial.unlink()
    except OSError:
        pass  # A missing optional index cannot lose a saved answer; legacy headers remain inspectable.


def legacy_listing(target):
    """Read the small header/tail of large historical files, without rewriting them.

    Old unindexed population counts remain unknown. Opening the answer still
    loads its complete immutable context and runs the existing scope checks.
    """
    stamp=listing_stamp(target)
    packed=target.suffix=='.gz'
    if not packed and stamp[0]<=65536:
        data=read_snapshot(target)
        return listing_row(data)
    decoder=json.JSONDecoder();question=None
    opener=gzip.open if packed else open
    with opener(target,'rt',encoding='utf-8')as stream:
        text=stream.read(4096);pos=1
        while True:
            begin=pos
            try:
                while text[pos].isspace()or text[pos]==',':pos+=1
                if text[pos]!='"':break
                key,end=decoder.raw_decode(text,pos);pos=end
                while text[pos].isspace():pos+=1
                if text[pos]!=':':break
                pos+=1
                while text[pos].isspace():pos+=1
                # The native writer places the question before evidence. Do
                # not scan an arbitrary legacy evidence tree to find a title.
                if key not in ('schema','context_policy_identity','question','original_question'):break
                if text[pos]!='"':break
                value,end=decoder.raw_decode(text,pos);pos=end
                if key=='question':
                    if isinstance(value,str):question=value
                    break
            except (json.JSONDecodeError,IndexError):
                pos=begin
                more=stream.read(4096)
                if not more:break
                text+=more
    created=None
    with target.open('rb')as stream:
        stream.seek(max(0,stamp[0]-4096));tail=stream.read().decode('utf-8',errors='replace')
    marker=-1 if packed else tail.rfind(', "id":')
    if marker>=0:
        try:
            suffix=json.loads('{'+tail[marker+2:])
            if suffix.get('id')==target.parent.name:created=suffix.get('created_at')
        except (ValueError,TypeError):pass
    if listing_stamp(target)!=stamp:raise ValueError('Saved answer changed while reading its catalog header')
    return {'id':target.parent.name,'question':question or 'Historical investigation · open to inspect',
            'patients':None,'created_at':created,'result_scope_available':None,'input_patients':None,
            'catalog_scope':'Historical header only; open the complete answer to inspect its population.'}


def listing(path):
    out=[]
    # The API already returns the 100 newest entries. Select them BEFORE any
    # payload read; the former code decoded every historical investigation.
    files={target.parent:snapshot_path(target.parent/'answer.json')
           for pattern in ('ANS-*/answer.json','ANS-*/answer.json.gz')for target in root(path).glob(pattern)}
    files=sorted(files.values(),key=lambda p:p.stat().st_mtime,reverse=True)[:100]
    for target in files:
        index=target.with_name('listing.json');row=None
        if index.is_file():
            try:
                value=json.loads(index.read_text())
                if value.get('schema')=='answer-listing/1'and value.get('file')==listing_stamp(target)and value['row']['id']==target.parent.name:
                    row=value['row']
            except (OSError,ValueError,KeyError,TypeError):pass
        out.append(row if row is not None else legacy_listing(target))
    return out


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
    items = [];fields={}
    for item in work(path, context):
        checks = review_dependency_status(path, item)
        for check in checks:
            for field in check.get('fields')or[]:
                state=fields.setdefault(item['patient_id'],{}).setdefault(field,{'source_current':True,'dependency_ids':[]})
                state['source_current']=state['source_current']and check.get('finding_source_current',False)
                state['dependency_ids'].append(check['id'])
        items.append({**item, 'review_dependency_status': checks,
            'parent_answer_stale': context['stale'],
            'source_review_required': (context.get('source_review_required', False) or context['invalidated']
                                      or any(not d['source_current'] for d in checks)),
            'source_disposition_status': context['source_disposition']['status']})
    from .workflow import read
    return {'context': context, 'workflow': read(path, key), 'items': items, 'finding_freshness':fields,
            'counts': dict(Counter(i['state'] for i in items)),
            'programs': PROGRAMS, 'states': STATES, 'external_dispatch_performed': False}


def supports_patient_work(context):
    """Summary input membership is provenance, not permission to create work."""
    answer = context['answer']
    computed = answer.get('computed_as')
    contract = (context.get('planner_receipt') or {}).get('request_contract') or {}
    summary_select = any(s.get('kind') in ('count', 'count_distinct', 'avg', 'sum', 'min', 'max')
                         for s in contract.get('request', {}).get('select', []))
    forbidden = (answer.get('actionable_cohort') is False or answer.get('answer_type') in ('count', 'value')
                or summary_select or (isinstance(computed, dict) and computed.get('metric_id'))
                or any(n.get('operation') == 'governed_metric' for n in (context.get('graph') or {}).get('nodes', [])))
    if forbidden:
        return False
    return (answer.get('actionable_cohort') is True or answer.get('answer_type') == 'patient_set'
            or any(isinstance(r, dict) and (r.get('patient_id') or r.get('patient'))
                   for r in answer.get('table') or []))


def scope_options(context):
    """Public scope choices; membership itself remains server-owned."""
    answer = context['answer']
    contract = (context.get('planner_receipt') or {}).get('request_contract') or {}
    summary = (contract.get('kind') == 'request_program' or answer.get('answer_type') == 'composite'
               or ('request' in contract and contract.get('membership_query') is None)
               or answer.get('analysis_population_count') is not None or answer.get('answer_type') == 'value'
               or (contract.get('kind') == 'general_analysis' and contract.get('returns') != 'patient_set'
                   and not contract.get('analysis', {}).get('patient_selection'))
               # A non-empty count/table that retained no patient ids (event or provider rows) selects no cohort.
               or (answer.get('answer_type') in ('count', 'table') and bool(answer.get('count') or answer.get('table'))))
    shared = (answer.get('answer_type') == 'composite' and answer.get('membership_role') == 'shared_selection'
              and answer.get('shared_selection_established') is True)
    populations=_current_population_scopes(context)
    return {'result_scope_available': bool(context['patient_ids']) or shared or not summary,
            'input_patients': None if context.get('input_patient_ids') is None else len(context['input_patient_ids']),
            'population_scopes':[{'id':s['id'],'label':s['label'],'patients':s['patient_count'],
                'source_rows':s['counted_source_rows'],'clinical_eligibility_established':False}for s in populations],
            'population_scope_discovery_available':bool((context.get('investigation')or{}).get('tool_calls'))}


def _current_population_scopes(context):
    from ..clinical import membership
    snapshot=context.get('native_population_scopes')or{}
    if (snapshot.get('schema')!=membership.NATIVE_POPULATIONS or snapshot.get('source')!=context.get('source')
        or snapshot.get('membership_policy')!=membership.native_population_identity()):return []
    from ...contracts import digest
    scopes=[]
    for scope in snapshot.get('scopes',[]):
        ids=scope.get('patient_ids');proof=scope.get('proof')or{}
        if (not isinstance(ids,list)or any(not isinstance(p,str)or not p for p in ids)
            or len(set(ids))!=len(ids)or scope.get('patient_count')!=len(ids)
            or proof.get('source')!=context['source']or proof.get('patient_ids_digest')!=digest(ids)
            or proof.get('membership_policy')!=snapshot['membership_policy']
            or scope.get('id')!='POP-'+digest(proof)[:24]):continue
        scopes.append(scope)
    return scopes


def _prepare_native_population_snapshot(path,context):
    """Recover exact native count memberships without rerunning an analyst."""
    calls=(context.get('investigation')or{}).get('tool_calls')or[]
    from ..clinical import membership,backstop_agent as B,semantic_analyst as S,sql_query,semantic_layer
    import sqlglot
    from sqlglot import exp
    source=context['source'];by={};names=set();all_columns=False
    for call in calls:
        result=call.get('result')or{};view=result.get('result_view')
        if view:
            if view in by:raise ValueError('Ambiguous native result identity')
            by[view]=result
        query=result.get('declared_executed_query')or result.get('executed_query')
        if call.get('name')!='query_sql'or not isinstance(query,str)or not re.search(r'\b(?:count|sum|min|max)\s*\(',query,re.I):continue
        try:tree=sqlglot.parse_one(query,read='sqlite')
        except sqlglot.errors.SqlglotError:continue
        names.update(c.name for c in tree.find_all(exp.Column))
        all_columns=all_columns or any(not isinstance(s.parent,exp.Count)for s in tree.find_all(exp.Star))
    root_view=(context['answer'].get('table_binding')or{}).get('result_view')or context['answer'].get('table_from_tool')
    roots=list(dict.fromkeys([*([root_view]if root_view else[]),*B.computed_prose_views(context['answer'].get('computed_prose',[]))]))
    wh=S.SemanticWarehouse(Path(path)/'semantic.sqlite3');registered=set();checks=0
    try:
        wh.define(source['as_of'])
        if wh.meta.get('postprocessing_identity')not in (None,semantic_layer.policy_identity()):raise ValueError('Native population source policy differs from the connected snapshot')
        known={r[0]for r in wh.db.execute('SELECT patient_id FROM patient')}
        def execute(query,parameters):
            nonlocal checks
            tree,tables=sql_query.native_query_bindings(query,by)
            if tree is None:raise ValueError('Population selection could not resolve its native tables')
            for table in tables:
                view=next(name for name in by if name.casefold()==table.name.casefold())
                if view in registered:continue
                result=by[view];columns,rows=B.output_table(result)
                from ..clinical.result_assembly import incomplete_reason
                if result.get('error')or incomplete_reason(result):raise ValueError('Population selection depends on incomplete native input')
                selected=columns if all_columns else [c for c in columns if c in names or c=='patient_id']
                if not selected:raise ValueError('Native population input has no resolved columns')
                values=[[r[columns.index(c)]for c in selected]for r in rows]
                B.define_result(wh,view,selected,values,receipt=result);registered.add(view)
            checks+=1
            cursor=wh.db.execute(query,parameters);return [c[0]for c in cursor.description],[list(r)for r in cursor]
        snapshot=membership.native_populations(calls,roots,execute,known,source)
        if source_identity(path)!=source:raise ValueError('Warehouse changed while preparing the native populations')
    finally:wh.close()
    # An unresolved dependency can yield no scopes. Record the attempted native
    # policy and its explicit unavailable reason, rather than losing the saved
    # analytical answer or substituting an empty patient population.
    snapshot.setdefault('membership_policy',membership.native_population_identity())
    snapshot['native_calls_digest']=fingerprint(calls)
    return snapshot,checks


def prepare_population_scopes(path, request):
    """Discover exact native patient populations on demand, without providers."""
    if set(request)!={'context_id'}:raise ValueError('Exact saved answer identity required')
    context=load(path,request['context_id'],current=True)
    calls=(context.get('investigation')or{}).get('tool_calls')or[]
    if not calls:raise ValueError('This answer has no retained native calculations to inspect')
    from ..clinical import membership
    cached=context.get('native_population_scopes')or{}
    if (cached.get('schema')==membership.NATIVE_POPULATIONS and cached.get('source')==context['source']
        and cached.get('membership_policy')==membership.native_population_identity()):
        options=scope_options(context)
        return {'status':'prepared','action_context':{'id':context['id'],'patients':len(context['patient_ids']),
            'url':'/action-center?context='+context['id'],**options},'population_scopes':options['population_scopes'],
            'unavailable':cached.get('unavailable',[]),'new_paid_api_calls':0,'original_analytical_engine_replays':0,
            'native_sql_checks':0,'cache_hit':True,'clinical_eligibility_established':False}
    snapshot,checks=_prepare_native_population_snapshot(path,context)
    source=context['source']
    result={'answer':context['answer'],'graph':context.get('graph'),'results':context.get('results'),
        'planner_receipt':context.get('planner_receipt'),'planning_economics':context.get('planning_economics'),
        'investigation':context.get('investigation'),'workflow':context.get('workflow')or[],
        'execution_snapshot':source,'cohort_transform':context.get('cohort_transform')}
    if context.get('input_patient_ids')is not None and result['cohort_transform']is None:
        result['cohort_transform']={'input_patient_ids':context['input_patient_ids'],'scope':'input_provenance','operations':[]}
    saved=capture(path,context['question'],result,original_question=context.get('original_question'),_population_scopes=snapshot)
    return {'status':'prepared','action_context':saved,'population_scopes':saved['population_scopes'],
        'unavailable':snapshot['unavailable'],'new_paid_api_calls':0,'original_analytical_engine_replays':0,
        'native_sql_checks':checks,'cache_hit':False,'clinical_eligibility_established':False}


def open_population_members(path, request):
    """Read all members of one current saved native population, without inference."""
    if set(request)!={'context_id','population_id'}:raise ValueError('Exact saved answer and population required')
    context=load(path,request['context_id'],current=True)
    scope=next((s for s in _current_population_scopes(context)if s['id']==request['population_id']),None)
    if scope is None:raise ValueError('Population is unavailable for this current answer')
    return {'context_id':context['id'],'population_id':scope['id'],'label':scope['label'],
        'patient_count':scope['patient_count'],'patient_ids':scope['patient_ids'],
        'source':context['source'],'binding':scope['proof']['binding'],
        'selection_query':scope['proof']['selection_query'],'parameters':scope['proof']['parameters'],
        'new_paid_api_calls':0,'clinical_eligibility_established':False,'actionable_cohort':False}


def require_patient_reference(context):
    """An empty summary membership is not an observed empty patient cohort."""
    if not scope_options(context)['result_scope_available']:
        from ..clinical.question_input import QuestionNotUnderstood
        raise QuestionNotUnderstood('This analysis has no single patient cohort. Choose the population you want to follow from its saved counts; no whole-warehouse or empty cohort was substituted.')


def reference_scope(context, scope='result'):
    """Select a current saved scope without mutating the immutable answer.

    The original input is analytical provenance. It carries no authority from
    a narrower output cohort to create patient work.
    """
    if scope == 'result':
        require_patient_reference(context)
        return context
    if isinstance(scope,str)and scope.startswith('population:'):
        selected=next((s for s in _current_population_scopes(context)if scope=='population:'+s['id']),None)
        if selected is None:raise ValueError('Choose an available source-current population from this exact saved answer')
        ids=selected['patient_ids']
        return {**context,'patient_ids':ids,'patient_rows':[{'patient_id':pid,'evidence_rows':[]}for pid in ids],
            'selected_population':{k:v for k,v in selected.items()if k!='patient_ids'},
            'answer':{**context['answer'],'actionable_cohort':False}}
    if scope != 'input' or context.get('input_patient_ids') is None:
        raise ValueError('This answer has no explicit saved input population. Choose an available result or the entire warehouse.')
    ids = context['input_patient_ids']
    return {**context, 'patient_ids': ids,
            'patient_rows': [{'patient_id': pid, 'evidence_rows': []} for pid in ids],
            'answer': {**context['answer'], 'actionable_cohort': False}}


REVIEW_DEPENDENCIES = 'answer-review-source-dependencies/1'


def review_source_receipt(path, selection):
    """Exact received-source versions for one declared patient/family/date scope.

    Empty sets and unknown clocks are retained. A digest establishes freshness
    of this input set, never source completeness, care absence or resolution.
    """
    from datetime import date
    if set(selection) != {'patient_id','streams','start','end','as_of'}:
        raise ValueError('Exact patient, source families and date scope required')
    streams = selection['streams']
    if (not isinstance(selection['patient_id'],str) or not selection['patient_id']
            or not isinstance(streams,list) or not streams or len(set(streams))!=len(streams)
            or any(not isinstance(s,str)or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',s)for s in streams)):
        raise ValueError('Distinct explicit source families and patient required')
    start,end,cutoff=(date.fromisoformat(selection[k]) for k in ('start','end','as_of'))
    if not start<=end<=cutoff:raise ValueError('Source dates must end by the saved cutoff')
    query=('SELECT record_id,stream,event_date,available_date,payload FROM record WHERE patient_id=? AND stream IN ('
           +','.join('?'for _ in streams)+') ORDER BY record_id')
    with sqlite3.connect((Path(path)/'semantic.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)as db:
        rows=db.execute(query,[selection['patient_id'],*streams]).fetchall()
    def day(value):
        try:return date.fromisoformat(value)if isinstance(value,str)and re.fullmatch(r'\d{4}-\d{2}-\d{2}',value)else None
        except ValueError:return None
    rows=[r for r in rows if (day(r[2])is None or start<=day(r[2])<=end)
          and(day(r[3])is None or day(r[3])<=cutoff)]
    manifest=[{'record_id':rid,'stream':stream,'event_date':event,'reported_available_date':available,
               'record_digest':fingerprint(json.loads(payload))}for rid,stream,event,available,payload in rows]
    return {'schema':REVIEW_DEPENDENCIES,'selection':selection,'records':manifest,'digest':fingerprint(manifest),
            'unknown_event_dates':sum(day(r['event_date'])is None for r in manifest),
            'unknown_reported_availability_dates':sum(day(r['reported_available_date'])is None for r in manifest),
            'scope':'Freshness of declared received inputs only; absence, clinical truth and care completion remain unestablished.'}


def prepare_review_dependency(path, context, request):
    required={'patient_id','key','question','streams','start','end'}
    if not isinstance(request,dict)or set(request)not in(required,required|{'fields'}):
        raise ValueError('Explicit review dependency, patient, question and source scope required')
    if request['patient_id']not in context['patient_ids']:
        raise ValueError('Review dependency is outside the saved population')
    if not isinstance(request['key'],str)or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',request['key']):
        raise ValueError('Named review dependency required')
    if not isinstance(request['question'],str)or not request['question'].strip():
        raise ValueError('Unresolved source question required')
    fields=request.get('fields',[])
    if not isinstance(fields,list)or any(not isinstance(f,str)or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*',f)for f in fields)or len(set(fields))!=len(fields):
        raise ValueError('Distinct received output-field paths required')
    rows=next((r['evidence_rows']for r in context['patient_rows']if r['patient_id']==request['patient_id']),[])
    if fields and not rows:raise ValueError('Named finding fields require received answer rows')
    for field in fields:
        for row in rows:
            value=row
            for key in field.split('.'):
                if not isinstance(value,dict)or key not in value:raise ValueError('Review dependency names an absent output field')
                value=value[key]
    selection={k:request[k]for k in ('patient_id','streams','start','end')}
    if not isinstance(selection['streams'],list)or any(not isinstance(s,str)for s in selection['streams']):
        raise ValueError('Explicit source-family list required')
    selection['streams']=sorted(selection['streams'])
    selection['as_of']=context['source']['as_of']
    source=review_source_receipt(path,selection)
    binding={'context_id':context['id'],'patient_id':request['patient_id'],'key':request['key'],
             'question':request['question'],'selection':selection,'fields':sorted(fields)}
    return {'schema':REVIEW_DEPENDENCIES,'id':'RD-'+fingerprint(binding)[:24],**binding,'source_receipt':source,
            'finding_source_digest':source['digest']if fields else None,
            'clinical_execution_authorized':False}


def review_dependency_status(path, item):
    dependencies=(item.get('evidence_review')or{}).get('dependencies')or[]
    out=[]
    for dep in dependencies:
        try:
            if not isinstance(dep,dict):raise ValueError('Stored review dependency is invalid')
            binding={k:dep[k]for k in ('context_id','patient_id','key','question','selection','fields')}
            if (dep['schema']!=REVIEW_DEPENDENCIES or dep['patient_id']!=item['patient_id']
                    or dep['context_id']!=item['context_id']or dep['id']!='RD-'+fingerprint(binding)[:24]
                    or dep['source_receipt']['digest']!=fingerprint(dep['source_receipt']['records'])):
                raise ValueError('Review dependency binding changed')
            current=review_source_receipt(path,dep['selection'])
            same=current==dep['source_receipt']
            out.append({'id':dep['id'],'key':dep['key'],'question':dep['question'],'fields':dep['fields'],'source_current':same,
                        'finding_source_current':current['digest']==dep.get('finding_source_digest')if dep['fields']else None,
                        'status':'received_inputs_current'if same else'source_reassessment_required',
                        'expected_digest':dep['source_receipt']['digest'],'current_digest':current['digest']})
        except (ValueError,KeyError,TypeError,OSError,sqlite3.Error)as exc:
            out.append({'id':dep.get('id')if isinstance(dep,dict)else None,'fields':dep.get('fields',[])if isinstance(dep,dict)else[], 'source_current':False,
                        'status':'source_dependency_unavailable','reason':str(exc)})
    return out


def create_work(path, request):
    keys={'context_id', 'program', 'patient_ids', 'owner', 'note'}
    if set(request) not in (keys, keys|{'review_dependencies'}):
        raise ValueError('Exact answer, program, selected patients, owner and note are required')
    program = request['program']
    if program not in PROGRAMS:
        raise ValueError('Unknown review program')
    c = load(path, request['context_id'], current=True, source_review=program == 'evidence_review')
    if (c['answer'].get('status') not in ('answered', 'partial', 'low_confidence') or
            (program != 'evidence_review' and
             (c['answer'].get('status') != 'answered' or c['answer'].get('missing')))):
        raise ValueError('Resolve the incomplete answer before preparing program work')
    if not supports_patient_work(c):
        raise ValueError('This analytical summary does not select an action population; ask for the intended patient cohort.')
    ids = request['patient_ids']
    if not isinstance(ids, list) or not ids or any(not isinstance(p, str) for p in ids) or len(set(ids)) != len(ids):
        raise ValueError('Select distinct patients from this answer')
    if not set(ids) <= set(c['patient_ids']):
        raise ValueError('Selected patients are outside the saved answer')
    dependencies={pid:[]for pid in ids}
    declared=request.get('review_dependencies',c['answer'].get('review_dependencies')if program=='evidence_review'else None)
    if declared is not None:
        if program!='evidence_review'or not isinstance(declared,list):
            raise ValueError('Source dependencies belong to internal evidence review only')
        seen=set()
        for raw in declared:
            dep=prepare_review_dependency(path,c,raw)
            if dep['patient_id']not in dependencies and 'review_dependencies'not in request:continue
            if dep['patient_id']not in dependencies or dep['id']in seen:
                raise ValueError('Duplicate dependency or patient outside selected work')
            seen.add(dep['id']);dependencies[dep['patient_id']].append(dep)
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
                if program == 'evidence_review':
                    assessment['evidence_review'] = {
                        'scope': 'source_evidence_only', 'answer_status': c['answer'].get('status'),
                        'unresolved_requirements': c['answer'].get('missing') or [],
                        'limitations': c.get('limitations') or c['answer'].get('limitations') or [],
                        'clinical_execution_authorized': False,
                        'suspended_source_readings': c['source_disposition']['suspended_assertions'],
                        'source_disposition': c['source_disposition']['status'],
                    }
                    if declared is not None:
                        assessment['evidence_review']['dependencies']=dependencies[pid]
                aid = fingerprint(assessment)
                db.execute('INSERT OR IGNORE INTO work_assessments VALUES (?,?,?,?)', (aid, pid, lane, json.dumps(assessment)))
                existing = db.execute('SELECT payload FROM work_items WHERE id=?', (key,)).fetchone()
                if existing and json.loads(existing[0])['owner'] != owner:
                    raise ValueError('Review work already has a different owner. Open the existing item to change ownership explicitly; no selected work was changed.')
                if existing:
                    if (program=='evidence_review'and declared is not None
                            and (json.loads(existing[0]).get('evidence_review')or{}).get('dependencies')!=dependencies[pid]):
                        raise ValueError('Saved review dependency selection changed; preserve prior work and reassess explicitly')
                    continue  # Idempotent creation never resets progress or changes the assigned owner.
                item = {'id': key, 'patient_id': pid, 'context_id': c['id'], 'program': program, 'lane': lane,
                        'decision_id': decision_id, 'assessment_id': aid, 'revision': 1, 'state': 'needs_review',
                        'owner': owner, 'note': note, 'requirements': assessment['requirements'], 'updated_at': now(),
                        'synthetic': True, 'external_dispatch_performed': False}
                if program == 'evidence_review':
                    item['evidence_review'] = assessment['evidence_review']
                db.execute('INSERT INTO work_items VALUES (?,?,?,?,?)', (key, pid, lane, 1, json.dumps(item)))
                db.execute('INSERT INTO question_decision_tasks VALUES (?,?,?)', (decision_id, key, json.dumps({'patient_id': pid})))
                db.execute('INSERT INTO work_events VALUES (?,?,?)', (key, 1, json.dumps({'action': 'review_created', 'item': item})))
            fresh = load(path, c['id'], current=True, source_review=program == 'evidence_review')
            if source_disposition_identity(fresh) != source_disposition_identity(c):
                raise ValueError('Source-reading status changed while preparing work. Reload before saving.')
    finally:
        db.close()
    return inspect(path, c['id'])


def update_work(path, request):
    if set(request) != {'context_id', 'item_id', 'revision', 'state', 'owner', 'note'}:
        raise ValueError('Exact work item, revision, state, owner and note are required')
    c = load(path, request['context_id'])
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
            local=(item['program']=='evidence_review'and bool((item.get('evidence_review')or{}).get('dependencies')))
            c=load(path,c['id'],current=True,source_review=True,local_review=local)
            checks=review_dependency_status(path,item)
            if request['state']=='review_complete'and any(not d['source_current']for d in checks):
                raise ValueError('A dependent source changed; reassess it before completing review')
            if item['program'] != 'evidence_review' and c['source_review_required']:
                raise ValueError('This answer relies on a suspended source reading. Open source-evidence review before changing program work.')
            if request['state'] not in TRANSITIONS[item['state']]:
                raise ValueError(f"Review cannot move from {item['state']} to {request['state']}; start or reopen the review first.")
            item.update(state=request['state'], owner=owner, note=note, revision=row[0] + 1, updated_at=now())
            db.execute('UPDATE work_items SET revision=?,payload=? WHERE id=?', (item['revision'], json.dumps(item), item['id']))
            db.execute('INSERT INTO work_events VALUES (?,?,?)', (item['id'], item['revision'], json.dumps({'action': 'review_updated', 'item': item})))
            fresh = load(path, c['id'], current=True, source_review=item['program'] == 'evidence_review',local_review=local)
            if review_dependency_status(path,item)!=checks:
                raise ValueError('Review source dependencies changed while updating work')
            if source_disposition_identity(fresh) != source_disposition_identity(c):
                raise ValueError('Source-reading status changed while updating work. Reload before saving.')
    finally:
        db.close()
    return inspect(path, c['id'])


def refresh_review_sources(path, request):
    """Rebind selected review inputs; keep the prior answer and all versions.

    This records an explicit internal source refresh, never a new interpretation
    or completed clinical review. Changed work returns to in_review.
    """
    if set(request)!={'context_id','item_id','revision','dependency_ids','owner','note'}:
        raise ValueError('Exact work revision, selected source requests, owner and note required')
    selected=request['dependency_ids']
    if (type(request['revision'])is not int or not isinstance(selected,list)or not selected
            or any(not isinstance(x,str)for x in selected)or len(set(selected))!=len(selected)):
        raise ValueError('Distinct source request IDs and an exact work revision required')
    owner,note=str(request['owner']).strip(),str(request['note']).strip()
    if not owner or len(owner)>120 or len(note)>2000:raise ValueError('Valid review owner and note required')
    c=load(path,request['context_id']);db=_db(path,c)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT revision,payload FROM work_items WHERE id=?',(request['item_id'],)).fetchone()
            if not row or row[0]!=request['revision']:raise ValueError('Work changed or is absent; reload before refreshing sources')
            item=json.loads(row[1]);dependencies=(item.get('evidence_review')or{}).get('dependencies')or[]
            if item['program']!='evidence_review'or not dependencies or item['state']=='cancelled':
                raise ValueError('Select active source-bound evidence review work')
            if item['owner']!=owner:raise ValueError('Review owner changed; update ownership explicitly before refreshing sources')
            checks={d['id']:d for d in review_dependency_status(path,item)}
            if not set(selected)<=set(checks)or any(checks[d]['status']=='source_dependency_unavailable'for d in selected):
                raise ValueError('Selected source request is absent or its binding is unavailable')
            load(path,c['id'],current=True,source_review=True,local_review=True)
            new=[{**dep,'source_receipt':review_source_receipt(path,dep['selection'])}if dep['id']in selected else dep for dep in dependencies]
            if new!=dependencies:
                prior=item['assessment_id']
                assessment=json.loads(db.execute('SELECT payload FROM work_assessments WHERE id=?',(prior,)).fetchone()[0])
                assessment={**assessment,'evidence_review':{**assessment['evidence_review'],'dependencies':new},
                    'source_refresh':{'source':source_identity(path),'owner':owner,'note':note,
                                      'original_answer_unchanged':True,'clinical_findings_recomputed':False}}
                aid=fingerprint(assessment)
                db.execute('INSERT OR IGNORE INTO work_assessments VALUES (?,?,?,?)',(aid,item['patient_id'],item['lane'],json.dumps(assessment)))
                item={**item,'assessment_id':aid,'evidence_review':assessment['evidence_review'],
                      'state':'in_review','note':note,'revision':row[0]+1,'updated_at':now()}
                db.execute('UPDATE work_items SET revision=?,payload=? WHERE id=?',(item['revision'],json.dumps(item),item['id']))
                db.execute('INSERT INTO work_events VALUES (?,?,?)',(item['id'],item['revision'],json.dumps({
                    'action':'source_inputs_refreshed','dependency_ids':selected,'prior_assessment_id':prior,'item':item,
                    'clinical_findings_recomputed':False})))
                current={d['id']:d for d in review_dependency_status(path,item)}
                if any(not current[d]['source_current']for d in selected):raise ValueError('Selected sources changed while refreshing')
                load(path,c['id'],current=True,source_review=True,local_review=True)
    finally:db.close()
    return inspect(path,c['id'])


def saved_followup_edit(question):
    """Recognize existing finite saved-set edits without selecting a population."""
    text = ' '.join(question.lower().strip(' ?.!').split())
    text = re.sub(r'^(?:can|could) i (?:get|have|see)\s+', 'show me ', text)
    from ..clinical.conversation import group_edit, group_dimension
    edit = group_edit(text)
    if edit: text = 'group them by ' + group_dimension(edit['dim'])
    patient_list = re.fullmatch(r'(?:show me|give me|show|list) (?:the )?patient list(?: for (?:that|this|the saved) (?:group|cohort|population))?', text)
    listing = patient_list or re.fullmatch(r'(?:(?:give|show|send|get)(?: me)?(?: the)?(?: list of)?|list|show|count)(?: me)?(?: the)?(?: list of)? (?:them|those patients|these patients|those|these|the patients)', text)
    grouping = re.fullmatch(r'(?:group|break down|break (?:that|this|it|them|those) down|split(?: that| it| this| them)?(?: out)?)(?: them| those patients| these patients| that| it| this)? by (pcp|practice|payer|line of business|payer type|coverage)', text)
    exclusion = re.fullmatch(r'(?:exclude|excluding|remove)(?: the)? (?:patients|ones|those|them)(?: who have| who| that have)? (?:died|are deceased|deceased)', text)
    if not (listing or grouping or exclusion):
        return None
    return text,listing,grouping,exclusion


def followup(path, key, question, *, scope='result'):
    """Transform the exact saved scope; an ambiguous summary never broadens it."""
    parsed=saved_followup_edit(question)
    if parsed is None:return None
    text,listing,grouping,exclusion=parsed
    c = reference_scope(load(path, key, current=True, follow_up=True), scope)
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
            field = {'pcp': 'pcp_clinician_id', 'practice': 'pcp_practice_id', 'payer': 'payer_id', 'line of business': 'line_of_business',
                     'payer type': 'line_of_business', 'coverage': 'coverage_type'}[grouping.group(1)]
            query = f'SELECT patient_id,{field} FROM patient'
            people = {r['patient_id']: r[field] for r in db.execute(query) if r['patient_id'] in ids}
            counts = Counter(people.get(pid) or 'Unknown' for pid in ids)
            table = [{grouping.group(1): k, 'patients': v} for k, v in sorted(counts.items())]
            operations.append({'operation': 'group_saved_cohort', 'field': field})
            evidence.append({'query': query, 'restricted_to_context': key,
                             'definition': f'Observed patient.{field}; no inference of ongoing care or alternate attribution.'})
        if listing:
            operations.append({'operation': 'count_saved_cohort' if text.startswith('count') else 'list_saved_cohort'})
            if c.get('selected_population')and not text.startswith('count'):
                from .patient_evidence import capture_profiles
                profiles=(capture_profiles(path,sorted(ids))or{}).get('patients')or{}
                table=[{'patient_id':pid,'patient_name':profiles.get(pid,{}).get('display_name'),
                    'profile_source_date':profiles.get(pid,{}).get('profile_source_date'),
                    'profile_status':profiles.get(pid,{}).get('identity_status')or'profile_unavailable'}for pid in sorted(ids)]
    finally:
        db.close()
    parent = c['answer']
    status = parent['status']
    count_only = bool(listing and text.startswith('count'))
    answer = {'status': status, 'answer_type': 'count' if count_only else 'table', 'count': len(ids),
              'headline': f'{len(ids)} patients from the saved cohort' + (f', grouped by {grouping.group(1)}.' if grouping else '.'),
              'table': [] if count_only else table, 'patient_ids': sorted(ids),
              'actionable_cohort': bool(ids) and supports_patient_work(c),
              'missing': parent.get('missing') or [], 'limitations': [*(parent.get('limitations') or []),
                  'This transformation preserves the original answer limitations and does not re-establish cohort correctness.'],
              'source_checks': evidence,
              'evidence_discovery': parent.get('evidence_discovery', []),
              'inherited_evidence_context': {'id': key, 'source': c['source']},
              'query_evidence': parent.get('query_evidence')}
    if not supports_patient_work(c):
        answer['headline'] = f'{len(ids)} ' + ('original input patients' if scope == 'input' else 'contributing patients') + ' · analytical provenance only'
    if scope == 'input':
        answer['limitations'].append('Explicit follow-up on the original question input, not patients selected by its output. No patient-work authority is inherited.')
    return {'question': question, 'answer': answer, 'route': 'saved_cohort',
            'cohort_patient_rows': ([{'patient_id':pid,'evidence_rows':[r for r in table if r.get('patient_id')==pid]}for pid in sorted(ids)]
                if c.get('selected_population')and listing and not count_only else [r for r in rows if r['patient_id'] in ids]),
            'planner_receipt': {'route': 'saved_cohort', 'new_paid_api_calls': 0,
                                'origin_request_contract': (c.get('planner_receipt') or {}).get('origin_request_contract')
                                    or (c.get('planner_receipt') or {}).get('request_contract')},
            'cohort_transform': {'parent_context_id': key, 'input_patient_ids': sorted(c['patient_ids']), 'scope': scope, 'operations': operations,
                **({'population_policy':c['selected_population']['proof']['membership_policy']}if c.get('selected_population')else
                   {'population_policy':c['cohort_transform']['population_policy']}if (c.get('cohort_transform')or{}).get('population_policy')else{})},
            'new_paid_api_calls': 0, 'planning_economics': {'known_api_estimate_usd': 0, 'new_api_estimate_usd': 0}}


def evidence_bundle(path, key, *, retained_inspection=False):
    if type(retained_inspection)is not bool:raise ValueError('Explicit retained inspection flag required')
    c = load(path, key, current=not retained_inspection)
    b = Bundle({'workspace': key, 'as_of': c['source']['as_of'], 'synthetic': True,
                'retained_inspection':retained_inspection,'clinical_execution_authorized':False,
                'scope': 'Exact saved answer and internal review work only. No new population, eligibility or clinical outcome may be inferred.'})
    b.add('saved_answer', {'context_id': key}, c, definition=c['question'],
          as_of=c['source']['as_of'], data_fp=fingerprint(c['source']), source='server-saved /ask execution')
    items = work(path, c)
    b.add('review_work', {'context_id': key}, {'items': items, 'counts': dict(Counter(i['state'] for i in items))},
          definition='Internal review states, not outreach, enrollment or clinical completion', data_fp=fingerprint(items))
    return c, b


def source_disposition_identity(context):
    disposition = context.get('source_disposition') or {}
    return fingerprint([disposition.get('status'), disposition.get('suspended_assertions')])


def bundle_validator(path, bundle, *, source_projection=True):
    """Validate against the evidence actually sent, including its work snapshot."""
    def validate_dependencies():
        contexts, checks = {}, []
        for rid, receipt in bundle.receipts.items():
            if receipt['tool'] != 'saved_answer': continue
            key = receipt['args']['context_id']
            fresh = load(path, key, current=not bool(bundle.meta.get('retained_inspection')))
            if bundle.results[rid].get('dependencies') != fresh.get('dependencies'):
                raise ValueError('Saved answer bundle differs from its current sealed derivation.')
            if (source_projection and 'source_disposition' in bundle.results[rid]
                    and source_disposition_identity(bundle.results[rid]) != source_disposition_identity(fresh)):
                raise ValueError('Source-reading status changed during rendering. Regenerate the report from the current review state.')
            contexts[key] = fresh
            checks += fresh['dependency_validation']
        for receipt in bundle.receipts.values():
            if receipt['tool'] != 'review_work': continue
            key = receipt['args']['context_id']
            if key not in contexts or fingerprint(work(path, contexts[key])) != receipt['data_fingerprint']:
                raise ValueError('Review work changed during generation; regenerate the deliverable from current work.')
        return checks
    return validate_dependencies


def compose_frontier(path, request, *, allow_live=False, send=None):
    if set(request) != {'context_id', 'request'} or not isinstance(request['request'], str) or not 1 <= len(request['request'].strip()) <= 4000:
        raise ValueError('A saved answer and a deliverable request up to 4000 characters are required')
    if not allow_live:
        raise ValueError('Frontier composition is disabled on this server; the local command center remains available')
    from .compose import compose
    c, b = evidence_bundle(path, request['context_id'])
    from ..clinical.request_plan import quote_text
    purposes=[s.get('purpose','final') for s in c.get('workflow',[]) if s.get('kind')=='deliverable'
              and s.get('quote') and quote_text(s['quote']) in request['request']]
    if purposes and len(set(purposes))==1:b.meta['deliverable_purpose']=purposes[0]
    # The original question, source, returned evidence and revisioned review
    # work are already in this bundle. A design prompt cannot fetch new facts.
    validate_dependencies = bundle_validator(path, b)
    validate_dependencies()
    return compose(workspace(path, c), request['request'].strip(), evidence_bundle=b, tool_calls=[], send=send,
                   validate_dependencies=validate_dependencies)
