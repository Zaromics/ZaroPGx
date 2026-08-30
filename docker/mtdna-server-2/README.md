# mtDNA-Server 2 sidecar

Calls mitochondrial variants and the MT-RNR1 aminoglycoside-risk alleles that
every other ZaroPGx sidecar reports as a permanent "no call." A FastAPI wrapper
around the tools [mtDNA-Server 2](https://github.com/genepi/mtdna-server-2)
ships in its own container image: [mutserve](https://github.com/seppinho/mutserve),
[haplogrep3](https://github.com/genepi/haplogrep3) (with the
`phylotree-fu-rcrs@1.2` phylotree pre-installed) and
[haplocheck](https://github.com/genepi/haplocheck).

## Why FROM the upstream image, not upstream's own pipeline

`quay.io/genepi/mtdna-server-2` is built to run as a Nextflow pipeline that
launches its own containers, which needs a Docker socket mounted into the
container. That is exactly the pattern that got the `hlatyping` service
disabled in this stack (see the commented-out block in `compose.yml`), so this
sidecar does not do it: it uses the upstream image only as a base — for the
tool jars and the R/rmarkdown/pandoc stack that renders upstream's
`report.Rmd` — and calls those tools directly from a small FastAPI app, the
same shape as every other ZaroPGx sidecar (gatk-api, nextflow, pharmcat,
pypgx, zarohla).

## Pinned versions

| Component | Version |
|---|---|
| mtDNA-Server 2 (base image / pipeline release) | v2.1.16 |
| mutserve | 2.0.3 |
| haplogrep3 | 3.2.2 |
| haplocheck | 1.3.3 |
| phylotree | fu-rcrs@1.2 |

The vendored `files/rcrs_mutserve.fasta` and `report.Rmd` are fetched from
the `v2.1.16` tag of the upstream repository
(see the `Dockerfile` build args / fetch step) and must be bumped together
with the base image tag — `report.Rmd` reads fields that mutserve/haplocheck
output, and a version skew between them fails silently, rendering a page with
empty panels rather than an error.

`GET /health` reports the mutserve/haplogrep3/haplocheck versions from the
`MUTSERVE_VERSION` / `HAPLOGREP_VERSION` / `HAPLOCHECK_VERSION` environment
variables the base image already sets, plus `mtdna-server-2` (the pipeline
release, hardcoded to the pinned tag). It also publishes the same information
to `/data/versions/mtdna-server-2.json`, in the shared per-tool manifest
format `VersionManager` reads for report citations.

## Two calling modes

- **VCF mode** (`_call_from_vcf`) — haplogroup calling via haplogrep3 plus the
  MT-RNR1 lookup, straight from an uploaded VCF. Does not run mutserve (it
  reads alignments, not variant calls) and does not render `report.Rmd`
  (which needs coverage, per-sample statistics and haplocheck contamination
  that only exist when there was a BAM to compute them from). Available for
  GRCh38, GRCh37/b37 (renamed to `chrM`, not lifted — b37's `MT` is already
  rCRS-coordinate) and hg19 (lifted, since MT-RNR1 sits inside one ungapped
  block of the chain).
- **Alignment mode** (`_call_from_alignment`) — the full pipeline: mutserve
  calls variants from the BAM against the vendored rCRS reference, haplogrep3
  and haplocheck run on the result, and upstream's `report.Rmd` renders the
  same `report.html` mtDNA-Server 2 itself produces. Gated on a minimum mean
  coverage (upstream's own floor) below which MT-RNR1 stays a no-call rather
  than being reported as reference. hg19 alignments are refused outright:
  hg19's `chrM` is a different sequence (`NC_001807`, 16571 bp) from rCRS, and
  there is no alignment-level liftover in this stack.

Both modes are implemented and wired into the pipeline (`pipelines/pgx/main.nf`'s
`MtdnaCall` process, gated on `--skip_mtdna`); the app's upload form exposes a
`mtdna_enabled` toggle, and a completed job's report includes a dedicated mtDNA section,
a versioned citation, and a "mtDNA Reports" download group (report/haplogroups/chrM VCF,
whichever the calling mode produced).

## Endpoints

- `GET /health` — `{"status": "healthy", "service": "mtdna-server-2", "versions": {...}, "alleles_known": 25}`,
  or `503` with the list of missing required paths (mutserve/haplogrep3/haplocheck
  jars, vendored rCRS FASTA) if the image is incomplete.
- `POST /call-mtdna` — submit a VCF or BAM/CRAM (`file`, `build`, `absent_to_ref`) for
  mitochondrial calling. Returns haplogroup, MT-RNR1 call, matched allele names, the
  extracted chrM VCF path, and — for an alignment input — the rendered `report.html`
  path (`null` for a VCF-only call, with `report_unavailable_reason` explaining why).
- `POST /cancel/{job_key}` — cancel a running job, mirroring the other sidecars.

## Scratch directory

Per-job working files live under `DATA_DIR/temp/mtdna/<job>` (i.e.
`/data/temp/mtdna/<job>` in the container, bind-mounted from `./data` on the
host) rather than `/tmp`. The Nextflow pipeline copies this service's report
out of that path from a different container, which only works because `./data`
is bind-mounted into both; anything written to `/tmp` would vanish silently.

## Building and running

As part of the stack (the normal path — always the `pgx-native` context, never bare
`docker`, and always from the repo root):

```bash
docker --context pgx-native compose build mtdna
docker --context pgx-native compose up -d mtdna
curl -s http://localhost:5062/health
```

Standalone, for iterating on this image alone:

```bash
docker --context pgx-native build -f docker/mtdna-server-2/Dockerfile -t zaropgx-mtdna:dev .
docker --context pgx-native run --rm -d --name mtdna_probe -p 15062:5000 zaropgx-mtdna:dev
curl -s http://localhost:15062/health
docker --context pgx-native rm -f mtdna_probe
```

## Test fixture

`tests/e2e` and manual verification of the alignment path use a real BAM,
`data/mtdna-testdata/HG00096.chrM.bam` — **not** committed (matches the rest of
`data/`'s convention; see `.gitignore`). Re-fetch it from upstream's own test data at
the pinned `v2.1.16` tag if it's missing:

```bash
mkdir -p data/mtdna-testdata
curl -fsSL -o data/mtdna-testdata/HG00096.chrM.bam \
  https://raw.githubusercontent.com/genepi/mtdna-server-2/v2.1.16/tests/data/bam/HG00096.chrM.bam
```

It's a 1000 Genomes sample, b37-headered (`SN:MT LN:16569`, ~270k reads on MT) — the
Ruling-16 case this sidecar exists to get right: `MT`/rCRS-length naming classifies as
B37 (rename-only, no liftover), never hg19. At last verification it produced haplogroup
`H16a1` and `MT-RNR1: Reference` at 1331x mean coverage.
