---
title: Installation
---

# Installation

## Requirements

- **Linux**, with Docker Engine; or **Windows 10 22H2 / 11** with WSL 2 (2.1.5 or later) and
  either Docker Desktop or Docker Engine inside WSL; or **macOS** with Docker Desktop.
- Docker Compose v2.24 or later, and Git.
- An x86-64 machine, or one that can emulate it: the published images are `linux/amd64`
  only. On Apple Silicon, turn on Rosetta emulation in Docker Desktop.
- Memory: no minimum is enforced. A VCF needs little; alignments need much more, and the
  stack allows the GATK service up to 28 GB and PyPGx up to 20 GB.
- Disk: about 2.5 GB of reference genomes, 5.3 GB for the CRAM reference cache, the images,
  and room for your inputs and reports. The FASTQ lane needs another 8-9 GB for its index.
- A network connection for the first start, which pulls the images and downloads the
  reference data.

## One-command setup

Linux, macOS or WSL:

```bash
curl -fsSL https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.sh | bash
```

Windows (PowerShell):

```powershell
iwr -useb https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.ps1 | iex
```

Alpine, as root (installs Bash and curl first):

```sh
apk add --no-cache bash curl && curl -fsSL https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.sh | bash
```

The script checks for Git, Docker and Compose and offers to install what is missing (apt,
dnf, yum, zypper, pacman, apk, winget or Chocolatey; on macOS it installs Git only). It
clones the repository into `./ZaroPGx`, checks out the release tag it names, and runs
`start-docker.sh --auto-local`, which:

1. copies `.env.local` to `.env`;
2. generates `SECRET_KEY` and `DB_PASSWORD`;
3. creates `data/` and gives the services that need it write access;
4. pulls the images and starts the stack.

Installing Docker may need you to log out and in again, and installing WSL a reboot. Run
the same command again afterwards. You can read the scripts first:
[bootstrap.sh](https://github.com/Zaromics/ZaroPGx/blob/main/bootstrap.sh),
[bootstrap.ps1](https://github.com/Zaromics/ZaroPGx/blob/main/bootstrap.ps1).

## Manual setup

```bash
git clone https://github.com/Zaromics/ZaroPGx.git
cd ZaroPGx
cp .env.local .env
./start-docker.sh          # Windows without WSL: .\start-docker.ps1
```

The three templates differ in a few values (all bind the app to `127.0.0.1:8765`):

| | `.env.local` | `.env.production` | `.env.example` |
|---|---|---|---|
| `LOG_LEVEL` | `DEBUG` | `INFO` | `DEBUG` |
| `NETWORK_SUBNET` | `172.20.0.0/16` | `172.28.0.0/16` | `172.20.0.0/16` |

`.env.example` explains each setting inline and has a few that the other two leave at
their defaults. See {doc}`configuration`.

To run Compose yourself instead of `start-docker.sh`, set `DB_PASSWORD` and `SECRET_KEY` in
`.env` first: the templates ship them blank, and Compose and the app refuse to start without
them. Do not change `DB_PASSWORD` once the database volume exists.

```bash
docker compose pull
docker compose up -d
```

To build the images from source, `docker compose up -d --build`. They are still built for
`linux/amd64`.

## First start

PharmCAT downloads its GRCh38 reference data (about 8-15 minutes), and the genome
downloader stages the reference genomes and builds the CRAM reference cache (a 3.2 GB
download). The app waits for PharmCAT to be healthy before it starts.

Then:

- the app: `http://localhost:8765`
- this documentation: `http://localhost:8765/docs`
- the API reference: `http://localhost:8765/api/docs`

To analyse FASTQ files, build the alignment index once, after the reference genomes are
staged: `./scripts/build-align-index.sh` (about an hour).

## Who can reach it

The app listens on `127.0.0.1:8765`: this machine only.

To run it on one machine and use it from others on your network (a desktop or home server
reached from a laptop, say), set `BIND_ADDRESS=0.0.0.0:8765` and run `docker compose up -d`;
then open `http://<that machine's address>:8765`. This is a supported way to run it. With
the default `ZAROPGX_AUTH_MODE=open` there is no password, so anyone who can reach the port
can upload data and read every report: on a network you do not fully trust, turn on the
shared password ({doc}`configuration`). To serve it beyond your network, see
{doc}`deployment`.

An install made before this default keeps the `BIND_ADDRESS` in its `.env`; check it.

The other services publish on `127.0.0.1` only (`INTERNAL_BIND_ADDRESS`) and do not
authenticate. Leave them there.

## Updating

Run the one-command setup again with `--update` (PowerShell: `-Update`), from the folder
that contains `ZaroPGx/`:

```bash
curl -fsSL https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.sh | bash -s -- --update
```

It refuses to update a checkout with local changes. On the next start, `start-docker.sh`
moves an older release's `ZAROPGX_TAG` in `.env` to the new release; `latest` and custom
tags are left alone. Read `UPGRADING.md` in the repository for what each release changes.

## Removing

```bash
docker compose down          # stop; keeps the database and downloaded data
docker compose down -v       # also deletes the database and PharmCAT's reference volume
```

Uploads and reports are under `data/`, reference genomes under `reference/`; delete their
contents to remove them.
