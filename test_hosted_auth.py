"""Tests for clinical_core.agere.hosted.auth (python -m unittest test_hosted_auth)."""
import hashlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from clinical_core.agere.hosted import auth as A
from clinical_core.agere.hosted.auth import AuthError, AuthStore

PW = "correct horse battery"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "hosted.sqlite3")
        self.clock = Clock()
        self.s = AuthStore(self.db, clock=self.clock)
        self.org = self.s.create_org("Acme Health")
        self.user = self.s.create_user("  Alice@Example.COM ", PW, name="Alice")
        self.s.add_member(self.org, self.user, "admin")

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def raw(self, sql, args=()):
        c = sqlite3.connect(self.db)
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()


class PasswordTests(Base):
    def test_policy_min_length(self):
        with self.assertRaises(AuthError):
            self.s.create_user("bob@example.com", "short-pass1")  # 11 chars
        self.s.create_user("bob@example.com", "x" * 12)

    def test_email_normalised_and_unique(self):
        self.assertEqual(self.user["email"], "alice@example.com")
        with self.assertRaises(AuthError):
            self.s.create_user("ALICE@example.com ", PW)

    def test_no_plaintext_and_scrypt_params(self):
        for p in (self.db, self.db + "-wal"):
            if os.path.exists(p):
                with open(p, "rb") as fh:
                    self.assertNotIn(PW.encode(), fh.read())
        (salt, h, params), = self.raw("SELECT pw_salt, pw_hash, pw_params FROM users WHERE email='alice@example.com'")
        self.assertEqual(len(salt), 16)
        expect = hashlib.scrypt(PW.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
        self.assertEqual(h, expect)
        self.assertIn('"n": 16384', params)

    def test_salt_per_user(self):
        self.s.create_user("bob@example.com", PW)
        salts = {r[0] for r in self.raw("SELECT pw_salt FROM users")}
        hashes = {r[0] for r in self.raw("SELECT pw_hash FROM users")}
        self.assertEqual(len(salts), 2)
        self.assertEqual(len(hashes), 2)

    def test_verify(self):
        self.assertEqual(self.s.verify_password("ALICE@example.com", PW)["id"], self.user["id"])
        self.assertIsNone(self.s.verify_password("alice@example.com", PW + "x"))
        self.assertIsNone(self.s.verify_password("nobody@example.com", PW))

    def test_compare_digest_used(self):
        with mock.patch.object(A.hmac, "compare_digest", wraps=A.hmac.compare_digest) as cd:
            self.s.verify_password("alice@example.com", "wrong password!!")
            self.assertTrue(cd.called)
            cd.reset_mock()
            self.s.verify_password("ghost@example.com", "wrong password!!")
            self.assertTrue(cd.called)

    def test_set_password(self):
        self.s.set_password(self.user, "a brand new password")
        self.assertIsNone(self.s.verify_password("alice@example.com", PW))
        self.assertIsNotNone(self.s.verify_password("alice@example.com", "a brand new password"))
        with self.assertRaises(AuthError):
            self.s.set_password(self.user, "short")

    def test_set_password_sessions(self):
        tok, _ = self.s.create_session(self.user, self.org)
        self.s.set_password(self.user, "a brand new password")  # in-session change keeps the session
        self.assertIsNotNone(self.s.session(tok))
        self.s.set_password(self.user, "another new password", revoke_sessions=True)
        self.assertIsNone(self.s.session(tok))

    def test_disabled_cannot_login(self):
        self.s.disable_user(self.user)
        self.assertIsNone(self.s.verify_password("alice@example.com", PW))


class SessionTests(Base):
    def test_lifecycle(self):
        tok, csrf = self.s.create_session(self.user, self.org, "1.2.3.4", "UA")
        sess = self.s.session(tok)
        self.assertEqual(sess["user"]["id"], self.user["id"])
        self.assertEqual(sess["org"], self.org["slug"])
        self.assertEqual(sess["user"]["email"], "alice@example.com")
        self.assertEqual(sess["role"], "admin")
        self.assertEqual(sess["csrf"], csrf)
        self.assertGreater(sess["expires"], self.clock.t)
        # token stored only as sha256
        hashes = [r[0] for r in self.raw("SELECT token_hash FROM sessions")]
        self.assertEqual(hashes, [hashlib.sha256(tok.encode()).hexdigest()])
        self.assertFalse(self.raw("SELECT 1 FROM sessions WHERE token_hash=?", (tok,)))
        self.assertTrue(self.s.revoke_session(tok))
        self.assertIsNone(self.s.session(tok))
        self.assertIsNone(self.s.session("garbage"))
        self.assertIsNone(self.s.session(""))

    def test_token_shapes(self):
        tok, csrf = self.s.create_session(self.user, self.org)
        self.assertGreaterEqual(len(tok), 43)
        self.assertGreaterEqual(len(csrf), 32)

    def test_csrf_stable(self):
        tok, csrf = self.s.create_session(self.user, self.org)
        self.clock.t += 120
        self.assertEqual(self.s.session(tok)["csrf"], csrf)
        self.clock.t += 3600
        self.assertEqual(self.s.session(tok)["csrf"], csrf)
        self.assertTrue(self.s.check_csrf(tok, csrf))
        self.assertFalse(self.s.check_csrf(tok, "nope"))
        tok2, csrf2 = self.s.create_session(self.user, self.org)
        self.assertNotEqual(csrf, csrf2)

    def test_idle_expiry(self):
        tok, _ = self.s.create_session(self.user, self.org)
        self.clock.t += 12 * 3600 - 10
        self.assertIsNotNone(self.s.session(tok))  # refreshes last_seen
        self.clock.t += 12 * 3600 - 10
        self.assertIsNotNone(self.s.session(tok))
        self.clock.t += 12 * 3600 + 1
        self.assertIsNone(self.s.session(tok))

    def test_absolute_expiry(self):
        tok, _ = self.s.create_session(self.user, self.org)
        for _ in range(17):  # active every 11h for > 7 days (187 h)
            self.clock.t += 11 * 3600
            res = self.s.session(tok)
            if self.clock.t - 1_000_000.0 > 7 * 24 * 3600:
                self.assertIsNone(res)
            else:
                self.assertIsNotNone(res)
        self.assertIsNone(self.s.session(tok))

    def test_last_seen_throttled(self):
        tok, _ = self.s.create_session(self.user, self.org)
        start = self.raw("SELECT last_seen FROM sessions")[0][0]
        self.clock.t += 30
        self.s.session(tok)
        self.assertEqual(self.raw("SELECT last_seen FROM sessions")[0][0], start)
        self.clock.t += 31
        self.s.session(tok)
        self.assertEqual(self.raw("SELECT last_seen FROM sessions")[0][0], self.clock.t)

    def test_non_member_cannot_get_session(self):
        other = self.s.create_org("Other")
        with self.assertRaises(AuthError):
            self.s.create_session(self.user, other)

    def test_removed_member_session_dies(self):
        tok, _ = self.s.create_session(self.user, self.org)
        self.s.remove_member(self.org, self.user)
        self.assertIsNone(self.s.session(tok))


class ApiKeyTests(Base):
    def test_create_lookup_revoke(self):
        key = self.s.create_api_key(self.org, self.user, "ci", "analyst")
        self.assertTrue(key.startswith("agk_"))
        info = self.s.api_key(key)
        self.assertEqual(info["role"], "analyst")
        self.assertEqual(info["org"], self.org["slug"])
        self.assertEqual(info["user"]["id"], self.user["id"])
        rows = self.raw("SELECT token_hash, prefix FROM api_keys")
        self.assertEqual(rows[0][0], hashlib.sha256(key.encode()).hexdigest())
        self.assertFalse(self.raw("SELECT 1 FROM api_keys WHERE token_hash=? OR prefix=?", (key, key)))
        self.assertIsNone(self.s.api_key(key[:-1] + ("A" if key[-1] != "A" else "B")))
        self.assertIsNone(self.s.api_key("notakey"))
        listed = self.s.api_keys(self.org["slug"])
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["prefix"], key[:8])
        self.assertEqual(set(listed[0]), {"id", "name", "role", "prefix", "user_id", "created_at",
                                          "last_used_at", "revoked"})
        self.assertNotIn(key, str(listed))
        self.assertNotIn(hashlib.sha256(key.encode()).hexdigest(), str(listed))
        self.assertTrue(self.s.revoke_api_key(self.org["slug"], listed[0]["id"]))
        self.assertIsNone(self.s.api_key(key))
        self.assertTrue(self.s.api_keys(self.org)[0]["revoked"])
        self.assertFalse(self.s.revoke_api_key(self.org["slug"], str(listed[0]["id"])))  # already revoked

    def test_revoke_by_id_respects_org(self):
        key = self.s.create_api_key(self.org, self.user, "ci", "viewer")
        kid = self.s.api_key(key)["key_id"]
        other = self.s.create_org("Other")
        with self.assertRaises(PermissionError):
            self.s.revoke_api_key(other["slug"], kid)
        with self.assertRaises(PermissionError):
            self.s.revoke_api_key(self.org["slug"], 99999)
        with self.assertRaises(PermissionError):
            self.s.revoke_api_key(self.org["slug"], "nonsense")
        self.assertIsNotNone(self.s.api_key(key))
        self.assertTrue(self.s.revoke_api_key(self.org["slug"], kid))

    def test_role_validation(self):
        with self.assertRaises(AuthError):
            self.s.create_api_key(self.org, self.user, "x", "superuser")
        with self.assertRaises(AuthError):
            self.s.create_api_key(self.org, self.user, "x", "owner")  # admin cannot mint owner


