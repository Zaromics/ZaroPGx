# ZaroHLA

ZaroHLA is a containerized, API-driven wrapper around [OptiType v1.5.0](https://github.com/FRED-2/OptiType), providing precise HLA Class I genotyping from NGS data via a modern web interface.

## Overview

This repository provides a lightweight FastAPI service that exposes the modernized OptiType v1.5 genotyping algorithm. By leveraging the pip-installable version of OptiType (sourced directly from the FRED-2 GitHub repository, pinned to the `v1.5.0` tag), ZaroHLA handles the complexities of dependency management, data formatting, and execution within a clean, isolated Docker environment.

### Features
* **Native OptiType 1.5 Integration:** Uses the updated, high-performance Click-based CLI (`optitype run`) rather than the legacy `OptiTypePipeline.py` script.
* **YARA Read Mapping by Default:** Utilizes the YARA read mapper (via the Debian `seqan-apps` package) to ensure robust, fast read mapping for the ILP solver. 
* **FastAPI Backend:** A fast, asynchronous HTTP API allowing for easy integration into larger bioinformatics pipelines.
* **Containerized Deployment:** Fully reproducible builds using Docker.

## Getting Started

### Prerequisites
* [Docker](https://docs.docker.com/get-docker/)

### Building the Image

To build the Docker image locally, run:

```bash
docker build -t zarohla:latest .
```

### Running the Service

You can start the service using the provided test script or manually via Docker:

```bash
# Using the test script
./run_test.sh

# Or manually via Docker
mkdir -p data
docker run -d -p 5000:5000 --name zarohla -v $(pwd)/data:/data zarohla:latest
```

The API will be available at `http://localhost:5000`. You can check the health of the service at `http://localhost:5000/health`.

## API Usage

### `POST /call-hla`

Submit paired-end sequencing reads to initiate an HLA typing run.

**Parameters (Form Data):**
* `file1`: The first FASTQ file (UploadFile).
* `file2`: The second FASTQ file (UploadFile).
* `seq_type`: The sequencing type, e.g., `dna` or `rna` (default: `dna`).
* `mapper`: The read mapper to use, e.g., `yara` or `razers3` (default: `yara`).
* `outdir`: The directory to store the results within the container (default: `/data/results`).

**Example Request:**

```bash
curl -X 'POST' \
  'http://localhost:5000/call-hla' \
  -H 'accept: application/json' \
  -H 'Content-Type: multipart/form-data' \
  -F 'file1=@patient_1.fastq' \
  -F 'file2=@patient_2.fastq' \
  -F 'seq_type=dna' \
  -F 'mapper=yara'
```

## Maintenance Notes
* **Version pin:** `Dockerfile` installs OptiType pinned to `git+https://github.com/FRED-2/OptiType.git@v1.5.0` (previously floated on `master`, which was non-reproducible). Bump the `@v1.5.0` tag deliberately when a newer OptiType release ships.
* **OptiType PyPI Status:** Once OptiType officially publishes version 1.5.0+ to the public PyPI registry, the `Dockerfile` can be safely switched from the pinned GitHub installation to standard pip (`pip install optitype==<version>`).
