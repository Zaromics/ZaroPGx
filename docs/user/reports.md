---
title: Reports
---

# Reports

Each analysis writes its outputs to `data/reports/<patient_id>/<job_id>/`:

| File | What it is |
|---|---|
| `<job_id>_pgx_report.pdf` | The report, for printing |
| `<job_id>_pgx_report_interactive.html` | The same results, browsable, with charts |
| `<job_id>_pgx_pharmcat.html`, `.json`, `.tsv` | PharmCAT's own reports |
| `<job_id>_pgx_fhir_report.json`, `.xml` | The results as a FHIR R4 bundle |
| `mtdna_result.json`, `mtdna_variants.vcf.gz`, `mtdna_report.html` | Mitochondrial haplogroup and MT-RNR1 call, the chrM variants, and (alignments only) mtDNA-Server 2's own report |
| `pypgx_result.json` | PyPGx's calls, with its evidence |
| `report.html`, `timeline.html`, `trace.txt` | Nextflow's record of the run |

Lanes that convert or genotype the input also keep the intermediate VCF (for example
`lifted.grch38.vcf.gz` after a liftover).

## The PDF

The sections, in order. Each starts on a new page; sections that must fit a page are
fitted to it.

**Cover.** Sample ID, report ID and date, and the reference build if the file was lifted
over. **Methodology** says how the sample was analysed and whose guidelines PharmCAT applies
(CPIC, DPWG, FDA), PharmCAT's allele-definition build and guideline data version, and how
positions the file did not cover were treated. The disclaimer follows.

**Alerts and Warnings.** What qualifies these results: limits of the input type (a VCF
cannot give HLA calls, for instance), genes called from partial coverage, variants PharmCAT
could not read, positions assumed to be reference, genes whose reads mostly had mapping
quality 0 (an alignment that was not ALT-aware), a PharmCAT version nobody has verified.
Read these first.

**Executive Summary.** The 25 genes PharmCAT reports on, on one page, the same genes in the
same order in every report. Each row has the diplotype, the phenotype and, where the
guideline uses one, the activity score. A reference call is shown as PharmCAT labels it
(CFTR's as "Reference/Reference"; the notes give the allele's name). A gene the data did not call is a grey "Not called"
row. The page is a right-hand page, and its notes are on the page behind it, so a
double-sided print holds both on one sheet.

**Genetic Results.** Every gene the run called: up to 91, PharmCAT's 25 and the other 66
from PyPGx and OptiType. Columns: diplotype, phenotype, activity score, implications, and
two letters:

- **Call**: the tool that made the call. C PharmCAT, P PyPGx, O OptiType, M mtDNA-Server 2,
  X an outside call whose tool was not recorded, – no call, ? not recorded.
- **Guide**: the guideline behind the phenotype. C CPIC, D DPWG, F FDA, P PharmGKB.

Genes examined without a call are listed after the table, with the reason when one is known.

**Mitochondrial DNA**, when that step ran: the haplogroup and its quality, and the MT-RNR1
call with its phenotype (aminoglycoside-induced hearing loss).

**Drugs.** Every drug with guidance for this sample, marked by the strongest
recommendation: Strong, Moderate or Optional (CPIC's strengths); Guideline available (DPWG
or FDA guidance without a CPIC strength); No recommendation (a guideline that gives no
genotype-based action); Unclassified.

**Drug Recommendations.** For each drug, each source's recommendation: CPIC and DPWG
guidelines, FDA labels and FDA's pharmacogenetic associations, with the strength and a link
to the guideline's page at ClinPGx, where its full text and literature are. When the input is unphased and a gene has several possible diplotypes, guidance
that differs between them is marked "Applies if".

**Workflow Diagram**: the steps this run took, in the order they ran, and what each
passed to PharmCAT; a step that was skipped is drawn dashed, off the path. Then
**Platform and Citations**: every tool and data source this run used, at the versions that
ran, with numbered references and the terms of use their sources require ({doc}`credits`
lists all of them); and the **Genomic Datafile Header** as uploaded.

## Labels

Badge colours follow CPIC's ranking of each phenotype (its EHR priority):

- green: normal metabolism or function, G6PD Normal, an HLA risk allele negative, normal
  risk of aminoglycoside-induced hearing loss;
- amber: intermediate metabolism, decreased function, G6PD Variable;
- red: poor metabolism or function, G6PD Deficient, an HLA risk allele positive, Malignant
  Hyperthermia Susceptible, increased risk of aminoglycoside-induced hearing loss;
