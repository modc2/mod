#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$DIR/config.json"

API_PORT=$(jq -r '.port // .ports.api // 8080' "$CONFIG")
APP_PORT=$(jq -r '.app_port // .ports.app // 3000' "$CONFIG")
ADMIN_PORT=$(jq -r '.caddy_admin_port // 2091' "$CONFIG")

PORT="$API_PORT" bash "$DIR/src/api/stop.sh"
PORT="$APP_PORT" bash "$DIR/src/app/stop.sh"
caddy stop --address "localhost:$ADMIN_PORT" 2>/dev/null
echo "Gateway stopped"
