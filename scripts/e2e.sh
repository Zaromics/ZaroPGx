#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
./scripts/e2e-up.sh
export ZAROPGX_E2E=1
export ZAROPGX_E2E_BASE_URL="${ZAROPGX_E2E_BASE_URL:-http://127.0.0.1:18765}"
set +e
uv run pytest -m e2e -q --tb=short
status=$?
set -e
if [[ "$status" -eq 0 ]]; then
  ./scripts/e2e-down.sh
else
  mkdir -p e2e-logs
  docker compose --env-file .env.e2e -p zaropgx_e2e logs --no-color > e2e-logs/compose.log || true
  echo "E2E failed; stack left up for inspection. Logs: e2e-logs/compose.log"
fi
exit "$status"
