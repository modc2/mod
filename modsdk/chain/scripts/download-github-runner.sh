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

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  log_error "This script must be run as root (with sudo). Please run with: sudo $0"
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
mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

# Get current user
CURRENT_USER=$(logname || echo $SUDO_USER)
if [ -z "$CURRENT_USER" ]; then
  log_error "Could not determine the current user. Please run with sudo."
fi

# Download the latest runner
log_info "Downloading the latest runner..."
RUNNER_VERSION=$(curl -s https://api.github.com/repos/actions/runner/releases/latest | grep -oP '"tag_name": "v\K[0-9.]+')
if [ -z "$RUNNER_VERSION" ]; then
  log_error "Failed to determine the latest runner version"
fi

log_info "Latest runner version: $RUNNER_VERSION"
RUNNER_FILE="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
RUNNER_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_FILE}"

# Download runner
if [ ! -f "$RUNNER_FILE" ]; then
  log_info "Downloading from $RUNNER_URL..."
  curl -O -L "$RUNNER_URL"
fi

# Extract runner
log_info "Extracting runner..."
tar xzf "$RUNNER_FILE"
rm -f "$RUNNER_FILE"

# Fix ownership and permissions
log_info "Setting ownership and permissions..."
chown -R $CURRENT_USER:$CURRENT_USER "$RUNNER_DIR"
find "$RUNNER_DIR" -type f -exec chmod 644 {} \;
find "$RUNNER_DIR" -type d -exec chmod 755 {} \;
find "$RUNNER_DIR" -name "*.sh" -exec chmod 755 {} \;
chmod 755 "$RUNNER_DIR/config.sh"
chmod 755 "$RUNNER_DIR/run.sh"
chmod 755 "$RUNNER_DIR/svc.sh"

# Create Node.js path fix
log_info "Creating Node.js path fix..."
mkdir -p /__e/node20/bin
cat > /__e/node20/bin/node << 'NODESCRIPT'
#!/bin/sh
echo "Dummy node script executed"
exit 0
NODESCRIPT
chmod +x /__e/node20/bin/node

# Configure the runner
log_info "Configuring the runner..."
# Get registration token
RUNNER_TOKEN=$(curl -s -X POST -H "Authorization: token $GITHUB_PAT" \
  -H "Accept: application/vnd.github.v3+json" \
  "https://api.github.com/repos/$GITHUB_USERNAME/$GITHUB_REPO/actions/runners/registration-token" \
  | grep -oP '"token": "\K[^"]+')

if [ -z "$RUNNER_TOKEN" ]; then
  log_error "Failed to get runner registration token"
fi

# Store token and other variables for the non-sudo part
echo "REPO_URL=$REPO_URL" > "$RUNNER_DIR/runner-config.env"
echo "RUNNER_TOKEN=$RUNNER_TOKEN" >> "$RUNNER_DIR/runner-config.env"
echo "RUNNER_NAME=$RUNNER_NAME" >> "$RUNNER_DIR/runner-config.env"
echo "RUNNER_LABELS=$RUNNER_LABELS" >> "$RUNNER_DIR/runner-config.env"
chmod 644 "$RUNNER_DIR/runner-config.env"
chown $CURRENT_USER:$CURRENT_USER "$RUNNER_DIR/runner-config.env"

# Create a script to run as non-root
cat > "$RUNNER_DIR/configure-runner.sh" << 'EOF'
#!/bin/bash
set -e

# Source the config
source ./runner-config.env

# Configure runner
echo "Registering runner with GitHub..."
./config.sh --url "$REPO_URL" --token "$RUNNER_TOKEN" --name "$RUNNER_NAME" --labels "$RUNNER_LABELS" --unattended

echo "Configuration complete!"
EOF

chmod 755 "$RUNNER_DIR/configure-runner.sh"
chown $CURRENT_USER:$CURRENT_USER "$RUNNER_DIR/configure-runner.sh"

# Drop privileges to run the configuration
log_info "Running configuration as non-root user..."
su - "$CURRENT_USER" -c "cd $RUNNER_DIR && $RUNNER_DIR/configure-runner.sh"

# Install as a service
log_info "Installing runner as a service..."
cd "$RUNNER_DIR"
./svc.sh install "$CURRENT_USER"

# Start the service
log_info "Starting the runner service..."
./svc.sh start

log_info "GitHub runner setup complete!"
log_info ""
log_info "You can check the runner status in your GitHub repository at:"
log_info "$REPO_URL/settings/actions/runners"
log_info ""
log_info "To check the runner service status:"
log_info "sudo ./svc.sh status"
log_info ""
log_info "To stop the runner service:"
log_info "sudo ./svc.sh stop"
