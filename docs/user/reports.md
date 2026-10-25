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
| `unlisted_variants.json` | The variants in the pharmacogenes that no allele definition lists, each with its consequence, frequency, AlphaMissense score, ClinVar classification and tier |
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
**Call**, the tool the run records for the gene: the one that made the call or, for a gene
without a call, the one that tried. C PharmCAT, P PyPGx, O OptiType, M mtDNA-Server 2, X an
outside call whose tool was not recorded, – no call and no tool on record for the gene
(PharmCAT made none and was given none, or no tool returned a result for it), ? not
recorded. The key above the table lists only the letters the report uses. The guideline behind each recommendation (CPIC, DPWG or FDA) heads it under Drug
Recommendations. The table has no guideline column: PharmCAT 3.1 and later record no
guideline source per gene.

**Implications** says what the phenotype means, as CPIC defines the term (for example
"Decreased transporter function, between normal and poor" for *SLCO1B1* Decreased
Function), and points to the drug recommendations for what follows from it; it names no
dose or drug of its own. An N/A call's is a dash: the note on N/A says what it means, once,
behind the Executive Summary when a gene there is N/A, else under the table. The
interactive report's gene cards and panels say the same in words.

Beside a diplotype the table says, in a few words, what PyPGx's copy number found ("one
copy deleted", "tandem \*68+\*4"; its code is in the interactive report's evidence panel),
and which alleles a different phasing would give ("\*6 if these variants lie on one
chromosome").

Genes examined without a call are listed after the table, each with its Call letter (as
they are in the interactive report's Genes tab, which lists every gene), and with the reason
when one is known: for a PyPGx gene, no reads over it, or at any of its positions, in the
alignment or gVCF, or no genotype in a VCF. For a gene PyPGx can find with both copies
deleted (CYP2A6, CYP2D6, GSTM1, GSTT1, SULT1A1, UGT2B17), no reads at its positions may be
that deletion, and the reason says so. A PyPGx call made from only some of its positions
says beside it how many of the rest were read as the reference, and why: no reads there,
or no genotype in the file.

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

PharmCAT's own calls get the same kind of note. PharmCAT calls *CYP2B6*, *CYP2C19*,
*CYP4F2* and *SLCO1B1* itself, from small variants, on every input, a whole genome
included, unless `PYPGX_PHARMCAT_PREFERENCE` is `pypgx`, which gives them to PyPGx
({doc}`configuration`). PharmCAT 3.4
names the structural alleles it cannot detect from a VCF: the *CYP2B7*-*CYP2B6* hybrids
*CYP2B6* \*29 and \*30, and the deletions *CYP2C19* \*36 and \*37, *CYP4F2* \*16 and
*SLCO1B1* \*48 and \*49. It says so in its own HTML report's disclaimers, not in the
report.json this report is built from. Each such call of PharmCAT's (Call C) is marked
⚠ "Copy number not assessed", with the alleles it would miss. Under the default, `auto`,
PyPGx also runs these genes on an alignment, for their copy number, and the calls stay
PharmCAT's. Where the copy number was measured and found normal (*CYP2B6* and *CYP4F2* by
PyPGx, *CYP2C19* and *SLCO1B1* by ZaroPGx's deletion check, below, from a whole genome)
there is no note: the alert says so once. When PyPGx makes these calls instead (`pypgx`),
it cannot show them all either: it has no copy-number model for *CYP2C19* or *SLCO1B1*, so
without the read depth the deletion check needs they are "Copy number not assessed (no
copy-number model)". Its *CYP4F2* call from a whole genome measures the gene's copy number,
so it can show a whole-gene deletion (PyPGx's \*DEL), though not a partial one. If PyPGx
measured a copy number other than normal for a gene whose call shown is PharmCAT's, the
call may be wrong: its row in the Executive Summary says ⚠ "PyPGx found a structural
allele", and the alert says so first. One such state of PyPGx's is *CYP2B6* \*30, which its
own call leaves Indeterminate (its copy-number state Tandem1): its one example, the 1000
Genomes genome HG01806, has three copies from inside *CYP2B7* to *CYP2B6* between exons 4
and 5, the tandem duplication that joins *CYP2B6* exons 1-4 to *CYP2B7* exons 5-9 beside an
intact *CYP2B6*. The report names it "the \*30 hybrid duplication". PharmCAT keeps its call:
CPIC has no phenotype for a \*30 hybrid beside an intact *CYP2B6*. One entry under Alerts and Warnings names the genes
not assessed ("Structural alleles not assessed") and says once what PyPGx measured; each
gene has a short note behind the Executive Summary. These notes appear on a whole genome's
report too. *G6PD* gets no note: PharmCAT lists no structural *G6PD* allele.
An outside call neither tool made, such as one from `OUTSIDECALLSOVERRIDE`'s file, gets no
note either.

**Deletions in CYP2C19 and SLCO1B1.** PyPGx has no copy-number model for *CYP2C19* or
*SLCO1B1*, so ZaroPGx checks them itself, from the read depth PyPGx measures on a GRCh38
whole-genome alignment, normalised against the same control gene (*VDR*): the median copy
number of each 5 kb across the gene, where a run of three or more stretches under 1.35
copies, with a median of at most 1.2, is a deletion of one copy, and under 0.4 of both (a
deletion under 15 kb is not called). A deletion is named by the exons it takes:
*CYP2C19* \*36 (the whole gene) or \*37 (a partial deletion that includes exon 1), *SLCO1B1*
\*48 (the whole gene; a deletion that size may take part of *SLCO1B3* too) or \*49 (part of
it), all no function in CPIC's tables. With one copy deleted, the other copy's variants read
homozygous: a *CYP2C19* \*1/\*37 sample, an intermediate metabolizer, reads \*1/\*1 from
small variants. So when the check names a deletion under a homozygous call, the call takes
it, \*1/\*37, and goes to PharmCAT as an outside call; the row says ⚠ "Deletion found from
read depth", the note gives the call from small variants alone, and the alert asks for an
independent test before acting on it. A deletion no star allele with a function names (a
*CYP2C19* deletion that keeps exon 1, such as one of exons 2-5) or a heterozygous call over
a deletion leaves PharmCAT's call, marked as one that cannot show it. Found normal, neither
gene gets a note, and the alert says so once. On 1000 Genomes 30x genomes the check calls
*CYP2C19* \*37 in HG00268, the exons 2-5 deletion in HG01771 and *SLCO1B1* \*48 in HG01531
and HG03856 and \*49 in HG03436, and no deletion in 62 GeT-RM reference genomes, both at
their own depth (about 30x) and downsampled to 15x, the lowest depth the check runs at.

**Deletions of DPYD exons.** CPIC's *DPYD* table names alleles by variant and lists no
deletion, so neither PharmCAT nor PyPGx calls one, and a call from small variants cannot
show one. Yet deletions of *DPYD* exons have been found in patients with DPD deficiency
(van Kuilenburg et al., Hum Genet 2009;125:581-590: exon 12 in three patients, exons 14-16 in
one; Saarenheimo et al., Cancer Chemother Pharmacol 2021;87:657-663: exon 4 in four Finnish
patients), and DPD deficiency can make a fluoropyrimidine (5-fluorouracil, capecitabine)
dangerous. So the same check measures *DPYD*, in 1 kb stretches, since these deletions take
an exon or a few and a few kb: a run of four or more under 1.35 copies, with a median of at
most 1.1, is a deletion (one under 4 kb is not called). No allele names it, so PharmCAT's
call and phenotype stand; the row says ⚠ "Deletion found from read depth", and the alert
says which exons, that CPIC assigns the deletion no function and the call cannot show it,
and that DPD activity may be reduced, so to confirm it with an independent test, such as DPD
phenotyping (DPD activity or plasma uracil) or a *DPYD* copy-number assay, before
fluoropyrimidine treatment. Found normal, the alert says so with *CYP2C19* and *SLCO1B1*.
Without read depth (a VCF, BCF or gVCF, or a genome under 15x) the call is marked as one that
cannot show a deletion of *DPYD* exons. GeT-RM's *DPYD* study (Gaedigk et al., J Mol Diagn
2024;26(10):864-875) has one copy of exon 4 deleted in HG00325 and of exon 11 in NA19093: on
their 1000 Genomes genomes the check calls both, and no deletion in the 65 others, at their
own depth (about 30x) and downsampled to 20x and to 15x.

**PharmCAT's notes.** PharmCAT attaches its own messages to calls and drugs, and the report
shows them where PharmCAT's own report does, in PharmCAT's words (a link as its words and
its address): a gene's under Genetic Results ("PharmCAT's notes on these calls"), one line
a gene, called or not, and in the interactive report's gene panel; a drug's at the head of
the drug under Drug Recommendations, each marked with the gene it is about. A drug with no
recommendation for this sample still gets its notes, in a list after the recommendations:
PharmCAT's own report gives such a drug its section too. Among them: an unphased call's
"The assigned genotype is \*1/\*15; however, \*5/\*37 cannot be ruled out without phased
data.", UGT1A1's repeat caveat, NAT2's choice among several calls, DPYD's activity-score
rule, the warfarin notes with the rs12777823 genotype PharmCAT highlights for warfarin, and,
where RYR1 has data but no call, its malignant hyperthermia warning on RYR1 and the
anaesthetics. PharmCAT's footnote that a gene on the negative strand is given on the
positive one is said once, for all such genes. Left out: PharmCAT's outside-call note (the
Call column names the tool); its note that a reference allele means no variant at the
positions its definition uses (the call's name says so); the notes it gives every CYP3A4
call (where its allele definitions come from) and every G6PD call and drug (how some VCF
files write chromosome X), which change nothing about this sample; SLCO1B1's note on what
recommendations rest on if no star allele is called, when one is; and, when Alerts and
Warnings lists the positions PharmCAT had no genotype for, its own notes on those
positions. A report rebuilt from a report.json alone has no such alert, and shows them.
None appears on the Executive Summary.

**Variants Outside the Allele Definitions.** PharmCAT and PyPGx call a gene from the
variants their allele definitions name, and nothing else: a rare variant that stops the
protein, if no definition lists it, leaves the gene reported as normal. Every run also
reads every other variant in the coding exons, and splice sites, of 52 genes: every gene
PharmCAT has allele definitions for (IFNL3 aside, which it types by one variant outside
its exons), and the drug-metabolising enzymes and transporters PyPGx types. PharmCAT's
genes include CFTR, RYR1, CACNA1S, F2 and F5, where a variant can also bear on a disease
(cystic fibrosis, malignant hyperthermia and some myopathies, thrombophilia or a bleeding
disorder); the section says so when it lists one. A variant that passes the quality
checks, is under 1% in every gnomAD genetic ancestry group and may change the protein is
listed here with its consequence on the gene's MANE Select transcript, its frequency and
ClinVar's classification, and in a tier: predicted loss of function (a stop, a frameshift,
a splice site, a lost start, or ClinVar pathogenic for the gene's drug response), possible
loss of function (the same, with less confidence), predicted damaging (AlphaMissense above
0.564), possibly damaging (AlphaMissense 0.152 to 0.564), or effect uncertain (an in-frame
change, a lost stop, a splice-region change, a missense AlphaMissense has no score for).
Each one marks its gene's call, beside it and in the Executive Summary's notes, in its
tier's words. In CYP2D6, CYP2A6, GSTM1 and UGT2B17 the mark adds that the variant may be a
near-identical neighbouring gene's, whose reads can align there. Common variants, those
with no predicted effect and calls that fail the quality checks are only counted;
`unlisted_variants.json` has them all. Nothing changes a call or a phenotype: the effect
is a prediction, and whether the variant is on the same chromosome copy as an allele in
the call is not known. In the FHIR export each marking variant is a Variant observation
with a diagnostic implication that ties it to the gene's genotype, coded "Uncertain
significance", or ClinVar's classification where ClinVar has one for the gene's drug
response. The check reads only the annotation the genome downloader builds once
(`ANNOTATION_ENABLED`, {doc}`configuration`): nothing about the sample leaves the machine.
Its sources are pinned, ClinVar to a dated release, and refreshed with each ZaroPGx
release, so a sample gives the same section on every run of one version. Without the
annotation, this section says the check did not run.

**Mitochondrial DNA**, when that step ran: the haplogroup and its quality, and the MT-RNR1
call with its phenotype (aminoglycoside-induced hearing loss), or why it was not called.

**Drugs.** Every drug with guidance for this sample, marked by the strongest
recommendation: Strong, Moderate or Optional (CPIC's strengths); Guideline available (DPWG
or FDA guidance without a CPIC strength); No recommendation (a guideline that gives no
genotype-based action); Unclassified.

**Drug Recommendations.** For each drug, each source's recommendation: CPIC and DPWG
guidelines, FDA labels and FDA's pharmacogenetic associations, with the strength of
recommendation where the source gives one, and a link
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
note saying so, and the mapping is provisional. A gene the run did not call still has a
genotype, since the IG's genotype profile requires a value, and its value carries a code
of HL7's DataAbsentReason saying why. `not-performed` (text "Not performed"): nothing
tested the gene. No tool reported it and none was to examine it, or the run did not run
the tool that supplies it: no HLA typing (every VCF run, or OptiType turned off: HLA-A,
HLA-B and HLA-C), no mtDNA calling (MT-RNR1), no PyPGx (CYP2D6, which PharmCAT takes from
PyPGx), no PharmCAT. `error` (text "Error", and a note saying why): the tool that tried
the gene failed on it (a PyPGx error, or a PyPGx run the job turned on that ended without
results); a gene PharmCAT examined itself stays `unknown` whatever PyPGx did. `unknown`:
a tool tested the gene and could not call it (text as the tool put it: "Unknown",
"Unknown/Unknown", "Indeterminate"). That includes F2 and F5 after PharmCAT ran: it
examines them in every run but lists them only when it calls them (its unannotated gene
calls), so a VCF without their positions leaves them out of its report. The bundle validates against those profiles with no errors (HAPI
FHIR's `$validate`, October 2026); {doc}`../samples/README` lists the warnings that
remain and why. Bundles written before then do not validate, and their XML is not
readable as FHIR. The `/fhir` endpoints ({doc}`../developer/api-reference`) also
generate, preview and save a bundle. `POST /fhir/save/workflow/{job_id}` and the
endpoints that take a PharmCAT run id build it from PharmCAT's rows in the database:
PharmCAT's genes only, without what the report step knew about the run, so a gene with
no call is `unknown` there. Saving that way overwrites the job's bundle, and a push
then sends it; regenerating the job's report writes the full bundle again.

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
