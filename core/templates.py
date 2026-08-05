import random
import re
from typing import Dict, Any

def process_spintax(text: str) -> str:
    pattern = r"\{([^{}]+)\}"
    while re.search(pattern, text):
        def repl(match):
            options = match.group(1).split("|")
            return random.choice(options)
        text = re.sub(pattern, repl, text)
    return text

SCENARIOS = [
    # Scenario A: Official App Update SMS Failure
    {
        "name": "App Update SMS Failure",
        "subject": "{Support|Urgent|Verification Request}: {SMS OTP Code Unavailable|Login Not Available Right Now|Cannot Receive SMS Code} - {phone_number}",
        "body": """{Dear WhatsApp Support Team|Hello Support Team|Hi WhatsApp Support},

{I am facing an issue|I am unable to log in|My account is currently restricted} when attempting to request an SMS verification code on WhatsApp. The application displays the message: "Login not available right now".

{This problem started right after|This occurred after} I updated the WhatsApp application to the latest version ({wa_version}) from the official Google Play Store on my {device_model} ({os_version}).

{Account Information|Device & Account Specs}:
- WhatsApp Number: {phone_number}
- Registered Region / Carrier: {country_name} ({carrier_name})
- Device Spec: {device_model} [{os_build}]
- Application Status: Official Play Store Version ({wa_version})

{I have always complied with WhatsApp Terms of Service|I use WhatsApp strictly for personal and professional communication|My business contacts depend on this account}. {Please review my account status and lift the registration restriction|Kindly unblock the SMS verification gate for my number|I request your assistance to restore normal SMS login functionality}.

Attached is the exact screenshot of the error message encountered.

{Sincerely|Best regards|Thank you for your support},
{sender_name}"""
    },
    # Scenario B: Device Switch Registration Lock
    {
        "name": "New Device Switch Lock",
        "subject": "{Account Support|Verification Assistance}: {Registration Code Error|Login Unavailable|SMS Failure on New Phone} - {phone_number}",
        "body": """{Hi WhatsApp Team|To WhatsApp Customer Support|Hello},

{I recently transferred my SIM card to a new device|I switched my primary phone to a new {device_model}} and tried to sign in to my official WhatsApp account. However, I am blocked by the screen stating "Login not available right now".

{My number is active|I can receive regular SMS and calls from my operator {carrier_name}}, but WhatsApp SMS verification fails to trigger.

{Account & Device Details|Technical Profile}:
- Phone Number: {phone_number}
- Carrier / Country: {carrier_name} / {country_name}
- Hardware Model: {device_model} ({os_version})
- App Version: {wa_version}

{I urgently need access to my account as all my work contacts are on WhatsApp|This account is essential for my daily communications|I have done nothing wrong and always follow WhatsApp policies}. {Please check my number and enable SMS verification again|Kindly reset my login verification lock|Your prompt help in restoring my login would be greatly appreciated}.

{Regards|Thank you|Sincerely},
{sender_name}"""
    },
    # Scenario C: Network Carrier Glitch Lock
    {
        "name": "Carrier Network Glitch",
        "subject": "{Help Needed|Support Ticket|Login Issue}: {Verification System Unavailable|SMS Gateway Error|Login Not Available} - {phone_number}",
        "body": """{Dear Support|Hello WhatsApp Team|Greetings},

I am writing to report a login issue with my WhatsApp account ({phone_number}). When trying to receive the SMS verification code, the app continuously shows "Login not available right now".

{Due to a temporary network disturbance with my carrier|During a temporary signal loss on {carrier_name}}, the SMS request failed and now my device seems to be flagged by the security system.

{Diagnostics|Information}:
- Phone Number: {phone_number}
- Country & Carrier: {country_name} - {carrier_name}
- Mobile Device: {device_model} ({os_build})
- Client: Official WhatsApp {wa_version}

{Please verify that my account is in good standing and reset the SMS gateway limit|I kindly request an inspection of my account to clear this login block|My account is vital for my daily duties}.

{Best regards|Warm regards|Thank you},
{sender_name}"""
    }
]

def build_appeal_email(phone_data: Dict[str, Any], device_data: Dict[str, Any], sender_name: str) -> Dict[str, str]:
    scenario = random.choice(SCENARIOS)
    
    replacements = {
        "phone_number": phone_data["formatted"],
        "country_name": phone_data["country"],
        "carrier_name": phone_data["carrier"],
        "device_model": f"{device_data['brand']} {device_data['model']}",
        "os_version": device_data["os"],
        "os_build": device_data["build"],
        "wa_version": device_data["wa_version"],
        "sender_name": sender_name
    }
    
    raw_subject = scenario["subject"]
    raw_body = scenario["body"]
    
    for k, v in replacements.items():
        raw_subject = raw_subject.replace(f"{{{k}}}", str(v))
        raw_body = raw_body.replace(f"{{{k}}}", str(v))
        
    final_subject = process_spintax(raw_subject)
    final_body = process_spintax(raw_body)
    
    return {
        "scenario_name": scenario["name"],
        "subject": final_subject,
        "body": final_body,
        "sender_name": sender_name
    }
