"""Explicit, work-scoped retirement of machine context with immutable history.

An operational report is not clinician adjudication. Original answers and old
assessments remain intact. Retirements are exact revision-bound exceptions to
the ordinary attachment-preservation rule, never silent context removal.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from ...contracts import digest
from . import program_work_v1 as S, synthesis_work as synthesis
from . import work_context_preservation as preservation

VERSION = 'work-answer-retirement/1'
REASONS = {'stale_dependencies', 'replaced_for_review', 'outside_work_scope', 'withdrawn_for_review'}
AUTHORITY = 'self_reported_local_context_change_not_clinical_review'


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), preservation.identity()])


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        raise ValueError('Exact retirement request fields required')


def ids(value, *, empty=False):
    if not isinstance(value, list) or not (0 if empty else 1) <= len(value) <= 8 or any(not isinstance(v, str) or not re.fullmatch('[a-f0-9]{64}', v) for v in value) or len(set(value)) != len(value):
        raise ValueError('Distinct exact saved answer IDs required')
    return sorted(value)


def database(path):
    db = S.database(path)
    # Migrate the old guard atomically. New guards still reject every ordinary
    # answer loss, and only allow a recorded exact before/after transition.
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('CREATE TABLE IF NOT EXISTS answer_retirement_plans(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS answer_retirements(id TEXT PRIMARY KEY,item TEXT NOT NULL,before_revision INTEGER NOT NULL,after_revision INTEGER NOT NULL,before_assessment TEXT NOT NULL,after_assessment TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(item,before_revision))')
        db.execute('CREATE TABLE IF NOT EXISTS answer_retirement_requests(id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,payload TEXT NOT NULL)')
        for table in ('answer_retirement_plans', 'answer_retirements', 'answer_retirement_requests'):
            for operation in ('UPDATE', 'DELETE'):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Answer retirement history is immutable'); END")
        db.execute('''CREATE TRIGGER IF NOT EXISTS preserve_machine_answer_attachments_v2
          BEFORE UPDATE OF payload ON work_items WHEN EXISTS (
            SELECT 1 FROM work_assessments old_a,json_each(old_a.payload,'$.synthesis_context.answers') old_answer
            WHERE old_a.id=json_extract(OLD.payload,'$.assessment_id')
            AND NOT EXISTS (SELECT 1 FROM work_assessments new_a,json_each(new_a.payload,'$.synthesis_context.answers') new_answer
              WHERE new_a.id=json_extract(NEW.payload,'$.assessment_id')
              AND json_extract(new_answer.value,'$.answer_id')=json_extract(old_answer.value,'$.answer_id'))
            AND NOT EXISTS (SELECT 1 FROM answer_retirements r,json_each(r.payload,'$.removed_answer_ids') removed
              WHERE r.item=OLD.id AND r.before_revision=OLD.revision AND r.after_revision=NEW.revision
              AND r.before_assessment=json_extract(OLD.payload,'$.assessment_id')
              AND r.after_assessment=json_extract(NEW.payload,'$.assessment_id')
              AND removed.value=json_extract(old_answer.value,'$.answer_id'))
          ) BEGIN SELECT RAISE(ABORT,'Attached machine answers would be lost; prepare a current proposal'); END''')
        for operation in ('INSERT', 'UPDATE'):
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS prevent_retired_answer_restore_{operation}
              BEFORE {operation} ON work_items WHEN EXISTS (
                SELECT 1 FROM answer_retirements r,json_each(r.payload,'$.removed_answer_ids') removed,
                  work_assessments a,json_each(a.payload,'$.synthesis_context.answers') active
                WHERE r.item=NEW.id AND a.id=json_extract(NEW.payload,'$.assessment_id')
                AND removed.value=json_extract(active.value,'$.answer_id')
              ) BEGIN SELECT RAISE(ABORT,'Retired answer cannot be silently reattached to this work item'); END''')
        db.execute('DROP TRIGGER IF EXISTS preserve_machine_answer_attachments_v1')
        db.commit()
    except Exception:
        db.rollback(); db.close(); raise
    return db


def load(db, table, key):
    if table not in {'answer_retirement_plans', 'answer_retirements'}:
        raise ValueError('Unknown retirement table')
    row = db.execute('SELECT payload FROM '+table+' WHERE id=?', (key,)).fetchone()
    if not row:
        raise ValueError('Unknown retirement receipt')
    value = json.loads(row[0])
    if value['id'] != digest({k: v for k, v in value.items() if k != 'id'}):
        raise S.WorkConflict('Retirement receipt integrity changed')
    return value


def answer_state(path, context, answer):
    single = {**context, 'answers': [answer]}
    try:
        synthesis.verify(path, [single])
        return {'answer_id': answer['answer_id'], 'current': True, 'reason': None}
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {'answer_id': answer['answer_id'], 'current': False, 'reason': str(exc)[:400]}


def affected_assessments(db, item_id, removed):
    keys = {json.loads(raw)['assessment_id'] for raw, in db.execute('SELECT payload FROM work_events WHERE item=?', (item_id,))}
    keys.add(S.load(db, 'work_items', item_id)['assessment_id'])
    return sorted(key for key in keys if removed & {a['answer_id'] for a in (S.load(db, 'work_assessments', key).get('synthesis_context') or {}).get('answers', [])})


def buyer_impact(db, assessment_ids):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='capability_tasks'").fetchone():
        return []
    rows=[]
    for key,raw in db.execute('SELECT t.id,p.payload FROM capability_tasks t JOIN capability_plans p ON p.id=json_extract(t.payload,\'$.plan_id\')'):
        origin=json.loads(raw)['request']['origin']
        if origin['kind']=='assessment' and origin['id'] in assessment_ids:
            rows.append({'task_id':key,'assessment_id':origin['id'],'effect':'requires_new_assessment_origin'})
    return sorted(rows,key=lambda r:r['task_id'])


def inspect(path, item_id):
    db=database(path)
    try:
        item=S.load(db,'work_items',S.bounded(item_id,'work item ID',64));a=S.load(db,'work_assessments',item['assessment_id'])
        context=a.get('synthesis_context');answers=[]
        if context:
            answers=[{**answer_state(path,context,v),'question':v.get('question'),'answer_hash':v.get('answer_hash')} for v in context['answers']]
        history=[load(db,'answer_retirements',key) for key, in db.execute('SELECT id FROM answer_retirements WHERE item=? ORDER BY before_revision',(item_id,))]
        return {'schema':VERSION,'item_id':item_id,'patient_id':item['patient_id'],'program_id':item['program_id'],
                'revision':item['revision'],'assessment_id':a['id'],'answers':answers,'retirements':history,
                'authority':AUTHORITY,'human_review_recorded':False,'clinical_execution_authorized':False}
    finally:db.close()


def prepare(path, request):
    exact(request,'item_id expected_revision answer_ids replacement_ids reason note')
    removed=ids(request['answer_ids']);replacements=ids(request['replacement_ids'],empty=True)
    if set(removed)&set(replacements):raise ValueError('A retired answer cannot replace itself')
    S.bounded(request['item_id'],'work item ID',64)
    if not isinstance(request['reason'],str) or request['reason'] not in REASONS:raise ValueError('Known operational retirement reason required')
    S.bounded(request['note'],'retirement explanation',3000)
    if type(request['expected_revision']) is not int or request['expected_revision']<1:raise ValueError('Positive work revision required')
    db=database(path)
    try:
        item=S.load(db,'work_items',request['item_id']);a=S.load(db,'work_assessments',item['assessment_id'])
        if item['revision']!=request['expected_revision']:raise S.WorkConflict('Work item changed; reload')
        context=a.get('synthesis_context');old={v['answer_id']:v for v in (context or {}).get('answers',[])}
        if not set(removed)<=set(old):raise ValueError('Retirement must name answers attached to this work item')
        states=[answer_state(path,context,old[key]) for key in removed]
        if request['reason']=='stale_dependencies' and any(r['current'] for r in states):
            raise ValueError('Stale-dependency retirement requires failed current verification for every removed answer')
        if request['reason']=='replaced_for_review' and not replacements:raise ValueError('Replacement answers required')
        remaining=sorted(set(old)-set(removed))
        if remaining:synthesis.verify(path,[{**context,'answers':[old[key] for key in remaining]}])
        new_ids=sorted(set(remaining)|set(replacements))
        retired={value for raw, in db.execute('SELECT payload FROM answer_retirements WHERE item=?',(item['id'],)) for value in json.loads(raw)['removed_answer_ids']}
        if retired & set(new_ids):raise S.WorkConflict('Retired answer cannot be silently reattached to this work item')
        new_contexts=synthesis.contexts(path,new_ids,[item['patient_id']]) if new_ids else {}
        # Other evidence is explicitly inherited and verified by the frozen
        # engine. Staleness there is a separate blocker, never sanitized away.
        kwargs={'synthesis_contexts':new_contexts}
        for field in ('decision_context','accountability_context'):
            if a.get(field) is not None:kwargs[field+'s']={item['patient_id']:a[field]}
        from .program_work import engine
        target=engine(item['program_id']).prepare(path,item['program_id'],[item['patient_id']],**kwargs)
        proposal=S.load(db,'work_proposals',target['proposal_id']);new_assessment=proposal['assessment_ids'][0]
        if new_assessment==a['id']:raise ValueError('Retirement must change the active assessment')
        affected=affected_assessments(db,item['id'],set(removed))
        result={'schema':VERSION,'implementation_identity':identity(),'item_id':item['id'],'patient_id':item['patient_id'],
                'program_id':item['program_id'],'expected_revision':item['revision'],'before_item_hash':digest(item),
                'before_assessment_id':a['id'],'after_assessment_id':new_assessment,'proposal_id':target['proposal_id'],
                'removed_answer_ids':removed,'replacement_answer_ids':replacements,'retained_answer_ids':remaining,
                'reason':request['reason'],'note':request['note'],'removed_answer_status':states,
                'affected_assessment_ids':affected,'downstream_tasks':buyer_impact(db,affected),'authority':AUTHORITY,
                'reassessment_required':True,'human_review_recorded':False,'clinical_execution_authorized':False}
        if S.load(db,'work_items',item['id'])!=item:raise S.WorkConflict('Work changed during retirement preparation')
        result['id']=digest(result)
        with db:db.execute('INSERT OR IGNORE INTO answer_retirement_plans VALUES (?,?)',(result['id'],json.dumps(result)))
        return result
    finally:db.close()


def verify_proposal(path, db, plan):
    from .program_work import engine
    p=S.load(db,'work_proposals',plan['proposal_id']);e=engine(plan['program_id'])
    if p['id']!=digest({k:v for k,v in p.items() if k!='id'}):raise S.WorkConflict('Retirement target proposal integrity changed')
    code=e.code_identity(plan['program_id']) if hasattr(e,'adapter') else e.code_identity()
    if p['code_identity']!=code or p['source']!=S.metadata(path) or p['program']!=e.definition(plan['program_id']):
        raise S.WorkConflict('Retirement target source or program changed; prepare again')
    if p['assessment_ids']!=[plan['after_assessment_id']] or [x[0] for x in p['input_hashes']]!=[plan['patient_id']]:
        raise S.WorkConflict('Retirement target patient scope changed')
    src=S.warehouse(path)
    try:rows=e.analysis_rows(path,src,p['program'],[plan['patient_id']])
    finally:src.close()
    if any((hashlib.sha256(rows[pid].encode()).hexdigest() if pid in rows else None)!=h for pid,h in p['input_hashes']):
        raise S.WorkConflict('Retirement target evidence changed; prepare again')
    a=S.load(db,'work_assessments',plan['after_assessment_id'])
    if a['id']!=digest({k:v for k,v in a.items() if k!='id'}) or a['patient_id']!=plan['patient_id'] or a['program']!=p['program'] or a['source']!=p['source']:
        raise S.WorkConflict('Retirement target assessment integrity changed')
    actual={v['answer_id'] for v in (a.get('synthesis_context') or {}).get('answers',[])}
    if actual!=set(plan['retained_answer_ids'])|set(plan['replacement_answer_ids']):raise S.WorkConflict('Retirement target answer scope changed')
    old=S.load(db,'work_assessments',plan['before_assessment_id'])
    if any(old.get(k)!=a.get(k) for k in ('decision_context','accountability_context')):raise S.WorkConflict('Unrelated work context changed')
    from .selection_context import verify
    from .program_accountability import verify_contexts
    verify(path,[a['decision_context']] if a.get('decision_context') else [])
    verify_contexts(path,[a['accountability_context']] if a.get('accountability_context') else [])
    synthesis.verify(path,[a['synthesis_context']] if a.get('synthesis_context') else [])
    return a


def apply(path, request):
    exact(request,'plan_id actor request_id');actor=S.bounded(request['actor'],'recorded actor',120);rid=S.bounded(request['request_id'],'request ID',120)
    db=database(path);key=digest(request)
    try:
        db.execute('BEGIN IMMEDIATE')
        prior=db.execute('SELECT request_hash,payload FROM answer_retirement_requests WHERE id=?',(rid,)).fetchone()
        if prior:
            if prior[0]!=key:raise S.WorkConflict('Request ID already used for a different retirement')
            return {**json.loads(prior[1]),'idempotent_replay':True}
        plan=load(db,'answer_retirement_plans',request['plan_id']);item=S.load(db,'work_items',plan['item_id'])
        if plan['implementation_identity']!=identity():raise S.WorkConflict('Retirement implementation changed; prepare again')
        if digest(item)!=plan['before_item_hash'] or item['revision']!=plan['expected_revision']:raise S.WorkConflict('Work changed after retirement preview; reload')
        a=verify_proposal(path,db,plan);before=item['state']
        if plan['reason']=='stale_dependencies':
            old=S.load(db,'work_assessments',plan['before_assessment_id'])['synthesis_context']
            if any(answer_state(path,old,v)['current'] for v in old['answers'] if v['answer_id'] in plan['removed_answer_ids']):
                raise S.WorkConflict('Removed answer now verifies as current; prepare a new explanation')
        receipt={**{k:v for k,v in plan.items() if k!='id'},'plan_id':plan['id'],'actor':actor,'recorded_at':S.now(),
                 'after_revision':item['revision']+1,'downstream_tasks':buyer_impact(db,plan['affected_assessment_ids'])}
        receipt['id']=digest(receipt)
        db.execute('INSERT INTO answer_retirements VALUES (?,?,?,?,?,?,?)',(receipt['id'],item['id'],item['revision'],item['revision']+1,item['assessment_id'],a['id'],json.dumps(receipt)))
        item=copy.deepcopy(item);item.update(revision=item['revision']+1,assessment_id=a['id'],reassessment_required=True,
                                            review_candidate=a['review_candidate'],candidate_status=a['status'])
        if before in S.TERMINAL:item['state']='review_needed';item['cycle']+=1
        S.append_event(db,item,actor,'machine_context_retired',{'retirement_id':receipt['id'],'note':plan['note'],
            'removed_answer_ids':plan['removed_answer_ids'],'replacement_answer_ids':plan['replacement_answer_ids'],
            'human_review_recorded':False},before)
        result={'schema':VERSION,'item':item,'retirement':receipt,'external_actions_executed':0,'human_review_recorded':False}
        db.execute('INSERT INTO answer_retirement_requests VALUES (?,?,?)',(rid,key,json.dumps(result)));db.commit()
        return {**result,'idempotent_replay':False}
    except Exception:db.rollback();raise
    finally:db.close()


def materialize(path, engine, proposal_id, actor):
    database(path).close()
    try:return engine.materialize(path,proposal_id,actor)
    except sqlite3.IntegrityError as exc:
        if 'would be lost' in str(exc) or 'Retired answer' in str(exc):raise S.WorkConflict(str(exc)) from exc
        raise


def assert_origin_active(path, request):
    if not isinstance(request,dict) or request.get('kind')!='assessment':return
    # Read-only check: do not acquire a write/schema lock inside buyer operations.
    db=sqlite3.connect((Path(path)/'program-work.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='answer_retirements'").fetchone():return
        row=db.execute("SELECT r.id FROM answer_retirements r,json_each(r.payload,'$.affected_assessment_ids') a WHERE a.value=? LIMIT 1",(request.get('id'),)).fetchone()
        if row:raise S.WorkConflict('Assessment includes explicitly retired machine context; use the current work assessment')
    finally:db.close()


def install_origin_guard():
    """Service bootstrap guard; all existing buyer adapters call this origin hook."""
    from . import care_capabilities as care
    if getattr(care.origin,'retirement_guard',False):return
    original=care.origin
    def guarded(path,request):
        assert_origin_active(path,request)
        value=original(path,request)
        assert_origin_active(path,request)
        return value
    guarded.retirement_guard=True
    care.origin=guarded
