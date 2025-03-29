# ZaroPGx Pipeline Conversion Guide
## Converting from Aldy to GATK+Stargazer

This guide outlines the steps to convert the ZaroPGx pipeline from using Aldy to using GATK for variant calling and Stargazer for CYP2D6 calling.

### Quick Start

1. Delete Aldy-related files:
```
rm -rf docker/aldy
rm test_aldy_multi_gene.py
rm debug_aldy.sh
rm ALDY_README.rst
```

2. Create directories for new services:
```
mkdir -p docker/gatk docker/stargazer
```

3. Copy the new Docker configurations and wrapper code to the appropriate directories.

4. Setup Poetry for dependency management:
```
pip install poetry
poetry install
```

5. Download reference genomes:
```
chmod +x setup_reference_genomes.sh
./setup_reference_genomes.sh
```

6. Rebuild and restart the Docker containers:
```
docker compose down
docker compose build gatk stargazer
docker compose up -d
```

### Major Components Added

1. **GATK Service**: Handles variant calling across all PGx regions of the genome
   - Location: docker/gatk/
   - Endpoints: 
     - /health - Check service status
     - /variant-call - Call variants from genomic files

2. **Stargazer Service**: Specialized CYP2D6 star allele calling
   - Location: docker/stargazer/
   - Endpoints:
     - /health - Check service status
     - /genotype - Call CYP2D6 star alleles

3. **Poetry Configuration**: Manages Python dependencies
   - Added pyproject.toml

### Reference Genomes

Reference genomes need to be downloaded and indexed for GATK to function properly. The `setup_reference_genomes.sh` script automates this process and downloads:

- hg19 (UCSC)
- hg38 (UCSC)
- GRCh37 (1000 Genomes)
- GRCh38 (symlink to hg38)

### Current Status and Known Issues

#### Progress Made:
1. Created a Genome Downloader service to handle downloading and indexing reference genomes
2. Set up container structure for GATK and Stargazer services
3. Created a GATK API wrapper service that handles HTTP requests and executes GATK commands
4. Added proper dependencies in docker-compose.yml to ensure services start in the correct order

#### Current Issues:
1. **GATK API Service Error**: The GATK API is returning a 500 Internal Server Error when called
   - Possible causes: GATK installation issues, path issues, file permissions
   - Next steps: Check the GATK API logs, test GATK commands directly in the container

2. **Stargazer Setup**: Stargazer needs a local ZIP file to be properly mounted and installed
   - Currently looking for the ZIP file at `/tmp/stargazer.zip` in the container

3. **Reference Genome Issues**: The genome downloader may be failing to properly index genomes
   - Need to verify reference genome files exist and are properly indexed

#### Troubleshooting Steps:
1. Check GATK API logs: `docker logs pgx_gatk_api`
2. Test GATK commands directly in the container:
   ```bash
   docker exec -it pgx_gatk_api /bin/bash
   gatk --version
   # Test a simple GATK command
   ```
3. Verify file paths and permissions:
   ```bash
   docker exec -it pgx_gatk_api ls -la /reference
   docker exec -it pgx_gatk_api ls -la /tmp
   ```

### Note about deployment

When deploying this application, make sure:

1. Reference genome data is properly mounted to containers
2. All services can communicate with each other via the internal Docker network
3. Port mappings are correctly configured

For production deployments, consider using private Docker registries and incorporating security best practices.

### Testing the Pipeline

To test the complete pipeline:

1. Upload a VCF or BAM file through the FastAPI endpoint
2. The file will be processed by GATK (if not already a VCF)
3. CYP2D6 star alleles will be called by Stargazer
4. The final processed VCF will be analyzed by PharmCAT
5. Check the final report generated 