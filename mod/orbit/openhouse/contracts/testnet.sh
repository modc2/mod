#!/usr/bin/env bash
# Deploy OpenHouse and pay one month of rent on a real chain.
#
#   ./testnet.sh                      local anvil chain, zero setup (default)
#   RPC_URL=https://sepolia.base.org OWNER_KEY=0x… BANK_KEY=0x… RENTER_KEY=0x… ./testnet.sh
#                                     Base Sepolia — needs testnet ETH on OWNER and RENTER
#
# Keys are read from the environment only. Local mode starts its own anvil
# on ANVIL_PORT (default 8547) and stops exactly that process when done.
set -euo pipefail
cd "$(dirname "$0")"
FOUNDRY=${FOUNDRY:-/root/.foundry/bin}
PATH="$FOUNDRY:$PATH"

ANVIL_PID=""
if [ -z "${RPC_URL:-}" ]; then
  PORT=${ANVIL_PORT:-8547}
  anvil --port "$PORT" --silent &
  ANVIL_PID=$!
  trap 'kill "$ANVIL_PID" 2>/dev/null || true' EXIT
  RPC_URL="http://127.0.0.1:$PORT"
  for _ in $(seq 50); do cast chain-id --rpc-url "$RPC_URL" >/dev/null 2>&1 && break; sleep 0.1; done
  echo "local chain: anvil on $RPC_URL (chain $(cast chain-id --rpc-url "$RPC_URL"))"
else
  echo "remote chain: $RPC_URL (chain $(cast chain-id --rpc-url "$RPC_URL"))"
fi

forge script script/Testnet.s.sol:Testnet --rpc-url "$RPC_URL" --broadcast --slow 2>&1 \
  | grep -E "house|tokens|principalUsd18|equityPpm|feePoolUsd18|ONCHAIN EXECUTION|Error|error|revert" || true
