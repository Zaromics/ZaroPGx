#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

"$ROOT/scripts/e2e-env.sh"

export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

COMPOSE=(docker compose --env-file .env.e2e -p zaropgx_e2e)

SERVICES=(db app pharmcat nextflow pypgx gatk-api genome-downloader zarohla kroki)

echo "Building e2e images..."
"${COMPOSE[@]}" build

echo "Starting pinned services: ${SERVICES[*]}"
"${COMPOSE[@]}" up -d "${SERVICES[@]}"

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
