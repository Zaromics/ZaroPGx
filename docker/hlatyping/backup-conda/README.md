# Conda Backup for nf-core/hlatyping

This directory contains the conda-based implementation of the hlatyping service as a backup.

## Files

- `Dockerfile.hlatyping.conda` - Dockerfile using conda/mamba for dependency management
- `configure_conda.sh` - Script to configure conda channels for bioconda
- `README.md` - This file

## Usage

To use the conda version instead of the Docker version:

1. Copy the conda Dockerfile to replace the main one:
   ```bash
   cp docker/hlatyping/backup-conda/Dockerfile.hlatyping.conda docker/hlatyping/Dockerfile.hlatyping
   ```

2. Update docker-compose.yml to use conda profile:
   ```yaml
   environment:
     - HLATYPING_PROFILE=conda
   ```

3. Remove the docker socket mount from docker-compose.yml:
   ```yaml
   volumes:
     - ./data:/data
     # - /var/run/docker.sock:/var/run/docker.sock  # Comment out this line
     - ./data/nextflow:/opt/nextflow
   ```

## Differences

- **Conda version**: Uses conda/mamba to install dependencies, runs with `-profile conda`
- **Docker version**: Uses Docker CLI to run containers, runs with `-profile docker`

## Notes

The conda version was previously the default but was switched to Docker for better reproducibility and performance. This backup is maintained in case conda is needed for specific environments where Docker-in-Docker is not available.
