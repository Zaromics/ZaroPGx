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

# Download PharmCAT pipeline distribution (v3.0.0)
ENV PHARMCAT_VERSION=3.0.0
RUN wget -q https://github.com/PharmGKB/PharmCAT/releases/download/v${PHARMCAT_VERSION}/pharmcat-pipeline-${PHARMCAT_VERSION}.tar.gz \
    && mkdir -p /pharmcat/pipeline \
    && tar -xzf pharmcat-pipeline-${PHARMCAT_VERSION}.tar.gz -C /pharmcat/pipeline \
    && rm pharmcat-pipeline-${PHARMCAT_VERSION}.tar.gz \
    && ln -s /pharmcat/pipeline/pharmcat-${PHARMCAT_VERSION}-all.jar /pharmcat/pharmcat.jar

# Install PharmCAT Python dependencies
RUN pip3 install -r /pharmcat/pipeline/requirements.txt

# Add PharmCAT pipeline scripts to path
ENV PATH="/pharmcat/pipeline:${PATH}"

# Install Flask wrapper API dependencies
COPY requirements.txt /tmp/
RUN pip3 install -r /tmp/requirements.txt

# Copy the wrapper script and start script
COPY pharmcat_wrapper.py /app/
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
