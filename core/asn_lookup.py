import re
import urllib.parse
import secrets
from typing import Dict, Any, Tuple, Optional
import phonenumbers
from phonenumbers import geocoder, carrier as phone_carrier
from config import OUTBOUND_PROXY

# Lookup map for specific carriers -> ASN
CARRIER_ASN_MAP = [
    # Indonesia (ID)
    (r"^\+?62811|^\+?62812|^\+?62813|^\+?62821|^\+?62822|^\+?62823|^\+?62851|^\+?62852|^\+?62853", {"carrier": "Telkomsel", "country_code": "id", "asn": "23693"}),
    (r"^\+?62814|^\+?62815|^\+?62816|^\+?62855|^\+?62856|^\+?62857|^\+?62858", {"carrier": "Indosat Ooredoo", "country_code": "id", "asn": "4761"}),
    (r"^\+?62817|^\+?62818|^\+?62819|^\+?62859|^\+?62877|^\+?62878|^\+?62831|^\+?62832|^\+?62838", {"carrier": "XL Axiata", "country_code": "id", "asn": "24203"}),
    (r"^\+?62881|^\+?62882|^\+?62883|^\+?62884|^\+?62885|^\+?62886|^\+?62887|^\+?62888|^\+?62889", {"carrier": "Smartfren", "country_code": "id", "asn": "45727"}),
    (r"^\+?62895|^\+?62896|^\+?62897|^\+?62898|^\+?62899", {"carrier": "Tri (3 / IOH)", "country_code": "id", "asn": "45558"}),

    # Saudi Arabia (SA)
    (r"^\+?96650|^\+?96653|^\+?96655|^\+?96651", {"carrier": "STC (Saudi Telecom)", "country_code": "sa", "asn": "39386"}),
    (r"^\+?96654|^\+?96656", {"carrier": "Mobily (Etihad Etisalat)", "country_code": "sa", "asn": "35819"}),
    (r"^\+?96658|^\+?96659", {"carrier": "Zain KSA", "country_code": "sa", "asn": "43766"}),

    # United Arab Emirates (AE)
    (r"^\+?97150|^\+?97154|^\+?97156", {"carrier": "Etisalat (e&)", "country_code": "ae", "asn": "8966"}),
    (r"^\+?97152|^\+?97155|^\+?97158", {"carrier": "du (EITC)", "country_code": "ae", "asn": "15802"}),

    # Egypt (EG)
    (r"^\+?2010", {"carrier": "Vodafone Egypt", "country_code": "eg", "asn": "24863"}),
    (r"^\+?2012", {"carrier": "Orange Egypt", "country_code": "eg", "asn": "8452"}),
    (r"^\+?2011", {"carrier": "Etisalat Misr", "country_code": "eg", "asn": "36992"}),
    (r"^\+?2015", {"carrier": "WE (Telecom Egypt)", "country_code": "eg", "asn": "8452"}),

    # Kuwait (KW)
    (r"^\+?9659", {"carrier": "Zain Kuwait", "country_code": "kw", "asn": "31006"}),
    (r"^\+?9656", {"carrier": "Ooredoo Kuwait", "country_code": "kw", "asn": "20858"}),
    (r"^\+?9655", {"carrier": "STC Kuwait", "country_code": "kw", "asn": "47589"}),

    # Qatar (QA)
    (r"^\+?9745|^\+?9746", {"carrier": "Ooredoo Qatar", "country_code": "qa", "asn": "8781"}),
    (r"^\+?9743|^\+?9747", {"carrier": "Vodafone Qatar", "country_code": "qa", "asn": "48020"}),

    # Oman (OM)
    (r"^\+?9689", {"carrier": "Omantel", "country_code": "om", "asn": "8529"}),
    (r"^\+?9687", {"carrier": "Ooredoo Oman", "country_code": "om", "asn": "28885"}),

    # Jordan (JO)
    (r"^\+?96279", {"carrier": "Zain Jordan", "country_code": "jo", "asn": "9038"}),
    (r"^\+?96277", {"carrier": "Orange Jordan", "country_code": "jo", "asn": "8376"}),
    (r"^\+?96278", {"carrier": "Umniah", "country_code": "jo", "asn": "25324"}),

    # Iraq (IQ)
    (r"^\+?96478|^\+?96479", {"carrier": "Zain Iraq", "country_code": "iq", "asn": "51684"}),
    (r"^\+?96477", {"carrier": "Asiacell", "country_code": "iq", "asn": "51375"}),
    (r"^\+?96475", {"carrier": "Korek Telecom", "country_code": "iq", "asn": "50953"}),

    # Bahrain (BH)
    (r"^\+?97339|^\+?97338|^\+?97332", {"carrier": "Batelco", "country_code": "bh", "asn": "5416"}),
    (r"^\+?97336|^\+?97337", {"carrier": "Zain Bahrain", "country_code": "bh", "asn": "35753"}),
    (r"^\+?97333|^\+?97334", {"carrier": "STC Bahrain", "country_code": "bh", "asn": "43845"}),

    # Morocco (MA)
    (r"^\+?21266|^\+?21267", {"carrier": "Maroc Telecom", "country_code": "ma", "asn": "6167"}),
    (r"^\+?21261|^\+?21262", {"carrier": "Orange Morocco", "country_code": "ma", "asn": "36903"}),
    (r"^\+?21263|^\+?21264", {"carrier": "inwi", "country_code": "ma", "asn": "36925"}),

    # Algeria (DZ)
    (r"^\+?2136", {"carrier": "Mobilis", "country_code": "dz", "asn": "36947"}),
    (r"^\+?2137", {"carrier": "Djezzy", "country_code": "dz", "asn": "33777"}),
    (r"^\+?2135", {"carrier": "Ooredoo Algeria", "country_code": "dz", "asn": "36947"}),

    # Brazil (BR)
    (r"^\+?55119|^\+?55219|^\+?55319|^\+?55419", {"carrier": "Claro Brazil", "country_code": "br", "asn": "28573"}),
    (r"^\+?55159|^\+?55199|^\+?55479|^\+?55489", {"carrier": "Vivo (Telefonica)", "country_code": "br", "asn": "26599"}),
    (r"^\+?55129|^\+?55139|^\+?55149", {"carrier": "TIM Brasil", "country_code": "br", "asn": "26615"}),

    # United States (US)
    (r"^\+?1212|^\+?1315|^\+?1516|^\+?1631|^\+?1718|^\+?1917", {"carrier": "AT&T Mobility", "country_code": "us", "asn": "7018"}),
    (r"^\+?1201|^\+?1551|^\+?1609|^\+?1732|^\+?1856|^\+?1908", {"carrier": "Verizon Wireless", "country_code": "us", "asn": "6167"}),
    (r"^\+?1206|^\+?1253|^\+?1360|^\+?1425|^\+?1509", {"carrier": "T-Mobile USA", "country_code": "us", "asn": "21928"}),
]

