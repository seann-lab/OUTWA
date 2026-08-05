import re
from typing import List
from core.generator import parse_phone_metadata

def extract_phone_numbers_from_text(raw_text: str) -> List[str]:
    """
    Extracts and normalizes raw phone numbers from text/lines/CSV into valid E.164 list.
    """
    lines = raw_text.splitlines()
    numbers = []
    seen = set()
    
    for line in lines:
        # Split by common delimiters like comma, semicolon, tab, pipe
        tokens = re.split(r"[,;\t|]+", line)
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            # Remove spaces, dashes, parentheses
            cleaned = re.sub(r"[^\d+]", "", token)
            if not cleaned:
                continue
                
            # Parse metadata
            meta = parse_phone_metadata(cleaned)
            if meta["valid"]:
                formatted = meta["formatted"]
                if formatted not in seen:
                    seen.add(formatted)
                    numbers.append(formatted)
            elif len(cleaned) >= 9:
                # Basic fallback if prefix is missing (assume +62 if 08xx)
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
