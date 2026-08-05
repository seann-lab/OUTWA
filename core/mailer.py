import smtplib
import socket
import uuid
import time
import json
import logging
import urllib.parse
import requests
import email.utils
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any, Tuple
from config import TARGET_RECIPIENTS, APPS_SCRIPT_URL, OUTBOUND_PROXY

logger = logging.getLogger(__name__)

# Apply SOCKS5 proxy if OUTBOUND_PROXY is configured in .env
if OUTBOUND_PROXY:
    try:
        import socks
        p = urllib.parse.urlparse(OUTBOUND_PROXY)
        proxy_type = socks.SOCKS5 if p.scheme.startswith("socks5") else socks.HTTP
        socks.set_default_proxy(proxy_type, p.hostname, p.port, username=p.username, password=p.password)
        socks.wrap_module(smtplib)
        logger.info(f"OUTBOUND_PROXY configured for SMTP ({p.hostname}:{p.port})")
    except Exception as e:
        logger.warning(f"Failed to setup OUTBOUND_PROXY for SMTP: {e}")

# Force IPv4 resolution for sockets if legacy SMTP is used
_orig_getaddrinfo = socket.getaddrinfo

def _force_ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

socket.getaddrinfo = _force_ipv4_getaddrinfo

def sanitize_apps_script_url(url: str) -> str:
    url = url.strip()
    if url.endswith('/dev'):
        url = url[:-4] + '/exec'
    elif '/dev?' in url:
        url = url.replace('/dev?', '/exec?')
    return url

def send_via_apps_script(sender_email: str, sender_password: str, sender_name: str, recipients: list, subject: str, body: str) -> Tuple[bool, str]:
    """
    Sends email via Google Apps Script Web App HTTP Bridge over HTTPS Port 443.
    """
    if not APPS_SCRIPT_URL:
        return False, "APPS_SCRIPT_URL environment variable is not configured."
        
    target_url = sanitize_apps_script_url(APPS_SCRIPT_URL)
    
    payload = {
        "email": sender_email,
        "password": sender_password,
        "sender_name": sender_name,
        "recipients": ",".join(recipients) if isinstance(recipients, list) else recipients,
        "subject": subject,
        "body": body
    }
    
    proxies = {"http": OUTBOUND_PROXY, "https": OUTBOUND_PROXY} if OUTBOUND_PROXY else None

    try:
        resp = requests.post(
            target_url,
            data=json.dumps(payload),
            headers={"Content-Type": "text/plain;charset=utf-8"},
            proxies=proxies,
            timeout=10,
            allow_redirects=True
        )
        
        if resp.status_code == 200:
            try:
                res_json = resp.json()
                if res_json.get("success"):
                    return True, "Email sent via Apps Script HTTP Bridge (Port 443)."
                return False, f"Apps Script Error: {res_json.get('error', resp.text)}"
            except Exception:
                if "success" in resp.text.lower() or "dispatched" in resp.text.lower():
                    return True, "Email sent via Apps Script HTTP Bridge."
                return False, f"Apps Script Non-JSON Response: {resp.text[:150]}"
        elif resp.status_code == 403:
            return False, f"Apps Script 403 Forbidden. Pastikan 'Who has access' di-set 'Anyone'."
        else:
            return False, f"Apps Script HTTP Status {resp.status_code}: {resp.text[:150]}"
    except Exception as e:
        return False, f"Apps Script HTTP Network Error: {str(e)}"

def send_appeal_email(phone_data: Dict[str, Any], email_payload: Dict[str, str]) -> Tuple[bool, str, Dict[str, Any]]:
    # Late import to prevent circular dependency
    from core.database import get_next_sender, add_appeal
    
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

    # --- STRATEGY 1: Direct SMTP SSL Port 465 FIRST (Fastest for Termux & Proxy environments) ---
    msg = MIMEMultipart()
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = ", ".join(TARGET_RECIPIENTS)
    msg["Subject"] = email_payload["subject"]
    msg["Message-ID"] = message_id
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["User-Agent"] = "Mozilla/5.0 (Android 14; Mobile; rv:124.0) Gecko/124.0 Firefox/124.0"
    msg.attach(MIMEText(email_payload["body"], "plain", "utf-8"))
    
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=6) as server:
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
        return True, "Email sent successfully to 3 WhatsApp Support targets (Port 465 SSL).", {
            "appeal_id": appeal_id,
            "sender_email": sender_email,
            "message_id": message_id,
            "recipients": TARGET_RECIPIENTS
        }
    except Exception as e:
        errors.append(f"Port 465 error: {str(e)}")
        logger.warning(f"Port 465 failed: {e}. Trying HTTP Relay / Port 587...")

    # --- STRATEGY 2: HTTP Bridge via Google Apps Script (Port 443) ---
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
            
    # --- STRATEGY 3: Direct SMTP Port 587 STARTTLS (Fallback) ---
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=6) as server:
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

    return False, f"Send Error: { ' | '.join(errors) }", {}
