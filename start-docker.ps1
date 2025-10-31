# PowerShell Docker startup script for ZaroPGx
# Works in PowerShell on Windows with automatic Docker detection
#
# Smart Docker Detection (tries in order):
#   1. Check if Docker is already running and accessible
#   2. Try to start Docker Desktop for current user
#   3. Fall back to Docker in WSL2 if Desktop fails
#   4. Minimal user intervention required
#
# Note: Docker Desktop runs per-user on Windows
#   - Each Windows user has their own Docker Desktop instance
#   - Cannot share Docker Desktop between Windows users
#   - WSL2 Docker Engine can be shared across all users
#
# Usage:
#   .\start-docker.ps1                # Interactive - prompts for environment
#   .\start-docker.ps1 -AutoLocal     # Automatic - uses .env.local
#
#   If execution policy error, run with:
#   powershell -ExecutionPolicy Bypass -File start-docker.ps1

param(
    [switch]$AutoLocal  # Automatically use .env.local without prompting
)

Write-Host "Starting ZaroPGx with Docker Compose" -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Green

# Check and set execution policy for this process (doesn't require admin)
$currentPolicy = Get-ExecutionPolicy -Scope Process
if ($currentPolicy -eq "Restricted" -or $currentPolicy -eq "AllSigned") {
    Write-Host "  Setting execution policy to Bypass for this session..." -ForegroundColor Yellow
    try {
        Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force
    } catch {
        Write-Host "  [WARNING] Could not set execution policy: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

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

# Function to check if Docker in WSL2 is available and configure it
function Test-DockerInWSL {
    Write-Host "  Checking for Docker in WSL2..." -ForegroundColor Cyan
    
    # Check if WSL is available
    $wslAvailable = Get-Command wsl -ErrorAction SilentlyContinue
    if (-not $wslAvailable) {
        Write-Host "    WSL not available" -ForegroundColor Gray
        return $false
    }
    
    # Check for Docker in default WSL distribution
    try {
        $dockerInWsl = wsl bash -c "command -v docker" 2>&1
        if ($LASTEXITCODE -eq 0 -and $dockerInWsl) {
            Write-Host "    [OK] Docker found in WSL2" -ForegroundColor Green
            
            # Check if Docker daemon is running
            $dockerStatus = wsl bash -c "docker info >/dev/null 2>&1 && echo 'running' || echo 'stopped'" 2>&1
            if ($dockerStatus -match "running") {
                Write-Host "    [OK] Docker daemon is running in WSL2" -ForegroundColor Green
                
                # Configure DOCKER_HOST to use WSL2
                $wslIp = wsl bash -c "hostname -I | awk '{print `$1}'" 2>&1
                if ($wslIp -and $wslIp -match "\d+\.\d+\.\d+\.\d+") {
                    $env:DOCKER_HOST = "tcp://${wslIp}:2375"
                    Write-Host "    Configured DOCKER_HOST=$env:DOCKER_HOST" -ForegroundColor Gray
                    
                    # Test connection
                    try {
                        docker info 2>&1 | Out-Null
                        if ($LASTEXITCODE -eq 0) {
                            Write-Host "    [OK] Successfully connected to Docker in WSL2" -ForegroundColor Green
                            return $true
                        }
                    } catch {}
                }
                
                Write-Host "    [WARNING] Docker in WSL2 not exposed to Windows" -ForegroundColor Yellow
                Write-Host "    To use Docker in WSL2 from Windows, you need to expose the daemon" -ForegroundColor Gray
                return $false
            } elseif ($dockerStatus -match "stopped") {
                Write-Host "    Docker daemon is not running in WSL2" -ForegroundColor Gray
                Write-Host "    Attempting to start Docker daemon..." -ForegroundColor Yellow
                
                # Try to start Docker daemon in WSL2
                wsl bash -c "sudo service docker start" 2>&1 | Out-Null
                Start-Sleep -Seconds 3
                
                # Check again
                $dockerStatus = wsl bash -c "docker info >/dev/null 2>&1 && echo 'running' || echo 'stopped'" 2>&1
                if ($dockerStatus -match "running") {
                    Write-Host "    [OK] Docker daemon started in WSL2" -ForegroundColor Green
                    return $true
                } else {
                    Write-Host "    [WARNING] Could not start Docker daemon in WSL2" -ForegroundColor Yellow
                    return $false
                }
            }
        }
    } catch {
        Write-Host "    Error checking Docker in WSL2: $($_.Exception.Message)" -ForegroundColor Gray
    }
    
    return $false
}

# Check Docker status and start if needed (Windows)
Write-Host "  Checking Docker status..." -ForegroundColor Yellow
$dockerOk = $false
$dockerSource = "Unknown"

# First, check if Docker is already accessible
try {
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { 
        $dockerOk = $true
        $dockerSource = "Already Running"
        Write-Host "  [OK] Docker is already running and accessible" -ForegroundColor Green
    }
} catch {}

# If Docker not accessible and on Windows, try to start it
if (-not $dockerOk -and ($IsWindows -or $env:OS -eq "Windows_NT")) {
    Write-Host "  Docker is not accessible. Trying Docker Desktop first..." -ForegroundColor Yellow
    
    # Try Docker Desktop for current user
    $dockerDesktopStarted = $false
    
    # Check if Docker Desktop is already running under another user
    $dockerDesktopRunning = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($dockerDesktopRunning) {
        $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        try {
            $processOwner = (Get-WmiObject Win32_Process -Filter "ProcessId=$($dockerDesktopRunning.Id)").GetOwner()
            $processUser = "$($processOwner.Domain)\$($processOwner.User)"
            
            if ($processUser -ne $currentUser) {
                Write-Host "  [WARNING] Docker Desktop is running under different user: $processUser" -ForegroundColor Yellow
                Write-Host "  Cannot use another user's Docker Desktop instance" -ForegroundColor Gray
            } else {
                Write-Host "  Docker Desktop is already running under current user" -ForegroundColor Gray
                $dockerDesktopStarted = $true
            }
        } catch {
            Write-Host "  Docker Desktop process found - attempting to connect..." -ForegroundColor Gray
            $dockerDesktopStarted = $true
        }
    }
    
    # If not running for current user, try to start it
    if (-not $dockerDesktopStarted) {
        $candidates = @(
            "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
            "$env:ProgramFiles(x86)\Docker\Docker\Docker Desktop.exe"
        )
        
        foreach ($p in $candidates) {
            if (Test-Path $p) {
                Write-Host "  Starting Docker Desktop: $p" -ForegroundColor Cyan
                try {
                    Start-Process -FilePath $p -ErrorAction Stop
                    $dockerDesktopStarted = $true
                    Write-Host "  [OK] Docker Desktop process started" -ForegroundColor Gray
                } catch {
                    Write-Host "  [WARNING] Failed to start: $($_.Exception.Message)" -ForegroundColor Yellow
                }
                break
            }
        }
    }
    
    # Wait for Docker Desktop if we started it
    if ($dockerDesktopStarted) {
        Write-Host "  Waiting for Docker Desktop to be ready (up to 180 seconds)..." -ForegroundColor Gray
        $timeoutSec = 180
        $elapsed = 0
        $dotCount = 0
        
        while (-not $dockerOk -and $elapsed -lt $timeoutSec) {
            Start-Sleep -Seconds 3
            $elapsed += 3
            $dotCount++
            Write-Host "." -NoNewline -ForegroundColor Gray
            
            try { 
                docker info 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) { 
                    $dockerOk = $true
                    $dockerSource = "Docker Desktop"
                }
            } catch {}
            
            if ($dotCount % 10 -eq 0 -and -not $dockerOk) {
                Write-Host " ($elapsed seconds)" -ForegroundColor Gray
                Write-Host "  Still waiting" -NoNewline -ForegroundColor Gray
            }
        }
        Write-Host ""
        
        if ($dockerOk) {
            Write-Host "  [OK] Docker Desktop is ready!" -ForegroundColor Green
        } else {
            Write-Host "  [WARNING] Docker Desktop did not become ready" -ForegroundColor Yellow
        }
    }
    
    # If Docker Desktop failed, try Docker in WSL2 as fallback
    if (-not $dockerOk) {
        Write-Host ""
        Write-Host "  Docker Desktop not available. Trying Docker in WSL2..." -ForegroundColor Yellow
        
        if (Test-DockerInWSL) {
            $dockerOk = $true
            $dockerSource = "Docker in WSL2"
        }
    }
    
    # Final check: if nothing worked, exit with error
    if (-not $dockerOk) {
        Write-Host ""
        Write-Host "  [ERROR] Could not start or connect to Docker" -ForegroundColor Red
        Write-Host ""
        Write-Host "Attempted methods:" -ForegroundColor Yellow
        Write-Host "  1. Docker Desktop for current user - Failed" -ForegroundColor Gray
        Write-Host "  2. Docker in WSL2 - Not available or not configured" -ForegroundColor Gray
        Write-Host ""
        Write-Host "Solutions:" -ForegroundColor Yellow
        Write-Host "  1. Install Docker Desktop: https://www.docker.com/products/docker-desktop" -ForegroundColor Cyan
        Write-Host "  2. Or set up Docker in WSL2 (see documentation)" -ForegroundColor Cyan
        Write-Host "  3. Check Docker Desktop logs: $env:LOCALAPPDATA\Docker\log" -ForegroundColor Cyan
        Write-Host ""
        if ($didPush) { Pop-Location }
        exit 1
    }
}

Write-Host "  [OK] Using Docker from: $dockerSource" -ForegroundColor Green
Write-Host ""

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