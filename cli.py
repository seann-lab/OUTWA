#!/usr/bin/env python3
import asyncio
import datetime
import logging
import os
import re
import select
import shutil
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

import requests
from rich import box
from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.prompt import Prompt, Confirm
from rich.style import Style
from rich.table import Table
from rich.text import Text

# Local Core Modules
from config import (
    COOLDOWN_SECONDS,
    POLL_INTERVAL_SECONDS,
    INPUT_DIR,
    REPORTS_DIR,
    OUTBOUND_PROXY,
    get_gmail_accounts
)
from core.database import (
    init_db,
    sync_senders_from_env,
    get_active_senders,
    get_next_sender,
    get_pending_appeals,
    get_recent_appeals,
    add_sender,
    mark_appeal_success
)
from core.generator import parse_phone_metadata, generate_device_spec, generate_random_identity
from core.templates import build_appeal_email
from core.mailer import send_appeal_email
from core.bulk_parser import extract_phone_numbers_from_text, get_available_input_files
from core.wa_profiler import (
    check_wa_engine_health,
    update_wa_engine_health_cache,
    request_wa_pairing_code,
    start_wa_bulk_scan,
    get_wa_scan_job_status,
    cancel_wa_scan_job
)
from core.exporter import generate_wa_profiler_csv
from core.imap_listener import poll_gmail_inbox

console = Console()

# Background IMAP Worker State
imap_daemon_running = True
last_imap_alert: Optional[Dict[str, Any]] = None

def trigger_android_alert():
    """Triggers Termux system bell and Android notification if termux-api is available."""
    sys.stdout.write('\a')
    sys.stdout.flush()
    try:
        if shutil.which("termux-vibrate"):
            os.system("termux-vibrate -d 300 >/dev/null 2>&1")
    except Exception:
        pass

def imap_background_worker():
    global imap_daemon_running, last_imap_alert
    while imap_daemon_running:
        try:
            senders = get_active_senders()
            for s in senders:
                if not imap_daemon_running:
                    break
                def imap_cb(appeal_item):
                    global last_imap_alert
                    last_imap_alert = appeal_item
                    trigger_android_alert()
                    
                poll_gmail_inbox(s, imap_cb)
        except Exception:
            pass
        for _ in range(POLL_INTERVAL_SECONDS):
            if not imap_daemon_running:
                break
            time.sleep(1)

def ensure_wa_engine_running():
    """Pre-flight check: ensures Baileys Node.js subprocess is listening on port 12711."""
    try:
        r = requests.get("http://127.0.0.1:12711/health", timeout=1)
        if r.status_code == 200:
            return True
    except Exception:
        pass

    engine_dir = Path("/data/data/com.termux/files/home/storage/shared/opencode-projects/Appeal bot/core/wa_engine")
    if not engine_dir.exists():
        engine_dir = Path(__file__).resolve().parent / "core" / "wa_engine"
        
    node_bin = shutil.which("node") or "/data/data/com.termux/files/usr/bin/node"
    
    if (engine_dir / "server.js").exists():
        try:
            env = os.environ.copy()
            # Clean proxy vars for node
            env.pop('HTTP_PROXY', None)
            env.pop('HTTPS_PROXY', None)
            env.pop('ALL_PROXY', None)
            env.pop('http_proxy', None)
            env.pop('https_proxy', None)
            env.pop('all_proxy', None)
            
            subprocess.Popen(
                [node_bin, "server.js"],
                cwd=str(engine_dir),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            for _ in range(15):
                time.sleep(0.5)
                try:
                    r = requests.get("http://127.0.0.1:12711/health", timeout=1)
                    if r.status_code == 200:
                        return True
                except Exception:
                    pass
        except Exception:
            pass
    return False

def render_header() -> Panel:
    update_wa_engine_health_cache()
    health = check_wa_engine_health()
    wa_status = health.get("connection", "DISCONNECTED")
    wa_user = health.get("user") or "None"
    wa_reg = health.get("registered", False)

    if wa_reg or wa_status == "CONNECTED":
        wa_badge = f"[bold green]🟢 CONNECTED[/bold green] ([cyan]{wa_user}[/cyan])"
    elif wa_status == "CONNECTING":
        wa_badge = "[bold yellow]🟡 CONNECTING...[/bold yellow]"
    else:
        wa_badge = "[bold red]🔴 DISCONNECTED[/bold red]"

    senders = get_active_senders()
    sender_badge = f"[bold green]🟢 {len(senders)} Gmail Active[/bold green]" if senders else "[bold red]🔴 0 Senders[/bold red]"

    proxy_badge = "[bold green]🟢 SOCKS5 FlameProxies Active[/bold green]" if OUTBOUND_PROXY else "[bold yellow]⚪ Direct Connection (No Proxy)[/bold yellow]"

    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="right", ratio=1)
    grid.add_row(
        "[bold cyan]⚡ OUTWA APEX • TERMUX STANDALONE SUITE v2.0[/bold cyan]",
        f"📅 [dim]{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]"
    )
    grid.add_row(
        f"📱 WA Helper : {wa_badge}",
        f"📬 Senders Pool : {sender_badge}"
    )
    grid.add_row(
        f"🛡️ Proxy Out  : {proxy_badge}",
        f"📡 IMAP Daemon : [bold green]🟢 Active ({POLL_INTERVAL_SECONDS}s)[/bold green]"
    )

    return Panel(grid, style="bold cyan", border_style="cyan", box=box.ROUNDED)