class MembershipTests(Base):
    def test_roles(self):
        self.assertEqual(self.s.role(self.user, self.org), "admin")
        self.s.add_member(self.org, self.user, "viewer")
        self.assertEqual(self.s.role(self.user, self.org), "viewer")
        with self.assertRaises(AuthError):
            self.s.add_member(self.org, self.user, "god")
        for r in ("owner", "admin", "analyst", "viewer"):
            self.s.add_member(self.org, self.user, r)
            self.assertEqual(self.s.role("alice@example.com", self.org["slug"]), r)
        ms = self.s.memberships(self.user)
        self.assertEqual([(m["org_slug"], m["org_name"]) for m in ms], [("acme-health", "Acme Health")])

    def test_user_forms_accepted(self):
        rec = self.s.verify_password("alice@example.com", PW)
        sess_user = self.s.session(self.s.create_session(rec, "acme-health")[0])["user"]
        for form in (rec, sess_user, "alice@example.com", "ALICE@example.com", self.user["id"]):
            self.assertEqual(self.s.role(form, "acme-health"), "admin")
            self.assertEqual(self.s.memberships(form)[0]["org_slug"], "acme-health")
            tok, _ = self.s.create_session(form, "acme-health")
            self.assertIsNotNone(self.s.session(tok))
            self.assertTrue(self.s.create_api_key("acme-health", form, "k", "viewer").startswith("agk_"))
        self.s.set_password("alice@example.com", "password via email form")
        self.assertIsNotNone(self.s.verify_password("alice@example.com", "password via email form"))

    def test_members_listing(self):
        bob, pw = self.s.find_or_create_user("Bob@Example.com", "Bob")
        self.s.add_member("acme-health", "bob@example.com", "viewer")
        ms = self.s.members("acme-health")
        self.assertEqual([(m["email"], m["role"]) for m in ms],
                         [("alice@example.com", "admin"), ("bob@example.com", "viewer")])
        self.assertTrue({"email", "name", "role", "disabled", "added_at"} <= set(ms[0]))
        self.assertEqual(ms[1]["name"], "Bob")
        self.assertFalse(ms[1]["disabled"])
        self.s.remove_member("acme-health", "bob@example.com")
        self.assertEqual([m["email"] for m in self.s.members("acme-health")], ["alice@example.com"])
        self.s.add_member("acme-health", bob, "analyst")
        self.s.remove_member("acme-health", bob)
        self.assertIsNone(self.s.role(bob, "acme-health"))

    def test_find_or_create_user(self):
        u, pw = self.s.find_or_create_user("  New@Example.com ", "New")
        self.assertEqual(u["email"], "new@example.com")
        self.assertEqual(len(pw), 20)
        self.assertIsNotNone(self.s.verify_password("new@example.com", pw))
        u2, pw2 = self.s.find_or_create_user("new@example.com")
        self.assertEqual(u2["id"], u["id"])
        self.assertIsNone(pw2)
        self.assertIsNone(self.s.find_or_create_user("alice@example.com")[1])

    def test_cross_org_isolation(self):
        org_b = self.s.create_org("Beta Clinic")
        bob = self.s.create_user("bob@beta.org", PW)
        self.s.add_member(org_b, bob, "owner")
        self.assertIsNone(self.s.role(self.user, org_b))
        self.assertIsNone(self.s.role(bob, self.org))
        ta, _ = self.s.create_session(self.user, self.org)
        tb, _ = self.s.create_session(bob, org_b)
        self.assertEqual(self.s.session(ta)["org"], self.org["slug"])
        self.assertEqual(self.s.session(tb)["org"], org_b["slug"])
        ka = self.s.create_api_key(self.org, self.user, "a", "viewer")
        self.assertEqual(self.s.api_key(ka)["org"], self.org["slug"])
        self.assertEqual(self.s.api_keys(org_b), [])
        self.assertEqual([m["email"] for m in self.s.members(org_b)], ["bob@beta.org"])
        with self.assertRaises(AuthError):
            self.s.create_api_key(org_b, self.user, "sneak", "viewer")
        self.s.record_usage(org_b, "ws", "q1", 1.0, 2.0)
        self.assertEqual(self.s.usage(self.org)["records"], 0)
        self.assertTrue(all(e["org_id"] == self.org["id"] for e in self.s.audit_log(self.org)))
        self.assertTrue(all(e["org_id"] == org_b["id"] for e in self.s.audit_log(org_b)))

    def test_multi_org_user(self):
        org_b = self.s.create_org("Beta Clinic")
        self.s.add_member(org_b, self.user, "viewer")
        ta, _ = self.s.create_session(self.user, self.org)
        tb, _ = self.s.create_session(self.user, org_b)
        self.assertEqual(self.s.session(ta)["role"], "admin")
        self.assertEqual(self.s.session(tb)["role"], "viewer")


