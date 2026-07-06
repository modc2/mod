#!/bin/bash
set -e

# NearTensor deployment script
# Deploys governance token, registry, and genesis subnet to NEAR testnet

NETWORK="${NETWORK:-testnet}"
ACCOUNT="${1:-neartensor.testnet}"
GOV_ACCOUNT="gov.${ACCOUNT}"

echo "=== NearTensor Deployment ==="
echo "Network: ${NETWORK}"
echo "Registry: ${ACCOUNT}"
echo "Governance: ${GOV_ACCOUNT}"
echo ""

# 1. Build all contracts
echo ">>> Building contracts..."
cargo near build non-reproducible-wasm
echo ""

# 2. Check WASM files exist
SUBNET_WASM="target/near/neartensor_subnet.wasm"
REGISTRY_WASM="target/near/neartensor_registry.wasm"
GOV_WASM="target/near/neartensor_governance.wasm"

for wasm in "$SUBNET_WASM" "$REGISTRY_WASM" "$GOV_WASM"; do
    if [ ! -f "$wasm" ]; then
        echo "ERROR: WASM not found: $wasm"
        exit 1
    fi
    echo "  $(basename $wasm): $(du -h $wasm | cut -f1)"
done
echo ""

# 3. Deploy governance token
echo ">>> Deploying governance token to ${GOV_ACCOUNT}..."
npx near-cli-rs contract deploy "${GOV_ACCOUNT}" \
    use-file "${GOV_WASM}" without-init-call \
    network-config ${NETWORK} sign-with-keychain send

npx near-cli-rs contract call-function as-transaction \
    "${GOV_ACCOUNT}" new \
    json-args '{"name":"NearTensor Governance","symbol":"NTGOV"}' \
    prepaid-gas '30 Tgas' attached-deposit '0 NEAR' \
    sign-with-keychain network-config ${NETWORK} send
echo ""

# 4. Deploy registry
echo ">>> Deploying registry to ${ACCOUNT}..."
npx near-cli-rs contract deploy "${ACCOUNT}" \
    use-file "${REGISTRY_WASM}" without-init-call \
    network-config ${NETWORK} sign-with-keychain send

npx near-cli-rs contract call-function as-transaction \
    "${ACCOUNT}" new \
    json-args "{\"governance_token\":\"${GOV_ACCOUNT}\",\"registration_cost\":\"1000000000000000000000000\",\"immunity_period\":86400}" \
    prepaid-gas '30 Tgas' attached-deposit '0 NEAR' \
    sign-with-keychain network-config ${NETWORK} send
echo ""

# 5. Store subnet WASM in registry
echo ">>> Storing subnet WASM in registry ($(du -h ${SUBNET_WASM} | cut -f1))..."
echo "  (This requires a borsh-encoded call - use mod.py for this step)"
echo ""

# 6. Mint governance tokens
echo ">>> Minting governance tokens..."
npx near-cli-rs contract call-function as-transaction \
    "${GOV_ACCOUNT}" mint \
    json-args "{\"to\":\"${ACCOUNT}\",\"amount\":\"100000000000000000000000000\"}" \
    prepaid-gas '30 Tgas' attached-deposit '0 NEAR' \
    sign-with-keychain network-config ${NETWORK} send
echo ""

echo "=== Deployment Complete ==="
echo "Registry:   ${ACCOUNT}"
echo "Governance: ${GOV_ACCOUNT}"
echo "Explorer:   https://testnet.nearblocks.io/address/${ACCOUNT}"
echo ""
echo "Next steps:"
echo "  1. Store subnet WASM: m neartensor/store_wasm"
echo "  2. Register genesis subnet: m neartensor/register_subnet name=genesis"
echo "  3. Register validators: m neartensor/register_validator subnet_id=0 key=<pubkey>"
