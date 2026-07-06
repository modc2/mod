#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
ADMIN_PORT=$(jq -r '.caddy_admin_port // 2110' "$DIR/config.json" 2>/dev/null)
caddy stop --address "localhost:$ADMIN_PORT" 2>/dev/null
pkill -f 'freetune-api' 2>/dev/null
pkill -f 'next start -p 50211' 2>/dev/null
pkill -f 'trainer.train' 2>/dev/null
pkill -f 'trainer.infer' 2>/dev/null
echo "freetune stopped"
