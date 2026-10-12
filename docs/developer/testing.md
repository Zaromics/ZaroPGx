---
title: Testing
---

# Testing

## The unit and API suite

```bash
uv run pytest -q -m "not e2e"
```

It needs Docker: `tests/postgres.py` starts a throwaway `postgres:18.6` container, its data on
tmpfs, built from `db/init` exactly as production is, and empties the tables each test wrote
to after it. To use a database you already run, set `ZAROPGX_TEST_POSTGRES_URL` (the role must
be `zaropgx_user`); the schema is built on it if missing. Under `CI=true` a missing database is
an error, not a skip.

Fixtures from `tests/conftest.py`: `db_session` and `session_factory` (SQLAlchemy sessions on
that database) and `client` (a FastAPI `TestClient` with the database dependency overridden).

Tests that need Node 22 (two that run the interactive report's JavaScript) or Pango and the
Liberation fonts (the WeasyPrint layout tests) skip without them. CI installs both.

Use the public fixtures in `test_data/` (described in `test_data/README.md`): PharmCAT's
example VCFs, GRCh37 and T2T SNP sets for the liftover and refusal paths, and small BAM, CRAM
and SAM files over the pharmacogenes and HLA. Never commit a real person's genotypes.

## The end-to-end suite

```bash
./scripts/e2e.sh
```

This brings up a separate stack (Compose project `zaropgx_e2e`, app on `127.0.0.1:18765`)
from `compose.yml` plus `compose.e2e.yml`, waits for it to be healthy (up to
`E2E_HEALTH_TIMEOUT` seconds, default 1500), then runs `pytest -m e2e --zaropgx-e2e`
against it. On success it tears the stack down; on failure it leaves it up and writes
`e2e-logs/compose.log`. Tear it down afterwards with `./scripts/e2e-down.sh`.

`scripts/e2e-env.sh` writes `.env.e2e` on the first run and reuses it after. The e2e stack
turns the mtDNA step and the CRAM reference cache off.

| Test | Input | Runs when |
|---|---|---|
| `test_vcf_pipeline` | `test_data/pharmcat.example.vcf` | always |
| `test_bam_pipeline`, `test_alignment_conversion_pipeline` | BAM, CRAM, SAM | `ZAROPGX_E2E_REFERENCE=1` |
| `test_bam_hla_pipeline` | BAM with HLA reads | `ZAROPGX_E2E_REFERENCE=1` |
| `test_grch37_liftover_pipeline` | GRCh37 VCF | `ZAROPGX_E2E_REFERENCE=1` |

The reference lanes need a populated `./reference` (the genome downloader's). Each test also
checks that every report link the job returns resolves. FASTQ and mtDNA have no end-to-end
test.

PharmCAT downloads its 844 MB GRCh38 reference on first start, into a volume the harness
deletes on success. To skip the download, point `E2E_PHARMCAT_REFERENCE` at a directory that
holds `reference.fna.bgz` and its `.fai` and `.gzi`.

## CI

`.github/workflows/ci.yml` runs on every push and pull request:

- **Lint**: black, isort and flake8 at the versions in {doc}`development-setup`.
- **Tests**: the unit suite, with Node 22, Pango and the Liberation fonts installed.
- **E2E (compose)**: the VCF lane. It keeps PharmCAT's reference in the Actions cache and
  downloads it from Zenodo only when the cache is empty.