# Country default ASN map for fallback when carrier regex isn't specific
COUNTRY_DEFAULT_ASN = {
    "sa": "39386",  # Saudi Arabia (STC)
    "ae": "8966",   # UAE (Etisalat)
    "eg": "24863",  # Egypt (Vodafone)
    "kw": "31006",  # Kuwait (Zain)
    "qa": "8781",   # Qatar (Ooredoo)
    "om": "8529",   # Oman (Omantel)
    "jo": "9038",   # Jordan (Zain)
    "iq": "51684",  # Iraq (Zain)
    "bh": "5416",   # Bahrain (Batelco)
    "ma": "6167",   # Morocco (Maroc Telecom)
    "dz": "36947",  # Algeria (Mobilis)
    "tn": "37492",  # Tunisia (Ooredoo)
    "lb": "9051",   # Lebanon (Alfa/Touch)
    "ye": "30873",  # Yemen (Yemen Mobile)
    "sd": "36972",  # Sudan (Zain)
    "ly": "37284",  # Libya (Libyana)
    "sy": "29256",  # Syria (Syriatel)
    "ps": "20935",  # Palestine (Jawwal)
    "id": "23693",  # Indonesia (Telkomsel)
    "br": "28573",  # Brazil (Claro)
    "us": "7018",   # USA (AT&T)
    "gb": "5089",   # UK (Virgin Media / O2)
    "de": "3320",   # Germany (Deutsche Telekom)
    "fr": "12322",  # France (Free / Orange)
    "tr": "47524",  # Turkey (Turkcell)
    "in": "55836",  # India (Reliance Jio)
    "my": "4788",   # Malaysia (TM / CelcomDigi)
    "ph": "9299",   # Philippines (PLDT / Smart)
    "sg": "4657",   # Singapore (Singtel)
    "ng": "29465",  # Nigeria (MTN)
    "pk": "24499",  # Pakistan (Jazz / Telenor)
    "ru": "12389",  # Russia (Rostelecom / MTS)
    "mx": "8151",   # Mexico (Uninet / Telcel)
    "ca": "852",    # Canada (TELUS / Rogers)
    "au": "1221",   # Australia (Telstra)
}

