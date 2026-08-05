import time
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters
)
from config import ADMIN_CHAT_ID, COOLDOWN_SECONDS
from core.database import (
    get_appeal_stats,
    get_active_senders,
    check_cooldown,
    get_pending_appeals,
    add_sender,
    remove_sender
)
from core.generator import parse_phone_metadata, generate_random_identity, generate_device_spec
from core.templates import build_appeal_email
from core.mailer import send_appeal_email

# Conversation States
WAITING_PHONE = 1
WAITING_SENDER = 2

def is_admin(user_id: int) -> bool:
    if not ADMIN_CHAT_ID:
        return True
    return str(user_id) == str(ADMIN_CHAT_ID).strip()

def build_dashboard_markup() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🚀 Buat Appeal Baru", callback_data="btn_new_appeal")],
        [
            InlineKeyboardButton("📋 Live Monitor", callback_data="btn_monitor"),
            InlineKeyboardButton("⚙️ Kelola Sender", callback_data="btn_senders")
        ],
        [InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="btn_dashboard")]
    ]
    return InlineKeyboardMarkup(keyboard)

def render_dashboard_text() -> str:
    stats = get_appeal_stats()
    senders = get_active_senders()
    
    text = (
        "🚀 *WA APPEAL AUTOMATION CENTER*\n"
        "──────────────────────────────\n"
        "📊 *Status System & Performance:*\n"
        f" • Total Permohonan : `{stats['total']}`\n"
        f" • Appeal Sukses (WA): 🟢 `{stats['success']}`\n"
        f" • Dipantau (Pending): 🟡 `{stats['pending']}`\n"
        f" • Akun Sender Gmail : 🟢 `{len(senders)} Akun Aktif`\n"
        "──────────────────────────────\n"
        "💡 *Panduan:* Tekan tombol *🚀 Buat Appeal Baru* untuk mengajukan unblock SMS verification pada nomor target."
    )
    return text

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Akses ditolak. Bot ini hanya untuk Admin.")
        return
        
    await update.message.reply_text(
        render_dashboard_text(),
        parse_mode="Markdown",
        reply_markup=build_dashboard_markup()
    )

async def dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        render_dashboard_text(),
        parse_mode="Markdown",
        reply_markup=build_dashboard_markup()
    )

async def new_appeal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "📝 *INPUT NOMOR TELEPON TARGET*\n"
        "──────────────────────────────\n"
        "Silakan kirimkan nomor WhatsApp target yang mengalami error *\"Login not available right now\"*.\n\n"
        "💡 *Contoh Format:* `+6281234567890` atau `5511999998888`"
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Batal / Kembali", callback_data="btn_dashboard")]])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    return WAITING_PHONE

async def process_phone_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_phone = update.message.text.strip()
    phone_data = parse_phone_metadata(raw_phone)
    
    if not phone_data["valid"]:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Dashboard", callback_data="btn_dashboard")]])
        await update.message.reply_text("❌ Nomor telepon tidak valid. Pastikan format nomor diawali kode negara (+62..., +55..., dll).", reply_markup=markup)
        return ConversationHandler.END
        
    phone_number = phone_data["formatted"]
    
    # Check 3 minutes cooldown
    is_cooldown, rem_sec = check_cooldown(phone_number)
    if is_cooldown:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Dashboard", callback_data="btn_dashboard")]])
        text = (
            "⏳ *COOLDOWN GUARD AKTIF*\n"
            "──────────────────────────────\n"
            f"Nomor `{phone_number}` baru saja diajukan.\n"
            f"Harap tunggu *{rem_sec} detik* lagi sebelum mengajukan ulang nomor ini."
        )
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
        return ConversationHandler.END
        
    # Generate profile & hardware fingerprint
    device_data = generate_device_spec()
    sender_name = generate_random_identity(phone_data["country_code"])
    email_payload = build_appeal_email(phone_data, device_data, sender_name)
    
    context.user_data["phone_data"] = phone_data
    context.user_data["device_data"] = device_data
    context.user_data["email_payload"] = email_payload
    
    return await render_preview_screen(update, context)