- blue: rapid metabolism and increased function, which is not necessarily better;
- grey: uncertain and indeterminate results, unresolved calls ("A or B"), and the rest.

The label is always printed beside the colour.

| Label | Meaning |
|---|---|
| Normal / Intermediate / Poor / Rapid / Ultrarapid Metabolizer, Normal / Decreased / Poor Function, etc. | The phenotype PharmCAT assigns from the alleles' functions in its guideline data |
| N/A | The tool that called the gene defines no phenotype for it (CYP4F2, VKORC1, IFNL3, most PyPGx-only genes, and HLA-C, which OptiType types and PharmCAT does not cover); guidance that uses the gene works from the genotype |
| Possibly Wild Type | A reference call, with no phenotype, from a file that lists only variants and may not cover every position PharmCAT checks (VCF, BCF) |
| Likely Wild Type | A reference call, with no phenotype, from data genotyped at every position (reads, or a gVCF's reference blocks) |
| A or B | The input is unphased and PharmCAT found more than one diplotype that fits; each is listed, with its phenotype |
| Uncertain Susceptibility | RYR1 and CACNA1S: no known malignant hyperthermia variant was found, which does not rule the condition out |
| Not called | The data did not support a call; no phenotype or guidance follows |

## The interactive report

A single HTML file with tabs: Alerts, Summary, Genes, mtDNA, Drugs, Recs, Visualizations
(phenotype chart, drug-gene network, recommendations chart), Method, Export, Header and
Acknowledgements. It carries its own scripts (Chart.js and D3 included), so it works offline
and when opened from the downloaded ZIP. **Export** links the job's FHIR bundle, PharmCAT's
JSON, TSV and HTML, and the PDF, whichever were written.

## FHIR

`<job_id>_pgx_fhir_report.json` and `.xml` hold the run's results as a FHIR R4 bundle,
written with every report while `FHIR_EXPORT_ENABLED=true`: each gene's genotype and
phenotype, each drug's implication and recommendations, on the profiles of the HL7 Genomics
Reporting IG 3.0.0. The bundle validates against those profiles with no errors (HAPI FHIR's
`$validate`, October 2026); {doc}`../samples/README` lists the warnings that remain and why.
Bundles written before then do not validate, and their XML is not readable as FHIR. The
`/fhir` endpoints ({doc}`../developer/api-reference`) also generate, preview and save a
bundle.

Writing the files sends nothing anywhere. To put a job's results on a FHIR server, ask for
it: `POST /fhir/push/workflow/{job_id}` sends the job's bundle to `FHIR_SERVER_URL`, in
Compose the bundled HAPI FHIR server (`127.0.0.1:8090/fhir` on the host). It goes as one
transaction. The Patient is created once per ZaroPGx patient and reused by later pushes;
the report and its resources are created the first time and updated in place when the job
is pushed again, not copied. Every resource pushed for a job, the Patient aside, is tagged
`urn:zaropgx:job-id|<job_id>`, and a push deletes, in the same transaction, the job's
resources its bundle no longer has: the recommendations a regenerated report dropped, or a
whole older bundle written before October 2026. The answer says how many resources the
server created, how many it already had and how many it deleted, and where the report is
(`DiagnosticReport/<id>`). If the server is down or refuses the bundle, the request fails
with the reason (HTTP 502; the `X-FHIR-Server-Reachable` header is `false` for a server that
could not be reached, `true` for one that answered with an error), nothing is changed on
the server, and the job is not affected. Credentials in `FHIR_SERVER_URL`
(`user:password@`) are sent as HTTP Basic auth and never shown. Nothing is pushed when a
job ends. Sending to an outside health record system is not supported.

On the web page, a finished job's results have a **Push to FHIR server** button while
`HAPI_FHIR_ENABLED` and `FHIR_EXPORT_ENABLED` are both on. It makes the same request: it
sends that job's bundle to this installation's FHIR server (`FHIR_SERVER_URL`, the bundled
HAPI server), then shows what the server did, per resource type, or why nothing was
pushed: the server could not be reached, or it did not accept the bundle. Pressing it
again replaces what the job put on that server before: its resources are updated in
place, and those the bundle no longer has are deleted. The page offers no other server.

## Using the results

The reports are computed from the data you uploaded, by the tools named in them. They are
not a clinical test. A result that matters for a treatment decision should be confirmed by
an accredited laboratory and interpreted by a clinician.
