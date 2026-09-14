#!/bin/bash
# PokeGo Teleport Bot - Linux Start Script
# Usage: ./start.sh
# For background use: nohup ./start.sh > bot.log 2>&1 &
# Or with screen/tmux: screen -S pokebot ./start.sh

cd "$(dirname "$0")"

echo "============================================"
echo "  PokeGo Teleport Bot v2.0"
echo "============================================"
echo ""

# Activate virtual environment
if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Run ./setup.sh first."
    exit 1
fi

source venv/bin/activate

# Check dependencies
if ! python3 -c "import discord" 2>/dev/null; then
    echo "Dependencies not installed. Run ./setup.sh first."
    exit 1
fi

echo "Starting bot..."
echo ""

# Get local IP for dashboard access
LOCAL_IP=$(hostname -I | awk '{print $1}')
echo "  Web Dashboard (local):  http://127.0.0.1:8765"
echo "  Web Dashboard (network): http://${LOCAL_IP}:8765"
echo "  Press Ctrl+C to stop"
echo ""

python3 main.py
