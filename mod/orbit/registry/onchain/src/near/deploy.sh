#!/usr/bin/env bash
set -euo pipefail

# ── NEAR Registry Deploy Script ──────────────────────────────────────────────
# Builds the WASM contract, creates a testnet account, and deploys.
#
# Usage:
#   ./deploy.sh                     # auto-generates account name
#   ./deploy.sh myregistry          # uses myregistry.testnet
#   NEAR_ACCOUNT=x ./deploy.sh     # uses env var
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

NETWORK="${NEAR_NETWORK:-testnet}"
NEAR_CLI="npx near-cli-rs"
WASM_PATH="target/near/near_registry.wasm"

# Account name: arg > env > random
if [[ -n "${1:-}" ]]; then
    ACCOUNT="${1}.testnet"
elif [[ -n "${NEAR_ACCOUNT:-}" ]]; then
    ACCOUNT="${NEAR_ACCOUNT}"
else
    ACCOUNT="mod-registry-$(date +%s).testnet"
fi

echo "═══════════════════════════════════════════════════"
echo "  NEAR Registry Deploy"
echo "  Account: $ACCOUNT"
echo "  Network: $NETWORK"
echo "═══════════════════════════════════════════════════"

# ── Build ────────────────────────────────────────────────────────────────────

echo ""
echo "▸ Checking wasm32 target..."
if ! rustup target list --installed | grep -q wasm32-unknown-unknown; then
    rustup target add wasm32-unknown-unknown
fi

echo "▸ Building contract..."
cargo near build non-reproducible-wasm

if [[ ! -f "$WASM_PATH" ]]; then
    echo "ERROR: WASM not found at $WASM_PATH"
    exit 1
fi

WASM_SIZE=$(wc -c < "$WASM_PATH" | tr -d ' ')
echo "  Built: $WASM_PATH ($WASM_SIZE bytes)"

# ── Create Account ───────────────────────────────────────────────────────────

echo ""
echo "▸ Creating testnet account: $ACCOUNT"
$NEAR_CLI account create-account fund-myself "$ACCOUNT" '200 NEAR' \
    autogenerate-new-keypair save-to-keychain \
    network-config "$NETWORK" create 2>&1 || {
    echo "  Account may already exist, continuing..."
}

# ── Deploy ───────────────────────────────────────────────────────────────────

echo ""
echo "▸ Deploying contract to $ACCOUNT..."
$NEAR_CLI contract deploy "$ACCOUNT" \
    use-file "$WASM_PATH" without-init-call \
    network-config "$NETWORK" sign-with-keychain send

# ── Initialize ───────────────────────────────────────────────────────────────

echo ""
echo "▸ Initializing contract..."
$NEAR_CLI contract call-function as-transaction \
    "$ACCOUNT" new json-args '{}' \
    prepaid-gas '30 Tgas' attached-deposit '0 NEAR' \
    sign-with-keychain network-config "$NETWORK" send

# ── Verify ───────────────────────────────────────────────────────────────────

echo ""
echo "▸ Verifying..."
$NEAR_CLI contract call-function as-read-only \
    "$ACCOUNT" next_mod_id json-args '{}' \
    network-config "$NETWORK" now

# ── Save deployment info ─────────────────────────────────────────────────────

cat > "$SCRIPT_DIR/deployment.json" <<EOF
{
    "network": "$NETWORK",
    "account": "$ACCOUNT",
    "wasm": "$WASM_PATH",
    "wasm_size": $WASM_SIZE,
    "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

# ── Update registry config.json ──────────────────────────────────────────────

REGISTRY_CONFIG="$SCRIPT_DIR/../../registry/config.json"
if command -v python3 &>/dev/null && [[ -f "$REGISTRY_CONFIG" ]]; then
    python3 -c "
import json, sys
with open('$REGISTRY_CONFIG') as f:
    cfg = json.load(f)
cfg.setdefault('near', {})
cfg['near']['$NETWORK'] = {
    'rpc': 'https://rpc.$NETWORK.near.org',
    'account': '$ACCOUNT',
    'deployed_at': '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
}
with open('$REGISTRY_CONFIG', 'w') as f:
    json.dump(cfg, f, indent=2)
print('Updated config.json')
"
fi

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Deployed successfully!"
echo "  Contract: $ACCOUNT"
echo "  Explorer: https://$NETWORK.nearblocks.io/address/$ACCOUNT"
echo "═══════════════════════════════════════════════════"
