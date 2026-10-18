"""Memoised free-text readers for streaming builds.

The two free-text passes dominate a semantic build (pilot: note_facts ~144 s, therapy_barriers ~139 s, almost all of it
in note_facts.extract and therapy_barriers.candidates). Both functions are pure: their result depends only on their
arguments (one note's text and its stream / field; one text unit). Streaming builds run them through a persistent cache
keyed by a hash of the arguments, namespaced by a hash of the function's source, so

- a mini build re-reads only notes it has not seen before, and
- therapy_barriers.build, whose tier-2 classifier is fitted on every patient's tier-1 labels and whose case ids number
  candidates across the whole population (a global step), can be re-run over the whole build after every tick for the
  price of its global part (~5 s at pilot scale).

Every call returns a fresh copy (callers mutate the dicts). The batch path never uses this module.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import inspect
from pathlib import Path
import pickle
import sqlite3
import zlib

CACHE_FILE = 'text-reader-cache.sqlite3'


def _namespace(fn):
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        src = fn.__qualname__
    return hashlib.sha256(src.encode()).hexdigest()[:12]


class _Memo:
    """SQLite-backed: every lookup is a primary-key read and misses are written in batches, so memory stays flat however
    many notes a build reads (a preloaded dict of a pilot's note texts would push the process into swap)."""
    BATCH = 500

    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path)
        self.db.execute('CREATE TABLE IF NOT EXISTS memo(ns TEXT, key TEXT, value BLOB, PRIMARY KEY(ns, key))')
        self.new = []
        self.stats = {'hits': 0, 'misses': 0}

    def wrap(self, fn):
        ns = fn.__module__.rsplit('.', 1)[-1] + '.' + fn.__name__ + ':' + _namespace(fn)

        def wrapped(*args, **kw):
            key = hashlib.sha256(pickle.dumps((args, sorted(kw.items())), protocol=4)).hexdigest()
            row = self.db.execute('SELECT value FROM memo WHERE ns = ? AND key = ?', (ns, key)).fetchone()
            if row is not None:
                self.stats['hits'] += 1
                return pickle.loads(zlib.decompress(row[0]))
            self.stats['misses'] += 1
            out = fn(*args, **kw)
            raw = pickle.dumps(out, protocol=4)
            self.new.append((ns, key, zlib.compress(raw, 3)))
            if len(self.new) >= self.BATCH:
                self.flush()
            return pickle.loads(raw)
        wrapped.__wrapped__ = fn
        return wrapped

    def flush(self):
        if self.new:
            with self.db:
                self.db.executemany('INSERT OR REPLACE INTO memo VALUES (?,?,?)', self.new)
            self.new = []

    def close(self):
        self.flush()
        self.db.close()


@contextmanager
def text_readers(cache_dir):
    """Patch note_facts.extract and therapy_barriers.candidates with the persistent memo for the duration."""
    from ..clinical import note_facts, therapy_barriers
    memo = _Memo(Path(cache_dir) / CACHE_FILE)
    saved = (note_facts.extract, therapy_barriers.candidates)
    note_facts.extract = memo.wrap(saved[0])
    therapy_barriers.candidates = memo.wrap(saved[1])
    try:
        yield memo
    finally:
        note_facts.extract, therapy_barriers.candidates = saved
        memo.close()
