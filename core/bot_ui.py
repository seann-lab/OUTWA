import asyncio
import time
import datetime
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters
)
from config import ADMIN_CHAT_ID, COOLDOWN_SECONDS, DATA_DIR
from core.database import (
    get_appeal_stats,
    get_active_senders,
    check_cooldown,
    get_pending_appeals,
    add_sender,
    remove_sender,
    record_profiler_job,
    complete_profiler_job
)
from core.generator import parse_phone_metadata, generate_random_identity, generate_device_spec
from core.templates import build_appeal_email
from core.mailer import send_appeal_email
from core.bulk_parser import extract_phone_numbers_from_text
from core.exporter import generate_wa_profiler_csv
from core.wa_profiler import (
    check_wa_engine_health,
    request_wa_pairing_code,
    start_wa_bulk_scan,
    get_wa_scan_job_status
)

# Conversation States
WAITING_PHONE = 1
WAITING_SENDER = 2
WAITING_PROFILER_INPUT = 3
WAITING_PAIRING_PHONE = 4

def is_admin(user_id: int) -> bool:
    if not ADMIN_CHAT_ID:
        return True
    return str(user_id).strip() == str(ADMIN_CHAT_ID).strip()

def build_dashboard_markup() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🚀 Buat Appeal Baru", callback_data="btn_new_appeal")],
        [InlineKeyboardButton("🔍 Bulk WA Profiler & Vermet Scanner", callback_data="btn_profiler_menu")],
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
    wa_health = check_wa_engine_health()
    wa_status_badge = "🟢 CONNECTED" if wa_health.get("connection") == "CONNECTED" else "🔴 DISCONNECTED"
    
    text = (
        "🚀 *WA APPEAL & PROFILER AUTOMATION CENTER*\n"
        "──────────────────────────────\n"
        "📊 *Status System & Performance:*\n"
        f" • Total Appeal     : `{stats['total']}`\n"
        f" • Appeal Sukses (WA): 🟢 `{stats['success']}`\n"
        f" • Dipantau (Pending): 🟡 `{stats['pending']}`\n"
        f" • Akun Sender Gmail : 🟢 `{len(senders)} Akun Aktif`\n"
        f" • WA Engine Scanner : {wa_status_badge}\n"
        "──────────────────────────────\n"
        "💡 *Panduan:* Pilih fitur *🚀 Buat Appeal Baru* atau *🔍 Bulk WA Profiler & Vermet* melalui tombol di bawah."
    )
    return text

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(f"❌ Akses ditolak. ID Telegram Anda ({user_id}) tidak terdaftar sebagai Admin.")
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
    await query.answer("Memproses pengiriman...")
    
    phone_data = context.user_data["phone_data"]
    email_payload = context.user_data["email_payload"]
    
    await query.edit_message_text("⏳ *MEMPROSES PENGIRIMAN...*\n\nMengirim email via SMTP Gmail ke 3 target WhatsApp Support...", parse_mode="Markdown")
    
    success, msg, info = await asyncio.to_thread(send_appeal_email, phone_data, email_payload)
    
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

# --- BULK WA PROFILER & VERMET MENU HANDLERS ---

async def profiler_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    wa_health = check_wa_engine_health()
    conn_status = wa_health.get("connection", "DISCONNECTED")
    conn_badge = "🟢 CONNECTED" if conn_status == "CONNECTED" else "🔴 DISCONNECTED"
    
    text = (
        "🔍 *BULK WA PROFILER & VERMET SCANNER*\n"
        "──────────────────────────────\n"
        f"📡 *Status WA Engine:* {conn_badge}\n"
        f"👤 *Akun Helper:* `{wa_health.get('user', 'Belum Terhubung')}`\n"
        "──────────────────────────────\n"
        "Fitur ini dapat memindai 100 - 1.000 nomor sekaligus untuk memeriksa:\n"
        " • Status Registrasi WA\n"
        " • 🔵 Status Meta Verified (Vermet) & Nama Resmi\n"
        " • 🛍️ Indikator Penawaran & Katalog Bisnis\n"
        " • Teks Bio / About Status Pengguna\n\n"
        "Silakan pilih aksi di bawah:"
    )
    
    keyboard = []
    if conn_status == "CONNECTED":
        keyboard.append([InlineKeyboardButton("🚀 Mulai Pemindaian Bulk", callback_data="btn_start_profiler_scan")])
    keyboard.append([InlineKeyboardButton("🔑 Connect WA Helper (Pairing Code)", callback_data="btn_pairing_wizard")])
    keyboard.append([InlineKeyboardButton("🏠 Kembali ke Dashboard", callback_data="btn_dashboard")])
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def pairing_wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "🔑 *CONNECT WA HELPER (PAIRING CODE MODE)*\n"
        "──────────────────────────────\n"
        "Silakan kirimkan nomor telepon akun WA Helper/Tumbal yang akan digunakan untuk pemindaian.\n\n"
        "💡 *Contoh:* `+628999999999` atau `628999999999`"
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Batal", callback_data="btn_profiler_menu")]])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    return WAITING_PAIRING_PHONE

