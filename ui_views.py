# ui_views.py — full script with BUY + BUY PO (Pre Order) system
# --------------------------------------------------------------
# Fitur:
# - SET GROWID
# - BUY (pembelian langsung dari stock_items)
# - BUY PO (Pre Order) maksimal 10 per user per produk
#   * DM konfirmasi wajib aktif (kalau DM mati -> PO dibatalkan)
#   * Ada nomor antrian pada saat pencatatan PO
#   * Alokasi otomatis saat restock lewat allocate_preorders(kode)
#   * Partial fulfill: kalau stok nggak cukup, sisa tetap waiting
#   * DM hasil (success / partial). Kalau DM gagal saat fulfillment -> cancel & kembalikan stok
# - ProductSelect (BUY) & ProductSelectPO (BUY PO)
# - StockView dengan tombol BUY PO di samping BUY

import re
import asyncio
import discord
from discord.ui import View, Button, Modal, TextInput, Select

# ===== Globals (diisi dari setup) =====
bot = None
c = None
conn = None
fmt_wl = None
PREFIX = "!"



# ===== Helpers umum =====
NAME_REGEX = re.compile(r"^[a-z0-9]+$")


def normalize_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def is_valid_name(name: str) -> bool:
    return NAME_REGEX.match(name or "") is not None


async def send_ephemeral_countdown(
    interaction: discord.Interaction,
    content: str = "",
    seconds: int = 30,
    embed: "discord.Embed | None" = None,
):
    note = f"\n⏳ This message will expire in {seconds} seconds..."
    await interaction.response.send_message(content + note, ephemeral=True, embed=embed)
    try:
        msg = await interaction.original_response()
    except Exception:
        return
    for i in range(seconds - 1, 0, -1):
        await asyncio.sleep(1)
        try:
            await msg.edit(
                content=content + f"\n⏳ This message will expire in {i} seconds...",
                embed=embed,
            )
        except Exception:
            break
    await asyncio.sleep(1)
    try:
        await msg.edit(
            content="✅ Expired (ephemeral disappears on reload / channel change).",
            embed=embed,
        )
    except Exception:
        pass


# ===== Schema helpers =====
def ensure_users_schema(cur, connection):
    # Users
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            nama TEXT,
            balance INTEGER DEFAULT 0
        )
    """
    )
    connection.commit()

def ensure_preorders_schema(cur, connection):
    # Preorders (GrowID terlebih dahulu -> nama, lalu user_id)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS preorders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT,
            user_id INTEGER,
            kode TEXT,
            amount INTEGER,
            status TEXT DEFAULT 'waiting', -- waiting | success | cancelled
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    connection.commit()    
    
def ensure_transactions_schema(cur, connection):
    # Catat transaksi BUY
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            kode TEXT,
            jumlah INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Detail item dari transaksi BUY
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transaction_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER,
            nama_barang TEXT
        )
    """)
    connection.commit()


def ensure_transaction_items_schema(cur, connection):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transaction_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER,
            nama_barang TEXT
        )
    """)
    connection.commit()

def ensure_preorder_items_schema(cur, connection):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS preorder_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            preorder_id INTEGER,
            nama_barang TEXT
        )
    """)
    connection.commit()


async def fetch_products_for_select():
    # Ambil daftar produk untuk dropdown
    cur = c  # alias
    cur.execute(
        """
        SELECT s.kode, s.judul, COUNT(i.id) as jumlah, s.harga
        FROM stock s
        LEFT JOIN stock_items i ON s.kode = i.kode
        GROUP BY s.kode, s.judul, s.harga
        ORDER BY s.judul ASC
    """
    )
    return cur.fetchall()


