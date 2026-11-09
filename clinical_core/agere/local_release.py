"""Same-machine, offline checkpoints. Restores never activate or rewind provider state."""
from contextlib import contextmanager
from datetime import datetime, timezone
import ctypes
import fcntl
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
VERSION = 'agere-local-checkpoint/1'
LOCK = '.agere-release.lock'
INTERNAL_LINKS = ('annotation-link.json', 'clinical-state-link.json', 'longitudinal-link.json')
READ_POSTS = frozenset('/api/stack/' + name for name in (
    'decision/lineage', 'decision/statement-review-open', 'decision/statement-review-status',
    'decision/statement-review-outcome-open', 'decision/population-accountability-open',
    'decision/population-prediction-open', 'decision/population-discovery-open',
    'decision/economics-open', 'reader-comparison/inspect', 'portable-intake/inspect'))
READ_GETS = frozenset(('/health', '/stack', '/api/stack/meta', '/api/stack/decision',
    '/api/stack/work', '/api/stack/evidence-thread', '/api/stack/semantic',
    '/api/stack/requirement-contracts', '/action-center',
    '/api/stack/action-contexts', '/api/stack/action-context',
    '/api/stack/action-deliverables', '/api/stack/action-deliverable'))
LOCAL_POSTS = frozenset('/api/stack/' + name for name in (
    'search', 'cohort', 'operator', 'execute-plan', 'compose',
    'context/prepare', 'context/from-question',
    'portable-intake/example', 'portable-intake/preview', 'portable-intake/save',
    'portable-intake/list', 'portable-intake/compare',
    'capabilities/save', 'capabilities/prepare',
    'decision/prepare', 'decision/scoped-prepare', 'decision/queue',
    'decision/event', 'decision/observe', 'decision/followup',
    'decision/population-prepare', 'decision/population-match', 'decision/population-queue',
    'decision/population-route-member', 'decision/population-allocate',
    'decision/population-execute', 'decision/population-accountability',
    'decision/population-prediction-options', 'decision/population-prediction',
    'decision/population-discovery-options', 'decision/population-discovery',
    'decision/economics', 'action-work/create', 'action-work/update', 'action-work/refresh'))
LOCAL_GETS = frozenset('/api/stack/' + name for name in (
    'context', 'case-synthesis', 'decision/source', 'decision/population-config', 'outcome-contracts',
    'source-atlas', 'source-records', 'capabilities', 'capabilities/catalog',
    'capabilities/availability', 'capabilities/tasks', 'capabilities/plan',
    'capabilities/assessment', 'learning-cohorts'))


def recovery_routes(mode):
    """Declared local installation surface; no model/learning mutations enabled."""
    if mode not in ('inspection_only', 'owned_work_only', 'local_operations'):
        raise ValueError('Unsupported installation mode')
    posts = READ_POSTS
    if mode in ('owned_work_only', 'local_operations'):
        posts |= {'/api/stack/decision/event'}
    return (READ_GETS | LOCAL_GETS, posts | LOCAL_POSTS) if mode == 'local_operations' else (READ_GETS, posts)


def check_local_request(path, body):
    # Do not turn relocation into new model activation or a hosted retry. Local
    # search still uses the existing pinned retrieval models; clinical readers
    # and their workspace-bound qualifications are separate.
    if path == '/api/stack/compose' and body.get('planning') != 'scoped_local_candidate':
        raise PermissionError('This installation supports explicit scoped local composition; hosted planning is disabled.')
    if path == '/api/stack/decision/prepare' and body.get('planner', 'cited_source_type_baseline') != 'cited_source_type_baseline':
        raise PermissionError('This installation supports the cited-source baseline; candidate model planning is disabled.')


def read(path):
    return json.loads(Path(path).read_text())


