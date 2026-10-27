# What Cyrius, Aldy, ursaPGx and pangu would add, 2026-10-27

A published benchmark put PyPGx and Aldy ahead of PharmCAT and ursaPGx overall, with
ursaPGx ahead on CYP2D6, and recommended routing each gene to the caller that does it
best (Gros-La-Faige et al., *Clin Pharmacol Ther* 2026, doi:10.1002/cpt.70365). This is
what that would mean here, measured against where ZaroPGx 0.4.1 actually loses calls on
the 61 GeT-RM genomes rather than against what the tools claim. It is kept so the
question does not have to be re-argued from memory, as
[stellarpgx-comparison.md](stellarpgx-comparison.md) is.

## Where the calls are actually lost

766 of 804 report calls agree with GeT-RM (95.3%). Of the 38 that do not:

- **18** are CYP2E1, SLC22A2 and GSTT1, where GeT-RM's own value is parenthesised — one
  assay, no consensus. No caller fixes those; the truth is the weak part.
- **7** are CYP2D6 and CYP2A6 structure or assignment. The CYP2D6 three are worth
  spelling out, because the obvious reading of them is wrong. PyPGx calls `*5` well: it
  had it right in six of the seven genomes that carry one (`*1/*5` twice, `*2/*5`,
  `*4/*5`, `*5/*29`, `*68+*4/*5`), and got tandems such as `*1/*36x2 + *10`, `*1/*4x2`
  and `*2x2/*71` right too. Both structural misses have the **total copy number right**
  and the copies assigned wrongly, so no depth threshold would catch either:
  `*5/*36x2+*10x2` came back `*36 + *10/*36 + *10` (CNV state `Tandem2B`: four copies
  split two and two instead of none and four) and `*10/*36x2` came back
  `*10/*36 + *10` (`Tandem2A`: the second copy on that allele read as `*10`, not a
  second `*36`). Both are the limits of PyPGx's CNV-state vocabulary. The third,
  `*1/*21` as `*2/*21`, is a phasing error.
- **3** are NUDT15, where PharmCAT 3.4 has no `*2` definition and so calls every `*2`
  carrier `*3`.
- the rest are single calls spread over CYP2B6, CYP4F2, CYP1A2, NAT2, UGT2B15, HLA-B
  and HLA-C.

Only the second and third groups are errors another caller could fix.

## Licence: the constraint is distribution, not commercial use

ZaroPGx is not commercial, so noncommercial-only terms do not restrict running these
tools. What they restrict is shipping them inside the images we publish.

| tool | licence | last release | can go in our images | operator can install it |
|---|---|---|---|---|
| Cyrius (Illumina) | PolyForm Strict 1.0.0 | v1.1.1, 2021-05-03 | no | yes |
| Aldy 4 | Indiana University / IURTC | — | no | with limits |
| ursaPGx | MIT | untagged, 0.2.1 in DESCRIPTION | yes | — |
| pangu (PacBio) | BSD-3-Clause-Clear | v0.2.8, 2024-04-09 | yes | — |
| StellarPGx | MIT | v1.2.8 | yes, measured, not adopted | — |

**Cyrius** is PolyForm Strict 1.0.0. The copyright grant covers everything *"other than
distributing the software or making changes or new works based on the software"*, so it
cannot go in an image and cannot be patched, whatever the purpose. It does not stop
anyone running it: Illumina publish it on PyPI themselves (`pip install cyrius`, author
`xchen2@illumina.com`, licence field `PolyFormStrict`), so an operator who installs it
has it from the licensor. Its permitted purposes are noncommercial, so that restriction
travels to whoever runs it — a reason to name the licence where the feature is switched
on and leave it off by default, not a reason to skip it. Last release May 2021, last
commit October 2023: its definitions are frozen near PharmVar 5.x, so it is worth having
for structure, not for new alleles.

**Aldy's** terms restrict operation as well as distribution — noncommercial research
only, no third-party data processing, not for diagnostic purposes. That suits someone
running their own genome and not a hosted install.

