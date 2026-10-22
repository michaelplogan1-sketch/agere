"""Shared pieces of the payer / transitions / medication-access action families (care_gap_closure, transitions_of_care,
prior_auth_access): read-only semantic-db access, Wilson intervals and the action-step shape every family emits.

An action step is a proposal for a person or counterparty, never a claim it happened: {'step', 'counterparty', 'status':
'proposed', 'why'}. Nothing is marked done without a receipt (plans/act.py owns receipts); these engines only say what the
data shows is open and the next step that would close it. Synthetic warehouse data only."""
from pathlib import Path
import sqlite3


def open_db(path):
    from . import semantic_layer
    target = semantic_layer.ensure(path) if (Path(path)/'run.json').exists() else Path(path)/'semantic.sqlite3'
    return sqlite3.connect(f'file:{target}?mode=ro', uri=True)


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def wilson(k, n, z=1.96):
    if not n: return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** .5) / d
    return [round(max(0, c - h), 4), round(min(1, c + h), 4)]


def step(name, counterparty, why):
    return {'step': name, 'counterparty': counterparty, 'status': 'proposed', 'why': why}


def add_days(day, n):
    from datetime import date, timedelta
    return (date.fromisoformat(day[:10]) + timedelta(days=n)).isoformat()


def days_between(a, b):
    from datetime import date
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


def missing_tables(db, needed):
    """Tables a question needs that this warehouse lacks (reported as a limitation, never a crash)."""
    have = tables(db)
    return sorted(set(needed) - have)
