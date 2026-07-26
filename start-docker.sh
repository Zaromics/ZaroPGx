#!/bin/bash
# Cross-platform Docker startup script
# Works in WSL and when run with bash from PowerShell
# For native PowerShell support, use start-docker.ps1 instead

# Parse command line arguments
AUTO_LOCAL=false
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --auto-local) AUTO_LOCAL=true ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

echo "🚀 Starting ZaroPGx with Docker Compose"
echo "======================================"

# Detect environment
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    echo "📱 Detected: Windows environment"
    # PowerShell/WSL hybrid
    export COMPOSE_PROJECT_NAME=pgx
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "🐧 Detected: Linux or WSL environment"
    # Pure WSL
    export COMPOSE_PROJECT_NAME=pgx
else
    echo "❓ Unknown environment: $OSTYPE"
    exit 1
fi

# Rewrite KEY=value in .env in place (create the line if missing).
_set_env_var() {
    local key="$1"
    local value="$2"
    local tmp
    tmp="$(mktemp)"
    if [[ -f ".env" ]] && grep -qE "^${key}=" ".env"; then
        # Avoid sed -i portability issues between GNU and BSD sed.
        awk -v k="$key" -v v="$value" '
            BEGIN { done = 0 }
            $0 ~ ("^" k "=") { print k "=" v; done = 1; next }
            { print }
            END { if (!done) print k "=" v }
        ' ".env" >"$tmp" && mv "$tmp" ".env"
    else
        printf '%s=%s\n' "$key" "$value" >>".env"
        rm -f "$tmp"
    fi
}

_env_value() {
    local key="$1"
    local line
    line="$(grep -E "^${key}=" ".env" 2>/dev/null | tail -n 1 || true)"
    printf '%s' "${line#${key}=}"
}

_is_secret_sentinel() {
    case "$1" in
        ""|change_me|change_me_in_production|supersecretkey|supersecretkey_for_development|test123)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

_pgdata_volume_exists() {
    # compose.yml sets name: zaropgx; older start scripts also exported
    # COMPOSE_PROJECT_NAME=pgx. Check both so we never rotate an existing volume.
    docker volume inspect zaropgx_pgdata >/dev/null 2>&1 \
        || docker volume inspect pgx_pgdata >/dev/null 2>&1
}

_ensure_install_secrets() {
    if [[ ! -f ".env" ]]; then
        echo "❌ ERROR: .env is required. DB_PASSWORD has no compose default."
        echo "   Re-run and pick a template, or: cp .env.local .env && ./start-docker.sh"
        exit 1
    fi

    local secret_key db_password
    secret_key="$(_env_value SECRET_KEY)"
    if _is_secret_sentinel "$secret_key"; then
        secret_key="$(openssl rand -hex 32)"
        _set_env_var SECRET_KEY "$secret_key"
        echo "🔐 Generated a per-install SECRET_KEY in .env"
    fi

    db_password="$(_env_value DB_PASSWORD)"
    if _is_secret_sentinel "$db_password"; then
        if _pgdata_volume_exists; then
            echo "⚠️  DB_PASSWORD in .env is empty or a known placeholder, but a Postgres"
            echo "   data volume already exists. Postgres only honours POSTGRES_PASSWORD on"
            echo "   first init, so this script will not rotate it (that would brick the stack)."
            echo "   Put the password that initialized the volume into .env, or rotate with:"
            echo "     docker compose exec -T db psql -U zaropgx_user -d zaropgx_db \\"
            echo "       -c \"ALTER USER zaropgx_user WITH PASSWORD '...';\""
            exit 1
        fi
        db_password="$(openssl rand -hex 24)"
        _set_env_var DB_PASSWORD "$db_password"
        echo "🔐 Generated a per-install DB_PASSWORD in .env (fresh volume)"
    fi
}

# Check for .env file and create from template if needed
if [[ ! -f ".env" ]]; then
    if [[ "$AUTO_LOCAL" == "true" ]]; then
        # Auto-select .env.local for bootstrap one-command installation
        echo "📝 Setting up local development environment..."
        env_source=".env.local"
    else
        # Interactive selection for manual installation
        echo "📝 No .env file found. Choose a template:"
        echo "   1) .env.local      (Recommended for personal/home use)"
        echo "   2) .env.production (For web-facing deployment)"
        echo "   3) .env.example    (Complete configuration with documentation)"
        echo "   4) Abort           (compose now requires DB_PASSWORD in .env)"
        echo ""
        read -p "Select option [1-4]: " env_choice
        
        env_source=""
        case "$env_choice" in
            1) env_source=".env.local" ;;
            2) env_source=".env.production" ;;
            3) env_source=".env.example" ;;
            4) 
                echo "❌ Aborted. Copy a template to .env, then re-run."
                exit 1
                ;;
            *) env_source=".env.local" ;;
        esac
    fi
    
    if [[ -n "$env_source" ]] && [[ -f "$env_source" ]]; then
        cp "$env_source" ".env"
        echo "✅ Created .env from $env_source"
    elif [[ -n "$env_source" ]]; then
        echo "❌ ERROR: $env_source not found"
        exit 1
    fi
    echo ""
