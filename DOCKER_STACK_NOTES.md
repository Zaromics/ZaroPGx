# Docker Stack — Maintenance Notes

> Created 2026-05-28. Reconciled 2026-05-29. **Refreshed 2026-06-08** (core dependency
> refresh + `cpic`→`zaropgx` finalization + ZaroHLA fix). Dev environment.

## ✅ CURRENT STATE (2026-06-08)

Core stack rebuilt and healthy on refreshed versions (WSL-native docker):

| Service | Image / version | Notes |
|---|---|---|
| `pgx_db` | **postgres:18** | Fresh DB; data volume mounted at `/var/lib/postgresql` (PG18 layout) |
| `pgx_pharmcat` | **PharmCAT 3.2.0** | PharmVar + ClinPGx data refresh |
| `pgx_zarohla` | **ZaroHLA / OptiType v1.5** | Active HLA path on `:5060`; paired-end typing verified |
| `pgx_gatk_api` | GATK 4.6.2.0 | Uses the `./reference` bind mount |
| `pgx_app` | app | DB connects as `zaropgx_user` |

Other refreshed versions: htslib/bcftools **1.23.1** (pinned release tarballs in the main
image + the pharmcat container), PyPGx pinned to **0.26.0**.

## ✅ RESOLVED (this refresh)

- **`cpic_*` → `zaropgx_*` rename finished.** `compose.yml` defaults + the active `.env`
  now use `zaropgx_user`/`zaropgx_db`. This **fixed the db-init GRANT failure**: the
  `zaropgx_user` role is now created by the postgres entrypoint (it is `POSTGRES_USER`),
  so every `GRANT … TO zaropgx_user` succeeds. Fresh DB init runs clean.
- **PostgreSQL 17 → 18.** The DB was wiped (authorized) and re-seeded. ⚠️ PG18's Docker
  image stores data in a major-version subdir and **rejects a mount at
  `/var/lib/postgresql/data`** — the volume is now mounted at `/var/lib/postgresql`.
- **ZaroHLA wired + fixed.** Added the `zarohla` service to compose (it was built but
  never wired in). Fixed a real bug: the OptiType v1.5 CLI needs each paired-end file as
  its own `-i` (the wrapper appended the 2nd FASTQ as a bare positional → every
  paired-end/BAM run failed with "unexpected extra argument"). Also set `HOME`/
  `MPLCONFIGDIR` (the `zarouser` account has no home dir, which tripped matplotlib).
- **nf-core `hlatyping` service disabled.** Its build context `docker/hlatyping/` does
  not exist, so it could never build (a latent `compose build` landmine). Commented out
  as a restorable placeholder (kept the 2.2.0 pipeline version + the Nextflow ≥25.04.2
  note for whoever restores it).

## Still open (dev-only, intentional)

- Optional services down: `pypgx`, `genome-downloader`, `fhir-server`, `kroki`+`mermaid`,
  `nextflow`, `docs`. The app is healthy without them (degraded; its startup readiness
  loop logs warnings for each). Bring up with `docker compose up -d <svc>`
  (`gatk-api`/`pypgx` depend on `genome-downloader`; use `--no-deps` when `./reference`
  is already populated).
- `docker/hlatyping/` Dockerfile is missing — restore it to re-enable the nf-core path.
- `test_data/test.bam` and `test_data/NA12878.mini.bam` are **not real BAMs** — both are
  ~270 KB GitHub HTML pages (bad downloads). Replace with real BAM/FASTQ before relying on
  them as fixtures. ZaroHLA was verified with OptiType's `NA11995_*_fished.fastq` exome reads.

## Rebuild recipe (WSL docker)

```bash
# from the repo root, on the WSL-native docker engine
docker compose down -v --remove-orphans          # wipes pgdata + pharmcat-references (both required for upgrades)
docker compose up -d --build db pharmcat app zarohla
docker compose up -d --wait --no-deps gatk-api   # reuses ./reference, skips genome-downloader
```

Reports/uploads (`./data`) and the genome (`./reference`) are **bind mounts → survive
`down -v`**. PharmCAT re-downloads the GRCh38 reference (~8 min) on first start after the
`pharmcat-references` volume is wiped. The volume also shadows `/pharmcat`, so it **must**
be wiped for a PharmCAT version bump to actually take effect.

## Note on Docker engines

The `pgx_*` containers run on **WSL-native docker** (`wsl docker` / default context),
NOT Docker Desktop. (The Docker Desktop engine hosts a separate ollama/docs-mcp stack.)
