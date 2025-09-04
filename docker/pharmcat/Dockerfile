FROM python:3.12

# Add labels for better maintainability and alignment with official PharmCAT
LABEL maintainer="ZaroPGx (wrapper only)"
LABEL description="PharmCAT 3.0.1 Wrapper Service with Flask API"
LABEL version="3.0.1"

WORKDIR /pharmcat

# Install system dependencies (aligned with official PharmCAT)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    wget \
    unzip \
    curl \
    build-essential \
    git \
    autoconf \
    automake \
    libtool \
    pkg-config \
    zlib1g-dev \
    libbz2-dev \
    liblzma-dev \
    ca-certificates \
    apt-utils \
    apt-transport-https \
    gpg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

# Install Java 17 (aligned with official PharmCAT approach)
RUN curl https://packages.adoptium.net/artifactory/api/gpg/key/public | gpg --dearmor -o /etc/apt/keyrings/adoptium.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb $(awk -F= '/^VERSION_CODENAME/{print$2}' /etc/os-release) main" \
      | tee /etc/apt/sources.list.d/adoptium.list && \
    apt-get update && \
    apt-get -y install --no-install-recommends temurin-17-jdk && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install bcftools, htslib, and samtools from source (aligned with official PharmCAT)
ENV BCFTOOLS_VERSION=1.21
ENV HTSLIB_VERSION=1.21
ENV SAMTOOLS_VERSION=1.21
RUN wget https://github.com/samtools/htslib/releases/download/${HTSLIB_VERSION}/htslib-${HTSLIB_VERSION}.tar.bz2 && \
    wget https://github.com/samtools/bcftools/releases/download/${BCFTOOLS_VERSION}/bcftools-${BCFTOOLS_VERSION}.tar.bz2 && \
    wget https://github.com/samtools/samtools/releases/download/${SAMTOOLS_VERSION}/samtools-${SAMTOOLS_VERSION}.tar.bz2 && \
    tar -xjf htslib-${HTSLIB_VERSION}.tar.bz2 && \
    tar -xjf bcftools-${BCFTOOLS_VERSION}.tar.bz2 && \
    tar -xjf samtools-${SAMTOOLS_VERSION}.tar.bz2 && \
    cd htslib-${HTSLIB_VERSION} && \
    ./configure --prefix=/usr/local && \
    make -j$(nproc) && \
    make install && \
    cd ../bcftools-${BCFTOOLS_VERSION} && \
    ./configure --prefix=/usr/local && \
    make -j$(nproc) && \
    make install && \
    cd ../samtools-${SAMTOOLS_VERSION} && \
    ./configure --prefix=/usr/local && \
    make -j$(nproc) && \
    make install && \
    cd .. && \
    rm -rf htslib-${HTSLIB_VERSION} bcftools-${BCFTOOLS_VERSION} samtools-${SAMTOOLS_VERSION} \
           htslib-${HTSLIB_VERSION}.tar.bz2 bcftools-${BCFTOOLS_VERSION}.tar.bz2 samtools-${SAMTOOLS_VERSION}.tar.bz2 && \
    ldconfig

# Set up environment variables for PharmCAT tools (aligned with official PharmCAT)
ENV BCFTOOLS_PATH=/usr/local/bin/bcftools
ENV BGZIP_PATH=/usr/local/bin/bgzip
ENV HTSLIB_PATH=/usr/local
ENV PCAT_PLATFORM=docker
ENV PATH="/pharmcat:/usr/local/bin:${PATH}"

# Download PharmCAT pipeline distribution
ARG PHARMCAT_VERSION
RUN wget -q https://github.com/PharmGKB/PharmCAT/releases/download/v${PHARMCAT_VERSION}/pharmcat-pipeline-${PHARMCAT_VERSION}.tar.gz \
    && mkdir -p /pharmcat/pipeline \
    && tar -xzf pharmcat-pipeline-${PHARMCAT_VERSION}.tar.gz -C /pharmcat/pipeline \
    && rm pharmcat-pipeline-${PHARMCAT_VERSION}.tar.gz

# Set up PharmCAT directory structure (aligned with official layout)
RUN mkdir -p /pharmcat/pcat /pharmcat/data

# Debug: List what's in the pipeline directory to see actual file structure
RUN ls -la /pharmcat/pipeline/

# Copy PharmCAT preprocessor components from pipeline distribution
# First, find and copy the JAR file (it might be named differently)
RUN find /pharmcat/pipeline -name "*.jar" -exec cp {} /pharmcat/pharmcat.jar \; && \
    echo "JAR file copied successfully" && \
    ls -la /pharmcat/pharmcat.jar