def main_menu():
    init_db()
    env_senders = get_gmail_accounts()
    if env_senders:
        sync_senders_from_env(env_senders)

    ensure_wa_engine_running()

    # Start IMAP worker thread
    t = threading.Thread(target=imap_background_worker, daemon=True)
    t.start()

    while True:
        console.clear()
        console.print(render_header())

        global last_imap_alert
        if last_imap_alert:
            alert_panel = Panel(
                f"[bold green]🎉 PUSH ALERT: APPEAL DISETUJUI & BALASAN DITERIMA![/bold green]\n\n"
                f"📱 Nomor Target    : [bold white]{last_imap_alert.get('phone_number')}[/bold white]\n"
                f"📬 Gmail Pengirim  : [cyan]{last_imap_alert.get('sender_email')}[/cyan]\n"
                f"⏱️ Waktu Konfirmasi: [yellow]{datetime.datetime.now().strftime('%H:%M:%S WIB')}[/yellow]\n\n"
                f"💡 Buka aplikasi WhatsApp resmi di HP untuk melakukan verifikasi login SMS.",
                style="bold green",
                border_style="green",
                box=box.DOUBLE
            )
            console.print(alert_panel)
            last_imap_alert = None

        menu_table = Table(show_header=False, box=box.ROUNDED, expand=True, border_style="bright_blue")
        menu_table.add_column("No", style="bold yellow", width=6, justify="center")
        menu_table.add_column("Menu Feature", style="bold white")

        menu_table.add_row("[1]", "🚀 [bold cyan]Buat Tiket Appeal Unblock SMS[/bold cyan] (Single Target Wizard)")
        menu_table.add_row("[2]", "🔍 [bold green]WhatsApp Number Profiler Suite[/bold green] (Meta Vermet & Katalog Scan)")
        menu_table.add_row("[3]", "📊 [bold magenta]Monitor Status Tiket & Laporan Respon[/bold magenta] (Zendesk Status)")
        menu_table.add_row("[4]", "🔑 [bold yellow]Kelola Akun Gmail Pengirim[/bold yellow] (SMTP Rotasi Pool)")
        menu_table.add_row("[5]", "📱 [bold blue]Koneksikan Akun WA Helper Baru[/bold blue] (Pairing Code Mode)")
        menu_table.add_row("[6]", "🛠️ [bold white]Diagnostik Sistem & Test SOCKS5 Proxy[/bold white]")
        menu_table.add_row("[0]", "🚪 [bold red]Keluar ke Shell Termux[/bold red]")

        console.print(Panel(menu_table, title="[bold white]MENU UTAMA[/bold white]", border_style="bright_blue", box=box.ROUNDED))

        choice = Prompt.ask("[bold yellow]➔ Masukkan pilihan[/bold yellow]", choices=["0", "1", "2", "3", "4", "5", "6"], default="1")

        if choice == "1":
            run_single_appeal_wizard()
        elif choice == "2":
            run_profiler_suite()
        elif choice == "3":
            show_ticket_monitor()
        elif choice == "4":
            manage_senders_menu()
        elif choice == "5":
            run_pairing_wizard()
        elif choice == "6":
            run_diagnostics()
        elif choice == "0":
            global imap_daemon_running
            imap_daemon_running = False
            console.print("\n[bold green]👋 Terima kasih telah menggunakan OutWa Apex Termux Suite![/bold green]")
            break

