FROM python:3.12-slim

# Create a non-root user
RUN groupadd -r zarouser && useradd -r -g zarouser zarouser

# bwa, not bwa-mem2, and the reason is the index rather than the alignment.
#
# PyPGx forbids ALT contigs except chr22_KI270879v1_alt (GSTT1), so no
# off-the-shelf prebuilt index qualifies -- AWS iGenomes ships hs38DH (ALT-bearing)
# and Broad's ALT-free tarball omits the GSTT1 contig. The index must therefore be
# built locally, and that is what decides the aligner: bwa indexes a human genome
# in ~5 GB of RAM, bwa-mem2 needs ~28N GB (measured 80-90 GB, reported to fail
# outright on 64 GB). On a 32 GB VM that is not a preference, it is the only
# option that runs. Heng Li's own position on short reads is unretracted -- "bwa-mem
# is still better for production uses" -- and nf-core/sarek still defaults to it.
#
# LICENCE: bwa is GPL-3.0. It is invoked here as a separate executable over a
# process boundary and is not linked into anything, so this image is mere
# aggregation rather than a derived work. See docker/zaroalign/LICENSES.md.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bwa \
    samtools \
    curl \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir uvicorn[standard] gunicorn fastapi python-multipart httpx httptools psutil \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/zaroalign/app.py .
COPY app/api/utils/fastq_platform.py /app/fastq_platform.py
COPY app/utils/job_client.py /job-client/

RUN mkdir -p /data /reference /scratch && chown -R zarouser:zarouser /app /data /job-client /scratch

ENV HOME=/tmp

USER zarouser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:5000/health || exit 1

# One worker, for the same reason zarohla runs one: `running_processes` is a
# module-level dict, and a second worker would get its own copy, so a /cancel
# could land on the worker that does not hold the job and report success while
# bwa kept running.
CMD ["gunicorn", "app:app", "--workers", "1", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:5000", "--timeout", "0"]
