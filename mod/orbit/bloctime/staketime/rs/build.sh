#!/bin/bash
# Build the staketime_rs PyO3 module
set -e

cd "$(dirname "$0")"

export PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1

if command -v maturin &> /dev/null; then
    maturin build --release
    WHEEL=$(ls -t target/wheels/*.whl 2>/dev/null | head -1)
    if [ -n "$WHEEL" ]; then
        pip install "$WHEEL" --force-reinstall --quiet
    fi
else
    echo "maturin required: pip install maturin"
    exit 1
fi

echo "Build complete: staketime_rs"
python3 -c "from staketime_rs import StakeTimeEngine; print('  import OK')"
