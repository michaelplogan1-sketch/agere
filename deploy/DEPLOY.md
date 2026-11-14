# Deploying hosted Agere

**Current status (November 13): ready to deploy as a synthetic-data demonstration; not a qualified customer release.**
The hosted server now includes **Ask**: a signed-in user's question goes through the tiered answering stack (saved
recipes, built-in recipes, System One, the small model, a frontier-written recipe, then a bounded frontier takeover)
over the synthetic demonstration data. This was run end to end on the workstation through this same server code
(sign-in, CSRF, usage and audit) with the subscription transport. The paid-key transport used on the server
(`AGERE_CLAUDE_TRANSPORT=api`) is covered by a model-free test only: its first live call happens on the server, so run
the smoke test in step 5 before sharing the address. No real patient data; see the data rule below.

## The short version (owner's checklist)

1. Rent a Linux server (Ubuntu 24.04, 4 vCPU, 16 GB memory, 100 GB disk; about $50-100 a month at Hetzner, DigitalOcean
   or AWS Lightsail). Install Docker (`curl -fsSL https://get.docker.com | sh`).
2. Point a name at it: an `A` record such as `app.physicianbuilders.com` to the server's address (in the DNS settings
   where the domain is registered; the existing Vercel site at the main address is not touched).
3. On the server: clone the repository, `cd deploy`, `cp .env.example .env`, fill in the domain, a new
   `AGERE_SECRET_KEY`, the Anthropic key and the two System One keys, then `docker compose up -d --build`.
4. From the workstation: `deploy/send_demo_data.sh user@server` (copies the 9 GB synthetic data and learned recipes).
5. Create the first login (step 4 below), sign in at `https://<name>/`, and ask the five example questions.

Two containers: `agere` (serve_hosted.py on :8080, internal only) and `caddy` (TLS on 80/443, reverse proxy).
All state lives in the `agere-data` volume mounted at `/data`:

```
/data/hosted.sqlite3          users, orgs, sessions, API keys, audit, usage, encrypted credentials
/data/tenants/<org>/...       one workspace tree per customer
/data/artifacts/...           provider budget ledger and other default-path writes (/app/artifacts links here)
```

Architecture and module contracts: `docs/agere/HOSTED_DESIGN.md`.

## Data rule: synthetic data only

Until the items below are in place, load **synthetic or public data only**. No PHI, no customer extracts.

Taking real patient data would require:
- **BAA-eligible model endpoint.** A signed BAA with the model provider (Anthropic directly, or Claude through a
  BAA-covered cloud such as AWS Bedrock or Google Vertex AI), and `ANTHROPIC_API_KEY`/the gateway pointed at it.
- **HIPAA-ready hosting.** A BAA with the cloud host, encrypted disks and snapshots (the `/data` volume and backups),
  encrypted off-site backups, patched OS, restricted SSH.
- **Access logging.** The app audit log plus retained Caddy access logs and host logs, shipped somewhere tamper-evident.
- **SSO.** Org login through the customer's identity provider (SAML/OIDC) with MFA, instead of local passwords.

## 1. Server

Any Linux VM with Docker Engine and the compose plugin. Recommended: **4 vCPU / 16 GB RAM / 100 GB disk**
(building native workspaces from source extracts is the heavy part). Open inbound TCP 80 and 443 (and UDP 443 for
HTTP/3); keep 8080 closed.

## 2. DNS

Create an `A` record (and `AAAA` if the VM has IPv6) for your hostname, e.g. `agere.example.com`, pointing at the VM.
Caddy needs it resolving before first start to get the certificate.

## 3. Configure and start

```bash
git clone <repo> agere && cd agere/deploy
cp .env.example .env
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # -> AGERE_SECRET_KEY
$EDITOR .env          # AGERE_DOMAIN, AGERE_PUBLIC_URL, AGERE_SECRET_KEY, ANTHROPIC_API_KEY, TYPESAFE_API_KEY, PERPLEXITY_API_KEY
chmod 600 .env
docker compose up -d --build
docker compose ps     # agere should turn "healthy"
```

