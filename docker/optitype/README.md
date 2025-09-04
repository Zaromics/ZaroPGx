OptiType container integration

Overview

This directory documents how the upstream OptiType Docker image is integrated into the ZaroPGx compose stack to enable HLA class I typing (HLA-A, HLA-B, HLA-C) from FASTQ/BAM inputs.

Image

- Default image: smithnickh/optitype
- Tag: configured via environment variable OPTITYPE_IMAGE_TAG (default: latest)

Basic usage (inside container)

The image exposes the CLI. Typical run examples (DNA mode):

```
OptiTypePipeline.py -i /data/sample_R1.fastq /data/sample_R2.fastq --dna -o /data/results
```

Or RNA mode:

```
OptiTypePipeline.py -i /data/sample_R1.fastq /data/sample_R2.fastq --rna -o /data/results
```

Volumes and paths

- /data is mounted from host ./data to share inputs/outputs with the rest of the stack

Healthcheck

The container does not expose an HTTP API; the healthcheck verifies that `OptiTypePipeline.py` exists in PATH or common locations inside the container.

Examples with docker compose

- Run DNA typing with paired FASTQ:

```
docker compose exec optitype OptiTypePipeline.py -i /data/sample_R1.fastq /data/sample_R2.fastq --dna -o /data/results
```

- Run RNA typing with paired FASTQ:

```
docker compose exec optitype OptiTypePipeline.py -i /data/sample_R1.fastq /data/sample_R2.fastq --rna -o /data/results
```

- Run on BAM input (dna mode example):

```
docker compose exec optitype OptiTypePipeline.py -i /data/sample.bam --dna -o /data/results
```

Notes

- OptiType requires legacy dependencies (Python 2.7 and system tools) which are encapsulated by the upstream image to avoid conflicts with the main app.
- For large FASTQ/BAM files, prefer bind-mounting them into ./data and referencing paths under /data when invoking OptiType.


