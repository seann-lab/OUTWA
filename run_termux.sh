#!/usr/bin/env bash

echo "=================================================="
echo "🚀 STARTING OUTWA APEX TERMUX STANDALONE SUITE..."
echo "=================================================="

cd "$(dirname "$0")"

if [ ! -f ".env" ]; then
    echo "⚠️ Warning: .env file not found!"
fi

# Acquire Termux Wake Lock to prevent CPU sleep when screen is off
if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock
fi

# Clean orphan processes
pgrep -f "python.*cli.py" | xargs -r kill -9 2>/dev/null

sleep 1

# Launch Master TUI Suite
python3 cli.py
