FROM python:3.10

WORKDIR /app

# Install system dependencies for WeasyPrint
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    python3-pip \
    python3-setuptools \
    python3-wheel \
    python3-cffi \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Create and set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    wget \
    gnupg \
    lsb-release \
    curl \
    procps \
    # Add genomic tools for BAM/VCF handling
    samtools \
    tabix \
    bcftools \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install default JRE (using default-jre which is available in the repos)
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jre \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt /app/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create directories for data and reports
RUN mkdir -p /app/data/uploads /app/data/reports

# Copy health check script
COPY docker/app/health.sh /app/health.sh
RUN chmod +x /app/health.sh

# Copy application code
COPY . /app/

# Set proper permissions
RUN chmod -R 755 /app

# Expose port
EXPOSE 8000

# Command to run when container starts
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"] 