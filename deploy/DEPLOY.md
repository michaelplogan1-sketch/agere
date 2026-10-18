# Deploying hosted Agere

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
$EDITOR .env          # AGERE_DOMAIN, AGERE_PUBLIC_URL, AGERE_SECRET_KEY, ANTHROPIC_API_KEY
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

- **ANTHROPIC_API_KEY**: edit `.env`, then `docker compose up -d agere`.
- **AGERE_SECRET_KEY** (credential encryption): changing it makes existing stored credentials unreadable. Plan it:
  note which orgs have stored warehouse credentials, set the new key in `.env`, `docker compose up -d agere`, and have
  each org admin re-enter its credentials. Keep the old key with the matching backups.
- **User API keys** (`agk_...`): revoke and reissue from Admin; they are stored hashed and shown once.
- **Sessions**: revoking a user's sessions or disabling the user takes effect on the next request.
