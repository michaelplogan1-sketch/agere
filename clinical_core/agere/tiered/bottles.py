"""Tier 0: the bottle store. A bottle is a solved question shape that runs with zero model calls.

bottle = question pattern + typed parameters + deterministic SQL recipe + the validation it passed + provenance.
Matching is exact: the question must equal the bottle's template with each {name} slot filled by a value of that
slot's type. A bottle carries the schema version it was made under and is ignored under any other.
"""
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from . import ledger, store

DEFAULT = 'artifacts/agere-tiered-core/bottles.sqlite3'
SLOT = {'year': r'20\d{2}', 'month': r'20\d{2}-(?:0[1-9]|1[0-2])', 'int': r'\d{1,6}', 'text': r'[a-z0-9][a-z0-9 _\-]{0,60}'}


def _norm(text):
    return ' '.join(str(text).lower().strip().rstrip('?.').split())


def pattern(template, params):
    """Regex for a template, or None when the template does not carry every parameter exactly once."""
    text = _norm(template)
    names = re.findall(r'\{([a-z_][a-z0-9_]*)\}', text)
    if sorted(names) != sorted(params) or len(set(names)) != len(names): return None
    if any(params[n].get('type') not in SLOT for n in names): return None
    parts = re.split(r'\{[a-z_][a-z0-9_]*\}', text)
    out = re.escape(parts[0])
    for name, rest in zip(names, parts[1:]):
        out += f"(?P<{name}>{SLOT[params[name]['type']]})" + re.escape(rest)
    return out


