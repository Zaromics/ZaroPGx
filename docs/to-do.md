---
title: Project To‑Do & Roadmap
curation: full
---

# Project To‑Do & Roadmap

## Input Format Support Priorities

- **Priority 0   (Supported)**: VCF, GRCh38, NGS-derived
- **Priority 1   (Supported)**: VCF, GRCh37, NGS-derived — lifted over to GRCh38
  automatically before analysis (GATK Picard `LiftoverVcf` in gatk-api, UCSC hg19→hg38
  chain). Unliftable variants are dropped and counted; an implausibly high reject rate
  fails the run instead of producing a near-empty result.
- **Priority 1.5 (Development)**: BAM
- **Priority 2   (Development)**: CRAM, SAM, BCF, gVCF (NGS-derived). A GRCh38 GATK gVCF
  is genotyped into a plain VCF by gatk-api's `/gvcf-to-vcf` (two `GenotypeGVCFs` passes,
  the first `--include-non-variant-sites` over `pharmcat_positions.vcf`) before analysis.
- **Not accepted**: gVCFs whose reference blocks are `<*>` rather than GATK's `<NON_REF>`
  (DeepVariant, bcftools, some Illumina) — `GenotypeGVCFs` stops on them — and GRCh37/hg19
  gVCFs, because `pharmcat_positions.vcf` exists in GRCh38 coordinates only.
- **FASTQ** (in testing): short-read, single- or paired-end, up to 20 GB. Aligned to GRCh38
  with the bwa-mem GATK already bundles, then analysed as a BAM. Refused: over 20 GB, long
  reads (ONT, PacBio), or a platform that cannot be read from the read names.
- **Not accepted**: 23andMe, AncestryDNA. Detected by name and refused. A decision, not
  pending work: 23andMe v5 carries 229 of PharmCAT's 1,226 positions and 25 of the 157
  that define *CYP2D6*, AncestryDNA v2 380 and 14, no chip can show a duplication or
  deletion, and PyPGx reads the gaps as homozygous reference — so the report would be
  confidently wrong rather than incomplete. See `docs/user/file-formats.md`.
- **Not accepted**: T2T-CHM13, in any format. Detected from the file's own contig lengths
  or `##reference=` line and refused. Building a liftover lane for it is a separate
  decision, not a scheduled one: the T2T chains exclude GRCh38's ALT contigs (GSTT1 lives
  on one), and CYP2D6's CHM13 representation is uncharacterised.
- **Priority 3   (Research)**: Other sequencing/genotyping formats
- **Priority 4   (Research)**: BED, TXT formats

## Pipeline Function

