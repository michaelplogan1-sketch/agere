"""The internet-facing Agere server: logins, one workspace space per customer organization, admin, audit and usage.

    make_server(host, port, data_dir, *, secret_key, cookie_secure=True, trust_proxy=False) -> ThreadingHTTPServer

Every request resolves to (user, organization, role) from a session cookie or an `Authorization: Bearer agk_...` API
key before any data is touched. Cookie-authenticated requests that change state must carry the session's CSRF token
in `X-CSRF-Token`. Workspace calls are routed to workspace_api with the organization's own root
(<data>/tenants/<org>/workspaces), its decrypted warehouse credentials and a usage recorder, so one customer never sees
another's workspaces, files, credentials, usage or audit. Roles: viewer (read, ask), analyst (+ build and review
workspaces), admin and owner (+ members, API keys, credentials). See docs/agere/HOSTED_DESIGN.md.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from . import pages

COOKIE = 'agere_session'
BODY_LIMIT = 2 * 1024 * 1024
RANK = {'viewer': 0, 'analyst': 1, 'admin': 2, 'owner': 3}
SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin',
    'Cross-Origin-Opener-Policy': 'same-origin', 'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
    'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}


def _get(obj, *names):
    """Field from an auth record that may be a dict or an object (the auth store's record shape)."""
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


class Context:
    def __init__(self, data_dir, auth, credentials, *, cookie_secure=True, trust_proxy=False):
        self.data = Path(data_dir)
        self.auth = auth
        self.credentials = credentials
        self.cookie_secure = cookie_secure
        self.trust_proxy = trust_proxy

    def tenant_root(self, org_slug):
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', str(org_slug or '')):
            raise PermissionError('invalid organization')
        root = self.data / 'tenants' / org_slug / 'workspaces'
        root.mkdir(parents=True, exist_ok=True)
        return root


def make_server(host, port, data_dir, *, secret_key, cookie_secure=True, trust_proxy=False):
    from .auth import AuthStore
    from .secrets import CredentialStore, SecretBox
    data = Path(data_dir)
    data.mkdir(parents=True, exist_ok=True)
    auth = AuthStore(data / 'hosted.sqlite3')
    creds = CredentialStore(data / 'hosted.sqlite3', SecretBox(secret_key))
    ctx = Context(data, auth, creds, cookie_secure=cookie_secure, trust_proxy=trust_proxy)
    server = ThreadingHTTPServer((host, int(port)), make_handler(ctx))
    server.daemon_threads = True
    server.ctx = ctx
    return server


def make_handler(ctx):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'Agere'
        sys_version = ''

        def log_message(self, fmt, *args):  # one line per request, no bodies, no tokens
            print(f'{self.client_ip()} {self.command} {urlsplit(self.path).path} {args[1] if len(args) > 1 else ""}', flush=True)

        # --- plumbing ---------------------------------------------------------------------------------------------
        def client_ip(self):
            if ctx.trust_proxy and self.headers.get('X-Forwarded-For'):
                return self.headers['X-Forwarded-For'].split(',')[0].strip()
            return self.client_address[0]

        def send(self, code, payload=None, *, mime='application/json', cookies=(), location=None):
            raw = payload if isinstance(payload, bytes) else (
                payload.encode() if isinstance(payload, str) else json.dumps(payload if payload is not None else {}, default=str).encode())
            self.send_response(code)
            self.send_header('Content-Type', mime + ('; charset=utf-8' if mime.startswith('text/') else ''))
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            if ctx.cookie_secure:
                self.send_header('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
            for c in cookies:
                self.send_header('Set-Cookie', c)
            if location:
                self.send_header('Location', location)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(raw)

        def error(self, code, message):
            self.send(code, {'error': message})

        def cookie(self, name):
            for part in (self.headers.get('Cookie') or '').split(';'):
                k, _, v = part.strip().partition('=')
                if k == name:
                    return v
            return None

        def session_cookie(self, token, max_age):
            flags = f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}'
            return flags + ('; Secure' if ctx.cookie_secure else '')

        def body(self):
            n = int(self.headers.get('Content-Length') or 0)
            if n > BODY_LIMIT:
                raise ValueError('request too large')
            if not n:
                return {}
            if (self.headers.get('Content-Type') or '').split(';')[0].strip() != 'application/json':
                raise ValueError('JSON body required')
            data = json.loads(self.rfile.read(n))
            if not isinstance(data, dict):
                raise ValueError('object body required')
            return data

        def principal(self):
            """{'user', 'org', 'role', 'csrf', 'via'} from a session cookie or an API key, else None."""
            authz = self.headers.get('Authorization') or ''
            if authz.startswith('Bearer '):
                k = ctx.auth.api_key(authz[7:].strip())
                if k:
                    return {'user': _get(k, 'user', 'user_id'), 'org': _get(k, 'org', 'org_slug'), 'role': _get(k, 'role'),
                            'csrf': None, 'via': 'api_key', 'raw': k}
                return None
            token = self.cookie(COOKIE)
            if token:
                s = ctx.auth.session(token)
                if s:
                    return {'user': _get(s, 'user', 'user_id'), 'org': _get(s, 'org', 'org_slug'), 'role': _get(s, 'role'),
                            'csrf': _get(s, 'csrf'), 'via': 'session', 'token': token, 'raw': s}
            return None

        def org_slug(self, p):
            org = p['org']
            return _get(org, 'slug') if not isinstance(org, str) else org

        def user_id(self, p):
            u = p['user']
            return _get(u, 'id', 'email') if not isinstance(u, (str, int)) else u

        def require(self, p, role):
            return p is not None and RANK.get(p['role'], -1) >= RANK[role]

        def csrf_ok(self, p):
            if p['via'] != 'session':
                return True  # API keys are not sent automatically by browsers
            import hmac
            given = self.headers.get('X-CSRF-Token') or ''
            return bool(p.get('csrf')) and hmac.compare_digest(given, p['csrf'])

        # --- routing ----------------------------------------------------------------------------------------------
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlsplit(self.path).path
            try:
                if path == '/healthz':
                    return self.send(200, {'ok': True, 'service': 'agere-hosted'})
                if path == '/login':
                    return self.send(200, pages.login(), mime='text/html')
                p = self.principal()
                if path in ('/', '/app', '/admin', '/account'):
                    if p is None or p['via'] != 'session':
                        return self.send(303, b'', mime='text/plain', location='/login')
                    page = {'/': pages.app, '/app': pages.app, '/admin': pages.admin, '/account': pages.account}[path]()
                    return self.send(200, page, mime='text/html')
                if p is None:
                    return self.error(401, 'Sign in required')
                if path == '/api/auth/me':
                    return self.send(200, self.me(p))
                if path.startswith('/api/workspaces'):
                    return self.workspaces('GET', path, None, p)
                if path.startswith('/api/admin/'):
                    return self.admin_get(path, p)
                return self.error(404, 'Not found')
            except PermissionError as exc:
                return self.error(403, str(exc))
            except Exception as exc:  # noqa: BLE001 - never leak a traceback to the client
                print('error', path, type(exc).__name__, exc, flush=True)
                return self.error(500, 'Internal error')

        def do_POST(self):
            path = urlsplit(self.path).path
            try:
                body = self.body()
            except (ValueError, json.JSONDecodeError) as exc:
                return self.error(400, str(exc))
            try:
                if path == '/api/auth/login':
                    return self.login(body)
                p = self.principal()
                if p is None:
                    return self.error(401, 'Sign in required')
                if not self.csrf_ok(p):
                    return self.error(403, 'Missing or invalid CSRF token')
                if path == '/api/auth/logout':
                    if p['via'] == 'session':
                        ctx.auth.revoke_session(p['token'])
                    return self.send(200, {'ok': True}, cookies=[self.session_cookie('', 0)])
                if path == '/api/auth/switch':
                    return self.switch(body, p)
                if path == '/api/account/password':
                    return self.change_password(body, p)
                if path.startswith('/api/workspaces'):
                    return self.workspaces('POST', path, body, p)
                if path.startswith('/api/admin/'):
                    return self.admin_post(path, body, p)
                return self.error(404, 'Not found')
            except PermissionError as exc:
                return self.error(403, str(exc))
            except (ValueError, KeyError) as exc:
                return self.error(400, f'{type(exc).__name__}: {exc}'[:300])
            except Exception as exc:  # noqa: BLE001
                print('error', path, type(exc).__name__, exc, flush=True)
                return self.error(500, 'Internal error')

        # --- auth -------------------------------------------------------------------------------------------------
        def login(self, body):
            email, password = str(body.get('email') or '').strip().lower(), str(body.get('password') or '')
            ip = self.client_ip()
            if not ctx.auth.allow(f'login:{email}:{ip}', 10, 900) or not ctx.auth.allow(f'login-ip:{ip}', 50, 900):
                return self.error(429, 'Too many sign-in attempts; try again in 15 minutes')
            user = ctx.auth.verify_password(email, password, ip=ip)  # the store audits failures
            if not user:
                return self.error(401, 'Email or password is incorrect')
            orgs = ctx.auth.memberships(user)
            if not orgs:
                return self.error(403, 'Your account is not a member of any organization')
            want = body.get('org')
            pick = next((m for m in orgs if _get(m, 'org_slug', 'slug') == want), orgs[0])
            org = _get(pick, 'org_slug', 'slug', 'org')
            token, csrf = ctx.auth.create_session(user, org, ip, self.headers.get('User-Agent', '')[:200])
            return self.send(200, {'ok': True, 'org': org, 'csrf': csrf},
                             cookies=[self.session_cookie(token, 7 * 24 * 3600)])

        def me(self, p):
            user = p['user']
            out = {'org': self.org_slug(p), 'role': p['role'], 'via': p['via'], 'csrf': p.get('csrf')}
            if p['via'] == 'session':
                out['user'] = {'email': _get(user, 'email'), 'name': _get(user, 'name')} if not isinstance(user, (str, int)) else {'id': user}
                try:
                    out['orgs'] = [{'slug': _get(m, 'org_slug', 'slug'), 'name': _get(m, 'org_name', 'name'), 'role': _get(m, 'role')}
                                   for m in ctx.auth.memberships(user)]
                except Exception:  # noqa: BLE001
                    out['orgs'] = []
            return out

        def switch(self, body, p):
            if p['via'] != 'session':
                return self.error(400, 'Switching applies to browser sessions')
            target = str(body.get('org') or '')
            role = ctx.auth.role(p['user'], target)
            if not role:
                raise PermissionError('not a member of that organization')
            ctx.auth.revoke_session(p['token'])
            token, csrf = ctx.auth.create_session(p['user'], target, self.client_ip(), self.headers.get('User-Agent', '')[:200])
            return self.send(200, {'ok': True, 'org': target, 'csrf': csrf}, cookies=[self.session_cookie(token, 7 * 24 * 3600)])

        def change_password(self, body, p):
            if p['via'] != 'session':
                return self.error(400, 'Password changes apply to browser sessions')
            email = _get(p['user'], 'email')
            if not ctx.auth.verify_password(email, str(body.get('current') or '')):
                return self.error(403, 'Current password is incorrect')
            ctx.auth.set_password(p['user'], str(body.get('new') or ''), actor=self.user_id(p), ip=self.client_ip())
            return self.send(200, {'ok': True})

        # --- workspaces ---------------------------------------------------------------------------------------------
        def workspaces(self, method, path, body, p):
            from .. import workspace_api
            org = self.org_slug(p)
            root = ctx.tenant_root(org)
            uid = self.user_id(p)

            def resolve(name):
                return ctx.credentials.get(org, name)

            def on_usage(workspace, row):
                ctx.auth.record_usage(org, workspace, row.get('question', '')[:80], row.get('cost_usd'), row.get('seconds'))
                ctx.auth.audit(org, uid, 'ask', {'workspace': workspace, 'question': row.get('question'), 'decision': row.get('decision')})

            code, payload = workspace_api.handle(method, path, body, root=root, credentials=resolve, on_usage=on_usage,
                                                 can_write=self.require(p, 'analyst'))
            if method == 'POST' and code < 400 and not path.endswith('/ask'):
                ctx.auth.audit(org, uid, 'workspace:' + (path.rstrip('/').rsplit('/', 1)[-1]), {'path': path})
            return self.send(code, payload)

        # --- admin --------------------------------------------------------------------------------------------------
        def admin_get(self, path, p):
            org = self.org_slug(p)
            if path == '/api/admin/usage':
                if not self.require(p, 'admin'):
                    raise PermissionError('admins only')
                return self.send(200, {'usage': ctx.auth.usage(org)})
            if not self.require(p, 'admin'):
                raise PermissionError('admins only')
            if path == '/api/admin/members':
                return self.send(200, {'members': ctx.auth.members(org) if hasattr(ctx.auth, 'members') else []})
            if path == '/api/admin/keys':
                return self.send(200, {'keys': ctx.auth.api_keys(org) if hasattr(ctx.auth, 'api_keys') else []})
            if path == '/api/admin/credentials':
                return self.send(200, {'credentials': ctx.credentials.names(org)})
            if path == '/api/admin/audit':
                return self.send(200, {'audit': ctx.auth.audit_log(org, 200)})
            return self.error(404, 'Not found')

        def admin_post(self, path, body, p):
            if not self.require(p, 'admin'):
                raise PermissionError('admins only')
            org, uid = self.org_slug(p), self.user_id(p)
            if path == '/api/admin/members':
                email, role = str(body['email']).strip().lower(), str(body.get('role') or 'analyst')
                if role not in RANK or (role == 'owner' and p['role'] != 'owner'):
                    raise PermissionError('cannot grant that role')
                user, password = ctx.auth.find_or_create_user(email, body.get('name')) if hasattr(ctx.auth, 'find_or_create_user') else (None, None)
                ctx.auth.add_member(org, user or email, role, actor=uid, ip=self.client_ip())
                return self.send(200, {'ok': True, 'email': email, 'role': role, 'temporary_password': password})
            if path == '/api/admin/members/remove':
                email = str(body['email']).strip().lower()
                ctx.auth.remove_member(org, email, actor=uid, ip=self.client_ip())
                return self.send(200, {'ok': True})
            if path == '/api/admin/keys':
                role = str(body.get('role') or 'analyst')
                if role not in RANK or RANK[role] > RANK[p['role']]:
                    raise PermissionError('a key cannot exceed your own role')
                token = ctx.auth.create_api_key(org, p['user'], str(body.get('name') or 'api key')[:80], role, ip=self.client_ip())
                return self.send(200, {'ok': True, 'key': token, 'note': 'Shown once; store it now.'})
            if path == '/api/admin/keys/revoke':
                ctx.auth.revoke_api_key(org, body['id'], actor=uid, ip=self.client_ip())
                return self.send(200, {'ok': True})
            if path == '/api/admin/credentials':
                name = str(body['name']).strip()
                if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', name):
                    raise ValueError('credential names use letters, digits, dot, dash and underscore')
                values = body.get('values')
                if not isinstance(values, dict) or not values:
                    raise ValueError('values must be a non-empty object')
                ctx.credentials.put(org, name, {str(k): str(v) for k, v in values.items()}, kind=body.get('kind'))
                ctx.auth.audit(org, uid, 'credential_saved', {'name': name, 'keys': sorted(values)})
                return self.send(200, {'ok': True})
            if path == '/api/admin/credentials/delete':
                ctx.credentials.delete(org, str(body['name']))
                ctx.auth.audit(org, uid, 'credential_deleted', {'name': body['name']})
                return self.send(200, {'ok': True})
            return self.error(404, 'Not found')

    return Handler
