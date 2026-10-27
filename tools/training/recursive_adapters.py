"""Explicit offline residual-learning rounds, with fit-only correction selection.

The loop never manufactures clinical truth from model text. Every correction uses
an existing approved code target. Calibration/verification rows are kept unchanged,
and their results never choose the next fit examples. All releases stay candidates.
"""
from collections import defaultdict
import json
import math
from pathlib import Path
import time

from clinical_core.contracts import digest
from clinical_core.agere.clinical import adapter_learning as A, component_learning as C
from tools.training import adapter_tasks as T, adapters as D

VERSION='offline-recursive-adapter-study/1'


def correction_manifest(manifest,probe,output,*,corrections_per_family=8,rehearsal_per_family=2,epochs=2):
    """Choose only from fit families, using their own native execution failures."""
    if any(type(n) is not int or n<1 for n in (corrections_per_family,rehearsal_per_family,epochs)):
        raise ValueError('Choose explicit positive correction/rehearsal/epoch study sizes')
    spec,by,_=T.load(manifest)
    fit={digest([r['question'],r['family']]):r for r in by['fit']}
    if not probe or any(r['task'] not in fit or r['family']!=fit[r['task']]['family'] for r in probe):
        raise ValueError('Residual selection may use fit-only probes, never calibration or verification')
    failed={r['family'] for r in probe if not r['acceptable']}
    families=defaultdict(list)
    for row in by['fit']:families[row['family']].append(row)
    selected=[]
    for family,rows in families.items():
        amount=corrections_per_family if family in failed else rehearsal_per_family
        selected.extend(rows[:amount])
    # Fixed study allocation, not a runtime patient/query cap. No split is
    # reassigned and no generated output becomes a target.
    rows=[*selected,*by['calibration'],*by['verification']]
    output=C.checked_path(output);output.mkdir(parents=True,exist_ok=False)
    data=output/'tasks.jsonl';data.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    config=D.configuration(spec['training']);config['iterations']=math.ceil(len(selected)/config['batch_size'])*epochs
    new={**spec,'files':[{'path':str(data),'sha256':A.file_hash(data)}],'training':config,
        'provenance':spec['provenance']+' Residual revision uses only native fit-task failures and approved original targets; calibration/verification are unchanged.'}
    path=output/'manifest.json';path.write_text(json.dumps(new,indent=2)+'\n')
    audit={'schema':VERSION,'selection_split':'fit','probe_tasks':len(probe),'failed_families':sorted(failed),'fit_tasks':len(selected),
        'all_fit_families_retained':len(families),'calibration_unchanged':digest(by['calibration']),
        'verification_unchanged':digest(by['verification']),'generated_targets':0,'manifest':str(path)}
    (output/'selection.json').write_text(json.dumps(audit,indent=2)+'\n');return path,audit


def probe_fit(manifest,root,revision,*,stack,base,backend=None):
    spec,by,_=T.load(manifest);ctx=T.Context(stack);cfg=D.configuration(spec['training'])
    warm=A.training_revision(root,spec['learner'],revision,base);backend=backend or D.Mlx(base)
    rows=[];seen=set()
    for row in by['fit']:
        if row['family'] not in seen:rows.append(row);seen.add(row['family'])
    variants=[T.Context(v['path']) for v in spec.get('source_variants',[])]
    if ctx.identity!=spec['source_identity'] or any(c.identity!=v['source_identity'] for c,v in zip(variants,spec.get('source_variants',[]))):raise ValueError('Residual probe source changed')
    contract=A.contract(spec['learner'])
    import fcntl
    with (Path(root)/'adapter-training.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another local training job owns the GPU; no hidden retry')
        with D.offline() as guard,backend.generator(spec['learner'],warm['adapter'],ctx,cfg) as generate:
            stats={'reused':0,'generated':0};identity=digest([A.pin_base(base)['identity'],A.file_hash(warm['adapter']/'adapters.safetensors')])
            cached=D.cached_generator(generate,ctx,spec['learner'],Path(root),identity,cfg,stats)
            result=D.evaluate(ctx,spec['learner'],rows,cached,variants)
            if guard['blocked_attempts']:raise RuntimeError('Blocked external attempt invalidates residual selection')
    if T.Context(stack).identity!=ctx.identity:raise ValueError('Source changed during residual probing')
    if any(T.Context(v.path).identity!=v.identity for v in variants) or A.contract(spec['learner'])!=contract:raise ValueError('Variant or implementation changed during residual probing')
    return {**result,'selection_split':'fit','revision':revision,'provider_calls':0,'local_decodes':stats,'offline_guard':guard}


def run(manifest,root,*,stack,base,rounds=2,train=False,backend=None,corrections_per_family=8,rehearsal_per_family=2,corrective_epochs=2,initial_revision=None):
    if type(rounds) is not int or rounds<1:raise ValueError('Predeclare a positive number of local study rounds')
    root=C.checked_path(root);root.mkdir(parents=True,exist_ok=True)
    manifest=C.checked_path(manifest);spec,by,dataset=T.load(manifest)
    protocol={'schema':VERSION,'initial_manifest':str(manifest),'dataset':dataset,'rounds':rounds,
        'fit_probe':'First approved task from each fit family; native source acceptability only',
        'corrections_per_family':corrections_per_family,'rehearsal_per_family':rehearsal_per_family,'corrective_epochs':corrective_epochs,
        'selection_uses_verification':False,'install':False,'provider_calls':0}
    if initial_revision is not None:
        # Explicit transfer preserves an existing immutable candidate and
        # compares it on the new source/tasks before claiming a new gain.
        A.training_revision(root,spec['learner'],initial_revision,base)
        protocol['initial_revision']=initial_revision
    study=root/'recursive-studies'/digest(protocol);study.mkdir(parents=True,exist_ok=True)
    (study/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    if not train:return {**protocol,'status':'prepared','study':str(study)}
    import fcntl
    with (root/'recursive-study.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'schema':VERSION,'status':'busy','provider_calls':0}
        started=time.monotonic();history=[];current=manifest;parent=initial_revision
        for index in range(rounds):
            saved=study/f'round-{index+1}.json'
            if saved.exists():
                result=json.loads(saved.read_text());A.training_revision(root,spec['learner'],result['revision'],base)
            else:result=D.run(current,root,stack=stack,base=base,train=True,install=False,backend=backend,warm_start_revision=parent)
            if result.get('status') in ('busy','review_required'):raise RuntimeError('Recursive round did not complete: '+result['status'])
            (study/f'round-{index+1}.json').write_text(json.dumps(result,indent=2)+'\n');history.append(result)
            if index+1==rounds:break
            probe=probe_fit(manifest,root,result['revision'],stack=stack,base=base,backend=backend)
            (study/f'fit-probe-{index+1}.json').write_text(json.dumps(probe,indent=2)+'\n')
            if all(row['acceptable'] for row in probe['details']):break
            folder=study/f'correction-{index+1}'
            if (folder/'manifest.json').exists():
                current=folder/'manifest.json';T.load(current)
            else:current,audit=correction_manifest(manifest,probe['details'],folder,corrections_per_family=corrections_per_family,rehearsal_per_family=rehearsal_per_family,epochs=corrective_epochs)
            parent=result['revision']
        receipt={**protocol,'status':'completed','study':str(study),'completed_rounds':len(history),'seconds':time.monotonic()-started,
            'revisions':[r['revision'] for r in history],'outcomes':[r['status'] for r in history],'clinical_qualification':False}
        (study/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');return receipt
