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


def work_queue(rows, priority, resource=lambda r: None, top=10):
    """The proposed actions in `rows` as an order-book work queue (work_queue.OrderBook): best first, equal priority in arrival
    order. Returns {'resting', 'depth', 'next'}: a level-2 view and the next items a team would take. Capacity matching
    (OrderBook.match) is available to the caller; here nothing is filled, only ranked."""
    from ..work_queue import OrderBook
    book = OrderBook()
    for i, r in enumerate(rows):
        if r.get('next_action') or r.get('next_actions'):
            book.add(i, priority(r), resource(r), r)
    ordered = book.ordered()
    return {'resting': len(book), 'depth': book.depth()[:8],
            'next': [{'patient_id': v['payload']['patient_id'], 'priority': v['priority'], 'resource': v['resource'],
                      'action': (v['payload'].get('next_action') or v['payload']['next_actions'][0])['step']} for _, v in ordered[:top]]}


NEGATION_Q = r"\b(?:not|no|never|without|open|unresolved|missing|overdue|lapsed|didn't|did not|haven't|lack\w*)\b"
RATE_Q = r'%|\b(?:percent\w*|share|proportion|ratio|rate)\b'


def purpose(base, question, *, vocabulary=''):
    """Route purpose text. The plan's executable text is what the literal check reads, so the purpose states, in canonical words, the
    semantics the engine really applies: the exclusion of already-closed items, the denominator of a rate, and the domain terms."""
    import re
    low = str(question).lower()
    out = [base]
    if re.search(NEGATION_Q, low): out.append('excludes (not including) items that already have closing evidence')
    if re.search(RATE_Q, low): out.append('rate = numerator / denominator over the applicable population')
    if vocabulary: out.append(vocabulary)
    return '; '.join(out)
