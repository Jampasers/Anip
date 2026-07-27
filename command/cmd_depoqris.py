"""
QRIS Deposit Module - GoPay Merchant Integration
Deposit WL menggunakan QRIS statis dan validasi transaksi via Merchant Analytics.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import discord
import qrcode
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

# GoPay Merchant API Config
GOPAY_MERCHANT_BASE_URL = os.getenv("GOPAY_MERCHANT_BASE_URL", "https://api.gojekapi.com").rstrip("/")
GOBIZ_BASE_URL = os.getenv("GOBIZ_BASE_URL", "https://api.gobiz.co.id").rstrip("/")
GOBIZ_CLIENT_ID = os.getenv("GOBIZ_CLIENT_ID", "go-biz-web-new").strip()
GOPAY_EMAIL = os.getenv("GOPAY_EMAIL", "").strip()
GOPAY_PASSWORD = os.getenv("GOPAY_PASSWORD", "").strip()
GOPAY_MERCHANT_TOKEN = os.getenv("GOPAY_MERCHANT_TOKEN", os.getenv("GOPAY_BEARER_TOKEN", "")).strip()
GOPAY_MERCHANT_ID = os.getenv("GOPAY_MERCHANT_ID", "").strip()
GOPAY_TRANSACTION_QUERY_STATUSES = os.getenv(
    "GOPAY_TRANSACTION_QUERY_STATUSES",
    "SETTLEMENT,CAPTURE,REFUND,PARTIAL_REFUND",
).strip()
GOPAY_TRANSACTION_PAYMENT_TYPES = os.getenv(
    "GOPAY_TRANSACTION_PAYMENT_TYPES",
    "QRIS,GOPAY,OFFLINE_CREDIT_CARD,OFFLINE_DEBIT_CARD,CREDIT_CARD",
).strip()
GOPAY_TRANSACTION_FETCH_SIZE = int(os.getenv("GOPAY_TRANSACTION_FETCH_SIZE", "100"))

# QR static config
GOPAY_QRIS_STATIC_IMAGE_PATH = os.getenv("GOPAY_QRIS_STATIC_IMAGE_PATH", "").strip()
QRIS_STATIC_STRING = os.getenv(
    "QRIS_STATIC_STRING",
    "00020101021126610014COM.GO-JEK.WWW01189360091436016724240210G6016724240303UMI51440014ID.CO.QRIS.WWW0215ID10243572153580303UMI5204581553033605802ID5922Hanif Store, Tangerang6014KOTA TANGERANG61051511462070703A016304FEFD"
).strip()

# Pricing
DEFAULT_RATE_100_WL_RUPIAH = int(os.getenv("RATE_100_WL_RUPIAH", "210"))
RATE_SETTINGS_ID = 1
MIN_DEPOSIT_RUPIAH = 100
QRIS_TIMEOUT_SECONDS = int(os.getenv("QRIS_TIMEOUT_SECONDS", "300"))
QRIS_POLL_INTERVAL_SECONDS = int(os.getenv("QRIS_POLL_INTERVAL_SECONDS", "5"))
GOPAY_TOKEN_WATCH_INTERVAL_SECONDS = int(os.getenv("GOPAY_TOKEN_WATCH_INTERVAL_SECONDS", "300"))

# Log Channel
CHANNEL_QRIS_SUCCESS_LOG = int(os.getenv("CHANNEL_QRIS_SUCCESS_LOG", "0"))
TOKEN_SETTINGS_ID = 1

# Jakarta fixed offset, no DST.
JAKARTA_TZ = timezone(timedelta(hours=7))

# Globals
bot = None
c = None
conn = None
fmt_wl = None
PREFIX = "!"
_auth_lock = asyncio.Lock()
_deposit_create_lock = asyncio.Lock()

SUCCESS_TRANSACTION_STATUSES = {"SETTLEMENT", "CAPTURE"}


def now_jakarta() -> datetime:
    return datetime.now(JAKARTA_TZ)


def to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JAKARTA_TZ)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def local_iso(dt: datetime | None = None) -> str:
    dt = dt or now_jakarta()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JAKARTA_TZ)
    return dt.isoformat(timespec="seconds")


def _ensure_column(cur, connection, table_name: str, column_name: str, ddl_fragment: str) -> None:
    cur.execute(f"PRAGMA table_info({table_name})")
    columns = {row[1] for row in cur.fetchall()}
    if column_name not in columns:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_fragment}")
        connection.commit()


def ensure_qris_deposits_schema(cur, connection):
    """Create qris_deposits table if not exists and add migration columns."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS qris_deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            user_id INTEGER,
            amount_rupiah INTEGER,
            unique_code INTEGER DEFAULT 0,
            total_rupiah INTEGER DEFAULT 0,
            amount_wl INTEGER,
            status TEXT DEFAULT 'pending',
            expired_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            last_checked_at TEXT,
            merchant_tx_id TEXT,
            merchant_status TEXT,
            payment_type TEXT,
            payment_method TEXT DEFAULT 'gopay_merchant_qris'
        )
        """
    )
    _ensure_column(cur, connection, "qris_deposits", "unique_code", "INTEGER DEFAULT 0")
    _ensure_column(cur, connection, "qris_deposits", "total_rupiah", "INTEGER DEFAULT 0")
    _ensure_column(cur, connection, "qris_deposits", "last_checked_at", "TEXT")
    _ensure_column(cur, connection, "qris_deposits", "merchant_tx_id", "TEXT")
    _ensure_column(cur, connection, "qris_deposits", "merchant_status", "TEXT")
    _ensure_column(cur, connection, "qris_deposits", "payment_type", "TEXT")
    _ensure_column(cur, connection, "qris_deposits", "payment_method", "TEXT DEFAULT 'gopay_merchant_qris'")
    try:
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_qris_pending_total_rupiah
            ON qris_deposits(total_rupiah)
            WHERE status = 'pending' AND total_rupiah > 0
            """
        )
    except Exception as e:
        print(f"[QRIS] Failed to create pending total unique index: {e}")
    connection.commit()