def write(path, data):
    with Path(path).open('x') as f:
        json.dump(data, f, indent=2, allow_nan=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def file_entry(path):
    return {'size': path.stat().st_size, 'sha256': sha(path)}


@contextmanager
def build_lock(build, *, exclusive=False):
    """Cooperating launcher/checkpoint fence; noncooperating writers must be stopped."""
    with (Path(build) / LOCK).open('a+') as handle:
        try:
            fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Build is in use. Stop its server/writers before checkpoint or restore.') from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def inventory(root):
    result = {}
    for p in sorted(Path(root).rglob('*')):
        if p.is_symlink():
            raise ValueError('Checkpoint does not follow symlinks: ' + str(p))
        if p.is_file() and p.name != LOCK and not p.name.endswith('-shm'):
            s = p.stat()
            # Read-only SQLite opens may create empty WAL sidecars. They hold no
            # committed frames; nonempty WAL and main DB changes remain fenced.
            if p.name.endswith('-wal') and s.st_size == 0: continue
            result[p.relative_to(root).as_posix()] = (s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    return result


def code_identity():
    # Declarative rules and UI assets are executable application dependencies,
    # not optional documentation. Omitting them produces a runnable interpreter
    # whose saved decisions and workbench fail on first use.
    files = [p for p in (ROOT / 'clinical_core').rglob('*')
             if p.is_file() and p.suffix in ('.py', '.js', '.json', '.css', '.html') and '__pycache__' not in p.parts]
    # Versioned rules include nested coefficient tables and source mappings.
    files += [p for p in (ROOT / 'config/agere').rglob('*')
              if p.is_file() and p.suffix in ('.json', '.jsonl', '.csv', '.txt', '.cql')]
    files += list((ROOT / 'config').glob('*.json'))
    # The active build pointer is installation configuration, not release code.
    files = [p for p in files if p.name != 'active_warehouse.json']
    files += [ROOT / 'serve_current_warehouse.py', ROOT / 'agere_checkpoint.py']
    # These modules provide runtime prompts/validation, even when fitting and
    # hosted inference are disabled. Archive code only, never training data.
    files += [ROOT / name for name in ('tools/training/datasets.py',
              'tools/certify_gates.py', 'tools/normalization/jev_check.py')]
    files += list(ROOT.glob('requirements*.txt'))
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}


def validate_config(path, *, build_override=None):
    c = read(path)
    if c.get('schema') != 'active-healthcare-warehouse/1' or c.get('synthetic') is not True:
        raise ValueError('Supported synthetic warehouse configuration required')
    from .model_policy import application_runtime
    application_runtime(c)  # validate without changing the process environment
    configured_build = Path(c['build']).resolve()
    build = Path(build_override).resolve() if build_override is not None else configured_build
    if not build.is_dir():
        raise ValueError('Configured build is missing')
    receipt = read(build / 'connection.json')
    clinical = read(build / 'clinical/run.json')
    ann = read(build / 'annotations/run.json')
    evidence = read(build / 'evidence/run.json')
    stack = read(build / 'stack/run.json')
    if receipt.get('status') != 'connected' or receipt.get('snapshot') != c.get('snapshot'):
        raise ValueError('Warehouse connection snapshot differs from configuration')
    if stack.get('snapshot') != c['snapshot'] or not clinical.get('cache_key_digest'):
        raise ValueError('Stack snapshot or clinical identity mismatch')
    if any(x.get('source_run') != clinical['cache_key_digest'] for x in (ann, evidence, stack)):
        raise ValueError('Source-run identity mismatch')
    if ann.get('schema') != 'factorized-reader-run/2' or evidence.get('schema') != 'evidence-query/1':
        raise ValueError('Unsupported annotation/evidence schema')
    if ann.get('synthetic') is not True or evidence.get('synthetic') is not True or ann.get('model_identity') != evidence.get('model_identity'):
        raise ValueError('Annotation/evidence identity mismatch')
    for name in INTERNAL_LINKS:
        link = Path(read(build / 'stack' / name)['path']).resolve()
        if not link.is_relative_to(configured_build) or not (build / link.relative_to(configured_build)).is_dir():
            raise ValueError('Derived link must remain inside this build: ' + name)
    for name in ('stack/stack.sqlite3', 'warehouse.sqlite3'):
        if not (build / name).is_file():
            raise ValueError('Required store missing: ' + name)
    recovery = c.get('recovery')
    if recovery is not None:
        if recovery.get('mode') != 'inspection_only':
            raise ValueError('Unsupported recovery mode')
        record = read(build.parent / 'recovery.json')
        if record.get('schema') != VERSION or record.get('build') != str(configured_build) or record.get('checkpoint_id') != recovery.get('checkpoint_id'):
            raise ValueError('Recovery configuration does not match its receipt')
        if record['code'] != code_identity():
            raise ValueError('Recovery code differs from checkpoint release')
        check_dependencies(record['dependencies'])
    return c


def dependencies(build, config):
    """Explicit same-machine dependency boundary, not a standalone deployment archive."""
    dirs = {Path(config['source_release']), Path(read(build / 'stack/run.json')['encoder_path']),
            ROOT / 'models/bge-reranker-v2-m3'}
    for p in (build / 'stack').glob('*link.json'):
        def paths(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    if k == 'path' and isinstance(v, str):
                        target = Path(v).resolve()
                        if not target.is_relative_to(build):
                            dirs.add(target)
                    else:
                        paths(v)
            elif isinstance(value, list):
                for v in value: paths(v)
        paths(read(p))
    result = []
    for d in sorted(dirs):
        if not d.is_dir(): raise ValueError('External dependency missing: ' + str(d))
        physical = d.resolve()
        # Existing result/model manifests define identity. This does not claim that
        # every external byte is archived or that mutable research stores are frozen.
        manifests = {str(p.relative_to(d)): file_entry(p) for p in sorted(d.glob('*.json')) if p.is_file()}
        if not manifests: raise ValueError('External dependency has no identity manifest: ' + str(d))
        item = {'path': str(physical), 'verification': 'top_level_json_manifests', 'files': manifests}
        if d.is_relative_to(ROOT / 'models'):
            item['application_mount'] = str(d.relative_to(ROOT))
        result.append(item)
    from .gateway import default_ledger_path
    ledger = default_ledger_path().resolve()
    if not ledger.is_file(): raise ValueError('Current shared provider ledger required')
    result.append({'path': str(ledger), 'verification': 'current_shared_ledger_never_restored'})
    return result


def safe_relative(name):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or p.as_posix() != name or not p.parts:
        raise ValueError('Unsafe checkpoint path')
    return p


def check_dependencies(items):
    for item in items:
        p = Path(item['path'])
        if item['verification'] == 'current_shared_ledger_never_restored':
            if not p.is_file(): raise ValueError('Current shared ledger unavailable')
            continue
        if item['verification'] != 'top_level_json_manifests' or not p.is_dir():
            raise ValueError('External dependency unavailable')
        for name, expected in item['files'].items():
            f = p / safe_relative(name)
            if not f.is_file() or file_entry(f) != expected:
                raise ValueError('External dependency manifest changed: ' + str(f))


def fresh_destination(path, *sources):
    target = Path(path).absolute()
    if target.exists() or target.is_symlink(): raise ValueError('Destination already exists; nothing overwritten')
    target = target.resolve()
    for source in sources:
        source = Path(source).resolve()
        if target.is_relative_to(source) or source.is_relative_to(target):
            raise ValueError('Destination must be separate from source/build')
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@lru_cache(maxsize=1)
def _clone_function():
    if sys.platform != 'darwin': return None
    try:
        clone = ctypes.CDLL(None, use_errno=True).clonefile
    except (AttributeError, OSError):
        return None
    clone.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int)
    clone.restype = ctypes.c_int
    return clone


def copy_file(source, target):
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    clone = _clone_function()
    # clonefile is atomic, follows source symlinks with flags=0 and requires a
    # fresh destination. Both files remain independently mutable. Calling it
    # directly also avoids a subprocess for every small interpreter/library file.
    if clone is not None and source.is_file() and clone(os.fsencode(source), os.fsencode(target), 0) == 0:
        shutil.copystat(source, target)
        return
    # Other filesystems/platforms and existing destinations retain copy2 behavior.
    shutil.copy2(source, target)


def sqlite_copy(source, target):
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists(): raise ValueError('SQLite destination already exists')
    src = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        # A cold APFS clone avoids rewriting large frozen databases. Hold a read
        # transaction, refuse journals with frames, and fence concurrent changes.
        # WAL databases with committed frames still use SQLite's online backup.
        src.execute('BEGIN')
        src.execute('SELECT count(*) FROM sqlite_schema').fetchone()
        def stamp():
            s = source.stat()
            return (s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        def hot():
            return any(p.exists() and p.stat().st_size for p in
                       (Path(str(source) + '-wal'), Path(str(source) + '-journal')))
        before = stamp()
        if sys.platform == 'darwin' and not hot():
            cp = subprocess.run(['/bin/cp', '-c', str(source), str(target)], capture_output=True)
            if cp.returncode == 0:
                if hot() or stamp() != before:
                    raise ValueError('Source changed during cold SQLite copy: ' + str(source))
                with sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True) as check:
                    if check.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                        raise ValueError('SQLite consistency check failed: ' + str(source))
                if hot() or stamp() != before:
                    raise ValueError('Source changed during cold SQLite copy: ' + str(source))
                shutil.copystat(source, target)
                return 'cold_filesystem_clone'
            if target.exists():
                raise OSError('Cold SQLite clone failed with partial destination: ' + str(target))
        dst = sqlite3.connect(target)
        try:
            src.backup(dst, pages=8192)
            if dst.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError('SQLite consistency check failed: ' + str(source))
            dst.execute('PRAGMA journal_mode=DELETE')
        finally:
            dst.close()
    finally:
        src.close()
    # A consistent backup retains the source observation clock. The enclosing
    # checkpoint fences source changes and seals the backup bytes separately;
    # saved answers must not become stale merely because the file was copied.
    shutil.copystat(source, target)
    return 'sqlite_online_backup'


def sync_publish(stage, target):
    # All database connections are closed before publication. Flush copied bytes
    # as well as metadata before the final same-filesystem atomic rename.
    for p in stage.rglob('*'):
        if p.is_file():
            with p.open('rb') as f: os.fsync(f.fileno())
    for p in [*sorted((p for p in stage.rglob('*') if p.is_dir()), reverse=True), stage]:
        fd = os.open(p, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    if target.exists(): raise ValueError('Destination appeared during publication')
    stage.rename(target)
    fd = os.open(target.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def checkpoint(config_path, output):
    config = validate_config(config_path)
    config_sha = sha(config_path)
    if config.get('recovery'): raise ValueError('Checkpoint the active build, not a recovery inspection')
    build = Path(config['build']).resolve()
    target = fresh_destination(output, build)
    stage = target.with_name('.' + target.name + '.partial-' + uuid.uuid4().hex)
    stage.mkdir()
    with build_lock(build, exclusive=True):
        before = inventory(build)
        directories = sorted(p.relative_to(build).as_posix() for p in build.rglob('*') if p.is_dir())
        for name in directories: (stage / 'build' / name).mkdir(parents=True, exist_ok=True)
        code = code_identity(); deps = dependencies(build, config)
        entries = {}; databases = []; database_copy_methods = {}
        for name in before:
            if name.endswith(('-wal', '-journal')): continue
            source, dest = build / name, stage / 'build' / name
            with source.open('rb') as f: is_db = f.read(16) == b'SQLite format 3\x00'
            if is_db:
                database_copy_methods[name] = sqlite_copy(source, dest); databases.append(name)
            else: copy_file(source, dest)
            entries[name] = file_entry(dest)
        after = inventory(build)
        if before != after:
            write(stage / 'source-change-rejection.json', {'changed': [n for n in sorted(set(before) | set(after)) if before.get(n) != after.get(n)]})
            raise ValueError('Source changed during checkpoint; partial output not published')
        if sha(config_path) != config_sha: raise ValueError('Configuration changed during checkpoint')
        if code != code_identity(): raise ValueError('Code changed during checkpoint')
        check_dependencies(deps)
        manifest = {'schema': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
                    'original_build': str(build), 'configuration': config, 'files': entries,
                    'directories': directories, 'databases': databases, 'code': code, 'dependencies': deps,
                    'database_copy_methods': database_copy_methods,
                    'scope': 'Same-machine inspection recovery; external manifest dependencies and current ledger required. Not clinical or production qualification.'}
        write(stage / 'manifest.json', manifest)
        write(stage / 'complete.json', {'manifest_sha256': sha(stage / 'manifest.json')})
        sync_publish(stage, target)
    return {'checkpoint': str(target), 'checkpoint_id': sha(target / 'manifest.json'),
            'files': len(entries), 'databases': len(databases), 'bytes': sum(e['size'] for e in entries.values())}


def verify_checkpoint(path):
    path = Path(path).resolve()
    if sha(path / 'manifest.json') != read(path / 'complete.json')['manifest_sha256']:
        raise ValueError('Checkpoint manifest checksum mismatch')
    m = read(path / 'manifest.json')
    if m.get('schema') != VERSION: raise ValueError('Unsupported checkpoint version')
    if m['code'] != code_identity(): raise ValueError('Checkpoint code differs from current release')
    check_dependencies(m['dependencies'])
    names = inventory(path / 'build')
    if set(names) != set(m['files']): raise ValueError('Checkpoint inventory mismatch')
    for name in m['directories']:
        if not (path / 'build' / safe_relative(name)).is_dir(): raise ValueError('Missing checkpoint directory')
    for name, expected in m['files'].items():
        p = path / 'build' / safe_relative(name)
        if file_entry(p) != expected: raise ValueError('Corrupt checkpoint file: ' + name)
    return m


def restore(checkpoint_path, output):
    source = Path(checkpoint_path).resolve()
    m = verify_checkpoint(source)
    target = fresh_destination(output, source, m['original_build'])
    stage = target.with_name('.' + target.name + '.partial-' + uuid.uuid4().hex)
    stage.mkdir()
    for name in m['directories']: (stage / 'build' / safe_relative(name)).mkdir(parents=True, exist_ok=True)
    for name, expected in m['files'].items():
        destination = stage / 'build' / safe_relative(name)
        copy_file(source / 'build' / name, destination)
        if file_entry(destination) != expected: raise ValueError('Copy changed during restore: ' + name)
    # Keep immutable saved evidence/results unchanged; only installation links move.
    changes = {}
    original = Path(m['original_build'])
    for name in INTERNAL_LINKS:
        p = stage / 'build/stack' / name
        value = read(p); old = value['path']
        relative = Path(old).resolve().relative_to(original)
        value['path'] = str(target / 'build' / relative)
        p.write_text(json.dumps(value, indent=2) + '\n')
        changes[name] = {'from': old, 'to': value['path']}
    checkpoint_id = sha(source / 'manifest.json')
    config = dict(m['configuration'], build=str(target / 'build'),
                  recovery={'mode': 'inspection_only', 'checkpoint_id': checkpoint_id})
    write(stage / 'configuration.json', config)
    write(stage / 'recovery.json', {'schema': VERSION, 'checkpoint_id': checkpoint_id,
          'build': str(target / 'build'), 'link_rebindings': changes,
          'code': m['code'], 'dependencies': m['dependencies'], 'provider_ledger_restored': False})
    validate_config(stage / 'configuration.json', build_override=stage / 'build')
    sync_publish(stage, target)
    return {'restored': str(target), 'configuration': str(target / 'configuration.json'),
            'mode': 'inspection_only', 'checkpoint_id': checkpoint_id, 'link_rebindings': changes}
