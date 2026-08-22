FROM python:3.12-slim

# Create a non-root user
RUN groupadd -r zarouser && useradd -r -g zarouser zarouser

# Install system and runtime dependencies, build OptiType, and clean up in one step
# - curl: needed for Docker HEALTHCHECK
# - glpk-utils: needed for OptiType ILP solver (glpsol)
# - seqan-apps: provides yara_mapper and razers3
# - samtools: needed for BAM to FASTQ conversion
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    libglpk-dev \
    glpk-utils \
    seqan-apps \
    curl \
    samtools \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir git+https://github.com/FRED-2/OptiType.git@v1.5.0 uvicorn[standard] gunicorn fastapi python-multipart httpx httptools psutil \
    && apt-get purge -y --auto-remove build-essential git libglpk-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy application code
COPY docker/zarohla/app.py .
COPY app/utils/job_client.py /job-client/

# Create /data directory for uploads/results and set permissions
RUN mkdir -p /data && chown -R zarouser:zarouser /app /data /job-client

# Matplotlib (imported by OptiType) needs a writable config dir; zarouser has no home
ENV HOME=/tmp
ENV MPLCONFIGDIR=/tmp/matplotlib

# Switch to non-root user for security
USER zarouser

# Expose API port
EXPOSE 5000

# Add a healthcheck to ensure the container is ready to accept traffic
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:5000/health || exit 1

# Start the application using Gunicorn with Uvicorn workers for production.
#
# ONE worker, deliberately, and not for throughput reasons.
#
# app.py's `running_processes` registry is a module-level dict. Under
# `--workers 2` each gunicorn worker imports the module and gets its own copy,
# and the OS decides which worker accepts a given connection -- so a POST
# /cancel had roughly a coin-flip chance of landing on the worker that does not
# hold the job, finding nothing in its registry, and answering
# {"status": "success"} while OptiType kept running. `--preload` does not fix
# this: it forks after import, and the dict is mutated after the fork, so each
# worker still diverges into its own copy-on-write page.
#
# The concurrency given up is small, and it is not where you would guess.
# /call-hla is `async def` and awaits
# `asyncio.create_subprocess_exec(...).communicate()`, so the event loop is free
# for the whole multi-minute OptiType run -- the expensive part costs nothing,
# one worker serves many concurrent typings, and the real parallelism was always
# in the OptiType and samtools child processes, which are unaffected. Load is low
# by construction too: only pipelines/pgx/main.nf submits HLA jobs (see the note
# on this service in compose.yml), at most one per pipeline run.
#
# What is genuinely lost: app.py used to ingest an upload as
# `f.write(await file.read())` -- the whole file into memory, then a synchronous
# write. It now streams in UPLOAD_CHUNK_BYTES chunks, the same shape the
# gatk-api sidecar already used, so that part no longer blocks the loop for the
# duration of the read. `shutil.rmtree`s of the job dir are still synchronous
# and still block the one event loop while they run, so while a large cleanup
# is in flight this container will not answer /health or the broadcast
# /cancel, where a second worker could have. A second worker is still the
# wrong fix for that: it only halves the odds of a blocked request landing on
# the busy worker while breaking every cancel that lands on the wrong one.
#
# The other alternative -- a registry shared across workers -- means real shared
# state (Redis, or a file under /data with locking), which is a lot of moving
# parts for a service that does not need the second worker in the first place.
#
# tests/test_log_rotation_252.py pins this: dropping to one worker is what lets
# app.py write a single /data/zarohla_progress.log again instead of a per-pid
# one, and a future `--workers 2` here must go back through both.
CMD ["gunicorn", "app:app", "--workers", "1", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:5000"]
