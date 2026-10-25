# Upgrading ZaroPGx

Changes that need action on an existing install. Newest first. One-command installs are
checked out at a release tag, so update them with the documented bootstrap update command:

```bash
curl -fsSL https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.sh | bash -s -- --update
```

```powershell
iex "& { $(iwr -useb https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.ps1) } -Update"
```

Run these as given: they fetch the scripts from `main`. The `bootstrap.sh` in your checkout
pins its own release and updates to nothing newer.

Development installs that deliberately track a branch can continue to use `git pull` followed
by `docker compose up -d`.

## v0.4.0 → v0.4.1

### The FHIR server's heap is set

The FHIR server's `-Xmx2g` was never applied (the image does not read `JAVA_OPTS`), so its
Java heap could grow to a quarter of the host's memory. It is now 2 GB, which a first start
and whole-genome pushes fit with room to spare; `HAPI_JAVA_HEAP` in `.env` changes it.
`docker compose up -d fhir-server` applies it.

### Updates wait for running jobs and uploads

`start-docker.sh` and `start-docker.ps1`, which `--update` ends with, no longer stop a
stack that is busy: they ask the app whether a job is running or an upload is still
arriving and stop there if one is. `--force` (`-Force`) restarts anyway. An app from before
this release can report its jobs but not its uploads, so on this update the script says to
make sure no upload is arriving.

### PyPGx measures CYP2B6 and CYP4F2 copy number from reads

`PYPGX_PHARMCAT_PREFERENCE` now defaults to `auto` (it was `pharmcat`). On a BAM, CRAM, SAM
or FASTQ, PyPGx then also runs the genes PharmCAT calls itself, so a whole genome's report
no longer marks CYP2B6 and CYP4F2 "Copy number not assessed": Alerts and Warnings gives
PyPGx's copy number for them. The calls shown stay PharmCAT's. VCF, BCF and gVCF runs are
unchanged.

Your `.env` keeps its value. If it says `PYPGX_PHARMCAT_PREFERENCE=pharmcat`, change it to
`auto` and restart PyPGx:

```bash
docker compose up -d pypgx
```

### CYP2C19 and SLCO1B1 deletions from read depth

