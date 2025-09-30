---
title: Troubleshooting Guide
---

# Troubleshooting Guide

Common issues and solutions for ZaroPGx.

## Installation Issues

### Docker Not Starting

**Symptoms:**
- `docker compose up` fails
- Services show "Exited" status
- Port binding errors

**Solutions:**
```bash
# Check Docker status
docker --version
docker compose version

# Restart Docker service
sudo systemctl restart docker  # Linux
# Or restart Docker Desktop (macOS/Windows)

# Check port availability
netstat -tulpn | grep :8765
netstat -tulpn | grep :5444

# Change ports in .env file if needed
BIND_ADDRESS=8766  # Use different port
```

### Out of Memory Errors

**Symptoms:**
- Containers killed with "OOMKilled" status
- Processing fails with memory errors
- System becomes unresponsive

**Solutions:**
```bash
# Check available memory
free -h
docker stats

# Increase Docker memory limit
# Docker Desktop: Settings → Resources → Memory → 8GB+

# Reduce memory usage
# Disable optional services in .env
GATK_ENABLED=false
PYPGX_ENABLED=false
```

### Permission Errors

**Symptoms:**
- "Permission denied" errors
- Cannot write to data directories
- Container startup failures

**Solutions:**
```bash
# Fix directory permissions
sudo chown -R $USER:$USER .
chmod -R 755 data/
chmod -R 755 reference/

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

## File Upload Issues

### Upload Failures

**Symptoms:**
- Files fail to upload
- "Invalid file format" errors
- Upload timeout errors

**Solutions:**
```bash
# Check file format
file sample.vcf
head -20 sample.vcf

# Validate VCF file
bcftools view sample.vcf > /dev/null

# Check file size
ls -lh sample.vcf

# Increase upload timeout in .env
MAX_UPLOAD_TIMEOUT_SEC=600
```

### File Processing Errors

**Symptoms:**
- Processing stops at specific stage
- "Invalid reference genome" errors
- "Missing index file" warnings

**Solutions:**
```bash
# Check file headers
bcftools view -h sample.vcf

# Verify reference genome
grep "reference" sample.vcf

# Include index files
# Upload both .vcf.gz and .vcf.gz.tbi

# Check file integrity
md5sum sample.vcf
```

## Analysis Issues

### Processing Hangs

**Symptoms:**
- Progress stops at specific percentage
- No updates for extended time
- Container logs show no activity

**Solutions:**
```bash
# Check container status
docker compose ps
docker compose logs app

# Check resource usage
docker stats

# Restart specific service
docker compose restart app

# Check disk space
df -h
```

### Low Quality Results

**Symptoms:**
- Many "No Call" results
- Low confidence scores
- Missing genes in results

**Solutions:**
```bash
# Check coverage depth
samtools depth input.bam | awk '{sum+=$3} END {print sum/NR}'

# Verify file quality
fastqc sample.fastq

# Check reference genome
ls -la reference/

# Review analysis parameters
# Enable additional tools in .env
```

### Memory Issues During Processing

**Symptoms:**
- Containers killed during analysis
- "Out of memory" errors in logs
- Processing fails on large files

**Solutions:**
```bash
# Increase container memory
# Edit docker-compose.yml
services:
  app:
    deploy:
      resources:
        limits:
          memory: 8G

# Use smaller reference genome
# Download targeted reference subset

# Process files in smaller batches
# Split large VCF files
```

## Report Generation Issues

### PDF Generation Fails

**Symptoms:**
- No PDF report generated
- "PDF generation failed" errors
- HTML report works but PDF doesn't

**Solutions:**
```bash
# Check PDF engine
docker compose logs app | grep -i pdf

# Switch PDF engine
PDF_ENGINE=reportlab  # or weasyprint

# Check disk space
df -h

# Review report template
ls -la app/reports/templates/
```

### Missing Report Sections

**Symptoms:**
- Empty report sections
- "No data available" messages
- Incomplete analysis results

**Solutions:**
```bash
# Check analysis logs
docker compose logs pharmcat
docker compose logs pypgx

# Verify data files
ls -la data/reports/{job_id}/

