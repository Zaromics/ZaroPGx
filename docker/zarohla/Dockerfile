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
    && pip install --no-cache-dir git+https://github.com/FRED-2/OptiType.git uvicorn[standard] gunicorn fastapi python-multipart httpx httptools psutil \
    && apt-get purge -y --auto-remove build-essential git libglpk-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy application code
COPY docker/zarohla/app.py .
COPY app/utils/workflow_client.py /workflow-client/

# Create /data directory for uploads/results and set permissions
RUN mkdir -p /data && chown -R zarouser:zarouser /app /data /workflow-client

# Switch to non-root user for security
USER zarouser

# Expose API port
EXPOSE 5000

# Add a healthcheck to ensure the container is ready to accept traffic
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:5000/health || exit 1

# Start the application using Gunicorn with Uvicorn workers for production
CMD ["gunicorn", "app:app", "--workers", "2", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:5000"]
