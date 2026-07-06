#!/bin/bash
# Dev variant of docker-entrypoint.sh: same cred + API setup, but runs
# `next dev` against the host-mounted source so edits hot-reload.
# Used via docker-compose.override.yml (which also mounts src/app/src
# from the host over the image's baked copy).
set -e

API_PORT="${API_PORT:-8820}"
APP_PORT="${APP_PORT:-8823}"

CRED_SRC="/host-claude/.credentials.json"
CRED_DIR="/home/node/.claude"
CRED_DST="$CRED_DIR/.credentials.json"

mkdir -p "$CRED_DIR"

if [ -f "$CRED_SRC" ]; then
    ln -sf "$CRED_SRC" "$CRED_DST"
    unset ANTHROPIC_API_KEY
    echo "credentials: linked from host mount (OAuth subscription)"

    # Validate the OAuth token at boot. The historical 401-loop failure
    # was a stale/expired token on the host that nobody noticed until a
    # job ran. Fail loudly here so the operator re-logins on the host
    # (`claude` on the host refreshes ~/.claude/.credentials.json) before
    # any job is submitted, instead of mid-run.
    python3 - <<'PYEOF' || echo "credentials: WARNING — validation skipped" >&2
import json, sys, time, os
try:
    with open("/host-claude/.credentials.json") as f:
        d = json.load(f)
    oauth = d.get("claudeAiOauth") or {}
    access = oauth.get("accessToken")
    expires_ms = oauth.get("expiresAt")
    if not access:
        print("credentials: ERROR — no accessToken in file", file=sys.stderr)
        sys.exit(1)
    if not expires_ms:
        print("credentials: WARNING — no expiresAt field; cannot validate", file=sys.stderr)
        sys.exit(0)
    now_ms = int(time.time() * 1000)
    remaining_s = (expires_ms - now_ms) // 1000
    if remaining_s <= 0:
        print(f"credentials: ERROR — OAuth token EXPIRED {-remaining_s}s ago. Run `claude` on the host to refresh.", file=sys.stderr)
        sys.exit(2)
    h = remaining_s // 3600
    m = (remaining_s % 3600) // 60
    print(f"credentials: token valid (expires in {h}h{m}m)")
    if remaining_s < 3600:
        print(f"credentials: WARNING — token expires in {remaining_s//60}m; consider refreshing on host", file=sys.stderr)
except Exception as e:
    print(f"credentials: ERROR parsing host file: {e}", file=sys.stderr)
    sys.exit(3)