**ursaPGx does not call CYP2D6 itself.** `R/read-pgx.R` answers a request for it with
`stop("Please use cyrius() to perform calling for CYP2D6.")`, and `R/cyrius.R` is a
reticulate wrapper that installs and imports Cyrius. The benchmark's CYP2D6 figure for
ursaPGx (97.7%) is therefore Cyrius's, with Cyrius's licence. What ursaPGx itself does is
annotate star alleles from an already-phased multi-sample VCF — and its published
validation used phased **short-read** 1000 Genomes VCFs (NYGC 30x, GRCh38), not long
reads. Its overall figure in the same benchmark was 83.0%.

**pangu** is the only permissively licensed, still-maintained CYP2D6 structural caller,
but it wants PacBio HiFi reads, which the pipeline refuses at upload with a 400:
bwa-mem has no long-read preset and OptiType is short-read only
(`app/api/utils/fastq_platform.py`).

Two further corrections to the brief that motivated this: the benchmark tested ursaPGx
0.2.1, PyPGx 0.25.0, Aldy 4.7 and PharmCAT 2.15.4 with DRAGEN 4.4 as a comparator —
Cyrius and StellarPGx appear only as the outside CYP2D6 callers inside ursaPGx and
PharmCAT. And CYP2A6 was not benchmarked at all, so nothing published supports routing
CYP2A6 anywhere.

Its most useful finding for us is that on CYP2D6 each caller fails on a different kind of
structural variant: StellarPGx and Aldy on tandems, **PyPGx on whole duplications**. That
is the shape of two of our three CYP2D6 errors.

## Three calls we already have and discard

On an alignment run PyPGx genotypes PharmCAT's genes too, for their copy number, but
PharmCAT's call is the one printed. Scoring both callers on the genes where both speak:

| gene | n | PharmCAT right | PyPGx right | PharmCAT only | PyPGx only | net if PyPGx preferred |
|---|---|---|---|---|---|---|
| NUDT15 | 11 | 8 | 10 | 0 | 2 | **+2** |
| CYP4F2 | 20 | 17 | 18 | 0 | 1 | **+1** |
| CYP2C19 | 26 | 26 | 26 | 0 | 0 | 0 |
| CYP2C9 | 26 | 26 | 26 | 0 | 0 | 0 |
| CYP3A5 | 25 | 25 | 25 | 0 | 0 | 0 |
| NAT2 | 20 | 19 | 19 | 0 | 0 | 0 |
| CYP2B6 | 25 | 23 | 22 | 1 | 0 | −1 |
| CYP3A4 | 28 | 28 | 27 | 1 | 0 | −1 |
| UGT1A1 | 20 | 20 | 19 | 1 | 0 | −1 |
| TPMT | 35 | 35 | 33 | 2 | 0 | −2 |

Preferring PyPGx everywhere is a net loss, so "PharmCAT's genes are PharmCAT's" is
broadly right. On NUDT15 and CYP4F2 it is not: PyPGx is right everywhere PharmCAT is,
plus three more, and there is no case where the swap costs a call.

NUDT15 is the one to act on, because the reason is mechanistic and not a score: PharmCAT
3.4 has no `*2`, so `*2/*2` can only ever print `*3/*3` and `*2/*5` only `*3/*5`, on every
sample that carries it, for ever. The phenotype survives — both alleles are No Function —
but the diplotype is wrong. CYP4F2 is +1 of 20, which is real but thin; it is left until
more samples say the same thing.

## What is worth building

1. **Prefer PyPGx's NUDT15.** *Built.* genes.json's `pypgx_preferred_over_pharmcat`,
   honoured by `pharmcat_outside_call_genes` in the wrapper and by main.nf's own
   fallback filter. +2 calls, no new dependency. CYP4F2 held pending more samples.
2. **Say when the two callers disagree.** *Built*, `app/reports/caller_agreement.py`.
   For a gene both call, the report printed PharmCAT's and said nothing about PyPGx's.
   CYP4F2 in NA19908 was the case: PharmCAT printed `*1/*4`, PyPGx called `*2/*3` and
   recorded `alternative_phase: ["*4"]` — it knew both readings fit — and GeT-RM says
   `*2/*3`. A difference in the alleles is now a note beside the call, a note in the
   Executive Summary and one alert; which call is printed does not change, so it costs
   no accuracy.

   Only calls both callers write as star alleles are compared, which leaves out every
   gene whose callers describe a genotype in different terms (ABCG2, IFNL3 and VKORC1
   by rsID, DPYD by variant, G6PD's "MALE", the RYR1 and CFTR variants PyPGx has no
   allele for), and NAT2 is left out by name: PharmCAT 3.4 uses PharmVar's names and
   PyPGx the legacy groups, so its `*12` or `*13` is PharmCAT's `*4`. Of the 1,159
   gene-sample pairs on these genomes where PharmCAT called a gene itself and PyPGx
   called it too, 671 are comparable that way and 21 named different alleles: 7 NAT2,
   and 14 real differences — about one note per four reports, never more than one in a
   report. PyPGx was right in four, PharmCAT in four, both wrong in one. A gene whose
   printed call is PyPGx's has no second opinion to show, because PharmCAT records only
   the outside call and not its own matcher's reading.
