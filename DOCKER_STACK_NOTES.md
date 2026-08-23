# Docker Stack — Maintenance Notes

> Created 2026-05-28. Reconciled 2026-05-29. **Refreshed 2026-06-08** (core dependency
> refresh + `cpic`→`zaropgx` finalization + ZaroHLA fix). **Refreshed 2026-07-11**
> (PharmCAT 3.2.0→3.3.0; htslib/bcftools/samtools 1.23.1→1.23.2 security patch).
> **Refreshed 2026-07-14** (PharmCAT 3.3.0→3.4.0 + htslib/bcftools/samtools 1.23.2→1.24,
> adopting PharmCAT 3.4.0's newly recommended bcftools. 1.24 also drops the experimental,
> unadopted CRAM v4 codec for security — no impact here: ZaroPGx's only CRAM→BAM step uses
> the GATK image's own samtools, not these pinned builds). Dev environment.

## 2026-08-23 refresh — v0.3.0 rebuild + first live PyPGx run

All six images rebuilt as **0.3.0** on the WSL-native engine (security + I/O fixes from
the 2026-08-08 and 2026-08-22 sessions; the app-layer half is live on restart via the
`./:/app` mount, the sidecar half needs these rebuilds). The stack was fully **down**
when this started; bringing it back up surfaced several pre-existing deployment issues,
now fixed:

- **The native docker socket had been hijacked by Docker Desktop.** `/run/docker.sock`
  was owned by `docker-desktop-`, so `docker` inside Ubuntu-22.04 reached Desktop. A
  systemd `docker.socket` restart reclaimed it for the native `dockerd` (a secondary
  `ListenStream=/run/docker-native.sock` drop-in was also added as an explicit native
  handle). Verify with `ss -lnxp | grep docker.sock` (see the engine note below).
- **`.env` still held the placeholder `SECRET_KEY`** (`supersecretkey_for_development`),
  which now hard-fails app startup. Generated a per-install key (`start-docker.sh` does
  this automatically; the sentinel list is in that script).
- **The `zaropgx_pgdata` volume carried a stale pre-`jobs` schema.** Postgres only runs
  `db/init/*.sql` on a *fresh* data dir, so this April-created volume never got the
  job-based schema — every upload 500'd with `relation "jobs" does not exist`. Reset
  the DB volume (backed up first with `pg_dumpall`; the old data was 3 dev workflows)
  and let postgres re-init the current schema. **Any deployment on an old volume needs
  this reset** — it is the documented `down -v` step, but you can do just pgdata:
  `docker compose rm -sf db app && docker volume rm zaropgx_pgdata && docker compose up -d db`
  (leaves `pharmcat-references` intact, no GRCh38 re-download).
- **The PyPGx pipeline had never run end-to-end**, and the first live VCF run found two
  blockers (now fixed in `docker/pypgx/`): the `run-ngs-pipeline` path passed the
  caller's build wording (`hg38`) straight to PyPGx's `--assembly`, which accepts only
  `GRCh37`/`GRCh38` (→ `KeyError: 'hg38Region'`); and the image pulled **pandas 3.x**,
  which pypgx 0.26.0 breaks on (`ValueError: setting an array element with a sequence`)
  — pandas is now pinned `==2.2.3`. A per-gene error was also logged as the stderr
  *stream object* instead of its text, masking both.
- **Full VCF e2e is now GREEN** (first successful live pipeline run). Getting there past
  the assembly+pandas fixes took four more pre-existing fixes, each only reachable once
  the prior cleared: PyPGx wants Ensembl-style unprefixed contigs, so a chr-prefixed VCF
  (GATK/UCSC, and PharmCAT's own example) is now renamed before PyPGx; a gene whose
  chromosome carries no variants is treated as no-data, not a failure; a single gene
  error no longer fails the 68-gene step (systemic-only failure); and datetimes are
  coerced out of the JSON metadata columns (the run finished report generation but was
  marked failed on `Object of type datetime is not JSON serializable`). Verify:
  `ZAROPGX_E2E_BASE_URL=http://127.0.0.1:8765 .venv/Scripts/python.exe -m pytest -m e2e`
  → 1 passed; the job reaches `completed` 4/4 with real HTML/PDF/PharmCAT/FHIR artifacts.
  Residual (non-blocking): one gene hits a PyPGx-internal `IndexError` and is recorded as
  a per-gene failure; report-time `PyPGx per-gene enrichment skipped` warns on a
  `pypgx_result.json` path (`Not a directory`) but the report still generates.

## ✅ CURRENT STATE (2026-06-08)

Core stack rebuilt and healthy on refreshed versions (WSL-native docker):

| Service | Image / version | Notes |
|---|---|---|
| `pgx_db` | **postgres:18** | Fresh DB; data volume mounted at `/var/lib/postgresql` (PG18 layout) |
| `pgx_pharmcat` | **PharmCAT 3.4.0** | PharmVar data refresh; reporter multi-phenotype fix |
| `pgx_zarohla` | **ZaroHLA / OptiType v1.5** | Active HLA path on `:5060`; paired-end typing verified |
| `pgx_gatk_api` | GATK 4.6.2.0 | Uses the `./reference` bind mount |
| `pgx_app` | app | DB connects as `zaropgx_user` |

Other refreshed versions: htslib/bcftools **1.24** (pinned release tarballs in the main
image + the pharmcat container; the pharmcat container also pins samtools **1.24**), PyPGx
pinned to **0.26.0**.

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
`pharmcat-references` volume is wiped.

The volume is mounted at `/pharmcat-references` (cache only). `start.sh` symlinks
`reference.fna.bgz*` into `/pharmcat/` from the image. It no longer masks `/pharmcat`,
so a PharmCAT version bump takes effect on image rebuild **without** wiping the volume.
Wipe `pharmcat-references` only when you intentionally want to re-fetch the GRCh38
reference (or reclaim disk).

## Note on Docker engines — READ BEFORE RUNNING COMPOSE

The `pgx_*` containers run on **WSL-native docker**, NOT Docker Desktop. (Docker
Desktop hosts the separate goldflipper-evo / ollama / docs-mcp stacks.)

**`wsl docker` is NOT a reliable way to reach the native engine.** Docker Desktop's
WSL integration is enabled for `Ubuntu-22.04`, and it bind-mounts Desktop's socket
over `/run/docker.sock` inside the distro. While Desktop is running, `docker` typed
inside Ubuntu-22.04 reaches **Desktop**. An earlier version of this note said
otherwise; that was wrong.

Verify before running compose:

```bash
sudo ss -lnxp | grep -E '/(var/)?run/docker\.sock'
# owner "dockerd"         -> native engine   (correct)
# owner "docker-desktop-" -> Docker Desktop  (stop it first)
```

Because both engines accept this `compose.yml` and Compose scopes state per engine,
bringing the stack up on the wrong one creates a **silent duplicate** with identical
container names — no conflict, no warning. That happened 2026-08-02: a
database-less `pgx_*` set (0.2.8, partly built from `.worktrees/assume-reference-42b`)
appeared on Docker Desktop and thereafter raced the real stack for ports
5001/5002/5053/5055/5060 on every boot. Removed 2026-08-10; volumes were left intact.

Rules:
- Run compose only from the repo root, never from a worktree.
- Docker Desktop must hold **zero** `pgx_*` containers — check with
  `docker ps -a --filter name=pgx_` from Windows.
- A "port already allocated" error here almost always means a duplicate is starting,
  not that the stack is broken. `curl` the port first.
- Permanent fix if this keeps happening: turn off Docker Desktop's WSL integration
  for `Ubuntu-22.04` (Settings → Resources → WSL Integration), which makes `docker`
  inside the distro reach the native engine deterministically. Note this also means
  Desktop-hosted stacks must then be driven from Windows.
