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
    try:return _runtime_model(role,root)
    except (ValueError,KeyError,OSError,sqlite3.Error):return None


def active_identity():
    root=os.environ.get('AGERE_COMPONENT_RELEASE_ROOT')
    try:
        if not root or not (checked_path(root)/'components.sqlite3').is_file():return None
        db=database(root)
    except (ValueError,OSError,sqlite3.Error) as exc:return {'unavailable':type(exc).__name__}
    try:revision=db.execute("SELECT COALESCE(MAX(seq),0) FROM component_events WHERE kind IN ('activated','quarantined')").fetchone()[0]
    finally:db.close()
    return {'root':str(checked_path(root)),'revision':revision,
            'models':{role:str(p) if (p:=runtime_model(role,root)) is not None else None for role in ROLES}}


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
    root=root_for(stack.path);requests=(body or {}).get('learning') or {};review=requests.get('component_review')
    answer=result.get('answer') or {};row={'schema':VERSION,'question':question,'at_ns':time.time_ns(),
        'source_identity':(result.get('execution_snapshot') or {}),'status':answer.get('status'),
        'source_kind':'machine_observation','eligible':False,'role':None,'accepted_labels':[],
        'family':None,'active_models':active_identity(),'clinical_qualification':False}
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
    if review is not None:
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
        for role in ROLES:feedback(os.environ.get('AGERE_COMPONENT_RELEASE_ROOT') or root,role,accepted=False,reason=reason)
        info=(result.get('investigation') or {}).get('recursive_improvement') or {}
        from .harness_improvement import record_feedback
        for ident in {x for x in ((info.get('candidate') or {}).get('id'),(info.get('replay') or {}).get('used')) if x}:
            record_feedback(stack.path,ident,accepted=False,reason=reason)
        row.update(source_kind='user_correction',note=reason)
    db=database(root)
    try:
        with db:
            cur=db.execute('INSERT INTO component_observations(payload) VALUES (?)',(json.dumps(row,sort_keys=True),))
            seq=cur.lastrowid
    finally:db.close()
    return {'status':'recorded','observation':seq,'eligible':row['eligible'],'source_kind':row['source_kind'],'root':str(root)}


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


def run_plan(stack):
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
