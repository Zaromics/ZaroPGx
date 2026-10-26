# ZaroPGx — A Pharmacogenomic Analysis Platform

[![CI](https://github.com/Zaromics/ZaroPGx/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Zaromics/ZaroPGx/actions/workflows/ci.yml?query=branch%3Amain)
[![Latest release](https://img.shields.io/github/v/release/Zaromics/ZaroPGx)](https://github.com/Zaromics/ZaroPGx/releases/latest)
[![License: AGPL-3.0](https://img.shields.io/github/license/Zaromics/ZaroPGx)](LICENSE)

**See the analysis in action: click below to watch a demo on YouTube:**
[<img src="https://img.youtube.com/vi/FzKI48IQb3I/hqdefault.jpg" width="600" height="400"
/>](https://www.youtube.com/embed/FzKI48IQb3I)

---
## What ZaroPGx Does

ZaroPGx is a self-hosted pharmacogenomic analytic pipeline. As input, ZaroPGx accepts a VCF, BCF, gVCF, BAM, CRAM,
SAM or short-read FASTQ, and reports diplotypes and phenotypes for up to 91 pharmacogenes, with the
CPIC, DPWG and FDA guidances that apply, with reports in PDF, interactive HTML, and FHIR R4 bundle forms.

<details>
<summary>Supported genes (91)</summary>

- **PharmCAT (25):** ABCG2, CACNA1S, CFTR, CYP2B6, CYP2C9, CYP2C19, CYP2D6 (from PyPGx), CYP3A4,
  CYP3A5, CYP4F2, DPYD, F2, F5, G6PD, HLA-A and HLA-B (from OptiType), IFNL3, MT-RNR1 (from
  mtDNA-Server 2), NAT2, NUDT15, RYR1, SLCO1B1, TPMT, UGT1A1, VKORC1
- **PyPGx (65):** ABCB1, ACYP2, ADRA2A, ADRB2, ANKK1, APOE, ATM, BCHE, BDNF, COMT, CYP1A1, CYP1A2,
  CYP1B1, CYP2A6, CYP2A13, CYP2C8, CYP2E1, CYP2F1, CYP2J2, CYP2R1, CYP2S1, CYP2W1, CYP3A7,
  CYP3A43, CYP4A11, CYP4A22, CYP4B1, CYP17A1, CYP19A1, CYP26A1, DBH, DRD2, GRIK1, GRIK4,
  GRIN2B, GSTM1, GSTP1, GSTT1, HTR1A, HTR2A, IFNL4, ITGB3, ITPA, MTHFR, NAT1, OPRK1, OPRM1, POR,
  PTGIS, RARG, SLC6A4, SLC15A2, SLC22A2, SLC28A3, SLC47A2, SLCO1B3, SLCO2B1, SULT1A1, TBXAS1,
  UGT1A4, UGT1A6, UGT2B7, UGT2B15, UGT2B17, XPC
- **OptiType (1):** HLA-C

</details>

**Workflow**: *Genomic data sample submission → Preprocessing (if needed) → OptiType HLA Allele Calling → mtDNA-Server 2 Mitochondrial DNA Allele Calling → PyPGx Allele Calling → PharmCAT phenotype matching with Outside Calls → Report Creation → optional FHIR export*

Designed as a self-hostable Docker Compose stack, ZaroPGx enables absolute **data privacy and security** when loaded in a local and secure network. It is research software, not a clinical test.

### Quickstart with One-Command Setup

**PowerShell (Windows):**
```powershell
iwr -useb https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.ps1 | iex
```

**Bash (Linux / macOS / WSL):**
```bash
curl -fsSL https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.sh | bash
```

This single command will:
- Check for required dependencies (Git, Docker, Docker Compose)
- Offer to automatically install missing dependencies (with your permission)
- Download the ZaroPGx bootstrap script
- Clone the repository
- Create necessary directories
- Start the Docker compose containers

**Security Note:** If you're cautious about running remote scripts (which is good practice), you can inspect the bootstrap scripts here:
- PowerShell: https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.ps1
- Bash: https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.sh

**To update an existing installation** (read [UPGRADING.md](UPGRADING.md) first):

**PowerShell:**
```powershell
iex "& { $(iwr -useb https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.ps1) } -Update"
```

**Bash:**
```bash
curl -fsSL https://raw.githubusercontent.com/Zaromics/ZaroPGx/main/bootstrap.sh | bash -s -- --update
```

<img width="600" height="1100" alt="zaropgx_demo" src="https://github.com/user-attachments/assets/50de2e8d-b496-424b-b2fb-0d34d7e39505" />

## Status
**This project is in early development.**

**Core functionality is being implemented incrementally:**

*Input formats*
- [X] Priority 0 (*Supported*): **VCF, GRCh38**/hg38, NGS-derived.
- [X] Priority 1 (*Supported*): **VCF, GRCh37**/hg19, NGS-derived. *Lifted over to GRCh38.*
- [...] Priority 2 (*Testing*): **BAM, CRAM, SAM, BCF, gVCF**, NGS-derived, GRCh38.
- [...] Priority 2 (*Testing*): **FASTQ**, short reads, up to 20 GB. *Aligned to GRCh38.*
- [...] Priority 3 (*Research*): Other sequencing and genotyping formats.
- [...] Priority 4 (*Research*): BED, 23andMe, AncestryDNA, various TXT formats. *Detected and refused for now.*
- [...] Priority 5 (*Early research*): T2T-CHM13, and others. *Detected and refused for now.*

*Features*
- [X] Priority 0 (*Supported*): **PDF and interactive HTML custom in-house reports.**
- [X] Priority 1 (*Supported*): **mtDNA-Server 2**: haplogroup and the MT-RNR1 outside call.
- [o] Priority 2 (*Development*): **FHIR R4 export** (JSON, XML) following HL7's Genomics Reporting IG; push to the bundled HAPI FHIR server.
- [...] Priority 3 (*Development*): Interactive HTML enhancements with useful visualizations, fully DB-oriented data handling. *Projected release in v0.5*
- [...] Priority 4 (*Research*): FHIR online export direct to PHR/EHR
- [...] Priority 5 (*Early research*): In-depth and targeted analytics, with specialty curation to reduce cognitive load
- [...] Priority 6 (*Early research*): Complete transition to fully DB-based workflow; pulling and normalizing data directly from published databases

## Requirements
<u>Software</u>

**NB: The simple bootstrap script will automatically install missing dependencies for you!**

**Linux** environment preferred
- *Docker*; *Docker Compose* (>= 2.24); *Git* -- at minimum
- Auto-install supported via: apt, yum, dnf, pacman

**Windows 10/11** requires *WSL2* installed and configured
- *WSL2*; *Docker*; *Docker Compose*; *Git*
- Auto-install supported via: winget or chocolatey

**macOS** requires either *Docker Desktop* or to run a Linux VM
- Auto-install supported via: homebrew (Git only; Docker Desktop must be installed manually)
- (macOS support needs testing)

<u>Hardware</u>

- *Virtualization*: Hardware Virtualization must be enabled for Windows and macOS users

(resource usage, projected)

- *Internet connection*: <u>first run only</u> requires significant bandwidth to fetch images and load reference genomes; advisable to NOT be on a metered connection, and preferably use a wired one.
- *Hardware, Minimum* (limited function): **4 CPU cores, 8 GB DDR3 RAM, 50 GB storage**
- *Hardware, Acceptable* (full function): **8 CPU cores, 32-64 GB DDR4 RAM, 1 TB NVMe SSD storage**
- *Hardware, Preferred* (fast, full function): **16 CPU cores, 128 GB ECC DDR4+ RAM, 2 TB NVMe SSD storage**

## Manual Installation (Advanced)

If you prefer more control or want to customize the installation:

1. **Clone the repository**
   ```bash
   git clone https://github.com/Zaromics/ZaroPGx.git
   cd ZaroPGx
   ```

2. **Choose your environment**

   For personal and home (LAN) use, a local deployment is recommended

   **Local Development (default):** Your typical template for personal / home use
   ```bash
   cp .env.local .env
   ```

   **Web Deployment:** For hosting an externally accessible service on the web
   ```bash
   cp .env.production .env
   ```

   **Custom Configuration:** More complete and in-line documented, for convenience
   ```bash
   cp .env.example .env
   ```

3. **Start services**

   - **PowerShell (Windows):** `.\start-docker.ps1`
   - **Bash (Linux / Mac / Windows WSL):** `./start-docker.sh`

4. **Access the Main App**
   - Web UI: `http://localhost:8765`
   - Documentation: `http://localhost:8765/docs`
   - HAPI FHIR dashboard (optional): `http://localhost:8090`

## Usage

### Web Interface (Recommended)

1. Open `http://localhost:8765`
2. Upload a genomic data file
3. Observe progress, and on completion you will see links to the custom PDF and interactive HTML reports, as well as PharmCAT's report and raw data outputs.

Reports are written to `data/reports/<patient_id>/<job_id>/`.

### REST API (Advanced and Debugging)

**See the FastAPI docs on your instance's page: `http://localhost:8765/api/docs`**

## Sample Data Access
For real-world sample data, try browsing the **Personal Genome Project**:
- https://www.personalgenomes.org/

Sample files available in the repo:
- `app/static/demo/NA12878.pgx-demo.cram` (used by **Run Demo**)
- `test_data/sample_cpic.vcf`

## Contributions are welcome and are gratefully appreciated!

## Acknowledgements & Citations
If your work is a part of ZaroPGx and you wish to add or amend text recognizing yourself, your work, and/or your organization, please send me a message and I will gladly attend to your request.

<details>
<summary>Software and data credits</summary>

<!-- credits:start (scripts/render_credits.py writes this part) -->

**Software**

- [Nextflow](https://www.nextflow.io)
  - Di Tommaso P, Chatzou M, Floden EW, Barja PP, Palumbo E, Notredame C. Nextflow enables reproducible computational workflows. *Nature Biotechnology*. 2017;35(4):316–319. doi:[10.1038/nbt.3820](https://doi.org/10.1038/nbt.3820)
- [SAMtools, BCFtools and HTSlib](https://www.htslib.org)
  - Danecek P, Bonfield JK, Liddle J, et al. Twelve years of SAMtools and BCFtools. *GigaScience*. 2021;10(2):giab008. doi:[10.1093/gigascience/giab008](https://doi.org/10.1093/gigascience/giab008)
  - Bonfield JK, Marshall J, Danecek P, et al. HTSlib: C library for reading/writing high-throughput sequencing data. *GigaScience*. 2021;10(2):giab007. doi:[10.1093/gigascience/giab007](https://doi.org/10.1093/gigascience/giab007)
  - Danecek P, McCarthy SA. BCFtools/csq: haplotype-aware variant consequences. *Bioinformatics*. 2017;33(13):2037–2039. doi:[10.1093/bioinformatics/btx100](https://doi.org/10.1093/bioinformatics/btx100)
- [pysam](https://github.com/pysam-developers/pysam)
- [GATK](https://gatk.broadinstitute.org)
  - Van der Auwera GA, O'Connor BD. Genomics in the Cloud: Using Docker, GATK, and WDL in Terra. 1st ed. O'Reilly Media; 2020.
  - McKenna A, Hanna M, Banks E, et al. The Genome Analysis Toolkit: A MapReduce framework for analyzing next-generation DNA sequencing data. *Genome Research*. 2010;20(9):1297–1303. doi:[10.1101/gr.107524.110](https://doi.org/10.1101/gr.107524.110)
  - Poplin R, Ruano-Rubio V, DePristo MA, et al. Scaling accurate genetic variant discovery to tens of thousands of samples [Preprint]. *bioRxiv*. 2017:201178. doi:[10.1101/201178](https://doi.org/10.1101/201178)
- [Picard](https://broadinstitute.github.io/picard/)
  - Broad Institute. Picard Toolkit [Software]. GitHub repository; 2019. <https://broadinstitute.github.io/picard/>
- [BWA-MEM](https://github.com/lh3/bwa)
  - Li H. Aligning sequence reads, clone sequences and assembly contigs with BWA-MEM [Preprint]. *arXiv*. 2013:1303.3997v2. doi:[10.48550/arXiv.1303.3997](https://doi.org/10.48550/arXiv.1303.3997)
- [PyPGx](https://pypgx.readthedocs.io)
  - Lee SB, Shin JY, Kwon NJ, Kim C, Seo JS. ClinPharmSeq: A targeted sequencing panel for clinical pharmacogenetics implementation. *PLOS ONE*. 2022;17(7):e0272129. doi:[10.1371/journal.pone.0272129](https://doi.org/10.1371/journal.pone.0272129)
  - Lee SB, Wheeler MM, Patterson K, et al. Stargazer: a software tool for calling star alleles from next-generation sequencing data using CYP2D6 as a model. *Genetics in Medicine*. 2019;21(2):361–372. doi:[10.1038/s41436-018-0054-0](https://doi.org/10.1038/s41436-018-0054-0)
  - Lee SB, Wheeler MM, Thummel KE, Nickerson DA. Calling Star Alleles With Stargazer in 28 Pharmacogenes With Whole Genome Sequences. *Clinical Pharmacology & Therapeutics*. 2019;106(6):1328–1337. doi:[10.1002/cpt.1552](https://doi.org/10.1002/cpt.1552)
- [fuc](https://github.com/sbslee/fuc)
  - Lee SB, Shin JY, Kwon NJ, Kim C, Seo JS. ClinPharmSeq: A targeted sequencing panel for clinical pharmacogenetics implementation. *PLOS ONE*. 2022;17(7):e0272129. doi:[10.1371/journal.pone.0272129](https://doi.org/10.1371/journal.pone.0272129)
- [Beagle](https://faculty.washington.edu/browning/beagle/beagle.html)
  - Browning BL, Tian X, Zhou Y, Browning SR. Fast two-stage phasing of large-scale sequence data. *American Journal of Human Genetics*. 2021;108(10):1880–1890. doi:[10.1016/j.ajhg.2021.08.005](https://doi.org/10.1016/j.ajhg.2021.08.005)
- [scikit-learn](https://scikit-learn.org)
  - Pedregosa F, Varoquaux G, Gramfort A, et al. Scikit-learn: Machine Learning in Python. *Journal of Machine Learning Research*. 2011;12(85):2825–2830. <https://jmlr.org/papers/v12/pedregosa11a.html>
- [OptiType](https://github.com/FRED-2/OptiType)
  - Szolek A, Schubert B, Mohr C, Sturm M, Feldhahn M, Kohlbacher O. OptiType: precision HLA typing from next-generation sequencing data. *Bioinformatics*. 2014;30(23):3310–3316. doi:[10.1093/bioinformatics/btu548](https://doi.org/10.1093/bioinformatics/btu548)
- [YARA](https://github.com/seqan/seqan/tree/main/apps/yara)
  - Siragusa E, Weese D, Reinert K. Fast and accurate read mapping with approximate seeds and multiple backtracking. *Nucleic Acids Research*. 2013;41(7):e78. doi:[10.1093/nar/gkt005](https://doi.org/10.1093/nar/gkt005)
  - Siragusa E. Approximate string matching for high-throughput sequencing [dissertation]. Freie Universität Berlin; 2015. doi:[10.17169/refubium-15562](https://doi.org/10.17169/refubium-15562)
- [SeqAn](https://www.seqan.de)
  - Reinert K, Dadi TH, Ehrhardt M, et al. The SeqAn C++ template library for efficient sequence analysis: A resource for programmers. *Journal of Biotechnology*. 2017;261:157–168. doi:[10.1016/j.jbiotec.2017.07.017](https://doi.org/10.1016/j.jbiotec.2017.07.017)
- [GLPK](https://www.gnu.org/software/glpk/)
- [Pyomo](https://www.pyomo.org)
  - Bynum ML, Hackebeil GA, Hart WE, et al. Pyomo — Optimization Modeling in Python. 3rd ed. Springer; 2021. doi:[10.1007/978-3-030-68928-5](https://doi.org/10.1007/978-3-030-68928-5)
  - Hart WE, Watson JP, Woodruff DL. Pyomo: modeling and solving mathematical programs in Python. *Mathematical Programming Computation*. 2011;3(3):219–260. doi:[10.1007/s12532-011-0026-8](https://doi.org/10.1007/s12532-011-0026-8)
- [mtDNA-Server 2](https://github.com/genepi/mtdna-server-2)
  - Weissensteiner H, Forer L, Kronenberg F, Schönherr S. mtDNA-Server 2: advancing mitochondrial DNA analysis through highly parallelized data processing and interactive analytics. *Nucleic Acids Research*. 2024;52(W1):W102–W107. doi:[10.1093/nar/gkae296](https://doi.org/10.1093/nar/gkae296)
- [mutserve](https://github.com/seppinho/mutserve)
  - Weissensteiner H, Forer L, Fuchsberger C, et al. mtDNA-Server: next-generation sequencing data analysis of human mitochondrial DNA in the cloud. *Nucleic Acids Research*. 2016;44(W1):W64–W69. doi:[10.1093/nar/gkw247](https://doi.org/10.1093/nar/gkw247)
  - Weissensteiner H, Forer L, Fendt L, et al. Contamination detection in sequencing studies using the mitochondrial phylogeny. *Genome Research*. 2021;31(2):309–316. doi:[10.1101/gr.256545.119](https://doi.org/10.1101/gr.256545.119)
- [Haplogrep 3](https://github.com/genepi/haplogrep3)
  - Schönherr S, Weissensteiner H, Kronenberg F, Forer L. Haplogrep 3 - an interactive haplogroup classification and analysis platform. *Nucleic Acids Research*. 2023;51(W1):W263–W268. doi:[10.1093/nar/gkad284](https://doi.org/10.1093/nar/gkad284)
- [haplocheck](https://github.com/genepi/haplocheck)
  - Weissensteiner H, Forer L, Fendt L, et al. Contamination detection in sequencing studies using the mitochondrial phylogeny. *Genome Research*. 2021;31(2):309–316. doi:[10.1101/gr.256545.119](https://doi.org/10.1101/gr.256545.119)
- [PharmCAT](https://pharmcat.org)
  - Sangkuhl K, Whirl-Carrillo M, Whaley RM, et al. Pharmacogenomics Clinical Annotation Tool (PharmCAT). *Clinical Pharmacology & Therapeutics*. 2020;107(1):203–210. doi:[10.1002/cpt.1568](https://doi.org/10.1002/cpt.1568)
  - Li B, Sangkuhl K, Keat K, et al. How to Run the Pharmacogenomics Clinical Annotation Tool (PharmCAT). *Clinical Pharmacology & Therapeutics*. 2023;113(5):1036–1047. doi:[10.1002/cpt.2790](https://doi.org/10.1002/cpt.2790)
  - Klein TE, Ritchie MD. PharmCAT: A Pharmacogenomics Clinical Annotation Tool. *Clinical Pharmacology & Therapeutics*. 2018;104(1):19–22. doi:[10.1002/cpt.928](https://doi.org/10.1002/cpt.928)
- [WeasyPrint](https://weasyprint.org)
- [Kroki](https://kroki.io)
- [Mermaid](https://mermaid.js.org)
  - Sveidqvist K, Contributors to Mermaid. Mermaid: Generate diagrams from markdown-like text [Software]. GitHub repository. <https://github.com/mermaid-js/mermaid>
- [Chart.js](https://www.chartjs.org)
- [D3](https://d3js.org)
  - Bostock M, Ogievetsky V, Heer J. D³ Data-Driven Documents. *IEEE Transactions on Visualization and Computer Graphics*. 2011;17(12):2301–2309. doi:[10.1109/TVCG.2011.185](https://doi.org/10.1109/TVCG.2011.185)

**Data**

- [GRCh38 (Genome Reference Consortium)](https://www.ncbi.nlm.nih.gov/grc/human)
  - Schneider VA, Graves-Lindsay T, Howe K, et al. Evaluation of GRCh38 and de novo haploid genome assemblies demonstrates the enduring quality of the reference assembly. *Genome Research*. 2017;27(5):849–864. doi:[10.1101/gr.213611.116](https://doi.org/10.1101/gr.213611.116)
- [PharmCAT's GRCh38 reference FASTA](https://zenodo.org/records/7288118)
  - Woon M. GRCh38.p13 Reference FASTA (bgzip'd with faidx) [Data set]. Zenodo; 2022. doi:[10.5281/zenodo.7288118](https://doi.org/10.5281/zenodo.7288118)
- [UCSC hg38](https://hgdownload.soe.ucsc.edu/goldenPath/hg38/bigZips/)
  - Casper J, Speir ML, Raney BJ, et al. The UCSC Genome Browser database: 2026 update. *Nucleic Acids Research*. 2026;54(D1):D1331–D1335. doi:[10.1093/nar/gkaf1250](https://doi.org/10.1093/nar/gkaf1250)
- [1000 Genomes GRCh38 analysis set](https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/technical/reference/GRCh38_reference_genome/)
  - Zheng-Bradley X, Streeter I, Fairley S, Richardson D, Clarke L, Flicek P; 1000 Genomes Project Consortium. Alignment of 1000 Genomes Project reads to reference assembly GRCh38. *GigaScience*. 2017;6(7):1–8. doi:[10.1093/gigascience/gix038](https://doi.org/10.1093/gigascience/gix038)
- [GRCh37](https://www.ncbi.nlm.nih.gov/grc/human)
  - Church DM, Schneider VA, Graves T, et al. Modernizing Reference Genome Assemblies. *PLOS Biology*. 2011;9(7):e1001091. doi:[10.1371/journal.pbio.1001091](https://doi.org/10.1371/journal.pbio.1001091)
- [UCSC hg19ToHg38 chain](https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/)
  - Hinrichs AS, Karolchik D, Baertsch R, et al. The UCSC Genome Browser Database: update 2006. *Nucleic Acids Research*. 2006;34(Database issue):D590–D598. doi:[10.1093/nar/gkj144](https://doi.org/10.1093/nar/gkj144)
- [Ensembl and MANE](https://www.ensembl.org)
  - Yates AD, Austine-Orimoloye O, Azov AG, et al. Ensembl 2026. *Nucleic Acids Research*. 2026;54(D1):D1053–D1060. doi:[10.1093/nar/gkaf1239](https://doi.org/10.1093/nar/gkaf1239)
  - Morales J, Pujar S, Loveland JE, et al. A joint NCBI and EMBL-EBI transcript set for clinical genomics and research. *Nature*. 2022;604(7905):310–315. doi:[10.1038/s41586-022-04558-8](https://doi.org/10.1038/s41586-022-04558-8)
- [AlphaMissense](https://github.com/google-deepmind/alphamissense)
  - Cheng J, Novati G, Pan J, et al. Accurate proteome-wide missense variant effect prediction with AlphaMissense. *Science*. 2023;381(6664):eadg7492. doi:[10.1126/science.adg7492](https://doi.org/10.1126/science.adg7492)
- [gnomAD](https://gnomad.broadinstitute.org)
  - Chen S, Francioli LC, Goodrich JK, et al. A genomic mutational constraint map using variation in 76,156 human genomes. *Nature*. 2024;625(7993):92–100. doi:[10.1038/s41586-023-06045-0](https://doi.org/10.1038/s41586-023-06045-0)
- [ClinVar](https://www.ncbi.nlm.nih.gov/clinvar/)
  - Landrum MJ, Lee JM, Benson M, et al. ClinVar: improving access to variant interpretations and supporting evidence. *Nucleic Acids Research*. 2018;46(D1):D1062–D1067. doi:[10.1093/nar/gkx1153](https://doi.org/10.1093/nar/gkx1153)
- [GIAB genomic stratifications](https://www.nist.gov/programs-projects/genome-bottle)
  - Dwarshuis N, Kalra D, McDaniel J, et al. The GIAB genomic stratifications resource for human reference genomes. *Nature Communications*. 2024;15(1):9029. doi:[10.1038/s41467-024-53260-y](https://doi.org/10.1038/s41467-024-53260-y)
- [1000 Genomes Project 30x panel](https://www.internationalgenome.org)
  - Byrska-Bishop M, Evani US, Zhao X, et al. High-coverage whole-genome sequencing of the expanded 1000 Genomes Project cohort including 602 trios. *Cell*. 2022;185(18):3426–3440.e19. doi:[10.1016/j.cell.2022.08.004](https://doi.org/10.1016/j.cell.2022.08.004)
  - 1000 Genomes Project Consortium; Auton A, Brooks LD, Durbin RM, et al. A global reference for human genetic variation. *Nature*. 2015;526(7571):68–74. doi:[10.1038/nature15393](https://doi.org/10.1038/nature15393)
- [IPD-IMGT/HLA](https://www.ebi.ac.uk/ipd/imgt/hla/)
  - Barker DJ, Natarajan RHL, Cooper MA, et al. The IPD-IMGT/HLA database: recent developments in sequence submission. *Nucleic Acids Research*. 2026;54(D1):D1152–D1158. doi:[10.1093/nar/gkaf1218](https://doi.org/10.1093/nar/gkaf1218)
  - Robinson J, Halliwell JA, McWilliam H, Lopez R, Parham P, Marsh SGE. The IMGT/HLA database. *Nucleic Acids Research*. 2013;41(D1):D1222–D1227. doi:[10.1093/nar/gks949](https://doi.org/10.1093/nar/gks949)
- [PhyloTree](https://github.com/genepi/phylotree-fu-rcrs)
  - van Oven M, Kayser M. Updated comprehensive phylogenetic tree of global human mitochondrial DNA variation. *Human Mutation*. 2009;30(2):E386–E394. doi:[10.1002/humu.20921](https://doi.org/10.1002/humu.20921)
  - Dür A, Huber N, Parson W. Fine-Tuning Phylogenetic Alignment and Haplogrouping of mtDNA Sequences. *International Journal of Molecular Sciences*. 2021;22(11):5747. doi:[10.3390/ijms22115747](https://doi.org/10.3390/ijms22115747)
- [Revised Cambridge Reference Sequence (rCRS)](https://www.ncbi.nlm.nih.gov/nuccore/NC_012920.1)
  - Andrews RM, Kubacka I, Chinnery PF, Lightowlers RN, Turnbull DM, Howell N. Reanalysis and revision of the Cambridge reference sequence for human mitochondrial DNA. *Nature Genetics*. 1999;23(2):147. doi:[10.1038/13779](https://doi.org/10.1038/13779)
- [CPIC](https://www.clinpgx.org/cpic)
  - Caudle KE, Whirl-Carrillo M, Relling MV, et al. Advancing Clinical Pharmacogenomics Worldwide Through the Clinical Pharmacogenetics Implementation Consortium (CPIC). *Clinical Pharmacology & Therapeutics*. 2025;118(6):1512–1522. doi:[10.1002/cpt.70005](https://doi.org/10.1002/cpt.70005)
- [DPWG](https://www.knmp.nl/dossiers/farmacogenetica/pharmacogenetics)
  - Swen JJ, Wilting I, de Goede AL, et al. Pharmacogenetics: From Bench to Byte. *Clinical Pharmacology & Therapeutics*. 2008;83(5):781–787. doi:[10.1038/sj.clpt.6100507](https://doi.org/10.1038/sj.clpt.6100507)
  - Swen JJ, Nijenhuis M, de Boer A, et al. Pharmacogenetics: From Bench to Byte—An Update of Guidelines. *Clinical Pharmacology & Therapeutics*. 2011;89(5):662–673. doi:[10.1038/clpt.2011.34](https://doi.org/10.1038/clpt.2011.34)
- [FDA](https://www.fda.gov/medical-devices/precision-medicine/table-pharmacogenetic-associations)
  - U.S. Food and Drug Administration. Table of Pharmacogenetic Associations. <https://www.fda.gov/medical-devices/precision-medicine/table-pharmacogenetic-associations>
  - U.S. Food and Drug Administration. Table of Pharmacogenomic Biomarkers in Drug Labeling. <https://www.fda.gov/drugs/science-and-research-drugs/table-pharmacogenomic-biomarkers-drug-labeling>
- [ClinPGx (PharmGKB)](https://www.clinpgx.org)
  - Whirl-Carrillo M, Huddart R, Gong L, et al. An Evidence-Based Framework for Evaluating Pharmacogenomics Knowledge for Personalized Medicine. *Clinical Pharmacology & Therapeutics*. 2021;110(3):563–572. doi:[10.1002/cpt.2350](https://doi.org/10.1002/cpt.2350)
- [PharmVar](https://www.pharmvar.org)
  - Gaedigk A, Casey ST, Whirl-Carrillo M, Miller NA, Klein TE. Pharmacogene Variation Consortium: A Global Resource and Repository for Pharmacogene Variation. *Clinical Pharmacology & Therapeutics*. 2021;110(3):542–545. doi:[10.1002/cpt.2321](https://doi.org/10.1002/cpt.2321)
  - Gaedigk A, Whirl-Carrillo M, Pratt VM, Miller NA, Klein TE. PharmVar and the Landscape of Pharmacogenetic Resources. *Clinical Pharmacology & Therapeutics*. 2020;107(1):43–46. doi:[10.1002/cpt.1654](https://doi.org/10.1002/cpt.1654)
  - Gaedigk A, Sangkuhl K, Whirl-Carrillo M, Twist GP, Klein TE, Miller NA; PharmVar Steering Committee. The Evolution of PharmVar. *Clinical Pharmacology & Therapeutics*. 2019;105(1):29–32. doi:[10.1002/cpt.1275](https://doi.org/10.1002/cpt.1275)
  - Gaedigk A, Ingelman-Sundberg M, Miller NA, Leeder JS, Whirl-Carrillo M, Klein TE; PharmVar Steering Committee. The Pharmacogene Variation (PharmVar) Consortium: Incorporation of the Human Cytochrome P450 (CYP) Allele Nomenclature Database. *Clinical Pharmacology & Therapeutics*. 2018;103(3):399–401. doi:[10.1002/cpt.910](https://doi.org/10.1002/cpt.910)
- [dbSNP](https://www.ncbi.nlm.nih.gov/snp/)
  - Phan L, Zhang H, Wang Q, et al. The evolution of dbSNP: 25 years of impact in genomic research. *Nucleic Acids Research*. 2025;53(D1):D925–D931. doi:[10.1093/nar/gkae977](https://doi.org/10.1093/nar/gkae977)

Licenses and terms of use: [Credits](docs/user/credits.md).

<!-- credits:end -->

</details>

This project was originally inspired by software such as **NeuroPGx**, available here: https://github.com/Andreater/NeuroPGx
- Zampatti S, Fabrizio C, Ragazzo M, et al. Precision Medicine into Clinical Practice: A Web-Based Tool Enables Real-Time Pharmacogenetic Assessment of Tailored Treatments in Psychiatric Disorders. *Journal of Personalized Medicine*. 2021;11(9):851. doi:[10.3390/jpm11090851](https://doi.org/10.3390/jpm11090851)

## License

This project is provided under the [AGPLv3](LICENSE) License.

Copyright (C) 2024-2026 Iliya Yaroshevskiy
