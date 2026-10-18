"""Hosted server end to end on a real socket: sign-in, CSRF, roles, API keys, tenant isolation, admin, rate limits."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from clinical_core.agere.hosted.auth import AuthStore
from clinical_core.agere.hosted.secrets import SecretBox
from clinical_core.agere.hosted.server import make_server
from clinical_core.agere.onboarding import fixtures


class Client:
    def __init__(self, base):
        self.base, self.cookie, self.csrf, self.key = base, None, None, None

    def call(self, method, path, body=None, csrf=True):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header('Content-Type', 'application/json')
        if self.cookie:
            req.add_header('Cookie', self.cookie)
        if self.key:
            req.add_header('Authorization', 'Bearer ' + self.key)
        if csrf and self.csrf and method != 'GET':
            req.add_header('X-CSRF-Token', self.csrf)
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=30) as r:
                return r.status, r.headers, self._json(r.read())
        except urllib.error.HTTPError as e:
            return e.code, e.headers, self._json(e.read())

    @staticmethod
    def _json(raw):
        try:
            return json.loads(raw or b'{}')
        except ValueError:
            return {'_html': raw.decode(errors='replace')[:200]}

    def login(self, email, password):
        code, headers, body = self.call('POST', '/api/auth/login', {'email': email, 'password': password})
        if code == 200:
            self.cookie = headers['Set-Cookie'].split(';')[0]
            self.csrf = body['csrf']
        return code, body


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class HostedServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        data = Path(cls.tmp.name) / 'data'
        cls.server = make_server('127.0.0.1', 0, data, secret_key=SecretBox.generate_key(), cookie_secure=False)
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        auth = AuthStore(data / 'hosted.sqlite3')
        cls.pw = 'correct horse battery staple'
        for org, email in (('Acme Health', 'owner@acme.test'), ('Beta Clinic', 'owner@beta.test')):
            o = auth.create_org(org)
            u = auth.create_user(email, cls.pw)
            auth.add_member(o, u, 'owner')
        v = auth.create_user('viewer@acme.test', cls.pw)
        auth.add_member('acme-health', v, 'viewer')
        a = auth.create_user('analyst@acme.test', cls.pw)
        auth.add_member('acme-health', a, 'analyst')
        cls.source = Path(cls.tmp.name) / 'clarity.sqlite'
        fixtures.clarity_like(cls.source, n_patients=10)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def client(self, email):
        c = Client(self.base)
        code, _ = c.login(email, self.pw)
        self.assertEqual(code, 200)
        return c

    def test_health_and_signed_out_behaviour(self):
        c = Client(self.base)
        self.assertEqual(c.call('GET', '/healthz')[0], 200)
        code, headers, _ = c.call('GET', '/app')
        self.assertEqual((code, headers['Location']), (303, '/login'))
        self.assertEqual(c.call('GET', '/api/workspaces')[0], 401)
        self.assertEqual(c.call('GET', '/login')[0], 200)
        code, headers, _ = c.call('GET', '/healthz')
        self.assertEqual(headers['X-Frame-Options'], 'DENY')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_wrong_password_and_cookie_flags(self):
        c = Client(self.base)
        self.assertEqual(c.login('owner@acme.test', 'not the password!!')[0], 401)
        code, headers, body = c.call('POST', '/api/auth/login', {'email': 'owner@acme.test', 'password': self.pw})
        cookie = headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Lax', cookie)

    def test_csrf_required_for_session_writes(self):
        c = self.client('owner@acme.test')
        code, _, body = c.call('POST', '/api/workspaces', {'name': 'x', 'source': 'sqlite:///nope'}, csrf=False)
        self.assertEqual(code, 403)
        self.assertIn('CSRF', body['error'])

    def test_tenant_isolation_and_roles(self):
        acme = self.client('owner@acme.test')
        code, _, body = acme.call('POST', '/api/workspaces', {'name': 'Acme Warehouse', 'source': f'sqlite:///{self.source}'})
        self.assertEqual(code, 202)
        for _ in range(200):
            code, _, d = acme.call('GET', '/api/workspaces/acme-warehouse')
            if not d.get('building') and d.get('status') in ('ready', 'failed'):
                break
            time.sleep(0.05)
        self.assertEqual(d['status'], 'ready')
        beta = self.client('owner@beta.test')
        self.assertEqual(beta.call('GET', '/api/workspaces')[2]['workspaces'], [])
        self.assertEqual(beta.call('GET', '/api/workspaces/acme-warehouse')[0], 404)
        viewer = self.client('viewer@acme.test')
        self.assertEqual(len(viewer.call('GET', '/api/workspaces')[2]['workspaces']), 1)
        self.assertEqual(viewer.call('POST', '/api/workspaces/acme-warehouse/run', {})[0], 403)
        self.assertEqual(viewer.call('GET', '/api/admin/members')[0], 403)
        analyst = self.client('analyst@acme.test')
        self.assertEqual(analyst.call('GET', '/api/admin/keys')[0], 403)
        self.assertEqual(analyst.call('GET', '/api/workspaces/acme-warehouse/review')[0], 200)

    def test_api_keys_scope_and_revoke(self):
        acme = self.client('owner@acme.test')
        code, _, body = acme.call('POST', '/api/admin/keys', {'name': 'etl', 'role': 'viewer'})
        self.assertEqual(code, 200)
        k = Client(self.base)
        k.key = body['key']
        code, _, me = k.call('GET', '/api/auth/me')
        self.assertEqual((code, me['org'], me['role']), (200, 'acme-health', 'viewer'))
        self.assertEqual(k.call('POST', '/api/workspaces', {'name': 'y', 'source': 'sqlite:///nope'})[0], 403)
        keys = acme.call('GET', '/api/admin/keys')[2]['keys']
        self.assertNotIn(body['key'], json.dumps(keys))
        kid = next(x['id'] for x in keys if x['name'] == 'etl')
        self.assertEqual(acme.call('POST', '/api/admin/keys/revoke', {'id': kid})[0], 200)
        self.assertEqual(k.call('GET', '/api/auth/me')[0], 401)
        beta = self.client('owner@beta.test')
        self.assertIn(beta.call('POST', '/api/admin/keys/revoke', {'id': kid})[0], (400, 403))

    def test_credentials_hidden_and_isolated(self):
        acme = self.client('owner@acme.test')
        code, _, _ = acme.call('POST', '/api/admin/credentials',
                               {'name': 'acme-sf', 'kind': 'snowflake', 'values': {'SNOWFLAKE_PASSWORD': 'hunter2-secret'}})
        self.assertEqual(code, 200)
        listing = acme.call('GET', '/api/admin/credentials')[2]
        self.assertEqual([c['name'] for c in listing['credentials']], ['acme-sf'])
        self.assertNotIn('hunter2-secret', json.dumps(listing))
        audit = acme.call('GET', '/api/admin/audit')[2]
        self.assertNotIn('hunter2-secret', json.dumps(audit))
        beta = self.client('owner@beta.test')
        self.assertEqual(beta.call('GET', '/api/admin/credentials')[2]['credentials'], [])

    def test_members_and_logout(self):
        acme = self.client('owner@acme.test')
        code, _, body = acme.call('POST', '/api/admin/members', {'email': 'new@acme.test', 'role': 'analyst'})
        self.assertEqual(code, 200)
        self.assertTrue(body['temporary_password'])
        newbie = Client(self.base)
        self.assertEqual(newbie.login('new@acme.test', body['temporary_password'])[0], 200)
        self.assertEqual(newbie.call('POST', '/api/auth/logout', {})[0], 200)
        self.assertEqual(newbie.call('GET', '/api/auth/me')[0], 401)
        analyst = self.client('analyst@acme.test')
        self.assertEqual(analyst.call('POST', '/api/admin/members', {'email': 'x@acme.test', 'role': 'owner'})[0], 403)

    def test_login_rate_limit(self):
        c = Client(self.base)
        codes = [c.login('nobody@beta.test', 'wrong password here')[0] for _ in range(12)]
        self.assertIn(429, codes)


if __name__ == '__main__':
    unittest.main()
