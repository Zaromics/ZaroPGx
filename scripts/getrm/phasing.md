# What phasing would actually buy us, 2026-10-27

Two of ZaroPGx's 38 wrong calls on the 61 GeT-RM genomes are phasing errors, and
neither caller has read evidence behind its phasing. This is what the reads can and
cannot settle, measured rather than assumed, and where a fix belongs in the stack.
`phase-blocks.py` produces the per-region numbers.

## Where phase is decided today

Nothing in the pipeline phases anything. PharmCAT's report says so on every call of
all 61 genomes: `phased: false`, 1,159 of 1,159. It sets `effectivelyPhased` on 990
of them, meaning phase could not have changed that call; **169 are calls where phase
mattered and none was supplied**, and 27 of those PharmCAT could not resolve at all
and printed as alternatives: DPYD 21, RYR1 4, CYP2B6 1, SLCO1B1 1.

PyPGx phases with Beagle, statistically, from the 1000 Genomes panel. It records when
another phasing of the same variants would name a different allele: 34 of its 5,307
successful calls across the 61 genomes carry `alternative_phase`, over nine genes —
CYP1B1 10, CYP2D6 9, UGT2B15 6, CYP4A22 3, APOE 2, and one each of SLCO1B1, ABCB1,
CYP2B6 and CYP4F2. **The stack already knows which calls hang on a coin-flip.**

## What short reads can reach

A star allele defined by two or more core variants can only be told from the same
variants on opposite chromosomes if their phase is known. From `lexicon/allele-table.csv`,
179 star alleles across 30 genes are defined that way. Against a 2x150 library whose
fragments run 415–431 bp median and ~580 bp at the 90th percentile:

| | alleles |
|---|---|
| whole span within one fragment (<= 600 bp) — reads settle it outright | **51** |
| span wider, but every step <= 600 bp | 1 |
| at least one step wider than 600 bp | 127 |

Median span is 2,422 bp, the 90th percentile 24,242 bp, the widest 80,235 bp. So
read-backed phasing is a **narrow** tool on short reads: it reaches about 28% of the
alleles where phase decides anything.

Chaining through intervening heterozygous sites does not rescue the rest. Measured on
real cuts, the chain breaks well before it needs to:

| case | distance | fragments carrying both | verdict |
|---|---|---|---|
| APOE rs429358/rs7412, NA19908 (E2/E4, alternative E3) | 138 bp | **9** | settled |
| CYP1B1 `*2` pair, HG00276 | 213 bp | **16** | settled |
| CYP2D6 2850C>T / `*21` insertion, HG00589 | 270 bp | **8** | settled |
| CYP1B1 `*3` core to `*2` pair, HG00276 | 3,974 bp | 0 | out of reach |
| CYP2B6 516G>T / 785A>G, NA06991 | 2,422 bp | 0 | out of reach |
| CYP4F2 `*4` pair, HG00436 and NA19908 | 17,957 bp | 0 | out of reach |

CYP4F2 is the clearest refutation of the chaining hope: 55 heterozygous sites lie
between its two variants, about one every 330 bp, and the chain still breaks at six
gaps in HG00436 (widest 3,485 bp) and eight in NA19908.

Crucially, **the alleles within reach are concentrated in PyPGx's genes, not
PharmCAT's**: CYP2D6 15 of 49, CYP2A6 9 of 12, POR 4 of 6, UGT1A1 2 of 2, against
SLCO1B1 1 of 22 and CYP2B6 2 of 14. CYP2D6 and CYP2A6 are exactly the two genes with
no second caller and seven wrong calls between them. Of PharmCAT's 27 unresolved
calls, the only one that looked reachable — CYP2B6 `*1/*6 or *1/*9` — is not.

## Upstream: PyPGx is not going to do this for us

PyPGx issue #32 asked for phased input in 2021. The maintainer answered that "it's
not easy to work with phased VCF directly", and 0.10.0 (2021-12-19) delivered it:
`import-variants` accepts phased VCFs, and `run-ngs-pipeline` accepts them and skips
statistical phasing. `--variants` still documents the condition as *fully* phased.
Every phasing entry in the changelog since — 0.11.0, 0.17.0, 0.18.0, 0.19.0, 0.21.0,
0.22.0, 0.23.0, 0.26.0 — is a bug fix to `estimate_phase_beagle()`. Neither the issue
nor the changelog mentions read-backed phasing. We run PyPGx 0.27.0, so the
phased-input path is available to us; nobody upstream is building the phaser.

PharmCAT supports `FORMAT/PS` as of 3.0 and we run 3.4, so it would consume a phased
VCF without any change on our side.

## Where a fix belongs

**Not** by replacing PyPGx's phasing. PyPGx skips Beagle only for a *fully* phased
VCF, and read-backed phasing produces blocks, not a full phasing — four blocks across
CYP2D6 in HG00589. Handing PyPGx a fully phased VCF therefore means running WhatsHap
*and then* Beagle ourselves and owning the result, so a bug in our phasing would
silently move every PyPGx call, to gain the 28% of alleles reads actually reach.

**Not** by phasing the VCF for PharmCAT either, tempting as it is given it already
reads PS. The measurement says PharmCAT's phase-sensitive genes have wide spans: its
one plausibly reachable unresolved call is not reachable.

**The fit for this stack is to adjudicate, not to phase.** PyPGx already enumerates
the alternative reading (`alternative_phase`) for the 34 calls that hang on phase.
For such a call, the question is binary and local: are these two variants on one
chromosome or two? Answering it needs no phaser at all — count the fragments covering
both positions and see which arrangement they carry, which is what `phase-blocks.py`
already does. That means:

- no new tool in the image, no WhatsHap dependency, nothing changed inside PyPGx;
- it fires only where the reads genuinely reach, and says nothing where they do not;
- it can start by *reporting* the read evidence beside the call, exactly as
  `app/reports/caller_agreement.py` reports a disagreement between callers, and only
  later decide the call, once the agreement rate is measured on all 61 genomes.

Expected yield on the GeT-RM set: one wrong call corrected outright (CYP2D6 HG00589
`*2/*21` for `*1/*21`), the APOE and CYP1B1 coin-flips settled, and CYP2A6 — 9 of its
12 multi-variant alleles within reach, 3 discordant calls and 1 no-call today — the
most likely place for more. CYP4F2's `*1/*4` stays wrong: only a panel or long reads
can decide it.
