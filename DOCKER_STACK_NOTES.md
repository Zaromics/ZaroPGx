# Docker Stack — Maintenance Notes

> Created 2026-05-28. Reconciled 2026-05-29. **Refreshed 2026-06-08** (core dependency
> refresh + `cpic`→`zaropgx` finalization + ZaroHLA fix). **Refreshed 2026-07-11**
> (PharmCAT 3.2.0→3.3.0; htslib/bcftools/samtools 1.23.1→1.23.2 security patch).
> **Refreshed 2026-07-14** (PharmCAT 3.3.0→3.4.0 + htslib/bcftools/samtools 1.23.2→1.24,
> adopting PharmCAT 3.4.0's newly recommended bcftools. 1.24 also drops the experimental,
> unadopted CRAM v4 codec for security — no impact here: ZaroPGx's only CRAM→BAM step uses
> the GATK image's own samtools, not these pinned builds). **Refreshed 2026-08-30**
> (mtDNA-Server 2 service shipped — see below). Dev environment.

## 2026-08-30 refresh — mtDNA-Server 2 lands as a real service

`docker/mtdna-server-2/` stopped being a build context with no compose entry: the
`mtdna` service now runs as container `pgx_mtdna` on
`${INTERNAL_BIND_ADDRESS:-127.0.0.1}:5062 → 5000` (`GET /health`, `POST /call-mtdna`,
`POST /cancel/{job_key}`), wired into `pipelines/pgx/main.nf` (`MtdnaCall`, gated on
`--skip_mtdna`), the upload form (`mtdna_enabled` toggle), the stage-glyph row
(`stageMtDNA`), and the report generator (its own report section, a versioned citation,
and a "mtDNA Reports" download group).

- **Image**: built `FROM quay.io/genepi/mtdna-server-2:v2.1.16` — not run as upstream's
  own Nextflow pipeline (that needs a mounted Docker socket, the same trap that got
  `hlatyping` disabled below), but as a base for its tool jars, called directly from a
  small FastAPI wrapper like every other sidecar here.
- **Pinned component versions** (from the base image's own env vars, surfaced at
  `GET /health` and `/data/versions/mtdna-server-2.json`): mutserve **2.0.3**,
  haplogrep3 **3.2.2** (phylotree `fu-rcrs@1.2`), haplocheck **1.3.3**.
- **Measured image size: ~4.52 GB** — the largest image in the stack (previous largest
  was PyPGx at 2.4 GB). The base image alone accounts for **4.48 GB** of that; the
  FastAPI wrapper layer adds only ~40 MB on top. Do not be surprised by the `docker
  --context pgx-native images` output — this is expected, not a leak.
- **Two calling modes**: `_call_from_vcf` (haplogrep3 only — haplogroup + the MT-RNR1
  lookup; no `report.Rmd` render, since that needs coverage/contamination metrics a VCF
  doesn't carry) and `_call_from_alignment` (mutserve + haplogrep3 + haplocheck, then
  upstream's own `report.Rmd` rendered to `report.html`). The alignment path gates
  MT-RNR1 `Reference` on a 50x mean-coverage floor (`MIN_MEAN_COVERAGE`,
  `docker/mtdna-server-2/app.py`); the VCF path only reports `Reference` when the caller
  passes `absent_to_ref` explicitly (same honesty rule as PharmCAT's outside calls).
- **Bump rule — the image tag, `docker/mtdna-server-2/files/` and the vendored
  `report.Rmd` move together as one unit.** `report.Rmd` reads fields that
  mutserve/haplocheck/haplogrep3 produce; a version skew between the R template and the
  tool output that feeds it fails silently — the page still renders 200, with the
  mismatched panel just blank. When bumping the `v2.1.16` pin, re-fetch `files/` and
  `report.Rmd` from the *same* upstream tag in the same change, never independently.
- **Scratch directory constraint**: per-job files live under
  `DATA_DIR/temp/mtdna/<job>` (`/data/temp/mtdna/<job>` in the container), not `/tmp` —
  `main.nf`'s `PharmCATRun`/report-copy steps read the sidecar's `report_html` path from
  a *different* container, which only works because `./data` is bind-mounted into both.
- **Test fixture**: `data/mtdna-testdata/HG00096.chrM.bam` (21 MB, gitignored — see
  `docker/mtdna-server-2/README.md` for the re-fetch command) is a real b37-headered
  (`SN:MT LN:16569`, 270k reads) 1000 Genomes BAM used to verify the alignment path; it
  previously produced haplogroup `H16a1` with `MT-RNR1: Reference` at 1331x coverage.

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

- **Full BAM e2e is now GREEN too** — the BAM→PyPGx→PharmCAT→report lane had never
  completed and unwound a stack of pre-existing bugs (all fixed): HLA/OptiType ran
  unconditionally (skip_hla now honored across all alignment branches); pypgx
  create-input-vcf used the wrong CLI signature + needed FASTA/BAM indexing via pysam;
  PyPGxBam2Vcf's output glob didn't match the `.vcf.gz` it emits. Fixture:
  `test_data/pgx_ngs_example.bam` (a real 30-read GRCh38 BAM). Its e2e
  (`tests/e2e/test_bam_pipeline.py`) is gated on `ZAROPGX_E2E_REFERENCE=1` because the
  lane needs the multi-GB reference (absent in CI). Run it with the full stack up:
  `ZAROPGX_E2E=1 ZAROPGX_E2E_REFERENCE=1 ZAROPGX_E2E_BASE_URL=http://127.0.0.1:8765 .venv/Scripts/python.exe -m pytest -m e2e --zaropgx-e2e`.
  Note: the first BAM run faidx-indexes the GRCh38 FASTA (~1 min, cached after).

- **GRCh37→GRCh38 liftover is now real** (2026-08-23) — gatk-api grew a
  `/liftover-vcf` endpoint running Picard `LiftoverVcf` (GATK bumped
  **4.6.2.0 → 4.7.0.0**, image rebuild required) against the hg38 reference, with the
  UCSC chain at `reference/chain/hg19ToHg38.over.chain.gz` (genome-downloader now
  fetches it on fresh deploys). A GRCh37/hg19 VCF upload is no longer
  unsupported/provisional: main.nf routes it through a new `LiftoverVCF` process
  (`--source_build`, sent by the app off the *detected* build) before PyPGx/PharmCAT.
  The sidecar normalises `1`-style contigs to `chr1` first — the UCSC chain is
  chr-prefixed, and without the rename LiftoverVcf rejects every record — and fails
  the run when the reject rate is implausible (>50%) instead of returning a
  near-empty VCF. Unliftable variants are dropped and counted (reject VCF kept
  beside the output). New Job step: `liftover` (registered in
  `app/services/workflow_registry.py` — an unregistered step name 404s its status
  updates and hangs [pending], the same trap the HLA lane hit).

- **A GRCh37/hg19-aligned BAM/CRAM/SAM is now refused** (2026-08-29) — the liftover
  above converts a *called* file's coordinates, so it does not reach aligned reads:
  those have variants called out of them first, against gene regions looked up by
  assembly, so a GRCh37 BAM analysed as GRCh38 reads each gene ~400 kb off (CYP2D6)
  and reports star alleles that are not the patient's, with nothing erroring. It was
  silently accepted before, because `FileAnalysis` carried the alignment header's
  *ambiguity* evidence but not the build it declared. It now carries
  `reference_genome`, and `determine_workflow` refuses GRCh37-aligned files
  (`unsupported` + NOT `is_provisional`, so the upload gate returns 400) pointing at
  the two real ways out: call variants yourself and upload the VCF (which IS lifted),
  or realign. `CONTIG_LENGTH_ASSEMBLIES` was extended from chr1/2/3/X to the PGx
  chromosomes (6, 7, 10, 12, 16, 19, 22) so a targeted-panel file — which carries
  none of the first four — can be identified at all; lengths re-read from the three
  shipped `.dict` files, hg19 and b37 confirmed identical and no length shared
  between builds. Verified live: a chr22-only GRCh37 panel SAM → detected GRCh37 →
  HTTP 400; a GRCh38 BAM → HTTP 200.

- **CRAM, SAM and BAM+HLA lanes are now GREEN too** (2026-08-23) — every input lane
  runs end to end. CRAM/SAM convert to BAM via gatk-api then rejoin the BAM lane;
  the BAM+HLA lane leaves OptiType on and exercises the full HLA path. Fixtures:
  `test_data/pgx_ngs_example.{cram,sam}` (derived from the BAM) and
  `test_data/pgx_wgs_hla_example.bam` (paired-end, 100% properly paired, CYP2C19
  reads on chr10 + tiled HLA-A/B/C reads on chr6). E2e:
  `tests/e2e/test_alignment_conversion_pipeline.py` (CRAM+SAM) and
  `tests/e2e/test_bam_hla_pipeline.py` (BAM+HLA, 6/6 steps), same
  `ZAROPGX_E2E_REFERENCE=1` gating. Two lane bugs fixed getting HLA green:
  main.nf updated the HLA step under the wrong name (`zarohla_bam`/`zarohla_fastq`
  vs the registry's `hla_typing`), so the step hung [pending]; and zarohla's
  `/call-hla` ran `samtools fastq` on a coordinate-sorted BAM without collating
  first, dropping mates and choking OptiType — now `samtools collate -u` runs
  first. All five lanes (VCF/BAM/CRAM/SAM/BAM+HLA) verified against the live stack.

## ✅ CURRENT STATE (2026-06-08)

Core stack rebuilt and healthy on refreshed versions (WSL-native docker):

| Service | Image / version | Notes |
|---|---|---|
| `pgx_db` | **postgres:18** | Fresh DB; data volume mounted at `/var/lib/postgresql` (PG18 layout) |
| `pgx_pharmcat` | **PharmCAT 3.4.0** | PharmVar data refresh; reporter multi-phenotype fix |
| `pgx_zarohla` | **ZaroHLA / OptiType v1.5** | Active HLA path on `:5060`; paired-end typing verified |
| `pgx_mtdna` | **mtDNA-Server 2 v2.1.16** (mutserve 2.0.3, haplogrep3 3.2.2, haplocheck 1.3.3) | Active mitochondrial/MT-RNR1 path on `:5062`; ~4.52 GB image, largest in the stack |
| `pgx_gatk_api` | GATK **4.7.0.0** | Uses the `./reference` bind mount; also serves `/liftover-vcf` (Picard LiftoverVcf, GRCh37→GRCh38, chain at `reference/chain/hg19ToHg38.over.chain.gz`) |
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

**Permanent fix in place (2026-08-23): the `pgx-native` docker context.** A native
dockerd `docker.socket` drop-in (`/etc/systemd/system/docker.socket.d/native-secondary.conf`)
adds a second listener at `/run/docker-native.sock` that Docker Desktop never
bind-mounts over, and the `pgx-native` context points at it. Run this stack through it:
`docker --context pgx-native compose ...` (or `export DOCKER_CONTEXT=pgx-native`).
That reaches the native engine deterministically regardless of Desktop's state, so
Desktop's WSL integration stays ON and the Desktop-hosted stacks
(goldflipper-evo/ollama/docs-mcp) keep working from bare `docker`. The context is
per-user (`~/.docker`), created for `root` and `iliya`; recreate with
`docker context create pgx-native --docker host=unix:///run/docker-native.sock`. The
older option — turning off Desktop's WSL integration for Ubuntu-22.04 — also works but
forces the Desktop stacks to be driven from Windows, so the context is preferred.

Rules:
- Prefer `docker --context pgx-native` for every command here; bare `docker` in the
  distro reaches whichever engine currently owns `/var/run/docker.sock` (usually
  Desktop after a Desktop restart).
- Run compose only from the repo root, never from a worktree.
- Docker Desktop must hold **zero** `pgx_*` containers — check with
  `docker ps -a --filter name=pgx_` from Windows.
- A "port already allocated" error here almost always means a duplicate is starting,
  not that the stack is broken. `curl` the port first.
