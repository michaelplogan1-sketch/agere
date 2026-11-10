"""Sealed same-platform executable archives and isolated synthetic work recovery.

The archive contains application code, CPython and its installed libraries. The
original model/source/research dependencies remain explicit external resources.
Learning qualifications retain their original workspace binding; no plan rewrite,
validation monkeypatch, credential loading or provider-ledger rollback occurs.
"""
import argparse
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import sys
import sysconfig
import tarfile
import uuid
from . import local_release as L

VERSION = 'agere-runnable-local-release/1'
CODE_ONLY = 'agere-code-only-local-release/1'
BOOTSTRAP = '''import os, sys
from pathlib import Path
root = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path[:0] = [str(root / 'app'), str(root / 'runtime/site-packages')]
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
from clinical_core.agere.runnable_release import main
main(root)
'''


def archive_files(root):
    """No inherited caches, credentials, checkout or user site on the release path."""
    result = {}
    for p in sorted(Path(root).rglob('*')):
        if '__pycache__' in p.parts or p.suffix in ('.pyc', '.pyo'): continue
        if p.is_symlink() and not p.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError('External runtime symlink is not archivable: ' + str(p))
        if p.is_file(): result[p.relative_to(root).as_posix()] = L.file_entry(p)
    return result


def copy_entries(source, target, entries):
    for name, expected in entries.items():
        src, dst = Path(source) / L.safe_relative(name), Path(target) / L.safe_relative(name)
        L.copy_file(src, dst)
        if L.file_entry(dst) != expected: raise ValueError('Archive copy changed: ' + name)


def verify_entries(root, entries):
    if archive_files(root) != entries: raise ValueError('Executable archive bytes changed: ' + str(root))


def cohort_contract(build):
    """Discover the previously implicit frozen-workspace dependency without opening
    its learning engine or making a qualification claim about relocated data."""
    p = Path(build) / 'stack/learning-cohorts.sqlite3'
    if not p.exists(): return []
    with sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute('SELECT id,path,manifest_sha256 FROM cohorts ORDER BY id').fetchall()
    result = []
    for key, path, expected in rows:
        plan = Path(path) / 'plan.json'
        if L.sha(plan) != expected: raise ValueError('Registered learning cohort manifest changed')
        value = L.read(plan)
        result.append({'cohort_id': key, 'plan': str(plan), 'sha256': expected,
                       'original_stack': value['stack'], 'source_links': value['source_links'],
                       'installation_status': 'retained_external_workspace_qualification_not_transferred'})
    return result


def verify_contract(contract):
    for row in contract:
        if L.sha(row['plan']) != row['sha256']: raise ValueError('Frozen installation dependency changed')
        if L.read(row['plan'])['stack'] != row['original_stack']:
            raise ValueError('Frozen installation workspace changed')


def qualification_status(contract, key=None):
    """Expose original qualification provenance without impersonating its workspace.

    Deterministic local operations do not depend on making a frozen fitted reader
    valid at a new path. A future reader activation needs its own qualification.
    """
    verify_contract(contract)
    rows = [{'id': r['cohort_id'], 'status': 'unavailable_in_this_installation',
             'reason': 'Frozen cohort qualification is bound to its original workspace.',
             'original_stack': r['original_stack'], 'plan': r['plan'],
             'plan_sha256': r['sha256'], 'source_links': r['source_links'],
             'qualification_transferred': False, 'reading_enabled': False} for r in contract]
    if key is not None:
        selected = [r for r in rows if r['id'] == key]
        if not selected: raise ValueError('Unknown registered installation cohort')
        return selected[0]
    return {'cohorts': rows, 'qualification_transferred': False,
            'local_operations_require_fitted_cohort': False}


