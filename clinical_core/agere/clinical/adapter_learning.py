"""Pinned local SLM adapter releases in the existing component registry.

No model download, provider inference or legacy training-store discovery. Runtime
consumers still compile, check and execute proposals through their native paths.
"""
import hashlib
import json
import os
import shutil
from pathlib import Path
from ...contracts import digest

VERSION='local-adapter-release/1'
LEARNERS=('typed','normalizer','bottled','narrow')
_HASHES={}


def role(learner):
    if learner not in LEARNERS:raise ValueError('Unknown local adapter learner')
    return 'slm_'+learner


def contract(learner):
    role(learner)
    root=Path(__file__).resolve().parents[3]
    names=['clinical_core/agere/clinical/'+n for n in ('adapter_learning.py','component_learning.py','local_request_worker.py',
        'local_normalizer.py','bottled.py','request_plan.py','request_language.py','general_analysis.py','literal_check.py','semantic_analyst.py','sql_query.py')]
    names += ['tools/training/adapter_tasks.py','tools/training/adapters.py','tools/training/datasets.py']
    names += ['clinical_core/agere/clinical/'+n for n in ('schema_prefetch.py','request_discovery.py','constrained_decoding.py',
        'request_program.py','cohort_families.py','nl2sql.py','source_profile.py','proposers.py','analysis_graph.py','answer_shape.py')]
    return digest({'learner':learner,'schema':VERSION,'code':{n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in names}})


def file_hash(path):
    """Rehash changed files; ctime/inode also invalidate a restored mtime."""
    from .component_learning import checked_path
    path=checked_path(path);s=path.stat();key=(str(path),s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if key not in _HASHES:
        h=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(2**20),b''):h.update(chunk)
        _HASHES[key]=h.hexdigest()
    return _HASHES[key]


def checked_child(directory,name):
    from .component_learning import checked_path
    directory=checked_path(directory);path=checked_path(directory/name)
    if not path.is_relative_to(directory) or not path.is_file():raise ValueError('Missing or escaping pinned model file')
    return path


def pin_base(directory):
    from .component_learning import checked_path
    directory=checked_path(directory);spec=json.loads((directory/'manifest.json').read_text())
    if not isinstance(spec.get('files'),dict) or not spec['files']:raise ValueError('Pinned base model manifest required')
    for name,entry in spec['files'].items():
        p=checked_child(directory,name)
        if p.stat().st_size!=entry['bytes'] or file_hash(p)!=entry['sha256']:raise ValueError('Pinned base model changed: '+name)
    required={'config.json','tokenizer_config.json','tokenizer.json'}
    if not required<=spec['files'].keys() or not any(n.endswith('.safetensors') for n in spec['files']):raise ValueError('Base manifest must pin weights, configuration and tokenizer')
    # Loaders must not consume additional unpinned weights/configuration.
    relevant={p.name for p in directory.iterdir() if p.suffix in ('.safetensors','.json','.jinja','.model','.txt') and p.name!='manifest.json'}
    if relevant-spec['files'].keys():raise ValueError('Unpinned base model loading files')
    return {'path':str(directory),'identity':digest(spec),'manifest':spec}


def capture(root,learner,directory,base,qualification,*,parent=None,activate=False):
    """Copy immutable adapter files before publishing their metadata pointer."""
    from . import component_learning as C
    root=C.checked_path(root);directory=C.checked_path(directory);base=pin_base(base)
    names=('adapter_config.json','adapters.safetensors')
    files={n:{'sha256':file_hash(checked_child(directory,n)),'bytes':(directory/n).stat().st_size} for n in names}
    ident=digest({'base':base['identity'],'files':files});target=root/'adapters'/ident;target.mkdir(parents=True,exist_ok=True)
    for name in names:
        to=target/name
        if to.exists() and file_hash(to)!=files[name]['sha256']:raise ValueError('Immutable adapter release differs')
        if not to.exists():shutil.copy2(directory/name,to)
    model={'schema':VERSION,'learner':learner,'adapter':str(target),'files':files,
        'base':{'path':base['path'],'identity':base['identity']},'artifact_identity':ident}
    return C.register(root,role(learner),model,qualification,parent=parent,activate=activate)


