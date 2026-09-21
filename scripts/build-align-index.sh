#!/usr/bin/env bash
#
# Build the FASTQ lane's alignment reference and its bwa index.
#
# This is a host-side prerequisite, run once, not part of any image build. Two
# reasons it cannot be an off-the-shelf download:
#
#   1. PyPGx requires alignment to MAIN CONTIGS ONLY -- ALT contigs reduce
#      variant-calling sensitivity -- with exactly one exception,
#      chr22_KI270879v1_alt, which carries GSTT1 and must be present or that gene
#      cannot be called at all. AWS iGenomes ships hs38DH (ALT-bearing, wrong);
#      Broad's ALT-free tarball omits the GSTT1 contig (also wrong). Nothing
#      published satisfies both halves.
#   2. The result is ~3 GB of FASTA and ~5.6 GB of index. That does not belong in
#      a published Docker image.
#
# Expect the bwa index to take roughly 60-90 minutes and ~5 GB of RAM. bwa-mem2
# would want ~28N GB (measured 80-90 GB) which is why this lane uses bwa -- see
# docker/zaroalign/LICENSES.md.
#
# Usage:
#   scripts/build-align-index.sh [OUT_DIR] [SOURCE_FASTA]
#
# OUT_DIR defaults to $ZAROPGX_ALIGN_REFERENCE, then ./reference/pypgx.

set -euo pipefail

OUT_DIR="${1:-${ZAROPGX_ALIGN_REFERENCE:-./reference/pypgx}}"
SOURCE_FASTA="${2:-./reference/hg38/Homo_sapiens_assembly38.fasta}"
TARGET="${OUT_DIR}/pypgx_grch38.fasta"
IMAGE="${ZAROPGX_ALIGN_IMAGE:-zaromicsresearch/zaropgx-zaroalign:0.3.1}"
DOCKER="${DOCKER:-docker}"

if [ ! -f "$SOURCE_FASTA" ]; then
  echo "Source FASTA not found: $SOURCE_FASTA" >&2
  echo "Point argument 2 at a GRCh38 FASTA that contains chr22_KI270879v1_alt." >&2
  exit 1
fi
if [ ! -f "${SOURCE_FASTA}.fai" ]; then
  echo "Source FASTA is not indexed (${SOURCE_FASTA}.fai missing)." >&2
  exit 1
fi
if ! grep -q "^chr22_KI270879v1_alt" "${SOURCE_FASTA}.fai"; then
  echo "Source FASTA has no chr22_KI270879v1_alt contig, so GSTT1 could not be" >&2
  echo "called from reads aligned against it. Refusing to build a reference that" >&2
  echo "silently drops a pharmacogene." >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

if [ -f "${TARGET}.bwt" ]; then
  echo "Index already present at ${TARGET}.bwt -- nothing to do."
  echo "Delete it to force a rebuild."
  exit 0
fi

echo "==> Building PyPGx-compliant GRCh38 (main contigs + chr22_KI270879v1_alt)"
if [ ! -f "$TARGET" ]; then
  CONTIGS=$(awk '$1 ~ /^chr([0-9]+|X|Y|M)$/ {print $1}' "${SOURCE_FASTA}.fai")
  echo "    main contigs: $(echo "$CONTIGS" | wc -w) + chr22_KI270879v1_alt"
  # shellcheck disable=SC2086
  samtools faidx "$SOURCE_FASTA" $CONTIGS chr22_KI270879v1_alt > "$TARGET"
  samtools faidx "$TARGET"
else
  echo "    reference already built, reusing $TARGET"
fi

echo "==> Building bwa index (expect 60-90 minutes)"
$DOCKER run --rm \
  -v "$(cd "$OUT_DIR" && pwd)":/ref \
  "$IMAGE" \
  bwa index "/ref/$(basename "$TARGET")"

echo "==> Done. Index files:"
ls -la "$OUT_DIR"