else
    echo "✅ Environment configuration found (.env)"
fi

_ensure_install_secrets
echo ""

# compose.yml is tracked in git, so it arrives and updates with `git pull` rather than
# being copied once and then frozen forever. Put local customization in
# compose.override.yml, which Compose merges automatically with no extra flags.
if [[ ! -f "compose.yml" ]]; then
    echo "❌ ERROR: compose.yml not found. Run this from the repository root."
    exit 1
fi
if [[ -f "docker-compose.yml" ]]; then
    # Compose prefers compose.yml over docker-compose.yml, so a leftover file from the
    # old copy-the-example flow is now silently ignored along with any edits in it.
    echo "⚠️  A legacy docker-compose.yml is present and is NO LONGER USED."
    echo "   compose.yml (tracked) takes precedence. If you customized the old file:"
    echo "     mv docker-compose.yml compose.override.yml"
    echo "   and trim it to only the settings you actually changed."
fi
echo "✅ Docker Compose configuration found"

# Ensure data directories exist
echo "📁 Creating data directories..."
mkdir -p data/uploads
mkdir -p data/reports
mkdir -p data/nextflow/work
mkdir -p data/nextflow/assets
mkdir -p reference

# Set proper permissions (important for WSL)
echo "🔐 Setting permissions..."
chmod -R 755 data/
chmod -R 755 reference/

# Start containers
echo "🐳 Starting ZaroPGx Docker Compose containers..."
docker compose down --remove-orphans
# Published images by default: pull pre-built images from Docker Hub.
# Build-only services (no published image) are skipped here and built on `up`.
# To build everything locally instead, run: docker compose build
docker compose pull
docker compose up -d

# Wait for app ready state by watching logs
echo "⏳ Waiting for ZaroPGx to be ready (up to 5 minutes)..."

timeout=300
start_ts=$(date +%s)
spin='|/-\'
i=0
ready=0

while (( $(date +%s) - start_ts < timeout )); do
  if docker compose logs --no-color app | grep -q "ZaroPGx is ready and listening for requests!"; then
    echo ""
    echo "✅ ZaroPGx is ready!"
    ready=1
    break
  fi
  printf "\r  Launching... %s" "${spin:i++%${#spin}:1}"
  sleep 2
done
echo ""

if [[ "$ready" != "1" ]]; then
  echo "⚠️  App did not report ready within timeout. Continuing anyway."
fi

# Check container status
echo "📊 Container Status:"
docker compose ps

# Test the app health endpoint
echo "🧪 Testing app health endpoint..."
sleep 5

# Test with curl if available
if command -v curl &> /dev/null; then
    echo "Testing GET /health on http://localhost:8765..."
    curl -f http://localhost:8765/health \
      --connect-timeout 5 --max-time 10 || echo "❌ Health check failed (this is expected if app is still starting)"
else
    echo "ℹ️  curl not available, skipping endpoint test"
fi

echo ""
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  ✅ ZaroPGx setup is complete! Containers are starting..."
echo "════════════════════════════════════════════════════════════════"
echo "Click the link below: -------\ "
echo "🌐 Web interface: http://localhost:8765"
echo "📊 Check status:     docker compose ps"
echo "📝 View logs:        docker compose logs -f"
echo "🔄 Restart:          docker compose restart"
echo "🛑 Stop:             docker compose down"
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  🔧 Troubleshooting"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "If something's not working, try these steps IN ORDER:"
echo ""
echo "1️⃣  Quick restart (fixes most issues):"
echo "    docker compose restart"
echo ""
echo "2️⃣  Clean rebuild (if restart didn't work):"
echo "    docker compose down"
echo "    docker compose build --no-cache"
echo "    docker compose up -d"
echo ""
echo "3️⃣  Full reset (removes volumes - YOUR DATA WILL BE DELETED):"
echo "    docker compose down -v"
echo "    docker compose build --no-cache"
echo "    docker compose up -d"
echo ""
echo "4️⃣  Nuclear option (only if you have NO other Docker projects):"
echo "    ⚠️  WARNING: This affects ALL Docker containers on your system!"
echo "    "
echo "    # Stop everything"
echo "    docker stop \$(docker ps -aq)"
echo "    "
echo "    # Remove stopped containers"
echo "    docker container prune -f"
echo "    "
echo "    # Remove unused networks"
echo "    docker network prune -f"
echo "    "
echo "    # Remove zaropgx's volumes only"
echo "    docker compose down -v"
echo "    "
echo "    # Rebuild and start"
echo "    docker compose build --no-cache"
echo "    docker compose up -d"
echo ""
echo "════════════════════════════════════════════════════════════════"
echo ""