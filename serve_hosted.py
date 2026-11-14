"""Run hosted Agere (logins, one workspace space per customer organization). Configuration comes from the environment:

    AGERE_DATA_DIR      state directory (default ./artifacts/hosted): hosted.sqlite3 + tenants/<org>/workspaces
    AGERE_HOST          bind address (default 0.0.0.0; put Caddy or another TLS proxy in front)
    AGERE_PORT          port (default 8080)
    AGERE_SECRET_KEY    Fernet key for stored warehouse credentials (required)
    AGERE_COOKIE_SECURE 1 (default) sets Secure cookies and HSTS; 0 only for local http testing
    AGERE_TRUST_PROXY   1 when behind a proxy that sets X-Forwarded-For (default 1 with AGERE_COOKIE_SECURE)
    AGERE_CLAUDE_TRANSPORT subscription (default; the signed-in Claude subscription) or api (paid key in ANTHROPIC_API_KEY; hosted only)
    AGERE_TIERED_DB     demonstration semantic layer for Ask (synthetic); with AGERE_TIERED_BOTTLES and AGERE_TIERED_LEDGER
    TYPESAFE_API_KEY, PERPLEXITY_API_KEY   System One providers used by Ask

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


def configure_models():
    """Economical routing plus one Claude transport.
    subscription (default): the signed-in Claude subscription through the CLI; any external key is removed.
    api: a paid Anthropic API key from the environment. Allowed here only (Mike, 2026-11-13: hosted server only);
         the tiered core uses it, and the older workspace question path is refused in this mode."""
    from clinical_core.agere import model_policy
    transport = os.environ.get('AGERE_CLAUDE_TRANSPORT', 'subscription').strip().lower()
    os.environ.update(model_policy.application_runtime({})['environment'])
    os.environ['AGERE_HOSTED'] = '1'
    if transport == 'api':
        if not (os.environ.get('ANTHROPIC_API_KEY') or '').startswith('sk-ant-'):
            raise ValueError('AGERE_CLAUDE_TRANSPORT=api needs ANTHROPIC_API_KEY in the server environment')
        os.environ['AGERE_CLAUDE_TRANSPORT'] = 'api'
        return 'api'
    if transport != 'subscription':
        raise ValueError('AGERE_CLAUDE_TRANSPORT must be subscription or api')
    from clinical_core.agere import subscription_transport
    os.environ['AGERE_CLAUDE_TRANSPORT'] = 'subscription'
    os.environ.pop('ANTHROPIC_API_KEY', None)
    if not subscription_transport.install():
        raise RuntimeError('Claude subscription transport could not be installed')
    os.environ['ANTHROPIC_API_KEY'] = 'subscription-transport'  # internal availability marker, never an external key
    return 'subscription'


def main():
    removed = scrub_environment()
    if removed:
        print(f'removed {len(removed)} warehouse credential variable(s) from the environment (tenants supply their own)', flush=True)
    data = Path(os.environ.get('AGERE_DATA_DIR') or Path(__file__).resolve().parent / 'artifacts' / 'hosted')
    os.environ.setdefault('AGERE_LEDGER_PATH', str(data / 'provider-budget.sqlite3'))  # model-call ledger lives with the data
    (data / 'artifacts').mkdir(parents=True, exist_ok=True)  # default-path writes (background learning logs) land on the volume
    key = os.environ.get('AGERE_SECRET_KEY')
    if not key:
        raise SystemExit('AGERE_SECRET_KEY is required (python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")')
    secure = os.environ.get('AGERE_COOKIE_SECURE', '1') != '0'
    trust = os.environ.get('AGERE_TRUST_PROXY', '1' if secure else '0') == '1'
    host, port = os.environ.get('AGERE_HOST', '0.0.0.0'), int(os.environ.get('AGERE_PORT', '8080'))
    transport = configure_models()
    server = make_server(host, port, data, secret_key=key, cookie_secure=secure, trust_proxy=trust)
    from clinical_core.agere.hosted import ask
    print(f'Agere hosted on {host}:{port} (data {data}, secure cookies {"on" if secure else "off"}, Claude transport {transport}, '
          f'Ask {"on" if ask.available() else "off: set AGERE_TIERED_DB to the demonstration semantic layer"})', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