def resource_mounts(dependencies):
    """Bind application model names to physical resources, not checkout aliases."""
    mounts = {}
    for item in dependencies:
        target = Path(item['path'])
        name = item.get('application_mount')
        if name is None and target.is_relative_to(L.ROOT / 'models'):
            name = str(target.relative_to(L.ROOT))
        if name is not None:
            relative = L.safe_relative(name)
            if len(relative.parts) < 2 or relative.parts[0] != 'models':
                raise ValueError('External application mount must name a model resource')
            if name in mounts and mounts[name] != str(target.resolve()):
                raise ValueError('Conflicting application model mount')
            mounts[name] = str(target.resolve())
    return mounts


def code_only_preflight(target, authorization=None):
    """A recorded, path-bound operator exception never weakens the general guard.

    This is local build-owner authorization, not customer authentication. It
    permits only the declared code archive/rehearsal; no database/model/runtime
    copy path calls this function.
    """
    target = Path(target).resolve()
    if authorization is None:
        return L.require_storage(target)
    record = L.read(authorization)
    if (record.get('schema') != 'agere-code-only-storage-authorization/1'
            or record.get('approved') is not True or not record.get('authority')
            or target not in {Path(record[k]).resolve() for k in ('archive', 'installation')}
            or any(record.get(k) != 0 for k in ('database_copy_bytes', 'model_copy_bytes', 'runtime_copy_bytes'))):
        raise ValueError('Code-only storage authorization does not cover this target')
    return {'authorization': str(Path(authorization).resolve()), 'authorization_sha256': L.sha(authorization),
            'scope': 'explicit code-only exception; general storage guard unchanged'}


def _code_payloads():
    code = L.code_identity()
    # The existing launcher imports this credential helper only for an explicitly
    # enabled live subscription. Include its code, never credentials or caches.
    code['run_integrated_stack.py'] = L.sha(L.ROOT / 'run_integrated_stack.py')
    payloads = {}
    for name, expected in code.items():
        path = L.ROOT / L.safe_relative(name)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Code changed while preparing archive')
        payloads['app/' + name] = data
    return payloads


