"""dev3 halves (2026-10-24): the independent dev set is split once, by a fixed hash of each chain's root id, into a BUILD half (develop
against it) and a CHECK half (look only to measure whether a change transferred; never tune on it). A follow-up stays with its parent."""
import hashlib


def root_of(q, by_id):
    seen = set()
    while q.get('follow_up_of') and q['follow_up_of'] in by_id and q['id'] not in seen:
        seen.add(q['id'])
        q = by_id[q['follow_up_of']]
    return q['id']


def half_of(q, by_id):
    return 'check' if int(hashlib.sha1(root_of(q, by_id).encode()).hexdigest(), 16) % 2 else 'build'


def keep(qs, half):
    """The questions of one half ('build' | 'check'); None or 'all' keeps every question."""
    if half in (None, 'all'): return qs
    by_id = {q['id']: q for q in qs}
    return [q for q in qs if half_of(q, by_id) == half]
