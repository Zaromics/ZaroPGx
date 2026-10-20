# Use a small base image with Python
# Debian 13 (trixie), not 12 (bookworm): WeasyPrint 70 with bookworm's Pango 1.50
# measures a line with its trailing space, so a line that just fits is split after
# its first word (Kozea/WeasyPrint#2946). The check after the Python install keeps
# it that way.
FROM python:3.12-trixie

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libpq-dev \
    git \
    zlib1g-dev \
    libbz2-dev \
    liblzma-dev \
    autoconf \
    automake \
    libtool \
    pkg-config \
    gettext \
    libcurl4-openssl-dev \
    libdeflate-dev \
    # WeasyPrint dependencies - complete set for proper SVG rendering
         libcairo2 \
         libpango-1.0-0 \
         libpangocairo-1.0-0 \
         libgdk-pixbuf-2.0-0 \
         libffi-dev \
         shared-mime-info \
         libglib2.0-0 \
         libgirepository-1.0-1 \
         libjpeg-dev \
         libopenjp2-7-dev \
         fonts-dejavu \
         libharfbuzz0b \
         libpangoft2-1.0-0 \
         libwoff1 \
         fonts-liberation \
         fonts-dejavu-core \
         fonts-dejavu-extra \
         fonts-noto \
         fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
# uv from Astral's image, pinned by version and digest (tests/test_dockerfile_uv_pin.py).
COPY --from=ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 /uv /uvx /usr/local/bin/

# Install htslib + bcftools from pinned release tarballs
# (reproducible and faster than building git HEAD; matches the pharmcat container)
ENV HTSLIB_VERSION=1.24
ENV BCFTOOLS_VERSION=1.24
RUN curl -fsSL -O https://github.com/samtools/htslib/releases/download/${HTSLIB_VERSION}/htslib-${HTSLIB_VERSION}.tar.bz2 && \
    curl -fsSL -O https://github.com/samtools/bcftools/releases/download/${BCFTOOLS_VERSION}/bcftools-${BCFTOOLS_VERSION}.tar.bz2 && \
    tar -xjf htslib-${HTSLIB_VERSION}.tar.bz2 && \
    tar -xjf bcftools-${BCFTOOLS_VERSION}.tar.bz2 && \
    cd htslib-${HTSLIB_VERSION} && \
    ./configure --prefix=/usr/local && \
    make -j"$(nproc)" && \
    make install && \
    cd ../bcftools-${BCFTOOLS_VERSION} && \
    ./configure --prefix=/usr/local && \
    make -j"$(nproc)" && \
    make install && \
    cd .. && \
    ldconfig && \
    rm -rf htslib-${HTSLIB_VERSION} bcftools-${BCFTOOLS_VERSION} \
           htslib-${HTSLIB_VERSION}.tar.bz2 bcftools-${BCFTOOLS_VERSION}.tar.bz2

# Set work directory
WORKDIR /app

# Copy dependency manifests and install with uv into system site-packages
COPY pyproject.toml uv.lock ./
# Export locked requirements and sync them into the system environment
RUN uv export --frozen --no-dev --format requirements-txt > requirements.lock \
    && uv pip sync --system requirements.lock \
    && rm -f requirements.lock

# WeasyPrint 70 needs Pango 1.52 or later to break lines where they fit (see FROM).
RUN python -c "from weasyprint.text.ffi import pango; v = pango.pango_version(); \
assert v >= 15200, f'Pango {v}: WeasyPrint 70 needs 1.52 or later (Kozea/WeasyPrint#2946)'"

# Install Sphinx docs requirements separately (kept lightweight)
COPY docs/requirements.txt ./docs/requirements.txt
RUN uv pip install --system -r docs/requirements.txt

# Create directories for data and reports
RUN mkdir -p /data/reports /data/uploads

# Copy application code
# Note: Reference genome files are not included in the build context (see .dockerignore)
# They are mounted as volumes at runtime
COPY . /app/

# Build Sphinx documentation to be served by the app at /docs
# Do not fail the image build if docs have warnings
RUN [ -d docs ] && python -m sphinx -b html -E -a docs docs/_build/html || true

# Expose the port for the application
EXPOSE 8000

# Command to run the application with debug mode but without reload
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "debug"] 