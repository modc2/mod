#!/usr/bin/env bash
# Weekly reward-pool keeper.
#
# The pool (the Treasury, funded by $1 MOD mints) is distributed to BlocTime
# holders pull-style — each holder claims their pro-rata share via withdrawToken.
# This keeper runs once a week to record a snapshot of the pool (size + total
# BlocTime) into the epoch log (~/.mod/chain/pool_epochs.json), which the web
# app surfaces as "this week's pool". Install via crontab (see install note at
# the bottom). Honors $CHAIN_API_URL and $CHAIN_NETWORK.
set -euo pipefail
CHAIN_API_URL="${CHAIN_API_URL:-http://localhost:8800}"
CHAIN_NETWORK="${CHAIN_NETWORK:-testnet}"

curl -fsS --max-time 60 -X POST "${CHAIN_API_URL}/pool/snapshot" \
  -H 'content-type: application/json' \
  -d "{\"network\":\"${CHAIN_NETWORK}\"}" \
  && echo " [pool-keeper] snapshot recorded $(date -u +%FT%TZ)"

# Install (weekly, Sundays 00:00 UTC):
#   ( crontab -l 2>/dev/null; echo "0 0 * * 0 $(pwd)/pool_keeper.sh >> /tmp/pool_keeper.log 2>&1" ) | crontab -