# ============================================================
# GrowID Modal
# ============================================================
class GrowIDModal(Modal, title="Set / Change GrowID"):
    def __init__(self, author_id: int):
        super().__init__()
        self.author_id = author_id
        self.name_input = TextInput(
            label="GrowID (huruf+angka, auto lowercase)",
            placeholder="contoh: hanif123",
            required=True,
            max_length=20,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        new_name = normalize_name(self.name_input.value)
        if not new_name:
            await interaction.response.send_message(
                "❌ Invalid GrowID (gunakan huruf/angka).", ephemeral=True
            )
            return

        # Cek tidak dipakai user lain
        c.execute("SELECT user_id FROM users WHERE nama=?", (new_name,))
        row = c.fetchone()
        if row and row[0] != self.author_id:
            await interaction.response.send_message(
                "❌ GrowID sudah dipakai user lain.", ephemeral=True
            )
            return

        # Update/insert
        c.execute("SELECT nama FROM users WHERE user_id=?", (self.author_id,))
        me = c.fetchone()
        if me:
            c.execute(
                "UPDATE users SET nama=? WHERE user_id=?", (new_name, self.author_id)
            )
            conn.commit()
            await interaction.response.send_message(
                f"✅ GrowID updated: {new_name}", ephemeral=True
            )
        else:
            c.execute(
                "INSERT INTO users (nama,balance,user_id) VALUES (?,0,?)",
                (new_name, self.author_id),
            )
            conn.commit()
            await interaction.response.send_message(
                f"✅ GrowID registered: {new_name}", ephemeral=True
            )


# ============================================================
# BUY (langsung)
# ============================================================
class BuyModal(Modal, title="Enter Amount"):
    def __init__(self, kode: str, author: discord.Member):
        super().__init__()
        self.kode = kode
        self.author = author
        self.qty_input = TextInput(label="Amount", placeholder="1", required=True)
        self.add_item(self.qty_input)

    async def on_submit(self, interaction: discord.Interaction):
        # Validasi amount
        try:
            amount = int(str(self.qty_input.value).strip())
            if amount <= 0:
                raise ValueError
        except Exception:
            await interaction.response.send_message(
                "❌ Invalid amount (harus angka > 0).", ephemeral=True
            )
            return

        # Validasi user
        uid = self.author.id
        c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        u = c.fetchone()
        if not u:
            await interaction.response.send_message(
                "❌ Register dulu (SET GROWID).", ephemeral=True
            )
            return
        balance = int(u[0] or 0)

        # Cek stock & harga
        c.execute("SELECT COUNT(*) FROM stock_items WHERE kode=?", (self.kode,))
        stok = int(c.fetchone()[0] or 0)
        if stok < amount:
            await interaction.response.send_message(
                f"❌ Stock tidak cukup. Tersisa: {stok}", ephemeral=True
            )
            return

        c.execute("SELECT harga FROM stock WHERE kode=?", (self.kode,))
        h = c.fetchone()
        if not h:
            await interaction.response.send_message(
                "❌ Invalid product code.", ephemeral=True
            )
            return
        price = int(h[0])
        total = price * amount
        if balance < total:
            await interaction.response.send_message(
                "❌ Balance kurang.", ephemeral=True
            )
            return

        # Ambil items
        c.execute(
            "SELECT id, nama_barang FROM stock_items WHERE kode=? ORDER BY id LIMIT ?",
            (self.kode, amount),
        )
        items = c.fetchall()
        c.execute("SELECT balance, poin FROM users WHERE user_id = ?", (uid,))
        row = c.fetchone()
        balance_sekarang = None
        wl_dari_poin = None
        sisa_poin = None
        poin_after = None

        if row:
            balance_sekarang, poin_sekarang = row
            poin_after = poin_sekarang + total
            wl_dari_poin = poin_after // 5
            sisa_poin = poin_after % 5
            c.execute("UPDATE users SET balance = ?, poin = ? WHERE user_id = ?",
              (balance_sekarang + wl_dari_poin, sisa_poin, uid))

        ids = [str(x[0]) for x in items]
        if not ids:
            await interaction.response.send_message(
                "❌ Stock berubah, coba lagi.", ephemeral=True
            )
            return
        bought_names = "\n".join([x[1] for x in items])

        new_balance = balance - total
        await interaction.response.defer(ephemeral=True)

        # DM wajib sukses
        try:
            msg = (
                "```🛒 Purchase Success!\n"
                "--------------------------\n"
                f"Code   : {self.kode}\n"
                f"Amount : {amount}\n"
                f"Price  : {price}\n"
                f"Total  : {total}\n"
                f"Balance: {new_balance}\n\n"
                f"📦 Items:\n{bought_names}```"
            )
            await self.author.send(
                f"🔄 Konversi poin selesai!\n"
                f"+{wl_dari_poin} WL dari poin\n"
                f"WL Kamu Sekarang: {balance_sekarang + wl_dari_poin}\n"
                f"🪙 Sisa poin kamu sekarang: {sisa_poin}"
            )
            await self.author.send(msg)
        except Exception:
            await interaction.followup.send(
                "❌ DM kamu mati. Pembelian dibatalkan.", ephemeral=True
            )
            return

        # Commit transaksi
        c.execute(
            f"DELETE FROM stock_items WHERE id IN ({','.join(['?'] * len(ids))})",
            ids
        )
        c.execute(
            "UPDATE users SET balance = ? WHERE user_id = ?",
            (new_balance, uid)
        )
        c.execute(
            "INSERT INTO transactions (user_id, kode, jumlah) VALUES (?, ?, ?)",
            (uid, self.kode, amount)
        )
        transaction_id = c.lastrowid  # ambil order number

        # Simpan detail item yang dibeli
        for _, nama_barang in items:
            c.execute(
                "INSERT INTO transaction_items (transaction_id, nama_barang) VALUES (?, ?)",
                (transaction_id, nama_barang)
            )

        conn.commit()

        # ✅ Tambahkan role BUY ke pembeli
        try:
            guild = interaction.guild
            role = guild.get_role(839981629044555853)  # Role "Buy"
            if role:
                await self.author.add_roles(role)
                print(f"[DEBUG] Role 'Buy' diberikan ke {self.author}.")
            else:
                print("[WARN] Role 'Buy' tidak ditemukan di server.")
        except Exception as e:
            print(f"[ERROR] Gagal memberi role 'Buy': {e}")

        # ✅ Kirim testimoni ke channel seller
        channel_id = 839981637567643668  # ganti sesuai channel ID testimoni
        channel = bot.get_channel(channel_id)
        if channel:
            embed = discord.Embed(
                title=f"#Order Number: {transaction_id}",
                color=discord.Color.gold()
            )
            embed.add_field(name="<a:megaphone:1419515391851626580> Pembeli", value=self.author.mention, inline=False)
            embed.add_field(name="Produk <a:menkrep:1122531571098980394>", value=f"{amount} {self.kode}", inline=False)
            embed.add_field(name="Total Price", value=f"{fmt_wl(total)} <a:world_lock:1419515667773657109>", inline=False)
            embed.set_footer(text="Thanks For Purchasing Our Product(s)")
            await channel.send(embed=embed)

        # Debug log
        print(f"[DEBUG] Transaksi {transaction_id} oleh {self.author} berhasil. Testimoni dikirim ke {channel_id}.")


# ============================================================
# BUY PO (Pre Order)
# ============================================================
class BuyPOModal(Modal, title="Enter PO Amount (Max 10)"):
    """
    - Max 10 per user per produk dalam status waiting
    - DM konfirmasi wajib sukses (kalau DM mati -> cancel PO)
    - Kirim nomor antrian
    """

    def __init__(self, kode: str, author: discord.Member):
        super().__init__()
        self.kode = kode
        self.author = author
        self.qty_input = TextInput(
            label="Amount (1-10)", placeholder="1", required=True
        )
        self.add_item(self.qty_input)

    async def on_submit(self, interaction: discord.Interaction):
        uid = self.author.id
        # Harus terdaftar
        c.execute("SELECT nama FROM users WHERE user_id=?", (uid,))
        row = c.fetchone()
        if not row:
            await interaction.response.send_message(
                "❌ Kamu belum register. Klik **SET GROWID**.", ephemeral=True
            )
            return
        growid = row[0]

        # Amount 1..10
        try:
            amt = int(str(self.qty_input.value).strip())
            if amt <= 0 or amt > 10:
                raise ValueError
        except Exception:
            await interaction.response.send_message(
                "❌ Amount harus 1–10.", ephemeral=True
            )
            return

        # Cek total waiting existing user utk kode ini
        c.execute(
            """
            SELECT COALESCE(SUM(amount),0)
            FROM preorders
            WHERE user_id=? AND kode=? AND status='waiting'
        """,
            (uid, self.kode),
        )
        waiting_total = int(c.fetchone()[0] or 0)
        if waiting_total >= 10 or waiting_total + amt > 10:
            await interaction.response.send_message(
                "❌ Max PO 10 per produk (kamu sudah penuh).", ephemeral=True
            )
            return

                # Ambil harga produk
        c.execute("SELECT harga FROM stock WHERE kode=?", (self.kode,))
        h = c.fetchone()
        if not h:
            await interaction.response.send_message("❌ Produk tidak valid.", ephemeral=True)
            return
        price = int(h[0])
        total = price * amt

        # Ambil saldo user
        c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        row_balance = c.fetchone()
        balance = int(row_balance[0] or 0)

        # Cek saldo cukup atau tidak
        if balance < total:
            await interaction.response.send_message(
                f"❌ Saldo tidak cukup. Kamu butuh {fmt_wl(total)} WL, saldo kamu {fmt_wl(balance)} WL.",
                ephemeral=True
            )
            return

        # Potong saldo user
        new_balance = balance - total
        c.execute("UPDATE users SET balance=? WHERE user_id=?", (new_balance, uid))

        # Insert PO waiting
        c.execute(
            """
            INSERT INTO preorders (nama, user_id, kode, amount, status)
            VALUES (?, ?, ?, ?, 'waiting')
            """,
            (growid, uid, self.kode, amt),
        )
        po_id = c.lastrowid
        conn.commit()


        # Hitung nomor antrian
        c.execute(
            """
            SELECT COUNT(*) FROM preorders
            WHERE kode=? AND status='waiting'
            AND created_at <= (SELECT created_at FROM preorders WHERE id=?)
        """,
            (self.kode, po_id),
        )
        queue_pos = int(c.fetchone()[0] or 1)

        # DM konfirmasi PO (wajib sukses)
        try:
            msg = (
                "```📦 Pre Order Dicatat\n"
                "--------------------------\n"
                f"Produk  : {self.kode}\n"
                f"Jumlah  : {amt}\n"
                f"Status  : Menunggu stok\n"
                f"Antrian : #{queue_pos}```"
            )
            await self.author.send(msg)
            await interaction.response.send_message(
                "✅ PO dicatat. Cek DM untuk detail.", ephemeral=True
            )
        except Exception:
            # DM mati -> batalkan PO
            c.execute("UPDATE preorders SET status='cancelled' WHERE id=?", (po_id,))
            conn.commit()
            await interaction.response.send_message(
                "❌ DM kamu mati, PO dibatalkan.", ephemeral=True
            )


# ============================================================
# Product Selectors
# ============================================================
class ProductSelect(Select):
    """Dropdown untuk BUY (tampilkan stok & harga)."""

    def __init__(self, author: discord.Member, products=None):
        if not products:
            options = [
                discord.SelectOption(
                    label="(No products)", value="none", description="Add stock first"
                )
            ]
        else:
            options = [
                discord.SelectOption(
                    label=f"{title}",
                    description=f"Stock: {qty} | Price: {price} WL",
                    value=code,
                )
                for (code, title, qty, price) in products
            ]
        super().__init__(
            placeholder="Choose a product...",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.author = author

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await send_ephemeral_countdown(interaction, "No products yet.")
            return
        await interaction.response.send_modal(
            BuyModal(kode=self.values[0], author=self.author)
        )


class ProductSelectView(View):
    def __init__(self, author: discord.Member, products=None):
        super().__init__(timeout=90)
        self.add_item(ProductSelect(author, products=products))


class ProductSelectPO(Select):
    """Dropdown untuk BUY PO (tanpa stok, teks 'Pre Order Product | Price: ...')."""

    def __init__(self, author: discord.Member, products=None):
        if not products:
            options = [
                discord.SelectOption(
                    label="(No products)", value="none", description="Add stock first"
                )
            ]
        else:
            options = [
                discord.SelectOption(
                    label=f"{title}",
                    description=f"Pre Order Product | Price: {price} WL",
                    value=code,
                )
                for (code, title, qty, price) in products
            ]
        super().__init__(
            placeholder="Choose a product (PO)...",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.author = author

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await send_ephemeral_countdown(interaction, "No products yet.")
            return
        await interaction.response.send_modal(
            BuyPOModal(kode=self.values[0], author=self.author)
        )


class ProductSelectPOView(View):
    def __init__(self, author: discord.Member, products=None):
        super().__init__(timeout=90)
        self.add_item(ProductSelectPO(author, products=products))


# ============================================================
# Stock View (dengan tombol BUY PO)
# ============================================================
class StockView(View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(
            Button(label="Buy", style=discord.ButtonStyle.green, custom_id="buy",
                disabled=self.is_mt())
        )
        self.add_item(
            Button(label="Buy PO", style=discord.ButtonStyle.green, custom_id="buy_po",
                disabled=self.is_mt())
        )  # << NEW
        self.add_item(
            Button(
                label="Deposit", style=discord.ButtonStyle.blurple, custom_id="deposit",
                disabled=self.is_mt()
            )
        )
        self.add_item(
            Button(
                label="Set GrowID", style=discord.ButtonStyle.gray, custom_id="growid",
                disabled=self.is_mt()
            )
        )
        self.add_item(
            Button(
                label="My Balance",
                style=discord.ButtonStyle.secondary,
                custom_id="balance",
                disabled=self.is_mt()
            )
        )



    def is_mt(self):
        c.execute("SELECT is_mt FROM maintenance LIMIT 1")
        row = c.fetchone()
        is_mt = row[0] if row else 0

        if is_mt == 1:
            return True
        return False


# ============================================================
# Engine: Alokasi PO saat restock
# ============================================================
async def allocate_preorders(kode: str):
    """
    Jalankan fungsi ini SETIAP kali stok untuk `kode` ditambahkan (restock).
    - Ambil daftar PO waiting (FIFO).
    - Ambil item dari stock_items sesuai jatah user.
    - Kirim DM struk. Kalau DM gagal -> cancel PO & lanjut.
    - Partial fulfill: kalau stok habis di tengah, sisa amount tetap waiting.
    """
    # Hitung stok dulu
    c.execute("SELECT COUNT(*) FROM stock_items WHERE kode=?", (kode,))
    stock_available = int(c.fetchone()[0] or 0)
    if stock_available <= 0:
        return

    # (Opsional) ambil harga untuk info
    c.execute("SELECT harga FROM stock WHERE kode=?", (kode,))
    pr = c.fetchone()
    price = int(pr[0]) if pr else 0

    # Ambil queue PO
    c.execute(
        """
        SELECT id, user_id, nama, amount 
        FROM preorders
        WHERE kode=? AND status='waiting'
        ORDER BY created_at ASC, id ASC
    """,
        (kode,),
    )
    queue = c.fetchall()

    for po_id, user_id, growid, amount in queue:
        if stock_available <= 0:
            break

        # Jatah user = min(amount, stock_available)
        jatah = min(amount, stock_available)
        if jatah <= 0:
            continue

        # Ambil item
        c.execute(
            "SELECT id, nama_barang FROM stock_items WHERE kode=? ORDER BY id LIMIT ?",
            (kode, jatah),
        )
        items = c.fetchall()
        if not items or len(items) < jatah:
            # stok berubah, refresh count & lanjut
            c.execute("SELECT COUNT(*) FROM stock_items WHERE kode=?", (kode,))
            stock_available = int(c.fetchone()[0] or 0)
            continue

        ids = [str(x[0]) for x in items]
        bought_names = "\n".join([x[1] for x in items])

        # Coba DM sebelum commit
        member = bot.get_user(user_id)
        try:
            dm_msg = (
                "```🛒 Pre Order Success!\n"
                "--------------------------\n"
                f"Code   : {kode}\n"
                f"Amount : {jatah}\n"
                f"Price  : {price}\n"
                f"Total  : {price*jatah}\n\n"
                f"📦 Items:\n{bought_names}```"
            )
            if member is None:
                raise RuntimeError("Member not found in cache")
            await member.send(dm_msg)
        except Exception:
            # DM gagal -> cancel PO & lanjut user berikutnya
            c.execute("UPDATE preorders SET status='cancelled' WHERE id=?", (po_id,))
            conn.commit()
            continue

        # DM sukses -> commit transaksi
        c.execute(
            f"DELETE FROM stock_items WHERE id IN ({','.join(['?']*len(ids))})", ids
        )
        c.execute(
            "INSERT INTO transactions (user_id, kode, jumlah) VALUES (?, ?, ?)",
            (user_id, kode, jatah),
        )
        transaction_id = c.lastrowid

        c.executemany(
    "INSERT INTO preorder_items (preorder_id, nama_barang) VALUES (?, ?)",
    [(po_id, x[1]) for x in items]
)

        
        stock_available -= jatah

        if jatah == amount:
            # terpenuhi semua
            c.execute("UPDATE preorders SET status='success' WHERE id=?", (po_id,))
            channel_od = 839981637567643668
            channel = bot.get_channel(channel_od)
            if channel:
                embed = discord.Embed(
                    title=f"#Pesanan Pre Order Number: {transaction_id}",
                    color=discord.Color.gold()
                )
                embed.add_field(name="<a:megaphone:1419515391851626580> Pembeli", value=f"<@{user_id}> ({growid})", inline=False)
                embed.add_field(name="Produk <a:menkrep:1122531571098980394>", value=f"{jatah} {kode}", inline=False)
                embed.add_field(name="Total Price", value=f"{fmt_wl(price * jatah)} <a:world_lock:1419515667773657109>", inline=False)
                embed.set_footer(text="Thanks For Purchasing Our Product(s)")
                await channel.send(embed=embed)

        else:
            # partial fulfill -> sisa tetap waiting (kurangi amount)
            sisa = amount - jatah
            c.execute("UPDATE preorders SET amount=? WHERE id=?", (sisa, po_id))

        conn.commit()


# ============================================================
# Hook after addstock
# ============================================================
from discord.ext import tasks


@tasks.loop(seconds=10)  # jalan tiap 10 detik
async def auto_allocate_po():
    # Ambil semua kode produk yg ada preorder waiting
    c.execute("SELECT DISTINCT kode FROM preorders WHERE status='waiting'")
    rows = c.fetchall()
    for (kode,) in rows:
        await allocate_preorders(kode)


# ============================================================
# Setup hook
# ============================================================
def setup(_bot, _c, _conn, _fmt_wl, _PREFIX):
    global bot, c, conn, fmt_wl, PREFIX
    bot = _bot
    c = _c
    conn = _conn
    fmt_wl = _fmt_wl
    PREFIX = _PREFIX

    # pastikan schema
    ensure_users_schema(c, conn)
    ensure_preorders_schema(c, conn)
    ensure_transactions_schema(c, conn)
    ensure_transaction_items_schema(c, conn)
    ensure_preorder_items_schema(c, conn)
    
    # handler tombol
    async def on_interaction(interaction: discord.Interaction): ...

    bot.add_listener(on_interaction, "on_interaction")

    # pastikan loop auto allocate start saat bot ready
    @bot.event
    async def on_ready():
        if not auto_allocate_po.is_running():
            auto_allocate_po.start()

        print("[AUTO_ALLOCATE] Loop started")

    # handler tombol
    async def on_interaction(interaction: discord.Interaction):
        if not getattr(interaction, "data", None):
            return
        cid = interaction.data.get("custom_id", "")
        user = interaction.user

        # BUY (lama)
        if cid == "buy":
            products = await fetch_products_for_select()
            await interaction.response.send_message(
                "🛒 Choose product:",
                view=ProductSelectView(user, products=products),
                ephemeral=True,
            )
            return

        # BUY PO (baru)
        if cid == "buy_po":
            products = await fetch_products_for_select()
            await interaction.response.send_message(
                "🛒 Choose PO product (Max 10 per user):",
                view=ProductSelectPOView(user, products=products),
                ephemeral=True,
            )
            return

        # SET GROWID
        if cid == "growid":
            await interaction.response.send_modal(GrowIDModal(user.id))
            return

        # Balance
        if cid == "balance":
            c.execute("SELECT nama, balance FROM users WHERE user_id=?", (user.id,))
            r = c.fetchone()
            if r:
                await send_ephemeral_countdown(
                    interaction,
                    f"GrowID: {r[0]} | Balance: {fmt_wl(int(r[1] or 0))} WL",
                )
            else:
                await send_ephemeral_countdown(interaction, "❌ Kamu belum register.")
            return

        # Deposit info
        if cid == "deposit":
            emb = discord.Embed(title="💳 Deposit Info", color=discord.Color.gold())
            emb.add_field(name="World", value="MODALMEKI")
            emb.add_field(name="Name Bot", value="everyone")
            await send_ephemeral_countdown(interaction, "ℹ️ Deposit info", embed=emb)
            return

    bot.add_listener(on_interaction, "on_interaction")


# ============================================================
# Catatan:
# - Panggil allocate_preorders(kode) SETIAP kali kamu menambah stok
#   (misal di command admin restock), agar PO waiting langsung didistribusi.
# - Kalau kamu butuh command admin contoh:
#
#   @bot.command()
#   @commands.has_permissions(administrator=True)
#   async def restockpo(ctx, kode: str):
#       await allocate_preorders(kode)
#       await ctx.reply(f"PO untuk {kode} sudah dialokasikan (kalau ada).")
#
#   (Taruh di modul command-mu, pastikan import allocate_preorders)
# ============================================================
