---
title: Deployment Guide
---

# Deployment Guide

Complete guide for deploying ZaroPGx in production environments.

## Deployment Overview

ZaroPGx can be deployed in various environments:
- **Local Development**: Docker Compose on local machine
- **Cloud Deployment**: AWS, Google Cloud, Azure
- **On-Premises**: Private data centers
- **Hybrid**: Combination of cloud and on-premises

## Prerequisites

### System Requirements

**Minimum Production Requirements:**
- 8 CPU cores
- 32 GB RAM
- 500 GB SSD storage
- Ubuntu 20.04+ or CentOS 8+
- Docker 20.10+
- Docker Compose 2.0+

**Recommended Production Requirements:**
- 16+ CPU cores
- 64+ GB RAM
- 1+ TB NVMe SSD storage
- Ubuntu 22.04 LTS
- Docker 24.0+
- Docker Compose 2.20+

### Network Requirements

**Ports:**
- 8765: Main application (configurable)
- 5444: PostgreSQL database (configurable)
- 5001: PharmCAT service (configurable)
- 5002: GATK API (configurable)
- 5053: PyPGx service (configurable)
- 8090: FHIR server (configurable)

**Firewall Configuration:**
```bash
# Allow required ports
sudo ufw allow 8765/tcp
sudo ufw allow 5444/tcp
sudo ufw allow 5001/tcp
sudo ufw allow 5002/tcp
sudo ufw allow 5053/tcp
sudo ufw allow 8090/tcp

# Enable firewall
sudo ufw enable
```

## Environment Configuration

### Production Environment

**Create production environment file:**
```bash
cp .env.production .env
```

**Production environment variables:**
```bash
# Security
SECRET_KEY=your-super-secret-key-here
ZAROPGX_DEV_MODE=false
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Database
POSTGRES_PASSWORD=your-secure-db-password
DB_HOST=db
DB_PORT=5432
DB_NAME=cpic_db
DB_USER=cpic_user
DB_PASSWORD=your-secure-db-password

# Application
BIND_ADDRESS=0.0.0.0
LOG_LEVEL=INFO
AUTHOR_NAME=Your Organization
SOURCE_URL=https://github.com/your-org/ZaroPGx

# Services
PHARMCAT_API_URL=http://pharmcat:5000
PYPGX_API_URL=http://pypgx:5000
GATK_API_URL=http://gatk-api:5000
FHIR_SERVER_URL=http://fhir-server:8080/fhir

# Features
GATK_ENABLED=true
PYPGX_ENABLED=true
OPTITYPE_ENABLED=true
KROKI_ENABLED=true
HAPI_FHIR_ENABLED=true

# Performance
MAX_CONCURRENT_WORKFLOWS=10
MAX_UPLOAD_SIZE_BYTES=1073741824  # 1GB
MAX_HEADER_READ_BYTES=1000000000  # 1GB

# PDF Generation
PDF_ENGINE=weasyprint
PDF_FALLBACK=true

# Report Options
INCLUDE_PHARMCAT_HTML=true
INCLUDE_PHARMCAT_JSON=false
INCLUDE_PHARMCAT_TSV=false
```

### Security Configuration

**Generate secure secrets:**
```bash
# Generate secret key
openssl rand -hex 32

# Generate database password
openssl rand -base64 32

# Generate JWT secret
openssl rand -hex 64
```

**SSL/TLS Configuration:**
```bash
# Generate SSL certificates
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes

# Or use Let's Encrypt
sudo apt install certbot
sudo certbot certonly --standalone -d your-domain.com
```

## Docker Compose Production

### Production Docker Compose

**Create production compose file:**
```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  app:
    build: ./docker/app
    ports:
      - "8765:8000"
    environment:
      - ZAROPGX_DEV_MODE=false
      - SECRET_KEY=${SECRET_KEY}
      - DB_PASSWORD=${DB_PASSWORD}
    volumes:
      - ./data:/data
      - ./reference:/reference
      - ./logs:/app/logs
    depends_on:
      - db
      - pharmcat
      - pypgx
      - gatk-api
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  db:
    image: postgres:15
    ports:
      - "5444:5432"
    environment:
      - POSTGRES_PASSWORD=${DB_PASSWORD}
      - POSTGRES_DB=cpic_db
      - POSTGRES_USER=cpic_user
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./db/init:/docker-entrypoint-initdb.d
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U cpic_user -d cpic_db"]
      interval: 30s
      timeout: 10s
      retries: 3

  pharmcat:
    build: ./docker/pharmcat
    ports:
      - "5001:5000"
    volumes:
      - ./data:/data
      - ./reference:/reference
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  pypgx:
    build: ./docker/pypgx
    ports:
      - "5053:5000"
    volumes:
      - ./data:/data
      - ./reference:/reference
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  gatk-api:
    build: ./docker/gatk-api
    ports:
      - "5002:5000"
    volumes:
      - ./data:/data
      - ./reference:/reference
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  fhir-server:
    image: hapiproject/hapi-fhir-jpaserver-starter:latest
    ports:
      - "8090:8080"
    environment:
      - SPRING_DATASOURCE_URL=jdbc:postgresql://db:5432/cpic_db
      - SPRING_DATASOURCE_USERNAME=cpic_user
      - SPRING_DATASOURCE_PASSWORD=${DB_PASSWORD}
    depends_on:
      - db
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/fhir/metadata"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  postgres_data:
    driver: local
```

