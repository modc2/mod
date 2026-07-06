#!/bin/bash
# Entrypoint for codex orbit module. Starts the Rust codex-jobs API + Next.js
# app, both running as the `node` user. The OpenAI key is sourced from the
# encrypted ciphertext store (mounted at /home/node/.mod/codex) on demand via
# the Rust API's /key endpoint — the OPENAI_API_KEY env is a fallback only.
set -e

API_PORT="${API_PORT:-8830}"
APP_PORT="${APP_PORT:-8831}"

PRIVATE_DIR="/home/node/.mod/codex"
mkdir -p "$PRIVATE_DIR"
chown -R node:node "$PRIVATE_DIR" 2>/dev/null || true

if [ -n "$OPENAI_API_KEY" ]; then
    echo "credentials: using OPENAI_API_KEY env (fallback)"
else
    echo "credentials: relying on encrypted ciphertext store at $PRIVATE_DIR/keys.json (set per-wallet via /key)"
fi

cat > /tmp/run-as-codex.sh <<EOF
#!/bin/bash
set -e
API_PORT=$API_PORT
APP_PORT=$APP_PORT

PORT=\$API_PORT /app/bin/codex-jobs &
API_PID=\$!
echo "codex-jobs API on :\$API_PORT (pid \$API_PID)"

for i in \$(seq 1 30); do
    if curl -sf "http://localhost:\$API_PORT/health" > /dev/null 2>&1; then
        echo "API ready"
        break
    fi
    sleep 1
done

if [ -d /app/src/app ]; then
    cd /app/src/app
    NEXT_PUBLIC_API_URL="http://localhost:\$API_PORT" \\
    NEXT_PUBLIC_BASE_PATH="/codex" \\
    PORT=\$APP_PORT \\
    HOSTNAME="0.0.0.0" \\
    npx next start -p \$APP_PORT -H 0.0.0.0 &
    APP_PID=\$!
    echo "next.js app on :\$APP_PORT (pid \$APP_PID)"
fi

cleanup() {
    echo "shutting down..."
    kill \${APP_PID:-} \$API_PID 2>/dev/null || true
    wait \${APP_PID:-} \$API_PID 2>/dev/null || true
}
trap cleanup SIGTERM SIGINT

if [ -n "\${APP_PID:-}" ]; then
    wait -n \$API_PID \$APP_PID
else
    wait \$API_PID
fi
EXIT=\$?
cleanup
exit \$EXIT
EOF
chmod +x /tmp/run-as-codex.sh
chown node:node /tmp/run-as-codex.sh

if command -v gosu >/dev/null 2>&1; then
    exec gosu node /tmp/run-as-codex.sh
else
    exec /tmp/run-as-codex.sh
fi
