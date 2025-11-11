# PowerShell bootstrap script for ZaroPGx
# Clones (or optionally updates) the repository and launches the startup script
#
# Usage:
#   Quick install (bypasses execution policy):
#     iwr -useb https://raw.githubusercontent.com/Zaroganos/ZaroPGx/main/bootstrap.ps1 | iex
#   
#   Or download and run (may require execution policy change):
#     powershell -ExecutionPolicy Bypass -File bootstrap.ps1
#
# System Requirements:
#   - Windows 10 22H2 (build 19045) or Windows 11 22H2 (build 22631) or higher
#   - WSL version 2.1.5 or later (for Docker Desktop)
#   - Git for Windows [TODO: Add to the bootstrap script]
#   - Docker Desktop (requires WSL2 backend)
#   - 16GB RAM minimum, 64-bit processor with Second Level Address Translation (SLAT)
#   - Hardware virtualization enabled in BIOS/UEFI

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

# Check and set execution policy for this process (doesn't require admin)
$currentPolicy = Get-ExecutionPolicy -Scope Process
if ($currentPolicy -eq "Restricted" -or $currentPolicy -eq "AllSigned") {
    Write-Host "Setting execution policy to Bypass for this session..." -ForegroundColor Yellow
    try {
        Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force
        Write-Host "  [OK] Execution policy set for this session" -ForegroundColor Green
    } catch {
        Write-Host "  [WARNING] Could not set execution policy: $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "  You may need to run: Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser" -ForegroundColor Yellow
    }
}

# Function to check if running as administrator
function Test-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Helper: Get Windows build number (used to decide if wsl --install is supported)
function Get-WindowsBuildNumber {
    try {
        $build = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').CurrentBuildNumber
        return [int]$build
    } catch {
        return 0
    }
}

# Helper: Get WSL version (Docker Desktop requires WSL 2.1.5+)
function Get-WSLVersion {
    try {
        $wslVersionOutput = wsl --version 2>&1 | Out-String
        # Remove null characters that cause spacing issues in UTF-16 output
        $wslVersionOutput = $wslVersionOutput -replace '\0', ''
        if ($LASTEXITCODE -eq 0 -and $wslVersionOutput -match "WSL\s*version:\s*(\d+\.\d+\.\d+(?:\.\d+)?)") {
            return [version]$matches[1]
        }
        # If wsl --version doesn't work, we're on inbox/legacy WSL (pre-2.0)
        return [version]"0.0.0"
    } catch {
        return [version]"0.0.0"
    }
}

