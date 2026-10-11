---
title: FAQ
curation: not
---

# FAQ

**Is this a clinical test?**
No. ZaroPGx runs established tools (PharmCAT, PyPGx, OptiType, mtDNA-Server 2, GATK) on data
you provide and reports what they find. A result that matters for a treatment should be
confirmed by an accredited laboratory and interpreted by a clinician.

**Which genes does it cover?**
91. The 25 PharmCAT reports on, which drive the drug recommendations: PharmCAT calls 21 of
them from the data and takes CYP2D6 (from PyPGx), HLA-A and HLA-B (from OptiType) and
MT-RNR1 (from mtDNA-Server 2) as outside calls. Then 65 more that PyPGx calls, and HLA-C
from OptiType. `config/genes.json` is the list.

**What can I upload?**
VCF, BCF, gVCF, BAM, CRAM, SAM, and short-read FASTQ up to 20 GB, on GRCh38; a GRCh37 VCF or
BCF is lifted over. See {doc}`file-formats`.

**Can I analyse my 23andMe or AncestryDNA file?**
Not yet. A chip carries a small fraction of the positions PharmCAT calls on, and PyPGx would
read the missing ones as reference, so a poor metaboliser could be reported as normal. The
refusal explains this with the numbers for your chip.

**Why are so many genes "Not called" from my VCF?**
A variant-only VCF does not say which positions match the reference. See
{doc}`troubleshooting`.

**Does my data leave my machine?**
The analysis runs on your host and the files stay in `data/`. Installing and the first start
download software and reference data; opening an interactive report loads two JavaScript
libraries from public CDNs. Nothing about your data is sent. See {doc}`deployment`.

**How long are files kept?**
An upload is deleted when its job ends (`KEEP_UPLOADS=true` keeps it). Reports stay until you
delete them.

**Can several people share an instance?**
They can share one password (`ZAROPGX_AUTH_MODE=password`); there are no accounts, and
everyone with the password sees every report. See {doc}`deployment`.

**How long does an analysis take?**
A VCF, minutes. A 30x whole-genome BAM took about 20 minutes on the test machine, its CRAM
about 40; FASTQ adds alignment.

**Which tool versions does it use?**
PharmCAT 3.4.0, PyPGx 0.27.0, GATK 4.7.0.0, OptiType 1.5.0, mtDNA-Server 2 v2.1.16. Each
report's Platform and Citations page lists those that ran for it.

**What do "N/A", "Possibly Wild Type" and "or" mean in a report?**
See the labels in {doc}`reports`.
