#!/bin/bash
set -e

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
  echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
  echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $1"
  exit 1
}

# Check required tools
log_info "Checking required tools..."
if ! command -v docker &> /dev/null; then
  log_error "docker is required but not installed. Please install it first."
fi

if ! command -v curl &> /dev/null; then
  log_error "curl is required but not installed. Please install it first."
fi

# Get GitHub repository information
read -p "Enter your GitHub username: " GITHUB_USERNAME
read -p "Enter your repository name: " GITHUB_REPO
read -p "Enter your GitHub Personal Access Token (with repo scope): " GITHUB_PAT

# Validate inputs
if [ -z "$GITHUB_USERNAME" ] || [ -z "$GITHUB_REPO" ] || [ -z "$GITHUB_PAT" ]; then
  log_error "All inputs are required"
fi

REPO_URL="https://github.com/$GITHUB_USERNAME/$GITHUB_REPO"
RUNNER_NAME="modnet-runner-$(hostname)-$(date +%s)"
RUNNER_LABELS="self-hosted,linux,x64,modnet-runner"

log_info "Setting up GitHub Actions runner for $REPO_URL"
log_info "Runner name: $RUNNER_NAME"
log_info "Runner labels: $RUNNER_LABELS"

# Create runner directory
RUNNER_DIR="/home/github-runner/actions-runner"
RUNNER_WORK_DIR="$RUNNER_DIR/_work"
mkdir -p "$RUNNER_DIR"
mkdir -p "$RUNNER_WORK_DIR"

# Set proper permissions
chmod -R 777 "$RUNNER_DIR"
cd "$RUNNER_DIR"

# Check if runner is already running
if docker ps | grep -q "github-runner"; then
  log_warn "A GitHub runner container is already running. Stopping it..."
  docker stop github-runner || true
  docker rm github-runner || true
fi

# Get current user ID and group ID
USER_ID=$(id -u)
GROUP_ID=$(id -g)

# Get Docker group ID for socket access
DOCKER_GID=$(getent group docker | cut -d: -f3 || echo "$GROUP_ID")

# Start the runner in Docker
log_info "Starting GitHub runner in Docker..."
log_info "Using user ID: $USER_ID, group ID: $GROUP_ID, Docker group ID: $DOCKER_GID"

# Add additional setup commands
cat > "$RUNNER_DIR/setup-permissions.sh" << 'EOF'
#!/bin/bash
set -e

# Add the runner user to the docker group
if getent group docker > /dev/null; then
  usermod -aG docker runner || true
fi

# Make sure the docker socket is accessible
if [ -e /var/run/docker.sock ]; then
  chmod 666 /var/run/docker.sock || true
fi

# Create necessary directories for GitHub Actions without changing permissions of existing files
# Only create directories if they don't exist
mkdir -p /__w/_temp
mkdir -p /__w/_actions
mkdir -p /__w/_tool
mkdir -p /__w/_work

# Set permissions only on newly created directories, not recursively
chmod 777 /__w/_temp 2>/dev/null || true
chmod 777 /__w/_actions 2>/dev/null || true
chmod 777 /__w/_tool 2>/dev/null || true
chmod 777 /__w/_work 2>/dev/null || true

# Create work directory in the runner home if it doesn't exist
mkdir -p /home/runner/_work 2>/dev/null || true
chmod 777 /home/runner/_work 2>/dev/null || true

# Create dummy node executable to prevent errors during cleanup
mkdir -p /__e/node20/bin
cat > /__e/node20/bin/node << 'NODESCRIPT'
#!/bin/sh
echo "Dummy node script executed"
exit 0
NODESCRIPT
chmod +x /__e/node20/bin/node
EOF

chmod +x "$RUNNER_DIR/setup-permissions.sh"

# Clean up existing runner files before starting container
log_info "Cleaning up existing runner files..."
rm -rf "$RUNNER_DIR/_work" || log_warn "Could not remove existing work directory"

# Fix permissions on host before starting container
log_info "Fixing permissions on host..."
sudo chmod -R 777 "$RUNNER_DIR" || log_warn "Could not set permissions on runner directory"
sudo chmod 666 /var/run/docker.sock || log_warn "Could not set permissions on Docker socket"

# Run container with host network and as root initially
docker run -d --name github-runner \
  --restart always \
  --privileged \
  --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$RUNNER_DIR":/home/runner \
  -e GITHUB_PAT="$GITHUB_PAT" \
  -e GITHUB_ACCESS_TOKEN="$GITHUB_PAT" \
  -e RUNNER_REPOSITORY_URL="$REPO_URL" \
  -e RUNNER_NAME="$RUNNER_NAME" \
  -e RUNNER_LABELS="$RUNNER_LABELS" \
  -e RUNNER_WORK_DIRECTORY="_work" \
  -e RUNNER_TOKEN_REPO="$REPO_URL" \
  -e DISABLE_RUNNER_UPDATE=true \
  -e RUNNER_ALLOW_RUNASROOT=true \
  tcardonne/github-runner:latest

# Execute permission fix inside container
log_info "Setting up permissions inside container..."
docker exec github-runner bash -c "/home/runner/setup-permissions.sh"

log_info "Waiting for runner to register..."
sleep 5

# Check if runner is running
if ! docker ps | grep -q "github-runner"; then
  log_error "Failed to start GitHub runner container"
fi

log_info "GitHub runner setup complete!"
log_info "Runner is now registered and ready to accept jobs"
log_info "You can check the runner status in your GitHub repository at:"
log_info "$REPO_URL/settings/actions/runners"
log_info ""
log_info "To view runner logs:"
log_info "docker logs -f github-runner"
log_info ""
log_info "To stop the runner:"
log_info "docker stop github-runner"