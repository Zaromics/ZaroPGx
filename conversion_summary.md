# ZaroPGx Pipeline Conversion Summary

## Overview
We have updated the ZaroPGx pipeline to replace Aldy with GATK for variant calling and Stargazer for CYP2D6 star allele calling. This document summarizes the changes made and provides steps to implement them.

## Pipeline Changes

### Previous Pipeline:
Aldy → PharmCAT

### New Pipeline:
GATK → Stargazer (for CYP2D6 only) → PharmCAT

## Changes Made

1. Created `pyproject.toml` for Poetry dependency management.
2. Updated `docker-compose.yml` to replace Aldy service with GATK and Stargazer services.
3. Created new Dockerfiles and service wrappers:
   - docker/gatk/Dockerfile.gatk
   - docker/gatk/gatk_wrapper.py
   - docker/gatk/requirements.txt
   - docker/stargazer/Dockerfile.stargazer
   - docker/stargazer/stargazer_wrapper.py
   - docker/stargazer/requirements.txt
4. Updated environment variables in the main application.

## Files to Delete

The following files should be deleted as they're no longer needed:
- docker/aldy/Dockerfile.aldy
- docker/aldy/aldy_wrapper.py
- test_aldy_multi_gene.py
- debug_aldy.sh
- ALDY_README.rst

## Implementation Steps

1. Create directories for the new services:
   ```
   mkdir -p docker/gatk docker/stargazer
   ```

2. Copy the new Dockerfiles, wrapper code, and requirements files to their respective directories.

3. Delete the files that are no longer needed.

4. Update the app's main code to use GATK and Stargazer instead of Aldy.

5. Setup Poetry for dependency management:
   ```
   pip install poetry
   poetry init
   poetry install
   ```

6. Rebuild and restart the services:
   ```
   docker compose down
   docker compose build gatk stargazer
   docker compose up -d
   ```

## Notes

- GATK service provides variant calling for all PGx-relevant regions.
- Stargazer is specifically for CYP2D6 star allele calling.
- The PharmCAT service remains unchanged.
- Reference genome files will need to be downloaded as part of the setup process. 