"""Authentication and tenancy store for hosted Agere (docs/agere/HOSTED_DESIGN.md).

Stdlib only. One SQLite database (WAL mode) holds organizations, users, memberships,
sessions, API keys, audit entries, usage records and rate-limit events.

Security properties:
- passwords: scrypt (n=2**14, r=8, p=1, 16-byte per-user salt), verified with hmac.compare_digest;
  minimum length 12; plaintext is never stored.
- session and API-key tokens: secrets.token_urlsafe(32); only their sha256 hex digest is stored.
- sessions: idle timeout 12 h, absolute timeout 7 days, last_seen written at most once a minute,
  one CSRF token per session (secrets.token_urlsafe(24)).
- disable_user revokes every session and API key of that user; disabled users cannot log in.
- AuthStore is safe to share across threads (one connection per thread, busy_timeout 5000).

Objects are plain dicts. Methods that take an ``org`` accept an org dict, an org id (int) or a slug (str);
methods that take a ``user`` accept a user dict, a user id (int) or an email (str).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import sys
import threading
from pathlib import Path
import time
from typing import Any, Callable, Optional

ROLES = ("owner", "admin", "analyst", "viewer")
ROLE_RANK = {"viewer": 0, "analyst": 1, "admin": 2, "owner": 3}

MIN_PASSWORD_LENGTH = 12
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16

SESSION_IDLE_S = 12 * 3600
SESSION_ABSOLUTE_S = 7 * 24 * 3600
LAST_SEEN_RESOLUTION_S = 60

API_KEY_PREFIX = "agk_"
SLUG_MAX = 48

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orgs (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT,
    pw_salt BLOB NOT NULL,
    pw_hash BLOB NOT NULL,
    pw_params TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    password_set_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
    org_id INTEGER NOT NULL REFERENCES orgs(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    role TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (org_id, user_id)
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    org_id INTEGER NOT NULL REFERENCES orgs(id),
    csrf TEXT NOT NULL,
    ip TEXT,
    ua TEXT,
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    revoked_at REAL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,
    org_id INTEGER NOT NULL REFERENCES orgs(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_used REAL,
    revoked_at REAL
);
CREATE INDEX IF NOT EXISTS api_keys_user ON api_keys(user_id);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    org_id INTEGER,
    user_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    ip TEXT,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_org ON audit(org_id, at);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL,
    workspace TEXT,
    question_id TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_org ON usage(org_id, at);
CREATE TABLE IF NOT EXISTS rate_events (
    key TEXT NOT NULL,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_events_key ON rate_events(key, at);
"""



def question_ref(text: Optional[str]) -> dict:
    """What the audit and usage logs keep of a question: a keyed fingerprint and its length, never the words (they can
    name a patient). The same question gives the same fingerprint, so repeats can be counted without storing them."""
    from ..tiered.ledger import fingerprint
    return {'question_fingerprint': fingerprint(text), 'question_chars': len(' '.join(str(text or '').split()))}

class AuthError(ValueError):
    """Raised for invalid input (weak password, unknown role, duplicate email, unknown org/user)."""


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:SLUG_MAX].strip("-")
    return s or "org"


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _scrypt(password: str, salt: bytes, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P,
            dklen: int = SCRYPT_DKLEN) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=dklen)