PYEOF
elif [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "credentials: using ANTHROPIC_API_KEY env"
else
    echo "credentials: NONE" >&2
fi

chown -h node:node "$CRED_DIR" "$CRED_DST" 2>/dev/null || true

PRIVATE_DIR="/home/node/.mod/claude"
mkdir -p "$PRIVATE_DIR"
chown node:node "$PRIVATE_DIR" 2>/dev/null || true

# The image's baked .next/ was created during `next build` (probably as
# root). next dev tries to unlink BUILD_ID and rebuild — give the node user
# ownership, and wipe it so dev starts from a clean slate.
chown -R node:node /app/src/app/.next 2>/dev/null || true
rm -rf /app/src/app/.next/* 2>/dev/null || true

# DooD: claude needs the docker CLI + read/write on the host socket so it
# can orchestrate sibling containers. The container has no outbound
# network on modnet (Docker Desktop quirk), so apt install is doomed —
# we provision the binary via `docker cp` once from the host static
# tarball: download.docker.com/linux/static/stable/aarch64/docker-*.tgz.
# Skip if the CLI is already in place (rebuilt image or prior copy).
if ! command -v docker >/dev/null 2>&1; then
    echo "[dev] docker CLI not installed — copy linux/arm64 binary to /usr/local/bin/docker" >&2
fi
# The host socket arrives as root:root mode 660 from Docker Desktop on
# Mac. Open it to all users in this container so `node` (the runtime
# user for claude-jobs / next dev) can call docker without sudo.
# Single-user dev only: anyone in this container is already root-equivalent
# on the host via the socket, so 666 doesn't add new exposure.
if [ -S /var/run/docker.sock ]; then
    chmod 666 /var/run/docker.sock 2>/dev/null || true
    echo "[dev] docker socket mounted: $(stat -c '%a %u:%g' /var/run/docker.sock 2>/dev/null)"
fi

# Boot-time claude-code upgrade: the image bakes a specific version, and
# `docker compose up -d --force-recreate` wipes any prior in-place upgrade.
# If a staged tarball pair is mounted at /root/cc-stage, install over the
# image version (idempotent — skips when versions already match).
CC_STAGE=/root/cc-stage
if [ -f "$CC_STAGE/wrapper.tgz" ] && [ -f "$CC_STAGE/native-arm64.tgz" ]; then
    STAGED_VERSION=$(tar -xzf "$CC_STAGE/wrapper.tgz" -O package/package.json 2>/dev/null | grep -oE '"version":\s*"[^"]+"' | head -1 | cut -d'"' -f4)
    CURRENT_VERSION=$(/usr/local/bin/claude --version 2>/dev/null | awk '{print $1}')
    if [ -n "$STAGED_VERSION" ] && [ "$STAGED_VERSION" != "$CURRENT_VERSION" ]; then
        echo "[dev] upgrading claude-code: $CURRENT_VERSION -> $STAGED_VERSION"
        CC_DIR=/usr/local/lib/node_modules/@anthropic-ai/claude-code
        NATIVE_DIR=/usr/local/lib/node_modules/@anthropic-ai/claude-code-linux-arm64
        rm -rf "$CC_DIR" "$NATIVE_DIR" 2>/dev/null
        mkdir -p "$CC_DIR" "$NATIVE_DIR"
        tar -xzf "$CC_STAGE/wrapper.tgz" -C "$CC_DIR" --strip-components=1
        tar -xzf "$CC_STAGE/native-arm64.tgz" -C "$NATIVE_DIR" --strip-components=1
        (cd "$CC_DIR" && node install.cjs >/dev/null 2>&1) || true
        ln -sf "$CC_DIR/bin/claude.exe" /usr/local/bin/claude
        chmod +x "$CC_DIR/bin/claude.exe" 2>/dev/null
        echo "[dev] claude-code now $(claude --version 2>/dev/null)"
    fi
fi

cat > /tmp/run-as-claude.sh <<EOF
#!/bin/bash
set -e
API_PORT=$API_PORT
APP_PORT=$APP_PORT

export HOME=/home/node
export USER=node

# Capture stdout+stderr to log files the in-app LOGS tab can read via
# the standard /tmp/mod-{api,app}-{name}.log convention.
API_LOG=/tmp/mod-api-claude.log
APP_LOG=/tmp/mod-app-claude.log
: > "\$API_LOG"
: > "\$APP_LOG"

PORT=\$API_PORT /app/bin/claude-jobs >> "\$API_LOG" 2>&1 &
API_PID=\$!
echo "[dev] claude-jobs API on :\$API_PORT (pid \$API_PID) -> \$API_LOG"

for i in \$(seq 1 30); do
    if curl -sf "http://localhost:\$API_PORT/health" > /dev/null 2>&1; then
        echo "[dev] API ready"
        break
    fi
    sleep 1
done

cd /app/src/app
NEXT_PUBLIC_BASE_PATH="/claude" \\
PORT=\$APP_PORT \\
HOSTNAME="0.0.0.0" \\
NEXT_TELEMETRY_DISABLED=1 \\
npx next dev -p \$APP_PORT -H 0.0.0.0 >> "\$APP_LOG" 2>&1 &
APP_PID=\$!
echo "[dev] next.js dev server on :\$APP_PORT (pid \$APP_PID) -> \$APP_LOG"

cleanup() {
    echo "[dev] shutting down..."
    kill \$APP_PID \$API_PID 2>/dev/null || true
    wait \$APP_PID \$API_PID 2>/dev/null || true
}
trap cleanup SIGTERM SIGINT

wait -n \$API_PID \$APP_PID
EXIT=\$?
cleanup
exit \$EXIT
EOF
chmod +x /tmp/run-as-claude.sh
chown node:node /tmp/run-as-claude.sh

exec runuser -u node -- /tmp/run-as-claude.sh
