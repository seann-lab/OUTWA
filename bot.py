import asyncio
import logging
import datetime
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
    cancel_handler,
    WAITING_PHONE
)
from core.imap_listener import start_imap_listener_loop

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def notify_whatsapp_success(app: Application, appeal_item: dict):
    """
    Push notification sent to admin Telegram chat when WhatsApp auto-reply is detected.
    """
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

    conv_handler = ConversationHandler(
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
        ]
    )

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("addsender", add_sender_cmd))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(dashboard_callback, pattern="^btn_dashboard$"))
    app.add_handler(CallbackQueryHandler(monitor_callback, pattern="^btn_monitor$"))
    app.add_handler(CallbackQueryHandler(senders_callback, pattern="^btn_senders$"))

    logger.info("Starting High-Performance Telegram Bot (Concurrent Polling mode)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
