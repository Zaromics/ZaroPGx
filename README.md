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

**Access.** The app listens on `127.0.0.1:8765`, this machine only, and asks for no login
(`ZAROPGX_AUTH_MODE=open`). To use it from other devices on your network, set
`BIND_ADDRESS=0.0.0.0:8765`; anyone who can reach it then gets in unless you turn on the
shared password. To serve it beyond your network, see
[Running a server](docs/user/deployment.md).

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

**This project is in early development.**

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
- [...] Priority 3 (*Development*): Interactive HTML enhancements with useful visualizations, fully DB-oriented data handling. *Projected release in v0.5*
- [...] Priority 4 (*Research*): FHIR online export direct to PHR/EHR
- [...] Priority 5 (*Early research*): In-depth and targeted analytics, with specialty curation to reduce cognitive load
- [...] Priority 6 (*Early research*): Complete transition to fully DB-based workflow; pulling and normalizing data directly from published databases

## Sample Data

Example inputs in the repository: `app/static/demo/pharmcat.example.vcf` (the **Run Demo**
button), and in `test_data/`, PharmCAT's example VCFs, GRCh37 and T2T SNP sets, and a small
BAM, CRAM and SAM. For real-world data, try the **Personal Genome Project**, whose
participants share their genomes publicly: https://www.personalgenomes.org/

## Contributions are welcome and are gratefully appreciated!

See [contributing](docs/developer/contributing.md).

## Acknowledgements & Citations
ZaroPGx is built on the software and data below, and we thank their authors and maintainers. Every report lists, under Platform and Citations, the parts its own run used, with versions and these references.
If your work is a part of ZaroPGx and you wish to add or amend text recognizing yourself, your work, and/or your organization, please send me a message and I will gladly attend to your request.

<!-- credits:start (scripts/render_credits.py writes this part) -->

**Software**

