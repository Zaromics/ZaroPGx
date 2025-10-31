# PowerShell Docker startup script for ZaroPGx
# Works in PowerShell on Windows (including with Docker Desktop)

param(
    [switch]$AutoLocal  # Automatically use .env.local without prompting
)

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

# Check for .env file and create from template if needed
if (-not (Test-Path ".env")) {
    if ($AutoLocal) {
        # Auto-select .env.local for bootstrap one-command installation
        Write-Host "  Setting up local development environment..." -ForegroundColor Yellow
        $envSource = ".env.local"
    } else {
        # Interactive selection for manual installation
        Write-Host "  No .env file found. Choose a template:" -ForegroundColor Yellow
        Write-Host "    1) .env.local      (Recommended for personal/home use)" -ForegroundColor Cyan
        Write-Host "    2) .env.production (For web-facing deployment)" -ForegroundColor Cyan
        Write-Host "    3) .env.example    (Complete configuration with documentation)" -ForegroundColor Cyan
        Write-Host "    4) Skip             (Use inline defaults - not recommended)" -ForegroundColor Gray
        Write-Host ""
        
        $envChoice = Read-Host "Select option [1-4]"
        
        $envSource = $null
        switch ($envChoice) {
            "1" { $envSource = ".env.local" }
            "2" { $envSource = ".env.production" }
            "3" { $envSource = ".env.example" }
            "4" { 
                Write-Host "  Skipping .env creation. Using inline defaults." -ForegroundColor Yellow
                Write-Host "  Note: Some features may require environment configuration" -ForegroundColor Gray
            }
            default { $envSource = ".env.local" }
        }
    }
    
    if ($envSource -and (Test-Path $envSource)) {
        Copy-Item $envSource ".env"
        Write-Host "  [OK] Created .env from $envSource" -ForegroundColor Green
        if (-not $AutoLocal) {
            Write-Host "  Note: Review and customize .env as needed (especially SECRET_KEY)" -ForegroundColor Gray
        }
    } elseif ($envSource) {
        Write-Host "  [WARNING] $envSource not found, using inline defaults" -ForegroundColor Yellow
    }
    Write-Host ""
} else {
    Write-Host "  [OK] Environment configuration found (.env)" -ForegroundColor Gray
}

