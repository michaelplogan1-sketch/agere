"""Parallel immutable learning cohorts with inherited development boundaries.

The attached source-review queue stays in place. New samples use a detached input
view, shared received sources, and their own queue/plan. Prior split assignments
are constraints, never labels; conflicting connected components are quarantined.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import json
import re
import sqlite3
import time

from ...contracts import digest
from . import learning_queue as queue, semantic_reader as reader, semantic_student as student
from . import source_distillation as legacy, distillation_qualification as qualification
from . import local_decisions, learning_suggestions

VERSION = 'source-learning-cohorts/1'
SALT = 'agere-development-extension-60-20-20-v1'
VIEW_FILES = ('run.json', 'stack.sqlite3', 'annotation-link.json', 'longitudinal-link.json')
LIMITS = legacy.LIMITS + [
    'Previous development assignments constrain new connected components. Conflicting inherited partitions are excluded, not reassigned.',
    'The larger development sample is not an independent clinical evaluation. Earlier annotation/model exposure may remain.',
    'Question-triggered source tasks and the attached fixed audit sample are preserved separately.',
]


def identity():
    return digest([VERSION, queue.sha(__file__), legacy.code_identity(), reader.identity()])


def write(path, value):
    path=Path(path); tmp=path.with_suffix('.tmp')
    queue.write(tmp,value);tmp.replace(path)


def registry(stack):
    db=sqlite3.connect(Path(stack)/'learning-cohorts.sqlite3',timeout=30)
    db.executescript('''CREATE TABLE IF NOT EXISTS cohorts(id TEXT PRIMARY KEY,path TEXT NOT NULL,manifest_sha256 TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS immutable_cohort_update BEFORE UPDATE ON cohorts BEGIN SELECT RAISE(ABORT,'Cohorts are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_cohort_delete BEFORE DELETE ON cohorts BEGIN SELECT RAISE(ABORT,'Cohorts are append-only'); END;''')
    return db


def keys(row):
    return [('patient',row['patient_family']),('source',row['source_hash']),('template',row['template']),
            ('input',digest(student.model_text(row['input'])))]


def partition(rows, anchors):
    all_rows=[*rows,*anchors]; parent=list(range(len(all_rows)));seen={}
    def root(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    for i,row in enumerate(all_rows):
        for key in keys(row):
            if key in seen:parent[root(i)]=root(seen[key])
            else:seen[key]=i
    groups=defaultdict(list)
    for i in range(len(all_rows)):groups[root(i)].append(i)
    selected=[];excluded=[]
    for indices in groups.values():
        current=[i for i in indices if i<len(rows)]
        if not current:continue
        inherited={all_rows[i]['partition'] for i in indices if i>=len(rows)}
        component=digest(sorted({digest(keys(all_rows[i])) for i in indices}))
        if len(inherited)>1:
            excluded.extend({'item_id':rows[i]['item_id'],'reason':'conflicting_inherited_partitions',
                'component':component,'inherited_partitions':sorted(inherited)} for i in current)
            continue
        bucket=int(digest([SALT,component])[:16],16)%10000
        part=next(iter(inherited)) if inherited else 'train' if bucket<6000 else 'calibration' if bucket<8000 else 'validation'
        selected.extend({**rows[i],'partition':part,'component':component,
            'partition_basis':'inherited' if inherited else 'predeclared_hash_60_20_20'} for i in current)
    return sorted(selected,key=lambda r:r['item_id']),sorted(excluded,key=lambda r:r['item_id'])


def anchor_plan(path):
    path=Path(path).resolve();m=json.loads(path.read_text());target=path.parent/'targets.json'
    if queue.sha(target)!=m['files']['targets.json']:raise ValueError('Prior development targets changed')
    rows=json.loads(target.read_text())
    if any(r['partition'] not in student.PARTITIONS for r in rows):raise ValueError('Invalid inherited partition')
    return rows,{'path':str(path),'sha256':queue.sha(path),'targets_sha256':queue.sha(target)}


def prepare(stack, output, *, size=500, anchor_paths=()):
    stack,output=Path(stack).resolve(),Path(output).resolve()
    original_queue,original_run,_,_=queue.connected(stack)
    attached_before={name:queue.sha(stack/name) for name in ('learning-queue-link.json','source-distillation-link.json') if (stack/name).exists()}
    if type(size) is not int or not 4<=size<=500:raise ValueError('Cohort sample size must be 4–500')
    if output.exists():raise ValueError('Fresh cohort directory required')
    paths={Path(p).resolve() for p in anchor_paths}
    if (stack/'source-distillation-link.json').exists():
        link=json.loads((stack/'source-distillation-link.json').read_text());p=Path(link['path'])/'plan.json'
        if queue.sha(p)!=link['plan_sha256']:raise ValueError('Attached development plan changed')
        paths.add(p.resolve())
    db=registry(stack)
    try:registered=list(db.execute('SELECT id,path,manifest_sha256 FROM cohorts'))
    finally:db.close()
    older=[]
    for previous_id,p,h in registered:
        prior=Path(p)/'plan.json'
        if queue.sha(prior)!=h or digest(json.loads(prior.read_text()))!=previous_id:raise ValueError('Registered prior plan changed')
        older.append(prior)
    paths.update(older);anchors=[];anchor_receipts=[]
    for p in sorted(paths):
        rows,receipt=anchor_plan(p);anchors.extend(rows);anchor_receipts.append(receipt)
    # Preserve prior source-reader exposure as fitting-only constraints. Read
    # context/identity, never reviews or proposed labels, to create these anchors.
    database=reader.db(stack)
    try:saved=[json.loads(raw) for raw, in database.execute('SELECT payload FROM readings')]
    finally:database.close()
    planned={(digest(r['target']),r['source_hash']) for r in anchors if 'target' in r}
    exposures=[]
    for r in saved:
        if r['reader_identity']!=reader.identity():continue
        if (digest(r['target']),r['source_hash']) in planned:continue
        try:
            record,text,_=reader.source(stack,r['target']);student.model_text(r['context'])
        except ValueError:continue
        if digest(record)!=r['source_hash']:continue
        exposures.append({'item_id':r['id'],'patient_family':r['target']['patient_id'],
            'source_hash':r['source_hash'],'template':digest(queue.normalize(text)),
            'input':r['context'],'partition':'train'})
    # Only the verified original-person policy is supported by this adapter.
    policy,_=queue.lineage(original_run['lineage_root'],json.loads((stack/'run.json').read_text()))
    if any(pid!=family for pid,family in policy['patient_families'].items()):
        raise ValueError('Original-person lineage required for this cohort adapter')
    anchors.extend(exposures)
    output.mkdir(parents=True);view=output/'input-view';view.mkdir()
    for name in VIEW_FILES:
        if not (stack/name).exists():raise ValueError('Missing source-view input: '+name)
        (view/name).symlink_to(stack/name)
    # Snapshot sampling hints in a separate DB; never alias SQLite journals.
    source=reader.db(stack);destination=reader.db(view)
    try:source.backup(destination)
    finally:source.close();destination.close()
    run=queue.build(view,original_run['lineage_root'],output/'queue',size=size)
    _,_,_,items=queue.connected(view);candidate_rows=[];unassessed=[]
    for item in items:
        if not item['target']:
            unassessed.append({'item_id':item['id'],'reason':'no_marked_target_not_a_retrieval_miss'});continue
        record,text,meta=reader.source(stack,item['target'])
        if digest(record)!=item['source_hash']:raise ValueError('Acquisition source changed')
        packet=reader.packet(record,text,item['target'],meta['snapshot']['as_of'])
        try:student.model_text(packet)
        except ValueError as exc:
            unassessed.append({'item_id':item['id'],'reason':'context_not_fit_ready','detail':str(exc)});continue
        candidate_rows.append({'item_id':item['id'],'target':item['target'],'patient_family':item['patient_family'],
            'source_hash':item['source_hash'],'template':digest(queue.normalize(text)),'input':packet})
    rows,conflicts=partition(candidate_rows,anchors)
    model_manifest=local_decisions.verify(reader.ROOT/'models/kev-4b/agere-model.json')
    teacher_identity=digest([local_decisions.VERSION,model_manifest,local_decisions.sha256(local_decisions.__file__)])
    for name,value in [('candidates',candidate_rows),('anchors',anchors),('targets',rows),('excluded',conflicts)]:
        queue.write(output/(name+'.json'),value)
    plan={'schema':VERSION,'implementation':identity(),'synthetic':True,'created_at':datetime.now(timezone.utc).isoformat(),
        'stack':str(stack),'source_identity':run['source_identity'],'reader_identity':reader.identity(),
        'queue_run_sha256':queue.sha(output/'queue/run.json'),'protection_register':run['protection_register'],
        'source_links':{name:str((stack/name).resolve()) for name in VIEW_FILES},'anchor_plans':anchor_receipts,
        'prior_reading_exposures':len(exposures),'teacher':'kev','teacher_identity':teacher_identity,
        'teacher_selection':'Installed source of machine imitation, declared before new readings; no superiority claim.',
        'partition_salt':SALT,'target_count':len(rows),'counts':dict(Counter(r['partition'] for r in rows)),
        'components':len({r['component'] for r in rows}),'inherited_targets':sum(r['partition_basis']=='inherited' for r in rows),
        'unassessed':unassessed,'conflicting_targets_excluded':len(conflicts),
        'files':{name+'.json':queue.sha(output/(name+'.json')) for name in ('candidates','anchors','targets','excluded')},
        'design':qualification.assess(rows,unassessed,student.POLICY),'limits':LIMITS}
    queue.write(output/'plan.json',plan);key=digest(plan)
    if any(queue.sha(stack/name)!=h for name,h in attached_before.items()):raise ValueError('Original acquisition attachment changed during build')
    # Validate every new source/dependency before making this cohort discoverable.
    validate(stack,output,plan)
    db=registry(stack)
    try:
        with db:db.execute('INSERT INTO cohorts VALUES (?,?,?)',(key,str(output),queue.sha(output/'plan.json')))
    finally:db.close()
    return {'id':key,**plan}


def validate(stack, output, plan):
    stack,output=Path(stack).resolve(),Path(output);view=output/'input-view'
    if plan['schema']!=VERSION or plan['implementation']!=identity() or plan['stack']!=str(stack):
        raise ValueError('Cohort implementation or parent workspace changed')
    for name,target in plan['source_links'].items():
        if not (view/name).is_symlink() or (view/name).resolve()!=Path(target) or (stack/name).resolve()!=Path(target):
            raise ValueError('Detached source-view binding changed')
    _,run,policy,items=queue.connected(view)
    if queue.sha(output/'queue/run.json')!=plan['queue_run_sha256'] or run['source_identity']!=plan['source_identity']:
        raise ValueError('Cohort source acquisition changed')
    for p in plan['anchor_plans']:
        _,receipt=anchor_plan(p['path'])
        if receipt!=p:raise ValueError('Inherited development plan changed')
    for name,h in plan['files'].items():
        if queue.sha(output/name)!=h:raise ValueError('Cohort target or anchor integrity changed')
    rows=json.loads((output/'targets.json').read_text());candidates=json.loads((output/'candidates.json').read_text())
    anchors=json.loads((output/'anchors.json').read_text());excluded=json.loads((output/'excluded.json').read_text())
    if partition(candidates,anchors)!=(rows,excluded):raise ValueError('Inherited component partitions changed')
    by_id={r['id']:r for r in items}
    for row in rows:
        item=by_id.get(row['item_id'])
        if item is None or item['target']!=row['target'] or item['patient_family']!=row['patient_family']:
            raise ValueError('Target not in this protected acquisition')
        if queue.eligibility(row['target']['patient_id'],policy,run['protection_register']):
            raise ValueError('Protected cohort source')
        record,text,meta=reader.source(stack,row['target'])
        if digest(record)!=row['source_hash'] or reader.packet(record,text,row['target'],meta['snapshot']['as_of'])!=row['input']:
            raise ValueError('Cohort received source/context changed')
    return rows


def connected(stack, key):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact registered cohort ID required')
    db=registry(stack)
    try:r=db.execute('SELECT path,manifest_sha256 FROM cohorts WHERE id=?',(key,)).fetchone()
    finally:db.close()
    if r is None:raise ValueError('Unknown learning cohort')
    output=Path(r[0]);plan=json.loads((output/'plan.json').read_text())
    if queue.sha(output/'plan.json')!=r[1] or digest(plan)!=key:raise ValueError('Cohort manifest changed')
    return output,plan,validate(stack,output,plan)


def reading_for(stack, row, plan):
    key=digest([plan['reader_identity'],plan['source_identity'],row['target'],row['source_hash'],
                plan['teacher'],plan['teacher_identity'],row['input']])
    db=reader.db(stack)
    try:exists=db.execute('SELECT 1 FROM readings WHERE id=?',(key,)).fetchone()
    finally:db.close()
    return reader.inspect(stack,key) if exists else None


def acquire(stack, key, *, notify=lambda r:None):
    output,plan,rows=connected(stack,key)
    if not plan['design']['structural_minimums_met']:
        raise ValueError('Declared bulk qualification run needs a design meeting structural minimums; exploratory reading remains separate')
    with (output/'acquisition.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('This cohort is already acquiring')
        file=output/'attempts.json';attempts=json.loads(file.read_text()) if file.exists() else {'cohort_id':key,'rows':[]}
        if attempts['cohort_id']!=key:raise ValueError('Acquisition journal belongs to another cohort')
        done={r['item_id'] for r in attempts['rows']};model=None
        for row in rows:
            if row['item_id'] in done:continue
            attempt={'item_id':row['item_id'],'status':'running','retry_attempted':False};attempts['rows'].append(attempt);write(file,attempts)
            try:
                result=reading_for(stack,row,plan);cached=result is not None
                if result is None:
                    if model is None:
                        model=local_decisions.KevMLX()
                        if model.identity!=plan['teacher_identity']:raise ValueError('Native teacher identity changed')
                    result=reader.read(stack,row['target'],plan['teacher'],local_model=model)
                attempt.update(status='complete',reading_id=result['id'],cache_hit=cached,usage=result['usage'],new_paid_api_calls=0)
            except (ValueError,RuntimeError,OSError,ImportError) as exc:
                attempt.update(status='failed',error=str(exc)[:500],failure_kind=learning_suggestions.failure_kind(str(exc)))
            write(file,attempts);notify(attempt)
            if attempt['status']=='failed' and model is None:break
        return attempts


def snapshot_rows(stack, key):
    output,plan,targets=connected(stack,key);rows=[]
    journal=json.loads((output/'attempts.json').read_text())
    if (journal['cohort_id']!=key or len(journal['rows'])!=len(targets) or
        {r['item_id'] for r in journal['rows']}!={r['item_id'] for r in targets} or
        any(r['status']!='complete' for r in journal['rows'])):
        raise ValueError('Complete declared acquisition required; failures cannot silently filter supervision')
    for target in targets:
        r=reading_for(stack,target,plan)
        if r is None:raise ValueError('Missing declared teacher reading')
        rows.append({'id':target['item_id'],'input':r['context'],'labels':r['reading']['labels'],
            'partition':target['partition'],'patient_family':target['patient_family'],'patient_id':target['target']['patient_id'],
            'source_hash':target['source_hash'],'source_families':[target['template']],'target':target['target'],
            'teacher':{'provider':plan['teacher'],'model_identity':plan['teacher_identity'],'reading_id':r['id'],
                'labels_digest':digest(r['reading']['labels']),'context_digest':digest(r['context'])},
            'supervision':'Unreviewed machine teacher; imitation target only','clinical_judgment_label':False})
    student.validate_rows(rows)
    return output,plan,rows


def freeze(stack, key):
    output,plan,rows=snapshot_rows(stack,key);dataset=output/'dataset'
    if dataset.exists():raise ValueError('Fresh immutable dataset required')
    dataset.mkdir()
    for part in student.PARTITIONS:
        (dataset/(part+'.jsonl')).write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows if r['partition']==part))
    manifest={'schema':VERSION,'cohort_id':key,'rows_digest':digest(rows),'supervision':'machine_teacher_snapshot',
        'files':{p+'.jsonl':queue.sha(dataset/(p+'.jsonl')) for p in student.PARTITIONS},'labels_created_by_humans':0}
    queue.write(dataset/'manifest.json',manifest);return manifest


def dataset(stack, key):
    output,plan,rows=snapshot_rows(stack,key);m=json.loads((output/'dataset/manifest.json').read_text())
    if m['cohort_id']!=key or m['rows_digest']!=digest(rows) or set(m['files'])!={p+'.jsonl' for p in student.PARTITIONS}:
        raise ValueError('Teacher snapshot changed')
    for name,h in m['files'].items():
        if queue.sha(output/'dataset'/name)!=h:raise ValueError('Teacher partition file changed')
        saved=[json.loads(line) for line in (output/'dataset'/name).read_text().splitlines()]
        if saved!=[r for r in rows if r['partition']==name.removesuffix('.jsonl')]:raise ValueError('Teacher partition rows changed')
    return output,plan,rows,m


def fit(stack, key):
    output,plan,rows,m=dataset(stack,key);model_root=output/'models'
    if model_root.exists():raise ValueError('A fit has already been attempted for this cohort; preserve it')
    model_root.mkdir();report={'cohort_id':key,'models':{},'failures':{},'status':'running','human_reviewed_labels_used':False}
    write(model_root/'run.json',report)
    try:
        for kind in ('lexical','minilm'):
            try:
                model=student.fit(rows,model_root/kind,kind=kind,supervision='machine_teacher_snapshot',
                    provenance={'cohort_id':key,'teacher_snapshot_sha256':queue.sha(output/'dataset/manifest.json'),'human_reviewed_labels_used':False})
                report['models'][kind]={'manifest_sha256':queue.sha(model_root/kind/'manifest.json'),
                    **{k:model[k] for k in ('counts','calibration','validation','release','training_seconds','model_artifact_bytes')}}
            except (ValueError,RuntimeError,OSError,ImportError) as exc:
                report['failures'][kind]=str(exc)[:500]
            write(model_root/'run.json',report)
        dataset(stack,key)
        report['status']='complete_shadow_only' if not report['failures'] else 'partial_shadow_only' if report['models'] else 'failed'
    except (ValueError,RuntimeError,OSError,ImportError) as exc:
        report.update(status='failed',error=str(exc)[:500]);raise
    finally:write(model_root/'run.json',report)
    return report


def status(stack, key):
    output,plan,rows=connected(stack,key)
    attempts=json.loads((output/'attempts.json').read_text())['rows'] if (output/'attempts.json').exists() else []
    model_file=output/'models/run.json';models=json.loads(model_file.read_text()) if model_file.exists() else None
    return {'id':key,'status':'current','target_count':len(rows),'counts':plan['counts'],'components':plan['components'],
        'inherited_targets':plan['inherited_targets'],'conflicting_targets_excluded':plan['conflicting_targets_excluded'],
        'unassessed_source_fields':len(plan['unassessed']),'prior_reading_exposures':plan['prior_reading_exposures'],
        'design':plan['design'],'acquisition':{'completed':sum(r['status']=='complete' for r in attempts),
            'failed':sum(r['status']=='failed' for r in attempts),'interrupted_or_running':sum(r['status']=='running' for r in attempts),
            'pending':len(rows)-len(attempts),'recorded_inference_seconds':sum(r.get('usage',{}).get('seconds',0) for r in attempts),
            'local_input_tokens':sum(r.get('usage',{}).get('input_tokens',0) for r in attempts)},
        'models':models,'automatic_acceptance':False,'limits':LIMITS}


def listing(stack):
    db=registry(stack)
    try:ids=[r[0] for r in db.execute('SELECT id FROM cohorts ORDER BY rowid DESC')]
    finally:db.close()
    rows=[]
    for key in ids:
        try:rows.append(status(stack,key))
        except (ValueError,FileNotFoundError,KeyError) as exc:rows.append({'id':key,'status':'stale','reason':str(exc)[:200]})
    return {'cohorts':rows,'automatic_acceptance':False,'new_paid_api_calls':0}


def read(stack, request):
    if not isinstance(request,dict) or set(request)!={'cohort_id','kind','target'} or request['kind'] not in ('lexical','minilm'):
        raise ValueError('Explicit cohort, fitted reader kind and source target required')
    output,plan,rows,m=dataset(stack,request['cohort_id']);kind=request['kind']
    report=json.loads((output/'models/run.json').read_text());model_path=output/'models'/kind
    if report['status'] not in ('complete_shadow_only','partial_shadow_only') or kind not in report['models'] or queue.sha(model_path/'manifest.json')!=report['models'][kind]['manifest_sha256']:
        raise ValueError('Completed unchanged shadow model required; failed variants cannot serve')
    manifest=student.verify(model_path)
    if manifest['kind']!=kind or manifest['dataset_digest']!=digest(rows) or manifest['provenance']['teacher_snapshot_sha256']!=queue.sha(output/'dataset/manifest.json'):
        raise ValueError('Cohort model supervision changed')
    target=request['target'];record,text,meta=reader.source(stack,target);packet=reader.packet(record,text,target,meta['snapshot']['as_of'])
    student.model_text(packet);model_id=queue.sha(model_path/'manifest.json');provider='cohort_'+kind
    source_identity={k:meta[k] for k in ('source_digest','snapshot')}
    key=digest([identity(),request['cohort_id'],reader.identity(),source_identity,target,digest(record),provider,model_id,packet])
    db=reader.db(stack)
    try:
        saved=db.execute('SELECT payload FROM readings WHERE id=?',(key,)).fetchone()
        if saved:return {**json.loads(saved[0]),'cache_hit':True,'new_paid_api_calls':0}
        started=time.perf_counter();model=student.Student(model_path)
        try:result=model.predict(packet)
        finally:model.close()
        receipt={'schema':reader.VERSION,'id':key,'synthetic':True,'target':target,'source_hash':digest(record),
            'source_identity':source_identity,'reader_identity':reader.identity(),'model_identity':model_id,'provider':provider,'context':packet,
            'reading':{'labels':result['labels'],'distributions':{a:{'choice':result['labels'][a],'probabilities':result['probabilities'][a]} for a in reader.AXES},
                'semantic_validation':'pending','clinical_authority':False,'automatic_acceptance':False},
            'requirement_bridge':reader.implications(result['labels'],target['concept']),
            'student':{'cohort_id':request['cohort_id'],'supervision':manifest['supervision'],'release':manifest['release'],
                'confidence':result['confidence'],'passes_development_threshold':result['passes_development_threshold']},
            'usage':{'local_seconds_including_load':time.perf_counter()-started,'prediction_seconds':result['local_seconds'],'paid_api_calls':0,'hosted_tokens':0},
            'cache_hit':False,'new_paid_api_calls':0,'current_clinical_state_established':False,'training_label_created':False}
        with db:db.execute('INSERT OR IGNORE INTO readings VALUES (?,?,?)',(key,target['patient_id'],json.dumps(receipt,allow_nan=False)))
        return receipt
    finally:db.close()
