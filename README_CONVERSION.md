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