class DisableTests(Base):
    def test_disable_kills_sessions_and_keys(self):
        t1, _ = self.s.create_session(self.user, self.org)
        t2, _ = self.s.create_session(self.user, self.org)
        k = self.s.create_api_key(self.org, self.user, "k", "viewer")
        self.s.disable_user(self.user)
        self.assertIsNone(self.s.session(t1))
        self.assertIsNone(self.s.session(t2))
        self.assertIsNone(self.s.api_key(k))
        self.assertEqual(self.raw("SELECT COUNT(*) FROM sessions WHERE revoked_at IS NULL")[0][0], 0)
        self.assertEqual(self.raw("SELECT COUNT(*) FROM api_keys WHERE revoked_at IS NULL")[0][0], 0)
        with self.assertRaises(AuthError):
            self.s.create_session(self.user, self.org)
        # re-enabling does not resurrect old credentials
        self.s.enable_user(self.user)
        self.assertIsNone(self.s.session(t1))
        self.assertIsNone(self.s.api_key(k))


class RateLimitTests(Base):
    def test_allow(self):
        key = "login:alice@example.com:1.2.3.4"
        self.assertTrue(all(self.s.allow(key, 5, 60) for _ in range(5)))
        self.assertFalse(self.s.allow(key, 5, 60))
        self.assertTrue(self.s.allow("login:other", 5, 60))
        self.clock.t += 61
        self.assertTrue(self.s.allow(key, 5, 60))

    def test_allow_across_threads(self):
        results = []
        lock = threading.Lock()

        def worker():
            for _ in range(10):
                r = self.s.allow("shared", 25, 60)
                with lock:
                    results.append(r)

        ts = [threading.Thread(target=worker) for _ in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sum(results), 25)

    def test_shared_across_store_instances(self):
        other = AuthStore(self.db, clock=self.clock)
        try:
            self.assertTrue(self.s.allow("k", 1, 60))
            self.assertFalse(other.allow("k", 1, 60))
        finally:
            other.close()


