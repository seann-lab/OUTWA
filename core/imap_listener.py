import imaplib
import email
import re
import ssl
import urllib.parse
import asyncio
import logging
from typing import List, Dict, Optional, Callable
from config import OUTBOUND_PROXY
from core.database import get_active_senders, get_pending_appeals, mark_appeal_success

logger = logging.getLogger(__name__)

# Patterns matching WhatsApp Zendesk auto-reply
PATTERNS = [
    r"Contact us in our app",
    r"Replies to this email won't be monitored",
    r"Replies to this message won't be read",
    r"support form or browse our Help Center",
    r"collect information that’s necessary to understand and resolve your issue"
]

class SOCKS5IMAP4_SSL(imaplib.IMAP4_SSL):
    """
    Isolated SOCKS5 IMAP4_SSL client for Gmail IMAP over SOCKS5 proxy.
    """
    def _create_socket(self, timeout=15):
        if OUTBOUND_PROXY:
            try:
                import socks
                p = urllib.parse.urlparse(OUTBOUND_PROXY)
                proxy_type = socks.SOCKS5 if p.scheme.startswith("socks5") else socks.HTTP
                
                s = socks.socksocket()
                s.set_proxy(proxy_type, p.hostname, p.port, username=p.username, password=p.password)
                s.settimeout(timeout if timeout is not None else self.timeout)
                s.connect((self.host, self.port))
                
                context = ssl.create_default_context()
                return context.wrap_socket(s, server_hostname=self.host)
            except Exception as e:
                logger.warning(f"SOCKS5 Proxy IMAP connection failed: {e}. Falling back to direct socket.")
                
        return super()._create_socket(timeout)

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
        mail = SOCKS5IMAP4_SSL("imap.gmail.com", 993, timeout=15)
        mail.login(email_user, email_pass)
        mail.select("inbox")
        
        status, messages = mail.search(None, '(FROM "whatsapp.com" UNSEEN)')
            
        if status != "OK" or not messages[0]:
            mail.logout()
            return

        msg_ids = messages[0].split()
        for m_id in msg_ids:
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
                        phone_matched = extract_phone_from_text(full_text)
                        
                        from core.database import mark_appeal_success_by_phone
                        
                        item = None
                        if phone_matched:
                            item = mark_appeal_success_by_phone(phone_matched)
                            
                        if item and notify_callback:
                            try:
                                notify_callback(item)
                            except Exception as cb_err:
                                logger.error(f"Error in IMAP notify_callback: {cb_err}")
                                
        mail.logout()
    except Exception as e:
        logger.debug(f"IMAP poll error for {email_user}: {e}")

async def start_imap_listener_loop(interval_seconds: int = 35, notify_callback: Optional[Callable] = None):
    logger.info(f"Starting Smart IMAP Listener Daemon (Interval: {interval_seconds}s)")
    while True:
        try:
            senders = get_active_senders()
            pending = get_pending_appeals()
            
            if senders and pending:
                for sender in senders:
                    await asyncio.to_thread(poll_gmail_inbox, sender, notify_callback)
        except Exception as e:
            logger.error(f"Error in IMAP listener main loop: {e}")
            
        await asyncio.sleep(interval_seconds)
