import re
import os
from pathlib import Path
from typing import List, Dict, Any
from core.generator import parse_phone_metadata
from config import INPUT_DIR

def extract_phone_numbers_from_text(raw_text: str) -> List[str]:
    """
    Extracts and normalizes raw phone numbers from text/lines/CSV into valid E.164 list.
    """
    lines = raw_text.splitlines()
    numbers = []
    seen = set()
    
    for line in lines:
        tokens = re.split(r"[,;\t|]+", line)
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            cleaned = re.sub(r"[^\d+]", "", token)
            if not cleaned:
                continue
                
            meta = parse_phone_metadata(cleaned)
            if meta["valid"]:
                formatted = meta["formatted"]
                if formatted not in seen:
                    seen.add(formatted)
                    numbers.append(formatted)
            elif len(cleaned) >= 9:
                if cleaned.startswith("08"):
                    cleaned = "+62" + cleaned[1:]
                elif not cleaned.startswith("+"):
                    cleaned = "+" + cleaned
                    
                meta_retry = parse_phone_metadata(cleaned)
                if meta_retry["valid"]:
                    formatted = meta_retry["formatted"]
                    if formatted not in seen:
                        seen.add(formatted)
                        numbers.append(formatted)
                        
    return numbers

def get_available_input_files() -> List[Dict[str, Any]]:
    """
    Scans INPUT_DIR for .txt, .csv, and .tsv files.
    Returns list of dicts with file metadata.
    """
    if not INPUT_DIR.exists():
        INPUT_DIR.mkdir(parents=True, exist_ok=True)
        
    valid_exts = {".txt", ".csv", ".tsv"}
    files_info = []
    
    for item in os.listdir(INPUT_DIR):
        item_path = INPUT_DIR / item
        if item_path.is_file() and item_path.suffix.lower() in valid_exts:
            size_kb = item_path.stat().st_size / 1024.0
            try:
                with open(item_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    extracted = extract_phone_numbers_from_text(content)
                    line_count = len(extracted)
            except Exception:
                line_count = 0
                
            files_info.append({
                "filename": item,
                "filepath": str(item_path.resolve()),
                "size_kb": round(size_kb, 1),
                "count": line_count
            })
            
    files_info.sort(key=lambda x: x["filename"].lower())
    return files_info
