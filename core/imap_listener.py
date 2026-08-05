import imaplib
import email
import re
import asyncio
import logging
from typing import List, Dict, Optional, Callable
from core.database import get_active_senders, get_pending_appeals, mark_appeal_success_by_phone, mark_appeal_success

logger = logging.getLogger(__name__)

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
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(email_user, email_pass)
        mail.select("inbox")
        
        # Strictly search for UNREAD messages from whatsapp.com only
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
                    sender_from = msg.get("From", "")
                    
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
                    
                    # Verify BOTH whatsapp pattern AND pending appeal match to prevent false triggers
                    if check_body_patterns(full_text):
                        pending_appeals = get_pending_appeals()
                        matched_appeal = None
                        
                        # Match phone number strictly inside the email text
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
    logger.info(f"Starting IMAP Listener Daemon (Polling interval: {interval_seconds}s)")
    while True:
        try:
            senders = get_active_senders()
            for sender in senders:
                await asyncio.to_thread(poll_gmail_inbox, sender, notify_callback)
        except Exception as e:
            logger.error(f"IMAP Loop Exception: {str(e)}")
        await asyncio.sleep(interval_seconds)