# Helper: Check if any WSL distribution is installed
function Test-WSLDistribution {
    try {
        $distros = wsl --list --quiet 2>&1 | Out-String
        $distros = $distros.Trim()
        return ($LASTEXITCODE -eq 0 -and $distros -ne "")
    } catch {
        return $false
    }
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
        
        # Try multiple methods to get script path
        $scriptPath = $null
        if ($PSCommandPath -and (Test-Path $PSCommandPath)) {
            $scriptPath = $PSCommandPath
            Write-Host "  Using script path: $scriptPath" -ForegroundColor Gray
        } elseif ($MyInvocation.MyCommand.Path -and (Test-Path $MyInvocation.MyCommand.Path)) {
            $scriptPath = $MyInvocation.MyCommand.Path
            Write-Host "  Using script path: $scriptPath" -ForegroundColor Gray
        } elseif ($MyInvocation.ScriptName -and (Test-Path $MyInvocation.ScriptName)) {
            $scriptPath = $MyInvocation.ScriptName
            Write-Host "  Using script path: $scriptPath" -ForegroundColor Gray
        }
        
        # If script path not found or running from memory, create temp file
        if (-not $scriptPath) {
            Write-Host "  Script is running from memory, creating temporary file..." -ForegroundColor Gray
            $tempScript = Join-Path $env:TEMP "zaropgx-bootstrap-temp.ps1"
            
            # Try to get script content from current execution context
            $scriptContent = $null
            
            # Method 1: Try to get from MyInvocation
            try {
                $scriptContent = $MyInvocation.MyCommand.ScriptBlock.ToString()
                if ($scriptContent) {
                    Write-Host "  Retrieved script from execution context" -ForegroundColor Gray
                }
            } catch {}
            
            # Method 2: If that didn't work, try reading from PSCommandPath anyway
            if (-not $scriptContent -and $PSCommandPath) {
                try {
                    $scriptContent = Get-Content $PSCommandPath -Raw -ErrorAction Stop
                    Write-Host "  Retrieved script from PSCommandPath" -ForegroundColor Gray
                } catch {}
            }
            
            # Method 3: Last resort - download from GitHub
            if (-not $scriptContent) {
                Write-Host "  Downloading fresh copy of bootstrap script from GitHub..." -ForegroundColor Gray
                try {
                    # Convert git URL to raw content URL (remove .git suffix if present)
                    $rawRepoUrl = $RepoUrl -replace '\.git$', ''
                    $downloadUrl = "$rawRepoUrl/raw/$Branch/bootstrap.ps1"
                    Write-Host "  Download URL: $downloadUrl" -ForegroundColor Gray
                    $scriptContent = (Invoke-WebRequest -Uri $downloadUrl -UseBasicParsing).Content
                } catch {
                    Write-Host "  [ERROR] Cannot retrieve script for elevation: $($_.Exception.Message)" -ForegroundColor Red
                    Write-Host ""
                    Write-Host "  Workaround: Run this script as Administrator directly" -ForegroundColor Yellow
                    Write-Host "  Right-click PowerShell -> Run as Administrator, then:" -ForegroundColor Yellow
                    Write-Host "    cd `"$((Get-Location).Path)`"" -ForegroundColor Cyan
                    Write-Host "    .\bootstrap.ps1" -ForegroundColor Cyan
                    Write-Host ""
                    return $false
                }
            }
            
            # Save to temp file
            $scriptContent | Out-File -FilePath $tempScript -Encoding UTF8 -Force
            $scriptPath = $tempScript
            Write-Host "  [OK] Temporary script created: $tempScript" -ForegroundColor Green
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
        
        Write-Host ""
        Write-Host "  Starting elevated PowerShell window..." -ForegroundColor Cyan
        Write-Host "  Please approve the UAC prompt if it appears." -ForegroundColor Yellow
        Write-Host ""
        
        try {
            $elevatedProcess = Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" $($arguments -join ' ')" -PassThru -Wait
            
            # Clean up temp script if we created one
            if ($scriptPath -like "*zaropgx-bootstrap-temp.ps1") {
                try { Remove-Item $scriptPath -Force -ErrorAction SilentlyContinue } catch {}
            }
            
            if ($elevatedProcess.ExitCode -ne 0) {
                Write-Host "  Installation process exited with code: $($elevatedProcess.ExitCode)" -ForegroundColor Yellow
                Write-Host ""
                
                # Provide helpful error messages for common exit codes
                if ($elevatedProcess.ExitCode -eq -196608 -or $elevatedProcess.ExitCode -eq 1) {
                    Write-Host "  This may indicate:" -ForegroundColor Yellow
                    Write-Host "    - UAC prompt was cancelled" -ForegroundColor Gray
                    Write-Host "    - Script execution was blocked" -ForegroundColor Gray
                    Write-Host "    - An error occurred during installation" -ForegroundColor Gray
                    Write-Host ""
                }
                
                return $false
            }
            # After successful installation, re-check dependencies and continue
            Write-Host ""
            Write-Host "Dependencies installed successfully. Re-checking..." -ForegroundColor Green
            return $true
        } catch {
            Write-Host "  Failed to elevate privileges: $($_.Exception.Message)" -ForegroundColor Red
            Write-Host ""
            return $false
        }
    }
    
    # If SkipDependencyCheck is set and MissingDeps is provided, we're in the elevated process
    # Parse MissingDeps if it was passed as a comma-separated string
    if ($SkipDependencyCheck -and $MissingDeps.Count -gt 0) {
        Write-Host ""
        Write-Host "════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
        Write-Host "  ZaroPGx Dependency Installation (Elevated)" -ForegroundColor Cyan
        Write-Host "════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  This window is running with administrator privileges" -ForegroundColor Gray
        Write-Host "  Installing required dependencies..." -ForegroundColor Gray
        Write-Host ""
        
        if ($MissingDeps.Count -eq 1 -and $MissingDeps[0] -match ',') {
            # Split comma-separated string into array
            $MissingDeps = $MissingDeps[0] -split ',' | ForEach-Object { $_.Trim() }
        }
        
        $installSuccess = $true
        
        # Install dependencies and exit (this is the elevated process)
        foreach ($dep in $MissingDeps) {
            Write-Host ""
            Write-Host "Installing $dep..." -ForegroundColor Yellow
            
            try {
                switch ($dep) {
                    "Git" {
                        if ($wingetAvailable) {
                            winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
                            if ($LASTEXITCODE -ne 0) { throw "Git installation failed with exit code $LASTEXITCODE" }
                        } elseif ($chocoAvailable) {
                            choco install git -y
                            if ($LASTEXITCODE -ne 0) { throw "Git installation failed with exit code $LASTEXITCODE" }
                        }
                    }
                    "Docker" {
                        if ($wingetAvailable) {
                            Write-Host "Installing Docker Desktop via winget..." -ForegroundColor Cyan
                            Write-Host "(This may take several minutes)" -ForegroundColor Gray
                            winget install --id Docker.DockerDesktop -e --source winget --accept-package-agreements --accept-source-agreements
                            if ($LASTEXITCODE -ne 0) { throw "Docker installation failed with exit code $LASTEXITCODE" }
                        } elseif ($chocoAvailable) {
                            choco install docker-desktop -y
                            if ($LASTEXITCODE -ne 0) { throw "Docker installation failed with exit code $LASTEXITCODE" }
                        }
                    }
                    "WSL2" {
                        # Prefer the modern, supported path: wsl --install (Win10 22H2+/Win11)
                        # Windows 10 build 19045 (22H2) or Windows 11 build 22000+ support wsl --install
                        $buildNumber = Get-WindowsBuildNumber
                        $supportsWslInstall = ($buildNumber -ge 19041)

                        if ($supportsWslInstall) {
                            Write-Host "Installing WSL using 'wsl --install'..." -ForegroundColor Cyan
                            # Set WSL2 as default BEFORE installing distributions
                            try { wsl --set-default-version 2 2>&1 | Out-Null } catch {}
                            # Install WSL and Ubuntu 22.04; default can be changed later if desired
                            wsl --install -d Ubuntu-22.04
                            # Update WSL to latest version (ensures we have WSL 2.1.5+)
                            Write-Host "Updating WSL to latest version..." -ForegroundColor Cyan
                            try { wsl --update } catch { Write-Host "  Note: WSL update may complete after restart" -ForegroundColor Gray }
                        } elseif ($wingetAvailable) {
                            Write-Host "Installing WSL from Microsoft Store via winget..." -ForegroundColor Cyan
                            # Updated package ID for WSL in Microsoft Store; try legacy ID as fallback
                            winget install --id Microsoft.WSL -e --source winget --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
                            if ($LASTEXITCODE -ne 0) {
                                Write-Host "Primary WSL package ID failed, trying legacy ID..." -ForegroundColor Yellow
                                winget install --id Microsoft.WindowsSubsystemLinux -e --source winget --accept-package-agreements --accept-source-agreements
                            }
                            Write-Host "Enabling required Windows features for WSL..." -ForegroundColor Cyan
                            Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart
                            Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart
                            wsl --set-default-version 2
                            Write-Host "Restart may be required. After restart, install a distro with: wsl --install -d Ubuntu-22.04" -ForegroundColor Yellow
                        } else {
                            Write-Host "Installing WSL2 manually..." -ForegroundColor Cyan
                            # Enable WSL and Virtual Machine Platform features
                            Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart
                            Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart
                            # Set WSL default version to 2
                            wsl --set-default-version 2
                            Write-Host "If prompted for the WSL kernel, download from: https://github.com/microsoft/wsl/releases" -ForegroundColor Gray
                            Write-Host "After reboot, install a distro with: wsl --install -d Ubuntu-22.04" -ForegroundColor Yellow
                        }
                    }
                }
                Write-Host "  [OK] $dep installation completed" -ForegroundColor Green
            } catch {
                Write-Host "  [ERROR] Failed to install ${dep}: $($_.Exception.Message)" -ForegroundColor Red
                $installSuccess = $false
            }
        }
        
        Write-Host ""
        Write-Host "════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
        if ($installSuccess) {
            Write-Host "  Installation Complete!" -ForegroundColor Green
            Write-Host "════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
            Write-Host ""
            Write-Host "Dependencies installed successfully!" -ForegroundColor Green
            Write-Host ""
            Write-Host "Important Next Steps:" -ForegroundColor Yellow
            Write-Host "  1. Close this window" -ForegroundColor Gray
            Write-Host "  2. Restart your terminal/PowerShell session" -ForegroundColor Gray
            Write-Host "  3. If you installed WSL2, you may need to restart your computer" -ForegroundColor Gray
            Write-Host "  4. Start Docker Desktop manually (if installed)" -ForegroundColor Gray
            Write-Host "  5. Re-run the bootstrap script" -ForegroundColor Gray
            Write-Host ""
            Write-Host "This window will close in 10 seconds..." -ForegroundColor Gray
            Start-Sleep -Seconds 10
            exit 0
        } else {
            Write-Host "  Installation Completed with Errors" -ForegroundColor Yellow
            Write-Host "════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
            Write-Host ""
            Write-Host "Some dependencies failed to install." -ForegroundColor Yellow
            Write-Host "Please review the error messages above." -ForegroundColor Yellow
            Write-Host ""
            Write-Host "Press any key to close this window..." -ForegroundColor Gray
            $null = $host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
            exit 1
        }
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
                # Prefer the modern, supported path: wsl --install (Win10 22H2+/Win11)
                # Windows 10 build 19045 (22H2) or Windows 11 build 22000+ support wsl --install
                $buildNumber = Get-WindowsBuildNumber
                $supportsWslInstall = ($buildNumber -ge 19041)

                if ($supportsWslInstall) {
                    Write-Host "Installing WSL using 'wsl --install'..." -ForegroundColor Cyan
                    # Set WSL2 as default BEFORE installing distributions
                    try { wsl --set-default-version 2 2>&1 | Out-Null } catch {}
                    # Install WSL and Ubuntu 22.04; default can be changed later if desired
                    wsl --install -d Ubuntu-22.04
                    # Update WSL to latest version (ensures we have WSL 2.1.5+)
                    Write-Host "Updating WSL to latest version..." -ForegroundColor Cyan
                    try { wsl --update } catch { Write-Host "  Note: WSL update may complete after restart" -ForegroundColor Gray }
                } elseif ($wingetAvailable) {
                    Write-Host "Installing WSL from Microsoft Store via winget..." -ForegroundColor Cyan
                    # Updated package ID for WSL in Microsoft Store; try legacy ID as fallback
                    winget install --id Microsoft.WSL -e --source winget --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
                    if ($LASTEXITCODE -ne 0) {
                        Write-Host "Primary WSL package ID failed, trying legacy ID..." -ForegroundColor Yellow
                        winget install --id Microsoft.WindowsSubsystemLinux -e --source winget --accept-package-agreements --accept-source-agreements
                    }
                    Write-Host "Enabling required Windows features for WSL..." -ForegroundColor Cyan
                    Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart
                    Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart
                    wsl --set-default-version 2
                    Write-Host "Restart may be required. After restart, install a distro with: wsl --install -d Ubuntu-22.04" -ForegroundColor Yellow
                } else {
                    Write-Host "Installing WSL2 manually..." -ForegroundColor Cyan
                    # Enable WSL and Virtual Machine Platform features
                    Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart
                    Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart
                    # Set WSL default version to 2
                    wsl --set-default-version 2
                    Write-Host "If prompted for the WSL kernel, download from: https://github.com/microsoft/wsl/releases" -ForegroundColor Gray
                    Write-Host "After reboot, install a distro with: wsl --install -d Ubuntu-22.04" -ForegroundColor Yellow
                }
            }
        }
    }
    
    Write-Host ""
    Write-Host "Dependencies installed! You may need to:" -ForegroundColor Green
    Write-Host "  1. Restart your computer (especially if WSL2 was installed)" -ForegroundColor Yellow
    Write-Host "  2. Run 'wsl --update' to ensure WSL version 2.1.5+ is installed" -ForegroundColor Yellow
    Write-Host "  3. Restart your terminal/PowerShell session" -ForegroundColor Yellow
    Write-Host "  4. Start Docker Desktop manually" -ForegroundColor Yellow
    Write-Host "  5. Re-run this bootstrap script" -ForegroundColor Yellow
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
            Write-Host "  [WARNING] WSL2 not found" -ForegroundColor Yellow
        } else {
            # Check WSL version (Docker Desktop requires WSL 2.1.5+)
            $wslVersion = Get-WSLVersion
            $minRequiredVersion = [version]"2.1.5"
            $needsUpdate = $false
            
            if ($wslVersion -eq [version]"0.0.0") {
                Write-Host "  [WARNING] WSL found but using legacy/inbox version (pre-2.0)" -ForegroundColor Yellow
                Write-Host "  [WARNING] Docker Desktop requires WSL version 2.1.5 or higher" -ForegroundColor Yellow
                $needsUpdate = $true
            } elseif ($wslVersion -lt $minRequiredVersion) {
                Write-Host "  [WARNING] WSL version $wslVersion found (Docker requires 2.1.5+)" -ForegroundColor Yellow
                $needsUpdate = $true
            } else {
                Write-Host "  [OK] WSL version $wslVersion found" -ForegroundColor Green
            }
            
            # Offer to update WSL if outdated
            if ($needsUpdate) {
                Write-Host ""
                $updateResponse = Read-Host "Would you like to update WSL now? (Y/n)"
                if ($updateResponse -notmatch '^[Nn]') {
                    Write-Host "  Updating WSL..." -ForegroundColor Cyan
                    try {
                        wsl --update
                        if ($LASTEXITCODE -eq 0) {
                            Write-Host "  [OK] WSL updated successfully!" -ForegroundColor Green
                            Write-Host "  Note: You may need to restart your terminal" -ForegroundColor Gray
                        } else {
                            Write-Host "  [WARNING] WSL update completed with warnings" -ForegroundColor Yellow
                        }
                    } catch {
                        Write-Host "  [WARNING] WSL update failed: $($_.Exception.Message)" -ForegroundColor Yellow
                        Write-Host "  Please run 'wsl --update' manually as administrator" -ForegroundColor Yellow
                    }
                } else {
                    Write-Host "  Skipping WSL update. Docker Desktop may not start correctly." -ForegroundColor Yellow
                }
            }
            
            # Check if WSL2 is properly configured and a distribution is installed
            try {
                $wslList = wsl --list --verbose 2>&1 | Out-String
                if ($LASTEXITCODE -eq 0) {
                    if ($wslList -match "VERSION.*2") {
                        $hasDistro = Test-WSLDistribution
                        if ($hasDistro) {
                            Write-Host "  [OK] WSL2 distributions found" -ForegroundColor Green
                        } else {
                            Write-Host "  [WARNING] WSL2 enabled but no distributions installed" -ForegroundColor Yellow
                            Write-Host ""
                            Write-Host "  A Linux distribution is required for Docker in WSL2" -ForegroundColor Cyan
                            Write-Host "  Installing Ubuntu 22.04 (recommended)..." -ForegroundColor Yellow
                            
                            try {
                                wsl --install -d Ubuntu-22.04 --no-launch
                                if ($LASTEXITCODE -eq 0) {
                                    Write-Host "  [OK] Ubuntu 22.04 installed" -ForegroundColor Green
                                    Write-Host "  Note: You'll need to set up a username/password on first use" -ForegroundColor Gray
                                } else {
                                    Write-Host "  [WARNING] Failed to install Ubuntu automatically" -ForegroundColor Yellow
                                    Write-Host "  Please run: wsl --install -d Ubuntu-22.04" -ForegroundColor Yellow
                                }
                            } catch {
                                Write-Host "  [WARNING] Could not install Ubuntu: $($_.Exception.Message)" -ForegroundColor Yellow
                                Write-Host "  Please run: wsl --install -d Ubuntu-22.04" -ForegroundColor Yellow
                            }
                        }
                    } else {
                        Write-Host "  [WARNING] WSL found but WSL2 may not be set as default" -ForegroundColor Yellow
                        Write-Host "  [WARNING] Set WSL2 as default: wsl --set-default-version 2" -ForegroundColor Yellow
                    }
                } else {
                    Write-Host "  [WARNING] WSL found but may not be properly configured" -ForegroundColor Yellow
                }
            } catch {
                Write-Host "  [WARNING] WSL found but status check failed" -ForegroundColor Yellow
            }
        }
    }
    
    # Check Git
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if (-not $gitCmd) {
        $missingDeps += "Git"
    } else {
        Write-Host "  [OK] Git found" -ForegroundColor Green
    }
    
    # Check Docker
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCmd) {
        $missingDeps += "Docker"
    } else {
        Write-Host "  [OK] Docker found" -ForegroundColor Green
        
        # Check if Docker is running
        try {
            docker version 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  [WARNING] Docker is installed but not running. Please start Docker Desktop." -ForegroundColor Yellow
            } else {
                Write-Host "  [OK] Docker is running" -ForegroundColor Green
            }
        } catch {
            Write-Host "  [WARNING] Docker is installed but not running. Please start Docker Desktop." -ForegroundColor Yellow
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
                Write-Host "  [OK] Docker Compose (V2) found" -ForegroundColor Green
            }
        } catch {}
    } else {
        Write-Host "  [OK] Docker Compose found" -ForegroundColor Green
    }
    
    if (-not $composeCmd -and -not $composeV2) {
        Write-Host "  [WARNING] Docker Compose not found (usually included with Docker Desktop)" -ForegroundColor Yellow
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
                Write-Host "  WSL2:           https://learn.microsoft.com/en-us/windows/wsl/install" -ForegroundColor Gray
                Write-Host "  WSL Update:     Run 'wsl --update' in PowerShell (admin)" -ForegroundColor Gray
            }
            if ($missingDeps -contains "Git") {
                Write-Host "  Git:            https://git-scm.com/downloads" -ForegroundColor Gray
            }
            if ($missingDeps -contains "Docker") {
                Write-Host "  Docker Desktop: https://www.docker.com/products/docker-desktop" -ForegroundColor Gray
                Write-Host "  Requirements:   Windows 10 22H2 (build 19045) or Windows 11" -ForegroundColor Gray
                Write-Host "                  WSL version 2.1.5 or higher" -ForegroundColor Gray
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
            Write-Host "[WARNING] Some dependencies may still need configuration:" -ForegroundColor Yellow
            foreach ($dep in $stillMissing) {
                Write-Host "  - $dep" -ForegroundColor Yellow
            }
            Write-Host ""
            Write-Host "You may need to:" -ForegroundColor Yellow
            Write-Host "  1. Restart your computer (especially if WSL2 was installed)" -ForegroundColor Gray
            Write-Host "  2. Run 'wsl --update' to ensure WSL 2.1.5+ is installed" -ForegroundColor Gray
            Write-Host "  3. Restart your terminal/PowerShell session" -ForegroundColor Gray
            Write-Host "  4. Re-run this bootstrap script" -ForegroundColor Gray
            Write-Host ""
            $continue = Read-Host "Continue anyway? (y/N)"
            if ($continue -notmatch '^[Yy]') {
                Write-Host "Setup cancelled. Please restart this script when ready." -ForegroundColor Yellow
                exit 0
            }
        } else {
            Write-Host "[OK] All dependencies are now available!" -ForegroundColor Green
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
                
                # Re-normalize line endings after pull (in case .gitattributes was updated)
                Write-Host "Re-normalizing line endings..." -ForegroundColor Gray
                git -C $TargetDir rm --cached -r . 2>&1 | Out-Null
                git -C $TargetDir reset --hard 2>&1 | Out-Null
            }
        }
    } else {
        Write-Host "Skipping update (use -Update to fetch latest)." -ForegroundColor Gray
        
        # Check if we need to re-normalize line endings even without update
        if (Test-Path (Join-Path $TargetDir ".git")) {
            $shFile = Join-Path $TargetDir "start-docker.sh"
            if (Test-Path $shFile) {
                # Check if file has CRLF line endings
                $content = Get-Content -Path $shFile -Raw
                if ($content -match "`r`n") {
                    Write-Host "Detected incorrect line endings, re-normalizing files..." -ForegroundColor Yellow
                    git -C $TargetDir rm --cached -r . 2>&1 | Out-Null
                    git -C $TargetDir reset --hard 2>&1 | Out-Null
                    Write-Host "  [OK] Files re-normalized" -ForegroundColor Green
                }
            }
        }
    }
}

$startScript = Join-Path $TargetDir "start-docker.ps1"
if (-not (Test-Path $startScript)) {
    Write-Host "start-docker.ps1 not found at: $startScript" -ForegroundColor Red
    exit 1
}

Write-Host "Launching startup script..." -ForegroundColor Yellow
Write-Host "Using local development configuration (.env.local)" -ForegroundColor Cyan

# Launch start-docker.ps1 with execution policy bypass
try {
    powershell.exe -ExecutionPolicy Bypass -File $startScript -AutoLocal
    $startScriptExitCode = $LASTEXITCODE
    if ($startScriptExitCode -ne 0) {
        Write-Host "start-docker.ps1 exited with code: $startScriptExitCode" -ForegroundColor Yellow
    }
} catch {
    Write-Host "Failed to launch start-docker.ps1: $($_.Exception.Message)" -ForegroundColor Red
    if ($didPush) { Pop-Location }
    exit 1
}


