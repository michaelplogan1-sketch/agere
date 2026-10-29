"""Explicit, versioned local component learning and release feedback.

Only these new operational tables are read. Model judgments are observations,
not physician approval; training inputs and release activation are explicit.
"""
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from ...contracts import digest

VERSION='component-learning/1'
ROLES=('router','tier_router','shape')
ADAPTER_ROLES=('slm_typed','slm_normalizer','slm_bottled','slm_narrow')
ALL_ROLES=ROLES+ADAPTER_ROLES
_ENCODERS={}


def checked_path(path):
    p=Path(path).resolve()
    # /private/tmp and /private/var are macOS system temp locations; a
    # project/source directory actually named private remains prohibited.
    parts=p.parts[2:] if p.parts[1:3] in (('private','tmp'),('private','var')) else p.parts
    banned={'private','bench','dev3','heldout','held-out','agere-sealed-eval','learning.sqlite3','question-plans.sqlite3','answers.sqlite3'}
    prefixes=('dev3','heldout','held-out','learning.sqlite3','question-plans.sqlite3','answers.sqlite3')
    if any(x.lower() in banned or x.lower().startswith(prefixes) for x in parts):raise ValueError('Protected or legacy input is not an approved learning source')
    return p


def root_for(path):return checked_path(Path(path)/'component-learning')


def database(root):
    root=checked_path(root);root.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(root/'components.sqlite3',timeout=30)
    db.executescript('''CREATE TABLE IF NOT EXISTS component_models(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS component_events(seq INTEGER PRIMARY KEY,model TEXT,kind TEXT,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS component_observations(seq INTEGER PRIMARY KEY,payload TEXT NOT NULL);''')
    for table in ('component_models','component_events','component_observations'):
        for action in ('UPDATE','DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable component history');END")
    db.commit();return db


def event(db,model,kind,payload):
    db.execute('INSERT INTO component_events(model,kind,payload) VALUES (?,?,?)',(model,kind,json.dumps(payload,sort_keys=True)))


def labels(role):
    if role=='router':
        from .learned_router import LABELS
        return list(LABELS)
    if role=='tier_router':
        from .proposers import ORDER
        return list(ORDER)
    if role=='shape':return ['count','value','table','patient_set','composite']
    raise ValueError('Unsupported component role')


def contract(role):
    if role in ADAPTER_ROLES:
        from .adapter_learning import contract as adapter_contract
        return adapter_contract(role[4:])
    from . import learned_router as LR,local_compiler,analysis_graph,tier_router
    from ... import local_encoders
    return digest({'role':role,'labels':labels(role),'cues':LR.cue_names(),'version':LR.VERSION,
                   'component_code':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'encoder_code':hashlib.sha256(Path(local_encoders.__file__).read_bytes()).hexdigest(),
                   'feature_helpers':{m.__name__:hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                       for m in (local_compiler,analysis_graph,tier_router)},
                   'feature_code':hashlib.sha256(Path(LR.__file__).read_bytes()).hexdigest()})


def encoder_reference(encoder):
    if not hasattr(encoder,'path'):return None  # authored test encoders need the supplied matching instance
    from ...local_encoders import PinnedSentenceEncoder
    return {'kind':'pinned_sentence' if isinstance(encoder,PinnedSentenceEncoder) else 'local',
        'path':str(checked_path(encoder.path)),'identity':encoder.identity,'pooling':encoder.pooling,
        **({'manifest_path':str(checked_path(encoder.manifest_path))} if hasattr(encoder,'manifest_path') else {})}


def load_encoder(reference,root):
    """Reuse existing pinned, local-only loaders; no downloads or unpinned remote code."""
    from ...local_encoders import LocalEncoder,PinnedSentenceEncoder
    if reference.get('kind') not in ('local','pinned_sentence'):raise ValueError('Unknown pinned encoder runtime')
    path=checked_path(reference['path'])
    if not path.is_dir():raise ValueError('Pinned local encoder files are unavailable')
    cache=checked_path(root)/'encoder-cache';cache.mkdir(parents=True,exist_ok=True)
    if reference['kind']=='pinned_sentence':
        encoder=PinnedSentenceEncoder(path,manifest_path=checked_path(reference['manifest_path']),cache=cache/(digest(reference)+'.sqlite3'))
    else:encoder=LocalEncoder(path,pooling=reference['pooling'],cache=cache/(digest(reference)+'.sqlite3'))
    if encoder.identity!=reference['identity']:
        encoder.close();raise ValueError('Pinned encoder identity changed')
    return encoder


