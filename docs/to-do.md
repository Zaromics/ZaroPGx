---
title: Project To‑Do & Roadmap
curated: true
---

# Project To‑Do & Roadmap

## Input Format Support Priorities

- **Priority 0   (Supported)**: VCF, GRCh38, NGS-derived
- **Priority 1   (Supported)**: VCF, GRCh37, NGS-derived — lifted over to GRCh38
  automatically before analysis (GATK Picard `LiftoverVcf` in gatk-api, UCSC hg19→hg38
  chain). Unliftable variants are dropped and counted; an implausibly high reject rate
  fails the run instead of producing a near-empty result.
- **Priority 2   (Testing)**: BAM, CRAM, SAM, BCF, gVCF (NGS-derived; GRCh38, except that a
  GRCh37 BCF is converted and then lifted over like a VCF). A GRCh38 GATK gVCF
  is genotyped into a plain VCF by gatk-api's `/gvcf-to-vcf` (two `GenotypeGVCFs` passes,
  the first `--include-non-variant-sites` over `pharmcat_positions.vcf`) before analysis.
- **Not accepted**: gVCFs whose reference blocks are `<*>` rather than GATK's `<NON_REF>`
  (DeepVariant, bcftools, some Illumina) — `GenotypeGVCFs` stops on them — and GRCh37/hg19
  gVCFs, because `pharmcat_positions.vcf` exists in GRCh38 coordinates only.
- **FASTQ** (in testing): short-read, single- or paired-end, up to 20 GB. Aligned to GRCh38
  with the bwa-mem GATK already bundles, then analysed as a BAM. Refused: over the cap
  (`FASTQ_MAX_UPLOAD_BYTES`, 20 GB by default), more than the free disk can hold (about 9x
  the FASTQ, checked before alignment starts), long reads (ONT, PacBio), or a platform that
  cannot be read from the read names. The cap is about scratch disk and run time. On 2% of
  NA12878's 30x reads (1.08 GB, 7.6 M pairs) alignment took 11 minutes and peaked at 6.1x
  the FASTQ's size in disk (Oct 2026), so a whole genome would take hours. Open: measure
  disk and MarkDuplicatesSpark's memory on one whole-genome FASTQ.
- **Not accepted yet — support in development**: 23andMe, AncestryDNA. Detected by name
  and refused for now. Converting the file is the easy part; support has to solve what
  comes after it: 23andMe v5 carries 229 of PharmCAT's 1,226 positions and 25 of the 157
  that define *CYP2D6*, AncestryDNA v2 380 and 14, no chip can show a duplication or
  deletion, and PyPGx reads the gaps as homozygous reference — so today's pipeline would
  report confidently wrong calls rather than incomplete ones. See
  `docs/user/file-formats.md`.
- **Not accepted yet — support in development**: T2T-CHM13, in any format. Detected from
  the file's own contig lengths or `##reference=` line and refused for now. A liftover
  lane for it has to solve two problems: the T2T chains exclude GRCh38's ALT contigs
  (GSTT1 lives on one), and CYP2D6's CHM13 representation is uncharacterised.
- **Not accepted — long reads**: ONT and PacBio, in FASTQ (detected from the read
  names) and refused. Accepting them is a lane, not a flag: bwa-mem has no long-read
  preset, so alignment needs minimap2, and OptiType types HLA from short reads only.
  PyPGx has a long-read mode with no structural-variant detection; pangu
  (BSD-3-Clause-Clear) calls CYP2D6 from PacBio HiFi. See
  `scripts/getrm/caller-augmentation.md`.
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
  - Dynamic resource allocation (CPU/memory by attempt, file type+size): moot as framed.
    Every Nextflow process is a curl call to a service, so the services' compose limits
    hold the resources; per-process cpus/memory were removed (968ec89, Oct 2026) and
    `tests/test_nextflow_config_resources.py` keeps them out.
  - [DONE, by the app rather than Nextflow] Track the active step in the page: each
    service reports its step (`app/utils/job_client.py`), the page lights the running
    stage's glyph, and the bar is weighted by step (below).
