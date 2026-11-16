"""A local stand-in EMR: a FHIR R4 server, on this machine only, that answers what Epic, athenahealth or Oracle Health answer.

It serves a Bulk FHIR export folder (fhir_synth.py writes one) the way an EMR's FHIR API does, so Agere's EMR connection is
exercised end to end with no real EMR:
  GET  /metadata                                capability statement
  GET  /.well-known/smart-configuration         SMART configuration (token endpoint, Backend Services)
  POST /auth/token                              SMART Backend Services: client_credentials with a signed JWT client assertion
                                                (RS384, the client's public key registered in advance); returns a bearer token
  GET  /{Type}/{id}                             read
  GET  /{Type}?patient=&subject=&code=&date=&_count=   search, paged as a searchset Bundle with a next link
  GET  /Group/{id}/$export                      Bulk Data kickoff (Prefer: respond-async): 202 and a status URL
  GET  /bulkstatus/{job}                        202 while running, then 200 with the export manifest
  GET  /bulkfiles/{Type}.ndjson                 an exported file
Every data request needs the bearer token. It listens on 127.0.0.1 only and holds no real data. This is test code for the
connection, not a product service.

    python -m clinical_core.agere.tiered.fhir_server [port]
"""
import base64
import hashlib
import json
import secrets
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