# Check for docker-compose.yml and create from example if needed
if (-not (Test-Path "docker-compose.yml") -and -not (Test-Path "compose.yml")) {
    if (Test-Path "docker-compose.yml.example") {
        Write-Host "  Creating docker-compose.yml from example..." -ForegroundColor Yellow
        Copy-Item "docker-compose.yml.example" "docker-compose.yml"
        Write-Host "  [OK] Created docker-compose.yml" -ForegroundColor Green
        Write-Host "  Note: Review and customize docker-compose.yml if needed" -ForegroundColor Gray
    } else {
        Write-Host "  [ERROR] No docker-compose.yml or docker-compose.yml.example found!" -ForegroundColor Red
        if ($didPush) { Pop-Location }
        exit 1
    }
} else {
    Write-Host "  [OK] Docker Compose configuration found" -ForegroundColor Gray
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

# Check WSL version if on Windows (Docker Desktop requires WSL 2.1.5+)
if ($IsWindows -or $env:OS -eq "Windows_NT") {
    try {
        $wslVersionOutput = wsl --version 2>&1 | Out-String
        # Remove null characters that cause spacing issues in UTF-16 output
        $wslVersionOutput = $wslVersionOutput -replace '\0', ''
        if ($LASTEXITCODE -eq 0 -and $wslVersionOutput -match "WSL\s*version:\s*(\d+\.\d+\.\d+(?:\.\d+)?)") {
            $wslVersion = [version]$matches[1]
            $minRequiredVersion = [version]"2.1.5"
            if ($wslVersion -lt $minRequiredVersion) {
                Write-Host "  [WARNING] WSL version $wslVersion detected (Docker requires 2.1.5+)" -ForegroundColor Yellow
                Write-Host "  Docker Desktop may fail to start or show errors" -ForegroundColor Yellow
                Write-Host ""
                $updateResponse = Read-Host "Update WSL now? (Y/n)"
                if ($updateResponse -notmatch '^[Nn]') {
                    Write-Host "  Updating WSL..." -ForegroundColor Cyan
                    wsl --update
                    Write-Host "  WSL update complete. Please restart your terminal if needed." -ForegroundColor Green
                    Write-Host ""
                }
            }
        }
    } catch {
        # WSL version check failed, continue anyway
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
    $serviceRunning = $false
    
    # Check if Docker service is running
    try {
        $svc = Get-Service -Name "com.docker.service" -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -eq "Running") {
            $serviceRunning = $true
            Write-Host "  Docker service is running" -ForegroundColor Gray
        } elseif ($svc) {
            # Try to start the service
            Start-Service -Name "com.docker.service" -ErrorAction Stop
            $serviceRunning = $true
            $started = $true
            Write-Host "  Started Docker service" -ForegroundColor Gray
        }
    } catch {
        # Service start failed (likely needs admin or doesn't exist)
        Write-Host "  Could not start Docker service (trying executable method)..." -ForegroundColor Gray
    }

    # Check if Docker Desktop is already running
    $dockerDesktopRunning = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue
    if ($dockerDesktopRunning) {
        Write-Host "  Docker Desktop process is already running (PID: $($dockerDesktopRunning.Id))" -ForegroundColor Gray
        $started = $true
    } else {
        # Try starting Docker Desktop.exe directly
        $candidates = @(
            "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
            "$env:ProgramFiles(x86)\Docker\Docker\Docker Desktop.exe"
        )
        $exeFound = $false
        foreach ($p in $candidates) {
            if (Test-Path $p) {
                Write-Host "  Starting Docker Desktop from: $p" -ForegroundColor Cyan
                try {
                    Start-Process -FilePath $p -ErrorAction Stop
                    $exeFound = $true
                    $started = $true
                    Write-Host "  [OK] Docker Desktop process started" -ForegroundColor Gray
                } catch {
                    Write-Host "  [WARNING] Failed to start Docker Desktop: $($_.Exception.Message)" -ForegroundColor Yellow
                }
                break
            }
        }
        
        if (-not $exeFound) {
            Write-Host "  [WARNING] Docker Desktop executable not found in standard locations" -ForegroundColor Yellow
            Write-Host "  Searched:" -ForegroundColor Gray
            foreach ($p in $candidates) {
                Write-Host "    - $p" -ForegroundColor Gray
            }
        }
    }
    
    if (-not $started -and -not $serviceRunning) {
        Write-Host "  [WARNING] Could not start Docker Desktop automatically" -ForegroundColor Yellow
        Write-Host "  Please start Docker Desktop manually and wait for it to be ready" -ForegroundColor Yellow
    }

    # Wait up to 180s for Docker to become ready
    if ($started -or $serviceRunning) {
        Write-Host "  Waiting for Docker Desktop to be ready..." -ForegroundColor Gray
        Write-Host "  This may take up to 3 minutes, especially on first start..." -ForegroundColor Gray
    }
    $timeoutSec = 180
    $elapsed = 0
    $dotCount = 0
    while (-not $dockerOk -and $elapsed -lt $timeoutSec) {
        Start-Sleep -Seconds 3
        $elapsed += 3
        $dotCount++
        Write-Host "." -NoNewline -ForegroundColor Gray
        
        # Use docker info instead of docker version - more reliable for checking daemon readiness
        try { 
            docker info 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { 
                $dockerOk = $true 
            }
        } catch {}
        
        # Show elapsed time every 30 seconds
        if ($dotCount % 10 -eq 0 -and -not $dockerOk) {
            Write-Host " ($elapsed seconds)" -ForegroundColor Gray
            Write-Host "  Still waiting" -NoNewline -ForegroundColor Gray
        }
    }
    Write-Host ""  # New line after progress dots

    if ($dockerOk) { 
        Write-Host "  [OK] Docker Desktop is running" -ForegroundColor Green 
    } else { 
        Write-Host "  [ERROR] Docker Desktop did not become ready within $timeoutSec seconds" -ForegroundColor Red
        Write-Host ""
        Write-Host "Troubleshooting steps:" -ForegroundColor Yellow
        Write-Host "  1. Check if Docker Desktop is running (system tray whale icon)" -ForegroundColor Cyan
        Write-Host "  2. Try starting Docker Desktop manually from the Start menu" -ForegroundColor Cyan
        Write-Host "  3. Check Docker Desktop logs at: $env:LOCALAPPDATA\Docker\log" -ForegroundColor Cyan
        Write-Host "  4. Ensure WSL 2 is properly configured: wsl --list --verbose" -ForegroundColor Cyan
        Write-Host "  5. Try restarting the Docker service: Restart-Service com.docker.service" -ForegroundColor Cyan
        Write-Host "  6. After Docker Desktop is running, re-run this script" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "For more help, run Docker Desktop diagnostics:" -ForegroundColor Gray
        Write-Host '  & "C:\Program Files\Docker\Docker\resources\com.docker.diagnose.exe" gather' -ForegroundColor Gray
        Write-Host ""
        if ($didPush) { Pop-Location }
        exit 1
    }
}

# At this point, Docker should be running
if (-not $dockerOk) {
    Write-Host "  [ERROR] Docker is not running. Cannot start containers." -ForegroundColor Red
    Write-Host "  Please start Docker Desktop manually and try again." -ForegroundColor Yellow
    if ($didPush) { Pop-Location }
    exit 1
}

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