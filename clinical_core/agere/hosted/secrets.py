"""Per-tenant encrypted warehouse credentials for hosted Agere.

SecretBox wraps cryptography's Fernet (AES-128-CBC + HMAC-SHA256) keyed by AGERE_SECRET_KEY, the urlsafe base64
encoding of 32 random bytes (SecretBox.generate_key()). CredentialStore keeps one encrypted mapping per
(org, name) in SQLite. Each ciphertext also seals the org and name it was written for, so a row copied to another
tenant or name fails to open. Error messages name orgs, credential names and keys, never secret values or the
master key.
"""
import base64
import binascii
import json
import os
import sqlite3
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

ENV_KEY = 'AGERE_SECRET_KEY'


class SecretError(Exception):
    """Invalid key, undecryptable token or unusable credential mapping. Never carries secret values."""


class SecretBox:
    def __init__(self, key):
        if isinstance(key, str): key = key.strip().encode('ascii', 'ignore')
        if not isinstance(key, (bytes, bytearray)) or not key:
            raise SecretError(f'{ENV_KEY} must be the urlsafe base64 encoding of 32 bytes (see SecretBox.generate_key())')
        try:
            raw = base64.urlsafe_b64decode(bytes(key))
        except (binascii.Error, ValueError):
            raw = b''
        if len(raw) != 32:
            raise SecretError(f'{ENV_KEY} must be the urlsafe base64 encoding of 32 bytes (see SecretBox.generate_key())')
        self._fernet = Fernet(base64.urlsafe_b64encode(raw))

    @staticmethod
    def generate_key():
        return Fernet.generate_key().decode('ascii')

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        if not env.get(ENV_KEY): raise SecretError(f'{ENV_KEY} is not set')
        return cls(env[ENV_KEY])

    def __repr__(self): return 'SecretBox(<key hidden>)'

    def encrypt(self, mapping):
        if not isinstance(mapping, Mapping): raise SecretError('Credentials must be a mapping')
        bad = [k for k in mapping if not isinstance(k, str)]
        if bad: raise SecretError('Credential keys must be strings')
        try:
            data = json.dumps(dict(mapping), sort_keys=True, separators=(',', ':')).encode('utf-8')
        except (TypeError, ValueError):
            raise SecretError(f'Credential values must be JSON-serializable (keys: {sorted(mapping)})') from None
        return self._fernet.encrypt(data).decode('ascii')

    def decrypt(self, token):
        if isinstance(token, str): token = token.encode('ascii', 'ignore')
        try:
            data = self._fernet.decrypt(token)
        except (InvalidToken, TypeError, ValueError):
            raise SecretError('Credential could not be decrypted (wrong AGERE_SECRET_KEY or corrupted ciphertext)') from None
        try:
            out = json.loads(data.decode('utf-8'))
        except ValueError:
            raise SecretError('Decrypted credential is not valid JSON') from None
        if not isinstance(out, dict): raise SecretError('Decrypted credential is not a mapping')
        return out


def _now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _label(value, what):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f'{what} must be a non-empty printable string')
    return value


class CredentialStore:
    """credentials(org, name, ciphertext, kind, created_at, updated_at), primary key (org, name).
    Thread-safe: one SQLite connection per thread, WAL journal, busy_timeout."""
    BUSY_MS = 5000

    def __init__(self, db_path, box):
        if not isinstance(box, SecretBox): raise TypeError('box must be a SecretBox')
        self.db_path, self.box = str(db_path), box
        if self.db_path != ':memory:': Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        db = self._db()
        db.execute('''CREATE TABLE IF NOT EXISTS credentials (
            org TEXT NOT NULL, name TEXT NOT NULL, ciphertext TEXT NOT NULL, kind TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (org, name))''')

    def _db(self):
        db = getattr(self._local, 'db', None)
        if db is None:
            db = sqlite3.connect(self.db_path, timeout=self.BUSY_MS / 1000, isolation_level=None, check_same_thread=True)
            db.execute(f'PRAGMA busy_timeout = {int(self.BUSY_MS)}')
            if self.db_path != ':memory:': db.execute('PRAGMA journal_mode = WAL')
            self._local.db = db
        return db

    def close(self):
        db = getattr(self._local, 'db', None)
        if db is not None: db.close(); self._local.db = None

    # The sealed envelope binds the mapping to its (org, name) slot.
    @staticmethod
    def _seal(box, org, name, mapping):
        if not isinstance(mapping, Mapping): raise SecretError('Credentials must be a mapping')
        bad = [k for k in mapping if not isinstance(k, str)]
        if bad: raise SecretError('Credential keys must be strings')
        return box.encrypt({'org': org, 'name': name, 'values': dict(mapping)})

    @staticmethod
    def _open(box, org, name, ciphertext):
        env = box.decrypt(ciphertext)
        if env.get('org') != org or env.get('name') != name or not isinstance(env.get('values'), dict):
            raise SecretError(f'Credential {name!r} for org {org!r} does not belong to this slot')
        return env['values']

    def put(self, org, name, mapping, kind=None):
        _label(org, 'org'); _label(name, 'name')
        if kind is not None: _label(kind, 'kind')
        ct, now = self._seal(self.box, org, name, mapping), _now()
        self._db().execute(
            '''INSERT INTO credentials (org, name, ciphertext, kind, created_at, updated_at) VALUES (?,?,?,?,?,?)
               ON CONFLICT(org, name) DO UPDATE SET ciphertext=excluded.ciphertext, kind=excluded.kind,
               updated_at=excluded.updated_at''', (org, name, ct, kind, now, now))

    def get(self, org, name):
        row = self._db().execute('SELECT ciphertext FROM credentials WHERE org=? AND name=?', (org, name)).fetchone()
        if row is None: raise KeyError(f'No credential {name!r} for org {org!r}')
        return self._open(self.box, org, name, row[0])

    def names(self, org):
        return [{'name': n, 'kind': k, 'updated_at': u} for n, k, u in self._db().execute(
            'SELECT name, kind, updated_at FROM credentials WHERE org=? ORDER BY name', (org,))]

    def delete(self, org, name):
        cur = self._db().execute('DELETE FROM credentials WHERE org=? AND name=?', (org, name))
        return cur.rowcount > 0

    def rotate(self, old_box, new_box):
        """Re-encrypt every row from old_box to new_box in one transaction; afterwards this store uses new_box.
        Rows already readable with new_box are left as they are (a resumed rotation). Any row readable by neither
        key aborts the whole rotation with nothing changed. Returns the number of rows re-encrypted."""
        if not isinstance(old_box, SecretBox) or not isinstance(new_box, SecretBox):
            raise TypeError('old_box and new_box must be SecretBox instances')
        db, changed = self._db(), 0
        with self._lock:
            db.execute('BEGIN IMMEDIATE')
            try:
                for org, name, ct in db.execute('SELECT org, name, ciphertext FROM credentials').fetchall():
                    try:
                        values = self._open(old_box, org, name, ct)
                    except SecretError:
                        self._open(new_box, org, name, ct)  # raises if readable by neither key
                        continue
                    db.execute('UPDATE credentials SET ciphertext=?, updated_at=? WHERE org=? AND name=?',
                               (self._seal(new_box, org, name, values), _now(), org, name))
                    changed += 1
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise
            self.box = new_box
        return changed
