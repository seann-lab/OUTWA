import requests
import logging
from typing import Dict, Any, List, Tuple, Optional

logger = logging.getLogger(__name__)

WA_ENGINE_URL = "http://127.0.0.1:12711"

# In-Memory RAM Cache for 0.01ms Zero-Latency UI rendering
_cached_wa_health: Dict[str, Any] = {
    "status": "UNKNOWN",
    "connection": "DISCONNECTED",
    "registered": False,
    "user": None
}

def update_wa_engine_health_cache() -> Dict[str, Any]:
    """
    Background worker update function. Runs every 5s in asyncio loop.
    """
    global _cached_wa_health
    try:
        resp = requests.get(f"{WA_ENGINE_URL}/health", timeout=2)
        if resp.status_code == 200:
            _cached_wa_health = resp.json()
            return _cached_wa_health
    except Exception:
        pass
    _cached_wa_health = {"status": "OFFLINE", "connection": "DISCONNECTED", "registered": False, "user": None}
    return _cached_wa_health

def check_wa_engine_health() -> Dict[str, Any]:
    """
    Returns in-memory cached health status immediately (Zero I/O blocking, < 0.01ms).
    """
    return _cached_wa_health

def request_wa_pairing_code(phone_number: str) -> Tuple[bool, str, str]:
    """
    Requests 8-digit pairing code from Baileys WA Engine.
    """
    try:
        resp = requests.post(
            f"{WA_ENGINE_URL}/request-pairing",
            json={"phone": phone_number},
            timeout=35
        )
        data = resp.json()
        if data.get("success"):
            if data.get("registered"):
                return True, "ALREADY_REGISTERED", ""
            return True, data.get("pairingCode", ""), data.get("rawCode", "")
        return False, data.get("error", "Unknown pairing error"), ""
    except Exception as e:
        return False, f"Gagal terhubung ke WA Engine ({str(e)})", ""

def start_wa_bulk_scan(job_id: str, phone_numbers: List[str]) -> Tuple[bool, str]:
    try:
        resp = requests.post(
            f"{WA_ENGINE_URL}/scan",
            json={"jobId": job_id, "numbers": phone_numbers},
            timeout=10
        )
        data = resp.json()
        if data.get("success"):
            return True, job_id
        return False, data.get("error", "Scan initialization failed")
    except Exception as e:
        return False, f"Failed to start scan job: {str(e)}"

def get_wa_scan_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    try:
        resp = requests.get(f"{WA_ENGINE_URL}/job", params={"id": job_id}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                return data.get("job")
    except Exception:
        pass
    return None

def cancel_wa_scan_job(job_id: str) -> bool:
    try:
        resp = requests.post(f"{WA_ENGINE_URL}/cancel-job", json={"jobId": job_id}, timeout=5)
        return resp.json().get("success", False)
    except Exception:
        return False
