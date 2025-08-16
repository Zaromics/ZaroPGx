FROM openjdk:17-slim

WORKDIR /pharmcat

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    unzip \
    curl \
    python3-pip \
    build-essential \
    git \
    autoconf \
    automake \
    libtool \
    pkg-config \
    zlib1g-dev \
    libbz2-dev \
    liblzma-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install bcftools and htslib from source
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

# Set up environment variables for PharmCAT tools
ENV BCFTOOLS_PATH=/usr/local/bin/bcftools
ENV BGZIP_PATH=/usr/local/bin/bgzip

# Download PharmCAT pipeline distribution
ARG PHARMCAT_VERSION
RUN wget -q https://github.com/PharmGKB/PharmCAT/releases/download/v${PHARMCAT_VERSION}/pharmcat-pipeline-${PHARMCAT_VERSION}.tar.gz \
    && mkdir -p /pharmcat/pipeline \
    && tar -xzf pharmcat-pipeline-${PHARMCAT_VERSION}.tar.gz -C /pharmcat/pipeline \
    && rm pharmcat-pipeline-${PHARMCAT_VERSION}.tar.gz \
    && ln -s /pharmcat/pipeline/pharmcat-${PHARMCAT_VERSION}-all.jar /pharmcat/pharmcat.jar

# Install uv and PharmCAT Python dependencies
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
RUN uv pip install --system -r /pharmcat/pipeline/requirements.txt

# Add PharmCAT pipeline scripts to path
RUN chmod +x /pharmcat/pipeline/pharmcat_pipeline && \
    ln -sf /pharmcat/pipeline/pharmcat_pipeline /usr/local/bin/pharmcat_pipeline
ENV PATH="/pharmcat/pipeline:/usr/local/bin:${PATH}"

# Install Flask wrapper API dependencies
COPY requirements.txt /tmp/
RUN uv pip install --system -r /tmp/requirements.txt

# Copy the wrapper script and start script
COPY pharmcat.py /app/
COPY start.sh /start.sh
RUN chmod +x /start.sh

# Create data directory
RUN mkdir -p /data
VOLUME /data

# Set up environment variables
ENV DATA_DIR=/data
ENV PHARMCAT_JAR=/pharmcat/pharmcat.jar

# Expose port for the Flask API
EXPOSE 5000

# Set working directory for the API
WORKDIR /app

# Start the Flask API using the start script
CMD ["/start.sh"]