- [ZaroPGx](https://github.com/Zaromics/ZaroPGx): Runs the pipeline and writes the reports.
- [Nextflow](https://www.nextflow.io): Runs the pipeline's steps in order.
  - Di Tommaso P, Chatzou M, Floden EW, Barja PP, Palumbo E, Notredame C. Nextflow enables reproducible computational workflows. *Nature Biotechnology*. 2017;35(4):316–319. doi:[10.1038/nbt.3820](https://doi.org/10.1038/nbt.3820)
- [SAMtools, BCFtools and HTSlib](https://www.htslib.org): PharmCAT's VCF preprocessing; file conversion and variant calling where the input needs them.
  - Danecek P, Bonfield JK, Liddle J, et al. Twelve years of SAMtools and BCFtools. *GigaScience*. 2021;10(2):giab008. doi:[10.1093/gigascience/giab008](https://doi.org/10.1093/gigascience/giab008)
  - Bonfield JK, Marshall J, Danecek P, et al. HTSlib: C library for reading/writing high-throughput sequencing data. *GigaScience*. 2021;10(2):giab007. doi:[10.1093/gigascience/giab007](https://doi.org/10.1093/gigascience/giab007)
- [pysam](https://github.com/pysam-developers/pysam): Reads the input files for ZaroPGx and PyPGx.
- [GATK](https://gatk.broadinstitute.org): gVCF genotyping, liftover, read alignment and re-alignment of the CYP2D6 and UGT2B loci.
  - Van der Auwera GA, O'Connor BD. Genomics in the Cloud: Using Docker, GATK, and WDL in Terra. 1st ed. O'Reilly Media; 2020.
  - McKenna A, Hanna M, Banks E, et al. The Genome Analysis Toolkit: A MapReduce framework for analyzing next-generation DNA sequencing data. *Genome Research*. 2010;20(9):1297–1303. doi:[10.1101/gr.107524.110](https://doi.org/10.1101/gr.107524.110)
  - Poplin R, Ruano-Rubio V, DePristo MA, et al. Scaling accurate genetic variant discovery to tens of thousands of samples [Preprint]. *bioRxiv*. 2017:201178. doi:[10.1101/201178](https://doi.org/10.1101/201178)
- [Picard](https://broadinstitute.github.io/picard/): LiftoverVcf (GRCh37 to GRCh38) and FastqToSam.
  - Broad Institute. Picard Toolkit [Software]. GitHub repository; 2019. <https://broadinstitute.github.io/picard/>
- [BWA-MEM](https://github.com/lh3/bwa): Aligns to GRCh38, with GATK's BwaSpark, a FASTQ's reads and the CYP2D6 and UGT2B loci's reads.
  - Li H. Aligning sequence reads, clone sequences and assembly contigs with BWA-MEM [Preprint]. *arXiv*. 2013:1303.3997v2. doi:[10.48550/arXiv.1303.3997](https://doi.org/10.48550/arXiv.1303.3997)
- [PyPGx](https://pypgx.readthedocs.io): Star alleles beyond PharmCAT's genes, CYP2D6 and structural variants.
  - Lee SB, Shin JY, Kwon NJ, Kim C, Seo JS. ClinPharmSeq: A targeted sequencing panel for clinical pharmacogenetics implementation. *PLOS ONE*. 2022;17(7):e0272129. doi:[10.1371/journal.pone.0272129](https://doi.org/10.1371/journal.pone.0272129)
  - Lee SB, Wheeler MM, Patterson K, et al. Stargazer: a software tool for calling star alleles from next-generation sequencing data using CYP2D6 as a model. *Genetics in Medicine*. 2019;21(2):361–372. doi:[10.1038/s41436-018-0054-0](https://doi.org/10.1038/s41436-018-0054-0)
  - Lee SB, Wheeler MM, Thummel KE, Nickerson DA. Calling Star Alleles With Stargazer in 28 Pharmacogenes With Whole Genome Sequences. *Clinical Pharmacology & Therapeutics*. 2019;106(6):1328–1337. doi:[10.1002/cpt.1552](https://doi.org/10.1002/cpt.1552)
- [fuc](https://github.com/sbslee/fuc): Calls variants from the reads for PyPGx.
  - Lee SB, Shin JY, Kwon NJ, Kim C, Seo JS. ClinPharmSeq: A targeted sequencing panel for clinical pharmacogenetics implementation. *PLOS ONE*. 2022;17(7):e0272129. doi:[10.1371/journal.pone.0272129](https://doi.org/10.1371/journal.pone.0272129)
- [Beagle](https://faculty.washington.edu/browning/beagle/beagle.html): Phases variants for PyPGx.
  - Browning BL, Tian X, Zhou Y, Browning SR. Fast two-stage phasing of large-scale sequence data. *American Journal of Human Genetics*. 2021;108(10):1880–1890. doi:[10.1016/j.ajhg.2021.08.005](https://doi.org/10.1016/j.ajhg.2021.08.005)
- [scikit-learn](https://scikit-learn.org): Runs PyPGx's copy-number models.
  - Pedregosa F, Varoquaux G, Gramfort A, et al. Scikit-learn: Machine Learning in Python. *Journal of Machine Learning Research*. 2011;12(85):2825–2830. <https://jmlr.org/papers/v12/pedregosa11a.html>
- [OptiType](https://github.com/FRED-2/OptiType): HLA-A, HLA-B and HLA-C typing.
  - Szolek A, Schubert B, Mohr C, Sturm M, Feldhahn M, Kohlbacher O. OptiType: precision HLA typing from next-generation sequencing data. *Bioinformatics*. 2014;30(23):3310–3316. doi:[10.1093/bioinformatics/btu548](https://doi.org/10.1093/bioinformatics/btu548)
- [YARA](https://github.com/seqan/seqan/tree/main/apps/yara): Maps the HLA reads for OptiType.
  - Siragusa E, Weese D, Reinert K. Fast and accurate read mapping with approximate seeds and multiple backtracking. *Nucleic Acids Research*. 2013;41(7):e78. doi:[10.1093/nar/gkt005](https://doi.org/10.1093/nar/gkt005)
  - Siragusa E. Approximate string matching for high-throughput sequencing [dissertation]. Freie Universität Berlin; 2015. doi:[10.17169/refubium-15562](https://doi.org/10.17169/refubium-15562)
- [SeqAn](https://www.seqan.de): The library YARA is built on.
  - Reinert K, Dadi TH, Ehrhardt M, et al. The SeqAn C++ template library for efficient sequence analysis: A resource for programmers. *Journal of Biotechnology*. 2017;261:157–168. doi:[10.1016/j.jbiotec.2017.07.017](https://doi.org/10.1016/j.jbiotec.2017.07.017)
- [GLPK](https://www.gnu.org/software/glpk/): Solves OptiType's integer linear program.
- [Pyomo](https://www.pyomo.org): Builds OptiType's integer linear program.
  - Bynum ML, Hackebeil GA, Hart WE, et al. Pyomo — Optimization Modeling in Python. 3rd ed. Springer; 2021. doi:[10.1007/978-3-030-68928-5](https://doi.org/10.1007/978-3-030-68928-5)
  - Hart WE, Watson JP, Woodruff DL. Pyomo: modeling and solving mathematical programs in Python. *Mathematical Programming Computation*. 2011;3(3):219–260. doi:[10.1007/s12532-011-0026-8](https://doi.org/10.1007/s12532-011-0026-8)
- [mtDNA-Server 2](https://github.com/genepi/mtdna-server-2): Mitochondrial variants, haplogroup and the MT-RNR1 call.
  - Weissensteiner H, Forer L, Kronenberg F, Schönherr S. mtDNA-Server 2: advancing mitochondrial DNA analysis through highly parallelized data processing and interactive analytics. *Nucleic Acids Research*. 2024;52(W1):W102–W107. doi:[10.1093/nar/gkae296](https://doi.org/10.1093/nar/gkae296)
- [mutserve](https://github.com/seppinho/mutserve): Calls mitochondrial variants from the reads.
  - Weissensteiner H, Forer L, Fuchsberger C, et al. mtDNA-Server: next-generation sequencing data analysis of human mitochondrial DNA in the cloud. *Nucleic Acids Research*. 2016;44(W1):W64–W69. doi:[10.1093/nar/gkw247](https://doi.org/10.1093/nar/gkw247)
  - Weissensteiner H, Forer L, Fendt L, et al. Contamination detection in sequencing studies using the mitochondrial phylogeny. *Genome Research*. 2021;31(2):309–316. doi:[10.1101/gr.256545.119](https://doi.org/10.1101/gr.256545.119)
- [Haplogrep 3](https://github.com/genepi/haplogrep3): Assigns the mitochondrial haplogroup.
  - Schönherr S, Weissensteiner H, Kronenberg F, Forer L. Haplogrep 3 - an interactive haplogroup classification and analysis platform. *Nucleic Acids Research*. 2023;51(W1):W263–W268. doi:[10.1093/nar/gkad284](https://doi.org/10.1093/nar/gkad284)
- [haplocheck](https://github.com/genepi/haplocheck): Checks the mitochondrial reads for contamination.
  - Weissensteiner H, Forer L, Fendt L, et al. Contamination detection in sequencing studies using the mitochondrial phylogeny. *Genome Research*. 2021;31(2):309–316. doi:[10.1101/gr.256545.119](https://doi.org/10.1101/gr.256545.119)
- [PharmCAT](https://pharmcat.org): Diplotypes, phenotypes and drug guidance.
  - Sangkuhl K, Whirl-Carrillo M, Whaley RM, et al. Pharmacogenomics Clinical Annotation Tool (PharmCAT). *Clinical Pharmacology & Therapeutics*. 2020;107(1):203–210. doi:[10.1002/cpt.1568](https://doi.org/10.1002/cpt.1568)
  - Li B, Sangkuhl K, Keat K, et al. How to Run the Pharmacogenomics Clinical Annotation Tool (PharmCAT). *Clinical Pharmacology & Therapeutics*. 2023;113(5):1036–1047. doi:[10.1002/cpt.2790](https://doi.org/10.1002/cpt.2790)
  - Klein TE, Ritchie MD. PharmCAT: A Pharmacogenomics Clinical Annotation Tool. *Clinical Pharmacology & Therapeutics*. 2018;104(1):19–22. doi:[10.1002/cpt.928](https://doi.org/10.1002/cpt.928)
- [WeasyPrint](https://weasyprint.org): Draws the PDF.
- [Kroki](https://kroki.io): Draws the workflow diagram.
- [Mermaid](https://mermaid.js.org): The workflow diagram's layout.
  - Sveidqvist K, Contributors to Mermaid. Mermaid: Generate diagrams from markdown-like text [Software]. GitHub repository. <https://github.com/mermaid-js/mermaid>
- [Chart.js](https://www.chartjs.org): The interactive report's charts.
- [D3](https://d3js.org): The interactive report's gene-drug network.
  - Bostock M, Ogievetsky V, Heer J. D³ Data-Driven Documents. *IEEE Transactions on Visualization and Computer Graphics*. 2011;17(12):2301–2309. doi:[10.1109/TVCG.2011.185](https://doi.org/10.1109/TVCG.2011.185)

**Data and knowledge**

- [GRCh38 (Genome Reference Consortium)](https://www.ncbi.nlm.nih.gov/grc/human): The genome build every call is made on.
  - Schneider VA, Graves-Lindsay T, Howe K, et al. Evaluation of GRCh38 and de novo haploid genome assemblies demonstrates the enduring quality of the reference assembly. *Genome Research*. 2017;27(5):849–864. doi:[10.1101/gr.213611.116](https://doi.org/10.1101/gr.213611.116)
- [PharmCAT's GRCh38 reference FASTA](https://zenodo.org/records/7288118): Normalises the input in PharmCAT's preprocessor.
  - Woon M. GRCh38.p13 Reference FASTA (bgzip'd with faidx) [Data set]. Zenodo; 2022. doi:[10.5281/zenodo.7288118](https://doi.org/10.5281/zenodo.7288118)
- [UCSC hg38](https://hgdownload.soe.ucsc.edu/goldenPath/hg38/bigZips/): The reference for reads, gVCFs and liftover.
  - Casper J, Speir ML, Raney BJ, et al. The UCSC Genome Browser database: 2026 update. *Nucleic Acids Research*. 2026;54(D1):D1331–D1335. doi:[10.1093/nar/gkaf1250](https://doi.org/10.1093/nar/gkaf1250)
- [1000 Genomes GRCh38 analysis set](https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/technical/reference/GRCh38_reference_genome/): Decodes the CRAM, with UCSC hg38, by sequence checksum.
  - Zheng-Bradley X, Streeter I, Fairley S, Richardson D, Clarke L, Flicek P; 1000 Genomes Project Consortium. Alignment of 1000 Genomes Project reads to reference assembly GRCh38. *GigaScience*. 2017;6(7):1–8. doi:[10.1093/gigascience/gix038](https://doi.org/10.1093/gigascience/gix038)
- [GRCh37](https://www.ncbi.nlm.nih.gov/grc/human): The build the input was on, before liftover.
  - Church DM, Schneider VA, Graves T, et al. Modernizing Reference Genome Assemblies. *PLOS Biology*. 2011;9(7):e1001091. doi:[10.1371/journal.pbio.1001091](https://doi.org/10.1371/journal.pbio.1001091)
- [UCSC hg19ToHg38 chain](https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/): Lifts the input from GRCh37 to GRCh38.
  - Hinrichs AS, Karolchik D, Baertsch R, et al. The UCSC Genome Browser Database: update 2006. *Nucleic Acids Research*. 2006;34(Database issue):D590–D598. doi:[10.1093/nar/gkj144](https://doi.org/10.1093/nar/gkj144)
- [1000 Genomes Project 30x panel](https://www.internationalgenome.org): The haplotypes Beagle phases against.
  - Byrska-Bishop M, Evani US, Zhao X, et al. High-coverage whole-genome sequencing of the expanded 1000 Genomes Project cohort including 602 trios. *Cell*. 2022;185(18):3426–3440.e19. doi:[10.1016/j.cell.2022.08.004](https://doi.org/10.1016/j.cell.2022.08.004)
  - 1000 Genomes Project Consortium; Auton A, Brooks LD, Durbin RM, et al. A global reference for human genetic variation. *Nature*. 2015;526(7571):68–74. doi:[10.1038/nature15393](https://doi.org/10.1038/nature15393)
- [IPD-IMGT/HLA](https://www.ebi.ac.uk/ipd/imgt/hla/): The HLA allele sequences OptiType types against.
  - Barker DJ, Natarajan RHL, Cooper MA, et al. The IPD-IMGT/HLA database: recent developments in sequence submission. *Nucleic Acids Research*. 2026;54(D1):D1152–D1158. doi:[10.1093/nar/gkaf1218](https://doi.org/10.1093/nar/gkaf1218)
  - Robinson J, Halliwell JA, McWilliam H, Lopez R, Parham P, Marsh SGE. The IMGT/HLA database. *Nucleic Acids Research*. 2013;41(D1):D1222–D1227. doi:[10.1093/nar/gks949](https://doi.org/10.1093/nar/gks949)
- [PhyloTree](https://github.com/genepi/phylotree-fu-rcrs): The mitochondrial phylogeny haplogroups are assigned on.
  - van Oven M, Kayser M. Updated comprehensive phylogenetic tree of global human mitochondrial DNA variation. *Human Mutation*. 2009;30(2):E386–E394. doi:[10.1002/humu.20921](https://doi.org/10.1002/humu.20921)
  - Dür A, Huber N, Parson W. Fine-Tuning Phylogenetic Alignment and Haplogrouping of mtDNA Sequences. *International Journal of Molecular Sciences*. 2021;22(11):5747. doi:[10.3390/ijms22115747](https://doi.org/10.3390/ijms22115747)
- [Revised Cambridge Reference Sequence (rCRS)](https://www.ncbi.nlm.nih.gov/nuccore/NC_012920.1): The mitochondrial reference.
  - Andrews RM, Kubacka I, Chinnery PF, Lightowlers RN, Turnbull DM, Howell N. Reanalysis and revision of the Cambridge reference sequence for human mitochondrial DNA. *Nature Genetics*. 1999;23(2):147. doi:[10.1038/13779](https://doi.org/10.1038/13779)
- [CPIC](https://www.clinpgx.org/cpic): Guidelines: phenotypes and recommendations.
  - Caudle KE, Whirl-Carrillo M, Relling MV, et al. Advancing Clinical Pharmacogenomics Worldwide Through the Clinical Pharmacogenetics Implementation Consortium (CPIC). *Clinical Pharmacology & Therapeutics*. 2025;118(6):1512–1522. doi:[10.1002/cpt.70005](https://doi.org/10.1002/cpt.70005)
- [DPWG](https://www.knmp.nl/dossiers/farmacogenetica/pharmacogenetics): Guidelines of the Dutch Pharmacogenetics Working Group (KNMP).
  - Swen JJ, Wilting I, de Goede AL, et al. Pharmacogenetics: From Bench to Byte. *Clinical Pharmacology & Therapeutics*. 2008;83(5):781–787. doi:[10.1038/sj.clpt.6100507](https://doi.org/10.1038/sj.clpt.6100507)
  - Swen JJ, Nijenhuis M, de Boer A, et al. Pharmacogenetics: From Bench to Byte—An Update of Guidelines. *Clinical Pharmacology & Therapeutics*. 2011;89(5):662–673. doi:[10.1038/clpt.2011.34](https://doi.org/10.1038/clpt.2011.34)
- [FDA](https://www.fda.gov/medical-devices/precision-medicine/table-pharmacogenetic-associations): Drug-label annotations and pharmacogenetic associations.
  - U.S. Food and Drug Administration. Table of Pharmacogenetic Associations. <https://www.fda.gov/medical-devices/precision-medicine/table-pharmacogenetic-associations>
  - U.S. Food and Drug Administration. Table of Pharmacogenomic Biomarkers in Drug Labeling. <https://www.fda.gov/drugs/science-and-research-drugs/table-pharmacogenomic-biomarkers-drug-labeling>
- [ClinPGx (PharmGKB)](https://www.clinpgx.org): Allele definitions, guideline and label annotations, as PharmCAT carries them.
  - Whirl-Carrillo M, Huddart R, Gong L, et al. An Evidence-Based Framework for Evaluating Pharmacogenomics Knowledge for Personalized Medicine. *Clinical Pharmacology & Therapeutics*. 2021;110(3):563–572. doi:[10.1002/cpt.2350](https://doi.org/10.1002/cpt.2350)
- [PharmVar](https://www.pharmvar.org): Star-allele definitions and names.
  - Gaedigk A, Casey ST, Whirl-Carrillo M, Miller NA, Klein TE. Pharmacogene Variation Consortium: A Global Resource and Repository for Pharmacogene Variation. *Clinical Pharmacology & Therapeutics*. 2021;110(3):542–545. doi:[10.1002/cpt.2321](https://doi.org/10.1002/cpt.2321)
  - Gaedigk A, Whirl-Carrillo M, Pratt VM, Miller NA, Klein TE. PharmVar and the Landscape of Pharmacogenetic Resources. *Clinical Pharmacology & Therapeutics*. 2020;107(1):43–46. doi:[10.1002/cpt.1654](https://doi.org/10.1002/cpt.1654)
  - Gaedigk A, Sangkuhl K, Whirl-Carrillo M, Twist GP, Klein TE, Miller NA; PharmVar Steering Committee. The Evolution of PharmVar. *Clinical Pharmacology & Therapeutics*. 2019;105(1):29–32. doi:[10.1002/cpt.1275](https://doi.org/10.1002/cpt.1275)
  - Gaedigk A, Ingelman-Sundberg M, Miller NA, Leeder JS, Whirl-Carrillo M, Klein TE; PharmVar Steering Committee. The Pharmacogene Variation (PharmVar) Consortium: Incorporation of the Human Cytochrome P450 (CYP) Allele Nomenclature Database. *Clinical Pharmacology & Therapeutics*. 2018;103(3):399–401. doi:[10.1002/cpt.910](https://doi.org/10.1002/cpt.910)
- [dbSNP](https://www.ncbi.nlm.nih.gov/snp/): Variant identifiers (rs numbers).
  - Phan L, Zhang H, Wang Q, et al. The evolution of dbSNP: 25 years of impact in genomic research. *Nucleic Acids Research*. 2025;53(D1):D925–D931. doi:[10.1093/nar/gkae977](https://doi.org/10.1093/nar/gkae977)

Licences, terms of use and the platform's own components: [Credits](docs/user/credits.md).

<!-- credits:end -->

This project was originally inspired by software such as **NeuroPGx**, available here: https://github.com/Andreater/NeuroPGx
- Zampatti S, Fabrizio C, Ragazzo M, et al. Precision Medicine into Clinical Practice: A Web-Based Tool Enables Real-Time Pharmacogenetic Assessment of Tailored Treatments in Psychiatric Disorders. *Journal of Personalized Medicine*. 2021;11(9):851. doi:[10.3390/jpm11090851](https://doi.org/10.3390/jpm11090851)

## License

[AGPLv3](LICENSE)

Copyright (C) 2024-2026 Iliya Yaroshevskiy

This project is provided under the AGPLv3 License.