def make_code_only(config_path, output, *, authorization=None):
    """Archive code, not a warehouse/checkpoint/Python installation.

    The existing mutable build and provider ledger stay shared and never restored.
    This mode is explicitly same-machine, not a standalone customer distribution.
    """
    guard = code_only_preflight(output, authorization)
    config = L.validate_config(config_path)
    build = Path(config['build']).resolve()
    out = Path(output).resolve()
    if out.exists(): raise ValueError('Archive already exists')
    payloads = _code_payloads()
    config = dict(config, build=str(build))
    from .gateway import default_ledger_path
    ledger = default_ledger_path().resolve()
    if not ledger.is_file(): raise ValueError('Current provider ledger is required')
    bootstrap = ("import sys\nfrom pathlib import Path\n"
                 "sys.dont_write_bytecode = True\nroot = Path(__file__).resolve().parent\n"
                 "sys.path.insert(0, str(root / 'app'))\n"
                 "from clinical_core.agere.runnable_release import code_only_main\ncode_only_main(root)\n")
    payloads.update({'configuration.json': (json.dumps(config, indent=2)+'\n').encode(),
                     'bootstrap.py': bootstrap.encode()})
    record = {'schema': CODE_ONLY, 'files': {name: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                for name, data in payloads.items()},
              'platform': {'system': platform.system(), 'machine': platform.machine(), 'python': platform.python_version()},
              'python': str(Path(sys.executable).absolute()), 'build': str(build),
              'models': str((L.ROOT / 'models').resolve()), 'ledger': str(ledger),
              'authorization': guard, 'database_copy_bytes': 0, 'model_copy_bytes': 0, 'runtime_copy_bytes': 0,
              'scope': 'Same-machine code archive; external Python/libraries, source/model resources and mutable build/work/input/ledger stores remain required. No qualification transfer or full backup.'}
    payloads['release.json'] = (json.dumps(record, indent=2)+'\n').encode()
    record_id = hashlib.sha256(payloads['release.json']).hexdigest()
    payloads['complete.json'] = (json.dumps({'release_id': record_id})+'\n').encode()
    out.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive publication preserves every previous attempt. An interrupted
    # archive remains identifiable and must never overwrite a known good one.
    with out.open('xb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for name, data in payloads.items():
            entry = tarfile.TarInfo(name); entry.size = len(data); entry.mode = 0o644
            archive.addfile(entry, io.BytesIO(data))
    return {'archive': str(out), 'archive_sha256': L.sha(out), 'release_id': record_id,
            'archive_bytes': out.stat().st_size, 'uncompressed_bytes': sum(map(len, payloads.values())),
            'files': len(payloads), 'source_data_copied': False, 'runtime_copied': False}


def _verified_code_archive(archive):
    files = {}
    for entry in archive.getmembers():
        name = entry.name
        L.safe_relative(name)
        if not entry.isfile() or name in files:
            raise ValueError('Code archive requires unique regular files')
        files[name] = archive.extractfile(entry).read()
    record = json.loads(files['release.json'])
    if (record.get('schema') != CODE_ONLY
            or hashlib.sha256(files['release.json']).hexdigest() != json.loads(files['complete.json'])['release_id']
            or set(files) != set(record['files']) | {'release.json', 'complete.json'}):
        raise ValueError('Code archive identity/content mismatch')
    for name, expected in record['files'].items():
        if {'size': len(files[name]), 'sha256': hashlib.sha256(files[name]).hexdigest()} != expected:
            raise ValueError('Code archive bytes changed: ' + name)
        relative = name.removeprefix('app/')
        allowed = (relative.startswith(('clinical_core/', 'config/')) and Path(relative).suffix in
                   ('.py', '.js', '.json', '.jsonl', '.css', '.html', '.csv', '.txt', '.cql'))
        allowed = allowed or relative in ('serve_current_warehouse.py', 'agere_checkpoint.py', 'run_integrated_stack.py',
            'tools/training/datasets.py', 'tools/certify_gates.py', 'tools/normalization/jev_check.py')
        allowed = allowed or (Path(relative).name == relative and relative.startswith('requirements') and relative.endswith('.txt'))
        if name not in ('configuration.json', 'bootstrap.py') and not (name.startswith('app/') and allowed):
            raise ValueError('Unexpected non-code archive entry')
    return record, files


def install_code_only(archive, output, *, authorization=None):
    code_only_preflight(archive, authorization)
    code_only_preflight(output, authorization)
    out = Path(output).resolve()
    if out.exists(): raise ValueError('Installation already exists')
    with tarfile.open(archive, 'r:gz') as stream:
        record, files = _verified_code_archive(stream)
    if record['platform'] != {'system': platform.system(), 'machine': platform.machine(), 'python': platform.python_version()}:
        raise ValueError('Code archive requires its declared Python/platform')
    if Path(sys.executable).absolute() != Path(record['python']):
        raise ValueError('Use the declared existing Python environment')
    if any(record.get(k) != 0 for k in ('database_copy_bytes', 'model_copy_bytes', 'runtime_copy_bytes')):
        raise ValueError('Not a code-only archive')
    # All members are validated before any extraction; never tar.extractall().
    out.mkdir(parents=True)
    for name, data in files.items():
        dest = out / L.safe_relative(name); dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data)
    (out / 'app/models').symlink_to(record['models'], target_is_directory=True)
    return verify_code_only(out, authorization=authorization)


