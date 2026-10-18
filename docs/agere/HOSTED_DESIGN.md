# Hosted Agere: deployable service with logins and one workspace space per customer

## Shape
```
internet ── Caddy (TLS, HTTP/2, security headers) ── agere-hosted (serve_hosted.py, 0.0.0.0:8080)
                                                        │  auth (sessions, API keys, CSRF, rate limits, audit)
                                                        │  tenancy: every request resolves to (user, organization, role)
                                                        ├─ /api/workspaces/*  → workspace_api scoped to the org's root
                                                        ├─ /app, /login, /admin, /account pages
                                                        └─ optional /demo/* → internal demo stack (AGERE_DEMO_UPSTREAM)
state volume /data:  hosted.sqlite3 (users, orgs, sessions, keys, audit, usage)
                     tenants/<org_slug>/workspaces/<slug>/...   (one directory tree per customer)
                     secrets: credentials encrypted with AGERE_SECRET_KEY (Fernet)
```

## Modules (clinical_core/agere/hosted/)
- `auth.py` — `AuthStore(path)`:
  - orgs: `create_org(name) -> org`, `get_org(slug)`, `list_orgs()`
  - users: `create_user(email, password, name=None) -> user`, `verify_password(email, password) -> user|None`
    (scrypt n=2**14 r=8 p=1, 16-byte salt, constant-time compare), `set_password`, `disable_user`
  - memberships: `add_member(org, user, role)`, role in owner|admin|analyst|viewer; `memberships(user)`; `role(user, org)`
  - sessions: `create_session(user, org, ip, ua) -> (token, csrf)`; `session(token) -> {user, org, role, csrf, expires}`;
    `revoke_session(token)`; idle timeout 12 h, absolute 7 days; tokens are 32 random bytes, stored as sha256
  - API keys: `create_api_key(org, user, name, role) -> plaintext once` (prefix `agk_`), stored hashed; `api_key(token)`
  - audit: `audit(org, user, action, detail)`; `audit_log(org, limit)`
  - usage: `record_usage(org, workspace, question_id, cost_usd, seconds)`; `usage(org, since)` (no caps; tracked only)
  - rate limit: `allow(key, limit, window_s)` (login attempts per email+ip)
  - CLI: `python -m clinical_core.agere.hosted.auth bootstrap --org NAME --email E` (prints a one-time password)
- `secrets.py` — `SecretBox(key)` (Fernet from AGERE_SECRET_KEY, base64 32 bytes); `CredentialStore(auth_db_path, box)`:
  `put(org, name, mapping)`, `get(org, name) -> mapping`, `names(org)`, `delete(org, name)`; values never logged.
  Connectors accept credentials explicitly: `connectors.connect(spec, credentials=None)` uses the mapping instead of
  os.environ for SNOWFLAKE_* / DATABRICKS_* / GOOGLE_APPLICATION_CREDENTIALS_JSON / PG*.
- `server.py` — `make_server(host, port, data_dir, auth, ...)`; request pipeline: security headers → auth (cookie
  session or `Authorization: Bearer agk_...`) → CSRF for cookie-authenticated unsafe methods (header `X-CSRF-Token`)
  → role check → handler. Workspace calls go to `workspace_api.handle(method, path, body, root=<org root>, actor=...)`.
  Roles: viewer = read + ask; analyst = + create/run/decide/batch; admin/owner = + members, API keys, credentials.
- `serve_hosted.py` (repo root) — env config: AGERE_DATA_DIR, AGERE_HOST, AGERE_PORT, AGERE_SECRET_KEY,
  AGERE_COOKIE_SECURE (default on), AGERE_PUBLIC_URL, ANTHROPIC_API_KEY, AGERE_DEMO_UPSTREAM (optional).

## Rules
- Synthetic data only until a BAA is in place (stated on the login page footer and in DEPLOY.md).
- A tenant never sees another tenant's workspaces, files, audit, usage or credentials (tests prove it).
- No spend caps are added (product policy is Mike's); usage is recorded per tenant and shown to admins.
