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

from . import store

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
                                  'schema_version,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                                  (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), template, rx,
                                   json.dumps({n: {'type': p['type']} for n, p in params.items()}), recipe['sql'],
                                   json.dumps(recipe.get('grain') or []), json.dumps(validation), json.dumps(provenance),
                                   store.schema_version(), status))
            self.db.commit()
            return cur.lastrowid, None
        except sqlite3.IntegrityError:
            return None, 'a bottle with this pattern already exists'

    def match(self, question, *, statuses=('promoted', 'provisional')):
        """-> (bottle dict, params) or None. Exact match only, current schema version only."""
        text, version = _norm(question), store.schema_version()
        marks = ','.join('?' * len(statuses))
        for bid, rx, params_json, sql, grain_json in self.db.execute(
                f'SELECT id, pattern, params_json, sql, grain_json FROM bottle WHERE schema_version=? AND status IN ({marks}) ORDER BY id',
                (version, *statuses)):
            m = re.fullmatch(rx, text)
            if not m: continue
            types = json.loads(params_json)
            params = {n: (int(v) if types[n]['type'] == 'int' else v) for n, v in m.groupdict().items()}
            return {'id': bid, 'sql': sql, 'grain': json.loads(grain_json)}, params
        return None

    def used(self, bottle_id):
        self.db.execute('UPDATE bottle SET uses = uses + 1 WHERE id=?', (bottle_id,)); self.db.commit()

    def set_status(self, bottle_id, status, reason=None):
        row = self.db.execute('SELECT provenance_json FROM bottle WHERE id=?', (bottle_id,)).fetchone()
        prov = json.loads(row[0]); prov.setdefault('status_history', []).append({'status': status, 'reason': reason,
                                                                                 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
        self.db.execute('UPDATE bottle SET status=?, provenance_json=? WHERE id=?', (status, json.dumps(prov), bottle_id)); self.db.commit()

    def all(self):
        cur = self.db.execute('SELECT id, template, status, uses, schema_version FROM bottle ORDER BY id')
        return [dict(zip(('id', 'template', 'status', 'uses', 'schema_version'), r)) for r in cur.fetchall()]
