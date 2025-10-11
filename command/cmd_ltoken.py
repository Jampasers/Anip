# cmd_ltoken.py
# ======================================================================================
# FINAL PANJANG (≈600+ lines)
# - Harga JUAL fix 26 WL (tidak pakai price API)
# - Tampilkan produk dari /stock (skip "Old Account"), gaya emoji mirip ui_views
# - Tombol: 🛒 Buy (dropdown → modal), 💰 Balance (DB bot), 🌍 Deposit World (tabel deposit)
# - Purchase: potong saldo sementara -> POST /purchase
#     * Jika accounts langsung ada -> DM buyer + sukses
#     * Jika masih processing -> simpan pending_orders (status=PENDING), tampil ⏳ Processing
# - Background monitor:
#     * start via on_ready (bukan di setup) -> aman di discord.py v2
#     * loop tanpa timeout, cek /getOrder
#     * fallback ke /getOrders bila /getOrder lambat update
#     * kalau Success + accounts -> DM buyer & mark SUCCESS
#     * kalau Failed -> rollback saldo & mark FAILED
# - Command manual: !order <id>, !myorders, !setdeposit <world> <botname> (admin only via role/env)
# - Logging detail: [DEBUG][ltoken] ...
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

# ----------------------------------
# KONFIG
# ----------------------------------
API_KEY: str = "b095e061b5a809bd5336329a27bf81cb"
BASE_URL: str = "https://cid.surferwallet.net/publicApi"
SELL_PRICE_WL: int = 26            # harga jual fix
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
# DB SCHEMA ENSURE
# ======================================================================================
def ensure_schema(c, conn) -> None:
    """Pastikan tabel yang diperlukan ada."""
    log_debug("[DB] Ensuring required tables exist...")
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            nama TEXT,
            balance INTEGER DEFAULT 0
        )
        """
    )
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
        async with session.get(url, params=params, json=json_body, headers=headers) as resp:
            status = resp.status
            try:
                data = await resp.json(content_type=None)
            except Exception:
                text = await resp.text()
                log_debug(f"[HTTP][GET][{status}] Non-JSON: {text[:500]}")
                return {}
            log_debug(f"[HTTP][GET][{status}] {str(data)[:900]}")
            return data

async def http_post_json(url: str, *, payload: Dict[str, Any],
                         headers: Optional[Dict[str, str]] = None) -> Any:
    """POST -> JSON"""
    log_debug(f"[HTTP][POST] {url} payload={payload}")
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            status = resp.status
            try:
                data = await resp.json(content_type=None)
            except Exception:
                text = await resp.text()
                log_debug(f"[HTTP][POST][{status}] Non-JSON: {text[:500]}")
                return {}
            log_debug(f"[HTTP][POST][{status}] {str(data)[:900]}")
            return data

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

    # 1) GET query
    data = await http_get_json(url, params={"apikey": API_KEY, "orderID": order_id})
    if isinstance(data, dict) and data:
        if ("status" in data) or ("accounts" in data) or ("success" in data) or ("processing" in data):
            return data

    # 2) GET + JSON body
    data = await http_get_json(url, json_body={"apikey": API_KEY, "orderID": order_id},
                               headers={"Content-Type": "application/json"})
    if isinstance(data, dict) and data:
        if ("status" in data) or ("accounts" in data) or ("success" in data) or ("processing" in data):
            return data

    # 3) POST + JSON body
    data = await http_post_json(url, payload={"apikey": API_KEY, "orderID": order_id},
                                headers={"Content-Type": "application/json"})
    if isinstance(data, dict):
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

def format_accounts_dm(product_name: str, qty: int, total: int, accounts: List[Dict[str, Any]]) -> str:
    header = (
        "✅ Your LToken Order is Ready!\n\n"
        f"📦 Product : {product_name}\n"
        f"🔢 Quantity: {qty}\n"
        f"💰 Total   : {total} {EMO_WL}\n\n"
        "Here are your accounts:\n"
        "────────────────────────────"
    )
    rows = []
    for acc in accounts:
        # Field yang umum di getOrder real: name, token, rid, vid, mac, wk
        rows.append(
            f"\n👤 GrowID   : `{acc.get('name')}`\n"
            f"🔑 Token    : `{acc.get('token')}`\n"
            f"📧 RID      : `{acc.get('rid')}`\n"
            f"🆔 VID      : `{acc.get('vid')}`\n"
            f"🔧 MAC      : `{acc.get('mac')}`\n"
            f"🔩 WK       : `{acc.get('wk')}`\n"
            "────────────────────────────"
        )
    tail = "\n⚠️ Keep your credentials safe."
    return header + "".join(rows) + tail

# ======================================================================================
# RENDER EMBED
# ======================================================================================
def render_stock_embed(products: List[Dict[str, Any]], balance_web: int) -> discord.Embed:
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
        stock_real = min(instock, balance_web // SELL_PRICE_WL) if SELL_PRICE_WL > 0 else instock

        block = (
            f"{EMO_TOA}  {name}\n"
            f"{EMO_PANAH}  Stock Web: {instock}\n"
            f"{EMO_PANAH}  Price: {SELL_PRICE_WL} {EMO_WL}\n"
            f"{EMO_PANAH}  Your Available Stock: {stock_real}\n"
            f"{EMO_PANAH}  Your Web Balance: {balance_web} {EMO_WL}"
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
        await interaction.response.send_modal(BuyLTokenModal(self.c, self.conn, product_name))

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

        # 3) Hitung total harga fix 26 WL
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
        result = await api_purchase(self.product_name, qty)
        log_debug(f"[PURCHASE] resp={result}")

        order_id = str(result.get("orderID") or result.get("orderId") or "").strip()
        accounts = result.get("accounts", []) or result.get("accounts:", []) or []
        order_date = result.get("orderDate")
        processing_flag = bool(result.get("processing", False))
        status_text = str(result.get("status", "")).lower()

        # 6) Jika langsung ada akun -> sukses + DM
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
            await interaction.response.send_message(embed=emb, ephemeral=True)

            # DM akun
            try:
                await interaction.user.send(format_accounts_dm(self.product_name, qty, total, accounts))
            except discord.Forbidden:
                try:
                    await interaction.followup.send("⚠️ Could not DM you the account details. Please enable your DMs!", ephemeral=True)
                except Exception:
                    pass
            return

        # 7) Kalau masih processing (tidak ada accounts) -> simpan pending & tampil Processing
        if result.get("success") and (processing_flag or "processing" in status_text):
            if not order_id:
                # Invalid; rollback
                self.c.execute("UPDATE users SET balance=? WHERE user_id=?", (balance_db, uid))
                self.conn.commit()
                await interaction.response.send_message("❌ Purchase failed: Invalid order state.", ephemeral=True)
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
            processing.add_field(name="Status", value="Payment received, generating token...")
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

class StockView(View):
    def __init__(self, products: List[Dict[str, Any]], c, conn, requester_id: int):
        super().__init__(timeout=None)
        self.c = c
        self.conn = conn
        self.requester_id = requester_id
        # Dropdown pilih produk
        self.add_item(ProductSelect(products, c, conn))

    @discord.ui.button(label="💰 Balance", style=discord.ButtonStyle.blurple, row=1)
    async def btn_balance(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        self.c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        row = self.c.fetchone()
        bal = int(row[0]) if row else 0
        emb = discord.Embed(title="💰 Your Balance", color=discord.Color.blurple())
        emb.add_field(name="Balance", value=f"{bal} {EMO_WL}")
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
# MONITOR BACKGROUND
# ======================================================================================
async def monitor_pending_orders_loop(bot: commands.Bot, c, conn) -> None:
    """
    Cek pending_orders tanpa timeout:
      - getOrder prioritas
      - fallback getOrders kalau getOrder kosong
      - status:
          * success + accounts -> DM, mark SUCCESS (orders dan pending_orders)
          * failed             -> rollback saldo, mark FAILED
          * processing         -> lanjut tunggu
    """
    await bot.wait_until_ready()
    log_debug("[MONITOR] Started pending orders loop.")
    while not bot.is_closed():
        try:
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
                try:
                    data = await api_get_order(order_id)
                except Exception as e:
                    log_debug(f"[MONITOR] getOrder error {order_id}: {e}")

                status_lower = str(data.get("status", "")).lower()
                accounts = data.get("accounts", []) or []
                processing_flag = bool(data.get("processing", False))

                # Jika masih processing
                if processing_flag or "processing" in status_lower:
                    log_debug(f"[MONITOR] order_id={order_id} still processing...")
                    # coba fallback getOrders; kadang di sini sudah success
                    if fallback_orders_cache is None:
                        try:
                            fallback_orders_cache = await api_get_orders()
                        except Exception as e:
                            log_debug(f"[MONITOR] getOrders error: {e}")
                            fallback_orders_cache = None
                    if isinstance(fallback_orders_cache, list):
                        match = next((o for o in fallback_orders_cache if str(o.get("orderID")) == str(order_id)), None)
                        if match:
                            status_lower = str(match.get("status", "")).lower()
                            accounts = match.get("accounts", []) or []
                            log_debug(f"[MONITOR] Fallback getOrders matched: status={status_lower}, accounts={len(accounts)}")

                # SUCCESS + accounts -> DM + mark SUCCESS
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
                            await user.send(format_accounts_dm(product_name, int(qty), int(total), accounts))
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
                            await user.send(f"❌ Order {order_id} failed. Your balance has been restored to {balance_before} {EMO_WL}.")
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

def setup(bot: commands.Bot, c, conn, fmt_wl, PREFIX, DB_NAME=None):
    """
    Entry point yang akan dipanggil dari bot_core.py
    - Register command
    - Ensure schema
    - Start monitor via on_ready (1x)
    """
    ensure_schema(c, conn)

    # -------------- COMMAND: ltokenstock --------------
    @bot.hybrid_command(
        name="ltokenstock",
        usage=f"{PREFIX}ltokenstock",
        description="Show LToken stock (web balance based, price fixed 26 WL)"
    )
    @app_commands.guilds(_guild_obj_from_env() or discord.Object(0))
    async def ltokenstock(ctx: commands.Context):
        # Ambil products & balance web
        data = await api_get_products()
        products = _extract_products(data)
        balance_web = await api_get_balance_web()

        # Render dan send
        embed = render_stock_embed(products, balance_web)
        await ctx.send(embed=embed, view=StockView(products, c, conn, ctx.author.id))

    # -------------- COMMAND: order <id> --------------
    @bot.command(name="order", help="Check order status by ID (usage: !order <order_id>)")
    async def order_status(ctx: commands.Context, order_id: str):
        await ctx.trigger_typing()
        data = await api_get_order(order_id)
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
                emb.add_field(name="Info", value="✅ Accounts are ready. Check your DMs.", inline=False)
            else:
                emb.add_field(name="Info", value="⚠️ Success without accounts payload yet.", inline=False)
        elif "fail" in status.lower():
            emb.add_field(name="Status", value="❌ FAILED", inline=False)
        elif "process" in status.lower():
            emb.add_field(name="Status", value=f"⏳ {status_up}", inline=False)
        else:
            emb.add_field(name="Status", value=f"⏳ {status_up}", inline=False)

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
            await ctx.reply("❌ You have no orders yet.", mention_author=False)
            return

        lines = []
        for (order_id, product_name, qty, unit_price, total, status, created_at) in rows:
            lines.append(
                f"**#{order_id}** • {product_name} x{qty} • {total} {EMO_WL} • {status} • {created_at}"
            )

        emb = discord.Embed(title="🧾 Your Recent Orders", color=discord.Color.blue(),
                             description="\n".join(lines))
        await ctx.reply(embed=emb, mention_author=False)

    # -------------- COMMAND: setdeposit (admin only) --------------
    # optional: hanya jika mau atur deposit via command. Boleh dihapus kalau tidak dibutuhkan.
    @bot.command(name="setdeposit", help="Set deposit world & bot name (admin only)")
    @commands.has_permissions(administrator=True)
    async def setdeposit(ctx: commands.Context, world: str, botname: str):
        c.execute("DELETE FROM deposit")
        c.execute("INSERT INTO deposit (world, bot) VALUES (?, ?)", (world, botname))
        conn.commit()
        await ctx.reply(f"✅ Deposit updated.\nWorld: **{world}**\nBot: **{botname}**", mention_author=False)

    # -------------- on_ready: start monitor once --------------
    # ===== Background Task untuk cek order pending =====
async def monitor_pending_orders(bot, c, conn):
    await bot.wait_until_ready()
    while not bot.is_closed():
        # cek pending order di DB
        # panggil API getOrder
        # update status jadi SUCCESS/FAILED
        # DM buyer kalau success
        await asyncio.sleep(10)

def start_monitor(bot, c, conn):
    """Dipanggil dari bot_core biar monitor jalan"""
    import asyncio
    if not hasattr(bot, "ltoken_monitor_started"):
        bot.ltoken_monitor_started = True
        asyncio.create_task(monitor_pending_orders(bot, c, conn))
        print("[DEBUG][ltoken] monitor_pending_orders loop started")