PyPGx has no copy-number model for CYP2C19 or SLCO1B1, so a GRCh38 whole genome's report
said their copy number was not assessed, and a sample with one copy deleted was called from
the other alone: CYP2C19 *1/*37, an Intermediate Metabolizer, read *1/*1, Normal. ZaroPGx
now checks both genes for deletions from PyPGx's read depth. A named deletion (CYP2C19 *36
or *37, SLCO1B1 *48 or *49) under a homozygous call goes into the call PharmCAT is given,
and the report says so beside it; found normal, the alert says so once. The check runs in
the PyPGx image, so it needs the new images (`docker compose pull`, then
`docker compose up -d`). VCF, BCF and gVCF runs are unchanged.

### DPYD exon deletions from read depth

CPIC's DPYD table lists no deletion, so a deletion of DPYD exons, found in patients with DPD
deficiency, went unseen and the call read normal. The same check now measures DPYD from
PyPGx's read depth; a deletion it finds is named by its exons in an alert asking for an
independent test before fluoropyrimidine treatment. PharmCAT's call and phenotype are not
changed. A VCF, BCF or gVCF report now also marks DPYD's call as one that cannot show such a
deletion. Like the CYP2C19 and SLCO1B1 check, it needs the new images.

### CYP2B6 *30 named

PyPGx's CYP2B6 copy-number state Tandem1, which its own call leaves Indeterminate, is the
*30 hybrid duplication. Beside PharmCAT's call the report now says PyPGx found it, and a
normal CYP2B6 copy number no longer carries a "*30 not assessed" note. PharmCAT keeps its
call.

### Reports list the variants no allele definition lists

The genome downloader now builds the annotation this check reads, once, on its first
start after the update: about 1 GB fetched (Ensembl's transcripts, AlphaMissense, ClinVar,
gnomAD v4.1's ranges for these genes), about 3 MB kept in `reference/annotation`. Its
sources are pinned, ClinVar to a dated release, and each ZaroPGx release that refreshes
them rebuilds it. To skip the check, set `ANNOTATION_ENABLED=false` in `.env` before
starting; reports then say it did not run.

## v0.3.2 → v0.4.0

### The image tag moves with the release

`start-docker.sh` and `start-docker.ps1` now replace `ZAROPGX_TAG` in `.env` with the new
release when it names an older one (such as `0.3.2`), and say so. A blank tag, `latest`, a
newer release and any tag that is not a plain release number (`0.4.0-rc1`, `v0.3.2`, a local
build's) are left alone. If you start the stack with `docker compose` directly, set it
yourself, then pull:

```bash
docker compose pull && docker compose up -d
```

Left at `0.3.2`, the services run the old images under the new code.

Every `zaromicsresearch/zaropgx-*` image must come from the same release, `pypgx` and
`gatk-api` above all. gatk-api 0.4.0 writes a `##ZaroPGx_uncovered_genes` line into the VCF
it makes from a gVCF, which pypgx 0.4.0 reads to tell which genes the gVCF covered; without
that line, a gVCF loses the PyPGx call for every gene where the sample matches the reference.
pypgx 0.4.0 also asks gatk-api to re-align reads (`/realign-reads`, below). `nextflow` and
`pypgx` must match too: pypgx 0.4.0 believes that line only when nextflow 0.4.0 tells it
the upload was a gVCF (`upload_type`). Behind an older nextflow, a gVCF loses its PyPGx
calls wherever its genes have positions without a record. The start scripts move every
image together; this matters only with a custom tag, or after `docker compose build` of a
single service.

### Logs written before 0.4.0 may contain the database password

Earlier releases wrote the database password into the app's log on every start, and
`/services-status` logged the database URL on every call (fixed in 0.4.0, #155). Updating
recreates the app container, which discards its log. If you saved or shared that log, delete
the copies, and if anyone else could read it, change the database password: the steps are in
[SECURITY.md](SECURITY.md).

### The app binds to loopback in new installs

The templates now set `BIND_ADDRESS=127.0.0.1:8765` (they had `8765`, and `.env.production`
`0.0.0.0:8765`, which listen on every interface with no password by default). Your `.env`
keeps its value. With a bare port or `0.0.0.0`, anyone on your network can open the app.
If you use it from other devices, that is what you want: consider turning on
`ZAROPGX_AUTH_MODE=password`. If you do not, change it to `127.0.0.1:8765`.

### Uploads are deleted when their job ends

An upload is now removed once its job completes, fails or is cancelled; the reports keep the
results. To keep uploads, add `KEEP_UPLOADS=true` to `.env`.

### HAPI FHIR v8.12.0-2

The templates now pin `HAPI_FHIR_TAG=v8.12.0-2` (was `v8.10.0-2`). An existing `.env` keeps
its value. If you change it, HAPI migrates its schema on first start and cannot go back:
back up the database first (see the docs, *Running a server*). Either tag works with the
implementation guide below: on a `fhir` schema made by 0.3.2, `v8.10.0-2` and `v8.12.0-2`
both installed all three packages on the first start, and `$validate` of the Run Demo
bundle gave the same errors (none) and warnings on both.

### The FHIR server installs the Genomics Reporting IG

On the first start after updating, `fhir-server` installs the HL7 Genomics Reporting IG
3.0.0, which the FHIR export follows, and the two packages it depends on (HL7 Terminology
6.1.0 and the FHIR Extensions Pack 5.1.0). They are read from `docker/fhir-server/packages`,
which `compose.yml` now mounts read-only; nothing is downloaded. That start takes 14 to 25
seconds longer and adds about 50 MB to the `fhir` schema; later starts skip the install. If
one of the package files is missing, HAPI does not start, and its log says
`HAPI-2031: Error loading "file:///fhir-packages/..."`: restore the file from the release
(`git checkout -- docker/fhir-server/packages`). Details: `docs/developer/architecture.md`,
*The FHIR server's implementation guide*.

### CRAM reference cache

The genome downloader now builds a local cache of the reference sequences CRAMs are written
against, so CRAMs made with the 1000 Genomes GRCh38 analysis set decode as well as UCSC hg38
ones. On the first start after updating, it downloads 3.2 GB and keeps 5.3 GB under
`reference/ref_cache`. `REF_CACHE_ENABLED=false` skips it; CRAMs then decode only if they were
written against UCSC hg38. No service fetches reference sequences from the internet any more.

### Run Demo analyses NA12878's reads

**Run Demo** used to upload PharmCAT's example VCF. It now analyses
`app/static/demo/NA12878.pgx-demo.cram`, a 30 MB cut of NA12878's 1000 Genomes 30x CRAM,
through every stage; that took about 8 minutes on a 10-core machine. The CRAM decodes only
against the 1000 Genomes GRCh38 analysis set in the CRAM reference cache (above), so until
the genome downloader has cached it, Run Demo answers 503 and the page says why; with
`REF_CACHE_ENABLED=false` it always does. Like any CRAM, it fails with GATK switched off.
`app/static/demo/pharmcat.example.vcf` is gone, and with it the address
`/static/demo/pharmcat.example.vcf`; the same file is `test_data/pharmcat.example2.vcf`. The
CRAM adds 30 MB to the repository, which the update downloads.

The cache is built by the genome downloader's start-up run. With `DOWNLOAD_ON_STARTUP=false`
in `.env`, an updated install never builds it, and Run Demo answers 503 for good; the
downloader's `/start-download` does not help, it answers `already_completed`. To build the
cache, remove that line or set it to `true`, then `docker compose up -d genome-downloader`:
with the genomes already staged, it fetches only the cache.

### Nextflow no longer mounts the Docker socket

`compose.yml` no longer gives the `nextflow` service `/var/run/docker.sock`; it never started
containers. If your `compose.override.yml` adds the socket (or removes it for rootless
Docker), delete those lines.

### `data/versions` must be writable

`start-docker.sh` now also creates `data/versions`, world-writable and sticky like
`data/temp`. ZaroHLA and mtDNA-Server 2 record their versions there; without it the report's
Platform table has no rows for OptiType and mtDNA-Server 2. If you start the stack with
`docker compose up` directly:

```bash
mkdir -p data/versions && chmod 1777 data/versions
```

### PyPGx no longer reads a VCF's missing records as the reference

From a VCF or BCF, PyPGx used to call every gene it was asked for, reading a gene with no
record in the file as the reference sequence. It now calls a gene only where the file has a
genotype at one of the positions PyPGx defines the gene's alleles at; a gene with none gets
no call and a note, unless **Assume absent sites = reference** is on. On the old demo VCF
(`test_data/pharmcat.example2.vcf`), asked for all 66 of its genes, PyPGx now calls 2, where
it called 40.
A VCF you analysed with 0.3.2 can therefore come back with most PyPGx genes not called.
Turn the option on only for a whole-genome call set, where a missing record does mean the
reference (`docs/user/usage.md`, *Options*; `docs/user/file-formats.md`, *Variant Call
Format*). For alignments and gVCFs the option does nothing for PyPGx, as before.
This holds for a VCF ZaroPGx wrote, too (an alignment's or a gVCF's, downloaded and
uploaded again): the `##ZaroPGx_…` lines in its header, which say which genes had reads,
are not read from an upload, since a file can be filtered or edited after export. Upload the
BAM, CRAM or gVCF itself to have its reads counted.

### PyPGx judges an alignment's or gVCF's genes at their own positions

From an alignment (BAM, CRAM, SAM, or a FASTQ once aligned) or a gVCF, PyPGx used to call a
gene when any read, or any covered gVCF block, fell anywhere in its region, and read the
gene's positions without reads as the reference. On a targeted panel, reads over *UGT1A1*'s
first exon made *UGT1A4* and *UGT1A6*, whose regions overlap it, look covered, and they were
called \*1/\*1 from no data. A gene is now judged at the positions PyPGx defines its
alleles at. With no reads at any of them it gets no call and a note; for *CYP2A6*, *CYP2D6*,
*GSTM1*, *GSTT1*, *SULT1A1* and *UGT2B17* the note adds that both copies may be deleted,
which the data cannot tell apart. With some of them unread it is called, with a note giving
how many positions were read as the reference, and a ⚠ beside the gene in the Executive
Summary. A 30x whole genome is not affected: NA12878's has reads at every one of the 1,169
positions. A panel or exome analysed with 0.3.2 can come back with fewer PyPGx calls, and
notes on some of the rest. This needs the 0.4.0 `pypgx`, `gatk-api` and `app` images (see
*The image tag moves with the release*).

### The alignment index also re-aligns reads on ALT contigs

The index `scripts/build-align-index.sh` builds for FASTQ (see v0.3.1 → v0.3.2) now serves
BAM, CRAM and SAM too. When an alignment has 20 or more reads whose primary alignment is on
GRCh38's ALT contigs for *CYP2D6* or *UGT2B17*/*UGT2B15* (an alignment that was not
ALT-aware, typically), those loci's reads are aligned again against it before PyPGx and
PharmCAT's positions read them. Without the index, they are called from the reads as
uploaded, and *CYP2D6* and *UGT2B15* are flagged in the report: their calls may be wrong.
Build it if you analyse alignments made against a GRCh38 with ALT contigs.