def ensure_qris_rate_schema(cur, connection):
    """Create qris_rate_settings table and ensure default row exists."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS qris_rate_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            rate_100_wl INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        "INSERT OR IGNORE INTO qris_rate_settings (id, rate_100_wl) VALUES (?, ?)",
        (RATE_SETTINGS_ID, max(1, DEFAULT_RATE_100_WL_RUPIAH)),
    )
    connection.commit()


def ensure_qris_token_schema(cur, connection):
    """Create table for the live GoPay token, with .env as initial fallback."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS qris_token_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            token TEXT NOT NULL,
            merchant_id TEXT,
            updated_by INTEGER,
            updated_at TEXT,
            source TEXT
        )
        """
    )
    _ensure_column(cur, connection, "qris_token_settings", "merchant_id", "TEXT")
    _ensure_column(cur, connection, "qris_token_settings", "source", "TEXT")
    if GOPAY_MERCHANT_TOKEN:
        cur.execute(
            """
            INSERT OR IGNORE INTO qris_token_settings (id, token, merchant_id, updated_by, updated_at, source)
            VALUES (?, ?, ?, NULL, ?, ?)
            """,
            (TOKEN_SETTINGS_ID, GOPAY_MERCHANT_TOKEN, GOPAY_MERCHANT_ID, local_iso(), "env"),
        )
    connection.commit()


def get_active_merchant_token() -> str:
    """Return token from DB first, then fallback to the token loaded from .env."""
    if c is not None:
        try:
            c.execute("SELECT token FROM qris_token_settings WHERE id = ?", (TOKEN_SETTINGS_ID,))
            row = c.fetchone()
            if row and row[0]:
                return str(row[0]).strip()
        except Exception as e:
            print(f"[QRIS] Failed to load merchant token from DB: {e}")
    return GOPAY_MERCHANT_TOKEN


def get_active_merchant_id() -> str:
    """Return merchant id from DB first, then fallback to .env."""
    if c is not None:
        try:
            c.execute("SELECT merchant_id FROM qris_token_settings WHERE id = ?", (TOKEN_SETTINGS_ID,))
            row = c.fetchone()
            if row and row[0]:
                return str(row[0]).strip()
        except Exception as e:
            print(f"[QRIS] Failed to load merchant id from DB: {e}")
    return GOPAY_MERCHANT_ID


def mask_token(token: str) -> str:
    token = (token or "").strip()
    if len(token) <= 12:
        return "***"
    return f"{token[:6]}...{token[-6:]}"


def update_env_values(values: dict[str, str]) -> None:
    """Update selected keys in .env so the latest GoBiz token survives restarts."""
    env_path = Path(".env")
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        remaining = dict(values)
        new_lines = []

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                new_lines.append(line)
                continue

            key = line.split("=", 1)[0].strip()
            if key in remaining:
                value = str(remaining.pop(key)).replace("\n", "").replace("\r", "")
                new_lines.append(f"{key}={value}")
            else:
                new_lines.append(line)

        for key, value in remaining.items():
            value = str(value).replace("\n", "").replace("\r", "")
            new_lines.append(f"{key}={value}")

        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"[GoBizAuth] Failed to update .env: {e}")


def save_gopay_auth(token: str, merchant_id: str, source: str = "auto_login") -> None:
    """Persist GoBiz auth in memory, DB, and .env."""
    global GOPAY_MERCHANT_TOKEN, GOPAY_MERCHANT_ID
    token = (token or "").strip()
    merchant_id = (merchant_id or "").strip()
    if not token:
        return

    GOPAY_MERCHANT_TOKEN = token
    if merchant_id:
        GOPAY_MERCHANT_ID = merchant_id

    if c is not None and conn is not None:
        c.execute(
            """
            INSERT OR REPLACE INTO qris_token_settings (id, token, merchant_id, updated_by, updated_at, source)
            VALUES (?, ?, ?, NULL, ?, ?)
            """,
            (TOKEN_SETTINGS_ID, token, merchant_id or GOPAY_MERCHANT_ID, local_iso(), source),
        )
        conn.commit()

    env_updates = {"GOPAY_MERCHANT_TOKEN": token}
    if merchant_id or GOPAY_MERCHANT_ID:
        env_updates["GOPAY_MERCHANT_ID"] = merchant_id or GOPAY_MERCHANT_ID
    update_env_values(env_updates)


def get_gobiz_headers(unique_id: str, access_token: str | None = None) -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "id",
        "Authentication-Type": "go-id",
        "Authorization": f"Bearer {access_token}" if access_token else "Bearer",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "Gojek-Country-Code": "ID",
        "Gojek-Timezone": "Asia/Jakarta",
        "Origin": "https://portal.gofoodmerchant.co.id",
        "Referer": "https://portal.gofoodmerchant.co.id/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "X-AppVersion": "platform-v3.107.0-94ce5d57",
        "X-PhoneMake": "Windows 10 64-bit",
        "X-PhoneModel": "Chrome 149.0.0.0 on Windows 10 64-bit",
        "X-Platform": "Web",
        "X-User-Locale": "en-US",
        "X-User-Type": "merchant",
        "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "x-DeviceOS": "Web",
        "x-appId": "go-biz-web-dashboard",
        "x-uniqueid": unique_id,
    }


def extract_error_message(payload, fallback: str = "Unknown error") -> str:
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                return str(first.get("message") or fallback)
            return str(first)
        return str(payload.get("message") or fallback)
    return fallback


async def gobiz_post_json(url: str, payload: dict, headers: dict[str, str]) -> tuple[int, dict]:
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = {"message": (await resp.text())[:300]}
            return resp.status, data


async def login_gobiz_with_password() -> tuple[str, str]:
    """Login to GoBiz with GOPAY_EMAIL/GOPAY_PASSWORD and return token + merchant id."""
    if not GOPAY_EMAIL or not GOPAY_PASSWORD:
        raise RuntimeError("GOPAY_EMAIL/GOPAY_PASSWORD belum diisi di .env")

    unique_id = str(uuid.uuid4())
    headers = get_gobiz_headers(unique_id)

    status, validation_data = await gobiz_post_json(
        f"{GOBIZ_BASE_URL}/goid/login/request",
        {"email": GOPAY_EMAIL, "login_type": "password", "client_id": GOBIZ_CLIENT_ID},
        headers,
    )
    if status >= 400:
        raise RuntimeError(f"Validasi email GoBiz gagal ({status}): {extract_error_message(validation_data)}")

    status, token_data = await gobiz_post_json(
        f"{GOBIZ_BASE_URL}/goid/token",
        {
            "client_id": GOBIZ_CLIENT_ID,
            "grant_type": "password",
            "data": {"email": GOPAY_EMAIL, "password": GOPAY_PASSWORD},
        },
        headers,
    )
    if status >= 400 or token_data.get("errors"):
        raise RuntimeError(f"Login GoBiz gagal ({status}): {extract_error_message(token_data)}")

    access_token = str(token_data.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("Login GoBiz tidak mengembalikan access_token")

    merchant_id = await fetch_gobiz_merchant_id(access_token)
    return access_token, merchant_id


def extract_merchant_list(payload) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    candidates = (
        payload.get("merchants"),
        payload.get("hits"),
        payload.get("data"),
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            if candidate and isinstance(candidate[0], dict) and "_source" in candidate[0]:
                return [item.get("_source") or item for item in candidate]
            return [item for item in candidate if isinstance(item, dict)]

    nested_hits = payload.get("hits", {}).get("hits") if isinstance(payload.get("hits"), dict) else None
    if isinstance(nested_hits, list):
        return [item.get("_source") or item for item in nested_hits if isinstance(item, dict)]

    return []


async def fetch_gobiz_merchant_id(access_token: str) -> str:
    unique_id = str(uuid.uuid4())
    status, data = await gobiz_post_json(
        f"{GOBIZ_BASE_URL}/v1/merchants/search",
        {"from": 0, "to": 50, "_source": ["id", "merchant_name"]},
        get_gobiz_headers(unique_id, access_token),
    )
    if status >= 400:
        raise RuntimeError(f"Gagal mengambil merchant GoBiz ({status}): {extract_error_message(data)}")

    merchants = extract_merchant_list(data)
    if not merchants:
        raise RuntimeError("Tidak ada merchant yang terasosiasi dengan akun GoBiz ini")

    merchant_id = str(merchants[0].get("id") or merchants[0].get("merchant_id") or "").strip()
    if not merchant_id:
        raise RuntimeError("Merchant ID tidak ditemukan dari response GoBiz")

    merchant_name = merchants[0].get("merchant_name") or merchants[0].get("name") or "Unknown"
    print(f"[GoBizAuth] Using merchant {merchant_name} ({merchant_id})")
    return merchant_id


async def is_gobiz_token_valid(token: str) -> bool:
    token = (token or "").strip()
    if not token:
        return False

    try:
        status, _ = await gobiz_post_json(
            f"{GOBIZ_BASE_URL}/v1/merchants/search",
            {"from": 0, "to": 1, "_source": ["id"]},
            get_gobiz_headers(str(uuid.uuid4()), token),
        )
        return status != 401
    except Exception as e:
        print(f"[GoBizAuth] Token validation error: {e}")
        return False


async def ensure_gopay_authenticated(force_login: bool = False) -> bool:
    """Ensure a valid token + merchant id are available, logging in when needed."""
    async with _auth_lock:
        token = get_active_merchant_token()
        merchant_id = get_active_merchant_id()

        if token and merchant_id and not force_login and await is_gobiz_token_valid(token):
            save_gopay_auth(token, merchant_id, source="validated")
            return True

        print("[GoBizAuth] Token kosong/invalid, login ulang...")
        try:
            new_token, new_merchant_id = await login_gobiz_with_password()
            save_gopay_auth(new_token, new_merchant_id, source="auto_login")
            print(f"[GoBizAuth] Login sukses, token aktif {mask_token(new_token)}")
            return True
        except Exception as e:
            print(f"[GoBizAuth] Login gagal: {e}")
            return False


def get_rate_100_wl() -> int:
    """
    Return current rate from DB as: 100 WL = Rp X.
    Falls back to default if DB is unavailable.
    """
    fallback = max(1, DEFAULT_RATE_100_WL_RUPIAH)
    if c is None:
        return fallback

    try:
        c.execute("SELECT rate_100_wl FROM qris_rate_settings WHERE id = ?", (RATE_SETTINGS_ID,))
        row = c.fetchone()
        if row and row[0] is not None:
            return max(1, int(row[0]))
    except Exception as e:
        print(f"[QRIS] Failed to load rate from DB: {e}")

    return fallback


def format_rate_100_wl(rate_100_wl: int | None = None) -> str:
    """Format helper for displaying rate text in messages/logs."""
    rate = int(rate_100_wl if rate_100_wl is not None else get_rate_100_wl())
    if callable(fmt_wl):
        return f"100 WL = Rp {fmt_wl(rate)}"
    return f"100 WL = Rp {rate:,}".replace(",", ".")


def mask_growid(growid: str) -> str:
    """Mask GrowID for privacy."""
    if not growid or len(growid) < 1:
        return "xxx"
    return f"{growid[0].upper()}xxx"


def convert_rupiah_to_wl(rupiah_amount: int) -> int:
    """Convert Rupiah to WL using dynamic rate (100 WL = Rp X)."""
    rate_100_wl = get_rate_100_wl()
    return int((int(rupiah_amount) * 100) / rate_100_wl)


def parse_iso_datetime(iso_string: str) -> datetime | None:
    """Parse ISO datetime string and preserve timezone when present."""
    if not iso_string:
        return None

    try:
        value = str(iso_string).strip().replace("Z", "+00:00")
        pattern = r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d+)([+-]\d{2}:\d{2})"
        match = re.match(pattern, value)
        if match:
            base = match.group(1)
            decimals = match.group(2)[:6]
            tz = match.group(3)
            value = f"{base}.{decimals}{tz}"

        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=JAKARTA_TZ)
        return parsed
    except Exception as e:
        print(f"[QRIS] Failed to parse datetime '{iso_string}': {e}")
        return None


def generate_order_id() -> str:
    """Generate unique order ID."""
    now = now_jakarta()
    return f"DEP{now.strftime('%y%m%d%H%M%S')}{now.microsecond // 1000:03d}"


def normalize_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def extract_int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)

    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Rp", "").replace("IDR", "").replace(" ", "")
    if text.count(".") == 1 and text.replace(".", "", 1).isdigit():
        # Either decimal or thousand separator.
        left, right = text.split(".")
        if len(right) == 3 and left.replace("-", "").isdigit():
            try:
                return int(left + right)
            except ValueError:
                pass
        try:
            return int(float(text.replace(",", ".")))
        except ValueError:
            pass

    cleaned = re.sub(r"[^\d-]", "", text)
    if cleaned in {"", "-"}:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def normalize_merchant_amount(raw_amount) -> int | None:
    """
    Normalize GoPay merchant amount into Rupiah.
    Some API responses return values in minor units, so 900 means Rp 9.
    """
    amount = extract_int(raw_amount)
    if amount is None:
        return None
    if amount >= 100 and amount % 100 == 0:
        return amount // 100
    return amount


def normalize_status(value) -> str:
    return normalize_text(value).upper()


def get_transaction_id(tx: dict) -> str:
    return normalize_text(
        tx.get("id")
        or tx.get("transaction_id")
        or tx.get("transactionId")
        or tx.get("order_id")
        or tx.get("orderId")
        or tx.get("reference_id")
        or tx.get("referenceId")
    )


def get_transaction_datetime(tx: dict) -> datetime | None:
    for key in (
        "transaction_time",
        "transactionTime",
        "transaction_date",
        "transactionDate",
        "transacted_at",
        "transactedAt",
        "paid_at",
        "paidAt",
        "settled_at",
        "settledAt",
        "created_at",
        "createdAt",
        "updated_at",
        "updatedAt",
        "timestamp",
    ):
        raw_value = tx.get(key)
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            timestamp = float(raw_value)
            if timestamp > 1_000_000_000_000:
                timestamp = timestamp / 1000
            try:
                return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(JAKARTA_TZ)
            except Exception:
                pass

        parsed = parse_iso_datetime(normalize_text(raw_value))
        if parsed:
            return parsed
    return None


def merchant_transaction_already_used(merchant_tx_id: str) -> bool:
    if not merchant_tx_id or c is None:
        return False

    try:
        c.execute(
            """
            SELECT 1
            FROM qris_deposits
            WHERE merchant_tx_id = ?
              AND status = 'completed'
            LIMIT 1
            """,
            (merchant_tx_id,),
        )
        return c.fetchone() is not None
    except Exception as e:
        print(f"[QRIS] Failed to check used merchant tx id: {e}")
        return False


def extract_transactions(payload) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("transactions", "data", "items", "result", "records"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = extract_transactions(value)
            if nested:
                return nested

    for value in payload.values():
        if isinstance(value, list):
            nested = [item for item in value if isinstance(item, dict)]
            if nested:
                return nested
        elif isinstance(value, dict):
            nested = extract_transactions(value)
            if nested:
                return nested

    return []


def get_today_prefix() -> str:
    return now_jakarta().date().isoformat()


def get_used_unique_codes_today() -> set[int]:
    if c is None:
        return set()

    try:
        prefix = get_today_prefix()
        c.execute(
            """
            SELECT unique_code
            FROM qris_deposits
            WHERE created_at LIKE ?
              AND unique_code IS NOT NULL
              AND unique_code > 0
            """,
            (f"{prefix}%",),
        )
        rows = c.fetchall()
        return {int(row[0]) for row in rows if row and row[0] is not None}
    except Exception as e:
        print(f"[QRIS] Failed to load today's unique codes: {e}")
        return set()


def get_used_invoice_totals_today() -> set[int]:
    if c is None:
        return set()

    try:
        prefix = get_today_prefix()
        c.execute(
            """
            SELECT amount_rupiah, unique_code, total_rupiah
            FROM qris_deposits
            WHERE created_at LIKE ?
              AND status IN ('pending', 'completed')
            """,
            (f"{prefix}%",),
        )
        totals: set[int] = set()
        for amount_rupiah, unique_code, total_rupiah in c.fetchall():
            totals.add(compute_total_rupiah(int(amount_rupiah or 0), int(unique_code or 0), total_rupiah))
        return totals
    except Exception as e:
        print(f"[QRIS] Failed to load today's invoice totals: {e}")
        return set()


def generate_unique_code(amount_rupiah: int) -> int:
    used_codes = get_used_unique_codes_today()
    used_totals = get_used_invoice_totals_today()
    code = 1
    while code in used_codes or (int(amount_rupiah) + code) in used_totals:
        code += 1
    return code


def compute_total_rupiah(amount_rupiah: int, unique_code: int, total_rupiah: int | None = None) -> int:
    if total_rupiah and int(total_rupiah) > 0:
        return int(total_rupiah)
    return int(amount_rupiah) + int(unique_code)


def crc16_ccitt(data: str) -> str:
    crc = 0xFFFF
    for char in data.encode('ascii'):
        crc ^= (char << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def parse_qris_tlv(qris_str: str) -> dict[str, str]:
    tags = {}
    i = 0
    while i < len(qris_str):
        if i + 4 > len(qris_str):
            break
        tag = qris_str[i:i+2]
        length_str = qris_str[i+2:i+4]
        try:
            length = int(length_str)
        except ValueError:
            break
            
        val = qris_str[i+4:i+4+length]
        tags[tag] = val
        i += 4 + length
    return tags


def make_dynamic_qris(static_qris: str, amount: int) -> str:
    tags = parse_qris_tlv(static_qris)
    tags['01'] = '12'  # Change Point of Initiation Method to dynamic
    tags['54'] = str(amount)  # Inject amount
    
    payload = ""
    for tag in sorted(tags.keys()):
        if tag == '63':
            continue
        val = tags[tag]
        payload += f"{tag}{len(val):02d}{val}"
        
    payload += "6304"
    crc = crc16_ccitt(payload)
    return payload + crc


def resolve_static_qr_image() -> discord.File | None:
    if GOPAY_QRIS_STATIC_IMAGE_PATH:
        path = Path(GOPAY_QRIS_STATIC_IMAGE_PATH)
        if path.is_file():
            return discord.File(str(path), filename="qris.png")

    return None


def get_merchant_headers(token: str | None = None) -> dict[str, str]:
    active_token = (token or get_active_merchant_token()).strip()
    return {
        "Authorization": f"Bearer {active_token}",
        "Accept": "application/json",
    }


async def fetch_merchant_transactions(
    start_time: datetime,
    end_time: datetime,
    token_override: str | None = None,
) -> list[dict]:
    """
    Fetch merchant transactions from GoPay Merchant Analytics.
    The response shape can vary, so we flatten it defensively.
    """
    if token_override is None:
        await ensure_gopay_authenticated()

    active_token = (token_override or get_active_merchant_token()).strip()
    active_merchant_id = get_active_merchant_id()
    if not active_token or not active_merchant_id:
        print("[QRIS] GOPAY_MERCHANT_TOKEN or GOPAY_MERCHANT_ID not configured")
        return []

    url = f"{GOPAY_MERCHANT_BASE_URL}/merchant-analytics/v2/merchants/transactions"
    params = {
        "from": 0,
        "size": GOPAY_TRANSACTION_FETCH_SIZE,
        "statuses": GOPAY_TRANSACTION_QUERY_STATUSES,
        "payment_types": GOPAY_TRANSACTION_PAYMENT_TYPES,
        "start_time": to_utc_iso(start_time),
        "end_time": to_utc_iso(end_time),
        "merchant_ids": active_merchant_id,
    }

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params, headers=get_merchant_headers(active_token)) as resp:
                text = await resp.text()
                if resp.status == 401 and token_override is None:
                    print("[QRIS] Merchant token expired on analytics request, login ulang...")
                    if await ensure_gopay_authenticated(force_login=True):
                        params["merchant_ids"] = get_active_merchant_id()
                        async with session.get(
                            url,
                            params=params,
                            headers=get_merchant_headers(get_active_merchant_token()),
                        ) as retry_resp:
                            retry_text = await retry_resp.text()
                            if retry_resp.status != 200:
                                print(f"[QRIS] Merchant API Retry Error: {retry_resp.status} - {retry_text[:500]}")
                                return []
                            try:
                                retry_data = await retry_resp.json(content_type=None)
                            except Exception:
                                import json

                                retry_data = json.loads(retry_text)
                            return extract_transactions(retry_data)
                    return []

                if resp.status != 200:
                    print(f"[QRIS] Merchant API Error: {resp.status} - {text[:500]}")
                    return []

                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    import json

                    data = json.loads(text)
                return extract_transactions(data)
    except Exception as e:
        print(f"[QRIS] Merchant request error: {e}")
        return []


def find_matching_transaction(
    transactions: list[dict],
    expected_total_rupiah: int,
    created_after: datetime | None = None,
) -> dict | None:
    for tx in transactions:
        status = normalize_status(
            tx.get("status")
            or tx.get("transaction_status")
            or tx.get("payment_status")
        )
        if status not in SUCCESS_TRANSACTION_STATUSES:
            continue

        amount = normalize_merchant_amount(
            tx.get("amount")
            or tx.get("gross_amount")
            or tx.get("value")
            or tx.get("transaction_amount")
        )
        if amount is None or amount != int(expected_total_rupiah):
            continue

        merchant_id = normalize_text(
            tx.get("merchant_id")
            or tx.get("merchantId")
            or tx.get("merchantID")
        )
        active_merchant_id = get_active_merchant_id()
        if active_merchant_id and merchant_id and merchant_id != active_merchant_id:
            continue

        merchant_tx_id = get_transaction_id(tx)
        if merchant_transaction_already_used(merchant_tx_id):
            continue

        if created_after:
            tx_dt = get_transaction_datetime(tx)
            if not tx_dt:
                continue
            if tx_dt < created_after - timedelta(seconds=3):
                continue

        return tx
    return None


async def mark_deposit_expired(dep_id: int, order_id: str, user_id: int) -> None:
    c.execute("UPDATE qris_deposits SET status = 'expired' WHERE id = ?", (dep_id,))
    conn.commit()
    print(f"[QRIS] Deposit {order_id} expired")

    try:
        user = await bot.fetch_user(user_id)
        await user.send(
            f"Deposit QRIS `{order_id}` telah expired. Silakan buat deposit baru."
        )
    except Exception:
        pass


async def complete_deposit(
    dep_id: int,
    order_id: str,
    user_id: int,
    total_rupiah: int,
    amount_wl: int,
    tx: dict,
) -> None:
    merchant_tx_id = get_transaction_id(tx)
    merchant_status = normalize_status(
        tx.get("status")
        or tx.get("transaction_status")
        or tx.get("payment_status")
    )
    payment_type = normalize_text(
        tx.get("payment_type")
        or tx.get("paymentType")
        or tx.get("type")
    )

    c.execute(
        """
        UPDATE qris_deposits
        SET status = 'completed',
            completed_at = ?,
            merchant_tx_id = ?,
            merchant_status = ?,
            payment_type = ?,
            last_checked_at = ?
        WHERE id = ?
        """,
        (local_iso(), merchant_tx_id, merchant_status, payment_type, local_iso(), dep_id),
    )

    c.execute("SELECT balance, nama FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row:
        new_balance = (row[0] or 0) + amount_wl
        growid = row[1] or "Unknown"
        c.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
        conn.commit()

        print(f"[QRIS] Deposit {order_id} completed! User {user_id} +{amount_wl} WL")

        try:
            user = await bot.fetch_user(user_id)
            embed = discord.Embed(
                title="Deposit QRIS Berhasil",
                color=discord.Color.green(),
            )
            embed.add_field(name="Order ID", value=f"`{order_id}`", inline=False)
            embed.add_field(name="Jumlah Bayar", value=f"Rp {total_rupiah:,}".replace(",", "."), inline=True)
            embed.add_field(name="WL Diterima", value=f"+{fmt_wl(amount_wl)} WL", inline=True)
            embed.add_field(name="Saldo Sekarang", value=f"{fmt_wl(new_balance)} WL", inline=False)
            await user.send(embed=embed)
        except Exception as e:
            print(f"[QRIS] Failed to notify user {user_id}: {e}")

        if CHANNEL_QRIS_SUCCESS_LOG:
            try:
                log_channel = bot.get_channel(CHANNEL_QRIS_SUCCESS_LOG)
                if log_channel:
                    log_embed = discord.Embed(
                        title="Transaksi QRIS Berhasil",
                        color=discord.Color.green(),
                        timestamp=now_jakarta(),
                    )
                    log_embed.add_field(name="Order ID", value=f"`{order_id}`", inline=False)
                    log_embed.add_field(name="User", value=f"<@{user_id}>", inline=True)
                    log_embed.add_field(name="GrowID", value=mask_growid(growid), inline=True)
                    log_embed.add_field(name="Total Bayar", value=f"Rp {total_rupiah:,}".replace(",", "."), inline=True)
                    log_embed.add_field(name="WL Diterima", value=f"{fmt_wl(amount_wl)} WL", inline=True)
                    log_embed.add_field(
                        name="Konversi",
                        value=(
                            f"```Rp {total_rupiah:,} -> {fmt_wl(amount_wl)} WL\n"
                            f"(Rate: {format_rate_100_wl()})```"
                        ).replace(",", "."),
                        inline=False,
                    )
                    if merchant_tx_id:
                        log_embed.add_field(name="Merchant Tx", value=f"`{merchant_tx_id}`", inline=False)
                    log_embed.set_footer(text="QRIS Deposit System")
                    await log_channel.send(embed=log_embed)
            except Exception as e:
                print(f"[QRIS] Failed to send log to channel: {e}")
    else:
        conn.commit()


@tasks.loop(seconds=QRIS_POLL_INTERVAL_SECONDS)
async def monitor_pending_deposits():
    """Check pending deposits every few seconds until timeout."""
    if not c or not conn or not bot:
        return

    try:
        c.execute(
            """
            SELECT id, order_id, user_id, amount_rupiah, unique_code, total_rupiah, amount_wl, expired_at, created_at
            FROM qris_deposits
            WHERE status = 'pending'
            """
        )
        pending = c.fetchall()
        if not pending:
            return

        now_local = now_jakarta()
        cutoff = now_local - timedelta(seconds=QRIS_TIMEOUT_SECONDS)

        # Fetch once per loop, then match all pending deposits.
        tx_start = cutoff - timedelta(seconds=30)
        tx_end = now_local
        transactions = await fetch_merchant_transactions(tx_start, tx_end)

        for deposit in pending:
            dep_id, order_id, user_id, amount_rupiah, unique_code, total_rupiah, amount_wl, expired_at, created_at = deposit
            amount_rupiah = int(amount_rupiah or 0)
            unique_code = int(unique_code or 0)
            total_rupiah = compute_total_rupiah(amount_rupiah, unique_code, total_rupiah)
            amount_wl = int(amount_wl or 0)

            created_dt = parse_iso_datetime(created_at) or now_local
            expiry_dt = parse_iso_datetime(expired_at) if expired_at else created_dt + timedelta(seconds=QRIS_TIMEOUT_SECONDS)

            if expiry_dt is None:
                expiry_dt = created_dt + timedelta(seconds=QRIS_TIMEOUT_SECONDS)

            if now_local >= expiry_dt:
                await mark_deposit_expired(dep_id, order_id, user_id)
                continue

            match_tx = find_matching_transaction(transactions, total_rupiah, created_after=created_dt)
            c.execute(
                "UPDATE qris_deposits SET last_checked_at = ? WHERE id = ?",
                (local_iso(), dep_id),
            )

            if match_tx:
                await complete_deposit(
                    dep_id=dep_id,
                    order_id=order_id,
                    user_id=user_id,
                    total_rupiah=total_rupiah,
                    amount_wl=amount_wl,
                    tx=match_tx,
                )
            else:
                conn.commit()

    except Exception as e:
        print(f"[QRIS] Monitor Error: {e}")


@tasks.loop(seconds=GOPAY_TOKEN_WATCH_INTERVAL_SECONDS)
async def monitor_gopay_token():
    """Validate GoBiz token periodically and refresh it when invalid."""
    if not c or not conn:
        return

    try:
        ok = await ensure_gopay_authenticated()
        if ok:
            print("[GoBizAuth] Token watcher OK")
        else:
            print("[GoBizAuth] Token watcher gagal memastikan token valid")
    except Exception as e:
        print(f"[GoBizAuth] Token watcher error: {e}")


async def process_qris_deposit(interaction: discord.Interaction, wl_amount: int) -> bool:
    """
    Legacy path (WL input) disabled.
    Flow aktif sekarang: input Rupiah -> convert ke WL.
    """
    _ = wl_amount
    message = "Input deposit via WL tidak dipakai. Gunakan input Rupiah (akan otomatis convert ke WL)."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
    return False


def get_active_pending_deposit(user_id: int) -> tuple | None:
    """Return the user's newest unexpired pending invoice and expire stale rows."""
    now_local = now_jakarta()
    stale_ids: list[int] = []

    c.execute(
        """
        SELECT id, order_id, total_rupiah, created_at, expired_at
        FROM qris_deposits
        WHERE user_id = ? AND status = 'pending'
        ORDER BY id DESC
        """,
        (user_id,),
    )
    rows = c.fetchall()

    for dep_id, order_id, total_rupiah, created_at, expired_at in rows:
        expiry_dt = parse_iso_datetime(expired_at) if expired_at else None
        if not expiry_dt:
            created_dt = parse_iso_datetime(created_at) or now_local
            expiry_dt = created_dt + timedelta(seconds=QRIS_TIMEOUT_SECONDS)

        if now_local >= expiry_dt:
            stale_ids.append(dep_id)
            continue

        for stale_id in stale_ids:
            c.execute(
                "UPDATE qris_deposits SET status = 'expired' WHERE id = ? AND status = 'pending'",
                (stale_id,),
            )
        if stale_ids:
            conn.commit()
        return dep_id, order_id, int(total_rupiah or 0), expiry_dt

    for stale_id in stale_ids:
        c.execute(
            "UPDATE qris_deposits SET status = 'expired' WHERE id = ? AND status = 'pending'",
            (stale_id,),
        )
    if stale_ids:
        conn.commit()

    return None