def run_single_appeal_wizard():
    console.clear()
    console.print(render_header())
    console.print(Panel("[bold yellow]🚀 FORMULIR PENGAJUAN TIKET APPEAL UNBLOCK SMS[/bold yellow]", border_style="yellow", box=box.ROUNDED))

    raw_phone = Prompt.ask("\n[bold cyan]➔ Masukkan nomor WhatsApp target[/bold cyan] (contoh: 081234567890)")
    phone_data = parse_phone_metadata(raw_phone)

    if not phone_data["valid"]:
        console.print("\n[bold red]❌ Format nomor telepon tidak valid! Kirim nomor dengan kode negara (+62...)[/bold red]")
        time.sleep(2)
        return

    while True:
        spec = generate_device_spec()
        sender_name = generate_random_identity(phone_data["country_code"])
        email_payload = build_appeal_email(phone_data, spec, sender_name)
        sender = get_next_sender()

        if not sender:
            console.print("\n[bold red]❌ Tidak ada akun Gmail sender yang aktif! Tambahkan sender di Menu [4].[/bold red]")
            Prompt.ask("\n[dim]Tekan Enter untuk kembali...[/dim]")
            return

        preview_table = Table(show_header=False, box=box.ROUNDED, expand=True, border_style="bright_cyan")
        preview_table.add_column("Field", style="bold yellow", width=20)
        preview_table.add_column("Detail Value", style="bold white")

        preview_table.add_row("Nomor Target", f"[bold green]{phone_data['formatted']}[/bold green] ({phone_data['country']})")
        preview_table.add_row("Identitas HP", f"{spec['brand']} {spec['model']}")
        preview_table.add_row("Versi OS / WA", f"{spec['os']} • WA {spec['wa_version']}")
        preview_table.add_row("Pengirim Gmail", f"[cyan]{sender['email']}[/cyan]")
        preview_table.add_row("Subjek Email", email_payload['subject'])
        preview_table.add_row("Jalur Outbound", "🟢 SOCKS5 FlameProxies (Port 465 SSL)" if OUTBOUND_PROXY else "⚪ Direct Connection")

        console.clear()
        console.print(render_header())
        console.print(Panel(preview_table, title="[bold white]PREVIEW TIKET APPEAL[/bold white]", border_style="bright_cyan", box=box.ROUNDED))

        console.print("\n[bold yellow]Pilihan tindakan:[/bold yellow]")
        console.print(" [1] 🚀 Kirim Email Appeal Sekarang")
        console.print(" [2] 🎲 Acak Ulang Identitas & Subjek (Reroll)")
        console.print(" [0] ❌ Batal")

        act = Prompt.ask("\n[bold yellow]➔ Masukkan opsi[/bold yellow]", choices=["0", "1", "2"], default="1")

        if act == "2":
            continue
        elif act == "0":
            return
        elif act == "1":
            with console.status("[bold green]Mengirim email via SMTP SSL Gmail & SOCKS5 Proxy...[/bold green]", spinner="dots"):
                success, msg, info = send_appeal_email(phone_data, email_payload)

            if success:
                res_panel = Panel(
                    f"[bold green]🎉 TIKET BERHASIL DIAJUKAN & TERKIRIM![/bold green]\n\n"
                    f"📱 Nomor Target   : [bold white]{phone_data['formatted']}[/bold white]\n"
                    f"📬 Pengirim Gmail : [cyan]{info.get('sender_email')}[/cyan]\n"
                    f"⏱️ Cooldown Guard : [yellow]{COOLDOWN_SECONDS} Detik (3 Menit)[/yellow]\n\n"
                    f"💡 System IMAP Listener sedang memantau balasan dari WhatsApp Support di background.",
                    style="bold green",
                    border_style="green",
                    box=box.ROUNDED
                )
                console.print(res_panel)
            else:
                console.print(f"\n[bold red]❌ GAGAL MENGIRIM APPEAL:[/bold red] {msg}")

            Prompt.ask("\n[dim]Tekan Enter untuk kembali ke menu utama...[/dim]")
            return

