#!/usr/bin/env bash

set -euo pipefail

# Bash bootstrap script for ZaroPGx
# Clones (or optionally updates) the repository and launches the startup script

REPO_URL="https://github.com/Zaroganos/ZaroPGx.git"
BRANCH="main"
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
  elif command -v yum >/dev/null 2>&1; then
    echo "yum"
  elif command -v dnf >/dev/null 2>&1; then
    echo "dnf"
  elif command -v brew >/dev/null 2>&1; then
    echo "brew"
  elif command -v pacman >/dev/null 2>&1; then
    echo "pacman"
  else
    echo "none"
  fi
}

# Function to install dependencies
install_dependencies() {
  local missing_deps=("$@")
  
  echo ""
  echo "Missing dependencies detected: ${missing_deps[*]}"
  echo ""
  
  local pkg_mgr=$(detect_package_manager)
  
  if [[ "$pkg_mgr" == "none" ]]; then
    echo "No supported package manager found (apt, yum, dnf, brew, pacman)."
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
          yum) $sudo_cmd yum install -y git ;;
          dnf) $sudo_cmd dnf install -y git ;;
          brew) brew install git ;;
          pacman) $sudo_cmd pacman -S --noconfirm git ;;
        esac
        ;;
      Docker)
        case "$pkg_mgr" in
          apt)
            echo "Installing Docker via official Docker repository..."
            # Install prerequisites
            $sudo_cmd apt-get update
            $sudo_cmd apt-get install -y ca-certificates curl gnupg
            
            # Add Docker's official GPG key
            $sudo_cmd install -m 0755 -d /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/ubuntu/gpg | $sudo_cmd gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            $sudo_cmd chmod a+r /etc/apt/keyrings/docker.gpg
            
            # Add Docker repository
            echo \
              "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
              $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
              $sudo_cmd tee /etc/apt/sources.list.d/docker.list > /dev/null
            
            # Install Docker
            $sudo_cmd apt-get update
            $sudo_cmd apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;
          yum|dnf)
            $sudo_cmd $pkg_mgr install -y docker docker-compose
            $sudo_cmd systemctl start docker
            $sudo_cmd systemctl enable docker
            ;;
          brew)
            echo "On macOS, please install Docker Desktop manually:"
            echo "https://www.docker.com/products/docker-desktop"
            ;;
          pacman)
            $sudo_cmd pacman -S --noconfirm docker docker-compose
            $sudo_cmd systemctl start docker
            $sudo_cmd systemctl enable docker
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
      echo "  ⚠ Docker is installed but not running. Please start Docker service."
      echo "    Try: sudo systemctl start docker"
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

echo "Repository URL: ${REPO_URL}"
echo "Branch: ${BRANCH}"
echo "Target directory: ${TARGET_DIR}"

if [[ ! -d "${TARGET_DIR}" ]]; then
  echo "Cloning repository..."
  git clone --branch "${BRANCH}" "${REPO_URL}" "${TARGET_DIR}"
else
  echo "Target directory already exists: ${TARGET_DIR}"
  if [[ "${UPDATE}" == "true" && -d "${TARGET_DIR}/.git" ]]; then
    echo "Updating repository (fast-forward only)..."
    pushd "${TARGET_DIR}" >/dev/null
    if [[ -n "$(git status --porcelain)" ]]; then
      echo "Working tree has uncommitted changes; refusing to update. Commit or stash first." >&2
      exit 1
    fi
    git fetch --all --prune
    git checkout "${BRANCH}"
    git pull --ff-only
    popd >/dev/null
  else
    echo "Skipping update (use -u/--update to fetch latest)."
  fi
fi

cd "${TARGET_DIR}"

if [[ -f "./start-docker.sh" ]]; then
  echo "Launching startup script..."
  bash ./start-docker.sh
else
  echo "start-docker.sh not found in ${TARGET_DIR}" >&2
  exit 1
fi


