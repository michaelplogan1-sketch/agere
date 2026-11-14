"""The internet-facing Agere server: logins, one workspace space per customer organization, admin, audit and usage.

    make_server(host, port, data_dir, *, secret_key, cookie_secure=True, trust_proxy=False) -> ThreadingHTTPServer

Every request resolves to (user, organization, role) from a session cookie or an `Authorization: Bearer agk_...` API
key before any data is touched. Cookie-authenticated requests that change state must carry the session's CSRF token
in `X-CSRF-Token`. Workspace calls are routed to workspace_api with the organization's own root
(<data>/tenants/<org>/workspaces), its decrypted warehouse credentials and a usage recorder, so one customer never sees
another's workspaces, files, credentials, usage or audit. Roles: viewer (read, ask), analyst (+ build and review
workspaces), admin and owner (+ members, API keys, credentials). See docs/agere/HOSTED_DESIGN.md.
GET /api/workspaces/<slug>/value is served here (hosted/value.py): illustrative physician rewards from receipted work.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import zipfile
from urllib.parse import urlsplit

from . import pages

COOKIE = 'agere_session'
BODY_LIMIT = 2 * 1024 * 1024
RANK = {'viewer': 0, 'analyst': 1, 'admin': 2, 'owner': 3}
SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin',
    'Cross-Origin-Opener-Policy': 'same-origin', 'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
    # the two web fonts of the design system (hosted/brand.py) are the only third-party assets
    'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                               "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                               "base-uri 'none'; form-action 'self'",
}


UPLOAD_MAX = int(os.environ.get('AGERE_UPLOAD_MAX_BYTES') or 10 * 1024 ** 3)
UNZIP_MAX = int(os.environ.get('AGERE_UNZIP_MAX_BYTES') or 40 * 1024 ** 3)
UNZIP_MAX_FILES = int(os.environ.get('AGERE_UNZIP_MAX_FILES') or 500_000)
FILE_SCHEMES = ('sqlite:///', 'duckdb:///', 'files:///', 'native:///')
REMOTE_SCHEMES = ('snowflake://', 'bigquery://', 'databricks://', 'postgres://', 'postgresql://')


def check_source(source, uploads_root):
    """A hosted workspace may read a cloud warehouse, or files the organization uploaded; never other server paths
    (another tenant's data, the server's own files) and never a database on the server's private network."""
    s = str(source or '').strip()
    if s.startswith(REMOTE_SCHEMES):
        if s.startswith(('postgres://', 'postgresql://')) and os.environ.get('AGERE_ALLOW_PRIVATE_DB') != '1':
            host = urlsplit(s).hostname or ''
            try:
                addrs = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
            except OSError:
                raise ValueError(f'cannot resolve database host {host!r}')
            for a in addrs:
                ip = ipaddress.ip_address(a)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    raise PermissionError('databases on private or local networks are not reachable from hosted Agere')
        return s
    scheme = next((x for x in FILE_SCHEMES if s.startswith(x)), None)
    raw = s[len(scheme) - 1:] if scheme else s
    path = Path(raw).resolve()
    root = Path(uploads_root).resolve()
    if path != root and root not in path.parents:
        raise PermissionError('file sources must be uploads of this organization (use the upload button or /api/uploads)')
    if scheme:
        return scheme[:-1] + str(path)  # e.g. 'files://' + '/abs/path' -> files:///abs/path
    return ('files://' if path.is_dir() else 'sqlite://') + str(path)


