#!/bin/bash
set -euo pipefail
NAME=taox

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
    docker rm -f "$NAME" >/dev/null
    echo "stopped $NAME"
else
    echo "$NAME not running"
fi
