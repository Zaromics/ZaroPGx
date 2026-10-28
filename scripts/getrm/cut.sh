#!/usr/bin/env bash
#
# Cut GeT-RM reference samples to the reads ZaroPGx reads, from their 1000 Genomes 30x
# CRAMs (scripts/make-demo-cram.sh, the cut the demo is made with): about 30 MB and ten
# minutes a sample, fetched by region, instead of a 16 GB genome each.
#
# Usage:
#   scripts/getrm/cut.sh OUT_DIR [SAMPLE ...]
#
# With no SAMPLE, every sample in scripts/getrm/core.txt. A sample already cut is
# skipped. JOBS (default 4) cuts run at once. A cut the archive's stream breaks off is
# tried again, up to TRIES (default 3) times.
# SAMPLES_TSV (default scripts/getrm/samples.tsv) gives each sample's run and CRAM URL, in
# samples.tsv's columns. Needs what make-demo-cram.sh needs: the reference the genome
# downloader stages (ZAROPGX_REFERENCE, default ./reference).
#
# A cut is thousands of small range requests, and EBI's FTP host drops them under that
# load: samtools then ends with "Seek at offset ... failed" and "EOF marker is absent",
# the stream broken rather than the data bad. The same CRAMs are on the AWS Open Data
# mirror, which is built for range requests and measured here about 45x faster. MIRROR
# (default below) is tried first, per sample, and the archive URL from samples.tsv is
# used when a sample is not on it. The mirror keeps the 2,504-sample panel under data/
# and the 698 related genomes under additional_698_related/data/, so both prefixes are
# probed; every sample in samples.tsv is on one of them. MIRROR= empty uses the archive
# for every sample.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${1:?usage: scripts/getrm/cut.sh OUT_DIR [SAMPLE ...]}"
shift
JOBS="${JOBS:-4}"
TRIES="${TRIES:-3}"
SAMPLES_TSV="${SAMPLES_TSV:-${HERE}/samples.tsv}"
MIRROR="${MIRROR-https://1000genomes.s3.amazonaws.com/1000G_2504_high_coverage}"
# Without curl every probe fails and every sample silently takes the archive's much
# slower path, which is the one thing this script exists to avoid.
if [ -n "$MIRROR" ] && ! command -v curl > /dev/null 2>&1; then
  echo "cut.sh: curl is needed to probe the mirror; set MIRROR= to cut from the archive" >&2
  exit 1
fi
if [ "$#" -gt 0 ]; then
  SAMPLES=("$@")
else
  mapfile -t SAMPLES < "${HERE}/core.txt"
fi
mkdir -p "$OUT_DIR"

# Where the mirror keeps this sample, or nothing when it does not keep it. Probed
# twice: one 503 under parallel load would otherwise send a sample down the archive's
# much slower path with nothing in the log saying why it went there.
mirror_url() {
  local run="$1" sample="$2" base probe
  [ -n "${MIRROR:-}" ] || return 0
  for probe in 1 2; do
    for base in "${MIRROR}/data" "${MIRROR}/additional_698_related/data"; do
      if curl -sf -o /dev/null -r 0-0 --max-time 30 \
          "${base}/${run}/${sample}.final.cram.crai"; then
        printf '%s' "${base}/${run}/${sample}.final.cram"
        return 0
      fi
    done
    [ "$probe" -eq 1 ] && sleep 5
  done
  return 0
}

cut_one() {
  local sample="$1" run archive url from
  [ -f "${OUT_DIR}/${sample}.pgx.cram.crai" ] && return 0
  read -r run archive < <(awk -F'\t' -v s="$sample" '$1 == s { print $4, $5 }' "$SAMPLES_TSV")
  if [ -z "${archive:-}" ]; then
    echo "${sample}: not in ${SAMPLES_TSV}" >&2
    return 1
  fi
  url=$(mirror_url "$run" "$sample")
  local try
  for try in $(seq 1 "$TRIES"); do
    # The archive URL is the fallback for a cut that breaks, not only for a sample
    # the mirror does not have: a stream S3 drops three times is not a sample that
    # cannot be cut. The last try always uses the archive.
    from="${url:-$archive}"
    if "${HERE}/../make-demo-cram.sh" "$from" "$OUT_DIR" "${sample}.pgx" \
        "${sample}, 1000 Genomes 30x (NYGC, ${run})" > "${OUT_DIR}/${sample}.log" 2>&1; then
      echo "${sample}: $(stat -c %s "${OUT_DIR}/${sample}.pgx.cram") bytes"
      return 0
    fi
    if [ -n "$url" ] && [ "$try" -ge "$((TRIES - 1))" ]; then
      echo "${sample}: the mirror cut failed ${try}x, finishing from the archive" >&2
      url=""
    fi
    [ "$try" -lt "$TRIES" ] && sleep 60
  done
  echo "${sample}: failed ${TRIES} times, see ${OUT_DIR}/${sample}.log" >&2
  return 1
}
export -f cut_one mirror_url
export HERE OUT_DIR SAMPLES_TSV TRIES MIRROR

printf '%s\n' "${SAMPLES[@]}" | xargs -P "$JOBS" -I{} bash -c 'cut_one "$1"' _ {}