def run_profiler_suite():
    console.clear()
    console.header = render_header()
    console.print(render_header())
    console.print(Panel("[bold green]🔍 WHATSAPP NUMBER PROFILER & META VERMET SCANNER[/bold green]", border_style="green", box=box.ROUNDED))

    console.print("\n[bold yellow]Pilih Sumber Input Nomor:[/bold yellow]")
    console.print(" [1] 📁 Auto-Detect dari Folder input/ (Rekomendasi)")
    console.print(" [2] 📝 Masukkan List Nomor Manual (Ketik / Paste)")
    console.print(" [3] 📂 Masukkan Path File Manual")
    console.print(" [0] ⬅️ Kembali")

    mode = Prompt.ask("\n[bold yellow]➔ Masukkan pilihan[/bold yellow]", choices=["0", "1", "2", "3"], default="1")

    numbers = []

    if mode == "0":
        return
    elif mode == "1":
        files = get_available_input_files()
        if not files:
            console.print("\n[bold yellow]ℹ️ Folder 'input/' masih kosong![/bold yellow]")
            console.print(f"💡 Silakan letakkan file .txt atau .csv Anda di folder:\n   👉 [bold cyan]{INPUT_DIR.resolve()}[/bold cyan]\n")
            manual_path = Prompt.ask("[bold yellow]➔ Masukkan path file manual atau tekan Enter untuk batal[/bold yellow]", default="")
            if not manual_path:
                return
            if not os.path.exists(manual_path):
                console.print("[bold red]❌ File tidak ditemukan![/bold red]")
                time.sleep(2)
                return
            with open(manual_path, "r", encoding="utf-8", errors="ignore") as f:
                numbers = extract_phone_numbers_from_text(f.read())
        else:
            file_table = Table(show_header=True, header_style="bold yellow", box=box.ROUNDED, expand=True)
            file_table.add_column("No", style="bold cyan", width=6, justify="center")
            file_table.add_column("Nama File", style="bold white")
            file_table.add_column("Ukuran File", style="yellow")
            file_table.add_column("Perkiraan Nomor", style="bold green")

            for idx, fitem in enumerate(files, 1):
                file_table.add_row(str(idx), fitem['filename'], f"{fitem['size_kb']} KB", f"~{fitem['count']} Nomor")

            console.print(Panel(file_table, title="[bold white]DAFTAR FILE DI FOLDER input/[/bold white]", border_style="cyan", box=box.ROUNDED))

            file_choice = Prompt.ask(f"[bold yellow]➔ Pilih nomor file [1-{len(files)}][/bold yellow]", default="1")
            try:
                sel_idx = int(file_choice) - 1
                selected_file = files[sel_idx]['filepath']
                with open(selected_file, "r", encoding="utf-8", errors="ignore") as f:
                    numbers = extract_phone_numbers_from_text(f.read())
            except Exception:
                console.print("[bold red]❌ Pilihan file tidak valid![/bold red]")
                time.sleep(2)
                return
    elif mode == "2":
        console.print("\n[dim]Tempel/ketik nomor telepon (pisahkan dengan enter atau koma). Tekan Enter 2x jika selesai:[/dim]")
        lines = []
        while True:
            line = input()
            if not line.strip():
                break
            lines.append(line)
        raw_input_text = "\n".join(lines)
        numbers = extract_phone_numbers_from_text(raw_input_text)
    elif mode == "3":
        manual_path = Prompt.ask("\n[bold yellow]➔ Masukkan path file lengkap[/bold yellow]")
        if not os.path.exists(manual_path):
            console.print("[bold red]❌ File tidak ditemukan![/bold red]")
            time.sleep(2)
            return
        with open(manual_path, "r", encoding="utf-8", errors="ignore") as f:
            numbers = extract_phone_numbers_from_text(f.read())

    if not numbers:
        console.print("\n[bold red]❌ Tidak ada nomor telepon valid yang ditemukan untuk di-scan![/bold red]")
        time.sleep(2)
        return

    console.print(f"\n[bold green]✓ Berhasil memuat {len(numbers)} nomor telepon valid.[/bold green]")
    job_id = f"job_cli_{int(time.time())}"

    ok, job_res = start_wa_bulk_scan(job_id, numbers)
    if not ok:
        console.print(f"\n[bold red]❌ Gagal memulai job scan:[/bold red] {job_res}")
        Prompt.ask("\n[dim]Tekan Enter untuk kembali...[/dim]")
        return

    console.print(f"\n[bold cyan]🚀 Scan dimulai (Job ID: {job_id}). Memantau progres...[/bold cyan]\n")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task(f"[cyan]Scanning {len(numbers)} nomor...", total=len(numbers))

        while not progress.finished:
            time.sleep(2)
            job_data = get_wa_scan_job_status(job_id)
            if not job_data:
                continue

            done = job_data.get("done", 0)
            status = job_data.get("status")

            progress.update(task, completed=done)

            if status in ["COMPLETED", "CANCELLED"] or done >= len(numbers):
                break

    job_final = get_wa_scan_job_status(job_id)
    results = job_final.get("results", []) if job_final else []

    active_wa = [r for r in results if r.get('exists')]
    vermet_accounts = [r for r in results if r.get('isVermet')]
    business_offers = [r for r in results if r.get('hasOffers')]
    business_accounts = [r for r in results if r.get('accountType') == 'Business']

    sum_table = Table(show_header=False, box=box.ROUNDED, expand=True, border_style="bold green")
    sum_table.add_column("Metrik", style="bold yellow", width=30)
    sum_table.add_column("Hasil Scan", style="bold white")

    sum_table.add_row("Total Nomor Di-scan", str(len(results)))
    sum_table.add_row("Nomor WA Aktif (Valid)", f"[bold green]{len(active_wa)}[/bold green] ({(len(active_wa)/len(results))*100:.1f}%)")
    sum_table.add_row("Nomor Tidak Terdaftar (Di-filter)", f"[dim]{len(results) - len(active_wa)}[/dim]")
    sum_table.add_row("Akun WA Bisnis", str(len(business_accounts)))
    sum_table.add_row("Memiliki Penawaran / Katalog", str(len(business_offers)))
    sum_table.add_row("Meta Verified (Vermet)", f"[bold cyan]{len(vermet_accounts)}[/bold cyan]")

    console.print(Panel(sum_table, title="[bold white]📊 RINGKASAN HASIL BULK PROFILER[/bold white]", border_style="bold green", box=box.ROUNDED))

    filepath = generate_wa_profiler_csv(results, REPORTS_DIR, include_non_wa=False)
    console.print(f"\n[bold green]📁 Laporan Bersih (Hanya Nomor Aktif) Tersimpan di:[/bold green]")
    console.print(f"👉 [bold cyan]{filepath}[/bold cyan]\n")

    Prompt.ask("[dim]Tekan Enter untuk kembali ke menu utama...[/dim]")