class AuditTests(Base):
    def actions(self, org):
        return [e["action"] for e in self.s.audit_log(org, 1000)]

    def test_audit_entries(self):
        self.s.verify_password("alice@example.com", PW, ip="9.9.9.9")
        self.s.verify_password("alice@example.com", "wrong wrong wrong", ip="9.9.9.9")
        self.s.set_password(self.user, "another long password")
        plat = self.s.audit_log(None, 100)
        acts = [e["action"] for e in plat]
        for a in ("user.created", "login.ok", "login.failed", "password.set"):
            self.assertIn(a, acts)
        ok = next(e for e in plat if e["action"] == "login.ok")
        self.assertEqual(ok["ip"], "9.9.9.9")
        self.assertEqual(ok["user_id"], self.user["id"])
        self.assertEqual(ok["detail"]["email"], "alice@example.com")
        self.assertNotIn(PW, str(plat))

        bob = self.s.create_user("bob@example.com", PW)
        self.s.add_member(self.org, bob, "analyst", actor=self.user, ip="5.5.5.5")
        key = self.s.create_api_key(self.org, self.user, "ci", "viewer")
        self.s.revoke_api_key(self.org["slug"], key, actor=self.user)
        self.s.remove_member(self.org, bob, actor=self.user)
        self.s.audit(self.org, self.user, "custom.thing", {"x": 1}, ip="1.1.1.1")
        acts = self.actions(self.org)
        for a in ("org.created", "member.added", "api_key.created", "api_key.revoked", "member.removed",
                  "custom.thing"):
            self.assertIn(a, acts)
        added = next(e for e in self.s.audit_log(self.org) if e["action"] == "member.added"
                     and e["detail"]["user_id"] == bob["id"])
        self.assertEqual(added["user_id"], self.user["id"])
        self.assertEqual(added["ip"], "5.5.5.5")
        self.assertEqual(added["detail"]["role"], "analyst")
        self.assertEqual(added["at"], self.clock.t)
        self.assertEqual(self.s.audit_log(self.org, limit=2)[0]["action"], "custom.thing")
        self.assertEqual(len(self.s.audit_log(self.org, limit=2)), 2)
        import json
        json.dumps(self.s.audit_log(self.org["slug"], 200))
        json.dumps(self.s.usage(self.org["slug"]))
        self.s.audit(None, None, "login_failed", {"email": "x@y.z", "ip": "1.2.3.4"})
        self.assertEqual(self.s.audit_log(None, 1)[0]["action"], "login_failed")


