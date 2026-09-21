# Third-party licences in the `zaropgx-zaroalign` image

ZaroPGx publishes this image on Docker Hub, so the licences of what it ships are
a distribution question, not just an attribution one.

## bwa — GPL-3.0

`bwa` (Burrows-Wheeler Aligner, Heng Li) is installed from Debian's `bwa` package
and is licensed **GPL-3.0**.

**Why it is here rather than an MIT-licensed aligner.** PyPGx requires reads to be
aligned to main contigs only, with exactly one exception — `chr22_KI270879v1_alt`,
which carries GSTT1 and must be present or that gene cannot be called. No
off-the-shelf prebuilt index satisfies both halves of that rule, so the index has
to be built locally, and that is what rules out the alternatives on this hardware:

| aligner | licence | index-build peak RAM |
|---|---|---|
| bwa | GPL-3.0 | ~5 GB |
| bwa-mem2 | MIT | ~28N GB — measured 80–90 GB, reported to fail on 64 GB |
| minimap2 | MIT | >11 GB, and not recommended by its author for short reads |

On a 32 GB VM, bwa is the only one that completes an index build. Heng Li's own
unretracted position is that "bwa-mem is still better for production uses" for
short reads, and nf-core/sarek still defaults to `bwa-mem`, so this is not a
compromise on accuracy either.

**How it is used, and why that matters for the licence.** `bwa` is invoked as a
standalone executable over a process boundary (`asyncio.create_subprocess_exec`).
Nothing in ZaroPGx links against it, includes its headers, or derives from its
source. Under the GPL's own terms this is *mere aggregation*: the image is a
collection of independently licensed programs sharing a filesystem, and combining
them on one volume does not extend the GPL to the other programs.

What the GPL does still require, and what this file exists to satisfy, is that the
licence travel with the binary and that recipients know where to get its source.

- Upstream source: <https://github.com/lh3/bwa>
- Debian source package: `apt-get source bwa` on a Debian base of the same release,
  or <https://sources.debian.org/src/bwa/>
- Licence text: `/usr/share/doc/bwa/copyright` inside this image

## samtools — MIT/Expat

`samtools` is MIT/Expat licensed. Source: <https://github.com/samtools/samtools>.

## Reference data

The alignment reference is **not** shipped in this image. It is built on the host
from an existing GRCh38 FASTA and bind-mounted at `/reference/pypgx`. No reference
sequence is redistributed by this image.
