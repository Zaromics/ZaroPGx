# Use a smaller base image with Python
FROM python:3.10-slim

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
    # WeasyPrint dependencies
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    libglib2.0-0 \
    libjpeg-dev \
    libopenjp2-7-dev \
    && rm -rf /var/lib/apt/lists/*

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

# Copy requirements.txt and install dependencies
COPY requirements.txt .

# Install Python dependencies - install directly from requirements.txt for better versioning
RUN pip install --no-cache-dir -r requirements.txt

# Ensure aiohttp is installed explicitly with a specific version
RUN pip install --no-cache-dir aiohttp

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