### Deploy with Production Compose

```bash
# Deploy production stack
docker compose -f docker-compose.prod.yml up -d --build

# Check status
docker compose -f docker-compose.prod.yml ps

# View logs
docker compose -f docker-compose.prod.yml logs -f
```

## Cloud Deployment

### AWS Deployment

**EC2 Instance Setup:**
```bash
# Launch EC2 instance (t3.xlarge or larger)
# Install Docker
sudo apt update
sudo apt install docker.io docker-compose-plugin
sudo usermod -aG docker ubuntu

# Clone repository
git clone https://github.com/Zaroganos/ZaroPGx.git
cd ZaroPGx

# Configure environment
cp .env.production .env
# Edit .env with your settings

# Deploy
docker compose up -d --build
```

**EKS Deployment:**
```yaml
# kubernetes/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: zaro-pgx-app
spec:
  replicas: 3
  selector:
    matchLabels:
      app: zaro-pgx-app
  template:
    metadata:
      labels:
        app: zaro-pgx-app
    spec:
      containers:
      - name: app
        image: zaro-pgx-app:latest
        ports:
        - containerPort: 8000
        env:
        - name: DB_HOST
          value: "postgres-service"
        - name: SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: zaro-pgx-secrets
              key: secret-key
        resources:
          requests:
            memory: "4Gi"
            cpu: "2"
          limits:
            memory: "8Gi"
            cpu: "4"
```

### Google Cloud Deployment

**GKE Deployment:**
```bash
# Create GKE cluster
gcloud container clusters create zaro-pgx-cluster \
  --zone=us-central1-a \
  --num-nodes=3 \
  --machine-type=e2-standard-4

# Deploy application
kubectl apply -f kubernetes/
```

### Azure Deployment

**AKS Deployment:**
```bash
# Create AKS cluster
az aks create \
  --resource-group zaro-pgx-rg \
  --name zaro-pgx-cluster \
  --node-count 3 \
  --node-vm-size Standard_D4s_v3

# Deploy application
kubectl apply -f kubernetes/
```

## Database Deployment

### PostgreSQL Configuration

**Production PostgreSQL settings:**
```sql
-- postgresql.conf
shared_buffers = 4GB
effective_cache_size = 12GB
maintenance_work_mem = 1GB
checkpoint_completion_target = 0.9
wal_buffers = 16MB
default_statistics_target = 100
random_page_cost = 1.1
effective_io_concurrency = 200
```

**Database backup:**
```bash
# Create backup script
#!/bin/bash
# backup-db.sh
BACKUP_DIR="/backups/postgres"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/zaro_pgx_$DATE.sql"

mkdir -p $BACKUP_DIR
docker compose exec -T db pg_dump -U cpic_user cpic_db > $BACKUP_FILE
gzip $BACKUP_FILE

# Keep only last 30 days
find $BACKUP_DIR -name "*.sql.gz" -mtime +30 -delete
```

### Database Migration

**Run migrations:**
```bash
# Using Docker
docker compose exec app alembic upgrade head

# Or locally
alembic upgrade head
```

**Rollback migration:**
```bash
# Rollback to previous version
alembic downgrade -1

# Rollback to specific version
alembic downgrade <revision_id>
```

## Monitoring and Logging

### Application Monitoring

**Health checks:**
```bash
# Check application health
curl http://localhost:8765/health

# Check individual services
curl http://localhost:5001/health  # PharmCAT
curl http://localhost:5053/health  # PyPGx
curl http://localhost:5002/health  # GATK
```

**Resource monitoring:**
```bash
# Monitor container resources
docker stats

# Monitor system resources
htop
iostat -x 1
```

### Logging Configuration

**Centralized logging:**
```yaml
# docker-compose.logging.yml
version: '3.8'
services:
  app:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    volumes:
      - ./logs:/app/logs
```

**Log aggregation:**
```bash
# Install ELK stack
docker compose -f docker-compose.logging.yml up -d

# View logs
docker compose logs -f app
```

### Alerting

**Health check script:**
```bash
#!/bin/bash
# health-check.sh
HEALTH_URL="http://localhost:8765/health"
ALERT_EMAIL="admin@your-org.com"

if ! curl -f $HEALTH_URL > /dev/null 2>&1; then
    echo "ZaroPGx is down!" | mail -s "ZaroPGx Alert" $ALERT_EMAIL
    # Restart services
    docker compose restart
fi
```