def verify_code_only(root, *, authorization=None):
    code_only_preflight(root, authorization)
    root = Path(root).resolve(); record = L.read(root / 'release.json')
    if (record.get('schema') != CODE_ONLY
            or L.sha(root / 'release.json') != L.read(root / 'complete.json')['release_id']):
        raise ValueError('Installed code-only release identity changed')
    for name, expected in record['files'].items():
        if L.file_entry(root / L.safe_relative(name)) != expected:
            raise ValueError('Installed code changed: ' + name)
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and not p.is_symlink()}
    if actual != set(record['files']) | {'release.json', 'complete.json'}:
        raise ValueError('Unexpected installed code/content')
    if any(p.is_symlink() and p != root / 'app/models' for p in root.rglob('*')):
        raise ValueError('Unexpected installation symlink')
    mount = root / 'app/models'
    if not mount.is_symlink() or mount.resolve() != Path(record['models']).resolve():
        raise ValueError('Declared external model mount changed')
    if not Path(record['ledger']).is_file(): raise ValueError('Current shared ledger unavailable')
    if Path(sys.executable).absolute() != Path(record['python']): raise ValueError('Declared Python environment changed')
    if record['platform'] != {'system': platform.system(), 'machine': platform.machine(), 'python': platform.python_version()}:
        raise ValueError('Declared Python/platform changed')
    L.validate_config(root / 'configuration.json')
    return {'status': 'verified_code_only_same_machine', 'release_id': L.sha(root / 'release.json'),
            'application': str(root / 'app'), 'shared_build': record['build'], 'python': record['python'],
            'ledger': record['ledger'], 'scope': record['scope']}


def code_only_main(root):
    parser = argparse.ArgumentParser(description='Same-machine code-only release; existing data/runtime dependencies required')
    parser.add_argument('--storage-authorization', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check')
    serve = commands.add_parser('serve'); serve.add_argument('--port', type=int, required=True)
    serve.add_argument('--allow-live-stack', action='store_true')
    args = parser.parse_args()
    result = verify_code_only(root, authorization=args.storage_authorization)
    if args.command == 'check':
        print(json.dumps(result)); return
    record = L.read(root / 'release.json')
    os.environ['AGERE_LEDGER_PATH'] = record['ledger']
    from serve_current_warehouse import main as launch
    launch(['--config', str(root / 'configuration.json'), '--ledger', record['ledger'], '--port', str(args.port)]
           + (['--allow-live-stack'] if args.allow_live_stack else []))


def make(checkpoint, output):
    cp = Path(checkpoint).resolve(); data = L.verify_checkpoint(cp)
    out = L.fresh_destination(output, cp, data['original_build'])
    stage = out.with_name('.' + out.name + '.partial-' + uuid.uuid4().hex); stage.mkdir()
    code = L.code_identity()
    for name, expected in code.items():
        L.copy_file(L.ROOT / name, stage / 'app' / name)
        if L.sha(stage / 'app' / name) != expected: raise ValueError('Release code changed')
    runtime = {}
    for name, source in [('python', Path(sys.base_prefix)), ('site-packages', Path(sysconfig.get_paths()['purelib']))]:
        entries = archive_files(source)
        copy_entries(source, stage / 'runtime' / name, entries)
        if archive_files(source) != entries: raise ValueError('Runtime changed while archiving')
        runtime[name] = entries
    (stage / 'bootstrap.py').write_text(BOOTSTRAP)
    dependencies = data['dependencies']
    # Model paths are explicit read dependencies. The shared ledger directory is
    # mounted, never copied: all modules keep their existing failed-key accounting.
    mounts = resource_mounts(dependencies)
    ledger = next(d['path'] for d in dependencies if d['verification'] == 'current_shared_ledger_never_restored')
    mounts['artifacts/agere-full'] = str(Path(ledger).parent)
    frozen = cohort_contract(cp / 'build')
    record = {'schema': VERSION, 'checkpoint_path': str(cp), 'checkpoint_id': L.sha(cp / 'manifest.json'),
              'code': code, 'runtime': runtime, 'bootstrap_sha256': L.sha(stage / 'bootstrap.py'),
              'platform': {'system': platform.system(), 'machine': platform.machine(), 'python': platform.python_version()},
              'distributions': sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions()),
              'dependencies': dependencies, 'mounts': mounts, 'frozen_cohorts': frozen,
              'scope': 'Same-platform local installation with original external resources. Declared local operations may be enabled; hosted inference and qualification transfer disabled.'}
    L.check_dependencies(dependencies); verify_contract(frozen)
    if L.code_identity() != code: raise ValueError('Code changed while archiving')
    L.write(stage / 'release.json', record)
    L.write(stage / 'complete.json', {'release_id': L.sha(stage / 'release.json')})
    L.sync_publish(stage, out)
    return {'release': str(out), 'release_id': L.sha(out / 'release.json'), 'code_files': len(code),
            'runtime_files': sum(map(len, runtime.values())), 'runtime_bytes': sum(x['size'] for v in runtime.values() for x in v.values())}


