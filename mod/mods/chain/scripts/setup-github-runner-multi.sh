#!/bin/bash
set -e

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

log_info "Checking required tools..."
command -v docker >/dev/null || log_error "docker is required but not installed."

# Config via file or prompts
CONFIG_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -c|--config)
      CONFIG_FILE="$2"; shift 2 ;;
    *) shift ;;
  esac
done

# Defaults
: "${LABELS:=self-hosted,linux,x64,modnet-runner}"
: "${BASE_DIR:=/home/github-runner/actions-runner}"
: "${IMAGE:=tcardonne/github-runner:latest}"

if [ -n "$CONFIG_FILE" ] && [ -f "$CONFIG_FILE" ]; then
  log_info "Loading config from $CONFIG_FILE"
  # shellcheck disable=SC1090
  . "$CONFIG_FILE"
fi

# If not provided via config/env, prompt
if [ -z "$GITHUB_PAT" ]; then
  read -p "Enter GitHub Personal Access Token (repo scope): " GITHUB_PAT
fi
if [ -z "$REPOS" ]; then
  read -p "Enter repositories (comma-separated owner/repo, e.g. mod-net/chain,mod-net/bridge): " REPOS
fi

[ -z "$GITHUB_PAT" ] && log_error "GitHub PAT is required"
[ -z "$REPOS" ] && log_error "At least one repository is required"

mkdir -p "$BASE_DIR"
sudo chmod -R 777 "$BASE_DIR" || true

# Common setup-permissions script content
PERM_FIX_SCRIPT='#!/bin/bash
set -e
if getent group docker > /dev/null; then
  usermod -aG docker runner || true
fi
if [ -e /var/run/docker.sock ]; then
  chmod 666 /var/run/docker.sock || true
fi
mkdir -p /__w/_temp /__w/_actions /__w/_tool /__w/_work
chmod 777 /__w/_temp /__w/_actions /__w/_tool /__w/_work 2>/dev/null || true
mkdir -p /home/runner/_work 2>/dev/null || true
chmod 777 /home/runner/_work 2>/dev/null || true
mkdir -p /__e/node20/bin
cat > /__e/node20/bin/node << \"NODESCRIPT\"
#!/bin/sh
echo "Dummy node script executed"
exit 0
NODESCRIPT
chmod +x /__e/node20/bin/node'

# Normalize list into array
REPO_ARRAY=()
IFS=',' read -ra TMP <<< "$REPOS"
for item in "${TMP[@]}"; do
  # trim spaces
  repo=$(echo "$item" | xargs)
  [ -z "$repo" ] || REPO_ARRAY+=("$repo")
done

for OWNER_REPO in "${REPO_ARRAY[@]}"; do
  REPO_URL="https://github.com/${OWNER_REPO}"
  SAFE_NAME=${OWNER_REPO//\//_}
  RUNNER_DIR="${BASE_DIR}/${SAFE_NAME}"
  RUNNER_WORK_DIR="_work"
  RUNNER_NAME="modnet-${SAFE_NAME}-$(hostname)-$(date +%s)"
  CONTAINER_NAME="github-runner-${SAFE_NAME}"

  log_info "Configuring runner for ${REPO_URL}"
  mkdir -p "$RUNNER_DIR" && sudo chmod -R 777 "$RUNNER_DIR" || true

  # Stop any existing container for this repo
  if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    log_warn "Container ${CONTAINER_NAME} exists. Removing..."
    docker rm -f "${CONTAINER_NAME}" || true
  fi

  # Write permission fixer
  echo "$PERM_FIX_SCRIPT" > "${RUNNER_DIR}/setup-permissions.sh"
  chmod +x "${RUNNER_DIR}/setup-permissions.sh"

  log_info "Starting runner container ${CONTAINER_NAME}"
  docker run -d --name "${CONTAINER_NAME}" \
    --restart always \
    --privileged \
    --network host \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "${RUNNER_DIR}":/home/runner \
    -e GITHUB_PAT="${GITHUB_PAT}" \
    -e GITHUB_ACCESS_TOKEN="${GITHUB_PAT}" \
    -e RUNNER_REPOSITORY_URL="${REPO_URL}" \
    -e RUNNER_NAME="${RUNNER_NAME}" \
    -e RUNNER_LABELS="${LABELS}" \
    -e RUNNER_WORK_DIRECTORY="${RUNNER_WORK_DIR}" \
    -e RUNNER_TOKEN_REPO="${REPO_URL}" \
    -e DISABLE_RUNNER_UPDATE=true \
    -e RUNNER_ALLOW_RUNASROOT=true \
    "$IMAGE"

  log_info "Applying permissions inside ${CONTAINER_NAME}"
  docker exec "${CONTAINER_NAME}" bash -c "/home/runner/setup-permissions.sh" || true

done

log_info "All requested runners started. Check: docker ps | grep github-runner-"
