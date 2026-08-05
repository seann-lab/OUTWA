import sqlite3
import time
from typing import Optional, List, Dict, Tuple
from config import DB_PATH, COOLDOWN_SECONDS

def get_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Appeals table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS appeals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT NOT NULL,
                country_code TEXT,
                carrier TEXT,
                sender_email TEXT NOT NULL,
                message_id TEXT UNIQUE,
                subject TEXT,
                body TEXT,
                status TEXT DEFAULT 'PENDING',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        
        # Cooldown registry table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cooldown_registry (
                phone_number TEXT PRIMARY KEY,
                last_appeal_at INTEGER NOT NULL
            )
        """)
        
        # Senders pool table (dynamically manageable)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS senders_pool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                status TEXT DEFAULT 'ACTIVE',
                total_sent INTEGER DEFAULT 0,
                last_used_at INTEGER DEFAULT 0
            )
        """)
        
        # WA Profiler scan jobs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wa_profiler_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT UNIQUE NOT NULL,
                total_numbers INTEGER DEFAULT 0,
                status TEXT DEFAULT 'RUNNING',
                file_path TEXT,
                created_at INTEGER NOT NULL
            )
        """)
        
        # System settings / pointer index
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        conn.commit()

def check_cooldown(phone_number: str) -> Tuple[bool, int]:
    now = int(time.time())
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT last_appeal_at FROM cooldown_registry WHERE phone_number = ?", (phone_number,))
        row = cursor.fetchone()
        if row:
            last_appeal = row["last_appeal_at"]
            elapsed = now - last_appeal
            if elapsed < COOLDOWN_SECONDS:
                return True, COOLDOWN_SECONDS - elapsed
    return False, 0

def set_cooldown(phone_number: str):
    now = int(time.time())
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO cooldown_registry (phone_number, last_appeal_at)
            VALUES (?, ?)
            ON CONFLICT(phone_number) DO UPDATE SET last_appeal_at = ?
        """, (phone_number, now, now))
        conn.commit()

def add_appeal(phone_number: str, country_code: str, carrier: str, sender_email: str, message_id: str, subject: str, body: str) -> int:
    now = int(time.time())
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO appeals (phone_number, country_code, carrier, sender_email, message_id, subject, body, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
        """, (phone_number, country_code, carrier, sender_email, message_id, subject, body, now, now))
        appeal_id = cursor.lastrowid
        conn.commit()
    set_cooldown(phone_number)
    return appeal_id

def mark_appeal_success(appeal_id: int) -> bool:
    now = int(time.time())
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE appeals SET status = 'SUCCESS', updated_at = ? WHERE id = ?", (now, appeal_id))
        conn.commit()
        return cursor.rowcount > 0

def mark_appeal_success_by_phone(phone_number: str) -> Optional[Dict]:
    now = int(time.time())
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM appeals WHERE phone_number = ? AND status = 'PENDING' ORDER BY id DESC LIMIT 1", (phone_number,))
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE appeals SET status = 'SUCCESS', updated_at = ? WHERE id = ?", (now, row["id"]))
            conn.commit()
            return dict(row)
    return None

def get_pending_appeals() -> List[Dict]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM appeals WHERE status = 'PENDING' ORDER BY id ASC")
        return [dict(r) for r in cursor.fetchall()]

def get_appeal_stats() -> Dict[str, int]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total FROM appeals")
        total = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) as success FROM appeals WHERE status = 'SUCCESS'")
        success = cursor.fetchone()["success"]
        cursor.execute("SELECT COUNT(*) as pending FROM appeals WHERE status = 'PENDING'")
        pending = cursor.fetchone()["pending"]
        return {"total": total, "success": success, "pending": pending}

# Sender Pool Management (Round Robin)
def sync_senders_from_env(env_accounts: List[Dict]):
    with get_connection() as conn:
        cursor = conn.cursor()
        for acc in env_accounts:
            cursor.execute("""
                INSERT INTO senders_pool (email, password, status)
                VALUES (?, ?, 'ACTIVE')
                ON CONFLICT(email) DO UPDATE SET password = ?
            """, (acc["email"], acc["password"], acc["password"]))
        conn.commit()

def add_sender(email: str, password: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO senders_pool (email, password, status)
            VALUES (?, ?, 'ACTIVE')
            ON CONFLICT(email) DO UPDATE SET password = ?, status = 'ACTIVE'
        """, (email, password, password))
        conn.commit()

def remove_sender(email: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM senders_pool WHERE email = ?", (email,))
        conn.commit()

def get_active_senders() -> List[Dict]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM senders_pool WHERE status = 'ACTIVE' ORDER BY id ASC")
        return [dict(r) for r in cursor.fetchall()]

def get_next_sender() -> Optional[Dict]:
    senders = get_active_senders()
    if not senders:
        return None
    
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'rr_pointer'")
        row = cursor.fetchone()
        pointer = int(row["value"]) if row else 0
        
        idx = pointer % len(senders)
        selected = senders[idx]
        
        next_pointer = idx + 1
        cursor.execute("""
            INSERT INTO settings (key, value) VALUES ('rr_pointer', ?)
            ON CONFLICT(key) DO UPDATE SET value = ?
        """, (str(next_pointer), str(next_pointer)))
        
        # update usage count
        now = int(time.time())
        cursor.execute("UPDATE senders_pool SET total_sent = total_sent + 1, last_used_at = ? WHERE id = ?", (now, selected["id"]))
        conn.commit()
        return selected

def record_profiler_job(job_id: str, total_numbers: int):
    now = int(time.time())
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO wa_profiler_jobs (job_id, total_numbers, status, created_at)
            VALUES (?, ?, 'RUNNING', ?)
            ON CONFLICT(job_id) DO UPDATE SET total_numbers = ?
        """, (job_id, total_numbers, now, total_numbers))
        conn.commit()

def complete_profiler_job(job_id: str, file_path: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE wa_profiler_jobs SET status = 'COMPLETED', file_path = ? WHERE job_id = ?
        """, (file_path, job_id))
        conn.commit()
