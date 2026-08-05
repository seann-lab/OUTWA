import asyncio
import logging
import datetime
import subprocess
import os
import sys
import time
import requests
from pathlib import Path
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters
)

from config import (
    TELEGRAM_BOT_TOKEN,
    ADMIN_CHAT_ID,
    POLL_INTERVAL_SECONDS,
    get_gmail_accounts
)
from core.database import init_db, sync_senders_from_env
from core.bot_ui import (
    start_handler,
    dashboard_callback,
    new_appeal_callback,
    process_phone_input,
    reroll_spec_callback,
    execute_appeal_callback,
    monitor_callback,
    senders_callback,
    add_sender_cmd,
    profiler_menu_callback,
    pairing_wizard_callback,
    process_pairing_phone,
    start_profiler_scan_callback,
    process_profiler_input,
    cancel_handler,
    WAITING_PHONE,
    WAITING_PROFILER_INPUT,
    WAITING_PAIRING_PHONE
)
from core.imap_listener import start_imap_listener_loop
from core.wa_profiler import update_wa_engine_health_cache, check_wa_engine_health

import shutil

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

engine_process = None

def start_wa_engine_subprocess():
    global engine_process
    
    # Check if server is already running and healthy
    try:
        r = requests.get("http://127.0.0.1:12711/health", timeout=1)
        if r.status_code == 200:
            logger.info("Baileys WA Engine Node.js is already running and healthy.")
            return
    except Exception:
        pass

    engine_dir = Path("/data/data/com.termux/files/home/storage/shared/opencode-projects/Appeal bot/core/wa_engine")
    node_bin = shutil.which("node") or "/data/data/com.termux/files/usr/bin/node"
    
    try:
        logger.info("Starting Baileys WA Engine Node.js Subprocess (Port 12711)...")
        engine_process = subprocess.Popen(
            [node_bin, "server.js"],
            cwd=str(engine_dir),
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        logger.info(f"WA Engine Subprocess launched with PID {engine_process.pid}")
        
        # Wait up to 5 seconds for health endpoint to become ready
        for i in range(10):
            time.sleep(0.5)
            try:
                r = requests.get("http://127.0.0.1:12711/health", timeout=1)
                if r.status_code == 200:
                    logger.info("WA Engine Node.js server is fully ready and listening!")
                    break
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Failed to launch WA Engine Subprocess: {e}")

async def refresh_wa_health_loop():
    while True:
        try:
            await asyncio.to_thread(update_wa_engine_health_cache)
        except Exception:
            pass
        await asyncio.sleep(5)

async def notify_whatsapp_success(app: Application, appeal_item: dict):
    if not ADMIN_CHAT_ID:
        return
        
    now_str = datetime.datetime.now().strftime("%H:%M:%S WIB")
    text = (
        "🎉 *[ PUSH ALERT: APPEAL BERHASIL! ]*\n"
        "──────────────────────────────\n"
        "Balasan dari WhatsApp Support (Zendesk Meta) telah terkonfirmasi!\n\n"
        f"📱 *Nomor Target   :* `{appeal_item['phone_number']}`\n"
        f"📬 *Pengirim Gmail :* `{appeal_item['sender_email']}`\n"
        f"⏱️ *Waktu Verifikasi:* `{now_str}`\n"
        "──────────────────────────────\n"
        "💡 Anda sekarang dapat membuka aplikasi WhatsApp resmi untuk melakukan verifikasi login SMS."
    )
    
    try:
        await app.bot.send_message(
            chat_id=int(ADMIN_CHAT_ID),
            text=text,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to send Telegram push alert: {str(e)}")

async def post_init(app: Application):
    logger.info("Initializing database...")
    init_db()
    
    start_wa_engine_subprocess()
    asyncio.create_task(refresh_wa_health_loop())
    
    env_senders = get_gmail_accounts()
    if env_senders:
        sync_senders_from_env(env_senders)
        logger.info(f"Loaded {len(env_senders)} sender accounts from environment.")
        
    def imap_callback(appeal_item):
        asyncio.create_task(notify_whatsapp_success(app, appeal_item))
        
    asyncio.create_task(start_imap_listener_loop(POLL_INTERVAL_SECONDS, imap_callback))
    logger.info("Bot & IMAP listener loop started successfully.")

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is missing! Exiting...")
        return
        
    builder = Application.builder().token(TELEGRAM_BOT_TOKEN).concurrent_updates(True)
    builder.post_init(post_init)
    app = builder.build()

    appeal_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(new_appeal_callback, pattern="^btn_new_appeal$")],
        states={
            WAITING_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_phone_input),
                CallbackQueryHandler(reroll_spec_callback, pattern="^btn_reroll_spec$"),
                CallbackQueryHandler(execute_appeal_callback, pattern="^btn_execute_appeal$"),
                CallbackQueryHandler(dashboard_callback, pattern="^btn_dashboard$")
            ]
        },
        fallbacks=[
            CallbackQueryHandler(dashboard_callback, pattern="^btn_dashboard$"),
            CommandHandler("cancel", cancel_handler)
        ],
        per_message=False
    )

    pairing_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(pairing_wizard_callback, pattern="^btn_pairing_wizard$")],
        states={
            WAITING_PAIRING_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_pairing_phone),
                CallbackQueryHandler(profiler_menu_callback, pattern="^btn_profiler_menu$")
            ]
        },
        fallbacks=[
            CallbackQueryHandler(profiler_menu_callback, pattern="^btn_profiler_menu$"),
            CommandHandler("cancel", cancel_handler)
        ],
        per_message=False
    )

    scan_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_profiler_scan_callback, pattern="^btn_start_profiler_scan$")],
        states={
            WAITING_PROFILER_INPUT: [
                MessageHandler((filters.TEXT | filters.Document.ALL) & ~filters.COMMAND, process_profiler_input),
                CallbackQueryHandler(profiler_menu_callback, pattern="^btn_profiler_menu$")
            ]
        },
        fallbacks=[
            CallbackQueryHandler(profiler_menu_callback, pattern="^btn_profiler_menu$"),
            CommandHandler("cancel", cancel_handler)
        ],
        per_message=False
    )

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("addsender", add_sender_cmd))
    app.add_handler(appeal_conv_handler)
    app.add_handler(pairing_conv_handler)
    app.add_handler(scan_conv_handler)
    app.add_handler(CallbackQueryHandler(dashboard_callback, pattern="^btn_dashboard$"))
    app.add_handler(CallbackQueryHandler(profiler_menu_callback, pattern="^btn_profiler_menu$"))
    app.add_handler(CallbackQueryHandler(monitor_callback, pattern="^btn_monitor$"))
    app.add_handler(CallbackQueryHandler(senders_callback, pattern="^btn_senders$"))

    logger.info("Starting High-Performance Telegram Bot (Concurrent Polling mode)...")
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        if engine_process:
            logger.info("Terminating WA Engine Subprocess...")
            engine_process.terminate()

if __name__ == "__main__":
    main()
