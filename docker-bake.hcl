# Bake targets aligned with compose.yml buildable services used by e2e.
# Keep contexts/dockerfiles/args in sync with compose.yml when those change.
# Pre-built Hub images (db, kroki, …) are not baked — compose pulls them.

variable "ZAROPGX_TAG" {
  default = "0.2.8"
}

variable "PHARMCAT_VERSION" {
  default = "3.4.0"
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
  context    = "."
  dockerfile = "Dockerfile"
  tags       = ["zaromicsresearch/zaropgx-app:${ZAROPGX_TAG}"]
  cache-from = ["type=gha,scope=zaropgx-app"]
  cache-to   = ["type=gha,mode=max,scope=zaropgx-app"]
}

target "pharmcat" {
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
  context    = "."
  dockerfile = "docker/nextflow/Dockerfile.nextflow"
  tags       = ["zaromicsresearch/zaropgx-nextflow:${ZAROPGX_TAG}"]
  cache-from = ["type=gha,scope=zaropgx-nextflow"]
  cache-to   = ["type=gha,mode=max,scope=zaropgx-nextflow"]
}

target "pypgx" {
  context    = "."
  dockerfile = "docker/pypgx/Dockerfile.pypgx"
  tags       = ["zaromicsresearch/zaropgx-pypgx:${ZAROPGX_TAG}"]
  cache-from = ["type=gha,scope=zaropgx-pypgx"]
  cache-to   = ["type=gha,mode=max,scope=zaropgx-pypgx"]
}

target "gatk-api" {
  context    = "."
  dockerfile = "docker/gatk-api/Dockerfile.gatk-api"
  tags       = ["zaromicsresearch/zaropgx-gatk-api:${ZAROPGX_TAG}"]
  cache-from = ["type=gha,scope=zaropgx-gatk-api"]
  cache-to   = ["type=gha,mode=max,scope=zaropgx-gatk-api"]
}

target "genome-downloader" {
  context    = "./docker/genome-downloader"
  dockerfile = "Dockerfile.downloader"
  tags       = ["zaromicsresearch/zaropgx-genome-downloader:${ZAROPGX_TAG}"]
  cache-from = ["type=gha,scope=zaropgx-genome-downloader"]
  cache-to   = ["type=gha,mode=max,scope=zaropgx-genome-downloader"]
}

target "zarohla" {
  context    = "."
  dockerfile = "docker/zarohla/Dockerfile"
  tags       = ["zaromicsresearch/zaropgx-zarohla:${ZAROPGX_TAG}"]
  cache-from = ["type=gha,scope=zaropgx-zarohla"]
  cache-to   = ["type=gha,mode=max,scope=zaropgx-zarohla"]
}
