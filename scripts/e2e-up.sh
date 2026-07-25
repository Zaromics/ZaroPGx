#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

"$ROOT/scripts/e2e-env.sh"

export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

COMPOSE=(docker compose --env-file .env.e2e -p zaropgx_e2e)

SERVICES=(db app pharmcat nextflow pypgx gatk-api genome-downloader zarohla kroki)

# Bake/compose image tag + PharmCAT arg (compose.yml defaults if unset).
ZAROPGX_TAG="${ZAROPGX_TAG:-0.2.8}"
PHARMCAT_VERSION="${PHARMCAT_VERSION:-3.4.0}"
if [[ -f .env.e2e ]]; then
  _tag="$(grep -E '^ZAROPGX_TAG=' .env.e2e | head -n1 | cut -d= -f2- || true)"
  _pcv="$(grep -E '^PHARMCAT_VERSION=' .env.e2e | head -n1 | cut -d= -f2- || true)"
  [[ -n "${_tag}" ]] && ZAROPGX_TAG="${_tag}"
  [[ -n "${_pcv}" ]] && PHARMCAT_VERSION="${_pcv}"
fi
export ZAROPGX_TAG PHARMCAT_VERSION

if [[ "${CI:-}" == "true" ]]; then
  export BUILDX_NO_DEFAULT_ATTESTATIONS=1
  # Prefer builder from docker/setup-buildx-action; create only if none is usable.
  if ! docker buildx inspect >/dev/null 2>&1; then
    docker buildx create --use --name zaropgx-e2e || docker buildx use zaropgx-e2e
  fi
  echo "Building e2e images via buildx bake (GHA cache)..."
  docker buildx bake --load -f docker-bake.hcl
  echo "Starting pinned services (no rebuild): ${SERVICES[*]}"
  "${COMPOSE[@]}" up -d --no-build "${SERVICES[@]}"
else
  echo "Building e2e images..."
  "${COMPOSE[@]}" build
  echo "Starting pinned services: ${SERVICES[*]}"
  "${COMPOSE[@]}" up -d "${SERVICES[@]}"
fi

HEALTH_URL="http://127.0.0.1:18765/health"
TIMEOUT="${E2E_HEALTH_TIMEOUT:-1500}"
start_ts=$(date +%s)

echo "Waiting for ${HEALTH_URL} (timeout ${TIMEOUT}s)..."
while (( $(date +%s) - start_ts < TIMEOUT )); do
  if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
    echo "Stack healthy at ${HEALTH_URL}"
    exit 0
  fi
  sleep 5
done

echo "Health check failed after ${TIMEOUT}s"
"${COMPOSE[@]}" ps
exit 1