- [DONE Oct 2026, fix/progress-bar-weighted-steps] Progress is a weighted sum of the
  steps a job runs, each weighted by its measured duration and paced by how long it has
  been running, so it reaches 100% across any input type; it used to freeze for minutes
  when steps ran in parallel.
- Accept uploads by URL (streamed). The upload takes files only.
- Use the index uploaded with its data file. Multi-file selects work: an index chosen with
  its file is paired by name and named on the page, FASTQ R1/R2 pairs are paired, but the
  index itself is not used (`app/api/utils/file_processor.py`).
- [DONE Oct 2026] Index files: a current index beside an alignment is reused, otherwise the
  service indexes it (`app/utils/shared_input.py`). Unaligned reads (FASTQ) are aligned to
  GRCh38's main contigs plus chr22_KI270879v1_alt, cut from the hg38 FASTA the genome
  downloader stages (UCSC's hg38, saved as `reference/hg38/Homo_sapiens_assembly38.fasta`;
  `scripts/build-align-index.sh`).
- [DONE Sep-Oct 2026] Preprocessing beside PyPGx's VCF: for aligned reads and gVCFs, ZaroPGx
  genotypes every PharmCAT position itself (1f4d8a2); reads on GRCh38's ALT contigs are
  aligned again to the main contigs (below).
- [DONE] Add mtdna-server-2: mutserve, haplogrep3 and haplocheck in the `mtdna` sidecar,
  supplying the MT-RNR1 outside call and the report's mtDNA section.
- [DONE Oct 2026] ZaroHLA (OptiType) is wired in: the `hla_typing` step types HLA-A, -B and
  -C from every alignment input, and HLA-A and -B reach PharmCAT as outside calls.
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

