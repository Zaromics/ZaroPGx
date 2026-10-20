#!/usr/bin/env bash
#
# Cut the demo's input, app/static/demo/NA12878.pgx-demo.cram, from the 1000 Genomes
# 30x CRAM of NA12878 (New York Genome Center, run ERR3239334, 15.8 GB).
#
# The Run Demo button analyses this file. Reads, not a VCF: only reads take every
# stage (HLA typing, PyPGx's deletion and duplication calls, mtDNA, PharmCAT's
# positions genotyped from the reads), and NA12878's answers are published (GeT-RM's
# CYP2D6 *3/*68+*4, HLA-A*01:01/*11:01, -B*08:01/*56:01, -C*01:02/*07:01, haplogroup
# H13a1a1). It keeps the reads every call depends on, about 2.0 M reads and 30 MB:
#
#   - PyPGx's region of every gene in lexicon/gene-table.csv, VDR (its copy-number
#     control) and GSTT1's on chr22_KI270879v1_alt included, 2 kb either side;
#   - 200 bp either side of each of PharmCAT's positions;
#   - the 14 windows whose depth tells a whole genome from a panel
#     (WGS_PROBE_WINDOWS_GRCH38, docker/pypgx/pypgx_wrapper.py). Without them PyPGx is
#     not given depth, and the deletion and duplication calls are lost;
#   - HLA-A, -B and -C (HLA_CLASS_I_LOCI, docker/zarohla/app.py) 5 kb either side, and
#     every read on the HLA allele contigs, where the aligner put most HLA reads.
#     zarohla also gives OptiType the rest of the MHC, its ALT contigs and unplaced
#     reads; these class I reads alone type NA12878's six published alleles;
#   - one chrM read in ten (seed 7, so the cut repeats), about 1,300x.
#
# Reads on GRCh38's other ALT contigs are left out: in this CRAM every one at the
# CYP2D6 and UGT2B17 loci is a supplementary copy of a read on the main chromosome.
#
# Checked 2026-10-20 against the whole genome through ZaroPGx's own services: the same
# 66 PyPGx diplotypes and copy numbers, the same HLA alleles and haplogroup, and the
# same genotype at every PharmCAT position (1,199 reference, 24 variant, 3 uncertain).
#
# Usage:
#   scripts/make-demo-cram.sh [SOURCE] [OUT_DIR]
#
# SOURCE is NA12878's CRAM or BAM, a URL or a local file; the default is the 1000
# Genomes URL, which samtools reads by region, so only the blocks needed are fetched.
# OUT_DIR defaults to ./app/static/demo; run it from the repository's root. Needs the
# reference the genome downloader stages: its cache of the 1000 Genomes analysis set,
# which decodes the source and encodes the result, and PharmCAT's position list.

set -euo pipefail

SOURCE="${1:-https://ftp.sra.ebi.ac.uk/vol1/run/ERR323/ERR3239334/NA12878.final.cram}"
OUT_DIR="${2:-./app/static/demo}"
NAME="NA12878.pgx-demo"
REFERENCE="${ZAROPGX_REFERENCE:-./reference}"
POSITIONS="${REFERENCE}/pharmcat/pharmcat_positions.vcf"
IMAGE="${ZAROPGX_DEMO_IMAGE:-zaromicsresearch/zaropgx-gatk-api:0.4.0}"
DOCKER="${DOCKER:-docker}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -f "${REFERENCE}/ref_cache/.ref_cache_1kg_analysis_set.done" ]; then
  echo "The 1000 Genomes analysis set is not in ${REFERENCE}/ref_cache: start the stack" >&2
  echo "and let the genome downloader cache it, or set ZAROPGX_REFERENCE." >&2
  exit 1
fi
if [ ! -f "$POSITIONS" ]; then
  echo "PharmCAT's position list is missing: $POSITIONS" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
ABS_OUT="$(cd "$OUT_DIR" && pwd)"
ABS_REF="$(cd "$REFERENCE" && pwd)"
WORK="$(mktemp -d "${ABS_OUT}/.make-demo.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

echo "==> Regions"
python3 - "$REPO" "$POSITIONS" > "${WORK}/regions.bed" <<'PY'
import ast
import csv
import sys
from pathlib import Path

