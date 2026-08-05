#!/usr/bin/env bash

echo "=================================================="
echo "🚀 STARTING WA APPEAL & PROFILER BOT ON TERMUX..."
echo "=================================================="

cd "$(dirname "$0")"

if [ ! -f ".env" ]; then
    echo "⚠️ Warning: .env file not found!"
fi

# Clean up any orphan processes before starting
pgrep -f "python.*bot.py" | xargs -r kill -9 2>/dev/null
pgrep -f "node.*server.js" | xargs -r kill -9 2>/dev/null

sleep 1

# Start Main Telegram Bot (which auto-manages Node.js server.js lifecycle)
python3 bot.py