async def render_preview_screen(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    phone_data = context.user_data["phone_data"]
    device_data = context.user_data["device_data"]
    email_payload = context.user_data["email_payload"]
    
    text = (
        "🔍 *PREVIEW APPEAL & HARDWARE FINGERPRINT*\n"
        "──────────────────────────────\n"
        f"📱 *Nomor Target  :* `{phone_data['display']}`\n"
        f"🏳️ *Negara / ISP  :* `{phone_data['country']} ({phone_data['carrier']})`\n"
        f"👤 *Nama Pengirim :* `{email_payload['sender_name']}`\n"
        f"🤖 *Device Spec   :* `{device_data['brand']} {device_data['model']} ({device_data['os']})`\n"
        f"💬 *Skenario      :* `{email_payload['scenario_name']}`\n"
        f"🎯 *Target Server :* `android@, support@, smb_web@`\n"
        "──────────────────────────────\n"
        "📌 *Subjek Email:* \n"
        f"`{email_payload['subject']}`"
    )
    
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Eksekusi Sekarang", callback_data="btn_execute_appeal")],
        [InlineKeyboardButton("🎲 Acak Ulang Identitas", callback_data="btn_reroll_spec")],
        [InlineKeyboardButton("🔙 Batalkan", callback_data="btn_dashboard")]
    ])
    
    if hasattr(update_or_query, "callback_query") and update_or_query.callback_query:
        await update_or_query.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await update_or_query.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
        
    return WAITING_PHONE

async def reroll_spec_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Acak ulang profil & identitas...")
    
    phone_data = context.user_data["phone_data"]
    device_data = generate_device_spec()
    sender_name = generate_random_identity(phone_data["country_code"])
    email_payload = build_appeal_email(phone_data, device_data, sender_name)
    
    context.user_data["device_data"] = device_data
    context.user_data["email_payload"] = email_payload
    
    return await render_preview_screen(update, context)

async def execute_appeal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    phone_data = context.user_data["phone_data"]
    email_payload = context.user_data["email_payload"]
    
    await query.edit_message_text("⏳ *MEMPROSES PENGIRIMAN...*\n\nMengirim email via SMTP Gmail ke 3 target WhatsApp Support...", parse_mode="Markdown")
    
    success, msg, info = send_appeal_email(phone_data, email_payload)
    
    if success:
        text = (
            "🟡 *TIKET BERHASIL DIAJUKAN*\n"
            "──────────────────────────────\n"
            f"📱 *Nomor Target  :* `{phone_data['formatted']}`\n"
            f"📬 *Pengirim Gmail:* `{info['sender_email']}`\n"
            "📡 *Status        :* 🟡 `PENDING WA RESPONSE`\n"
            f"🔄 *Cooldown Guard:* 🟢 `3 Menit Aktif` ({COOLDOWN_SECONDS}s)\n"
            "──────────────────────────────\n"
            "Sistem sedang memantau inbox Gmail via IMAP di background.\n"
            "Notifikasi pop-up akan dikirimkan otomatis saat WA membalas."
        )
    else:
        text = (
            "❌ *GAGAL MENGIRIM APPEAL*\n"
            "──────────────────────────────\n"
            f"Reason: `{msg}`\n\n"
            "Pastikan akun Gmail sender telah diisi dengan App Password 16 karakter."
        )
        
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Buat Appeal Lain", callback_data="btn_new_appeal")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="btn_dashboard")]
    ])
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    return ConversationHandler.END

async def monitor_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    pending = get_pending_appeals()
    if not pending:
        text = (
            "📋 *LIVE MONITOR TIKET*\n"
            "──────────────────────────────\n"
            "Tidak ada tiket yang sedang pending.\n"
            "Semua permohonan telah selesai atau belum ada appeal baru."
        )
    else:
        text = "📋 *LIVE MONITOR TIKET (PENDING WA RESPONSE)*\n──────────────────────────────\n"
        for idx, item in enumerate(pending[:5], 1):
            created_str = datetime.datetime.fromtimestamp(item['created_at']).strftime('%H:%M:%S')
            text += f"{idx}. `{item['phone_number']}` | Sender: `{item['sender_email']}` | Waktu: `{created_str}`\n"
            
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Dashboard", callback_data="btn_dashboard")]])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)

async def senders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    senders = get_active_senders()
    text = "⚙️ *KELOLA SENDER GMAIL POOL*\n──────────────────────────────\n"
    if not senders:
        text += "⚠️ Belum ada akun Gmail yang dikonfigurasi di sender pool.\n"
    else:
        for idx, s in enumerate(senders, 1):
            text += f"{idx}. `{s['email']}` | Sent: `{s['total_sent']}` | Status: 🟢 ACTIVE\n"
            
    text += "\n💡 *Untuk menambah sender:* Kirim format `/addsender email:app_password`"
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Dashboard", callback_data="btn_dashboard")]])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)

async def add_sender_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id): return
    
    args = context.args
    if not args or ":" not in args[0]:
        await update.message.reply_text("❌ Format salah. Gunakan: `/addsender email@gmail.com:app_password`", parse_mode="Markdown")
        return
        
    email_val, pass_val = args[0].split(":", 1)
    add_sender(email_val.strip(), pass_val.strip())
    await update.message.reply_text(f"✅ Akun `{email_val.strip()}` berhasil ditambahkan ke sender pool!", parse_mode="Markdown")

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return ConversationHandler.END
