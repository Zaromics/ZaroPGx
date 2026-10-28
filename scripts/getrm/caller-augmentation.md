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

## Cyrius, run on six of the genomes

Item 4 below used to say the measurement had not been done. It has: Cyrius 1.1.1 in a
throwaway venv (nothing shipped, nothing added to any image), on the three CYP2D6 calls
we get wrong and the three structurally hard ones we get right, so the comparison shows
both what it would fix and what it would cost.

| sample | GeT-RM | ZaroPGx 0.4.1 | Cyrius 1.1.1 | |
|---|---|---|---|---|
| HG00436 | `*2x2/*71` | `*2x2/*71` | `*2x2/*71` | both right |
| NA12878 | `*3/(*68)+*4` | `*3/*68 + *4` | `*3/*68+*4` | both right |
| NA18526 | `*1/*36x2+*10` | `*1/*36x2 + *10` | `*1/*36+*36+*10` | both right |
| HG00589 | `*1/*21` | `*2/*21` | `*1/*21` | **Cyrius fixes it** |
| NA18565 | `*10/*36x2` | `*10/*36 + *10` | `*36/*36+*10` | both wrong |
| NA18545 | `*5/*36x2+*10x2` | `*36 + *10/*36 + *10` | `*36+*10/*36+*10` | both wrong |

Cyrius 4 of 6, us 3 of 6, every call `PASS`, coverage MAD 0.065–0.078. It costs us
nothing on the three we already get right, which is the result that matters most: an
opt-in second caller that regressed a correct call would not be worth having.

The single win is **HG00589, and it is a phasing decision, not a structural one**.
Copy number is ordinary there — Cyrius reads `cn2`, two copies, no hybrid — and it calls
the same three variants we do: chr22:42,126,611 and chr22:42,127,941 (`*2`'s cores) and
the `*21` insertion at chr22:42,128,218. The only difference is which chromosome the two
`*2` variants are put on. Cyrius puts them with the insertion, making that allele `*21`
and the other `*1`; Beagle put them opposite, making `*2/*21`. This is exactly the call
item 3 measured and found **within a short-read phaser's reach** — 8 fragments carry
both deciding positions. We can have this one without Cyrius.

The two it does not fix are the two whose total copy number was already right:

- **NA18545 — both callers produce the identical wrong answer.** Cyrius reads four
  CYP2D6 units (`*10_*10_*36_*36`, `exon9hyb_exon9hyb`) and so do we; GeT-RM's
  `*5/*36x2+*10x2` is the same four units with all of them on one chromosome and none
  on the other, against the two-and-two both callers assume. Nothing in the depth
  separates those arrangements — the copy number is identical, and the copies are
  near-identical repeats about 10 kb apart, so the reads cannot phase them either. A
  purpose-built CYP2D6 caller landing on our error independently is the strongest
  evidence we have that this call is not a deficiency in our caller choice.
- **NA18565 — Cyrius gets the alleles right and the arrangement wrong.** It reads
  `*10_*36_*36`, which is GeT-RM's content exactly; we read `*10`, `*36`, `*10` and
  miss the second hybrid. So the hybrid-counting is a real modelling advantage, but it
  does not produce a correct diplotype here: Cyrius splits those three as `*36` against
  `*36+*10` where GeT-RM has `*10` against `*36x2`.

**On re-cutting.** Cyrius normalises depth against 3,004 intervals over 22 autosomes,
so each sample is thousands of range requests rather than the handful a PGx cut makes.
EBI's FTP host will not sustain that — four of the first six attempts died with
`Seek at offset … failed` / `EOF marker is absent` after htslib exhausted its own
reconnects, and the two that survived were running at about 0.15 MB/min. The same cuts
against the AWS Open Data mirror of the same CRAMs
(`1000genomes.s3.amazonaws.com/1000G_2504_high_coverage/data/<run>/<sample>.final.cram`)
ran at about 7 MB/min and all six finished in 11–25 minutes, two of them needing a
retry. The run accession is already in `samples.tsv`'s EBI URL, so the mirror URL can be
built from it. Worth knowing before anyone plans a re-cut of all 61.

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

   **CYP2D6: within reach, and settled.** HG00589 was called `*2/*21` where GeT-RM
   says `*1/*21`. All three deciding positions are heterozygous — `*2`'s cores at
   chr22:42,126,611 and chr22:42,127,941, `*21`'s 2580_2581insC at chr22:42,128,211 —
   and all three sit in one read-backed block. The pair that settles it, 2850C>T and
   the `*21` insertion, are 270 bp apart, and the reads have since been counted:
   **7 fragments carry the `*2` core and the insertion together, none carry one
   without the other**. That makes the allele `*2` core + insC = `*21` and the other
   chromosome reference, which is GeT-RM's `*1/*21`. No phaser was needed to find it,
   only a pileup over the two positions the call already names.

   `arrangement.py` then asked the same question of every call on all 61 genomes —
   719 pairs of heterozygous variants within 1 kb, 413 the reads decide, **411
   of 413 agreeing with the call**. Beagle is right 99.5% of the time the reads can check it,
   and the only other disagreement is CYP2F1 in NA19026, a gene with no dosing
   guideline. The yield is one corrected CYP2D6 call, not a haul; the case for
   building it is that the check is cheap and never guesses where the reads are
   silent. [phasing.md](phasing.md) has the detail, including the two ways the
   measurement produced false findings before it was right.
4. **Cyrius as an opt-in second CYP2D6 caller — measured, and weaker than it looked.**
   The plumbing exists: PharmCAT already takes CYP2D6 from an outside caller, which is
   how PyPGx's reaches the report. Cyrius needs only numpy, scipy, pysam and statsmodels
   and reads the BAM the pipeline already has, and it would go in as a runtime-detected
   option, off by default, licence named where it is enabled. The section above measures
   it: 4 of 6 against our 3, no regression on the three we already get right, and its one
   win is the `*1/*21` phasing call that **item 3 can fix without it**. What is left is
   one genuine modelling advantage that still does not yield a correct call (the second
   `*36` in NA18565) and one error it makes identically to ours (NA18545). Taking on a
   PolyForm Strict dependency and naming someone else's noncommercial licence in our UI
   buys, on this evidence, nothing that item 3 does not. Build item 3 first and re-ask
   afterwards. Measured on six genomes, not 61: a wider run would firm up the hybrid
   advantage, but it would have to overturn the HG00589 finding to change the answer.
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
alternatives do not beat us on the genes where we are weak. That left two routes to the
same capability: build the method ourselves (2 and 3), or support the tool without
shipping it (4). Running Cyrius has now separated them. Its advantage over us on these
genomes is one call, and that call is a phasing decision the reads can settle, so route 4
collapses into route 3. 1 and 2 are built; 3 is worth building for CYP2D6, and the
measurement says not to bother for CYP4F2.