def model_encoder(model,existing=None):
    if model.get('feature_kind')=='lexical':return None
    if existing is not None and getattr(existing,'identity',None)==model.get('encoder_identity'):return existing
    reference=model.get('encoder_reference');root=os.environ.get('AGERE_COMPONENT_RELEASE_ROOT')
    if not reference or not root:return None
    key=digest(reference)
    try:
        if key not in _ENCODERS:_ENCODERS[key]=load_encoder(reference,root)
        return _ENCODERS[key]
    except (ValueError,KeyError,OSError,RuntimeError,ImportError):return None


def models(root):
    db=database(root)
    try:
        rows=[]
        for ident,payload in db.execute('SELECT id,payload FROM component_models ORDER BY rowid'):
            state=db.execute("SELECT kind,seq FROM component_events WHERE model=? AND kind IN ('activated','quarantined') ORDER BY seq DESC LIMIT 1",(ident,)).fetchone()
            rows.append({**json.loads(payload),'id':ident,'state':state[0] if state else 'candidate','event':state[1] if state else 0})
        return rows
    finally:db.close()


def active(root,role):
    rows=[m for m in models(root) if m['role']==role and m['state']=='activated']
    return max(rows,key=lambda r:r['event']) if rows else None


def feedback_revision(root):
    db=database(root)
    try:return db.execute("SELECT COALESCE(MAX(seq),0) FROM component_events WHERE kind='user_feedback'").fetchone()[0]
    finally:db.close()


def last_feedback(root,role):
    db=database(root)
    try:
        for seq,payload in db.execute("SELECT seq,payload FROM component_events WHERE kind='user_feedback' ORDER BY seq DESC"):
            value=json.loads(payload)
            if value.get('role')==role:return {'revision':seq,**value}
        return None
    finally:db.close()


def _runtime_model(role,root=None):
    root=root or os.environ.get('AGERE_COMPONENT_RELEASE_ROOT')
    if not root or not (checked_path(root)/'components.sqlite3').is_file():return None
    found=active(root,role)
    if not found:return None
    path=checked_path(found['model_path'])
    if found['contract']!=contract(role) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=found['model_sha256']:return None
    return path


