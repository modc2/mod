#!/bin/bash
set -e

API_PORT=50091
APP_PORT=3091
GATEWAY_PORT="${GATEWAY_PORT:-3000}"

echo "── polymarket dev ──"

# ── Start Rust API (only if the binary is present — dev-only image
#    that ships without it should skip silently and rely on
#    POLYMARKET_API_URL set externally) ──
if [ -x /app/bin/polymarket-api ]; then
    PORT=$API_PORT STRAT_HMAC_SECRET="${STRAT_HMAC_SECRET:-}" /app/bin/polymarket-api &
    API_PID=$!
    echo "API starting on :$API_PORT (pid $API_PID)"
    for i in $(seq 1 30); do
        if curl -sf http://localhost:$API_PORT/health > /dev/null 2>&1; then
            echo "API ready"
            break
        fi
        sleep 1
    done
else
    echo "No /app/bin/polymarket-api — relying on POLYMARKET_API_URL=$POLYMARKET_API_URL"
fi

# ── Build CID + onchain pin ──
# Prefer the localfs/IPFS CID published onchain via
# `m polymarket/publish_build` (recorded in /app/config.json under
# build_onchain). Falls back to a deterministic sha256 manifest so a
# never-published build still has a verifiable hash.
BUILD_CID=""
BUILD_TX=""
BUILD_SCAN=""
if [ -f /app/config.json ]; then
    BUILD_CID=$(python3 -c "import json; print(json.load(open('/app/config.json')).get('build_onchain',{}).get('cid',''))" 2>/dev/null || echo "")
    BUILD_TX=$(python3 -c "import json; print(json.load(open('/app/config.json')).get('build_onchain',{}).get('tx_hash',''))" 2>/dev/null || echo "")
    BUILD_SCAN=$(python3 -c "import json; print(json.load(open('/app/config.json')).get('build_onchain',{}).get('scan',''))" 2>/dev/null || echo "")
fi
cd /app/src/app

# ── Sync node_modules with host package.json ──
# The dev compose mounts the source dir from the host but keeps node_modules
# as an anonymous volume populated by `npm ci` at image-build time. That
# means adding a new dep on the host (package.json updated) leaves the
# running container with stale node_modules and Next.js fails with
# "Module not found". Re-run npm install when package.json is newer than
# the installed tree, so a host-side `npm i foo` propagates on container
# restart without needing a full image rebuild.
if [ ! -d node_modules ] || [ package.json -nt node_modules/.package-lock.json ] 2>/dev/null; then
    echo "deps out of sync — running npm install"
    npm install --no-audit --no-fund --prefer-offline
fi

if [ -z "$BUILD_CID" ]; then
    if command -v sha256sum > /dev/null 2>&1; then
        BUILD_HASH=$(find app -type f \( -name '*.tsx' -o -name '*.ts' -o -name '*.css' \) \
            | sort \
            | xargs sha256sum 2>/dev/null \
            | sha256sum \
            | cut -d' ' -f1)
        BUILD_CID="sha256:${BUILD_HASH}"
    else
        BUILD_CID="dev-$(date +%s)"
    fi
fi
BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "Build CID: $BUILD_CID${BUILD_TX:+ (tx: $BUILD_TX)}"

# ── Start Next.js (dev mode with hot-reload) ──
POLYMARKET_API_URL="${POLYMARKET_API_URL:-http://localhost:$API_PORT}" \
NEXT_PUBLIC_API_URL="/api/polymarket" \
NEXT_PUBLIC_BASE_PATH="/polymarket" \
NEXT_PUBLIC_BUILD_CID="$BUILD_CID" \
NEXT_PUBLIC_BUILD_TIME="$BUILD_TIME" \
NEXT_PUBLIC_BUILD_TX="$BUILD_TX" \
NEXT_PUBLIC_BUILD_SCAN="$BUILD_SCAN" \
PORT=$APP_PORT \
npx next dev -p $APP_PORT &
APP_PID=$!
echo "App (dev) starting on :$APP_PORT (pid $APP_PID)"
cd /app

# Wait for app to be ready
for i in $(seq 1 60); do
    if curl -sf http://localhost:$APP_PORT/polymarket > /dev/null 2>&1; then
        echo "App ready"
        break
    fi
    sleep 1
done

# ── Generate Caddyfile (only if caddy is installed in this image) ──
if command -v caddy > /dev/null 2>&1; then
    WHITEPAPER_HOST="${WHITEPAPER_HOST:-host.docker.internal}"
    WHITEPAPER_HOST_API_PORT="${WHITEPAPER_HOST_API_PORT:-50106}"
    WHITEPAPER_HOST_APP_PORT="${WHITEPAPER_HOST_APP_PORT:-3106}"

    cat > /app/Caddyfile <<EOF
{
    admin localhost:2019
}

:${GATEWAY_PORT} {
    @api path /api/polymarket /api/polymarket/*
    handle @api {
        uri strip_prefix /api/polymarket
        reverse_proxy localhost:${API_PORT}
    }

    @l2 path /api/polymarket-l2 /api/polymarket-l2/*
    handle @l2 {
        uri strip_prefix /api/polymarket-l2
        reverse_proxy https://clob.polymarket.com {
            header_up Host clob.polymarket.com
        }
    }

    @whitepaper_api path /api/whitepaper /api/whitepaper/*
    handle @whitepaper_api {
        uri strip_prefix /api/whitepaper
        reverse_proxy ${WHITEPAPER_HOST}:${WHITEPAPER_HOST_API_PORT}
    }
    handle /whitepaper* {
        reverse_proxy ${WHITEPAPER_HOST}:${WHITEPAPER_HOST_APP_PORT}
    }

    handle /* {
        reverse_proxy localhost:${APP_PORT}
    }
}
EOF

    caddy run --config /app/Caddyfile &
    CADDY_PID=$!
    echo "Gateway on :$GATEWAY_PORT (pid $CADDY_PID)"
fi

echo ""
echo "  API:     http://localhost:$API_PORT/health"
echo "  App:     http://localhost:$APP_PORT/polymarket"
[ -n "${CADDY_PID:-}" ] && echo "  Gateway: http://localhost:$GATEWAY_PORT/polymarket"
echo ""

# ── Handle shutdown ──
cleanup() {
    echo "shutting down..."
    kill ${CADDY_PID:-} ${APP_PID:-} ${API_PID:-} 2>/dev/null || true
    wait ${CADDY_PID:-} ${APP_PID:-} ${API_PID:-} 2>/dev/null || true
}
trap cleanup SIGTERM SIGINT

# Wait on whichever processes we actually started. wait -n returns on
# the first one to exit; we then cleanup and exit.
PIDS=""
[ -n "${API_PID:-}" ] && PIDS="$PIDS $API_PID"
[ -n "${APP_PID:-}" ] && PIDS="$PIDS $APP_PID"
[ -n "${CADDY_PID:-}" ] && PIDS="$PIDS $CADDY_PID"
# shellcheck disable=SC2086
wait -n $PIDS
EXIT_CODE=$?
echo "process exited with code $EXIT_CODE — stopping all"
cleanup
exit $EXIT_CODE