def verify(root, *, installed=False):
    root = Path(root).resolve(); r = L.read(root / 'release.json')
    if r.get('schema') != VERSION or L.sha(root / 'release.json') != L.read(root / 'complete.json')['release_id']:
        raise ValueError('Release manifest checksum or version mismatch')
    if r['platform']['system'] != platform.system() or r['platform']['machine'] != platform.machine():
        raise ValueError('Release requires its declared operating system and architecture')
    if L.sha(root / 'bootstrap.py') != r['bootstrap_sha256']: raise ValueError('Release bootstrap changed')
    for name, expected in r['code'].items():
        if L.sha(root / 'app' / L.safe_relative(name)) != expected: raise ValueError('Archived application changed')
    for name, entries in r['runtime'].items(): verify_entries(root / 'runtime' / name, entries)
    L.check_dependencies(r['dependencies']); verify_contract(r['frozen_cohorts'])
    if installed:
        for name, target in r['mounts'].items():
            p = root / 'app' / L.safe_relative(name)
            if not p.is_symlink() or p.resolve() != Path(target).resolve(): raise ValueError('Installation dependency mount changed')
        state = L.read(root / 'installation.json')
        if state.get('release_id') != L.sha(root / 'release.json') or state.get('root') != str(root):
            raise ValueError('Installation receipt is not bound to this location and release')
        L.recovery_routes(state.get('mode'))
        if state.get('mode') == 'local_operations' and state.get('policy_sha256') != L.sha(root / 'app/clinical_core/agere/local_release.py'):
            raise ValueError('Installation local operation policy changed')
        config = L.validate_config(root / 'configuration.json')
        if config.get('recovery', {}).get('checkpoint_id') != r['checkpoint_id'] or config['build'] != str(root / 'build'):
            raise ValueError('Installation configuration changed')
    return r


