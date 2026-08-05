#!/usr/bin/env bash

echo "=================================================="
echo "🚀 STARTING WA APPEAL BOT DIRECTLY ON TERMUX..."
echo "=================================================="

cd "$(dirname "$0")"

if [ ! -f ".env" ]; then
    echo "⚠️ Warning: .env file not found!"
fi

python3 bot.py
