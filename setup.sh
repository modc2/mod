#!/bin/bash
set -e

MOD_DIR="${MOD_DIR:-$HOME/mod}"

echo "=== mod setup ==="

# ---- System: Node + PM2 ----
if ! command -v node &> /dev/null; then
    echo "[+] Installing Node.js..."
    if command -v apt-get &> /dev/null; then
        apt-get update && apt-get install -y --no-install-recommends nodejs npm
    elif command -v brew &> /dev/null; then
        brew install node
    else
        echo "[!] Cannot install Node.js - install manually"
        exit 1
    fi
fi
echo "[ok] node $(node --version)"

if ! command -v pm2 &> /dev/null; then
    echo "[+] Installing PM2..."
    npm install -g pm2
fi
echo "[ok] pm2 $(pm2 --version 2>/dev/null)"

# ---- System: Rust ----
if ! command -v rustc &> /dev/null; then
    echo "[+] Installing Rust via rustup..."
    curl https://sh.rustup.rs -sSf | sh -s -- -y
fi
export PATH="$HOME/.cargo/bin:$PATH"
echo "[ok] rust $(rustc --version 2>/dev/null)"

# ---- Python: mod ----
echo "[+] Installing mod..."
pip install --timeout 300 -e "$MOD_DIR"
echo "[ok] mod CLI: $(which m)"

# ---- Python: commune ----
if ! python3 -c "import commune" 2>/dev/null; then
    echo "[+] Installing commune..."
    pip install commune
fi
echo "[ok] commune installed"

echo ""
echo "=== setup complete ==="