def runtime_model(role,root=None):
    try:
        selected=_runtime_model(role,root)
        if selected is not None:return selected
        # Isolated routing candidates may reorder checked proposals; they do
        # not acquire answer authority or alter global activated pointers.
        manifest=os.environ.get('AGERE_ADAPTER_CANDIDATES')
        if not manifest or role not in ('router','tier_router'):return None
        spec=json.loads(checked_path(manifest).read_text())
        if spec.get('schema')!='checked-adapter-candidates/1' or spec.get('authority')!='isolated_checked_proposal':return None
        entry=spec.get('components',{}).get(role)
        if not entry:return None
        found=next((m for m in models(entry['registry']) if m['id']==entry['revision'] and m['role']==role),None)
        if not found or found['state']=='quarantined':return None
        path=checked_path(found['model_path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=found['model_sha256']:return None
        return path
    except (ValueError,KeyError,OSError,sqlite3.Error):return None


def active_identity():
    root=os.environ.get('AGERE_COMPONENT_RELEASE_ROOT')
    try:
        if not root or not (checked_path(root)/'components.sqlite3').is_file():return None
        db=database(root)
    except (ValueError,OSError,sqlite3.Error) as exc:return {'unavailable':type(exc).__name__}
    try:revision=db.execute("SELECT COALESCE(MAX(seq),0) FROM component_events WHERE kind IN ('activated','quarantined') OR (kind='user_feedback' AND json_extract(payload,'$.accepted')=0)").fetchone()[0]
    finally:db.close()
    from .adapter_learning import release_identity
    selected={}
    for role in ALL_ROLES:
        if role in ADAPTER_ROLES:selected[role]=release_identity(role[4:],root)
        else:
            p=runtime_model(role,root);selected[role]=str(p)if p is not None else None
    if revision==0 and not any(selected.values()):return None
    manifest=os.environ.get('AGERE_ADAPTER_CANDIDATES')
    return {'root':str(checked_path(root)),'revision':revision,'models':selected,
            **({'isolated_candidate_manifest':hashlib.sha256(checked_path(manifest).read_bytes()).hexdigest()}if manifest else {})}


def register(root,role,model,qualification,*,parent=None,activate=False):
    root=checked_path(root);payload={'schema':VERSION,'role':role,'contract':contract(role),
        'parent':parent,'model_sha256':digest(model),'qualification':qualification}
    ident=digest(payload);directory=root/'models'/ident;directory.mkdir(parents=True,exist_ok=True)
    path=directory/'model.json';raw=json.dumps(model,sort_keys=True).encode()
    if path.exists() and path.read_bytes()!=raw:raise ValueError('Immutable model revision differs')
    if not path.exists():path.write_bytes(raw)
    payload.update(model_path=str(path),model_sha256=hashlib.sha256(raw).hexdigest())
    db=database(root)
    try:
        with db:
            db.execute('INSERT OR IGNORE INTO component_models VALUES (?,?)',(ident,json.dumps(payload,sort_keys=True)))
            event(db,ident,'qualified',qualification)
            if activate:
                if not qualification.get('selected'):raise ValueError('Unselected candidate cannot be activated')
                event(db,ident,'activated',{'reason':'Explicit candidate release; native answer/evidence checks remain required'})
    finally:db.close()
    return {**payload,'id':ident,'activated':activate}


def feedback(root,role,*,accepted,reason):
    if type(accepted) is not bool or not isinstance(reason,str) or not reason.strip():raise ValueError('Explicit feedback judgment and reason required')
    current=[r for r in models(root) if r['role']==role and r['state']=='activated'];db=database(root)
    try:
        with db:
            event(db,None,'user_feedback',{'role':role,'accepted':accepted,'reason':reason})
            for model in current:
                event(db,model['id'],'physician_feedback',{'accepted':accepted,'reason':reason})
                if not accepted:event(db,model['id'],'quarantined',{'reason':reason})
    finally:db.close()


def rollback(root,role,to_id,reason):
    target=next((r for r in models(root) if r['id']==to_id and r['role']==role),None)
    if not target or not target['qualification'].get('selected') or not reason.strip():raise ValueError('Rollback needs a previously selected revision and reason')
    db=database(root)
    try:
        with db:event(db,to_id,'activated',{'rollback':True,'reason':reason})
    finally:db.close()


def observe(stack,question,result,body=None):
    """Collect new operational observations; only direct client feedback can be human supervision."""
    from .evaluation import active as evaluating
    if evaluating():return {'status':'evaluation_disabled'}
    root=root_for(stack.path);requests=(body or {}).get('learning') or {};review=requests.get('component_review');adapter_review=requests.get('adapter_review')
    if review is not None and adapter_review is not None:raise ValueError('Review one component role per observation')
    answer_review=requests.get('answer_review');derived=[]
    if answer_review is not None:
        if not isinstance(answer_review,dict) or set(answer_review)-{'accepted','note','family','acceptance','population','learners'} or type(answer_review.get('accepted')) is not bool or not isinstance(answer_review.get('note'),str) or not answer_review['note'].strip():raise ValueError('Explicit saved-answer acceptance and reason required')
        if review is not None or adapter_review is not None:raise ValueError('Use answer review or direct component review')
        if not answer_review['accepted']:requests={**requests,'correction':{'note':answer_review['note']}}
    answer=result.get('answer') or {};row={'schema':VERSION,'question':question,'at_ns':time.time_ns(),
        'source_identity':(result.get('execution_snapshot') or {}),'status':answer.get('status'),
        'source_kind':'machine_observation','eligible':False,'role':None,'accepted_labels':[],
        'family':None,'active_models':active_identity(),'clinical_qualification':False}
    if result.get('id'):row['answer_context_id']=result['id']
    # Successful native executions can supervise routing, explicitly tagged as
    # machine supervision. They never become clinical answer labels. Incomplete
    # compound requests and missing downstream work cannot supply positive labels.
    interpretation=(result.get('planner_receipt') or {}).get('interpretation') or {}
    steps=result.get('workflow') or [];expected=answer.get('workflow') or []
    complete=answer.get('status')=='answered' and not answer.get('missing') and interpretation.get('execution_accepted') is True
    complete=complete and answer.get('verification') in ('checked','system_one_reviewed') and all(
        s.get('status') in ('ready','complete','no_matches') for s in steps)
    if expected:complete=complete and [(s.get('kind'),s.get('quote')) for s in steps]==[(s.get('kind'),s.get('quote')) for s in expected]
    if complete:
        tier=(interpretation.get('proposer') or {}).get('name') or interpretation.get('interpreter')
        if tier in labels('tier_router'):
            nodes=(result.get('graph') or {}).get('nodes') or []
            row.update(role='tier_router',accepted_labels=[tier],source_kind='machine_review',eligible=True,
                family='execution/'+digest({'tier':tier,'operations':[n.get('operation') for n in nodes],
                    'parameter_shapes':[sorted((n.get('parameters') or {}).keys()) for n in nodes]}))
    if answer_review and answer_review['accepted']:
        from tools.training.adapter_tasks import reviewed_tasks
        derived,reason=reviewed_tasks(stack,question,result,answer_review)
        if derived:
            row.update(derived[0],source_kind='user_review',eligible=True,note=answer_review['note'])
            for task in derived:feedback(os.environ.get('AGERE_COMPONENT_RELEASE_ROOT') or root,task['role'],accepted=True,reason=answer_review['note'])
        else:row.update(role=None,source_kind='user_answer_review',eligible=False,reason=reason,note=answer_review['note'])
    elif adapter_review is not None:
        from .adapter_learning import role as adapter_role
        from tools.training.adapter_tasks import Context,validate_acceptance
        required={'learner','accepted','target','reference_sql','acceptance','family','note'}
        if not isinstance(adapter_review,dict) or set(adapter_review)-{'population'}!=required:raise ValueError('Adapter review needs learner, accepted, target, reference_sql, acceptance, family and note')
        role=adapter_role(adapter_review['learner'])
        if type(adapter_review['accepted']) is not bool or any(not isinstance(adapter_review[k],str) or not adapter_review[k].strip() for k in ('reference_sql','family','note')):raise ValueError('Explicit adapter review and reason required')
        validate_acceptance(adapter_review['acceptance'])
        row.update({k:adapter_review[k] for k in ('target','reference_sql','acceptance','family','note')})
        if 'population' in adapter_review:
            population=adapter_review['population']
            if not isinstance(population,list) or any(not isinstance(pid,str) for pid in population):raise ValueError('Explicit reviewed patient population required')
            row['population']=population
        row.update(role=role,source_kind='user_review',eligible=adapter_review['accepted'],source_digest=Context(stack.path).identity)
        feedback(os.environ.get('AGERE_COMPONENT_RELEASE_ROOT') or root,role,accepted=adapter_review['accepted'],reason=adapter_review['note'])
        info=(result.get('investigation') or {}).get('recursive_improvement') or {}
        from .harness_improvement import record_feedback
        for ident in {x for x in ((info.get('candidate') or {}).get('id'),(info.get('replay') or {}).get('used')) if x}:
            record_feedback(stack.path,ident,accepted=adapter_review['accepted'],reason=adapter_review['note'])
    elif review is not None:
        if not isinstance(review,dict) or set(review)!={'role','accepted','accepted_labels','family','note'}:raise ValueError('Component review needs role, accepted, accepted_labels, family and note')
        role=review['role'];allowed=labels(role)
        if type(review['accepted']) is not bool or not isinstance(review['accepted_labels'],list) or any(x not in allowed for x in review['accepted_labels']):raise ValueError('Invalid reviewed component labels')
        if not isinstance(review['family'],str) or not review['family'].strip() or not isinstance(review['note'],str) or not review['note'].strip():raise ValueError('Feedback needs its source family and reason')
        if review['accepted'] and not review['accepted_labels']:raise ValueError('Accepted feedback needs at least one acceptable label')
        row.update(role=role,accepted_labels=review['accepted_labels'],family=review['family'],
            source_kind='user_review',eligible=review['accepted'],note=review['note'])
        model_root=os.environ.get('AGERE_COMPONENT_RELEASE_ROOT') or root
        feedback(model_root,role,accepted=review['accepted'],reason=review['note'])
        info=(result.get('investigation') or {}).get('recursive_improvement') or {}
        candidates={x for x in ((info.get('candidate') or {}).get('id'),(info.get('replay') or {}).get('used')) if x}
        from .harness_improvement import record_feedback
        for ident in candidates:record_feedback(stack.path,ident,accepted=review['accepted'],reason=review['note'])
    elif requests.get('correction'):
        # A direct client correction retires learned components and execution
        # reuse without inventing replacement labels from an unreviewed answer.
        reason=str(requests['correction'].get('note') or 'User correction')
        for role in ALL_ROLES:feedback(os.environ.get('AGERE_COMPONENT_RELEASE_ROOT') or root,role,accepted=False,reason=reason)
        info=(result.get('investigation') or {}).get('recursive_improvement') or {}
        from .harness_improvement import record_feedback
        for ident in {x for x in ((info.get('candidate') or {}).get('id'),(info.get('replay') or {}).get('used')) if x}:
            record_feedback(stack.path,ident,accepted=False,reason=reason)
        from .harness_improvement import record_question_correction
        record_question_correction(stack.path,question,reason)
        row.update(source_kind='user_correction',note=reason)
    db=database(root)
    try:
        with db:
            cur=db.execute('INSERT INTO component_observations(payload) VALUES (?)',(json.dumps(row,sort_keys=True),))
            seq=cur.lastrowid
            for task in derived[1:]:
                db.execute('INSERT INTO component_observations(payload) VALUES (?)',(json.dumps({**row,**task},sort_keys=True),))
    finally:db.close()
    return {'status':'recorded','observation':seq,'derived_adapter_tasks':len(derived),'eligible':row['eligible'],'source_kind':row['source_kind'],'root':str(root)}


def handle_feedback(stack,body):
    """Existing correction endpoint: review an exact saved execution without legacy stores."""
    from ..deliverables import answer_context
    if not isinstance(body,dict) or set(body)-{'context_id','question','note','accepted','acceptance','family','learners','expected','rule'}:raise ValueError('Unknown saved-answer feedback fields')
    accepted=body.get('accepted',False)
    if type(accepted) is not bool or not isinstance(body.get('note'),str) or not body['note'].strip():raise ValueError('Explicit feedback judgment and note required')
    result={};question=body.get('question');key=body.get('context_id')
    if key is not None:
        import re
        if not isinstance(key,str) or not re.fullmatch(r'ANS-[0-9a-f]{24}',key):raise ValueError('Exact saved answer context required')
        data=json.loads((answer_context.root(stack.path)/key/'answer.json').read_text())
        if question and question not in (data['question'],data.get('original_question')):raise ValueError('Feedback question differs from saved execution')
        question=data['question'];result=data
        if accepted:
            from .shared_planner import policy_identity
            if data['source']!=answer_context.source_identity(stack.path) or data.get('context_policy_identity')!=policy_identity():raise ValueError('Saved answer source/policy changed; rerun before using it for adapter learning')
    elif accepted:raise ValueError('Positive adapter feedback requires an exact saved answer context')
    if not isinstance(question,str) or not question.strip():raise ValueError('Feedback question required')
    review={k:body[k] for k in ('acceptance','family','learners') if k in body}|{'accepted':accepted,'note':body['note']}
    if key is not None:review['population']=result.get('input_patient_ids')
    if review.get('population') is None:review.pop('population',None)
    receipt=observe(stack,question,result,{'learning':{'answer_review':review}})
    if body.get('expected') is not None or body.get('rule') is not None:
        db=database(root_for(stack.path))
        try:
            with db:event(db,None,'reported_rule_feedback',{'question':question,'note':body['note'],
                'expected':body.get('expected'),'rule':body.get('rule'),'status':'unverified_report; not a training label or rule change'})
        finally:db.close()
    effect='Review saved to help improve future answers.' if accepted else 'Correction recorded. Related answers require revalidation.'
    return {**receipt,'context_id':key,'learning_cycle':run_plan(stack),'effect':effect}


def export(root,output,role,family_splits,*,include_machine=False):
    """Export explicitly reviewed NEW observations; latest feedback supersedes earlier labels."""
    root=checked_path(root);output=checked_path(output);db=database(root)
    try:rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM component_observations ORDER BY seq')]
    finally:db.close()
    latest={}
    for row in rows:
        if row.get('role')==role or row.get('source_kind')=='user_correction':
            latest[' '.join(row['question'].split()).casefold()]=row
    selected=[]
    for row in latest.values():
        if not row['eligible'] or row['source_kind']=='machine_review' and not include_machine:continue
        split=family_splits.get(row['family'])
        if split not in ('fit','calibration','verification'):raise ValueError('Every reviewed family needs an explicit split')
        selected.append({k:row[k] for k in ('question','accepted_labels','family','source_kind')}|{'split':split})
    output.mkdir(parents=True,exist_ok=True);revision=digest(selected);path=output/('reviewed-'+revision+'.jsonl')
    path.write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in selected))
    manifest={'schema':'approved-component-data/1','role':role,'files':[{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}],
        'allow_machine_supervision':include_machine,
        'provenance':'Newly recorded operational classification tasks with separate user/machine supervision; not clinical answer keys'}
    target=output/('manifest-'+revision+'.json');target.write_text(json.dumps(manifest,indent=2)+'\n');return target


