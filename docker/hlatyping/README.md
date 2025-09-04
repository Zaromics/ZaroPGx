nf-core/hlatyping container wrapper

Overview

This service wraps the nf-core/hlatyping Nextflow pipeline and exposes a simple HTTP API to run HLA typing (OptiType-based) using Docker as the execution backend.

Image

- Builds a container with Java + Nextflow + Docker CLI + FastAPI wrapper.
- Requires mounting the host Docker socket for `-profile docker` execution.

API

- GET /health: basic health status
- POST /type: run HLA typing
  - form fields:
    - file1 (optional): FASTQ R1
    - file2 (optional): FASTQ R2
    - bam (optional): BAM file
    - seq_type: "dna" or "rna" (default: dna)
    - sample_name (optional): logical sample identifier
    - out_prefix (optional): used for naming only, defaults into sample_name

Notes

- Generates a per-run `samplesheet.csv` per nf-core/hlatyping documentation and invokes:
  `nextflow run nf-core/hlatyping -r 2.1.0 -profile docker --input samplesheet.csv --outdir <OUTDIR>`
- Results are persisted under `/data/results/hlatyping_<uuid>`.

References

- nf-core/hlatyping 2.1.0 usage and samplesheet format: see the repository README and docs.