- [DONE Oct 2026, feat/realign-alt-loci] Reads on GRCh38's ALT contigs. An alignment
  made against a GRCh38 that has them (NCBI's full GRCh38, for one) without ALT-awareness
  puts many CYP2D6 and UGT2B17/UGT2B15 reads there as their primary alignment (on
  NA12878, 529 of CYP2D6's 1,570 stayed on chr22), where PyPGx never reads. ALT-aware,
  BWA keeps a read with a hit on the main chromosomes there and writes its ALT hit as a
  supplementary copy; only a read with no such hit is primary on the ALT (1 in
  NA12878's BAM).
  On NA12878 aligned so, CYP2D6 came out *1/*1 (truth *3/*68+*4) with no flag, at 48%
  MAPQ 0. Those loci's reads are now aligned again to the main contigs (gatk-api
  /realign-reads, BwaSpark) before PyPGx and PharmCAT's positions read them; the 66
  PyPGx calls then match the published ALT-aware BAM's. Needs the alignment index;
  without it the genes are flagged with the reason.

## Calling & Tools

### PharmCAT
  - Implement translation layer (lexicon) to translate outside calls to recognized
    nomenclature. In part: `app/utils/allele_translate.py` rewrites the synonym genes
    (ABCG2, IFNL3, VKORC1) and CYP2D6's tandem spelling; other genes pass through.
  - [DONE] Assume reference when missing: the upload page's two options (defaults from
    `.env`) for VCF and BCF; forced off for alignments and gVCFs, where ZaroPGx genotypes
    every PharmCAT position itself.
  - Genes PharmCAT calls but cannot name. PharmCAT 3.4 has no NUDT15 *2, so every *2
    carrier came back *3 — *2/*2 as *3/*3, *2/*5 as *3/*5: the phenotype was right and the
    diplotype was not, on every sample carrying it. PyPGx names *2, and had NUDT15 right
    10 times of 11 on the GeT-RM genomes against PharmCAT's 8, including every case
    PharmCAT had right. Its call is handed over as an outside call, listed in
    `config/genes.json`'s `pypgx_preferred_over_pharmcat` (`fix/nudt15-prefer-pypgx`).
  - CYP4F2 is the other gene PyPGx scored better on, 18 of 20 against 17, with no case
    PharmCAT alone had right. One call is thin; held until more samples than GeT-RM's say
    the same. `scripts/getrm/caller-augmentation.md` has the per-gene table.
  - [DONE Oct 2026] Say on the call when the two callers disagree about a gene both of
    them call (`app/reports/caller_agreement.py`). PharmCAT's was printed and PyPGx's
    dropped, with nothing marking the difference: in NA19908 PharmCAT printed CYP4F2
    `*1/*4` while PyPGx called `*2/*3`, which is what GeT-RM says. A difference in the
    alleles is now a note beside the call, a note in the Executive Summary and one
    alert; which call is printed does not change. Only star-allele calls are compared,
    and NAT2 is left out because its two callers use different names — 14 notes over the
    61 GeT-RM genomes, never more than one in a report.
### PyPGx
  - Batch execution (done) and advanced parallelization controls (CPU/RAM/storage)
  - BAM-to-VCF preprocessing check
  - Evaluate imputation options; expose via advanced settings
  - CYP2D6 copy-number *arrangement*, not copy number. PyPGx calls `*5` well — right in
    six of the seven GeT-RM genomes carrying one, and right on tandems like
    `*1/*36x2 + *10` — and both of its structural misses have the total copy number right
    with the copies assigned wrongly: `*5/*36x2+*10x2` as `*36 + *10/*36 + *10` (CNV state
    `Tandem2B`, four copies split two and two rather than none and four) and `*10/*36x2`
    as `*10/*36 + *10` (`Tandem2A`). No depth check reaches either; they are the limits of
    the CNV-state vocabulary, and the published CYP2D6 leaders resolve them from CYP2D7
    breakpoints or long reads. See `scripts/getrm/caller-augmentation.md`.
  - Read-backed phasing for CYP2D6, which the reads support; not for CYP4F2, which they
    do not. Phasing is Beagle's today: statistical, from a reference panel, with no read
    evidence behind it, and two wrong calls are phasing errors. Measured on the cached
    cuts (`scripts/getrm/phase-blocks.py`): CYP4F2's two `*4` variants are 17,957 bp
    apart, and although 55 heterozygous sites lie between them the 2x150 reads leave
    gaps no fragment bridges — 6 in HG00436, the widest 3,485 bp, 8 in NA19908 — so the
    two fall in different read-backed phase blocks in both, and no short-read phaser can
    decide `*4` against `*2/*3`. CYP2D6 is the opposite: in HG00589, called `*2/*21`
    where GeT-RM says `*1/*21`, all three deciding positions are heterozygous and in one
    block, and the pair that settles it (2850C>T and `*21`'s insertion, 270 bp apart) is
    carried by 7 fragments that all put the two **together**, none apart — which makes
    the call GeT-RM's `*1/*21`. Adjudicate, do not re-phase, and **check every call, not
    the ones PyPGx flags**: keying the check to `alternative_phase` finds nothing (the
    reads decide 2 of its 34 cases and confirm both), and HG00589 carries
    `alternative_phase: null` — Beagle was confident and wrong. Measured with
    `scripts/getrm/arrangement.py` over the 60 genomes with a cut: 713 pairs within
    1 kb, 411 the reads decide, 409 agreeing with the call. The yield is one corrected CYP2D6 call plus a
    CYP2F1 call with no dosing guideline, so the case is cheapness and safety, not
    volume. It must read indels (HG00589's `*21` is an insertion) and must not treat two
    variants at one position as a phasing question (UGT1A1 `*28` and `*36` are competing
    alleles of one repeat, and conflating them invents contradictions).
    `scripts/getrm/phasing.md` has the measurements.
    Only 51 of the 179 star alleles defined by two or more core variants have their
    whole span inside one 2x150 fragment, so reads reach about 28% of the alleles where
    phase decides anything (median span 2,422 bp, 90th percentile 24,242 bp), and
    chaining through intervening heterozygous sites does not rescue the rest. What they
    do reach is concentrated in PyPGx's genes — CYP2D6 15 of 49, CYP2A6 9 of 12,
    UGT1A1 2 of 2 — not PharmCAT's, whose one plausibly reachable unresolved call
    (CYP2B6 `*1/*6 or *1/*9`, 2,422 bp) is not reachable. PyPGx already names the
    alternative reading on the 34 calls over 61 genomes that hang on phase
    (`alternative_phase`), and deciding between two arrangements of two variants needs
    no phaser: count the fragments covering both, as `scripts/getrm/phase-blocks.py`
    does. Settled outright in the cuts: APOE rs429358/rs7412 138 bp apart, 9 fragments;
    CYP1B1's `*2` pair 213 bp, 16; CYP2D6's 2850C>T and `*21` insertion 270 bp, 8.
    Report the read evidence beside the call first, as the caller-disagreement note
    does, and only decide the call once the agreement rate is measured. Upstream will
    not do it for us: PyPGx has accepted a *fully* phased VCF since 0.10.0 (issue #32)
    and every phasing change since is a Beagle bug fix, with no mention of read-backed
    phasing; PharmCAT reads `FORMAT/PS` from 3.0 but its phase-sensitive genes are the
    wide ones.
  - [DONE Oct 2026] `GeneConfig.get_gene_set()` answered a name it did not know with
    *every* gene (`docker/pypgx/pypgx_wrapper.py`). Reachable without a typo:
    `_get_fallback_config()`, used when `config/genes.json` is not mounted, defines
    only `all` and `core`, so `pharmcat_outside_call_genes` asked for
    `pharmcat_can_call`, received all 91 genes, found every gene to be PharmCAT's own
    and handed PharmCAT no outside call at all — CYP2D6 included — in a run that
    otherwise looked normal. It now raises, naming the file and the sets it has, and a
    test checks that every set the wrapper names is in `genes.json`. Sets and
    categories were confused too: `/genotype` tested a requested name against the
    *categories*, so `genes=phase2` genotyped all 91 instead of its 8 and
    `genes=neuropsychopharmacogenes` was refused as an unsupported gene; a new
    `resolve_gene_selection()` takes a set, else a category, else a 400. Found while
    adding `pypgx_preferred_genes`, which reads `load_config()` directly.
  - CYP2D6 and CYP2A6 have no second caller in the stack, so their seven wrong calls on
    GeT-RM went unchallenged. Say so in the report when nothing corroborates a call,
    rather than printing it as flatly as a corroborated one.
### HLA Typing
  - [DONE Sep 2026] ZaroHLA types HLA-A/B/C from the aligned BAM on every alignment lane, a
    FASTQ after its alignment to GRCh38; the raw-FASTQ OptiType route was removed (3f53a38).
### Ancillary and Future tools
  - Now included in Zaromics suite
  - Star-allele callers surveyed Oct 2026, measured against where ZaroPGx actually loses
    calls rather than against their published figures:
    `scripts/getrm/caller-augmentation.md`. Cyrius (PolyForm Strict 1.0.0, which forbids
    distributing or modifying it) and Aldy (Indiana University, noncommercial, no
    third-party data processing) cannot go in the images we publish — a distribution
    limit, not a commercial one. ursaPGx is MIT but hands CYP2D6 to Cyrius and scores
    below us on everything else. StellarPGx was measured and not adopted
    (`scripts/getrm/stellarpgx-comparison.md`).
  - Cyrius as an opt-in CYP2D6 second opinion the operator installs themselves — measured
    Oct 2026 and **not worth taking on as things stand**. On the three CYP2D6 calls we get
    wrong and the three hard ones we get right it scores 4 of 6 against our 3, with no
    regression on the three we already get right. Its one win is the `*1/*21` call we
    print as `*2/*21`, and that is a phasing decision the reads can settle on their own
    (8 fragments carry both deciding positions), so the read-adjudication item above buys
    the same call without naming a noncommercial licence in our UI. Of the two it does not
    fix, one it gets wrong identically to us. Re-ask after read adjudication is built.
    `scripts/getrm/caller-augmentation.md` has the table and the per-sample copy numbers.

## Reporting

- [DONE Oct 2026] Unified report: PharmCAT's recommendations with PyPGx's calls for the
  genes PharmCAT does not call ("P" in the Call column), and PyPGx's per-gene evidence
  (fix/pypgx-full-record).
- Variant rarity context (no allele frequencies are shown). The mitochondrial haplogroup
  and its quality are in the report's mtDNA section (Aug 2026).
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
- [DONE Oct 2026, fix/report-phenotype-colours] "Decreased Function" is amber, like
  intermediate metabolism. Badge colours say what the phenotype means for function against
  what is typical, not CPIC's EHR priority, which ranks the need to act (decided
  2026-10-19; `app/reports/generator.py`).

## UI/UX

- [DONE Oct 2026, fix/ui-stage-row-fits] The stage row and the header fit a phone: in a
  form narrower than about 520px the stages sit four to a row (they ran off the card, and
  the page scrolled sideways), and a stage's popup stays on screen
  (`tests/e2e/test_ui_smoke.py` at 320, 375 and 768px). [DONE Oct 2026,
  fix/ui-stage-row-shows-what-runs] The stages a job has no step for are faded while it
  runs, and a stage switched off stays faded through a job's updates.
- A glyph for preprocessing (e.g. liftover), which lights the GATK glyph now. mtDNA has its
  own glyph (Aug 2026).
- Unify/clean redundant text
- **Front-end test harness.** Until Oct 2026 nothing rendered the page. What existed was
  Node-executed *logic* tests: `tests/test_ui_workflow_flag_reads.py` and
  `tests/test_upload_story_coherence.py` pull the inline `<script>` out of
  `index.html`, run it against real API JSON, and assert on the HTML string (CI
  installs Node 22 so they cannot silently skip). Everything else is source-text
  assertions. There was no `package.json`, no jsdom, and no browser driver, so
  nothing computed a style, clicked anything, or looked at the page.
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
  (`tests/e2e/report_links.py`); (2) [DONE Oct 2026] Playwright smoke over upload →
  progress → report in both themes, with a contrast assertion on the popups and the
  results card (`tests/e2e/test_ui_smoke.py`; with the 08-29 popup CSS put back, the
  dark theme's labels measure 1.22:1 and it fails; its VCF is hg38, and a GRCh37 job
  watched through the page on 2026-10-19 moved the bar through its 9-second liftover, 1 to
  8%, and on to 100); (3) only then consider visual snapshots.

## Data & Database

- PostgreSQL 18: add extensions; implement schemas
- [DONE] JSONB where appropriate (job, step and result metadata); escaping for special
  characters.
- Begin persisting normalized results; build lexicon layer translating between caller spelling
- Consolidating reference and sample material (FASTA/CPIC dumps) into a single `references/` area

## FHIR & Exporting

- [DONE Oct 2026, feat/fhir-push-hapi] The FHIR bundle validates against the HL7 Genomics
  Reporting IG 3.0.0 profiles with no errors (HAPI FHIR 8.12 `$validate` with the IG
  loaded, tx.fhir.org for LOINC and SNOMED CT). The 30x NA12878 bundle had 731 errors:
  observations without the Genetics category, a Task code outside LOINC LL4049-4, the
  genomic study's category. The XML bundle is now FHIR XML; before, no FHIR parser could
  read it. Warnings left: no narrative, no performer, recommendations matching two of the
  IG's profiles, HGNC codes no reachable terminology server checks
  (docs/samples/README.md).
- [DONE Oct 2026, feat/fhir-push-hapi] `POST /fhir/push/workflow/{job_id}` sends a job's
  bundle to the bundled HAPI server (`FHIR_SERVER_URL`) as a transaction, on request
  only; pushing again updates in place and deletes the job's resources the bundle no
  longer has (tagged `urn:zaropgx:job-id`). A server that is down gives 502; the job is
  untouched. `tests/test_fhir_push_hapi.py` runs it against a throwaway HAPI
  (`ZAROPGX_FHIR_TEST_URL`).
- [DONE Oct 2026, feat/ui-fhir-push-button] A finished job's results on the web page have
  a **Push to FHIR server** button (with `HAPI_FHIR_ENABLED` and `FHIR_EXPORT_ENABLED`
  on). It calls the push endpoint for that job and shows what the server created,
  updated and deleted, or why the push failed: the server unreachable, the bundle
  refused, or no answer after the bundle was sent, when the server may have stored it
  (`X-FHIR-Push-Outcome`). Bundled server only: the page does not choose or configure
  another.
- Push a finished job automatically when a deployment asks for it (a setting, off by
  default)?
- [DONE Oct 2026, feat/fhir-coded-implications] Phenotypes are therapeutic implications of
  their genotypes, coded from the IG's LOINC answers where one says the same thing, text
  elsewhere (table in docs/samples/README.md). Each drug's implication cites its genes'
  phenotype implications in `derivedFrom` rather than repeating them as the IG's examples
  do (decided Oct 2026). Open: codes for G6PD's terms and the hedged calls, if LOINC or the
  IG adds them.
- Medication recommendations carry no code: the profile's codes (LOINC LL4049-4: consider
  an alternative, decrease dose, ...) would have to be chosen from each guideline's text.
- Drugs are text: PharmCAT names them by ClinPGx id. RxNorm codes would need a mapping.
- [DONE Oct 2026, feat/hapi-genomics-ig] The bundled HAPI server loads the Genomics
  Reporting IG 3.0.0 and the two packages it depends on, so its `$validate` checks a bundle
  against the IG's profiles. They are pinned files in `docker/fhir-server/packages/`, not
  downloads: the IG changes every year or two, so it is updated with the code (decided
  2026-10-19). Offline, `$validate` does not check LOINC, SNOMED CT or HGNC codes. How to
  call it and how to update the packages: docs/developer/architecture.md.
- High/Low risk for the HLA, MT-RNR1 and malignant hyperthermia phenotypes is provisional:
  CPIC's EHR priority for the result, said in a note on each (Oct 2026, kept coded for
  now). Revisit if the IG, LOINC or CPIC gives these results terms of their own.
- HAPI FHIR server: adjust `ddl-auto` appropriately for prod vs dev
- Export to an outside EHR/PHR (a configurable server, authentication)
- Explore Fasten as a bridge for import/export to HAPI FHIR

## Security & Privacy

- [DONE Oct 2026] Self-hosted deployments never transmit genomic data externally. Audited
  Oct 2026: every service URL the app and sidecars fall back to names a compose service
  (pinned by `tests/test_no_external_service_defaults.py`), and the only other outbound
  traffic is reference and bundle downloads. htslib's fallback of fetching a CRAM's
  reference sequences from EBI by MD5 is closed (Oct 2026; the mtdna image's htslib 1.19
  did it by default): every alignment reader sets a local-only `REF_PATH`
  (`/reference/ref_cache/...`), pinned by the same test.
- Add cookie/consent footer for public deployments with per-user access gating (configurable via `.env`)
- Add Privacy Policy and legal page

## Docker & CI/CD

- [DONE Oct 2026] 0.4.0 is published: the `v0.4.0` tag main's `bootstrap.sh` clones, and
  the eight `zaromicsresearch/zaropgx-*` images at `0.4.0` (which `compose.yml` pulls,
  3b1fc3a) and `latest`. It fixes the two stages 0.3.2's genome-downloader failed on a
  fresh install (fix/downloader-reference-staging): grch37 was fetched over `ftp://`, which
  `requests` cannot do, and the PharmCAT reference tarball's chown failed under rootless or
  userns-remapped Docker. Found by running `bootstrap.sh` end to end on a clean daemon.
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
  (Answered in practice, Oct 2026: a current index beside the file is reused, otherwise
  made. Open: using the index uploaded with the file, above.)
- How to unify pipeline progress across heterogeneous inputs (FASTQ/BAM/VCF)? (In
  part answered Oct 2026: steps weighted by measured duration per input path.)
- Which schema to implement, ultimately?
- Visualizations: what would be useful?
- Should we integrate ClinPGx datasets directly for annotations, instead of (or alongside) a lexicon layer?