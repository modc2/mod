#!/bin/bash
# Quick dev mode startup script
# Usage: ./dev.sh

set -e

echo "Starting polymarket in DEV mode with hot reloading..."
docker-compose -f docker-compose.dev.yml up --build
