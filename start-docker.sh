#!/bin/bash
# Cross-platform Docker startup script
# Works in both WSL and PowerShell (when run with bash)

echo "🚀 Starting ZaroPGx Docker Environment"
echo "======================================"

# Detect environment
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    echo "📱 Detected: Windows environment"
    # PowerShell/WSL hybrid
    export COMPOSE_PROJECT_NAME=pgx
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "🐧 Detected: Linux/WSL environment"
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
echo "🐳 Starting Docker containers..."
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
echo "✅ Docker environment started!"
echo "🌐 Web interface: http://localhost:8765"
echo "📊 Container status: docker compose ps"
echo "📝 Logs: docker compose logs -f"
echo ""
echo "🔧 If you see issues, try:"
echo "   docker compose down && docker compose up -d --build"
