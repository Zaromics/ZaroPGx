---
title: Testing Guide
curation: partial
---

# Testing Guide

How ZaroPGx is tested today. Everything below is derived from `pyproject.toml`, the `tests/`
tree and `.github/workflows/ci.yml` — if this page and those files disagree, they are right.

## Two suites

There are exactly two, split by the `e2e` pytest marker:

| Suite | Selector | Needs Docker | What it exercises |
|---|---|---|---|
| **Fast** | `-m "not e2e"` | no | Pure functions, parsers, FastAPI routes via `TestClient`, contract assertions over `compose.yml` |
| **Full-stack e2e** | `-m e2e` | yes | A real Compose stack: upload a VCF, wait for the workflow, assert a report artifact exists |

The fast suite is the one you run while working. It builds its database as an **in-memory
SQLite** engine (`tests/conftest.py`) rather than talking to Postgres, so it needs no services
at all.

## Running the tests

**Fast suite:**

```bash
uv run pytest -q -m "not e2e"
```

On Windows, prefer the project venv — `uv run` will otherwise try to rebuild `pysam`, which has
no wheel for every platform:

```powershell
.venv\Scripts\python.exe -m pytest -q -m "not e2e"
```

```bash
# Unix equivalent
.venv/bin/python -m pytest -q -m "not e2e"
```

**Full-stack e2e** — brings the stack up, runs the marked tests, tears down on success:

```bash
./scripts/e2e.sh
```

Use Git Bash or WSL on Windows. Give Docker Desktop plenty of RAM (16 GB+). The first run
builds images cold and downloads PharmCAT's GRCh38 reference, so budget a long wait; later runs
reuse the BuildKit cache. **On failure the stack is deliberately left up** and the combined
logs are written to `e2e-logs/compose.log`.

The e2e stack is fully isolated from a developer stack: Compose project `zaropgx_e2e`,
`--env-file .env.e2e`, `-f compose.yml -f compose.e2e.yml`, and only the app published, at
`127.0.0.1:18765`. `scripts/e2e-up.sh` and `scripts/e2e-down.sh` are the two halves if you want
to drive them separately (`scripts/e2e-up.ps1` is the PowerShell counterpart).

## Layout

```
tests/
├── conftest.py                 # the only shared fixture module for the fast suite
├── e2e/
│   ├── conftest.py             # e2e_base_url / e2e_client fixtures (skip when not enabled)
│   ├── harness.py              # enable-flag plumbing shared with the root conftest
│   └── test_vcf_pipeline.py    # upload → poll → assert a report artifact
├── run_workflow_tests.py       # optional convenience runner for the workflow modules
└── test_*.py                   # flat modules, one per behaviour under test
```

There are no `unit/`, `integration/`, `performance/` or `fixtures/` subdirectories, and no
generated test-data tree — the modules are flat and named for what they pin down
(`test_tsv_parser.py`, `test_report_path_jail.py`, `test_compose_contract.py`,
`test_workflow_progress_renormalize_58.py`, …). Sample inputs live in `test_data/` at the repo
root; the e2e test uploads `test_data/pharmcat.example.vcf`.

## Configuration

There is no `pytest.ini`. All pytest configuration lives in `[tool.pytest.ini_options]` in
`pyproject.toml`:

- `testpaths = ["tests"]` — a bare `pytest` collects only that tree.
- `pythonpath = ["."]` — `tests/` has no `__init__.py` at the top level, so without this a bare
  `pytest` from the repo root cannot `import app.main`.
- `asyncio_mode = "strict"` — every async test carries an explicit `@pytest.mark.asyncio`.
  `auto` would also hijack the sync tests that drive `asyncio.run()` themselves.
- `addopts = "--strict-markers"` — an unregistered marker is an error, not a silent skip.
- `markers` — only two are registered: `asyncio` and `e2e`.
- `norecursedirs` — restates pytest's defaults (setting it *replaces* them) plus `.venv`,
  `reference`, `test_data`, `_build`, `work`.

Coverage is available (`pytest-cov` is in the `dev` extra) but **no coverage threshold is
enforced** anywhere — not in `addopts`, not in CI.

## Fixtures

`tests/conftest.py` is the whole fixture story for the fast suite:

- It sets `ZAROPGX_DEV_MODE`, `FHIR_EXPORT_ENABLED`, `SECRET_KEY`, `DATABASE_URL` and
  `DB_PASSWORD` **before** importing anything from `app.*`, because `app.main` and `app.api.db`
  read configuration at import time.
