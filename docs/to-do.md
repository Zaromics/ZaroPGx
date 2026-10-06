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
- Improve progress calculation by normalizing step/substep points to 100%
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
- Still unswept: the PyPGx and zarohla working directories under `data/temp/<random
  uuid>/`, which no job id names, and the PyPGx outputs the sidecar writes into the
  patient's report root (`pypgx_<run>/`, `<run>_pypgx_results.json`), one set per job,
  outside every job directory; the report reads the copy in the job directory.

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

- Unified report generation combining PharmCAT clinical recommendations with PyPGx gene coverage
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
  job or patient UUID.

## UI/UX

- Responsive glyphs: wrapping on small screens; grey-out non-applicable steps; size/flex adjustments
- Add preprocessing glyph (e.g., Liftover) where applicable & mtDNA glyph
- Interactive report's drug-gene network graph needs a design, not a patch. Measured on
  a 30x NA12878 report (Oct 2026): it is laid out at page load while its tab is hidden,
  so the SVG is created 0 px wide; with that fixed, the unbounded force layout of 217
  gene and drug nodes leaves 213 of them off-canvas, and clamping them in only piles
  them on the borders of a card that clips at 300 px. Decide what it should show at
  whole-genome scale (actionable drugs only? genes grouped?) before touching it.
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
  Cheapest useful order, when we get to it: (1) assert report URLs return 200 in
  the existing compose E2E job — catches the worst class, near-zero cost;
  (2) Playwright smoke over upload → progress → report in both themes, with a
  contrast assertion on the popups; (3) only then consider visual snapshots.

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

- Ensure self-hosted deployments never transmit genomic data externally
- Add cookie/consent footer for public deployments with per-user access gating (configurable via `.env`)
- Add Privacy Policy and legal page

## Docker & CI/CD

- [DONE] Clean compose stack; prefer `compose.yml` naming and remove legacy `docker-compose.yml` if redundant
- Implement CI/CD github action to dockerhub image build
- Clean up deprecated flags

## Documentation

- Achieve complete docs curation
- Provide example `.env` guidance; clarify build/run expectations for local Docker

## Engineering

- Modularize large Python modules into smaller, focused files to improve readability and maintainability

## Open Questions

- Where should indexing responsibility live (always regenerate vs recognize existing)?
- How to unify pipeline progress across heterogeneous inputs (FASTQ/BAM/VCF)?
- Which schema to implement, ultimately?
- Visualizations: what would be useful?
- Should we integrate ClinPGx datasets directly for annotations, instead of (or alongside) a lexicon layer?