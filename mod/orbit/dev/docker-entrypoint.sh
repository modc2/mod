#!/bin/bash
# Entrypoint runs as root to set up the credential mount, then drops to a
# non-root user (`claude`, uid 1000) before launching anything that will
# spawn the `claude` CLI. Claude Code refuses --dangerously-skip-permissions
# when invoked as root, so the privilege drop is load-bearing.
set -e

API_PORT="${API_PORT:-8870}"
APP_PORT="${APP_PORT:-8871}"

CRED_SRC="/host-claude/.credentials.json"
CRED_DIR="/home/node/.claude"
CRED_DST="$CRED_DIR/.credentials.json"

mkdir -p "$CRED_DIR"

if [ -f "$CRED_SRC" ]; then
    # Symlink so the CLI reads the live host file and can write refreshed tokens
    ln -sf "$CRED_SRC" "$CRED_DST"
    # The CLI runs as node (uid 1000); the host file is typically root-owned 0600
    # (that's how `claude login` writes it), which node can't read — every job
    # then fails with "Not logged in". Hand the real file to node so it can both
    # read the token and persist refreshed ones. The host runs claude as root,
    # which ignores ownership, so this costs the host nothing.
    chown node:node "$CRED_SRC" 2>/dev/null || chmod 0644 "$CRED_SRC" 2>/dev/null || true
    # A host re-login (or sync) rewrites the file as root:0600 mid-run, which
    # silently locks node out again until a restart — the recurring "Not logged
    # in" failure. Spawn a root-side guard that re-hands the file to node every
    # few seconds so access self-heals without a restart. Backgrounded BEFORE the
    # privilege drop so it keeps root euid (node can't chown a root-owned file);
    # `exec runuser` later replaces this shell but leaves this child running.
    (
        while true; do
            if [ -f "$CRED_SRC" ] && [ "$(stat -c %u "$CRED_SRC" 2>/dev/null)" != "1000" ]; then
                chown node:node "$CRED_SRC" 2>/dev/null || chmod 0644 "$CRED_SRC" 2>/dev/null || true
            fi
            sleep 5
        done
    ) &
    echo "credentials: ownership guard running (re-hands host file to node on change)"
    # When OAuth creds are present, unset any inherited ANTHROPIC_API_KEY so
    # claude doesn't prefer a stale/external key over the subscription token.
    unset ANTHROPIC_API_KEY
    echo "credentials: linked from host mount (OAuth subscription, ANTHROPIC_API_KEY ignored)"
elif [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "credentials: using ANTHROPIC_API_KEY env (no host mount)"
else
    echo "credentials: NONE — claude CLI will fail until you mount ~/.claude/.credentials.json or set ANTHROPIC_API_KEY" >&2
fi

# -h so we don't follow the credentials symlink (its target is chowned above).
chown -h node:node "$CRED_DIR" "$CRED_DST" 2>/dev/null || true

# Off-chain auth state (whitelist.json, gate.json, owner.json) — host-mounted.
# Only chown the top-level dir; `chown -R` over the blob store (~12k files on
# slow virtiofs) hangs the entrypoint for minutes. Files are written as `node`
# anyway, so recursive ownership isn't needed.
PRIVATE_DIR="/home/node/.mod/dev"
mkdir -p "$PRIVATE_DIR"
chown node:node "$PRIVATE_DIR" 2>/dev/null || true

# Inner script runs as non-root: starts Rust API + Next.js, traps shutdown.
# Clear any stale copy from a prior boot so a restart can recreate it cleanly.
rm -f /tmp/run-as-build.sh 2>/dev/null || true
cat > /tmp/run-as-build.sh <<EOF
#!/bin/bash
set -e
API_PORT=$API_PORT
APP_PORT=$APP_PORT

# runuser doesn't reset HOME by default — force it to node's home so the
# claude CLI can find ~/.claude/.credentials.json (the OAuth subscription token).
export HOME=/home/node
export USER=node

PORT=\$API_PORT /app/bin/dev-jobs &
API_PID=\$!
echo "dev-jobs API on :\$API_PORT (pid \$API_PID)"

for i in \$(seq 1 30); do
    if curl -sf "http://localhost:\$API_PORT/health" > /dev/null 2>&1; then
        echo "API ready"
        break
    fi
    sleep 1
done

cd /app/src/app
NEXT_PUBLIC_BASE_PATH="/dev" \\
PORT=\$APP_PORT \\
HOSTNAME="0.0.0.0" \\
npx next start -p \$APP_PORT -H 0.0.0.0 &
APP_PID=\$!
echo "next.js app on :\$APP_PORT (pid \$APP_PID)"

cleanup() {
    echo "shutting down..."
    kill \$APP_PID \$API_PID 2>/dev/null || true
    wait \$APP_PID \$API_PID 2>/dev/null || true
}
trap cleanup SIGTERM SIGINT

# Keep the container alive as long as the API (the load-bearing service) is up.
# next start can exit when the bind-mounted host build is stale; that must NOT
# tear the container down, since the user-facing app is served separately.
wait \$API_PID
EXIT=\$?
cleanup
exit \$EXIT
EOF
chmod +x /tmp/run-as-build.sh
chown node:node /tmp/run-as-build.sh

exec runuser -u node -- /tmp/run-as-build.sh
