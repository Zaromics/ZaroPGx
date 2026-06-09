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
        echo "   4) Skip            (Use inline defaults - not recommended)"
        echo ""
        read -p "Select option [1-4]: " env_choice
        
        env_source=""
        case "$env_choice" in
            1) env_source=".env.local" ;;
            2) env_source=".env.production" ;;
            3) env_source=".env.example" ;;
            4) 
                echo "⚠️  Skipping .env creation. Using inline defaults."
                echo "ℹ️  Note: Some features may require environment configuration"
                ;;
            *) env_source=".env.local" ;;
        esac
    fi
    
    if [[ -n "$env_source" ]] && [[ -f "$env_source" ]]; then
        cp "$env_source" ".env"
        echo "✅ Created .env from $env_source"
        if [[ "$AUTO_LOCAL" != "true" ]]; then
            echo "ℹ️  Note: Review and customize .env as needed (especially SECRET_KEY)"
        fi
    elif [[ -n "$env_source" ]]; then
        echo "⚠️  WARNING: $env_source not found, using inline defaults"
    fi
    echo ""
else
    echo "✅ Environment configuration found (.env)"
fi

# Check for docker-compose.yml and create from example if needed
if [[ ! -f "docker-compose.yml" ]] && [[ ! -f "compose.yml" ]]; then
    if [[ -f "docker-compose.yml.example" ]]; then
        echo "📝 Creating docker-compose.yml from example..."
        cp docker-compose.yml.example docker-compose.yml
        echo "✅ Created docker-compose.yml"
        echo "ℹ️  Note: Review and customize docker-compose.yml if needed"
    else
        echo "❌ ERROR: No docker-compose.yml or docker-compose.yml.example found!"
        exit 1
    fi
else
    echo "✅ Docker Compose configuration found"
fi

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