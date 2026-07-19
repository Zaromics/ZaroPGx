# Upgrading ZaroPGx

Changes that need action on an existing install. Newest first. If a version is not listed,
upgrading to it needs nothing beyond `git pull` and `docker compose up -d`.

## Unreleased

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
