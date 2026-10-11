# ZaroPGx — A Pharmacogenomic Analysis Platform
**See the analysis in action: click below to watch a demo on YouTube:**
[<img src="https://img.youtube.com/vi/FzKI48IQb3I/hqdefault.jpg" width="600" height="400"
/>](https://www.youtube.com/embed/FzKI48IQb3I)

---
## What ZaroPGx Does

ZaroPGx is a self-hosted pharmacogenomics pipeline. Upload a VCF, BCF, gVCF, BAM, CRAM, SAM or
short-read FASTQ; it converts or aligns the input as needed, calls star alleles with PyPGx, HLA
types with OptiType, calls mitochondrial variants with mtDNA-Server 2, and runs PharmCAT with
those calls as outside calls. The result is a PDF and an interactive HTML report: diplotypes and
phenotypes for up to 91 genes, and the CPIC, DPWG and FDA guidance that applies to them, along
with PharmCAT's own reports and a FHIR R4 bundle.

| | Genes | Called by |
|---|---:|---|
| PharmCAT's panel | 25 | PharmCAT (21); CYP2D6 from PyPGx, HLA-A and HLA-B from OptiType, MT-RNR1 from mtDNA-Server 2 |
| Beyond PharmCAT | 65 | PyPGx |
| HLA-C | 1 | OptiType |

It runs as a Docker Compose stack on your own machine; your data stays in its `data/`
directory. It is research software, not a clinical test.

### 🚀 Quickstart -- One-Command Setup

**PowerShell (Windows):**
```powershell
iwr -useb https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.ps1 | iex
```

**Bash (Linux / macOS / WSL):**
```bash
curl -fsSL https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.sh | bash
```

**Stock Alpine (run as root; installs Bash and curl first):**
```sh
apk add --no-cache bash curl && curl -fsSL https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.sh | bash
```

The bootstrap checks for Git, Docker and Docker Compose and offers to install what is missing,
clones the repository at the release tag, and starts the stack with the matching images. If it
installed Docker or WSL, you may need to log out or reboot and run it again. To update later,
run it with `--update` (Bash) or `-Update` (PowerShell). The scripts are short; read them first
if you prefer: [bootstrap.sh](bootstrap.sh), [bootstrap.ps1](bootstrap.ps1).

The first start takes 8-15 minutes while PharmCAT downloads its reference data. Then open
`http://localhost:8765`.

<img width="600" height="1100" alt="zaropgx_demo" src="https://github.com/user-attachments/assets/50de2e8d-b496-424b-b2fb-0d34d7e39505" />

### Manual setup

```bash
git clone https://github.com/Zaromics/ZaroPGx.git
cd ZaroPGx
cp .env.local .env
./start-docker.sh        # or .\start-docker.ps1; generates DB_PASSWORD and SECRET_KEY
```

Requirements: Docker with Compose v2.24+, and Git; Linux, Windows with WSL 2, or macOS with
Docker Desktop. The images are `linux/amd64` only (Apple Silicon needs Rosetta emulation). A
VCF needs little memory; alignments need much more (the stack allows GATK up to 28 GB and
PyPGx up to 20 GB). See [Installation](docs/user/installation.md).

**Access.** With the default `BIND_ADDRESS=8765` the app listens on every network interface,
and there is no login (`ZAROPGX_AUTH_MODE=open`). On a shared network, set
`BIND_ADDRESS=127.0.0.1:8765`, or turn on the shared password. To serve it beyond your machine,
see [Running a server](docs/user/deployment.md).

## Documentation

The full documentation is served by the app at `http://localhost:8765/docs`, and is in
[`docs/`](docs/index.md):

- [Running an analysis](docs/user/usage.md), [input formats](docs/user/file-formats.md),
  [reports](docs/user/reports.md)
- [Configuration](docs/user/configuration.md), [running a server](docs/user/deployment.md),
  [troubleshooting](docs/user/troubleshooting.md), [FAQ](docs/user/faq.md)
- [Architecture](docs/developer/architecture.md),
  [development setup](docs/developer/development-setup.md), [testing](docs/developer/testing.md),
  [contributing](docs/developer/contributing.md), [API](docs/developer/api-reference.md)
- [UPGRADING.md](UPGRADING.md): what to do when moving between releases

## Status

**This project is in early development.** NGS-derived GRCh38 VCFs are the most mature input.
The end-to-end tests run VCF, GRCh37 VCF, BAM, CRAM and SAM inputs through the whole stack;
BCF, gVCF and FASTQ have unit tests only. On the NA12878 (GIAB HG001) 30x genome as BAM and
CRAM, PharmCAT's calls matched the truth set for 19 of 19 genes, and HLA typing for 6 of 6
alleles (October 2026).

*Input formats*
- [X] Priority 0 (*Supported*): **VCF, GRCh38**/hg38, NGS-derived.
- [X] Priority 1 (*Supported*): **VCF, GRCh37**/hg19, NGS-derived. *Lifted over to GRCh38 before analysis.*
- [...] Priority 2 (*Testing*): **BAM, CRAM, SAM, BCF, gVCF**, all NGS-derived, GRCh38. *gVCFs need GATK reference blocks; CRAMs need one of three GRCh38 references.*
- [...] Priority 2 (*Testing*): **FASTQ**, short-read, single- or paired-end, up to 20 GB. *Aligned to GRCh38, then analysed as a BAM.*
- [...] Priority 3 (*Research*): Other sequencing and genotyping formats.
- [...] Priority 4 (*Research*): BED, 23andMe, AncestryDNA, various TXT formats. *Detected and refused for now.*
- [...] Priority 5 (*Early research*): T2T-CHM13, and others. *Detected and refused for now.*

What each format needs, and why some are refused: [input formats](docs/user/file-formats.md).

*Features*
- [X] Priority 0 (*Supported*): **PDF and interactive HTML custom in-house reports.**
- [X] Priority 1 (*Supported*): **mtDNA-Server 2**: mitochondrial variants, haplogroup and the MT-RNR1 outside call; mtDNA-Server 2's own report for alignments.
- [o] Priority 2 (*Development*): **FHIR R4 offline export** as JSON and XML, written with every report; custom PharmCAT definitions for outside calls.
- [...] Priority 3 (*Development*): Interactive HTML enhancements with useful visualizations, fully DB-oriented data handling. *Projected release in v0.4-0.5*
- [...] Priority 4 (*Research*): FHIR online export direct to PHR/EHR
- [...] Priority 5 (*Early research*): In-depth and targeted analytics, with specialty curation to reduce cognitive load
- [...] Priority 6 (*Early research*): Complete transition to fully DB-based workflow; pulling and normalizing data directly from published databases

## Sample Data

Example inputs in the repository: `app/static/demo/pharmcat.example.vcf` (the **Run Demo**
button), and in `test_data/`, PharmCAT's example VCFs, GRCh37 and T2T SNP sets, and a small
BAM, CRAM and SAM. For real-world data, try the **Personal Genome Project**:
https://my.pgp-hms.org/public_genetic_data

## Contributions are welcome and are gratefully appreciated!

See [contributing](docs/developer/contributing.md).

## Acknowledgements & Citations
**This section is incomplete as the constituent software components are being assembled and all relevant research and clinical publications are being compiled.
If your work is a part of ZaroPGx and you wish to add or amend text recognizing yourself, your work, and/or your organization, please send me a message and I will gladly attend to your request.**

- **PharmCAT** (Pharmacogenomics Clinical Annotation Tool, Pharmacogenomics Knowledge Base, managed at Stanford University & University of Pennsylvania)
  - Sangkuhl K, Whirl-Carrillo M, et al. *Clinical Pharmacology & Therapeutics.* 2020;107(1):203–210; Li B, Sangkuhl K, et al. How to Run the Pharmacogenomics Clinical Annotation Tool (PharmCAT). *Clinical Pharmacology & Therapeutics.* 2023;113(5):1036–1047.  Docs: https://pharmcat.clinpgx.org/
- **PyPGx** (by Dr. Seung-been "Steven" Lee)
  - Lee S‑B, et al. *PLOS ONE.* 2022 (ClinPharmSeq); Lee S‑B, et al. *Genetics in Medicine.* 2018 (Stargazer); Lee S‑B, et al. *Clinical Pharmacology & Therapeutics.* 2019 (Stargazer, 28 genes).  Docs: https://pypgx.readthedocs.io/en/latest/index.html
- **ZaroHLA** (based on OptiType, by Schubert B, Kohlbacher O, et al.)
  - Schubert B, et al. OptiType: precision HLA typing from next-generation sequencing data. *Bioinformatics.* 2014;30(23):3310-3316. doi: 10.1093/bioinformatics/btu548. Docs: https://github.com/FRED-2/OptiType
- **mtDNA-Server 2** (Institute of Genetic Epidemiology, Medical University of Innsbruck)
  - Weissensteiner H, Forer L, Kronenberg F, Schönherr S. mtDNA-Server 2: advancing mitochondrial DNA analysis through highly parallelized data processing and interactive analytics. *Nucleic Acids Res*. 2024 May 6:gkae296. doi: 10.1093/nar/gkae296. Epub ahead of print. PMID: 38709886.
- **GATK** (Genome Analysis Toolkit, Broad Institute)
  - McKenna A, et al. Genome Research. 2010;20(9):1297–1303; DePristo MA, et al. *Nature Genetics.* 2011;43(5):491–498.  Docs: https://gatk.broadinstitute.org/
- **SAMtools and BCFtools**
  - Danecek P, et al. Twelve years of SAMtools and BCFtools. *GigaScience.* 2021;10(2):giab008.
- **Nextflow**
  - Di Tommaso P, et al. Nextflow enables reproducible computational workflows. *Nature Biotechnology.* 2017;35(4):316–319.

This project was originally inspired by software such as **NeuroPGx**, available here: https://github.com/Andreater/NeuroPGx
- Zampatti, S.; Fabrizio, C.; Ragazzo, M.; Campoli, G.; Caputo, V.; Strafella, C.; Pellicano, C.; Cascella, R.; Spalletta, G.; Petrosini, L.; et al. Precision Medicine into Clinical Practice: A Web-Based Tool Enables Real-Time Pharmacogenetic Assessment of Tailored Treatments in Psychiatric Disorders. *J. Pers. Med.* 2021, 11, 851. https://doi.org/10.3390/jpm11090851

## License

[AGPLv3](LICENSE)

Copyright (C) 2024-2026 Iliya Yaroshevskiy

This project is provided under the AGPLv3 License.
