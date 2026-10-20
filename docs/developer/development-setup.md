---
title: Development setup
---

# Development setup

## Requirements

- Python 3.12 (exactly: `pyproject.toml` requires `>=3.12,<3.13`) and
  [uv](https://docs.astral.sh/uv/) 0.12.23 or newer (`pyproject.toml`'s
  `required-version`; CI and the images use exactly 0.12.23).
- Docker with Compose v2.24 or later. The test suite starts a PostgreSQL container, and the
  stack runs in Docker.
- Git.
- Optional: Node 22, for two tests that run the interactive report's JavaScript; Pango and
  the Liberation fonts, for the tests that render PDFs with WeasyPrint. Without them those
  tests are skipped.

```bash
git clone https://github.com/Zaromics/ZaroPGx.git
cd ZaroPGx
uv python install 3.12
uv sync --extra dev
```

## Running the stack

```bash
cp .env.local .env
./start-docker.sh
```

`start-docker.sh` generates `SECRET_KEY` and `DB_PASSWORD` (the templates ship them blank,
and Compose will not start without a password), prepares `data/` and its permissions, pulls
the images and starts the stack. The app is at `http://localhost:8765`.

The `app` and `nextflow` containers mount the working tree (`./` at `/app` and
`/workspace`), so they run your checked-out code:

- App code: `docker compose restart app`.
- `pipelines/pgx/main.nf`: picked up by the next job.
- Anything under `docker/<service>/`: that code is built into the image, so rebuild it,
  e.g. `docker compose up -d --build pharmcat`.

Put local changes to `compose.yml` in `compose.override.yml` (gitignored), which Compose
merges in. Running the app outside Docker does not give a working pipeline: Nextflow is
not published to the host and reports back to `http://app:8000`.

The FASTQ lane needs an alignment index on the host. Build it once with
`scripts/build-align-index.sh` (about an hour, 8-9 GB; it needs the hg38 reference the
genome downloader stages).

## Lint, as CI runs it

```bash
uv tool run black==26.10.0 --check app tests
uv tool run isort==9.0.2 --check-only --profile black app tests
uv tool run flake8==7.4.1 --select=E9,F63,F7,F82 app tests
```

The versions are pinned to `uv.lock`; `tests/test_ci_lint_pins.py` fails if they drift.
CI also runs a full `flake8 app tests` for information; it does not fail the build.

## Tests

```bash
uv run pytest -q -m "not e2e"
```

The suite runs against PostgreSQL, never SQLite: `tests/postgres.py` starts a throwaway
`postgres:18.6` container built from `db/init`, or uses the database at
`ZAROPGX_TEST_POSTGRES_URL`. See {doc}`testing` for the end-to-end suite.

## Database changes

Fresh databases are built from `db/init/00_complete_database_schema.sql`; existing ones are
brought up to date at app startup by `app/api/db_migrations.py`, which applies each file in
`db/init/migrations/` once and records it in `schema_migrations`. For a schema change:

1. Change `00_complete_database_schema.sql`, so fresh databases get it.
2. Add the next numbered file, e.g. `db/init/migrations/07_<what>.sql`. It must be
   idempotent: it also runs on fresh databases, which already have the change. Keep to
   plain statements; the applier splits the file on `;`.

## Documentation

The docs are Sphinx with MyST Markdown, in `docs/`.

```bash
uv run --no-project --with-requirements docs/requirements.txt \
  sphinx-build -W -b html docs docs/_build/html
```

`-W` turns warnings into errors, as Read the Docs does (`.readthedocs.yaml`). For a
live-reloading preview, `make -C docs live` (port 8000), or start the `docs` service:
`docker compose --profile optional up -d docs` (127.0.0.1:5070). The app serves
`docs/_build/html` at `/docs` and builds it at startup if it is missing.

Every page shows a notice that it was composed, in part or in whole, with generative AI
(`docs/_ext/ai_disclaimer.py`). A page the maintainer has curated personally carries
`curated: true` in its front matter, which drops the notice; nothing else shows it.
