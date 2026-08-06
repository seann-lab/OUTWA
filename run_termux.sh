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

# Ensure Baileys WA Engine Node.js process is active
ENGINE_HEALTH=$(curl -s --connect-timeout 1 http://127.0.0.1:12711/health 2>/dev/null)
if [[ "$ENGINE_HEALTH" != *"status"* ]]; then
    echo "⚡ Launching Baileys WA Engine Subprocess (Port 12711)..."
    cd "core/wa_engine" || exit 1
    nohup node server.js > engine.log 2>&1 &
    cd ../..
    sleep 2
fi

# Clean orphan CLI processes
pgrep -f "python.*cli.py" | xargs -r kill -9 2>/dev/null

sleep 1

# Launch Master TUI Suite
python3 cli.py
