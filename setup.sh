#!/bin/bash
# PokeGo Teleport Bot - Linux Setup Script
# Run this once to install everything needed on Ubuntu/Debian

set -e

echo "============================================"
echo "  PokeGo Teleport Bot - Setup"
echo "============================================"
echo ""

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo "Detected: $NAME $VERSION"
    if [ "$ID" = "ubuntu" ] || [ "$ID" = "debian" ] || [ "$ID_LIKE" = "debian" ]; then
        echo "Installing system dependencies..."
        sudo apt-get update -qq
        # Ubuntu 24.04+ renamed libasound2 to libasound2t64
        LIBASOUND="libasound2"
        if apt-cache show libasound2t64 &>/dev/null; then
            LIBASOUND="libasound2t64"
        fi
        sudo apt-get install -y -qq python3 python3-pip python3-venv \
            libnss3 libatk1.0-0 libatk-bridge2.0-0 \
            libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
            libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
            libcairo2 $LIBASOUND libnspr4 libatspi2.0-0
        echo "System dependencies installed."
    else
        echo "Non-Debian system detected. You may need to install Playwright dependencies manually."
        echo "Run: playwright install-deps chromium"
    fi
else
    echo "Could not detect OS. Assuming Linux with Python3 available."
fi

echo ""

cd "$(dirname "$0")"

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed."
    echo "Install with: sudo apt-get install python3 python3-pip python3-venv"
    exit 1
fi

echo "Python 3: $(python3 --version)"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# Upgrade pip
pip install --upgrade pip -q

# Install dependencies
echo "Installing Python dependencies..."
pip install -r requirements.txt -q

# Install Playwright browser
echo "Installing Playwright browser..."
playwright install chromium

echo ""
echo "Setup complete! Run ./start.sh to start the bot."
