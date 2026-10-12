---
title: Home
curation: not
---

# {{ project_name }}

ZaroPGx is a self-hosted pharmacogenomics pipeline. Upload a VCF, BCF, gVCF, BAM, CRAM, SAM
or FASTQ, and it runs PharmCAT, PyPGx, OptiType and mtDNA-Server 2 over it and writes a PDF
and an interactive report: genotypes and phenotypes for 91 genes, and the CPIC, DPWG and FDA
guidance that applies to them. It runs as a Docker Compose stack on your own machine.

It is research software, not a clinical test.

## Coverage

| | Genes | Called by |
|---|---:|---|
| PharmCAT's panel | 25 | PharmCAT (21); CYP2D6 from PyPGx, HLA-A and HLA-B from OptiType, MT-RNR1 from mtDNA-Server 2, as outside calls |
| Beyond PharmCAT | 65 | PyPGx |
| HLA-C | 1 | OptiType |

The panel's 25 genes carry the drug guidance. `config/genes.json` lists them all.

## Getting started

{doc}`user/installation`, then {doc}`user/usage`.

```{toctree}
:maxdepth: 2
:caption: User guide

user/installation
user/usage
user/file-formats
user/reports
user/configuration
user/deployment
user/troubleshooting
user/faq
```

```{toctree}
:maxdepth: 2
:caption: Developer guide

developer/architecture
developer/development-setup
developer/testing
developer/contributing
developer/api-reference
samples/README
```

```{toctree}
:maxdepth: 1
:caption: Roadmap

to-do
```
