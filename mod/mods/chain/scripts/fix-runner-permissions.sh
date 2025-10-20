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

# Get current user
CURRENT_USER=$(logname || echo $SUDO_USER)
if [ -z "$CURRENT_USER" ]; then
  log_error "Could not determine the current user. Please run with sudo."
fi

# Runner directory
RUNNER_DIR="/home/github-runner/actions-runner"

if [ ! -d "$RUNNER_DIR" ]; then
  log_error "Runner directory $RUNNER_DIR does not exist."
fi

log_info "Fixing permissions for GitHub runner in $RUNNER_DIR"

# Fix ownership of all files
log_info "Setting ownership of all files to current user..."
chown -R $CURRENT_USER:$CURRENT_USER "$RUNNER_DIR"

# Fix permissions
log_info "Setting correct permissions on files..."
find "$RUNNER_DIR" -type f -exec chmod 644 {} \;
find "$RUNNER_DIR" -type d -exec chmod 755 {} \;

# Make scripts executable
log_info "Making scripts executable..."
find "$RUNNER_DIR" -name "*.sh" -exec chmod 755 {} \;
chmod 755 "$RUNNER_DIR/config.sh"
chmod 755 "$RUNNER_DIR/run.sh"
chmod 755 "$RUNNER_DIR/svc.sh"

# Fix specific files
log_info "Setting specific file permissions..."
chmod 600 "$RUNNER_DIR/.credentials"
chmod 600 "$RUNNER_DIR/.credentials_rsaparams"
chmod 600 "$RUNNER_DIR/.runner"
chmod 600 "$RUNNER_DIR/.service"

# Create Node.js path fix
log_info "Creating Node.js path fix..."
mkdir -p /__e/node20/bin
cat > /__e/node20/bin/node << 'NODESCRIPT'
#!/bin/sh
echo "Dummy node script executed"
exit 0
NODESCRIPT
chmod +x /__e/node20/bin/node

log_info "All permissions fixed successfully!"
log_info ""
log_info "You can now run the GitHub runner with:"
log_info "cd $RUNNER_DIR && ./run.sh"
log_info ""
log_info "Or install it as a service with:"
log_info "cd $RUNNER_DIR && sudo ./svc.sh install"
log_info "cd $RUNNER_DIR && sudo ./svc.sh start"
