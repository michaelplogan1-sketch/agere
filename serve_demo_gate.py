"""Password gate for showing the local app to someone outside this machine.

    python serve_demo_gate.py set-password        # writes a new random password to artifacts/agere-demo/password.txt (owner-only)
    python serve_demo_gate.py serve [--port 8909] [--upstream 127.0.0.1:8908]

Listens on 127.0.0.1 only; a tunnel connects to it. Every request must carry HTTP Basic credentials (user "demo").
Only a salted scrypt hash of the password is kept beside the plaintext file the owner reads. Ten wrong passwords
from one address within ten minutes lock that address out for the rest of the window. Requests are forwarded
unchanged to the app; nothing is cached or logged except the method, path and status.
"""
import base64
import hashlib
import hmac
import http.client
import json
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path('artifacts/agere-demo')
USER = 'demo'
WINDOW, MAX_FAILURES = 600, 10
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding', 'upgrade'}


def _hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1).hex()


def set_password():
    HOME.mkdir(parents=True, exist_ok=True)
    password, salt = secrets.token_urlsafe(18), secrets.token_bytes(16)
    (HOME / 'gate.json').write_text(json.dumps({'user': USER, 'salt': salt.hex(), 'hash': _hash(password, salt)}))
    target = HOME / 'password.txt'
    target.write_text(f'user: {USER}\npassword: {password}\n')
    os.chmod(target, 0o600); os.chmod(HOME / 'gate.json', 0o600)
    return str(target)


def make_handler(upstream, gate):
    salt, want, failures = bytes.fromhex(gate['salt']), gate['hash'], {}

    class Gate(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, fmt, *args):
            sys.stderr.write(f'{time.strftime("%H:%M:%S")} {self.command} {self.path.split("?")[0]} {args[1] if len(args) > 1 else ""}\n')

        def _client(self):
            return (self.headers.get('CF-Connecting-IP') or self.headers.get('X-Forwarded-For') or self.client_address[0]).split(',')[0].strip()

        def _refuse(self, status, text, extra=()):
            body = text.encode()
            self.send_response(status)
            for k, v in (('Content-Type', 'text/plain; charset=utf-8'), ('Content-Length', str(len(body))), ('Cache-Control', 'no-store'), *extra):
                self.send_header(k, v)
            self.end_headers(); self.wfile.write(body)

        def _allowed(self):
            who, now = self._client(), time.time()
            recent = [t for t in failures.get(who, []) if now - t < WINDOW]
            failures[who] = recent
            if len(recent) >= MAX_FAILURES:
                self._refuse(429, 'Too many wrong passwords. Try again later.'); return False
            header = self.headers.get('Authorization') or ''
            try:
                user, _, password = base64.b64decode(header.split(' ', 1)[1]).decode().partition(':') if header.lower().startswith('basic ') else ('', '', '')
            except Exception:
                user, password = '', ''
            if user == gate['user'] and password and hmac.compare_digest(_hash(password, salt), want): return True
            if header: recent.append(now)
            self._refuse(401, 'Password required.', (('WWW-Authenticate', 'Basic realm="Agere demo", charset="UTF-8"'),)); return False

        def _forward(self):
            if not self._allowed(): return
            length = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP | {'authorization', 'host', 'content-length'}}
            headers['Host'] = upstream
            try:
                conn = http.client.HTTPConnection(upstream, timeout=1800)
                conn.request(self.command, self.path, body=body, headers=headers)
                got = conn.getresponse(); data = got.read()
            except Exception as exc:
                self._refuse(502, f'The app is not reachable: {type(exc).__name__}'); return
            self.send_response(got.status)
            for k, v in got.getheaders():
                if k.lower() not in HOP | {'content-length'}: self.send_header(k, v)
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
            conn.close()

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _forward

    return Gate


def serve(port=8909, upstream='127.0.0.1:8908'):
    gate = json.loads((HOME / 'gate.json').read_text())
    server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(upstream, gate))
    print(f'gate on http://127.0.0.1:{port} -> {upstream} (user {gate["user"]})', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    args = sys.argv[1:]
    if args and args[0] == 'set-password':
        print('password written to', set_password())
    else:
        opt = lambda name, default: args[args.index(name) + 1] if name in args else default
        serve(int(opt('--port', 8909)), opt('--upstream', '127.0.0.1:8908'))
