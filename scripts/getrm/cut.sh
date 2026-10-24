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
# skipped. JOBS (default 4) cuts run at once. A cut the archive's stream breaks off (EBI
# drops long range requests now and then) is tried again, up to TRIES (default 3) times.
# SAMPLES_TSV (default scripts/getrm/samples.tsv) gives each sample's run and CRAM URL, in
# samples.tsv's columns. Needs what make-demo-cram.sh needs: the reference the genome
# downloader stages (ZAROPGX_REFERENCE, default ./reference).

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${1:?usage: scripts/getrm/cut.sh OUT_DIR [SAMPLE ...]}"
shift
JOBS="${JOBS:-4}"
TRIES="${TRIES:-3}"
SAMPLES_TSV="${SAMPLES_TSV:-${HERE}/samples.tsv}"
if [ "$#" -gt 0 ]; then
  SAMPLES=("$@")
else
  mapfile -t SAMPLES < "${HERE}/core.txt"
fi
mkdir -p "$OUT_DIR"

cut_one() {
  local sample="$1" run url
  [ -f "${OUT_DIR}/${sample}.pgx.cram.crai" ] && return 0
  read -r run url < <(awk -F'\t' -v s="$sample" '$1 == s { print $4, $5 }' "$SAMPLES_TSV")
  if [ -z "${url:-}" ]; then
    echo "${sample}: not in ${SAMPLES_TSV}" >&2
    return 1
  fi
  local try
  for try in $(seq 1 "$TRIES"); do
    if "${HERE}/../make-demo-cram.sh" "$url" "$OUT_DIR" "${sample}.pgx" \
        "${sample}, 1000 Genomes 30x (NYGC, ${run})" > "${OUT_DIR}/${sample}.log" 2>&1; then
      echo "${sample}: $(stat -c %s "${OUT_DIR}/${sample}.pgx.cram") bytes"
      return 0
    fi
    [ "$try" -lt "$TRIES" ] && sleep 60
  done
  echo "${sample}: failed ${TRIES} times, see ${OUT_DIR}/${sample}.log" >&2
  return 1
}
export -f cut_one
export HERE OUT_DIR SAMPLES_TSV TRIES

printf '%s\n' "${SAMPLES[@]}" | xargs -P "$JOBS" -I{} bash -c 'cut_one "$1"' _ {}
