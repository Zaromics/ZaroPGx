#!/usr/bin/env bash

set -euo pipefail

# Bash bootstrap script for ZaroPGx
# Clones (or optionally updates) the repository and launches the startup script

REPO_URL="https://github.com/Zaromics/ZaroPGx.git"
RELEASE_VERSION="0.3.2"
BRANCH="v${RELEASE_VERSION}"
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
  elif command -v apk >/dev/null 2>&1; then
    echo "apk"
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

# curl | bash feeds the script on stdin, so `read` must use the terminal.
prompt_yes() {
  local prompt="$1"
  local reply=""
  if [[ ! -r /dev/tty ]]; then
    echo "No terminal is available to answer prompts." >&2
    echo "Install Git and Docker, then re-run, or save the script and run: bash bootstrap.sh" >&2
    return 1
  fi
  read -r -n 1 -p "$prompt" reply </dev/tty || return 1
  echo "" >/dev/tty
  [[ "$reply" =~ ^[Yy]$ ]]
}

# Start the daemon we just installed. WSL with systemd can run it too.
start_docker_daemon() {
  local sudo_cmd="$1"
  if has_systemd; then
    $sudo_cmd systemctl enable --now docker
    return
  fi
  if command -v service >/dev/null 2>&1; then
    $sudo_cmd service docker start
    return
  fi
  if command -v rc-service >/dev/null 2>&1; then
    $sudo_cmd rc-update add docker default
    $sudo_cmd rc-service docker start
    return
  fi
  echo "Start the Docker daemon yourself, then re-run this bootstrap."
}

require_compose_v2() {
  local minimum_major=2
  local minimum_minor=24
  local version major minor

  if ! version="$(docker compose version --short 2>/dev/null)"; then
    echo "Docker Compose V2 is not installed." >&2
    return 1
  fi
  version="${version#v}"
  version="${version%%[^0-9.]*}"
  IFS=. read -r major minor _ <<<"$version"
  if [[ ! "$major" =~ ^[0-9]+$ || ! "$minor" =~ ^[0-9]+$ ]]; then
    echo "Could not parse Docker Compose version: ${version}" >&2
    return 1
  fi
  if (( major < minimum_major || (major == minimum_major && minor < minimum_minor) )); then
    echo "Docker Compose ${version} is too old; ZaroPGx requires 2.24.0 or newer." >&2
    return 1
  fi
  echo "  ✓ Docker Compose ${version} found"
}

