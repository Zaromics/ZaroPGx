#!/usr/bin/env bash

set -euo pipefail

# Bash bootstrap script for ZaroPGx
# Clones (or optionally updates) the repository and launches the startup script

REPO_URL="https://github.com/Zaroganos/ZaroPGx.git"
# TODO: Change back to "main" when ready to merge
BRANCH="bootstrap_one-cmd"
TARGET_DIR="ZaroPGx"
UPDATE="false"
SKIP_DEPENDENCY_CHECK="false"

usage() {
  cat <<EOF
Usage: $0 [options]

Options:
  -r, --repo <url>      Repository URL (default: ${REPO_URL})
  -b, --branch <name>   Branch or tag to checkout (default: ${BRANCH})
  -d, --dir <path>      Target directory (default: ${TARGET_DIR})
  -u, --update          Update existing clean repo (fast-forward only)
  -s, --skip-deps       Skip dependency checking
  -h, --help            Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -r|--repo)
      REPO_URL="$2"; shift 2 ;;
    -b|--branch)
      BRANCH="$2"; shift 2 ;;
    -d|--dir|--target)
      TARGET_DIR="$2"; shift 2 ;;
    -u|--update)
      UPDATE="true"; shift 1 ;;
    -s|--skip-deps)
      SKIP_DEPENDENCY_CHECK="true"; shift 1 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage; exit 1 ;;
  esac
done

# Function to detect package manager
detect_package_manager() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "apt"
  elif command -v dnf >/dev/null 2>&1; then
    echo "dnf"
  elif command -v yum >/dev/null 2>&1; then
    echo "yum"
  elif command -v zypper >/dev/null 2>&1; then
    echo "zypper"
  elif command -v pacman >/dev/null 2>&1; then
    echo "pacman"
  elif command -v brew >/dev/null 2>&1; then
    echo "brew"
  else
    echo "none"
  fi
}

# Detect if running inside Windows Subsystem for Linux (WSL)
is_wsl() {
  # Check for WSL-specific indicators
  grep -qi microsoft /proc/version 2>/dev/null || \
  [[ -n "${WSL_DISTRO_NAME-}" ]] || \
  [[ -n "${WSL_INTEROP-}" ]] || \
  [[ -f /proc/sys/fs/binfmt_misc/WSLInterop ]]
}

# Detect if systemd is the init system and available for service management
has_systemd() {
  command -v systemctl >/dev/null 2>&1 && [ "$(ps -p 1 -o comm=)" = "systemd" ]
}

