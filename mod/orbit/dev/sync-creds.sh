#!/bin/bash
# Re-extract the Claude Code OAuth token from the macOS Keychain into
# ~/.claude/.credentials.json so the bind-mounted container picks up the
# refreshed accessToken. Atomic + idempotent: writes to a tmp file, compares
# bytes, only swaps if changed.
#
# Wired by ~/Library/LaunchAgents/com.mod.claude.creds-sync.plist (every 15m).
set -euo pipefail

CRED_FILE="$HOME/.claude/.credentials.json"
TMP_FILE="$CRED_FILE.tmp.$$"

mkdir -p "$(dirname "$CRED_FILE")"

if ! security find-generic-password -s "Claude Code-credentials" -w > "$TMP_FILE" 2>/dev/null; then
    rm -f "$TMP_FILE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] keychain entry not found — skipping" >&2
    exit 0
fi

if [ -f "$CRED_FILE" ] && cmp -s "$TMP_FILE" "$CRED_FILE"; then
    rm -f "$TMP_FILE"
    exit 0
fi

chmod 600 "$TMP_FILE"
mv "$TMP_FILE" "$CRED_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] credentials updated"
