#!/bin/bash
# PokeGo Teleport Bot - Startup Script
# Double-click this file to start the bot
# Runs with caffeinate -s to prevent sleep when lid is closed

cd "$(dirname "$0")"

echo "============================================"
echo "  PokeGo Teleport Bot v2.0"
echo "============================================"
echo ""

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed."
    echo "Install from https://python.org or run: brew install python"
    echo ""
    echo "Press any key to close..."
    read -n 1
    exit 1
fi

echo "[1/4] Python 3 found: $(python3 --version)"

# Create virtual environment if needed
if [ ! -d "venv" ]; then
    echo "[2/4] Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install dependencies if needed
if ! python3 -c "import discord" 2>/dev/null; then
    echo "[3/4] Installing dependencies..."
    pip install -r requirements.txt
    echo "  Installing Playwright browser..."
    playwright install chromium
else
    echo "[3/4] Dependencies already installed"
fi

# Start the bot with caffeinate to prevent sleep
echo "[4/4] Starting bot (caffeinated)..."
echo ""
echo "  Web Dashboard: http://127.0.0.1:8765"
echo "  Press Ctrl+C to stop"
echo ""
exec caffeinate -s python3 main.py
