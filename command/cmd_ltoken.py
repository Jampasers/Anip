# cmd_ltoken.py
# ======================================================================================
# FINAL PANJANG & LENGKAP (Siap Pakai)
# - HARGA JUAL FIX 27 WL (bisa override via env LTOKEN_SELL_PRICE_WL)
# - Auto-Role & Auto-Testimoni menggunakan ID HARDCODED LOKAL (sesuai permintaan user)
# - Background monitor:
#     * loop cek /getOrder real-time
#     * kalau Success + accounts -> DM buyer & mark SUCCESS (KIRIM FILE .TXT)
#     * kalau Failed -> rollback saldo & mark FAILED
# ======================================================================================

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import View, Select, Modal, TextInput
from utils import is_allowed_user
import aiohttp
import asyncio
import datetime
import os
from typing import Any, Dict, List, Optional, Tuple
from io import BytesIO
from datetime import datetime as dt

# ----------------------------------
# KONFIG (GLOBAL)
# ----------------------------------
API_KEY: str = ""
BASE_URL: str = "https://cid.surferwallet.net/publicApi"  # Default
SELL_PRICE_WL: int = 27  # harga jual fix (Updated)
MONITOR_INTERVAL_SEC: int = 5  # interval monitor pending
GUILD_ID_ENV = "SERVER_ID"  # untuk @app_commands.guilds

# --- [PERBAIKAN] DUA BARIS PENYEBAB CRASH GLOBAL DIHAPUS ---
# role = interaction.guild.get_role(839981629044555853)
# channel = interaction.client.get_channel(839981637567643668)
# -----------------------------------------------------------------

# Ambil override dari ENV bila ada
try:
    SELL_PRICE_WL = int(os.getenv("LTOKEN_SELL_PRICE_WL", SELL_PRICE_WL))
except Exception:
    pass
try:
    MONITOR_INTERVAL_SEC = int(os.getenv("LTOKEN_MONITOR_INTERVAL_SEC", MONITOR_INTERVAL_SEC))
except Exception:
    pass

# Cooldown khusus tombol BUY
BUY_COOLDOWN_SEC: int = int(os.getenv("LTOKEN_BUY_COOLDOWN_SEC", "10"))

# --- [TIDAK DIGUNAKAN LAGI UNTUK AUTOTESTIMONI & ROLE KARENA MENGGUNAKAN HARDCODED ID] ---
# ROLE_BUYLTOKEN_ID: Optional[int] = None
# TESTIMONI_CHANNEL_ID: Optional[int] = None
# try:
#     _rid = os.getenv("LTOKEN_ROLE_ID")
#     ROLE_BUYLTOKEN_ID = int(_rid) if _rid else None
# except Exception:
#     ROLE_BUYLTOKEN_ID = None
# try:
#     _cid = os.getenv("LTOKEN_TESTIMONI_CHANNEL_ID")
#     TESTIMONI_CHANNEL_ID = int(_cid) if _cid else None
# except Exception:
#     TESTIMONI_CHANNEL_ID = None
# -------------------------------------------------------------------------------------------

# ----------------------------------
# HARDCODED ID LOKAL (Sesuai Permintaan)
# ----------------------------------
# ID Role Pembeli Anda: 839981629044555853
LOCAL_ROLE_ID = 839981629044555853
# ID Channel Testimoni Anda: 839981637567643668
LOCAL_TESTIMONI_CHANNEL_ID = 839981637567643668


# ----------------------------------
# EMOJI (sesuai ui_views)
# ----------------------------------
EMO_EXCLAMATION = "<a:exclamation:1419518587072282654>"
EMO_TOA        = "<a:toa:1122531485090582619>"
EMO_PANAH      = "<a:panah1:1419515217892606053>"
EMO_WL         = "<a:world_lock:1419515667773657109>"

# ----------------------------------
# LOGGING SEDERHANA
# ----------------------------------
def log_debug(msg: str) -> None:
    print(f"[DEBUG][ltoken] {msg}")


