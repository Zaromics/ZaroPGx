---
title: Credits
---

# Credits and licences

ZaroPGx is built on the software and data below, and we thank their authors and maintainers. Every report lists, under **Platform and Citations**, the parts its own run used, at the versions that ran, with these references. This page lists all of them. It is written by `scripts/render_credits.py` from `app/reports/credits.py`, the list the reports use.

## Software

| Software | Used for | Used when | Licence | Ref. |
|---|---|---|---|---|
| [ZaroPGx](https://github.com/Zaromics/ZaroPGx) | Runs the pipeline and writes the reports | Every run | AGPL-3.0-only |  |
| [Nextflow](https://www.nextflow.io) | Runs the pipeline's steps in order | Every run | Apache-2.0 | 1 |
| [SAMtools, BCFtools and HTSlib](https://www.htslib.org) | PharmCAT's VCF preprocessing; file conversion and variant calling where the input needs them | Every run | MIT; BSD-3-Clause (HTSlib's CRAM code) | 2–3 |
| [pysam](https://github.com/pysam-developers/pysam) | Reads the input files for ZaroPGx and PyPGx | Every run | MIT |  |
| [GATK](https://gatk.broadinstitute.org) | gVCF genotyping, liftover and read alignment | gVCF, GRCh37 VCF or BCF, FASTQ | Apache-2.0 | 4–6 |
| [Picard](https://broadinstitute.github.io/picard/) | LiftoverVcf (GRCh37 to GRCh38) and FastqToSam | GRCh37 VCF or BCF, FASTQ | MIT | 7 |
| [BWA-MEM](https://github.com/lh3/bwa) | Aligns the reads to GRCh38 (GATK BwaSpark) | FASTQ | Apache-2.0 (as built into GATK) | 8 |
| [PyPGx](https://pypgx.readthedocs.io) | Star alleles beyond PharmCAT's genes, CYP2D6 and structural variants | Every run, unless PyPGx is off | MIT | 9–11 |
| [fuc](https://github.com/sbslee/fuc) | Calls variants from the reads for PyPGx | BAM, CRAM, SAM, FASTQ | MIT | 9 |
| [Beagle](https://faculty.washington.edu/browning/beagle/beagle.html) | Phases variants for PyPGx | When PyPGx phases an input that is not already phased | GPL-3.0-or-later | 12 |
| [scikit-learn](https://scikit-learn.org) | Runs PyPGx's copy-number models | Whole-genome alignments of 15x or more | BSD-3-Clause | 13 |
| [OptiType](https://github.com/FRED-2/OptiType) | HLA-A, HLA-B and HLA-C typing | BAM, CRAM, SAM, FASTQ, with HLA typing on | BSD-3-Clause | 14 |
| [YARA](https://github.com/seqan/seqan/tree/main/apps/yara) | Maps the HLA reads for OptiType | BAM, CRAM, SAM, FASTQ, with HLA typing on | BSD-3-Clause | 15–16 |
| [SeqAn](https://www.seqan.de) | The library YARA is built on | BAM, CRAM, SAM, FASTQ, with HLA typing on | BSD-3-Clause | 17 |
| [GLPK](https://www.gnu.org/software/glpk/) | Solves OptiType's integer linear program | BAM, CRAM, SAM, FASTQ, with HLA typing on | GPL-3.0-or-later |  |
| [Pyomo](https://www.pyomo.org) | Builds OptiType's integer linear program | BAM, CRAM, SAM, FASTQ, with HLA typing on | BSD-3-Clause | 18–19 |
| [mtDNA-Server 2](https://github.com/genepi/mtdna-server-2) | Mitochondrial variants, haplogroup and the MT-RNR1 call | With mtDNA analysis on | MIT | 20 |
| [mutserve](https://github.com/seppinho/mutserve) | Calls mitochondrial variants from the reads | BAM, CRAM, SAM, FASTQ, with mtDNA analysis on | MIT | 21–22 |
| [Haplogrep 3](https://github.com/genepi/haplogrep3) | Assigns the mitochondrial haplogroup | With mtDNA analysis on | MIT | 23 |
| [haplocheck](https://github.com/genepi/haplocheck) | Checks the mitochondrial reads for contamination | BAM, CRAM, SAM, FASTQ, with mtDNA analysis on | MIT | 22 |
| [PharmCAT](https://pharmcat.org) | Diplotypes, phenotypes and drug guidance | Every run | MPL-2.0 | 24–26 |
| [WeasyPrint](https://weasyprint.org) | Draws the PDF | The PDF report | BSD-3-Clause |  |
| [Kroki](https://kroki.io) | Draws the workflow diagram | The workflow diagram, with Kroki on | MIT |  |
| [Mermaid](https://mermaid.js.org) | The workflow diagram's layout | The workflow diagram, with Kroki on | MIT | 27 |
| [Chart.js](https://www.chartjs.org) | The interactive report's charts | The interactive report | MIT |  |
| [D3](https://d3js.org) | The interactive report's gene-drug network | The interactive report | ISC | 28 |

## Data and knowledge

| Source | Used for | Used when | Licence or terms | Ref. |
|---|---|---|---|---|
| [GRCh38 (Genome Reference Consortium)](https://www.ncbi.nlm.nih.gov/grc/human) | The genome build every call is made on | Every run | No restrictions (NCBI) | 29 |
| [PharmCAT's GRCh38 reference FASTA](https://zenodo.org/records/7288118) | Normalises the input in PharmCAT's preprocessor | Every run | CC-BY-4.0 | 30 |
| [UCSC hg38](https://hgdownload.soe.ucsc.edu/goldenPath/hg38/bigZips/) | The reference for reads, gVCFs and liftover | BAM, CRAM, SAM, FASTQ, gVCF, GRCh37 VCF or BCF | Freely available (UCSC) | 31 |
| [1000 Genomes GRCh38 analysis set](https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/technical/reference/GRCh38_reference_genome/) | Decodes the CRAM, with UCSC hg38, by sequence checksum | CRAM | EMBL-EBI terms of use | 32 |
| [GRCh37](https://www.ncbi.nlm.nih.gov/grc/human) | The build the input was on, before liftover | GRCh37 VCF or BCF | No restrictions (NCBI) | 33 |
| [UCSC hg19ToHg38 chain](https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/) | Lifts the input from GRCh37 to GRCh38 | GRCh37 VCF or BCF | UCSC Chain Files licence (non-commercial) | 34 |
| [1000 Genomes Project 30x panel](https://www.internationalgenome.org) | The haplotypes Beagle phases against | When Beagle phases | EMBL-EBI terms of use | 35–36 |
| [IPD-IMGT/HLA](https://www.ebi.ac.uk/ipd/imgt/hla/) | The HLA allele sequences OptiType types against | BAM, CRAM, SAM, FASTQ, with HLA typing on | CC-BY-ND | 37–38 |
| [PhyloTree](https://github.com/genepi/phylotree-fu-rcrs) | The mitochondrial phylogeny haplogroups are assigned on | With mtDNA analysis on | MIT (Forensic Update) | 39–40 |
| [Revised Cambridge Reference Sequence (rCRS)](https://www.ncbi.nlm.nih.gov/nuccore/NC_012920.1) | The mitochondrial reference | With mtDNA analysis on | No restrictions (NCBI) | 41 |
| [CPIC](https://www.clinpgx.org/cpic) | Guidelines: phenotypes and recommendations | Every run | CC0-1.0 | 42 |
| [DPWG](https://www.knmp.nl/dossiers/farmacogenetica/pharmacogenetics) | Guidelines of the Dutch Pharmacogenetics Working Group (KNMP) | Every run | Not stated | 43–44 |
| [FDA](https://www.fda.gov/medical-devices/precision-medicine/table-pharmacogenetic-associations) | Drug-label annotations and pharmacogenetic associations | Every run | Public domain (U.S. Government) | 45–46 |
| [ClinPGx (PharmGKB)](https://www.clinpgx.org) | Allele definitions, guideline and label annotations, as PharmCAT carries them | Every run | CC-BY-SA-4.0 | 47 |
| [PharmVar](https://www.pharmvar.org) | Star-allele definitions and names | Every run | CC-BY-SA-4.0 | 48–51 |
| [dbSNP](https://www.ncbi.nlm.nih.gov/snp/) | Variant identifiers (rs numbers) | Every run | No restrictions (NCBI) | 52 |

## The platform

What runs the application, its database and its pages. None of it touches a call, so reports do not list it.

| Component | Used for | Licence |
|---|---|---|
| [FastAPI](https://fastapi.tiangolo.com) | The web application and its API | MIT |
| [Starlette](https://www.starlette.io) | FastAPI's web toolkit | BSD-3-Clause |
| [Uvicorn](https://www.uvicorn.org) | Serves the application | BSD-3-Clause |
| [Gunicorn](https://gunicorn.org) | Serves the HLA and mtDNA services | MIT |
| [Pydantic](https://docs.pydantic.dev) | Request and settings models | MIT |
| [SQLAlchemy](https://www.sqlalchemy.org) | Database access | MIT |
| [psycopg](https://www.psycopg.org) | PostgreSQL driver | LGPL-3.0-only |
| [PostgreSQL](https://www.postgresql.org) | Jobs, results and FHIR storage | PostgreSQL |
| [HAPI FHIR JPA Server](https://hapifhir.io) | The bundled FHIR server | Apache-2.0 |
| [Jinja2](https://jinja.palletsprojects.com) | Report templates | BSD-3-Clause |
| [ReportLab](https://www.reportlab.com/opensource/) | The fallback PDF engine | BSD-3-Clause |
| [Pillow](https://python-pillow.github.io) | Images for WeasyPrint and ReportLab | MIT-CMU |
| [Biopython](https://biopython.org) | Reads FASTQ and FASTA headers | Biopython License or BSD-3-Clause |
| [Requests](https://requests.readthedocs.io) | HTTP between the services | Apache-2.0 |
| [HTTPX](https://www.python-httpx.org) | HTTP between the services | BSD-3-Clause |
| [AIOHTTP](https://docs.aiohttp.org) | HTTP between the services | Apache-2.0 |
| [python-multipart](https://github.com/Kludex/python-multipart) | File uploads | Apache-2.0 |
| [python-jose](https://github.com/mpdavis/python-jose) | Sign-in tokens, when sign-in is on | MIT |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | Reads .env | BSD-3-Clause |
| [Werkzeug](https://werkzeug.palletsprojects.com) | Upload file names | BSD-3-Clause |
| [Bootstrap](https://getbootstrap.com) | The web pages' styles and scripts | MIT |
| [Bootstrap Icons](https://icons.getbootstrap.com) | The web pages' icons | MIT |
| [Swagger UI](https://swagger.io/tools/swagger-ui/) | The API explorer at /api/docs | Apache-2.0 |
| [ReDoc](https://redocly.com/redoc) | The API reference at /api/redoc | MIT |
| [Sphinx](https://www.sphinx-doc.org) | Builds this documentation | BSD-2-Clause |
| [MyST-Parser](https://myst-parser.readthedocs.io) | Markdown for Sphinx | MIT |
| [Read the Docs Sphinx theme](https://sphinx-rtd-theme.readthedocs.io) | This documentation's theme | MIT |
| [Liberation fonts](https://github.com/liberationfonts) | The PDF's text | GPL-2.0-only WITH Font-exception-2.0 |
| [DejaVu fonts](https://dejavu-fonts.github.io) | Monospace and fallback text | Bitstream-Vera |
| [Noto fonts](https://fonts.google.com/noto) | Fallback text | OFL-1.1 |
| [Eclipse Temurin and OpenJDK](https://adoptium.net) | Run the Java tools | GPL-2.0-only WITH Classpath-exception-2.0 |
| [R, R Markdown and their packages](https://www.r-project.org) | Draw mtDNA-Server 2's own report | R: GPL-2.0-only OR GPL-3.0-only; packages: their own |
| [Docker and Docker Compose](https://www.docker.com) | Run the services | Apache-2.0 |
| [uv](https://docs.astral.sh/uv/) | Installs the Python packages | MIT OR Apache-2.0 |

## References

1. Di Tommaso P, Chatzou M, Floden EW, Barja PP, Palumbo E, Notredame C. Nextflow enables reproducible computational workflows. *Nature Biotechnology*. 2017;35(4):316–319. doi:[10.1038/nbt.3820](https://doi.org/10.1038/nbt.3820)
2. Danecek P, Bonfield JK, Liddle J, et al. Twelve years of SAMtools and BCFtools. *GigaScience*. 2021;10(2):giab008. doi:[10.1093/gigascience/giab008](https://doi.org/10.1093/gigascience/giab008)
3. Bonfield JK, Marshall J, Danecek P, et al. HTSlib: C library for reading/writing high-throughput sequencing data. *GigaScience*. 2021;10(2):giab007. doi:[10.1093/gigascience/giab007](https://doi.org/10.1093/gigascience/giab007)
4. Van der Auwera GA, O'Connor BD. Genomics in the Cloud: Using Docker, GATK, and WDL in Terra. 1st ed. O'Reilly Media; 2020.
5. McKenna A, Hanna M, Banks E, et al. The Genome Analysis Toolkit: A MapReduce framework for analyzing next-generation DNA sequencing data. *Genome Research*. 2010;20(9):1297–1303. doi:[10.1101/gr.107524.110](https://doi.org/10.1101/gr.107524.110)
6. Poplin R, Ruano-Rubio V, DePristo MA, et al. Scaling accurate genetic variant discovery to tens of thousands of samples [Preprint]. *bioRxiv*. 2017:201178. doi:[10.1101/201178](https://doi.org/10.1101/201178)
7. Broad Institute. Picard Toolkit [Software]. GitHub repository; 2019. <https://broadinstitute.github.io/picard/>
8. Li H. Aligning sequence reads, clone sequences and assembly contigs with BWA-MEM [Preprint]. *arXiv*. 2013:1303.3997v2. doi:[10.48550/arXiv.1303.3997](https://doi.org/10.48550/arXiv.1303.3997)
9. Lee SB, Shin JY, Kwon NJ, Kim C, Seo JS. ClinPharmSeq: A targeted sequencing panel for clinical pharmacogenetics implementation. *PLOS ONE*. 2022;17(7):e0272129. doi:[10.1371/journal.pone.0272129](https://doi.org/10.1371/journal.pone.0272129)
10. Lee SB, Wheeler MM, Patterson K, et al. Stargazer: a software tool for calling star alleles from next-generation sequencing data using CYP2D6 as a model. *Genetics in Medicine*. 2019;21(2):361–372. doi:[10.1038/s41436-018-0054-0](https://doi.org/10.1038/s41436-018-0054-0)
11. Lee SB, Wheeler MM, Thummel KE, Nickerson DA. Calling Star Alleles With Stargazer in 28 Pharmacogenes With Whole Genome Sequences. *Clinical Pharmacology & Therapeutics*. 2019;106(6):1328–1337. doi:[10.1002/cpt.1552](https://doi.org/10.1002/cpt.1552)
12. Browning BL, Tian X, Zhou Y, Browning SR. Fast two-stage phasing of large-scale sequence data. *American Journal of Human Genetics*. 2021;108(10):1880–1890. doi:[10.1016/j.ajhg.2021.08.005](https://doi.org/10.1016/j.ajhg.2021.08.005)
13. Pedregosa F, Varoquaux G, Gramfort A, et al. Scikit-learn: Machine Learning in Python. *Journal of Machine Learning Research*. 2011;12(85):2825–2830. <https://jmlr.org/papers/v12/pedregosa11a.html>
14. Szolek A, Schubert B, Mohr C, Sturm M, Feldhahn M, Kohlbacher O. OptiType: precision HLA typing from next-generation sequencing data. *Bioinformatics*. 2014;30(23):3310–3316. doi:[10.1093/bioinformatics/btu548](https://doi.org/10.1093/bioinformatics/btu548)
15. Siragusa E, Weese D, Reinert K. Fast and accurate read mapping with approximate seeds and multiple backtracking. *Nucleic Acids Research*. 2013;41(7):e78. doi:[10.1093/nar/gkt005](https://doi.org/10.1093/nar/gkt005)
16. Siragusa E. Approximate string matching for high-throughput sequencing [dissertation]. Freie Universität Berlin; 2015. doi:[10.17169/refubium-15562](https://doi.org/10.17169/refubium-15562)
17. Reinert K, Dadi TH, Ehrhardt M, et al. The SeqAn C++ template library for efficient sequence analysis: A resource for programmers. *Journal of Biotechnology*. 2017;261:157–168. doi:[10.1016/j.jbiotec.2017.07.017](https://doi.org/10.1016/j.jbiotec.2017.07.017)
18. Bynum ML, Hackebeil GA, Hart WE, et al. Pyomo — Optimization Modeling in Python. 3rd ed. Springer; 2021. doi:[10.1007/978-3-030-68928-5](https://doi.org/10.1007/978-3-030-68928-5)
19. Hart WE, Watson JP, Woodruff DL. Pyomo: modeling and solving mathematical programs in Python. *Mathematical Programming Computation*. 2011;3(3):219–260. doi:[10.1007/s12532-011-0026-8](https://doi.org/10.1007/s12532-011-0026-8)
20. Weissensteiner H, Forer L, Kronenberg F, Schönherr S. mtDNA-Server 2: advancing mitochondrial DNA analysis through highly parallelized data processing and interactive analytics. *Nucleic Acids Research*. 2024;52(W1):W102–W107. doi:[10.1093/nar/gkae296](https://doi.org/10.1093/nar/gkae296)
21. Weissensteiner H, Forer L, Fuchsberger C, et al. mtDNA-Server: next-generation sequencing data analysis of human mitochondrial DNA in the cloud. *Nucleic Acids Research*. 2016;44(W1):W64–W69. doi:[10.1093/nar/gkw247](https://doi.org/10.1093/nar/gkw247)
22. Weissensteiner H, Forer L, Fendt L, et al. Contamination detection in sequencing studies using the mitochondrial phylogeny. *Genome Research*. 2021;31(2):309–316. doi:[10.1101/gr.256545.119](https://doi.org/10.1101/gr.256545.119)
23. Schönherr S, Weissensteiner H, Kronenberg F, Forer L. Haplogrep 3 - an interactive haplogroup classification and analysis platform. *Nucleic Acids Research*. 2023;51(W1):W263–W268. doi:[10.1093/nar/gkad284](https://doi.org/10.1093/nar/gkad284)
24. Sangkuhl K, Whirl-Carrillo M, Whaley RM, et al. Pharmacogenomics Clinical Annotation Tool (PharmCAT). *Clinical Pharmacology & Therapeutics*. 2020;107(1):203–210. doi:[10.1002/cpt.1568](https://doi.org/10.1002/cpt.1568)
25. Li B, Sangkuhl K, Keat K, et al. How to Run the Pharmacogenomics Clinical Annotation Tool (PharmCAT). *Clinical Pharmacology & Therapeutics*. 2023;113(5):1036–1047. doi:[10.1002/cpt.2790](https://doi.org/10.1002/cpt.2790)
26. Klein TE, Ritchie MD. PharmCAT: A Pharmacogenomics Clinical Annotation Tool. *Clinical Pharmacology & Therapeutics*. 2018;104(1):19–22. doi:[10.1002/cpt.928](https://doi.org/10.1002/cpt.928)
27. Sveidqvist K, Contributors to Mermaid. Mermaid: Generate diagrams from markdown-like text [Software]. GitHub repository. <https://github.com/mermaid-js/mermaid>
28. Bostock M, Ogievetsky V, Heer J. D³ Data-Driven Documents. *IEEE Transactions on Visualization and Computer Graphics*. 2011;17(12):2301–2309. doi:[10.1109/TVCG.2011.185](https://doi.org/10.1109/TVCG.2011.185)
29. Schneider VA, Graves-Lindsay T, Howe K, et al. Evaluation of GRCh38 and de novo haploid genome assemblies demonstrates the enduring quality of the reference assembly. *Genome Research*. 2017;27(5):849–864. doi:[10.1101/gr.213611.116](https://doi.org/10.1101/gr.213611.116)
30. Woon M. GRCh38.p13 Reference FASTA (bgzip'd with faidx) [Data set]. Zenodo; 2022. doi:[10.5281/zenodo.7288118](https://doi.org/10.5281/zenodo.7288118)
31. Casper J, Speir ML, Raney BJ, et al. The UCSC Genome Browser database: 2026 update. *Nucleic Acids Research*. 2026;54(D1):D1331–D1335. doi:[10.1093/nar/gkaf1250](https://doi.org/10.1093/nar/gkaf1250)
32. Zheng-Bradley X, Streeter I, Fairley S, Richardson D, Clarke L, Flicek P; 1000 Genomes Project Consortium. Alignment of 1000 Genomes Project reads to reference assembly GRCh38. *GigaScience*. 2017;6(7):1–8. doi:[10.1093/gigascience/gix038](https://doi.org/10.1093/gigascience/gix038)
33. Church DM, Schneider VA, Graves T, et al. Modernizing Reference Genome Assemblies. *PLOS Biology*. 2011;9(7):e1001091. doi:[10.1371/journal.pbio.1001091](https://doi.org/10.1371/journal.pbio.1001091)
34. Hinrichs AS, Karolchik D, Baertsch R, et al. The UCSC Genome Browser Database: update 2006. *Nucleic Acids Research*. 2006;34(Database issue):D590–D598. doi:[10.1093/nar/gkj144](https://doi.org/10.1093/nar/gkj144)
35. Byrska-Bishop M, Evani US, Zhao X, et al. High-coverage whole-genome sequencing of the expanded 1000 Genomes Project cohort including 602 trios. *Cell*. 2022;185(18):3426–3440.e19. doi:[10.1016/j.cell.2022.08.004](https://doi.org/10.1016/j.cell.2022.08.004)
36. 1000 Genomes Project Consortium; Auton A, Brooks LD, Durbin RM, et al. A global reference for human genetic variation. *Nature*. 2015;526(7571):68–74. doi:[10.1038/nature15393](https://doi.org/10.1038/nature15393)
37. Barker DJ, Natarajan RHL, Cooper MA, et al. The IPD-IMGT/HLA database: recent developments in sequence submission. *Nucleic Acids Research*. 2026;54(D1):D1152–D1158. doi:[10.1093/nar/gkaf1218](https://doi.org/10.1093/nar/gkaf1218)
38. Robinson J, Halliwell JA, McWilliam H, Lopez R, Parham P, Marsh SGE. The IMGT/HLA database. *Nucleic Acids Research*. 2013;41(D1):D1222–D1227. doi:[10.1093/nar/gks949](https://doi.org/10.1093/nar/gks949)
39. van Oven M, Kayser M. Updated comprehensive phylogenetic tree of global human mitochondrial DNA variation. *Human Mutation*. 2009;30(2):E386–E394. doi:[10.1002/humu.20921](https://doi.org/10.1002/humu.20921)
40. Dür A, Huber N, Parson W. Fine-Tuning Phylogenetic Alignment and Haplogrouping of mtDNA Sequences. *International Journal of Molecular Sciences*. 2021;22(11):5747. doi:[10.3390/ijms22115747](https://doi.org/10.3390/ijms22115747)
41. Andrews RM, Kubacka I, Chinnery PF, Lightowlers RN, Turnbull DM, Howell N. Reanalysis and revision of the Cambridge reference sequence for human mitochondrial DNA. *Nature Genetics*. 1999;23(2):147. doi:[10.1038/13779](https://doi.org/10.1038/13779)
42. Caudle KE, Whirl-Carrillo M, Relling MV, et al. Advancing Clinical Pharmacogenomics Worldwide Through the Clinical Pharmacogenetics Implementation Consortium (CPIC). *Clinical Pharmacology & Therapeutics*. 2025;118(6):1512–1522. doi:[10.1002/cpt.70005](https://doi.org/10.1002/cpt.70005)
43. Swen JJ, Wilting I, de Goede AL, et al. Pharmacogenetics: From Bench to Byte. *Clinical Pharmacology & Therapeutics*. 2008;83(5):781–787. doi:[10.1038/sj.clpt.6100507](https://doi.org/10.1038/sj.clpt.6100507)
44. Swen JJ, Nijenhuis M, de Boer A, et al. Pharmacogenetics: From Bench to Byte—An Update of Guidelines. *Clinical Pharmacology & Therapeutics*. 2011;89(5):662–673. doi:[10.1038/clpt.2011.34](https://doi.org/10.1038/clpt.2011.34)
45. U.S. Food and Drug Administration. Table of Pharmacogenetic Associations. <https://www.fda.gov/medical-devices/precision-medicine/table-pharmacogenetic-associations>
46. U.S. Food and Drug Administration. Table of Pharmacogenomic Biomarkers in Drug Labeling. <https://www.fda.gov/drugs/science-and-research-drugs/table-pharmacogenomic-biomarkers-drug-labeling>
47. Whirl-Carrillo M, Huddart R, Gong L, et al. An Evidence-Based Framework for Evaluating Pharmacogenomics Knowledge for Personalized Medicine. *Clinical Pharmacology & Therapeutics*. 2021;110(3):563–572. doi:[10.1002/cpt.2350](https://doi.org/10.1002/cpt.2350)
48. Gaedigk A, Casey ST, Whirl-Carrillo M, Miller NA, Klein TE. Pharmacogene Variation Consortium: A Global Resource and Repository for Pharmacogene Variation. *Clinical Pharmacology & Therapeutics*. 2021;110(3):542–545. doi:[10.1002/cpt.2321](https://doi.org/10.1002/cpt.2321)
49. Gaedigk A, Whirl-Carrillo M, Pratt VM, Miller NA, Klein TE. PharmVar and the Landscape of Pharmacogenetic Resources. *Clinical Pharmacology & Therapeutics*. 2020;107(1):43–46. doi:[10.1002/cpt.1654](https://doi.org/10.1002/cpt.1654)
50. Gaedigk A, Sangkuhl K, Whirl-Carrillo M, Twist GP, Klein TE, Miller NA; PharmVar Steering Committee. The Evolution of PharmVar. *Clinical Pharmacology & Therapeutics*. 2019;105(1):29–32. doi:[10.1002/cpt.1275](https://doi.org/10.1002/cpt.1275)
51. Gaedigk A, Ingelman-Sundberg M, Miller NA, Leeder JS, Whirl-Carrillo M, Klein TE; PharmVar Steering Committee. The Pharmacogene Variation (PharmVar) Consortium: Incorporation of the Human Cytochrome P450 (CYP) Allele Nomenclature Database. *Clinical Pharmacology & Therapeutics*. 2018;103(3):399–401. doi:[10.1002/cpt.910](https://doi.org/10.1002/cpt.910)
52. Phan L, Zhang H, Wang Q, et al. The evolution of dbSNP: 25 years of impact in genomic research. *Nucleic Acids Research*. 2025;53(D1):D925–D931. doi:[10.1093/nar/gkae977](https://doi.org/10.1093/nar/gkae977)

## Terms of use

Reports print the notices of the sources their run used. All of them:

- **UCSC hg19ToHg38 chain.** The UCSC Chain Files are property of The Regents of the University of California, and made available free for non-commercial use by Independent Researchers and Nonprofit Organizations. Any other use of UCSC Chain Files requires a commercial license (https://genome.ucsc.edu/license/).

- **1000 Genomes Project 30x panel.** The 1000 Genomes 30x data were generated at the New York Genome Center with funds provided by NHGRI Grants 3UM1HG008901-03S1 and 3UM1HG008901-04S2, from cell lines and DNA samples obtained from the NIGMS Human Genetic Cell Repository at the Coriell Institute for Medical Research.

- **IPD-IMGT/HLA.** HLA allele sequences are from the IPD-IMGT/HLA Database, used under the Creative Commons Attribution-NoDerivs licence.

- **CPIC.** CPIC® content is available under CC0 1.0. CPIC guidelines and content are subject to updates and modifications; refer to ClinPGx (https://www.clinpgx.org/cpic) for the most current content. CPIC® is a registered service mark of the U.S. Department of Health & Human Services (HHS).

- **ClinPGx (PharmGKB).** Content from ClinPGx/PharmGKB (https://www.clinpgx.org), as carried by PharmCAT, is used under the Creative Commons Attribution-ShareAlike 4.0 International License (https://creativecommons.org/licenses/by-sa/4.0/). ZaroPGx changes its presentation and may abridge it. ClinPGx does not endorse ZaroPGx. Under ClinPGx's terms its data are for research use, not for sale.

- **PharmVar.** Star-allele definitions from the Pharmacogene Variation Consortium (PharmVar, https://www.pharmvar.org), through CPIC and ClinPGx, are used under the Creative Commons Attribution-ShareAlike 4.0 International License, for research use.

The FHIR export carries codes from these terminologies:

- **LOINC.** This material contains content from LOINC (http://loinc.org). LOINC is copyright © Regenstrief Institute, Inc. and the Logical Observation Identifiers Names and Codes (LOINC) Committee and is available at no cost under the license at http://loinc.org/license. LOINC® is a registered United States trademark of Regenstrief Institute, Inc.

- **SNOMED CT.** This material includes SNOMED Clinical Terms® (SNOMED CT®) which is used by permission of the International Health Terminology Standards Development Organisation (IHTSDO). All rights reserved. SNOMED CT®, was originally created by The College of American Pathologists. "SNOMED" and "SNOMED CT" are registered trademarks of the IHTSDO. Use of SNOMED CT is subject to its licence (https://www.snomed.org/get-snomed).

- **HGNC.** Gene identifiers from the HUGO Gene Nomenclature Committee (HGNC, https://www.genenames.org), released under CC0.

- **HL7 FHIR.** HL7®, FHIR® and the FHIR [FLAME DESIGN] are the registered trademarks of Health Level Seven International and their use does not constitute endorsement by HL7. FHIR R4 and the Genomics Reporting Implementation Guide are published under CC0.

## Citing ZaroPGx

`CITATION.cff` in the repository says how to cite ZaroPGx itself. Please cite the tools and data above as well: ZaroPGx's results are theirs.
