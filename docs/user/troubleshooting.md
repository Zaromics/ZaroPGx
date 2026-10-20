---
title: Troubleshooting
---

# Troubleshooting

Start with the state of the services and the logs:

```bash
docker compose ps                      # every service should be "healthy"
docker compose logs -f app             # or pharmcat, nextflow, pypgx, gatk-api, ...
curl http://localhost:8765/services-status
```

A failed job's own record is in its directory, `data/reports/<patient_id>/<job_id>/`:
Nextflow's `trace.txt` and `report.html`, and the PharmCAT pipeline log.

## The stack does not start

**`required variable DB_PASSWORD is missing a value`.** The `.env` templates ship
`DB_PASSWORD` and `SECRET_KEY` blank. Run `./start-docker.sh`, which generates both, or set
them yourself.

**The app exits with "SECRET_KEY is missing or still a tracked placeholder".** Same cause:
set `SECRET_KEY` in `.env` to a long random value.

**The app waits, and `pharmcat` is "starting" or "unhealthy".** On first start PharmCAT
downloads its reference data, usually in 8-15 minutes; the app waits until it is healthy.
`docker compose logs pharmcat` shows the download. On a slow connection it can take longer
than the health check allows. Leave it to finish, then `docker compose up -d`.

**A port is already in use.** The app's port is `BIND_ADDRESS` in `.env`. The other
services' ports are fixed in `compose.yml`; change one in `compose.override.yml`.

**On Apple Silicon or another ARM machine, the images will not run.** They are
`linux/amd64` only. Turn on Rosetta emulation in Docker Desktop.

## A job fails

**"Permission denied" from HLA typing or mtDNA, on Linux.** Those services run as uid 999
and need to write to `data/temp` and `data/versions`. `start-docker.sh` sets this up; if you
started the stack another way, run it once, or:

```bash
mkdir -p data/temp/mtdna data/versions
chmod 1777 data/temp data/temp/mtdna data/versions
```

**FASTQ: "The bwa-mem index image is not present".** Build the alignment index once:
`./scripts/build-align-index.sh`.

**The report flags CYP2D6 or UGT2B15 for reads on ALT contigs, not re-aligned.** The
alignment put 20 or more of that locus's reads on GRCh38's ALT contigs as their primary
alignment (it was not ALT-aware), and ZaroPGx could not align them again; the alert gives
the reason. Most often the alignment index is missing: build it as above and run the
sample again. An ALT-aware alignment, whose ALT hits are supplementary copies of reads
kept on the main chromosomes, is not flagged for them ({doc}`file-formats`).

**A CRAM is refused, naming contigs that cannot be decoded.** It was written against a
reference ZaroPGx does not hold ({doc}`file-formats`). Convert it to BAM with its own
reference (`samtools view -b -T <that FASTA> -o out.bam in.cram`) and upload the BAM.

**A GRCh37 VCF fails at liftover.** More than half of its variants could not be mapped to
GRCh38. Check that the file really is GRCh37/hg19.

**With GATK switched off, a CRAM, SAM, BCF, gVCF or FASTQ fails.** Those need GATK's
service for conversion or alignment. Switch it back on.

**The app was restarted while a job ran, and the job never finished.** The app follows each
pipeline run from inside its own process; a restart loses track of it. The pipeline may
finish, but no report is made. Cancel the job and run it again, and avoid restarting the app
while jobs run.

## The results look wrong

**Many genes are "Not called" from a VCF.** A variant-only VCF has no records where the
sample matches the reference, so PharmCAT cannot tell reference from not-sequenced. The
report's notes say so. Either run it again with **Assume absent sites = reference**
({doc}`usage`), accepting that uncovered positions will read as reference, or upload the
gVCF or the alignment instead.

**A gene shows several diplotypes joined by "or".** The input is unphased and more than one
diplotype fits. Phased data, or reads, can resolve it.

**No HLA calls.** HLA typing needs reads: a VCF cannot provide them.

**The PDF failed.** `docker compose logs app` names the error. With `PDF_FALLBACK=true`, a
WeasyPrint failure falls back to ReportLab, which produces a plainer PDF.