def safe_unzip(zip_path, dest):
    """Extract a zip without path traversal, absolute paths, links or decompression bombs. -> (files, bytes)."""
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    total = count = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename
            if info.is_dir():
                continue
            if name.startswith(('/', '\\')) or '..' in Path(name).parts or ':' in Path(name).parts[0]:
                raise ValueError(f'unsafe path in archive: {name[:120]}')
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f'links are not allowed in archives: {name[:120]}')
            count += 1
            total += info.file_size
            if count > UNZIP_MAX_FILES or total > UNZIP_MAX:
                raise ValueError('archive expands beyond the allowed size or file count')
            target = (dest / name).resolve()
            if dest not in target.parents:
                raise ValueError(f'unsafe path in archive: {name[:120]}')
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, 'wb') as out:
                written = 0
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > info.file_size + 1024:
                        raise ValueError('archive member larger than declared')
                    out.write(chunk)
    return count, total


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

    def uploads_root(self, org_slug):
        root = self.tenant_root(org_slug).parent / 'uploads'
        root.mkdir(parents=True, exist_ok=True)
        return root

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
                if path == '/favicon.ico':
                    return self.send(204, b'', mime='image/x-icon')
                p = self.principal()
                if path in ('/', '/app', '/admin', '/account', '/ask'):
                    if p is None or p['via'] != 'session':
                        return self.send(303, b'', mime='text/plain', location='/login')
                    from . import ask
                    if path == '/' and ask.available():
                        return self.send(303, b'', mime='text/plain', location='/ask')
                    if path == '/ask':
                        return self.send(200, ask.page(), mime='text/html')
                    page = {'/': pages.app, '/app': pages.app, '/admin': pages.admin, '/account': pages.account}[path]()
                    return self.send(200, page, mime='text/html')
                if p is None:
                    return self.error(401, 'Sign in required')
                if path == '/api/auth/me':
                    return self.send(200, self.me(p))
                if path == '/api/ask/status':
                    from . import ask
                    return self.send(200, ask.status())
                if path == '/api/uploads':
                    root = ctx.uploads_root(self.org_slug(p))
                    items = [{'name': x.name, 'kind': 'folder' if x.is_dir() else 'file',
                              'bytes': x.stat().st_size if x.is_file() else None, 'path': str(x)}
                             for x in sorted(root.iterdir()) if not x.name.endswith('.part')]
                    return self.send(200, {'uploads': items})
                m = re.fullmatch(r'/api/workspaces/([a-z0-9][a-z0-9_-]{0,80})/value', path)
                if m:
                    return self.physician_value(m.group(1), p)
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

        def do_PUT(self):
            path = urlsplit(self.path).path
            try:
                p = self.principal()
                if p is None:
                    return self.error(401, 'Sign in required')
                if not self.csrf_ok(p):
                    return self.error(403, 'Missing or invalid CSRF token')
                if not path.startswith('/api/uploads/'):
                    return self.error(404, 'Not found')
                if not self.require(p, 'analyst'):
                    raise PermissionError('your role cannot upload data')
                return self.upload(path.rsplit('/', 1)[-1], p)
            except PermissionError as exc:
                return self.error(403, str(exc))
            except (ValueError, zipfile.BadZipFile) as exc:
                return self.error(400, str(exc)[:300])
            except Exception as exc:  # noqa: BLE001
                print('error', path, type(exc).__name__, exc, flush=True)
                return self.error(500, 'Internal error')

        def upload(self, name, p):
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,120}', name):
                raise ValueError('file names use letters, digits, dot, dash and underscore')
            n = int(self.headers.get('Content-Length') or 0)
            if n <= 0:
                raise ValueError('empty upload')
            if n > UPLOAD_MAX:
                return self.error(413, f'upload larger than {UPLOAD_MAX // 1024 ** 3} GB')
            org = self.org_slug(p)
            root = ctx.uploads_root(org)
            target = root / name
            tmp = root / (name + '.part')
            left = n
            with open(tmp, 'wb') as out:
                while left:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        raise ValueError('upload ended early')
                    out.write(chunk)
                    left -= len(chunk)
            tmp.replace(target)
            result = {'ok': True, 'name': name, 'bytes': n}
            if name.lower().endswith('.zip'):
                folder = root / name[:-4]
                files, size = safe_unzip(target, folder)
                target.unlink()
                result.update(extracted_files=files, extracted_bytes=size,
                              sources={'native': f'native://{folder}', 'files': f'files://{folder}'})
            else:
                result['sources'] = {'sqlite' if name.endswith(('.sqlite', '.sqlite3', '.db')) else 'files':
                                     (f'sqlite://{target}' if name.endswith(('.sqlite', '.sqlite3', '.db')) else f'files://{root}')}
            ctx.auth.audit(org, self.user_id(p), 'upload', {'name': name, 'bytes': n}, ip=self.client_ip())
            return self.send(200, result)

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
                if path == '/api/ask':
                    from . import ask
                    code, payload = ask.answer(ctx, self.org_slug(p), self.user_id(p), body, ip=self.client_ip())
                    return self.send(code, payload)
                if path.startswith('/api/workspaces'):
                    if path.endswith('/ask') and os.environ.get('AGERE_CLAUDE_TRANSPORT') == 'api':
                        return self.error(409, 'Workspace questions use the older path, which is not run on the paid API. Use Ask.')
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
            if method == 'POST' and path.rstrip('/') == '/api/workspaces' and isinstance(body, dict):
                body = {**body, 'source': check_source(body.get('source'), ctx.uploads_root(org))}

            def resolve(name):
                return ctx.credentials.get(org, name)

            def on_usage(workspace, row):
                ctx.auth.record_usage(org, workspace, row.get('context_id') or row.get('question', '')[:80], row.get('cost_usd'), row.get('seconds'))
                ctx.auth.audit(org, uid, 'workspace.ask', {'workspace': workspace, 'question': row.get('question'), 'decision': row.get('decision'),
                                                        'context_id': row.get('context_id')},
                               ip=self.client_ip())

            from urllib.parse import parse_qsl
            query = dict(parse_qsl(urlsplit(self.path).query)) if method == 'GET' else None  # work-item filters (?kind=&state=...)
            code, payload = workspace_api.handle(method, path, body, root=root, credentials=resolve, on_usage=on_usage,
                                                 can_write=self.require(p, 'analyst'), query=query)
            if method == 'POST' and code < 400 and not path.endswith('/ask'):
                parts = [x for x in path.split('/') if x][2:]
                action = 'workspace.create' if not parts else 'workspace.' + (parts[1] if len(parts) > 1 else 'update')
                ctx.auth.audit(org, uid, action, {'workspace': parts[0] if parts else (body or {}).get('name')}, ip=self.client_ip())
            return self.send(code, payload)

        def physician_value(self, slug, p):
            """GET /api/workspaces/<slug>/value[?npi=]: illustrative physician rewards from receipted work (hosted/value.py)."""
            from urllib.parse import parse_qs
            from ..onboarding import workspace as wsmod
            from .value import physician_value
            try:
                ws = wsmod.Workspace(slug, root=ctx.tenant_root(self.org_slug(p)))
            except FileNotFoundError as exc:
                return self.error(404, str(exc))
            npi = (parse_qs(urlsplit(self.path).query).get('npi') or [None])[0]
            return self.send(200, physician_value(ws.dir, npi=npi))

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
                emails = {m.get('user_id'): m.get('email') for m in ctx.auth.members(org)}
                rows = [{**r, 'user': emails.get(r.get('user_id')) or (r.get('user_id') and f"user {r['user_id']}") or 'system'}
                        for r in ctx.auth.audit_log(org, 200)]
                return self.send(200, {'audit': rows})
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
                ctx.auth.audit(org, uid, 'credential.saved', {'name': name, 'keys': sorted(values)}, ip=self.client_ip())
                return self.send(200, {'ok': True})
            if path == '/api/admin/credentials/delete':
                ctx.credentials.delete(org, str(body['name']))
                ctx.auth.audit(org, uid, 'credential.deleted', {'name': body['name']}, ip=self.client_ip())
                return self.send(200, {'ok': True})
            return self.error(404, 'Not found')

    return Handler
