import re
import urllib.parse
import secrets
from typing import Dict, Any, Tuple, Optional
from config import OUTBOUND_PROXY

# Lookup table mapping phone prefixes to Operator Name, Country Code, and Primary ASN
PREFIX_ASN_MAP = [
    # Indonesia (ID - 510)
    (r"^\+?62811|^\+?62812|^\+?62813|^\+?62821|^\+?62822|^\+?62823|^\+?62851|^\+?62852|^\+?62853", {"carrier": "Telkomsel", "country_code": "id", "asn": "23693"}),
    (r"^\+?62814|^\+?62815|^\+?62816|^\+?62855|^\+?62856|^\+?62857|^\+?62858", {"carrier": "Indosat Ooredoo", "country_code": "id", "asn": "4761"}),
    (r"^\+?62817|^\+?62818|^\+?62819|^\+?62859|^\+?62877|^\+?62878|^\+?62831|^\+?62832|^\+?62838", {"carrier": "XL Axiata / Axis", "country_code": "id", "asn": "24203"}),
    (r"^\+?62881|^\+?62882|^\+?62883|^\+?62884|^\+?62885|^\+?62886|^\+?62887|^\+?62888|^\+?62889", {"carrier": "Smartfren", "country_code": "id", "asn": "45727"}),
    (r"^\+?62895|^\+?62896|^\+?62897|^\+?62898|^\+?62899", {"carrier": "Tri (3 / IOH)", "country_code": "id", "asn": "45558"}),
    
    # Brazil (BR - 724)
    (r"^\+?55119|^\+?55219|^\+?55319|^\+?55419|^\+?55519", {"carrier": "Claro Brazil", "country_code": "br", "asn": "28573"}),
    (r"^\+?55159|^\+?55199|^\+?55479|^\+?55489", {"carrier": "Vivo (Telefonica)", "country_code": "br", "asn": "26599"}),
    (r"^\+?55129|^\+?55139|^\+?55149", {"carrier": "TIM Brasil", "country_code": "br", "asn": "26615"}),
    
    # United States (US - 310)
    (r"^\+?1212|^\+?1315|^\+?1516|^\+?1631|^\+?1718|^\+?1917", {"carrier": "AT&T Mobility", "country_code": "us", "asn": "7018"}),
    (r"^\+?1201|^\+?1551|^\+?1609|^\+?1732|^\+?1856|^\+?1908", {"carrier": "Verizon Wireless", "country_code": "us", "asn": "6167"}),
    (r"^\+?1206|^\+?1253|^\+?1360|^\+?1425|^\+?1509", {"carrier": "T-Mobile USA", "country_code": "us", "asn": "21928"}),
]

def resolve_carrier_and_asn(phone_number: str) -> Dict[str, str]:
    """
    Resolves carrier name, country code ISO, and primary BGP ASN for a given E.164 phone number.
    """
    clean_phone = phone_number.strip().replace(" ", "").replace("-", "")
    if not clean_phone.startswith("+"):
        clean_phone = "+" + clean_phone
        
    for pattern, info in PREFIX_ASN_MAP:
        if re.search(pattern, clean_phone):
            return {
                "carrier": info["carrier"],
                "country_code": info["country_code"],
                "asn": info["asn"],
                "display": f"{info['carrier']} (AS{info['asn']})"
            }
            
    # Generic Country Code Fallback
    if clean_phone.startswith("+62"):
        return {"carrier": "Indonesian Carrier", "country_code": "id", "asn": "23693", "display": "Indonesia (AS23693)"}
    elif clean_phone.startswith("+55"):
        return {"carrier": "Brazilian Carrier", "country_code": "br", "asn": "28573", "display": "Brazil (AS28573)"}
    elif clean_phone.startswith("+1"):
        return {"carrier": "US Mobile Network", "country_code": "us", "asn": "7018", "display": "USA (AS7018)"}
        
    return {"carrier": "Mobile Network", "country_code": "id", "asn": "23693", "display": "Mobile (AS23693)"}

def extract_flameproxies_prefix() -> str:
    """
    Extracts base customer sub-account package prefix from OUTBOUND_PROXY in .env.
    """
    if OUTBOUND_PROXY:
        try:
            p = urllib.parse.urlparse(OUTBOUND_PROXY)
            username = p.username or ""
            if "-country-" in username:
                return username.split("-country-")[0]
            elif "-package-" in username:
                return username
        except Exception:
            pass
    return "flm0634e734-package-standard"

def build_flameproxies_usn(phone_number: str) -> str:
    """
    Builds dynamic, carrier-matched FlameProxies Username (USN) string with unique session ID.
    Example output:
    flm0634e734-package-standard-country-id-asn-23693-mode-fast-session-09d1c200c5-time-100
    """
    meta = resolve_carrier_and_asn(phone_number)
    customer_prefix = extract_flameproxies_prefix()
    country_code = meta["country_code"].lower()
    asn = meta["asn"]
    session_id = secrets.token_hex(5)
    
    usn = f"{customer_prefix}-country-{country_code}-asn-{asn}-mode-fast-session-{session_id}-time-100"
    return usn