def release_identity(learner,root=None,base=None):
    from . import component_learning as C
    try:
        path=C.runtime_model(role(learner),root)
        candidate=None
        if path is None:
            candidate=candidate_metadata(learner)
            if candidate is None:return None
            path=Path(candidate['model_path'])
        model=json.loads(path.read_text())
        pinned=pin_base(base or model['base']['path'])
        if model.get('schema')!=VERSION or model.get('learner')!=learner or pinned['identity']!=model['base']['identity']:return None
        directory=C.checked_path(model['adapter'])
        if set(model['files'])!={'adapter_config.json','adapters.safetensors'}:return None
        for name,entry in model['files'].items():
            p=checked_child(directory,name)
            if p.stat().st_size!=entry['bytes'] or file_hash(p)!=entry['sha256']:return None
        return {'adapter':str(directory),'base_identity':pinned['identity'],'artifact_identity':model['artifact_identity'],
                **({'candidate':True,'revision':candidate['revision'],'qualification':'checked proposal only; not a promoted model',
                    'training_contract':candidate['training_contract'],'runtime_contract':contract(learner)} if candidate else {})}
    except (OSError,ValueError,KeyError,TypeError):return None


def candidate_metadata(learner):
    """Explicit isolated-study candidates, never production activation or a scan of old lessons.

    The current native compiler checks each proposal. A changed serving contract
    is recorded rather than pretending that historical qualification transfers.
    Metadata and weights still have to match their immutable registry hashes.
    """
    from . import component_learning as C
    manifest=os.environ.get('AGERE_ADAPTER_CANDIDATES')
    if not manifest:return None
    spec=json.loads(C.checked_path(manifest).read_text())
    if spec.get('schema')!='checked-adapter-candidates/1' or spec.get('authority')!='isolated_checked_proposal':
        raise ValueError('Explicit isolated checked-proposal candidate manifest required')
    entry=spec.get('learners',{}).get(learner)
    if entry is None:return None
    root=C.checked_path(entry['registry']);revision=entry['revision']
    found=next((m for m in C.models(root) if m['id']==revision and m['role']==role(learner)),None)
    if not found or found['state']=='quarantined':raise ValueError('Missing or quarantined adapter candidate')
    path=C.checked_path(found['model_path'])
    if file_hash(path)!=found['model_sha256']:raise ValueError('Adapter candidate metadata changed')
    return {'revision':revision,'model_path':str(path),'training_contract':found['contract']}


def runtime_adapter(learner,base):
    value=release_identity(learner,base=base)
    return Path(value['adapter']) if value else None


def training_revision(root,learner,revision,base):
    """Explicit immutable initialization only; this never changes runtime pointers."""
    from . import component_learning as C
    found=next((m for m in C.models(root) if m['id']==revision and m['role']==role(learner)),None)
    if not found or found['state']=='quarantined':raise ValueError('An explicit non-quarantined local adapter revision is required')
    path=C.checked_path(found['model_path'])
    if found['contract']!=contract(learner) or file_hash(path)!=found['model_sha256']:raise ValueError('Training revision source contract or metadata changed')
    model=json.loads(path.read_text());pinned=pin_base(base)
    if model.get('schema')!=VERSION or model.get('learner')!=learner or model['base']['identity']!=pinned['identity']:raise ValueError('Training revision base or role differs')
    directory=C.checked_path(model['adapter'])
    if set(model['files'])!={'adapter_config.json','adapters.safetensors'}:raise ValueError('Training revision files differ')
    for name,entry in model['files'].items():
        p=checked_child(directory,name)
        if p.stat().st_size!=entry['bytes'] or file_hash(p)!=entry['sha256']:raise ValueError('Training revision artifact changed')
    return {'revision':revision,'adapter':directory,'artifact_identity':model['artifact_identity']}


def export(root,output,learner,family_splits):
    from . import component_learning as C
    db=C.database(root)
    try:rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM component_observations ORDER BY seq')]
    finally:db.close()
    latest={}
    for row in rows:
        if row.get('role')==role(learner) or row.get('source_kind')=='user_correction':latest[' '.join(row['question'].split()).casefold()]=row
    selected=[]
    for row in latest.values():
        if not row.get('eligible') or row.get('source_kind')!='user_review':continue
        split=family_splits.get(row['family'])
        if split not in ('fit','calibration','verification'):raise ValueError('Every reviewed adapter family needs a split')
        selected.append({k:row[k] for k in ('question','target','reference_sql','acceptance','family','source_kind','source_digest','population') if k in row}|{'split':split})
    output=C.checked_path(output);output.mkdir(parents=True,exist_ok=True);ident=digest(selected)
    data=output/('tasks-'+ident+'.jsonl');data.write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in selected))
    spec={'schema':'approved-adapter-data/1','learner':learner,'files':[{'path':str(data),'sha256':file_hash(data)}],
        'source_identity':next(iter({r['source_digest'] for r in selected}),None),
        'provenance':'New direct client-reviewed task targets; native result acceptance, not legacy clinical answer keys'}
    if len({r['source_digest'] for r in selected})>1:raise ValueError('Reviewed adapter tasks span different source snapshots')
    manifest=output/('manifest-'+ident+'.json');manifest.write_text(json.dumps(spec,indent=2)+'\n');return manifest
