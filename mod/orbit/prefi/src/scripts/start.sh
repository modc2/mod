#!/usr/bin/env bash
#
# ╔══════════════════════════════════════════╗
# ║  PREFI — Startup Script                 ║
# ║  Starts FastAPI backend + Next.js app    ║
# ╚══════════════════════════════════════════╝
#
# Usage: ./scripts/start.sh [--dev] [--prod]
#   --dev     Run in dev mode (default)
#   --prod    Build and run in production mode

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER_DIR="$ROOT_DIR/server"
APP_DIR="$ROOT_DIR/app"

BACKEND_PID=""
FRONTEND_PID=""

BACKEND_PORT=8830
FRONTEND_PORT=8831

# ── Colors ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
AMBER='\033[0;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

print_banner() {
    echo -e "${GREEN}"
    echo "  ╔══════════════════════════════════════╗"
    echo "  ║   PREFI // Prediction Markets        ║"
    echo "  ║   🎯 FastAPI + Next.js               ║"
    echo "  ╚══════════════════════════════════════╝"
    echo -e "${NC}"
}

log() { echo -e "  ${GREEN}[✓]${NC} $1"; }
warn() { echo -e "  ${AMBER}[!]${NC} $1"; }
err() { echo -e "  ${RED}[✕]${NC} $1"; }
info() { echo -e "  ${BLUE}[·]${NC} $1"; }

# ── Cleanup on exit ───────────────────────────────────────────────────
cleanup() {
    echo ""
    warn "Shutting down..."

    if [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
        info "Stopping frontend (PID $FRONTEND_PID)"
        kill "$FRONTEND_PID" 2>/dev/null || true
        wait "$FRONTEND_PID" 2>/dev/null || true
    fi

    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        info "Stopping backend (PID $BACKEND_PID)"
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi

    log "All processes stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM EXIT

# ── Parse args ────────────────────────────────────────────────────────
PROD_MODE=false

for arg in "$@"; do
    case "$arg" in
        --prod)  PROD_MODE=true ;;
        --dev)   PROD_MODE=false ;;
        --help|-h)
            echo "Usage: $0 [--dev|--prod]"
            echo "  --dev     Dev mode with hot reload (default)"
            echo "  --prod    Production mode"
            exit 0
            ;;
    esac
done

# ── Kill existing processes ───────────────────────────────────────────
kill_existing() {
    local port=$1
    local name=$2
    local pids
    pids=$(lsof -ti:"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        warn "Killing existing $name on port $port (PIDs: $pids)"
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 0.5
    fi
}

# ── Main ──────────────────────────────────────────────────────────────

print_banner

# 0. Kill existing on our ports
kill_existing $BACKEND_PORT "backend"
kill_existing $FRONTEND_PORT "frontend"

# 1. Check prerequisites
info "Checking prerequisites..."

if ! command -v python3 &>/dev/null; then
    err "python3 not found"
    exit 1
fi

if ! command -v node &>/dev/null; then
    err "node not found — install Node.js"
    exit 1
fi

log "Prerequisites OK (python3 + node)"

# 2. Install Python deps if needed
if ! python3 -c "import fastapi" 2>/dev/null; then
    info "Installing FastAPI + uvicorn..."
    pip3 install fastapi uvicorn requests 2>&1 || {
        err "pip install failed"
        exit 1
    }
fi

# 3. Install frontend deps if needed
if [ ! -d "$APP_DIR/node_modules" ]; then
    info "Installing frontend dependencies..."
    (cd "$APP_DIR" && npm install 2>&1) || {
        err "npm install failed"
        exit 1
    }
    log "Frontend deps installed"
else
    log "Frontend deps OK"
fi

# 4. Start FastAPI backend
info "Starting FastAPI backend on port $BACKEND_PORT..."
export PYTHONPATH="$ROOT_DIR:$ROOT_DIR/../../.."

if [ "$PROD_MODE" = true ]; then
    (cd "$SERVER_DIR" && python3 -m uvicorn server:app --host 0.0.0.0 --port "$BACKEND_PORT") &
else
    (cd "$SERVER_DIR" && python3 -m uvicorn server:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload) &
fi
BACKEND_PID=$!
sleep 2

if kill -0 "$BACKEND_PID" 2>/dev/null; then
    log "Backend running (PID $BACKEND_PID) → http://localhost:$BACKEND_PORT"
else
    err "Backend failed to start"
    exit 1
fi

# 5. Start Next.js frontend
export NEXT_PUBLIC_API_URL="http://localhost:$BACKEND_PORT"

if [ "$PROD_MODE" = true ]; then
    info "Building frontend for production..."
    (cd "$APP_DIR" && npm run build 2>&1) || {
        err "Frontend build failed"
        exit 1
    }
    info "Starting frontend in production mode on port $FRONTEND_PORT..."
    (cd "$APP_DIR" && npx next start -p "$FRONTEND_PORT") &
    FRONTEND_PID=$!
else
    info "Starting frontend in dev mode on port $FRONTEND_PORT..."
    (cd "$APP_DIR" && npx next dev -p "$FRONTEND_PORT") &
    FRONTEND_PID=$!
fi

sleep 3

if kill -0 "$FRONTEND_PID" 2>/dev/null; then
    log "Frontend running (PID $FRONTEND_PID) → http://localhost:$FRONTEND_PORT"
else
    err "Frontend failed to start"
    exit 1
fi

# 6. Ready
echo ""
echo -e "  ${GREEN}${BOLD}═══════════════════════════════════════${NC}"
echo -e "  ${GREEN}${BOLD}  🎯 PREFI IS RUNNING${NC}"
echo -e "  ${GREEN}${BOLD}═══════════════════════════════════════${NC}"
echo ""
echo -e "  ${AMBER}Frontend${NC}  → ${BOLD}http://localhost:$FRONTEND_PORT${NC}"
echo -e "  ${BLUE}Backend${NC}   → ${BOLD}http://localhost:$BACKEND_PORT${NC}"
echo -e "  ${DIM}Health${NC}    → http://localhost:$BACKEND_PORT/health"
echo -e "  ${DIM}Docs${NC}      → http://localhost:$BACKEND_PORT/docs"
echo ""
echo -e "  ${DIM}Press Ctrl+C to stop all services${NC}"
echo ""

# Wait forever (cleanup runs on SIGINT/SIGTERM)
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
    sleep 2
done