No local `cryptography`? Generate the key after the build instead:
`docker compose run --rm --no-deps agere python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

## 4. Bootstrap the first org and owner

```bash
docker compose exec agere python -m clinical_core.agere.hosted.auth bootstrap \
  --db /data/hosted.sqlite3 --org "Acme Health" --email admin@acme.example
```

It prints a one-time password. Sign in at `https://<domain>/login`, change it under Account, then add members and
API keys from Admin.

## 5. Smoke test

```bash
./smoke_hosted.sh https://agere.example.com
AGERE_SMOKE_KEY=agk_... ./smoke_hosted.sh https://agere.example.com   # also checks an API key
```

## Backups

Back up the database with SQLite's online backup (safe while running), then the tenant trees:

```bash
STAMP=$(date +%Y%m%d-%H%M%S)
docker compose exec agere python -c "import sqlite3; s=sqlite3.connect('/data/hosted.sqlite3'); d=sqlite3.connect('/data/hosted-backup.sqlite3'); s.backup(d); d.close(); s.close()"
docker compose cp agere:/data/hosted-backup.sqlite3 ./backup-$STAMP-hosted.sqlite3
docker compose exec agere rm /data/hosted-backup.sqlite3
docker compose exec -T agere tar -czf - -C /data tenants artifacts > ./backup-$STAMP-data.tgz
```

Copy both files off the VM (encrypted). Keep `.env` (it holds `AGERE_SECRET_KEY`) backed up separately: without it
the stored credentials in a restored database cannot be decrypted. Run it from cron daily.

Restore: `docker compose stop agere`, copy the `.sqlite3` back to `/data/hosted.sqlite3` and untar into `/data`
(e.g. with `docker compose run --rm --no-deps -v "$PWD":/backup agere sh -c 'cp /backup/<file>.sqlite3 /data/hosted.sqlite3 && tar -xzf /backup/<file>.tgz -C /data'`),
then `docker compose start agere`.

## Upgrades

```bash
cd agere && git pull
cd deploy && docker compose up -d --build
docker image prune -f
```

Take a backup first. The volume survives rebuilds; only the code image changes.

## Logs

```bash
docker compose logs -f agere     # app
docker compose logs -f caddy     # JSON access log + certificate events
```

Logs rotate at 20 MB x 5 files per container. App audit events (logins, key use, admin actions) are in
`hosted.sqlite3` and visible to org admins.

## Key rotation

- **ANTHROPIC_API_KEY** (hosted only): replace it in `.env` and `docker compose up -d agere`. Keep a monthly limit on it in the Anthropic console.
- **AGERE_SECRET_KEY** (credential encryption): changing it makes existing stored credentials unreadable. Plan it:
  note which orgs have stored warehouse credentials, set the new key in `.env`, `docker compose up -d agere`, and have
  each org admin re-enter its credentials. Keep the old key with the matching backups.
- **User API keys** (`agk_...`): revoke and reissue from Admin; they are stored hashed and shown once.
- **Sessions**: revoking a user's sessions or disabling the user takes effect on the next request.

## Customer data delivery

- **Uploads:** customers deliver extracts through the app (Workspaces → "Or upload extracts") or
  `PUT /api/uploads/<name>` with an API key (`curl -T extract.zip -H "Authorization: Bearer agk_..." https://$AGERE_DOMAIN/api/uploads/extract.zip`).
  Zips are extracted safely (no path traversal, links or decompression bombs) into the organization's own folder;
  the response names the workspace source to use (`native://...` for X12/HL7/NCPDP/FHIR/EHR feeds, `files://...` for
  plain tables).
- **Limits (environment):** `AGERE_UPLOAD_MAX_BYTES` (default 10 GB), `AGERE_UNZIP_MAX_BYTES` (40 GB),
  `AGERE_UNZIP_MAX_FILES` (500,000). Caddy allows large bodies only on `/api/uploads/*`.
- **Source rules:** a workspace reads a cloud warehouse (Snowflake, BigQuery, Databricks, Postgres) with credentials an
  admin saved, or files its own organization uploaded. Paths elsewhere on the server are refused, and Postgres hosts on
  private or loopback networks are refused unless `AGERE_ALLOW_PRIVATE_DB=1`.
- **Model-cost ledger:** `AGERE_LEDGER_PATH` (default `/data/provider-budget.sqlite3`).
