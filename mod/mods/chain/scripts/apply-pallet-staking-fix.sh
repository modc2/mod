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

# Find the pallet-staking crate path
PALLET_STAKING_PATH=$(find ~/.cargo/registry/src -path "*/pallet-staking-41.0.0/src/migrations.rs" | head -n 1)

if [ -z "$PALLET_STAKING_PATH" ]; then
  log_error "Could not find pallet-staking-41.0.0 in cargo registry"
fi

MIGRATIONS_FILE="$PALLET_STAKING_PATH"
PATCH_FILE=$(dirname "$0")/fix-pallet-staking.patch

if [ ! -f "$MIGRATIONS_FILE" ]; then
  log_error "Could not find migrations.rs file at $MIGRATIONS_FILE"
fi

if [ ! -f "$PATCH_FILE" ]; then
  log_error "Could not find patch file at $PATCH_FILE"
fi

# Create a backup
cp "$MIGRATIONS_FILE" "${MIGRATIONS_FILE}.bak"
log_info "Created backup at ${MIGRATIONS_FILE}.bak"

# Apply the patch
log_info "Applying patch to $MIGRATIONS_FILE"

# Make the file writable if it's not
if [ ! -w "$MIGRATIONS_FILE" ]; then
  chmod u+w "$MIGRATIONS_FILE" || log_error "Failed to make migrations file writable"
  log_info "Made migrations file writable"
fi

# Apply the patch with relaxed options
if patch -N -l -p0 "$MIGRATIONS_FILE" "$PATCH_FILE"; then
  log_info "Successfully applied patch to $MIGRATIONS_FILE"
else
  # If patch fails, it might be already applied
  if grep -q "#\[cfg(not(feature = \"try-runtime\"))]" "$MIGRATIONS_FILE"; then
    log_info "Patch already applied to $MIGRATIONS_FILE"
  else
    log_error "Failed to apply patch to $MIGRATIONS_FILE. Check patch file and migrations.rs for compatibility."
  fi
fi

log_info "pallet-staking fix applied successfully!"