# Verify the JAR file is accessible and has proper permissions
RUN echo "Verifying JAR file..." && \
    ls -la /pharmcat/pharmcat.jar && \
    file /pharmcat/pharmcat.jar && \
    echo "JAR file verification complete"

# Copy other components (with error handling for missing files)
RUN if [ -f "/pharmcat/pipeline/pharmcat_vcf_preprocessor" ]; then \
        cp /pharmcat/pipeline/pharmcat_vcf_preprocessor /pharmcat/; \
    else \
        echo "Warning: pharmcat_vcf_preprocessor not found"; \
    fi && \
    if [ -f "/pharmcat/pipeline/pharmcat_pipeline" ]; then \
        cp /pharmcat/pipeline/pharmcat_pipeline /pharmcat/; \
    else \
        echo "Warning: pharmcat_pipeline not found"; \
    fi && \
    if [ -f "/pharmcat/pipeline/pharmcat_positions.vcf" ]; then \
        cp /pharmcat/pipeline/pharmcat_positions.vcf* /pharmcat/; \
    else \
        echo "Warning: pharmcat_positions.vcf not found"; \
    fi && \
    if [ -f "/pharmcat/pipeline/pharmcat_regions.bed" ]; then \
        cp /pharmcat/pipeline/pharmcat_regions.bed /pharmcat/; \
    else \
        echo "Warning: pharmcat_regions.bed not found"; \
    fi

# Copy reference genome files to pipeline directory where PharmCAT expects them
RUN if [ -f "/pharmcat/reference.fna.bgz" ]; then \
        cp /pharmcat/reference.fna.bgz /pharmcat/pipeline/; \
        echo "Reference genome copied to pipeline directory"; \
    else \
        echo "Warning: reference.fna.bgz not found"; \
    fi && \
    if [ -f "/pharmcat/reference.fna.bgz.gzi" ]; then \
        cp /pharmcat/reference.fna.bgz.gzi /pharmcat/pipeline/; \
        echo "Reference genome index copied to pipeline directory"; \
    else \
        echo "Warning: reference.fna.bgz.gzi not found"; \
    fi

# Set permissions for files that exist
RUN chmod 755 /pharmcat/pharmcat.jar && \
    if [ -f "/pharmcat/pharmcat_vcf_preprocessor" ]; then chmod 755 /pharmcat/pharmcat_vcf_preprocessor; fi && \
    if [ -f "/pharmcat/pharmcat_pipeline" ]; then chmod 755 /pharmcat/pharmcat_pipeline; fi

# Set up pcat directory with Python modules (if they exist in pipeline)
RUN if [ -d "/pharmcat/pipeline/pcat" ]; then \
        cp -r /pharmcat/pipeline/pcat/* /pharmcat/pcat/; \
    fi

# Install PharmCAT Python dependencies (using pip3 on Python base image)
RUN pip3 install -r /pharmcat/pipeline/requirements.txt

# Add PharmCAT pipeline scripts to path and verify installation
RUN chmod +x /pharmcat/pipeline/pharmcat_pipeline && \
    ln -sf /pharmcat/pipeline/pharmcat_pipeline /usr/local/bin/pharmcat_pipeline && \
    # Create a symlink in the pipeline directory so the script can find the JAR
    ln -sf /pharmcat/pharmcat.jar /pharmcat/pipeline/pharmcat.jar && \
    echo "Verifying PharmCAT installation..." && \
    ls -la /pharmcat/ && \
    ls -la /pharmcat/pipeline/ && \
    echo "Testing pharmcat_pipeline command..." && \
    pharmcat_pipeline --version && \
    echo "Testing pharmcat_pipeline help..." && \
    pharmcat_pipeline --help | head -5

# Install Flask wrapper API dependencies
COPY requirements.txt /tmp/
RUN pip3 install -r /tmp/requirements.txt

# Copy the wrapper script and start script
COPY pharmcat.py /app/
COPY start.sh /start.sh
RUN chmod +x /start.sh

# Create data directory and set up volume
RUN mkdir -p /data
VOLUME /data

# Set up environment variables (aligned with official PharmCAT)
ENV DATA_DIR=/data
ENV PHARMCAT_PIPELINE_DIR=/pharmcat/pipeline
ENV PHARMCAT_JAR=/pharmcat/pharmcat.jar
ENV PHARMCAT_HOME=/pharmcat
ENV CLASSPATH=/pharmcat/pharmcat.jar

# Expose port for the Flask API
EXPOSE 5000

# Set working directory for the API
WORKDIR /app

# Start the Flask API using the start script
CMD ["/start.sh"]
