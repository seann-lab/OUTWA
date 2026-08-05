import random
import re
from typing import Dict, Any, Tuple
import phonenumbers
from phonenumbers import geocoder, carrier as carrier_mapper
from faker import Faker

# Real popular Android devices dataset
ANDROID_DEVICES = [
    {"brand": "Samsung", "model": "Galaxy A54 5G", "code": "SM-A546B", "os": "Android 14", "build": "UP1A.231005.007"},
    {"brand": "Samsung", "model": "Galaxy S23 Ultra", "code": "SM-S918B", "os": "Android 14", "build": "UP1A.231005.007"},
    {"brand": "Xiaomi", "model": "Redmi Note 13 Pro 5G", "code": "2312DRA50G", "os": "Android 13", "build": "TQ3A.230901.001"},
    {"brand": "Xiaomi", "model": "13T Pro", "code": "23078PND5G", "os": "Android 14", "build": "UKQ1.230804.001"},
    {"brand": "Realme", "model": "11 Pro+ 5G", "code": "RMX3741", "os": "Android 13", "build": "SP1A.210812.016"},
    {"brand": "Google", "model": "Pixel 7a", "code": "GWKK3", "os": "Android 14", "build": "AP1A.240305.019"},
    {"brand": "Motorola", "model": "Moto G84 5G", "code": "XT2347-2", "os": "Android 13", "build": "T1TC33.81-22"},
    {"brand": "OPPO", "model": "Reno10 Pro 5G", "code": "CPH2525", "os": "Android 13", "build": "TP1A.220905.001"},
    {"brand": "Vivo", "model": "V29 5G", "code": "V2250", "os": "Android 13", "build": "TP1A.220624.014"},
]

WA_BUILD_VERSIONS = [
    "v2.24.12.78 Official Play Store",
    "v2.24.11.85 Official Play Store",
    "v2.24.13.72 Official Play Store",
    "v2.24.10.76 Official Play Store",
]

# Faker locales mapping
COUNTRY_FAKER_MAP = {
    "ID": "id_ID",
    "BR": "pt_BR",
    "US": "en_US",
    "IN": "en_IN",
    "MX": "es_MX",
    "AR": "es_AR",
    "PH": "en_PH",
    "NG": "en_NG",
    "GB": "en_GB",
    "RU": "ru_RU",
}

def parse_phone_metadata(raw_phone: str) -> Dict[str, Any]:
    cleaned = re.sub(r"[^\d+]", "", raw_phone)
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
        
    try:
        parsed = phonenumbers.parse(cleaned, None)
        if not phonenumbers.is_valid_number(parsed):
            return {"valid": False, "formatted": cleaned, "country": "Unknown", "country_code": "XX", "carrier": "Unknown"}
            
        country_name = geocoder.description_for_number(parsed, "en") or "Unknown"
        iso_code = phonenumbers.region_code_for_number(parsed) or "US"
        carrier_name = carrier_mapper.name_for_number(parsed, "en") or "Mobile Network"
        formatted_e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        formatted_intl = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        
        return {
            "valid": True,
            "formatted": formatted_e164,
            "display": formatted_intl,
            "country": country_name,
            "country_code": iso_code,
            "carrier": carrier_name
        }
    except Exception:
        return {"valid": False, "formatted": cleaned, "country": "Unknown", "country_code": "XX", "carrier": "Unknown"}

def generate_random_identity(country_code: str) -> str:
    locale = COUNTRY_FAKER_MAP.get(country_code.upper(), "en_US")
    try:
        fake = Faker([locale, "en_US"])
    except Exception:
        fake = Faker("en_US")
    return fake.name()

def generate_device_spec() -> Dict[str, str]:
    dev = random.choice(ANDROID_DEVICES)
    wa_ver = random.choice(WA_BUILD_VERSIONS)
    return {
        "brand": dev["brand"],
        "model": dev["model"],
        "code": dev["code"],
        "os": dev["os"],
        "build": dev["build"],
        "wa_version": wa_ver,
        "full_spec": f"{dev['brand']} {dev['model']} ({dev['code']}) - {dev['os']} [{dev['build']}]"
    }
