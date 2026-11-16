"""One row per question: tiers attempted, tokens and dollars per tier, latency, validation, final tier."""
import json
import os
import sqlite3
import time
from pathlib import Path

DEFAULT = 'artifacts/agere-tiered-core/ledger.sqlite3'
MAX_ROWS_KEPT = 200
# USD per million tokens, from config/agere/pricing.json (checked 2026-10-12).
PRICES = {
    'claude-sonnet-5-5': {'in': 2.0, 'out': 10.0, 'cache_write': 4.0, 'cache_read': 0.1},
    'claude-haiku-5-5': {'in': 0.1, 'out': 0.5, 'cache_write': 0.2, 'cache_read': 0.01},
    'claude-opus-5-5': {'in': 4.0, 'out': 20.0, 'cache_write': 8.0, 'cache_read': 0.2},
}


def claude_usd(model, usage):
    p = PRICES[model]
    return (usage.get('input_tokens', 0) * p['in'] + usage.get('output_tokens', 0) * p['out'] +
            usage.get('cache_creation_input_tokens', 0) * p['cache_write'] + usage.get('cache_read_input_tokens', 0) * p['cache_read']) / 1e6


def tokens(usage):
    return sum(usage.get(k, 0) or 0 for k in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'))



def hosted():
    """The customer web server sets AGERE_HOSTED=1: shared stores then keep no question text or result rows."""
    return os.environ.get('AGERE_HOSTED') == '1'


def fingerprint(text):
    """A keyed fingerprint of a question (keyed by AGERE_SECRET_KEY when set): repeats can be counted, the words are not kept.
    A question can name a patient, and logs are read and retained more widely than the data."""
    import hashlib, hmac
    words = ' '.join(str(text or '').split()).lower()
    return 'q:' + hmac.new((os.environ.get('AGERE_SECRET_KEY') or 'agere-audit').encode(), words.encode(), hashlib.sha256).hexdigest()[:16]


class Ledger:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('AGERE_TIERED_LEDGER', DEFAULT))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute('''CREATE TABLE IF NOT EXISTS question(
            id INTEGER PRIMARY KEY, at TEXT, run TEXT, question TEXT, final_tier TEXT, status TEXT, validation TEXT,
            tiers_json TEXT, tokens INTEGER, usd REAL, seconds REAL, recipe_json TEXT, result_rows INTEGER,
            eval_id TEXT, correct INTEGER, config_json TEXT)''')
        if 'rows_json' not in [c[1] for c in self.db.execute('PRAGMA table_info(question)')]:
            self.db.execute('ALTER TABLE question ADD COLUMN rows_json TEXT')  # the answer itself, so a miss can be explained later
        self.db.commit()

    def write(self, run, question, outcome, *, eval_id=None, correct=None, config=None):
        steps = outcome['steps']
        self.db.execute('INSERT INTO question(at,run,question,final_tier,status,validation,tiers_json,tokens,usd,seconds,recipe_json,'
                        'result_rows,eval_id,correct,config_json,rows_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), run, fingerprint(question) if hosted() else question, outcome.get('final_tier'), outcome['status'],
                         json.dumps(outcome.get('validation')), json.dumps(steps), sum(s.get('tokens', 0) for s in steps),
                         sum(s.get('usd', 0.0) for s in steps), outcome['seconds'], json.dumps(outcome.get('recipe'), default=str),
                         len(outcome.get('rows') or []), eval_id, None if correct is None else int(correct), json.dumps(config or {}),
                         None if hosted() else json.dumps({'columns': outcome.get('columns'), 'rows': (outcome.get('rows') or [])[:MAX_ROWS_KEPT]}, default=str)))
        self.db.commit()

    def rows(self, run):
        cur = self.db.execute('SELECT * FROM question WHERE run=? ORDER BY id', (run,))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]
