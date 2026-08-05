import imaplib
import email
import re
import urllib.parse
import asyncio
import logging
from typing import List, Dict, Optional, Callable
from config import OUTBOUND_PROXY
from core.database import get_active_senders, get_pending_appeals, mark_appeal_success

logger = logging.getLogger(__name__)

# Apply SOCKS5 proxy if OUTBOUND_PROXY is configured
if OUTBOUND_PROXY:
    try:
        import socks
        p = urllib.parse.urlparse(OUTBOUND_PROXY)
        proxy_type = socks.SOCKS5 if p.scheme.startswith("socks5") else socks.HTTP
        socks.set_default_proxy(proxy_type, p.hostname, p.port, username=p.username, password=p.password)
        socks.wrap_module(imaplib)
        logger.info(f"OUTBOUND_PROXY configured for IMAP ({p.hostname}:{p.port})")
    except Exception as e:
        logger.warning(f"Failed to setup OUTBOUND_PROXY for IMAP: {e}")

# Patterns matching WhatsApp Zendesk auto-reply
PATTERNS = [
    r"Contact us in our app",
    r"Replies to this email won't be monitored",
    r"Replies to this message won't be read",
    r"support form or browse our Help Center",
    r"collect information that’s necessary to understand and resolve your issue"
]

def check_body_patterns(body_text: str) -> bool:
    for pat in PATTERNS:
        if re.search(pat, body_text, re.IGNORECASE):
            return True
    return False

def extract_phone_from_text(text: str) -> Optional[str]:
    match = re.search(r"\+?\d{10,15}", text)
    if match:
        num = match.group(0)
        return num if num.startswith("+") else "+" + num
    return None

def poll_gmail_inbox(sender: Dict[str, str], notify_callback: Optional[Callable] = None):
    email_user = sender["email"]
    email_pass = sender["password"]
    
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=15)
        mail.login(email_user, email_pass)
        mail.select("inbox")
        
        # Search ONLY UNREAD messages from whatsapp.com
        status, messages = mail.search(None, '(FROM "whatsapp.com" UNSEEN)')
            
        if status != "OK" or not messages[0]:
            mail.logout()
            return

        msg_ids = messages[0].split()
        for m_id in msg_ids:
            # Fetch message body
            res, msg_data = mail.fetch(m_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    subject = msg.get("Subject", "")
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            if content_type == "text/plain":
                                try:
                                    body += part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                except: pass
                    else:
                        try:
                            body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
                        except: pass
                        
                    full_text = f"{subject}\n{body}"
                    
                    if check_body_patterns(full_text):
                        pending_appeals = get_pending_appeals()
                        matched_appeal = None
                        
                        for appeal in pending_appeals:
                            phone_raw = appeal["phone_number"]
                            phone_digits = phone_raw.replace("+", "")
                            if phone_raw in full_text or phone_digits in full_text:
                                matched_appeal = appeal
                                break
                                
                        if matched_appeal:
                            mark_appeal_success(matched_appeal["id"])
                            logger.info(f"MATCH SUCCESS! WhatsApp reply verified for {matched_appeal['phone_number']}")
                            if notify_callback:
                                notify_callback(matched_appeal)
                                
        mail.logout()
    except Exception as e:
        logger.error(f"IMAP Poll Error for {email_user}: {str(e)}")

async def start_imap_listener_loop(interval_seconds: int, notify_callback: Optional[Callable] = None):
    logger.info(f"Starting Smart IMAP Listener Daemon (Interval: {interval_seconds}s, Zero-Traffic Idle Mode: Enabled)")
    while True:
        try:
            # Zero-Traffic Guard: If no appeals are PENDING, don't open IMAP connection (0 KB proxy quota used)
            pending_appeals = get_pending_appeals()
            if pending_appeals:
                senders = get_active_senders()
                for sender in senders:
                    await asyncio.to_thread(poll_gmail_inbox, sender, notify_callback)
        except Exception as e:
            logger.error(f"IMAP Loop Exception: {str(e)}")
        await asyncio.sleep(interval_seconds)
