#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env.e2e"
if [[ -f "$ENV_FILE" ]]; then
  echo "Using existing $ENV_FILE"
  exit 0
fi
DB_PASSWORD="$(openssl rand -hex 24)"
SECRET_KEY="$(openssl rand -hex 32)"
cat >"$ENV_FILE" <<EOF
SECRET_KEY=${SECRET_KEY}
DB_PASSWORD=${DB_PASSWORD}
DB_USER=zaropgx_user
DB_NAME=zaropgx_db
DB_HOST=db
DB_PORT=5432
ZAROPGX_AUTH_MODE=open
ZAROPGX_DEV_MODE=true
BIND_ADDRESS=127.0.0.1:18765
INTERNAL_BIND_ADDRESS=127.0.0.1
INCLUDE_PHARMCAT_HTML=true
INCLUDE_PHARMCAT_JSON=true
INCLUDE_PHARMCAT_TSV=true
EXECSUM_USE_TSV=true
LOG_LEVEL=INFO
EOF
echo "Wrote $ENV_FILE"
