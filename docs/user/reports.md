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
quality 0 (an alignment that was not ALT-aware), the CYP2D6 and UGT2B loci when their reads
were aligned again (or could not be), genes called without copy number (below), a PharmCAT
version nobody has verified. Read these first.

**Executive Summary.** The 25 genes PharmCAT reports on, on one page, the same genes in the
same order in every report. Each row has the diplotype, the phenotype and, where the
guideline uses one, the activity score. A reference call is shown as PharmCAT labels it
(CFTR's as "Reference/Reference"; the notes give the allele's name). A gene the data did not call is a grey "Not called"
row. A ⚠ beside a gene marks a call that may be wrong, and the notes say why: most of its
reads had mapping quality 0, or some of its positions were read as the reference with
nothing to show it. The page is a right-hand page, and its notes are on the page behind
it, so a double-sided print holds both on one sheet.

**Genetic Results.** Every gene the run called: up to 91, PharmCAT's 25 and the other 66
from PyPGx and OptiType. Columns: diplotype, phenotype, activity score, implications, and
**Call**, the tool that made the call: C PharmCAT, P PyPGx, O OptiType, M mtDNA-Server 2,
X an outside call whose tool was not recorded, – no call, ? not recorded. The guideline
behind each recommendation (CPIC, DPWG or FDA) heads it under Drug Recommendations. The table
has no guideline column: PharmCAT 3.1 and later record no guideline source per gene.

Genes examined without a call are listed after the table, with the reason when one is known:
for a PyPGx gene, no reads over it, or at any of its positions, in the alignment or gVCF, or
no genotype in a VCF. For a gene PyPGx can find with both copies deleted (CYP2A6, CYP2D6,
GSTM1, GSTT1, SULT1A1, UGT2B17), no reads at its positions may be that deletion, and the
reason says so. A PyPGx call made from only some of its positions says beside it how many
of the rest were read as the reference, and why: no reads there, or no genotype in the
file.

**Copy number not assessed.** Some alleles are structural variants: a whole-gene deletion, a
duplication, or a hybrid with a neighbouring gene, such as *CYP2D6* \*5, \*1x2, \*36 and
\*68, *GSTM1* and *GSTT1* \*0 and *UGT2B17* \*2. PyPGx calls them from read depth, which
ZaroPGx measures only from a GRCh38 whole-genome alignment ({doc}`file-formats`). For any
other input, PyPGx calls the 13 genes it defines such alleles for from small variants
alone, and the call cannot show them: a *CYP2D6* \*1/\*5 (an intermediate metabolizer) or
\*1/\*1x2 (ultrarapid) reads \*1/\*1, and *GSTM1* \*A/\*A does not say how many copies of
the gene the sample has, if any. Each such call of PyPGx's (Call P) is marked "Copy number
not assessed", with what it cannot show and why: the input was a VCF, BCF or gVCF; the
alignment's median depth was too low for a whole genome; it is not on GRCh38; PyPGx could
not measure the depth; or, where the run did not record the reason (a report rebuilt
later), only that no read depth reached PyPGx. One entry under Alerts and Warnings names
the genes, and such a gene's row in the Executive Summary points to its note. A
whole-genome alignment's report has none of these.