3. **Read-backed phasing — for CYP2D6, not for CYP4F2.** PyPGx phases with Beagle —
   statistical, from a reference panel, with no read evidence behind it. Two of the
   wrong calls are phasing errors, and measuring the reads (`phase-blocks.py`, on the
   cached cuts) says only one of them is within a short-read phaser's reach.

   **CYP4F2: out of reach.** `*4` is chr19:15,879,621 C>T and chr19:15,897,578 A>C
   **on one chromosome**; `*2/*3` is the same two variants on opposite ones. In HG00436
   and NA19908 both sit at allele fraction ~0.5 — het, het, phase unknown from the
   genotypes alone — and Beagle put them on one haplotype in HG00436 (wrong) and on two
   in NA19908 (right), from nothing but the panel. The two are 17,957 bp apart. There
   is no shortage of heterozygous sites between them — 55 in each sample, about one
   every 330 bp — but the 2x150 reads (median fragment 415–429 bp) leave gaps no
   fragment bridges: 6 of them in HG00436, the widest 3,485 bp, and 8 in NA19908. The
   two variants fall in different read-backed phase blocks in both samples, whether a
   link needs one fragment or two. Only a panel or long reads can decide CYP4F2 here.

   **CYP2D6: within reach.** HG00589 was called `*2/*21` where GeT-RM says `*1/*21`.
   All three deciding positions are heterozygous — `*2`'s cores at chr22:42,126,611 and
   chr22:42,127,941, `*21`'s 2580_2581insC at chr22:42,128,211 — and all three sit in
   one read-backed block. The pair that settles it, 2850C>T and the `*21` insertion,
   are 270 bp apart with **8 fragments carrying both**: the reads say outright whether
   2850C>T is on the `*21` chromosome, which is the question the call got wrong.
   WhatsHap is MIT and StellarPGx already depends on it.
4. **Cyrius as an opt-in second CYP2D6 caller.** The plumbing exists: PharmCAT already
   takes CYP2D6 from an outside caller, which is how PyPGx's reaches the report. Cyrius
   needs only numpy, scipy, pysam and statsmodels and reads the BAM the pipeline already
   has. Not in the image: detected at runtime if the operator installed it, off by
   default, licence named where it is enabled. Measuring whether its published 97.7%
   holds on our genomes costs a re-cut — it normalises depth against 3,004 control
   intervals across 22 autosomes (`data/CYP2D6_region_38.bed`), which the cached cuts do
   not contain, so `cut.sh` has to run again against that BED, about ten minutes a
   sample.
5. **Say when a CYP2D6 or CYP2A6 call has nothing behind it.** Those two have no second
   caller at all, so the 7 errors there are unchallenged. StellarPGx is MIT and fails on
   different structural variants than PyPGx, so running it as a disagreement detector
   alone would let the report mark a diplotype uncertain instead of printing it flat.
   Cheaper than adopting it, and what the software intent asks for.
6. **A long-read path** is a separate project, not a caller swap: minimap2, a long-read
   HLA typer, PyPGx's long-read mode (which has no SV detection), pangu for CYP2D6. Worth
   starting only if long-read input is wanted.

The two tools that would genuinely beat us — Cyrius on CYP2D6 structure, Aldy on CYP2B6
`*6`/`*9` from reads — cannot go in the published images, and the permissively licensed
alternatives do not beat us on the genes where we are weak. That leaves two routes to the
same capability: build the method ourselves (2 and 3), or support the tool without
shipping it (4). They are not exclusive, and 1 is worth doing either way. 1 and 2 are
built; 3 is worth building for CYP2D6, and the measurement says not to bother for
CYP4F2.
