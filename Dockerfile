# Use a small base image with Python
FROM python:3.12-bookworm

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
         libgdk-pixbuf2.0-0 \
         libffi-dev \
         shared-mime-info \
         libglib2.0-0 \
         libgirepository-1.0-1 \
         libjpeg-dev \
         libopenjp2-7-dev \
         graphviz \
         python3-graphviz \
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
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

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
    && uv pip install --system "graphviz>=0.21,<1.0.0" \
    && rm -f requirements.lock

# Install Sphinx docs requirements separately (kept lightweight)
COPY docs/requirements.txt ./docs/requirements.txt
RUN uv pip install --system -r docs/requirements.txt

# Create directories for data and reports
RUN mkdir -p /data/reports /data/uploads

# Copy application code
# Note: Reference genome files are not included in the build context (see .dockerignore)
# They are mounted as volumes at runtime
COPY . /app/

# Build Sphinx documentation to be served by the app at /documentation
# Do not fail the image build if docs have warnings
RUN [ -d docs ] && python -m sphinx -b html -E -a docs docs/_build/html || true

# Expose the port for the application
EXPOSE 8000

# Command to run the application with debug mode but without reload
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "debug"] 