#!/bin/bash
# Upgrade Node.js → 22 on macOS/Ubuntu

set -e

echo "Current Node.js version:"
node -v

# Detect install method
if command -v fnm &>/dev/null; then
    echo "Using fnm..."
    fnm install 22
    fnm use 22
    fnm default 22
elif command -v nvm &>/dev/null || [ -s "$HOME/.nvm/nvm.sh" ]; then
    [ -s "$HOME/.nvm/nvm.sh" ] && source "$HOME/.nvm/nvm.sh"
    echo "Using nvm..."
    nvm install 22
    nvm use 22
    nvm alias default 22
elif command -v brew &>/dev/null; then
    echo "Using Homebrew..."
    brew install node@22
    brew unlink node 2>/dev/null || brew unlink node@20 2>/dev/null || true
    brew link --overwrite node@22
elif command -v volta &>/dev/null; then
    echo "Using Volta..."
    volta install node@22
else
    echo "No package manager found. Installing fnm..."
    curl -fsSL https://fnm.vercel.app/install | bash
    export PATH="$HOME/.local/share/fnm:$PATH"
    eval "$(fnm env)"
    fnm install 22
    fnm use 22
    fnm default 22
fi

echo ""
echo "Upgraded to:"
node -v
npm -v
