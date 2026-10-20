"""Question-driven financial learning, bound to cited program versions.

Feedback is not truth. Only an exact parameter/value match to the current stored
rulebook becomes a reusable rule lesson. This verifies the bounded rule, not the
free-text correction, the external source's accuracy, or an ACO's entitlement.
"""
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

from . import programs

VERSION = 'economics-learning/1'
PARAMETERS = {
    'sharing_rate': 'Savings sharing rate', 'loss_rate': 'Loss sharing rate',
    'loss_rate_min': 'Minimum loss sharing rate', 'loss_rate_max': 'Maximum loss sharing rate',
    'savings_cap_pct': 'Savings payment cap', 'loss_cap_pct': 'Loss cap against benchmark',
    'loss_cap_revenue_pct': 'Loss cap against participant revenue', 'two_sided': 'Downside risk applies',
    'msr': 'Minimum savings rate', 'mlr': 'Minimum loss rate',
}
TOPICS = {
    'shared_savings': r'\bshared[- ]savings\b|\bmssp\b',
    'downside_risk': r'\bdownside\b|\bshared[- ]loss(?:es)?\b|\btwo[- ]sided\b|\bloss (?:rate|cap|share|sharing)\b',
    'benchmarks': r'\bbenchmark\b|\bminimum (?:savings|loss) rate\b|\bmsr\b|\bmlr\b',
    'quality_payment': r'\bquality\b.*\b(?:savings|payment|contract|mssp)\b',
}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def topics(question):
    return [k for k, pattern in TOPICS.items() if re.search(pattern, question, re.I)]


def _scope(question):
    ref = programs.detect(question)
    if not ref or ref.get('year') is None: return None
    return (ref['id'], ref.get('variant'), ref['year'])


def rule_options(question):
    """Only offer clauses when the original question supplies a program and year."""
    scope = _scope(question)
    if not scope: return []
    ref = dict(zip(('id', 'variant', 'year'), scope))
    try: res = programs.resolve(ref)
    except ValueError: return []
    out = []
    for name, label in PARAMETERS.items():
        if name not in res['parameters']: continue
        value = programs.value(res, name)
        out.append({'label': f"{res['label']} {res['year']} · {label}",
                    'program': ref, 'parameter': name, 'value': value,
                    'source_digest': programs.document_digest(res['doc']),
                    'sources': programs.sources(res, {name})})
    return out


def verify_rule(path, correction_id, rule):
    """Promote only the source-bound fact; never promote the author's free-text note."""
    if type(correction_id) is not int or correction_id < 1: raise ValueError('A saved correction is required')
    if not isinstance(rule, dict) or set(rule) != {'program', 'parameter', 'value', 'source_digest'}:
        raise ValueError('Rule verification needs program, parameter, value and source_digest')
    ref, name = rule['program'], rule['parameter']
    if not isinstance(ref, dict) or set(ref) - {'id', 'variant', 'year'} or type(ref.get('year')) is not int:
        raise ValueError('Rule verification requires an explicit program and year')
    if name not in PARAMETERS: raise ValueError('Unsupported financial rule parameter')
    res = programs.resolve(ref)
    if programs.validate_program(res['doc']): raise ValueError('Program specification failed validation')
    digest = programs.document_digest(res['doc'])
    if rule['source_digest'] != digest: raise ValueError('Program specification changed; reload the rule before verification')
    if name not in res['parameters'] or _json(programs.value(res, name)) != _json(rule['value']):
        raise ValueError('Correction value does not match the cited program parameter')
    from . import learning
    with closing(learning._db(path)) as db, db:
        row = db.execute('SELECT question,note,created FROM corrections WHERE rowid=?', (correction_id,)).fetchone()
        if not row: raise ValueError('Unknown correction')
        scope = _scope(row[0])
        if scope != (res['id'], res['variant'], res['year']):
            raise ValueError('Correction must name the same program, variant and performance year')
        payload = {'schema': VERSION, 'correction_id': correction_id, 'question': row[0],
                   'program': {'id': res['id'], 'variant': res['variant'], 'year': res['year']},
                   'parameter': name, 'value': programs.value(res, name), 'source_digest': digest,
                   'sources': programs.sources(res, {name}), 'last_verified': res['doc']['last_verified'],
                   'authority': 'exact_match_to_stored_cited_rule; free_text_feedback_not_verified',
                   'verification': 'deterministic_parameter_and_version_check', 'created': time.time()}
        ident = hashlib.sha256(_json([correction_id, rule]).encode()).hexdigest()
        db.execute('CREATE TABLE IF NOT EXISTS economics_lessons(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        db.executescript('''CREATE TRIGGER IF NOT EXISTS economics_lessons_immutable_update BEFORE UPDATE ON economics_lessons
            BEGIN SELECT RAISE(ABORT,'Rule lessons are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS economics_lessons_immutable_delete BEFORE DELETE ON economics_lessons
            BEGIN SELECT RAISE(ABORT,'Rule lessons are append-only'); END;''')
        db.execute('INSERT OR IGNORE INTO economics_lessons VALUES(?,?)', (ident, _json(payload)))
    return {'id': ident, 'status': 'verified_rule', 'parameter': name,
            'effect': 'Rule checked against the cited specification and saved for this program and year. The free-text correction remains feedback.'}


def lessons(path):
    target = Path(path) / 'learning.sqlite3'
    if not target.exists(): return []
    with closing(sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='economics_lessons'").fetchone(): return []
        rows = db.execute('SELECT id,payload FROM economics_lessons').fetchall()
        corrections = db.execute('SELECT rowid,question FROM corrections ORDER BY rowid DESC').fetchall()
    latest = {}
    for cid, q in corrections:
        scope = _scope(q)
        if scope and scope not in latest: latest[scope] = cid
    out = []
    for ident, raw in rows:
        lesson = json.loads(raw)
        ref = lesson['program']; status = 'active'
        try:
            res = programs.resolve(ref)
            if programs.document_digest(res['doc']) != lesson['source_digest']: status = 'source_changed'
        except (ValueError, KeyError): status = 'source_unavailable'
        if status == 'active' and latest.get((ref['id'], ref.get('variant'), ref['year']), 0) > lesson['correction_id']:
            status = 'suspended_by_new_correction'
        out.append({'id': ident, **lesson, 'status': status})
    return sorted(out, key=lambda r: r['created'], reverse=True)


def context(path, question):
    scope = _scope(question)
    if not scope: return []
    return [r for r in lessons(path) if r['status'] == 'active' and
            (r['program']['id'], r['program'].get('variant'), r['program']['year']) == scope][:8]


def summary(path):
    counts, gaps, questions = Counter(), Counter(), 0
    target = Path(path) / 'learning.sqlite3'
    if target.exists():
        with closing(sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='outcomes'").fetchone():
                for q, status, reason, n in db.execute('SELECT question,status,reason,COUNT(*) FROM outcomes GROUP BY question,status,reason'):
                    tags = topics(q)
                    if not tags: continue
                    questions += n
                    for tag in tags: counts[tag] += n
                    if status != 'answered': gaps[reason or status] += n
    learned = lessons(path)
    return {'schema': VERSION, 'questions': questions, 'topics': dict(counts),
            'gaps': [{'reason': r, 'count': n} for r, n in gaps.most_common(10)],
            'verified_rules': sum(r['status'] == 'active' for r in learned),
            'inactive_rules': sum(r['status'] != 'active' for r in learned),
            'lessons': learned[:20], 'model_weights_trained': False,
            'mode': 'Questions accumulate automatically; source-checked rule corrections inform later questions in the same program and year.'}
