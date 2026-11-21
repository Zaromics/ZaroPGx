# Bake targets aligned with compose.yml buildable services used by e2e.
# Keep contexts/dockerfiles/args in sync with compose.yml when those change.
# Pre-built Hub images (db, kroki, …) are not baked — compose pulls them.
#
# mtdna is intentionally NOT a bake target and is NOT in e2e's SERVICES list
# (scripts/e2e-up.sh): its image (built FROM upstream's
# quay.io/genepi/mtdna-server-2:v2.1.16) is 2.3 GB to pull and 7.2 GB
# unpacked, and would exhaust a GitHub Actions runner's disk. compose.e2e.yml sets MTDNA_ENABLED=false on
# the app service to match. Do not add an mtdna bake target without solving
# the disk-budget problem first.

variable "ZAROPGX_TAG" {
  default = "0.4.5"
}

variable "PHARMCAT_VERSION" {
  default = "3.4.0"
}

# The commit the images are built from, stamped into the app image so a running
# container can say whether the code it serves is the code it was built with
# (app/utils/build_identity.py). Set it on the release build:
#   ZAROPGX_COMMIT=$(git rev-parse HEAD) docker buildx bake -f docker-bake.hcl --push
variable "ZAROPGX_COMMIT" {
  default = "unknown"
}

# Every published image is linux/amd64, whatever machine cuts the release. Without this
# a bake builds for the builder's own platform: amd64 on the machine releases have been
# cut from, which is the only reason they have been right. Cut one on Apple Silicon and
# arm64 images would go out under the same tags while every install's compose.yml asks
# for linux/amd64 (installation.md, "the published images are linux/amd64"). On an arm64
# builder this cross-builds, which wants QEMU.
target "_release" {
  platforms = ["linux/amd64"]
}

group "default" {
  targets = [
    "app",
    "pharmcat",
    "nextflow",
    "pypgx",
    "gatk-api",
    "genome-downloader",
    "zarohla",
  ]
}

target "app" {
  inherits   = ["_release"]
  context    = "."
  dockerfile = "Dockerfile"
  args = {
    ZAROPGX_TAG    = ZAROPGX_TAG
    ZAROPGX_COMMIT = ZAROPGX_COMMIT
  }
  tags       = ["zaromicsresearch/zaropgx-app:${ZAROPGX_TAG}"]
  cache-from = ["type=gha,scope=zaropgx-app"]
  cache-to   = ["type=gha,mode=max,scope=zaropgx-app"]
}

target "pharmcat" {
  inherits   = ["_release"]
  context    = "."
  dockerfile = "docker/pharmcat/Dockerfile"
  args = {
    PHARMCAT_VERSION = PHARMCAT_VERSION
  }
  tags       = ["zaromicsresearch/zaropgx-pharmcat:${ZAROPGX_TAG}"]
  cache-from = ["type=gha,scope=zaropgx-pharmcat"]
  cache-to   = ["type=gha,mode=max,scope=zaropgx-pharmcat"]
}

target "nextflow" {
  inherits   = ["_release"]
  context    = "."
  dockerfile = "docker/nextflow/Dockerfile.nextflow"
  tags       = ["zaromicsresearch/zaropgx-nextflow:${ZAROPGX_TAG}"]
  cache-from = ["type=gha,scope=zaropgx-nextflow"]
  cache-to   = ["type=gha,mode=max,scope=zaropgx-nextflow"]
}

target "pypgx" {
  inherits   = ["_release"]
  context    = "."
  dockerfile = "docker/pypgx/Dockerfile.pypgx"
  tags       = ["zaromicsresearch/zaropgx-pypgx:${ZAROPGX_TAG}"]
  cache-from = ["type=gha,scope=zaropgx-pypgx"]
  cache-to   = ["type=gha,mode=max,scope=zaropgx-pypgx"]
}

target "gatk-api" {
  inherits   = ["_release"]
  context    = "."
  dockerfile = "docker/gatk-api/Dockerfile.gatk-api"
  tags       = ["zaromicsresearch/zaropgx-gatk-api:${ZAROPGX_TAG}"]
  cache-from = ["type=gha,scope=zaropgx-gatk-api"]
  cache-to   = ["type=gha,mode=max,scope=zaropgx-gatk-api"]
}

target "genome-downloader" {
  inherits   = ["_release"]
  context    = "./docker/genome-downloader"
  dockerfile = "Dockerfile.downloader"
  tags       = ["zaromicsresearch/zaropgx-genome-downloader:${ZAROPGX_TAG}"]
  cache-from = ["type=gha,scope=zaropgx-genome-downloader"]
  cache-to   = ["type=gha,mode=max,scope=zaropgx-genome-downloader"]
}

target "zarohla" {
  inherits   = ["_release"]
  context    = "."
  dockerfile = "docker/zarohla/Dockerfile"
  tags       = ["zaromicsresearch/zaropgx-zarohla:${ZAROPGX_TAG}"]
  cache-from = ["type=gha,scope=zaropgx-zarohla"]
  cache-to   = ["type=gha,mode=max,scope=zaropgx-zarohla"]
}
