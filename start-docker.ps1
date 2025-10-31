# PowerShell Docker startup script for ZaroPGx
# Works in PowerShell on Windows (including with Docker Desktop)

Write-Host "Starting ZaroPGx with Docker Compose" -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Green

# Detect environment
$env:COMPOSE_PROJECT_NAME = "pgx"

if ($IsWindows -or $env:OS -eq "Windows_NT") {
    Write-Host "  Detected: Windows PowerShell environment" -ForegroundColor Cyan
} elseif ($IsLinux) {
    Write-Host "  Detected: Linux environment" -ForegroundColor Cyan
} else {
    Write-Host "  Unknown environment" -ForegroundColor Yellow
}

# Ensure we run from the repository root (so compose.yml and .env are discovered)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$didPush = $false
if ($scriptDir -and (Test-Path $scriptDir)) {
    Push-Location $scriptDir
    $didPush = $true
}

# Ensure data directories exist
Write-Host "  Creating data directories..." -ForegroundColor Yellow
$directories = @(
    "data/uploads",
    "data/reports", 
    "data/nextflow/work",
    "data/nextflow/assets",
    "reference"
)

foreach ($dir in $directories) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "  [OK] Created: $dir" -ForegroundColor Green
    } else {
        Write-Host "  [OK] Exists: $dir" -ForegroundColor Gray
    }
}

# Check Docker Desktop status and start it if needed (Windows)
Write-Host "  Checking Docker Desktop status..." -ForegroundColor Yellow
$dockerOk = $false
try {
    docker version 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
} catch {}

if (-not $dockerOk -and ($IsWindows -or $env:OS -eq "Windows_NT")) {
    Write-Host "  Docker is not running. Attempting to start Docker Desktop..." -ForegroundColor Yellow
    $started = $false
    try {
        $svc = Get-Service -Name "com.docker.service" -ErrorAction SilentlyContinue
        if ($svc) {
            if ($svc.Status -ne "Running") { Start-Service -Name "com.docker.service"; $started = $true } else { $started = $true }
        }
    } catch {}

    if (-not $started) {
        $candidates = @(
            "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
            "$env:ProgramFiles(x86)\Docker\Docker\Docker Desktop.exe"
        )
        foreach ($p in $candidates) {
            if (Test-Path $p) {
                Start-Process -FilePath $p | Out-Null
                $started = $true
                break
            }
        }
    }

    # Wait up to 180s for Docker to become ready
    $timeoutSec = 180
    $elapsed = 0
    while (-not $dockerOk -and $elapsed -lt $timeoutSec) {
        Start-Sleep -Seconds 3
        $elapsed += 3
        try { docker version 2>&1 | Out-Null; if ($LASTEXITCODE -eq 0) { $dockerOk = $true } } catch {}
    }

    if ($dockerOk) { Write-Host "  [OK] Docker Desktop is running" -ForegroundColor Green }
    else { Write-Host "  [WARNING] Docker Desktop did not become ready within $timeoutSec seconds" -ForegroundColor Yellow }
}

# Inform about environment file usage
if (Test-Path ".env") { Write-Host "  Using environment file: .env" -ForegroundColor Gray } else { Write-Host "  No .env found; using defaults and inline environment" -ForegroundColor Yellow }

# Start containers
Write-Host "  Starting ZaroPGx Docker Compose containers..." -ForegroundColor Yellow
Write-Host "  Stopping existing containers..." -ForegroundColor Gray
docker compose down --remove-orphans

Write-Host "  Building and starting containers..." -ForegroundColor Gray
docker compose up -d --build

if ($LASTEXITCODE -ne 0) {
    Write-Host "  Docker Compose failed to start containers" -ForegroundColor Red
    if ($didPush) { Pop-Location }
    exit 1
}

# Wait for services to be ready
Write-Host "  Waiting for services to start..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

# Check container status
Write-Host "  Container Status:" -ForegroundColor Cyan
docker compose ps

# Test the app health endpoint
Write-Host "  Testing app health endpoint..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

# Test with Invoke-WebRequest (PowerShell equivalent of curl)
try {
    Write-Host "Testing GET /health on http://localhost:8765..." -ForegroundColor Gray
    $response = Invoke-WebRequest -Uri "http://localhost:8765/health" -TimeoutSec 10 -ErrorAction Stop
    if ($response.StatusCode -eq 200) {
        Write-Host "[SUCCESS] Health check passed!" -ForegroundColor Green
    } else {
        Write-Host "[WARNING] Health check returned status: $($response.StatusCode)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[ERROR] Health check failed (this is expected if app is still starting)" -ForegroundColor Yellow
    Write-Host "   Error: $($_.Exception.Message)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "[SUCCESS] ZaroPGx in Docker environment is started!" -ForegroundColor Green
Write-Host ">> Web interface: http://localhost:8765" -ForegroundColor Cyan
Write-Host ">> Container status: docker compose ps" -ForegroundColor Cyan
Write-Host ">> Logs: docker compose logs -f" -ForegroundColor Cyan
Write-Host ""
Write-Host ">> If you see issues, try:" -ForegroundColor Yellow
Write-Host `
"   docker compose down; docker compose build --no-cache; docker compose up -d --force-recreate" `
-ForegroundColor Gray

if ($didPush) { Pop-Location }
exit 0