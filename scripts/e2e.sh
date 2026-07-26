#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
./scripts/e2e-up.sh
export ZAROPGX_E2E=1
export ZAROPGX_E2E_BASE_URL="${ZAROPGX_E2E_BASE_URL:-http://127.0.0.1:18765}"

# Prefer project venv pytest (avoids uv re-building pysam on Windows).
if [[ -x "$ROOT/.venv/Scripts/python.exe" ]]; then
  PYTEST=("$ROOT/.venv/Scripts/python.exe" -m pytest)
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTEST=("$ROOT/.venv/bin/python" -m pytest)
else
  PYTEST=(uv run pytest)
fi

set +e
"${PYTEST[@]}" -m e2e -q --tb=short
status=$?
set -e
if [[ "$status" -eq 0 ]]; then
  ./scripts/e2e-down.sh
else
  mkdir -p e2e-logs
  docker compose --env-file .env.e2e -p zaropgx_e2e -f compose.yml -f compose.e2e.yml logs --no-color > e2e-logs/compose.log || true
  echo "E2E failed; stack left up for inspection. Logs: e2e-logs/compose.log"
fi
exit "$status"
