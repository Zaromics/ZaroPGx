OptiType container integration

Overview

This directory documents how the upstream OptiType Docker image is integrated into the ZaroPGx compose stack to enable HLA class I typing (HLA-A, HLA-B, HLA-C) from FASTQ/BAM inputs.

Image

- Default image: fred2/optitype
- Tag: configured via environment variable OPTITYPE_IMAGE_TAG (default: latest)

Basic usage (inside container)

The upstream image exposes the CLI. Typical run examples (DNA mode):

```
python /OptiTypePipeline.py -i /data/sample_R1.fastq /data/sample_R2.fastq --dna -o /data/results
```

Or RNA mode:

```
python /OptiTypePipeline.py -i /data/sample_R1.fastq /data/sample_R2.fastq --rna -o /data/results
```

Volumes and paths

- /data is mounted from host ./data to share inputs/outputs with the rest of the stack

Healthcheck

The image does not expose an HTTP API; the healthcheck simply verifies that the OptiType pipeline script is present and Python can import key dependencies.

Notes

- OptiType requires legacy dependencies (Python 2.7 and system tools) which are encapsulated by the upstream image to avoid conflicts with the main app.
- For large FASTQ/BAM files, prefer bind-mounting them into ./data and referencing paths under /data when invoking OptiType.


