#!/bin/bash
# Cross-platform Docker startup script
# Works in WSL and when run with bash from PowerShell
# For native PowerShell support, use start-docker.ps1 instead

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
docker compose up -d --build

# Wait for services to be ready
echo "⏳ Waiting for services to start..."
sleep 10

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