**Cron job:**
```bash
# Add to crontab
*/5 * * * * /path/to/health-check.sh
```

## Security Hardening

### Container Security

**Security scanning:**
```bash
# Scan for vulnerabilities
docker scout cves zaro-pgx-app:latest

# Scan with Trivy
trivy image zaro-pgx-app:latest
```

**Non-root user:**
```dockerfile
# Dockerfile
FROM python:3.12-slim

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser
USER appuser

# Rest of Dockerfile
```

### Network Security

**Firewall rules:**
```bash
# UFW configuration
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow 8765/tcp
sudo ufw enable
```

**SSL/TLS termination:**
```nginx
# nginx.conf
server {
    listen 443 ssl;
    server_name your-domain.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://localhost:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Data Security

**Encryption at rest:**
```bash
# Encrypt data directory
sudo cryptsetup luksFormat /dev/sdb1
sudo cryptsetup open /dev/sdb1 encrypted_data
sudo mkfs.ext4 /dev/mapper/encrypted_data
sudo mount /dev/mapper/encrypted_data /data
```

**Backup encryption:**
```bash
# Encrypt backups
gpg --symmetric --cipher-algo AES256 backup.sql
```

## Performance Optimization

### Container Optimization

**Resource limits:**
```yaml
# docker-compose.override.yml
services:
  app:
    deploy:
      resources:
        limits:
          memory: 8G
          cpus: '4'
        reservations:
          memory: 4G
          cpus: '2'
```

**JVM optimization:**
```bash
# For Java services
JAVA_OPTS="-Xms4g -Xmx8g -XX:+UseG1GC -XX:MaxGCPauseMillis=200"
```

### Database Optimization

**Connection pooling:**
```python
# app/api/db.py
from sqlalchemy.pool import QueuePool

engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=20,
    max_overflow=30,
    pool_pre_ping=True
)
```

**Query optimization:**
```sql
-- Add indexes
CREATE INDEX idx_workflows_status ON workflows(status);
CREATE INDEX idx_workflow_steps_workflow_id ON workflow_steps(workflow_id);
CREATE INDEX idx_genetic_data_patient_id ON genetic_data(patient_id);
```

## Backup and Recovery

### Backup Strategy

**Database backup:**
```bash
#!/bin/bash
# backup-db.sh
BACKUP_DIR="/backups/postgres"
DATE=$(date +%Y%m%d_%H%M%S)

# Create backup
docker compose exec -T db pg_dump -U cpic_user cpic_db | gzip > "$BACKUP_DIR/db_$DATE.sql.gz"

# Upload to S3
aws s3 cp "$BACKUP_DIR/db_$DATE.sql.gz" s3://your-backup-bucket/
```

**File backup:**
```bash
#!/bin/bash
# backup-files.sh
BACKUP_DIR="/backups/files"
DATE=$(date +%Y%m%d_%H%M%S)

# Create tar archive
tar -czf "$BACKUP_DIR/files_$DATE.tar.gz" /data/reports /data/uploads

# Upload to S3
aws s3 cp "$BACKUP_DIR/files_$DATE.tar.gz" s3://your-backup-bucket/
```

### Recovery Process

**Database recovery:**
```bash
# Restore from backup
gunzip -c db_20240115_120000.sql.gz | docker compose exec -T db psql -U cpic_user cpic_db
```

**Full system recovery:**
```bash
# Stop services
docker compose down

# Restore data
tar -xzf files_20240115_120000.tar.gz -C /

# Restore database
gunzip -c db_20240115_120000.sql.gz | docker compose exec -T db psql -U cpic_user cpic_db

# Start services
docker compose up -d
```

## Troubleshooting

### Common Issues

**Out of memory:**
```bash
# Check memory usage
free -h
docker stats

# Increase swap
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

**Disk space issues:**
```bash
# Check disk usage
df -h
du -sh /data/*

# Clean up old data
find /data/reports -mtime +30 -type d -exec rm -rf {} +
```

**Service connectivity:**
```bash
# Check network
docker network ls
docker network inspect zaro-pgx_pgx-network

# Test connectivity
docker compose exec app curl http://pharmcat:5000/health
```

### Performance Issues

**Slow processing:**
```bash
# Check resource usage
htop
iostat -x 1

# Optimize Docker settings
# Increase memory and CPU limits
# Use SSD storage
```

**Database performance:**
```sql
-- Check slow queries
SELECT query, mean_time, calls 
FROM pg_stat_statements 
ORDER BY mean_time DESC 
LIMIT 10;

-- Check table sizes
SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
FROM pg_tables 
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
```

## Next Steps

- **Architecture Overview**: {doc}`architecture`
- **API Reference**: {doc}`api-reference`
- **Development Setup**: {doc}`development-setup`
- **Contributing**: {doc}`contributing`
