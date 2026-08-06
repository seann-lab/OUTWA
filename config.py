import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

INPUT_DIR = BASE_DIR / "input"
REPORTS_DIR = BASE_DIR / "reports"
INPUT_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "appeal.db"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")

# Single string formatted like: "email1:pass1,email2:pass2" or default list
GMAIL_ACCOUNTS_RAW = os.getenv("GMAIL_ACCOUNTS", "")

# Optional Google Apps Script WebApp Relay URL for HTTPS 443 Email Sending
APPS_SCRIPT_URL = os.getenv("APPS_SCRIPT_URL", "")

# Optional SOCKS5/HTTP Proxy for Termux environment bypass
OUTBOUND_PROXY = os.getenv("OUTBOUND_PROXY", "")

def get_gmail_accounts():
    accounts = []
    if GMAIL_ACCOUNTS_RAW:
        parts = GMAIL_ACCOUNTS_RAW.split(",")
        for p in parts:
            if ":" in p:
                user, pwd = p.strip().split(":", 1)
                accounts.append({"email": user.strip(), "password": pwd.strip()})
    return accounts

COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "180")) # 3 minutes default
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "35"))

WA_SUPPORT_EMAIL = "support@support.whatsapp.com"
WA_ANDROID_EMAIL = "android@support.whatsapp.com"
WA_SMB_EMAIL = "smb_web@support.whatsapp.com"

TARGET_RECIPIENTS = [WA_SUPPORT_EMAIL, WA_ANDROID_EMAIL, WA_SMB_EMAIL]
