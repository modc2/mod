#!/bin/bash
# Installation script for LocalFS

set -e

echo "📦 Installing LocalFS..."

# Install Python dependencies
echo "Installing Python dependencies..."
pip install -r requirements.txt

# Check if user wants to build Rust bindings
read -p "Build Rust bindings for better performance? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if command -v cargo &> /dev/null; then
        bash build.sh
    else
        echo "⚠️  Rust not found. Skipping Rust bindings (will use pure Python fallback)"
        echo "   To install Rust: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    fi
else
    echo "⚠️  Skipping Rust bindings (will use pure Python fallback)"
fi

echo ""
echo "✅ LocalFS installation complete!"
echo ""
echo "Quick test:"
echo "  python -c 'from localfs import LocalFS; lfs = LocalFS(); print(lfs.test())'"