# Function to install dependencies
install_dependencies() {
  local missing_deps=("$@")
  
  echo ""
  echo "Missing dependencies detected: ${missing_deps[*]}"
  echo ""
  
  local pkg_mgr=$(detect_package_manager)
  
  if [[ "$pkg_mgr" == "none" ]]; then
    echo "No supported package manager found (apt, dnf, yum, zypper, pacman, brew)."
    echo ""
    echo "Please install dependencies manually:"
    echo "  Git:            https://git-scm.com/downloads"
    echo "  Docker:         https://docs.docker.com/engine/install/"
    echo "  Docker Compose: https://docs.docker.com/compose/install/"
    echo ""
    return 1
  fi
  
  echo "Detected package manager: $pkg_mgr"
  echo ""
  
  read -p "Would you like to automatically install missing dependencies? (y/N) " -n 1 -r
  echo ""
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Installation cancelled. Please install dependencies manually."
    return 1
  fi
  
  # Check if we need sudo
  local sudo_cmd=""
  if [[ $EUID -ne 0 ]]; then
    if command -v sudo >/dev/null 2>&1; then
      echo "Administrator privileges required for installation."
      echo "You may be prompted for your password..."
      echo ""
      sudo_cmd="sudo"
    else
      echo "Error: This script needs to be run as root or with sudo for installation."
      return 1
    fi
  fi
  
  # Install missing dependencies
  for dep in "${missing_deps[@]}"; do
    echo ""
    echo "Installing $dep..."
    
    case "$dep" in
      Git)
        case "$pkg_mgr" in
          apt) $sudo_cmd apt-get update && $sudo_cmd apt-get install -y git ;;
          dnf) $sudo_cmd dnf install -y git ;;
          yum) $sudo_cmd yum install -y git ;;
          zypper) $sudo_cmd zypper install -y git ;;
          pacman) $sudo_cmd pacman -S --noconfirm git ;;
          brew) brew install git ;;
        esac
        ;;
      Docker)
        case "$pkg_mgr" in
          apt)
            echo "Installing Docker via official Docker repository..."
            # Install prerequisites
            $sudo_cmd apt-get update
            $sudo_cmd apt-get install -y ca-certificates curl gnupg

            # Determine Debian vs Ubuntu for the correct repository
            repo_distro="debian"
            distro_codename=""
            if [[ -r /etc/os-release ]]; then
              . /etc/os-release
              if [[ "${ID:-}" = "ubuntu" || "${ID_LIKE:-}" =~ ubuntu ]]; then
                repo_distro="ubuntu"
                distro_codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
              else
                distro_codename="${VERSION_CODENAME:-}"
              fi
            fi
            
            # Fallback if codename is still empty
            if [[ -z "$distro_codename" ]]; then
              distro_codename=$(lsb_release -cs 2>/dev/null || echo "stable")
            fi

            # Add Docker's official key and repo for the detected distro
            $sudo_cmd install -m 0755 -d /etc/apt/keyrings
            $sudo_cmd curl -fsSL "https://download.docker.com/linux/${repo_distro}/gpg" -o /etc/apt/keyrings/docker.asc
            $sudo_cmd chmod a+r /etc/apt/keyrings/docker.asc
            echo \
              "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${repo_distro} \
              ${distro_codename} stable" | \
              $sudo_cmd tee /etc/apt/sources.list.d/docker.list > /dev/null

            # Install Docker
            $sudo_cmd apt-get update
            $sudo_cmd apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;
          yum)
            echo "Installing Docker via official Docker repository (CentOS/YUM)..."
            # Set up Docker CE repo (CentOS)
            $sudo_cmd yum install -y yum-utils
            $sudo_cmd yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
            # Install Docker CE and Compose plugin
            $sudo_cmd yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            # Enable and start Docker where systemd is available (not in WSL)
            if ! is_wsl && has_systemd; then
              $sudo_cmd systemctl enable --now docker
            else
              echo "Skipping systemctl enable/start (WSL or non-systemd environment detected)."
            fi
            ;;
          dnf)
            echo "Installing Docker via official Docker repository (RHEL/Fedora/DNF)..."
            # Set up Docker CE repo (choose RHEL vs Fedora appropriately)
            $sudo_cmd dnf -y install dnf-plugins-core
            if [[ -r /etc/os-release ]]; then
              . /etc/os-release
            fi
            if [[ "${ID:-}" = "fedora" || "${ID_LIKE:-}" =~ fedora ]]; then
              $sudo_cmd dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
            else
              $sudo_cmd dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
            fi
            # Install Docker CE and Compose plugin
            $sudo_cmd dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            # Enable and start Docker where systemd is available (not in WSL)
            if ! is_wsl && has_systemd; then
              $sudo_cmd systemctl enable --now docker
            else
              echo "Skipping systemctl enable/start (WSL or non-systemd environment detected)."
            fi
            ;;
          zypper)
            echo "Installing Docker via zypper (openSUSE/SUSE)..."
            # Install Docker from official SUSE repositories
            $sudo_cmd zypper refresh
            $sudo_cmd zypper install -y docker docker-compose
            # Enable and start Docker where systemd is available (not in WSL)
            if ! is_wsl && has_systemd; then
              $sudo_cmd systemctl enable --now docker
            else
              echo "Skipping systemctl enable/start (WSL or non-systemd environment detected)."
            fi
            ;;
          pacman)
            echo "Installing Docker via pacman (Arch Linux)..."
            $sudo_cmd pacman -Sy --noconfirm docker docker-compose
            # Enable and start Docker where systemd is available (not in WSL)
            if ! is_wsl && has_systemd; then
              $sudo_cmd systemctl enable --now docker
            else
              echo "Skipping systemctl enable/start (WSL or non-systemd environment detected)."
            fi
            ;;
          brew)
            echo "On macOS, please install Docker Desktop manually:"
            echo "https://www.docker.com/products/docker-desktop"
            ;;
        esac
        ;;
    esac
  done
  
  echo ""
  echo "Dependencies installed! You may need to:"
  echo "  1. Restart your terminal session (or run: source ~/.bashrc)"
  echo "  2. Add your user to the docker group: sudo usermod -aG docker \$USER"
  echo "  3. Log out and back in for group changes to take effect"
  echo "  4. Re-run this bootstrap script"
  echo ""
  
  return 0
}

