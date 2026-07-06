#!/bin/bash
# Build script for LocalFS Rust bindings

set -e

echo "🦀 Building LocalFS Rust bindings..."

# Check if maturin is installed
if ! command -v maturin &> /dev/null; then
    echo "Installing maturin..."
    pip install maturin
fi

# Check if Rust is installed
if ! command -v cargo &> /dev/null; then
    echo "❌ Rust is not installed. Please install Rust first:"
    echo "   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    exit 1
fi

# Build the Rust extension
cd rust
echo "Building Rust extension..."
maturin develop --release

cd ..
echo "✅ Build complete! Rust bindings are ready."
echo ""
echo "To test the installation, run:"
echo "  python -c 'from localfs import LocalFS; lfs = LocalFS(); print(lfs.test())'"
