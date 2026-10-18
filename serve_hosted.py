"""Run hosted Agere (logins, one workspace space per customer organization). Configuration comes from the environment:

    AGERE_DATA_DIR      state directory (default ./artifacts/hosted): hosted.sqlite3 + tenants/<org>/workspaces
    AGERE_HOST          bind address (default 0.0.0.0; put Caddy or another TLS proxy in front)
    AGERE_PORT          port (default 8080)
    AGERE_SECRET_KEY    Fernet key for stored warehouse credentials (required)
    AGERE_COOKIE_SECURE 1 (default) sets Secure cookies and HSTS; 0 only for local http testing
    AGERE_TRUST_PROXY   1 when behind a proxy that sets X-Forwarded-For (default 1 with AGERE_COOKIE_SECURE)
    ANTHROPIC_API_KEY   model provider key for answering

First organization and owner:  python -m clinical_core.agere.hosted.auth bootstrap --db <data>/hosted.sqlite3 --org NAME --email E
See deploy/DEPLOY.md and docs/agere/HOSTED_DESIGN.md.
"""
import os
from pathlib import Path

from clinical_core.agere.hosted.server import make_server


# Warehouse credentials come only from each tenant's encrypted store. Client libraries (libpq especially) fill missing
# settings from the process environment, so operator-level warehouse variables are removed before anything connects.
TENANT_SCOPED_ENV = ('PG', 'SNOWFLAKE_', 'DATABRICKS_', 'GOOGLE_APPLICATION_CREDENTIALS', 'BIGQUERY_')


def scrub_environment(environ=os.environ):
    removed = [k for k in list(environ) if k.startswith(TENANT_SCOPED_ENV)]
    for k in removed:
        del environ[k]
    return removed


def main():
    removed = scrub_environment()
    if removed:
        print(f'removed {len(removed)} warehouse credential variable(s) from the environment (tenants supply their own)', flush=True)
    data = Path(os.environ.get('AGERE_DATA_DIR') or Path(__file__).resolve().parent / 'artifacts' / 'hosted')
    os.environ.setdefault('AGERE_LEDGER_PATH', str(data / 'provider-budget.sqlite3'))  # model-call ledger lives with the data
    key = os.environ.get('AGERE_SECRET_KEY')
    if not key:
        raise SystemExit('AGERE_SECRET_KEY is required (python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")')
    secure = os.environ.get('AGERE_COOKIE_SECURE', '1') != '0'
    trust = os.environ.get('AGERE_TRUST_PROXY', '1' if secure else '0') == '1'
    host, port = os.environ.get('AGERE_HOST', '0.0.0.0'), int(os.environ.get('AGERE_PORT', '8080'))
    if not os.environ.get('ANTHROPIC_API_KEY'):
        print('warning: ANTHROPIC_API_KEY is not set; questions cannot be answered', flush=True)
    server = make_server(host, port, data, secret_key=key, cookie_secure=secure, trust_proxy=trust)
    print(f'Agere hosted on {host}:{port} (data {data}, secure cookies {"on" if secure else "off"})', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
