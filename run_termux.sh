#!/usr/bin/env bash

echo "=================================================="
echo "🚀 STARTING WA APPEAL & PROFILER BOT ON TERMUX..."
echo "=================================================="

cd "$(dirname "$0")"

if [ ! -f ".env" ]; then
    echo "⚠️ Warning: .env file not found!"
fi

# 1. Start Baileys WA Engine Node.js Microservice if not running
ENGINE_PID=$(pgrep -f "node.*server.js")
if [ -z "$ENGINE_PID" ]; then
    echo "📡 Starting Baileys WA Engine Microservice on port 12711..."
    cd core/wa_engine || exit 1
    nohup node server.js > engine.log 2>&1 &
    cd ../.. || exit 1
    sleep 2
    echo "✅ Baileys WA Engine started."
else
    echo "🟢 Baileys WA Engine is already running (PID: $ENGINE_PID)."
fi

# 2. Start Main Telegram Bot
python3 bot.py