def resolve_carrier_and_asn(phone_number: str) -> Dict[str, str]:
    """
    Resolves carrier name, ISO country code, country display name, and BGP ASN for any E.164 phone number.
    Uses carrier regex map -> phonenumbers library -> country default ASN.
    """
    clean_phone = phone_number.strip().replace(" ", "").replace("-", "")
    if not clean_phone.startswith("+"):
        clean_phone = "+" + clean_phone

    # 1. Specific carrier regex check
    for pattern, info in CARRIER_ASN_MAP:
        if re.search(pattern, clean_phone):
            return {
                "carrier": info["carrier"],
                "country_code": info["country_code"],
                "country_name": geocoder.description_for_number(phonenumbers.parse(clean_phone), "en") or info["country_code"].upper(),
                "asn": info["asn"],
                "display": f"{info['carrier']} (AS{info['asn']})"
            }

    # 2. Dynamic phonenumbers resolution for ANY country in the world
    try:
        parsed = phonenumbers.parse(clean_phone)
        iso_code = phonenumbers.region_code_for_number(parsed)
        if iso_code:
            iso_code = iso_code.lower()
            cntry_name = geocoder.description_for_number(parsed, "en") or iso_code.upper()
            c_name = phone_carrier.name_for_number(parsed, "en") or f"{cntry_name} Mobile"
            
            # Lookup default ASN for the detected country ISO, fallback to 23693 if unknown
            asn = COUNTRY_DEFAULT_ASN.get(iso_code, "23693")
            
            return {
                "carrier": c_name,
                "country_code": iso_code,
                "country_name": cntry_name,
                "asn": asn,
                "display": f"{c_name} (AS{asn})"
            }
    except Exception:
        pass

    # 3. Final safety fallback (only if parsing failed entirely)
    return {
        "carrier": "International Carrier",
        "country_code": "us",
        "country_name": "International",
        "asn": "7018",
        "display": "International (AS7018)"
    }

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
    Example output for Saudi Arabia:
    flm0634e734-package-standard-country-sa-asn-39386-mode-fast-session-09d1c200c5-time-100
    """
    meta = resolve_carrier_and_asn(phone_number)
    customer_prefix = extract_flameproxies_prefix()
    country_code = meta["country_code"].lower()
    asn = meta["asn"]
    session_id = secrets.token_hex(5)

    usn = f"{customer_prefix}-country-{country_code}-asn-{asn}-mode-fast-session-{session_id}-time-100"
    return usn
