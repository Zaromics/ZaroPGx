---
title: Running an analysis
---

# Running an analysis

Open `http://localhost:8765`.

## Choosing the input

**Genomic File(s)** takes one data file per analysis: a VCF, BCF, gVCF, BAM, CRAM, SAM or
FASTQ ({doc}`file-formats`). Add the file's index if you have one; it is recognised and
named, but the pipeline makes its own. For paired-end FASTQ, choose both mates. Any further
data file is ignored, with a warning saying which. A VCF, BCF or gVCF must hold one sample.

**Sample Identifier** is optional. It is printed as the report's Sample ID and must be
1-64 characters: letters, digits, `.`, `_` and `-`, starting with a letter or digit. Left
blank, the job gets a random identifier.

**View Header** shows what ZaroPGx found in the file before anything is uploaded for
analysis: the format, reference build, samples and contigs, and the steps the analysis will
run, or why the file will be refused.

**Run Demo** analyses PharmCAT's example VCF.

## Options

The switches above the progress bar turn steps off. A step the input does not need never
runs, whatever its switch says.

| Switch | Turns off |
|---|---|
| OptiType | HLA typing (alignments only); HLA-A and HLA-B are then not called |
| mtDNA | mitochondrial calling; MT-RNR1 is then not called |
| GATK | conversions and alignment; CRAM, SAM, BCF, gVCF and FASTQ then fail |
| PyPGx | PyPGx's calls; CYP2D6 and the 65 genes outside PharmCAT's panel are then not called |
| Report | the PDF and interactive reports; PharmCAT's own reports remain |

Under **PharmCAT**, two options apply to VCF and BCF files:

- **Assume absent sites = reference** (`--absent-to-ref`): a PharmCAT position the file has
  no record for is taken to be homozygous reference. It also lets PyPGx call a gene the
  file has no record in as reference; with it off, PyPGx does not call such a gene, and
  the report says why. It also lets MT-RNR1 be called reference when no MT-RNR1 allele
  is found, but only where the file's chrM calls show the gene was read (a variant
  inside it, and a haplogroup with none of its defining variants missing). A VCF with
  no chrM calls gets no MT-RNR1 call and no haplogroup either way. This MT-RNR1 part
  applies to a gVCF too.
- **Assume unspecified sites = reference** (`--unspecified-to-ref`): a position whose
  genotype is `./.` is taken to be homozygous reference. PyPGx does not follow it: a
  gene with only `./.` records is not called either way.

Both together are PharmCAT's `--missing-to-ref`. A variant-only VCF (one that lists only
the sites where the sample differs from the reference) leaves most of PharmCAT's positions
absent, so without the first option PharmCAT cannot call many genes; with it, it calls
them, but a position the file never covered reads as reference, which can over-call `*1`
and normal phenotypes. The same holds for PyPGx's genes. The report says which was used,
in its Methodology and in Alerts and Warnings. For alignments and gVCFs the options do
nothing for PharmCAT or PyPGx: ZaroPGx genotypes every PharmCAT position itself, and PyPGx
does not call a gene the reads (or the gVCF's reference blocks) do not cover. (A gVCF's
MT-RNR1 still follows the first option, as above.)

The defaults of all these come from `.env` ({doc}`configuration`).

## While it runs

The progress bar follows the steps as each service reports them. **Cancel** stops the
pipeline and marks the job cancelled; files the job produced so far are removed, and other
jobs' reports are not touched.

A VCF takes minutes. Whole-genome alignments take much longer: on the test machine, a 30x
BAM took about 20 minutes and the same genome as a CRAM about 40. FASTQ adds alignment.

## Results

When the job completes, the page links to:

- the PDF report and the interactive HTML report ({doc}`reports`);
- PharmCAT's own HTML report, JSON and TSV;
- for mitochondrial calling: the haplogroup result, the chrM VCF and, for alignments,
  mtDNA-Server 2's own report;
- a ZIP of every report for this patient.

With the FHIR server on, **Push to FHIR server** sends the job's FHIR bundle to it
({doc}`reports`).

Everything is in `data/reports/<patient_id>/<job_id>/` on the host. The upload itself is
deleted when the job ends, unless `KEEP_UPLOADS=true`.

To run analyses from a script, see {doc}`../developer/api-reference`.