- [DONE Aug 2026] Real coordinate-conversion liftover for GRCh37 input: Picard
  `LiftoverVcf` (bundled in GATK, `/liftover-vcf` on gatk-api) with UCSC's
  hg19ToHg38.over.chain.gz, contig-prefix normalisation in front of it, a reject VCF with
  per-record reasons, and a reject-rate guard. Plain `bcftools annotate --rename-chrs` was
  tried and deleted earlier (Aug 2026) because it only renames contigs, it does not
  convert coordinates. Both report lanes state the per-run counts ("N variants lifted,
  N dropped as unliftable"), read from the liftover step's output_data.
- Clarify workflow vs job IDs; define single source for workflow definition and per-run job state
- Represent workflows as finite state matrix, each unique and deterministic workflow should have an assigned ID which can be quickly spot checked 
- Nextflow orchestration
  - Dynamic resource allocation (CPU/memory by attempt, file type+size, etc.)
  - Track active tool/stage; reflect in UI icons and progress
- [DONE Oct 2026, fix/progress-bar-weighted-steps] Progress is a weighted sum of the
  steps a job runs, each weighted by its measured duration and paced by how long it has
  been running, so it reaches 100% across any input type; it used to freeze for minutes
  when steps ran in parallel.
- Accept uploads by URL (streamed) and multi-file selects (main + index) with proper pairing
- Recognize and/or regenerate index files as needed; map unaligned to appropriate reference: currently GRCh38.p14
- Consider preprocessing complementing PyPGx-led VCF generation (evaluate necessity)
- [DONE] Add mtdna-server-2: mutserve, haplogrep3 and haplocheck in the `mtdna` sidecar,
  supplying the MT-RNR1 outside call and the report's mtDNA section.
- Finish wiring in ZaroHLA
- Improve analysis, make better use of samtools and bcftools
- [DONE Oct 2026, fix/discard-uploads-when-job-ends] A job's upload is deleted when the
  job completes, fails or is cancelled (`KEEP_UPLOADS=true` keeps it); it used to stay
  for good, 41 GB per 30x genome. The mtDNA sidecar's `data/temp/mtdna/<job_id>/`, left
  behind by a worker killed mid-request, is now in the completion cleanup.
- [DONE Oct 2026, fix/sidecar-work-dirs-per-job] Sidecars work in
  `data/temp/<service>/<job_id>/`, removed when the job ends however it ends; PyPGx's
  /genotype no longer copies its pipelines and a summary JSON into the patient's report
  root.
- [DONE Oct 2026, fix/pypgx-full-record] PyPGx's per-gene evidence reaches the report.
  The dormant merge (it read pipeline folders that never reach the job directory, with
  column names results.zip does not have) is deleted; the wrapper now reads PyPGx's
  whole record (phenotype where PyPGx has a rule, copy number, candidate alleles,
  alternative phasings, supporting variants with allele fractions) and keeps the
  copy-number and allele-fraction plots. PyPGx does not fill gaps in PharmCAT's rows:
  PharmCAT stays the source of the phenotype for every gene it covers, and PyPGx's
  calls reach it as diplotype-only outside calls.

## Calling & Tools

### PharmCAT
  - Implement translation layer (lexicon) to translate outside calls to recognized nomenclature
  - Implement optional and intelligent switch to toggle assume reference when missing
### PyPGx
  - Batch execution (done) and advanced parallelization controls (CPU/RAM/storage)
  - BAM-to-VCF preprocessing check
  - Evaluate imputation options; expose via advanced settings
### HLA Typing
  - Use ZaroHLA (OptiType) for HLA-A/B/C when FASTQ; confirm BAM pathway
  - Align to GRCh38 as part of HLA path
### Ancillary and Future tools
  - Now included in Zaromics suite

## Reporting

- [DONE Oct 2026] Unified report: PharmCAT's recommendations with PyPGx's calls for the
  genes PharmCAT does not call ("P" in the Call column), and PyPGx's per-gene evidence
  (fix/pypgx-full-record).
- Add demographics mini-section: mitochondrial lineage/haplogroup and variant rarity context
- Standardize folder naming of generated reports (timestamp-based) and place logs under `data/logs/`
- [DONE] Display workflow ID specific Kroki/Mermaid workflow diagram in both HTML and PDF outputs
- Add clear wording: sample vs patient terminology; avoid assumptions of medical context
- Abstract report theme so cross-pipeline outputs remain stylistically consistent
- Custom reports: add a QR code containing the raw data
- [DONE Oct 2026, fix/report-recommendation-call] DPYD on unphased input. PharmCAT
  lists each allele it found as its own "Indeterminate" source call and doses from the
  one recommendation call it builds from them (30x NA12878: `c.1601G>A (*4)/c.1627A>G
  (*5)`, Normal Metabolizer, 2.0). The gene table, Executive Summary and interactive
  report showed the per-allele entries; where the source is not one diplotype they now
  show the recommendation call, as the dosing guidance does.
- [DONE Oct 2026, fix/report-sample-identifier] The sample identifier entered at upload
  (else the file header's) is the reports' "Sample ID"; every report used to print the
  job or patient UUID. Without one it says "Not provided", and the job id is the
  separate Report ID line in every report.

- [DONE Oct 2026] Unphased input: a call PharmCAT could not narrow to one diplotype is shown
  as every candidate, in every view, not the first as if certain
  (fix/report-unresolved-calls); where the guidance differs by candidate, each
  recommendation says which it applies to ("Applies if: SLCO1B1 Poor Function ...",
  fix/recommendations-per-candidate). Genes not called are named, with the reason.
- [DONE Oct 2026] Recommendation headings name their guideline (CPIC / DPWG / FDA) in
  reports from uploads; they read "General" (fix/report-guideline-source-names).
- [DONE Oct 2026] The network graph draws only genes that set the guidance, never an
  uncalled gene, and colours an unresolved call grey (fix/network-hubs-uncalled-unresolved).
- [DONE Oct 2026] The HTML report no longer ends with a debug dump of its template
  context (fix/report-no-debug-dump).
- [DONE Oct 2026] PDF layout: page 1 holds the whole disclaimer; the workflow diagram
  keeps its proportions; the genomic file header prints in full (it ran off the page) as
  an appendix after the citations; the licence never opens a page alone. Major sections
  keep their own pages (decided). The Executive Summary is one page: it covers PharmCAT's
  panel, and the PDF tightens it step by step when it would run over; a section spilling
  a few lines onto a page (Alerts, Drugs, Platform and Citations) is tightened to end a
  page sooner (app/reports/pdf_fit.py; fix/report-exec-summary-one-page,
  fix/pdf-sections-fit-pages). F2 and F5 are read as called
  (fix/report-unannotated-gene-calls); score badges are legible.
- Open: should "Decreased Function" get a warning colour (it is neutral grey)?

## UI/UX

- Responsive glyphs: wrapping on small screens; grey-out non-applicable steps; size/flex adjustments
- Add preprocessing glyph (e.g., Liftover) where applicable & mtDNA glyph
- Unify/clean redundant text
- **Front-end test harness — nothing renders the page today.** What exists is
  Node-executed *logic* tests: `tests/test_ui_workflow_flag_reads.py` and
  `tests/test_upload_story_coherence.py` pull the inline `<script>` out of
  `index.html`, run it against real API JSON, and assert on the HTML string (CI
  installs Node 22 so they cannot silently skip). Everything else is source-text
  assertions. There is no `package.json`, no jsdom, and no browser driver, so
  nothing computes a style, clicks anything, or looks at the page.
  That gap produced three bugs in one sitting on 2026-08-29, each invisible to
  the 1700-test suite:
  - PharmCAT popup text unreadable in dark mode (a white card inheriting the
    dark theme's body colour) — needs *computed styles*, not markup.
  - Progress bar frozen for the whole liftover step — needed a live run watched
    end to end.
  - Every completed job deleting its own reports — needed a post-run check that
    the report links actually resolve.
  Cheapest useful order: (1) [DONE Oct 2026] every compose E2E lane fetches each
  `/reports/` link its completed job lists and requires a 200 with a body
  (`tests/e2e/report_links.py`); (2) Playwright smoke over upload → progress →
  report in both themes, with a contrast assertion on the popups; (3) only then
  consider visual snapshots.

## Data & Database

- PostgreSQL 18: add extensions; implement schemas
- Adopt JSONB where appropriate; ensure escaping for special characters (done)
- Begin persisting normalized results; build lexicon layer translating between caller spelling
- Consolidating reference and sample material (FASTA/CPIC dumps) into a single `references/` area

## FHIR & Exporting

- HAPI FHIR server integration; adjust `ddl-auto` appropriately for prod vs dev
- Implement export per HL7 Genomics Reporting IG v3 via FHIr r4
- Explore Fasten as a bridge for import/export to HAPI FHIR

## Security & Privacy

- Ensure self-hosted deployments never transmit genomic data externally. Audited
  Oct 2026: every service URL the app and sidecars fall back to names a compose service
  (pinned by `tests/test_no_external_service_defaults.py`), and the only other outbound
  traffic is reference and bundle downloads. htslib's fallback of fetching a CRAM's
  reference sequences from EBI by MD5 is closed (Oct 2026; the mtdna image's htslib 1.19
  did it by default): every alignment reader sets a local-only `REF_PATH`
  (`/reference/ref_cache/...`), pinned by the same test.
- Add cookie/consent footer for public deployments with per-user access gating (configurable via `.env`)
- Add Privacy Policy and legal page

## Docker & CI/CD

- Publish new images before pointing new installs at them: `bootstrap.sh` pins
  `ZAROPGX_TAG=0.3.2`, and that release's genome-downloader image fails two stages on a
  fresh install, both fixed on main since (fix/downloader-reference-staging): grch37 is
  fetched over `ftp://`, which `requests` cannot do (every host), and the PharmCAT
  reference tarball's chown fails under rootless or userns-remapped Docker. Found by
  running `bootstrap.sh` end to end on a clean daemon (Oct 2026); the rest of the
  install and a VCF job were fine.
- [DONE Oct 2026, fix/sidecars-read-shared-inputs] Services read an alignment on the
  shared /data volume in place instead of each storing an upload copy: on 30x NA12878
  the 41.2 GB copies in `data/temp/mtdna/` and `data/temp/zarohla/` (about 124 GB at
  once with the upload) and PyPGx's are gone, and the BAM run took 17 minutes against
  35 to 42 in the three runs before it.
- [DONE Oct 2026, fix/sidecars-read-shared-inputs] A CRAM or SAM job no longer keeps
  the BAM it was converted to in its report folder (41 GB beside 25 MB of reports); a
  FASTQ job still publishes its aligned BAM, that lane's product.
- [DONE Oct 2026, fix/downloader-drop-archives] The genome downloader removes each
  archive once its FASTA is indexed (3.7 GB: the three .fa.gz and PharmCAT's tarball),
  on existing installs too.
- [DONE Oct 2026, fix/nextflow-no-docker-socket] nextflow no longer mounts the Docker
  socket: no process declares a container, so nothing used it, and it made the
  container that runs the unauthenticated POST /run root-equivalent on the host. Rootless
  installs need no socket remap.
- [DONE Oct 2026, fix/zarohla-reference-mount] zarohla mounts `/reference` for its
  `REF_PATH`, so a CRAM posted to it directly can be decoded.

- [DONE] Clean compose stack; prefer `compose.yml` naming and remove legacy `docker-compose.yml` if redundant
- Implement CI/CD github action to dockerhub image build
- Clean up deprecated flags

## Documentation

- Achieve complete docs curation
- [DONE] Example `.env` guidance and local Docker build/run: `.env.example`,
  `docs/user/installation.md` (setup and options), `docs/user/configuration.md`
  (every key).

## Engineering

- Modularize large Python modules into smaller, focused files to improve readability and maintainability

## Open Questions

- Where should indexing responsibility live (always regenerate vs recognize existing)?
- How to unify pipeline progress across heterogeneous inputs (FASTQ/BAM/VCF)? (In
  part answered Oct 2026: steps weighted by measured duration per input path.)
- Which schema to implement, ultimately?
- Visualizations: what would be useful?
- Should we integrate ClinPGx datasets directly for annotations, instead of (or alongside) a lexicon layer?