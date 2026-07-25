#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
docker compose --env-file .env.e2e -p zaropgx_e2e down -v --remove-orphans || true