class Bottles:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('AGERE_TIERED_BOTTLES', DEFAULT))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute('''CREATE TABLE IF NOT EXISTS bottle(
            id INTEGER PRIMARY KEY, created TEXT, template TEXT, pattern TEXT UNIQUE, params_json TEXT, sql TEXT, grain_json TEXT,
            validation_json TEXT, provenance_json TEXT, schema_version TEXT, status TEXT, uses INTEGER DEFAULT 0)''')
        if 'read_json' not in [c[1] for c in self.db.execute('PRAGMA table_info(bottle)')]:
            self.db.execute('ALTER TABLE bottle ADD COLUMN read_json TEXT')
        # The track record: every use of a saved recipe, every independent re-check of its answer, and every time it was applied
        # to a question it does not fit. Read by clinical_core/agere/tiered/upkeep.py, which decides whether a recipe keeps answering.
        self.db.execute('''CREATE TABLE IF NOT EXISTS bottle_event(
            id INTEGER PRIMARY KEY, at TEXT, bottle_id INTEGER, kind TEXT, question TEXT, params_json TEXT, detail_json TEXT)''')
        self.db.execute('CREATE INDEX IF NOT EXISTS bottle_event_bottle ON bottle_event(bottle_id, kind)')
        self.db.commit()

    def add(self, question, recipe, validation, provenance, *, status='provisional'):
        """Store a validated Tier 4 recipe as a bottle. -> (id, None) or (None, reason it was not bottled)."""
        template, params = recipe.get('question_template'), recipe.get('params') or {}
        if not template: return None, 'no question template'
        rx = pattern(template, params)
        if rx is None: return None, 'template does not carry each parameter exactly once with a known type'
        m = re.fullmatch(rx, _norm(question))
        if not m: return None, 'template does not reproduce the original question'
        if any(_norm(m.group(n)) != _norm(params[n].get('value')) for n in params): return None, 'template slots differ from the recipe parameters'
        used = set(re.findall(r'(?<![:\w]):([a-z_][a-z0-9_]*)', recipe['sql']))
        if used != set(params): return None, 'SQL placeholders do not match the parameters'
        try:
            cur = self.db.execute('INSERT INTO bottle(created,template,pattern,params_json,sql,grain_json,validation_json,provenance_json,'
                                  'schema_version,status,read_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                  (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), template, rx,
                                   json.dumps({n: {'type': p['type']} for n, p in params.items()}), recipe['sql'],
                                   json.dumps(recipe.get('grain') or []), json.dumps(validation), json.dumps(provenance),
                                   store.schema_version(), status, json.dumps(recipe['read']) if recipe.get('read') else None))
            self.db.commit()
            return cur.lastrowid, None
        except sqlite3.IntegrityError:
            old = self.db.execute('SELECT id, status FROM bottle WHERE pattern = ?', (rx,)).fetchone()
            if not old or old[1] != 'rejected': return None, 'a bottle with this pattern already exists'
            # A rejected plan is replaced by the new validated one, which starts again as provisional.
            self.db.execute('UPDATE bottle SET created=?, template=?, params_json=?, sql=?, grain_json=?, validation_json=?, provenance_json=?, '
                            'schema_version=?, status=?, read_json=? WHERE id=?',
                            (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), template, json.dumps({n: {'type': p['type']} for n, p in params.items()}),
                             recipe['sql'], json.dumps(recipe.get('grain') or []), json.dumps(validation),
                             json.dumps({**provenance, 'replaced_rejected': True}), store.schema_version(), status,
                             json.dumps(recipe['read']) if recipe.get('read') else None, old[0]))
            self.db.commit()
            return old[0], None

    def match(self, question, *, statuses=('promoted', 'provisional')):
        """-> (bottle dict, params) or None. Exact match only, current schema version only."""
        # Matched without regard to case, but slot values keep the question's own case (identifiers are case-sensitive).
        text, version = ' '.join(str(question).strip().rstrip('?.').split()), store.schema_version()
        marks = ','.join('?' * len(statuses))
        for bid, rx, params_json, sql, grain_json, read_json in self.db.execute(
                f'SELECT id, pattern, params_json, sql, grain_json, read_json FROM bottle WHERE schema_version=? AND status IN ({marks}) ORDER BY id',
                (version, *statuses)):
            m = re.fullmatch(rx, text, re.I)
            if not m: continue
            types = json.loads(params_json)
            params = {n: (int(v) if types[n]['type'] == 'int' else v) for n, v in m.groupdict().items()}
            return {'id': bid, 'sql': sql, 'grain': json.loads(grain_json), 'read': json.loads(read_json) if read_json else None}, params
        return None

    EVENTS = ('use', 'near_use', 'check_agree', 'check_differ', 'misapplied')

    def used(self, bottle_id, question=None, params=None, via='exact'):
        self.db.execute('UPDATE bottle SET uses = uses + 1 WHERE id=?', (bottle_id,)); self.db.commit()
        self.event(bottle_id, 'use' if via == 'exact' else 'near_use', question, params)

    def event(self, bottle_id, kind, question=None, params=None, detail=None, at=None):
        """One line of a recipe's track record. kind: use, near_use, check_agree, check_differ, misapplied."""
        if kind not in self.EVENTS: raise ValueError(f'unknown track-record event {kind}')
        self.db.execute('INSERT INTO bottle_event(at, bottle_id, kind, question, params_json, detail_json) VALUES(?,?,?,?,?,?)',
                        (at or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), bottle_id, kind,
                         ledger.fingerprint(question) if (question is not None and ledger.hosted()) else question,
                         json.dumps(params, default=str) if params is not None else None, json.dumps(detail, default=str) if detail is not None else None))
        self.db.commit()

    def record(self, bottle_id):
        """-> {kind: count} for one recipe."""
        out = {k: 0 for k in self.EVENTS}
        out.update(dict(self.db.execute("SELECT kind, COUNT(*) FROM bottle_event WHERE bottle_id = ? AND (detail_json IS NULL OR detail_json NOT LIKE '%superseded%') GROUP BY kind", (bottle_id,)).fetchall()))
        return out

    def records(self):
        """-> {bottle_id: {kind: count}} for every recipe with any event."""
        out = {}
        for bid, kind, n in self.db.execute("SELECT bottle_id, kind, COUNT(*) FROM bottle_event WHERE detail_json IS NULL OR detail_json NOT LIKE '%superseded%' GROUP BY bottle_id, kind"):
            out.setdefault(bid, {k: 0 for k in self.EVENTS})[kind] = n
        return out

    def uses(self, bottle_id, limit=20):
        """-> distinct (question, params) this recipe answered, most recent first."""
        seen, out = set(), []
        for q, pj in self.db.execute("SELECT question, params_json FROM bottle_event WHERE bottle_id = ? AND kind IN ('use', 'near_use') AND question IS NOT NULL ORDER BY id DESC", (bottle_id,)):
            if q.lower() in seen: continue
            seen.add(q.lower()); out.append((q, json.loads(pj) if pj else {}))
            if len(out) >= limit: break
        return out

    def near_blocked(self, at_least=2):
        """Recipes applied to a question they do not fit at least this many times: the near-match rung no longer offers them."""
        return {r[0] for r in self.db.execute("SELECT bottle_id FROM bottle_event WHERE kind = 'misapplied' GROUP BY bottle_id HAVING COUNT(*) >= ?", (at_least,))}

    def set_status(self, bottle_id, status, reason=None):
        row = self.db.execute('SELECT provenance_json FROM bottle WHERE id=?', (bottle_id,)).fetchone()
        prov = json.loads(row[0]); prov.setdefault('status_history', []).append({'status': status, 'reason': reason,
                                                                                 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
        self.db.execute('UPDATE bottle SET status=?, provenance_json=? WHERE id=?', (status, json.dumps(prov), bottle_id)); self.db.commit()

    def full(self):
        """Every bottle with its recipe, for the learning tools."""
        cur = self.db.execute('SELECT id, template, status, sql, params_json, grain_json, read_json, validation_json, provenance_json FROM bottle ORDER BY id')
        return [{'id': r[0], 'template': r[1], 'status': r[2], 'sql': r[3], 'params': json.loads(r[4] or '{}'), 'grain': json.loads(r[5] or '[]'),
                 'read': json.loads(r[6]) if r[6] else None, 'validation': json.loads(r[7] or '{}'), 'provenance': json.loads(r[8] or '{}')} for r in cur.fetchall()]

    def all(self):
        cur = self.db.execute('SELECT id, template, status, uses, schema_version FROM bottle ORDER BY id')
        return [dict(zip(('id', 'template', 'status', 'uses', 'schema_version'), r)) for r in cur.fetchall()]
