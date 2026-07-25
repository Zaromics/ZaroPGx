# Upgrading ZaroPGx

Changes that need action on an existing install. Newest first. If a version is not listed,
upgrading to it needs nothing beyond `git pull` and `docker compose up -d`.

## Unreleased

### Full-stack e2e harness (developers / CI)

Local and CI now share `./scripts/e2e.sh` / `./scripts/e2e-up.sh` against isolated
compose project `zaropgx_e2e` on host port `18765` (auth open). Fast pytest excludes
`@pytest.mark.e2e`; the e2e job builds via Buildx with GHA cache. No action required
for runtime installs — this is a test/CI change only.

### Auth gate defaults to open (no behaviour change)

A front-door ASGI gate is installed (`ZAROPGX_AUTH_MODE=open|audit|password`,
default `open`). At default-open, existing installs are unchanged after
`git pull && docker compose up`. `ZAROPGX_DEV_MODE=false` does **not** turn
auth on — it logs a warning naming `ZAROPGX_AUTH_MODE`. To enforce the gate,
set `ZAROPGX_AUTH_MODE=password` and `ZAROPGX_AUTH_PASSWORD` in `.env`.

The gate is a shared install password (cookie `SameSite=Lax` or
`Authorization: Bearer` with a `gate=true` JWT, or the raw password as Bearer).
Anyone past it can still fetch any patient report; there is no per-user access
control yet.

**Password mode is a front door, not a full ACL.** These stay reachable without
the install password by design for in-stack callers:

- `/api/v1/workflows/*` (including WebSocket status)
- `/health`, docs/static, `/login`, `/logout`, `/token`

`/token` in password mode requires `ZAROPGX_AUTH_PASSWORD` and returns a
`gate=true` JWT. The legacy `test`/`test` credentials still work in open/audit
modes but those JWTs **cannot** unlock password mode.

If the app is published beyond localhost (`BIND_ADDRESS=0.0.0.0:8765`), treat
the workflow allowlist as part of your threat model until service-to-service
credentials land.

### Config delivery: `.env` owns behaviour, compose owns topology

The app service now declares `env_file: .env` (Compose >= 2.24). Behavioural
toggles such as `INCLUDE_PHARMCAT_JSON` / `INCLUDE_PHARMCAT_TSV` are no longer
hardcoded in `compose.yml`, so values in your `.env` take effect. Tracked
profiles set those to `true` (matching the previous compose overrides).
`KROKI_URL` was removed from the env templates — compose always uses
`http://kroki:8000` inside the stack.

**What to do:** after `git pull`, re-copy or merge the new defaults if you still
have `INCLUDE_PHARMCAT_JSON=false` from an older profile and want the JSON/TSV
artifacts in reports. CORS for the live reference instance is
`pgx.zaromics.com` (replacing the retired zimerguz hostname).

### Per-install secrets; missing `DB_PASSWORD` is a hard compose failure

`compose.yml` no longer falls back to `test123`. If `DB_PASSWORD` is unset,
`docker compose up` fails at parse time with an error that names the fix.
Tracked `.env.local` / `.env.production` / `.env.example` ship blank `SECRET_KEY`
and `DB_PASSWORD`; `start-docker.sh` / `start-docker.ps1` generate unique values
into `.env` on first run.

**What to do:**

- Prefer `./start-docker.sh` or `./start-docker.ps1` — they create `.env` and fill
  secrets automatically.
- If you manage `.env` yourself, set a unique `DB_PASSWORD` and `SECRET_KEY` before
  `docker compose up`.
- **Existing Postgres volume:** do not invent a new `DB_PASSWORD`. Postgres only
  applies `POSTGRES_PASSWORD` when the data directory is empty. Keep the password
  that initialized the volume, or rotate with `ALTER USER` and then update `.env`.
  The start scripts refuse to overwrite a blank/placeholder password when
  `zaropgx_pgdata` (or legacy `pgx_pgdata`) already exists.

### `compose.yml` is now tracked in git

Previously the compose file was gitignored and `start-docker` copied `docker-compose.yml.example`
into place **only when no compose file existed**. That meant your compose file was frozen at
whatever it copied on first run — no `git pull` ever updated it, so compose-level fixes never
reached you.