# Function to install dependencies
install_dependencies() {
  local missing_deps=("$@")
  
  echo ""
  echo "Missing dependencies detected: ${missing_deps[*]}"
  echo ""
  
  local pkg_mgr
  pkg_mgr="$(detect_package_manager)"
  
  if [[ "$pkg_mgr" == "none" ]]; then
    echo "No supported package manager found (apt, dnf, yum, zypper, pacman, apk, brew)."
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
  
  if ! prompt_yes "Would you like to automatically install missing dependencies? (y/N) "; then
    echo "Installation cancelled. Please install dependencies manually."
    return 1
  fi
  
  # Check if we need sudo
  local sudo_cmd=""
  local install_failed=0
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
          apk) $sudo_cmd apk add --no-cache git ;;
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
              # shellcheck source=/etc/os-release
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
            start_docker_daemon "$sudo_cmd"
            ;;
          yum)
            echo "Installing Docker via official Docker repository (CentOS/YUM)..."
            # Set up Docker CE repo (CentOS)
            $sudo_cmd yum install -y yum-utils
            $sudo_cmd yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
            # Install Docker CE and Compose plugin
            $sudo_cmd yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            start_docker_daemon "$sudo_cmd"
            ;;
          dnf)
            echo "Installing Docker via official Docker repository (RHEL/Fedora/DNF)..."
            # Set up Docker CE repo (choose RHEL vs Fedora appropriately)
            $sudo_cmd dnf -y install dnf-plugins-core
            if [[ -r /etc/os-release ]]; then
              # shellcheck source=/etc/os-release
              . /etc/os-release
            fi
            if [[ "${ID:-}" = "fedora" || "${ID_LIKE:-}" =~ fedora ]]; then
              $sudo_cmd dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
            else
              $sudo_cmd dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
            fi
            # Install Docker CE and Compose plugin
            $sudo_cmd dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            start_docker_daemon "$sudo_cmd"
            ;;
          zypper)
            echo "Installing Docker via zypper (openSUSE/SUSE)..."
            # Install Docker from official SUSE repositories
            $sudo_cmd zypper refresh
            $sudo_cmd zypper install -y docker docker-compose
            start_docker_daemon "$sudo_cmd"
            ;;
          pacman)
            echo "Installing Docker via pacman (Arch Linux)..."
            $sudo_cmd pacman -Sy --noconfirm docker docker-compose
            start_docker_daemon "$sudo_cmd"
            ;;
          apk)
            echo "Installing Docker via apk (Alpine)..."
            $sudo_cmd apk add --no-cache docker docker-cli docker-cli-compose docker-openrc
            start_docker_daemon "$sudo_cmd"
            ;;
          brew)
            echo "On macOS, Docker Desktop is not installed by this script."
            echo "Install it from https://www.docker.com/products/docker-desktop"
            echo "Then re-run this bootstrap."
            install_failed=1
            ;;
        esac
        ;;
      "Docker Compose")
        case "$pkg_mgr" in
          apt)
            $sudo_cmd apt-get update
            $sudo_cmd apt-get install -y docker-compose-plugin
            ;;
          dnf) $sudo_cmd dnf install -y docker-compose-plugin ;;
          yum) $sudo_cmd yum install -y docker-compose-plugin ;;
          zypper) $sudo_cmd zypper install -y docker-compose ;;
          pacman) $sudo_cmd pacman -S --noconfirm docker-compose ;;
          apk) $sudo_cmd apk add --no-cache docker-cli-compose ;;
          brew)
            echo "Install or update Docker Desktop to get Docker Compose 2.24.0 or newer."
            install_failed=1
            ;;
        esac
        ;;
    esac
  done
  
  if [[ "$install_failed" -ne 0 ]]; then
    return 1
  fi

  local dep
  local installed_docker=0
  for dep in "${missing_deps[@]}"; do
    if [[ "$dep" == "Docker" ]]; then
      installed_docker=1
    fi
  done
  if [[ "$installed_docker" -eq 1 && "$EUID" -ne 0 ]]; then
    if command -v usermod >/dev/null 2>&1; then
      $sudo_cmd usermod -aG docker "$USER" || true
    elif command -v addgroup >/dev/null 2>&1; then
      $sudo_cmd addgroup "$USER" docker || true
    fi
    if ! docker ps >/dev/null 2>&1; then
      if $sudo_cmd docker ps >/dev/null 2>&1; then
        echo ""
        echo "Docker is installed and the daemon is running."
        echo "Log out and back in so this user joins the docker group, then re-run this bootstrap."
        echo ""
        return 2
      fi
      echo ""
      echo "Docker is installed, but the daemon is not responding."
      echo "Start it, then re-run this bootstrap."
      echo ""
      return 1
    fi
  fi

  echo ""
  echo "Dependencies installed."
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
  
  # start-docker.sh uses Compose V2 long-form env_file syntax.
  if ! require_compose_v2; then
    # Installing Docker through the supported branches installs its Compose
    # plugin too; avoid trying to install the same package twice.
    if [[ ! " ${missing_deps[*]} " =~ " Docker " ]]; then
      missing_deps+=("Docker Compose")
    fi
  fi
  
  # Handle missing dependencies
  if [[ ${#missing_deps[@]} -gt 0 ]]; then
    install_rc=0
    install_dependencies "${missing_deps[@]}" || install_rc=$?
    if [[ "$install_rc" -eq 2 ]]; then
      exit 0
    fi
    if [[ "$install_rc" -ne 0 ]]; then
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

    if ! prompt_yes "Dependencies installed. Continue with setup? (y/N) "; then
      echo "Setup cancelled. Please restart this script when ready."
      exit 0
    fi
  fi

  if ! require_compose_v2 >/dev/null; then
    echo "Docker Compose 2.24.0 or newer is required before setup can continue." >&2
    exit 1
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
    # Release hotfix tags may be replaced deliberately. Force-fetch tags so an
    # existing clone does not retain an older tag object locally.
    if ! git fetch --all --prune --force --tags; then
      echo "Error: Failed to fetch updates" >&2
      popd >/dev/null
      exit 1
    fi
    if ! git checkout "${BRANCH}"; then
      echo "Error: Failed to checkout branch ${BRANCH}" >&2
      popd >/dev/null
      exit 1
    fi
    # Release defaults are tags and therefore have no upstream to pull.
    # A caller-supplied branch still updates by fast-forward only.
    if git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' >/dev/null 2>&1; then
      if ! git pull --ff-only; then
        echo "Error: Failed to pull updates (fast-forward only)" >&2
        echo "       Repository may have diverged. Manual merge required." >&2
        popd >/dev/null
        exit 1
      fi
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
  echo "Using local development configuration (.env.local)"
  exec bash ./start-docker.sh --auto-local
else
  echo "Error: start-docker.sh not found in ${TARGET_DIR}" >&2
  exit 1
fi


