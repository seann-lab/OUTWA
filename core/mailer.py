import smtplib
import socket
import uuid
import time
import email.utils
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any, Tuple
from config import TARGET_RECIPIENTS
from core.database import get_next_sender, add_appeal

logger = logging.getLogger(__name__)

# Monkey-patch socket.getaddrinfo to force IPv4 (AF_INET) globally for outbound connections.
# This completely prevents Linux/Railway container from picking unroutable IPv6 routes (Errno 101).
_orig_getaddrinfo = socket.getaddrinfo

def _force_ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

socket.getaddrinfo = _force_ipv4_getaddrinfo

def send_appeal_email(phone_data: Dict[str, Any], email_payload: Dict[str, str]) -> Tuple[bool, str, Dict[str, Any]]:
    sender = get_next_sender()
    if not sender:
        return False, "No active Gmail sender available in sender pool.", {}
        
    sender_email = sender["email"]
    sender_password = sender["password"]
    sender_name = email_payload["sender_name"]
    
    # Generate unique Message-ID
    domain = sender_email.split("@")[-1] if "@" in sender_email else "gmail.com"
    message_id = f"<{uuid.uuid4().hex}.wa_appeal.{phone_data['formatted'].replace('+', '')}@{domain}>"
    
    msg = MIMEMultipart()
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = ", ".join(TARGET_RECIPIENTS)
    msg["Subject"] = email_payload["subject"]
    msg["Message-ID"] = message_id
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["User-Agent"] = "Mozilla/5.0 (Android 14; Mobile; rv:124.0) Gecko/124.0 Firefox/124.0"
    
    msg.attach(MIMEText(email_payload["body"], "plain", "utf-8"))
    
    errors = []
    
    # Strategy 1: Port 587 STARTTLS (Explicit IPv4 forced)
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, TARGET_RECIPIENTS, msg.as_string())
            
        appeal_id = add_appeal(
            phone_number=phone_data["formatted"],
            country_code=phone_data["country_code"],
            carrier=phone_data["carrier"],
            sender_email=sender_email,
            message_id=message_id,
            subject=email_payload["subject"],
            body=email_payload["body"]
        )
        return True, "Email sent successfully to 3 WhatsApp Support targets (Port 587).", {
            "appeal_id": appeal_id,
            "sender_email": sender_email,
            "message_id": message_id,
            "recipients": TARGET_RECIPIENTS
        }
    except Exception as e:
        errors.append(f"Port 587 error: {str(e)}")
        logger.warning(f"Port 587 failed: {e}. Trying Port 465 SSL...")

    # Strategy 2: Port 465 SSL (Explicit IPv4 forced)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, TARGET_RECIPIENTS, msg.as_string())
            
        appeal_id = add_appeal(
            phone_number=phone_data["formatted"],
            country_code=phone_data["country_code"],
            carrier=phone_data["carrier"],
            sender_email=sender_email,
            message_id=message_id,
            subject=email_payload["subject"],
            body=email_payload["body"]
        )
        return True, "Email sent successfully to 3 WhatsApp Support targets (Port 465).", {
            "appeal_id": appeal_id,
            "sender_email": sender_email,
            "message_id": message_id,
            "recipients": TARGET_RECIPIENTS
        }
    except Exception as e:
        errors.append(f"Port 465 error: {str(e)}")
        logger.warning(f"Port 465 failed: {e}.")

    return False, f"SMTP Send Error: { ' | '.join(errors) }", {}
