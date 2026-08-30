# The upstream pipeline's own image, used as a base rather than run as a
# pipeline. It already carries every tool this service needs -- mutserve 2.0.3,
# haplogrep3 3.2.2 with phylotree-fu-rcrs@1.2 pre-installed, haplocheck 1.3.3,
# and the R/rmarkdown/pandoc stack that renders upstream's report.Rmd -- so the
# sidecar invokes those directly.
#
# Deliberately NOT running upstream's Nextflow pipeline in here: that needs a
# mounted Docker socket, which is exactly what got the hlatyping service
# disabled (see the commented block in compose.yml).
FROM quay.io/genepi/mtdna-server-2:v2.1.16

RUN groupadd -r mtuser && useradd -r -g mtuser mtuser

# curl for the Docker HEALTHCHECK; the rest is the FastAPI surface. pip comes
# from the base image's conda root env, which also owns python 3.9+.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && pip install --no-cache-dir \
       "uvicorn[standard]" gunicorn fastapi python-multipart httpx httptools psutil \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/mtdna-server-2/app.py .
COPY docker/mtdna-server-2/report.Rmd .
COPY docker/mtdna-server-2/files/ /opt/mtdna-files/
COPY app/utils/job_client.py /job-client/
COPY app/mtdna/ /mtdna-lib/mtdna/

# Pre-built here, as root: /opt/mtdna-files stays root-owned below (only /app,
# /data, /job-client and /mtdna-lib are chowned to mtuser), and bcftools/gatk
# cannot lazily create a .fai/.dict next to the FASTA at request time -- the
# directory itself isn't group/other-writable, so the attempt fails with
# "Permission denied" the first time the VCF path's `bcftools norm -f` or a
# hg19 `gatk LiftoverVcf -R` runs. samtools dict, not gatk
# CreateSequenceDictionary: identical output GATK reads, one fewer JVM start.
RUN samtools faidx /opt/mtdna-files/rcrs_mutserve.fasta \
    && samtools dict /opt/mtdna-files/rcrs_mutserve.fasta \
       -o /opt/mtdna-files/rcrs_mutserve.dict

RUN mkdir -p /data && chown -R mtuser:mtuser /app /data /job-client /mtdna-lib

ENV HOME=/tmp

USER mtuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:5000/health || exit 1

# ONE worker, for the same reason as docker/zarohla/Dockerfile: the
# running_processes registry below is module-level state, so a second worker
# gets its own copy and POST /cancel lands on the wrong one about half the time.
# --preload does not fix it (fork happens after import, mutation after fork).
# It is also what lets this write a single /data/mtdna_progress.log rather than
# a per-pid one; tests/test_log_rotation_252.py pins that.
CMD ["gunicorn", "app:app", "--workers", "1", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:5000"]
