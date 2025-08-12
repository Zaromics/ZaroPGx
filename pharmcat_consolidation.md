# PharmCAT Service Consolidation Guide

## Converting from Dual-Container to Unified PharmCAT Service

This guide outlines the steps to merge the two PharmCAT-related containers into a single unified container that provides both the PharmCAT JAR functionality and the REST API wrapper.

### Quick Start

1. Create a new unified Dockerfile:
```bash
mkdir -p docker/pharmcat-unified
touch docker/pharmcat-unified/Dockerfile
```

2. Copy the wrapper code and dependencies:
```bash
cp docker/pharmcat/pharmcat.py docker/pharmcat-unified/
# Note: dependency management now uses uv for the main app; PharmCAT image still uses its own requirements file internally if needed.
cp docker/pharmcat/start.sh docker/pharmcat-unified/ # if needed
```

3. Update your docker-compose.yml to use the new unified container:
```bash
# Edit docker-compose.yml to replace both pharmcat and pharmcat services
```

4. Rebuild and restart the Docker containers:
```bash
docker compose down
docker compose build pharmcat-unified
docker compose up -d
```

### New Unified Dockerfile

Create a new file at `docker/pharmcat-unified/Dockerfile` with the following content:

```dockerfile
FROM openjdk:17-slim

WORKDIR /pharmcat

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    unzip \
    curl \
    python3-pip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Download PharmCAT release
ENV PHARMCAT_VERSION=3.0.0
RUN wget -q https://github.com/PharmGKB/PharmCAT/releases/download/v${PHARMCAT_VERSION}/pharmcat-${PHARMCAT_VERSION}-all.jar \
    && mv pharmcat-${PHARMCAT_VERSION}-all.jar pharmcat.jar \
    && chmod +x pharmcat.jar

# Create script for running pharmcat_pipeline
RUN echo '#!/bin/sh\n\
java -jar /pharmcat/pharmcat.jar pipeline "$@"' > /pharmcat/pharmcat_pipeline && \
    chmod +x /pharmcat/pharmcat_pipeline && \
    ln -s /pharmcat/pharmcat_pipeline /usr/local/bin/pharmcat_pipeline

# Create health check script for JAR
RUN echo '#!/bin/sh\n\
if [ -f "/pharmcat/pharmcat.jar" ]; then\n\
  echo "{\\"status\\":\\"ok\\",\\"service\\":\\"pharmcat\\"}" \n\
  exit 0\n\
else\n\
  echo "{\\"status\\":\\"error\\",\\"service\\":\\"pharmcat\\"}" \n\
  exit 1\n\
fi' > /pharmcat/health.sh && chmod +x /pharmcat/health.sh

# Create data directory
RUN mkdir -p /data
VOLUME /data

# Install Python dependencies
COPY requirements.txt .
RUN pip3 install -r requirements.txt

# Copy the wrapper script
COPY pharmcat.py /app/

# Set up environment variables
ENV DATA_DIR=/data
ENV PHARMCAT_JAR=/pharmcat/pharmcat.jar
ENV PATH=/pharmcat:/usr/local/bin:${PATH}

# Expose port for the Flask API
EXPOSE 5000

# Set working directory for the API
WORKDIR /app

# Start the Flask API (or use start.sh if you have one)
CMD ["python3", "/app/pharmcat.py"]
```

### Docker-Compose Changes

Update your `docker-compose.yml` file to replace both `pharmcat` and `pharmcat` services with:

```yaml
# PharmCAT Unified Service
pharmcat-unified:
  build:
    context: ./docker/pharmcat-unified
    dockerfile: Dockerfile
  container_name: pgx_pharmcat
  restart: unless-stopped
  volumes:
    - ./data:/data
  ports:
    - "5001:5000"  # Flask API port
  environment:
    - DATA_DIR=/data
    - JAVA_OPTS=-Xmx4g
    - PHARMCAT_LOG_LEVEL=DEBUG
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
    interval: 10s
    timeout: 5s
    retries: 5
    start_period: 30s
  networks:
    - pgx-network
```

### Volume Changes

Remove the `pharmcat-jar` volume from the `volumes:` section in docker-compose.yml as it's no longer needed.

### Dependency Updates

Update all references in your application code:
- Replace `http://pharmcat:5000` with `http://pharmcat-unified:5000`
- Remove any references to `http://pharmcat:8080` as it won't exist anymore

### Major Benefits

1. **Simplified Architecture**:
   - Single container handling all PharmCAT operations
   - No need to share JAR files between containers via volumes
   - Direct access to PharmCAT JAR from the API

2. **Improved Performance**:
   - Eliminates network overhead between components
   - Reduces container startup dependencies
   - More efficient resource utilization

3. **Easier Maintenance**:
   - Single Dockerfile to maintain
   - Simpler updates when new PharmCAT versions are released
   - Fewer moving parts in the overall system

### Testing the Unified Service

To test the consolidated PharmCAT service:

1. Start the container:
```bash
docker compose up -d pharmcat-unified
```

2. Check if the service is running:
```bash
curl http://localhost:5001/health
```

3. Test processing a VCF file:
```bash
curl -X POST -F 'file=@/path/to/test.vcf' http://localhost:5001/process
```

4. Verify the system is working with the main application:
```bash
curl -X POST -F 'genome=@sample.vcf' http://localhost:8765/generate-report
```

This consolidation simplifies your architecture while maintaining all functionality from both containers. 