repo, positions = Path(sys.argv[1]), sys.argv[2]
regions = []
for row in csv.DictReader(open(repo / "lexicon" / "gene-table.csv")):
    contig, span = row["GRCh38Region"].split(":")
    start, end = (int(x) for x in span.split("-"))
    regions.append(("chr" + contig, max(0, start - 1 - 2000), end + 2000))
for line in open(positions):
    if not line.startswith("#"):
        contig, pos = line.split("\t")[:2]
        regions.append((contig, int(pos) - 1 - 200, int(pos) + 200))


def constant(path, name):
    """A literal constant from the module that owns it, so the cut follows the code."""
    for node in ast.parse((repo / path).read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    sys.exit(f"{name} not found in {path}")


regions += constant("docker/pypgx/pypgx_wrapper.py", "WGS_PROBE_WINDOWS_GRCH38")
for start, end in constant("docker/zarohla/app.py", "HLA_CLASS_I_LOCI")["GRCh38"]:
    regions.append(("chr6", start - 5000, end + 5000))

merged = []
for contig, start, end in sorted(regions):
    if merged and merged[-1][0] == contig and start <= merged[-1][2]:
        merged[-1][2] = max(merged[-1][2], end)
    else:
        merged.append([contig, start, end])
for contig, start, end in merged:
    print(f"{contig}\t{start}\t{end}")
PY
echo "    $(wc -l < "${WORK}/regions.bed") intervals"

SOURCE_ARG="$SOURCE"
MOUNTS=()
if [ -f "$SOURCE" ]; then
  SOURCE_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
  MOUNTS=(-v "${SOURCE_DIR}:/src:ro")
  SOURCE_ARG="/src/$(basename "$SOURCE")"
fi

echo "==> Cutting ${SOURCE}"
$DOCKER run --rm ${MOUNTS[@]+"${MOUNTS[@]}"} -v "$ABS_REF":/reference:ro -v "$WORK":/work \
  -e 'REF_PATH=/reference/ref_cache/%2s/%2s/%s' -w /work --entrypoint bash "$IMAGE" -c '
set -euo pipefail
SOURCE="$1"
samtools view --no-PG -H "$SOURCE" > source_header.sam
awk -F"\t" '\''$1 == "@SQ" {
  for (i = 2; i <= NF; i++) { if ($i ~ /^SN:/) sn = substr($i, 4); if ($i ~ /^LN:/) ln = substr($i, 4) }
  if (sn ~ /^HLA-/) print sn "\t0\t" ln
}'\'' source_header.sam > hla_contigs.bed
samtools view --no-PG -b -M -L regions.bed -o genes.bam "$SOURCE"
samtools view --no-PG -b -M -L hla_contigs.bed -o hla.bam "$SOURCE"
samtools view --no-PG -b -s 7.1 -o chrm.bam "$SOURCE" chrM
# -c -p: the three parts share the read groups and programs of the source; keep one of each.
samtools merge --no-PG -c -p -f -o merged.bam genes.bam hla.bam chrm.bam
samtools sort --no-PG -o sorted.bam merged.bam
{
  # -s wrote "Sub-sampled fraction=0.1", which reads as if all of it were; the line below says what.
  samtools view --no-PG -H sorted.bam | grep -v "^@CO	Sub-sampled"
  printf "@CO\tZaroPGx demo: NA12878, 1000 Genomes 30x (NYGC, ERR3239334), cut by scripts/make-demo-cram.sh to %s\n" \
    "PyPGx and PharmCAT gene regions, depth windows, HLA class I, the HLA contigs and 1 in 10 chrM reads"
} > header.sam
samtools reheader --no-PG header.sam sorted.bam > final.bam
samtools view --no-PG -C -o demo.cram final.bam
samtools index demo.cram
samtools view -c demo.cram
' _ "$SOURCE_ARG"

mv "${WORK}/regions.bed" "${ABS_OUT}/${NAME}.regions.bed"
mv "${WORK}/demo.cram" "${ABS_OUT}/${NAME}.cram"
mv "${WORK}/demo.cram.crai" "${ABS_OUT}/${NAME}.cram.crai"
echo "==> Done:"
ls -la "${ABS_OUT}/${NAME}".*