EXPORT = Path('artifacts/agere-tiered-core/fhir/export')
TOKEN_SECONDS = 300
PAGE = 100


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def unb64url(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def public_key_from_jwk(jwk):
    n = int.from_bytes(unb64url(jwk['n']), 'big'); e = int.from_bytes(unb64url(jwk['e']), 'big')
    return rsa.RSAPublicNumbers(e, n).public_key()


class Store:
    """The export folder, indexed for reads and searches."""
    def __init__(self, folder):
        self.folder = Path(folder)
        self.manifest = json.loads((self.folder / 'manifest.json').read_text())
        self.by_id, self.by_type, self.by_patient = {}, defaultdict(list), defaultdict(lambda: defaultdict(list))
        for out in self.manifest['output']:
            kind = out['type']
            for line in open(self.folder / out['url']):
                if not line.strip(): continue
                r = json.loads(line)
                self.by_id[(kind, r['id'])] = r
                self.by_type[kind].append(r)
                ref = (r.get('subject') or {}).get('reference') or (r.get('patient') or {}).get('reference')
                pid = r['id'] if kind == 'Patient' else (ref.split('/')[-1] if ref else None)
                if pid: self.by_patient[pid][kind].append(r)

    def search(self, kind, params):
        pid = (params.get('patient') or params.get('subject') or [None])[0]
        pid = pid.split('/')[-1] if pid else None
        pool = self.by_patient[pid][kind] if pid else self.by_type[kind]
        if 'identifier' in params:
            value = params['identifier'][0].split('|')[-1]
            pool = [r for r in pool if any(i.get('value') == value for i in r.get('identifier', []))]
        if 'code' in params:
            codes = {c.split('|')[-1] for c in params['code'][0].split(',')}
            pool = [r for r in pool if codes & {c.get('code') for c in ((r.get('code') or r.get('medicationCodeableConcept') or {}).get('coding') or [])}]
        for d in params.get('date', []):   # ge2029-01-01, lt2030-01-01, or an exact prefix
            op, value = (d[:2], d[2:]) if d[:2] in ('ge', 'gt', 'le', 'lt', 'eq') else ('eq', d)
            def when(r):
                return str(r.get('effectiveDateTime') or (r.get('period') or {}).get('start') or (r.get('performedPeriod') or {}).get('start') or '')
            test = {'ge': lambda w: w >= value, 'gt': lambda w: w > value, 'le': lambda w: w[:len(value)] <= value, 'lt': lambda w: w < value,
                    'eq': lambda w: w.startswith(value)}[op]
            pool = [r for r in pool if when(r) and test(when(r))]
        return pool


class Server:
    def __init__(self, folder=EXPORT, clients=None, host='127.0.0.1', port=0, export_delay=0.2):
        self.store = Store(folder)
        self.clients = dict(clients or {})          # client_id -> public JWK
        self.tokens, self.jobs, self.seen_jti = {}, {}, set()
        self.export_delay = export_delay
        self.httpd = ThreadingHTTPServer((host, port), self._handler())
        self.base = f'http://{host}:{self.httpd.server_address[1]}'

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.httpd.shutdown(); self.httpd.server_close()

    # SMART Backend Services: the client signs a JWT with its private key; the server checks it with the registered public key.
    def issue(self, form):
        if form.get('grant_type') != 'client_credentials': return 400, {'error': 'unsupported_grant_type'}
        if form.get('client_assertion_type') != 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer': return 400, {'error': 'invalid_request'}
        try:
            head, body, sig = form['client_assertion'].split('.')
            header, claims = json.loads(unb64url(head)), json.loads(unb64url(body))
        except Exception:
            return 400, {'error': 'invalid_client', 'error_description': 'malformed client assertion'}
        jwk = self.clients.get(claims.get('iss'))
        if header.get('alg') != 'RS384' or not jwk or claims.get('sub') != claims.get('iss'):
            return 401, {'error': 'invalid_client', 'error_description': 'unknown client or algorithm'}
        try:
            public_key_from_jwk(jwk).verify(unb64url(sig), f'{head}.{body}'.encode(), padding.PKCS1v15(), hashes.SHA384())
        except Exception:
            return 401, {'error': 'invalid_client', 'error_description': 'signature does not verify'}
        now = time.time()
        if claims.get('aud') != self.base + '/auth/token' or not claims.get('exp') or claims['exp'] < now or claims['exp'] > now + 300:
            return 401, {'error': 'invalid_client', 'error_description': 'audience or expiry not acceptable'}
        if claims.get('jti') in self.seen_jti: return 401, {'error': 'invalid_client', 'error_description': 'assertion replayed'}
        self.seen_jti.add(claims.get('jti'))
        scope = form.get('scope') or 'system/*.read'
        token = secrets.token_urlsafe(24)
        self.tokens[token] = (claims['iss'], scope, now + TOKEN_SECONDS)
        return 200, {'access_token': token, 'token_type': 'bearer', 'expires_in': TOKEN_SECONDS, 'scope': scope}

    def authorized(self, header):
        if not header or not header.startswith('Bearer '): return False
        t = self.tokens.get(header[7:])
        return bool(t) and t[2] > time.time()

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body, kind='application/fhir+json', headers=()):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code); self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(data)))
                for k, v in headers: self.send_header(k, v)
                self.end_headers(); self.wfile.write(data)

            def outcome(self, code, text):
                self.send(code, {'resourceType': 'OperationOutcome', 'issue': [{'severity': 'error', 'code': 'processing', 'diagnostics': text}]})

            def do_POST(self):
                if urlparse(self.path).path != '/auth/token': return self.outcome(404, 'not found')
                length = int(self.headers.get('Content-Length') or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
                code, body = server.issue(form)
                self.send(code, body, 'application/json')

            def do_GET(self):
                url = urlparse(self.path); path, params = url.path, parse_qs(url.query)
                if path == '/metadata':
                    return self.send(200, {'resourceType': 'CapabilityStatement', 'status': 'active', 'fhirVersion': '4.0.1', 'kind': 'instance',
                                           'format': ['json'], 'rest': [{'mode': 'server', 'resource': [{'type': t} for t in sorted(server.store.by_type)]}]})
                if path == '/.well-known/smart-configuration':
                    return self.send(200, {'token_endpoint': server.base + '/auth/token', 'grant_types_supported': ['client_credentials'],
                                           'token_endpoint_auth_methods_supported': ['private_key_jwt'], 'token_endpoint_auth_signing_alg_values_supported': ['RS384'],
                                           'scopes_supported': ['system/*.read'], 'capabilities': ['client-confidential-asymmetric']}, 'application/json')
                if not server.authorized(self.headers.get('Authorization')): return self.outcome(401, 'a bearer token is required')
                parts = [p for p in path.split('/') if p]
                if len(parts) == 3 and parts[0] == 'Group' and parts[2] == '$export':
                    if 'respond-async' not in str(self.headers.get('Prefer')): return self.outcome(400, 'Prefer: respond-async is required')
                    job = secrets.token_hex(8); server.jobs[job] = time.time() + server.export_delay
                    return self.send(202, b'', headers=[('Content-Location', f'{server.base}/bulkstatus/{job}')])
                if len(parts) == 2 and parts[0] == 'bulkstatus':
                    ready = server.jobs.get(parts[1])
                    if ready is None: return self.outcome(404, 'no such export')
                    if time.time() < ready: return self.send(202, b'', headers=[('X-Progress', 'in progress'), ('Retry-After', '1')])
                    m = server.store.manifest
                    return self.send(200, {**m, 'request': f'{server.base}/Group/{parts[1]}/$export', 'requiresAccessToken': True,
                                           'output': [{**o, 'url': f"{server.base}/bulkfiles/{o['url']}"} for o in m['output']]}, 'application/json')
                if len(parts) == 2 and parts[0] == 'bulkfiles':
                    f = server.store.folder / parts[1]
                    if not f.exists() or not parts[1].endswith('.ndjson'): return self.outcome(404, 'no such file')
                    return self.send(200, f.read_bytes(), 'application/fhir+ndjson')
                if len(parts) == 2:
                    r = server.store.by_id.get((parts[0], parts[1]))
                    return self.send(200, r) if r else self.outcome(404, f'{parts[0]}/{parts[1]} not found')
                if len(parts) == 1:
                    found = server.store.search(parts[0], params)
                    count = int((params.get('_count') or [PAGE])[0]); offset = int((params.get('_offset') or [0])[0])
                    page = found[offset:offset + count]
                    links = [{'relation': 'self', 'url': server.base + self.path}]
                    if offset + count < len(found):
                        q = {k: v for k, v in params.items() if k not in ('_offset',)}
                        query = '&'.join(f'{k}={v[0]}' for k, v in q.items())
                        links.append({'relation': 'next', 'url': f'{server.base}/{parts[0]}?{query}&_offset={offset + count}'})
                    return self.send(200, {'resourceType': 'Bundle', 'type': 'searchset', 'total': len(found), 'link': links,
                                           'entry': [{'fullUrl': f"{server.base}/{parts[0]}/{r['id']}", 'resource': r} for r in page]})
                return self.outcome(404, 'not found')
        return Handler


if __name__ == '__main__':
    import sys
    s = Server(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8910).start()
    print('local FHIR stand-in at', s.base, '(no clients registered; register one with fhir_client.register)')
    try:
        while True: time.sleep(3600)
    except KeyboardInterrupt:
        s.stop()