class UsageTests(Base):
    def test_aggregation(self):
        org_b = self.s.create_org("Beta")
        self.s.record_usage(self.org, "ws1", "q1", 0.10, 3)
        self.s.record_usage(self.org, "ws1", "q2", 0.25, 4)
        self.clock.t += 100
        cutoff = self.clock.t
        self.s.record_usage(self.org, "ws2", "q3", 1.00, 10)
        self.s.record_usage(org_b, "ws1", "q9", 99.0, 99)
        u = self.s.usage(self.org)
        self.assertEqual(u["records"], 3)
        self.assertAlmostEqual(u["cost_usd"], 1.35)
        self.assertAlmostEqual(u["seconds"], 17)
        self.assertEqual(u["by_workspace"]["ws1"]["records"], 2)
        self.assertAlmostEqual(u["by_workspace"]["ws1"]["cost_usd"], 0.35)
        u2 = self.s.usage(self.org, since=cutoff)
        self.assertEqual(u2["records"], 1)
        self.assertEqual(list(u2["by_workspace"]), ["ws2"])
        self.assertAlmostEqual(self.s.usage(org_b)["cost_usd"], 99.0)


class SlugTests(Base):
    def test_slugs(self):
        self.assertEqual(self.org["slug"], "acme-health")
        o2 = self.s.create_org("ACME  health!!")
        o3 = self.s.create_org("acme_health")
        self.assertEqual(o2["slug"], "acme-health-2")
        self.assertEqual(o3["slug"], "acme-health-3")
        long = self.s.create_org("X" * 100)
        self.assertEqual(len(long["slug"]), 48)
        long2 = self.s.create_org("x" * 100)
        self.assertLessEqual(len(long2["slug"]), 48)
        self.assertNotEqual(long["slug"], long2["slug"])
        weird = self.s.create_org("Ünïcødé & Co. (TX)")
        self.assertRegex(weird["slug"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertEqual(self.s.create_org("!!!")["slug"], "org")
        self.assertEqual(self.s.get_org("acme-health-2")["name"], "ACME  health!!")
        self.assertIsNone(self.s.get_org("nope"))
        slugs = [o["slug"] for o in self.s.list_orgs()]
        self.assertEqual(len(slugs), len(set(slugs)))
        with self.assertRaises(AuthError):
            self.s.create_org("   ")


class ConcurrencyTests(Base):
    def test_threads_creating_sessions(self):
        tokens, errors = [], []
        lock = threading.Lock()

        def worker():
            try:
                for _ in range(15):
                    tok, _ = self.s.create_session(self.user, self.org, "1.1.1.1", "ua")
                    assert self.s.session(tok) is not None
                    with lock:
                        tokens.append(tok)
            except Exception as e:  # pragma: no cover
                with lock:
                    errors.append(e)

        ts = [threading.Thread(target=worker) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(len(set(tokens)), 120)
        self.assertEqual(self.raw("SELECT COUNT(*) FROM sessions")[0][0], 120)
        self.assertEqual(self.raw("PRAGMA journal_mode")[0][0], "wal")


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_function_and_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "h.sqlite3")
            out = io.StringIO()
            r = A.bootstrap(db, "Frontline Care", "Owner@Example.com", "Owner", out=out)
            self.assertEqual(len(r["password"]), 20)
            self.assertIn(r["password"], out.getvalue())
            s = AuthStore(db)
            try:
                self.assertEqual(s.verify_password("owner@example.com", r["password"])["name"], "Owner")
                self.assertEqual(s.role("owner@example.com", "frontline-care"), "owner")
            finally:
                s.close()
            out2 = io.StringIO()
            r2 = A.bootstrap(db, "Second Org", "owner@example.com", out=out2)
            self.assertIsNone(r2["password"])
            self.assertNotIn("one-time password", out2.getvalue())
            s = AuthStore(db)
            try:
                self.assertEqual(len(s.memberships("owner@example.com")), 2)
            finally:
                s.close()

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "h.sqlite3")
            p = subprocess.run([sys.executable, "-m", "clinical_core.agere.hosted.auth", "bootstrap", "--db", db,
                                "--org", "CLI Org", "--email", "cli@example.com", "--name", "Cli"],
                               capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__)))
            self.assertEqual(p.returncode, 0, p.stderr)
            line = next(l for l in p.stdout.splitlines() if l.startswith("one-time password:"))
            pw = line.split(":", 1)[1].strip()
            s = AuthStore(db)
            try:
                self.assertIsNotNone(s.verify_password("cli@example.com", pw))
                self.assertEqual(s.role("cli@example.com", "cli-org"), "owner")
            finally:
                s.close()


if __name__ == "__main__":
    unittest.main()
