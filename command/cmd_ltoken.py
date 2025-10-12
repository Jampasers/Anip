# cmd_ltoken.py
# ======================================================================================
# FINAL PANJANG (≈600+ lines)
# - Harga JUAL fix 27 WL
# - Tampilkan produk dari /stock (skip "Old Account"), gaya emoji mirip ui_views
# - Tombol: 🛒 Buy (Button -> View Dropdown -> Modal), 💰 Balance (DB bot), 🌍 Deposit World (tabel deposit)
# - Purchase: potong saldo sementara -> POST /purchase
#     * Jika accounts langsung ada -> DM buyer + sukses (KIRIM FILE .TXT)
#     * Jika masih processing -> simpan pending_orders (status=PENDING), tampil ⏳ Processing
# - Background monitor:
#     * loop tanpa timeout, cek /getOrder
#     * kalau Success + accounts -> DM buyer & mark SUCCESS (KIRIM FILE .TXT)
#     * kalau Failed -> rollback saldo & mark FAILED
# - Command manual: !order <id>, !myorders
# - COMMAND !setdeposit TELAH DIHAPUS
# - COOLDOWN Dihapus (tidak ada @commands.cooldown)
# ======================================================================================

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Select, Modal, TextInput
import aiohttp
import asyncio
import datetime
import os
from typing import Any, Dict, List, Optional, Tuple
from io import BytesIO # <--- BARU: Import untuk membuat file di memori

# ----------------------------------
# KONFIG (NILAI DIAMBIL DARI FILE YANG ANDA BERIKAN)
# ----------------------------------
API_KEY: str = "6f54c300286ece6ad5d0ff172d38d8c3" # <--- API KEY Anda (Updated)
BASE_URL: str = "https://cid.surferwallet.net/publicApi" 
SELL_PRICE_WL: int = 27            # harga jual fix (Updated)
MONITOR_INTERVAL_SEC: int = 5      # interval monitor pending (realtime, bisa 3-5 detik)
GUILD_ID_ENV = "SERVER_ID"         # untuk @app_commands.guilds

# ----------------------------------
# EMOJI SAMAAN DENGAN ui_views
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
# DB SCHEMA ENSURE (Dipertahankan di sini untuk memastikan, walau sudah ada di bot_core)
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
    conn.commit()