- `engine` (session-scoped) — an in-memory SQLite engine on a `StaticPool`. SQLite has no
  `CREATE SCHEMA`, so each Postgres schema the ORM models are qualified with is `ATTACH`ed as
  its own in-memory database; `Base.metadata.create_all()` then works unmodified.
- `database` (autouse) — creates every table before each test and drops them after.
- `db_session`, `job_service`, `connection_manager` — a session and the two services most tests
  need.
- `override_db_dependency` (autouse) — points FastAPI's `get_db` at the test session and unwinds
  afterwards, so it cannot leak between modules.
- `client` — a `TestClient` with the app's startup/shutdown hooks cleared (they reach for
  Postgres and sibling containers).

`tests/e2e/` adds `e2e_base_url` and `e2e_client`, which **skip** unless e2e was explicitly
enabled, and skip again if `/health` is not reachable.

### The e2e enable flag

e2e is opt-in via `ZAROPGX_E2E=1` **or** `--zaropgx-e2e`. The CLI flag exists because on
Windows/Git Bash a shell `export` frequently does not reach a Win32 `python.exe`, which used to
turn a whole e2e run into a silent all-skipped pass. `pytest_sessionfinish` in the root
conftest guards that case: if e2e was requested and zero tests passed, the run is failed with an
explanatory message instead of exiting green.

## Continuous integration

`.github/workflows/ci.yml` runs on every push to any branch, on pull requests, and on manual
dispatch, with in-progress runs cancelled per ref. Three jobs:

**`lint`** (10 min) — installs `black`, `isort` and `flake8` as standalone `uv` tools rather
than syncing the project (the full dependency set needs `pysam`, which is irrelevant to
formatting), then:

```bash
black --check app tests
isort --check-only --profile black app tests
flake8 --select=E9,F63,F7,F82 app tests      # blocking
flake8 app tests || true                     # informational only
```

The blocking selector is deliberately narrow: syntax errors, broken asserts and undefined
names. A full `flake8` still reports many style findings (mostly `E501`); it runs with
`|| true` so a non-zero exit does not paint a red annotation on a green job.

**`test`** (20 min) — `uv python install 3.12`, `uv sync --frozen --extra dev`, then
`uv run pytest -q -m "not e2e"`. No service containers: the fast suite is SQLite-backed.

**`e2e`** (90 min) — sets up Buildx (with `actions: write` so it can persist a `type=gha`
cache), syncs dependencies, runs `./scripts/e2e-up.sh`, then
`uv run pytest -m e2e -q --tb=short --zaropgx-e2e` against `http://127.0.0.1:18765`. On failure
it dumps Compose logs and uploads them as the `e2e-logs` artifact; teardown runs `if: always()`.

`mypy` is configured in `pyproject.toml` but is **not** part of the CI gate — the settings there
are an adoption baseline, not a ratchet. The only other workflow in the repo is
`render-diagrams.yml`, which re-renders `app/visualizations/**/*.mmd` and is unrelated to tests.

## What is not covered

Being explicit so nobody assumes otherwise:

- **No coverage gate.** `pytest-cov` is installed; nothing fails on low coverage.
- **No performance or load tests.** No timing or memory assertions exist anywhere.
- **No PharmCAT / PyPGx / GATK service doubles.** The fast suite never starts those services;
  anything that needs them is e2e or untested.
- **A single e2e case.** `test_vcf_pipeline.py` covers one GRCh38 VCF upload through to a report
  artifact, with GATK and OptiType switched off. BAM/CRAM/FASTQ paths have no automated
  coverage.
- **No frontend tests.** The web UI's JavaScript is untested.
- **No database-integration tests against real Postgres.** The fast suite is SQLite; the e2e
  stack exercises Postgres only incidentally.

## Adding a test

1. Put it in a flat `tests/test_<behaviour>.py` module. Name the module for the behaviour, not
   the source file.
2. Use the existing fixtures (`client`, `db_session`, `job_service`) rather than building your
   own engine.
3. Async tests need an explicit `@pytest.mark.asyncio` — `asyncio_mode` is `strict`.
4. Anything that needs a live stack belongs under `tests/e2e/` and must carry
   `@pytest.mark.e2e`, or it will run in the fast suite and in CI's `test` job.
5. Only `asyncio` and `e2e` are registered markers. `--strict-markers` makes a new one an error,
   so register it in `pyproject.toml` first.
6. Run `black app tests` and `isort --profile black app tests` before pushing; CI checks both.

## Next Steps

- **Development Setup**: {doc}`development-setup`
- **Architecture**: {doc}`architecture`
- **API Reference**: {doc}`api-reference`
- **Contributing**: {doc}`contributing`