# Check report configuration
grep -r "INCLUDE_" .env
```

## Database Issues

### Connection Errors

**Symptoms:**
- "Database connection failed" errors
- Services can't connect to database
- Authentication failures

**Solutions:**
```bash
# Check database status
docker compose ps db
docker compose logs db

# Test database connection
docker exec -it zaro-pgx-db psql -U cpic_user -d cpic_db

# Reset database
docker compose down
docker volume rm zaro-pgx_db_data
docker compose up -d --build
```

### Data Corruption

**Symptoms:**
- Inconsistent results
- Missing data in reports
- Database errors in logs

**Solutions:**
```bash
# Check database integrity
docker exec -it zaro-pgx-db psql -U cpic_user -d cpic_db -c "VACUUM ANALYZE;"

# Backup and restore
./scripts/db-backup.sh
./scripts/db-restore.sh

# Reinitialize database
docker compose down
docker volume rm zaro-pgx_db_data
docker compose up -d --build
```

## Network Issues

### Service Communication Errors

**Symptoms:**
- "Service unavailable" errors
- Timeout errors between services
- API calls fail

**Solutions:**
```bash
# Check network connectivity
docker network ls
docker network inspect zaro-pgx_pgx-network

# Test service endpoints
curl http://localhost:5001/health  # PharmCAT
curl http://localhost:5002/health  # GATK
curl http://localhost:5053/health  # PyPGx

# Restart services
docker compose restart
```

### Port Conflicts

**Symptoms:**
- "Port already in use" errors
- Services fail to start
- Cannot access web interface

**Solutions:**
```bash
# Check port usage
netstat -tulpn | grep :8765
lsof -i :8765

# Kill conflicting processes
sudo kill -9 $(lsof -t -i:8765)

# Change ports in .env
BIND_ADDRESS=8766
```

## Performance Issues

### Slow Processing

**Symptoms:**
- Analysis takes much longer than expected
- High CPU usage but slow progress
- System becomes unresponsive

**Solutions:**
```bash
# Check resource usage
docker stats
htop

# Optimize Docker settings
# Increase memory and CPU limits
# Use SSD storage for better I/O

# Disable optional services
GATK_ENABLED=false
OPTITYPE_ENABLED=false

# Use smaller reference genome
# Process smaller file subsets
```

### High Memory Usage

**Symptoms:**
- System runs out of memory
- Swap usage increases
- Containers killed frequently

**Solutions:**
```bash
# Monitor memory usage
free -h
docker stats

# Increase system memory
# Add swap space
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Optimize container memory
# Set memory limits in docker-compose.yml
```

## Log Analysis

### Viewing Logs

**All Services:**
```bash
docker compose logs
```

**Specific Service:**
```bash
docker compose logs app
docker compose logs db
docker compose logs pharmcat
```

**Follow Logs:**
```bash
docker compose logs -f app
```

**Filter Logs:**
```bash
docker compose logs app | grep ERROR
docker compose logs pharmcat | grep -i "failed"
```

### Common Log Messages

**ERROR: Database connection failed**
- Check database service status
- Verify connection parameters
- Check network connectivity

**WARNING: Low coverage detected**
- Check input file quality
- Verify reference genome
- Consider increasing coverage

**INFO: Processing completed successfully**
- Analysis finished normally
- Check for report files
- Review results

## Getting Help

### Self-Diagnosis

1. **Check Service Status**: `docker compose ps`
2. **Review Logs**: `docker compose logs`
3. **Check Resources**: `docker stats`
4. **Verify Configuration**: Review `.env` file
5. **Test Connectivity**: Use curl commands

### Community Support

**GitHub Issues:**
- Search existing issues
- Create new issue with logs
- Include system information
- Provide reproducible steps

**Documentation:**
- Review this troubleshooting guide
- Check API documentation
- Consult tool-specific docs

**Community Forums:**
- Join discussions on GitHub
- Ask questions in discussions
- Share solutions and tips

### Professional Support

**For Production Deployments:**
- Contact support team
- Request professional services
- Schedule consultation
- Get custom configuration help

## Next Steps

- **Learn about usage**: {doc}`usage`
- **Understand file formats**: {doc}`file-formats`
- **Configure advanced settings**: {doc}`../advanced-configuration`
- **Review FAQ**: {doc}`faq`
