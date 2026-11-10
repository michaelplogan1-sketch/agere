"""Lossless macOS filesystem compression for inactive saved-answer JSON only.

Paths, logical bytes, hashes, mode and modification clocks remain unchanged.
This never opens a warehouse, prunes history, changes JSON encoding, or touches
models, training data or evaluation banks. Run without --apply to inspect first.
macOS compression metadata needs native filesystem permissions: sandboxed ditto
can return success without a readable compressed file, so verify every copy.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

COMPRESSED = 0x20


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def stamp(path):
    s = Path(path).stat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_nlink)


def metadata(path):
    s = Path(path).stat()
    return (s.st_size, s.st_mtime_ns, stat.S_IMODE(s.st_mode), s.st_uid, s.st_gid)


def free_bytes(path):
    s = os.statvfs(path)
    return s.f_bavail * s.f_frsize


def attributes(path):
    # macOS Python does not expose os.listxattr/setxattr on every distribution.
    # Keep native compression metadata paired with its new resource fork.
    names = subprocess.check_output(['/usr/bin/xattr', str(path)], text=True).splitlines()
    excluded = {'com.apple.decmpfs', 'com.apple.ResourceFork'} if Path(path).stat().st_flags & COMPRESSED else set()
    return {name: bytes.fromhex(subprocess.check_output(['/usr/bin/xattr', '-px', name, str(path)], text=True))
            for name in names if name not in excluded}


def candidates(root, *, include_compressed=False):
    root = Path(root).resolve()
    if root.name != 'answer-workspaces':
        raise ValueError('Only an explicitly selected answer-workspaces directory is eligible')
    result = []
    for folder in root.iterdir():
        if folder.is_symlink() or not re.fullmatch(r'ANS-[0-9a-f]{24}', folder.name): continue
        for parent, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = [n for n in dirs if not (Path(parent) / n).is_symlink()]
            for name in files:
                if name not in ('answer.json', 'bundle.json'): continue
                p = Path(parent) / name
                if p.is_symlink(): continue
                s = p.stat()
                if (stat.S_ISREG(s.st_mode) and s.st_size > 1_048_576
                        and s.st_nlink == 1 and (include_compressed or not s.st_flags & COMPRESSED)):
                    result.append(p)
    return sorted(result, key=lambda p: p.stat().st_size)


def saved_key(path):
    parts = Path(path).parts
    try: return parts[parts.index('answer-workspaces') + 1:]
    except ValueError: raise ValueError('Not a saved-answer path') from None


def copy_index(roots, keys):
    """Locate the same saved snapshot in owned archives, metadata only."""
    result = {key: [] for key in keys}
    excluded = {'.git', '.venv', '.claude', 'node_modules', 'site-packages', '__pycache__',
                'models', 'adapters', 'research', 'bench', 'heldout', 'reserved', 'training'}
    for root in roots:
        for parent, dirs, files in os.walk(Path(root).resolve(), followlinks=False):
            dirs[:] = [n for n in dirs if n not in excluded and not (Path(parent) / n).is_symlink()]
            if 'answer-workspaces' not in Path(parent).parts: continue
            for name in files:
                if name not in ('answer.json', 'bundle.json'): continue
                p = Path(parent) / name
                if p.is_symlink(): continue
                key = saved_key(p)
                if key in result and p.stat().st_nlink == 1: result[key].append(p)
    return result


def share_compressed(source, target, *, source_hash=None, expected_source_stamp=None):
    """Share physical extents only after both independent files hash identically.

    Never fall back to a full copy. A saved path's own mode, owner and clocks
    remain unchanged even if an archive copy has a different timestamp.
    """
    from clinical_core.agere.local_release import _clone_function
    source, target = Path(source), Path(target)
    if source.resolve() == target.resolve(): return {'path': str(target), 'status': 'canonical_compressed'}
    if source.is_symlink() or target.is_symlink() or target.stat().st_nlink != 1:
        raise ValueError('Only independent ordinary snapshot copies can share storage')
    source_compressed = bool(source.stat().st_flags & COMPRESSED)
    source_stamp, target_stamp = stamp(source), stamp(target)
    if expected_source_stamp is not None and source_stamp != expected_source_stamp:
        raise ValueError('Canonical snapshot changed since verification')
    expected = metadata(target); current = target.stat()
    if source.stat().st_size != current.st_size:
        return {'path': str(target), 'status': 'different_content_preserved'}
    value = source_hash or digest(source)
    if digest(target) != value:
        return {'path': str(target), 'status': 'different_content_preserved'}
    if not source_compressed and target.stat().st_flags & COMPRESSED:
        return {'path': str(target), 'status': 'already_compressed', 'sha256': value,
                'logical_bytes': expected[0], 'mtime_ns': expected[1], 'metadata_preserved': True}
    clone = _clone_function()
    partial = target.with_name('.' + target.name + '.shared-' + uuid.uuid4().hex + '.partial')
    if clone is None or clone(os.fsencode(source), os.fsencode(partial), 0) != 0:
        raise OSError('Compressed clone unavailable; no full copy fallback: ' + str(partial))
    os.chmod(partial, stat.S_IMODE(current.st_mode))
    os.utime(partial, ns=(current.st_atime_ns, current.st_mtime_ns))
    # The new resource fork and compression header belong together. Preserve
    # other target-specific attributes rather than copying archive annotations
    # from the canonical path.
    attrs = attributes(target)
    for name in attributes(partial):
        if name not in attrs: subprocess.run(['/usr/bin/xattr', '-d', name, str(partial)], check=True)
    for name, value_attr in attrs.items():
        subprocess.run(['/usr/bin/xattr', '-wx', name, value_attr.hex(), str(partial)], check=True)
    if attributes(partial) != attrs:
        raise ValueError('Archive attributes differ; original and partial retained: ' + str(partial))
    if (metadata(partial) != expected or bool(partial.stat().st_flags & COMPRESSED) != source_compressed
            or digest(partial) != value):
        raise ValueError('Shared compressed copy failed preservation checks; partial retained: ' + str(partial))
    if stamp(source) != source_stamp or stamp(target) != target_stamp:
        raise ValueError('A snapshot changed before publication; partial retained: ' + str(partial))
    with partial.open('rb') as f: os.fsync(f.fileno())
    verified_inode = partial.stat().st_ino
    os.replace(partial, target)
    fd = os.open(target.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
    if metadata(target) != expected or target.stat().st_ino != verified_inode:
        raise RuntimeError('Post-publication shared snapshot integrity failed: ' + str(target))
    return {'path': str(target), 'status': 'shared_compressed_verified' if source_compressed else 'shared_uncompressed_verified', 'sha256': value,
            'logical_bytes': expected[0], 'mtime_ns': expected[1], 'metadata_preserved': True}


def compact(path, *, copier=None, before_publish=None):
    """Publish only a proven identical compressed inode; retain failed attempts."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError('Compression requires one ordinary, independently stored file')
    start = stamp(path); original_meta = metadata(path)
    if path.stat().st_flags & COMPRESSED:
        return {'path': str(path), 'status': 'already_compressed', 'sha256': digest(path),
                'logical_bytes': original_meta[0], 'mtime_ns': original_meta[1], 'metadata_preserved': True}
    # The original remains in place throughout verification. Reserve enough for
    # even an incompressible copy plus metadata; this is maintenance, not a query cap.
    if free_bytes(path.parent) < path.stat().st_size + 16_777_216:
        return {'path': str(path), 'status': 'deferred_insufficient_copy_space'}
    original_hash = digest(path)
    if stamp(path) != start: raise ValueError('Source changed while hashing; nothing replaced')
    partial = path.with_name('.' + path.name + '.compression-' + uuid.uuid4().hex + '.partial')
    if copier is None:
        def copier(source, target):
            subprocess.run(['/usr/bin/ditto', '--hfsCompression', str(source), str(target)], check=True)
    copier(path, partial)
    if (not partial.is_file() or metadata(partial) != original_meta or digest(partial) != original_hash):
        raise ValueError('Compressed copy did not preserve readable bytes/metadata; original and partial retained: ' + str(partial))
    if not partial.stat().st_flags & COMPRESSED:
        # ditto is permitted to decline compression. That is not corruption.
        # Preserve the attempt but share its original extents instead of keeping
        # a second full physical copy; the original snapshot is not replaced.
        if stamp(path) != start: raise ValueError('Source changed during native compression decline')
        share_compressed(path, partial, source_hash=original_hash, expected_source_stamp=start)
        return {'path': str(path), 'status': 'native_compression_declined_verified',
                'sha256': original_hash, 'logical_bytes': original_meta[0], 'mtime_ns': original_meta[1],
                'metadata_preserved': True, 'retained_attempt': str(partial)}
    with partial.open('rb') as f: os.fsync(f.fileno())
    if before_publish: before_publish()
    if stamp(path) != start:
        raise ValueError('Source changed during compression; original and partial retained: ' + str(partial))
    # Atomic replacement changes physical representation only. Logical content,
    # identity paths and observation clocks remain the verified original values.
    os.replace(partial, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
    if metadata(path) != original_meta or digest(path) != original_hash:
        raise RuntimeError('Post-publication integrity check failed: ' + str(path))
    return {'path': str(path), 'status': 'compressed_verified', 'logical_bytes': original_meta[0],
            'sha256': original_hash, 'mtime_ns': original_meta[1], 'metadata_preserved': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--copies-root', type=Path, action='append', default=[],
                        help='Owned artifact roots whose identical saved JSON copies should share compressed extents')
    parser.add_argument('--approved-manifest', type=Path,
                        help='Explicitly approved exact path list for cross-archive maintenance')
    parser.add_argument('--resume-journal', type=Path, action='append', default=[],
                        help='Skip completed preservation results; retry only unfinished paths')
    args = parser.parse_args(argv)
    if sys.platform != 'darwin': raise ValueError('This maintenance operation requires macOS')
    root = args.root.resolve(); files = candidates(root, include_compressed=bool(args.copies_root))
    approved = None
    if args.copies_root and args.apply:
        if args.approved_manifest is None:
            raise ValueError('Cross-archive writes require the exact approved manifest')
        manifest = json.loads(args.approved_manifest.read_text())
        if manifest.get('status') != 'approved_by_mike':
            raise ValueError('Cross-archive maintenance remains awaiting explicit approval')
        approved = {Path(p).resolve() for p in manifest['paths']}
        files = [p for p in files if p.resolve() in approved]
    # Finish already-compressed families first to release retained original
    # extents before allocating another compressed representation.
    files.sort(key=lambda p: (not bool(p.stat().st_flags & COMPRESSED), p.stat().st_size))
    index = copy_index(args.copies_root, {saved_key(p) for p in files}) if args.copies_root else {}
    if approved is not None:
        index = {key: [p for p in paths if p.resolve() in approved] for key, paths in index.items()}
    completed = set()
    settled = {'compressed_verified', 'already_compressed', 'shared_compressed_verified',
               'shared_uncompressed_verified', 'native_compression_declined_verified', 'different_content_preserved'}
    for previous in args.resume_journal:
        for line in previous.read_text().splitlines():
            result = json.loads(line)
            if result.get('status') in settled: completed.add(Path(result['path']).resolve())
    files = [p for p in files if p.resolve() not in completed or
             any(peer.resolve() not in completed for peer in index.get(saved_key(p), []))]
    print(json.dumps({'status': 'apply' if args.apply else 'inspection', 'files': len(files),
                      'logical_bytes': sum(p.stat().st_size for p in files), 'free_before': free_bytes(root)}), flush=True)
    if not args.apply: return
    args.journal.parent.mkdir(parents=True, exist_ok=True)
    # One owner; callers must stop serving/build jobs before maintenance.
    with (root / '.storage-maintenance.lock').open('a+b') as owner, args.journal.open('x') as journal:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for p in files:
            entry = {'path': str(p), 'status': 'started', 'free_bytes': free_bytes(root)}
            journal.write(json.dumps(entry) + '\n'); journal.flush(); os.fsync(journal.fileno())
            # Respect current snapshot publication locks without creating new
            # lock files in old immutable answer directories.
            folder = next(parent for parent in p.parents if parent.parent == root)
            lock_path = folder / '.capture.lock'
            with lock_path.open('rb') if lock_path.exists() else open(os.devnull, 'rb') as capture:
                if lock_path.exists(): fcntl.flock(capture, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try: entry = compact(p)
                except Exception as exc:
                    journal.write(json.dumps({'path': str(p), 'status': 'failed', 'error': repr(exc)}) + '\n')
                    journal.flush(); os.fsync(journal.fileno()); raise
            entry['free_bytes'] = free_bytes(root)
            journal.write(json.dumps(entry) + '\n'); journal.flush(); os.fsync(journal.fileno())
            print(json.dumps(entry), flush=True)
            if entry['status'] in ('compressed_verified', 'already_compressed', 'native_compression_declined_verified'):
                canonical_hash = entry.get('sha256') or digest(p)
                canonical_stamp = stamp(p)
                for target in sorted(set(index.get(saved_key(p), []))):
                    if target.resolve() == p.resolve(): continue
                    if target.resolve() in completed: continue
                    peer = {'path': str(target), 'status': 'share_started', 'canonical': str(p)}
                    journal.write(json.dumps(peer) + '\n'); journal.flush(); os.fsync(journal.fileno())
                    try:
                        peer = share_compressed(p, target, source_hash=canonical_hash, expected_source_stamp=canonical_stamp)
                        if peer['status'] == 'different_content_preserved' and target.stat().st_size > 1_048_576:
                            # A report revision may retain the same path but have
                            # different evidence. Never merge those versions;
                            # compress each one's own exact contents instead.
                            peer = compact(target)
                    except Exception as exc:
                        journal.write(json.dumps({'path': str(target), 'status': 'failed', 'error': repr(exc)}) + '\n')
                        journal.flush(); os.fsync(journal.fileno()); raise
                    peer['free_bytes'] = free_bytes(root)
                    journal.write(json.dumps(peer) + '\n'); journal.flush(); os.fsync(journal.fileno())
                    print(json.dumps(peer), flush=True)


if __name__ == '__main__': main()
