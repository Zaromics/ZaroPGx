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

**The fit for this stack is to adjudicate, not to phase.** For a call resting on two
heterozygous variants the question is binary and local: are they on one chromosome or
two? Answering it needs no phaser — count the fragments covering both positions and
see which arrangement they carry. No new tool in the image, no WhatsHap dependency,
nothing changed inside PyPGx, and it says nothing where the reads do not reach.

**But not triggered by `alternative_phase`.** That was the obvious design and it is
the wrong one. `arrangement.py` put it to the 34 calls that carry the flag: the reads
decide **3** — APOE in NA19093 (13 fragments to 0), and two others — and every one of
the three confirms the call PyPGx already prints. Of the other 31, 23 have no linking fragment at
all — the deciding variants are kilobases apart — and 8 are cases where the printed
reading and the alternative imply the same arrangement for the one pair the reads can
reach, so nothing is being asked.
The flag marks the calls PyPGx knows are uncertain, and those are not the calls that
are wrong. HG00589, whose CYP2D6 call *is* wrong, carries `alternative_phase: null`.
Beagle was confident and mistaken, and a feature keyed to the flag would never look.

**Every call has to be checked, not the flagged ones.** `arrangement.py` asks the
question of every diplotype resting on two heterozygous variants within 1 kb, across
all 61 genomes: 719 such pairs, 413 the reads decide, and
**411 of 413 agree with the call**. Beagle is right 99.5% of the time the reads can check it. The two it gets
wrong are:

| sample | gene | call | apart | the call says | the reads say |
|---|---|---|---|---|---|
| HG00589 | CYP2D6 | `*2/*21` | 270 bp | opposite chromosomes | **same**, 7 fragments to 0 |
| NA19026 | CYP2F1 | `*1/*2` | 146 bp | same chromosome | **opposite**, 5 fragments to 0 |

HG00589 is the one that matters: putting the `*2` core on the `*21` chromosome makes
that allele `*21` and the other reference, which is GeT-RM's `*1/*21`. It is one of
our three wrong CYP2D6 calls, and it is the same call Cyrius fixes
([caller-augmentation.md](caller-augmentation.md)) — so a samtools pileup buys what a
PolyForm Strict dependency would. CYP2F1 has no dosing guideline, so correcting it
changes no recommendation; it is listed because it is real, not because it is useful.
Nine other genomes carry the same CYP2F1 pair and all nine read `cis`, as their call
says.

Two things the measurement had to be taught, both of which produced false findings
first:

- **Indels count.** A substitutions-only pass finds 366 decidable pairs and only the
  CYP2F1 one. HG00589 hangs on an insertion, 2580_2581insC, so the check is worthless
  for the call that matters unless it reads indels.
- **Two variants at one position are not a phasing question.** UGT1A1 `*28` and `*36`
  are an insertion and a deletion of the same TA repeat. Counting "an indel is here"
  made a read carrying either look like evidence for both and produced three
  confident contradictions that do not exist. The check compares the indel's own
  sequence and skips pairs no fragment could carry together.

Expected yield on the GeT-RM set, then: **one wrong call corrected** — CYP2D6 HG00589
— plus a CYP2F1 call nobody prescribes on. Not the haul the APOE and CYP1B1
coin-flips suggested: those are flagged, reachable and *already right*. CYP4F2's
`*1/*4` stays wrong, as only a panel or long reads can decide it. The case for
building this is not volume. It is that it is cheap (one pileup over the positions a
call already names), it never guesses where the reads are silent, and the single call
it fixes is in the gene that matters most.

The HG00589 call has since been checked against a second opinion. Cyrius, Illumina's
CYP2D6 caller, was run on all 31 GeT-RM genomes with a scored CYP2D6 call
([caller-augmentation.md](caller-augmentation.md)) and returns GeT-RM's `*1/*21` from
the same three variants we call, differing from us only in which chromosome it puts
the two `*2` cores on. Two things follow. The truth value is not an artefact of the
GeT-RM assay — an independent caller reading the same reads agrees with it. And the
one call a restrictively licensed second caller would fix for us is this one, which
the reads settle on their own; across the full 31 Cyrius ties us at 28, so adjudication
is not merely worth building first — it is the only one of the two that gains anything.