### FHIR bundles are saved per job

A job's FHIR bundle is now written beside its other reports, as
`data/reports/<patient_id>/<job_id>/<job_id>_pgx_fhir_report.json` (and `.xml`), and the
report links to it there. It used to be `data/reports/<patient_id>/pgx_fhir_report.json`,
which each new job of the same patient overwrote. Scripts that read the old path need the
new one. Bundles already written stay where they are.

### "Actionable" means a prescribing change

An actionable finding is now a gene that a PharmCAT prescribing change for the sample comes
from: a different drug, a dose change or other prescribing guidance. It used to be every
gene whose phenotype was not "n/a", "Normal Metabolizer" or "Uncertain Susceptibility", so a
Normal Function, a negative HLA result or No Result counted. This changes
`GET /api/pharmcat/actionable/{run_id}`, the `actionable_findings` counts and lists in the
`/api/pharmcat` summaries, and the FHIR DiagnosticReport's conclusion ("N genes analyzed, M
actionable findings identified"). The fields keep their names and types; each finding gains
`drugs` and `prescribing_changes`. Runs stored earlier are read the new way. Details:
`docs/developer/api-reference.md`, *Actionable findings*.

## v0.3.1 → v0.3.2

### Image tag correction

Early copies of `.env.local` and `.env.production` pinned `ZAROPGX_TAG=0.2.8`.
That release predates the mtDNA image, so a complete `0.2.8` image set does not
exist. The startup scripts migrate that exact obsolete default to `0.3.2` before
stopping the current stack. Blank, `latest`, and custom tags are left unchanged.