def family_partition(root,role,rule):
    """Assign NEW source families consistently before looking at their labels."""
    if set(rule)!={'salt','fit','calibration','verification'} or not isinstance(rule['salt'],str) or not rule['salt']:
        raise ValueError('Family partition needs a stable salt and three positive proportions')
    if any(type(rule[k]) not in (int,float) or not 0<rule[k]<1 for k in ('fit','calibration','verification')) or abs(sum(rule[k] for k in ('fit','calibration','verification'))-1)>1e-9:
        raise ValueError('Family partition proportions must sum to one')
    db=database(root)
    try:rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM component_observations ORDER BY seq')]
    finally:db.close()
    families={r['family'] for r in rows if r.get('role')==role and r.get('family')}
    out={}
    for family in families:
        number=int(digest([rule['salt'],family]),16)/(16**64)
        out[family]='fit' if number<rule['fit'] else 'calibration' if number<rule['fit']+rule['calibration'] else 'verification'
    return out


def run_plan(stack,*,train_adapters=False):
    """Optional in-process offline learning cycle; no scheduler, provider or legacy discovery."""
    plan_path=os.environ.get('AGERE_COMPONENT_LEARNING_PLAN')
    if not plan_path:return {'status':'observation_only','reason':'No explicit learning plan configured','provider_calls':0}
    from .evaluation import active as evaluating
    if evaluating():return {'status':'evaluation_disabled','provider_calls':0}
    plan=json.loads(checked_path(plan_path).read_text())
    if plan.get('schema')!='component-learning-plan/1' or not isinstance(plan.get('jobs'),list):raise ValueError('Explicit component-learning-plan/1 required')
    import fcntl
    source=root_for(stack.path);root=checked_path(os.environ.get('AGERE_COMPONENT_RELEASE_ROOT') or source)
    root.mkdir(parents=True,exist_ok=True)
    with (root/'learning.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'busy','provider_calls':0}
        from tools.training import approved
        results=[]
        for job in plan['jobs']:
            if isinstance(job,dict) and 'learner' in job:
                from . import adapter_learning as adapters
                from tools.training import adapters as trainer
                if set(job)-{'learner','base','manifest','family_splits','partition','training','install'}:raise ValueError('Unknown adapter learning job fields')
                role=adapters.role(job['learner'])
                if type(job.get('install',False)) is not bool:raise ValueError('Explicit adapter install boolean required')
                if 'manifest' in job:
                    manifest=checked_path(checked_path(plan_path).parent/job['manifest'])
                    if json.loads(manifest.read_text()).get('learner')!=job['learner']:raise ValueError('Adapter learner differs from manifest')
                else:
                    if ('family_splits' in job)==('partition' in job):raise ValueError('Choose explicit adapter families or stable partition')
                    splits=job.get('family_splits') if 'family_splits' in job else family_partition(source,role,job['partition'])
                    manifest=adapters.export(source,root/'reviewed-exports'/role,job['learner'],splits)
                    spec=json.loads(manifest.read_text());spec['training']=job.get('training')
                    manifest=manifest.with_name('configured-'+digest(spec)+'.json');manifest.write_text(json.dumps(spec,indent=2)+'\n')
                try:
                    from tools.training.adapter_tasks import load as load_adapter_tasks
                    load_adapter_tasks(manifest)
                except ValueError as exc:
                    if 'Need separate nonempty' not in str(exc):raise
                    results.append({'role':role,'status':'waiting_for_reviewed_families','reason':str(exc),'provider_calls':0});continue
                request={'manifest':str(manifest),'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
                    'implementation':adapters.contract(job['learner']),
                    'base':str(checked_path(checked_path(plan_path).parent/job['base'])),
                    'stack':str(checked_path(stack.path)),'install':job.get('install',False),'learner':job['learner']}
                ident=digest(request)
                if train_adapters:
                    try:result=trainer.run(manifest,root,stack=stack.path,base=request['base'],train=True,install=request['install'])
                    except ValueError as exc:
                        if 'Need separate nonempty' not in str(exc):raise
                        result={'status':'waiting_for_reviewed_families','reason':str(exc),'provider_calls':0}
                    db=database(root)
                    try:
                        with db:event(db,ident,'adapter_training_completed',{'status':result['status'],'revision':result.get('revision')})
                    finally:db.close()
                else:
                    db=database(root)
                    try:
                        completed=db.execute("SELECT payload FROM component_events WHERE model=? AND kind='adapter_training_completed' ORDER BY seq DESC LIMIT 1",(ident,)).fetchone()
                        if not db.execute("SELECT 1 FROM component_events WHERE model=? AND kind='adapter_training_requested'",(ident,)).fetchone():
                            with db:event(db,ident,'adapter_training_requested',request)
                    finally:db.close()
                    result={'status':'adapter_job_completed' if completed else 'queued_offline_adapter_job','request':request,
                        'completion':json.loads(completed[0]) if completed else None,'provider_calls':0}
                results.append({'role':role,**result});continue
            if not isinstance(job,dict) or set(job)-{'role','manifest','family_splits','partition','include_machine','encoders','install'}:raise ValueError('Unknown learning job fields')
            role=job.get('role');labels(role)
            if type(job.get('install',False)) is not bool or type(job.get('include_machine',False)) is not bool:raise ValueError('Explicit boolean install/include_machine required')
            if 'manifest' in job:
                if set(job)-{'role','manifest','install'}:raise ValueError('Use an approved manifest or new reviewed observations')
                manifest=checked_path(checked_path(plan_path).parent/job['manifest'])
                if json.loads(manifest.read_text()).get('role')!=role:raise ValueError('Learning role differs from manifest')
            else:
                if ('family_splits' in job)==('partition' in job):raise ValueError('Choose explicit source-family splits or a stable family partition')
                splits=job.get('family_splits') if 'family_splits' in job else family_partition(source,role,job['partition'])
                manifest=export(source,root/'reviewed-exports'/role,role,splits,include_machine=job.get('include_machine',False))
                if job.get('encoders'):
                    spec=json.loads(manifest.read_text());spec['encoders']=job['encoders']
                    manifest=manifest.with_name('encoders-'+digest(spec)+'.json');manifest.write_text(json.dumps(spec,indent=2)+'\n')
            try:
                result=approved.run(manifest,root,encoder=getattr(getattr(stack,'retriever',None),'encoder',None),install=job.get('install',False))
            except ValueError as exc:
                if 'Need separate nonempty' not in str(exc):raise
                result={'status':'waiting_for_reviewed_families','reason':str(exc),'provider_calls':0}
            results.append({'role':role,**result})
        return {'status':'completed','jobs':results,'provider_calls':0}


def _shape_hint(stack,question):
    path=runtime_model('shape')
    if not path:return None
    from . import learned_router as LR
    import numpy as np
    raw=json.loads(path.read_text());encoder=model_encoder(raw,getattr(getattr(stack,'retriever',None),'encoder',None))
    if raw.get('feature_kind')=='lexical':X=np.asarray([LR.LEXICAL_SCALE*LR.lexical(question)])
    elif encoder is not None and encoder.identity==raw.get('encoder_identity'):X=LR.features([question],encoder)
    else:return None
    p=LR.softmax((X@np.asarray(raw['weights']).T+np.asarray(raw['bias']))/raw['temperature'])[0];i=int(p.argmax())
    return {'label':raw['labels'][i],'probability':float(p[i]),'confident':bool(p[i]>=raw['threshold']),
            'model':str(path),'role':'shape','instruction':'Interpretation hint only; explicit question wording and native answer checks remain authoritative'}


def shape_hint(stack,question):
    try:return _shape_hint(stack,question)
    except (ValueError,KeyError,OSError,RuntimeError,ImportError):return None