async def process_pairing_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_phone = update.message.text.strip()
    phone_data = parse_phone_metadata(raw_phone)
    
    if not phone_data["valid"]:
        await update.message.reply_text("❌ Format nomor tidak valid. Kirim nomor diawali kode negara (+62...).")
        return ConversationHandler.END
        
    target_phone = phone_data["formatted"]
    status_msg = await update.message.reply_text(f"⏳ Meminta Kode Pairing untuk `{target_phone}` dari Baileys Engine...", parse_mode="Markdown")
    
    try:
        success, code_res, raw_code = await asyncio.wait_for(
            asyncio.to_thread(request_wa_pairing_code, target_phone),
            timeout=35.0
        )
    except asyncio.TimeoutError:
        success, code_res, raw_code = False, "Waktu permintaan habis (Timeout 35s). Coba kirim ulang nomor.", ""
    except Exception as err:
        success, code_res, raw_code = False, str(err), ""
    
    if success:
        if code_res == "ALREADY_REGISTERED":
            text = "✅ *AKUN TERHUBUNG!* WA Engine sudah terhubung ke WhatsApp dan siap digunakan."
        else:
            text = (
                "🔑 *KODE PAIRING WHATSAPP:* \n"
                "──────────────────────────────\n"
                f"👉 `{code_res}`\n"
                "──────────────────────────────\n"
                "📌 *Langkah Penautan di HP:* \n"
                "1. Buka aplikasi WhatsApp di HP Helper.\n"
                "2. Buka *Pengaturan / Titik Tiga ➔ Perangkat Tertaut*.\n"
                "3. Pilih *Tautkan Perangkat* ➔ Klik *Tautkan dengan nomor telepon saja*.\n"
                f"4. Masukkan kode 8-digit di atas (`{code_res}`)."
            )
    else:
        text = f"❌ *PAIRING GAGAL:* `{code_res}`\n\nPastikan service Node.js di wa_engine aktif."
        
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Kembali ke Profiler Menu", callback_data="btn_profiler_menu")]])
    try:
        await status_msg.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    except Exception:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
    return ConversationHandler.END

async def start_profiler_scan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "📂 *INPUT NOMOR TARGET SCANNER*\n"
        "──────────────────────────────\n"
        "Silakan kirimkan daftar nomor target (100 - 1.000 nomor):\n\n"
        " 1. **Paste Teks:** Tempel kumpulan nomor di chat (koma/baris baru).\n"
        " 2. **Upload Dokumen:** Upload file `.txt` atau `.csv` berisi daftar nomor."
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Batal", callback_data="btn_profiler_menu")]])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    return WAITING_PROFILER_INPUT