# Check dependencies
if [[ "$SKIP_DEPENDENCY_CHECK" != "true" ]]; then
  echo "Checking dependencies..."
  missing_deps=()
  
  # Check Git
  if ! command -v git >/dev/null 2>&1; then
    missing_deps+=("Git")
  else
    echo "  ✓ Git found"
  fi
  
  # Check Docker
  if ! command -v docker >/dev/null 2>&1; then
    missing_deps+=("Docker")
  else
    echo "  ✓ Docker found"
    
    # Check if Docker is running
    if docker ps >/dev/null 2>&1; then
      echo "  ✓ Docker is running"
    else
      echo "  ⚠ Docker is installed but not running."
      if is_wsl; then
        echo "    WSL detected: Please start Docker Desktop on Windows and enable WSL integration."
        echo ""
        echo "    Steps:"
        echo "      1. Start Docker Desktop on Windows"
        echo "      2. Open Settings > Resources > WSL Integration"
        echo "      3. Enable integration for your WSL distribution"
        echo ""
        echo "    Note: WSL 2 with systemd (Windows 11 or updated Windows 10) supports"
        echo "          native Docker daemon. Check /etc/wsl.conf for systemd settings."
      elif has_systemd; then
        echo "    Start the service with: sudo systemctl start docker"
      else
        echo "    Systemd not detected. Start the Docker daemon manually for your init system."
      fi
    fi
  fi
  
  # Check Docker Compose
  if command -v docker-compose >/dev/null 2>&1; then
    echo "  ✓ Docker Compose found"
  elif docker compose version >/dev/null 2>&1; then
    echo "  ✓ Docker Compose (V2) found"
  else
    echo "  ⚠ Docker Compose not found (usually included with Docker installation)"
  fi
  
  # Handle missing dependencies
  if [[ ${#missing_deps[@]} -gt 0 ]]; then
    if ! install_dependencies "${missing_deps[@]}"; then
      echo ""
      echo "Please install the following and re-run this script:"
      for dep in "${missing_deps[@]}"; do
        echo "  - $dep"
      done
      echo ""
      echo "Installation links:"
      echo "  Git:            https://git-scm.com/downloads"
      echo "  Docker:         https://docs.docker.com/engine/install/"
      echo "  Docker Compose: https://docs.docker.com/compose/install/"
      echo ""
      exit 1
    fi
    
    # After installation, prompt to continue
    read -p "Dependencies installed. Continue with setup? (y/N) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      echo "Setup cancelled. Please restart this script when ready."
      exit 0
    fi
  fi
  
  echo ""
fi

echo ""
echo "Repository URL: ${REPO_URL}"
echo "Branch: ${BRANCH}"
echo "Target directory: ${TARGET_DIR}"
echo ""

if [[ ! -d "${TARGET_DIR}" ]]; then
  echo "Cloning repository..."
  if ! git clone --branch "${BRANCH}" "${REPO_URL}" "${TARGET_DIR}"; then
    echo "Error: Failed to clone repository" >&2
    exit 1
  fi
  echo "Repository cloned successfully."
else
  echo "Target directory already exists: ${TARGET_DIR}"
  if [[ "${UPDATE}" == "true" && -d "${TARGET_DIR}/.git" ]]; then
    echo "Updating repository (fast-forward only)..."
    pushd "${TARGET_DIR}" >/dev/null || exit 1
    if [[ -n "$(git status --porcelain)" ]]; then
      echo "Error: Working tree has uncommitted changes; refusing to update." >&2
      echo "       Please commit or stash changes first." >&2
      popd >/dev/null
      exit 1
    fi
    if ! git fetch --all --prune; then
      echo "Error: Failed to fetch updates" >&2
      popd >/dev/null
      exit 1
    fi
    if ! git checkout "${BRANCH}"; then
      echo "Error: Failed to checkout branch ${BRANCH}" >&2
      popd >/dev/null
      exit 1
    fi
    if ! git pull --ff-only; then
      echo "Error: Failed to pull updates (fast-forward only)" >&2
      echo "       Repository may have diverged. Manual merge required." >&2
      popd >/dev/null
      exit 1
    fi
    echo "Repository updated successfully."
    popd >/dev/null
  else
    echo "Skipping update (use -u/--update to fetch latest)."
  fi
fi

echo ""
cd "${TARGET_DIR}" || exit 1

if [[ -f "./start-docker.sh" ]]; then
  echo "Launching startup script..."
  exec bash ./start-docker.sh
else
  echo "Error: start-docker.sh not found in ${TARGET_DIR}" >&2
  exit 1
fi


