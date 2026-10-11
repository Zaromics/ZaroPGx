---
title: Running a server
curation: not
---

# Running a server

ZaroPGx has no user accounts. The most it offers is one shared password for the whole
instance (`ZAROPGX_AUTH_MODE=password`), and anyone who has it can read every report.
It does not terminate TLS. To serve it beyond your own machine, put it behind a reverse
proxy that does.

## Setup

```bash
git clone https://github.com/Zaromics/ZaroPGx.git
cd ZaroPGx
cp .env.production .env
```

In `.env`:

```bash
BIND_ADDRESS=127.0.0.1:8765      # the template's default: only the proxy reaches the app
ZAROPGX_AUTH_MODE=password
ZAROPGX_AUTH_PASSWORD=<a long random password>
```

Then `./start-docker.sh`. Leave `INTERNAL_BIND_ADDRESS=127.0.0.1`: the other services do
not authenticate, the database included.

## Reverse proxy

The proxy terminates TLS and forwards to `127.0.0.1:8765`. Two settings matter for
ZaroPGx: allow request bodies as large as the files you will upload (BAMs and CRAMs run to
tens of GB), and keep the request timeout long enough for such an upload to finish.

Caddy:

```text
pgx.example.org {
    request_body {
        max_size 100GB
    }
    reverse_proxy 127.0.0.1:8765
}
```

nginx:

```nginx
server {
    listen 443 ssl;
    server_name pgx.example.org;
    # ssl_certificate ...; ssl_certificate_key ...;
    client_max_body_size 100g;
    proxy_request_buffering off;
    proxy_read_timeout 3600s;
    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

The `Upgrade` headers carry the WebSocket the upload page uses for live progress.

## What leaves the machine

Analyses run on the host; uploads and reports stay in `data/`. Outside connections:

- At install and update: the repository from GitHub, the images from Docker Hub.
- On first start: PharmCAT's reference data from Zenodo, and reference genomes and chain
  files for the genome downloader.

The app's pages, its API explorer and the reports load nothing from other hosts: their
scripts, styles and fonts are served by the app or carried in the report.

## Backups

The database holds the job records and the loaded PharmCAT results; `data/reports/` holds
the reports themselves. Back up both.

A dump, with the stack running:

```bash
docker compose exec -T db pg_dump -U zaropgx_user zaropgx_db > zaropgx.sql
```

Or a copy of the volume's files: `./scripts/db-backup.sh` stops the database container and
copies the `zaropgx_pgdata` volume into `./db/data`; `./scripts/db-restore.sh` copies it
back. Start the database again afterwards with `docker compose up -d`.

## Resources

The heavy work runs in `gatk-api` (memory limit 28 GB), `pypgx` (20 GB) and `zarohla`
(HLA typing; its scratch, `ZAROPGX_SCRATCH`, needs tens of GB for a whole genome). VCF
analyses are light. Uploads are deleted when their job ends unless `KEEP_UPLOADS=true`;
reports are kept until you delete them.

## Logs

`docker compose logs -f <service>`. Each pipeline service also writes a progress log to
`data/` (`pharmcat_progress.log`, `pypgx_progress.log` and so on), and each job's directory
under `data/reports/` keeps Nextflow's `report.html`, `timeline.html` and `trace.txt`.

## Upgrading

See {doc}`installation` and `UPGRADING.md` in the repository.