# ======================================================================================
# HTTP HELPERS
# ======================================================================================
async def http_get_json(url: str, *,
                        params: Optional[Dict[str, Any]] = None,
                        json_body: Optional[Dict[str, Any]] = None,
                        headers: Optional[Dict[str, str]] = None) -> Any:
    """GET -> JSON (bisa pakai params atau JSON body jika server nerima)."""
    log_debug(f"[HTTP][GET] {url} params={params} body={json_body}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, json=json_body, headers=headers, timeout=10) as resp:
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

async def http_post_json(url: str, *, payload: Dict[str, Any],
                         headers: Optional[Dict[str, str]] = None) -> Any:
    """POST -> JSON"""
    log_debug(f"[HTTP][POST] {url} payload={payload}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
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
    """Cek status order by ID. Urutan: GET query -> GET+body -> POST+body."""
    url = f"{BASE_URL}/getOrder"

    # 1) GET query (paling umum)
    data = await http_get_json(url, params={"apikey": API_KEY, "orderID": order_id})
    if isinstance(data, dict) and data:
        if ("status" in data) or ("accounts" in data) or ("success" in data) or ("processing" in data):
            return data
    
    # 2) POST + JSON body (paling reliable)
    data = await http_post_json(url, payload={"apikey": API_KEY, "orderID": order_id},
                                headers={"Content-Type": "application/json"})
    if isinstance(data, dict):
        return data

    # 3) GET + JSON body (jarang, tapi dipertahankan dari file Anda)
    data = await http_get_json(url, json_body={"apikey": API_KEY, "orderID": order_id},
                               headers={"Content-Type": "application/json"})
    if isinstance(data, dict) and data:
        if ("status" in data) or ("accounts" in data) or ("success" in data) or ("processing" in data):
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
    return datetime.datetime.now().strftime('%H:%M:%S')

# --- NEW FORMATTING LOGIC ---
def format_single_account_to_string(acc: Dict[str, Any]) -> str:
    """Formats a single account dict into the pipe-separated login string format."""
    # Mapping API fields (acc) to required output keys
    # Menggunakan nilai default untuk field yang tidak disediakan API (seperti platform, cbits, playerAge)
    data = {
        # Ambil nilai dari API, jika tidak ada gunakan default yang diminta user
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
    
    # Urutan kunci sesuai permintaan: mac, wk, platform, rid, name, cbits, playerAge, token, vid
    parts = []
    for key in ["mac", "wk", "platform", "rid", "name", "cbits", "playerAge", "token", "vid"]:
        parts.append(f"{key}:{data.get(key, 'N/A')}")
        
    return "|".join(parts)

def format_accounts_dm(product_name: str, qty: int, total: int, accounts: List[Dict[str, Any]]) -> Tuple[str, discord.File]:
    """
    Formats accounts into a single file and prepares the DM message.
    Returns: (text_message, discord.File)
    """
    # 1. Format each account into the pipe-separated string
    formatted_accounts = []
    for acc in accounts:
        formatted_accounts.append(format_single_account_to_string(acc))

    # 2. Join into a single string (content for the file)
    file_content = "\n".join(formatted_accounts)
    
    # 3. Create the discord.File object using a BytesIO buffer
    file_name = f"LToken_Order_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    file_buffer = BytesIO(file_content.encode('utf-8'))
    discord_file = discord.File(file_buffer, filename=file_name)

    # 4. Create the text message
    text_message = (
        f"✅ **LToken Order Ready!**\n"
        f"📦 **Product** : {product_name}\n"
        f"🔢 **Quantity**: {qty}\n"
        f"💰 **Total** : {total} {EMO_WL}\n\n"
        f"{EMO_PANAH} File **`{file_name}`** berisi {qty} akun telah dikirim. Gunakan data di dalamnya untuk login.\n"
        "⚠️ Simpan data akun Anda dengan aman."
    )
    
    return text_message, discord_file
# --- END NEW FORMATTING LOGIC ---


# ======================================================================================
# RENDER EMBED
# ======================================================================================
def render_stock_embed(products: List[Dict[str, Any]], balance_web: int, fmt_wl) -> discord.Embed:
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
        # Logika: stock_real adalah batas terkecil antara (stok dari web) atau (jumlah yang bisa dibeli dengan saldo web)
        stock_can_buy = balance_web // SELL_PRICE_WL if SELL_PRICE_WL > 0 else instock
        stock_real = min(instock, stock_can_buy) 

        block = (
            f"{EMO_TOA}  {name}\n"
            f"{EMO_PANAH}  Stock Web: {fmt_wl(instock)}\n"
            f"{EMO_PANAH}  Price: {SELL_PRICE_WL} {EMO_WL}\n"
            f"{EMO_PANAH}  Your Available Stock: {fmt_wl(stock_real)}\n"
            f"{EMO_PANAH}  Your Web Balance: {fmt_wl(balance_web)} {EMO_WL}"
        )
        blocks.append(block)

    embed.description = "\n========================================\n".join(blocks) if blocks else "❌ Belum ada produk dari web."
    return embed

# ======================================================================================
# UI: Select + Modal + View
# ======================================================================================
class ProductSelect(Select):
    def __init__(self, products: List[Dict[str, Any]], c, conn):
        # build options skip 'Old Account'
        options = []
        for p in products:
            name = p.get("name", "Unknown")
            if "Old Account" in name:
                continue
            options.append(discord.SelectOption(label=name, description=f"Harga {SELL_PRICE_WL} WL"))
        super().__init__(placeholder="Pilih produk...", min_values=1, max_values=1,
                         options=options, disabled=(len(options) == 0))
        self.c = c
        self.conn = conn

    async def callback(self, interaction: discord.Interaction):
        product_name = self.values[0]
        # Setelah memilih produk, hapus pesan dropdown dan tampilkan modal
        await interaction.response.send_modal(BuyLTokenModal(self.c, self.conn, product_name))
        # Karena kita memanggil modal, pesan dropdown (ephemeral) akan tetap ada 
        # sampai di-dismiss, tidak perlu di-edit/dihapus di sini

class BuyLTokenModal(Modal, title="🛒 Buy LToken"):
    def __init__(self, c, conn, product_name: str):
        super().__init__()
        self.c = c
        self.conn = conn
        self.product_name = product_name
        self.qty_input = TextInput(label="Amount", placeholder="1", required=True)
        self.add_item(self.qty_input)

    async def on_submit(self, interaction: discord.Interaction):
        # 1) Validasi qty
        try:
            qty = int(str(self.qty_input.value).strip())
            if qty <= 0:
                raise ValueError
        except Exception:
            await interaction.response.send_message("❌ Invalid amount.", ephemeral=True)
            return

        uid = interaction.user.id

        # 2) Ambil balance user dari DB
        self.c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        row = self.c.fetchone()
        if not row:
            await interaction.response.send_message("❌ Kamu belum register.", ephemeral=True)
            return
        balance_db = int(row[0] or 0)

        # 3) Hitung total harga fix 27 WL
        total = qty * SELL_PRICE_WL
        if balance_db < total:
            await interaction.response.send_message(
                "❌ Purchase failed: Your central balance is insufficient to complete this order.",
                ephemeral=True
            )
            return

        # 4) Potong saldo sementara
        new_balance = balance_db - total
        self.c.execute("UPDATE users SET balance=? WHERE user_id=?", (new_balance, uid))
        self.conn.commit()
        log_debug(f"[ORDER] Balance cut user={uid} old={balance_db} new={new_balance} total={total}")

        # 5) Purchase
        result = {}
        try:
            # Gunakan API yang benar
            result = await api_purchase(self.product_name, qty)
        except aiohttp.ClientConnectorError:
            # Jika koneksi gagal, langsung rollback dan beri tahu user
            self.c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_db, uid))
            self.conn.commit()
            log_debug(f"[ORDER] Purchase failed due to connection error. Rollback user={uid}")
            await interaction.response.send_message(
                "❌ Purchase failed: Could not connect to API server. Please try again later.", ephemeral=True)
            return
        except Exception as e:
            self.c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_db, uid))
            self.conn.commit()
            log_debug(f"[ORDER] Purchase failed due to unknown API error: {e}. Rollback user={uid}")
            await interaction.response.send_message(
                f"❌ Purchase failed: Unknown API error ({e.__class__.__name__}). Rollback.", ephemeral=True)
            return

        log_debug(f"[PURCHASE] resp={result}")

        order_id = str(result.get("orderID") or result.get("orderId") or "").strip()
        accounts = result.get("accounts", []) or result.get("accounts:", []) or []
        order_date = result.get("orderDate")
        processing_flag = bool(result.get("processing", False))
        status_text = str(result.get("status", "")).lower()

        # 6) Jika langsung ada akun -> sukses + DM (UPDATE KIRIM FILE)
        if result.get("success") and accounts:
            # catat order selesai
            self.c.execute(
                "INSERT OR REPLACE INTO orders (order_id, user_id, product_name, qty, unit_price, total, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id or f"direct-{uid}-{datetime.datetime.now().timestamp()}", uid, self.product_name, qty, SELL_PRICE_WL, total, "Success", datetime.datetime.now())
            )
            self.conn.commit()

            emb = discord.Embed(title="✅ Purchase Success!", color=discord.Color.green())
            emb.add_field(name="Product", value=self.product_name, inline=False)
            emb.add_field(name="Quantity", value=str(qty), inline=True)
            emb.add_field(name="Total", value=f"{total} {EMO_WL}", inline=True)
            emb.add_field(name="Balance Baru", value=f"{new_balance} {EMO_WL}", inline=False)
            if order_id:
                emb.add_field(name="Order ID", value=order_id, inline=False)
            if order_date:
                emb.set_footer(text=f"Order Date: {order_date}")
            
            # TANGGAPI INTERAKSI DULU
            await interaction.response.send_message(embed=emb, ephemeral=True)

            # DM akun (MENGGUNAKAN FILE)
            try:
                dm_msg, dm_file = format_accounts_dm(self.product_name, qty, total, accounts)
                await interaction.user.send(dm_msg, file=dm_file)
            except discord.Forbidden:
                try:
                    await interaction.followup.send("⚠️ Gagal DM: Akun tidak terkirim. Mohon aktifkan DM Anda!", ephemeral=True)
                except Exception:
                    pass
            return

        # 7) Kalau masih processing (tidak ada accounts) -> simpan pending & tampil Processing
        if result.get("success") and (processing_flag or "processing" in status_text):
            if not order_id:
                # Invalid; rollback
                self.c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_db, uid))
                self.conn.commit()
                await interaction.response.send_message("❌ Purchase failed: Invalid order state (No order ID from API).", ephemeral=True)
                return

            # simpan ke pending_orders
            self.c.execute(
                """
                INSERT OR REPLACE INTO pending_orders
                    (order_id, user_id, product_name, qty, total, balance_before, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (order_id, uid, self.product_name, qty, total, balance_db, "PENDING")
            )
            # juga catat di orders ringkas
            self.c.execute(
                "INSERT OR REPLACE INTO orders (order_id, user_id, product_name, qty, unit_price, total, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id, uid, self.product_name, qty, SELL_PRICE_WL, total, "Processing", datetime.datetime.now())
            )
            self.conn.commit()
            log_debug(f"[ORDER] Pending saved orderID={order_id} user={uid}")

            # embed Processing
            processing = discord.Embed(title="⏳ Purchase Processing", color=discord.Color.orange())
            processing.add_field(name="Status", value="Pembayaran diterima, sedang proses pembuatan token...")
            processing.add_field(name="Product", value=self.product_name, inline=True)
            processing.add_field(name="Quantity", value=str(qty), inline=True)
            processing.add_field(name="Total", value=f"{total} {EMO_WL}", inline=True)
            processing.add_field(name="Balance (pending)", value=f"{new_balance} {EMO_WL}", inline=False)
            processing.add_field(name="Order ID", value=str(order_id), inline=False)
            if order_date:
                processing.set_footer(text=f"Order Date: {order_date}")
            await interaction.response.send_message(embed=processing, ephemeral=True)
            return

        # 8) Failure -> rollback
        err_msg = str(result.get("message", "Unknown error"))
        self.c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_db, uid))
        self.conn.commit()
        pretty = "❌ Purchase failed: Your central balance is insufficient to complete this order." if "insufficient" in err_msg.lower() else f"❌ Purchase failed: {err_msg}"
        await interaction.response.send_message(pretty, ephemeral=True)
        log_debug(f"[ORDER] Failed; rollback user={uid} reason={err_msg}")


# --- NEW VIEW FOR BUY FLOW (Dropdown) ---
class BuyFlowView(View):
    def __init__(self, products: List[Dict[str, Any]], c, conn):
        super().__init__(timeout=180) # Timeout 3 minutes
        # Dropdown pilih produk
        self.add_item(ProductSelect(products, c, conn))
        
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red, row=1)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Purchase dibatalkan.", embed=None, view=None)


# --- MAIN VIEW MODIFIED ---
class StockView(View):
    def __init__(self, products: List[Dict[str, Any]], c, conn, requester_id: int, fmt_wl):
        super().__init__(timeout=None)
        self.c = c
        self.conn = conn
        self.requester_id = requester_id
        self.fmt_wl = fmt_wl
        self.products = products # Simpan products untuk Buy button
        # self.add_item(ProductSelect(products, c, conn)) # LOGIKA DROPDOWN LAMA DIHAPUS

    @discord.ui.button(label="🛒 Buy LToken", style=discord.ButtonStyle.green, row=0) # New Buy Button
    async def btn_buy_start(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Pastikan hanya pemanggil command yang bisa memulai flow buy
        if interaction.user.id != self.requester_id:
             await interaction.response.send_message("❌ Hanya yang memanggil command ini yang bisa menggunakan tombol Buy.", ephemeral=True)
             return
             
        # Tampilkan dropdown di pesan ephemeral baru
        await interaction.response.send_message(
            "Pilih produk yang ingin dibeli, lalu masukkan jumlah di *modal* yang muncul:", 
            view=BuyFlowView(self.products, self.c, self.conn), 
            ephemeral=True
        )

    @discord.ui.button(label="💰 Balance", style=discord.ButtonStyle.blurple, row=1)
    async def btn_balance(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        self.c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        row = self.c.fetchone()
        bal = int(row[0]) if row else 0
        emb = discord.Embed(title="💰 Your Balance", color=discord.Color.blurple())
        emb.add_field(name="Balance", value=f"{self.fmt_wl(bal)} {EMO_WL}")
        await interaction.response.send_message(embed=emb, ephemeral=True)

    @discord.ui.button(label="🌍 Deposit World", style=discord.ButtonStyle.gray, row=1)
    async def btn_deposit(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.c.execute("SELECT world, bot FROM deposit LIMIT 1")
        row = self.c.fetchone()
        if row:
            world, botname = row
            emb = discord.Embed(title="🌍 Deposit Info", color=discord.Color.gold())
            emb.add_field(name="World", value=world, inline=True)
            emb.add_field(name="Bot Name", value=botname, inline=True)
            await interaction.response.send_message(embed=emb, ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Deposit info belum diatur.", ephemeral=True)

# ======================================================================================
# MONITOR BACKGROUND (Fungsi ini dipanggil oleh bot_core.py)
# ======================================================================================
async def monitor_pending_orders_loop(bot: commands.Bot, c, conn) -> None:
    """
    Cek pending_orders tanpa timeout:
      - getOrder prioritas
      - fallback getOrders kalau getOrder kosong
    """
    await bot.wait_until_ready()
    log_debug("[MONITOR] Started pending orders loop.")
    while not bot.is_closed():
        try:
            # Gunakan c, conn yang dilewatkan
            c.execute(
                "SELECT order_id, user_id, product_name, qty, total, balance_before FROM pending_orders WHERE status=?",
                ("PENDING",)
            )
            pendings = c.fetchall()
            if not pendings:
                await asyncio.sleep(MONITOR_INTERVAL_SEC)
                continue

            # satu kali fetch fallback (hemat request)
            fallback_orders_cache = None

            for (order_id, user_id, product_name, qty, total, balance_before) in pendings:
                log_debug(f"[MONITOR] Checking order_id={order_id} user_id={user_id}")
                data = {}
                # Inisiasi data dengan status "PROCESSING" agar tidak dianggap gagal
                status_lower = "processing" 
                
                try:
                    data = await api_get_order(order_id)
                    status_lower = str(data.get("status", status_lower)).lower()
                    accounts = data.get("accounts", []) or []
                    processing_flag = bool(data.get("processing", False))
                except Exception as e:
                    # Exception di sini menangkap aiohttp.ClientConnectorError (koneksi)
                    log_debug(f"[MONITOR] getOrder error {order_id}: {e.__class__.__name__}: {e}. Retrying next loop.")
                    continue # Langsung ke order berikutnya atau loop berikutnya

                # Jika masih processing (status dari API)
                if processing_flag or "processing" in status_lower:
                    log_debug(f"[MONITOR] order_id={order_id} still processing...")
                    # Coba fallback getOrders (hanya jika diperlukan)
                    if fallback_orders_cache is None:
                        try:
                            fallback_orders_cache = await api_get_orders()
                        except Exception as e:
                            log_debug(f"[MONITOR] getOrders error: {e.__class__.__name__}: {e}")
                            fallback_orders_cache = None
                    if isinstance(fallback_orders_cache, list):
                        match = next((o for o in fallback_orders_cache if str(o.get("orderID")) == str(order_id)), None)
                        if match:
                            status_lower = str(match.get("status", status_lower)).lower()
                            accounts = match.get("accounts", []) or []
                            log_debug(f"[MONITOR] Fallback getOrders matched: status={status_lower}, accounts={len(accounts)}")

                # SUCCESS + accounts -> DM + mark SUCCESS (UPDATE KIRIM FILE)
                if status_lower == "success" and accounts:
                    # update pending_orders
                    c.execute("UPDATE pending_orders SET status=? WHERE order_id=?", ("SUCCESS", order_id))
                    # update orders ringkas
                    c.execute("UPDATE orders SET status=? WHERE order_id=?", ("Success", order_id))
                    conn.commit()
                    log_debug(f"[MONITOR] SUCCESS order_id={order_id}, sending DM")

                    user = bot.get_user(int(user_id))
                    if user:
                        try:
                            # Menggunakan format_accounts_dm yang baru (mengembalikan file)
                            dm_msg, dm_file = format_accounts_dm(product_name, int(qty), int(total), accounts)
                            await user.send(dm_msg, file=dm_file)
                        except Exception as e:
                            log_debug(f"[MONITOR] DM fail user={user_id}: {e}")
                    continue

                # FAILED -> rollback
                if status_lower in ("failed", "fail", "canceled", "cancelled"):
                    c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_before, user_id))
                    c.execute("UPDATE pending_orders SET status=? WHERE order_id=?", ("FAILED", order_id))
                    c.execute("UPDATE orders SET status=? WHERE order_id=?", ("Failed", order_id))
                    conn.commit()
                    log_debug(f"[MONITOR] FAILED order_id={order_id} -> rollback to {balance_before}")

                    user = bot.get_user(int(user_id))
                    if user:
                        try:
                            await user.send(f"❌ Order {order_id} failed. Saldo Anda telah dikembalikan ke {balance_before} {EMO_WL}.")
                        except Exception as e:
                            log_debug(f"[MONITOR] DM fail user={user_id}: {e}")
                    continue

                # success tanpa accounts -> tunggu lagi
                if status_lower == "success" and not accounts:
                    log_debug(f"[MONITOR] order_id={order_id} success but no accounts yet, waiting...")
                    continue

                # selain itu: tetap pending
                log_debug(f"[MONITOR] order_id={order_id} remains pending... status={status_lower}")

            await asyncio.sleep(MONITOR_INTERVAL_SEC)

        except Exception as e:
            log_debug(f"[MONITOR] Loop error: {e}")
            await asyncio.sleep(MONITOR_INTERVAL_SEC)


# ======================================================================================
# COMMANDS
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

def setup(bot: commands.Bot, c, conn, fmt_wl, PREFIX, DB_NAME=None): # <-- SETUP FIX
    """
    Entry point yang akan dipanggil dari bot_core.py
    """
    ensure_schema(c, conn)

    # -------------- COMMAND: ltokenstock --------------
    @bot.hybrid_command(
        name="ltokenstock",
        usage=f"{PREFIX}ltokenstock",
        description="Show LToken stock (web balance based, price fixed 27 WL)"
    )
    @app_commands.guilds(_guild_obj_from_env() or discord.Object(0))
    async def ltokenstock(ctx: commands.Context):
        # Ambil products & balance web
        data = {}
        balance_web = 0
        try:
            data = await api_get_products()
            balance_web = await api_get_balance_web()
        except aiohttp.ClientConnectorError:
             await ctx.reply("❌ **ERROR**: Gagal terhubung ke API LToken. Cek BASE_URL/koneksi.", ephemeral=True, mention_author=False)
             return

        products = _extract_products(data)
        
        # Render dan send
        embed = render_stock_embed(products, balance_web, fmt_wl)
        await ctx.send(embed=embed, view=StockView(products, c, conn, ctx.author.id, fmt_wl))

    # -------------- COMMAND: order <id> --------------
    @bot.command(name="order", help="Check order status by ID (usage: !order <order_id>)")
    async def order_status(ctx: commands.Context, order_id: str):
        await ctx.trigger_typing()
        
        data = {}
        try:
            data = await api_get_order(order_id)
        except aiohttp.ClientConnectorError:
             await ctx.reply("❌ **ERROR**: Gagal terhubung ke API LToken. Cek BASE_URL/koneksi.", mention_author=False)
             return

        status = str(data.get("status", "Unknown"))
        success = data.get("success", False)
        quantity = safe_int(data.get("quantity", 0))
        product_name = "Ltoken_create"

        c.execute("SELECT product_name, qty, status FROM orders WHERE order_id=?", (order_id,))
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
                emb.add_field(name="Info", value="✅ Akun sudah siap. Cek DM Anda.", inline=False)
            else:
                emb.add_field(name="Info", value="⚠️ Success tapi belum ada akun. Monitor sedang memeriksa.", inline=False)
        elif "fail" in status.lower():
            emb.add_field(name="Status", value="❌ FAILED", inline=False)
        elif "process" in status.lower():
            emb.add_field(name="Status", value=f"⏳ {status_up}", inline=False)
        else:
            emb.add_field(name="Status", value=f"❓ {status_up}", inline=False) # Unknown status

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
            """, (uid,)
        )
        rows = c.fetchall()
        if not rows:
            await ctx.reply("❌ Anda belum memiliki pesanan.", mention_author=False)
            return

        lines = []
        for (order_id, product_name, qty, unit_price, total, status, created_at) in rows:
            lines.append(
                f"**#{order_id}** • {product_name} x{qty} • {fmt_wl(total)} {EMO_WL} • {status} • {created_at.split('.')[0]}" # format WL dan hilangkan milidetik
            )

        emb = discord.Embed(title="🧾 Pesanan Terbaru Anda", color=discord.Color.blue(),
                             description="\n".join(lines))
        await ctx.reply(embed=emb, mention_author=False)
        
    # -------------- COMMAND: setdeposit (TELAH DIHAPUS) --------------
    # Logic untuk command !setdeposit telah dihapus sesuai permintaan.