PharmCAT's own calls get the same note. PharmCAT calls *CYP2B6*, *CYP2C19*, *CYP4F2* and
*SLCO1B1* itself, from small variants, on every input, a whole genome included, unless
`PYPGX_PHARMCAT_PREFERENCE` gives them to PyPGx (`pypgx`, or `auto` for an alignment;
{doc}`configuration`). Compose's default, `pharmcat`, does not. PharmCAT 3.4 names the
structural alleles it cannot detect from a VCF: the *CYP2B7*-*CYP2B6* hybrids *CYP2B6*
\*29 and \*30, and the deletions *CYP2C19* \*36 and \*37, *CYP4F2* \*16 and *SLCO1B1*
\*48 and \*49. It says so in its own HTML report's disclaimers, not in the report.json
this report is built from. Each such call of PharmCAT's (Call C) is marked "Copy number not
assessed (PharmCAT's call)", with the alleles it would miss; the Executive Summary points
to the note, and one entry under Alerts and Warnings names the genes. These notes appear
on a whole genome's report too. PyPGx measures copy number for *CYP2B6* (not \*30) and
*CYP4F2* from a whole-genome alignment when it is set to call them; then the call is
PyPGx's, and noted only if its copy number was not assessed. No tool here measures it for
*CYP2C19* or *SLCO1B1*. *G6PD* gets no note: PharmCAT lists no structural *G6PD* allele.
An outside call PyPGx did not make, such as one from `OUTSIDECALLSOVERRIDE`'s file, gets
no note either.

**Mitochondrial DNA**, when that step ran: the haplogroup and its quality, and the MT-RNR1
call with its phenotype (aminoglycoside-induced hearing loss), or why it was not called.

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

Badge colours show what the phenotype says about the gene's function, or the risk
screened, against what is typical. They are not CPIC's EHR priority, which ranks the need
to act and differs by gene: a CYP3A5 normal metabolizer needs more tacrolimus than the
standard dose, set on the more common poor metabolizers, yet its badge is green. Whether
to act is what the drug recommendations say.

- green: normal metabolism or function, G6PD Normal, an HLA risk allele negative, normal
  risk of aminoglycoside-induced hearing loss;
- amber: intermediate metabolism, decreased function, G6PD Variable;
- red: poor metabolism or function, G6PD Deficient, an HLA risk allele positive, Malignant
  Hyperthermia Susceptibility, increased risk of aminoglycoside-induced hearing loss;
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
phenotype, each drug's implication, citing its genes' genotypes and phenotypes, and its
recommendations, on the profiles of the HL7 Genomics
Reporting IG 3.0.0. A phenotype is a therapeutic implication of its genotype, coded from the
IG's LOINC answers where one says the same thing (Poor metabolizer, Decreased function, High
risk for an HLA risk allele found, ...) and sent as text where none does (Likely Wild Type,
G6PD's terms, hedged and uncertain calls); {doc}`../samples/README` has the table. High
and Low risk (HLA risk alleles, MT-RNR1, malignant hyperthermia) follow CPIC's EHR priority
for the result, since the IG has no term for the result itself; such a phenotype carries a
note saying so, and the mapping is provisional. A gene the run did not call, including
one no tool reported (HLA from a VCF), has the genotype "Unknown", since the IG's
genotype profile requires a value. The bundle validates against those
profiles with no errors (HAPI FHIR's
`$validate`, October 2026); {doc}`../samples/README` lists the warnings that remain and why.
Bundles written before then do not validate, and their XML is not readable as FHIR. The
`/fhir` endpoints ({doc}`../developer/api-reference`) also generate, preview and save a
bundle.

Writing the files sends nothing anywhere. To put a job's results on a FHIR server, ask for
it: `POST /fhir/push/workflow/{job_id}` sends the job's bundle to `FHIR_SERVER_URL`, in
Compose the bundled HAPI FHIR server (`127.0.0.1:8090/fhir` on the host). Only a completed
job is pushed (HTTP 409 for one that failed, was cancelled or is still running), and only
one with a bundle: the bundle is written with the custom report, so a job run with that
report turned off has none (HTTP 404). It goes as one transaction. The Patient is created
once per ZaroPGx patient and reused by later pushes; the report and its resources are
created the first time and updated in place when the job is pushed again, not copied.
Every resource pushed for a job, the Patient aside, is tagged
`urn:zaropgx:job-id|<job_id>`, and a push deletes, in the same transaction, the job's
resources its bundle no longer has: the recommendations a regenerated report dropped, or a
whole older bundle written before October 2026. The answer says how many resources the
server created, how many it already had and how many it deleted, and where the report is
(`DiagnosticReport/<id>`). If the server is down or refuses the bundle, the request fails
with the reason (HTTP 502) and nothing is changed on the server. If the bundle was sent
and no answer came back (the server took more than five minutes, or the connection
dropped), the request fails the same way, but the server may have stored the bundle, or
may still be storing it; pushing again once it has finished is safe, since a push updates
the job's resources in place. The 502's `X-FHIR-Push-Outcome` header says which happened:
`unreachable`, `refused` or `unknown`. The job is not affected either way. Credentials in
`FHIR_SERVER_URL` (`user:password@`) are sent as HTTP Basic auth and never shown. Nothing
is pushed when a job ends. Sending to an outside health record system is not supported.

On the web page, a finished job's results have a **Push to FHIR server** button while
`HAPI_FHIR_ENABLED` and `FHIR_EXPORT_ENABLED` are both on. It makes the same request: it
sends that job's bundle to this installation's FHIR server (`FHIR_SERVER_URL`, the bundled
HAPI server), then shows what the server did, per resource type, or why the push failed:
the server could not be reached or did not accept the bundle, and nothing was pushed; or
it was sent the bundle and gave no answer, and may have stored it. Pressing it again
replaces what the job put on that server before: its resources are updated in place, and
those the bundle no longer has are deleted. The page offers no other server. It names the
server as this installation's FHIR server, and the report's and Patient's references
(`DiagnosticReport/<id>`) are relative to its base: `FHIR_SERVER_URL` is the address
ZaroPGx reaches it at, in Compose a container name the browser cannot open, while from the
host the bundled server is at `127.0.0.1:8090/fhir`.

## Using the results

The reports are computed from the data you uploaded, by the tools named in them. They are
not a clinical test. A result that matters for a treatment decision should be confirmed by
an accredited laboratory and interpreted by a clinician.
