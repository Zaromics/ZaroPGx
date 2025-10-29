# PowerShell bootstrap script for ZaroPGx
# Clones (or optionally updates) the repository and launches the startup script

[CmdletBinding()]
param(
    [string]$RepoUrl = "https://github.com/Zaroganos/ZaroPGx.git",
    [string]$Branch = "main",
    [string]$TargetDir = "ZaroPGx",
    [switch]$Update,
    [switch]$SkipDependencyCheck,
    [string[]]$MissingDeps = @()
)

Write-Host "ZaroPGx bootstrap" -ForegroundColor Green
Write-Host "==================" -ForegroundColor Green

# Function to check if running as administrator
function Test-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Function to install dependencies using available package manager
function Install-Dependencies {
    param([string[]]$MissingDeps)
    
    Write-Host ""
    Write-Host "Missing dependencies detected: $($MissingDeps -join ', ')" -ForegroundColor Yellow
    Write-Host ""
    
    # Check for winget (Windows Package Manager)
    $wingetAvailable = Get-Command winget -ErrorAction SilentlyContinue
    $chocoAvailable = Get-Command choco -ErrorAction SilentlyContinue
    
    if (-not $wingetAvailable -and -not $chocoAvailable) {
        Write-Host "No package manager found (winget or chocolatey)." -ForegroundColor Red
        Write-Host ""
        Write-Host "Please install dependencies manually:" -ForegroundColor Yellow
        Write-Host "  Git:            https://git-scm.com/downloads" -ForegroundColor Cyan
        Write-Host "  Docker Desktop: https://www.docker.com/products/docker-desktop" -ForegroundColor Cyan
        Write-Host ""
        return $false
    }
    
    $response = Read-Host "Would you like to automatically install missing dependencies? (y/N)"
    if ($response -notmatch '^[Yy]') {
        Write-Host "Installation cancelled. Please install dependencies manually." -ForegroundColor Yellow
        return $false
    }
    
    # Check if we need elevation
    if (-not (Test-Administrator)) {
        Write-Host "Administrator privileges required for installation." -ForegroundColor Yellow
        Write-Host "Restarting script with elevation..." -ForegroundColor Cyan
        
        $scriptPath = $MyInvocation.ScriptName
        if (-not $scriptPath) {
            $scriptPath = $PSCommandPath
        }
        
        # Build arguments to pass to elevated process
        $arguments = @()
        if ($RepoUrl -ne "https://github.com/Zaroganos/ZaroPGx.git") { $arguments += "-RepoUrl `"$RepoUrl`"" }
        if ($Branch -ne "main") { $arguments += "-Branch `"$Branch`"" }
        if ($TargetDir -ne "ZaroPGx") { $arguments += "-TargetDir `"$TargetDir`"" }
        if ($Update) { $arguments += "-Update" }
        # Pass SkipDependencyCheck and MissingDeps to elevated process
        $arguments += "-SkipDependencyCheck"
        # Pass missing dependencies as a comma-separated string
        $depString = $MissingDeps -join ','
        $arguments += "-MissingDeps '$depString'"
        
        try {
            $elevatedProcess = Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" $($arguments -join ' ')" -PassThru -Wait
            if ($elevatedProcess.ExitCode -ne 0) {
                Write-Host "Installation process exited with code: $($elevatedProcess.ExitCode)" -ForegroundColor Yellow
                return $false
            }
            # After successful installation, re-check dependencies and continue
            Write-Host ""
            Write-Host "Dependencies installed successfully. Re-checking..." -ForegroundColor Green
            return $true
        } catch {
            Write-Host "Failed to elevate privileges: $($_.Exception.Message)" -ForegroundColor Red
            return $false
        }
    }
    
    # If SkipDependencyCheck is set and MissingDeps is provided, we're in the elevated process
    # Parse MissingDeps if it was passed as a comma-separated string
    if ($SkipDependencyCheck -and $MissingDeps.Count -gt 0) {
        if ($MissingDeps.Count -eq 1 -and $MissingDeps[0] -match ',') {
            # Split comma-separated string into array
            $MissingDeps = $MissingDeps[0] -split ',' | ForEach-Object { $_.Trim() }
        }
        # Install dependencies and exit (this is the elevated process)
        foreach ($dep in $MissingDeps) {
            Write-Host ""
            Write-Host "Installing $dep..." -ForegroundColor Yellow
            
            switch ($dep) {
                "Git" {
                    if ($wingetAvailable) {
                        winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
                    } elseif ($chocoAvailable) {
                        choco install git -y
                    }
                }
                "Docker" {
                    if ($wingetAvailable) {
                        Write-Host "Installing Docker Desktop via winget..." -ForegroundColor Cyan
                        winget install --id Docker.DockerDesktop -e --source winget --accept-package-agreements --accept-source-agreements
                    } elseif ($chocoAvailable) {
                        choco install docker-desktop -y
                    }
                }
                "WSL2" {
                    if ($wingetAvailable) {
                        Write-Host "Installing WSL2 via winget..." -ForegroundColor Cyan
                        # Install WSL with Ubuntu as default distro
                        winget install --id Microsoft.WindowsSubsystemLinux -e --source winget --accept-package-agreements --accept-source-agreements
                        winget install --id Canonical.Ubuntu.2204.LTS -e --source winget --accept-package-agreements --accept-source-agreements
                        # Set WSL default version to 2
                        wsl --set-default-version 2
                    } elseif ($chocoAvailable) {
                        choco install wsl2 -y
                    } else {
                        Write-Host "Installing WSL2 manually..." -ForegroundColor Cyan
                        # Enable WSL and Virtual Machine Platform features
                        Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart
                        Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart
                        # Set WSL default version to 2
                        wsl --set-default-version 2
                    }
                }
            }
        }
        Write-Host ""
        Write-Host "Dependencies installed successfully!" -ForegroundColor Green
        exit 0
    }
    
    # Install missing dependencies (normal flow - when already running as admin)
    foreach ($dep in $MissingDeps) {
        Write-Host ""
        Write-Host "Installing $dep..." -ForegroundColor Yellow
        
        switch ($dep) {
            "Git" {
                if ($wingetAvailable) {
                    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
                } elseif ($chocoAvailable) {
                    choco install git -y
                }
            }
            "Docker" {
                if ($wingetAvailable) {
                    Write-Host "Installing Docker Desktop via winget..." -ForegroundColor Cyan
                    winget install --id Docker.DockerDesktop -e --source winget --accept-package-agreements --accept-source-agreements
                } elseif ($chocoAvailable) {
                    choco install docker-desktop -y
                }
            }
            "WSL2" {
                if ($wingetAvailable) {
                    Write-Host "Installing WSL2 via winget..." -ForegroundColor Cyan
                    # Install WSL with Ubuntu as default distro
                    winget install --id Microsoft.WindowsSubsystemLinux -e --source winget --accept-package-agreements --accept-source-agreements
                    winget install --id Canonical.Ubuntu.2204.LTS -e --source winget --accept-package-agreements --accept-source-agreements
                    # Set WSL default version to 2
                    wsl --set-default-version 2
                } elseif ($chocoAvailable) {
                    choco install wsl2 -y
                } else {
                    Write-Host "Installing WSL2 manually..." -ForegroundColor Cyan
                    # Enable WSL and Virtual Machine Platform features
                    Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart
                    Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart
                    # Set WSL default version to 2
                    wsl --set-default-version 2
                }
            }
        }
    }
    
    Write-Host ""
    Write-Host "Dependencies installed! You may need to:" -ForegroundColor Green
    Write-Host "  1. Restart your computer (especially if WSL2 was installed)" -ForegroundColor Yellow
    Write-Host "  2. Restart your terminal/PowerShell session" -ForegroundColor Yellow
    Write-Host "  3. Start Docker Desktop manually" -ForegroundColor Yellow
    Write-Host "  4. Re-run this bootstrap script" -ForegroundColor Yellow
    Write-Host ""
    
    return $true
}

# Check dependencies
if (-not $SkipDependencyCheck) {
    Write-Host "Checking dependencies..." -ForegroundColor Cyan
    $missingDeps = @()
    
    # Check WSL2 (Windows only)
    if ($IsWindows -or $env:OS -eq "Windows_NT") {
        $wslCmd = Get-Command wsl -ErrorAction SilentlyContinue
        if (-not $wslCmd) {
            $missingDeps += "WSL2"
            Write-Host "  ⚠ WSL2 not found" -ForegroundColor Yellow
        } else {
            # Check if WSL2 is properly configured
            try {
                wsl --status 2>&1 | Out-Null
                $wslList = wsl --list --verbose 2>&1
                if ($LASTEXITCODE -eq 0 -and $wslList -match "VERSION.*2") {
                    Write-Host "  ✓ WSL2 found and configured" -ForegroundColor Green
                } elseif ($LASTEXITCODE -eq 0) {
                    Write-Host "  ⚠ WSL found but WSL2 may not be set as default" -ForegroundColor Yellow
                    Write-Host "  ⚠ Consider running: wsl --set-default-version 2" -ForegroundColor Yellow
                } else {
                    Write-Host "  ⚠ WSL found but may not be properly configured" -ForegroundColor Yellow
                }
            } catch {
                Write-Host "  ⚠ WSL found but version check failed" -ForegroundColor Yellow
            }
        }
    }
    
    # Check Git
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if (-not $gitCmd) {
        $missingDeps += "Git"
    } else {
        Write-Host "  ✓ Git found" -ForegroundColor Green
    }
    
    # Check Docker
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCmd) {
        $missingDeps += "Docker"
    } else {
        Write-Host "  ✓ Docker found" -ForegroundColor Green
        
        # Check if Docker is running
        try {
            docker version 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  ⚠ Docker is installed but not running. Please start Docker Desktop." -ForegroundColor Yellow
            } else {
                Write-Host "  ✓ Docker is running" -ForegroundColor Green
            }
        } catch {
            Write-Host "  ⚠ Docker is installed but not running. Please start Docker Desktop." -ForegroundColor Yellow
        }
    }
    
    # Check Docker Compose
    $composeCmd = Get-Command docker-compose -ErrorAction SilentlyContinue
    $composeV2 = $false
    if (-not $composeCmd) {
        # Check for Docker Compose V2 (docker compose)
        try {
            docker compose version 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $composeV2 = $true
                Write-Host "  ✓ Docker Compose (V2) found" -ForegroundColor Green
            }
        } catch {}
    } else {
        Write-Host "  ✓ Docker Compose found" -ForegroundColor Green
    }
    
    if (-not $composeCmd -and -not $composeV2) {
        Write-Host "  ⚠ Docker Compose not found (usually included with Docker Desktop)" -ForegroundColor Yellow
    }
    
    # Handle missing dependencies
    if ($missingDeps.Count -gt 0) {
        $installed = Install-Dependencies -MissingDeps $missingDeps
        if (-not $installed) {
            Write-Host ""
            Write-Host "Please install the following and re-run this script:" -ForegroundColor Red
            foreach ($dep in $missingDeps) {
                Write-Host "  - $dep" -ForegroundColor Yellow
            }
            Write-Host ""
            Write-Host "Installation links:" -ForegroundColor Cyan
            if ($missingDeps -contains "WSL2") {
                Write-Host "  WSL2:          https://learn.microsoft.com/en-us/windows/wsl/install" -ForegroundColor Gray
            }
            if ($missingDeps -contains "Git") {
                Write-Host "  Git:            https://git-scm.com/downloads" -ForegroundColor Gray
            }
            if ($missingDeps -contains "Docker") {
                Write-Host "  Docker Desktop: https://www.docker.com/products/docker-desktop" -ForegroundColor Gray
            }
            Write-Host ""
            exit 1
        }
        
        # After installation, re-check dependencies to ensure they're now available
        Write-Host ""
        Write-Host "Re-checking dependencies after installation..." -ForegroundColor Cyan
        
        # Quick re-check - if still missing, prompt user
        $stillMissing = @()
        foreach ($dep in $missingDeps) {
            switch ($dep) {
                "Git" {
                    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
                    if (-not $gitCmd) { $stillMissing += "Git" }
                }
                "Docker" {
                    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
                    if (-not $dockerCmd) { $stillMissing += "Docker" }
                }
                "WSL2" {
                    $wslCmd = Get-Command wsl -ErrorAction SilentlyContinue
                    if (-not $wslCmd) { $stillMissing += "WSL2" }
                }
            }
        }
        
        if ($stillMissing.Count -gt 0) {
            Write-Host ""
            Write-Host "⚠️  Some dependencies may still need configuration:" -ForegroundColor Yellow
            foreach ($dep in $stillMissing) {
                Write-Host "  - $dep" -ForegroundColor Yellow
            }
            Write-Host ""
            Write-Host "You may need to:" -ForegroundColor Yellow
            Write-Host "  1. Restart your computer (especially if WSL2 was installed)" -ForegroundColor Gray
            Write-Host "  2. Restart your terminal/PowerShell session" -ForegroundColor Gray
            Write-Host "  3. Re-run this bootstrap script" -ForegroundColor Gray
            Write-Host ""
            $continue = Read-Host "Continue anyway? (y/N)"
            if ($continue -notmatch '^[Yy]') {
                Write-Host "Setup cancelled. Please restart this script when ready." -ForegroundColor Yellow
                exit 0
            }
        } else {
            Write-Host "✓ All dependencies are now available!" -ForegroundColor Green
        }
    }
    
    Write-Host ""
}

Write-Host "Repository URL: $RepoUrl" -ForegroundColor Cyan
Write-Host "Branch: $Branch" -ForegroundColor Cyan
Write-Host "Target directory: $TargetDir" -ForegroundColor Cyan

if (-not (Test-Path $TargetDir)) {
    Write-Host "Cloning repository..." -ForegroundColor Yellow
    git clone --branch $Branch $RepoUrl $TargetDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Clone failed." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "Target directory already exists: $TargetDir" -ForegroundColor Gray
    if ($Update) {
        if (-not (Test-Path (Join-Path $TargetDir ".git"))) {
            Write-Host "Existing directory is not a Git repository; skipping update." -ForegroundColor Yellow
        } else {
            Write-Host "Updating repository (fast-forward only)..." -ForegroundColor Yellow
            $status = (& git -C $TargetDir status --porcelain)
            if ($status) {
                Write-Host "Working tree has uncommitted changes; refusing to update. Commit or stash first." -ForegroundColor Yellow
            } else {
                git -C $TargetDir fetch --all --prune
                if ($LASTEXITCODE -ne 0) { Write-Host "git fetch failed." -ForegroundColor Red; exit 1 }
                git -C $TargetDir checkout $Branch
                if ($LASTEXITCODE -ne 0) { Write-Host "git checkout failed." -ForegroundColor Red; exit 1 }
                git -C $TargetDir pull --ff-only
                if ($LASTEXITCODE -ne 0) { Write-Host "git pull failed (non-fast-forward)." -ForegroundColor Red; exit 1 }
            }
        }
    } else {
        Write-Host "Skipping update (use -Update to fetch latest)." -ForegroundColor Gray
    }
}

$startScript = Join-Path $TargetDir "start-docker.ps1"
if (-not (Test-Path $startScript)) {
    Write-Host "start-docker.ps1 not found at: $startScript" -ForegroundColor Red
    exit 1
}

Write-Host "Launching startup script..." -ForegroundColor Yellow
& $startScript