`compose.yml` is now tracked and updates normally. `docker-compose.yml.example` is gone.

**What to do:**

- If you never edited your compose file, nothing — `git pull` brings the tracked one.
- If `git pull` refuses with *"untracked working tree files would be overwritten"*:
  ```bash
  mv compose.yml compose.yml.mine
  git pull
  ```
  then move any settings you actually changed into `compose.override.yml` (see below).
- **If you have a `docker-compose.yml`**, Compose prefers `compose.yml`, so your old file and every
  edit in it is now silently ignored. `start-docker` warns about this. Move your customizations:
  ```bash
  mv docker-compose.yml compose.override.yml
  ```
  then trim the override down to only the keys you changed — Compose merges it automatically, no
  flags needed.

Do not edit `compose.yml` directly any more; it will conflict on the next pull. Put local changes in
`compose.override.yml`, which is gitignored:

```yaml
# compose.override.yml
services:
  app:
    ports:
      - "9000:8000"
```

### Internal service ports are bound to localhost

The database, PharmCAT, GATK, PyPGx, ZaroHLA, genome-downloader, HAPI FHIR, Kroki and the docs
server were published on **all** network interfaces. None of them authenticate, and the database
shipped with a password published in this repository, so on any machine reachable from a network
they were open to it.

They are now bound to `127.0.0.1`. Nextflow is no longer published to the host at all — its
`POST /run` is unauthenticated and the service bind-mounts the Docker socket, which together make a
host mapping remote code execution.

**What still works, unchanged:** everything inside the stack (services talk over the Compose
network, which never used host ports), and every `curl http://localhost:5001/health`-style command
run **on the Docker host**.

**What breaks:** connecting to those ports from another machine — e.g. pgAdmin or DBeaver pointed at
`your-server:5444`.

**What to do:** prefer an SSH tunnel, which needs no configuration change:

```bash
ssh -L 5444:127.0.0.1:5444 your-server   # then connect to localhost:5444
```

If you genuinely need direct exposure, set it in `.env` and understand what you are opening:

```bash
INTERNAL_BIND_ADDRESS=0.0.0.0
```

The app itself is unaffected — `BIND_ADDRESS` still governs it, and `BIND_ADDRESS=0.0.0.0:8765`
still serves the LAN.

## v0.2.4 → v0.2.5

### Recover an existing database after the credential rename

Commit `4f01a76` changed the defaults from `cpic_user`/`cpic_db` to
`zaropgx_user`/`zaropgx_db`, but it did not rename roles or databases in existing volumes.
PostgreSQL uses `POSTGRES_USER` and `POSTGRES_DB` only while initializing an empty data
directory, so an existing v0.2.4 volume still has the legacy names.

**Zero-risk compatibility option:** keep using the names that already exist. Set these values in
`.env` and leave `DB_PASSWORD` equal to the password that initialized the volume:

```bash
DB_USER=cpic_user
DB_NAME=cpic_db
```

This changes no database data or catalog objects. The names can remain in place indefinitely.

**Clean hand-run rename:** use the recovery script when you want the existing volume to match the
current defaults. The script is never called by Compose or application startup. It is
idempotent, refuses ambiguous role/database states, and leaves an active database untouched
rather than terminating clients.

The credential rename is separate from a PostgreSQL major-version upgrade. Run these steps while
the volume is served by the PostgreSQL major version that created it. In particular, do not attach
a PostgreSQL 17 data directory directly to `postgres:18`; use `pg_upgrade` or dump/restore for
that separate transition.

```bash
# 1. Start the legacy volume with names that actually exist.
#    Keep its original DB_PASSWORD in .env.
docker compose up -d db

# 2. Take a logical backup of every database and role before changing catalog names.
docker compose exec -T db pg_dumpall -U cpic_user \
  > zaropgx-before-credential-rename.sql

# 3. Stop known database clients. Also disconnect external SQL clients.
docker compose stop app fhir-server nextflow

# 4. Rename the role and database. Safe to run again after a partial or completed run.
bash scripts/fix-legacy-credentials.sh
```

Then change `.env` to the current names and restart:

```bash
DB_USER=zaropgx_user
DB_NAME=zaropgx_db

docker compose up -d
```