def show_ticket_monitor():
    console.clear()
    console.print(render_header())
    console.print(Panel("[bold magenta]📊 MONITOR TIKET APPEAL & STATUS ZENDESK[/bold magenta]", border_style="magenta", box=box.ROUNDED))

    recent = get_recent_appeals(limit=25)
    if not recent:
        console.print("\n[dim]Belum ada riwayat tiket appeal yang dikirim.[/dim]")
    else:
        tbl = Table(show_header=True, header_style="bold yellow", box=box.ROUNDED, expand=True)
        tbl.add_column("ID Tiket", style="bold cyan", width=12)
        tbl.add_column("Nomor Target", style="bold white")
        tbl.add_column("Pengirim Gmail", style="cyan")
        tbl.add_column("Status Appeal", style="bold yellow")
        tbl.add_column("Waktu Pengajuan", style="dim")

        for item in recent:
            st = item.get("status", "PENDING")
            st_text = "[bold green]🟢 SUCCESS (UNBLOCKED)[/bold green]" if st == "SUCCESS" else "[bold yellow]🟡 PENDING WA RESPONSE[/bold yellow]"
            tbl.add_row(
                item.get("ticket_id", "-"),
                item.get("phone_number", "-"),
                item.get("sender_email", "-"),
                st_text,
                item.get("sent_at", "-")
            )
        console.print(tbl)

    Prompt.ask("\n[dim]Tekan Enter untuk kembali ke menu utama...[/dim]")