def check_password_policy(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def generate_password(length: int = 20) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


_DUMMY_SALT = b"\x00" * SALT_BYTES
_DUMMY_HASH = _scrypt("agere-dummy-password", _DUMMY_SALT)


class AuthStore:
    def __init__(self, path: str, clock: Callable[[], float] = time.time):
        self.path = str(path)
        if self.path != ':memory:':
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)  # a fresh deployment has no data directory yet
        self.clock = clock
        self._local = threading.local()
        self._all_conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        conn = self._conn()
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        # Older rows stored unknown costs as zero. Keep those rows intact and
        # mark their zero amounts as ambiguous instead of declaring them free.
        with self._tx() as c:
            if 'cost_known' not in {r['name'] for r in c.execute('PRAGMA table_info(usage)')}:
                c.execute('ALTER TABLE usage ADD COLUMN cost_known INTEGER')

    # ---------------------------------------------------------------- plumbing
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
            with self._conns_lock:
                self._all_conns.append(conn)
        return conn

    def _tx(self):
        store = self

        class _Tx:
            def __enter__(self_inner):
                self_inner.c = store._conn()
                self_inner.c.execute("BEGIN IMMEDIATE")
                return self_inner.c

            def __exit__(self_inner, exc_type, exc, tb):
                if exc_type is None:
                    self_inner.c.execute("COMMIT")
                else:
                    self_inner.c.execute("ROLLBACK")
                return False

        return _Tx()

    def close(self) -> None:
        with self._conns_lock:
            for c in self._all_conns:
                try:
                    c.close()
                except Exception:
                    pass
            self._all_conns.clear()
        self._local = threading.local()

    def _now(self) -> float:
        return float(self.clock())

    @staticmethod
    def _org_dict(row) -> dict:
        return {"id": row["id"], "slug": row["slug"], "name": row["name"], "created_at": row["created_at"]}

    @staticmethod
    def _user_dict(row) -> dict:
        return {"id": row["id"], "email": row["email"], "name": row["name"],
                "disabled": bool(row["disabled"]), "created_at": row["created_at"]}

    def _org_id(self, org: Any, required: bool = True) -> Optional[int]:
        if org is None:
            if required:
                raise AuthError("org required")
            return None
        if isinstance(org, dict):
            return int(org["id"])
        if isinstance(org, int):
            return org
        row = self._conn().execute("SELECT id FROM orgs WHERE slug=?", (str(org),)).fetchone()
        if row is None:
            if required:
                raise AuthError(f"unknown org {org!r}")
            return None
        return row["id"]

    def _user_id(self, user: Any, required: bool = True) -> Optional[int]:
        if user is None:
            if required:
                raise AuthError("user required")
            return None
        if isinstance(user, dict):
            return int(user["id"])
        if isinstance(user, int):
            return user
        row = self._conn().execute("SELECT id FROM users WHERE email=?", (normalize_email(str(user)),)).fetchone()
        if row is None:
            if required:
                raise AuthError(f"unknown user {user!r}")
            return None
        return row["id"]

    # ---------------------------------------------------------------- orgs
    def create_org(self, name: str, actor: Any = None) -> dict:
        name = (name or "").strip()
        if not name:
            raise AuthError("org name required")
        base = slugify(name)
        now = self._now()
        with self._tx() as c:
            slug, i = base, 1
            while c.execute("SELECT 1 FROM orgs WHERE slug=?", (slug,)).fetchone():
                i += 1
                suffix = f"-{i}"
                slug = base[: SLUG_MAX - len(suffix)].rstrip("-") + suffix
            cur = c.execute("INSERT INTO orgs(slug, name, created_at) VALUES (?,?,?)", (slug, name, now))
            org = {"id": cur.lastrowid, "slug": slug, "name": name, "created_at": now}
            self._audit_in(c, org["id"], self._user_id(actor, required=False), "org.created",
                           {"slug": slug, "name": name}, None)
        return org

    def get_org(self, slug: str) -> Optional[dict]:
        row = self._conn().execute("SELECT * FROM orgs WHERE slug=?", (slug,)).fetchone()
        return self._org_dict(row) if row else None

    def get_org_by_id(self, org_id: int) -> Optional[dict]:
        row = self._conn().execute("SELECT * FROM orgs WHERE id=?", (org_id,)).fetchone()
        return self._org_dict(row) if row else None

    def list_orgs(self) -> list[dict]:
        return [self._org_dict(r) for r in self._conn().execute("SELECT * FROM orgs ORDER BY id")]

    # ---------------------------------------------------------------- users
    def create_user(self, email: str, password: str, name: Optional[str] = None, actor: Any = None,
                    ip: Optional[str] = None) -> dict:
        email = normalize_email(email)
        if not email or "@" not in email:
            raise AuthError("valid email required")
        check_password_policy(password)
        salt = secrets.token_bytes(SALT_BYTES)
        digest = _scrypt(password, salt)
        params = json.dumps({"alg": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "dklen": SCRYPT_DKLEN})
        now = self._now()
        try:
            with self._tx() as c:
                cur = c.execute(
                    "INSERT INTO users(email, name, pw_salt, pw_hash, pw_params, disabled, created_at, password_set_at)"
                    " VALUES (?,?,?,?,?,0,?,?)", (email, name, salt, digest, params, now, now))
                uid = cur.lastrowid
                self._audit_in(c, None, self._user_id(actor, required=False) or uid, "user.created",
                               {"user_id": uid, "email": email}, ip)
        except sqlite3.IntegrityError:
            raise AuthError("email already registered") from None
        return {"id": uid, "email": email, "name": name, "disabled": False, "created_at": now}

    def get_user(self, user: Any) -> Optional[dict]:
        uid = self._user_id(user, required=False)
        if uid is None:
            return None
        row = self._conn().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return self._user_dict(row) if row else None

    def find_or_create_user(self, email: str, name: Optional[str] = None, actor: Any = None,
                            ip: Optional[str] = None) -> tuple[dict, Optional[str]]:
        """(user, temporary_password) for a new user (20 random chars, returned once); (user, None) if it exists."""
        existing = self.get_user(normalize_email(email))
        if existing is not None:
            return existing, None
        password = generate_password(20)
        try:
            return self.create_user(email, password, name=name, actor=actor, ip=ip), password
        except AuthError:
            existing = self.get_user(normalize_email(email))  # lost a race with another creator
            if existing is None:
                raise
            return existing, None

    def _check(self, row, password: str) -> bool:
        params = json.loads(row["pw_params"])
        candidate = _scrypt(password, row["pw_salt"], params["n"], params["r"], params["p"], params["dklen"])
        return hmac.compare_digest(candidate, row["pw_hash"])

    def verify_password(self, email: str, password: str, ip: Optional[str] = None) -> Optional[dict]:
        """Return the user dict on success, None otherwise. Audits login.ok / login.failed."""
        email = normalize_email(email)
        row = self._conn().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if row is None:
            # equalise timing with a real verification
            hmac.compare_digest(_scrypt(password or "", _DUMMY_SALT), _DUMMY_HASH)
            self.audit(None, None, "login.failed", {"email": email, "reason": "unknown"}, ip=ip)
            return None
        ok = self._check(row, password or "")
        if not ok:
            self.audit(None, row["id"], "login.failed", {"email": email, "reason": "password"}, ip=ip)
            return None
        if row["disabled"]:
            self.audit(None, row["id"], "login.failed", {"email": email, "reason": "disabled"}, ip=ip)
            return None
        self.audit(None, row["id"], "login.ok", {"email": email}, ip=ip)
        return self._user_dict(row)

    def set_password(self, user: Any, password: str, actor: Any = None, ip: Optional[str] = None,
                     revoke_sessions: bool = False) -> None:
        check_password_policy(password)
        uid = self._user_id(user)
        salt = secrets.token_bytes(SALT_BYTES)
        digest = _scrypt(password, salt)
        now = self._now()
        with self._tx() as c:
            n = c.execute("UPDATE users SET pw_salt=?, pw_hash=?, password_set_at=? WHERE id=?",
                          (salt, digest, now, uid)).rowcount
            if not n:
                raise AuthError("unknown user")
            if revoke_sessions:
                c.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now, uid))
            self._audit_in(c, None, self._user_id(actor, required=False) or uid, "password.set",
                           {"user_id": uid}, ip)

    def disable_user(self, user: Any, actor: Any = None, ip: Optional[str] = None) -> None:
        uid = self._user_id(user)
        now = self._now()
        with self._tx() as c:
            if not c.execute("UPDATE users SET disabled=1 WHERE id=?", (uid,)).rowcount:
                raise AuthError("unknown user")
            s = c.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now, uid)).rowcount
            k = c.execute("UPDATE api_keys SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now, uid)).rowcount
            self._audit_in(c, None, self._user_id(actor, required=False), "user.disabled",
                           {"user_id": uid, "sessions_revoked": s, "keys_revoked": k}, ip)

    def enable_user(self, user: Any, actor: Any = None, ip: Optional[str] = None) -> None:
        uid = self._user_id(user)
        with self._tx() as c:
            c.execute("UPDATE users SET disabled=0 WHERE id=?", (uid,))
            self._audit_in(c, None, self._user_id(actor, required=False), "user.enabled", {"user_id": uid}, ip)

    # ---------------------------------------------------------------- memberships
    def _guard_change(self, c: sqlite3.Connection, oid: int, uid: int, actor_role: Optional[str], new_role: Optional[str]) -> None:
        """An admin may not change or remove a member at or above their own rank, and an organization keeps at least one
        owner (buyer review: an admin could demote the owner). actor_role None = a platform operation (no check)."""
        rank = ROLE_RANK
        row = c.execute("SELECT role FROM memberships WHERE org_id=? AND user_id=?", (oid, uid)).fetchone()
        current = row[0] if row else None
        if actor_role is not None and current is not None and actor_role != 'owner' and rank[current] >= rank[actor_role]:
            raise AuthError("you cannot change or remove a member at or above your own role")
        if actor_role is not None and current == 'owner' and new_role != 'owner':
            owners = c.execute("SELECT COUNT(*) FROM memberships WHERE org_id=? AND role='owner'", (oid,)).fetchone()[0]
            if owners <= 1:
                raise AuthError("an organization must keep at least one owner")

    def add_member(self, org: Any, user: Any, role: str, actor: Any = None, ip: Optional[str] = None, actor_role: Optional[str] = None) -> None:
        if role not in ROLES:
            raise AuthError(f"role must be one of {ROLES}")
        oid, uid = self._org_id(org), self._user_id(user)
        now = self._now()
        with self._tx() as c:
            self._guard_change(c, oid, uid, actor_role, role)
            c.execute("INSERT INTO memberships(org_id, user_id, role, created_at) VALUES (?,?,?,?)"
                      " ON CONFLICT(org_id, user_id) DO UPDATE SET role=excluded.role", (oid, uid, role, now))
            self._audit_in(c, oid, self._user_id(actor, required=False), "member.added",
                           {"user_id": uid, "role": role}, ip)

    def remove_member(self, org: Any, user: Any, actor: Any = None, ip: Optional[str] = None, actor_role: Optional[str] = None) -> bool:
        oid, uid = self._org_id(org), self._user_id(user)
        now = self._now()
        with self._tx() as c:
            self._guard_change(c, oid, uid, actor_role, None)
            n = c.execute("DELETE FROM memberships WHERE org_id=? AND user_id=?", (oid, uid)).rowcount
            c.execute("UPDATE sessions SET revoked_at=? WHERE org_id=? AND user_id=? AND revoked_at IS NULL",
                      (now, oid, uid))
            c.execute("UPDATE api_keys SET revoked_at=? WHERE org_id=? AND user_id=? AND revoked_at IS NULL",
                      (now, oid, uid))
            self._audit_in(c, oid, self._user_id(actor, required=False), "member.removed",
                           {"user_id": uid, "existed": bool(n)}, ip)
        return bool(n)

    def memberships(self, user: Any) -> list[dict]:
        uid = self._user_id(user)
        rows = self._conn().execute(
            "SELECT o.*, m.role FROM memberships m JOIN orgs o ON o.id=m.org_id WHERE m.user_id=? ORDER BY o.id",
            (uid,)).fetchall()
        return [{"org_slug": r["slug"], "org_name": r["name"], "org_id": r["id"], "role": r["role"]} for r in rows]

    def members(self, org: Any) -> list[dict]:
        oid = self._org_id(org)
        rows = self._conn().execute(
            "SELECT u.id, u.email, u.name, u.disabled, m.role, m.created_at AS added_at FROM memberships m"
            " JOIN users u ON u.id=m.user_id WHERE m.org_id=? ORDER BY u.id", (oid,)).fetchall()
        return [{"user_id": r["id"], "email": r["email"], "name": r["name"], "role": r["role"],
                 "disabled": bool(r["disabled"]), "added_at": r["added_at"]} for r in rows]

    def role(self, user: Any, org: Any) -> Optional[str]:
        uid, oid = self._user_id(user, required=False), self._org_id(org, required=False)
        if uid is None or oid is None:
            return None
        row = self._conn().execute("SELECT role FROM memberships WHERE org_id=? AND user_id=?", (oid, uid)).fetchone()
        return row["role"] if row else None

    # ---------------------------------------------------------------- sessions
    def create_session(self, user: Any, org: Any, ip: Optional[str] = None, ua: Optional[str] = None) -> tuple[str, str]:
        uid, oid = self._user_id(user), self._org_id(org)
        u = self.get_user(uid)
        if u is None or u["disabled"]:
            raise AuthError("user disabled or unknown")
        if self.role(uid, oid) is None:
            raise AuthError("user is not a member of this org")
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        now = self._now()
        with self._tx() as c:
            c.execute("INSERT INTO sessions(token_hash, user_id, org_id, csrf, ip, ua, created_at, last_seen)"
                      " VALUES (?,?,?,?,?,?,?,?)", (_sha256(token), uid, oid, csrf, ip, (ua or "")[:512], now, now))
        return token, csrf

    def session(self, token: str) -> Optional[dict]:
        """Resolve a session token -> {user, org (slug), org_id, org_name, role, csrf, expires} or None."""
        if not token or not isinstance(token, str):
            return None
        c = self._conn()
        row = c.execute(
            "SELECT s.id AS sid, s.csrf, s.created_at AS s_created, s.last_seen, s.revoked_at, s.org_id, s.user_id,"
            " u.disabled FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
            (_sha256(token),)).fetchone()
        if row is None or row["revoked_at"] is not None or row["disabled"]:
            return None
        now = self._now()
        if now - row["last_seen"] > SESSION_IDLE_S or now - row["s_created"] > SESSION_ABSOLUTE_S:
            return None
        role = self.role(row["user_id"], row["org_id"])
        if role is None:
            return None
        last_seen = row["last_seen"]
        if now - last_seen >= LAST_SEEN_RESOLUTION_S:
            c.execute("UPDATE sessions SET last_seen=? WHERE id=?", (now, row["sid"]))
            last_seen = now
        expires = min(last_seen + SESSION_IDLE_S, row["s_created"] + SESSION_ABSOLUTE_S)
        org = self.get_org_by_id(row["org_id"])
        return {"user": self.get_user(row["user_id"]), "org": org["slug"], "org_id": org["id"],
                "org_name": org["name"], "role": role, "csrf": row["csrf"], "expires": expires,
                "session_id": row["sid"]}

    def revoke_session(self, token: str) -> bool:
        if not token:
            return False
        with self._tx() as c:
            return bool(c.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                                  (self._now(), _sha256(token))).rowcount)

    def check_csrf(self, token: str, csrf: Optional[str]) -> bool:
        s = self.session(token)
        return bool(s and csrf and hmac.compare_digest(s["csrf"].encode(), str(csrf).encode()))

    # ---------------------------------------------------------------- API keys
    def create_api_key(self, org: Any, user: Any, name: str, role: str, ip: Optional[str] = None) -> str:
        if role not in ROLES:
            raise AuthError(f"role must be one of {ROLES}")
        oid, uid = self._org_id(org), self._user_id(user)
        member_role = self.role(uid, oid)
        if member_role is None:
            raise AuthError("user is not a member of this org")
        if ROLE_RANK[role] > ROLE_RANK[member_role]:
            raise AuthError("API key role cannot exceed the creator's role")
        token = API_KEY_PREFIX + secrets.token_urlsafe(32)
        now = self._now()
        with self._tx() as c:
            cur = c.execute("INSERT INTO api_keys(token_hash, prefix, org_id, user_id, name, role, created_at)"
                            " VALUES (?,?,?,?,?,?,?)", (_sha256(token), token[:8], oid, uid, name, role, now))
            self._audit_in(c, oid, uid, "api_key.created", {"key_id": cur.lastrowid, "name": name, "role": role}, ip)
        return token

    def api_key(self, token: str) -> Optional[dict]:
        """Resolve a bearer key -> {key_id, name, user, org (slug), org_id, role} or None."""
        if not token or not isinstance(token, str) or not token.startswith(API_KEY_PREFIX):
            return None
        c = self._conn()
        row = c.execute("SELECT k.*, u.disabled FROM api_keys k JOIN users u ON u.id=k.user_id WHERE k.token_hash=?",
                        (_sha256(token),)).fetchone()
        if row is None or row["revoked_at"] is not None or row["disabled"]:
            return None
        member_role = self.role(row["user_id"], row["org_id"])
        if member_role is None:
            return None
        role = row["role"] if ROLE_RANK[row["role"]] <= ROLE_RANK[member_role] else member_role
        now = self._now()
        if row["last_used"] is None or now - row["last_used"] >= LAST_SEEN_RESOLUTION_S:
            c.execute("UPDATE api_keys SET last_used=? WHERE id=?", (now, row["id"]))
        org = self.get_org_by_id(row["org_id"])
        return {"key_id": row["id"], "name": row["name"], "user": self.get_user(row["user_id"]),
                "org": org["slug"], "org_id": org["id"], "role": role}

    def api_keys(self, org: Any) -> list[dict]:
        """Keys of one org: metadata only (never the token or its hash)."""
        oid = self._org_id(org)
        rows = self._conn().execute("SELECT id, prefix, name, role, user_id, created_at, last_used, revoked_at"
                                    " FROM api_keys WHERE org_id=? ORDER BY id", (oid,)).fetchall()
        return [{"id": r["id"], "name": r["name"], "role": r["role"], "prefix": r["prefix"][:8],
                 "user_id": r["user_id"], "created_at": r["created_at"], "last_used_at": r["last_used"],
                 "revoked": r["revoked_at"] is not None} for r in rows]

    def revoke_api_key(self, org: Any, key_id: Any, actor: Any = None, ip: Optional[str] = None) -> bool:
        """Revoke a key of ``org`` by id (or plaintext token). A key outside that org raises PermissionError.
        Returns True if it was revoked now, False if it was already revoked."""
        oid = self._org_id(org)
        if isinstance(key_id, str) and key_id.startswith(API_KEY_PREFIX):
            where, arg = "token_hash=?", _sha256(key_id)
        else:
            try:
                where, arg = "id=?", int(key_id)
            except (TypeError, ValueError):
                raise PermissionError("unknown API key") from None
        with self._tx() as c:
            row = c.execute(f"SELECT id, org_id FROM api_keys WHERE {where}", (arg,)).fetchone()
            if row is None or row["org_id"] != oid:
                raise PermissionError("API key does not belong to this organization")
            n = c.execute("UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                          (self._now(), row["id"])).rowcount
            if n:
                self._audit_in(c, oid, self._user_id(actor, required=False), "api_key.revoked",
                               {"key_id": row["id"]}, ip)
        return bool(n)

    # ---------------------------------------------------------------- audit
    def _audit_in(self, c: sqlite3.Connection, org_id: Optional[int], user_id: Optional[int], action: str,
                  detail: Any, ip: Optional[str]) -> None:
        c.execute("INSERT INTO audit(org_id, user_id, action, detail, ip, at) VALUES (?,?,?,?,?,?)",
                  (org_id, user_id, action, json.dumps(detail or {}, sort_keys=True, default=str), ip, self._now()))

    def audit(self, org: Any, user: Any, action: str, detail: Any = None, ip: Optional[str] = None) -> None:
        oid = self._org_id(org, required=False)
        uid = self._user_id(user, required=False)
        with self._tx() as c:
            self._audit_in(c, oid, uid, action, detail, ip)

    def audit_log(self, org: Any, limit: int = 100) -> list[dict]:
        """Entries for one org, newest first. Pass org=None for platform-level entries (logins, user creation)."""
        if org is None:
            rows = self._conn().execute("SELECT * FROM audit WHERE org_id IS NULL ORDER BY id DESC LIMIT ?",
                                        (int(limit),)).fetchall()
        else:
            rows = self._conn().execute("SELECT * FROM audit WHERE org_id=? ORDER BY id DESC LIMIT ?",
                                        (self._org_id(org), int(limit))).fetchall()
        return [{"id": r["id"], "org_id": r["org_id"], "user_id": r["user_id"], "action": r["action"],
                 "detail": json.loads(r["detail"]), "ip": r["ip"], "at": r["at"]} for r in rows]

    # ---------------------------------------------------------------- usage
    def record_usage(self, org: Any, workspace: Optional[str], question_id: Optional[str], cost_usd: Optional[float],
                     seconds: float) -> None:
        oid = self._org_id(org)
        with self._tx() as c:
            c.execute("INSERT INTO usage(org_id, workspace, question_id, cost_usd, seconds, at, cost_known) VALUES (?,?,?,?,?,?,?)",
                      (oid, workspace, question_id, float(cost_usd or 0), float(seconds or 0), self._now(), int(cost_usd is not None)))

    def usage(self, org: Any, since: Optional[float] = None) -> dict:
        """Aggregate usage for one org (tracked only; no caps)."""
        oid = self._org_id(org)
        since = float(since) if since is not None else 0.0
        c = self._conn()
        known = '(cost_known=1 OR (cost_known IS NULL AND cost_usd>0))'
        fields = (f'COUNT(*) AS n, COALESCE(SUM(CASE WHEN {known} THEN cost_usd ELSE 0 END),0) AS cost, '
                  f'COALESCE(SUM(CASE WHEN {known} THEN 0 ELSE 1 END),0) AS unknown, '
                  'COALESCE(SUM(seconds),0) AS secs')
        tot = c.execute(f'SELECT {fields} FROM usage WHERE org_id=? AND at>=?', (oid, since)).fetchone()
        by_ws = c.execute(f'SELECT workspace, {fields} FROM usage WHERE org_id=? AND at>=? '
                          'GROUP BY workspace ORDER BY workspace', (oid, since)).fetchall()

        def summary(r):
            return {'records': r['n'], 'cost_usd': None if r['unknown'] else round(r['cost'], 6),
                    'known_cost_usd': round(r['cost'], 6), 'unknown_cost_records': r['unknown'],
                    'seconds': round(r['secs'], 3)}

        return {'org_id': oid, 'since': since, **summary(tot),
                'by_workspace': {r['workspace']: summary(r) for r in by_ws}}

    # ---------------------------------------------------------------- rate limit
    def allow(self, key: str, limit: int, window_s: float) -> bool:
        """Sliding-window limiter: True and record the event if fewer than ``limit`` events in ``window_s``."""
        now = self._now()
        with self._tx() as c:
            c.execute("DELETE FROM rate_events WHERE key=? AND at<=?", (key, now - window_s))
            n = c.execute("SELECT COUNT(*) FROM rate_events WHERE key=?", (key,)).fetchone()[0]
            if n >= limit:
                return False
            c.execute("INSERT INTO rate_events(key, at) VALUES (?,?)", (key, now))
            return True


# -------------------------------------------------------------------- CLI
def bootstrap(db: str, org_name: str, email: str, name: Optional[str] = None, out=sys.stdout) -> dict:
    store = AuthStore(db)
    try:
        org = store.get_org(slugify(org_name))
        if org is None or org["name"] != org_name.strip():
            org = store.create_org(org_name)
        user = store.get_user(normalize_email(email))
        password = None
        if user is None:
            password = generate_password(20)
            user = store.create_user(email, password, name=name)
        store.add_member(org, user, "owner")
        print(f"org: {org['name']} (slug {org['slug']})", file=out)
        print(f"owner: {user['email']}", file=out)
        if password:
            print(f"one-time password: {password}", file=out)
            print("Store it now; it is not shown again.", file=out)
        else:
            print("existing user reused; password unchanged", file=out)
        return {"org": org, "user": user, "password": password}
    finally:
        store.close()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m clinical_core.agere.hosted.auth")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap", help="create an org and an owner user")
    b.add_argument("--db", required=True)
    b.add_argument("--org", required=True)
    b.add_argument("--email", required=True)
    b.add_argument("--name", default=None)
    args = p.parse_args(argv)
    if args.cmd == "bootstrap":
        bootstrap(args.db, args.org, args.email, args.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
