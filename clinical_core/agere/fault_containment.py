"""Fault containment: failures are contained where they happen, named, and healed - never allowed to take the answer path down.

Three containment points, each exercised by tools/fault_injection.py (injected failure sequences, recovery verified):

  missing tables   A decision engine reading a table this warehouse lacks raises DataAbsent (a ValueError naming the absent
                   source) instead of a raw sqlite OperationalError. The cascade treats it like any rejected proposal and moves
                   on; the receipt says which source is absent, and the stage diagnostics attribute it to execution.
  corrupt caches   A malformed SQLite file among the question-written state stores (learning, answers, conversations, plans, model
                   cache, ...) is quarantined (renamed, never deleted: it is evidence) and the store is recreated empty by its own
                   open-time schema code on the next use; learning restarts from the next answer. The warehouse and the semantic
                   layer are never touched: those are reported, not moved.
  provider faults  Already contained by the cascade (proposers.Cascade.run): an outage skips later Claude tiers for the question,
                   a timeout / error from one tier is noted and the next tier runs, a System One fallback picks among executed
                   candidates. fault_injection.py proves that, including sequences of faults in one question.
Synthetic data only."""
from pathlib import Path
import re
import sqlite3
import time

VERSION = 'fault-containment/1'
ABSENT = re.compile(r'no such (?:table|column|view): ?([\w.]+)', re.I)
CORRUPT = re.compile(r'malformed|disk image|not a database|file is encrypted or is not a database|database corruption', re.I)
# Question-written stores that are safe to recreate empty (mirrors request_locks.STATE_STORES).
RECREATABLE = ('answers.sqlite3', 'learning.sqlite3', 'conversations.sqlite3', 'question-plans.sqlite3', 'model-cache.sqlite3', 'outcomes.sqlite3',
               'interpretation-state.sqlite3', 'semantic-readings.sqlite3', 'context-passes.sqlite3', 'program-work.sqlite3', 'evidence-acquisition.sqlite3',
               'answer-stability.sqlite3', 'assertion-receipts.sqlite3', 'baseline-evidence.sqlite3', 'briefing-rank.sqlite3')
QUICK_CHECK_MAX_BYTES = 256 * 1024 * 1024  # a full quick_check on a larger store would stall the question; it is header/schema-checked instead


class DataAbsent(ValueError):
    """A source the question needs is not in this warehouse (the answer is partial or unknown, never a crash)."""
    def __init__(self, sources, detail=''):
        self.sources = sorted(set(sources))
        super().__init__(f"source {', '.join(self.sources)} absent from this warehouse; the question cannot be computed from the received data" + (f' ({detail})' if detail else ''))


def contain_engine(engine, target, patient_ids, params, as_of):
    """engine.execute with missing-source failures turned into DataAbsent."""
    try:
        return engine.execute(target, patient_ids, params, as_of)
    except sqlite3.OperationalError as exc:
        found = ABSENT.findall(str(exc))
        if found: raise DataAbsent(found, type(engine).__name__ if not hasattr(engine, '__name__') else engine.__name__.rsplit('.', 1)[-1]) from exc
        raise


def is_corrupt(exc):
    return isinstance(exc, sqlite3.DatabaseError) and not isinstance(exc, (sqlite3.OperationalError,)) or \
        (isinstance(exc, sqlite3.DatabaseError) and bool(CORRUPT.search(str(exc))))


def check_store(path):
    """None when the SQLite file is healthy, else the reason it is not."""
    path = Path(path)
    try:
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            if path.stat().st_size <= QUICK_CHECK_MAX_BYTES:
                row = db.execute('PRAGMA quick_check(1)').fetchone()
                if row and row[0] != 'ok': return f'quick_check: {row[0]}'
            else:
                db.execute('SELECT count(*) FROM sqlite_master').fetchone()
        finally:
            db.close()
    except sqlite3.DatabaseError as exc:
        return str(exc)
    return None


def heal_state_stores(stack_path, names=RECREATABLE, now=None):
    """Quarantine every corrupt recreatable store under stack_path. Returns [{store, reason, quarantined_to}] (empty = all healthy).
    The corrupt file and its -wal / -shm are renamed *.corrupt-<stamp>; the store's own schema code recreates it on next open."""
    out, stamp = [], time.strftime('%Y%m%dT%H%M%S', time.gmtime(now))
    for name in names:
        p = Path(stack_path) / name
        if not p.exists() or p.stat().st_size == 0: continue
        reason = check_store(p)
        if not reason: continue
        dest = p.with_name(f'{name}.corrupt-{stamp}')
        for suffix in ('', '-wal', '-shm'):
            src = Path(str(p) + suffix)
            if src.exists(): src.rename(Path(str(dest) + suffix))
        out.append({'store': name, 'reason': reason[:160], 'quarantined_to': dest.name})
    return out


def on_tier_error(stack_path, exc):
    """Called by the cascade when a tier raised: a database error triggers one health sweep of the state stores. Returns the heals
    (possibly empty) so the cascade can put them in the receipt; never raises."""
    try:
        if isinstance(exc, sqlite3.DatabaseError) and (CORRUPT.search(str(exc)) or not isinstance(exc, sqlite3.OperationalError)):
            return heal_state_stores(stack_path)
    except Exception:  # noqa: BLE001 - healing is best effort
        pass
    return []