### Documentation and API explorer addresses

The documentation moved from `/documentation` to `/docs`; old links redirect. The API
explorer (Swagger UI) moved from `/docs` to `/api/docs`, and ReDoc to `/api/redoc`.
`/openapi.json` is unchanged. Update bookmarks or scripts that open `/docs` for the API
explorer.

### Stage PharmCAT's position list

Aligned input (BAM, CRAM, SAM, FASTQ) and gVCFs are genotyped at every PharmCAT position,
read from `reference/pharmcat/pharmcat_positions.vcf`. The genome downloader fetches it on a
fresh install but skips a reference tree it has already filled, so an upgraded install may
not have it. Those jobs then fail and name the missing file.

```bash
mkdir -p reference/pharmcat
docker compose cp pharmcat:/pharmcat/pharmcat_positions.vcf reference/pharmcat/
```

Repeat this after every PharmCAT upgrade: the list is specific to the PharmCAT version.

### FASTQ needs an alignment index

FASTQ is aligned against a reference and index built once on the host:

```bash
scripts/build-align-index.sh
```

It reads `reference/hg38/Homo_sapiens_assembly38.fasta` (staged by the genome downloader),
takes about an hour and several GB of RAM, and writes ~8 GB to `reference/pypgx/` (set
`ZAROPGX_ALIGN_REFERENCE` to put it elsewhere). Until it exists, FASTQ jobs fail and say so.
No other input needs it.

