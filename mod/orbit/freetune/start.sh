#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
GATEWAY_PORT="${GATEWAY_PORT:-3000}"
CONFIG="$DIR/config.json"

MOD_NAME=$(jq -r '.name // "freetune"' "$CONFIG")
API_PORT=$(jq -r '.port // 50210' "$CONFIG")
APP_PORT=$(jq -r '.app_port // 50211' "$CONFIG")
ADMIN_PORT=$(jq -r '.caddy_admin_port // 2110' "$CONFIG")

bash "$DIR/stop.sh" 2>/dev/null

export PORT="$API_PORT"
bash "$DIR/src/api/start.sh" &
API_PID=$!
sleep 2

export PORT="$APP_PORT"
export API_PORT="$API_PORT"
bash "$DIR/src/app/start.sh" &
APP_PID=$!

trap "kill $API_PID $APP_PID 2>/dev/null" EXIT
echo "API:  http://localhost:$API_PORT"
echo "App:  http://localhost:$APP_PORT/$MOD_NAME"

# ── Gateway: /api/{name} → API, /{name} → app (mod protocol URL convention) ──
CADDYFILE="$DIR/Caddyfile"
cat > "$CADDYFILE" <<CADDY
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
CADDY

caddy stop --address "localhost:$ADMIN_PORT" 2>/dev/null
caddy start --config "$CADDYFILE" 2>/dev/null
echo "Gateway: http://localhost:$GATEWAY_PORT/$MOD_NAME"
wait