# ======================================================================================
# DB SCHEMA ENSURE
# ======================================================================================
def ensure_schema(c, conn) -> None:
    """Pastikan tabel yang diperlukan ada."""
    log_debug("[DB] Ensuring required tables exist...")
    # Tabel users
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            nama TEXT,
            balance INTEGER DEFAULT 0
        )
        """
    )
    # Tabel deposit
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS deposit (
            world TEXT,
            bot TEXT
        )
        """
    )
    # orders (riwayat ringkas)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            user_id INTEGER,
            product_name TEXT,
            qty INTEGER,
            unit_price INTEGER,
            total INTEGER,
            status TEXT,
            created_at TIMESTAMP
        )
        """
    )
    # pending_orders (untuk monitoring sampai final)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_orders (
            order_id TEXT PRIMARY KEY,
            user_id INTEGER,
            product_name TEXT,
            qty INTEGER,
            total INTEGER,
            balance_before INTEGER,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Tabel khusus ltoken orders (arsip sukses)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS ltoken_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            user_id INTEGER,
            product_name TEXT,
            qty INTEGER,
            total INTEGER,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


# ======================================================================================
# HTTP HELPERS
# ======================================================================================
async def http_get_json(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Any:
    """GET -> JSON (bisa pakai params atau JSON body jika server nerima)."""
    log_debug(f"[HTTP][GET] {url} params={params} body={json_body}")
    async with aiohttp.ClientSession() as session:
        try:
            # Wajib ada API_KEY
            if not API_KEY and "apikey" not in (params or {}):
                log_debug("[HTTP] Request skipped: API_KEY is empty.")
                return {"error": "apikey is required"}

            async with session.get(
                url, params=params, json=json_body, headers=headers, timeout=15
            ) as resp:
                status = resp.status
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    text = await resp.text()
                    log_debug(f"[HTTP][GET][{status}] Non-JSON: {text[:500]}")
                    return {}
                log_debug(f"[HTTP][GET][{status}] {str(data)[:900]}")
                return data
        except aiohttp.ClientConnectorError as e:
            log_debug(f"[HTTP][GET] Connection Error: {e}")
            raise e


async def http_post_json(
    url: str, *, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None
) -> Any:
    """POST -> JSON"""
    log_debug(f"[HTTP][POST] {url} payload={payload}")
    async with aiohttp.ClientSession() as session:
        try:
            if not API_KEY and "apikey" not in (payload or {}):
                log_debug("[HTTP] Request skipped: API_KEY is empty.")
                return {"error": "apikey is required"}

            async with session.post(
                url, json=payload, headers=headers, timeout=20
            ) as resp:
                status = resp.status
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    text = await resp.text()
                    log_debug(f"[HTTP][POST][{status}] Non-JSON: {text[:500]}")
                    return {}
                log_debug(f"[HTTP][POST][{status}] {str(data)[:900]}")
                return data
        except aiohttp.ClientConnectorError as e:
            log_debug(f"[HTTP][POST] Connection Error: {e}")
            raise e


# ======================================================================================
# API WRAPPERS
# ======================================================================================
async def api_get_products() -> Dict[str, Any]:
    """GET /stock -> { products: [...], tasks: [...] }"""
    url = f"{BASE_URL}/stock"
    return await http_get_json(url, params={"apikey": API_KEY})


async def api_get_balance_web() -> int:
    """GET /getBalance -> { balance: int }"""
    url = f"{BASE_URL}/getBalance"
    data = await http_get_json(url, params={"apikey": API_KEY})
    if isinstance(data, dict):
        try:
            return int(float(data.get("balance", 0)))
        except Exception:
            return 0
    return 0


async def api_purchase(product_name: str, qty: int) -> Dict[str, Any]:
    """POST /purchase -> buat order baru (task)"""
    url = f"{BASE_URL}/purchase"
    payload = {"apikey": API_KEY, "name": product_name, "quantity": qty}
    return await http_post_json(url, payload=payload)


async def api_get_order(order_id: str) -> Dict[str, Any]:
    """Cek status order by ID. Urutan: GET query -> POST+body -> GET+body (fallback)."""
    url = f"{BASE_URL}/getOrder"

    # 1) GET query
    data = await http_get_json(url, params={"apikey": API_KEY, "orderID": order_id})
    if isinstance(data, dict) and data:
        if (
            ("status" in data)
            or ("accounts" in data)
            or ("success" in data)
            or ("processing" in data)
        ):
            return data

    # 2) POST body (reliable)
    data = await http_post_json(
        url,
        payload={"apikey": API_KEY, "orderID": order_id},
        headers={"Content-Type": "application/json"},
    )
    if isinstance(data, dict):
        return data

    # 3) GET + JSON body (rare)
    data = await http_get_json(
        url,
        json_body={"apikey": API_KEY, "orderID": order_id},
        headers={"Content-Type": "application/json"},
    )
    if isinstance(data, dict) and data:
        if (
            ("status" in data)
            or ("accounts" in data)
            or ("success" in data)
            or ("processing" in data)
        ):
            return data

    return {}


async def api_get_orders() -> Any:
    """GET /getOrders -> list of orders (fallback untuk pencocokan)"""
    url = f"{BASE_URL}/getOrders"
    return await http_get_json(url, params={"apikey": API_KEY})


# ======================================================================================
# UTIL
# ======================================================================================
def safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(float(x))
    except Exception:
        return default


def now_str() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def format_single_account_to_string(acc: Dict[str, Any]) -> str:
    """Format satu akun ke string login pipe-separated."""
    data = {
        "mac": acc.get("mac", "02:00:00:00:00:00"),
        "wk": acc.get("wk", "NONE0"),
        "platform": acc.get("platform", "1"),
        "rid": acc.get("rid", "N/A"),
        "name": acc.get("name") or acc.get("growid", "N/A"),
        "cbits": acc.get("cbits", "1536"),
        "playerAge": acc.get("playerAge", "25"),
        "token": acc.get("token") or acc.get("ltoken", "N/A"),
        "vid": acc.get("vid", "N/A"),
    }
    parts = []
    for key in ["mac", "wk", "platform", "rid", "name", "cbits", "playerAge", "token", "vid"]:
        parts.append(f"{key}:{data.get(key, 'N/A')}")
    return "|".join(parts)


def format_accounts_dm(
    product_name: str, qty: int, total: int, accounts: List[Dict[str, Any]]
) -> Tuple[str, discord.File]:
    """
    Gabungkan akun ke satu file dan siapkan pesan DM.
    Returns: (text_message, discord.File)
    """
    formatted_accounts = [format_single_account_to_string(acc) for acc in accounts]
    file_content = "\n".join(formatted_accounts)

    file_name = f"LToken_Order_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    file_buffer = BytesIO(file_content.encode("utf-8"))
    discord_file = discord.File(file_buffer, filename=file_name)

    text_message = (
        f"✅ **LToken Order Ready!**\n"
        f"📦 **Product** : {product_name}\n"
        f"🔢 **Quantity**: {qty}\n"
        f"💰 **Total** : {qty * SELL_PRICE_WL if total is None else total} {EMO_WL}\n\n"
        f"{EMO_PANAH} File **`{file_name}`** berisi {qty} akun telah dikirim. Gunakan data di dalamnya untuk login.\n"
        "⚠️ Simpan data akun Anda dengan aman."
    )
    return text_message, discord_file


# ======================================================================================
# RENDER EMBED
# ======================================================================================
def render_stock_embed(
    products: List[Dict[str, Any]], balance_web: int, fmt_wl
) -> discord.Embed:
    title = f"{EMO_EXCLAMATION} LTOKEN PRODUCT LIST {EMO_EXCLAMATION}"
    embed = discord.Embed(title=title, color=discord.Color.red())
    embed.set_footer(text=f" Last Update: {now_str()}")

    blocks: List[str] = []
    for p in products:
        name = p.get("name", "Unknown")
        if "Old Account" in name:
            log_debug(f"[RENDER] Skip 'Old Account': {name}")
            continue
        instock = safe_int(p.get("instock", 0))
        stock_can_buy = balance_web // SELL_PRICE_WL if SELL_PRICE_WL > 0 else instock
        stock_real = min(instock, stock_can_buy)

        block = (
            f"{EMO_TOA}  {name}\n"
            f"{EMO_PANAH}  Price: {SELL_PRICE_WL} {EMO_WL}\n"
            f"{EMO_PANAH}  Your Available Stock: {fmt_wl(stock_real)}\n"
        )
        blocks.append(block)

    embed.description = (
        "\n========================================\n".join(blocks)
        if blocks
        else "❌ Belum ada produk dari web."
    )
    return embed


# ======================================================================================
# UI: Select + Modal + Views
# ======================================================================================
class ProductSelect(Select):
    def __init__(self, products: List[Dict[str, Any]], c, conn):
        options = []
        for p in products:
            name = p.get("name", "Unknown")
            if "Old Account" in name:
                continue
            options.append(
                discord.SelectOption(label=name, description=f"Harga {SELL_PRICE_WL} WL")
            )
        super().__init__(
            placeholder="Pilih produk...",
            min_values=1,
            max_values=1,
            options=options,
            disabled=(len(options) == 0),
        )
        self.c = c
        self.conn = conn

    async def callback(self, interaction: discord.Interaction):
        product_name = self.values[0]
        await interaction.response.send_modal(
            BuyLTokenModal(self.c, self.conn, product_name)
        )


class BuyLTokenModal(Modal, title="🛒 Buy LToken"):
    # Lock agar user tidak double purchase bersamaan
    active_purchases = set()

    def __init__(self, c, conn, product_name: str):
        super().__init__()
        self.c = c
        self.conn = conn
        self.product_name = product_name
        self.qty_input = TextInput(
            label=f"Amount ({product_name})", placeholder="1", required=True
        )
        self.add_item(self.qty_input)

    async def on_submit(self, interaction: discord.Interaction):
        uid = interaction.user.id

        # Defer dulu supaya interaction hidup & bebas 3s rule
        await interaction.response.defer(ephemeral=True)

        # Lock user
        if uid in BuyLTokenModal.active_purchases:
            await interaction.followup.send(
                "⚠️ Kamu masih punya transaksi berjalan. Tunggu beberapa detik sebelum membeli lagi.",
                ephemeral=True,
            )
            return
        BuyLTokenModal.active_purchases.add(uid)

        try:
            # 1) Validasi qty
            try:
                qty = int(str(self.qty_input.value).strip())
                if qty <= 0:
                    raise ValueError
            except Exception:
                await interaction.followup.send("❌ Invalid amount.", ephemeral=True)
                return

            # 2) Ambil balance user dari DB
            self.c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            row = self.c.fetchone()
            if not row:
                await interaction.followup.send(
                    "❌ Kamu belum register.", ephemeral=True
                )
                return
            balance_db = int(row[0] or 0)

            # 3) Hitung total harga fix
            total = qty * SELL_PRICE_WL
            if balance_db < total:
                await interaction.followup.send(
                    "❌ Purchase failed: Your central balance is insufficient to complete this order.",
                    ephemeral=True,
                )
                return

            # 4) Potong saldo sementara
            new_balance = balance_db - total
            self.c.execute(
                "UPDATE users SET balance=? WHERE user_id=?", (new_balance, uid)
            )
            self.conn.commit()
            log_debug(
                f"[ORDER] Balance cut user={uid} old={balance_db} new={new_balance} total={total}"
            )

            # 5) Purchase
            try:
                result = await api_purchase(self.product_name, qty)
            except aiohttp.ClientConnectorError:
                # Rollback
                self.c.execute(
                    "UPDATE users SET balance=? WHERE user_id=?", (balance_db, uid)
                )
                self.conn.commit()
                log_debug(
                    f"[ORDER] Purchase failed due to connection error. Rollback user={uid}"
                )
                await interaction.followup.send(
                    "❌ Purchase failed: Could not connect to API server. Please try again later.",
                    ephemeral=True,
                )
                return
            except Exception as e:
                self.c.execute(
                    "UPDATE users SET balance=? WHERE user_id=?", (balance_db, uid)
                )
                self.conn.commit()
                log_debug(
                    f"[ORDER] Purchase failed due to unknown API error: {e}. Rollback user={uid}"
                )
                await interaction.followup.send(
                    f"❌ Purchase failed: Unknown API error ({e.__class__.__name__}). Rollback.",
                    ephemeral=True,
                )
                return

            log_debug(f"[PURCHASE] resp={result}")

            order_id = str(result.get("orderID") or result.get("orderId") or "").strip()
            accounts = result.get("accounts", []) or result.get("accounts:", []) or []
            order_date = result.get("orderDate")
            processing_flag = bool(result.get("processing", False))
            status_text = str(result.get("status", "")).lower()

            # 6) Jika langsung ada akun -> sukses + DM (KIRIM FILE)
            if result.get("success") and accounts:
                # catat order selesai
                self.c.execute(
                    """
                    INSERT OR REPLACE INTO orders 
                    (order_id, user_id, product_name, qty, unit_price, total, status, created_at) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id or f"direct-{uid}-{datetime.datetime.now().timestamp()}",
                        uid,
                        self.product_name,
                        qty,
                        SELL_PRICE_WL,
                        total,
                        "Success",
                        datetime.datetime.now(),
                    ),
                )
                self.conn.commit()

                # Simpan ke tabel khusus
                self.c.execute(
                    """
                    INSERT INTO ltoken_orders (order_id, user_id, product_name, qty, total, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id or f"direct-{uid}-{datetime.datetime.now().timestamp()}",
                        uid,
                        self.product_name,
                        qty,
                        total,
                        "Success",
                    ),
                )
                self.conn.commit()

                
            # ✅ Auto-role & Auto-testimoni lokal (Menggunakan HARDCODED ID LOKAL)
            try:
                # --- 1️⃣ Pastikan order_id valid ---
                if not order_id:
                    self.c.execute(
                        "SELECT order_id FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
                        (uid,),
                    )
                    row = self.c.fetchone()
                    if row and row[0]:
                        order_id = row[0]
                    else:
                        order_id = f"local-{uid}-{int(datetime.datetime.now().timestamp())}"

                # --- 2️⃣ Tambahkan role pembeli (MENGGUNAKAN ID LOKAL) ---
                if interaction.guild:
                    role = interaction.guild.get_role(LOCAL_ROLE_ID)
                    if role:
                        await interaction.user.add_roles(role)
                        print(f"[DEBUG] Role 'Buy' diberikan ke {interaction.user}.")
                    else:
                        print(f"[WARN] Role 'Buy' ID={LOCAL_ROLE_ID} tidak ditemukan di server.")

                # --- 3️⃣ Kirim testimoni ke channel (MENGGUNAKAN ID LOKAL) ---
                channel = interaction.client.get_channel(LOCAL_TESTIMONI_CHANNEL_ID)
                if channel:
                    embed = discord.Embed(
                        title=f"#Order Number: {order_id}",
                        color=discord.Color.gold()
                    )
                    embed.add_field(
                        name="<a:megaphone:1419515391851626580> Pembeli",
                        value=interaction.user.mention,
                        inline=False
                    )
                    embed.add_field(
                        name="Produk <a:menkrep:1122531571098980394>",
                        value=f"{self.product_name} x{qty}",
                        inline=False
                    )
                    embed.add_field(
                        name="Total Price",
                        value=f"{total} {EMO_WL}",
                        inline=False
                    )
                    embed.set_footer(text="Thanks For Purchasing Our Product(s)")
                    await channel.send(embed=embed)
                    print(f"[DEBUG] Testimoni terkirim ke {channel.id} (order_id={order_id}).")
                else:
                    print("[WARN] Channel testimoni tidak ditemukan atau bot tidak punya izin.")

            except Exception as e:
                print(f"[ERROR] Gagal kirim testimoni/role: {e}")

            # DM akun (pakai file .txt)
            try:
                dm_msg, dm_file = format_accounts_dm(
                    self.product_name, qty, total, accounts
                )
                await interaction.user.send(dm_msg, file=dm_file)
            except discord.Forbidden:
                try:
                    await interaction.followup.send(
                        "⚠️ Gagal DM: Akun tidak terkirim. Mohon aktifkan DM Anda!",
                        ephemeral=True,
                    )
                except Exception:
                    pass

            # Balasan ke user (embed sukses)
            emb = discord.Embed(
                title="✅ Purchase Success!", color=discord.Color.green()
            )
            emb.add_field(name="Product", value=self.product_name, inline=False)
            emb.add_field(name="Quantity", value=str(qty), inline=True)
            emb.add_field(name="Total", value=f"{total} {EMO_WL}", inline=True)
            emb.add_field(
                name="Balance Baru", value=f"{new_balance} {EMO_WL}", inline=False
            )
            if order_id:
                emb.add_field(name="Order ID", value=order_id, inline=False)
            if order_date:
                emb.set_footer(text=f"Order Date: {order_date}")
            await interaction.followup.send(embed=emb, ephemeral=True)
            return

            # 7) Kalau masih processing (tidak ada accounts) -> simpan pending & tampil Processing
            if result.get("success") and (processing_flag or "processing" in status_text):
                if not order_id:
                    # Invalid; rollback
                    self.c.execute(
                        "UPDATE users SET balance=? WHERE user_id=?", (balance_db, uid)
                    )
                    self.conn.commit()
                    await interaction.followup.send(
                        "❌ Purchase failed: Invalid order state (No order ID from API).",
                        ephemeral=True,
                    )
                    return

                # Simpan pending
                self.c.execute(
                    """
                    INSERT OR REPLACE INTO pending_orders
                        (order_id, user_id, product_name, qty, total, balance_before, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (order_id, uid, self.product_name, qty, total, balance_db, "PENDING"),
                )
                self.c.execute(
                    """
                    INSERT OR REPLACE INTO orders 
                    (order_id, user_id, product_name, qty, unit_price, total, status, created_at) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        uid,
                        self.product_name,
                        qty,
                        SELL_PRICE_WL,
                        total,
                        "Processing",
                        datetime.datetime.now(),
                    ),
                )
                self.conn.commit()
                log_debug(f"[ORDER] Pending saved orderID={order_id} user={uid}")

                # embed Processing
                processing = discord.Embed(
                    title="⏳ Purchase Processing", color=discord.Color.orange()
                )
                processing.add_field(
                    name="Status",
                    value="Pembayaran diterima, sedang proses pembuatan token...",
                )
                processing.add_field(name="Product", value=self.product_name, inline=True)
                processing.add_field(name="Quantity", value=str(qty), inline=True)
                processing.add_field(name="Total", value=f"{total} {EMO_WL}", inline=True)
                processing.add_field(
                    name="Balance (pending)",
                    value=f"{new_balance} {EMO_WL}",
                    inline=False,
                )
                processing.add_field(name="Order ID", value=str(order_id), inline=False)
                if order_date:
                    processing.set_footer(text=f"Order Date: {order_date}")
                await interaction.followup.send(embed=processing, ephemeral=True)
                return

            # 8) Failure -> rollback
            err_msg = str(result.get("message", "Unknown error"))
            self.c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_db, uid))
            self.conn.commit()
            pretty = (
                "❌ Purchase failed: Your central balance is insufficient to complete this order."
                if "insufficient" in err_msg.lower()
                else f"❌ Purchase failed: {err_msg}"
            )
            await interaction.followup.send(pretty, ephemeral=True)
            log_debug(f"[ORDER] Failed; rollback user={uid} reason={err_msg}")

        finally:
            BuyLTokenModal.active_purchases.discard(uid)


# --- View pemilihan produk (dropdown) ---
class BuyFlowView(View):
    def __init__(self, products: List[Dict[str, Any]], c, conn):
        super().__init__(timeout=180)  # Timeout 3 minutes
        self.add_item(ProductSelect(products, c, conn))


# --- Cooldown manual untuk tombol Buy LToken ---
buy_cooldown: Dict[int, dt] = {}  # {user_id: datetime terakhir klik tombol Buy}

def is_on_cooldown(user_id: int, seconds: int = BUY_COOLDOWN_SEC) -> Tuple[bool, int]:
    now = dt.now()
    last = buy_cooldown.get(user_id)
    if not last:
        return False, 0
    elapsed = (now - last).total_seconds()
    remaining = max(0, seconds - int(elapsed))
    return elapsed < seconds, remaining


# --- MAIN VIEW ---
class StockView(View):
    def __init__(
        self, products: List[Dict[str, Any]], c, conn, requester_id: int, fmt_wl
    ):
        super().__init__(timeout=None)
        self.c = c
        self.conn = conn
        self.requester_id = requester_id
        self.fmt_wl = fmt_wl
        self.products = products

    # 🛒 BUY BUTTON (dengan cooldown khusus)
    @discord.ui.button(label="🛒 Buy LToken", style=discord.ButtonStyle.green, row=0)
    async def btn_buy_start(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        uid = interaction.user.id

        on_cd, remain = is_on_cooldown(uid)
        if on_cd:
            await interaction.response.send_message(
                f"⚠️ Kamu baru saja menekan tombol **Buy LToken**. Tunggu {remain} detik sebelum menekan lagi.",
                ephemeral=True,
            )
            return

        # Set timestamp cooldown
        buy_cooldown[uid] = dt.now()

        await interaction.response.send_message(
            "Pilih produk yang ingin dibeli, lalu masukkan jumlah di *modal* yang muncul:",
            view=BuyFlowView(self.products, self.c, self.conn),
            ephemeral=True,
        )

    # 💰 BALANCE BUTTON (tanpa cooldown)
    @discord.ui.button(label="💰 Balance", style=discord.ButtonStyle.blurple, row=1)
    async def btn_balance(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        uid = interaction.user.id
        self.c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        row = self.c.fetchone()
        bal = int(row[0]) if row else 0
        emb = discord.Embed(title="💰 Your Balance", color=discord.Color.blurple())
        emb.add_field(name="Balance", value=f"{self.fmt_wl(bal)} {EMO_WL}")
        await interaction.response.send_message(embed=emb, ephemeral=True)

    # 🌍 DEPOSIT BUTTON (tanpa cooldown)
    @discord.ui.button(label="🌍 Deposit World", style=discord.ButtonStyle.gray, row=1)
    async def btn_deposit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Ambil dari DB kalau ada; kalau tidak, fallback default
        self.c.execute("SELECT world, bot FROM deposit LIMIT 1")
        row = self.c.fetchone()
        if row:
            world, botname = row
        else:
            world, botname = "MODALMEKI", "In The World"
        emb = discord.Embed(title="🌍 Deposit Info", color=discord.Color.gold())
        emb.add_field(name="World", value=world, inline=True)
        emb.add_field(name="Bot Name", value=botname, inline=True)
        emb.set_footer(text="Gunakan world & bot di atas untuk deposit saldo kamu.")
        await interaction.response.send_message(embed=emb, ephemeral=True)


# ======================================================================================
# MONITOR BACKGROUND
# ======================================================================================
async def monitor_pending_orders_loop(bot: commands.Bot, c, conn) -> None:
    """
    Cek pending_orders tanpa timeout:
      - getOrder prioritas
      - fallback getOrders kalau getOrder kosong
    """
    await bot.wait_until_ready()
    log_debug("[MONITOR] Started pending orders loop.")

    # Cek API_KEY lagi sebelum loop berjalan
    if not API_KEY:
        log_debug("[MONITOR WARNING] API_KEY is empty. Monitoring loop skipped.")
        return

    while not bot.is_closed():
        try:
            c.execute(
                "SELECT order_id, user_id, product_name, qty, total, balance_before FROM pending_orders WHERE status=?",
                ("PENDING",),
            )
            pendings = c.fetchall()
            if not pendings:
                await asyncio.sleep(MONITOR_INTERVAL_SEC)
                continue

            fallback_orders_cache = None

            for order_id, user_id, product_name, qty, total, balance_before in pendings:
                log_debug(f"[MONITOR] Checking order_id={order_id} user_id={user_id}")
                data = {}
                status_lower = "processing"

                try:
                    data = await api_get_order(order_id)
                    status_lower = str(data.get("status", status_lower)).lower()
                    accounts = data.get("accounts", []) or []
                    processing_flag = bool(data.get("processing", False))
                except Exception as e:
                    log_debug(
                        f"[MONITOR] getOrder error {order_id}: {e.__class__.__name__}: {e}. Retrying next loop."
                    )
                    continue

                if processing_flag or "processing" in status_lower:
                    log_debug(f"[MONITOR] order_id={order_id} still processing...")
                    if fallback_orders_cache is None:
                        try:
                            fallback_orders_cache = await api_get_orders()
                        except Exception as e:
                            log_debug(
                                f"[MONITOR] getOrders error: {e.__class__.__name__}: {e}"
                            )
                            fallback_orders_cache = None
                    if isinstance(fallback_orders_cache, list):
                        match = next(
                            (
                                o
                                for o in fallback_orders_cache
                                if str(o.get("orderID")) == str(order_id)
                            ),
                            None,
                        )
                        if match:
                            status_lower = str(
                                match.get("status", status_lower)
                            ).lower()
                            accounts = match.get("accounts", []) or []
                            log_debug(
                                f"[MONITOR] Fallback getOrders matched: status={status_lower}, accounts={len(accounts)}"
                            )

                # SUCCESS + accounts -> DM + mark SUCCESS
                if status_lower == "success" and accounts:
                    c.execute(
                        "UPDATE pending_orders SET status=? WHERE order_id=?",
                        ("SUCCESS", order_id),
                    )
                    c.execute(
                        "UPDATE orders SET status=? WHERE order_id=?",
                        ("Success", order_id),
                    )
                    conn.commit()
                    log_debug(f"[MONITOR] SUCCESS order_id={order_id}, sending DM")

                    user = bot.get_user(int(user_id))
                    if user:
                        try:
                            dm_msg, dm_file = format_accounts_dm(
                                product_name, int(qty), int(total), accounts
                            )
                            await user.send(dm_msg, file=dm_file)
                        except Exception as e:
                            log_debug(f"[MONITOR] DM fail user={user_id}: {e}")

                    # Arsip ke ltoken_orders
                    try:
                        c.execute(
                            """
                            INSERT INTO ltoken_orders (order_id, user_id, product_name, qty, total, status)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (order_id, user_id, product_name, qty, total, "Success"),
                        )
                        conn.commit()
                    except Exception as e:
                        log_debug(f"[MONITOR] insert ltoken_orders fail: {e}")

                    # Role (opsional) - MENGGUNAKAN ID LOKAL
                    try:
                        if LOCAL_ROLE_ID:
                            for guild in bot.guilds:
                                member = guild.get_member(int(user_id))
                                if member:
                                    role = guild.get_role(LOCAL_ROLE_ID)
                                    if role:
                                        await member.add_roles(role)
                    except Exception as e:
                        log_debug(f"[ROLE] Gagal menambah role user={user_id}: {e}")

                    # Testimoni (opsional) - MENGGUNAKAN ID LOKAL
                    try:
                        if LOCAL_TESTIMONI_CHANNEL_ID:
                            channel = bot.get_channel(LOCAL_TESTIMONI_CHANNEL_ID)
                            if channel:
                                embed = discord.Embed(
                                    title=f"#LToken Order #{order_id}",
                                    color=discord.Color.green(),
                                )
                                user_mention = f"<@{user_id}>"
                                embed.add_field(
                                    name="👤 Buyer",
                                    value=user_mention,
                                    inline=False,
                                )
                                embed.add_field(
                                    name="🧩 Product",
                                    value=f"{product_name} x{qty}",
                                    inline=False,
                                )
                                embed.add_field(
                                    name="💰 Total",
                                    value=f"{total} {EMO_WL}",
                                    inline=False,
                                )
                                embed.set_footer(
                                    text="LToken successfully delivered — Thank you for your purchase 💎"
                                )
                                await channel.send(embed=embed)
                    except Exception as e:
                        log_debug(f"[TESTIMONI] Gagal kirim testimoni: {e}")

                    # Unlock
                    BuyLTokenModal.active_purchases.discard(int(user_id))
                    log_debug(f"[LOCK] Unlock user={user_id} setelah SUCCESS")
                    continue

                # FAILED -> rollback
                if status_lower in ("failed", "fail", "canceled", "cancelled"):
                    c.execute(
                        "UPDATE users SET balance=? WHERE user_id=?",
                        (balance_before, user_id),
                    )
                    c.execute(
                        "UPDATE pending_orders SET status=? WHERE order_id=?",
                        ("FAILED", order_id),
                    )
                    c.execute(
                        "UPDATE orders SET status=? WHERE order_id=?",
                        ("Failed", order_id),
                    )
                    conn.commit()
                    log_debug(
                        f"[MONITOR] FAILED order_id={order_id} -> rollback to {balance_before}"
                    )

                    user = bot.get_user(int(user_id))
                    if user:
                        try:
                            await user.send(
                                f"❌ Order {order_id} failed. Saldo Anda telah dikembalikan ke {balance_before} {EMO_WL}."
                            )
                        except Exception as e:
                            log_debug(f"[MONITOR] DM fail user={user_id}: {e}")

                    BuyLTokenModal.active_purchases.discard(int(user_id))
                    log_debug(f"[LOCK] Unlock user={user_id} setelah FAILED")
                    continue

                # success tanpa accounts -> tunggu lagi
                if status_lower == "success" and not accounts:
                    log_debug(
                        f"[MONITOR] order_id={order_id} success but no accounts yet, waiting..."
                    )
                    continue

                # selain itu: tetap pending
                log_debug(
                    f"[MONITOR] order_id={order_id} remains pending... status={status_lower}"
                )

            await asyncio.sleep(MONITOR_INTERVAL_SEC)

        except Exception as e:
            log_debug(f"[MONITOR] Loop error: {e}")
            await asyncio.sleep(MONITOR_INTERVAL_SEC)


# ======================================================================================
# COMMANDS & SETUP
# ======================================================================================
def _extract_products(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    return data.get("products", []) or []


def _guild_obj_from_env() -> Optional[discord.Object]:
    gid = os.getenv(GUILD_ID_ENV)
    try:
        if gid:
            return discord.Object(int(gid))
    except Exception:
        return None
    return None


def setup(bot: commands.Bot, c, conn, fmt_wl, PREFIX, DB_NAME=None):
    """
    Entry point yang akan dipanggil dari bot_core.py
    """
    # Load ENV
    global API_KEY, BASE_URL
    API_KEY = os.getenv("LTOKEN_API_KEY", "")
    BASE_URL = os.getenv("LTOKEN_BASE_URL", "https://cid.surferwallet.net/publicApi")

    if not API_KEY:
        print(
            "[CRITICAL] LTOKEN_API_KEY tidak ditemukan di .env. Fungsi LToken API akan error 400."
        )
    log_debug(f"[INIT] API_KEY loaded: {API_KEY[:4]}... (length: {len(API_KEY)})")

    ensure_schema(c, conn)

    # === AUTO REFRESH STOCK (SETIAP 60 DETIK) ===
    message_cache = {"channel_id": None, "message": None}

    def build_ltoken_embed():
        async def inner():
            data = await api_get_products()
            balance_web = await api_get_balance_web()
            products = _extract_products(data)
            return render_stock_embed(products, balance_web, fmt_wl)
        return inner

    @tasks.loop(seconds=60)
    async def update_ltoken_stock():
        """Loop untuk update embed LToken stock setiap 60 detik."""
        if message_cache["channel_id"] is None:
            return
        channel = bot.get_channel(message_cache["channel_id"])
        if channel is None or message_cache["message"] is None:
            return
        try:
            builder = build_ltoken_embed()
            embed = await builder()
            data = await api_get_products()
            products = _extract_products(data)
            await message_cache["message"].edit(
                embed=embed, view=StockView(products, c, conn, 0, fmt_wl)
            )
        except Exception as e:
            log_debug(f"[AUTO UPDATE LTOKEN] Error: {e}")
            try:
                builder = build_ltoken_embed()
                embed = await builder()
                data = await api_get_products()
                products = _extract_products(data)
                msg = await channel.send(
                    embed=embed, view=StockView(products, c, conn, 0, fmt_wl)
                )
                message_cache["message"] = msg
            except Exception as e2:
                log_debug(f"[AUTO UPDATE LTOKEN] Gagal kirim ulang: {e2}")

    @update_ltoken_stock.before_loop
    async def before_update_ltoken_stock():
        await bot.wait_until_ready()

    # === COMMAND UTAMA: /ltokenstock (juga bisa dipanggil sebagai !ltokenstock) ===
    @bot.hybrid_command(
        name="ltokenstock",
        usage=f"{PREFIX}ltokenstock",
        description="Show LToken stock dari web dan auto-refresh tiap 10 detik",
    )
    @is_allowed_user()
    @app_commands.guilds(_guild_obj_from_env() or discord.Object(0))
    async def ltokenstock(ctx: commands.Context):
        if not API_KEY:
            await ctx.reply(
                "❌ **ERROR**: LTOKEN_API_KEY belum diatur di file .env.",
                ephemeral=True,
                mention_author=False,
            )
            return

        try:
            data = await api_get_products()
            balance_web = await api_get_balance_web()
            products = _extract_products(data)
        except aiohttp.ClientConnectorError:
            await ctx.reply(
                "❌ **ERROR**: Gagal terhubung ke API LToken. Cek BASE_URL/koneksi.",
                ephemeral=True,
                mention_author=False,
            )
            return

        embed = render_stock_embed(products, balance_web, fmt_wl)
        msg = await ctx.send(
            embed=embed, view=StockView(products, c, conn, ctx.author.id, fmt_wl)
        )

        message_cache["channel_id"] = ctx.channel.id
        message_cache["message"] = msg

        if not update_ltoken_stock.is_running():
            update_ltoken_stock.start()
            log_debug("[AUTO UPDATE LTOKEN] Loop dimulai")

    # -------------- COMMAND: order <id> --------------
    @bot.command(name="order", help="Check order status by ID (usage: !order <order_id>)")
    async def order_status(ctx: commands.Context, order_id: str):
        if not API_KEY:
            await ctx.reply(
                "❌ **ERROR**: LTOKEN_API_KEY belum diatur di file .env.",
                mention_author=False,
            )
            return

        await ctx.trigger_typing()

        try:
            data = await api_get_order(order_id)
        except aiohttp.ClientConnectorError:
            await ctx.reply(
                "❌ **ERROR**: Gagal terhubung ke API LToken. Cek BASE_URL/koneksi.",
                mention_author=False,
            )
            return

        status = str(data.get("status", "Unknown"))
        quantity = safe_int(data.get("quantity", 0))
        product_name = "Ltoken_create"

        c.execute(
            "SELECT product_name, qty, status FROM orders WHERE order_id=?", (order_id,)
        )
        row = c.fetchone()
        if row:
            product_name, quantity, _st = row

        emb = discord.Embed(title="📦 Order Status", color=discord.Color.blurple())
        emb.add_field(name="Order ID", value=str(order_id), inline=False)
        emb.add_field(name="Product", value=product_name, inline=True)
        emb.add_field(name="Quantity", value=str(quantity), inline=True)

        status_up = status.upper()
        if status.lower() == "success":
            emb.add_field(name="Status", value="✅ SUCCESS", inline=False)
            accounts = data.get("accounts", []) or []
            if accounts:
                emb.add_field(
                    name="Info", value="✅ Akun sudah siap. Cek DM Anda.", inline=False
                )
            else:
                emb.add_field(
                    name="Info",
                    value="⚠️ Success tapi belum ada akun. Monitor sedang memeriksa.",
                    inline=False,
                )
        elif "fail" in status.lower():
            emb.add_field(name="Status", value="❌ FAILED", inline=False)
        elif "process" in status.lower():
            emb.add_field(name="Status", value=f"⏳ {status_up}", inline=False)
        else:
            emb.add_field(name="Status", value=f"❓ {status_up}", inline=False)

        await ctx.reply(embed=emb, mention_author=False)

    # -------------- COMMAND: myorders --------------
    @bot.command(name="myorders", help="Show your recent orders")
    async def myorders(ctx: commands.Context):
        uid = ctx.author.id
        c.execute(
            """
            SELECT order_id, product_name, qty, unit_price, total, status, created_at
            FROM orders
            WHERE user_id=?
            ORDER BY datetime(created_at) DESC
            LIMIT 10
            """,
            (uid,),
        )
        rows = c.fetchall()
        if not rows:
            await ctx.reply("❌ Anda belum memiliki pesanan.", mention_author=False)
            return

        lines = []
        for order_id, product_name, qty, unit_price, total, status, created_at in rows:
            created_show = str(created_at)
            if isinstance(created_at, str) and "." in created_at:
                created_show = created_at.split(".")[0]
            emb_line = (
                f"**#{order_id}** • {product_name} x{qty} • "
                f"{fmt_wl(total)} {EMO_WL} • {status} • {created_show}"
            )
            lines.append(emb_line)

        emb = discord.Embed(
            title="🧾 Pesanan Terbaru Anda",
            color=discord.Color.blue(),
            description="\n".join(lines),
        )
        await ctx.reply(embed=emb, mention_author=False)