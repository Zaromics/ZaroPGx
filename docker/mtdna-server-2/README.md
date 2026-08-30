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

Calling logic for both modes lands in later tasks; this image currently
exposes only the health/version surface below.

## Endpoints

- `GET /health` — `{"status": "healthy", "service": "mtdna-server-2", "versions": {...}, "alleles_known": 25}`,
  or `503` with the list of missing required paths (mutserve/haplogrep3/haplocheck
  jars, vendored rCRS FASTA) if the image is incomplete.
- `POST /call-mtdna` *(later task)* — submit a VCF or BAM/CRAM for mitochondrial
  calling.
- `POST /cancel` *(later task)* — cancel a running job, mirroring the other
  sidecars.

## Scratch directory

Per-job working files live under `DATA_DIR/temp/mtdna/<job>` (i.e.
`/data/temp/mtdna/<job>` in the container, bind-mounted from `./data` on the
host) rather than `/tmp`. The Nextflow pipeline copies this service's report
out of that path from a different container, which only works because `./data`
is bind-mounted into both; anything written to `/tmp` would vanish silently.

## Building and running

```bash
docker --context pgx-native build -f docker/mtdna-server-2/Dockerfile -t zaropgx-mtdna:dev .
docker --context pgx-native run --rm -d --name mtdna_probe -p 15062:5000 zaropgx-mtdna:dev
curl -s http://localhost:15062/health
docker --context pgx-native rm -f mtdna_probe
```

The compose service for this sidecar arrives in a later task.