async def process_profiler_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = ""
    if update.message.document:
        doc = update.message.document
        if not (doc.file_name.endswith(".txt") or doc.file_name.endswith(".csv")):
            await update.message.reply_text("❌ Harap upload file berformat `.txt` atau `.csv`.")
            return ConversationHandler.END
            
        file_obj = await context.bot.get_file(doc.file_id)
        byte_contents = await file_obj.download_as_bytearray()
        raw_text = byte_contents.decode("utf-8", errors="ignore")
    elif update.message.text:
        raw_text = update.message.text
        
    numbers = extract_phone_numbers_from_text(raw_text)
    if not numbers:
        await update.message.reply_text("❌ Tidak ditemukan nomor telepon valid dari input yang dikirim.")
        return ConversationHandler.END
        
    job_id = f"job_{int(time.time())}"
    success, res_msg = start_wa_bulk_scan(job_id, numbers)
    
    if not success:
        await update.message.reply_text(f"❌ Gagal memulai pemindaian: `{res_msg}`", parse_mode="Markdown")
        return ConversationHandler.END
        
    record_profiler_job(job_id, len(numbers))
    
    progress_msg = await update.message.reply_text(
        f"⏳ *PEMINDAIAN BULK DIPULAI...*\n"
        "──────────────────────────────\n"
        f"📋 Total Target : `{len(numbers)} Nomor`\n"
        f"🆔 Job ID       : `{job_id}`\n"
        "Progress       : [ ░░░░░░░░░░ ] 0%\n"
        "──────────────────────────────\n"
        "Sistem sedang memproses batch pemindaian dengan anti-banned protection...",
        parse_mode="Markdown"
    )
    
    asyncio.create_task(run_scan_progress_tracker(context.bot, update.effective_chat.id, progress_msg.message_id, job_id, numbers))
    return ConversationHandler.END

async def run_scan_progress_tracker(bot, chat_id: int, message_id: int, job_id: str, numbers: List[str]):
    last_done = 0
    total = len(numbers)
    
    while True:
        await asyncio.sleep(4)
        job_data = get_wa_scan_job_status(job_id)
        if not job_data:
            continue
            
        done = job_data.get("done", 0)
        status = job_data.get("status", "RUNNING")
        results = job_data.get("results", [])
        
        percent = int((done / total) * 100) if total > 0 else 0
        bar_len = 10
        filled = int((percent / 100) * bar_len)
        bar = "▓" * filled + "░" * (bar_len - filled)
        
        exists_cnt = sum(1 for r in results if r.get("exists"))
        vermet_cnt = sum(1 for r in results if r.get("isVermet"))
        offers_cnt = sum(1 for r in results if r.get("hasOffers"))
        non_wa_cnt = sum(1 for r in results if not r.get("exists"))
        
        if done != last_done or status == "COMPLETED":
            last_done = done
            text = (
                "⏳ *PEMINDAIAN BULK WA BERJALAN...*\n"
                "──────────────────────────────\n"
                f"📋 Total Target  : `{total} Nomor`\n"
                f"Progress        : [ {bar} ] {percent}%\n"
                "──────────────────────────────\n"
                "📊 *Statistik Real-Time:*\n"
                f" • 🟢 Terdaftar WA    : `{exists_cnt}`\n"
                f" • 🔵 Meta Verified   : `{vermet_cnt}`\n"
                f" • 🛍️ Ada Penawaran   : `{offers_cnt}`\n"
                f" • 🔴 Tidak Terdaftar : `{non_wa_cnt}`\n"
                "──────────────────────────────"
            )
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode="Markdown")
            except Exception:
                pass
                
        if status == "COMPLETED" or done >= total:
            csv_path = generate_wa_profiler_csv(results, DATA_DIR / "reports")
            complete_profiler_job(job_id, csv_path)
            
            final_text = (
                "🎉 *PEMINDAIAN SELESAI!*\n"
                "──────────────────────────────\n"
                f"📋 Total Diperiksa : `{total} Nomor`\n"
                f" • 🟢 Terdaftar WA    : `{exists_cnt}`\n"
                f" • 🔵 Meta Verified   : `{vermet_cnt}`\n"
                f" • 🛍️ Ada Penawaran   : `{offers_cnt}`\n"
                f" • 🔴 Tidak Terdaftar : `{non_wa_cnt}`\n"
                "──────────────────────────────\n"
                "📄 File laporan `.csv` terlampir di bawah."
            )
            
            try:
                await bot.send_message(chat_id=chat_id, text=final_text, parse_mode="Markdown")
                with open(csv_path, "rb") as doc_file:
                    await bot.send_document(
                        chat_id=chat_id,
                        document=doc_file,
                        filename=Path(csv_path).name,
                        caption="📄 Laporan Hasil WA Profiler & Vermet Scanner"
                    )
            except Exception as e:
                logger.error(f"Failed to deliver final CSV report: {e}")
            break

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