def install(root, output, *, resume_work=False, local_operations=False):
    if resume_work and local_operations: raise ValueError('Choose one installation mode')
    root = Path(root).resolve(); r = verify(root)
    cp = Path(r['checkpoint_path'])
    if L.sha(cp / 'manifest.json') != r['checkpoint_id']: raise ValueError('Release checkpoint changed')
    # Executing the archived version keeps old releases installable after the
    # development checkout changes. It also retains the original checkpoint gate.
    if L.code_identity() != r['code']: raise ValueError('Use this release bootstrap to install its exact archived code')
    out = L.fresh_destination(output, root, cp, L.read(cp / 'manifest.json')['original_build'])
    stage = out.with_name('.' + out.name + '.partial-' + uuid.uuid4().hex)
    # L.restore validates and publishes to a staging directory; configuration is
    # rebound again to the final location before this installation is published.
    L.restore(cp, stage)
    for name in ('app', 'runtime'): copy_entries(root / name, stage / name, archive_files(root / name))
    for name in ('bootstrap.py', 'release.json', 'complete.json'): L.copy_file(root / name, stage / name)
    for name, target in r['mounts'].items():
        dest = stage / 'app' / L.safe_relative(name); dest.parent.mkdir(parents=True, exist_ok=True); dest.symlink_to(target, target_is_directory=True)
    for name in L.INTERNAL_LINKS:
        p = stage / 'build/stack' / name; v = L.read(p)
        v['path'] = str(out / 'build' / Path(v['path']).relative_to(stage / 'build'))
        p.write_text(json.dumps(v, indent=2) + '\n')
    config = L.read(stage / 'configuration.json'); config['build'] = str(out / 'build')
    (stage / 'configuration.json').write_text(json.dumps(config, indent=2) + '\n')
    recovered = L.read(stage / 'recovery.json'); recovered['build'] = str(out / 'build')
    for value in recovered['link_rebindings'].values(): value['to'] = str(out / Path(value['to']).relative_to(stage))
    (stage / 'recovery.json').write_text(json.dumps(recovered, indent=2) + '\n')
    mode = 'local_operations' if local_operations else 'owned_work_only' if resume_work else 'inspection_only'
    L.write(stage / 'installation.json', {'schema': VERSION, 'root': str(out), 'release_id': L.sha(root / 'release.json'),
            'mode': mode,
            'policy_sha256': r['code'].get('clinical_core/agere/local_release.py') if local_operations else None,
            'allowed_work_route': '/api/stack/decision/event' if resume_work else None,
            'provider_ledger_restored': False, 'qualified_learning_workspace': False,
            'rollback': 'Stop this installation and install this sealed release into another fresh directory. Preserve this directory and its audit.'})
    L.validate_config(stage / 'configuration.json', build_override=stage / 'build')
    # Dependency symlinks are intentional and must not be fsynced/traversed as
    # archive contents; sync only regular local files, then rename atomically.
    for p in stage.rglob('*'):
        if p.is_file() and not p.is_symlink():
            with p.open('rb') as handle: os.fsync(handle.fileno())
    for p in [*sorted((p for p in stage.rglob('*') if p.is_dir() and not p.is_symlink()), reverse=True), stage]:
        fd=os.open(p,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    if out.exists(): raise ValueError('Installation destination appeared')
    stage.rename(out)
    fd=os.open(out.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return {'installation': str(out), 'mode': mode,
            'release_id': L.sha(out / 'release.json'), 'original_build_changed': False}


def main(root=None):
    p = argparse.ArgumentParser(description=__doc__); commands = p.add_subparsers(dest='command', required=True)
    make_cmd = commands.add_parser('make'); make_cmd.add_argument('--checkpoint', required=True); make_cmd.add_argument('--output', required=True)
    install_cmd = commands.add_parser('install'); install_cmd.add_argument('--output', required=True)
    modes = install_cmd.add_mutually_exclusive_group()
    modes.add_argument('--resume-work', action='store_true'); modes.add_argument('--local-operations', action='store_true')
    commands.add_parser('check')
    serve_cmd = commands.add_parser('serve'); serve_cmd.add_argument('--port', type=int, required=True)
    args = p.parse_args()
    if args.command == 'make': result = make(args.checkpoint, args.output)
    elif root is None: p.error('Use the archived bootstrap.py for install/check/serve')
    elif args.command == 'install': result = install(root, args.output, resume_work=args.resume_work, local_operations=args.local_operations)
    else:
        installed = (root / 'installation.json').exists(); r = verify(root, installed=installed)
        if args.command == 'serve':
            if not installed: p.error('Install the release into a fresh directory before serving')
            state = L.read(root / 'installation.json'); build = root / 'build'
            from .service import serve
            with L.build_lock(build):
                serve(build / 'clinical', args.port, annotations=build / 'annotations', evidence_queries=build / 'evidence',
                      stack=build / 'stack', recovery={'mode':state['mode'], 'release_id':state['release_id'],
                        'qualification':'original workspace retained; qualification does not transfer',
                        'frozen_cohorts':r['frozen_cohorts'],
                        'local_surface':{'get':sorted(L.recovery_routes(state['mode'])[0]),'post':sorted(L.recovery_routes(state['mode'])[1])}}, allow_live_stack=False)
            return
        result = {'status':'verified', 'installed':installed, 'release_id':L.sha(root / 'release.json'),
                  'executable':sys.executable, 'application':str(L.ROOT), 'scope':r['scope']}
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
