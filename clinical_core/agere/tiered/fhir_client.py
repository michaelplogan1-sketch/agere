"""Agere's connection to an EMR's FHIR API: SMART Backend Services authorization, FHIR search, and Bulk Data export.

The same client talks to the local stand-in (fhir_server.py) and to a real EMR: only the connection settings differ (the FHIR
base URL, the client id the EMR assigned when Agere was registered, the private key whose public half was registered, and the
Group to export). Authorization follows SMART Backend Services: a JWT client assertion signed RS384 with Agere's private key is
exchanged at the EMR's token endpoint for a short-lived bearer token. Private keys are kept outside the repository.

    sync(conn, out_folder)   export, download and import into a semantic layer -> path of the .sqlite3
    search(conn, 'Observation', patient='P-1001', code='2160-0')   -> resources, following next links
"""
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .fhir_server import b64url


def new_keypair():
    """-> (private key PEM bytes, public JWK). For registering Agere with an EMR, or with the local stand-in in tests."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nums = key.public_key().public_numbers()
    as_bytes = lambda n: n.to_bytes((n.bit_length() + 7) // 8, 'big')
    jwk = {'kty': 'RSA', 'alg': 'RS384', 'use': 'sig', 'kid': secrets.token_hex(6), 'n': b64url(as_bytes(nums.n)), 'e': b64url(as_bytes(nums.e))}
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem, jwk


@dataclass
class Connection:
    base_url: str
    client_id: str
    private_key_pem: bytes
    kid: str = ''
    group_id: str = 'all'
    scope: str = 'system/*.read'
    _token: tuple = field(default=None, repr=False)

    @classmethod
    def from_file(cls, path):
        """A connection settings file: {"base_url", "client_id", "private_key_path", "kid", "group_id"}."""
        c = json.loads(Path(path).read_text())
        return cls(c['base_url'].rstrip('/'), c['client_id'], Path(c['private_key_path']).read_bytes(), c.get('kid', ''), c.get('group_id', 'all'))


def _http(method, url, token=None, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers={'Accept': 'application/fhir+json', **(headers or {}),
                                                                      **({'Authorization': f'Bearer {token}'} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def token(conn):
    """A bearer token from the EMR's token endpoint, reused until a minute before it expires."""
    if conn._token and conn._token[1] > time.time() + 60: return conn._token[0]
    status, _, body = _http('GET', conn.base_url + '/.well-known/smart-configuration')
    if status != 200: raise RuntimeError(f'SMART configuration not available ({status})')
    endpoint = json.loads(body)['token_endpoint']
    head = b64url(json.dumps({'alg': 'RS384', 'typ': 'JWT', **({'kid': conn.kid} if conn.kid else {})}).encode())
    claims = b64url(json.dumps({'iss': conn.client_id, 'sub': conn.client_id, 'aud': endpoint, 'exp': int(time.time()) + 240,
                                'jti': secrets.token_hex(16)}).encode())
    key = serialization.load_pem_private_key(conn.private_key_pem, password=None)
    sig = b64url(key.sign(f'{head}.{claims}'.encode(), padding.PKCS1v15(), hashes.SHA384()))
    form = urllib.parse.urlencode({'grant_type': 'client_credentials', 'scope': conn.scope,
                                   'client_assertion_type': 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
                                   'client_assertion': f'{head}.{claims}.{sig}'}).encode()
    status, _, body = _http('POST', endpoint, data=form, headers={'Content-Type': 'application/x-www-form-urlencoded'})
    if status != 200: raise PermissionError(f'token request refused ({status}): {body[:200].decode(errors="replace")}')
    got = json.loads(body)
    conn._token = (got['access_token'], time.time() + int(got.get('expires_in', 300)))
    return conn._token[0]


def read(conn, kind, rid):
    status, _, body = _http('GET', f'{conn.base_url}/{kind}/{rid}', token(conn))
    return json.loads(body) if status == 200 else None


def search(conn, kind, limit=5000, **params):
    """Every resource a FHIR search returns, following the Bundle's next links (at most `limit`)."""
    url = f'{conn.base_url}/{kind}?' + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    out = []
    while url and len(out) < limit:
        status, _, body = _http('GET', url, token(conn))
        if status != 200: raise RuntimeError(f'{kind} search failed ({status})')
        bundle = json.loads(body)
        out += [e['resource'] for e in bundle.get('entry', [])]
        url = next((l['url'] for l in bundle.get('link', []) if l['relation'] == 'next'), None)
    return out[:limit]


def bulk_export(conn, out_folder, wait_seconds=600):
    """Kick off a Bulk Data export of the Group, poll until it is ready, download every file. -> the folder (with a manifest)."""
    out = Path(out_folder); out.mkdir(parents=True, exist_ok=True)
    status, headers, body = _http('GET', f'{conn.base_url}/Group/{conn.group_id}/$export', token(conn), headers={'Prefer': 'respond-async'})
    if status != 202: raise RuntimeError(f'export kickoff refused ({status}): {body[:200].decode(errors="replace")}')
    where = headers.get('Content-Location')
    deadline = time.time() + wait_seconds
    while True:
        status, headers, body = _http('GET', where, token(conn))
        if status == 200: break
        if status != 202 or time.time() > deadline: raise RuntimeError(f'export did not complete ({status})')
        time.sleep(min(5.0, float(headers.get('Retry-After') or 1)))
    manifest = json.loads(body)
    local = []
    for o in manifest['output']:
        name = o['url'].rsplit('/', 1)[-1]
        status, _, data = _http('GET', o['url'], token(conn) if manifest.get('requiresAccessToken') else None, headers={'Accept': 'application/fhir+ndjson'})
        if status != 200: raise RuntimeError(f'{name} download failed ({status})')
        (out / name).write_bytes(data)
        local.append({**o, 'url': name})
    (out / 'manifest.json').write_text(json.dumps({**manifest, 'output': local}, indent=1) + '\n')
    return out


def sync(conn, out_folder, db_path=None):
    """Export, download and import: the EMR's data as an Agere semantic layer. -> the .sqlite3 path."""
    from . import fhir_import
    folder = bulk_export(conn, out_folder)
    db = Path(db_path or Path(out_folder).with_suffix('.sqlite3'))
    fhir_import.build(folder, db)
    return db