def manage_senders_menu():
    console.clear()
    console.print(render_header())
    console.print(Panel("[bold yellow]🔑 PENGELOLA AKUN GMAIL PENGIRIM (SMTP POOL)[/bold yellow]", border_style="yellow", box=box.ROUNDED))

    senders = get_active_senders()
    if not senders:
        console.print("\n[bold red]❌ Belum ada akun Gmail sender yang terdaftar![/bold red]")
    else:
        tbl = Table(show_header=True, header_style="bold yellow", box=box.ROUNDED, expand=True)
        tbl.add_column("No", style="bold cyan", width=6, justify="center")
        tbl.add_column("Email Gmail", style="bold white")
        tbl.add_column("App Password", style="dim")
        tbl.add_column("Status SMTP", style="bold green")

        for idx, s in enumerate(senders, 1):
            pwd_masked = s['password'][:4] + "*" * 8 if len(s['password']) >= 4 else "********"
            tbl.add_row(str(idx), s['email'], pwd_masked, "🟢 READY")
        console.print(tbl)

    console.print("\n[bold yellow]Pilihan tindakan:[/bold yellow]")
    console.print(" [1] ➕ Tambah Akun Gmail Baru")
    console.print(" [0] ⬅️ Kembali ke Menu Utama")

    act = Prompt.ask("\n[bold yellow]➔ Pilihan[/bold yellow]", choices=["0", "1"], default="0")
    if act == "1":
        new_email = Prompt.ask("[bold cyan]➔ Masukkan Alamat Gmail[/bold cyan]")
        new_pass = Prompt.ask("[bold cyan]➔ Masukkan App Password (16 Karakter)[/bold cyan]")
        if "@" in new_email and len(new_pass.strip()) >= 8:
            add_sender(new_email.strip(), new_pass.strip())
            console.print("[bold green]✓ Akun Gmail baru berhasil ditambahkan ke database![/bold green]")
        else:
            console.print("[bold red]❌ Input email atau App Password tidak valid![/bold red]")
        time.sleep(2)

