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
- **Priority 2   (Development)**: CRAM, SAM, BCF (NGS-derived)
- **Not accepted**: FASTQ. ZaroPGx ships no aligner, so raw reads — single- or paired-end —
  are refused at upload. Implementing alignment is the only thing that would change this.
- **Priority 3   (Research)**: Other sequencing/genotyping formats
- **Priority 4   (Research)**: BED, gVCF, 23andMe, AncestryDNA, TXT formats
- **Priority 5   (Early Research)**: T2T and other emerging formats

## Pipeline Function

- [DONE Aug 2026] Real coordinate-conversion liftover for GRCh37 input: Picard
  `LiftoverVcf` (bundled in GATK, `/liftover-vcf` on gatk-api) with UCSC's
  hg19ToHg38.over.chain.gz, contig-prefix normalisation in front of it, a reject VCF with
  per-record reasons, and a reject-rate guard. Plain `bcftools annotate --rename-chrs` was
  tried and deleted earlier (Aug 2026) because it only renames contigs, it does not
  convert coordinates. Remaining nicety: surface the per-run reject count in the report
  itself, not only in the job log/step message.
- Clarify workflow vs job IDs; define single source for workflow definition and per-run job state
- Represent workflows as finite state matrix, each unique and deterministic workflow should have an assigned ID which can be quickly spot checked 
- Nextflow orchestration
  - Dynamic resource allocation (CPU/memory by attempt, file type+size, etc.)
  - Track active tool/stage; reflect in UI icons and progress
- Improve progress calculation by normalizing step/substep points to 100%
- Accept uploads by URL (streamed) and multi-file selects (main + index) with proper pairing
- Recognize and/or regenerate index files as needed; map unaligned to appropriate reference: currently GRCh38.p14
- Consider preprocessing complementing PyPGx-led VCF generation (evaluate necessity)
- Add mtdna-server-2
- Finish wiring in ZaroHLA
- Improve analysis, make better use of samtools and bcftools

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
- Display workflow ID specific Kroki/Mermaid workflow diagram in both HTML and PDF outputs
- Add clear wording: sample vs patient terminology; avoid assumptions of medical context
- Abstract report theme so cross-pipeline outputs remain stylistically consistent
- Custom reports: add a QR code containing the raw data

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

- Clean compose stack; prefer `compose.yml` naming and remove legacy `docker-compose.yml` if redundant
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