### Scratch directories on native Linux

ZaroHLA and mtDNA-Server 2 run as uid 999 and write under `data/temp`. `start-docker.sh`
now makes `data/temp` and `data/temp/mtdna` writable for them. If you start the stack with
`docker compose up` directly:

```bash
mkdir -p data/temp/mtdna && chmod 1777 data/temp data/temp/mtdna
```

Without it, HLA typing and mtDNA calling fail with "Permission denied". Docker Desktop on
Windows is not affected.

## v0.2.8 → v0.3.0

### Rebuild or pull every service image

- PharmCAT's assume-reference settings are per run: form fields `pharmcat_absent_to_ref`
  and `pharmcat_unspecified_to_ref`, with the UI's defaults taken from `.env`. The report
  states the settings each run used.
- Services take the job as the multipart field `job_id` (was `workflow_id`), and the job
  client is mounted at `/job-client`.
- The PharmCAT image ships the allele translator and map, so uploaded outside-call TSVs get
  their ABCG2, IFNL3 and VKORC1 synonyms rewritten.

`docker compose pull` (or `docker compose build`), then `docker compose up -d`.

### API renames (developers / API clients)

- Upload/status JSON field `file_id` → `data_id` (same UUID as `genetic_data.data_id`).
- Cancel payloads: `job_id` only (no `workflow_id` dual-accept).
- Cleanup: `POST /api/cleanup/job/{job_id}` (old `/api/cleanup/workflow/...` removed).
- WebSocket envelope type: `job_update` (was `workflow_update`).
- Report artifacts: `/data/reports/{patient_id}/{job_id}/`; display `report_id` = `job_id`.

No database migration. Report directories in the old flat layout are not moved.

### PharmCAT references volume no longer masks `/pharmcat`

`pharmcat-references` now mounts at `/pharmcat-references` (reference genome cache
only). The pipeline/jar always come from the image. Existing installs: `git pull` and
`docker compose up -d --build pharmcat`. You do **not** need `down -v` for a
`PHARMCAT_VERSION` bump anymore. Optional: recreate the volume once if you want a
clean cache (`docker volume rm zaropgx_pharmcat-references` then `up`).

`PHARMCAT_VERSION` is now passed as a runtime env var as well as a build arg, so
`/data/versions/pharmcat.json` matches `.env`. Those version JSON files are runtime
stamps and are gitignored.

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

- `/api/v1/jobs/*` (including WebSocket status)
- `/api/v1/workflows` and `/api/v1/workflows/*` — read-only **recipe catalog** (not the old instance/progress API under `/workflows`)
- `/health`, docs/static, `/login`, `/logout`, `/token`

`/token` in password mode requires `ZAROPGX_AUTH_PASSWORD` and returns a
`gate=true` JWT. The legacy `test`/`test` credentials still work in open/audit
modes but those JWTs **cannot** unlock password mode.

If the app is published beyond localhost (`BIND_ADDRESS=0.0.0.0:8765`), treat
the jobs allowlist as part of your threat model until service-to-service
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
artifacts in reports.

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
