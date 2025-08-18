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

# Install bcftools from source
RUN git clone --recurse-submodules https://github.com/samtools/htslib.git && \
    cd htslib && \
    autoreconf -i && \
    ./configure && \
    make && \
    make install && \
    cd .. && \
    git clone https://github.com/samtools/bcftools.git && \
    cd bcftools && \
    autoreconf -i && \
    ./configure && \
    make && \
    make install && \
    cd .. && \
    rm -rf htslib bcftools

# Set work directory
WORKDIR /app

# Copy dependency manifests and install with uv into system site-packages
COPY pyproject.toml uv.lock ./
# Export locked requirements and sync them into the system environment
RUN uv export --frozen --format requirements-txt > requirements.lock \
    && uv pip sync --system requirements.lock \
    && uv pip install --system "graphviz>=0.21,<1.0.0" \
    && rm -f requirements.lock

# Create directories for data and reports
RUN mkdir -p /data/reports /data/uploads

# Copy application code
# Note: Reference genome files are not included in the build context (see .dockerignore)
# They are mounted as volumes at runtime
COPY . /app/

# Expose the port for the application
EXPOSE 8000

# Command to run the application with debug mode but without reload
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "debug"] 