async def process_qris_deposit_rupiah(interaction: discord.Interaction, rupiah_amount: int) -> bool:
    """
    Process QRIS deposit request with Rupiah input.
    Adds a 2-digit unique code to the invoice and monitors merchant transactions.
    """
    user = interaction.user
    user_id = user.id

    if rupiah_amount < MIN_DEPOSIT_RUPIAH:
        await interaction.followup.send(
            f"Minimal deposit Rp {MIN_DEPOSIT_RUPIAH}.",
            ephemeral=True,
        )
        return False

    active_deposit = get_active_pending_deposit(user_id)
    if active_deposit:
        _, active_order_id, active_total, active_expiry = active_deposit
        remaining = max(0, int((active_expiry - now_jakarta()).total_seconds()))
        minutes, seconds = divmod(remaining, 60)
        await interaction.followup.send(
            (
                "Kamu masih punya invoice QRIS yang belum selesai.\n"
                f"Order ID: `{active_order_id}`\n"
                f"Total bayar: **Rp {active_total:,}**\n"
                f"Sisa waktu: **{minutes} menit {seconds} detik**\n"
                "Bayar invoice lama dulu, atau tunggu sampai expired baru buat invoice lagi."
            ).replace(",", "."),
            ephemeral=True,
        )
        return False

    if not await ensure_gopay_authenticated():
        await interaction.followup.send(
            "Sistem QRIS belum siap. Silakan hubungi admin atau coba lagi nanti.",
            ephemeral=True,
        )
        return False

    c.execute("SELECT nama FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if not row:
        await interaction.followup.send(
            "Kamu belum register. Klik **SET GROWID** dulu.",
            ephemeral=True,
        )
        return False

    growid = row[0]
    async with _deposit_create_lock:
        active_deposit = get_active_pending_deposit(user_id)
        if active_deposit:
            _, active_order_id, active_total, active_expiry = active_deposit
            remaining = max(0, int((active_expiry - now_jakarta()).total_seconds()))
            minutes, seconds = divmod(remaining, 60)
            await interaction.followup.send(
                (
                    "Kamu masih punya invoice QRIS yang belum selesai.\n"
                    f"Order ID: `{active_order_id}`\n"
                    f"Total bayar: **Rp {active_total:,}**\n"
                    f"Sisa waktu: **{minutes} menit {seconds} detik**\n"
                    "Bayar invoice lama dulu, atau tunggu sampai expired baru buat invoice lagi."
                ).replace(",", "."),
                ephemeral=True,
            )
            return False

        created_at = local_iso()
        expired_at = local_iso(now_jakarta() + timedelta(seconds=QRIS_TIMEOUT_SECONDS))
        order_id = ""
        unique_code = 0
        total_rupiah = 0
        wl_amount = 0

        for _ in range(100):
            unique_code = generate_unique_code(rupiah_amount)
            total_rupiah = rupiah_amount + unique_code
            wl_amount = convert_rupiah_to_wl(total_rupiah)
            order_id = generate_order_id()

            try:
                c.execute(
                    """
                    INSERT INTO qris_deposits (
                        order_id,
                        user_id,
                        amount_rupiah,
                        unique_code,
                        total_rupiah,
                        amount_wl,
                        status,
                        expired_at,
                        created_at,
                        payment_method
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        order_id,
                        user_id,
                        rupiah_amount,
                        unique_code,
                        total_rupiah,
                        wl_amount,
                        expired_at,
                        created_at,
                        "gopay_merchant_qris",
                    ),
                )
                conn.commit()
                break
            except Exception as e:
                conn.rollback()
                print(f"[QRIS] Failed to reserve invoice total, retrying: {e}")
        else:
            await interaction.followup.send(
                "Sistem sedang ramai membuat invoice QRIS. Coba lagi beberapa detik.",
                ephemeral=True,
            )
            return False

    # Generate dynamic QRIS
    os.makedirs("QR", exist_ok=True)
    qr_path = Path("QR") / f"{order_id}.png"
    try:
        dynamic_qris = make_dynamic_qris(QRIS_STATIC_STRING, total_rupiah)
        
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(dynamic_qris)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(str(qr_path))
        
        qr_file = discord.File(str(qr_path), filename="qris.png")
    except Exception as e:
        print(f"[QRIS] Failed to generate dynamic QR image: {e}")
        c.execute(
            "UPDATE qris_deposits SET status = 'cancelled' WHERE order_id = ? AND status = 'pending'",
            (order_id,),
        )
        conn.commit()
        await interaction.followup.send(
            "Gagal memproses QRIS. Silakan hubungi admin.",
            ephemeral=True,
        )
        return False

    try:
        try:
            embed = discord.Embed(
                title="Invoice Deposit QRIS",
                color=discord.Color.blue(),
            )
            embed.add_field(name="Order ID", value=f"`{order_id}`", inline=False)
            embed.add_field(name="GrowID", value=growid, inline=True)
            embed.add_field(name="Nominal", value=f"Rp {rupiah_amount:,}".replace(",", "."), inline=True)
            embed.add_field(name="Kode Unik", value=f"Rp {unique_code:,}".replace(",", "."), inline=True)
            embed.add_field(name="Total Bayar", value=f"**Rp {total_rupiah:,}**".replace(",", "."), inline=False)
            embed.add_field(name="WL yang Didapat", value=f"{fmt_wl(wl_amount)} WL", inline=True)
            embed.add_field(name="Expired", value=expired_at[:19].replace("T", " "), inline=True)
            embed.add_field(name="Metode", value="QRIS", inline=False)
            embed.add_field(
                name="PERINGATAN PENTING",
                value=(
                    "**WAJIB TRANSFER SESUAI TOTAL BAYAR PERSIS.**\n"
                    f"Kalau deposit Rp {rupiah_amount:,} dan kode unik Rp {unique_code:,}, "
                    f"maka yang harus dikirim adalah **Rp {total_rupiah:,}**.\n"
                    "Saldo dihitung dari total transfer ini, jadi kode unik tetap ikut masuk ke saldo.\n"
                    "Jangan kirim nominal dasar saja, karena saldo hanya masuk jika jumlah transfer sama persis."
                ).replace(",", "."),
                inline=False,
            )
            embed.set_image(url="attachment://qris.png")
            embed.set_footer(
                text="Scan QR lalu transfer nominal persis sesuai total bayar agar auto-detect."
            )

            await user.send(embed=embed, file=qr_file)
        except discord.Forbidden:
            c.execute(
                "UPDATE qris_deposits SET status = 'cancelled' WHERE order_id = ? AND status = 'pending'",
                (order_id,),
            )
            conn.commit()
            await interaction.followup.send(
                "DM kamu tidak aktif. Deposit dibatalkan.\nAktifkan DM dari server ini lalu coba lagi.",
                ephemeral=True,
            )
            return False
        except Exception as e:
            print(f"[QRIS] DM Error: {e}")
            c.execute(
                "UPDATE qris_deposits SET status = 'cancelled' WHERE order_id = ? AND status = 'pending'",
                (order_id,),
            )
            conn.commit()
            await interaction.followup.send(
                "Gagal mengirim DM. Deposit dibatalkan.",
                ephemeral=True,
            )
            return False

        await interaction.followup.send(
            (
                "INVOICE QRIS SUDAH DI KIRIM DI DM.\n"
                "**PERINGATAN:** yang ditransfer harus **sama persis** dengan total bayar di invoice.\n"
                f"deposit Rp {rupiah_amount:,} + kode unik Rp {unique_code:,} = total **Rp {total_rupiah:,}**.\n"
                f"Saldo kamu akan dihitung dari **Rp {total_rupiah:,}** itu, jadi kode unik tetap ikut masuk ke saldo.\n"
                f"Target WL: {fmt_wl(wl_amount)} WL"
            ).replace(",", "."),
            ephemeral=True,
        )
        return True
    finally:
        if qr_path.is_file():
            try:
                qr_path.unlink()
            except Exception:
                pass


def setup(_bot, _c, _conn, _fmt_wl, _PREFIX):
    global bot, c, conn, fmt_wl, PREFIX
    bot = _bot
    c = _c
    conn = _conn
    fmt_wl = _fmt_wl
    PREFIX = _PREFIX

    ensure_qris_deposits_schema(c, conn)
    ensure_qris_rate_schema(c, conn)
    ensure_qris_token_schema(c, conn)

    @bot.listen("on_ready")
    async def start_qris_monitor():
        if not monitor_gopay_token.is_running():
            await ensure_gopay_authenticated()
            monitor_gopay_token.start()
            print("[GoBizAuth] Token watcher started")

        if not monitor_pending_deposits.is_running():
            monitor_pending_deposits.start()
            print("[QRIS] Deposit monitor started")
