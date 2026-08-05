import smtplib
import socket
import uuid
import time
import json
import logging
import urllib.request
import urllib.parse
import email.utils
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any, Tuple
from config import TARGET_RECIPIENTS, APPS_SCRIPT_URL
from core.database import get_next_sender, add_appeal

logger = logging.getLogger(__name__)

# Force IPv4 resolution for sockets if legacy SMTP is used
_orig_getaddrinfo = socket.getaddrinfo

def _force_ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

socket.getaddrinfo = _force_ipv4_getaddrinfo

def send_via_apps_script(sender_email: str, sender_password: str, sender_name: str, recipients: list, subject: str, body: str) -> Tuple[bool, str]:
    """
    Sends email via Google Apps Script Web App HTTP Bridge over HTTPS Port 443.
    Bypasses all Railway / Cloud provider SMTP port blocks (25, 465, 587).
    """
    if not APPS_SCRIPT_URL:
        return False, "APPS_SCRIPT_URL environment variable is not configured."
        
    payload = {
        "email": sender_email,
        "password": sender_password,
        "sender_name": sender_name,
        "recipients": recipients,
        "subject": subject,
        "body": body
    }
    
    try:
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            APPS_SCRIPT_URL,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            res_text = resp.read().decode("utf-8")
            res_json = json.loads(res_text)
            if res_json.get("success"):
                return True, "Email sent via Apps Script HTTP Bridge (Port 443)."
            return False, f"Apps Script Error: {res_json.get('error', res_text)}"
    except Exception as e:
        return False, f"Apps Script HTTP Error: {str(e)}"

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
    
    errors = []

    # --- STRATEGY 1: HTTP Bridge via Google Apps Script (Port 443 - Immune to Railway SMTP Blocks) ---
    if APPS_SCRIPT_URL:
        success_http, msg_http = send_via_apps_script(
            sender_email, sender_password, sender_name, TARGET_RECIPIENTS, email_payload["subject"], email_payload["body"]
        )
        if success_http:
            appeal_id = add_appeal(
                phone_number=phone_data["formatted"],
                country_code=phone_data["country_code"],
                carrier=phone_data["carrier"],
                sender_email=sender_email,
                message_id=message_id,
                subject=email_payload["subject"],
                body=email_payload["body"]
            )
            return True, "Email sent successfully via HTTPS Port 443 Relay.", {
                "appeal_id": appeal_id,
                "sender_email": sender_email,
                "message_id": message_id,
                "recipients": TARGET_RECIPIENTS
            }
        else:
            errors.append(f"HTTP Relay Error: {msg_http}")
            logger.warning(f"HTTP Relay failed: {msg_http}. Fallback to Direct SMTP...")

    # --- STRATEGY 2: Direct SMTP (Port 587 / Port 465) for Railway Pro or Local Environments ---
    msg = MIMEMultipart()
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = ", ".join(TARGET_RECIPIENTS)
    msg["Subject"] = email_payload["subject"]
    msg["Message-ID"] = message_id
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["User-Agent"] = "Mozilla/5.0 (Android 14; Mobile; rv:124.0) Gecko/124.0 Firefox/124.0"
    msg.attach(MIMEText(email_payload["body"], "plain", "utf-8"))
    
    # Try Port 587 STARTTLS
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

    # Try Port 465 SSL
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

    return False, f"Send Error: { ' | '.join(errors) }", {}