def run_pairing_wizard():
    ensure_wa_engine_running()
    console.clear()
    console.print(render_header())
    console.print(Panel("[bold blue]📱 WHATSAPP HELPER PAIRING WIZARD[/bold blue]", border_style="blue", box=box.ROUNDED))

    phone_num = Prompt.ask("\n[bold cyan]➔ Masukkan nomor HP Helper[/bold cyan] (contoh: 082230071031)")
    phone_data = parse_phone_metadata(phone_num)

    if not phone_data["valid"]:
        console.print("[bold red]❌ Nomor telepon tidak valid![/bold red]")
        time.sleep(2)
        return

    raw_phone = phone_data["formatted"]
    console.print(f"\n[bold yellow]Meminta Kode Pairing 8-digit dari Baileys Engine untuk {raw_phone}...[/bold yellow]")

    with console.status("[bold green]Menghubungkan ke WhatsApp server...[/bold green]", spinner="dots"):
        success, code_res, raw_code = request_wa_pairing_code(raw_phone)

    if success:
        if code_res == "ALREADY_REGISTERED":
            console.print("\n[bold green]✅ AKUN TERHUBUNG! WA Engine sudah terpasang dan siap digunakan.[/bold green]")
        else:
            pair_panel = Panel(
                f"[bold green]🔑 KODE PAIRING WHATSAPP UTAMA:[/bold green]\n\n"
                f"👉 [bold yellow]{code_res}[/bold yellow] 👈\n\n"
                f"📌 [bold white]Langkah Penautan di Aplikasi WA HP:[/bold white]\n"
                f"1. Buka aplikasi WhatsApp di HP Helper.\n"
                f"2. Buka Pengaturan / Titik Tiga ➔ Perangkat Tertaut.\n"
                f"3. Pilih Tautkan Perangkat ➔ Klik 'Tautkan dengan nomor telepon saja'.\n"
                f"4. Masukkan 8 karakter kode di atas ([bold yellow]{code_res}[/bold yellow]).",
                style="bold green",
                border_style="green",
                box=box.DOUBLE
            )
            console.print(pair_panel)
    else:
        console.print(f"\n[bold red]❌ PAIRING GAGAL:[/bold red] {code_res}")

    Prompt.ask("\n[dim]Tekan Enter untuk kembali ke menu utama...[/dim]")

def run_diagnostics():
    console.clear()
    console.print(render_header())
    console.print(Panel("[bold white]🛠️ DIAGNOSTIK SISTEM & TEST SOCKS5 PROXY[/bold white]", border_style="white", box=box.ROUNDED))

    with console.status("[bold green]Jalankan uji tes sistem...[/bold green]"):
        health = check_wa_engine_health()
        senders = get_active_senders()
        
        # Check SOCKS5 Proxy latency
        proxy_ok = False
        proxy_lat = "N/A"
        if OUTBOUND_PROXY:
            try:
                t0 = time.time()
                proxies = {"http": OUTBOUND_PROXY, "https": OUTBOUND_PROXY}
                r = requests.get("https://www.google.com", proxies=proxies, timeout=5)
                if r.status_code == 200:
                    proxy_ok = True
                    proxy_lat = f"{(time.time() - t0)*1000:.1f} ms"
            except Exception:
                pass

    diag_table = Table(show_header=True, header_style="bold yellow", box=box.ROUNDED, expand=True)
    diag_table.add_column("Komponen Sistem", style="bold cyan", width=25)
    diag_table.add_column("Status Operasional", style="bold white")

    diag_table.add_row("Baileys WA Engine (Port 12711)", f"🟢 ONLINE ({health.get('connection')})" if health.get('status') == 'OK' else "🔴 OFFLINE")
    diag_table.add_row("Status Registrasi WA Helper", f"🟢 REGISTERED ({health.get('user')})" if health.get('registered') else "⚪ NOT REGISTERED")
    diag_table.add_row("Akun Gmail Sender Active", f"🟢 {len(senders)} Senders Ready" if senders else "🔴 0 Senders")
    diag_table.add_row("FlameProxies SOCKS5 Proxy", f"🟢 OK (Latency: {proxy_lat})" if proxy_ok else ("⚪ DIRECT (No Proxy)" if not OUTBOUND_PROXY else "🔴 PROXY ERROR"))
    diag_table.add_row("Database SQLite (appeal.db)", "🟢 WAL MODE ACTIVE")

    console.print(Panel(diag_table, title="[bold white]HASIL DIAGNOSTIK[/bold white]", border_style="white", box=box.ROUNDED))
    Prompt.ask("\n[dim]Tekan Enter untuk kembali ke menu utama...[/dim]")

if __name__ == "__main__":
    def sig_handler(sig, frame):
        global imap_daemon_running
        imap_daemon_running = False
        console.print("\n[bold red]👋 Exiting Termux Suite...[/bold red]")
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    main_menu()
