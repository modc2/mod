#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
GATEWAY_PORT="${GATEWAY_PORT:-3000}"
CONFIG="$DIR/config.json"

# ── Read config.json ──
MOD_NAME=$(jq -r '.name // "mod"' "$CONFIG")
API_PORT=$(jq -r '.port // .ports.api // 8080' "$CONFIG")
APP_PORT=$(jq -r '.app_port // .ports.app // 3000' "$CONFIG")
OWNER=$(jq -r '.owner // empty' "$CONFIG" 2>/dev/null)
CID=$(jq -r '.schema // empty' "$CONFIG" 2>/dev/null)
ADMIN_PORT=$(jq -r '.caddy_admin_port // 2091' "$CONFIG")

# ── Route path: owner modules = /{name}, non-owner = /{name}/{cid} ──
if [ -n "$OWNER" ]; then
    ROUTE_PATH="/$MOD_NAME"
    CID=""
else
    if [ -n "$CID" ]; then
        ROUTE_PATH="/$MOD_NAME/$CID"
    else
        ROUTE_PATH="/$MOD_NAME"
    fi
fi

bash "$DIR/stop.sh" 2>/dev/null

# ── Start API ──
export PORT="$API_PORT"
bash "$DIR/src/api/start.sh" &
API_PID=$!

sleep 2

# ── Start App ──
export PORT="$APP_PORT"
export API_PORT="$API_PORT"
bash "$DIR/src/app/start.sh" &
APP_PID=$!

trap "kill $API_PID $APP_PID 2>/dev/null" EXIT

echo "API:  http://localhost:$API_PORT"
echo "App:  http://localhost:$APP_PORT/$MOD_NAME"

# ── Generate Caddyfile from config ──
# Non-owner modules with a CID get /{name}/{cid} externally,
# but Caddy rewrites to /{name} before proxying to the app.
CADDYFILE="$DIR/Caddyfile"

if [ -n "$CID" ]; then
cat > "$CADDYFILE" <<EOF
{
    admin localhost:$ADMIN_PORT
}

:$GATEWAY_PORT {
    @${MOD_NAME}_api path /api/${MOD_NAME} /api/${MOD_NAME}/*
    handle @${MOD_NAME}_api {
        uri strip_prefix /api/${MOD_NAME}
        reverse_proxy localhost:${API_PORT}
    }
    @${MOD_NAME}_app path /${MOD_NAME}/${CID} /${MOD_NAME}/${CID}/*
    handle @${MOD_NAME}_app {
        uri replace /${MOD_NAME}/${CID} /${MOD_NAME} 1
        reverse_proxy localhost:${APP_PORT}
    }
    handle /* {
        reverse_proxy localhost:${APP_PORT}
    }
}
EOF
else
cat > "$CADDYFILE" <<EOF
{
    admin localhost:$ADMIN_PORT
}

:$GATEWAY_PORT {
    @${MOD_NAME}_api path /api/${MOD_NAME} /api/${MOD_NAME}/*
    handle @${MOD_NAME}_api {
        uri strip_prefix /api/${MOD_NAME}
        reverse_proxy localhost:${API_PORT}
    }
    @${MOD_NAME}_app path /${MOD_NAME} /${MOD_NAME}/*
    handle @${MOD_NAME}_app {
        reverse_proxy localhost:${APP_PORT}
    }
    handle /* {
        reverse_proxy localhost:${APP_PORT}
    }
}
EOF
fi

# ── Start Caddy gateway ──
caddy stop --address "localhost:$ADMIN_PORT" 2>/dev/null
caddy start --config "$CADDYFILE" 2>/dev/null
echo "Gateway: http://localhost:$GATEWAY_PORT$ROUTE_PATH"

wait
