import csv
import io
import os
import datetime
from pathlib import Path
from typing import List, Dict, Any
from core.asn_lookup import resolve_carrier_and_asn, build_flameproxies_usn

def generate_wa_profiler_csv(results: List[Dict[str, Any]], output_dir: Path, include_non_wa: bool = False) -> str:
    """
    Generates structured CSV file for WA Profiler results.
    Filters out non-WA numbers by default to produce clean, high-value reports.
    Includes Carrier ASN info and targeted FlameProxies USN for active WA numbers.
    Returns absolute file path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"WA_Profiler_Active_Report_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = output_dir / filename
    
    headers = [
        "No Telepon Target",
        "Negara / Wilayah",
        "Carrier & ASN",
        "FlameProxies USN (Carrier Matched)",
        "Status WA",
        "Tipe Akun",
        "Status Vermet (Meta Verified)",
        "Nama Resmi Vermet",
        "Level Verifikasi",
        "Ada Penawaran / Katalog",
        "Kategori Bisnis",
        "Deskripsi Bisnis",
        "Bio Status WA"
    ]
    
    rows = []
    for item in results:
        exists = item.get("exists", False)
        
        # Filter out unregistered numbers unless explicitly requested
        if not exists and not include_non_wa:
            continue
            
        phone = item.get("phone", "")
        status_wa = "TERDAFTAR (VALID)" if exists else "TIDAK TERDAFTAR"
        
        country = item.get("country", "")
        if not country and phone.startswith("+62"):
            country = "Indonesia"
            
        carrier_meta = resolve_carrier_and_asn(phone)
        carrier_asn_str = carrier_meta["display"]
        flameproxies_usn = build_flameproxies_usn(phone)
            
        account_type = item.get("accountType", "Personal")
        is_vermet = "🟢 VERIFIED" if item.get("isVermet") else "⚪ UNVERIFIED"
        verified_name = item.get("verifiedName", "") or "-"
        verified_level = item.get("verifiedLevel", "none").upper()
        
        has_offers = "🛍️ ADA PENAWARAN/KATALOG" if item.get("hasOffers") else "NIL"
        category = item.get("category", "") or "-"
        description = (item.get("description", "") or "-").replace("\n", " ")
        bio = (item.get("bio", "") or "-").replace("\n", " ")
        
        rows.append([
            phone,
            country,
            carrier_asn_str,
            flameproxies_usn,
            status_wa,
            account_type,
            is_vermet,
            verified_name,
            verified_level,
            has_offers,
            category,
            description,
            bio
        ])
        
    with open(filepath, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
        
    return str(filepath.resolve())
