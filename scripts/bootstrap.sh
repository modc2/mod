#!/usr/bin/env bash

# Cross-platform bootstrap script for Mac, Linux, and Windows (Git Bash/WSL)

set -e

# Detect OS
detect_os() {
    case "$(uname -s)" in
        Darwin*)
            echo "macos"
            ;;
        Linux*)
            echo "linux"
            ;;
        CYGWIN*|MINGW*|MSYS*)
            echo "windows"
            ;;
        *)
            echo "unknown"
            ;;
    esac
}

OS_TYPE=$(detect_os)
echo "Detected OS: $OS_TYPE"

# Package manager detection and installation
install_package() {
    local package=$1
    
    case $OS_TYPE in
        macos)
            if command -v brew &> /dev/null; then
                brew install "$package"
            else
                echo "Homebrew not found. Installing Homebrew..."
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
                brew install "$package"
            fi
            ;;
        linux)
            if command -v apt-get &> /dev/null; then
                sudo apt-get update && sudo apt-get install -y "$package"
            elif command -v yum &> /dev/null; then
                sudo yum install -y "$package"
            elif command -v dnf &> /dev/null; then
                sudo dnf install -y "$package"
            elif command -v pacman &> /dev/null; then
                sudo pacman -S --noconfirm "$package"
            else
                echo "No supported package manager found"
                exit 1
            fi
            ;;
        windows)
            if command -v choco &> /dev/null; then
                choco install -y "$package"
            elif command -v winget &> /dev/null; then
                winget install "$package"
            else
                echo "Please install Chocolatey or use winget for package management on Windows"
                exit 1
            fi
            ;;
        *)
            echo "Unsupported OS type: $OS_TYPE"
            exit 1
            ;;
    esac
}

# Path handling (cross-platform)
get_home_dir() {
    if [ "$OS_TYPE" = "windows" ]; then
        echo "$USERPROFILE"
    else
        echo "$HOME"
    fi
}

# Main bootstrap logic
echo "Starting bootstrap process..."

# Add your bootstrap commands here
# Example: install_package "git"
# Example: install_package "curl"

echo "Bootstrap completed successfully for $OS_TYPE!"
